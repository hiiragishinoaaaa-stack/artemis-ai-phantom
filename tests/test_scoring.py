"""scoring.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
import scoring
from token_watcher import TokenWatcher


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(config, "MIN_VOLUME_SOL_FOR_SCORE", 5.0)
    monkeypatch.setattr(config, "MIN_MARKET_CAP_SOL_FOR_SCORE", 15.0)
    monkeypatch.setattr(config, "HIGH_SCORE_THRESHOLD", 90)
    monkeypatch.setattr(config, "WATCH_SCORE_THRESHOLD", 80)
    monkeypatch.setattr(config, "LOW_SCORE_THRESHOLD", 70)


def _token(**overrides):
    watcher = TokenWatcher()
    token = watcher.on_token_created(
        mint="MINT1", name="Test", symbol="TEST", creator="c1", market_cap_sol=10.0, now=1000.0
    )
    token.buy_count = overrides.get("buy_count", 0)
    token.sell_count = overrides.get("sell_count", 0)
    token.unique_buyers = overrides.get("unique_buyers", set())
    token.total_volume_sol = overrides.get("total_volume_sol", 0.0)
    token.last_market_cap_sol = overrides.get("last_market_cap_sol", 0.0)
    return token


def test_compute_score_all_zero_when_nothing_happened():
    result = scoring.compute_score(_token())
    assert result.total == 0
    assert len(result.components) == 5


@pytest.mark.parametrize(
    "buy_count,expected_points",
    [(0, 0), (2, 0), (3, 10), (4, 10), (5, 20), (9, 20), (10, 30), (20, 30)],
)
def test_score_buy_count_tiers(buy_count, expected_points):
    component = scoring._score_buy_count(_token(buy_count=buy_count))
    assert component.points == expected_points


@pytest.mark.parametrize(
    "unique_count,expected_points",
    [(0, 0), (1, 0), (2, 10), (4, 10), (5, 20), (9, 20), (10, 30), (15, 30)],
)
def test_score_unique_buyers_tiers(unique_count, expected_points):
    buyers = {f"buyer{i}" for i in range(unique_count)}
    component = scoring._score_unique_buyers(_token(unique_buyers=buyers))
    assert component.points == expected_points


def test_score_buy_sell_ratio_no_sells():
    component = scoring._score_buy_sell_ratio(_token(buy_count=5, sell_count=0))
    assert component.points == 30


def test_score_buy_sell_ratio_zero_zero():
    component = scoring._score_buy_sell_ratio(_token(buy_count=0, sell_count=0))
    assert component.points == 0


@pytest.mark.parametrize(
    "buy_count,sell_count,expected_points",
    [(6, 2, 30), (4, 2, 20), (3, 2, 10), (2, 2, 0), (1, 2, 0)],
)
def test_score_buy_sell_ratio_tiers(buy_count, sell_count, expected_points):
    component = scoring._score_buy_sell_ratio(_token(buy_count=buy_count, sell_count=sell_count))
    assert component.points == expected_points


def test_score_volume_threshold():
    assert scoring._score_volume(_token(total_volume_sol=4.9)).points == 0
    assert scoring._score_volume(_token(total_volume_sol=5.0)).points == 10


def test_score_market_cap_threshold():
    assert scoring._score_market_cap(_token(last_market_cap_sol=14.9)).points == 0
    assert scoring._score_market_cap(_token(last_market_cap_sol=15.0)).points == 10


def test_compute_score_sums_all_components_and_caps_at_100():
    token = _token(
        buy_count=10,
        sell_count=0,
        unique_buyers={f"b{i}" for i in range(10)},
        total_volume_sol=10.0,
        last_market_cap_sol=20.0,
    )
    result = scoring.compute_score(token)
    assert result.total == 100  # 30+30+30+10+10 = 110 -> クランプされ100


@pytest.mark.parametrize(
    "score,expected_tier",
    [(100, "HIGH"), (90, "HIGH"), (89, "WATCH"), (80, "WATCH"), (79, "LOW"), (70, "LOW"), (69, None), (0, None)],
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
