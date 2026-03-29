#!/usr/bin/env python3
"""
$HYPE Master Analyzer
=====================
Hyperliquid公式APIから$HYPEの主要指標をリアルタイム取得・分析するツール。

取得指標:
  1. Fee (推定手数料収益)
  2. USDC入出金フロー (ブリッジTVL)
  3. FR (ファンディングレート - 現在値・予測・履歴)
  4. 出来高 (Perps + Spot)
  5. 現物TWAPの動向
  6. OI (建玉)
  7. 清算データ

データソース:
  - Hyperliquid公式API: https://api.hyperliquid.xyz/info
  - 補助: data.asxn.xyz / hyperdash.com / hypurrscan.io / DefiLlama / CoinGlass

使い方:
  pip install requests tabulate --break-system-packages
  python hype_analyzer.py
  python hype_analyzer.py --csv          # CSV出力
  python hype_analyzer.py --history 7    # FR履歴7日分
  python hype_analyzer.py --twap 24      # TWAP 24時間
  python hype_analyzer.py --all          # 全データ取得
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import requests
except ImportError:
    print("Error: requests が必要です。以下を実行してください:")
    print("  pip install requests --break-system-packages")
    sys.exit(1)

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

# ============================================================
# Constants
# ============================================================
API_URL = "https://api.hyperliquid.xyz/info"
HEADERS = {"Content-Type": "application/json"}
HYPE_COIN = "HYPE"
HYPE_SPOT_INDEX = "@107"  # HYPE/USDC spot pair

# Fee tiers (maker/taker) - Hyperliquid fee schedule
# https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
# Taker: 0.035%, Maker: 0.01% (default tier)
DEFAULT_TAKER_FEE_BPS = 3.5   # 0.035%
DEFAULT_MAKER_FEE_BPS = 1.0   # 0.01%

# ============================================================
# Colors for terminal output
# ============================================================
class C:
    """ANSI color codes."""
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"

def colored(text: str, color: str) -> str:
    return f"{color}{text}{C.END}"

# ============================================================
# API Client
# ============================================================
class HyperliquidAPI:
    """Hyperliquid Info API client."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _post(self, payload: dict) -> Any:
        """POST request to Hyperliquid info endpoint."""
        try:
            resp = self.session.post(API_URL, json=payload, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(colored(f"  API Error: {e}", C.RED))
            return None

    # --- Perpetuals ---

    def get_meta_and_asset_ctxs(self) -> tuple[list, list] | None:
        """Get all perp metadata + asset contexts (OI, funding, volume, etc.)."""
        data = self._post({"type": "metaAndAssetCtxs"})
        if data and len(data) == 2:
            return data[0]["universe"], data[1]
        return None

    def get_funding_history(self, coin: str, start_time: int, end_time: int | None = None) -> list | None:
        """Get historical funding rates for a coin."""
        payload = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_time,
        }
        if end_time:
            payload["endTime"] = end_time
        return self._post(payload)

    def get_predicted_fundings(self) -> list | None:
        """Get predicted funding rates for all coins."""
        return self._post({"type": "predictedFundings"})

    # --- Spot ---

    def get_spot_meta_and_asset_ctxs(self) -> tuple[list, list] | None:
        """Get all spot metadata + asset contexts."""
        data = self._post({"type": "spotMetaAndAssetCtxs"})
        if data and len(data) == 2:
            return data[0], data[1]
        return None

    # --- Candles ---

    def get_candles(self, coin: str, interval: str, start_time: int, end_time: int | None = None) -> list | None:
        """Get candlestick data. interval: 1m,5m,15m,1h,4h,1d etc."""
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
            }
        }
        if end_time:
            payload["req"]["endTime"] = end_time
        return self._post(payload)

    # --- Order Book ---

    def get_l2_book(self, coin: str) -> dict | None:
        """Get L2 order book (max 20 levels per side)."""
        return self._post({"type": "l2Book", "coin": coin})


