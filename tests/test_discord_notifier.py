"""discord_notifier.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import config
import discord_notifier
import scoring
from token_watcher import TokenWatcher


def _sent_content(mock_urlopen) -> str:
    """json.dumps(ensure_ascii=True)でエスケープされた本文を、元の文字列に戻す。"""
    request = mock_urlopen.call_args[0][0]
    return json.loads(request.data.decode("utf-8"))["content"]


def _token(name: str = "Test Coin", symbol: str = "TEST", unique_buyers_m5: int = 0, **overrides):
    watcher = TokenWatcher()
    token = watcher.start_tracking(mint="MintAddr123", name=name, symbol=symbol, now=1000.0)
    token.unique_buyers_m5 = unique_buyers_m5
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
    monkeypatch.setattr(config, "PHANTOM_REFERRAL_ID", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        mock_urlopen.assert_called_once()
        content = _sent_content(mock_urlopen)
        assert "MintAddr123" in content
        assert "85/100" in content
        assert "WATCH" in content


def test_notify_includes_phantom_link_without_referral_id(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "PHANTOM_REFERRAL_ID", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        content = _sent_content(mock_urlopen)
        assert "https://phantom.com/tokens/solana/MintAddr123" in content
        assert "referralId" not in content


def test_notify_includes_phantom_link_with_referral_id(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "PHANTOM_REFERRAL_ID", "it5dy15sgab")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        content = _sent_content(mock_urlopen)
        assert "https://phantom.com/tokens/solana/MintAddr123?referralId=it5dy15sgab" in content


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
        assert content.count("\n") == 2  # スコア行・mint行・Phantomリンク行の3行のみ


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


def test_notify_sends_only_to_main_webhook_when_score_below_100(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/main")
    monkeypatch.setattr(config, "DISCORD_PERFECT_SCORE_WEBHOOK_URL", "https://discord.com/api/webhooks/perfect")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(99), "HIGH", 60)
        mock_urlopen.assert_called_once()
        assert mock_urlopen.call_args[0][0].full_url == "https://discord.com/api/webhooks/main"


def test_notify_sends_to_both_webhooks_when_score_is_100_regardless_of_stars(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/main")
    monkeypatch.setattr(config, "DISCORD_PERFECT_SCORE_WEBHOOK_URL", "https://discord.com/api/webhooks/perfect")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(unique_buyers_m5=0), _score(100), "HIGH", 60)
        assert mock_urlopen.call_count == 2
        urls = {call.args[0].full_url for call in mock_urlopen.call_args_list}
        assert urls == {"https://discord.com/api/webhooks/main", "https://discord.com/api/webhooks/perfect"}


def test_notify_score_100_does_not_send_to_perfect_channel_when_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/main")
    monkeypatch.setattr(config, "DISCORD_PERFECT_SCORE_WEBHOOK_URL", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(unique_buyers_m5=10), _score(100), "HIGH", 60)
        mock_urlopen.assert_called_once()


def test_notify_star_upgrade_does_nothing_when_followup_webhook_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_FOLLOWUP_WEBHOOK_URL", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_star_upgrade(_token(unique_buyers_m5=10), _score(90), "HIGH", 300)
        mock_urlopen.assert_not_called()


def test_notify_star_upgrade_sends_to_followup_webhook_only(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/main")
    monkeypatch.setattr(config, "DISCORD_FOLLOWUP_WEBHOOK_URL", "https://discord.com/api/webhooks/followup")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_star_upgrade(_token(unique_buyers_m5=10), _score(90), "HIGH", 300)
        mock_urlopen.assert_called_once()
        assert mock_urlopen.call_args[0][0].full_url == "https://discord.com/api/webhooks/followup"
        content = _sent_content(mock_urlopen)
        assert "⭐⭐⭐" in content
        assert "MintAddr123" in content
        assert "90/100" in content


def test_notify_star_upgrade_fires_with_just_one_star(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_FOLLOWUP_WEBHOOK_URL", "https://discord.com/api/webhooks/followup")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_star_upgrade(_token(unique_buyers_m5=2), _score(90), "HIGH", 300)
        content = _sent_content(mock_urlopen)
        assert "⭐" in content
        assert "⭐⭐" not in content


@pytest.mark.parametrize(
    "unique_buyers_m5,expected_stars",
    [(0, ""), (1, ""), (2, "⭐"), (4, "⭐"), (5, "⭐⭐"), (9, "⭐⭐"), (10, "⭐⭐⭐"), (30, "⭐⭐⭐")],
)
def test_notify_shows_unique_buyer_stars(monkeypatch, unique_buyers_m5, expected_stars):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(unique_buyers_m5=unique_buyers_m5), _score(85), "WATCH", 60)
        content = _sent_content(mock_urlopen)
        score_line = content.splitlines()[0]
        if expected_stars:
            assert score_line.endswith(f" {expected_stars}")
        else:
            assert "⭐" not in score_line
