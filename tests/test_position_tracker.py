"""position_tracker.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
from position_tracker import PositionTracker, decide_exit_reason


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "POSITIONS_FILE_PATH", tmp_path / "positions.json")


def test_decide_exit_reason_none_when_flat():
    reason = decide_exit_reason(
        entry_price_usd=1.0,
        current_price_usd=1.05,
        opened_at=1000.0,
        now=1010.0,
        take_profit_pct=50.0,
        stop_loss_pct=-30.0,
        max_hold_seconds=3600,
    )
    assert reason is None


def test_decide_exit_reason_take_profit():
    reason = decide_exit_reason(
        entry_price_usd=1.0,
        current_price_usd=1.51,
        opened_at=1000.0,
        now=1010.0,
        take_profit_pct=50.0,
        stop_loss_pct=-30.0,
        max_hold_seconds=3600,
    )
    assert reason == "take_profit"


def test_decide_exit_reason_stop_loss():
    reason = decide_exit_reason(
        entry_price_usd=1.0,
        current_price_usd=0.69,
        opened_at=1000.0,
        now=1010.0,
        take_profit_pct=50.0,
        stop_loss_pct=-30.0,
        max_hold_seconds=3600,
    )
    assert reason == "stop_loss"


def test_decide_exit_reason_max_hold():
    reason = decide_exit_reason(
        entry_price_usd=1.0,
        current_price_usd=1.05,
        opened_at=1000.0,
        now=1000.0 + 3600,
        take_profit_pct=50.0,
        stop_loss_pct=-30.0,
        max_hold_seconds=3600,
    )
    assert reason == "max_hold"


def test_decide_exit_reason_none_when_entry_price_zero():
    reason = decide_exit_reason(
        entry_price_usd=0.0,
        current_price_usd=1.0,
        opened_at=1000.0,
        now=1010.0,
        take_profit_pct=50.0,
        stop_loss_pct=-30.0,
        max_hold_seconds=3600,
    )
    assert reason is None


def test_decide_exit_reason_take_profit_takes_priority_over_max_hold():
    reason = decide_exit_reason(
        entry_price_usd=1.0,
        current_price_usd=2.0,
        opened_at=1000.0,
        now=1000.0 + 999999,
        take_profit_pct=50.0,
        stop_loss_pct=-30.0,
        max_hold_seconds=3600,
    )
    assert reason == "take_profit"


def test_open_position_and_has_open_position():
    tracker = PositionTracker()
    tracker.open_position(
        mint="MINT1",
        name="Test",
        symbol="TEST",
        entry_price_usd=1.0,
        entry_amount_sol=0.02,
        token_amount_raw=1000,
        open_tx_signature="tx1",
        now=1000.0,
    )
    assert tracker.has_open_position("MINT1") is True
    assert tracker.has_any_position("MINT1") is True
    assert tracker.open_count() == 1


def test_has_any_position_false_for_unknown_mint():
    tracker = PositionTracker()
    assert tracker.has_any_position("UNKNOWN") is False
    assert tracker.has_open_position("UNKNOWN") is False


def test_close_position_computes_pnl_pct():
    tracker = PositionTracker()
    position = tracker.open_position(
        mint="MINT1",
        name="Test",
        symbol="TEST",
        entry_price_usd=1.0,
        entry_amount_sol=0.02,
        token_amount_raw=1000,
        open_tx_signature="tx1",
        now=1000.0,
    )
    tracker.close_position(position, exit_price_usd=1.5, close_tx_signature="tx2", reason="take_profit", now=1010.0)

    assert position.closed is True
    assert position.pnl_pct == 50.0
    assert tracker.has_open_position("MINT1") is False
    assert tracker.has_any_position("MINT1") is True  # 決済済みでも履歴には残る
    assert tracker.open_count() == 0


def test_open_positions_excludes_closed():
    tracker = PositionTracker()
    position = tracker.open_position(
        mint="MINT1", name="A", symbol="A", entry_price_usd=1.0, entry_amount_sol=0.02,
        token_amount_raw=1000, open_tx_signature="tx1", now=1000.0,
    )
    tracker.open_position(
        mint="MINT2", name="B", symbol="B", entry_price_usd=1.0, entry_amount_sol=0.02,
        token_amount_raw=1000, open_tx_signature="tx2", now=1000.0,
    )
    tracker.close_position(position, exit_price_usd=1.0, close_tx_signature="tx3", reason="manual", now=1010.0)

    open_mints = [p.mint for p in tracker.open_positions()]
    assert open_mints == ["MINT2"]


def test_persists_to_disk_and_reloads():
    tracker = PositionTracker()
    tracker.open_position(
        mint="MINT1", name="Test", symbol="TEST", entry_price_usd=1.0, entry_amount_sol=0.02,
        token_amount_raw=1000, open_tx_signature="tx1", now=1000.0,
    )

    assert config.POSITIONS_FILE_PATH.exists()
    reloaded = PositionTracker()
    assert reloaded.has_open_position("MINT1") is True
    assert reloaded.open_count() == 1


def test_missing_file_starts_empty():
    tracker = PositionTracker()
    assert tracker.open_count() == 0


def test_corrupt_file_starts_empty():
    config.POSITIONS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.POSITIONS_FILE_PATH.write_text("not json", encoding="utf-8")

    tracker = PositionTracker()
    assert tracker.open_count() == 0
