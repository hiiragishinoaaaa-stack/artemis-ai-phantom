"""analyze_filters.py の単体テスト。ネットワーク不要で実行できる。

このツールの存在意義は「前半・後半の両方で再現した条件だけを採用する」ことに
あるので、テストの主眼は**片方でしか効いていない条件を確実に落とせるか**に置く。
落とせないなら、条件を総当たりしてノイズを戦略にしてしまう。
"""
from __future__ import annotations

import pytest

from analyze_filters import (
    breakeven_loss,
    evaluate,
    loss_median,
    median_change,
    required_win_rate,
    win_median,
    win_rate,
    win_rate_stderr,
    _verdict,
)


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
    # 各半分には、条件に当たらない通知も混ぜる(そうしないと条件とその半分の
    # 基準が同じものになり、比較が成立しない)。
    first = _rows(5, 45) + _rows(20, 30, score=100)
    second = _rows(5, 45) + _rows(21, 29, score=100)
    rows = first + second
    baseline = win_rate(rows)
    result = evaluate(rows, first, second, lambda r: r["notified_score"] >= 100, baseline)

    assert result["replicated"] is True
    assert _verdict(result, baseline) in ("○", "◎")


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
    def _measure(kept_wins: int, kept_losses: int, other_wins: int, other_losses: int) -> tuple:
        half = _rows(other_wins, other_losses) + _rows(kept_wins, kept_losses, score=100)
        rows = half + half
        baseline = win_rate(rows)
        result = evaluate(rows, half, half, lambda r: r["notified_score"] >= 100, baseline)
        return result, baseline

    big, big_baseline = _measure(30, 70, 10, 190)  # 条件30% / 基準13.3%
    small, small_baseline = _measure(7, 38, 8, 92)  # 条件15.6% / 基準10.3%

    assert _verdict(big, big_baseline) == "◎"
    assert _verdict(small, small_baseline) == "○"


def test_a_filter_matching_nothing_reports_zero_rather_than_crashing():
    rows = _rows(5, 5)
    result = evaluate(rows, rows[:5], rows[5:], lambda r: False, baseline_win=20.0)
    assert result["count"] == 0
    assert result["enough"] is False


def test_each_half_is_compared_against_its_own_baseline():
    """相場自体が悪化した期間を、条件のせいにしない。

    後半は全体が沈んでいる。条件は後半でも「同じ時期の他より上」なのに、
    全体の基準と比べると下に見えてしまう。
    """
    first_other = _rows(30, 70)  # 前半の基準 30%
    second_other = _rows(10, 90)  # 後半の基準 10%
    first_kept = _rows(20, 30, score=100)  # 40% > 30%
    second_kept = _rows(8, 42, score=100)  # 16% > 10%(全体基準20%は下回る)

    first = first_other + first_kept
    second = second_other + second_kept
    rows = first + second
    baseline = win_rate(rows)

    result = evaluate(rows, first, second, lambda r: r["notified_score"] >= 100, baseline)

    assert result["second"] < baseline  # 全体基準と比べたら負けている
    assert result["replicated"] is True  # それでも同時期の他よりは上


def test_a_filter_below_its_own_half_baseline_is_still_rejected():
    """対照(効かない条件)は、基準の変え方に関係なく落ちること。"""
    first_other, second_other = _rows(30, 70), _rows(10, 90)
    first_kept = _rows(20, 30, score=100)  # 40% > 30%
    second_kept = _rows(4, 46, score=100)  # 8% < 10%

    first = first_other + first_kept
    second = second_other + second_kept
    rows = first + second

    result = evaluate(rows, first, second, lambda r: r["notified_score"] >= 100, win_rate(rows))
    assert result["replicated"] is False


def test_required_win_rate_reflects_how_far_winners_run():
    """負けが-90%で勝ちが+90%なら、収支±0には50%の勝率が要る。"""
    rows = [_rec(90.0), _rec(-90.0)]
    assert required_win_rate(rows) == 50.0

    # 勝ちが3倍に伸びるなら、必要な勝率は25%まで下がる
    rows = [_rec(270.0), _rec(-90.0)]
    assert required_win_rate(rows) == 25.0


def test_required_win_rate_uses_medians_so_one_moonshot_cannot_move_it():
    """大穴1件で必要勝率が下がってしまうと、成立していない条件を通してしまう。"""
    base = [_rec(50.0), _rec(50.0), _rec(-90.0), _rec(-90.0)]
    with_moonshot = base + [_rec(100000.0)]

    assert required_win_rate(with_moonshot) == required_win_rate(base)


def test_win_and_loss_medians_split_the_two_sides():
    rows = [_rec(10.0), _rec(90.0), _rec(-50.0), _rec(-90.0)]
    assert win_median(rows) == 50.0
    assert loss_median(rows) == -70.0


def test_breakeven_loss_is_the_stop_width_that_makes_a_filter_pay():
    """勝率40% / 勝ち中央値+90% なら、負けを-60%で止めれば±0。"""
    rows = _rows(4, 6)  # 勝ち+50%, 負け-80% の既定
    # 既定の値ではなく、意図した数字で作り直す
    rows = [_rec(90.0) for _ in range(4)] + [_rec(-99.0) for _ in range(6)]
    assert breakeven_loss(rows) == pytest.approx(60.0)


def test_breakeven_loss_matches_the_required_win_rate_view():
    """同じ事実の裏返しなので、2つの指標は必ず整合する。"""
    rows = [_rec(100.0) for _ in range(4)] + [_rec(-50.0) for _ in range(6)]
    # 実測の負けが必要な損切り幅より浅いなら、必要勝率も下回っているはず
    assert abs(loss_median(rows)) < breakeven_loss(rows)
    assert win_rate(rows) > required_win_rate(rows)


def test_breakeven_loss_is_zero_when_nothing_ever_wins():
    assert breakeven_loss([_rec(-90.0), _rec(-80.0)]) == 0.0


def test_star_and_speed_filters_are_ignored_on_records_that_predate_them():
    """古い記録には★も経過秒数も無い。0扱いになり、条件に当たらないだけで落ちない。"""
    from analyze_filters import _elapsed, _max_stars

    old = {"change_pct": 10.0, "notified_score": 100, "market_cap_at_notify_usd": 5_000_000.0}
    assert _max_stars(old) == 0
    assert _elapsed(old) == 0


def test_star_and_speed_filters_read_the_new_fields():
    from analyze_filters import _elapsed, _max_stars

    fresh = {"max_star_count": 3, "notified_elapsed_seconds": 60}
    assert _max_stars(fresh) == 3
    assert _elapsed(fresh) == 60


def test_build_filters_includes_the_star_and_speed_conditions():
    from analyze_filters import build_filters

    labels = [label for label, _ in build_filters()]
    assert "★1つ以上に到達" in labels
    assert "卒業60秒以内に通知" in labels
    assert "$1M以上 かつ 60秒以内" in labels
