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
from scoring import ScoreResult, star_count_for_unique_buyers
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


def _send(content: str, webhook_url: str) -> None:
    if not config.DISCORD_ENABLED or not webhook_url:
        return

    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("discord_notifier: Discordへの通知送信に失敗しました: %s", exc)


def _stars_display(unique_buyers_m5: int) -> str:
    """直近5分のユニーク買い手数を★0〜3個の文字列で表す(scoring.star_count_for_unique_buyers参照)。"""
    return "⭐" * star_count_for_unique_buyers(unique_buyers_m5)


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

    スコアが100点満点の場合、通常のDISCORD_WEBHOOK_URLに加えて
    DISCORD_PERFECT_SCORE_WEBHOOK_URL(満点専用チャンネル)にも同じ内容を
    送る(未設定なら送らない。★の数は問わない)。

    スコア行の末尾に、直近5分のユニーク買い手数を★0〜3個で表示する
    (少数のウォレットの自作自演ではなく、実際に多くの人が買っている
    ことをスコアの内訳を見なくても一目でわかるようにするため)。この
    時点で★0でも、後のチェックポイントで★1つ以上に育ったら
    `notify_star_upgrade()`が別途追い通知する(main.py参照)。
    """
    content = _build_message(token, score.total, tier)

    _send(content, config.DISCORD_WEBHOOK_URL)
    if score.total >= 100 and config.DISCORD_PERFECT_SCORE_WEBHOOK_URL:
        _send(content, config.DISCORD_PERFECT_SCORE_WEBHOOK_URL)


def _build_message(token: TrackedToken, score_total: int, tier: str) -> str:
    emoji = _TIER_EMOJI.get(tier, tier)
    score_line = f"{emoji} {tier} Score: {score_total}/100"
    stars = _stars_display(token.unique_buyers_m5)
    if stars:
        score_line += f" {stars}"
    lines = [score_line]

    name = token.name.strip()
    symbol = token.symbol.strip()
    if name or symbol:
        label = f"{name} (${symbol})" if name and symbol else (name or f"${symbol}")
        lines.append(label)

    lines.append(f"`{token.mint}`")
    lines.append(_phantom_link(token.mint))
    return "\n".join(lines)


def notify_star_upgrade(
    token: TrackedToken,
    score: ScoreResult,
    tier: str,
    elapsed_seconds: int,
) -> None:
    """既に通知済みのトークンが、後のチェックポイントで初めてユニーク買い手
    ★1つ以上が確認できた瞬間に呼び出す(main.py、1トークンにつき最大1回)。

    最初の通知時点(卒業直後)はDexScreenerの直近5分ウィンドウがまだ
    始まったばかりで、★0のまま通知されることが多い。その後実際に人が
    買い始めたことが確認できた瞬間を、通常のDISCORD_WEBHOOK_URLとは別の
    DISCORD_FOLLOWUP_WEBHOOK_URLへ知らせる(未設定なら送らない)。
    """
    if not config.DISCORD_FOLLOWUP_WEBHOOK_URL:
        return
    stars = _stars_display(token.unique_buyers_m5)
    content = f"🔥 ユニーク買い手{stars}を確認\n" + _build_message(token, score.total, tier)
    _send(content, config.DISCORD_FOLLOWUP_WEBHOOK_URL)
