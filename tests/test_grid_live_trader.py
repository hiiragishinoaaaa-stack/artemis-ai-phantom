"""grid_live_trader.py の単体テスト。実際のHyperliquid通信・ウォレットはモックする。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import config
import grid_live_trader
from grid_live_trader import GridLiveTracker, execute_close, execute_open, is_ready, should_open_position
from hyperliquid_client import OrderResult


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERP_GRID_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_CONFIRMED_RISK", True)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_MAX_OPEN_POSITIONS", 3)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_ORDER_USD", 10.0)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_SLIPPAGE", 0.01)
    monkeypatch.setattr(config, "PERP_GRID_LEVERAGE", 3.0)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_FEE_PCT_PER_SIDE", 0.045)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_POSITIONS_FILE_PATH", tmp_path / "grid_live_positions.json")
    monkeypatch.setattr(config, "DISCORD_ENABLED", False)  # 通知の実送信はしない


def test_is_ready_false_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "PERP_GRID_LIVE_ENABLED", False)
    ready, status = is_ready()
    assert ready is False
    assert "PERP_GRID_LIVE_ENABLED" in status


def test_is_ready_false_when_risk_not_confirmed(monkeypatch):
    monkeypatch.setattr(config, "PERP_GRID_LIVE_CONFIRMED_RISK", False)
    ready, status = is_ready()
    assert ready is False
    assert "PERP_GRID_LIVE_CONFIRMED_RISK" in status


def test_is_ready_false_when_wallet_missing(monkeypatch):
    import hyperliquid_wallet

    monkeypatch.setattr(config, "HYPERLIQUID_PRIVATE_KEY", "")
    hyperliquid_wallet._cached_account = None
    hyperliquid_wallet._load_attempted = False
    ready, status = is_ready()
    assert ready is False
    assert "HYPERLIQUID_PRIVATE_KEY" in status


def test_should_open_position_true_when_ready(monkeypatch):
    with patch("grid_live_trader.is_ready", return_value=(True, "ready")):
        should_open, reason = should_open_position(open_position_count=0)
    assert should_open is True
    assert reason == "ok"


def test_should_open_position_false_when_max_positions_open(monkeypatch):
    with patch("grid_live_trader.is_ready", return_value=(True, "ready")):
        should_open, reason = should_open_position(open_position_count=3)
    assert should_open is False
    assert reason == "max_positions_open"


def test_should_open_position_false_when_not_ready():
    with patch("grid_live_trader.is_ready", return_value=(False, "PERP_GRID_LIVE_ENABLED=false")):
        should_open, reason = should_open_position(open_position_count=0)
    assert should_open is False
    assert reason == "PERP_GRID_LIVE_ENABLED=false"


def test_tracker_open_and_close_position_round_trip():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=95.0, size=0.001, avg_price=95.1, now=1000.0)
    assert tracker.has_open_position("BTC", 3) is True

    tracker.record_close(position, exit_price=96.0, reason="take_profit", now=1010.0, leverage=3.0, fee_pct_per_side=0.045)
    assert position.closed is True
    assert tracker.has_open_position("BTC", 3) is False


def test_tracker_persists_to_disk_and_reloads():
    tracker = GridLiveTracker()
    tracker.set_center_price("BTC", 100.0)
    tracker.record_open("BTC", level_index=3, entry_price=95.0, size=0.001, avg_price=95.1, now=1000.0)

    assert config.PERP_GRID_LIVE_POSITIONS_FILE_PATH.exists()
    reloaded = GridLiveTracker()
    assert reloaded.center_price("BTC") == 100.0
    assert reloaded.has_open_position("BTC", 3) is True


def test_last_price_is_none_before_first_set():
    tracker = GridLiveTracker()
    assert tracker.last_price("BTC") is None


def test_set_last_price_persists_to_disk_and_reloads():
    tracker = GridLiveTracker()
    tracker.set_last_price("BTC", 95.2)
    assert tracker.last_price("BTC") == 95.2

    reloaded = GridLiveTracker()
    assert reloaded.last_price("BTC") == 95.2


def test_execute_open_records_position_on_success():
    tracker = GridLiveTracker()
    result = OrderResult(success=True, avg_price=95.2, filled_size=0.00105)
    with patch("grid_live_trader.hyperliquid_client.open_long", return_value=result):
        position = execute_open(tracker, "BTC", level_index=3, level_price=95.0, mid_price=95.2, now=1000.0)
    assert position is not None
    assert position.entry_price == 95.0
    assert position.size == 0.00105
    assert tracker.has_open_position("BTC", 3) is True


def test_execute_open_returns_none_on_failure():
    tracker = GridLiveTracker()
    result = OrderResult(success=False, error="Insufficient margin")
    with patch("grid_live_trader.hyperliquid_client.open_long", return_value=result):
        position = execute_open(tracker, "BTC", level_index=3, level_price=95.0, mid_price=95.2, now=1000.0)
    assert position is None
    assert tracker.has_open_position("BTC", 3) is False


def test_execute_open_returns_none_when_mid_price_zero():
    tracker = GridLiveTracker()
    position = execute_open(tracker, "BTC", level_index=3, level_price=95.0, mid_price=0.0, now=1000.0)
    assert position is None


def test_execute_close_closes_position_on_success():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=95.0, size=0.001, avg_price=95.1, now=1000.0)

    result = OrderResult(success=True, avg_price=96.0, filled_size=0.001)
    with patch("grid_live_trader.hyperliquid_client.close_long", return_value=result):
        success = execute_close(tracker, position, "take_profit", now=1010.0)
    assert success is True
    assert position.closed is True
    assert position.exit_price == 96.0


def test_execute_close_returns_false_on_failure():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=95.0, size=0.001, avg_price=95.1, now=1000.0)

    result = OrderResult(success=False, error="Order rejected")
    with patch("grid_live_trader.hyperliquid_client.close_long", return_value=result):
        success = execute_close(tracker, position, "stop_loss", now=1010.0)
    assert success is False
    assert position.closed is False
