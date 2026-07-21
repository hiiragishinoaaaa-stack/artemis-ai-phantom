"""discord_notifier.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import config
import discord_notifier
import scoring
from token_watcher import TokenWatcher


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(config, "HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT", 50.0)
    monkeypatch.setattr(config, "HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT", 20.0)
    monkeypatch.setattr(config, "DISCORD_HOLDER_CONCENTRATION_WARN_EMOJI", "⚠️")
    monkeypatch.setattr(config, "DISCORD_HOLDER_CONCENTRATION_HEALTHY_EMOJI", "✅")
    monkeypatch.setattr(config, "DISCORD_TWITTER_EMOJI", "🐦")
    monkeypatch.setattr(config, "DISCORD_TELEGRAM_EMOJI", "✈️")
    monkeypatch.setattr(config, "DISCORD_DUPLICATE_NAME_EMOJI", "🚨")
    monkeypatch.setattr(config, "DASHBOARD_PUBLIC_URL", "")


def _sent_content(mock_urlopen) -> str:
    """json.dumps(ensure_ascii=True)でエスケープされた本文を、元の文字列に戻す。"""
    request = mock_urlopen.call_args[0][0]
    return json.loads(request.data.decode("utf-8"))["content"]


def _sent_payload(mock_urlopen) -> dict:
    request = mock_urlopen.call_args[0][0]
    return json.loads(request.data.decode("utf-8"))


def _button_urls(payload: dict) -> list[str]:
    buttons = payload.get("components", [{}])[0].get("components", [])
    return [b["url"] for b in buttons]


def _token(name: str = "Test Coin", symbol: str = "TEST", unique_buyers_m5: int = 0, **overrides):
    watcher = TokenWatcher()
    token = watcher.start_tracking(mint="MintAddr123", name=name, symbol=symbol, now=1000.0)
    token.unique_buyers_m5 = unique_buyers_m5
    token.top10_holders_pct = overrides.get("top10_holders_pct")
    token.has_twitter = overrides.get("has_twitter", False)
    token.has_telegram = overrides.get("has_telegram", False)
    token.dexscreener_url = overrides.get("dexscreener_url", "")
    token.duplicate_name_reason = overrides.get("duplicate_name_reason", "")
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


def test_notify_sends_minimal_message_with_score(monkeypatch):
    """本文にはスコアのみ(mintアドレスやtier名の文字列は含めない、
    mintアドレスはボタンのURL側にのみ含まれる)。"""
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "PHANTOM_REFERRAL_ID", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        mock_urlopen.assert_called_once()
        content = _sent_content(mock_urlopen)
        assert "85/100" in content
        assert "MintAddr123" not in content
        assert "WATCH" not in content


def test_notify_button_links_to_phantom_without_referral_id(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "PHANTOM_REFERRAL_ID", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        urls = _button_urls(_sent_payload(mock_urlopen))
        assert "https://phantom.com/tokens/solana/MintAddr123" in urls
        assert not any("referralId" in u for u in urls)


def test_notify_button_links_to_phantom_with_referral_id(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "PHANTOM_REFERRAL_ID", "it5dy15sgab")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        urls = _button_urls(_sent_payload(mock_urlopen))
        assert "https://phantom.com/tokens/solana/MintAddr123?referralId=it5dy15sgab" in urls


def test_notify_appends_with_components_query_param_when_sending_buttons(monkeypatch):
    """通常のWebhook(application-owned webhookでないもの)はwith_components=true
    が無いとcomponentsを黙って無視するため、ボタン送信時は必ず付与する
    (エラーにはならずボタンだけ付かずに届く、という気付きにくい不具合の再発防止)。"""
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://discord.com/api/webhooks/x?with_components=true"


def test_notify_omits_detail_button_when_dashboard_url_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "DASHBOARD_PUBLIC_URL", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        payload = _sent_payload(mock_urlopen)
        buttons = payload["components"][0]["components"]
        assert len(buttons) == 1
        assert buttons[0]["label"] == "Phantomで開く"


def test_notify_includes_detail_button_when_dashboard_url_set(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "DASHBOARD_PUBLIC_URL", "http://76.13.180.239:8790")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(), _score(85), "WATCH", 60)
        payload = _sent_payload(mock_urlopen)
        buttons = payload["components"][0]["components"]
        assert len(buttons) == 2
        assert buttons[0]["label"] == "詳細"
        assert buttons[0]["url"] == "http://76.13.180.239:8790/token/MintAddr123"
        assert buttons[1]["label"] == "Phantomで開く"


def test_notify_detail_button_prefers_dexscreener_url_over_dashboard(monkeypatch):
    """SupabaseやDASHBOARD_PUBLIC_URLが無くても(落ちていても)、DexScreenerの
    ページは外部サービスなので常に「詳細」ボタンが機能する(こちら優先)。"""
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "DASHBOARD_PUBLIC_URL", "http://76.13.180.239:8790")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(
            _token(dexscreener_url="https://dexscreener.com/solana/MintAddr123"), _score(85), "WATCH", 60
        )
        payload = _sent_payload(mock_urlopen)
        buttons = payload["components"][0]["components"]
        assert buttons[0]["label"] == "詳細"
        assert buttons[0]["url"] == "https://dexscreener.com/solana/MintAddr123"


def test_notify_detail_button_falls_back_to_dashboard_when_no_dexscreener_url(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "DASHBOARD_PUBLIC_URL", "http://76.13.180.239:8790")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(dexscreener_url=""), _score(85), "WATCH", 60)
        payload = _sent_payload(mock_urlopen)
        buttons = payload["components"][0]["components"]
        assert buttons[0]["label"] == "詳細"
        assert buttons[0]["url"] == "http://76.13.180.239:8790/token/MintAddr123"


def test_notify_omits_detail_button_when_neither_dexscreener_url_nor_dashboard_url_set(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "DASHBOARD_PUBLIC_URL", "")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(dexscreener_url=""), _score(85), "WATCH", 60)
        payload = _sent_payload(mock_urlopen)
        buttons = payload["components"][0]["components"]
        assert len(buttons) == 1
        assert buttons[0]["label"] == "Phantomで開く"


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
        assert content.count("\n") == 0  # スコア行のみ


def test_notify_shows_duplicate_name_warning(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(
            _token(duplicate_name_reason="同じ名前「Test Coin」を名乗るトークンが既出です(先行mint: MINT0)"),
            _score(80),
            "HIGH",
            60,
        )
        content = _sent_content(mock_urlopen)
        assert "🚨" in content
        assert "なりすまし注意" in content
        assert "MINT0" in content


def test_notify_omits_duplicate_name_warning_when_no_duplicate(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(duplicate_name_reason=""), _score(80), "HIGH", 60)
        content = _sent_content(mock_urlopen)
        assert "なりすまし注意" not in content


def test_notify_uses_custom_discord_emoji_for_holder_badge(monkeypatch):
    """DISCORD_HOLDER_CONCENTRATION_*_EMOJIをDiscordのカスタム絵文字記法
    (<:name:id>)に差し替えても、そのままメッセージに使われること。"""
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(config, "DISCORD_HOLDER_CONCENTRATION_HEALTHY_EMOJI", "<:safe:123456789012345678>")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(top10_holders_pct=10.0), _score(95), "HIGH", 20)
        content = _sent_content(mock_urlopen)
        assert "<:safe:123456789012345678>" in content.splitlines()[0]


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
        assert mock_urlopen.call_args[0][0].full_url.startswith("https://discord.com/api/webhooks/main")


def test_notify_sends_to_both_webhooks_when_score_is_100_regardless_of_stars(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/main")
    monkeypatch.setattr(config, "DISCORD_PERFECT_SCORE_WEBHOOK_URL", "https://discord.com/api/webhooks/perfect")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(unique_buyers_m5=0), _score(100), "HIGH", 60)
        assert mock_urlopen.call_count == 2
        urls = {call.args[0].full_url for call in mock_urlopen.call_args_list}
        assert urls == {
            "https://discord.com/api/webhooks/main?with_components=true",
            "https://discord.com/api/webhooks/perfect?with_components=true",
        }


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
        assert mock_urlopen.call_args[0][0].full_url.startswith("https://discord.com/api/webhooks/followup")
        content = _sent_content(mock_urlopen)
        assert "⭐⭐⭐" in content
        assert "90/100" in content
        urls = _button_urls(_sent_payload(mock_urlopen))
        assert "https://phantom.com/tokens/solana/MintAddr123" in urls


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
            assert score_line.startswith(f"{expected_stars} ")
        else:
            assert "⭐" not in score_line
            assert score_line.startswith("85/100")


def test_notify_score_line_order_is_stars_then_score_then_holder_badge(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(
            _token(unique_buyers_m5=10, top10_holders_pct=10.0), _score(92), "HIGH", 60
        )
        content = _sent_content(mock_urlopen)
        score_line = content.splitlines()[0]
        assert score_line == "⭐⭐⭐ 92/100 ✅"


def test_notify_shows_warn_badge_when_holders_concentrated(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(top10_holders_pct=60.0), _score(85), "WATCH", 60)
        content = _sent_content(mock_urlopen)
        score_line = content.splitlines()[0]
        assert "⚠️" in score_line
        assert "✅" not in score_line


def test_notify_shows_healthy_badge_when_holders_distributed(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(top10_holders_pct=10.0), _score(85), "WATCH", 60)
        content = _sent_content(mock_urlopen)
        score_line = content.splitlines()[0]
        assert "✅" in score_line
        assert "⚠️" not in score_line


def test_notify_shows_no_holder_badge_when_neutral_or_unknown(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(top10_holders_pct=35.0), _score(85), "WATCH", 60)
        content = _sent_content(mock_urlopen)
        score_line = content.splitlines()[0]
        assert "⚠️" not in score_line
        assert "✅" not in score_line

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(top10_holders_pct=None), _score(85), "WATCH", 60)
        content = _sent_content(mock_urlopen)
        score_line = content.splitlines()[0]
        assert "⚠️" not in score_line
        assert "✅" not in score_line


def test_notify_shows_social_badges_next_to_name(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(
            _token(name="Some Coin", symbol="SOME", has_twitter=True, has_telegram=True), _score(85), "WATCH", 60
        )
        content = _sent_content(mock_urlopen)
        name_line = content.splitlines()[1]
        assert "Some Coin ($SOME)" in name_line
        assert "🐦" in name_line
        assert "✈️" in name_line


def test_notify_omits_social_badges_when_no_socials_detected(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")

    with patch("urllib.request.urlopen") as mock_urlopen:
        discord_notifier.notify_score_update(_token(name="Some Coin", symbol="SOME"), _score(85), "WATCH", 60)
        content = _sent_content(mock_urlopen)
        assert "🐦" not in content
        assert "✈️" not in content
