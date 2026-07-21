"""perp_paper_trader.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
from perp_paper_trader import PaperPerpTracker, decide_exit_reason


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERP_POSITIONS_FILE_PATH", tmp_path / "perp_positions.json")


def test_decide_exit_reason_long_take_profit_with_leverage():
    # LONG, 値上がり5% x レバレッジ3倍 = 15%の含み益 -> 利確ライン10%を超える
    reason = decide_exit_reason(
        direction="LONG", entry_price=100.0, current_price=105.0, leverage=3.0,
        opened_at=1000.0, now=1010.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400,
    )
    assert reason == "take_profit"


def test_decide_exit_reason_short_profits_from_price_drop():
    # SHORT, 値下がり5% x レバレッジ3倍 = 15%の含み益(SHORTは下落が利益)
    reason = decide_exit_reason(
        direction="SHORT", entry_price=100.0, current_price=95.0, leverage=3.0,
        opened_at=1000.0, now=1010.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400,
    )
    assert reason == "take_profit"


def test_decide_exit_reason_short_loses_from_price_rise():
    # SHORT, 値上がり5% x レバレッジ3倍 = -15%の含み損 -> 損切りライン-10%を下回る
    reason = decide_exit_reason(
        direction="SHORT", entry_price=100.0, current_price=105.0, leverage=3.0,
        opened_at=1000.0, now=1010.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400,
    )
    assert reason == "stop_loss"


def test_decide_exit_reason_none_when_flat():
    reason = decide_exit_reason(
        direction="LONG", entry_price=100.0, current_price=101.0, leverage=3.0,
        opened_at=1000.0, now=1010.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400,
    )
    assert reason is None


def test_decide_exit_reason_max_hold():
    reason = decide_exit_reason(
        direction="LONG", entry_price=100.0, current_price=101.0, leverage=3.0,
        opened_at=1000.0, now=1000.0 + 86400, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400,
    )
    assert reason == "max_hold"


def test_decide_exit_reason_none_when_entry_price_zero():
    reason = decide_exit_reason(
        direction="LONG", entry_price=0.0, current_price=101.0, leverage=3.0,
        opened_at=1000.0, now=1010.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400,
    )
    assert reason is None


def test_open_and_close_position_computes_leveraged_pnl():
    tracker = PaperPerpTracker()
    position = tracker.open_position("BTCUSDT", "LONG", entry_price=100.0, leverage=3.0, now=1000.0)
    assert tracker.has_open_position("BTCUSDT") is True

    tracker.close_position(position, exit_price=110.0, reason="take_profit", now=1010.0)
    assert position.closed is True
    assert position.pnl_pct == 30.0  # 10%値上がり x 3倍レバレッジ
    assert tracker.has_open_position("BTCUSDT") is False


def test_short_position_pnl_sign():
    tracker = PaperPerpTracker()
    position = tracker.open_position("BTCUSDT", "SHORT", entry_price=100.0, leverage=2.0, now=1000.0)
    tracker.close_position(position, exit_price=90.0, reason="take_profit", now=1010.0)
    assert position.pnl_pct == 20.0  # 10%値下がり x 2倍レバレッジ(SHORTなので利益)


def test_open_positions_excludes_closed():
    tracker = PaperPerpTracker()
    position = tracker.open_position("BTCUSDT", "LONG", entry_price=100.0, leverage=3.0, now=1000.0)
    tracker.open_position("ETHUSDT", "SHORT", entry_price=50.0, leverage=2.0, now=1000.0)
    tracker.close_position(position, exit_price=100.0, reason="manual", now=1010.0)

    open_symbols = [p.symbol for p in tracker.open_positions()]
    assert open_symbols == ["ETHUSDT"]


def test_persists_to_disk_and_reloads():
    tracker = PaperPerpTracker()
    tracker.open_position("BTCUSDT", "LONG", entry_price=100.0, leverage=3.0, now=1000.0)

    assert config.PERP_POSITIONS_FILE_PATH.exists()
    reloaded = PaperPerpTracker()
    assert reloaded.has_open_position("BTCUSDT") is True


def test_missing_file_starts_empty():
    tracker = PaperPerpTracker()
    assert tracker.open_positions() == []


def test_corrupt_file_starts_empty():
    config.PERP_POSITIONS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.PERP_POSITIONS_FILE_PATH.write_text("not json", encoding="utf-8")
    tracker = PaperPerpTracker()
    assert tracker.open_positions() == []
