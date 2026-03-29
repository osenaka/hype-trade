#!/usr/bin/env python3
from __future__ import annotations
"""
$HYPE Backtest Engine v2
========================
HYPE 上場日（2024-12-05）から現在まで、全主要指標の日次データを取得し
各指標 vs 将来リターンのラグ相関を分析、バックテストを実行する。

データソース:
  ASXN API (api-hyperliquid.asxn.xyz) — 認証不要
    - Fee (Revenue/Buybacks)
    - OI (Open Interest 時系列)
    - 清算 (Daily Liquidations)
    - FR  (Daily sum of funding rate)
    - 出来高 (Daily Volume)
    - USDC残高 (Stablecoin Supply)
  Hyperliquid 公式 API
    - 現物 TWAP (Spot Candles → rolling TWAP)
    - Perp 4h 足 (バックテスト用価格)

使い方:
  pip install requests pandas numpy tabulate

  python hype_backtest.py --fetch          # データ取得
  python hype_backtest.py --correlate      # 指標 vs 価格 ラグ相関分析
  python hype_backtest.py --run            # バックテスト実行
  python hype_backtest.py --optimize       # パラメータ最適化
  python hype_backtest.py --fetch --correlate --run  # 全部まとめて
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from itertools import product as iterproduct

MISSING = []
try:
    import requests
except ImportError:
    MISSING.append("requests")
try:
    import pandas as pd
    import numpy as np
except ImportError:
    MISSING.append("pandas numpy")
try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

if MISSING:
    print(f"pip install {' '.join(MISSING)} --break-system-packages")
    sys.exit(1)

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR      = "hype_data"
DAILY_FILE    = os.path.join(DATA_DIR, "daily_features.csv")    # 全指標統合
CANDLE_4H     = os.path.join(DATA_DIR, "candles_4h.csv")        # バックテスト用価格
BEST_PARAMS   = os.path.join(DATA_DIR, "best_params.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ── API ──────────────────────────────────────────────────────────────────────
ASXN_BASE = "https://api-hyperliquid.asxn.xyz"
HL_BASE   = "https://api.hyperliquid.xyz/info"
HL_SPOT   = "@107"   # HYPE/USDC spot index
HYPE      = "HYPE"

# ── Colors ───────────────────────────────────────────────────────────────────
class C:
    BOLD = "\033[1m"; DIM = "\033[2m"; GREEN = "\033[92m"
    RED  = "\033[91m"; YELLOW = "\033[93m"; CYAN = "\033[96m"; END = "\033[0m"

def c(t, col): return f"{col}{t}{C.END}"

# ─────────────────────────────────────────────────────────────────────────────
# 1. DATA FETCH
# ─────────────────────────────────────────────────────────────────────────────

_asxn_session: "requests.Session | None" = None

def _get_asxn_session() -> "requests.Session":
    global _asxn_session
    if _asxn_session is None:
        _asxn_session = requests.Session()
        _asxn_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
            "Referer": "https://data.asxn.xyz/",
            "Origin": "https://data.asxn.xyz",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        })
        try:
            _asxn_session.get("https://data.asxn.xyz/", timeout=10)
        except Exception:
            pass
    return _asxn_session

def asxn_get(path: str, params: dict | None = None) -> dict | list | None:
    url = ASXN_BASE + path
    try:
        r = _get_asxn_session().get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(c(f"  ASXN GET {path} → {e}", C.RED))
        return None

def hl_post(payload: dict) -> dict | list | None:
    try:
        r = requests.post(HL_BASE, json=payload,
                          headers={"Content-Type": "application/json"}, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(c(f"  HL POST → {e}", C.RED))
        return None

def fetch_candles_hl(coin: str, interval: str, start_ms: int, end_ms: int) -> list:
    all_c = []
    cur = start_ms
    while cur < end_ms:
        data = hl_post({"type": "candleSnapshot",
                        "req": {"coin": coin, "interval": interval,
                                "startTime": cur, "endTime": end_ms}})
        if not data:
            break
        all_c.extend(data)
        if len(data) < 2:
            break
        last_t = data[-1]["t"]
        if last_t <= cur:
            break
        cur = last_t + 1
        time.sleep(0.15)
    return all_c


ASXN_CACHE  = os.path.join(DATA_DIR, "asxn_cache.json")
LLAMA_CACHE = os.path.join(DATA_DIR, "llama_cache.json")

def _load_asxn_cache() -> "dict | None":
    """hype_data/asxn_cache.json があれば読み込む。"""
    if os.path.exists(ASXN_CACHE):
        print(c(f"  → キャッシュファイルを使用: {ASXN_CACHE}", C.YELLOW))
        with open(ASXN_CACHE, "r") as f:
            return json.load(f)
    return None

def _frames_from_cache(cache: dict) -> dict:
    """asxn_cache.json から各指標のDataFrameを生成する。"""
    frames = {}

    # FR
    if cache.get("fr"):
        df = pd.DataFrame(cache["fr"])
        df["date"] = pd.to_datetime(df["time"]).dt.normalize()
        df = df.rename(columns={"sum_funding": "fr_daily_sum"})
        frames["fr"] = df.set_index("date")[["fr_daily_sum"]]
        print(c(f"    ✓ FR: {len(df)} 行 (cache)", C.GREEN))

    # OI
    if cache.get("oi"):
        df = pd.DataFrame(cache["oi"])
        df["date"] = pd.to_datetime(df["time"]).dt.normalize()
        df = df.rename(columns={"open_interest": "oi_usd"})
        frames["oi"] = df.set_index("date")[["oi_usd"]]
        print(c(f"    ✓ OI: {len(df)} 行 (cache)", C.GREEN))

    # Volume
    if cache.get("vol"):
        df = pd.DataFrame(cache["vol"])
        df["date"] = pd.to_datetime(df["time"]).dt.normalize()
        vol_col = next((c2 for c2 in df.columns if "volume" in c2.lower() or "vlm" in c2.lower()), None)
        if vol_col:
            df = df.rename(columns={vol_col: "volume_usd"})
            frames["vol"] = df.set_index("date")[["volume_usd"]]
            print(c(f"    ✓ Volume: {len(df)} 行 (cache)", C.GREEN))

    # Liquidations
    if cache.get("liq"):
        df = pd.DataFrame(cache["liq"])
        df["date"] = pd.to_datetime(df["time"]).dt.normalize()
        df = df.rename(columns={"daily_notional_liquidated": "liquidations_usd"})
        frames["liq"] = df.set_index("date")[["liquidations_usd"]]
        print(c(f"    ✓ 清算: {len(df)} 行 (cache)", C.GREEN))

    # Fee
    if cache.get("fee"):
        df = pd.DataFrame(cache["fee"])
        df["date"] = pd.to_datetime(df["date"])
        fee_col = next((c2 for c2 in df.columns if "total" in c2.lower()), "total")
        df = df.rename(columns={fee_col: "fee_total"})
        if "fee_total" in df.columns:
            frames["fee"] = df.set_index("date")[["fee_total"]]
            print(c(f"    ✓ Fee: {len(df)} 行 (cache)", C.GREEN))

    # USDC
    if cache.get("usdc"):
        df = pd.DataFrame(cache["usdc"])
        date_col = next((c2 for c2 in df.columns if "date" in c2.lower() or "time" in c2.lower()), None)
        supply_col = next((c2 for c2 in df.columns if "supply" in c2.lower() or "total" in c2.lower()), None)
        if date_col and supply_col:
            df["date"] = pd.to_datetime(df[date_col]).dt.normalize()
            df = df.rename(columns={supply_col: "usdc_supply"})
            frames["usdc"] = df.set_index("date")[["usdc_supply"]]
            print(c(f"    ✓ USDC: {len(df)} 行 (cache)", C.GREEN))

    return frames


def _load_llama_cache() -> "pd.DataFrame | None":
    """hype_data/llama_cache.json からBridged USDCデータを読み込む。

    返り値: date をインデックスとする DataFrame（列: bridged_usdc）
    データソース: DefiLlama hyperliquid-bridge (Arbitrum側ロックUSDC)
    これが本当の「USDC入出金フロー」の残高。日次変化 = 純入出金額。
    """
    if not os.path.exists(LLAMA_CACHE):
        return None
    print(c(f"  → LlamaキャッシュFile読み込み: {LLAMA_CACHE}", C.YELLOW))
    with open(LLAMA_CACHE, "r") as f:
        data = json.load(f)
    rows = data.get("bridged_usdc", [])
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.rename(columns={"total_usd": "bridged_usdc"})
    df = df.set_index("date")[["bridged_usdc"]]
    print(c(f"    ✓ Bridged USDC: {len(df)} 行 "
            f"({df.index[0].date()} 〜 {df.index[-1].date()})", C.GREEN))
    return df


def _fetch_from_asxn(frames: dict) -> None:
    """ASXN API から各指標を取得して frames に追加する（キャッシュ非使用時）。"""

    # ── Fee (Revenue) ─────────────────────────────────────────────────────
    print("  → Fee (Revenue)...")
    rev = asxn_get("/api/buyback/revenues")
    if rev and "data" in rev:
        df = pd.DataFrame(rev["data"])
        df = df.rename(columns={"date": "date", "total": "fee_total",
                                 "HyperCore Buybacks": "fee_buybacks",
                                 "HyperEVM Burn": "fee_evm_burn",
                                 "Auction Burn": "fee_auction"})
        df["date"] = pd.to_datetime(df["date"])
        frames["fee"] = df.set_index("date")[["fee_total","fee_buybacks"]]
        print(c(f"    ✓ {len(df)} 行", C.GREEN))

    # ── OI ────────────────────────────────────────────────────────────────
    print("  → OI (Open Interest)...")
    oi = asxn_get("/api/cloudfront/open_interest", {"tokens": HYPE})
    if oi and "chart_data" in oi:
        df = pd.DataFrame(oi["chart_data"])
        df = df[df["coin"] == HYPE].copy()
        df["date"] = pd.to_datetime(df["time"]).dt.normalize()
        df = df.rename(columns={"open_interest": "oi_usd"})
        frames["oi"] = df.set_index("date")[["oi_usd"]]
        print(c(f"    ✓ {len(df)} 行", C.GREEN))

    # ── 清算 ──────────────────────────────────────────────────────────────
    print("  → 清算 (Liquidations)...")
    liq = asxn_get("/api/cloudfront/daily_notional_liquidated_by_coin", {"tokens": HYPE})
    if liq and "chart_data" in liq:
        df = pd.DataFrame(liq["chart_data"])
        df = df[df["coin"] == HYPE].copy()
        df["date"] = pd.to_datetime(df["time"]).dt.normalize()
        df = df.rename(columns={"daily_notional_liquidated": "liquidations_usd"})
        frames["liq"] = df.set_index("date")[["liquidations_usd"]]
        print(c(f"    ✓ {len(df)} 行", C.GREEN))

    # ── FR ────────────────────────────────────────────────────────────────
    print("  → FR (Funding Rate daily sum)...")
    fr = asxn_get("/api/cloudfront/funding_rate", {"tokens": HYPE})
    if fr and "chart_data" in fr:
        df = pd.DataFrame(fr["chart_data"])
        df = df[df["coin"] == HYPE].copy()
        df["date"] = pd.to_datetime(df["time"]).dt.normalize()
        df = df.rename(columns={"sum_funding": "fr_daily_sum"})
        frames["fr"] = df.set_index("date")[["fr_daily_sum"]]
        print(c(f"    ✓ {len(df)} 行", C.GREEN))
    else:
        # フォールバック: Hyperliquid fundingHistory API（リトライあり）
        print(c("    ASXN失敗 → Hyperliquid fundingHistory にフォールバック...", C.YELLOW))
        fr_start_ms = int((pd.Timestamp.now() - pd.Timedelta(days=500)).timestamp() * 1000)
        records = None
        for _attempt in range(4):
            records = hl_post({"type": "fundingHistory", "coin": HYPE, "startTime": fr_start_ms})
            if records:
                break
            print(c(f"    リトライ {_attempt+1}/3...", C.YELLOW))
            time.sleep(1.5)
        if records:
            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(df["time"], unit="ms").dt.normalize()
            df["fundingRate"] = df["fundingRate"].astype(float)
            df = df.groupby("date")["fundingRate"].sum().reset_index()
            df = df.rename(columns={"fundingRate": "fr_daily_sum"})
            frames["fr"] = df.set_index("date")[["fr_daily_sum"]]
            print(c(f"    ✓ {len(df)} 行 (Hyperliquid)", C.GREEN))
        else:
            print(c("    ✗ fundingHistory 取得失敗（全リトライ終了）", C.RED))

    # ── 出来高 ────────────────────────────────────────────────────────────
    # ※ spot candles から volume_usd は既に frames["spot"] に含まれている
    #    ASXN が取れた場合は perp 出来高で上書きする
    print("  → 出来高 (Daily Volume, ASXN perp)...")
    vol = asxn_get("/api/cloudfront/daily_usd_volume_by_coin", {"tokens": HYPE})
    if vol and "chart_data" in vol:
        df = pd.DataFrame(vol["chart_data"])
        df = df[df["coin"] == HYPE].copy()
        df["date"] = pd.to_datetime(df["time"]).dt.normalize()
        col = [c2 for c2 in df.columns if "volume" in c2.lower() or "vlm" in c2.lower()]
        if col:
            df = df.rename(columns={col[0]: "volume_usd"})
            frames["vol"] = df.set_index("date")[["volume_usd"]]
            print(c(f"    ✓ {len(df)} 行 (ASXN perp)", C.GREEN))
        else:
            print(c(f"    ? volume列が見つかりません: {df.columns.tolist()}", C.YELLOW))
    else:
        print(c("    ASXN失敗 → spot candles の volume_usd を使用（frames['spot']に含む）", C.YELLOW))

    # ── USDC 残高（Stablecoin Supply） ────────────────────────────────────
    print("  → USDC残高 (Stablecoin Supply)...")
    stable = asxn_get("/api/hyper-evm/stablecoin-supply-chart", {"time_range": "ALL"})
    if not stable or "chart_data" not in stable:
        stable = asxn_get("/api/hyper-evm/stablecoin-supply-chart", {"time_range": "90d"})
    if stable and "chart_data" in stable:
        df = pd.DataFrame(stable["chart_data"])
        date_col = [c2 for c2 in df.columns if "date" in c2.lower() or "time" in c2.lower()][0]
        supply_col = [c2 for c2 in df.columns if "supply" in c2.lower() or "total" in c2.lower()][0]
        df["date"] = pd.to_datetime(df[date_col]).dt.normalize()
        df = df.rename(columns={supply_col: "usdc_supply"})
        frames["usdc"] = df.set_index("date")[["usdc_supply"]]
        print(c(f"    ✓ {len(df)} 行", C.GREEN))

def cmd_fetch():
    """ASXN + Hyperliquid から全指標を取得して CSV に保存する。"""
    print(c("\n[FETCH] 全指標データを取得します...", C.CYAN))
    frames = {}

    # ── ASXN: キャッシュ優先、なければ API ───────────────────────────────
    cache = _load_asxn_cache()
    if cache:
        frames.update(_frames_from_cache(cache))
    else:
        _fetch_from_asxn(frames)

    # ── 既存 daily_features.csv から spot 列を引き継ぐ（API不可時のフォールバック）
    SPOT_COLS = ["spot_close","spot_twap_7d","spot_twap_14d","spot_twap_30d","spot_vwap_7d"]
    if os.path.exists(DAILY_FILE):
        try:
            _old = pd.read_csv(DAILY_FILE, index_col=0, parse_dates=True)
            _old_spot = [c2 for c2 in SPOT_COLS if c2 in _old.columns]
            if _old_spot:
                frames["spot_prev"] = _old[_old_spot]
                print(c(f"  → 既存CSV から spot 列を引き継ぎ: {_old_spot}", C.YELLOW))
        except Exception:
            pass

    # ── Spot TWAP（Hyperliquid candle → rolling） ─────────────────────────
    print("  → 現物 TWAP (Spot Candles from Hyperliquid)...")
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - 500 * 24 * 3600 * 1000   # 最大500日
    candles  = fetch_candles_hl(HL_SPOT, "1d", start_ms, now_ms)
    if not candles:
        candles = fetch_candles_hl(HYPE, "1d", start_ms, now_ms)
    if candles:
        df = pd.DataFrame(candles).rename(
            columns={"t":"time","o":"open","h":"high","l":"low","c":"close","v":"volume"})
        df["date"] = pd.to_datetime(df["time"], unit="ms", utc=True).dt.normalize().dt.tz_localize(None)
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        df = df.sort_values("date").reset_index(drop=True)
        # TWAP: 7日・14日・30日ローリング単純平均
        df["spot_close"]   = df["close"]
        df["spot_twap_7d"] = df["close"].rolling(7).mean()
        df["spot_twap_14d"]= df["close"].rolling(14).mean()
        df["spot_twap_30d"]= df["close"].rolling(30).mean()
        # VWAP 7日
        df["spot_vwap_7d"] = (df["close"] * df["volume"]).rolling(7).sum() / df["volume"].rolling(7).sum()
        # Volume: spot candle の v はコイン単位 → close との積でUSD近似
        df["volume_usd"] = df["volume"] * df["close"]
        frames["spot"] = df.set_index("date")[["spot_close","spot_twap_7d","spot_twap_14d","spot_twap_30d","spot_vwap_7d","volume_usd"]]
        print(c(f"    ✓ {len(df)} 行", C.GREEN))

    # ── 4h 足（バックテスト用） ────────────────────────────────────────────
    print("  → 4h 足 (Perp Candles)...")
    candles_4h = fetch_candles_hl(HYPE, "4h", start_ms, now_ms)
    if not candles_4h:
        candles_4h = fetch_candles_hl(HL_SPOT, "4h", start_ms, now_ms)
    if candles_4h:
        df4 = pd.DataFrame(candles_4h).rename(
            columns={"t":"time","o":"open","h":"high","l":"low","c":"close","v":"volume"})
        df4["time"] = pd.to_datetime(df4["time"], unit="ms", utc=True)
        for col in ["open","high","low","close","volume"]:
            df4[col] = df4[col].astype(float)
        df4 = df4.drop_duplicates("time").sort_values("time").reset_index(drop=True)
        df4[["time","open","high","low","close","volume"]].to_csv(CANDLE_4H, index=False)
        print(c(f"    ✓ {len(df4)} 行 → {CANDLE_4H}", C.GREEN))

    # ── DefiLlama Bridged USDC（入出金フロー） ────────────────────────────
    llama_df = _load_llama_cache()
    if llama_df is not None:
        frames["llama"] = llama_df
    else:
        print(c("  ⚠ llama_cache.json なし。ブラウザから取得してhype_dataに保存してください。", C.YELLOW))

    # ── 統合 ──────────────────────────────────────────────────────────────
    if not frames:
        print(c("  データ取得に失敗しました。", C.RED))
        return False

    merged = pd.concat(frames.values(), axis=1, join="outer")
    merged.index = pd.to_datetime(merged.index)
    merged = merged.sort_index()
    # 重複列を除去（volume_usd など spot と vol の両方に存在する場合）
    merged = merged.loc[:, ~merged.columns.duplicated(keep="first")]

    # 日次リターンを追加
    if "spot_close" in merged.columns:
        merged["ret_1d"]  = merged["spot_close"].pct_change(1).shift(-1)
        merged["ret_3d"]  = merged["spot_close"].pct_change(3).shift(-3)
        merged["ret_7d"]  = merged["spot_close"].pct_change(7).shift(-7)
        merged["ret_14d"] = merged["spot_close"].pct_change(14).shift(-14)

    # 派生フィーチャー
    if "oi_usd" in merged.columns:
        merged["oi_chg_1d"]  = merged["oi_usd"].pct_change(1)
        merged["oi_chg_3d"]  = merged["oi_usd"].pct_change(3)
    if "volume_usd" in merged.columns:
        merged["vol_ma7"]    = merged["volume_usd"].rolling(7).mean()
        merged["vol_ratio"]  = merged["volume_usd"] / merged["vol_ma7"]
    if "fee_total" in merged.columns:
        merged["fee_ma7"]    = merged["fee_total"].rolling(7).mean()
        merged["fee_ratio"]  = merged["fee_total"] / merged["fee_ma7"]
    if "usdc_supply" in merged.columns:
        # ゼロはデータなし（HyperEVM未開始期間）→ NaN に置換してから変化率計算
        usdc = merged["usdc_supply"].replace(0, float("nan"))
        merged["usdc_supply"] = usdc
        merged["usdc_chg_1d"] = usdc.pct_change(1)
        merged["usdc_chg_7d"] = usdc.pct_change(7)
    if "bridged_usdc" in merged.columns:
        # 本物のUSDC入出金フロー（ArbitrumブリッジにロックされたUSDC残高の変化）
        bridge = merged["bridged_usdc"]
        merged["bridge_flow_1d"] = bridge.diff(1)          # 1日純流入額（USD）
        merged["bridge_flow_7d"] = bridge.diff(7)          # 7日累積純流入額
        merged["bridge_flow_1d_pct"] = bridge.pct_change(1)  # 1日変化率
        merged["bridge_flow_7d_pct"] = bridge.pct_change(7)  # 7日変化率
    if "liquidations_usd" in merged.columns:
        merged["liq_ma7"]    = merged["liquidations_usd"].rolling(7).mean()
        merged["liq_ratio"]  = merged["liquidations_usd"] / (merged["liq_ma7"] + 1)
    if "fr_daily_sum" in merged.columns:
        merged["fr_ma7"]     = merged["fr_daily_sum"].rolling(7).mean()
        merged["fr_zscore"]  = (merged["fr_daily_sum"] - merged["fr_daily_sum"].rolling(30).mean()) \
                               / (merged["fr_daily_sum"].rolling(30).std() + 1e-9)
    if "spot_close" in merged.columns and "spot_twap_7d" in merged.columns:
        merged["twap_premium_7d"] = (merged["spot_close"] - merged["spot_twap_7d"]) / merged["spot_twap_7d"]

    merged.to_csv(DAILY_FILE)
    print(c(f"\n  ✓ 統合データ保存: {DAILY_FILE}", C.GREEN))
    print(f"  期間: {merged.index[0].date()} 〜 {merged.index[-1].date()} ({len(merged)} 日)")
    print(f"  特徴量: {merged.columns.tolist()}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 2. CORRELATION ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

FACTOR_COLS = [
    "fr_daily_sum", "fr_ma7", "fr_zscore",
    "oi_usd", "oi_chg_1d", "oi_chg_3d",
    "volume_usd", "vol_ratio",
    "fee_total", "fee_ratio",
    "usdc_supply", "usdc_chg_1d", "usdc_chg_7d",
    "bridged_usdc", "bridge_flow_1d", "bridge_flow_7d", "bridge_flow_1d_pct", "bridge_flow_7d_pct",
    "liquidations_usd", "liq_ratio",
    "twap_premium_7d",
    "spot_twap_7d", "spot_twap_14d",
]
RET_COLS = ["ret_1d", "ret_3d", "ret_7d", "ret_14d"]
RET_LABELS = {"ret_1d": "翌日", "ret_3d": "3日後", "ret_7d": "7日後", "ret_14d": "14日後"}


def cmd_correlate():
    """各指標と将来リターンのラグ相関を計算して表示する。"""
    if not os.path.exists(DAILY_FILE):
        print(c(f"Error: {DAILY_FILE} なし。先に --fetch を実行してください。", C.RED))
        return

    df = pd.read_csv(DAILY_FILE, index_col=0, parse_dates=True)
    available_factors = [f for f in FACTOR_COLS if f in df.columns]
    available_rets    = [r for r in RET_COLS if r in df.columns]

    print(c("\n[CORRELATE] 各指標 × 将来リターン ラグ相関分析", C.CYAN + C.BOLD))
    print(f"  分析期間: {df.index[0].date()} 〜 {df.index[-1].date()} ({len(df)} 日)")
    print(f"  有効因子: {len(available_factors)} 個")

    # ── 相関テーブル ─────────────────────────────────────────────────────
    corr_rows = []
    for fac in available_factors:
        sub = df[[fac] + available_rets].dropna()
        if len(sub) < 20:
            continue
        row = {"指標": fac, "サンプル数": len(sub)}
        for ret in available_rets:
            r = sub[fac].corr(sub[ret])
            row[RET_LABELS[ret]] = round(r, 3)
        corr_rows.append(row)

    corr_df = pd.DataFrame(corr_rows).set_index("指標")

    print(c("\n  ── 相関係数マトリクス（強い順） ──", C.BOLD))
    print(c("  ※ 正 = 指標↑のとき価格↑傾向、負 = 指標↑のとき価格↓傾向", C.DIM))
    print(c("  ※ |r| > 0.15 で弱い相関あり、> 0.30 で中程度", C.DIM))

    # 翌日リターンでソート
    sorted_df = corr_df.sort_values("翌日", key=abs, ascending=False)

    def fmt_corr(v):
        if isinstance(v, float):
            mark = "◉" if abs(v) > 0.3 else "○" if abs(v) > 0.15 else " "
            col  = C.GREEN if v > 0.1 else C.RED if v < -0.1 else ""
            return c(f"{mark}{v:+.3f}", col)
        return str(v)

    if HAS_TABULATE:
        rows = [[idx] + [fmt_corr(v) for v in row] for idx, row in sorted_df.iterrows()]
        print(tabulate(rows, headers=["指標"] + list(sorted_df.columns), tablefmt="simple_grid"))
    else:
        print(sorted_df.to_string())

    # ── 分位分析 ─────────────────────────────────────────────────────────
    print(c("\n  ── 分位分析（各指標を5分位に分けた翌日リターン） ──", C.BOLD))
    print(c("  ※ ランダムなら全分位で勝率≈50%・平均≈0%に収束するはず", C.DIM))
    print()

    target_ret = "ret_1d"
    top_factors = sorted_df.index[:6].tolist()  # 相関上位6指標を詳細表示

    for fac in top_factors:
        if fac not in df.columns or target_ret not in df.columns:
            continue
        sub = df[[fac, target_ret]].dropna()
        if len(sub) < 30:
            continue
        try:
            sub["q"] = pd.qcut(sub[fac], q=5, labels=["Q1(低)","Q2","Q3","Q4","Q5(高)"], duplicates="drop")
        except Exception:
            continue

        grp = sub.groupby("q", observed=True)[target_ret]
        stats = pd.DataFrame({
            "件数":    grp.count(),
            "平均リターン": (grp.mean() * 100).round(2),
            "勝率%":   (grp.apply(lambda x: (x > 0).mean()) * 100).round(1),
            "中央値":   (grp.median() * 100).round(2),
        })

        print(c(f"  [{fac}]", C.CYAN + C.BOLD))
        if HAS_TABULATE:
            print(tabulate(stats.reset_index(), headers="keys", tablefmt="simple_grid",
                           showindex=False, floatfmt=".2f"))
        else:
            print(stats.to_string())
        print()

    # ── USDC フロー特別分析 ───────────────────────────────────────────────
    if "usdc_chg_7d" in df.columns and "ret_7d" in df.columns:
        print(c("  ── USDC 7日フロー × 7日後リターン（資金フロー分析） ──", C.BOLD))
        sub = df[["usdc_chg_7d","ret_7d"]].dropna()
        try:
            sub["q"] = pd.qcut(sub["usdc_chg_7d"], q=5,
                               labels=["Q1(大流出)","Q2","Q3","Q4","Q5(大流入)"],
                               duplicates="drop")
            grp = sub.groupby("q", observed=True)["ret_7d"]
            stats = pd.DataFrame({
                "件数":       grp.count(),
                "7日後平均%": (grp.mean() * 100).round(2),
                "勝率%":      (grp.apply(lambda x: (x > 0).mean()) * 100).round(1),
            })
            if HAS_TABULATE:
                print(tabulate(stats.reset_index(), headers="keys", tablefmt="simple_grid",
                               showindex=False, floatfmt=".2f"))
            else:
                print(stats.to_string())
        except Exception:
            pass

    # CSV 保存
    corr_csv = os.path.join(DATA_DIR, "correlation_matrix.csv")
    corr_df.to_csv(corr_csv)
    print(c(f"\n  ✓ 相関マトリクス保存: {corr_csv}", C.GREEN))


# ─────────────────────────────────────────────────────────────────────────────
# 3. BACKTEST ENGINE (4h 足)
# ─────────────────────────────────────────────────────────────────────────────

def load_4h_with_daily() -> pd.DataFrame:
    """4h 足に日次指標をマージして返す。"""
    if not os.path.exists(CANDLE_4H):
        raise FileNotFoundError(f"{CANDLE_4H} なし。--fetch を先に実行してください。")

    df4 = pd.read_csv(CANDLE_4H, parse_dates=["time"])
    df4["time"] = pd.to_datetime(df4["time"], utc=True)
    for col in ["open","high","low","close","volume"]:
        df4[col] = df4[col].astype(float)
    df4 = df4.sort_values("time").reset_index(drop=True)

    # 4h 内部フィーチャー
    df4["vol_ma20"]   = df4["volume"].rolling(20).mean()
    df4["vol_ratio"]  = df4["volume"] / (df4["vol_ma20"] + 1e-9)
    df4["ret_next_4h"]= df4["close"].pct_change(1).shift(-1)

    # 日次データをマージ（その日の指標を4h足に結合）
    if os.path.exists(DAILY_FILE):
        daily = pd.read_csv(DAILY_FILE, index_col=0, parse_dates=True)
        daily.index = daily.index.tz_localize("UTC")
        df4["date"] = df4["time"].dt.normalize()
        df4 = pd.merge_asof(
            df4.sort_values("time"),
            daily.reset_index().rename(columns={"index": "date"}).sort_values("date"),
            left_on="time", right_on="date", direction="backward"
        )
        # カラム名衝突の解消（merge_asof で _x/_y suffix が付く場合）
        for col in ["vol_ratio", "vol_ma7", "date"]:
            if f"{col}_x" in df4.columns:
                df4 = df4.rename(columns={f"{col}_x": col})
            if f"{col}_y" in df4.columns:
                df4 = df4.drop(columns=[f"{col}_y"])
        if "volume_usd" in df4.columns and "volume" in df4.columns:
            df4 = df4.drop(columns=["volume_usd"])

    return df4.dropna(subset=["ret_next_4h"]).reset_index(drop=True)


LONG  =  1
SHORT = -1
FLAT  =  0


def run_backtest(df: pd.DataFrame, params: dict) -> dict:
    """
    複合シグナルバックテスト。
    Entry: FR + OI変化 + Volume + (USDC/Fee/清算 オプション)
    """
    fr_long    = params.get("fr_long_thresh",   -0.5)    # fr_daily_sum 閾値
    fr_short   = params.get("fr_short_thresh",   0.5)
    oi_thresh  = params.get("oi_chg_thresh",     0.02)   # OI 変化率
    vol_thresh = params.get("vol_spike",         1.5)    # 4h 出来高比率
    tp         = params.get("tp_pct",            0.05)
    sl         = params.get("sl_pct",            0.03)
    max_hold   = params.get("max_hold_bars",     6)
    fee_bps    = params.get("fee_bps",           3.5)
    lev        = params.get("leverage",          3)
    # オプション指標フィルター
    use_usdc   = params.get("use_usdc_filter",   False)
    use_liq    = params.get("use_liq_filter",    False)

    fee_rate = fee_bps / 10000
    trades   = []
    equity   = [1.0]
    pos      = FLAT
    entry_px = 0.0
    entry_bar= 0
    n = len(df)

    for i in range(1, n - 1):
        row = df.iloc[i]

        # ── 指標読み取り ──
        fr_val    = row.get("fr_daily_sum", 0) or 0
        oi_chg    = row.get("oi_chg_1d", 0)   or 0
        vr        = row.get("vol_ratio", 1)    or 1
        usdc_chg  = row.get("usdc_chg_1d", 0) or 0
        liq_r     = row.get("liq_ratio", 1)    or 1
        price     = row["close"]

        # ── Exit ──
        if pos != FLAT:
            pnl_raw  = (price - entry_px) / entry_px * pos
            hold     = i - entry_bar
            reason   = None
            if   pnl_raw * lev >= tp:     reason = "TP"
            elif pnl_raw * lev <= -sl:    reason = "SL"
            elif hold >= max_hold:        reason = "TIME"
            elif pos == LONG  and fr_val > abs(fr_short): reason = "FR_EXIT"
            elif pos == SHORT and fr_val < -abs(fr_long):  reason = "FR_EXIT"

            if reason:
                pnl_net = pnl_raw * lev - fee_rate * 2
                equity.append(equity[-1] * (1 + pnl_net))
                trades.append({
                    "entry_time": df.iloc[entry_bar]["time"],
                    "exit_time":  row["time"],
                    "side":       "LONG" if pos == LONG else "SHORT",
                    "entry_px":   round(entry_px, 4),
                    "exit_px":    round(price, 4),
                    "hold_bars":  hold,
                    "pnl_net_%":  round(pnl_net * 100, 3),
                    "exit":       reason,
                    "fr_entry":   round(fr_val, 4),
                    "oi_chg":     round(oi_chg, 4),
                })
                pos = FLAT
            continue

        # ── Entry ──
        # 基本条件
        long_base  = (fr_val  < fr_long)  and (oi_chg > oi_thresh)  and (vr > vol_thresh)
        short_base = (fr_val  > fr_short) and (oi_chg > oi_thresh)  and (vr > vol_thresh)

        # オプションフィルター
        if use_usdc:
            long_base  = long_base  and (usdc_chg > 0)   # USDC 流入時のみ Long
            short_base = short_base and (usdc_chg < 0)   # USDC 流出時のみ Short
        if use_liq:
            long_base  = long_base  and (liq_r < 1.5)    # 清算少ない時
            short_base = short_base and (liq_r > 1.5)    # 清算多い時

        if long_base and not short_base:
            pos      = LONG
            entry_px = df.iloc[i + 1]["open"]
            entry_bar= i
        elif short_base and not long_base:
            pos      = SHORT
            entry_px = df.iloc[i + 1]["open"]
            entry_bar= i

    # ── Stats ──
    if not trades:
        return {"trades": [], "stats": {"total_trades": 0}, "equity": equity}

    tdf   = pd.DataFrame(trades)
    pnls  = tdf["pnl_net_%"].values / 100
    wins  = pnls[pnls > 0]
    losses= pnls[pnls <= 0]
    eq_arr= np.array(equity)
    peak  = np.maximum.accumulate(eq_arr)
    max_dd= ((eq_arr - peak) / peak).min()
    sharpe= (pnls.mean() / (pnls.std() + 1e-9)) * np.sqrt(len(pnls))

    stats = {
        "total_trades":  len(tdf),
        "win_rate_%":    round((pnls > 0).mean() * 100, 1),
        "avg_win_%":     round(wins.mean() * 100, 3) if len(wins) else 0,
        "avg_loss_%":    round(losses.mean() * 100, 3) if len(losses) else 0,
        "profit_factor": round(abs(wins.sum() / losses.sum()), 2) if losses.sum() else 999,
        "total_pnl_%":   round(pnls.sum() * 100, 2),
        "max_dd_%":      round(max_dd * 100, 2),
        "sharpe_approx": round(sharpe, 2),
        "long_trades":   int((tdf["side"] == "LONG").sum()),
        "short_trades":  int((tdf["side"] == "SHORT").sum()),
        "final_equity":  round(eq_arr[-1], 4),
    }
    return {"trades": tdf.to_dict("records"), "stats": stats, "equity": equity}


def cmd_run(params_file: str | None = None):
    print(c("\n[RUN] バックテスト実行...", C.CYAN))
    params = {}
    load_from = params_file or BEST_PARAMS
    if load_from and os.path.exists(load_from):
        with open(load_from) as f:
            params = json.load(f)
        print(c(f"  パラメータ読込: {load_from}", C.DIM))
    else:
        print(c("  デフォルトパラメータ使用", C.DIM))

    try:
        df = load_4h_with_daily()
        print(f"  データ: {len(df)} 行 ({df['time'].iloc[0]} 〜 {df['time'].iloc[-1]})")
    except FileNotFoundError as e:
        print(c(f"Error: {e}", C.RED)); return

    res   = run_backtest(df, params)
    stats = res["stats"]

    print(c("\n" + "="*58, C.DIM))
    print(c("  バックテスト結果", C.BOLD + C.CYAN))
    print(c("="*58, C.DIM))

    def pf(v): return c(f"{v:+.2f}%", C.GREEN if v >= 0 else C.RED)
    rows = [
        ["総トレード数",   stats.get("total_trades", 0)],
        ["Long / Short",  f"{stats.get('long_trades',0)} / {stats.get('short_trades',0)}"],
        ["勝率",           f"{stats.get('win_rate_%',0):.1f}%"],
        ["平均利益",        pf(stats.get("avg_win_%",0))],
        ["平均損失",        pf(stats.get("avg_loss_%",0))],
        ["Profit Factor",  f"{stats.get('profit_factor',0):.2f}"],
        ["総損益",          pf(stats.get("total_pnl_%",0))],
        ["最大 DD",         c(f"{stats.get('max_dd_%',0):+.2f}%", C.RED)],
        ["Sharpe (近似)",   f"{stats.get('sharpe_approx',0):.2f}"],
        ["最終資産倍率",     f"{stats.get('final_equity',1.0):.4f}x"],
    ]
    if HAS_TABULATE:
        print(tabulate(rows, tablefmt="simple_grid"))
    else:
        for r in rows: print(f"  {r[0]:20s} {r[1]}")

    if params:
        print(c("\n  使用パラメータ:", C.BOLD))
        for k, v in params.items(): print(f"    {k:25s}: {v}")

    if res["trades"]:
        tdf = pd.DataFrame(res["trades"])
        tdf["entry_time"] = pd.to_datetime(tdf["entry_time"]).dt.strftime("%m/%d %H:%M")
        tdf["exit_time"]  = pd.to_datetime(tdf["exit_time"]).dt.strftime("%m/%d %H:%M")
        print(c(f"\n  全トレード ({len(tdf)} 件):", C.BOLD))
        disp = tdf[["entry_time","exit_time","side","entry_px","exit_px","pnl_net_%","exit","fr_entry","oi_chg"]]
        if HAS_TABULATE:
            print(tabulate(disp, headers="keys", tablefmt="simple_grid", showindex=False, floatfmt=".4f"))
        else:
            print(disp.to_string(index=False))

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(DATA_DIR, f"trades_{ts}.csv")
        tdf.to_csv(csv_path, index=False)
        print(c(f"\n  ✓ トレード CSV: {csv_path}", C.GREEN))

        # ASCII エクイティカーブ
        eq = res["equity"]
        if len(eq) > 2:
            print(c("\n  エクイティカーブ:", C.BOLD))
            _ascii_chart(eq)


def _ascii_chart(equity: list, width: int = 58, height: int = 10):
    mn, mx = min(equity), max(equity)
    if mx == mn: return
    sampled = equity[::max(1, len(equity)//width)][:width]
    norm    = [(v - mn) / (mx - mn) for v in sampled]
    for row in range(height, -1, -1):
        thr  = row / height
        line = "".join("█" if v >= thr else " " for v in norm)
        lbl  = f"{mn+(mx-mn)*thr:.3f}x" if row % (height//3) == 0 else "       "
        print(f"  {lbl:8s}|{line}|")


# ─────────────────────────────────────────────────────────────────────────────
# 4. OPTIMIZER
# ─────────────────────────────────────────────────────────────────────────────

GRID = {
    "fr_long_thresh":  [-0.3, -0.5, -1.0],
    "fr_short_thresh": [ 0.3,  0.5,  1.0],
    "oi_chg_thresh":   [0.01, 0.03, 0.05],
    "vol_spike":       [1.3,  1.5,  2.0],
    "tp_pct":          [0.04, 0.06, 0.10],
    "sl_pct":          [0.02, 0.03, 0.05],
    "max_hold_bars":   [4,    6,    12],
    "leverage":        [2,    3,    5],
}


def cmd_optimize(top_n: int = 10):
    print(c("\n[OPTIMIZE] グリッドサーチ最適化...", C.CYAN))
    try:
        df = load_4h_with_daily()
    except FileNotFoundError as e:
        print(c(f"Error: {e}", C.RED)); return

    keys   = list(GRID.keys())
    combos = list(iterproduct(*GRID.values()))
    print(f"  組み合わせ数: {len(combos):,}")

    results = []
    for idx, combo in enumerate(combos):
        p   = dict(zip(keys, combo))
        res = run_backtest(df, p)
        st  = res["stats"]
        if st.get("total_trades", 0) < 5:
            continue
        score = (
            max(st["sharpe_approx"], 0) *
            st["win_rate_%"] / 100 *
            (1 + min(st["profit_factor"], 10)) /
            max(-st["max_dd_%"] / 100 + 0.01, 0.001)
        )
        results.append({**p, **st, "score": score})
        if (idx + 1) % 500 == 0:
            print(f"  ... {idx+1}/{len(combos)}")

    if not results:
        print(c("  有効な結果がありませんでした。", C.YELLOW)); return

    rdf  = pd.DataFrame(results).sort_values("score", ascending=False).head(top_n)
    cols = ["score","total_trades","win_rate_%","total_pnl_%","max_dd_%",
            "sharpe_approx","profit_factor","fr_long_thresh","fr_short_thresh",
            "oi_chg_thresh","vol_spike","tp_pct","sl_pct","leverage"]
    print(c(f"\n  Top {top_n} パラメータ:", C.BOLD))
    if HAS_TABULATE:
        print(tabulate(rdf[cols].round(4).reset_index(drop=True),
                       headers="keys", tablefmt="simple_grid", showindex=False, floatfmt=".4f"))
    else:
        print(rdf[cols].to_string(index=False))

    best = rdf.iloc[0][keys].to_dict()
    with open(BEST_PARAMS, "w") as f:
        json.dump(best, f, indent=2)
    print(c(f"\n  ✓ 最良パラメータ保存: {BEST_PARAMS}", C.GREEN))


# ─────────────────────────────────────────────────────────────────────────────
# 5. ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="$HYPE Backtest Engine v2 — 全指標対応",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
手順:
  1. python hype_backtest.py --fetch              # データ取得
  2. python hype_backtest.py --correlate          # 指標 vs 価格 相関確認
  3. python hype_backtest.py --run                # バックテスト実行
  4. python hype_backtest.py --optimize           # パラメータ最適化
  5. python hype_backtest.py --run                # 最良パラメータで再実行

まとめて:
  python hype_backtest.py --fetch --correlate --run --optimize
        """
    )
    parser.add_argument("--fetch",     action="store_true", help="全指標データを取得")
    parser.add_argument("--correlate", action="store_true", help="指標 vs 将来リターン 相関分析")
    parser.add_argument("--run",       action="store_true", help="バックテスト実行")
    parser.add_argument("--optimize",  action="store_true", help="グリッドサーチ最適化")
    parser.add_argument("--params",    type=str, default=None, help="パラメータ JSON ファイル")
    parser.add_argument("--top",       type=int, default=10, help="最適化表示件数")
    args = parser.parse_args()

    if not any([args.fetch, args.correlate, args.run, args.optimize]):
        parser.print_help()
        return

    if args.fetch:
        cmd_fetch()
    if args.correlate:
        cmd_correlate()
    if args.run:
        cmd_run(params_file=args.params)
    if args.optimize:
        cmd_optimize(top_n=args.top)

    print()


if __name__ == "__main__":
    main()
