"""grid_trading.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

from grid_trading import compute_grid_levels, compute_grid_pnl_pct, decide_grid_exit_reason


def test_compute_grid_levels_basic():
    levels = compute_grid_levels(center_price=100.0, range_pct=10.0, grid_count=10)
    assert levels[0] == pytest.approx(90.0)
    assert levels[-1] == pytest.approx(110.0)
    assert len(levels) == 11


def test_compute_grid_levels_empty_when_center_zero():
    assert compute_grid_levels(0.0, 10.0, 10) == []


def test_compute_grid_levels_empty_when_grid_count_zero():
    assert compute_grid_levels(100.0, 10.0, 0) == []


def test_decide_grid_exit_reason_take_profit():
    assert decide_grid_exit_reason(100.0, 101.0, take_profit_pct=1.0, stop_loss_pct=-0.5) == "take_profit"


def test_decide_grid_exit_reason_stop_loss():
    assert decide_grid_exit_reason(100.0, 99.4, take_profit_pct=1.0, stop_loss_pct=-0.5) == "stop_loss"


def test_decide_grid_exit_reason_none_when_within_band():
    assert decide_grid_exit_reason(100.0, 100.2, take_profit_pct=1.0, stop_loss_pct=-0.5) is None


def test_decide_grid_exit_reason_none_when_entry_price_zero():
    assert decide_grid_exit_reason(0.0, 100.0, take_profit_pct=1.0, stop_loss_pct=-0.5) is None


def test_compute_grid_pnl_pct_no_fee():
    assert compute_grid_pnl_pct(100.0, 101.0, leverage=3.0) == pytest.approx(3.0)


def test_compute_grid_pnl_pct_with_fee():
    # 1%値上がり * 3倍 - 往復手数料(0.02%*2*3倍) = 3.0 - 0.12 = 2.88
    assert compute_grid_pnl_pct(100.0, 101.0, leverage=3.0, fee_pct_per_side=0.02) == pytest.approx(2.88)


def test_compute_grid_pnl_pct_zero_when_entry_price_zero():
    assert compute_grid_pnl_pct(0.0, 101.0, leverage=3.0) == 0.0
