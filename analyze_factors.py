"""
各指標 × 将来リターンの因子分析
──────────────────────────────────────────────────────────
「この指標が高い/低いとき、その後の価格はどう動くか」を実際のデータで検証する。

前提ロジックなし。データから傾向を発見する。

分析内容:
  1. 各指標の5分位 × 将来リターン（翌日/3日/7日/14日）
  2. 勝率・平均・中央値・標準偏差
  3. モノトニック性スコア（Q1→Q5 で単調増加/減少してるか）
  4. 指標間の複合分析（上位2指標の組み合わせ）
  5. 「過去30日の傾向（上昇/下降）」× リターン
"""

import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path(__file__).parent / "hype_data" / "daily_features.csv"

# ── ターミナル色付け ─────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def c(text, color): return f"{color}{text}{RESET}"

# ── データ読み込み ─────────────────────────────────
df = pd.read_csv(DATA, index_col=0, parse_dates=True)
print(c(f"\n{'='*60}", CYAN))
print(c(f"  HYPE 指標 × 将来リターン 徹底分析", BOLD))
print(c(f"  期間: {df.index[0].date()} 〜 {df.index[-1].date()} ({len(df)}日)", DIM))
print(c(f"{'='*60}\n", CYAN))

# ── 分析対象指標の定義 ──────────────────────────────
FACTOR_GROUPS = {
    "📊 FR（資金調達率）": [
        ("fr_daily_sum",  "FR 日次合計（raw）",       "逆張り期待"),
        ("fr_zscore",     "FR Zスコア（30日偏差）",    "逆張り期待"),
        ("fr_ma7",        "FR 7日MA",               "逆張り期待"),
    ],
    "💵 USDC入出金フロー": [
        ("bridged_usdc",        "Bridged USDC 残高",        "高水準=逆張り期待"),
        ("bridge_flow_7d_pct",  "7日間流入率（%）",           "流入加速=順張り期待"),
        ("bridge_flow_1d_pct",  "1日流入率（%）",             "流入=順張り期待"),
        ("bridge_flow_7d",      "7日純流入額（USD）",          "?"),
    ],
    "📈 出来高": [
        ("volume_usd",   "出来高（raw）",        "?"),
        ("vol_ratio",    "出来高/7日MA比率",     "急増=?"),
        ("vol_ma7",      "出来高 7日MA",         "?"),
    ],
    "💰 Fee（HL利益）": [
        ("fee_total",    "Fee収入（raw）",        "?"),
        ("fee_ratio",    "Fee/7日MA比率",        "急増=?"),
        ("fee_ma7",      "Fee 7日MA",            "?"),
    ],
    "📉 現物TWAP・価格": [
        ("spot_close",       "現物終値",              "?"),
        ("twap_premium_7d",  "TWAP7dプレミアム",       "過熱度"),
        ("spot_twap_7d",     "7日TWAP水準",           "逆張り期待"),
        ("spot_twap_30d",    "30日TWAP水準",          "逆張り期待"),
    ],
    "🔥 OI・清算（次点）": [
        ("oi_usd",           "OI（raw）",             "高い=レバ過多"),
        ("oi_chg_1d",        "OI 1日変化率",           "増加=モメンタム"),
        ("oi_chg_3d",        "OI 3日変化率",           "増加=モメンタム"),
        ("liquidations_usd", "清算額（raw）",           "急増=底打ち?"),
        ("liq_ratio",        "清算/7日MA比率",         "急増=底打ち?"),
    ],
}

HORIZONS = [("ret_1d", "翌日"), ("ret_3d", "3日後"), ("ret_7d", "7日後"), ("ret_14d", "14日後")]
N_QUANTILES = 5

# ── ユーティリティ ─────────────────────────────────
def color_ret(val):
    """リターン値を色付き文字列に変換"""
    if pd.isna(val): return f"{'  N/A':>7}"
    s = f"{val:+.1f}%"
    if val > 1.0:   return c(f"{s:>7}", GREEN)
    if val < -1.0:  return c(f"{s:>7}", RED)
    return f"{s:>7}"

def color_wr(val):
    if pd.isna(val): return f"{'N/A':>5}"
    s = f"{val:.0f}%"
    if val >= 55:   return c(f"{s:>5}", GREEN)
    if val <= 45:   return c(f"{s:>5}", RED)
    return f"{s:>5}"

