"""通知条件の候補を並べて、どれが本当に効いているかを比べる。

## 何を出すツールか

ここが出すのは**数えただけの生の数字**(件数・中央値・勝率)だけ。損切りを
入れた場合の期待値は出さない。あれは途中の値動きが分からないと計算できず、
推定で埋めると損切りを狭くするほど良く見えるという嘘の傾向が出るため
(analyze_drawdown.py の冒頭を参照)。

勝率と中央値は**ただ数えただけ**なので、その手のバイアスと無関係に信用できる。

## 多重比較の罠を必ず踏む構造になっている

条件の組み合わせを何通りも試せば、効果がゼロでも偶然良く見えるものが必ず
出る。試す数が増えるほど、一番良く見えた条件が「たまたま」である確率は上がる。

そこでこのツールは、データを**前半と後半に分けて独立に測る**。
**両方で基準を上回った条件だけを ○ とし、片方だけのものは × として捨てる。**
片方だけ良いものを採用すると、ノイズを戦略にしてしまう。

MT5側で同じ罠を実際に踏んだ記録が mt5-ai-trader/RESEARCH_FINDINGS.md にある。

使い方(VPS上、venv環境で):
  .venv/bin/python analyze_filters.py
  .venv/bin/python analyze_filters.py --checkpoint 1800
"""
from __future__ import annotations

import argparse
import math
import statistics
from typing import Callable

from analyze_buckets import load_records

_DEFAULT_MAX_MCAP = 1_000_000_000.0
# 前半・後半それぞれでこの件数を下回る条件は、符号が偶然で動くので判定しない。
_MIN_HALF_COUNT = 40


def win_rate(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if r["change_pct"] > 0) / len(rows) * 100


