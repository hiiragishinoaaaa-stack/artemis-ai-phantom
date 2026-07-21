"""perp_signals.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
import perp_signals


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(config, "PERP_SIGNAL_THRESHOLD", 40)


def test_compute_ema_none_when_insufficient_data():
    assert perp_signals.compute_ema([1.0, 2.0], period=5) is None


def test_compute_ema_constant_series_equals_the_constant():
    values = [100.0] * 30
    ema = perp_signals.compute_ema(values, period=20)
    assert ema == pytest.approx(100.0)


def test_compute_ema_rising_series_is_between_first_and_last():
    values = [float(i) for i in range(1, 51)]
    ema = perp_signals.compute_ema(values, period=20)
    assert values[0] < ema < values[-1]


def test_compute_rsi_none_when_insufficient_data():
    assert perp_signals.compute_rsi([1.0, 2.0], period=14) is None


def test_compute_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 20)]  # 単調増加
    rsi = perp_signals.compute_rsi(closes, period=14)
    assert rsi == pytest.approx(100.0)


def test_compute_rsi_all_losses_is_0():
    closes = [float(i) for i in range(20, 1, -1)]  # 単調減少
    rsi = perp_signals.compute_rsi(closes, period=14)
    assert rsi == pytest.approx(0.0)


def test_compute_rsi_flat_series_is_neutral():
    closes = [100.0] * 20
    rsi = perp_signals.compute_rsi(closes, period=14)
    assert rsi == pytest.approx(50.0)


def test_compute_signal_none_when_no_closes():
    assert perp_signals.compute_signal("BTCUSDT", [], None) is None


def test_compute_signal_none_when_insufficient_for_ema():
    assert perp_signals.compute_signal("BTCUSDT", [1.0, 2.0, 3.0], None) is None


def test_compute_signal_long_for_strong_uptrend():
    # 明確な上昇トレンド + プラスモメンタム
    closes = [100.0 + i * 2 for i in range(60)]
    signal = perp_signals.compute_signal("BTCUSDT", closes, funding_rate=None)
    assert signal is not None
    assert signal.direction == "LONG"
    assert signal.score > 0


def test_compute_signal_short_for_strong_downtrend():
    closes = [100.0 + (60 - i) * 2 for i in range(60)]
    signal = perp_signals.compute_signal("BTCUSDT", closes, funding_rate=None)
    assert signal is not None
    assert signal.direction == "SHORT"
    assert signal.score < 0


def test_compute_signal_neutral_for_flat_market():
    closes = [100.0] * 60
    signal = perp_signals.compute_signal("BTCUSDT", closes, funding_rate=None)
    assert signal is not None
    assert signal.direction == "NEUTRAL"


def test_compute_signal_extreme_positive_funding_biases_score_down():
    closes = [100.0] * 60
    signal_no_funding = perp_signals.compute_signal("BTCUSDT", closes, funding_rate=None)
    signal_with_funding = perp_signals.compute_signal("BTCUSDT", closes, funding_rate=0.001)
    assert signal_with_funding.score < signal_no_funding.score


def test_compute_signal_extreme_negative_funding_biases_score_up():
    closes = [100.0] * 60
    signal_no_funding = perp_signals.compute_signal("BTCUSDT", closes, funding_rate=None)
    signal_with_funding = perp_signals.compute_signal("BTCUSDT", closes, funding_rate=-0.001)
    assert signal_with_funding.score > signal_no_funding.score


def test_compute_signal_score_is_clamped_to_100_range():
    closes = [100.0 + i * 5 for i in range(60)]
    signal = perp_signals.compute_signal("BTCUSDT", closes, funding_rate=-0.01)
    assert -100 <= signal.score <= 100