# ============================================================
# Analysis Functions
# ============================================================

def find_hype_index(universe: list) -> int | None:
    """Find HYPE's index in the perpetuals universe."""
    for i, asset in enumerate(universe):
        if asset.get("name") == HYPE_COIN:
            return i
    return None


def calc_twap(candles: list) -> dict:
    """Calculate TWAP and related stats from candle data."""
    if not candles:
        return {}

    prices = []
    volumes = []
    vwap_num = 0.0
    vwap_den = 0.0

    for c in candles:
        o, h, l, close = float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])
        vol = float(c["v"])
        typical = (h + l + close) / 3.0
        prices.append(close)
        volumes.append(vol)
        vwap_num += typical * vol
        vwap_den += vol

    twap = sum(prices) / len(prices) if prices else 0
    vwap = vwap_num / vwap_den if vwap_den > 0 else 0
    high = max(float(c["h"]) for c in candles)
    low = min(float(c["l"]) for c in candles)
    first_close = float(candles[0]["c"])
    last_close = float(candles[-1]["c"])
    change_pct = ((last_close - first_close) / first_close * 100) if first_close else 0

    return {
        "twap": twap,
        "vwap": vwap,
        "high": high,
        "low": low,
        "open": float(candles[0]["o"]),
        "close": last_close,
        "change_pct": change_pct,
        "total_volume": sum(volumes),
        "candle_count": len(candles),
        "start": datetime.fromtimestamp(candles[0]["t"] / 1000, tz=timezone.utc),
        "end": datetime.fromtimestamp(candles[-1]["t"] / 1000, tz=timezone.utc),
    }


def estimate_fee_revenue(volume_24h: float) -> dict:
    """Estimate daily fee revenue from 24h volume.

    Hyperliquid fee structure:
    - Taker: 0.035% (3.5 bps)
    - Maker: 0.01%  (1.0 bps)
    - Assumption: ~60% taker / ~40% maker volume split
    """
    taker_ratio = 0.60
    maker_ratio = 0.40
    taker_fee = volume_24h * taker_ratio * (DEFAULT_TAKER_FEE_BPS / 10000)
    maker_fee = volume_24h * maker_ratio * (DEFAULT_MAKER_FEE_BPS / 10000)
    total = taker_fee + maker_fee
    return {
        "estimated_daily_fee": total,
        "estimated_annual_fee": total * 365,
        "taker_portion": taker_fee,
        "maker_portion": maker_fee,
        "volume_24h": volume_24h,
    }


def format_usd(val: float, decimals: int = 2) -> str:
    """Format a number as USD string."""
    if abs(val) >= 1_000_000_000:
        return f"${val / 1_000_000_000:,.{decimals}f}B"
    elif abs(val) >= 1_000_000:
        return f"${val / 1_000_000:,.{decimals}f}M"
    elif abs(val) >= 1_000:
        return f"${val / 1_000:,.{decimals}f}K"
    else:
        return f"${val:,.{decimals}f}"


def format_pct(val: float, decimals: int = 4) -> str:
    """Format as percentage."""
    return f"{val * 100:.{decimals}f}%"


def pct_color(val: float) -> str:
    """Color a percentage green/red."""
    s = format_pct(val)
    return colored(s, C.GREEN if val >= 0 else C.RED)


def print_section(title: str):
    """Print a section header."""
    width = 60
    print()
    print(colored("=" * width, C.DIM))
    print(colored(f"  {title}", C.BOLD + C.CYAN))
    print(colored("=" * width, C.DIM))


def simple_table(rows: list[list], headers: list[str] | None = None):
    """Print a table using tabulate or fallback."""
    if HAS_TABULATE:
        print(tabulate(rows, headers=headers or [], tablefmt="simple_grid", stralign="right"))
    else:
        if headers:
            print("  ".join(f"{h:>20}" for h in headers))
            print("  ".join("-" * 20 for _ in headers))
        for row in rows:
            print("  ".join(f"{str(c):>20}" for c in row))


