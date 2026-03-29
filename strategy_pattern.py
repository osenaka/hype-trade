"""
strategy_pattern.py
===================
分析から導いたパターンをそのまま戦略に変換してバックテスト。

【上昇しやすい条件（Long エントリー）】
  - 価格・OI・USDC残高・出来高が低水準（まだ盛り上がってない）
  - USDC流入率・OI変化率が上昇中（加速し始めてる）
  - Fee が静かな局面（相場が寝てる）

【下落しやすい条件（Short エントリー）】
  - 全指標が高水準（満員御礼）

実行方法:
  python strategy_pattern.py

TP/SL は 4時間足で管理（日次シグナル → 翌4h足オープンでエントリー）
"""

import pandas as pd
import numpy as np
from pathlib import Path

DAILY_CSV = Path(__file__).parent / "hype_data" / "daily_features.csv"
CANDLE_CSV = Path(__file__).parent / "hype_data" / "candles_4h.csv"

# ── パラメータ ─────────────────────────────────────
TP_PCT    = 0.05    # 利確 +5%
SL_PCT    = 0.01    # 損切 -1%
MAX_BARS  = 42      # 最大保有（42本 = 7日）
FEE_RATE  = 0.00035 # 片道手数料

# ターミナル色
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"
C = "\033[96m"; B = "\033[1m";  D = "\033[2m"; X = "\033[0m"

def col(v, s): return f"{s}{v}{X}"

# ── データ読み込み ─────────────────────────────────
daily = pd.read_csv(DAILY_CSV, index_col=0, parse_dates=True)
c4    = pd.read_csv(CANDLE_CSV)
c4["time"] = pd.to_datetime(c4["time"], utc=True)
c4 = c4.sort_values("time").reset_index(drop=True)

# daily の分位ランクを計算（0.0〜1.0）
def rank_col(df, col):
    return df[col].rank(pct=True, na_option="keep")

for col_name in ["bridged_usdc", "spot_close", "oi_usd", "volume_usd",
                 "vol_ma7", "fee_ma7", "spot_twap_7d"]:
    if col_name in daily.columns:
        daily[f"_rank_{col_name}"] = rank_col(daily, col_name)

# ── シグナル定義 ───────────────────────────────────
# 閾値（チューニング可能）
LOW_THRESH  = 0.45   # 下位45%以下 = 「低水準」
HIGH_THRESH = 0.70   # 上位70%以上 = 「高水準」
FLOW_MIN    = 0.003  # 7日流入率 0.3%以上 = 「加速中」
OI_CHG_MIN  = 0.005  # OI 1日変化率 0.5%以上 = 「増加中」（緩和）

