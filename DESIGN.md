# $HYPE Trading Toolkit — 設計メモ

## 背景と目的

Hyperliquid の `$HYPE` トレードにおいて重要とされる以下の指標を、
自分の手でデータとして取得・分析・検証するために作った。

```
重要指標（重要度順）
  1. Fee         — プロトコル収益。相場の熱量の代理変数
  2. USDC 入出金  — 資金フロー。大口の参入/退出の先行指標
  3. FR          — ファンディングレート。ポジション偏りの直接指標
  4. 出来高       — 参加者の活性度
  5. 現物 TWAP   — Perp の Mark Price 算出基準。乖離は収束圧力を生む
  6. OI / 清算   — ポジション残高と強制決済の状況
```

---

## スクリプト設計

### hype_analyzer.py

**役割:** スナップショット確認ツール。今この瞬間のHYPEの状態を把握する。

**設計方針:**
- 1 回実行すれば必要な情報がターミナルに揃うこと
- API コールは最小限（無駄なポーリングをしない）
- CSV オプションで時系列ログとしても使えること

**主な API エンドポイント:**

| エンドポイント | 取得内容 |
|---|---|
| `metaAndAssetCtxs` | 全 Perp の OI・出来高・FR・Mark Price を一括取得 |
| `fundingHistory` | 指定期間の FR 時系列（毎時決済のため最大 24×days 件） |
| `predictedFundings` | 次期 FR の予測値（現在のプレミアムから算出） |
| `spotMetaAndAssetCtxs` | Spot の価格・流通量 |
| `candleSnapshot` | OHLCV ローソク足（TWAP / VWAP 計算に使用） |

**Fee 推定の仮定:**
Hyperliquid API は Fee の直接取得エンドポイントを持たないため、
`dayNtlVlm`（24h 取引高）から以下の仮定で推計する。

```
推定日次 Fee = 取引高 × (Taker 0.035% × 60% + Maker 0.01% × 40%)
```

実際の Fee は [HypurrScan](https://hypurrscan.io/dashboard) / [ASXN](https://data.asxn.xyz/) を参照。

---

### hype_backtest.py

**役割:** ルールベース戦略の過去検証。シグナルの有効性を数値で確かめる。

**設計方針:**
- fetch → run → optimize の 3 ステップを明確に分離
- データは CSV に保存して再利用（API を毎回叩かない）
- 最適化結果を JSON で保持し、次回実行で自動読込

**エントリー条件の意図:**

```
Long の条件: FR < 閾値(負) AND 出来高 > MA比 AND OI 増加

  FR が負 = ショート勢がロング勢に払っている状態
          = ショートが多すぎる = アンワインド圧力がある
  出来高スパイク = 参加者が動いている = シグナルに信憑性がある
  OI 増加 = 新規ポジションが積み上がっている = トレンドに勢いがある
```

**OI プロキシについて:**
Hyperliquid の `metaAndAssetCtxs` で `openInterest` は取得できるが、
時系列としての蓄積がないため、バックテストでは `volume × close` の
3 期間移動平均の変化率を OI 変化の代理変数として使用している。

**グリッドサーチのスコア関数:**

```python
score = sharpe × 勝率 × (1 + PF) / (-MaxDD + 0.01)
```

単純に総利益最大化すると過学習しやすいため、
Sharpe（リターンの安定性）・勝率・PF・MaxDD を複合したスコアで評価。

---

### hype_factor_analysis.py

**役割:** 「どの指標が価格予測に有効か」を戦略設計前に検証する。

**設計方針:**
- 仮説検証ファースト。ルールを作る前にデータで裏付けを取る
- 分位分析（Quintile Analysis）が核心
- HTML ダッシュボードで視覚的に確認できること

**分位分析の読み方:**

各指標を Q1（最小値側）〜 Q5（最大値側）に 5 分割し、
それぞれの分位に属する時の将来リターン（平均・勝率）を比較する。

```
例: FR の分位分析

  Q1（FR が最も低い = ショート過多）→ 4h 後リターン平均 +0.8%、勝率 56%
  Q5（FR が最も高い = ロング過多）  → 4h 後リターン平均 -0.5%、勝率 44%

  → FR が低い時は買い有利、高い時は売り有利 というシグナルが有効と判断できる
```

ランダムな指標であれば全分位でリターン ≈ 0%、勝率 ≈ 50% に収束する。
そこからの乖離がシグナルの強さ。

**FR Zスコアを使う理由:**

生の FR 値だけでなく「いつもと比べてどれだけ偏っているか」も重要。
過去 48 本（= 8 日）の平均・標準偏差で正規化した Zスコアも分析対象に含めている。

```python
fr_zscore = (fr - fr.rolling(48).mean()) / fr.rolling(48).std()
```

---

## 既知の制限

| 制限 | 内容 | 回避策 |
|------|------|--------|
| USDC 入出金フロー | API に存在しない | Dune / DefiLlama を手動確認 |
| OI の時系列 | API は現在値のみ | volume×price をプロキシに使用 |
| 清算データ | API に直接なし | CoinGlass で確認 |
| Fee の実値 | API に直接なし | 出来高から推計 or HypurrScan |
| Spot TWAP | `@107` のインデックスは変わる可能性あり | Perp にフォールバック実装済み |
| Python 3.9 | `X \| Y` 型ヒント非対応 | `from __future__ import annotations` で解決済み |

---

## 今後の拡張アイデア

### 近い将来

- **定期実行スクリプト** — cron で `hype_analyzer.py --csv` を毎時実行してログを蓄積する
- **FR + 価格の乖離アラート** — FR が一定閾値を超えたら通知（LINE / Slack Webhook）
- **Spot-Perp 乖離モニタリング** — Premium の拡大/縮小をトラッキング

### 中期

- **より長い時系列でのファクター検証** — 現在 90 日。1 年以上でのロバストネス確認
- **機械学習シグナル** — 分位分析で有効と判明した指標を特徴量として LightGBM などで学習
- **ポジションサイジング** — Kelly 基準 / 固定分率の比較検証

### 長期

- **USDC フローの自動取得** — Dune API または Ethereum RPC から直接オンチェーンデータ取得
- **HLP Vault との相関分析** — HLP のポジションと価格の関係を追う
- **マルチアセット展開** — BTC / SOL など他 Perp への横展開

---

## 参考リンク

| リソース | URL |
|---------|-----|
| Hyperliquid API ドキュメント | https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api |
| HypurrScan (Fee / 統計) | https://hypurrscan.io/dashboard |
| ASXN Dashboard | https://data.asxn.xyz/ |
| HyperDash | https://hyperdash.com/?chart1=HYPE |
| DefiLlama - Fees | https://defillama.com/protocol/fees/hyperliquid |
| Dune - USDC Deposit | https://dune.com/kouei/hyperliquid-usdc-deposit |
| Coinalyze - FR | https://coinalyze.net/hyperliquid/funding-rate/ |
| CoinGlass - OI / 清算 | https://www.coinglass.com/currencies/HYPE/futures |
