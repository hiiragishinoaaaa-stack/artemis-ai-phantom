"""通知結果を「通知時点の条件」で層別し、どの条件のときに伸びたのかを見る。

analyze_outcomes.py が全体の平均・中央値を出すのに対し、こちらは
「通知時の時価総額」「スコア」「Tier」といった**通知した時点で分かって
いた条件**ごとに結果を分けて並べる。伸びた層と伸びなかった層が分かれば、
それがそのまま通知条件の候補になる。

## 異常値の除外について

market_cap は供給量の取得失敗などで桁違いの値が入ることがある
(実際、時価総額$73,000,000,000という記録が混ざっていた。pump.fun発の
トークンでは物理的にありえない)。1件でも混ざると平均が壊れるため
(実測で、たった1件が全体平均を-20%から+5195%へ押し上げていた)、
--max-mcap を超える記録は既定で除外する。

平均は外れ値1件で簡単に壊れるので、判断は**中央値と勝率**で行うこと。

使い方(VPS上、venv環境で):
  .venv/bin/python analyze_buckets.py
  .venv/bin/python analyze_buckets.py --checkpoint 1800
  .venv/bin/python analyze_buckets.py --max-mcap 5000000
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict

# pump.fun発のトークンが1時間で到達しうる上限としては十分に緩い値。
# これを超える記録は供給量の取得ミス等によるデータ異常とみなす。
_DEFAULT_MAX_MCAP = 1_000_000_000.0


def load_records(path: str, checkpoint: int, max_mcap: float) -> tuple[list[dict], int]:
    """指定チェックポイントの記録を読み、異常値を除外して返す。

    戻り値は(採用した記録, 異常値として除外した件数)。
    """
    rows: list[dict] = []
    dropped = 0
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return [], 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("checkpoint_seconds") != checkpoint:
            continue
        if record.get("change_pct") is None:
            continue
        notify_mcap = record.get("market_cap_at_notify_usd") or 0
        now_mcap = record.get("market_cap_now_usd") or 0
        if notify_mcap <= 0 or notify_mcap > max_mcap or now_mcap > max_mcap:
            dropped += 1
            continue
        rows.append(record)
    return rows, dropped


def summarise(rows: list[dict]) -> tuple[int, float, float, float]:
    """(件数, 中央値, 勝率%, 上下5%を除いた平均)。"""
    changes = sorted(r["change_pct"] for r in rows)
    if not changes:
        return 0, 0.0, 0.0, 0.0
    wins = sum(1 for c in changes if c > 0)
    cut = len(changes) // 20
    trimmed = changes[cut : len(changes) - cut] or changes
    return len(changes), statistics.median(changes), wins / len(changes) * 100, statistics.mean(trimmed)


def _print_table(title: str, groups: dict[str, list[dict]], order: list[str]) -> None:
    print(f"\n=== {title} ===")
    print(f"{'区分':<22}{'件数':>7}{'中央値':>10}{'勝率':>9}{'刈込平均':>11}")
    for key in order:
        rows = groups.get(key)
        if not rows:
            continue
        n, median, win_rate, trimmed = summarise(rows)
        print(f"{key:<22}{n:>7}{median:>9.1f}%{win_rate:>8.1f}%{trimmed:>10.1f}%")


def bucket_by_mcap(rows: list[dict], edges: list[float]) -> tuple[dict[str, list[dict]], list[str]]:
    """通知時の時価総額で層に分ける。伸びた銘柄が低位に偏っていないかを見る。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    labels: list[str] = []
    bounds = [0.0] + edges + [float("inf")]
    for i in range(len(bounds) - 1):
        low, high = bounds[i], bounds[i + 1]
        label = f"${low:,.0f}〜" + ("上限なし" if high == float("inf") else f"${high:,.0f}")
        labels.append(label)
        for record in rows:
            mcap = record["market_cap_at_notify_usd"]
            if low <= mcap < high:
                groups[label].append(record)
    return groups, labels


def main() -> None:
    parser = argparse.ArgumentParser(description="通知結果を通知時点の条件で層別する")
    parser.add_argument("--path", default="logs/outcomes.jsonl")
    parser.add_argument("--checkpoint", type=int, default=3600, help="対象のチェックポイント秒数(既定3600)")
    parser.add_argument(
        "--max-mcap",
        type=float,
        default=_DEFAULT_MAX_MCAP,
        help="これを超える時価総額の記録はデータ異常として除外する",
    )
    args = parser.parse_args()

    rows, dropped = load_records(args.path, args.checkpoint, args.max_mcap)
    if not rows:
        print(f"{args.path} に {args.checkpoint}秒 の記録がありませんでした。")
        return

    n, median, win_rate, trimmed = summarise(rows)
    print(f"=== {args.checkpoint}秒後 / {n}件(データ異常として除外: {dropped}件) ===")
    print(f"中央値: {median:+.1f}% / 勝率: {win_rate:.1f}% / 上下5%を除いた平均: {trimmed:+.1f}%")
    print(f"参考(異常値を含む生の平均): {statistics.mean([r['change_pct'] for r in rows]):+.1f}%")

    groups, labels = bucket_by_mcap(rows, [10_000, 30_000, 100_000, 300_000, 1_000_000])
    _print_table("通知時の時価総額で層別", groups, labels)

    tier_groups: dict[str, list[dict]] = defaultdict(list)
    for record in rows:
        tier_groups[str(record.get("notified_tier") or "不明")].append(record)
    _print_table("通知Tierで層別", tier_groups, sorted(tier_groups))

    score_groups: dict[str, list[dict]] = defaultdict(list)
    for record in rows:
        score = record.get("notified_score") or 0
        score_groups[f"{score // 10 * 10}〜{score // 10 * 10 + 9}点"].append(record)
    _print_table("通知スコアで層別", score_groups, sorted(score_groups, key=lambda s: int(s.split("〜")[0])))

    print(
        "\n※判断は中央値と勝率で行うこと。平均は外れ値1件で簡単に壊れる"
        "\n  (実測で、たった1件のデータ異常が全体平均を-20%から+5195%へ押し上げていた)。"
        "\n※ある層だけ中央値・勝率が明確に良いなら、それが通知条件の候補になる。"
        "\n  ただし件数が少ない層は偶然で動くので、数十件以上あるかを必ず見ること。"
    )


if __name__ == "__main__":
    main()
