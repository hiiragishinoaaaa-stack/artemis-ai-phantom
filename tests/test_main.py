"""main.py の単体テスト(ネットワーク非依存の部分のみ)。"""
from __future__ import annotations

import pytest

from main import _RecentTokenNames, _decide_notification_action


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
        # 既に通知済み・★3つ到達・未送信 → 追い通知。
        (False, None, True, False, 3, "followup"),
        (False, "HIGH", True, False, 3, "followup"),
        # ★がまだ3つに届いていない → 何もしない。
        (False, None, True, False, 2, None),
        (False, None, True, False, 0, None),
        # 既に追い通知送信済み → 二重送信しない。
        (False, None, True, True, 3, None),
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
