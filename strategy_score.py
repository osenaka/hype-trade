"""
データ実証ベース逆張り戦略
──────────────────────────────────────────────────────────
analyze_factors.py の結果から、実際に価格を先行している指標だけを使う。

採用指標（4つ）:
  spot_close  現物価格   低水準→Long  Q1/Q5スプレッド -17.7pt ★最強
  bridged_usdc USDC残高  低水準→Long  Q1/Q5スプレッド -14.8pt
  oi_usd      OI        低水準→Long  Q1/Q5スプレッド -11.7pt
  vol_ma7     出来高MA7  低水準→Long  Q1/Q5スプレッド  -8.1pt

除外指標:
  FR          → ほぼランダム（スプレッド-2.4pt程度）
  Fee         → シグナル弱く方向も不安定
  USDC流入率   → ホライズン依存で不安定

エントリー: 4指標中3以上が「低水準」でLong
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys

# ─── 設定 ────────────────────────────────────────────
TP_PCT        = 0.05    # 利確 +5%
SL_PCT        = 0.03    # 損切 -3%
MAX_BARS      = 84      # 最大保有 14日（84本 × 4h）
FEE_RATE      = 0.00035 # 手数料（片道）

LONG_MIN_SCORE = 3      # 4指標中3以上でLong
MAX_SCORE      = 4

# 各指標の「低水準」閾値（percentileランク）
# analyze_factors.pyの結果: Q1（下位20%）で特に効果が高いため0.40以下を条件に
PRICE_LOW = 0.40   # 現物価格が下位40%以下 → Long +1
USDC_LOW  = 0.40   # USDC残高が下位40%以下 → Long +1
OI_LOW    = 0.40   # OIが下位40%以下       → Long +1
VOL_LOW   = 0.40   # 出来高MA7が下位40%以下 → Long +1

# ─── データ読み込み ────────────────────────────────────
DAILY_CSV  = Path("hype_data/daily_features.csv")
CANDLE_CSV = Path("hype_data/candles_4h.csv")

daily = pd.read_csv(DAILY_CSV, index_col=0, parse_dates=True)
c4    = pd.read_csv(CANDLE_CSV)
c4["time"] = pd.to_datetime(c4["time"], utc=True)
c4 = c4.sort_values("time").reset_index(drop=True)

CANDLE_START = c4["time"].iloc[0]

# ─── percentile ランク（直近180日ローリング） ─────────────
# 全期間ランクは「価格が上昇してきた銘柄」で機能しなくなるため、
# 直近180日の中での相対水準を使う
RANK_WINDOW = 180

for col in ["spot_close", "bridged_usdc", "oi_usd", "vol_ma7"]:
    if col not in daily.columns:
        continue
    daily[f"_r_{col}"] = (
        daily[col]
        .rolling(RANK_WINDOW, min_periods=30)
        .apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    )

# ─── スコアリング ─────────────────────────────────────
def compute_score(row):
    r_price = row.get("_r_spot_close",   np.nan)
    r_usdc  = row.get("_r_bridged_usdc", np.nan)
    r_oi    = row.get("_r_oi_usd",       np.nan)
    r_vol   = row.get("_r_vol_ma7",      np.nan)

    l_price = int(pd.notna(r_price) and r_price <= PRICE_LOW)
    l_usdc  = int(pd.notna(r_usdc)  and r_usdc  <= USDC_LOW)
    l_oi    = int(pd.notna(r_oi)    and r_oi    <= OI_LOW)
    l_vol   = int(pd.notna(r_vol)   and r_vol   <= VOL_LOW)

    score  = l_price + l_usdc + l_oi + l_vol
    detail = f"Price:{l_price} USDC:{l_usdc} OI:{l_oi} Vol:{l_vol}"
    return score, detail

results = daily.apply(lambda r: pd.Series(compute_score(r), index=["score","detail"]), axis=1)
daily["score"]  = results["score"]
daily["detail"] = results["detail"]
daily["signal"] = daily["score"].apply(lambda s: "LONG" if s >= LONG_MIN_SCORE else None)

# スコア分布
daily_bt = daily[daily.index >= CANDLE_START.tz_localize(None)]
print("─── スコア分布（バックテスト対象期間内）───")
print(f"  総日数: {len(daily_bt)}")
for sc in range(MAX_SCORE + 1):
    n = (daily_bt["score"] == sc).sum()
    sig = " ← エントリー" if sc >= LONG_MIN_SCORE else ""
    print(f"  スコア {sc}/{MAX_SCORE}: {n:>3}日{sig}")
print()

# ─── バックテストエンジン ─────────────────────────────
c4_times = c4["time"].values
c4_opens = c4["open"].values
c4_highs = c4["high"].values
c4_lows  = c4["low"].values

trades = []
open_until = None

for date, row in daily.iterrows():
    if row["signal"] != "LONG":
        continue

    entry_dt = pd.Timestamp(date, tz="UTC") + pd.Timedelta(days=1)
    if entry_dt < CANDLE_START:
        continue
    if open_until is not None and entry_dt < open_until:
        continue

    edt_np = np.datetime64(entry_dt.replace(tzinfo=None), "ns")
    idx_arr = np.where(c4_times >= edt_np)[0]
    if len(idx_arr) == 0:
        continue

    si = idx_arr[0]
    entry_px = c4_opens[si]
    if entry_px <= 0:
        continue

    tp = entry_px * (1 + TP_PCT)
    sl = entry_px * (1 - SL_PCT)
    exit_px = None; exit_reason = None; exit_time = None
    end = min(si + MAX_BARS, len(c4_opens))

    for i in range(si, end):
        h = c4_highs[i]; l = c4_lows[i]
        if l <= sl: exit_px = sl; exit_reason = "SL"; exit_time = c4_times[i]; break
        if h >= tp: exit_px = tp; exit_reason = "TP"; exit_time = c4_times[i]; break

    if exit_px is None:
        if end < len(c4_opens):
            exit_px = c4_opens[end]; exit_reason = "TIME"; exit_time = c4_times[end]
        else:
            continue

    if exit_time is not None:
        et = pd.Timestamp(exit_time)
        open_until = et.tz_localize("UTC") if et.tzinfo is None else et

    raw = (exit_px - entry_px) / entry_px
    net = raw - FEE_RATE * 2

    trades.append({
        "date":     str(date.date()),
        "score":    int(row["score"]),
        "detail":   row["detail"],
        "entry_px": round(entry_px, 4),
        "exit_px":  round(exit_px, 4),
        "exit":     exit_reason,
        "raw_pnl":  round(raw * 100, 3),
        "net_pnl":  round(net * 100, 3),
    })

# ─── 集計 ─────────────────────────────────────────────
if not trades:
    print("⚠ シグナルなし"); sys.exit()

tdf  = pd.DataFrame(trades)
n    = len(tdf)
wins = (tdf["net_pnl"] > 0).sum()
wr   = wins / n * 100
avg_w = tdf[tdf["net_pnl"] > 0]["net_pnl"].mean() if wins > 0 else 0
avg_l = tdf[tdf["net_pnl"] <= 0]["net_pnl"].mean() if (n - wins) > 0 else 0
total = tdf["net_pnl"].sum()
pf    = (tdf[tdf["net_pnl"] > 0]["net_pnl"].sum() /
         (-tdf[tdf["net_pnl"] < 0]["net_pnl"].sum())) if (tdf["net_pnl"] < 0).any() else float("inf")

equity = [1.0]
for p in tdf["net_pnl"]:
    equity.append(equity[-1] * (1 + p / 100))
max_dd = 0.0; peak = equity[0]
for e in equity:
    if e > peak: peak = e
    dd = (peak - e) / peak
    if dd > max_dd: max_dd = dd

be = SL_PCT / (TP_PCT + SL_PCT) * 100

print(f"{'='*60}")
print(f"  逆張り4指標戦略（データ実証ベース）")
print(f"  TP: +{TP_PCT*100:.0f}%  /  SL: -{SL_PCT*100:.0f}%  /  最大保有: {MAX_BARS//6}日")
print(f"  エントリー: {LONG_MIN_SCORE}/{MAX_SCORE}指標以上が下位{int(PRICE_LOW*100)}%に低水準")
print(f"  損益分岐勝率: {be:.1f}%")
print(f"{'='*60}")
print(f"  総トレード数         {n}")
print(f"  勝率                 {wr:.1f}%  (損益分岐: {be:.1f}%)")
print(f"  平均利益（勝ち）     {avg_w:+.2f}%")
print(f"  平均損失（負け）     {avg_l:+.2f}%")
print(f"  Profit Factor        {pf:.2f}")
print(f"  総損益               {total:+.2f}%")
print(f"  最大DD               -{max_dd*100:.1f}%")
print(f"  最終資産倍率         {equity[-1]:.4f}x")
print()

print(f"  スコア別:")
for sc in sorted(tdf["score"].unique()):
    sub = tdf[tdf["score"] == sc]
    sw  = (sub["net_pnl"] > 0).sum()
    print(f"  {sc}/{MAX_SCORE}: {len(sub):>3}件 / 勝率{sw/len(sub)*100:.0f}% / 計{sub['net_pnl'].sum():+.2f}%")

print()
print(f"  決済理由:")
for r in ["TP", "SL", "TIME"]:
    sub = tdf[tdf["exit"] == r]
    if len(sub): print(f"  {r:<5} {len(sub):>3}件  avg: {sub['net_pnl'].mean():+.2f}%")

print(f"\n  {'─'*58}")
print(f"  全トレード ({n}件)")
print(f"  {'─'*58}")
print(f"  {'日付':<12} {'スコア':^6} {'エントリー':>9} {'決済':>9} {'PnL':>8} {'理由':<6} 内訳")
for _, t in tdf.iterrows():
    pnl_str = f"{t['net_pnl']:+.2f}%"
    print(f"  {t['date']:<12} {t['score']}/{MAX_SCORE}   "
          f"{t['entry_px']:>9.4f} {t['exit_px']:>9.4f} {pnl_str:>8} {t['exit']:<6} {t['detail']}")
