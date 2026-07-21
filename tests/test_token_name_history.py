"""token_name_history.py の単体テスト。ネットワーク不要で実行できる。"""
from __future__ import annotations

import json

import pytest

import config
from token_name_history import TokenNameHistory


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TOKEN_NAME_HISTORY_FILE_PATH", tmp_path / "token_name_history.json")


def test_first_occurrence_returns_none():
    history = TokenNameHistory()
    assert history.check_and_record("Mint1", "PepeCoin", "PEPE") is None


def test_same_mint_seen_twice_is_not_a_duplicate():
    history = TokenNameHistory()
    history.check_and_record("Mint1", "PepeCoin", "PEPE")
    assert history.check_and_record("Mint1", "PepeCoin", "PEPE") is None


def test_different_mint_same_name_is_flagged():
    history = TokenNameHistory()
    history.check_and_record("Mint1", "PepeCoin", "PEPE")
    reason = history.check_and_record("Mint2", "PepeCoin", "DIFFERENT")
    assert reason is not None
    assert "Mint1" in reason


def test_different_mint_same_symbol_is_flagged():
    history = TokenNameHistory()
    history.check_and_record("Mint1", "PepeCoin", "PEPE")
    reason = history.check_and_record("Mint2", "Different Name", "PEPE")
    assert reason is not None
    assert "Mint1" in reason


def test_name_match_is_case_and_whitespace_insensitive():
    history = TokenNameHistory()
    history.check_and_record("Mint1", "PepeCoin", "PEPE")
    reason = history.check_and_record("Mint2", "  pepecoin  ", "OTHER")
    assert reason is not None


def test_original_mint_stays_the_reference_after_a_duplicate_appears():
    history = TokenNameHistory()
    history.check_and_record("Mint1", "PepeCoin", "PEPE")
    history.check_and_record("Mint2", "PepeCoin", "PEPE")
    reason = history.check_and_record("Mint3", "PepeCoin", "PEPE")
    assert "Mint1" in reason
    assert "Mint2" not in reason


def test_empty_name_and_symbol_never_flagged():
    history = TokenNameHistory()
    history.check_and_record("Mint1", "", "")
    assert history.check_and_record("Mint2", "", "") is None


def test_persists_to_disk_and_reloads():
    history = TokenNameHistory()
    history.check_and_record("Mint1", "PepeCoin", "PEPE")

    assert config.TOKEN_NAME_HISTORY_FILE_PATH.exists()
    data = json.loads(config.TOKEN_NAME_HISTORY_FILE_PATH.read_text(encoding="utf-8"))
    assert data["by_name"]["pepecoin"] == "Mint1"
    assert data["by_symbol"]["pepe"] == "Mint1"

    reloaded = TokenNameHistory()
    reason = reloaded.check_and_record("Mint2", "PepeCoin", "PEPE")
    assert reason is not None
    assert "Mint1" in reason


def test_missing_file_starts_empty():
    history = TokenNameHistory()
    assert history.check_and_record("Mint1", "Anything", "ANY") is None


def test_corrupt_file_starts_empty():
    config.TOKEN_NAME_HISTORY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.TOKEN_NAME_HISTORY_FILE_PATH.write_text("not json", encoding="utf-8")

    history = TokenNameHistory()
    assert history.check_and_record("Mint1", "Anything", "ANY") is None
