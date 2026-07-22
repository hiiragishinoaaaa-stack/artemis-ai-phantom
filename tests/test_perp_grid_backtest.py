"""perp_grid_backtest.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

from perp_grid_backtest import GridBacktestResult, GridTrade, _print_report, run_grid_backtest


def _candle(time_: float, o: float, h: float, low: float, c: float) -> tuple[float, float, float, float, float]:
    return (time_, o, h, low, c)


def test_run_grid_backtest_empty_when_no_candles():
    result = run_grid_backtest([], range_pct=10.0, grid_count=10, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0)
    assert result.trades == []


def test_run_grid_backtest_empty_when_grid_count_zero():
    candles = [_candle(0, 100, 100, 100, 100)]
    result = run_grid_backtest(candles, range_pct=10.0, grid_count=0, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0)
    assert result.trades == []


def test_run_grid_backtest_opens_position_at_center_and_stays_open_when_flat():
    candles = [_candle(i, 100, 100, 100, 100) for i in range(5)]
    result = run_grid_backtest(candles, range_pct=10.0, grid_count=10, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0)
    assert result.trades == []
    assert result.still_open_count == 1
    assert result.center_price == 100
    assert result.lower_bound == pytest.approx(90.0)
    assert result.upper_bound == pytest.approx(110.0)


def test_run_grid_backtest_take_profit_closes_position():
    candles = [
        _candle(0, 100, 100, 100, 100),
        _candle(1, 100, 102, 100, 101),
        _candle(2, 100, 100, 100, 100),
    ]
    result = run_grid_backtest(candles, range_pct=10.0, grid_count=10, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0)
    take_profits = [t for t in result.trades if t.reason == "take_profit"]
    assert len(take_profits) >= 1
    assert take_profits[0].entry_price == pytest.approx(100.0)
    assert take_profits[0].pnl_pct == pytest.approx(1.0 * 3.0)


def test_run_grid_backtest_stop_loss_closes_position():
    candles = [
        _candle(0, 100, 100, 100, 100),
        _candle(1, 100, 100, 99.0, 99.0),  # sl_price = 100 * (1 - 0.5/100) = 99.5、99.0はそれを下回る
    ]
    result = run_grid_backtest(candles, range_pct=10.0, grid_count=10, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0)
    stop_losses = [t for t in result.trades if t.reason == "stop_loss"]
    assert len(stop_losses) == 1
    assert stop_losses[0].pnl_pct == pytest.approx(-0.5 * 3.0)


def test_run_grid_backtest_take_profit_deducts_fees():
    candles = [
        _candle(0, 100, 100, 100, 100),
        _candle(1, 100, 102, 100, 101),
        _candle(2, 100, 100, 100, 100),
    ]
    result = run_grid_backtest(
        candles, range_pct=10.0, grid_count=10, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0,
        fee_pct_per_side=0.02,
    )
    take_profits = [t for t in result.trades if t.reason == "take_profit"]
    assert len(take_profits) >= 1
    # 利確1.0% * 3倍 - 往復手数料(0.02%*2*3倍) = 3.0 - 0.12 = 2.88
    assert take_profits[0].pnl_pct == pytest.approx(1.0 * 3.0 - 2 * 0.02 * 3.0)


def test_run_grid_backtest_zero_fee_matches_default_behavior():
    candles = [
        _candle(0, 100, 100, 100, 100),
        _candle(1, 100, 102, 100, 101),
    ]
    with_zero_fee = run_grid_backtest(
        candles, range_pct=10.0, grid_count=10, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0, fee_pct_per_side=0.0
    )
    without_fee_arg = run_grid_backtest(
        candles, range_pct=10.0, grid_count=10, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0
    )
    assert with_zero_fee.trades[0].pnl_pct == without_fee_arg.trades[0].pnl_pct


def test_run_grid_backtest_win_rate_and_total_pnl():
    result = GridBacktestResult(
        trades=[
            GridTrade(100.0, 101.0, 0.0, 1.0, "take_profit", 3.0),
            GridTrade(100.0, 99.5, 0.0, 1.0, "stop_loss", -1.5),
        ]
    )
    assert result.win_rate == 50.0
    assert result.total_pnl_pct == pytest.approx(1.5)


def test_run_grid_backtest_daily_loss_limit_reduces_trades():
    # 大きく下落し続けるシナリオで、同じ日のうちに何度も損切りが発生する状況を作る。
    candles = [_candle(0, 100, 100, 100, 100)]
    price = 100.0
    for i in range(1, 40):
        price *= 0.99  # 毎回1%下落し続ける(グリッドに何度も触れて損切りを繰り返す)
        candles.append(_candle(i, price, price * 1.001, price * 0.999, price))

    result_without_limit = run_grid_backtest(
        candles, range_pct=50.0, grid_count=20, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0
    )
    result_with_limit = run_grid_backtest(
        candles,
        range_pct=50.0,
        grid_count=20,
        take_profit_pct=1.0,
        stop_loss_pct=-0.5,
        leverage=3.0,
        daily_loss_limit_pct=-1.0,
    )
    assert len(result_with_limit.trades) <= len(result_without_limit.trades)


def test_run_grid_backtest_buy_and_hold_comparison():
    candles = [_candle(0, 100, 100, 100, 100), _candle(1, 100, 100, 100, 110)]
    result = run_grid_backtest(candles, range_pct=10.0, grid_count=10, take_profit_pct=1.0, stop_loss_pct=-0.5, leverage=3.0)
    assert result.buy_and_hold_pnl_pct == pytest.approx(10.0)
    assert result.buy_and_hold_leveraged_pnl_pct == pytest.approx(30.0)


def test_grid_backtest_result_max_drawdown():
    result = GridBacktestResult(
        trades=[
            GridTrade(100.0, 101.0, 0.0, 1.0, "take_profit", 3.0),
            GridTrade(100.0, 99.5, 0.0, 1.0, "stop_loss", -6.0),
        ]
    )
    # 累積: +3 -> -3、ピークは+3なので最大ドローダウンは-6
    assert result.max_drawdown_pct == pytest.approx(-6.0)


def test_print_report_handles_empty_trades(capsys):
    result = GridBacktestResult(center_price=100.0, lower_bound=90.0, upper_bound=110.0, grid_step_pct=2.0)
    _print_report(result, "BTCUSDT", leverage=3.0)
    captured = capsys.readouterr()
    assert "取引が1件も発生しませんでした" in captured.out


def test_print_report_shows_stats_and_buy_and_hold(capsys):
    result = GridBacktestResult(
        trades=[GridTrade(100.0, 101.0, 0.0, 1.0, "take_profit", 3.0)],
        center_price=100.0,
        lower_bound=90.0,
        upper_bound=110.0,
        grid_step_pct=2.0,
        buy_and_hold_pnl_pct=5.0,
        buy_and_hold_leveraged_pnl_pct=15.0,
    )
    _print_report(result, "BTCUSDT", leverage=3.0)
    captured = capsys.readouterr()
    assert "取引数: 1件" in captured.out
    assert "Buy & Hold" in captured.out


def test_print_report_notes_fee_assumption(capsys):
    result = GridBacktestResult(trades=[GridTrade(100.0, 101.0, 0.0, 1.0, "take_profit", 3.0)])
    _print_report(result, "BTCUSDT", leverage=3.0, fee_pct_per_side=0.02)
    captured = capsys.readouterr()
    assert "手数料0.020%考慮済み" in captured.out


def test_print_report_notes_no_fee_by_default(capsys):
    result = GridBacktestResult(trades=[GridTrade(100.0, 101.0, 0.0, 1.0, "take_profit", 3.0)])
    _print_report(result, "BTCUSDT", leverage=3.0)
    captured = capsys.readouterr()
    assert "手数料は未考慮" in captured.out
