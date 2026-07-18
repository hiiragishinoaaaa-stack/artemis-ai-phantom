"""dashboard_analytics.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import dashboard_analytics


def test_summarize_notifications_empty_list():
    result = dashboard_analytics.summarize_notifications([])
    assert result == {
        "total_notifications": 0,
        "primary_count": 0,
        "followup_count": 0,
        "tier_counts": {},
        "star_counts": {"0": 0, "1": 0, "2": 0, "3": 0},
    }


def test_summarize_notifications_counts_tiers_and_stars_from_primary_only():
    rows = [
        {"notification_type": "primary", "tier": "HIGH", "star_count": 3},
        {"notification_type": "primary", "tier": "WATCH", "star_count": 0},
        {"notification_type": "primary", "tier": "HIGH", "star_count": 1},
        # followup行は同じトークンの再掲なので、tier_counts/star_countsには含めない。
        {"notification_type": "followup", "tier": "HIGH", "star_count": 3},
    ]
    result = dashboard_analytics.summarize_notifications(rows)

    assert result["total_notifications"] == 4
    assert result["primary_count"] == 3
    assert result["followup_count"] == 1
    assert result["tier_counts"] == {"HIGH": 2, "WATCH": 1}
    assert result["star_counts"] == {"0": 1, "1": 1, "2": 0, "3": 1}


def test_summarize_notifications_ignores_unknown_star_count_values():
    rows = [{"notification_type": "primary", "tier": "HIGH", "star_count": 99}]
    result = dashboard_analytics.summarize_notifications(rows)
    assert result["star_counts"] == {"0": 0, "1": 0, "2": 0, "3": 0}


def test_summarize_outcomes_empty_list():
    assert dashboard_analytics.summarize_outcomes([]) == {}


def test_summarize_outcomes_groups_by_checkpoint_and_computes_win_rate():
    rows = [
        {"checkpoint_seconds": 1800, "change_pct": 50.0},
        {"checkpoint_seconds": 1800, "change_pct": -10.0},
        {"checkpoint_seconds": 1800, "change_pct": 20.0},
        {"checkpoint_seconds": 3600, "change_pct": -5.0},
    ]
    result = dashboard_analytics.summarize_outcomes(rows)

    assert result["1800"]["count"] == 3
    assert result["1800"]["win_rate_pct"] == round(2 / 3 * 100, 1)
    assert result["1800"]["avg_change_pct"] == round((50.0 - 10.0 + 20.0) / 3, 1)
    assert result["3600"]["count"] == 1
    assert result["3600"]["win_rate_pct"] == 0.0
    assert result["3600"]["avg_change_pct"] == -5.0


def test_summarize_outcomes_treats_zero_change_as_not_a_win():
    rows = [{"checkpoint_seconds": 1800, "change_pct": 0.0}]
    result = dashboard_analytics.summarize_outcomes(rows)
    assert result["1800"]["win_rate_pct"] == 0.0


def test_summarize_outcomes_skips_rows_missing_fields():
    rows = [{"checkpoint_seconds": 1800}, {"change_pct": 5.0}, {}]
    result = dashboard_analytics.summarize_outcomes(rows)
    assert result == {}


def test_summarize_outcomes_sorts_by_checkpoint_seconds():
    rows = [
        {"checkpoint_seconds": 86400, "change_pct": 1.0},
        {"checkpoint_seconds": 1800, "change_pct": 1.0},
        {"checkpoint_seconds": 3600, "change_pct": 1.0},
    ]
    result = dashboard_analytics.summarize_outcomes(rows)
    assert list(result.keys()) == ["1800", "3600", "86400"]
