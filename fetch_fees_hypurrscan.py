"""
Hypurrscan /fees API から全期間Feeデータを取得して日次集計する
────────────────────────────────────────────────────
実行方法（Mac上で）:
  cd /path/to/hype-trade
  python3 fetch_fees_hypurrscan.py

出力: hype_data/fee_daily.csv
  date, fee_total, fee_spot, fee_perp
  （単位: USDC）

データソース:
  https://api.hypurrscan.io/fees
  → 累積プロトコルFeeのスナップショット（約24h間隔、460件以上）
  → total_fees / total_spot_fees は microUSDC 単位（1e-6 USDC）
  → 差分をとって日次Fee (USDC) に変換する
"""

import urllib.request
import json
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

API_URL = "https://api.hypurrscan.io/fees"
OUT_CSV = Path("hype_data/fee_daily.csv")

MICRO = 1_000_000  # microUSDC → USDC 変換係数

print("=== Hypurrscan /fees データ取得 ===")
print(f"URL: {API_URL}")
print()

# ── 取得 ────────────────────────────────────────────
print("取得中...")
req = urllib.request.Request(
    API_URL,
    headers={"User-Agent": "Mozilla/5.0"},
    method="GET"
)
with urllib.request.urlopen(req, timeout=30) as r:
    raw = json.loads(r.read())

print(f"スナップショット数: {len(raw)} 件")

# ── 時系列ソート ──────────────────────────────────────
data = sorted(raw, key=lambda x: x["time"])

oldest = datetime.fromtimestamp(data[0]["time"], tz=timezone.utc).date()
newest = datetime.fromtimestamp(data[-1]["time"], tz=timezone.utc).date()
print(f"期間: {oldest} 〜 {newest}")
print()

# ── 日次集計（差分方式）─────────────────────────────────
# スナップショットは約24h間隔。「その日の累積値」をその日付に割り当て、
# 前日との差分を日次Feeとする。
# 同じ日に複数スナップショットがある場合は最後の値を使う。

# まず日付→最後のスナップショットのマップを作る
daily_snap = {}
for snap in data:
    dt = datetime.fromtimestamp(snap["time"], tz=timezone.utc).date().isoformat()
    daily_snap[dt] = snap  # 後勝ち（その日の最新値）

sorted_days = sorted(daily_snap.keys())
print(f"有効日数: {len(sorted_days)} 日")

# 差分で日次Feeを計算
rows = []
prev_total = None
prev_spot  = None

for day in sorted_days:
    snap = daily_snap[day]
    cur_total = snap["total_fees"]
    cur_spot  = snap["total_spot_fees"]

    if prev_total is not None:
        fee_total = (cur_total - prev_total) / MICRO
        fee_spot  = (cur_spot  - prev_spot)  / MICRO
        fee_perp  = fee_total - fee_spot

        # 異常値チェック（マイナスや極端に大きい値は除外）
        if fee_total < 0:
            print(f"  ⚠ {day}: fee_total が負 ({fee_total:.0f}) → スキップ")
        else:
            rows.append({
                "date":      day,
                "fee_total": round(fee_total, 2),
                "fee_spot":  round(fee_spot, 2),
                "fee_perp":  round(fee_perp, 2),
            })

    prev_total = cur_total
    prev_spot  = cur_spot

# ── 出力 ─────────────────────────────────────────────
OUT_CSV.parent.mkdir(exist_ok=True)
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "fee_total", "fee_spot", "fee_perp"])
    w.writeheader()
    for row in rows:
        w.writerow(row)

print(f"出力行数: {len(rows)} 日分")
print(f"保存先: {OUT_CSV}")
print()

# ── サマリー ─────────────────────────────────────────
if rows:
    totals = [r["fee_total"] for r in rows]
    print(f"日次Fee統計（USDC）:")
    print(f"  平均:   {sum(totals)/len(totals):>12,.0f}")
    print(f"  中央値: {sorted(totals)[len(totals)//2]:>12,.0f}")
    print(f"  最大:   {max(totals):>12,.0f}")
    print(f"  最小:   {min(totals):>12,.0f}")
    print(f"  累計:   {sum(totals):>12,.0f}")
    print()
    print("最新10日:")
    for r in rows[-10:]:
        print(f"  {r['date']}: perp {r['fee_perp']:>10,.0f} USDC / spot {r['fee_spot']:>8,.0f} USDC / 計 {r['fee_total']:>10,.0f} USDC")

print()
print("次のステップ:")
print("  python3 merge_fees.py  # daily_features.csv に fee_total を結合")