def monotonic_score(values):
    """
    Q1〜Q5 の値が単調増加 → +1.0、単調減少 → -1.0
    ランダム → 0付近
    """
    vals = [v for v in values if not pd.isna(v)]
    if len(vals) < 3: return 0.0
    diffs = [vals[i+1] - vals[i] for i in range(len(vals)-1)]
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    return (pos - neg) / len(diffs)

def quintile_analysis(factor_col, ret_col, df):
    """1指標 × 1リターン列の5分位分析"""
    sub = df[[factor_col, ret_col]].dropna()
    if len(sub) < 50:
        return None
    try:
        sub = sub.copy()
        sub["q"] = pd.qcut(sub[factor_col], N_QUANTILES, labels=False, duplicates="drop")
        result = sub.groupby("q")[ret_col].agg(
            count="count",
            mean=lambda x: x.mean() * 100,
            median=lambda x: x.median() * 100,
            win_rate=lambda x: (x > 0).mean() * 100,
            std=lambda x: x.std() * 100,
        ).reset_index()
        return result
    except Exception:
        return None

# ── メイン分析ループ ────────────────────────────────
results_summary = []  # 後で複合分析に使う

for group_name, factors in FACTOR_GROUPS.items():
    print(c(f"\n{'─'*60}", CYAN))
    print(c(f"  {group_name}", BOLD))
    print(c(f"{'─'*60}", CYAN))

    for factor_col, factor_label, hypothesis in factors:
        if factor_col not in df.columns:
            print(c(f"  ⚠ {factor_col} → データなし", YELLOW))
            continue

        valid_count = df[factor_col].notna().sum()
        if valid_count < 50:
            print(c(f"  ⚠ {factor_col} → サンプル不足 ({valid_count}件)", YELLOW))
            continue

        print(f"\n  {c(factor_label, BOLD)} {c(f'[{factor_col}]', DIM)}  {c(f'仮説:{hypothesis}', DIM)}")
        print(f"  {c(f'有効サンプル: {valid_count}件', DIM)}")

        # ヘッダー行
        header = f"  {'分位':^8} {'件数':>5}"
        for _, hz_label in HORIZONS:
            header += f"  {'平均':>7} {'勝率':>5}"
        print(c(header, DIM))
        print(c(f"  {'':^8} {'':>5}" + "  " + "  ".join([f"{'─'*7} {'─'*5}" for _ in HORIZONS]), DIM))

        row_data = []  # モノトニック計算用
        mono_means = {hz: [] for _, hz in [(r, l) for r, l in HORIZONS]}

        for q_idx in range(N_QUANTILES):
            q_label = ["Q1(低)", "Q2   ", "Q3   ", "Q4   ", "Q5(高)"][q_idx]
            row_str = f"  {q_label:^8}"

            first_count = None
            means_this_row = []

            for ret_col, hz_label in HORIZONS:
                res = quintile_analysis(factor_col, ret_col, df)
                if res is None:
                    row_str += f"  {'N/A':>7} {'N/A':>5}"
                    continue
                q_row = res[res["q"] == q_idx]
                if q_row.empty:
                    row_str += f"  {'N/A':>7} {'N/A':>5}"
                    continue
                cnt  = int(q_row["count"].values[0])
                mean = q_row["mean"].values[0]
                wr   = q_row["win_rate"].values[0]
                if first_count is None:
                    first_count = cnt
                means_this_row.append(mean)
                mono_means[hz_label].append(mean)
                row_str += f"  {color_ret(mean)} {color_wr(wr)}"

            count_str = f"{first_count}" if first_count else "N/A"
            row_str = row_str[:2] + f"{count_str:>5}" + row_str[7:]
            print(row_str)

        # モノトニックスコアと方向性サマリー
        print()
        scores = []
        for ret_col, hz_label in HORIZONS:
            vals = mono_means[hz_label]
            if len(vals) >= 3:
                ms = monotonic_score(vals)
                scores.append((hz_label, ms))

        if scores:
            summary_parts = []
            for hz_label, ms in scores:
                if ms >= 0.5:
                    arrow = c(f"↑順張り({ms:+.1f})", GREEN)
                elif ms <= -0.5:
                    arrow = c(f"↓逆張り({ms:+.1f})", RED)
                else:
                    arrow = c(f"→不明({ms:+.1f})", YELLOW)
                summary_parts.append(f"{hz_label}:{arrow}")
            print(f"  {c('方向性:', DIM)} {' | '.join(summary_parts)}")

        # サマリーに追加（複合分析用）
        q1_7d = None
        q5_7d = None
        res_7d = quintile_analysis(factor_col, "ret_7d", df)
        if res_7d is not None:
            q1_row = res_7d[res_7d["q"] == 0]
            q5_row = res_7d[res_7d["q"] == 4]
            if not q1_row.empty: q1_7d = q1_row["mean"].values[0]
            if not q5_row.empty: q5_7d = q5_row["mean"].values[0]
        results_summary.append({
            "factor": factor_col,
            "label": factor_label,
            "q1_7d_ret": q1_7d,
            "q5_7d_ret": q5_7d,
            "spread_7d": (q5_7d - q1_7d) if (q1_7d and q5_7d) else None,
        })

