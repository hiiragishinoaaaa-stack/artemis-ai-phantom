"""scoring.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
import scoring
from token_watcher import TokenWatcher


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(config, "MIN_VOLUME_USD_FOR_SCORE", 300.0)
    monkeypatch.setattr(config, "MIN_LIQUIDITY_USD_FOR_SCORE", 2000.0)
    monkeypatch.setattr(config, "HIGH_SCORE_THRESHOLD", 75)
    monkeypatch.setattr(config, "WATCH_SCORE_THRESHOLD", 50)
    monkeypatch.setattr(config, "LOW_SCORE_THRESHOLD", 35)


def _token(**overrides):
    watcher = TokenWatcher()
    token = watcher.start_tracking(mint="MINT1", name="Test", symbol="TEST", now=1000.0)
    token.buys_m5 = overrides.get("buys_m5", 0)
    token.sells_m5 = overrides.get("sells_m5", 0)
    token.unique_buyers_m5 = overrides.get("unique_buyers_m5", 0)
    token.volume_m5_usd = overrides.get("volume_m5_usd", 0.0)
    token.liquidity_usd = overrides.get("liquidity_usd", 0.0)
    token.price_change_m5_pct = overrides.get("price_change_m5_pct", 0.0)
    token.rugcheck_checked = overrides.get("rugcheck_checked", False)
    token.rugcheck_danger = overrides.get("rugcheck_danger", False)
    token.rugcheck_danger_reason = overrides.get("rugcheck_danger_reason", "")
    token.blocked_creator_reason = overrides.get("blocked_creator_reason", "")
    return token


def test_compute_score_all_zero_when_nothing_happened():
    result = scoring.compute_score(_token())
    assert result.total == 0
    assert len(result.components) == 8


@pytest.mark.parametrize(
    "buys,expected_points",
    [(0, 0), (4, 0), (5, 10), (9, 10), (10, 20), (19, 20), (20, 30), (40, 30)],
)
def test_score_buys_m5_tiers(buys, expected_points):
    component = scoring._score_buys_m5(_token(buys_m5=buys))
    assert component.points == expected_points


def test_score_buy_sell_ratio_no_sells():
    component = scoring._score_buy_sell_ratio(_token(buys_m5=5, sells_m5=0))
    assert component.points == 30


def test_score_buy_sell_ratio_zero_zero():
    component = scoring._score_buy_sell_ratio(_token(buys_m5=0, sells_m5=0))
    assert component.points == 0


@pytest.mark.parametrize(
    "buys,sells,expected_points",
    [(6, 2, 30), (4, 2, 20), (3, 2, 10), (2, 2, 0), (1, 2, 0)],
)
def test_score_buy_sell_ratio_tiers(buys, sells, expected_points):
    component = scoring._score_buy_sell_ratio(_token(buys_m5=buys, sells_m5=sells))
    assert component.points == expected_points


@pytest.mark.parametrize(
    "unique_buyers,expected_points",
    [(0, 0), (1, 0), (2, 5), (4, 5), (5, 10), (9, 10), (10, 20), (30, 20)],
)
def test_score_unique_buyers_m5_tiers(unique_buyers, expected_points):
    component = scoring._score_unique_buyers_m5(_token(unique_buyers_m5=unique_buyers))
    assert component.points == expected_points


def test_score_volume_m5_threshold():
    assert scoring._score_volume_m5(_token(volume_m5_usd=299.0)).points == 0
    assert scoring._score_volume_m5(_token(volume_m5_usd=300.0)).points == 10


def test_score_liquidity_threshold():
    assert scoring._score_liquidity(_token(liquidity_usd=1999.0)).points == 0
    assert scoring._score_liquidity(_token(liquidity_usd=2000.0)).points == 10


@pytest.mark.parametrize(
    "change,expected_points",
    [(-10.0, 0), (0.0, 0), (0.1, 5), (19.9, 5), (20.0, 10), (49.9, 10), (50.0, 20), (100.0, 20)],
)
def test_score_price_change_m5_tiers(change, expected_points):
    component = scoring._score_price_change_m5(_token(price_change_m5_pct=change))
    assert component.points == expected_points


def test_compute_score_sums_all_components_and_caps_at_100():
    token = _token(
        buys_m5=20,
        sells_m5=0,
        volume_m5_usd=10000.0,
        liquidity_usd=10000.0,
        price_change_m5_pct=100.0,
        rugcheck_checked=True,
        rugcheck_danger=False,
    )
    result = scoring.compute_score(token)
    assert result.total == 100  # 30+30+10+10+20+10 = 110 -> クランプされ100


def test_score_rugcheck_safety_unchecked_gives_no_points():
    assert scoring._score_rugcheck_safety(_token(rugcheck_checked=False)).points == 0


def test_score_rugcheck_safety_checked_and_safe_gives_bonus():
    component = scoring._score_rugcheck_safety(_token(rugcheck_checked=True, rugcheck_danger=False))
    assert component.points == 10


def test_score_rugcheck_safety_danger_gives_large_negative_penalty():
    component = scoring._score_rugcheck_safety(
        _token(rugcheck_checked=True, rugcheck_danger=True, rugcheck_danger_reason="Single holder ownership")
    )
    assert component.points < 0
    assert "Single holder ownership" in component.detail


def test_compute_score_forces_zero_when_rugcheck_danger_detected_even_with_max_other_components():
    token = _token(
        buys_m5=20,
        sells_m5=0,
        volume_m5_usd=10000.0,
        liquidity_usd=10000.0,
        price_change_m5_pct=100.0,
        rugcheck_checked=True,
        rugcheck_danger=True,
        rugcheck_danger_reason="Mint authority still active",
    )
    result = scoring.compute_score(token)
    assert result.total == 0


def test_score_creator_blocklist_no_match_gives_no_points():
    assert scoring._score_creator_blocklist(_token(blocked_creator_reason="")).points == 0


def test_score_creator_blocklist_match_gives_large_negative_penalty():
    component = scoring._score_creator_blocklist(_token(blocked_creator_reason="通知後に-95%下落"))
    assert component.points < 0
    assert "通知後に-95%下落" in component.detail


def test_compute_score_forces_zero_when_creator_blocklisted_even_with_max_other_components():
    token = _token(
        buys_m5=20,
        sells_m5=0,
        volume_m5_usd=10000.0,
        liquidity_usd=10000.0,
        price_change_m5_pct=100.0,
        rugcheck_checked=True,
        rugcheck_danger=False,
        blocked_creator_reason="RugCheck危険フラグ: Mint authority still active",
    )
    result = scoring.compute_score(token)
    assert result.total == 0


@pytest.mark.parametrize(
    "score,expected_tier",
    [(100, "HIGH"), (75, "HIGH"), (74, "WATCH"), (50, "WATCH"), (49, "LOW"), (35, "LOW"), (34, None), (0, None)],
)
def test_tier_for_score(score, expected_tier):
    assert scoring.tier_for_score(score) == expected_tier


def test_is_upgrade_from_none():
    assert scoring.is_upgrade(None, "LOW") is True
    assert scoring.is_upgrade(None, None) is False


def test_is_upgrade_rank_comparison():
    assert scoring.is_upgrade("LOW", "WATCH") is True
    assert scoring.is_upgrade("WATCH", "HIGH") is True
    assert scoring.is_upgrade("HIGH", "WATCH") is False
    assert scoring.is_upgrade("WATCH", "WATCH") is False
    assert scoring.is_upgrade("WATCH", "LOW") is False
    assert scoring.is_upgrade("WATCH", None) is False
