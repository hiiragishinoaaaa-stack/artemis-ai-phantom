"""grid_live_trader.py の単体テスト。実際のHyperliquid通信・ウォレットはモックする。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import config
import grid_live_trader
from grid_live_trader import (
    GridLiveTracker,
    check_pending_closes,
    check_pending_opens,
    execute_close,
    execute_open,
    is_ready,
    should_open_position,
)
from hyperliquid_client import OrderResult, OrderStatusResult, PostOnlyResult


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERP_GRID_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_CONFIRMED_RISK", True)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_MAX_OPEN_POSITIONS", 3)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_ORDER_USD", 10.0)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_SLIPPAGE", 0.01)
    monkeypatch.setattr(config, "PERP_GRID_LEVERAGE", 3.0)
    monkeypatch.setattr(config, "PERP_GRID_TAKE_PROFIT_PCT", 0.2)
    monkeypatch.setattr(config, "PERP_GRID_STOP_LOSS_PCT", -0.1)
    monkeypatch.setattr(config, "PERP_GRID_LIVE_FEE_PCT_PER_SIDE", 0.015)
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
    assert tracker.open_positions("BTC") == [position]

    tracker.record_close(position, exit_price=96.0, reason="take_profit", now=1010.0, leverage=3.0, fee_pct_per_side=0.015)
    assert position.closed is True
    assert tracker.has_open_position("BTC", 3) is False


def test_reopening_same_level_preserves_prior_closed_history():
    """過去のバグの回帰テスト(grid_paper_trader.pyと同じ設計上の欠陥):
    同じ水準が決済後に再利用されても、以前の決済記録が上書きされて
    消えないこと。
    """
    tracker = GridLiveTracker()
    first = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    tracker.record_close(first, exit_price=101.0, reason="take_profit", now=1010.0, leverage=3.0, fee_pct_per_side=0.0)

    second = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1020.0)
    tracker.record_close(second, exit_price=99.0, reason="stop_loss", now=1030.0, leverage=3.0, fee_pct_per_side=0.0)

    all_positions = tracker.all_positions("BTC")
    assert len(all_positions) == 2
    wins = sum(1 for p in all_positions if p.pnl_pct > 0)
    losses = sum(1 for p in all_positions if p.pnl_pct < 0)
    assert wins == 1
    assert losses == 1


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


def test_pending_open_position_not_counted_as_open_but_is_active():
    tracker = GridLiveTracker()
    tracker.record_pending_open("BTC", level_index=3, level_price=95.0, size=0.001, oid=42, now=1000.0)
    assert tracker.has_open_position("BTC", 3) is True  # 水準としては使用中
    assert tracker.open_positions("BTC") == []  # まだ約定していないので「保有中」には含まない
    assert len(tracker.pending_open_positions("BTC")) == 1
    assert len(tracker.active_positions("BTC")) == 1


def test_confirm_open_moves_position_from_pending_to_open():
    tracker = GridLiveTracker()
    position = tracker.record_pending_open("BTC", level_index=3, level_price=95.0, size=0.001, oid=42, now=1000.0)
    tracker.confirm_open(position, avg_price=95.05, filled_size=0.00105, now=1005.0)
    assert position.pending_open is False
    assert position.entry_price == 95.05
    assert position.size == 0.00105
    assert tracker.open_positions("BTC") == [position]
    assert tracker.pending_open_positions("BTC") == []


def test_remove_position_deletes_pending_open():
    tracker = GridLiveTracker()
    tracker.record_pending_open("BTC", level_index=3, level_price=95.0, size=0.001, oid=42, now=1000.0)
    tracker.remove_position("BTC", 3)
    assert tracker.has_open_position("BTC", 3) is False
    assert tracker.all_positions("BTC") == []


def test_record_pending_close_and_cancel_round_trip():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=95.0, size=0.001, avg_price=95.1, now=1000.0)
    tracker.record_pending_close(position, oid=7, reason="take_profit", now=1010.0)
    assert position.pending_close is True
    assert len(tracker.pending_close_positions("BTC")) == 1
    # まだ決済されてないので open_positions には引き続き含まれる(買いは約定済みのため)
    assert tracker.open_positions("BTC") == [position]

    tracker.cancel_pending_close(position)
    assert position.pending_close is False
    assert tracker.pending_close_positions("BTC") == []


# --- execute_open ---


def test_execute_open_records_pending_position_when_resting():
    tracker = GridLiveTracker()
    result = PostOnlyResult(success=True, resting=True, oid=42)
    with patch("grid_live_trader.hyperliquid_client.place_post_only_buy", return_value=result):
        position = execute_open(tracker, "BTC", level_index=3, level_price=95.0, mid_price=95.2, now=1000.0)
    assert position is not None
    assert position.pending_open is True
    assert position.open_oid == 42
    assert position.entry_price == 95.0
    assert tracker.has_open_position("BTC", 3) is True
    assert tracker.open_positions("BTC") == []


def test_execute_open_records_open_position_when_immediately_filled():
    tracker = GridLiveTracker()
    result = PostOnlyResult(success=True, filled=True, avg_price=95.2, filled_size=0.00105)
    with patch("grid_live_trader.hyperliquid_client.place_post_only_buy", return_value=result):
        position = execute_open(tracker, "BTC", level_index=3, level_price=95.0, mid_price=95.2, now=1000.0)
    assert position is not None
    assert position.pending_open is False
    assert tracker.open_positions("BTC") == [position]


def test_execute_open_returns_none_on_failure():
    tracker = GridLiveTracker()
    result = PostOnlyResult(success=False, error="Insufficient margin")
    with patch("grid_live_trader.hyperliquid_client.place_post_only_buy", return_value=result):
        position = execute_open(tracker, "BTC", level_index=3, level_price=95.0, mid_price=95.2, now=1000.0)
    assert position is None
    assert tracker.has_open_position("BTC", 3) is False


def test_execute_open_returns_none_when_mid_price_zero():
    tracker = GridLiveTracker()
    position = execute_open(tracker, "BTC", level_index=3, level_price=95.0, mid_price=0.0, now=1000.0)
    assert position is None


def test_execute_open_skips_when_level_already_active():
    tracker = GridLiveTracker()
    tracker.record_pending_open("BTC", level_index=3, level_price=95.0, size=0.001, oid=1, now=1000.0)
    with patch("grid_live_trader.hyperliquid_client.place_post_only_buy") as mock_place:
        position = execute_open(tracker, "BTC", level_index=3, level_price=95.0, mid_price=95.2, now=1001.0)
    assert position is None
    mock_place.assert_not_called()


# --- check_pending_opens ---


def test_check_pending_opens_confirms_fill():
    tracker = GridLiveTracker()
    position = tracker.record_pending_open("BTC", level_index=3, level_price=95.0, size=0.001, oid=42, now=1000.0)
    status = OrderStatusResult(found=True, is_filled=True, avg_price=95.05, filled_size=0.001)
    with patch("grid_live_trader.hyperliquid_client.query_order_status", return_value=status):
        check_pending_opens(tracker, now=1010.0)
    assert position.pending_open is False
    assert position.entry_price == 95.05
    assert tracker.open_positions("BTC") == [position]


def test_check_pending_opens_removes_canceled_order():
    tracker = GridLiveTracker()
    tracker.record_pending_open("BTC", level_index=3, level_price=95.0, size=0.001, oid=42, now=1000.0)
    status = OrderStatusResult(found=True, is_filled=False, is_open=False)
    with patch("grid_live_trader.hyperliquid_client.query_order_status", return_value=status):
        check_pending_opens(tracker, now=1010.0)
    assert tracker.has_open_position("BTC", 3) is False


def test_check_pending_opens_leaves_unresolved_order_when_status_unknown():
    tracker = GridLiveTracker()
    position = tracker.record_pending_open("BTC", level_index=3, level_price=95.0, size=0.001, oid=42, now=1000.0)
    status = OrderStatusResult(found=False)
    with patch("grid_live_trader.hyperliquid_client.query_order_status", return_value=status):
        check_pending_opens(tracker, now=1010.0)
    assert position.pending_open is True
    assert tracker.has_open_position("BTC", 3) is True


# --- execute_close ---


def test_execute_close_returns_false_when_position_still_pending_open():
    tracker = GridLiveTracker()
    position = tracker.record_pending_open("BTC", level_index=3, level_price=95.0, size=0.001, oid=1, now=1000.0)
    success = execute_close(tracker, position, "take_profit", now=1010.0)
    assert success is False


def test_execute_close_take_profit_places_resting_post_only_sell():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    result = PostOnlyResult(success=True, resting=True, oid=7)
    with patch("grid_live_trader.hyperliquid_client.place_post_only_sell", return_value=result) as mock_sell:
        success = execute_close(tracker, position, "take_profit", now=1010.0)
    assert success is True
    assert position.pending_close is True
    assert position.close_oid == 7
    assert position.closed is False
    args, _ = mock_sell.call_args
    assert args[0] == "BTC"
    assert args[2] == pytest.approx(100.2)  # entry * (1 + 0.2%)


def test_execute_close_take_profit_closes_immediately_when_filled():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    result = PostOnlyResult(success=True, filled=True, avg_price=100.2, filled_size=0.001)
    with (
        patch("grid_live_trader.hyperliquid_client.place_post_only_sell", return_value=result),
        patch("grid_live_trader.perp_market_data.estimate_funding_cost_pct", return_value=0.0),
    ):
        success = execute_close(tracker, position, "take_profit", now=1010.0)
    assert success is True
    assert position.closed is True
    assert position.exit_price == 100.2


def test_execute_close_take_profit_deducts_estimated_funding_cost():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    result = PostOnlyResult(success=True, filled=True, avg_price=100.2, filled_size=0.001)
    with (
        patch("grid_live_trader.hyperliquid_client.place_post_only_sell", return_value=result),
        patch("grid_live_trader.perp_market_data.estimate_funding_cost_pct", return_value=0.05) as mock_estimate,
    ):
        execute_close(tracker, position, "take_profit", now=1010.0)
    mock_estimate.assert_called_once_with("BTC", 1000.0, 1010.0, config.PERP_GRID_LEVERAGE)
    # 0.2%利確 * レバレッジ3倍 - 往復手数料(0.015%*2*3倍) - ファンディング0.05
    assert position.pnl_pct == pytest.approx(0.2 * 3.0 - 2 * 0.015 * 3.0 - 0.05)


def test_execute_close_take_profit_skips_when_already_pending_close():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    tracker.record_pending_close(position, oid=7, reason="take_profit", now=1005.0)
    with patch("grid_live_trader.hyperliquid_client.place_post_only_sell") as mock_sell:
        success = execute_close(tracker, position, "take_profit", now=1010.0)
    assert success is False
    mock_sell.assert_not_called()


def test_execute_close_stop_loss_uses_market_order():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    result = OrderResult(success=True, avg_price=99.5, filled_size=0.001)
    with (
        patch("grid_live_trader.hyperliquid_client.close_long", return_value=result),
        patch("grid_live_trader.perp_market_data.estimate_funding_cost_pct", return_value=0.0),
    ):
        success = execute_close(tracker, position, "stop_loss", now=1010.0)
    assert success is True
    assert position.closed is True
    assert position.exit_price == 99.5


def test_execute_close_stop_loss_cancels_pending_take_profit_first():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    tracker.record_pending_close(position, oid=7, reason="take_profit", now=1005.0)

    result = OrderResult(success=True, avg_price=99.0, filled_size=0.001)
    with (
        patch("grid_live_trader.hyperliquid_client.cancel_order", return_value=True) as mock_cancel,
        patch("grid_live_trader.hyperliquid_client.close_long", return_value=result),
        patch("grid_live_trader.perp_market_data.estimate_funding_cost_pct", return_value=0.0),
    ):
        success = execute_close(tracker, position, "stop_loss", now=1010.0)
    assert success is True
    mock_cancel.assert_called_once_with("BTC", 7)
    assert position.closed is True
    assert position.close_reason == "stop_loss"


def test_execute_close_returns_false_on_failure():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)

    result = OrderResult(success=False, error="Order rejected")
    with patch("grid_live_trader.hyperliquid_client.close_long", return_value=result):
        success = execute_close(tracker, position, "stop_loss", now=1010.0)
    assert success is False
    assert position.closed is False


# --- check_pending_closes ---


def test_check_pending_closes_confirms_fill():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    tracker.record_pending_close(position, oid=7, reason="take_profit", now=1005.0)

    status = OrderStatusResult(found=True, is_filled=True, avg_price=100.2, filled_size=0.001)
    with (
        patch("grid_live_trader.hyperliquid_client.query_order_status", return_value=status),
        patch("grid_live_trader.perp_market_data.estimate_funding_cost_pct", return_value=0.0),
    ):
        check_pending_closes(tracker, now=1010.0)
    assert position.closed is True
    assert position.exit_price == 100.2
    assert position.close_reason == "take_profit"


def test_check_pending_closes_confirms_fill_deducts_estimated_funding_cost():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    tracker.record_pending_close(position, oid=7, reason="take_profit", now=1005.0)

    status = OrderStatusResult(found=True, is_filled=True, avg_price=100.2, filled_size=0.001)
    with (
        patch("grid_live_trader.hyperliquid_client.query_order_status", return_value=status),
        patch("grid_live_trader.perp_market_data.estimate_funding_cost_pct", return_value=0.05) as mock_estimate,
    ):
        check_pending_closes(tracker, now=1010.0)
    mock_estimate.assert_called_once_with("BTC", 1000.0, 1010.0, config.PERP_GRID_LEVERAGE)
    assert position.pnl_pct == pytest.approx(0.2 * 3.0 - 2 * 0.015 * 3.0 - 0.05)


def test_check_pending_closes_reverts_to_open_when_canceled():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    tracker.record_pending_close(position, oid=7, reason="take_profit", now=1005.0)

    status = OrderStatusResult(found=True, is_filled=False, is_open=False)
    with patch("grid_live_trader.hyperliquid_client.query_order_status", return_value=status):
        check_pending_closes(tracker, now=1010.0)
    assert position.closed is False
    assert position.pending_close is False
    assert tracker.open_positions("BTC") == [position]


def test_check_pending_closes_leaves_unresolved_when_status_unknown():
    tracker = GridLiveTracker()
    position = tracker.record_open("BTC", level_index=3, entry_price=100.0, size=0.001, avg_price=100.0, now=1000.0)
    tracker.record_pending_close(position, oid=7, reason="take_profit", now=1005.0)

    status = OrderStatusResult(found=False)
    with patch("grid_live_trader.hyperliquid_client.query_order_status", return_value=status):
        check_pending_closes(tracker, now=1010.0)
    assert position.pending_close is True
    assert position.closed is False
