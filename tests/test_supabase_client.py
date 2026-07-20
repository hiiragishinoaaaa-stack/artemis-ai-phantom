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


def _http_error(code: int, headers: dict | None = None) -> "urllib.error.HTTPError":
    import urllib.error

    return urllib.error.HTTPError(
        url="https://project-ref.supabase.co/rest/v1/notifications",
        code=code,
        msg="rate limited" if code == 429 else "error",
        hdrs=headers or {},
        fp=None,
    )


def test_insert_notification_retries_once_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(supabase_client.time, "sleep", lambda _seconds: None)
    responses = [_http_error(429), _response({})]

    def fake_urlopen(*_args, **_kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch("urllib.request.urlopen", side_effect=fake_urlopen) as mock_urlopen:
        supabase_client.insert_notification({"mint": "MINT1"})
        assert mock_urlopen.call_count == 2


def test_insert_notification_gives_up_after_one_retry_on_repeated_429(monkeypatch):
    monkeypatch.setattr(supabase_client.time, "sleep", lambda _seconds: None)
    with patch("urllib.request.urlopen", side_effect=_http_error(429)) as mock_urlopen:
        supabase_client.insert_notification({"mint": "MINT1"})  # 例外を送出しないことを確認
        assert mock_urlopen.call_count == 2  # 初回 + 1回だけ再試行


def test_insert_notification_does_not_retry_on_non_429_http_error(monkeypatch):
    monkeypatch.setattr(supabase_client.time, "sleep", lambda _seconds: None)
    with patch("urllib.request.urlopen", side_effect=_http_error(403)) as mock_urlopen:
        supabase_client.insert_notification({"mint": "MINT1"})
        mock_urlopen.assert_called_once()


def test_retry_after_seconds_uses_header_when_present():
    exc = _http_error(429, headers={"Retry-After": "2"})
    assert supabase_client._retry_after_seconds(exc) == 2.0


def test_retry_after_seconds_caps_header_value():
    exc = _http_error(429, headers={"Retry-After": "999"})
    assert supabase_client._retry_after_seconds(exc) == supabase_client._RATE_LIMIT_MAX_WAIT_SECONDS


def test_retry_after_seconds_falls_back_to_default_when_missing():
    exc = _http_error(429)
    assert supabase_client._retry_after_seconds(exc) == supabase_client._RATE_LIMIT_DEFAULT_WAIT_SECONDS


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
