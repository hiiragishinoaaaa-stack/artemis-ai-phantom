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

## 「ちょうど-100.0%」の扱い

現在時価総額がちょうど0の記録は、本物のラグ(価値が完全に消えた)かもしれないし、
DexScreenerがmarketCapを返さなかっただけかもしれない。旧実装は後者も0で保存して
いたため、両者が同じ「-100.0%」として混ざっている。層別表の下に『データ品質』の
欄を出すので、割合が高い層は --exclude-zero-now を付けて再実行し、数字が動くか
どうかを見ること。(記録側は修正済みで、以降の取得失敗はnullとして残る)

使い方(VPS上、venv環境で):
  .venv/bin/python analyze_buckets.py
  .venv/bin/python analyze_buckets.py --exclude-zero-now
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


def load_records(
    path: str, checkpoint: int, max_mcap: float, exclude_zero_now: bool = False
) -> tuple[list[dict], dict[str, int]]:
    """指定チェックポイントの記録を読み、異常値を除外して返す。

    戻り値は(採用した記録, 件数の内訳)。内訳のキーは:

    - ``dropped``      桁違いの時価総額(供給量の取得ミス等)として除外した件数
    - ``unresolved``   change_pctがnull、つまり最新時価総額を取得できなかった記録
    - ``zero_now``     現在時価総額がちょうど0の記録。**本物のラグとは限らない**

    ``zero_now`` は要注意。DexScreenerがmarketCapを返さなかっただけの記録も、
    旧実装では0として保存され、変化率が「ちょうど-100.0%」になっていた。
    ラグと取得失敗をここで区別できないため、``exclude_zero_now`` で外した場合と
    比べて数字が動くかどうかで、その層が本物かを判断する。
    (記録側は修正済み。以降は取得失敗をnullで残す。outcome_tracker.py参照)
    """
    rows: list[dict] = []
    counts = {"dropped": 0, "unresolved": 0, "zero_now": 0}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return [], counts

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
            counts["unresolved"] += 1
            continue
        notify_mcap = record.get("market_cap_at_notify_usd") or 0
        now_mcap = record.get("market_cap_now_usd") or 0
        if notify_mcap <= 0 or notify_mcap > max_mcap or now_mcap > max_mcap:
            counts["dropped"] += 1
            continue
        if now_mcap <= 0:
            counts["zero_now"] += 1
            if exclude_zero_now:
                continue
        rows.append(record)
    return rows, counts


def summarise(rows: list[dict]) -> tuple[int, float, float, float]:
    """(件数, 中央値, 勝率%, 上下5%を除いた平均)。"""
    changes = sorted(r["change_pct"] for r in rows)
    if not changes:
        return 0, 0.0, 0.0, 0.0
    wins = sum(1 for c in changes if c > 0)
    cut = len(changes) // 20
    trimmed = changes[cut : len(changes) - cut] or changes
    return len(changes), statistics.median(changes), wins / len(changes) * 100, statistics.mean(trimmed)


def expectancy_with_stop(rows: list[dict], stop_pct: float) -> tuple[float, float]:
    """損切りを入れた場合の1件あたり期待値と、勝った側の中央値を返す。

    ここまでの集計は全て「買って決済せず持ちっぱなし」の結果で、これは
    取りうる中で最悪の運用にあたる(実測で中央値-85%)。負けを一定で
    打ち切れば、同じ通知でも収支はまったく変わる。

    ただしチェックポイントの値しか無いため、途中の値動きは分からない。
    ここでは「損切り幅まで下がった銘柄は、そこで切れたはず」と仮定して
    負け側を stop_pct に置き換える。実際には一気に飛んで滑ることがあるので、
    この数字は**上振れた見積もり**として扱うこと(下限ではなく上限の目安)。
    """
    changes = [r["change_pct"] for r in rows]
    if not changes:
        return 0.0, 0.0
    capped = [c if c > -stop_pct else -stop_pct for c in changes]
    wins = [c for c in changes if c > 0]
    return statistics.mean(capped), statistics.median(wins) if wins else 0.0


def _print_table(title: str, groups: dict[str, list[dict]], order: list[str], stop_pct: float) -> None:
    print(f"\n=== {title} ===")
    print(
        f"{'区分':<22}{'件数':>7}{'中央値':>10}{'勝率':>9}{'刈込平均':>11}"
        f"{'勝ち中央値':>12}{f'-{stop_pct:g}%損切りEV':>16}"
    )
    for key in order:
        rows = groups.get(key)
        if not rows:
            continue
        n, median, win_rate, trimmed = summarise(rows)
        stopped_ev, win_median = expectancy_with_stop(rows, stop_pct)
        print(
            f"{key:<22}{n:>7}{median:>9.1f}%{win_rate:>8.1f}%{trimmed:>10.1f}%"
            f"{win_median:>11.1f}%{stopped_ev:>15.1f}%"
        )


