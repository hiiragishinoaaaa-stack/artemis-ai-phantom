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
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1000.0)
    assert tracker.open_positions("TESTUSDT") == []
    assert tracker.last_price("TESTUSDT") == 100.0


def test_second_poll_opens_only_levels_price_actually_crossed():
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1000.0)

    # 100 -> 96: 中心(100)より下の水準のうち、96〜100の間にあるものだけ約定するはず。
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=96.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1010.0)

    opened_prices = sorted(p.entry_price for p in tracker.open_positions("TESTUSDT"))
    assert opened_prices
    assert all(96.0 <= price <= 100.0 for price in opened_prices)
    # 中心より上の水準(価格が一度も到達していない)は約定していないこと。
    assert all(price <= 100.0 for price in opened_prices)


def test_upward_movement_does_not_open_positions():
    """上昇中に水準をまたいでも買わないこと(「下がったら買い」が前提の
    グリッド戦略で、上昇中の通過も買ってしまうと、上昇トレンド中は
    見かけ上の勝率が実力以上に高くなり、反転時に逆回転して含み損が
    積み上がるという実害が過去に発生した回帰テスト)。
    """
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1000.0)

    # 100 -> 104: 中心より上の水準をまたぐが、上昇中なので何も買わないはず。
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=104.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1010.0)

    assert tracker.open_positions("TESTUSDT") == []


def test_no_movement_opens_nothing_on_second_poll():
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1000.0)
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1010.0)
    assert tracker.open_positions("TESTUSDT") == []


def test_short_disabled_by_default_does_not_open_on_rise():
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1000.0)
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=104.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1010.0)
    assert tracker.open_positions("TESTUSDT") == []


def test_short_enabled_opens_on_rise_and_not_on_dip(monkeypatch):
    monkeypatch.setattr(config, "PERP_GRID_SHORT_ENABLED", True)
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1000.0)

    # 100 -> 104: 上昇中なので、ショートは開くが、ロングは開かないはず。
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=104.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1010.0)

    opened = tracker.open_positions("TESTUSDT")
    assert opened
    assert all(p.side == "short" for p in opened)
    assert all(100.0 <= p.entry_price <= 104.0 for p in opened)


def test_short_enabled_long_and_short_can_open_independently(monkeypatch):
    monkeypatch.setattr(config, "PERP_GRID_SHORT_ENABLED", True)
    tracker = GridPaperTracker()
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1000.0)
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=96.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1010.0)
    with patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=104.0):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=1020.0)

    # 96で開いたロングは104への急騰で利確済みになっているはずなので、
    # open_positions()ではなくall_positions()(決済済み含む)で両サイドの
    # 建玉が実際に開かれたことを確認する。
    sides = {p.side for p in tracker.all_positions("TESTUSDT")}
    assert sides == {"long", "short"}


def test_process_grid_symbol_close_deducts_estimated_funding_cost():
    """決済時、その建玉の保有期間について実際のファンディングレート履歴を
    取得し、コストとしてpnl_pctから差し引くこと(perp_market_data.
    estimate_funding_cost_ptcが正しい引数で呼ばれ、その戻り値が反映される)。
    """
    tracker = GridPaperTracker()
    tracker.get_or_init_levels("TESTUSDT", 100.0, config.PERP_GRID_RANGE_PCT, config.PERP_GRID_COUNT)
    tracker.set_last_price("TESTUSDT", 100.0)
    tracker.open_position("TESTUSDT", level_index=0, entry_price=100.0, now=1000.0)

    with (
        patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=100.3),
        patch("perp_sniper.perp_market_data.estimate_funding_cost_pct", return_value=0.05) as mock_estimate,
    ):
        perp_sniper._process_grid_symbol("TESTUSDT", tracker, now=2000.0)

    mock_estimate.assert_called_once_with("TESTUSDT", 1000.0, 2000.0, config.PERP_GRID_LEVERAGE)
    closed = [p for p in tracker.all_positions("TESTUSDT") if p.closed]
    assert len(closed) == 1
    # 利確0.3% * レバレッジ3倍 - ファンディング0.05 = 0.9 - 0.05 = 0.85
    assert closed[0].pnl_pct == pytest.approx(0.85)


def test_process_grid_symbol_uses_backtest_informed_defaults_for_known_symbol(monkeypatch):
    """BTCUSDT/ETHUSDT/SOLUSDTは、このfixtureがmonkeypatchしたグローバル
    既定値(count=9等)ではなく、config.PERP_GRID_SYMBOL_DEFAULTSの
    銘柄別既定値(実測に基づく)がグリッド生成に使われること。
    """
    tracker = GridPaperTracker()
    with (
        patch.object(GridPaperTracker, "get_or_init_levels", wraps=tracker.get_or_init_levels) as spy_levels,
        patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=50000.0),
    ):
        perp_sniper._process_grid_symbol("BTCUSDT", tracker, now=1000.0)

    spy_levels.assert_called_once_with("BTCUSDT", 50000.0, 10.0, 100)


def test_process_grid_symbol_env_override_beats_backtest_informed_default(monkeypatch):
    monkeypatch.setenv("PERP_GRID_COUNT_BTCUSDT", "250")
    tracker = GridPaperTracker()
    with (
        patch.object(GridPaperTracker, "get_or_init_levels", wraps=tracker.get_or_init_levels) as spy_levels,
        patch("perp_sniper.perp_market_data.fetch_mark_price", return_value=50000.0),
    ):
        perp_sniper._process_grid_symbol("BTCUSDT", tracker, now=1000.0)

    spy_levels.assert_called_once_with("BTCUSDT", 50000.0, 10.0, 250)
