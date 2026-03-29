# Memory

## Projects
| Name | What |
|------|------|
| **hype-trade** | $HYPEのトレード戦略バックテストプロジェクト |

→ 詳細: memory/projects/hype-trade.md

## Terms
| Term | Meaning |
|------|---------|
| FR | Funding Rate（資金調達率） |
| OI | Open Interest（建玉残高） |
| TWAP | Time-Weighted Average Price（時間加重平均価格） |
| TP | Take Profit（利確） |
| SL | Stop Loss（損切） |
| PF | Profit Factor |
| bridged USDC | HyperliquidブリッジされたUSDC残高 |
| bridge_flow | USDCの入出金フロー |

## Preferences
- 長期セッションをまたぐ重要情報は必ずこのファイルに記録すること

# currentDate
Today's date is 2026-03-28.

## Fee・Buyback 定義（2026-03-28確定）

### Fee（プロトコル手数料）
- **定義**: トレーダーがHyperliquidでトレード時に支払う手数料
- **料率**: Perps taker 0.035%〜0.045%、maker 0.01%〜
- **分配**: HLP、Assistance Fund（約97%）、Deployers
- **取得**: `https://api.hypurrscan.io/fees`（全期間取得可能）

### Buyback（バイバック）
- **定義**: Assistance Fund（0xfefe）がUSDCでHYPEを市場購入
- **仕組み**: Fee → Assistance Fund → HYPE購入 → burn
- **取得制限**:
  - ASXN: 2025-03-20〜（USD金額のみ）
  - Hyperliquid API: 直近31日のみ

### シグナル分析サマリー
- **Fee急減時（<-5%）**: +3.89%、勝率57.4% → Long有効
- **Fee中間帯（Q2-Q3）**: +5.3%、勝率62% → 最良ゾーン
- **低Buyback時（≤$2M）**: +4.82%、勝率59.4%

→ 詳細: memory/projects/hype-trade.md

## 0xfefe バイバック調査結果（2026-03-25更新）

### 結論：バイバックは止まっていなかった
- Duneデータ（251,942 HYPE / 6日間）は不完全なデータセットの断片にすぎない
- ASXNキャッシュで確認: 2025-03-20以降も毎日継続してバイバック実施
- 「11ヶ月の空白」は完全なデータ誤認

### バイバック全体像（USD、ASXNデータ）
- 期間: 2025-03-20〜2026-03-20（366日間、途切れなし）
- 合計: **$821,687,496**（約8.2億ドル）
- 月次平均: ~$63M/月
- ピーク月: 2025年8月（$110,338,772）
- データ: hype_data/asxn_cache.json の `fee.HyperCore Buybacks`

### 月次サマリー（USD）
| 月 | バイバック額 |
|----|------------|
| 2025-03（3/20〜） | $10.8M |
| 2025-04 | $41.9M |
| 2025-05 | $70.4M |
| 2025-06 | $62.5M |
| 2025-07 | $89.7M |
| 2025-08 | $110.3M |
| 2025-09 | $80.9M |
| 2025-10 | $103.2M |
| 2025-11 | $71.4M |
| 2025-12 | $47.0M |
| 2026-01 | $53.3M |
| 2026-02 | $48.3M |
| 2026-03（〜3/20） | $31.9M |

### 0xfefeアドレス個別データ（直近31日のみ取得可能）
- 期間: 2026-02-20〜2026-03-22（APIの31日制限）
- 累計: 1,309,345 HYPE / $43,036,889
- 日次平均: 42,237 HYPE / $1,388,287
- 平均価格: $32.87/HYPE
- データ: hype_data/fefe_buybacks.csv

### データソース信頼性メモ
- ⚠️ Dune `dune.gfi_research.dataset_assistant_fund_hyperliquid` → 不完全（最初の断片のみ）
- ✅ ASXN `fee.HyperCore Buybacks` → 2025-03-20以降の全期間USD金額
- ✅ Hyperliquid API `userFillsByTime` → 直近31日のHYPE数量・価格
- 2025年中のHYPE数量は不明（ASXNはUSDのみ、APIは31日制限）

### ローンチ期（2024-11〜2025-03）の調査結果（2026-03-25確定）
- **2024年中もバイバックあった可能性が高い**（後述のentryNtlから強く示唆）
- `userFillsByTime` APIは直近31日のみ保持（古いデータは破棄）→ 2024年の fills は取得不可
- `userNonFundingLedgerUpdates` の `spotGenesis` レコードは**HYPEではなくWOW/MEOW/NEIRO等**の他スポットトークン
  - 新規スポット上場時にAssistance Fundが割当を受けるもので、バイバックとは別
- ASXN `fee` データは2025-03-20からしか存在しない（それ以前は追跡していなかった）
- **実際のバイバック開始**: TGE当日（2024-11-29）から開始していた可能性大（下記根拠）

### entryNtlから計算したバイバック全体像（2026-03-25確認）
- 0xfefeの現在HYPE保有: **42,571,128 HYPE**
- 現保有のコスト合計（entryNtl）: **$1,031,563,091**
- ASXN追跡分（2025-03-20〜2026-03-20）: $821,687,496
- 直近5日（2026-03-21〜2026-03-25）推定: $6,941,435
- **pre-ASXN期間の推定支出**: ~$202,934,160（111日間、日次$1.83M/日）
- ⚠️ entryNtlは**現保有分のみ**のコスト。SendAssetで既に送り出したHYPEのコストは含まれないため、**真の累計支出は$1.03B超**
- 全期間平均購入単価: **$24.23/HYPE**
