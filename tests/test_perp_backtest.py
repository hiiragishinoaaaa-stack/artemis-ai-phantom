"""perp_backtest.py の単体テスト。ネットワーク不要で実行できる(candlesは
呼び出し側が組み立てて渡す設計のため)。
"""
from __future__ import annotations

import pytest

from perp_backtest import BacktestResult, BacktestTrade, run_backtest


def _candles(prices: list[float], start_time: float = 0.0, step_seconds: float = 3600.0) -> list[tuple[float, float]]:
    return [(start_time + i * step_seconds, price) for i, price in enumerate(prices)]


def test_run_backtest_empty_when_insufficient_candles():
    candles = _candles([100.0] * 10)
    result = run_backtest("BTCUSDT", candles, leverage=3.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400)
    assert result.trades == []


def test_run_backtest_opens_and_closes_long_on_uptrend_take_profit():
    # 50本のウォームアップ(緩やかな上昇)+その後さらに上昇を続けて利確させる
    prices = [100.0 + i for i in range(120)]
    candles = _candles(prices)
    result = run_backtest(
        "BTCUSDT", candles, leverage=3.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400 * 10
    )
    assert len(result.trades) >= 1
    first_trade = result.trades[0]
    assert first_trade.direction == "LONG"
    assert first_trade.reason == "take_profit"
    assert first_trade.pnl_pct >= 10.0


def test_run_backtest_opens_and_closes_short_on_downtrend_take_profit():
    prices = [300.0 - i for i in range(120)]
    candles = _candles(prices)
    result = run_backtest(
        "BTCUSDT", candles, leverage=3.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400 * 10
    )
    assert len(result.trades) >= 1
    first_trade = result.trades[0]
    assert first_trade.direction == "SHORT"
    assert first_trade.reason == "take_profit"
    assert first_trade.pnl_pct >= 10.0


def test_run_backtest_flat_market_produces_no_trades():
    candles = _candles([100.0] * 120)
    result = run_backtest(
        "BTCUSDT", candles, leverage=3.0, take_profit_pct=10.0, stop_loss_pct=-10.0, max_hold_seconds=86400
    )
    assert result.trades == []


def test_run_backtest_max_hold_closes_position_when_flat_after_entry():
    # 明確な上昇トレンドでLONGを建てた直後、横ばいになって利確にも損切りにも
    # 届かないまま最大保有時間に達するケース。
    prices = [100.0 + i for i in range(60)] + [159.0] * 200
    candles = _candles(prices)
    result = run_backtest(
        "BTCUSDT", candles, leverage=3.0, take_profit_pct=50.0, stop_loss_pct=-50.0, max_hold_seconds=3600 * 10
    )
    assert len(result.trades) >= 1
    assert result.trades[0].reason == "max_hold"


def test_run_backtest_does_not_open_second_position_while_one_is_open():
    prices = [100.0 + i for i in range(120)]
    candles = _candles(prices)
    result = run_backtest(
        "BTCUSDT", candles, leverage=3.0, take_profit_pct=1000.0, stop_loss_pct=-1000.0, max_hold_seconds=86400 * 1000
    )
    # 利確/損切りに届かないよう極端な閾値にしているため、ポジションは1つも
    # 決済されない(=取引記録は0件のまま、2つ目を建てようともしない)。
    assert result.trades == []


def test_run_backtest_daily_loss_limit_blocks_new_entries_same_day():
    # 1本目のトレンドで即座に損切りさせ、同じ日のうちに反対方向の
    # シグナルが出ても、日次ドローダウン制限に引っかかって新規エントリー
    # しないことを確認する。
    up_then_down = [100.0 + i for i in range(60)] + [100.0 - i for i in range(60)]
    candles = _candles(up_then_down, step_seconds=1.0)  # 秒刻みにして全部同じ日にする
    result_without_limit = run_backtest(
        "BTCUSDT", candles, leverage=3.0, take_profit_pct=1000.0, stop_loss_pct=-1.0, max_hold_seconds=86400
    )
    result_with_limit = run_backtest(
        "BTCUSDT",
        candles,
        leverage=3.0,
        take_profit_pct=1000.0,
        stop_loss_pct=-1.0,
        max_hold_seconds=86400,
        daily_loss_limit_pct=-0.5,
    )
    assert len(result_with_limit.trades) <= len(result_without_limit.trades)


def test_backtest_result_win_rate():
    result = BacktestResult(
        trades=[
            BacktestTrade("LONG", 100.0, 110.0, 0.0, 1.0, "take_profit", 10.0),
            BacktestTrade("LONG", 100.0, 90.0, 0.0, 1.0, "stop_loss", -10.0),
        ]
    )
    assert result.win_rate == 50.0
    assert result.total_pnl_pct == 0.0


def test_backtest_result_win_rate_zero_when_no_trades():
    result = BacktestResult(trades=[])
    assert result.win_rate == 0.0
    assert result.total_pnl_pct == 0.0


def test_backtest_result_max_drawdown():
    result = BacktestResult(
        trades=[
            BacktestTrade("LONG", 100.0, 110.0, 0.0, 1.0, "take_profit", 10.0),
            BacktestTrade("LONG", 100.0, 80.0, 0.0, 1.0, "stop_loss", -20.0),
            BacktestTrade("LONG", 100.0, 105.0, 0.0, 1.0, "take_profit", 5.0),
        ]
    )
    # 累積: +10 -> -10 -> -5、ピークは+10なので最大ドローダウンは-20
    assert result.max_drawdown_pct == pytest.approx(-20.0)
