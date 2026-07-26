"""通知結果の「平均」を押し上げている外れ値を実際に目視するための調査ツール。

analyze_outcomes.py が出す平均が中央値と極端に食い違うとき(例: 中央値
-84.9%に対して平均+5195.7%)、それは次のどちらかを意味する。

1. 本当に数件だけ極端に伸びたトークンがあった(=宝くじ型の分布)
2. 通知時の時価総額が極端に小さく、わずかな値動きが%として爆発しただけ
   (=データ由来の見かけ上の大当たり)

この2つは戦略上まったく意味が違う。1なら「当たりを取りこぼさない設計」に
価値があるが、2なら平均自体が幻で、中央値だけを信じるべきになる。
どちらかは上位の生レコードを実際に見れば判別できるので、それを出す。

使い方(VPS上、venv環境で):
  .venv/bin/python inspect_outliers.py                     # 既定 logs/outcomes.jsonl の3600秒
  .venv/bin/python inspect_outliers.py logs/outcomes.jsonl 1800
"""
import json, statistics, sys
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else "logs/outcomes.jsonl"
cp = int(sys.argv[2]) if len(sys.argv) > 2 else 3600

rows = []
for line in open(path, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    if r.get("checkpoint_seconds") == cp and r.get("change_pct") is not None:
        rows.append(r)

if not rows:
    print(f"{cp}秒のレコードがありません。")
    raise SystemExit

changes = sorted(r["change_pct"] for r in rows)
print(f"=== {cp}秒後 / {len(rows)}件 ===")
print(f"平均   : {statistics.mean(changes):+.1f}%")
print(f"中央値 : {statistics.median(changes):+.1f}%")

print("\n【上位10件(ここに平均を押し上げてる犯人がいる)】")
print(f"{'変化率':>14}{'通知時の時価総額':>18}{'現在の時価総額':>18}  銘柄")
for r in sorted(rows, key=lambda r: -r["change_pct"])[:10]:
    print(f"{r['change_pct']:>13.1f}%{r.get('market_cap_at_notify_usd', 0):>18,.1f}"
          f"{r.get('market_cap_now_usd', 0):>18,.1f}  {r.get('symbol', '')}")

# 通知時の時価総額が極小だと、わずかな動きが%で爆発する(データ由来の偽の大当たり)
tiny = [r for r in rows if (r.get("market_cap_at_notify_usd") or 0) < 1000]
print(f"\n通知時の時価総額が$1,000未満: {len(tiny)}件 ({len(tiny)/len(rows)*100:.1f}%)")
if tiny:
    print("  → この層は%が爆発しやすく、平均を歪める。中央値で見るべき理由。")

# 極端な外れ値を除いた平均(トリム平均)。本当に儲かる形かの目安。
trimmed = changes[len(changes)//20 : len(changes) - len(changes)//20] or changes
print(f"\n上下5%を除いた平均: {statistics.mean(trimmed):+.1f}%")
print(f"プラスだった件数  : {sum(1 for c in changes if c > 0)}件 ({sum(1 for c in changes if c > 0)/len(changes)*100:.1f}%)")
import json, statistics, sys
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else "logs/outcomes.jsonl"
cp = int(sys.argv[2]) if len(sys.argv) > 2 else 3600

rows = []
for line in open(path, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    if r.get("checkpoint_seconds") == cp and r.get("change_pct") is not None:
        rows.append(r)

if not rows:
    print(f"{cp}秒のレコードがありません。")
    raise SystemExit

changes = sorted(r["change_pct"] for r in rows)
print(f"=== {cp}秒後 / {len(rows)}件 ===")
print(f"平均   : {statistics.mean(changes):+.1f}%")
print(f"中央値 : {statistics.median(changes):+.1f}%")

print("\n【上位10件(ここに平均を押し上げてる犯人がいる)】")
print(f"{'変化率':>14}{'通知時の時価総額':>18}{'現在の時価総額':>18}  銘柄")
for r in sorted(rows, key=lambda r: -r["change_pct"])[:10]:
    print(f"{r['change_pct']:>13.1f}%{r.get('market_cap_at_notify_usd', 0):>18,.1f}"
          f"{r.get('market_cap_now_usd', 0):>18,.1f}  {r.get('symbol', '')}")

# 通知時の時価総額が極小だと、わずかな動きが%で爆発する(データ由来の偽の大当たり)
tiny = [r for r in rows if (r.get("market_cap_at_notify_usd") or 0) < 1000]
print(f"\n通知時の時価総額が$1,000未満: {len(tiny)}件 ({len(tiny)/len(rows)*100:.1f}%)")
if tiny:
    print("  → この層は%が爆発しやすく、平均を歪める。中央値で見るべき理由。")

# 極端な外れ値を除いた平均(トリム平均)。本当に儲かる形かの目安。
trimmed = changes[len(changes)//20 : len(changes) - len(changes)//20] or changes
print(f"\n上下5%を除いた平均: {statistics.mean(trimmed):+.1f}%")
print(f"プラスだった件数  : {sum(1 for c in changes if c > 0)}件 ({sum(1 for c in changes if c > 0)/len(changes)*100:.1f}%)")
