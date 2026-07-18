"""main.py の単体テスト(ネットワーク非依存の部分のみ)。"""
from __future__ import annotations

import pytest

from main import _RecentTokenNames, _build_notification_row, _build_outcome_row, _decide_notification_action
from outcome_tracker import TrackedOutcome
from scoring import ScoreResult
from token_watcher import TokenWatcher


def test_remember_and_get_round_trip():
    cache = _RecentTokenNames()
    cache.remember("MINT1", "Test Coin", "TEST")
    assert cache.get("MINT1") == ("Test Coin", "TEST")


def test_get_returns_empty_tuple_for_unknown_mint():
    cache = _RecentTokenNames()
    assert cache.get("UNKNOWN") == ("", "")


def test_remember_ignores_entries_with_no_name_and_no_symbol():
    cache = _RecentTokenNames()
    cache.remember("MINT1", "", "")
    assert cache.get("MINT1") == ("", "")


def test_remember_evicts_oldest_when_over_capacity():
    cache = _RecentTokenNames(max_size=2)
    cache.remember("OLD", "Old Coin", "OLD")
    cache.remember("MID", "Mid Coin", "MID")
    cache.remember("NEW", "New Coin", "NEW")  # 上限超過、最古(OLD)が間引かれる

    assert cache.get("OLD") == ("", "")
    assert cache.get("MID") == ("Mid Coin", "MID")
    assert cache.get("NEW") == ("New Coin", "NEW")


@pytest.mark.parametrize(
    "is_tier_upgrade,tier,discord_notified,stars_followup_sent,star_count,expected",
    [
        # 初到達: HIGH/WATCHは通常通知、LOWは何もしない(discord_notifiedが
        # まだFalseなので、後段のfollowup条件にも入らない)。
        (True, "HIGH", False, False, 0, "primary"),
        (True, "WATCH", False, False, 0, "primary"),
        (True, "LOW", False, False, 0, None),
        # 既に通知済み・★1つ以上を確認・未送信 → 追い通知(★1つでも発火する)。
        (False, None, True, False, 1, "followup"),
        (False, None, True, False, 2, "followup"),
        (False, "HIGH", True, False, 3, "followup"),
        # ★がまだ0のまま → 何もしない。
        (False, None, True, False, 0, None),
        # 既に追い通知送信済み → 二重送信しない。
        (False, None, True, True, 1, None),
        # 一度もDiscordへ実通知していない(LOW止まり等) → 追い通知もしない。
        (False, None, False, False, 3, None),
    ],
)
def test_decide_notification_action(
    is_tier_upgrade, tier, discord_notified, stars_followup_sent, star_count, expected
):
    action = _decide_notification_action(is_tier_upgrade, tier, discord_notified, stars_followup_sent, star_count)
    assert action == expected


def test_decide_notification_action_ignores_followup_conditions_on_upgrade_checkpoint():
    """同じチェックポイントでtier昇格とfollowup条件がどちらも満たされても、
    優先されるのはprimaryであり、followupと二重発火はしない。"""
    action = _decide_notification_action(
        is_tier_upgrade=True,
        tier="HIGH",
        discord_notified=True,
        stars_followup_sent=False,
        star_count=3,
    )
    assert action == "primary"


def test_build_notification_row_includes_all_supabase_fields():
    watcher = TokenWatcher()
    token = watcher.start_tracking(mint="MINT1", name="Test Coin", symbol="TEST", now=1000.0)
    token.unique_buyers_m5 = 10
    token.buys_m5 = 20
    token.sells_m5 = 2
    token.volume_m5_usd = 1234.5
    token.liquidity_usd = 5000.0
    token.price_change_m5_pct = 42.0
    token.market_cap_usd = 80000.0
    token.rugcheck_danger = False
    token.rugcheck_warn_count = 1
    token.creator = "CreatorAddr1"

    row = _build_notification_row(token, ScoreResult(total=90, components=[]), "HIGH", 60, "primary")

    assert row == {
        "mint": "MINT1",
        "name": "Test Coin",
        "symbol": "TEST",
        "notification_type": "primary",
        "tier": "HIGH",
        "score": 90,
        "unique_buyers_m5": 10,
        "star_count": 3,
        "buys_m5": 20,
        "sells_m5": 2,
        "volume_m5_usd": 1234.5,
        "liquidity_usd": 5000.0,
        "price_change_m5_pct": 42.0,
        "market_cap_usd": 80000.0,
        "rugcheck_danger": False,
        "rugcheck_warn_count": 1,
        "creator": "CreatorAddr1",
        "elapsed_seconds": 60,
    }


def test_build_outcome_row_matches_supabase_schema():
    outcome = TrackedOutcome(
        mint="MINT1",
        name="Test Coin",
        symbol="TEST",
        notified_at=1000.0,
        notified_tier="HIGH",
        notified_score=90,
        market_cap_at_notify_usd=10000.0,
        last_market_cap_usd=15000.0,
    )

    row = _build_outcome_row(outcome, checkpoint_seconds=1800, change_pct=50.0)

    assert row == {
        "mint": "MINT1",
        "name": "Test Coin",
        "symbol": "TEST",
        "notified_tier": "HIGH",
        "notified_score": 90,
        "checkpoint_seconds": 1800,
        "market_cap_at_notify_usd": 10000.0,
        "market_cap_now_usd": 15000.0,
        "change_pct": 50.0,
    }
