"""grid_paper_trader.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
from grid_paper_trader import GridPaperTracker


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERP_GRID_POSITIONS_FILE_PATH", tmp_path / "grid_positions.json")


def test_get_or_init_levels_fixes_center_on_first_call():
    tracker = GridPaperTracker()
    levels = tracker.get_or_init_levels("BTCUSDT", 100.0, range_pct=10.0, grid_count=10)
    assert levels[0] == pytest.approx(90.0)
    assert tracker.center_price("BTCUSDT") == 100.0


def test_get_or_init_levels_keeps_center_fixed_on_later_calls():
    tracker = GridPaperTracker()
    tracker.get_or_init_levels("BTCUSDT", 100.0, range_pct=10.0, grid_count=10)
    levels_again = tracker.get_or_init_levels("BTCUSDT", 150.0, range_pct=10.0, grid_count=10)
    assert tracker.center_price("BTCUSDT") == 100.0
    assert levels_again[0] == pytest.approx(90.0)


def test_open_position_and_has_open_position():
    tracker = GridPaperTracker()
    tracker.open_position("BTCUSDT", level_index=3, entry_price=95.0, now=1000.0)
    assert tracker.has_open_position("BTCUSDT", 3) is True
    assert tracker.has_open_position("BTCUSDT", 4) is False
    assert tracker.has_open_position("ETHUSDT", 3) is False


def test_open_positions_filters_by_symbol():
    tracker = GridPaperTracker()
    tracker.open_position("BTCUSDT", level_index=1, entry_price=90.0, now=1000.0)
    tracker.open_position("ETHUSDT", level_index=1, entry_price=2000.0, now=1000.0)
    assert [p.symbol for p in tracker.open_positions("BTCUSDT")] == ["BTCUSDT"]
    assert len(tracker.open_positions()) == 2


def test_close_position_computes_pnl_with_fee():
    tracker = GridPaperTracker()
    position = tracker.open_position("BTCUSDT", level_index=3, entry_price=100.0, now=1000.0)
    tracker.close_position(position, exit_price=101.0, reason="take_profit", now=1010.0, leverage=3.0, fee_pct_per_side=0.02)
    assert position.closed is True
    assert position.pnl_pct == pytest.approx(2.88)
    assert tracker.has_open_position("BTCUSDT", 3) is False


def test_all_positions_includes_closed():
    tracker = GridPaperTracker()
    position = tracker.open_position("BTCUSDT", level_index=3, entry_price=100.0, now=1000.0)
    tracker.close_position(position, exit_price=101.0, reason="take_profit", now=1010.0, leverage=3.0, fee_pct_per_side=0.0)
    tracker.open_position("BTCUSDT", level_index=4, entry_price=95.0, now=1020.0)

    all_positions = tracker.all_positions("BTCUSDT")
    assert len(all_positions) == 2
    assert sum(1 for p in all_positions if p.closed) == 1


def test_persists_to_disk_and_reloads():
    tracker = GridPaperTracker()
    tracker.get_or_init_levels("BTCUSDT", 100.0, range_pct=10.0, grid_count=10)
    tracker.open_position("BTCUSDT", level_index=3, entry_price=95.0, now=1000.0)

    assert config.PERP_GRID_POSITIONS_FILE_PATH.exists()
    reloaded = GridPaperTracker()
    assert reloaded.center_price("BTCUSDT") == 100.0
    assert reloaded.has_open_position("BTCUSDT", 3) is True


def test_missing_file_starts_empty():
    tracker = GridPaperTracker()
    assert tracker.open_positions() == []
    assert tracker.center_price("BTCUSDT") is None


def test_corrupt_file_starts_empty():
    config.PERP_GRID_POSITIONS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.PERP_GRID_POSITIONS_FILE_PATH.write_text("not json", encoding="utf-8")
    tracker = GridPaperTracker()
    assert tracker.open_positions() == []
