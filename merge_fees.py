"""
fee_daily.csv を daily_features.csv に結合する（pandas不要版）
────────────────────────────────────────────────────
実行方法:
  python3 merge_fees.py
"""

import csv
from pathlib import Path
from collections import defaultdict

FEE_CSV   = Path("hype_data/fee_daily.csv")
DAILY_CSV = Path("hype_data/daily_features.csv")
OUT_CSV   = Path("hype_data/daily_features.csv")  # 上書き

# ── fee_daily.csv 読み込み ────────────────────────────
print("fee_daily.csv 読み込み中...")
fee_map = {}  # date_str -> {fee_total, fee_spot, fee_perp}
with open(FEE_CSV, newline="") as f:
    for row in csv.DictReader(f):
        fee_map[row["date"]] = {
            "fee_total": row["fee_total"],
            "fee_spot":  row["fee_spot"],
            "fee_perp":  row["fee_perp"],
        }
print(f"  {len(fee_map)} 日分読み込み完了")

# ── daily_features.csv 読み込み ───────────────────────
print("daily_features.csv 読み込み中...")
with open(DAILY_CSV, newline="") as f:
    reader = csv.DictReader(f)
    orig_fields = reader.fieldnames[:]
    rows = list(reader)
print(f"  {len(rows)} 行 / カラム: {orig_fields[:5]}...")

# ── 既存fee列の削除 & 新列追加 ─────────────────────────
fee_cols = ["fee_total", "fee_spot", "fee_perp", "fee_ma7"]
# 既存からfee系を除いたフィールドリスト
base_fields = [c for c in orig_fields if c not in fee_cols]
new_fields   = base_fields + ["fee_total", "fee_spot", "fee_perp", "fee_ma7"]

# ── fee_ma7 計算用：日付順にfee_totalを並べる ─────────────
# まず全行に fee_total を付ける
date_col = orig_fields[0]  # 先頭列が日付インデックス
for row in rows:
    date_str = row[date_col][:10]  # YYYY-MM-DD
    if date_str in fee_map:
        row.update(fee_map[date_str])
    else:
        row["fee_total"] = ""
        row["fee_spot"]  = ""
        row["fee_perp"]  = ""

# fee_ma7 計算（直近7日の平均）
window = []
for row in rows:
    val = float(row["fee_total"]) if row["fee_total"] else None
    if val is not None:
        window.append(val)
        if len(window) > 7:
            window.pop(0)
        row["fee_ma7"] = f"{sum(window)/len(window):.2f}"
    else:
        row["fee_ma7"] = ""

# ── 書き出し ───────────────────────────────────────────
with open(OUT_CSV, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=new_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

# ── 結果サマリー ──────────────────────────────────────
fee_count = sum(1 for r in rows if r["fee_total"])
print(f"\n結合完了: {len(rows)} 行 / fee_total 有効: {fee_count} 日")
print(f"保存先: {OUT_CSV}")

# 末尾5行確認
print("\n最新5行（fee関連のみ）:")
for row in rows[-5:]:
    d = row[date_col][:10]
    ft = row.get("fee_total","")
    fm = row.get("fee_ma7","")
    print(f"  {d}: fee_total={ft:>12}  fee_ma7={fm:>12}")

print("\n次のステップ: python3 strategy_score.py")
