# $HYPE Trading Toolkit

Hyperliquid の `$HYPE` を分析・バックテストするための Python スクリプト集。

---

## ファイル構成

```
hype-trade/
├── hype_backtest.py          # メインスクリプト（データ取得・相関分析・バックテスト）
├── hype_analyzer.py          # リアルタイム指標モニタリング（簡易ダッシュボード）
├── save_asxn.py              # ASXNデータ受信サーバー（初回データ取得時のみ使用）
├── hype_data/
│   ├── asxn_cache.json       # ASXNから取得した生データのキャッシュ（手動更新）
│   ├── llama_cache.json      # DefiLlamaから取得したBridged USDCデータ（手動更新）
│   ├── daily_features.csv    # 全指標を統合した日次データ（--fetch で自動生成）
│   ├── candles_4h.csv        # 4時間足OHLCVデータ（--fetch で自動更新）
│   ├── correlation_matrix.csv # 相関分析結果（--correlate で自動生成）
│   └── trades_YYYYMMDD_*.csv # バックテストの全トレード記録（--run で自動生成）
├── requirements.txt
└── README.md
```

---

## 取得できているデータ一覧

| 指標 | 期間 | 日数 | 取得元 | 信頼性 | 備考 |
|------|------|------|--------|--------|------|
| Spot価格（OHLCV） | 2024-11-29〜現在 | 478日 | Hyperliquid API | ◎ | 公式API直接取得 |
| 4時間足（OHLCV） | 2024-11-29〜現在 | 2865本 | Hyperliquid API | ◎ | バックテスト用 |
| FR（資金調達率日次合計） | 2024-12-05〜現在 | 472日 | ASXN / Hyperliquid | ◎ | |
| OI（建玉残高） | 2024-12-05〜現在 | 472日 | ASXN | ◎ | |
| Volume（出来高） | 2024-12-05〜現在 | 470日 | ASXN | ◎ | 2日前まで |
| 清算額 | 2024-12-05〜現在 | 465日 | ASXN | ◎ | |
| Fee収入 | 2025-03-20〜現在 | 366日 | ASXN | ○ | HyperEVM開始以降のみ |
| USDC残高（HyperEVM） | 2025-03-14〜現在 | 373日 | ASXN | △ | ※下記注意参照 |
| **Bridged USDC（入出金フロー）** | **2023-06-09〜現在** | **1017日** | **DefiLlama** | **◎** | **本物の純入出金残高** |

### USDC系指標の使い分け

| 列名 | 意味 | ソース | 用途 |
|------|------|--------|------|
| `usdc_supply` | HyperEVM上のステーブルコイン総供給量（約1.1B） | ASXN | HyperEVM活動の代理指標 |
| `bridged_usdc` | ArbitrumブリッジにロックされているUSDC残高（約4B） | DefiLlama | **本物の入出金フロー残高** |

`bridged_usdc` の日次変化（`bridge_flow_1d`）が実際の Hyperliquid への純入金額・純出金額に相当する。
日本語 Hyperliquid Discord の「Bridge Locked USDC」報告値と一致することを確認済み。

---

## 派生指標（daily_features.csv）

`--fetch` 実行時に自動計算される派生指標：

| 列名 | 内容 |
|------|------|
| `oi_chg_1d` / `oi_chg_3d` | OIの1日・3日変化率 |
| `vol_ma7` / `vol_ratio` | 出来高の7日MA・対MA比率 |
| `fee_ma7` / `fee_ratio` | Fee収入の7日MA・対MA比率 |
| `usdc_chg_1d` / `usdc_chg_7d` | HyperEVM USDC残高の1日・7日変化率 |
| `bridge_flow_1d` / `bridge_flow_7d` | Bridged USDCの1日・7日純流入額（USD絶対値） |
| `bridge_flow_1d_pct` / `bridge_flow_7d_pct` | Bridged USDCの1日・7日変化率 |
| `liq_ma7` / `liq_ratio` | 清算額の7日MA・対MA比率 |
| `fr_ma7` / `fr_zscore` | FRの7日MA・30日Zスコア |
| `spot_twap_7d/14d/30d` | スポット価格の7/14/30日TWAP |
| `spot_vwap_7d` | 出来高加重の7日VWAP |
| `twap_premium_7d` | `(価格 - TWAP7d) / TWAP7d` |
| `ret_1d/3d/7d/14d` | 翌日・3日後・7日後・14日後リターン（教師ラベル） |

---

## 相関分析の主な結果（2023-06-09〜2026-03-21, 1017日）

