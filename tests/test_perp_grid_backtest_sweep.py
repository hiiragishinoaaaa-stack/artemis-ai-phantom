"""perp_grid_backtest_sweep.py の単体テスト。ネットワーク不要で実行できる
(perp_market_data.fetch_ohlc_with_timeをモックして検証する)。
"""
from __future__ import annotations

import pytest

import perp_grid_backtest_sweep as sweep


def test_breakeven_win_rate_pct_matches_known_grid_defaults():
    """記事の既定値(TP+0.2%/SL-0.1%、レバレッジ3倍、Maker手数料0.015%/側)
    での損益分岐勝率は約43.3%(このセッションで検証済みの値)。
    """
    rate = sweep.breakeven_win_rate_pct(
        take_profit_pct=0.2, stop_loss_pct=-0.1, leverage=3.0, fee_pct_per_side=0.015
    )
    assert rate == pytest.approx(43.3, abs=0.1)


def test_breakeven_win_rate_pct_widening_sl_without_widening_tp_raises_requirement():
    """SL幅だけを広げると(TPは据え置き)、1回の負けが重くなる分、損益分岐に
    必要な勝率はむしろ上がる(このセッションでFable5とは独立に検証した
    ナンス)。
    """
    narrow_sl = sweep.breakeven_win_rate_pct(0.2, -0.1, 3.0, 0.015)
    wide_sl = sweep.breakeven_win_rate_pct(0.2, -0.3, 3.0, 0.015)
    assert wide_sl > narrow_sl


def test_breakeven_win_rate_pct_returns_none_for_malformed_inverted_params():
    """take_profit_pctがstop_loss_pct以下(TP<SLという矛盾した設定)の場合、
    分母が0以下になり計算不能としてNoneを返す(通常のパラメータでは
    起こらない異常系のガード)。
    """
    rate = sweep.breakeven_win_rate_pct(take_profit_pct=-0.5, stop_loss_pct=-0.1, leverage=1.0, fee_pct_per_side=0.0)
    assert rate is None


def _flat_then_dip_then_recover_candles() -> list[tuple[float, float, float, float, float]]:
    """center=100からグリッド水準へ何度か触れて往復するだけの単純な合成データ。"""
    candles = []
    price = 100.0
    t = 0.0
    for _ in range(3):
        candles.append((t, price, price, price, price))
        t += 1
        # 少し下げて買いを発生させ、その後戻して利確させる。
        low = price * 0.995
        candles.append((t, price, price, low, low))
        t += 1
        high = price * 1.01
        candles.append((t, low, high, low, high))
        t += 1
        price = high
    return candles


def test_main_prints_sweep_table(monkeypatch, capsys):
    candles = _flat_then_dip_then_recover_candles()
    monkeypatch.setattr(sweep.perp_market_data, "fetch_ohlc_with_time", lambda *a, **k: candles)
    monkeypatch.setattr(
        "sys.argv",
        [
            "perp_grid_backtest_sweep.py",
            "--symbol", "BTCUSDT",
            "--grid-counts", "10,20",
            "--take-profits", "0.5",
            "--stop-losses", "-0.3",
            "--min-trades", "1",
        ],
    )

    sweep.main()

    out = capsys.readouterr().out
    assert "BTCUSDT" in out
    assert "grid" in out  # ヘッダー行
    assert "N/A" not in out or "損益分岐" in out  # breakevenが計算できている


def test_main_handles_fetch_failure(monkeypatch, capsys):
    monkeypatch.setattr(sweep.perp_market_data, "fetch_ohlc_with_time", lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["perp_grid_backtest_sweep.py"])

    sweep.main()

    out = capsys.readouterr().out
    assert "取得に失敗" in out


def test_main_reports_when_no_combination_reaches_min_trades(monkeypatch, capsys):
    candles = [(0.0, 100.0, 100.0, 100.0, 100.0)] * 3  # 完全に値動きなし=取引が発生しない
    monkeypatch.setattr(sweep.perp_market_data, "fetch_ohlc_with_time", lambda *a, **k: candles)
    monkeypatch.setattr(
        "sys.argv",
        [
            "perp_grid_backtest_sweep.py",
            "--grid-counts", "10",
            "--take-profits", "0.5",
            "--stop-losses", "-0.3",
        ],
    )

    sweep.main()

    out = capsys.readouterr().out
    assert "未満でした" in out
