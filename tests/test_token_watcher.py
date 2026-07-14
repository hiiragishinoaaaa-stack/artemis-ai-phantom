"""token_watcher.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
from token_watcher import TokenWatcher


@pytest.fixture(autouse=True)
def _patch_filter_config(monkeypatch):
    monkeypatch.setattr(config, "OBSERVATION_WINDOW_SECONDS", 45)
    monkeypatch.setattr(config, "MIN_BUY_COUNT", 5)
    monkeypatch.setattr(config, "MIN_UNIQUE_BUYERS", 3)
    monkeypatch.setattr(config, "MAX_SELL_TO_BUY_RATIO", 1.0)
    monkeypatch.setattr(config, "MIN_MARKET_CAP_SOL", 0.0)
    monkeypatch.setattr(config, "MAX_TRACKED_TOKENS", 500)


def _create(watcher: TokenWatcher, mint: str = "MINT1", now: float = 1000.0):
    return watcher.on_token_created(
        mint=mint, name="Test Coin", symbol="TEST", creator="creator1", market_cap_sol=10.0, now=now
    )


def test_on_token_created_starts_tracking():
    watcher = TokenWatcher()
    token = _create(watcher)
    assert watcher.get("MINT1") is token
    assert len(watcher) == 1


def test_on_token_created_is_idempotent_for_same_mint():
    watcher = TokenWatcher()
    first = _create(watcher)
    second = _create(watcher)
    assert first is second
    assert len(watcher) == 1


def test_on_trade_updates_buy_and_unique_buyers():
    watcher = TokenWatcher()
    _create(watcher)
    watcher.on_trade("MINT1", "buy", "buyerA", market_cap_sol=12.0)
    watcher.on_trade("MINT1", "buy", "buyerA", market_cap_sol=13.0)  # 同一アドレスの2回目
    watcher.on_trade("MINT1", "buy", "buyerB", market_cap_sol=14.0)

    token = watcher.get("MINT1")
    assert token.buy_count == 3
    assert len(token.unique_buyers) == 2  # buyerAは1人としてカウント
    assert token.last_market_cap_sol == 14.0


def test_on_trade_ignores_unknown_mint():
    watcher = TokenWatcher()
    watcher.on_trade("UNKNOWN", "buy", "buyerA", market_cap_sol=1.0)
    assert len(watcher) == 0


def test_due_for_evaluation_respects_observation_window():
    watcher = TokenWatcher()
    _create(watcher, now=1000.0)

    assert watcher.due_for_evaluation(now=1010.0) == []  # まだ10秒しか経ってない
    due = watcher.due_for_evaluation(now=1046.0)  # 46秒経過
    assert len(due) == 1
    assert due[0].mint == "MINT1"


def test_due_for_evaluation_excludes_already_evaluated():
    watcher = TokenWatcher()
    token = _create(watcher, now=1000.0)
    watcher.evaluate(token)

    assert watcher.due_for_evaluation(now=1046.0) == []


def test_evaluate_passes_when_all_conditions_met():
    watcher = TokenWatcher()
    _create(watcher)
    for i in range(5):
        watcher.on_trade("MINT1", "buy", f"buyer{i}", market_cap_sol=10.0 + i)

    token = watcher.get("MINT1")
    assert watcher.evaluate(token) is True
    assert token.evaluated is True


def test_evaluate_fails_when_buy_count_too_low():
    watcher = TokenWatcher()
    _create(watcher)
    for i in range(4):  # MIN_BUY_COUNT=5未満
        watcher.on_trade("MINT1", "buy", f"buyer{i}", market_cap_sol=10.0)

    token = watcher.get("MINT1")
    assert watcher.evaluate(token) is False


def test_evaluate_fails_when_unique_buyers_too_low():
    watcher = TokenWatcher()
    _create(watcher)
    for _ in range(5):  # 買い件数は5だが全部同じアドレス
        watcher.on_trade("MINT1", "buy", "sameBuyer", market_cap_sol=10.0)

    token = watcher.get("MINT1")
    assert watcher.evaluate(token) is False


def test_evaluate_fails_when_sell_pressure_too_high():
    watcher = TokenWatcher()
    _create(watcher)
    for i in range(5):
        watcher.on_trade("MINT1", "buy", f"buyer{i}", market_cap_sol=10.0)
    for _ in range(6):  # 売り件数(6)が買い件数(5)×1.0を超える
        watcher.on_trade("MINT1", "sell", "someone", market_cap_sol=8.0)

    token = watcher.get("MINT1")
    assert watcher.evaluate(token) is False


def test_evaluate_respects_min_market_cap(monkeypatch):
    monkeypatch.setattr(config, "MIN_MARKET_CAP_SOL", 50.0)
    watcher = TokenWatcher()
    _create(watcher)
    for i in range(5):
        watcher.on_trade("MINT1", "buy", f"buyer{i}", market_cap_sol=20.0)  # 50未満のまま

    token = watcher.get("MINT1")
    assert watcher.evaluate(token) is False


def test_forget_removes_token():
    watcher = TokenWatcher()
    _create(watcher)
    watcher.forget("MINT1")
    assert watcher.get("MINT1") is None
    assert len(watcher) == 0


def test_evicts_oldest_when_over_capacity(monkeypatch):
    monkeypatch.setattr(config, "MAX_TRACKED_TOKENS", 2)
    watcher = TokenWatcher()
    _create(watcher, mint="OLD", now=1000.0)
    _create(watcher, mint="MID", now=1001.0)
    assert len(watcher) == 2

    _create(watcher, mint="NEW", now=1002.0)  # 上限超過、最古(OLD)が間引かれる
    assert len(watcher) == 2
    assert watcher.get("OLD") is None
    assert watcher.get("MID") is not None
    assert watcher.get("NEW") is not None
