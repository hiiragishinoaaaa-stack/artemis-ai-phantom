"""token_watcher.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import pytest

import config
from token_watcher import TokenWatcher


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(config, "MIGRATION_CHECKPOINTS_SECONDS", (0, 60, 300, 900))
    monkeypatch.setattr(config, "MAX_TRACKED_TOKENS", 500)


def _start(watcher: TokenWatcher, mint: str = "MINT1", now: float = 1000.0):
    return watcher.start_tracking(mint=mint, name="Test Coin", symbol="TEST", now=now)


def _pair(**overrides) -> dict:
    base = {
        "url": "https://dexscreener.com/solana/MINT1",
        "txns": {"m5": {"buys": overrides.get("buys", 3), "sells": overrides.get("sells", 1)}},
        "buyers": {"m5": overrides.get("unique_buyers", 2)},
        "volume": {"m5": overrides.get("volume", 123.0)},
        "priceChange": {"m5": overrides.get("price_change", 15.0)},
        "liquidity": {"usd": overrides.get("liquidity", 4000.0)},
        "marketCap": overrides.get("market_cap", 50000.0),
    }
    return base


def test_start_tracking_begins_tracking():
    watcher = TokenWatcher()
    token = _start(watcher)
    assert watcher.get("MINT1") is token
    assert len(watcher) == 1
    assert token.checkpoint_index == 0
    assert token.finished is False
    assert token.notified_tier is None
    assert token.has_pair_data is False
    assert token.rugcheck_checked is False
    assert token.rugcheck_danger is False


def test_start_tracking_is_idempotent_for_same_mint():
    watcher = TokenWatcher()
    first = _start(watcher)
    second = _start(watcher)
    assert first is second
    assert len(watcher) == 1


def test_apply_snapshot_populates_fields_from_pair():
    watcher = TokenWatcher()
    token = _start(watcher)
    watcher.apply_snapshot(
        token,
        _pair(buys=7, sells=2, unique_buyers=6, volume=555.0, price_change=25.0, liquidity=9000.0, market_cap=80000.0),
    )

    assert token.has_pair_data is True
    assert token.buys_m5 == 7
    assert token.sells_m5 == 2
    assert token.unique_buyers_m5 == 6
    assert token.volume_m5_usd == 555.0
    assert token.price_change_m5_pct == 25.0
    assert token.liquidity_usd == 9000.0
    assert token.market_cap_usd == 80000.0
    assert token.dexscreener_url == "https://dexscreener.com/solana/MINT1"


def test_apply_snapshot_handles_missing_nested_fields():
    watcher = TokenWatcher()
    token = _start(watcher)
    watcher.apply_snapshot(token, {})

    assert token.has_pair_data is True
    assert token.buys_m5 == 0
    assert token.sells_m5 == 0
    assert token.unique_buyers_m5 == 0
    assert token.volume_m5_usd == 0.0
    assert token.liquidity_usd == 0.0


def test_apply_rugcheck_report_marks_safe_when_no_danger_reason():
    watcher = TokenWatcher()
    token = _start(watcher)
    watcher.apply_rugcheck_report(token, None, "CreatorAddr1")

    assert token.rugcheck_checked is True
    assert token.rugcheck_danger is False
    assert token.rugcheck_danger_reason == ""
    assert token.creator == "CreatorAddr1"
    assert token.rugcheck_warn_count == 0


def test_apply_rugcheck_report_records_warn_count():
    watcher = TokenWatcher()
    token = _start(watcher)
    watcher.apply_rugcheck_report(token, None, "CreatorAddr1", warn_count=2)

    assert token.rugcheck_warn_count == 2


def test_apply_rugcheck_report_marks_danger_when_reason_given():
    watcher = TokenWatcher()
    token = _start(watcher)
    watcher.apply_rugcheck_report(token, "Single holder ownership", "CreatorAddr1")

    assert token.rugcheck_checked is True
    assert token.rugcheck_danger is True
    assert token.rugcheck_danger_reason == "Single holder ownership"


def test_apply_rugcheck_report_handles_missing_creator():
    watcher = TokenWatcher()
    token = _start(watcher)
    watcher.apply_rugcheck_report(token, None, None)

    assert token.creator == ""


def test_apply_creator_block_sets_reason():
    watcher = TokenWatcher()
    token = _start(watcher)
    watcher.apply_creator_block(token, "通知後に-95%下落")

    assert token.blocked_creator_reason == "通知後に-95%下落"


def test_apply_creator_block_clears_reason_when_none():
    watcher = TokenWatcher()
    token = _start(watcher)
    watcher.apply_creator_block(token, "some reason")
    watcher.apply_creator_block(token, None)

    assert token.blocked_creator_reason == ""


def test_due_for_checkpoint_respects_first_checkpoint_of_zero():
    watcher = TokenWatcher()
    _start(watcher, now=1000.0)

    due = watcher.due_for_checkpoint(now=1000.0)  # チェックポイント0秒は即時到達
    assert len(due) == 1
    assert due[0].mint == "MINT1"


def test_due_for_checkpoint_respects_later_checkpoints():
    watcher = TokenWatcher()
    token = _start(watcher, now=1000.0)
    watcher.mark_checkpoint_done(token)  # 0秒チェックポイントを消化済みにする

    assert watcher.due_for_checkpoint(now=1030.0) == []  # まだ60秒経ってない
    due = watcher.due_for_checkpoint(now=1061.0)
    assert len(due) == 1


def test_due_for_checkpoint_excludes_finished_tokens():
    watcher = TokenWatcher()
    token = _start(watcher, now=1000.0)
    for _ in range(len(config.MIGRATION_CHECKPOINTS_SECONDS)):
        watcher.mark_checkpoint_done(token)

    assert token.finished is True
    assert watcher.due_for_checkpoint(now=100000.0) == []


def test_current_checkpoint_seconds_advances():
    watcher = TokenWatcher()
    token = _start(watcher, now=1000.0)
    assert watcher.current_checkpoint_seconds(token) == 0

    watcher.mark_checkpoint_done(token)
    assert token.checkpoint_index == 1
    assert watcher.current_checkpoint_seconds(token) == 60


def test_mark_checkpoint_done_marks_finished_after_last_checkpoint():
    watcher = TokenWatcher()
    token = _start(watcher, now=1000.0)
    for expected_seconds in config.MIGRATION_CHECKPOINTS_SECONDS:
        assert watcher.current_checkpoint_seconds(token) == expected_seconds
        assert token.finished is False
        watcher.mark_checkpoint_done(token)

    assert token.finished is True


def test_forget_removes_token():
    watcher = TokenWatcher()
    _start(watcher)
    watcher.forget("MINT1")
    assert watcher.get("MINT1") is None
    assert len(watcher) == 0


def test_evicts_oldest_when_over_capacity(monkeypatch):
    monkeypatch.setattr(config, "MAX_TRACKED_TOKENS", 2)
    watcher = TokenWatcher()
    _start(watcher, mint="OLD", now=1000.0)
    _start(watcher, mint="MID", now=1001.0)
    assert len(watcher) == 2

    _start(watcher, mint="NEW", now=1002.0)  # 上限超過、最古(OLD)が間引かれる
    assert len(watcher) == 2
    assert watcher.get("OLD") is None
    assert watcher.get("MID") is not None
    assert watcher.get("NEW") is not None