# ── 総合ランキング ──────────────────────────────────
print(c(f"\n{'='*60}", CYAN))
print(c(f"  📊 総合ランキング（7日後リターン Q1→Q5 スプレッド順）", BOLD))
print(c(f"  ※ スプレッドが大きいほど「指標の高低で未来が変わる」", DIM))
print(c(f"{'='*60}", CYAN))

valid_results = [r for r in results_summary if r["spread_7d"] is not None]
valid_results.sort(key=lambda x: abs(x["spread_7d"]), reverse=True)

print(f"\n  {'指標':<28} {'Q1(低)時':>9} {'Q5(高)時':>9} {'差':>8}  方向")
print(c(f"  {'─'*28} {'─'*9} {'─'*9} {'─'*8}  {'─'*10}", DIM))
for r in valid_results[:15]:
    spread = r["spread_7d"]
    q1 = r["q1_7d_ret"]
    q5 = r["q5_7d_ret"]
    direction = c("↑高い→上昇", GREEN) if spread > 0 else c("↑高い→下落", RED)
    print(f"  {r['label']:<28} {color_ret(q1)} {color_ret(q5)} {color_ret(spread)}  {direction}")

# ── 上位2指標の複合分析 ─────────────────────────────
print(c(f"\n{'='*60}", CYAN))
print(c(f"  🔀 複合分析（上位指標の組み合わせ）", BOLD))
print(c(f"  ※ 2つの指標が同時に「好条件」のとき何が起きるか", DIM))
print(c(f"{'='*60}", CYAN))

# 有望な指標ペアを選定（スプレッドTOP指標から）
top_factors = [r["factor"] for r in valid_results[:6] if r["factor"] in df.columns]

combos_analyzed = []
for i in range(len(top_factors)):
    for j in range(i+1, len(top_factors)):
        f1, f2 = top_factors[i], top_factors[j]
        sub = df[[f1, f2, "ret_7d"]].dropna()
        if len(sub) < 30:
            continue

        try:
            sub = sub.copy()
            sub["q1"] = pd.qcut(sub[f1], 3, labels=["低","中","高"], duplicates="drop")
            sub["q2"] = pd.qcut(sub[f2], 3, labels=["低","中","高"], duplicates="drop")
        except Exception:
            continue

        # 4パターン抽出：(低,低) (低,高) (高,低) (高,高)
        patterns = [("低","低"), ("低","高"), ("高","低"), ("高","高")]
        combo_rows = []
        for p1, p2 in patterns:
            mask = (sub["q1"] == p1) & (sub["q2"] == p2)
            seg = sub[mask]["ret_7d"]
            if len(seg) < 5: continue
            combo_rows.append({
                "cond": f"{p1}/{p2}",
                "n": len(seg),
                "mean": seg.mean() * 100,
                "wr": (seg > 0).mean() * 100,
            })

        if len(combo_rows) >= 3:
            means = [r["mean"] for r in combo_rows]
            spread = max(means) - min(means)
            combos_analyzed.append({
                "f1": f1, "f2": f2,
                "rows": combo_rows,
                "spread": spread,
            })

combos_analyzed.sort(key=lambda x: x["spread"], reverse=True)

for combo in combos_analyzed[:5]:
    f1_label = next((r["label"] for r in results_summary if r["factor"] == combo["f1"]), combo["f1"])
    f2_label = next((r["label"] for r in results_summary if r["factor"] == combo["f2"]), combo["f2"])
    print(f"\n  {c(f1_label, BOLD)} × {c(f2_label, BOLD)}")
    combo_desc = f"({combo['f1']} × {combo['f2']})"
    combo_spread = f"スプレッド: {combo['spread']:+.1f}%"
    print(f"  {c(combo_desc, DIM)}")
    print(f"  {c(combo_spread, YELLOW)}")
    print(f"  {'条件(F1/F2)':^12} {'件数':>5} {'7日後平均':>10} {'勝率':>6}")
    print(c(f"  {'─'*12} {'─'*5} {'─'*10} {'─'*6}", DIM))
    for row in combo["rows"]:
        print(f"  {row['cond']:^12} {row['n']:>5} {color_ret(row['mean'])} {color_wr(row['wr'])}")