# ============================================================
# Main Dashboard
# ============================================================

def run_dashboard(args):
    api = HyperliquidAPI()
    now_ms = int(time.time() * 1000)
    csv_rows = []

    print()
    print(colored("╔══════════════════════════════════════════════════════════╗", C.BOLD + C.YELLOW))
    print(colored("║          $HYPE Master Analyzer  v1.0                    ║", C.BOLD + C.YELLOW))
    print(colored("║          Hyperliquid Protocol Analytics                 ║", C.BOLD + C.YELLOW))
    print(colored("╚══════════════════════════════════════════════════════════╝", C.BOLD + C.YELLOW))
    print(colored(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", C.DIM))

    # -------------------------------------------------------
    # 1. Perp Meta & Asset Contexts
    # -------------------------------------------------------
    print_section("1. Perpetuals Market Data (HYPE-USD)")
    print("  Fetching metaAndAssetCtxs...")
    result = api.get_meta_and_asset_ctxs()

    hype_perp = None
    hype_meta = None
    all_perp_volume = 0.0

    if result:
        universe, ctxs = result
        idx = find_hype_index(universe)
        if idx is not None:
            hype_meta = universe[idx]
            hype_perp = ctxs[idx]

        # Calculate total exchange volume for fee estimation
        for ctx in ctxs:
            vol = float(ctx.get("dayNtlVlm", 0))
            all_perp_volume += vol

    if hype_perp:
        mark = float(hype_perp.get("markPx", 0))
        funding = float(hype_perp.get("funding", 0))
        oi = float(hype_perp.get("openInterest", 0))
        day_vol = float(hype_perp.get("dayNtlVlm", 0))
        prev_day_vol = float(hype_perp.get("prevDayPx", 0))
        oracle = float(hype_perp.get("oraclePx", 0))
        premium = float(hype_perp.get("premium", 0))

        rows = [
            ["Mark Price", f"${mark:,.4f}"],
            ["Oracle Price", f"${oracle:,.4f}"],
            ["Premium", format_pct(premium, 4)],
            ["Current FR (hourly)", pct_color(funding)],
            ["Current FR (annualized)", pct_color(funding * 24 * 365)],
            ["Open Interest", f"{oi:,.2f} HYPE"],
            ["OI (USD)", format_usd(oi * mark)],
            ["24h Volume (USD)", format_usd(day_vol)],
        ]
        simple_table(rows, ["Metric", "Value"])

        csv_rows.append({"section": "perp", "metric": "mark_price", "value": mark})
        csv_rows.append({"section": "perp", "metric": "oracle_price", "value": oracle})
        csv_rows.append({"section": "perp", "metric": "funding_rate_hourly", "value": funding})
        csv_rows.append({"section": "perp", "metric": "funding_rate_annual", "value": funding * 24 * 365})
        csv_rows.append({"section": "perp", "metric": "open_interest_hype", "value": oi})
        csv_rows.append({"section": "perp", "metric": "open_interest_usd", "value": oi * mark})
        csv_rows.append({"section": "perp", "metric": "volume_24h_usd", "value": day_vol})
    else:
        print(colored("  HYPE perp data not found.", C.RED))

    # -------------------------------------------------------
    # 2. Fee Revenue Estimation
    # -------------------------------------------------------
    print_section("2. Fee Revenue (Protocol推定)")
    if all_perp_volume > 0:
        fee = estimate_fee_revenue(all_perp_volume)
        hype_fee = estimate_fee_revenue(float(hype_perp.get("dayNtlVlm", 0))) if hype_perp else None

        rows = [
            ["Exchange Total 24h Volume", format_usd(all_perp_volume)],
            ["Exchange Est. Daily Fee", format_usd(fee["estimated_daily_fee"])],
            ["Exchange Est. Annual Fee", format_usd(fee["estimated_annual_fee"])],
            ["", ""],
            ["HYPE Perp 24h Volume", format_usd(hype_fee["volume_24h"]) if hype_fee else "N/A"],
            ["HYPE Perp Est. Daily Fee", format_usd(hype_fee["estimated_daily_fee"]) if hype_fee else "N/A"],
        ]
        simple_table(rows, ["Metric", "Value"])
        print(colored("  ※ Taker 0.035% / Maker 0.01% / 60:40 split で推定", C.DIM))
        print(colored("  ※ 実際のFee: https://hypurrscan.io/dashboard / https://data.asxn.xyz/", C.DIM))

        csv_rows.append({"section": "fee", "metric": "exchange_volume_24h", "value": all_perp_volume})
        csv_rows.append({"section": "fee", "metric": "exchange_est_daily_fee", "value": fee["estimated_daily_fee"]})
        csv_rows.append({"section": "fee", "metric": "exchange_est_annual_fee", "value": fee["estimated_annual_fee"]})
    else:
        print(colored("  Volume data not available for fee estimation.", C.RED))

    # -------------------------------------------------------
    # 3. Funding Rate (Predicted + History)
    # -------------------------------------------------------
    print_section("3. Funding Rate (FR)")

    # Predicted funding
    print("  Fetching predicted fundings...")
    predicted = api.get_predicted_fundings()
    if predicted:
        for item in predicted:
            # predicted format: [[coin, predicted_fr, premium], ...]
            if isinstance(item, list) and len(item) >= 2:
                coin_name = item[0]
                if coin_name == HYPE_COIN:
                    pred_data = item[1]
                    if isinstance(pred_data, dict):
                        pred_fr = float(pred_data.get("fundingRate", 0))
                        pred_premium = float(pred_data.get("premium", 0))
                    else:
                        pred_fr = float(pred_data) if pred_data else 0
                        pred_premium = 0
                    print(f"  Predicted Next FR: {pct_color(pred_fr)} (hourly)")
                    print(f"  Predicted Premium: {format_pct(pred_premium, 4)}")
                    csv_rows.append({"section": "fr", "metric": "predicted_fr_hourly", "value": pred_fr})
                    break

    # FR History
    history_days = args.history if args.history else (7 if args.all else 1)
    start_ms = now_ms - (history_days * 24 * 60 * 60 * 1000)
    print(f"  Fetching FR history ({history_days}d)...")
    fr_history = api.get_funding_history(HYPE_COIN, start_ms, now_ms)

    if fr_history and len(fr_history) > 0:
        rates = [float(f["fundingRate"]) for f in fr_history]
        avg_fr = sum(rates) / len(rates)
        max_fr = max(rates)
        min_fr = min(rates)
        latest_fr = rates[-1]
        positive_count = sum(1 for r in rates if r > 0)
        negative_count = sum(1 for r in rates if r < 0)

        rows = [
            [f"FR History ({history_days}d)", f"{len(rates)} records"],
            ["Latest FR", pct_color(latest_fr)],
            ["Average FR (hourly)", pct_color(avg_fr)],
            ["Average FR (annualized)", pct_color(avg_fr * 24 * 365)],
            ["Max FR", pct_color(max_fr)],
            ["Min FR", pct_color(min_fr)],
            ["Positive / Negative", f"{positive_count} / {negative_count}"],
            ["Positive Rate", f"{positive_count / len(rates) * 100:.1f}%"],
        ]
        simple_table(rows, ["Metric", "Value"])

        csv_rows.append({"section": "fr", "metric": "avg_fr_hourly", "value": avg_fr})
        csv_rows.append({"section": "fr", "metric": "avg_fr_annual", "value": avg_fr * 24 * 365})
        csv_rows.append({"section": "fr", "metric": "max_fr", "value": max_fr})
        csv_rows.append({"section": "fr", "metric": "min_fr", "value": min_fr})
        csv_rows.append({"section": "fr", "metric": "positive_ratio", "value": positive_count / len(rates)})

        # Show last 12 FR entries
        print()
        print(colored("  直近12件のFR:", C.BOLD))
        recent = fr_history[-12:]
        fr_rows = []
        for f in recent:
            ts = datetime.fromtimestamp(f["time"] / 1000, tz=timezone.utc)
            fr_val = float(f["fundingRate"])
            fr_rows.append([
                ts.strftime("%m/%d %H:%M"),
                pct_color(fr_val),
                format_pct(fr_val * 24 * 365, 2) + " (ann.)",
            ])
        simple_table(fr_rows, ["Time (UTC)", "FR (hourly)", "Annualized"])
    else:
        print(colored("  FR history not available.", C.YELLOW))

    # -------------------------------------------------------
    # 4. Spot Market Data & TWAP
    # -------------------------------------------------------
    print_section("4. Spot Market & TWAP")

    # Spot meta
    print("  Fetching spot meta...")
    spot_result = api.get_spot_meta_and_asset_ctxs()
    if spot_result:
        spot_meta, spot_ctxs = spot_result
        tokens = spot_meta.get("tokens", [])
        universe = spot_meta.get("universe", [])

        # Find HYPE spot pair
        hype_spot_ctx = None
        for i, pair in enumerate(universe):
            pair_tokens = pair.get("tokens", [])
            pair_name = pair.get("name", "")
            # HYPE token index is 150, USDC is 0
            if pair_name == "HYPE/USDC" or (len(pair_tokens) == 2 and 150 in [pair_tokens[0], pair_tokens[1]]):
                if i < len(spot_ctxs):
                    hype_spot_ctx = spot_ctxs[i]
                break

        if hype_spot_ctx:
            spot_price = float(hype_spot_ctx.get("midPx", 0) or hype_spot_ctx.get("markPx", 0))
            spot_vol = float(hype_spot_ctx.get("dayNtlVlm", 0))
            circ_supply = float(hype_spot_ctx.get("circulatingSupply", 0))

            rows = [
                ["Spot Mid Price", f"${spot_price:,.4f}"],
                ["Spot 24h Volume", format_usd(spot_vol)],
                ["Circulating Supply", f"{circ_supply:,.0f} HYPE" if circ_supply else "N/A"],
                ["Market Cap", format_usd(spot_price * circ_supply) if circ_supply else "N/A"],
            ]
            simple_table(rows, ["Metric", "Value"])

            csv_rows.append({"section": "spot", "metric": "spot_price", "value": spot_price})
            csv_rows.append({"section": "spot", "metric": "spot_volume_24h", "value": spot_vol})
            csv_rows.append({"section": "spot", "metric": "circulating_supply", "value": circ_supply})
        else:
            print(colored("  HYPE spot pair not found in API response.", C.YELLOW))

    # TWAP calculation
    twap_hours = args.twap if args.twap else (24 if args.all else 4)
    start_ms_twap = now_ms - (twap_hours * 60 * 60 * 1000)

    # Determine interval based on hours
    if twap_hours <= 4:
        interval = "5m"
    elif twap_hours <= 24:
        interval = "15m"
    elif twap_hours <= 72:
        interval = "1h"
    else:
        interval = "4h"

    print(f"  Fetching spot candles ({twap_hours}h, interval={interval})...")
    # Use HYPE spot index @107 for spot candles
    candles = api.get_candles(HYPE_SPOT_INDEX, interval, start_ms_twap, now_ms)

    # Fallback: try perp candles if spot fails
    if not candles or len(candles) == 0:
        print("  Spot candles not available, trying perp candles...")
        candles = api.get_candles(HYPE_COIN, interval, start_ms_twap, now_ms)

    if candles and len(candles) > 0:
        twap = calc_twap(candles)
        print()
        print(colored(f"  TWAP Analysis ({twap_hours}h):", C.BOLD))
        rows = [
            ["Period", f"{twap['start'].strftime('%m/%d %H:%M')} - {twap['end'].strftime('%m/%d %H:%M')} UTC"],
            ["TWAP (Simple)", f"${twap['twap']:,.4f}"],
            ["VWAP", f"${twap['vwap']:,.4f}"],
            ["Open", f"${twap['open']:,.4f}"],
            ["Close", f"${twap['close']:,.4f}"],
            ["High", f"${twap['high']:,.4f}"],
            ["Low", f"${twap['low']:,.4f}"],
            ["Change", colored(f"{twap['change_pct']:+.2f}%", C.GREEN if twap['change_pct'] >= 0 else C.RED)],
            ["Total Volume", format_usd(twap['total_volume'])],
            ["Candles", str(twap['candle_count'])],
        ]
        simple_table(rows, ["Metric", "Value"])

        csv_rows.append({"section": "twap", "metric": f"twap_{twap_hours}h", "value": twap['twap']})
        csv_rows.append({"section": "twap", "metric": f"vwap_{twap_hours}h", "value": twap['vwap']})
        csv_rows.append({"section": "twap", "metric": f"change_pct_{twap_hours}h", "value": twap['change_pct']})
    else:
        print(colored("  Candle data not available.", C.YELLOW))

    # -------------------------------------------------------
    # 5. USDC Flow Info
    # -------------------------------------------------------
    print_section("5. USDC入出金フロー")
    print(colored("  ※ ブリッジの入出金データはオンチェーンデータが必要です。", C.YELLOW))
    print(colored("    以下のダッシュボードで確認できます:", C.DIM))
    print()
    refs = [
        ("Dune - USDC Deposit", "https://dune.com/kouei/hyperliquid-usdc-deposit"),
        ("Dune - Bridge TVL", "https://dune.com/entropy_advisors/hyperliquid-bridge-tvl"),
        ("DefiLlama - Bridge", "https://defillama.com/bridge/hyperliquid"),
        ("ASXN Dashboard", "https://data.asxn.xyz/"),
        ("HypurrScan", "https://hypurrscan.io/dashboard"),
    ]
    for name, url in refs:
        print(f"    {colored(name, C.CYAN)}: {url}")

    # -------------------------------------------------------
    # 6. Summary & Signals
    # -------------------------------------------------------
    print_section("6. Summary & Signal Check")

    if hype_perp:
        mark = float(hype_perp.get("markPx", 0))
        funding_val = float(hype_perp.get("funding", 0))
        oi_val = float(hype_perp.get("openInterest", 0))
        vol_val = float(hype_perp.get("dayNtlVlm", 0))

        signals = []

        # FR signal
        if funding_val > 0.0001:
            signals.append(("FR", "HIGH LONG", "ロングが多い → ショートに有利な環境", C.RED))
        elif funding_val < -0.0001:
            signals.append(("FR", "HIGH SHORT", "ショートが多い → ロングに有利な環境", C.GREEN))
        else:
            signals.append(("FR", "NEUTRAL", "ファンディングはほぼニュートラル", C.YELLOW))

        # OI / Volume ratio
        oi_usd = oi_val * mark
        if vol_val > 0:
            oi_vol_ratio = oi_usd / vol_val
            if oi_vol_ratio > 0.5:
                signals.append(("OI/Vol", "HIGH", f"OI/Vol = {oi_vol_ratio:.2f} → ポジション溜まり気味", C.RED))
            elif oi_vol_ratio < 0.1:
                signals.append(("OI/Vol", "LOW", f"OI/Vol = {oi_vol_ratio:.2f} → 回転が速い", C.GREEN))
            else:
                signals.append(("OI/Vol", "NORMAL", f"OI/Vol = {oi_vol_ratio:.2f}", C.YELLOW))

        # Premium signal
        premium_val = float(hype_perp.get("premium", 0))
        if abs(premium_val) > 0.005:
            direction = "上" if premium_val > 0 else "下"
            signals.append(("Premium", f"乖離{direction}", f"Premium = {format_pct(premium_val)} → Perp価格が現物より{direction}", C.YELLOW))

        print()
        for sig_name, sig_status, sig_desc, sig_color in signals:
            print(f"  [{colored(sig_name, C.BOLD)}] {colored(sig_status, sig_color)} - {sig_desc}")

    # -------------------------------------------------------
    # External Links
    # -------------------------------------------------------
    print_section("7. 外部リンク集")
    links = [
        ("Hyperliquid App", "https://app.hyperliquid.xyz/trade/HYPE"),
        ("HyperDash", "https://hyperdash.com/?chart1=HYPE"),
        ("HypurrScan Dashboard", "https://hypurrscan.io/dashboard"),
        ("ASXN Data", "https://data.asxn.xyz/"),
        ("CoinGlass - HYPE Futures", "https://www.coinglass.com/currencies/HYPE/futures"),
        ("Coinalyze - FR", "https://coinalyze.net/hyperliquid/funding-rate/"),
        ("DefiLlama - Fees", "https://defillama.com/protocol/fees/hyperliquid"),
    ]
    for name, url in links:
        print(f"  {colored(name, C.CYAN)}: {url}")

    # -------------------------------------------------------
    # CSV Export
    # -------------------------------------------------------
    if args.csv:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"hype_data_{ts}.csv"
        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["section", "metric", "value", "timestamp"])
            writer.writeheader()
            for row in csv_rows:
                row["timestamp"] = datetime.now(timezone.utc).isoformat()
                writer.writerow(row)
        print()
        print(colored(f"  CSV exported: {filename}", C.GREEN))

    # FR history CSV
    if args.csv and fr_history:
        fr_filename = f"hype_fr_history_{ts}.csv"
        with open(fr_filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["time", "coin", "fundingRate", "premium"])
            writer.writeheader()
            for entry in fr_history:
                writer.writerow({
                    "time": datetime.fromtimestamp(entry["time"] / 1000, tz=timezone.utc).isoformat(),
                    "coin": entry.get("coin", HYPE_COIN),
                    "fundingRate": entry.get("fundingRate", ""),
                    "premium": entry.get("premium", ""),
                })
        print(colored(f"  FR History CSV exported: {fr_filename}", C.GREEN))

    # Candle CSV
    if args.csv and candles:
        candle_filename = f"hype_candles_{ts}.csv"
        with open(candle_filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            for c in candles:
                writer.writerow({
                    "time": datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc).isoformat(),
                    "open": c["o"],
                    "high": c["h"],
                    "low": c["l"],
                    "close": c["c"],
                    "volume": c["v"],
                })
        print(colored(f"  Candles CSV exported: {candle_filename}", C.GREEN))

    print()
    print(colored("  Done! いっぱい手数料落としてください 🚀", C.BOLD + C.GREEN))
    print()


# ============================================================
# Entry point
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="$HYPE Master Analyzer - Hyperliquid Protocol Analytics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使い方:
  python hype_analyzer.py              基本ダッシュボード
  python hype_analyzer.py --csv        CSV出力付き
  python hype_analyzer.py --history 7  FR履歴7日分
  python hype_analyzer.py --twap 24    TWAP 24時間
  python hype_analyzer.py --all        全データ取得 (FR 7d + TWAP 24h)
        """
    )
    parser.add_argument("--csv", action="store_true", help="CSVファイルにエクスポート")
    parser.add_argument("--history", type=int, default=0, help="FR履歴の日数 (default: 1)")
    parser.add_argument("--twap", type=int, default=0, help="TWAP計算の時間数 (default: 4)")
    parser.add_argument("--all", action="store_true", help="全データ取得 (FR 7d + TWAP 24h)")

    args = parser.parse_args()
    run_dashboard(args)


if __name__ == "__main__":
    main()
