#!/usr/bin/env python3
"""
日次データ更新スクリプト
========================
毎日1回実行して daily_features.csv を更新

使い方:
  python update_daily_data.py          # 本日分を追加
  python update_daily_data.py --check  # 追加せず確認のみ

VPSでの自動実行（cron例）:
  0 1 * * * cd /path/to/hype-trade && python3 update_daily_data.py >> logs/update.log 2>&1
"""

import argparse
import csv
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# === 設定 ===
DATA_DIR = Path(__file__).parent / "hype_data"
DAILY_FEATURES = DATA_DIR / "daily_features.csv"

HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
HYPURRSCAN_API = "https://api.hypurrscan.io/fees"

# === API関数 ===
def api_post(payload):
    """Hyperliquid API POST"""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        HYPERLIQUID_API,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def get_hype_data():
    """HYPEの現在データ取得"""
    ctx = api_post({"type": "metaAndAssetCtxs"})

    for i, asset in enumerate(ctx[0].get("universe", [])):
        if asset.get("name") == "HYPE":
            asset_ctx = ctx[1][i]
            mark_px = float(asset_ctx.get("markPx", 0))
            return {
                "oi_usd": float(asset_ctx.get("openInterest", 0)) * mark_px,
                "volume_usd": float(asset_ctx.get("dayNtlVlm", 0)),
                "mark_price": mark_px,
                "funding_rate": float(asset_ctx.get("funding", 0)),
            }
    return None

def get_spot_price():
    """HYPE現物価格"""
    mids = api_post({"type": "allMids"})
    return float(mids.get("@107", 0))

def get_funding_history(days=7):
    """過去N日のFunding Rate履歴"""
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)

    result = api_post({
        "type": "fundingHistory",
        "coin": "HYPE",
        "startTime": start_time,
        "endTime": end_time
    })

    # Group by day
    daily_fr = {}
    for r in result:
        ts = r['time'] / 1000
        date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
        fr = float(r['fundingRate'])
        daily_fr[date] = daily_fr.get(date, 0) + fr

    return daily_fr

def get_fee_history():
    """Fee履歴（累計→日次変換）"""
    req = urllib.request.Request(
        HYPURRSCAN_API,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())

    daily_fees = {}
    for i in range(1, len(data)):
        prev = data[i-1]
        curr = data[i]

        prev_date = datetime.fromtimestamp(prev['time'], tz=timezone.utc).strftime('%Y-%m-%d')
        curr_date = datetime.fromtimestamp(curr['time'], tz=timezone.utc).strftime('%Y-%m-%d')

        if prev_date != curr_date:
            total = (curr['total_fees'] - prev['total_fees']) / 1e6
            spot = (curr['total_spot_fees'] - prev['total_spot_fees']) / 1e6
            perp = total - spot
            daily_fees[curr_date] = {
                'total': total,
                'spot': spot,
                'perp': perp,
            }

    return daily_fees

def get_usdc_supply():
    """USDC供給量"""
    try:
        result = api_post({"type": "spotMeta"})
        for token in result.get("tokens", []):
            if token.get("name") == "USDC":
                # Convert from raw units
                return float(token.get("totalSupply", 0)) / 1e6
    except:
        pass
    return None

def load_existing_data():
    """既存データを読み込み"""
    if not DAILY_FEATURES.exists():
        return []

    with open(DAILY_FEATURES) as f:
        return list(csv.DictReader(f))

def calculate_ma(values, n=7):
    """移動平均"""
    if len(values) < n:
        return None
    return sum(values[-n:]) / n

def calculate_change(current, previous):
    """変化率"""
    if previous is None or previous == 0:
        return None
    return (current - previous) / previous