def compute_signal(row):
    """
    各行について Long/Short/None を返す。

    Long 条件（修正版）:
      - bridged_usdc は必須（2023年〜データあり、最重要逆張り指標）
      - spot_close / oi_usd / vol_ma7 は「データがあれば低水準」を確認
        → データ揃っていれば2/3以上が低水準でOK（AND→多数決）
      - bridge_flow_7d_pct が上昇中（必須）
      - oi_chg_1d が上昇中 OR データなし（緩和）
      - fee_ma7 が静か（データがあれば低水準）
    """
    # 分位ランク取得（NaN の場合は中立扱い）
    r_usdc  = row.get("_rank_bridged_usdc", np.nan)
    r_price = row.get("_rank_spot_close",   np.nan)
    r_oi    = row.get("_rank_oi_usd",       np.nan)
    r_vol   = row.get("_rank_vol_ma7",      np.nan)
    r_fee   = row.get("_rank_fee_ma7",      np.nan)

    flow_7d = row.get("bridge_flow_7d_pct", np.nan)
    oi_chg  = row.get("oi_chg_1d",          np.nan)

    # ── Long 条件 ──────────────────────────────────
    # bridged_usdc は必須（最重要逆張り指標）
    usdc_low = pd.notna(r_usdc) and r_usdc < LOW_THRESH

    # spot_close / oi / vol: データがある列だけカウント、2/3以上が低水準でOK
    optional_checks = []
    if pd.notna(r_price): optional_checks.append(r_price < LOW_THRESH)
    if pd.notna(r_oi):    optional_checks.append(r_oi    < LOW_THRESH)
    if pd.notna(r_vol):   optional_checks.append(r_vol   < LOW_THRESH)
    # データなしの場合はパス、あれば過半数が低水準
    opt_low = (len(optional_checks) == 0) or (sum(optional_checks) >= max(1, len(optional_checks) // 2 + 1))

    # 変化率系: 加速中（flow_7d は必須、oi_chg はデータなし or 上昇でOK）
    flow_accel = pd.notna(flow_7d) and flow_7d > FLOW_MIN
    oi_ok      = pd.isna(oi_chg) or oi_chg > OI_CHG_MIN

    # Fee: 静か（有効データがある場合のみ）
    fee_quiet = pd.isna(r_fee) or r_fee < LOW_THRESH

    # ── Short 条件 ─────────────────────────────────
    level_high = all([
        pd.notna(r_usdc)  and r_usdc  > HIGH_THRESH,
        pd.notna(r_price) and r_price > HIGH_THRESH,
        pd.notna(r_oi)    and r_oi    > HIGH_THRESH,
        pd.notna(r_vol)   and r_vol   > HIGH_THRESH,
    ])
    flow_out = pd.notna(flow_7d) and flow_7d < -FLOW_MIN

    if usdc_low and opt_low and flow_accel and oi_ok and fee_quiet:
        return "LONG"
    if level_high and flow_out:
        return "SHORT"
    return None

daily["signal"] = daily.apply(compute_signal, axis=1)

# キャンドルデータの開始日（この日以降のシグナルのみバックテスト対象）
CANDLE_START = c4["time"].iloc[0]  # 2024-11-29

# ── バックテストエンジン ────────────────────────────
trades = []

for date, row in daily.iterrows():
    sig = row["signal"]
    if sig is None:
        continue

    # シグナル日の翌日00:00以降の最初の4h足でエントリー
    entry_dt = pd.Timestamp(date, tz="UTC") + pd.Timedelta(days=1)

    # ⚠ キャンドルデータの範囲外はスキップ（2023〜2024-11-28のシグナルは除外）
    if entry_dt < CANDLE_START:
        continue

    future = c4[c4["time"] >= entry_dt].reset_index(drop=True)
    if len(future) == 0:
        continue

    entry_bar = future.iloc[0]
    entry_px  = entry_bar["open"]

    if entry_px <= 0:
        continue

    direction = 1 if sig == "LONG" else -1
    tp_price  = entry_px * (1 + direction * TP_PCT)
    sl_price  = entry_px * (1 - direction * SL_PCT)

    exit_px   = None
    exit_bar  = None
    exit_reason = None

    for i in range(len(future)):
        if i >= MAX_BARS:
            exit_px     = future.iloc[i]["open"]
            exit_bar    = future.iloc[i]
            exit_reason = "TIME"
            break

        bar = future.iloc[i]
        h, l = bar["high"], bar["low"]

        if sig == "LONG":
            if l <= sl_price:
                exit_px     = sl_price
                exit_bar    = bar
                exit_reason = "SL"
                break
            if h >= tp_price:
                exit_px     = tp_price
                exit_bar    = bar
                exit_reason = "TP"
                break
        else:  # SHORT
            if h >= sl_price:
                exit_px     = sl_price
                exit_bar    = bar
                exit_reason = "SL"
                break
            if l <= tp_price:
                exit_px     = tp_price
                exit_bar    = bar
                exit_reason = "TP"
                break

    if exit_px is None:
        continue

    raw_pnl = direction * (exit_px - entry_px) / entry_px
    net_pnl = raw_pnl - FEE_RATE * 2

    trades.append({
        "date":      date.date(),
        "signal":    sig,
        "entry_dt":  entry_bar["time"],
        "exit_dt":   exit_bar["time"] if exit_bar is not None else None,
        "entry_px":  round(entry_px, 4),
        "exit_px":   round(exit_px, 4),
        "exit":      exit_reason,
        "raw_pnl":   round(raw_pnl * 100, 3),
        "net_pnl":   round(net_pnl * 100, 3),
        # シグナル日の指標値
        "bridged_usdc_rank": round(row.get("_rank_bridged_usdc", np.nan), 3),
        "flow_7d_pct":       round(row.get("bridge_flow_7d_pct", np.nan), 4),
        "oi_chg_1d":         round(row.get("oi_chg_1d", np.nan), 4),
    })

# ── 結果表示 ───────────────────────────────────────
print(col(f"\n{'='*62}", C))
print(col(f"  パターン戦略 バックテスト結果", B))
print(col(f"  TP: +{TP_PCT*100:.0f}%  /  SL: -{SL_PCT*100:.0f}%  /  最大保有: {MAX_BARS//6}日", D))
print(col(f"{'='*62}", C))

if not trades:
    print(col("\n  ⚠ シグナルが1件も発生しませんでした。閾値を緩めてください。", Y))
else:
    tdf = pd.DataFrame(trades)

    # 全体集計
    n      = len(tdf)
    wins   = (tdf["net_pnl"] > 0).sum()
    wr     = wins / n * 100
    avg_w  = tdf[tdf["net_pnl"] > 0]["net_pnl"].mean() if wins > 0 else 0
    avg_l  = tdf[tdf["net_pnl"] <= 0]["net_pnl"].mean() if (n - wins) > 0 else 0
    total  = tdf["net_pnl"].sum()
    pf     = (-tdf[tdf["net_pnl"] > 0]["net_pnl"].sum() /
               tdf[tdf["net_pnl"] < 0]["net_pnl"].sum()) if (tdf["net_pnl"] < 0).any() else float("inf")

    # エクイティカーブ
    equity = [1.0]
    for pnl in tdf["net_pnl"]:
        equity.append(equity[-1] * (1 + pnl / 100))
    max_dd = 0.0
    peak = equity[0]
    for e in equity:
        if e > peak: peak = e
        dd = (peak - e) / peak
        if dd > max_dd: max_dd = dd

    # ── サマリー表示 ──
    def fmt(v, positive_good=True):
        if positive_good:
            c_ = G if v > 0 else R
        else:
            c_ = R if v > 0 else G
        return col(f"{v:+.2f}%", c_)

    print(f"\n  {'総トレード数':<20} {n}")
    long_n  = (tdf["signal"] == "LONG").sum()
    short_n = (tdf["signal"] == "SHORT").sum()
    print(f"  {'Long / Short':<20} {long_n} / {short_n}")
    wr_col = G if wr >= 50 else (Y if wr >= 40 else R)
    print(f"  {'勝率':<20} {col(f'{wr:.1f}%', wr_col)}")
    print(f"  {'平均利益（勝ち）':<18} {fmt(avg_w)}")
    print(f"  {'平均損失（負け）':<18} {fmt(avg_l)}")
    pf_col = G if pf >= 1.5 else (Y if pf >= 1.0 else R)
    print(f"  {'Profit Factor':<20} {col(f'{pf:.2f}', pf_col)}")
    print(f"  {'総損益':<20} {fmt(total)}")
    print(f"  {'最大DD':<20} {col(f'-{max_dd*100:.1f}%', R)}")
    print(f"  {'最終資産倍率':<19} {col(f'{equity[-1]:.4f}x', G if equity[-1] > 1 else R)}")

    # 決済理由内訳
    print(f"\n  {'決済理由:'}")
    for reason in ["TP", "SL", "TIME"]:
        sub = tdf[tdf["exit"] == reason]
        if len(sub) == 0: continue
        avg = sub["net_pnl"].mean()
        print(f"  {reason:<6} {len(sub):>3}件  平均: {fmt(avg)}")

    # ── Long / Short 別集計 ──
    for side in ["LONG", "SHORT"]:
        sub = tdf[tdf["signal"] == side]
        if len(sub) == 0: continue
        s_wins = (sub["net_pnl"] > 0).sum()
        s_wr   = s_wins / len(sub) * 100
        s_tot  = sub["net_pnl"].sum()
        wr_c   = G if s_wr >= 50 else (Y if s_wr >= 40 else R)
        print(f"\n  [{side}] {len(sub)}件 / 勝率 {col(f'{s_wr:.1f}%', wr_c)} / 合計 {fmt(s_tot)}")

    # ── 全トレード一覧 ──
    print(col(f"\n  {'─'*62}", D))
    print(col(f"  全トレード一覧 ({n}件)", B))
    print(col(f"  {'─'*62}", D))
    hdr = f"  {'日付':<12} {'方向':<6} {'エントリー':>10} {'決済':>10} {'PnL':>8} {'決済理由':<8}"
    print(col(hdr, D))
    for _, t in tdf.iterrows():
        pnl_col  = G if t["net_pnl"] > 0 else R
        side_col = G if t["signal"] == "LONG" else Y
        pnl_str  = col(f"{t['net_pnl']:+.2f}%", pnl_col)
        print(f"  {str(t['date']):<12} "
              f"{col(t['signal'], side_col):<15} "
              f"{t['entry_px']:>10.4f} "
              f"{t['exit_px']:>10.4f} "
              f"{pnl_str:>17} "
              f"{t['exit']:<8}")

    # ── シグナル発生条件のまとめ ──
    print(col(f"\n{'='*62}", C))
    print(col(f"  シグナル条件（現在の閾値）", B))
    print(col(f"{'='*62}", C))
    print(f"\n  {col('LONG（買い）', G)}")
    print(f"  ・Bridged USDC残高 / 価格 / OI / 出来高MA が 下位{int(LOW_THRESH*100)}%以下")
    print(f"  ・USDC 7日流入率  > +{FLOW_MIN*100:.1f}%（加速中）")
    print(f"  ・OI 1日変化率   > +{OI_CHG_MIN*100:.1f}%（OI増加中）")
    print(f"  ・Fee水準        < 下位{int(LOW_THRESH*100)}%（または未集計期間）")
    print(f"\n  {col('SHORT（売り）', Y)}")
    print(f"  ・Bridged USDC残高 / 価格 / OI / 出来高MA が 上位{int((1-HIGH_THRESH)*100)}%以上")
    print(f"  ・USDC 7日流入率  < -{FLOW_MIN*100:.1f}%（流出中）")

print(col(f"\n{'='*62}\n", C))
