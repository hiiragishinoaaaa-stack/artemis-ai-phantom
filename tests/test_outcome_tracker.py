"""outcome_tracker.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import json

import pytest

import config
from outcome_tracker import OutcomeTracker


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OUTCOME_CHECKPOINTS_SECONDS", (1800, 3600, 86400))
    monkeypatch.setattr(config, "OUTCOMES_FILE_PATH", tmp_path / "outcomes.jsonl")


def _register(
    tracker: OutcomeTracker,
    mint: str = "MINT1",
    now: float = 1000.0,
    market_cap_usd: float = 10000.0,
    creator: str = "",
):
    tracker.register(
        mint=mint,
        name="Test Coin",
        symbol="TEST",
        tier="WATCH",
        score=85,
        market_cap_usd=market_cap_usd,
        now=now,
        creator=creator,
    )


def test_register_starts_tracking():
    tracker = OutcomeTracker()
    _register(tracker)
    assert tracker.is_tracking("MINT1") is True
    assert len(tracker) == 1


def test_register_does_not_overwrite_existing():
    tracker = OutcomeTracker()
    _register(tracker, market_cap_usd=10000.0)
    _register(tracker, market_cap_usd=999999.0)  # 既に追跡中なので無視される

    outcome = tracker.due_for_checkpoint(now=1000.0 + 1800)[0]
    assert outcome.market_cap_at_notify_usd == 10000.0


def test_update_market_cap_ignores_untracked_mint():
    tracker = OutcomeTracker()
    tracker.update_market_cap("UNKNOWN", 50000.0)  # 例外を送出しないことを確認


def test_due_for_checkpoint_respects_first_checkpoint():
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0)

    assert tracker.due_for_checkpoint(now=1000.0 + 1799) == []
    due = tracker.due_for_checkpoint(now=1000.0 + 1800)
    assert len(due) == 1
    assert due[0].mint == "MINT1"


def test_due_for_checkpoint_excludes_in_flight_outcomes():
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0)
    outcome = tracker.due_for_checkpoint(now=1000.0 + 1800)[0]
    tracker.mark_in_flight(outcome)

    assert tracker.due_for_checkpoint(now=1000.0 + 1800) == []


def test_clear_in_flight_makes_outcome_due_again():
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0)
    outcome = tracker.due_for_checkpoint(now=1000.0 + 1800)[0]
    tracker.mark_in_flight(outcome)
    tracker.clear_in_flight(outcome)

    due = tracker.due_for_checkpoint(now=1000.0 + 1800)
    assert len(due) == 1
    assert due[0].mint == "MINT1"


def test_record_and_advance_writes_jsonl_and_advances_checkpoint():
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0, market_cap_usd=10000.0)
    tracker.update_market_cap("MINT1", 15000.0)  # +50%

    outcome = tracker.due_for_checkpoint(now=1000.0 + 1800)[0]
    change_pct = tracker.record_and_advance(outcome)

    assert change_pct == pytest.approx(50.0)
    assert outcome.checkpoint_index == 1
    assert outcome.finished is False

    lines = config.OUTCOMES_FILE_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["mint"] == "MINT1"
    assert record["checkpoint_seconds"] == 1800
    assert record["change_pct"] == pytest.approx(50.0)


def test_record_and_advance_reports_large_negative_change_on_crash():
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0, market_cap_usd=10000.0, creator="CreatorAddr1")
    tracker.update_market_cap("MINT1", 500.0)  # -95%

    outcome = tracker.due_for_checkpoint(now=1000.0 + 1800)[0]
    change_pct = tracker.record_and_advance(outcome)

    assert change_pct == pytest.approx(-95.0)
    assert outcome.creator == "CreatorAddr1"


def test_record_and_advance_records_null_when_market_cap_unavailable():
    """時価総額を取得できなかった記録は、0%でも-100%でもなくnullで残す。

    ここを数値で埋めると、取得失敗が『ちょうど-100.0%』の偽のラグとして
    集計に混ざる(analyze_buckets.py の『データ品質』欄を参照)。
    """
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0, market_cap_usd=10000.0, creator="CreatorAddr1")

    outcome = tracker.due_for_checkpoint(now=1000.0 + 1800)[0]
    change_pct = tracker.record_and_advance(outcome, market_cap_available=False)

    assert change_pct is None
    assert outcome.checkpoint_index == 1

    record = json.loads(config.OUTCOMES_FILE_PATH.read_text(encoding="utf-8").strip())
    assert record["change_pct"] is None
    assert record["market_cap_now_usd"] is None
    assert record["market_cap_at_notify_usd"] == 10000.0


def test_record_and_advance_records_null_when_notify_market_cap_missing():
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0, market_cap_usd=0.0)

    outcome = tracker.due_for_checkpoint(now=1000.0 + 1800)[0]
    assert tracker.record_and_advance(outcome) is None


def test_record_and_advance_still_reports_a_genuine_total_loss():
    """本物のラグ(取得はできたが時価総額がほぼ0)は、これまで通り記録する。"""
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0, market_cap_usd=10000.0)
    tracker.update_market_cap("MINT1", 1.0)

    outcome = tracker.due_for_checkpoint(now=1000.0 + 1800)[0]
    assert tracker.record_and_advance(outcome) == pytest.approx(-99.99)


def test_record_and_advance_marks_finished_after_last_checkpoint():
    tracker = OutcomeTracker()
    _register(tracker, now=1000.0)

    outcome = tracker._outcomes["MINT1"]
    for _ in range(len(config.OUTCOME_CHECKPOINTS_SECONDS)):
        tracker.record_and_advance(outcome)

    assert outcome.finished is True


def test_forget_removes_outcome():
    tracker = OutcomeTracker()
    _register(tracker)
    tracker.forget("MINT1")
    assert tracker.is_tracking("MINT1") is False
    assert len(tracker) == 0
