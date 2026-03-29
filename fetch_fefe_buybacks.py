"""
0xfefe (Assistance Fund) の全期間HYPE購入履歴を取得
────────────────────────────────────────────────────
Hyperliquid userFillsByTime APIを時間分割して全件取得

出力: hype_data/fefe_buybacks.csv
  date, hype_qty, usd_value, fill_count
  （単位: HYPE / USDC）
"""

import urllib.request
import json
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

FEFE = "0xfefefefefefefefefefefefefefefefefefefefe"
OUT_CSV = Path("hype_data/fefe_buybacks.csv")
OUT_FILLS = Path("hype_data/fefe_fills_raw.csv")
API_URL = "https://api.hyperliquid.xyz/info"

# 開始: Hyperliquid mainnet launch (2024-11-17)
START_DATE = datetime(2024, 11, 17, tzinfo=timezone.utc)
END_DATE   = datetime.now(timezone.utc)

CHUNK_DAYS = 3      # 3日ずつ取得（1日最大~400件 × 3 = 1200件で余裕あり）
LIMIT      = 2000   # API最大件数

def fetch_fills(start_ms, end_ms):
    payload = json.dumps({
        "type": "userFillsByTime",
        "user": FEFE,
        "startTime": start_ms,
        "endTime": end_ms
    }).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# ── 全期間を分割して取得 ────────────────────────────────
print(f"=== 0xfefe 全期間バイバック取得 ===")
print(f"期間: {START_DATE.date()} 〜 {END_DATE.date()}")
print(f"チャンクサイズ: {CHUNK_DAYS}日")
print()

all_fills = []
total_chunks = 0
cursor = START_DATE

while cursor < END_DATE:
    chunk_end = min(cursor + timedelta(days=CHUNK_DAYS), END_DATE)
    start_ms = int(cursor.timestamp() * 1000)
    end_ms   = int(chunk_end.timestamp() * 1000)

    try:
        fills = fetch_fills(start_ms, end_ms)
        n = len(fills)

        # HYPE (@107) の買いのみ
        hype_buys = [f for f in fills if f.get("coin") == "@107" and f.get("side") == "B"]

        # 2000件上限に達した場合は警告
        if n >= LIMIT:
            print(f"  ⚠ {cursor.date()}: 上限{LIMIT}件到達! チャンクを縮小してください")

        all_fills.extend(hype_buys)
        print(f"  {cursor.date()} 〜 {chunk_end.date()}: fills={n} / HYPE買い={len(hype_buys)}")

    except Exception as e:
        print(f"  ✗ {cursor.date()}: エラー {e}")

    cursor = chunk_end
    total_chunks += 1

print(f"\n取得完了: {total_chunks}チャンク / HYPE買い合計 {len(all_fills)}件")

# ── 日次集計 ───────────────────────────────────────────
daily = {}
for f in all_fills:
    day = datetime.fromtimestamp(f["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    if day not in daily:
        daily[day] = {"date": day, "hype_qty": 0.0, "usd_value": 0.0, "fill_count": 0}
    qty = float(f.get("sz", 0))
    px  = float(f.get("px", 0))
    daily[day]["hype_qty"]   += qty
    daily[day]["usd_value"]  += qty * px
    daily[day]["fill_count"] += 1

# ── CSV出力 ───────────────────────────────────────────
OUT_CSV.parent.mkdir(exist_ok=True)

rows = sorted(daily.values(), key=lambda x: x["date"])
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date","hype_qty","usd_value","fill_count"])
    w.writeheader()
    for row in rows:
        w.writerow({
            "date":       row["date"],
            "hype_qty":   round(row["hype_qty"], 2),
            "usd_value":  round(row["usd_value"], 2),
            "fill_count": row["fill_count"],
        })

# raw fills保存
with open(OUT_FILLS, "w", newline="") as f:
    if all_fills:
        keys = ["time","coin","side","px","sz","hash","tid"]
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_fills)

# ── サマリー ──────────────────────────────────────────
total_hype = sum(r["hype_qty"] for r in rows)
total_usd  = sum(r["usd_value"] for r in rows)
zero_days  = [r["date"] for r in rows if r["hype_qty"] == 0]

print(f"\n{'='*50}")
print(f"  全期間HYPE購入サマリー")
print(f"  日数: {len(rows)}日 (data有り)")
print(f"  累計購入量:  {total_hype:>15,.0f} HYPE")
print(f"  累計購入額:  ${total_usd:>14,.0f} USDC")
print(f"  日次平均量:  {total_hype/max(len(rows),1):>15,.0f} HYPE/日")
print(f"  日次平均額:  ${total_usd/max(len(rows),1):>14,.0f} USDC/日")
print(f"{'='*50}")

# 買っていない日の確認
# → daily集計に入っていない日 = fills=0の日
all_dates = set()
d = START_DATE
while d <= END_DATE:
    all_dates.add(d.strftime("%Y-%m-%d"))
    d += timedelta(days=1)

data_dates = set(daily.keys())
no_buy_days = sorted(all_dates - data_dates)

print(f"\n  「買っていない日」: {len(no_buy_days)}日")
if no_buy_days:
    print(f"  最初の10日: {no_buy_days[:10]}")
    print(f"  最後の10日: {no_buy_days[-10:]}")

print(f"\n保存先:")
print(f"  日次: {OUT_CSV}")
print(f"  生データ: {OUT_FILLS}")
