#!/usr/bin/env python3
"""
HYPE シグナル監視スクリプト
============================
OI低×FR低中×Fee中 のシグナルをリアルタイムで監視

使い方:
  python signal_monitor.py          # 1回実行
  python signal_monitor.py --loop   # 1時間ごとにループ
  python signal_monitor.py --check  # シグナル判定のみ（通知用）
"""

import argparse
import csv
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# === 設定 ===
API_URL = "https://api.hyperliquid.xyz/info"
DATA_DIR = Path(__file__).parent / "hype_data"
DAILY_FEATURES = DATA_DIR / "daily_features.csv"

# シグナル条件
SIGNAL_CONDITIONS = {
    "main": {  # メイン戦略: OI低×FR低中×Fee中
        "oi_usd": [1, 2],      # Q1-Q2
        "fr_ma7": [1, 2, 3],   # Q1-Q3
        "fee_ma7": [2, 3],     # Q2-Q3
    },
    "sub": {  # サブ戦略: OI低×FR低中
        "oi_usd": [1, 2],
        "fr_ma7": [1, 2, 3],
    }
}

# === API関数 ===
def api_post(payload):
    """Hyperliquid API POST"""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def get_meta():
    """全コインのメタデータ取得"""
    return api_post({"type": "meta"})

def get_all_mids():
    """全コインの中値取得"""
    return api_post({"type": "allMids"})

def get_funding_rate(coin="HYPE"):
    """現在のFunding Rate取得"""
    meta = get_meta()
    for asset in meta.get("universe", []):
        if asset.get("name") == coin:
            return float(asset.get("funding", 0))
    return None

def get_oi_and_volume():
    """OIと出来高を取得"""
    ctx = api_post({"type": "metaAndAssetCtxs"})

    for i, asset in enumerate(ctx[0].get("universe", [])):
        if asset.get("name") == "HYPE":
            asset_ctx = ctx[1][i]
            oi = float(asset_ctx.get("openInterest", 0))
            volume = float(asset_ctx.get("dayNtlVlm", 0))
            mark_px = float(asset_ctx.get("markPx", 0))
            funding = float(asset_ctx.get("funding", 0))
            return {
                "oi_usd": oi * mark_px,
                "volume_usd": volume,
                "mark_price": mark_px,
                "funding_rate": funding,
            }
    return None

def get_spot_price():
    """HYPE現物価格取得"""
    mids = get_all_mids()
    return float(mids.get("@107", 0))

def get_funding_ma7_realtime():
    """過去7日間のFunding Rate MA7をリアルタイム計算"""
    try:
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time = end_time - (7 * 24 * 60 * 60 * 1000)  # 7 days ago

        result = api_post({
            "type": "fundingHistory",
            "coin": "HYPE",
            "startTime": start_time,
            "endTime": end_time
        })

        if not result:
            return None

        # Group by day and sum funding rates
        daily_fr = {}
        for r in result:
            ts = r['time'] / 1000
            date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
            fr = float(r['fundingRate'])
            daily_fr[date] = daily_fr.get(date, 0) + fr

        # Calculate MA7
        if len(daily_fr) >= 7:
            sorted_dates = sorted(daily_fr.keys(), reverse=True)[:7]
            ma7 = sum(daily_fr[d] for d in sorted_dates) / 7
            return ma7
        elif daily_fr:
            return sum(daily_fr.values()) / len(daily_fr)
        return None
    except Exception as e:
        print(f"FR MA7取得エラー: {e}")
        return None

def get_fee_ma7_realtime():
    """過去7日間のFee MA7をリアルタイム計算"""
    try:
        req = urllib.request.Request(
            "https://api.hypurrscan.io/fees",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())

        if not data or len(data) < 8:
            return None

        # Convert cumulative to daily fees
        daily_fees = {}
        for i in range(1, len(data)):
            prev = data[i-1]
            curr = data[i]

            prev_date = datetime.fromtimestamp(prev['time'], tz=timezone.utc).strftime('%Y-%m-%d')
            curr_date = datetime.fromtimestamp(curr['time'], tz=timezone.utc).strftime('%Y-%m-%d')

            if prev_date != curr_date:
                daily_fee = (curr['total_fees'] - prev['total_fees']) / 1e6  # USD
                daily_fees[curr_date] = daily_fee

        # Calculate MA7
        if len(daily_fees) >= 7:
            sorted_dates = sorted(daily_fees.keys(), reverse=True)[:7]
            ma7 = sum(daily_fees[d] for d in sorted_dates) / 7
            return ma7
        return None
    except Exception as e:
        print(f"Fee MA7取得エラー: {e}")
        return None

# === 5分位計算 ===
def load_historical_quintiles():
    """過去データから5分位を計算"""
    if not DAILY_FEATURES.exists():
        print(f"Error: {DAILY_FEATURES} が見つかりません")
        return None

    with open(DAILY_FEATURES) as f:
        rows = list(csv.DictReader(f))

    # 2025年以降のデータのみ使用
    rows = [r for r in rows if r['date'] >= '2025-01-01']

    quintiles = {}
    for col in ['oi_usd', 'fr_ma7', 'fee_ma7']:
        vals = sorted([float(r[col]) for r in rows if r.get(col) and r[col].strip()])
        if len(vals) < 50:
            continue
        n = len(vals)
        quintiles[col] = {
            'q1': vals[int(n * 0.2)],
            'q2': vals[int(n * 0.4)],
            'q3': vals[int(n * 0.6)],
            'q4': vals[int(n * 0.8)],
            'min': vals[0],
            'max': vals[-1],
        }

    return quintiles