def _print_quality(groups: dict[str, list[dict]], order: list[str]) -> None:
    """層ごとに「現在時価総額がちょうど0」の割合を出す。

    ちょうど0は、本物のラグ(価値が完全に消えた)か、DexScreenerが値を
    返さなかっただけかのどちらか。区別がつかないので、割合が高い層は
    その層の中央値・勝率ごと信用しない。
    """
    suspicious = [(key, groups[key]) for key in order if groups.get(key)]
    print("\n=== データ品質(現在時価総額がちょうど0の割合) ===")
    print(f"{'区分':<22}{'件数':>7}{'0の件数':>10}{'割合':>9}")
    for key, rows in suspicious:
        zeros = sum(1 for r in rows if (r.get("market_cap_now_usd") or 0) <= 0)
        print(f"{key:<22}{len(rows):>7}{zeros:>10}{zeros / len(rows) * 100:>8.1f}%")


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
    parser.add_argument(
        "--stop-pct",
        type=float,
        default=30.0,
        help="損切り幅(%%)。この幅で切っていた場合の1件あたり期待値を併記する(既定30)",
    )
    parser.add_argument(
        "--exclude-zero-now",
        action="store_true",
        help="現在時価総額がちょうど0の記録(取得失敗の疑い)を集計から外す。"
        "付けた場合と付けない場合で数字が大きく動く層は、その数字を信用しないこと",
    )
    args = parser.parse_args()

    rows, counts = load_records(args.path, args.checkpoint, args.max_mcap, args.exclude_zero_now)
    if not rows:
        print(f"{args.path} に {args.checkpoint}秒 の記録がありませんでした。")
        return

    n, median, win_rate, trimmed = summarise(rows)
    print(f"=== {args.checkpoint}秒後 / {n}件(データ異常として除外: {counts['dropped']}件) ===")
    print(f"中央値: {median:+.1f}% / 勝率: {win_rate:.1f}% / 上下5%を除いた平均: {trimmed:+.1f}%")
    print(f"参考(異常値を含む生の平均): {statistics.mean([r['change_pct'] for r in rows]):+.1f}%")
    if counts["unresolved"]:
        print(f"取得失敗として記録され、集計から外れた件数: {counts['unresolved']}件")
    if counts["zero_now"]:
        state = "除外済み" if args.exclude_zero_now else "集計に含む"
        print(
            f"現在時価総額がちょうど0の件数: {counts['zero_now']}件({state})"
            " ← 本物のラグか取得失敗かは区別できない"
        )

    groups, labels = bucket_by_mcap(rows, [10_000, 30_000, 100_000, 300_000, 1_000_000])
    _print_table("通知時の時価総額で層別", groups, labels, args.stop_pct)
    if not args.exclude_zero_now:
        _print_quality(groups, labels)

    tier_groups: dict[str, list[dict]] = defaultdict(list)
    for record in rows:
        tier_groups[str(record.get("notified_tier") or "不明")].append(record)
    _print_table("通知Tierで層別", tier_groups, sorted(tier_groups), args.stop_pct)

    score_groups: dict[str, list[dict]] = defaultdict(list)
    for record in rows:
        score = record.get("notified_score") or 0
        score_groups[f"{score // 10 * 10}〜{score // 10 * 10 + 9}点"].append(record)
    _print_table("通知スコアで層別", score_groups, sorted(score_groups, key=lambda s: int(s.split("〜")[0])), args.stop_pct)

    print(
        f"\n※『-{args.stop_pct:g}%損切りEV』は、負けをその幅で打ち切れた場合の1件あたり期待値。"
        "\n  ここまでの他の数字は全て『買って決済せず持ちっぱなし』の結果で、取りうる中で最悪の運用。"
        "\n  この列がプラスの層があれば、通知そのものより**出口の作り方**が効いていることになる。"
        "\n  ただし途中の値動きが分からないため上振れた見積もり(滑りを含まない)。参考値として見ること。"
        "\n※判断は中央値と勝率で行うこと。平均は外れ値1件で簡単に壊れる"
        "\n  (実測で、たった1件のデータ異常が全体平均を-20%から+5195%へ押し上げていた)。"
        "\n※ある層だけ中央値・勝率が明確に良いなら、それが通知条件の候補になる。"
        "\n  ただし件数が少ない層は偶然で動くので、数十件以上あるかを必ず見ること。"
        "\n※『中央値ちょうど-100.0%』の層が出たら、まず --exclude-zero-now を付けて再実行する。"
        "\n  数字が大きく動くなら、それはラグではなく時価総額の取得失敗を見ていた可能性が高い。"
    )


if __name__ == "__main__":
    main()
