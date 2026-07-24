"""config.py の銘柄ごとのグリッドTP/SL/分割数設定の単体テスト。

優先順位: 個別の環境変数上書き(PERP_GRID_COUNT_<SYMBOL>等) >
実測に基づく銘柄別既定値(PERP_GRID_SYMBOL_DEFAULTS) > グローバル既定値
(PERP_GRID_COUNT等)。
"""
from __future__ import annotations

import config


def test_known_symbol_uses_backtest_informed_default(monkeypatch):
    monkeypatch.setattr(config, "PERP_GRID_COUNT", 999)
    monkeypatch.setattr(config, "PERP_GRID_TAKE_PROFIT_PCT", 999.0)
    monkeypatch.setattr(config, "PERP_GRID_STOP_LOSS_PCT", -999.0)
    monkeypatch.delenv("PERP_GRID_COUNT_BTCUSDT", raising=False)
    monkeypatch.delenv("PERP_GRID_TAKE_PROFIT_PCT_BTCUSDT", raising=False)
    monkeypatch.delenv("PERP_GRID_STOP_LOSS_PCT_BTCUSDT", raising=False)

    assert config.grid_count_for_symbol("BTCUSDT") == 100
    assert config.grid_take_profit_pct_for_symbol("BTCUSDT") == 0.40
    assert config.grid_stop_loss_pct_for_symbol("BTCUSDT") == -0.50


def test_unknown_symbol_falls_back_to_global_default(monkeypatch):
    monkeypatch.setattr(config, "PERP_GRID_COUNT", 42)
    monkeypatch.setattr(config, "PERP_GRID_RANGE_PCT", 12.5)
    monkeypatch.setattr(config, "PERP_GRID_TAKE_PROFIT_PCT", 0.33)
    monkeypatch.setattr(config, "PERP_GRID_STOP_LOSS_PCT", -0.22)
    monkeypatch.delenv("PERP_GRID_COUNT_DOGEUSDT", raising=False)

    assert config.grid_count_for_symbol("DOGEUSDT") == 42
    assert config.grid_range_pct_for_symbol("DOGEUSDT") == 12.5
    assert config.grid_take_profit_pct_for_symbol("DOGEUSDT") == 0.33
    assert config.grid_stop_loss_pct_for_symbol("DOGEUSDT") == -0.22


def test_env_var_override_wins_over_backtest_informed_default(monkeypatch):
    monkeypatch.setenv("PERP_GRID_COUNT_BTCUSDT", "250")
    monkeypatch.setenv("PERP_GRID_TAKE_PROFIT_PCT_BTCUSDT", "0.6")
    monkeypatch.setenv("PERP_GRID_STOP_LOSS_PCT_BTCUSDT", "-0.7")

    assert config.grid_count_for_symbol("BTCUSDT") == 250
    assert config.grid_take_profit_pct_for_symbol("BTCUSDT") == 0.6
    assert config.grid_stop_loss_pct_for_symbol("BTCUSDT") == -0.7


def test_malformed_env_var_override_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PERP_GRID_COUNT_BTCUSDT", "not-a-number")

    assert config.grid_count_for_symbol("BTCUSDT") == 100


def test_eth_and_sol_use_smaller_grid_and_unchanged_tight_sl(monkeypatch):
    for suffix in ("COUNT", "TAKE_PROFIT_PCT", "STOP_LOSS_PCT", "RANGE_PCT"):
        monkeypatch.delenv(f"PERP_GRID_{suffix}_ETHUSDT", raising=False)
        monkeypatch.delenv(f"PERP_GRID_{suffix}_SOLUSDT", raising=False)

    for symbol in ("ETHUSDT", "SOLUSDT"):
        assert config.grid_count_for_symbol(symbol) == 50
        assert config.grid_take_profit_pct_for_symbol(symbol) == 0.20
        assert config.grid_stop_loss_pct_for_symbol(symbol) == -0.10
