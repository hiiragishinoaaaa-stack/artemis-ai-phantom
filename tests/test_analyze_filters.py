"""analyze_filters.py の単体テスト。ネットワーク不要で実行できる。

このツールの存在意義は「前半・後半の両方で再現した条件だけを採用する」ことに
あるので、テストの主眼は**片方でしか効いていない条件を確実に落とせるか**に置く。
落とせないなら、条件を総当たりしてノイズを戦略にしてしまう。
"""
from __future__ import annotations

from analyze_filters import evaluate, median_change, win_rate, win_rate_stderr, _verdict


def _rec(change_pct: float, score: int = 80, mcap: float = 50_000.0) -> dict:
    return {
        "change_pct": change_pct,
        "notified_score": score,
        "market_cap_at_notify_usd": mcap,
        "notified_tier": "HIGH",
    }


def _rows(wins: int, losses: int, **kw) -> list[dict]:
    return [_rec(50.0, **kw) for _ in range(wins)] + [_rec(-80.0, **kw) for _ in range(losses)]


def test_win_rate_counts_only_positive_changes():
    assert win_rate(_rows(1, 3)) == 25.0
    assert win_rate([]) == 0.0


def test_median_change_uses_the_middle_value_not_the_mean():
    rows = [_rec(-90.0), _rec(-80.0), _rec(1000.0)]
    assert median_change(rows) == -80.0


def test_win_rate_stderr_shrinks_as_the_sample_grows():
    assert win_rate_stderr(_rows(25, 75)) > win_rate_stderr(_rows(250, 750))


def test_a_filter_that_works_in_both_halves_is_accepted():
    first = _rows(20, 30, score=100)
    second = _rows(21, 29, score=100)
    rows = first + second
    result = evaluate(rows, first, second, lambda r: r["notified_score"] >= 100, baseline_win=20.0)

    assert result["replicated"] is True
    assert _verdict(result, 20.0) in ("○", "◎")


def test_a_filter_that_only_works_in_one_half_is_rejected():
    """これがこのツールの存在理由。

    合計で見ると基準を大きく上回るのに、実際は前半だけで稼いでいる条件。
    合計しか見ないと採用してしまう。
    """
    first = _rows(45, 5, score=100)  # 90%
    second = _rows(5, 45, score=100)  # 10%
    rows = first + second
    result = evaluate(rows, first, second, lambda r: r["notified_score"] >= 100, baseline_win=20.0)

    assert result["win_rate"] == 50.0  # 合計だけ見れば基準の2.5倍に見える
    assert result["replicated"] is False
    assert _verdict(result, 20.0) == "×"


def test_a_thin_filter_is_not_judged_at_all():
    """件数が足りない条件は、良く見えても判定しない(偶然で符号が動くため)。"""
    first = _rows(5, 0, score=100)
    second = _rows(5, 0, score=100)
    rows = first + second
    result = evaluate(rows, first, second, lambda r: r["notified_score"] >= 100, baseline_win=20.0)

    assert result["win_rate"] == 100.0
    assert result["enough"] is False
    assert _verdict(result, 20.0) == "件数不足"


def test_a_large_lift_is_marked_more_strongly_than_a_small_one():
    big_first, big_second = _rows(30, 20), _rows(31, 19)
    big = evaluate(big_first + big_second, big_first, big_second, lambda r: True, baseline_win=20.0)

    small_first, small_second = _rows(11, 39), _rows(11, 39)
    small = evaluate(
        small_first + small_second, small_first, small_second, lambda r: True, baseline_win=20.0
    )

    assert _verdict(big, 20.0) == "◎"
    assert _verdict(small, 20.0) == "○"


def test_a_filter_matching_nothing_reports_zero_rather_than_crashing():
    rows = _rows(5, 5)
    result = evaluate(rows, rows[:5], rows[5:], lambda r: False, baseline_win=20.0)
    assert result["count"] == 0
    assert result["enough"] is False
