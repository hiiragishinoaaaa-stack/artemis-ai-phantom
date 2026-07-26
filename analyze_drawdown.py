"""『損切りEV』を30分時点のスナップショットで検算する。

analyze_buckets.py が出す「-N%損切りEV」は、負けを一定幅で打ち切れたと
仮定した数字だが、同時に**勝った銘柄が途中で一度も下がらなかった**ことも
仮定している。実際には、1時間後に+120%になった銘柄が最初の数分で-40%まで
落ちていれば、-30%の損切りはそこで発動していて、その+120%は手に入らない。

つまりあの数字は、**負け側だけを切り詰めて勝ち側を丸ごと残した**見積もりで、
構造的に上振れる。MT5側の時間帯別分析で踏んだ「勝ちは全部残り、負けの
一部だけ消える」のと同じ形の偏り(mt5-ai-trader/RESEARCH_FINDINGS.md参照)。
符号が良く見えたときほど、集計の作りを疑う。

ここでは30分時点のチェックポイントを使って、その仮定を部分的に検算する。
**30分時点で既に損切り幅より下だった銘柄は、1時間後にいくら戻していても
途中で確実に切られている。** これを反映して期待値を計算し直す。

30分という粗い解像度では数分単位の急落は捉えられないので、この数字も
依然として上限の見積もりではある。ただし analyze_buckets.py の数字よりは
確実に厳しく、両者の差がそのまま「経路を無視したことによる水増し分」の
下限になる。差が大きいほど、あの損切りEVは信用できない。

使い方(VPS上、venv環境で):
  .venv/bin/python analyze_drawdown.py
  .venv/bin/python analyze_drawdown.py --stop-pct 50
  .venv/bin/python analyze_drawdown.py --stop-pct 30 --slippage-pct 15
  .venv/bin/python analyze_drawdown.py --exact      # 実測の最安値だけで確定計算

なお、最安値の実測が貯まればこの推定は不要になる(--exact)。
min_change_pct が記録されている銘柄については、損切りが発動したかどうかを
仮定ではなく事実として判定できる(outcome_tracker.py参照)。
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict

_DEFAULT_MAX_MCAP = 1_000_000_000.0


def load_pairs(path: str, early: int, late: int, max_mcap: float) -> tuple[list[dict], dict[str, int]]:
    """同じmintの早い/遅いチェックポイントを突き合わせて返す。

    両方の時点が揃っている銘柄だけを対象にする(片方しか無い銘柄は、
    経路の検算ができないので比較から外す)。

    戻り値の各要素は {"mint", "early_pct", "late_pct", "notify_mcap"}。
    """
    by_mint: dict[str, dict[int, dict]] = defaultdict(dict)
    counts = {"unresolved": 0, "dropped": 0, "exact_zero_change": 0}
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
        checkpoint = record.get("checkpoint_seconds")
        if checkpoint not in (early, late):
            continue
        change_pct = record.get("change_pct")
        if change_pct is None:
            counts["unresolved"] += 1
            continue
        notify_mcap = record.get("market_cap_at_notify_usd") or 0
        now_mcap = record.get("market_cap_now_usd") or 0
        if notify_mcap <= 0 or notify_mcap > max_mcap or now_mcap > max_mcap:
            counts["dropped"] += 1
            continue
        # ちょうど0.00%は、値が動かなかったのではなく取得に失敗して前回値が
        # そのまま残った記録の疑いがある(旧実装の挙動。main.py参照)。
        if change_pct == 0.0:
            counts["exact_zero_change"] += 1
        by_mint[record.get("mint")][checkpoint] = record

    pairs = []
    for mint, records in by_mint.items():
        if early not in records or late not in records:
            continue
        # min_change_pct は実測の最安値。記録が始まる前(2026-07以前)の分は
        # 入っていないので、その場合はNoneのまま持ち回る。
        min_pct = records[late].get("min_change_pct")
        if min_pct is None:
            min_pct = records[early].get("min_change_pct")
        pairs.append(
            {
                "mint": mint,
                "early_pct": records[early]["change_pct"],
                "late_pct": records[late]["change_pct"],
                "notify_mcap": records[late]["market_cap_at_notify_usd"],
                "min_pct": min_pct,
                "max_pct": records[late].get("max_change_pct"),
            }
        )
    return pairs, counts


def rows_with_measured_low(pairs: list[dict]) -> list[dict]:
    """最安値が実測されている記録だけを抜き出す。

    安値の記録は2026-07から。それ以前に集めた分は経路が分からないので、
    確定計算(exact_outcomes)には使えない。
    """
    return [p for p in pairs if p.get("min_pct") is not None]


def exact_outcomes(rows: list[dict], stop_pct: float, fill_pct: float) -> list[float]:
    """最安値を実測してある記録だけを使い、損切りありの成績を確定で出す。

    min_change_pct は通知後に実際に観測した最安値。これが損切り幅を割って
    いれば、そのポジションは確実に切られている。推定ではないので、
    path_checked_outcomes のような『まだ上限の見積もり』という但し書きが
    要らない唯一の計算。

    ただし観測間隔(既定2分)より短い急落は取りこぼす。約定価格の滑りと
    合わせて fill_pct で見ておくこと。
    """
    outcomes = []
    for row in rows:
        if row["min_pct"] <= -stop_pct:
            outcomes.append(-fill_pct)
        else:
            outcomes.append(row["late_pct"])
    return outcomes


def naive_outcomes(pairs: list[dict], stop_pct: float, fill_pct: float) -> list[float]:
    """analyze_buckets.py と同じ計算。最終結果だけを見て負けを打ち切る。"""
    return [p["late_pct"] if p["late_pct"] > -stop_pct else -fill_pct for p in pairs]


def path_checked_outcomes(pairs: list[dict], stop_pct: float, fill_pct: float) -> list[float]:
    """30分時点も見て、そこで既に切られていた銘柄を負けとして扱う。

    30分時点で損切り幅を割っていれば、1時間後にどれだけ戻していても
    そのポジションはもう手元に無い。
    """
    outcomes = []
    for pair in pairs:
        if pair["early_pct"] <= -stop_pct or pair["late_pct"] <= -stop_pct:
            outcomes.append(-fill_pct)
        else:
            outcomes.append(pair["late_pct"])
    return outcomes


def winner_survival(pairs: list[dict], stop_pct: float) -> tuple[int, int]:
    """(1時間後にプラスだった件数, そのうち30分時点で切られずに済んだ件数)。

    この2つが大きく離れるほど、『損切りEV』は経路を無視して水増しされている。
    """
    winners = [p for p in pairs if p["late_pct"] > 0]
    survived = [p for p in winners if p["early_pct"] > -stop_pct]
    return len(winners), len(survived)


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _print_row(label: str, pairs: list[dict], stop_pct: float, fill_pct: float) -> None:
    if not pairs:
        return
    naive = _mean(naive_outcomes(pairs, stop_pct, fill_pct))
    checked = _mean(path_checked_outcomes(pairs, stop_pct, fill_pct))
    winners, survived = winner_survival(pairs, stop_pct)
    survival_pct = survived / winners * 100 if winners else 0.0
    print(
        f"{label:<22}{len(pairs):>7}{naive:>13.1f}%{checked:>15.1f}%"
        f"{winners:>8}{survived:>9}{survival_pct:>9.1f}%"
    )


def _bucket(pairs: list[dict], edges: list[float]) -> tuple[dict[str, list[dict]], list[str]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    labels: list[str] = []
    bounds = [0.0] + edges + [float("inf")]
    for i in range(len(bounds) - 1):
        low, high = bounds[i], bounds[i + 1]
        label = f"${low:,.0f}〜" + ("上限なし" if high == float("inf") else f"${high:,.0f}")
        labels.append(label)
        for pair in pairs:
            if low <= pair["notify_mcap"] < high:
                groups[label].append(pair)
    return groups, labels


def sweep_table(
    pairs: list[dict], stops: list[float], slippage: float, min_count: int
) -> tuple[list[str], dict[str, list[float]]]:
    """損切り幅を振って、層ごとの30分検算EVを一覧にする。

    戻り値は(行ラベル, ラベル→各損切り幅でのEV)。件数が min_count 未満の層は
    偶然で符号が動くので落とす。
    """
    groups, labels = _bucket(pairs, [10_000, 30_000, 100_000, 300_000, 1_000_000])
    rows: dict[str, list[float]] = {}
    order: list[str] = []
    for label in labels:
        bucket = groups.get(label, [])
        if len(bucket) < min_count:
            continue
        order.append(label)
        rows[label] = [
            _mean(path_checked_outcomes(bucket, stop, stop + slippage)) for stop in stops
        ]
    order.append("全体")
    rows["全体"] = [_mean(path_checked_outcomes(pairs, stop, stop + slippage)) for stop in stops]
    return order, rows


def _print_sweep(pairs: list[dict], stops: list[float], slippage: float, min_count: int) -> None:
    order, rows = sweep_table(pairs, stops, slippage, min_count)
    print(f"\n=== 30分検算EV / 滑り {slippage:g}% ===")
    header = f"{'区分':<22}" + "".join(f"{f'-{s:g}%':>10}" for s in stops)
    print(header)
    for label in order:
        line = f"{label:<22}" + "".join(f"{v:>9.1f}%" for v in rows[label])
        print(line)


def _print_exact_sweep(rows: list[dict], stops: list[float], slippage: float, min_count: int) -> None:
    groups, labels = _bucket(rows, [10_000, 30_000, 100_000, 300_000, 1_000_000])
    print(f"\n=== 確定EV(実測の最安値ベース) / 滑り {slippage:g}% ===")
    print(f"{'区分':<22}{'件数':>7}" + "".join(f"{f'-{s:g}%':>10}" for s in stops))
    for label in labels + ["全体"]:
        bucket = rows if label == "全体" else groups.get(label, [])
        if len(bucket) < min_count and label != "全体":
            continue
        cells = "".join(
            f"{_mean(exact_outcomes(bucket, stop, stop + slippage)):>9.1f}%" for stop in stops
        )
        print(f"{label:<22}{len(bucket):>7}{cells}")


def main() -> None:
    parser = argparse.ArgumentParser(description="損切りEVを30分時点のデータで検算する")
    parser.add_argument("--path", default="logs/outcomes.jsonl")
    parser.add_argument("--early", type=int, default=1800, help="経路の確認に使う早い方の秒数")
    parser.add_argument("--late", type=int, default=3600, help="最終結果とする遅い方の秒数")
    parser.add_argument("--stop-pct", type=float, default=30.0, help="損切り幅(%%)。既定30")
    parser.add_argument(
        "--slippage-pct",
        type=float,
        default=0.0,
        help="損切りが滑る分(%%)。ラグ中の銘柄は指定した幅では約定しない。"
        "10を指定すると、-30%%の損切りが実際には-40%%で約定したものとして計算する",
    )
    parser.add_argument("--max-mcap", type=float, default=_DEFAULT_MAX_MCAP)
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="損切り幅と滑りを総当たりして一覧にする。1回で全パターンを確認できる",
    )
    parser.add_argument(
        "--stop-list", type=float, nargs="+", default=[20, 30, 40, 50, 60, 70], help="--sweep用"
    )
    parser.add_argument(
        "--slippage-list", type=float, nargs="+", default=[0, 10, 20, 30], help="--sweep用"
    )
    parser.add_argument(
        "--min-count", type=int, default=100, help="--sweepで表示する層の最低件数(既定100)"
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="実測の最安値が記録されている銘柄だけを使い、損切りありの成績を確定で出す。"
        "推定を挟まないので、こちらに十分な件数がたまったら --sweep は用済みになる",
    )
    args = parser.parse_args()

    fill_pct = args.stop_pct + args.slippage_pct
    pairs, counts = load_pairs(args.path, args.early, args.late, args.max_mcap)
    if not pairs:
        print(
            f"{args.path} に {args.early}秒 と {args.late}秒 が両方そろった銘柄がありませんでした。"
        )
        return

    measured = rows_with_measured_low(pairs)
    if args.exact:
        if not measured:
            print(
                "最安値(min_change_pct)が記録された銘柄がまだありません。"
                "\nbotを再起動して、新しい通知が1時間分たまるまで待つこと"
                "(記録開始前の分には経路が入っていないため、確定計算はできません)。"
            )
            return
        print(f"=== 最安値まで実測できた銘柄: {len(measured)}件 / 全{len(pairs)}件 ===")
        print("推定ではなく確定。損切りが発動したかどうかを事実として判定している")
        for slippage in args.slippage_list:
            _print_exact_sweep(measured, args.stop_list, slippage, args.min_count)
        print(
            "\n※これは推定ではない。ただし観測間隔(既定2分)より短い急落は取りこぼす。"
            "\n※件数が少ないうちは符号が動く。数百件たまるまで結論を出さないこと。"
        )
        return

    if measured:
        print(
            f"※最安値まで実測できた銘柄が{len(measured)}件たまっています。"
            "--exact を付けると推定ではなく確定値で計算できます。"
        )

    if args.sweep:
        print(f"=== {args.early}秒と{args.late}秒が両方そろった銘柄: {len(pairs)}件 ===")
        print(f"件数{args.min_count}件未満の層は偶然で符号が動くため省略")
        for slippage in args.slippage_list:
            _print_sweep(pairs, args.stop_list, slippage, args.min_count)
        print(
            "\n※縦が損切り幅、横が滑り。数字はその組み合わせでの1件あたり期待値(%)。"
            "\n※滑りは『-30%で切ったつもりが実際は-40%で約定した』という分。ラグ中の銘柄では必ず起きる。"
            "\n  滑り0%の列だけを見ないこと。20〜30%でも符号が保つ組み合わせだけが候補。"
            "\n※どの損切り幅でも符号が変わらない層は、その層が本物である傍証になる。"
            "\n  逆に、特定の1マスだけ突出している場合は偶然を拾っている。"
        )
        return

    print(f"=== {args.early}秒と{args.late}秒が両方そろった銘柄: {len(pairs)}件 ===")
    print(f"損切り幅: -{args.stop_pct:g}% / 実際の約定: -{fill_pct:g}%(滑り{args.slippage_pct:g}%)")
    if counts["exact_zero_change"]:
        print(
            f"※変化率がちょうど0.00%の記録が{counts['exact_zero_change']}件ある。"
            "値が動かなかったのではなく取得失敗の疑いがある"
        )

    print("\n=== 通知時の時価総額で層別 ===")
    print(
        f"{'区分':<22}{'件数':>7}{'経路無視EV':>14}{'30分検算EV':>16}"
        f"{'勝ち':>8}{'生存':>9}{'生存率':>10}"
    )
    groups, labels = _bucket(pairs, [10_000, 30_000, 100_000, 300_000, 1_000_000])
    for label in labels:
        _print_row(label, groups.get(label, []), args.stop_pct, fill_pct)
    print("-" * 86)
    _print_row("全体", pairs, args.stop_pct, fill_pct)

    naive = _mean(naive_outcomes(pairs, args.stop_pct, fill_pct))
    checked = _mean(path_checked_outcomes(pairs, args.stop_pct, fill_pct))
    winners, survived = winner_survival(pairs, args.stop_pct)

    print(
        "\n※『経路無視EV』は analyze_buckets.py と同じ計算。最終結果だけを見て負けを打ち切った数字。"
        "\n※『30分検算EV』は、30分時点で既に損切り幅を割っていた銘柄を『そこで切られた』として"
        "\n  計算し直した数字。こちらの方が実態に近い。"
        f"\n※『生存率』は、1時間後にプラスだった{winners}件のうち、30分時点で切られずに済んだ割合"
        f"({survived}件)。"
        "\n  ここが低いほど、損切りは勝ち銘柄も一緒に切り落としていることになる。"
    )
    if checked <= 0 < naive:
        print(
            f"\n→ 経路を無視すると{naive:+.1f}%だが、30分時点を見ただけで{checked:+.1f}%まで落ちる。"
            "\n  この損切り幅では勝ち銘柄を切りすぎている。--stop-pct を広げて再実行すること。"
        )
    elif checked > 0:
        print(
            f"\n→ 30分時点で検算しても{checked:+.1f}%を維持している。"
            "\n  ただし30分より短い急落は捉えられないので、これでもまだ上限の見積もり。"
            "\n  --slippage-pct を付けて、約定が滑った場合にも耐えるかを必ず確認すること。"
        )
    else:
        print(f"\n→ どちらの計算でもマイナス({naive:+.1f}% / {checked:+.1f}%)。この幅の損切りでは成立しない。")


if __name__ == "__main__":
    main()
