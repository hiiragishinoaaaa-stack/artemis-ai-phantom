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

# Discordのメッセージコンポーネント(ボタン)の定数。Link(URL)スタイルの
# ボタンはBot側のインタラクション応答が不要なため、Webhookからの送信だけで
# 完結する(_build_components参照)。
_COMPONENT_TYPE_ACTION_ROW = 1
_COMPONENT_TYPE_BUTTON = 2
_BUTTON_STYLE_LINK = 5


def _send(content: str, webhook_url: str, components: list[dict] | None = None) -> None:
    if not config.DISCORD_ENABLED or not webhook_url:
        return

    payload: dict = {"content": content}
    request_url = webhook_url
    if components:
        payload["components"] = components
        # サーバー設定から作る通常のWebhook(application-owned webhookでないもの)は、
        # with_components=trueクエリパラメータを付けないとcomponentsを黙って無視する
        # (エラーにはならずボタンだけ付かずに届く、という気付きにくい仕様)。
        separator = "&" if "?" in webhook_url else "?"
        request_url = f"{webhook_url}{separator}with_components=true"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        request_url,
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


def _holder_concentration_badge(top10_holders_pct: float | None) -> str:
    """上位10保有者の集中度を⚠️/✅の絵文字1つで表す(scoring._score_holder_concentration
    と同じ閾値。判定不能または中間(どちらでもない)の場合は空文字を返す)。
    """
    if top10_holders_pct is None:
        return ""
    if top10_holders_pct >= config.HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT:
        return config.DISCORD_HOLDER_CONCENTRATION_WARN_EMOJI
    if top10_holders_pct < config.HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT:
        return config.DISCORD_HOLDER_CONCENTRATION_HEALTHY_EMOJI
    return ""


def _social_badges(token: TrackedToken) -> str:
    """検出できたソーシャルリンク(X/Twitter・Telegram)を絵文字で表す。"""
    badges = []
    if token.has_twitter:
        badges.append(config.DISCORD_TWITTER_EMOJI)
    if token.has_telegram:
        badges.append(config.DISCORD_TELEGRAM_EMOJI)
    return "".join(badges)


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

    本文はスコア・銘柄名のみの最小限にしている(2026-07、ユーザー希望に
    より出来高・mintアドレス等の詳細は削除。mintアドレスは「詳細」ボタンの
    遷移先ページに表示される。詳細はDEBUGログ側にも残る)。スコア行は
    「★(ユニーク買い手) スコア/100 ⚠️/✅(上位10保有者集中度)」の順、
    名前行の末尾にはX/Telegramの検出バッジも付く(_holder_concentration_
    badge/_social_badges参照、絵文字は.envでカスタム絵文字に差し替え
    可能)。「詳細」(ダッシュボードの/token/{mint}、DASHBOARD_PUBLIC_URL
    未設定なら付かない)・「Phantomで開く」のリンクボタンをメッセージに
    添付する(_build_components参照)。

    スコアが100点満点の場合、通常のDISCORD_WEBHOOK_URLに加えて
    DISCORD_PERFECT_SCORE_WEBHOOK_URL(満点専用チャンネル)にも同じ内容を
    送る(未設定なら送らない。★の数は問わない)。

    スコア行の先頭に、直近5分のユニーク買い手数を★0〜3個で表示する
    (少数のウォレットの自作自演ではなく、実際に多くの人が買っている
    ことをスコアの内訳を見なくても一目でわかるようにするため)。この
    時点で★0でも、後のチェックポイントで★1つ以上に育ったら
    `notify_star_upgrade()`が別途追い通知する(main.py参照)。
    """
    content = _build_message(token, score.total)
    components = _build_components(token)

    _send(content, config.DISCORD_WEBHOOK_URL, components=components)
    if score.total >= 100 and config.DISCORD_PERFECT_SCORE_WEBHOOK_URL:
        _send(content, config.DISCORD_PERFECT_SCORE_WEBHOOK_URL, components=components)


def _build_message(token: TrackedToken, score_total: int) -> str:
    score_line_parts = []
    stars = _stars_display(token.unique_buyers_m5)
    if stars:
        score_line_parts.append(stars)
    score_line_parts.append(f"{score_total}/100")
    holder_badge = _holder_concentration_badge(token.top10_holders_pct)
    if holder_badge:
        score_line_parts.append(holder_badge)
    lines = [" ".join(score_line_parts)]

    name = token.name.strip()
    symbol = token.symbol.strip()
    social_badges = _social_badges(token)
    if name or symbol:
        label = f"{name} (${symbol})" if name and symbol else (name or f"${symbol}")
        if social_badges:
            label += f" {social_badges}"
        lines.append(label)
    elif social_badges:
        lines.append(social_badges)

    return "\n".join(lines)


def _detail_url(token: TrackedToken) -> str:
    """「詳細」ボタンの遷移先を決める。

    DexScreenerの当該ペアページ(出来高・チャート・保有者リンク等が
    見れる、外部サービスなのでこちら側のダッシュボード/Supabaseが
    落ちていても常に見れる)を優先する。取得できていない場合のみ、
    設定されていればダッシュボード(DASHBOARD_PUBLIC_URL、任意)の
    /token/{mint}にフォールバックする。どちらも無ければ空文字を返す
    (呼び出し元でボタン自体を付けない)。
    """
    if token.dexscreener_url:
        return token.dexscreener_url
    if config.DASHBOARD_PUBLIC_URL:
        return f"{config.DASHBOARD_PUBLIC_URL.rstrip('/')}/token/{token.mint}"
    return ""


def _build_components(token: TrackedToken) -> list[dict]:
    """通知メッセージに添えるボタン行を組み立てる(Link(URL)スタイルのみ。
    Bot側のインタラクション応答が不要なため、Webhookからの送信だけで完結する)。

    「詳細」ボタン(_detail_url参照)を先頭に、「Phantomで開く」ボタンは
    常に付ける。
    """
    buttons = []
    detail_url = _detail_url(token)
    if detail_url:
        buttons.append(
            {
                "type": _COMPONENT_TYPE_BUTTON,
                "style": _BUTTON_STYLE_LINK,
                "label": "詳細",
                "url": detail_url,
            }
        )
    buttons.append(
        {
            "type": _COMPONENT_TYPE_BUTTON,
            "style": _BUTTON_STYLE_LINK,
            "label": "Phantomで開く",
            "url": _phantom_link(token.mint),
        }
    )
    return [{"type": _COMPONENT_TYPE_ACTION_ROW, "components": buttons}]


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
    content = f"🔥 ユニーク買い手{stars}を確認\n" + _build_message(token, score.total)
    _send(content, config.DISCORD_FOLLOWUP_WEBHOOK_URL, components=_build_components(token))