def main():
    parser = argparse.ArgumentParser(description="日次データ更新")
    parser.add_argument("--check", action="store_true", help="追加せず確認のみ")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    print(f"=== 日次データ更新: {today} ===")

    # 既存データ読み込み
    existing = load_existing_data()
    existing_dates = {r['date'] for r in existing}

    if today in existing_dates:
        print(f"本日分({today})は既に存在します。スキップ。")
        return

    print("データ取得中...")

    # 現在データ取得
    hype_data = get_hype_data()
    spot_price = get_spot_price()
    funding_history = get_funding_history(days=14)
    fee_history = get_fee_history()
    usdc_supply = get_usdc_supply()

    if not hype_data:
        print("エラー: HYPEデータ取得失敗")
        return

    print(f"  OI: ${hype_data['oi_usd']/1e6:.2f}M")
    print(f"  Volume: ${hype_data['volume_usd']/1e6:.2f}M")
    print(f"  Spot Price: ${spot_price:.2f}")

    # 今日のFR（当日分がまだなければ昨日分を使用）
    today_fr = funding_history.get(today, 0)
    if today_fr == 0:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')
        today_fr = funding_history.get(yesterday, 0)

    # FR MA7
    fr_values = sorted(funding_history.items())[-7:]
    fr_ma7 = sum(v for _, v in fr_values) / len(fr_values) if fr_values else None

    # 今日のFee
    today_fee = fee_history.get(today, {})
    if not today_fee:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')
        today_fee = fee_history.get(yesterday, {'total': 0, 'spot': 0, 'perp': 0})

    # Fee MA7
    fee_values = sorted(fee_history.items())[-7:]
    fee_ma7 = sum(v['total'] for _, v in fee_values) / len(fee_values) if fee_values else None

    # 過去データからの計算
    prev_rows = existing[-7:] if existing else []

    # OI変化率
    oi_1d_ago = float(prev_rows[-1]['oi_usd']) if prev_rows and prev_rows[-1].get('oi_usd') else None
    oi_3d_ago = float(prev_rows[-3]['oi_usd']) if len(prev_rows) >= 3 and prev_rows[-3].get('oi_usd') else None
    oi_chg_1d = calculate_change(hype_data['oi_usd'], oi_1d_ago)
    oi_chg_3d = calculate_change(hype_data['oi_usd'], oi_3d_ago)

    # Volume MA7
    vol_values = [float(r['volume_usd']) for r in prev_rows if r.get('volume_usd')]
    vol_values.append(hype_data['volume_usd'])
    vol_ma7 = calculate_ma(vol_values, 7)
    vol_ratio = hype_data['volume_usd'] / vol_ma7 if vol_ma7 else None

    # Fee ratio
    fee_ratio = today_fee['total'] / fee_ma7 if fee_ma7 and today_fee['total'] else None

    # Spot TWAPs
    spot_values = [float(r['spot_close']) for r in prev_rows if r.get('spot_close')]
    spot_values.append(spot_price)
    spot_twap_7d = calculate_ma(spot_values, 7)

    # Return計算（1日後リターンは翌日更新時に計算）
    spot_1d_ago = float(prev_rows[-1]['spot_close']) if prev_rows and prev_rows[-1].get('spot_close') else None
    ret_1d = calculate_change(spot_price, spot_1d_ago)

    # USDC変化
    usdc_1d_ago = float(prev_rows[-1]['usdc_supply']) if prev_rows and prev_rows[-1].get('usdc_supply') else None
    usdc_chg_1d = calculate_change(usdc_supply, usdc_1d_ago) if usdc_supply and usdc_1d_ago else None

    # 新しい行を作成
    new_row = {
        'date': today,
        'fr_daily_sum': today_fr,
        'oi_usd': hype_data['oi_usd'],
        'volume_usd': hype_data['volume_usd'],
        'liquidations_usd': '',  # 別途取得が必要
        'usdc_supply': usdc_supply or '',
        'bridged_usdc': '',  # 別途取得が必要
        'oi_chg_1d': oi_chg_1d or '',
        'oi_chg_3d': oi_chg_3d or '',
        'vol_ma7': vol_ma7 or '',
        'vol_ratio': vol_ratio or '',
        'fee_ratio': fee_ratio or '',
        'usdc_chg_1d': usdc_chg_1d or '',
        'usdc_chg_7d': '',
        'bridge_flow_1d': '',
        'bridge_flow_7d': '',
        'bridge_flow_1d_pct': '',
        'bridge_flow_7d_pct': '',
        'liq_ma7': '',
        'liq_ratio': '',
        'fr_ma7': fr_ma7 or '',
        'fr_zscore': '',
        'spot_close': spot_price,
        'spot_twap_7d': spot_twap_7d or '',
        'spot_twap_14d': '',
        'spot_twap_30d': '',
        'spot_vwap_7d': '',
        'ret_1d': ret_1d or '',
        'ret_3d': '',
        'ret_7d': '',
        'ret_14d': '',
        'twap_premium_7d': '',
        'fee_total': today_fee.get('total', ''),
        'fee_spot': today_fee.get('spot', ''),
        'fee_perp': today_fee.get('perp', ''),
        'fee_ma7': fee_ma7 or '',
    }

    print(f"\n=== 新規データ ===")
    print(f"  日付: {new_row['date']}")
    print(f"  OI: ${float(new_row['oi_usd'])/1e6:.2f}M")
    print(f"  Volume: ${float(new_row['volume_usd'])/1e6:.2f}M")
    print(f"  Spot: ${new_row['spot_close']:.2f}")
    print(f"  FR Daily: {float(new_row['fr_daily_sum']):.6f}")
    print(f"  FR MA7: {float(new_row['fr_ma7']):.6f}" if new_row['fr_ma7'] else "  FR MA7: N/A")
    print(f"  Fee Total: ${float(new_row['fee_total'])/1e6:.2f}M" if new_row['fee_total'] else "  Fee Total: N/A")
    print(f"  Fee MA7: ${float(new_row['fee_ma7'])/1e6:.2f}M" if new_row['fee_ma7'] else "  Fee MA7: N/A")

    if args.check:
        print("\n[確認モード] データは保存されませんでした。")
        return

    # CSVに追記
    fieldnames = list(new_row.keys())

    # 既存のヘッダーを使用
    if existing:
        with open(DAILY_FEATURES) as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames

    with open(DAILY_FEATURES, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(new_row)

    print(f"\n{DAILY_FEATURES} に追加しました。")

if __name__ == "__main__":
    main()
