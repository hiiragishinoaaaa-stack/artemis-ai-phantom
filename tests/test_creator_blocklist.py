"""creator_blocklist.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import json

import pytest

import config
from creator_blocklist import CreatorBlocklist


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CREATOR_BLOCKLIST_FILE_PATH", tmp_path / "creator_blocklist.json")


def test_is_blocked_returns_none_for_unknown_creator():
    blocklist = CreatorBlocklist()
    assert blocklist.is_blocked("CreatorAddr1") is None


def test_is_blocked_returns_none_for_empty_string():
    blocklist = CreatorBlocklist()
    assert blocklist.is_blocked("") is None


def test_record_and_is_blocked_round_trip():
    blocklist = CreatorBlocklist()
    blocklist.record("CreatorAddr1", "RugCheck危険フラグ: Mint authority still active")

    assert blocklist.is_blocked("CreatorAddr1") == "RugCheck危険フラグ: Mint authority still active"
    assert len(blocklist) == 1


def test_record_does_not_overwrite_existing_reason():
    blocklist = CreatorBlocklist()
    blocklist.record("CreatorAddr1", "first reason")
    blocklist.record("CreatorAddr1", "second reason")

    assert blocklist.is_blocked("CreatorAddr1") == "first reason"


def test_record_ignores_empty_creator():
    blocklist = CreatorBlocklist()
    blocklist.record("", "some reason")
    assert len(blocklist) == 0


def test_record_persists_to_disk_and_reloads():
    blocklist = CreatorBlocklist()
    blocklist.record("CreatorAddr1", "通知後に-95%下落")

    assert config.CREATOR_BLOCKLIST_FILE_PATH.exists()
    data = json.loads(config.CREATOR_BLOCKLIST_FILE_PATH.read_text(encoding="utf-8"))
    assert data == {"CreatorAddr1": "通知後に-95%下落"}

    reloaded = CreatorBlocklist()
    assert reloaded.is_blocked("CreatorAddr1") == "通知後に-95%下落"
    assert len(reloaded) == 1


def test_missing_file_starts_empty():
    blocklist = CreatorBlocklist()
    assert len(blocklist) == 0


def test_corrupt_file_starts_empty(monkeypatch):
    config.CREATOR_BLOCKLIST_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CREATOR_BLOCKLIST_FILE_PATH.write_text("not json", encoding="utf-8")

    blocklist = CreatorBlocklist()
    assert len(blocklist) == 0
