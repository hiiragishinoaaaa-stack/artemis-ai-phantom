"""discord_notifier.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import patch

import config
import discord_notifier
import scoring
from token_watcher import TokenWatcher


def _sent_content(mock_urlopen) -> str:
    """json.dumps(ensure_ascii=True)でエスケープされた本文を、元の文字列に戻す。"""
    request = mock_urlopen.call_args[0][0]
    return json.loads(request.data.decode("utf-8"))["content"]


def _token(name: str = "Test Coin", symbol: str = "TEST", **overrides):
    watcher = TokenWatcher()
    token = watcher.start_tracking(mint="MintAddr123", name=name, symbol=symbol, now=1000.0)
    return token


def _score(total: int = 85):
    return scoring.ScoreResult(total=total, components=[])


def test_notify_does_nothing_when_discord_disabled(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", False)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(), "WATCH", 60)
        mock_urlopen.assert_not_called()


def test_notify_does_nothing_when_webhook_url_missing(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(), "WATCH", 60)
        mock_urlopen.assert_not_called()


def test_notify_sends_minimal_message_with_score_and_mint(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        mock_urlopen.assert_called_once()
        content = _sent_content(mock_urlopen)
        assert "MintAddr123" in content
        assert "85/100" in content
        assert "WATCH" in content


def test_notify_includes_name_and_symbol_when_present(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(name="Some Coin", symbol="SOME"), _score(80), "HIGH", 60)
        content = _sent_content(mock_urlopen)
        assert "Some Coin ($SOME)" in content


def test_notify_omits_name_line_when_both_empty(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(name="", symbol=""), _score(80), "HIGH", 60)
        content = _sent_content(mock_urlopen)
        assert content.count("\n") == 1  # スコア行とmint行の2行のみ


def test_notify_high_tier_uses_high_emoji(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(95), "HIGH", 20)
        content = _sent_content(mock_urlopen)
        assert "🚨" in content
        assert "HIGH" in content


def test_notify_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        discord_notifier.notify_score_update(_token(), _score(), "WATCH", 60)  # 例外を送出しないことを確認
