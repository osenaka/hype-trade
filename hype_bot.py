#!/usr/bin/env python3
"""
HYPE 自動取引Bot
================
シグナル検出 → 自動でLongエントリー → TP/SLで決済

使い方:
  1. .envファイルに秘密鍵を設定
  2. python hype_bot.py --dry-run  # テスト実行（注文しない）
  3. python hype_bot.py            # 本番実行

必要パッケージ:
  pip install hyperliquid-python-sdk python-dotenv
"""

import argparse
import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv  # quintiles用

# === 設定 ===
CONFIG = {
    # 取引設定
    "symbol": "HYPE",
    "position_size_usd": 100,      # 1回のポジションサイズ（USD）
    "leverage": 3,                  # レバレッジ
    "tp_percent": 10.0,            # Take Profit %
    "sl_percent": 5.0,             # Stop Loss %
    "max_positions": 1,             # 最大同時ポジション数

    # 監視設定
    "check_interval": 3600,         # チェック間隔（秒）= 1時間
    "data_dir": Path(__file__).parent / "hype_data",

    # ログ設定
    "log_file": Path(__file__).parent / "bot_log.txt",
}

# === ロギング設定 ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(CONFIG["log_file"]),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# === Hyperliquid SDK ===
try:
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    HAS_SDK = True
except ImportError:
    HAS_SDK = False
    logger.warning("hyperliquid-python-sdk が見つかりません")
    logger.warning("pip install hyperliquid-python-sdk でインストールしてください")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# === 5分位計算 ===
def load_quintiles():
    """過去データから5分位を計算"""
    daily_features = CONFIG["data_dir"] / "daily_features.csv"
    if not daily_features.exists():
        logger.error(f"{daily_features} が見つかりません")
        return None

    with open(daily_features) as f:
        rows = list(csv.DictReader(f))

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
        }
    return quintiles

def get_quintile(value, q_data):
    """値がどの5分位か"""
    if value < q_data['q1']:
        return 1
    elif value < q_data['q2']:
        return 2
    elif value < q_data['q3']:
        return 3
    elif value < q_data['q4']:
        return 4
    return 5

# === リアルタイムデータ取得 ===
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
HYPURRSCAN_API = "https://api.hypurrscan.io/fees"