# ── 連続上昇・下落トレンドの分析 ─────────────────────
print(c(f"\n{'='*60}", CYAN))
print(c(f"  📈 トレンド分析（各指標の直近モメンタム）", BOLD))
print(c(f"  ※ 指標が「上昇トレンド中」か「下降トレンド中」かで分類", DIM))
print(c(f"{'='*60}", CYAN))

trend_factors = [
    ("bridged_usdc",       "Bridged USDC残高",      7),
    ("fr_daily_sum",       "FR日次合計",             7),
    ("volume_usd",         "出来高",                 7),
    ("oi_usd",             "OI",                    7),
    ("fee_total",          "Fee収入",                7),
]

print(f"\n  {'指標':<20} {'上昇トレンド中':^20} {'下降トレンド中':^20}")
print(f"  {'':^20} {'7日後avg  勝率':^20} {'7日後avg  勝率':^20}")
print(c(f"  {'─'*20} {'─'*20} {'─'*20}", DIM))

for factor_col, label, window in trend_factors:
    if factor_col not in df.columns: continue
    sub = df[[factor_col, "ret_7d"]].dropna().copy()
    if len(sub) < 60: continue

    sub["trend"] = sub[factor_col].pct_change(window)
    up   = sub[sub["trend"] > 0]["ret_7d"]
    down = sub[sub["trend"] < 0]["ret_7d"]

    if len(up) < 10 or len(down) < 10: continue

    up_mean = up.mean() * 100
    up_wr   = (up > 0).mean() * 100
    dn_mean = down.mean() * 100
    dn_wr   = (down > 0).mean() * 100

    print(f"  {label:<20} "
          f"{color_ret(up_mean)}({len(up):>3}件) {color_wr(up_wr)}  "
          f"{color_ret(dn_mean)}({len(down):>3}件) {color_wr(dn_wr)}")

# ── 極値（上位/下位5%）の分析 ──────────────────────
print(c(f"\n{'='*60}", CYAN))
print(c(f"  ⚡ 極値分析（上位/下位5% = 異常値のとき）", BOLD))
print(c(f"  ※ 指標が「異常に高い/低い」ときの特殊な動き", DIM))
print(c(f"{'='*60}", CYAN))

extreme_factors = [
    ("fr_daily_sum",       "FR日次合計"),
    ("bridge_flow_1d_pct", "1日流入率"),
    ("vol_ratio",          "出来高比率"),
    ("liq_ratio",          "清算比率"),
    ("twap_premium_7d",    "TWAPプレミアム"),
    ("oi_chg_1d",          "OI1日変化率"),
]

print(f"\n  {'指標':<20} {'下位5%(異常低)':^22} {'上位5%(異常高)':^22} {'通常時':^15}")
print(c(f"  {'─'*20} {'─'*22} {'─'*22} {'─'*15}", DIM))

for factor_col, label in extreme_factors:
    if factor_col not in df.columns: continue
    sub = df[[factor_col, "ret_7d"]].dropna().copy()
    if len(sub) < 50: continue

    lo5  = sub[factor_col].quantile(0.05)
    hi5  = sub[factor_col].quantile(0.95)

    bot = sub[sub[factor_col] <= lo5]["ret_7d"]
    top = sub[sub[factor_col] >= hi5]["ret_7d"]
    mid = sub[(sub[factor_col] > lo5) & (sub[factor_col] < hi5)]["ret_7d"]

    bot_m = bot.mean()*100 if len(bot)>3 else None
    top_m = top.mean()*100 if len(top)>3 else None
    mid_m = mid.mean()*100 if len(mid)>10 else None
    bot_w = (bot>0).mean()*100 if len(bot)>3 else None
    top_w = (top>0).mean()*100 if len(top)>3 else None

    print(f"  {label:<20} "
          f"{color_ret(bot_m)} {color_wr(bot_w)}({len(bot):>2}件)  "
          f"{color_ret(top_m)} {color_wr(top_w)}({len(top):>2}件)  "
          f"{color_ret(mid_m)}")

print(c(f"\n{'='*60}", CYAN))
print(c(f"  ✅ 分析完了", BOLD))
print(c(f"{'='*60}\n", CYAN))
