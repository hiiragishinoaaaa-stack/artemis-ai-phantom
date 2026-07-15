"""discord_notifier.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

from unittest.mock import patch

import config
import discord_notifier
import scoring
from token_watcher import TokenWatcher


def _token(**overrides):
    watcher = TokenWatcher()
    token = watcher.start_tracking(mint="MintAddr123", name="Test Coin", symbol="TEST", now=1000.0)
    token.buys_m5 = overrides.get("buys_m5", 7)
    token.sells_m5 = overrides.get("sells_m5", 1)
    token.volume_m5_usd = overrides.get("volume_m5_usd", 1234.0)
    token.price_change_m5_pct = overrides.get("price_change_m5_pct", 42.0)
    token.liquidity_usd = overrides.get("liquidity_usd", 5000.0)
    token.market_cap_usd = overrides.get("market_cap_usd", 80000.0)
    token.dexscreener_url = overrides.get("dexscreener_url", "")
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


def test_notify_sends_request_with_token_and_score_details(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args[0][0]
        body = request.data.decode("utf-8")
        assert "Test Coin" in body
        assert "TEST" in body
        assert "MintAddr123" in body
        assert "pump.fun/coin/MintAddr123" in body
        assert "dexscreener.com/solana/MintAddr123" in body
        assert "85/100" in body
        assert "60" in body
        assert "WATCH" in body


def test_notify_uses_dexscreener_url_when_available(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        token = _token(dexscreener_url="https://dexscreener.com/solana/somepair")
        discord_notifier.notify_score_update(token, _score(), "WATCH", 60)
        body = mock_urlopen.call_args[0][0].data.decode("utf-8")
        assert "https://dexscreener.com/solana/somepair" in body


def test_notify_high_tier_uses_high_priority_header(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(95), "HIGH", 20)
        request = mock_urlopen.call_args[0][0]
        body = request.data.decode("utf-8")
        assert "HIGH PRIORITY" in body


def test_notify_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        discord_notifier.notify_score_update(_token(), _score(), "WATCH", 60)  # 例外を送出しないことを確認
