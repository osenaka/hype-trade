"""
Assistance Fund (0xfefe) 全期間Feeデータ取得
───────────────────────────────────────────────
実行方法（Mac上で）:
  cd /path/to/hype-trade
  pip install requests
  python3 fetch_af_fees.py

出力: hype_data/af_fee_daily.csv
"""

import requests
import json
import time
import csv
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

AF_ADDR = "0xfefefefefefefefefefefefefefefefefefefefe"
HL_API  = "https://api.hyperliquid.xyz/info"
OUT_CSV = Path("hype_data/af_fee_daily.csv")

# 取得開始日（HYPEローンチ前から）
START_DT = datetime(2023, 6, 1, tzinfo=timezone.utc)
END_DT   = datetime.now(timezone.utc)

CHUNK_DAYS = 7  # 一度に取得する日数（レート制限対策）

def hl_post(payload, retries=3):
    for i in range(retries):
        try:
            r = requests.post(HL_API, json=payload, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  retry {i+1}/{retries}: {e}")
            time.sleep(2)
    return []

def ms(dt):
    return int(dt.timestamp() * 1000)

print(f"=== Assistance Fund Fee データ取得 ===")
print(f"アドレス: {AF_ADDR}")
print(f"期間: {START_DT.date()} 〜 {END_DT.date()}")
print()

# 動作確認
print("接続テスト...")
test = hl_post({"type": "userFillsByTime", "user": AF_ADDR,
                "startTime": ms(END_DT - timedelta(days=1))})
print(f"直近1日: {len(test)} fills")
if test:
    print(f"サンプル fill: {json.dumps(test[0], indent=2)}")
print()

# 全期間を CHUNK_DAYS 単位で取得
all_fills = []
cur = START_DT
total_chunks = int((END_DT - START_DT).days / CHUNK_DAYS) + 1

print(f"全期間取得開始（{total_chunks}チャンク）...")
chunk_no = 0
while cur < END_DT:
    nxt = min(cur + timedelta(days=CHUNK_DAYS), END_DT)
    fills = hl_post({
        "type": "userFillsByTime",
        "user": AF_ADDR,
        "startTime": ms(cur),
        "endTime":   ms(nxt),
    })
    all_fills.extend(fills)
    chunk_no += 1
    if chunk_no % 10 == 0:
        print(f"  {chunk_no}/{total_chunks} チャンク完了 ({cur.date()} 〜 {nxt.date()}) 累計{len(all_fills)}件")
    cur = nxt
    time.sleep(0.2)  # レート制限対策

print(f"\n取得完了: 合計 {len(all_fills)} fills")
print()

# 日次集計
# fillのフィールド: time(ms), coin, px, sz, side, closedPnl, fee, dir, hash...
daily = defaultdict(lambda: {"total_usdc": 0.0, "hype_bought": 0.0, "fill_count": 0})

for fill in all_fills:
    dt = datetime.fromtimestamp(fill["time"] / 1000, tz=timezone.utc)
    day = dt.date().isoformat()
    coin = fill.get("coin", "")
    sz   = float(fill.get("sz", 0))
    px   = float(fill.get("px", 0))
    side = fill.get("side", "")
    notional = sz * px

    # HYPEのバイバック（AFがBUY側）
    if "HYPE" in coin and side == "B":
        daily[day]["hype_bought"] += sz
        daily[day]["total_usdc"]  += notional
    elif "HYPE" not in coin:
        # 他のコインのfill（あれば）
        daily[day]["total_usdc"] += notional

    daily[day]["fill_count"] += 1

# CSV出力
rows = sorted(daily.items())
print(f"日次データ: {len(rows)} 日分")
if rows:
    print(f"  from: {rows[0][0]}")
    print(f"  to:   {rows[-1][0]}")
    print()
    print("最新5日分:")
    for day, v in rows[-5:]:
        print(f"  {day}: HYPE買い {v['hype_bought']:.2f}枚 / {v['total_usdc']:,.0f} USDC / {v['fill_count']}fills")

OUT_CSV.parent.mkdir(exist_ok=True)
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "total_usdc", "hype_bought", "fill_count"])
    w.writeheader()
    for day, v in rows:
        w.writerow({"date": day, **v})

print(f"\n保存: {OUT_CSV}")
print("次のステップ: daily_features.csv に結合するには hype_analyzer.py を再実行してください")