| 指標 | 翌日 | 7日後 | 14日後 | 解釈 |
|------|------|-------|--------|------|
| `bridged_usdc` | -0.14 | -0.28 | **-0.33** | 残高が高水準→下落しやすい（逆張り） |
| `spot_twap_7d` | -0.11 | -0.28 | **-0.34** | 価格水準が高い→下落しやすい（逆張り） |
| `oi_usd` | -0.10 | -0.25 | **-0.30** | OI高い→レバレッジ過多→下落しやすい |
| `bridge_flow_7d_pct` | +0.10 | +0.19 | **+0.27** | 流入加速中→モメンタム継続（順張り） |
| `bridge_flow_1d_pct` | +0.12 | +0.15 | **+0.20** | 日次流入率高い→短〜中期上昇傾向 |
| `oi_chg_3d` | +0.05 | **+0.23** | +0.17 | OI増加中→モメンタム継続 |

### Bridged USDC 残高水準 × 翌日リターン（分位分析）

| 分位 | 翌日平均リターン | 翌日勝率 | 解釈 |
|------|----------------|---------|------|
| Q1（残高最低） | **+1.95%** | **56.2%** | 資金少ない時期→上昇余地あり |
| Q2〜Q4 | -0.19〜+1.02% | 48〜58% | 中立 |
| Q5（残高最高） | **-0.58%** | **39.6%** | 資金が飽和→天井圏シグナル |

### 重要な発見：残高レベルvs変化率の非対称性

- **`bridged_usdc`（残高レベル）**: 逆張りシグナル。高水準に達したあとは下落しやすい
- **`bridge_flow_7d_pct`（変化率）**: 順張りシグナル。急増している最中はモメンタム継続
- **`bridge_flow_1d/7d`（絶対金額）**: ほぼ無相関（|r| < 0.05）。絶対額より変化率が重要

---

## 使い方

```bash
cd ~/claude-code/hype-trade
source .venv/bin/activate
```

### 通常の分析フロー

```bash
# 1. データ更新（Spotは毎回更新、ASXNはキャッシュから読む）
python hype_backtest.py --fetch

# 2. 相関分析
python hype_backtest.py --correlate

# 3. バックテスト実行
python hype_backtest.py --run

# 4. パラメータ最適化
python hype_backtest.py --optimize
```

### ASXNデータの再取得（月1回程度）

ASXNのデータは `asxn_cache.json` にキャッシュされているが、最新データに更新したい場合：

```bash
# ステップ1: 受信サーバーを起動（別ターミナル）
python save_asxn.py

# ステップ2: Claude に「ASXNデータを再取得して」と指示
# → hyperscreener.asxn.xyz にアクセスし、ブラウザの保存ボタンをクリック
# → asxn_cache.json が自動更新される

# ステップ3: サーバー終了後、--fetch で統合
python hype_backtest.py --fetch
```

### DefiLlama Bridged USDCデータの更新（月1回程度）

`llama_cache.json` は DefiLlama の `hyperliquid-bridge` プロトコルデータから取得。
VMからはAPI直接アクセス不可のため、ブラウザ経由で取得する：

```
Claude に「llama_cacheを更新して」と指示
→ ブラウザで api.llama.fi/updatedProtocol/hyperliquid-bridge を開き
→ chainTvls['Arbitrum'].tvl の日次データを取得
→ hype_data/llama_cache.json に保存
→ python hype_backtest.py --fetch で統合
```

---

## データソース

| ソース | 用途 | アクセス方法 |
|--------|------|-------------|
| `api.hyperliquid.xyz/info` | 価格・FR・4h足 | Python直接（API制限なし） |
| `api-hyperliquid.asxn.xyz` | OI・Volume・清算・Fee・USDC | ブラウザ経由（CloudFront保護あり） |
| `hyperscreener.asxn.xyz` | ASXN認証済みページ | データ取得用ブラウザタブ |
| `api.llama.fi/updatedProtocol/hyperliquid-bridge` | Bridged USDC（入出金フロー） | ブラウザ経由（VMからは403） |

---

## 注意事項

- バックテスト結果は過去データに基づく推定であり、将来の利益を保証しない
- ASXNデータはサードパーティ集計のため、Hyperliquid公式データとわずかに乖離する可能性がある
- `usdc_supply`（HyperEVM USDC）と `bridged_usdc`（Arbitrumブリッジ）は全く別の指標
- DefiLlama・HL APIはVMのプロキシ経由でアクセス不可（403）のため、ブラウザ経由で取得
- Python 3.9 で動作確認済み

---

## 開発ログ

### 2026-03-21

**USDC入出金フローの正体を特定・統合**

「トレーダーがツイートで言ってたUSDC入出金フロー」が何を指すか調査。hypurrscan.io のネットワークリクエストを解析し、DefiLlama の `hyperliquid-bridge` プロトコル（`api.llama.fi/updatedProtocol/hyperliquid-bridge`）の `chainTvls['Arbitrum'].tvl` が正解と特定。日本語Hyperliquid Discordの「Bridge Locked USDC」報告値と一致することも確認。

