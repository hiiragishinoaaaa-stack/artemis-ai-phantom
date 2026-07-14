"""token_watcher.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
from token_watcher import TokenWatcher


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(config, "EVALUATION_CHECKPOINTS_SECONDS", (20, 40, 60, 90, 120))
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
    assert token.checkpoint_index == 0
    assert token.finished is False
    assert token.notified_tier is None


def test_on_token_created_is_idempotent_for_same_mint():
    watcher = TokenWatcher()
    first = _create(watcher)
    second = _create(watcher)
    assert first is second
    assert len(watcher) == 1


def test_on_trade_updates_buy_unique_buyers_and_volume():
    watcher = TokenWatcher()
    _create(watcher)
    watcher.on_trade("MINT1", "buy", "buyerA", market_cap_sol=12.0, sol_amount=1.0)
    watcher.on_trade("MINT1", "buy", "buyerA", market_cap_sol=13.0, sol_amount=0.5)  # 同一アドレスの2回目
    watcher.on_trade("MINT1", "buy", "buyerB", market_cap_sol=14.0, sol_amount=2.0)

    token = watcher.get("MINT1")
    assert token.buy_count == 3
    assert len(token.unique_buyers) == 2  # buyerAは1人としてカウント
    assert token.last_market_cap_sol == 14.0
    assert token.total_volume_sol == pytest.approx(3.5)


def test_on_trade_ignores_unknown_mint():
    watcher = TokenWatcher()
    watcher.on_trade("UNKNOWN", "buy", "buyerA", market_cap_sol=1.0)
    assert len(watcher) == 0


def test_due_for_checkpoint_respects_first_checkpoint():
    watcher = TokenWatcher()
    _create(watcher, now=1000.0)

    assert watcher.due_for_checkpoint(now=1010.0) == []  # まだ10秒しか経ってない
    due = watcher.due_for_checkpoint(now=1021.0)  # 21秒経過(20秒チェックポイント到達)
    assert len(due) == 1
    assert due[0].mint == "MINT1"


def test_due_for_checkpoint_excludes_finished_tokens():
    watcher = TokenWatcher()
    token = _create(watcher, now=1000.0)
    for _ in range(len(config.EVALUATION_CHECKPOINTS_SECONDS)):
        watcher.mark_checkpoint_done(token)

    assert token.finished is True
    assert watcher.due_for_checkpoint(now=2000.0) == []


def test_current_checkpoint_seconds_advances():
    watcher = TokenWatcher()
    token = _create(watcher, now=1000.0)
    assert watcher.current_checkpoint_seconds(token) == 20

    watcher.mark_checkpoint_done(token)
    assert token.checkpoint_index == 1
    assert watcher.current_checkpoint_seconds(token) == 40


def test_mark_checkpoint_done_marks_finished_after_last_checkpoint():
    watcher = TokenWatcher()
    token = _create(watcher, now=1000.0)
    for expected_seconds in config.EVALUATION_CHECKPOINTS_SECONDS:
        assert watcher.current_checkpoint_seconds(token) == expected_seconds
        assert token.finished is False
        watcher.mark_checkpoint_done(token)

    assert token.finished is True


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
