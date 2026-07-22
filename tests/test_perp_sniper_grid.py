"""perp_sniper.py のグリッド処理(_process_grid_symbol)の単体テスト。

過去に「起動直後のポーリングで、価格が一度も動いていないのに中心価格より
上の水準が半分近く一斉に約定してしまう」というバグがあった(current_price
<= level_priceという単純な比較が、中心より上の水準では常にtrueになって
しまうため)。この回帰を防ぐためのテスト。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import config
import perp_sniper
from grid_paper_trader import GridPaperTracker


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERP_GRID_POSITIONS_FILE_PATH", tmp_path / "grid_positions.json")
    monkeypatch.setattr(config, "PERP_GRID_RANGE_PCT", 10.0)
    # 9にしているのは、中心価格(100)がちょうどグリッド水準の1つと一致
    # しないようにするため(10だと100自体が水準になり、「値動き無し」の
    # テストが意図せず「価格が水準ちょうどにある」ケースになってしまう)。
    monkeypatch.setattr(config, "PERP_GRID_COUNT", 9)
    monkeypatch.setattr(config, "PERP_GRID_TAKE_PROFIT_PCT", 0.2)
    monkeypatch.setattr(config, "PERP_GRID_STOP_LOSS_PCT", -0.1)
    monkeypatch.setattr(config, "PERP_GRID_LEVERAGE", 3.0)
    monkeypatch.setattr(config, "PERP_GRID_FEE_PCT_PER_SIDE", 0.0)
    monkeypatch.setattr(config, "PERP_GRID_SUMMARY_INTERVAL_SECONDS", 86400)
    monkeypatch.setattr(perp_sniper, "_last_grid_summary_at", {})


def test_first_poll_opens_no_positions_even_above_center():
    """起動直後(前回価格が無い)は、価格が動いていないので何も約定しない。"""
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("BTCUSDT", tracker, now=1000.0)
    assert tracker.open_positions("BTCUSDT") == []
    assert tracker.last_price("BTCUSDT") == 100.0


def test_second_poll_opens_only_levels_price_actually_crossed():
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("BTCUSDT", tracker, now=1000.0)

    # 100 -> 96: 中心(100)より下の水準のうち、96〜100の間にあるものだけ約定するはず。
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=96.0):
        perp_sniper._process_grid_symbol("BTCUSDT", tracker, now=1010.0)

    opened_prices = sorted(p.entry_price for p in tracker.open_positions("BTCUSDT"))
    assert opened_prices
    assert all(96.0 <= price <= 100.0 for price in opened_prices)
    # 中心より上の水準(価格が一度も到達していない)は約定していないこと。
    assert all(price <= 100.0 for price in opened_prices)


def test_no_movement_opens_nothing_on_second_poll():
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("BTCUSDT", tracker, now=1000.0)
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("BTCUSDT", tracker, now=1010.0)
    assert tracker.open_positions("BTCUSDT") == []


def test_process_grid_symbol_close_deducts_estimated_funding_cost():
    """決済時、その建玉の保有期間について実際のファンディングレート履歴を
    取得し、コストとしてpnl_pctから差し引くこと(perp_market_data.
    estimate_funding_cost_ptcが正しい引数で呼ばれ、その戻り値が反映される)。
    """
    tracker = GridPaperTracker()
    tracker.get_or_init_levels("BTCUSDT", 100.0, config.PERP_GRID_RANGE_PCT, config.PERP_GRID_COUNT)
    tracker.set_last_price("BTCUSDT", 100.0)
    tracker.open_position("BTCUSDT", level_index=0, entry_price=100.0, now=1000.0)

    with (
        patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.3),
        patch("perp_sniper.perp_market_data.estimate_funding_cost_pct", return_value=0.05) as mock_estimate,
    ):
        perp_sniper._process_grid_symbol("BTCUSDT", tracker, now=2000.0)

    mock_estimate.assert_called_once_with("BTCUSDT", 1000.0, 2000.0, config.PERP_GRID_LEVERAGE)
    closed = [p for p in tracker.all_positions("BTCUSDT") if p.closed]
    assert len(closed) == 1
    # 利確0.3% * レバレッジ3倍 - ファンディング0.05 = 0.9 - 0.05 = 0.85
    assert closed[0].pnl_pct == pytest.approx(0.85)