**追加したもの:**
- `hype_data/llama_cache.json` — DefiLlamaから取得したBridged USDCデータ（2023-06-09〜、1017日分）
- `_load_llama_cache()` 関数 — `hype_backtest.py` に追加
- `bridge_flow_1d/7d/1d_pct/7d_pct` — `--fetch` 時に自動計算される派生フィーチャー4列
- `FACTOR_COLS` に上記4列 + `bridged_usdc` を追加

**相関分析の主な発見（`--correlate`実行結果）:**
- `bridged_usdc`（残高レベル）は14日後リターンとr=-0.33の逆相関 → 天井圏シグナル
- `bridge_flow_7d_pct`（7日変化率）は14日後リターンとr=+0.27の順相関 → モメンタムシグナル
- 絶対金額（`bridge_flow_1d/7d`）はほぼ無相関（r<0.05）。変化率の方が重要

**調査して断念したもの:**
- hypurrscan `/spotUSDC` エンドポイント → Spot市場USDC残高（約970M）。Bridged USDCとは別物、追加価値なし
- 油汗ツール群（blp/halving/flowscan/loris/top.thankyoujeff.xyz） → すべてリアルタイムのみ、履歴データAPIなし
- `fr_premium`（mark-index乖離） → HL APIへのVMプロキシ403 + `fr_daily_sum`と高相関のため断念

**`daily_features.csv` の最終状態:** 33列・1017行（2023-06-09〜2026-03-21）

### 2026-03-29

**自動取引Bot完成 & VPSデプロイ**

**追加したファイル:**
- `hype_bot.py` — 自動取引Bot本体（シグナル検出→Longエントリー→TP/SL決済）
- `signal_monitor.py` — シグナル監視スクリプト（取引なし）
- `update_daily_data.py` — 日次データ蓄積スクリプト

**Bot戦略:**
- 条件: OI低(Q1-Q2) × FR低中(Q1-Q3) × Fee中(Q2-Q3) → Longエントリー
- バックテスト結果: 勝率84%、PF 10.67
- TP +10% / SL -5%

**リアルタイム化:**
- 全指標をAPIからリアルタイム取得（CSV依存なし）
- FR MA7: Hyperliquid `fundingHistory` API
- Fee MA7: Hypurrscan API

**VPSデプロイ（ConoHa）:**
- Ubuntu 22.04 / 2GB RAM
- screenで常駐化

---

## 自動取引Bot

### Bot設定（hype_bot.py）

```python
CONFIG = {
    "position_size_usd": 250,   # ポジションサイズ（USD）
    "leverage": 3,               # レバレッジ → 実効$750
    "tp_percent": 10.0,          # 利確 +10%
    "sl_percent": 5.0,           # 損切 -5%
    "check_interval": 3600,      # チェック間隔（1時間）
}
```

### 環境変数（.env）

```
HYPERLIQUID_PRIVATE_KEY=秘密鍵
HYPERLIQUID_WALLET_ADDRESS=ウォレットアドレス
```

### 実行コマンド

```bash
# テスト（注文しない）
python3 hype_bot.py --dry-run --once  # 1回
python3 hype_bot.py --dry-run         # ループ

# 本番
python3 hype_bot.py
```

### シグナル監視のみ

```bash
python3 signal_monitor.py
```

---

## VPSデプロイ手順

### 1. VPS契約

- ConoHa VPS 2GB（739円/月〜）
- Ubuntu 22.04
- セキュリティグループでSSH(22)許可

### 2. SSH接続

```bash
ssh root@VPSのIPアドレス
```

### 3. 環境構築

```bash
apt update && apt install git python3-pip screen -y
git clone https://github.com/osenaka/hype-trade.git
cd hype-trade
pip3 install hyperliquid-python-sdk python-dotenv
```

### 4. 秘密鍵設定

```bash
nano .env
# HYPERLIQUID_PRIVATE_KEY=xxx
# HYPERLIQUID_WALLET_ADDRESS=xxx
```

### 5. 動作確認

```bash
python3 hype_bot.py --dry-run --once
```

### 6. 常駐起動

```bash
screen -S hypebot
python3 hype_bot.py
# Ctrl+A → D で抜ける（バックグラウンド継続）
```

### 7. 再接続・停止

```bash
screen -r hypebot    # 再接続
# Ctrl+C で停止
```

---

## 注意事項（Bot運用）

- 秘密鍵は絶対にGitHubにアップしない（.gitignoreで除外済み）
- Bot用ウォレットは少額で運用推奨
- 本番前に必ず`--dry-run`でテスト
- ログは `bot_log.txt` に出力
