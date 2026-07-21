"""trade_executor.py の単体テスト。実際のネットワーク送信・署名はモックする。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import config
import trade_executor
from jupiter_client import SwapResult
from position_tracker import PositionTracker
from token_watcher import TokenWatcher


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AUTO_TRADE_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_TRADE_CONFIRMED_RISK", True)
    monkeypatch.setattr(config, "AUTO_TRADE_MIN_SCORE", 100)
    monkeypatch.setattr(config, "AUTO_TRADE_MAX_ELAPSED_SECONDS_FOR_ENTRY", 60)
    monkeypatch.setattr(config, "AUTO_TRADE_MAX_OPEN_POSITIONS", 3)
    monkeypatch.setattr(config, "AUTO_TRADE_BUY_AMOUNT_SOL", 0.02)
    monkeypatch.setattr(config, "AUTO_TRADE_SLIPPAGE_BPS", 500)
    monkeypatch.setattr(config, "POSITIONS_FILE_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(config, "TRADES_FILE_PATH", tmp_path / "trades.jsonl")
    monkeypatch.setattr(config, "DISCORD_TRADE_WEBHOOK_URL", "")


def _token(**overrides):
    watcher = TokenWatcher()
    token = watcher.start_tracking(mint="MINT1", name="Test Coin", symbol="TEST", now=1000.0)
    token.has_pair_data = overrides.get("has_pair_data", True)
    token.liquidity_usd = overrides.get("liquidity_usd", 5000.0)
    token.price_usd = overrides.get("price_usd", 0.001)
    token.rugcheck_danger = overrides.get("rugcheck_danger", False)
    token.blocked_creator_reason = overrides.get("blocked_creator_reason", "")
    token.duplicate_name_reason = overrides.get("duplicate_name_reason", "")
    return token


def test_should_auto_buy_true_when_all_conditions_met():
    should_buy, reason = trade_executor.should_auto_buy(_token(), elapsed_seconds=0, score_total=100, open_position_count=0)
    assert should_buy is True
    assert reason == "ok"


def test_should_auto_buy_false_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "AUTO_TRADE_ENABLED", False)
    should_buy, reason = trade_executor.should_auto_buy(_token(), 0, 100, 0)
    assert should_buy is False
    assert reason == "auto_trade_disabled"


def test_should_auto_buy_false_when_risk_not_confirmed(monkeypatch):
    monkeypatch.setattr(config, "AUTO_TRADE_CONFIRMED_RISK", False)
    should_buy, reason = trade_executor.should_auto_buy(_token(), 0, 100, 0)
    assert should_buy is False
    assert reason == "auto_trade_disabled"


def test_should_auto_buy_false_when_rugcheck_danger_even_with_perfect_score():
    should_buy, reason = trade_executor.should_auto_buy(
        _token(rugcheck_danger=True), elapsed_seconds=0, score_total=100, open_position_count=0
    )
    assert should_buy is False
    assert reason == "rugcheck_danger"


def test_should_auto_buy_false_when_creator_blocklisted():
    should_buy, reason = trade_executor.should_auto_buy(
        _token(blocked_creator_reason="通知後に-95%下落"), elapsed_seconds=0, score_total=100, open_position_count=0
    )
    assert should_buy is False
    assert reason == "creator_blocklisted"


def test_should_auto_buy_false_when_duplicate_name_detected():
    should_buy, reason = trade_executor.should_auto_buy(
        _token(duplicate_name_reason="既出です"), elapsed_seconds=0, score_total=100, open_position_count=0
    )
    assert should_buy is False
    assert reason == "duplicate_name"


def test_should_auto_buy_false_when_no_pair_data():
    should_buy, reason = trade_executor.should_auto_buy(
        _token(has_pair_data=False), elapsed_seconds=0, score_total=100, open_position_count=0
    )
    assert should_buy is False
    assert reason == "no_pair_data"


def test_should_auto_buy_false_when_price_zero():
    should_buy, reason = trade_executor.should_auto_buy(
        _token(price_usd=0.0), elapsed_seconds=0, score_total=100, open_position_count=0
    )
    assert should_buy is False
    assert reason == "no_pair_data"


def test_should_auto_buy_false_when_score_below_minimum():
    should_buy, reason = trade_executor.should_auto_buy(_token(), elapsed_seconds=0, score_total=99, open_position_count=0)
    assert should_buy is False
    assert reason == "score_too_low"


def test_should_auto_buy_false_when_too_late():
    should_buy, reason = trade_executor.should_auto_buy(_token(), elapsed_seconds=61, score_total=100, open_position_count=0)
    assert should_buy is False
    assert reason == "too_late"


def test_should_auto_buy_true_at_exact_elapsed_boundary():
    should_buy, _ = trade_executor.should_auto_buy(_token(), elapsed_seconds=60, score_total=100, open_position_count=0)
    assert should_buy is True


def test_should_auto_buy_false_when_max_positions_open():
    should_buy, reason = trade_executor.should_auto_buy(_token(), elapsed_seconds=0, score_total=100, open_position_count=3)
    assert should_buy is False
    assert reason == "max_positions_open"


def test_is_ready_false_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "AUTO_TRADE_ENABLED", False)
    ready, status = trade_executor.is_ready()
    assert ready is False
    assert "AUTO_TRADE_ENABLED" in status


def test_is_ready_false_when_wallet_missing(monkeypatch):
    monkeypatch.setattr(config, "SOLANA_WALLET_PRIVATE_KEY", "")
    import wallet

    wallet._cached_keypair = None
    wallet._load_attempted = False
    ready, status = trade_executor.is_ready()
    assert ready is False
    assert "SOLANA_WALLET_PRIVATE_KEY" in status


def test_execute_buy_records_position_on_success(monkeypatch):
    keypair = MagicMock()
    keypair.pubkey.return_value = "WalletPubkey"
    monkeypatch.setattr("wallet.get_keypair", lambda: keypair)

    result = SwapResult(success=True, tx_signature="tx123", out_amount_raw=500000)
    with patch("jupiter_client.execute_swap", return_value=result) as mock_swap:
        positions = PositionTracker()
        position = trade_executor.execute_buy(_token(), positions, now=1000.0)

    mock_swap.assert_called_once()
    assert position is not None
    assert position.mint == "MINT1"
    assert position.open_tx_signature == "tx123"
    assert positions.has_open_position("MINT1") is True


def test_execute_buy_returns_none_on_swap_failure(monkeypatch):
    keypair = MagicMock()
    keypair.pubkey.return_value = "WalletPubkey"
    monkeypatch.setattr("wallet.get_keypair", lambda: keypair)

    result = SwapResult(success=False, error="見積もり取得に失敗しました")
    with patch("jupiter_client.execute_swap", return_value=result):
        positions = PositionTracker()
        position = trade_executor.execute_buy(_token(), positions, now=1000.0)

    assert position is None
    assert positions.open_count() == 0


def test_execute_buy_returns_none_when_wallet_unavailable(monkeypatch):
    monkeypatch.setattr("wallet.get_keypair", lambda: None)
    positions = PositionTracker()
    position = trade_executor.execute_buy(_token(), positions, now=1000.0)
    assert position is None


def test_execute_sell_closes_position_on_success(monkeypatch):
    keypair = MagicMock()
    keypair.pubkey.return_value = "WalletPubkey"
    monkeypatch.setattr("wallet.get_keypair", lambda: keypair)

    positions = PositionTracker()
    position = positions.open_position(
        mint="MINT1", name="Test", symbol="TEST", entry_price_usd=1.0, entry_amount_sol=0.02,
        token_amount_raw=1000, open_tx_signature="tx1", now=1000.0,
    )

    sell_result = SwapResult(success=True, tx_signature="tx2", out_amount_raw=20000000)
    with patch("jupiter_client.get_token_balance_raw", return_value=1000), \
         patch("jupiter_client.execute_swap", return_value=sell_result), \
         patch("trade_executor._current_price_usd", return_value=1.5):
        success = trade_executor.execute_sell(position, "take_profit", positions, now=1010.0)

    assert success is True
    assert position.closed is True
    assert position.pnl_pct == 50.0
    assert positions.has_open_position("MINT1") is False


def test_execute_sell_fails_when_balance_unavailable(monkeypatch):
    keypair = MagicMock()
    keypair.pubkey.return_value = "WalletPubkey"
    monkeypatch.setattr("wallet.get_keypair", lambda: keypair)

    positions = PositionTracker()
    position = positions.open_position(
        mint="MINT1", name="Test", symbol="TEST", entry_price_usd=1.0, entry_amount_sol=0.02,
        token_amount_raw=1000, open_tx_signature="tx1", now=1000.0,
    )

    with patch("jupiter_client.get_token_balance_raw", return_value=None):
        success = trade_executor.execute_sell(position, "stop_loss", positions, now=1010.0)

    assert success is False
    assert position.closed is False


def test_check_and_close_positions_sells_when_take_profit_hit(monkeypatch):
    positions = PositionTracker()
    position = positions.open_position(
        mint="MINT1", name="Test", symbol="TEST", entry_price_usd=1.0, entry_amount_sol=0.02,
        token_amount_raw=1000, open_tx_signature="tx1", now=1000.0,
    )
    monkeypatch.setattr(config, "AUTO_TRADE_TAKE_PROFIT_PCT", 50.0)
    monkeypatch.setattr(config, "AUTO_TRADE_STOP_LOSS_PCT", -30.0)
    monkeypatch.setattr(config, "AUTO_TRADE_MAX_HOLD_SECONDS", 3600)

    with patch("trade_executor._current_price_usd", return_value=1.6), patch("trade_executor.execute_sell") as mock_sell:
        trade_executor.check_and_close_positions(positions, now=1010.0)

    mock_sell.assert_called_once_with(position, "take_profit", positions, 1010.0)


def test_check_and_close_positions_does_nothing_when_price_unavailable(monkeypatch):
    positions = PositionTracker()
    positions.open_position(
        mint="MINT1", name="Test", symbol="TEST", entry_price_usd=1.0, entry_amount_sol=0.02,
        token_amount_raw=1000, open_tx_signature="tx1", now=1000.0,
    )

    with patch("trade_executor._current_price_usd", return_value=None), patch("trade_executor.execute_sell") as mock_sell:
        trade_executor.check_and_close_positions(positions, now=1010.0)

    mock_sell.assert_not_called()