def median_change(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    return statistics.median(r["change_pct"] for r in rows)


def win_rate_stderr(rows: list[dict]) -> float:
    """勝率の標準誤差(pp)。件数が少ない条件を切り捨てる判断に使う。"""
    n = len(rows)
    if n == 0:
        return 0.0
    p = win_rate(rows) / 100
    return math.sqrt(p * (1 - p) / n) * 100


def _mcap(record: dict) -> float:
    return record.get("market_cap_at_notify_usd") or 0.0


def _score(record: dict) -> int:
    return int(record.get("notified_score") or 0)


def build_filters() -> list[tuple[str, Callable[[dict], bool]]]:
    """比較する条件の一覧。

    最後の『Tier HIGHのみ』は**効かないと分かっている対照**として入れてある。
    層別で HIGH 22.4% / WATCH 21.7% と差が無かった条件なので、この行が
    基準とほぼ同じ数字になることで、表の『差が無い』側の見え方が分かる。
    """
    return [
        ("スコア90点以上", lambda r: _score(r) >= 90),
        ("スコア100点以上", lambda r: _score(r) >= 100),
        ("$100,000以上", lambda r: _mcap(r) >= 100_000),
        ("$1,000,000以上", lambda r: _mcap(r) >= 1_000_000),
        ("スコア100 かつ $100k以上", lambda r: _score(r) >= 100 and _mcap(r) >= 100_000),
        ("スコア100 かつ $1M以上", lambda r: _score(r) >= 100 and _mcap(r) >= 1_000_000),
        ("$30k〜$100kを除外", lambda r: not (30_000 <= _mcap(r) < 100_000)),
        ("Tier HIGHのみ(対照)", lambda r: str(r.get("notified_tier") or "") == "HIGH"),
    ]


def win_median(rows: list[dict]) -> float:
    """勝った側だけの中央値。伸びしろの代表値。"""
    wins = [r["change_pct"] for r in rows if r["change_pct"] > 0]
    return statistics.median(wins) if wins else 0.0


def loss_median(rows: list[dict]) -> float:
    """負けた側だけの中央値(マイナス)。1回の負けで失う代表値。"""
    losses = [r["change_pct"] for r in rows if r["change_pct"] <= 0]
    return statistics.median(losses) if losses else 0.0


def required_win_rate(rows: list[dict]) -> float:
    """収支が±0になるのに必要な勝率(%)。

    勝ち中央値と負け中央値の大きさから計算する。平均ではなく中央値を使うのは、
    平均が外れ値1件で壊れるため(実測で1件が全体平均を-20%から+5195%へ動かした)。

    そのぶん**大穴の1件を勘定に入れない厳しめの基準**になる。ここを実際の勝率が
    上回っていれば、宝くじが当たらなくても成立するということ。
    """
    w = win_median(rows)
    l = abs(loss_median(rows))
    if w + l <= 0:
        return 0.0
    return l / (w + l) * 100


def breakeven_loss(rows: list[dict]) -> float:
    """収支が±0になる「負けの大きさ」(%)。損切りをどこに置けばよいかの目標値。

    勝率 p、勝ち中央値 W のとき、±0の条件は p×W = (1-p)×L なので

        L = p × W ÷ (1 - p)

    **負けをこの幅より浅く止められれば、その条件は成立する。** 実測の負け中央値が
    これより深いなら、通知条件をどう変えても足りず、出口を作るしかない。
    """
    p = win_rate(rows) / 100
    if p <= 0 or p >= 1:
        return 0.0
    return p * win_median(rows) / (1 - p)


def evaluate(
    rows: list[dict], first_half: list[dict], second_half: list[dict],
    predicate: Callable[[dict], bool], baseline_win: float,
) -> dict:
    """1つの条件について、全体と前半・後半の成績をまとめる。

    前半・後半は**それぞれ自分の半分の基準勝率**と比べる。全体の基準と比べると、
    相場そのものが悪化した期間に測った側が不当に不利になる(実測で基準勝率が
    前半24.8%→後半19.3%と落ちていた)。見たいのは「相場が良かったか」ではなく
    「同じ時期の他の通知より良かったか」。
    """
    kept = [r for r in rows if predicate(r)]
    kept_first = [r for r in first_half if predicate(r)]
    kept_second = [r for r in second_half if predicate(r)]

    enough = len(kept_first) >= _MIN_HALF_COUNT and len(kept_second) >= _MIN_HALF_COUNT
    replicated = (
        enough
        and win_rate(kept_first) > win_rate(first_half)
        and win_rate(kept_second) > win_rate(second_half)
    )
    return {
        "count": len(kept),
        "median": median_change(kept),
        "win_rate": win_rate(kept),
        "stderr": win_rate_stderr(kept),
        "first": win_rate(kept_first),
        "second": win_rate(kept_second),
        "first_count": len(kept_first),
        "second_count": len(kept_second),
        "win_median": win_median(kept),
        "loss_median": loss_median(kept),
        "required": required_win_rate(kept),
        "breakeven_loss": breakeven_loss(kept),
        "enough": enough,
        "replicated": replicated,
    }


def _verdict(result: dict, baseline_win: float) -> str:
    if not result["enough"]:
        return "件数不足"
    if not result["replicated"]:
        return "×"
    # 差が誤差の2倍を超えているかどうかも見る(件数が多くても差が小さければ弱い)。
    lift = result["win_rate"] - baseline_win
    return "◎" if lift > result["stderr"] * 2 else "○"


def main() -> None:
    parser = argparse.ArgumentParser(description="通知条件の候補を前半・後半で検証しながら比較する")
    parser.add_argument("--path", default="logs/outcomes.jsonl")
    parser.add_argument("--checkpoint", type=int, default=3600)
    parser.add_argument("--max-mcap", type=float, default=_DEFAULT_MAX_MCAP)
    args = parser.parse_args()

    rows, counts = load_records(args.path, args.checkpoint, args.max_mcap)
    if not rows:
        print(f"{args.path} に {args.checkpoint}秒 の記録がありませんでした。")
        return

    # JSONLは追記式なので、並び順がそのまま時系列になる。
    middle = len(rows) // 2
    first_half, second_half = rows[:middle], rows[middle:]
    baseline_win = win_rate(rows)

    print(f"=== 通知条件の比較({args.checkpoint}秒後 / {len(rows)}件) ===")
    print("数えただけの生の数字。損切りの想定は一切入っていない\n")
    print(
        f"{'条件':<26}{'件数':>7}{'勝率':>8}{'基準比':>9}{'前半':>8}{'後半':>8}{'判定':>8}"
    )
    print(
        f"{'全体(基準)':<26}{len(rows):>7}{baseline_win:>7.1f}%"
        f"{'-':>9}{win_rate(first_half):>7.1f}%{win_rate(second_half):>7.1f}%{'-':>8}"
    )
    print(f"{'(各半分の基準)':<26}{'':>7}{'':>8}{'':>9}"
          f"{win_rate(first_half):>7.1f}%{win_rate(second_half):>7.1f}%{'':>8}")
    print("-" * 74)

    best: tuple[float, str, dict] | None = None
    evaluated: list[tuple[str, dict, str]] = []
    for label, predicate in build_filters():
        result = evaluate(rows, first_half, second_half, predicate, baseline_win)
        if not result["count"]:
            continue
        verdict = _verdict(result, baseline_win)
        evaluated.append((label, result, verdict))
        lift = result["win_rate"] - baseline_win
        print(
            f"{label:<26}{result['count']:>7}{result['win_rate']:>7.1f}%"
            f"{lift:>+8.1f}p{result['first']:>7.1f}%{result['second']:>7.1f}%{verdict:>8}"
        )
        # 勝率ではなく**収支までの距離**で選ぶ。絞り込むほど勝率は上がるが、
        # 大きく走る銘柄も一緒に捨てるため、勝率が最高の条件が収支では最悪に
        # なることが実際に起きた(スコア100かつ$100k以上: 勝率41.4%で最高、
        # 必要勝率59.6%で最下位)。
        margin = result["win_rate"] - result["required"]
        if verdict in ("○", "◎") and (best is None or margin > best[0]):
            best = (margin, label, result)

    print("\n=== 勝率だけでは足りない: 収支が成立するか ===")
    print(
        f"{'条件':<26}{'勝率':>8}{'勝ち中央値':>12}{'負け中央値':>12}"
        f"{'必要勝率':>10}{'収支':>7}{'必要な損切り':>14}"
    )
    for label, result, verdict in evaluated:
        if verdict not in ("○", "◎"):
            continue
        ok = "○" if result["win_rate"] > result["required"] else "×"
        print(
            f"{label:<26}{result['win_rate']:>7.1f}%{result['win_median']:>11.1f}%"
            f"{result['loss_median']:>11.1f}%{result['required']:>9.1f}%{ok:>7}"
            f"{-result['breakeven_loss']:>13.1f}%"
        )

    print(
        "\n※『基準比』は全体の勝率からの差(パーセントポイント)。"
        "\n※『前半』『後半』はデータを時系列で半分に割って独立に測った勝率。"
        "\n  条件を何通りも試せば、効果ゼロでも偶然良く見えるものが必ず出る。"
        "\n  **両方で基準を上回った条件だけが本物の候補**で、片方だけのものはノイズ。"
        f"\n※判定 ◎=両方で上回り、かつ差が誤差の2倍超 / ○=両方で上回るが差は小さめ"
        f"\n  ×=片方が基準以下(採用しない) / 件数不足=前半か後半が{_MIN_HALF_COUNT}件未満"
        "\n※中央値がマイナスでも問題ない。1時間持ちっぱなしの結果であって、"
        "\n  実際には勝った側だけ伸ばして負けを切る運用になるため。見るのは勝率。"
    )
    print(
        "\n※『必要勝率』は、勝ち中央値と負け中央値だけで収支が±0になる勝率。"
        "\n  実際の勝率がこれを上回っていれば、大穴が当たらなくても成立する。"
        "\n  平均ではなく中央値で計算しているので、**大穴を勘定に入れない厳しめの基準**。"
        "\n※『収支』が × の条件は、勝率が上がっていても勝ち幅が足りていない。"
        "\n  その場合は通知条件ではなく、**出口(損切り・利確)を作らないと成立しない**。"
        "\n※『必要な損切り』は、負けをこの幅より浅く止められれば収支が成立する、という目標値。"
        "\n  実測の負け中央値がこれより深いなら、その差がそのまま出口で埋めるべき分。"
    )
    if counts["zero_now"]:
        print(f"※現在時価総額がちょうど0の記録が{counts['zero_now']}件含まれている。")

    if best is None:
        print(
            "\n→ 前半・後半の両方で基準を上回った条件はありませんでした。"
            "\n  今のスコアと時価総額では、通知を絞っても勝率は上がらない。別の材料が要る。"
        )
    else:
        margin, label, result = best
        print(
            f"\n→ 収支に一番近い候補: 『{label}』"
            f"\n  勝率 {result['win_rate']:.1f}%(基準 {baseline_win:.1f}%)、{result['count']}件。"
            f"\n  前半 {result['first']:.1f}% / 後半 {result['second']:.1f}% で再現している。"
        )
        if margin > 0:
            print("  必要勝率を上回っており、出口を作らなくても成立する。")
        else:
            print(
                f"  ただし必要勝率に{-margin:.1f}ポイント足りない。"
                f"\n  **負けを -{result['breakeven_loss']:.0f}% より浅く止められれば成立する。**"
                f"(実測の負け中央値は {result['loss_median']:.1f}%)"
                "\n  つまり勝負は通知条件ではなく出口。analyze_drawdown.py --exact で"
                "\n  実際にその幅で切れたかを確認すること。"
            )
        print(
            "\n※候補は勝率ではなく**収支までの距離**で選んでいる。絞り込むほど勝率は上がるが、"
            "\n  大きく走る銘柄も一緒に捨てるため、勝率が最高の条件が収支では最下位になりうる。"
        )


if __name__ == "__main__":
    main()
