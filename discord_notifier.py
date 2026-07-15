"""Discord Webhook通知(スコアが通知ラインを超えたトークンの通知)。

外部ライブラリを追加しないため、urllib.requestで直接Discord WebhookへPOSTする
(mt5-ai-traderのdiscord_notifier.pyと同じ方式)。DISCORD_ENABLED=false、
またはWebhook URL未設定の場合は何もしない(既定OFF)。送信の失敗はログに
記録するだけで、呼び出し元(監視ループ)には一切影響させない。

通知は2段階のみ(HIGH/WATCH)。LOWはログ保存のみでDiscordへは送らない
(main.py側でtier=="LOW"の場合はこのモジュールを呼ばない)。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config
from scoring import ScoreResult
from token_watcher import TrackedToken

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 5
# discord.com手前のCloudflareが、urllib標準のUser-Agent(例: "Python-urllib/3.12")
# を自動化されたアクセスとみなして403(error code: 1010)で拒否するため、
# 一般的なブラウザのUser-Agentを明示的に指定する。
_USER_AGENT = "Mozilla/5.0 (compatible; ARTEMIS-Phantom-Sniper/1.0)"

_TIER_EMOJI = {
    "HIGH": "🚨",
    "WATCH": "⚠",
}


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


def _phantom_link(mint: str) -> str:
    """Phantomアプリでこのトークンを直接開くリンクを組み立てる。

    PHANTOM_REFERRAL_ID(個人に紐づく値、.envでのみ設定)が設定されて
    いれば付与する。未設定でもリンク自体は生成される。
    """
    url = f"https://phantom.com/tokens/solana/{mint}"
    if config.PHANTOM_REFERRAL_ID:
        url += f"?{urllib.parse.urlencode({'referralId': config.PHANTOM_REFERRAL_ID})}"
    return url


def notify_score_update(
    token: TrackedToken,
    score: ScoreResult,
    tier: str,
    elapsed_seconds: int,
) -> None:
    """スコアが通知ライン(WATCH以上)を超えた/更新された瞬間に呼び出す。

    コピペ・タップだけで済むことを想定し、内容はスコア・銘柄名・mint
    アドレス・Phantomで開くリンクのみの最小限にしている(2026-07、
    ユーザー希望により出来高等の長文詳細・注意書きは削除。詳細はDEBUG
    ログ側に残る)。
    """
    emoji = _TIER_EMOJI.get(tier, tier)
    lines = [f"{emoji} {tier} Score: {score.total}/100"]

    name = token.name.strip()
    symbol = token.symbol.strip()
    if name or symbol:
        label = f"{name} (${symbol})" if name and symbol else (name or f"${symbol}")
        lines.append(label)

    lines.append(f"`{token.mint}`")
    lines.append(_phantom_link(token.mint))
    _send("\n".join(lines))