def api_post(url, payload):
    """POST request to API"""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def get_funding_ma7():
    """過去7日間のFunding Rate MA7をリアルタイム計算"""
    try:
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time = end_time - (7 * 24 * 60 * 60 * 1000)  # 7 days ago

        result = api_post(HYPERLIQUID_API, {
            "type": "fundingHistory",
            "coin": CONFIG["symbol"],
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

        # Calculate MA7 (average of daily sums)
        if len(daily_fr) >= 7:
            sorted_dates = sorted(daily_fr.keys(), reverse=True)[:7]
            ma7 = sum(daily_fr[d] for d in sorted_dates) / 7
            return ma7
        elif daily_fr:
            # If less than 7 days, use what we have
            return sum(daily_fr.values()) / len(daily_fr)
        return None
    except Exception as e:
        logger.error(f"FR MA7取得エラー: {e}")
        return None

def get_fee_ma7():
    """過去7日間のFee MA7をリアルタイム計算"""
    try:
        req = urllib.request.Request(
            HYPURRSCAN_API,
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
        logger.error(f"Fee MA7取得エラー: {e}")
        return None

# === Bot クラス ===
class HypeBot:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.quintiles = load_quintiles()

        if not HAS_SDK:
            raise RuntimeError("SDK not installed")

        # API接続
        self.info = Info(constants.MAINNET_API_URL, skip_ws=True)

        # 秘密鍵（環境変数から）
        self.private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
        self.wallet_address = os.getenv("HYPERLIQUID_WALLET_ADDRESS")

        if not dry_run and not self.private_key:
            raise ValueError("HYPERLIQUID_PRIVATE_KEY が設定されていません")

        if not dry_run:
            self.exchange = Exchange(
                self.private_key,
                constants.MAINNET_API_URL,
                vault_address=None,
                account_address=self.wallet_address
            )
        else:
            self.exchange = None

        logger.info(f"Bot初期化完了 (dry_run={dry_run})")

    def get_current_values(self):
        """現在の指標値をリアルタイム取得"""
        try:
            # メタデータ取得
            meta = self.info.meta_and_asset_ctxs()

            # HYPEのインデックスを探す
            hype_idx = None
            for i, asset in enumerate(meta[0]["universe"]):
                if asset["name"] == CONFIG["symbol"]:
                    hype_idx = i
                    break

            if hype_idx is None:
                logger.error("HYPEが見つかりません")
                return None

            ctx = meta[1][hype_idx]
            mark_price = float(ctx["markPx"])
            oi = float(ctx["openInterest"]) * mark_price
            funding = float(ctx["funding"])

            # リアルタイムでMA7を計算
            logger.info("リアルタイムMA7を計算中...")
            fr_ma7 = get_funding_ma7()
            fee_ma7 = get_fee_ma7()

            if fr_ma7 is not None:
                logger.info(f"  FR MA7: {fr_ma7:.6f} (リアルタイム)")
            if fee_ma7 is not None:
                logger.info(f"  Fee MA7: ${fee_ma7/1e6:.2f}M (リアルタイム)")

            return {
                "oi_usd": oi,
                "fr_ma7": fr_ma7,
                "fee_ma7": fee_ma7,
                "mark_price": mark_price,
                "funding_rate": funding,
            }
        except Exception as e:
            logger.error(f"データ取得エラー: {e}")
            return None

    def check_signal(self, values):
        """シグナル判定"""
        if not self.quintiles or not values:
            return False, {}

        # メイン条件: OI低(Q1-2) × FR低中(Q1-3) × Fee中(Q2-3)
        conditions = {
            "oi_usd": [1, 2],
            "fr_ma7": [1, 2, 3],
            "fee_ma7": [2, 3],
        }

        results = {}
        all_met = True

        for col, required in conditions.items():
            if col not in self.quintiles or values.get(col) is None:
                results[col] = {"met": False, "reason": "データなし"}
                all_met = False
                continue

            q = get_quintile(values[col], self.quintiles[col])
            met = q in required
            results[col] = {"quintile": q, "required": required, "met": met}
            if not met:
                all_met = False

        return all_met, results

    def get_position(self):
        """現在のポジション取得"""
        try:
            if self.wallet_address:
                state = self.info.user_state(self.wallet_address)
            else:
                return None

            for pos in state.get("assetPositions", []):
                if pos["position"]["coin"] == CONFIG["symbol"]:
                    size = float(pos["position"]["szi"])
                    entry_px = float(pos["position"]["entryPx"]) if pos["position"]["entryPx"] else 0
                    return {
                        "size": size,
                        "entry_price": entry_px,
                        "side": "long" if size > 0 else "short" if size < 0 else None,
                    }
            return {"size": 0, "entry_price": 0, "side": None}
        except Exception as e:
            logger.error(f"ポジション取得エラー: {e}")
            return None

    def place_order(self, side, size, price=None, order_type="market"):
        """注文を出す"""
        if self.dry_run:
            logger.info(f"[DRY-RUN] 注文: {side} {size} {CONFIG['symbol']} @ {price or 'market'}")
            return {"status": "dry-run", "side": side, "size": size}

        try:
            is_buy = side == "buy"

            # マーケット注文
            result = self.exchange.market_open(
                CONFIG["symbol"],
                is_buy,
                size,
                None,  # slippage
            )
            logger.info(f"注文結果: {result}")
            return result
        except Exception as e:
            logger.error(f"注文エラー: {e}")
            return None

    def set_tp_sl(self, entry_price, side):
        """TP/SLを設定"""
        if side == "long":
            tp_price = entry_price * (1 + CONFIG["tp_percent"] / 100)
            sl_price = entry_price * (1 - CONFIG["sl_percent"] / 100)
        else:
            tp_price = entry_price * (1 - CONFIG["tp_percent"] / 100)
            sl_price = entry_price * (1 + CONFIG["sl_percent"] / 100)

        if self.dry_run:
            logger.info(f"[DRY-RUN] TP: ${tp_price:.2f}, SL: ${sl_price:.2f}")
            return True

        try:
            # TP注文
            self.exchange.order(
                CONFIG["symbol"],
                not (side == "long"),  # TP is opposite side
                0,  # close position
                tp_price,
                {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}},
                reduce_only=True
            )

            # SL注文
            self.exchange.order(
                CONFIG["symbol"],
                not (side == "long"),
                0,
                sl_price,
                {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}},
                reduce_only=True
            )

            logger.info(f"TP/SL設定完了: TP=${tp_price:.2f}, SL=${sl_price:.2f}")
            return True
        except Exception as e:
            logger.error(f"TP/SL設定エラー: {e}")
            return False

    def run_once(self):
        """1回の監視サイクル"""
        logger.info("=" * 50)
        logger.info("シグナルチェック開始")

        # 現在値取得
        values = self.get_current_values()
        if not values:
            logger.warning("データ取得失敗")
            return

        logger.info(f"OI: ${values['oi_usd']/1e6:.2f}M, FR MA7: {values['fr_ma7']:.4f}, Fee MA7: ${values['fee_ma7']/1e6:.2f}M")

        # シグナル判定
        signal, results = self.check_signal(values)

        for col, r in results.items():
            status = "OK" if r.get("met") else "NG"
            q = r.get("quintile", "?")
            logger.info(f"  {col}: Q{q} → {status}")

        if not signal:
            logger.info("シグナルなし")
            return

        logger.info("★ シグナル発生！")

        # ポジション確認
        pos = self.get_position()
        if pos and pos.get("side") == "long":
            logger.info("既にLongポジションあり、スキップ")
            return

        # エントリー
        price = values["mark_price"]
        size = CONFIG["position_size_usd"] / price

        logger.info(f"エントリー: Long {size:.4f} HYPE @ ${price:.2f}")
        result = self.place_order("buy", size)

        if result:
            self.set_tp_sl(price, "long")

    def run_loop(self):
        """ループ実行"""
        logger.info("Bot開始（Ctrl+Cで終了）")

        while True:
            try:
                self.run_once()
                logger.info(f"次回チェック: {CONFIG['check_interval']}秒後")
                time.sleep(CONFIG["check_interval"])
            except KeyboardInterrupt:
                logger.info("Bot終了")
                break
            except Exception as e:
                logger.error(f"エラー: {e}")
                time.sleep(60)

# === メイン ===
def main():
    parser = argparse.ArgumentParser(description="HYPE自動取引Bot")
    parser.add_argument("--dry-run", action="store_true", help="テスト実行（注文しない）")
    parser.add_argument("--once", action="store_true", help="1回だけ実行")
    args = parser.parse_args()

    if not HAS_SDK:
        print("エラー: pip install hyperliquid-python-sdk を実行してください")
        return

    try:
        bot = HypeBot(dry_run=args.dry_run)

        if args.once:
            bot.run_once()
        else:
            bot.run_loop()
    except Exception as e:
        logger.error(f"起動エラー: {e}")

if __name__ == "__main__":
    main()
