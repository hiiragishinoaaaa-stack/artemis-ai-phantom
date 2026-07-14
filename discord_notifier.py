"""Discord Webhook通知(新規トークンが条件を満たしたときの通知)。

外部ライブラリを追加しないため、urllib.requestで直接Discord WebhookへPOSTする
(mt5-ai-traderのdiscord_notifier.pyと同じ方式)。DISCORD_ENABLED=false、
またはWebhook URL未設定の場合は何もしない(既定OFF)。送信の失敗はログに
記録するだけで、呼び出し元(監視ループ)には一切影響させない。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import config
from token_watcher import TrackedToken

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 5
# discord.com手前のCloudflareが、urllib標準のUser-Agent(例: "Python-urllib/3.12")
# を自動化されたアクセスとみなして403(error code: 1010)で拒否するため、
# 一般的なブラウザのUser-Agentを明示的に指定する。
_USER_AGENT = "Mozilla/5.0 (compatible; ARTEMIS-Phantom-Sniper/1.0)"


def _send(content: str) -> None:
    if not config.DISCORD_ENABLED or not config.DISCORD_WEBHOOK_URL:
        return

    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        config.DISCORD_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("discord_notifier: Discordへの通知送信に失敗しました: %s", exc)


def notify_token_passed_filter(token: TrackedToken) -> None:
    """観察期間を終え、フィルター条件を満たした新規トークンをDiscordへ通知する。

    自動売買は一切行わない。あくまで人間が判断するための情報提供。
    """
    name = token.name or "(no name)"
    symbol = token.symbol or "?"
    _send(
        f"🚀 **{name} (${symbol})** が初動フィルターを通過しました\n"
        f"Mint: `{token.mint}`\n"
        f"買い: {token.buy_count}件(ユニーク{len(token.unique_buyers)}人) / 売り: {token.sell_count}件\n"
        f"時価総額: {token.last_market_cap_sol:.2f} SOL\n"
        f"pump.fun: https://pump.fun/coin/{token.mint}\n"
        f"DexScreener: https://dexscreener.com/solana/{token.mint}\n"
        f"⚠️ 自動売買はしていません。必ず自分で内容を確認してから判断してください"
        f"(詐欺・ラグプルの可能性は常にあります)。"
    )