def get_quintile(value, quintile_data):
    """値がどの5分位に属するか判定"""
    if value < quintile_data['q1']:
        return 1
    elif value < quintile_data['q2']:
        return 2
    elif value < quintile_data['q3']:
        return 3
    elif value < quintile_data['q4']:
        return 4
    else:
        return 5

# === シグナル判定 ===
def check_signal(current_values, quintiles, strategy="main"):
    """シグナル条件をチェック"""
    conditions = SIGNAL_CONDITIONS[strategy]
    results = {}
    all_met = True

    for col, required_qs in conditions.items():
        if col not in quintiles or col not in current_values:
            results[col] = {"met": False, "reason": "データなし"}
            all_met = False
            continue

        q = get_quintile(current_values[col], quintiles[col])
        met = q in required_qs
        results[col] = {
            "value": current_values[col],
            "quintile": q,
            "required": required_qs,
            "met": met,
        }
        if not met:
            all_met = False

    return all_met, results

# === 表示 ===
def format_number(n, prefix=""):
    """数値を見やすくフォーマット"""
    if n >= 1_000_000_000:
        return f"{prefix}{n/1_000_000_000:.2f}B"
    elif n >= 1_000_000:
        return f"{prefix}{n/1_000_000:.2f}M"
    elif n >= 1_000:
        return f"{prefix}{n/1_000:.2f}K"
    else:
        return f"{prefix}{n:.2f}"

def display_status(current, quintiles, signal_main, signal_sub, results_main):
    """ステータス表示"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print()
    print("=" * 60)
    print(f"  HYPE シグナル監視  |  {now}")
    print("=" * 60)

    # 現在値
    print("\n【現在の指標値】")
    print(f"  HYPE価格:    ${current.get('spot_price', 0):.2f}")
    print(f"  OI:          {format_number(current.get('oi_usd', 0), '$')}")
    print(f"  FR (8h):     {current.get('funding_rate', 0)*100:.4f}%")
    print(f"  出来高(24h):  {format_number(current.get('volume_usd', 0), '$')}")

    # 5分位判定
    print("\n【5分位判定】")
    print(f"  {'指標':<12} | {'現在値':>12} | {'分位':>4} | {'条件':>8} | {'判定'}")
    print("  " + "-" * 55)

    for col, data in results_main.items():
        if "value" in data:
            value_str = format_number(data['value'], '$') if 'usd' in col else f"{data['value']:.4f}"
            q_str = f"Q{data['quintile']}"
            req_str = f"Q{data['required'][0]}-{data['required'][-1]}"
            met_str = "OK" if data['met'] else "NG"
            print(f"  {col:<12} | {value_str:>12} | {q_str:>4} | {req_str:>8} | {met_str}")

    # シグナル判定
    print("\n【シグナル判定】")

    if signal_main:
        print("  " + "=" * 40)
        print("  ★★★ メインシグナル発生中！ ★★★")
        print("  → OI低 × FR低中 × Fee中")
        print("  → Long推奨 (TP+10% / SL-5%)")
        print("  " + "=" * 40)
    elif signal_sub:
        print("  " + "-" * 40)
        print("  ☆ サブシグナル発生中")
        print("  → OI低 × FR低中 (Fee条件なし)")
        print("  " + "-" * 40)
    else:
        print("  シグナルなし（様子見）")

    print()

# === メイン ===
def run_once():
    """1回実行"""
    print("データ取得中...")

    # 過去データから5分位を計算
    quintiles = load_historical_quintiles()
    if not quintiles:
        return False, False

    # 現在値を取得
    try:
        hype_data = get_oi_and_volume()
        spot_price = get_spot_price()
    except Exception as e:
        print(f"API Error: {e}")
        return False, False

    current = {
        "oi_usd": hype_data["oi_usd"],
        "funding_rate": hype_data["funding_rate"],
        "volume_usd": hype_data["volume_usd"],
        "spot_price": spot_price,
    }

    # リアルタイムでMA7を計算
    print("リアルタイムMA7を計算中...")
    fr_ma7 = get_funding_ma7_realtime()
    fee_ma7 = get_fee_ma7_realtime()

    if fr_ma7 is not None:
        current['fr_ma7'] = fr_ma7
        print(f"  FR MA7: {fr_ma7:.6f} (リアルタイム)")
    if fee_ma7 is not None:
        current['fee_ma7'] = fee_ma7
        print(f"  Fee MA7: ${fee_ma7/1e6:.2f}M (リアルタイム)")

    # シグナル判定
    signal_main, results_main = check_signal(current, quintiles, "main")
    signal_sub, _ = check_signal(current, quintiles, "sub")

    # 表示
    display_status(current, quintiles, signal_main, signal_sub, results_main)

    return signal_main, signal_sub

def main():
    parser = argparse.ArgumentParser(description="HYPE シグナル監視")
    parser.add_argument("--loop", action="store_true", help="1時間ごとにループ実行")
    parser.add_argument("--check", action="store_true", help="シグナル判定のみ（exit code返却）")
    parser.add_argument("--interval", type=int, default=3600, help="ループ間隔（秒）")
    args = parser.parse_args()

    if args.loop:
        print("ループモードで起動（Ctrl+Cで終了）")
        while True:
            try:
                run_once()
                print(f"次回チェック: {args.interval}秒後")
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\n終了")
                break
    elif args.check:
        signal_main, signal_sub = run_once()
        # シグナル発生時は exit code 0
        exit(0 if signal_main else 1)
    else:
        run_once()

if __name__ == "__main__":
    main()
