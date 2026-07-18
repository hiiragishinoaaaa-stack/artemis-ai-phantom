"""supabase_client.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import config
import supabase_client


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setattr(config, "SUPABASE_SERVICE_ROLE_KEY", "service-role-secret")


def _response(payload) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_is_configured_true_when_both_set():
    assert supabase_client.is_configured() is True


def test_is_configured_false_when_url_missing(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    assert supabase_client.is_configured() is False


def test_is_configured_false_when_key_missing(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_SERVICE_ROLE_KEY", "")
    assert supabase_client.is_configured() is False


def test_insert_notification_does_nothing_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    with patch("urllib.request.urlopen") as mock_urlopen:
        supabase_client.insert_notification({"mint": "MINT1"})
        mock_urlopen.assert_not_called()


def test_insert_notification_posts_to_notifications_table():
    with patch("urllib.request.urlopen", return_value=_response({})) as mock_urlopen:
        supabase_client.insert_notification({"mint": "MINT1", "score": 90})
        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://project-ref.supabase.co/rest/v1/notifications"
        assert request.get_method() == "POST"
        assert request.get_header("Apikey") == "service-role-secret"
        assert request.get_header("Authorization") == "Bearer service-role-secret"
        assert json.loads(request.data.decode("utf-8")) == {"mint": "MINT1", "score": 90}


def test_insert_outcome_posts_to_outcomes_table():
    with patch("urllib.request.urlopen", return_value=_response({})) as mock_urlopen:
        supabase_client.insert_outcome({"mint": "MINT1", "change_pct": 12.5})
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://project-ref.supabase.co/rest/v1/outcomes"


def test_upsert_creator_blocklist_sets_merge_duplicates_prefer_header():
    with patch("urllib.request.urlopen", return_value=_response({})) as mock_urlopen:
        supabase_client.upsert_creator_blocklist("CreatorAddr1", "RugCheck危険フラグ: danger")
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://project-ref.supabase.co/rest/v1/creator_blocklist"
        prefer = request.get_header("Prefer")
        assert "resolution=merge-duplicates" in prefer
        assert json.loads(request.data.decode("utf-8")) == {
            "creator": "CreatorAddr1",
            "reason": "RugCheck危険フラグ: danger",
        }


def test_insert_notification_failure_does_not_raise():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        supabase_client.insert_notification({"mint": "MINT1"})  # 例外を送出しないことを確認


def test_fetch_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    with patch("urllib.request.urlopen") as mock_urlopen:
        assert supabase_client.fetch("notifications?select=*") is None
        mock_urlopen.assert_not_called()


def test_fetch_returns_list_on_success():
    with patch("urllib.request.urlopen", return_value=_response([{"mint": "MINT1"}])) as mock_urlopen:
        result = supabase_client.fetch("notifications?select=*&limit=10")
        assert result == [{"mint": "MINT1"}]
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://project-ref.supabase.co/rest/v1/notifications?select=*&limit=10"
        assert request.get_method() == "GET"


def test_fetch_returns_none_on_network_error():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert supabase_client.fetch("notifications?select=*") is None


def test_fetch_returns_none_on_non_list_response():
    with patch("urllib.request.urlopen", return_value=_response({"message": "error"})):
        assert supabase_client.fetch("notifications?select=*") is None


def test_fetch_returns_none_on_invalid_json():
    resp = MagicMock()
    resp.read.return_value = b"not json"
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=resp):
        assert supabase_client.fetch("notifications?select=*") is None
