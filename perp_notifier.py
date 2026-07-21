"""パーペチュアルのロング/ショートシグナル・ペーパートレード結果のDiscord通知。

discord_notifier.pyと同じ方式(urllib.requestのみ、外部ライブラリ不要)。
DISCORD_PERP_WEBHOOK_URL未設定なら何も送らない。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import config
from perp_paper_trader import PaperPosition
from perp_signals import PerpSignal

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 5
_USER_AGENT = "Mozilla/5.0 (compatible; ARTEMIS-Phantom-Sniper/1.0)"

_DIRECTION_EMOJI = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}


def _send(content: str) -> None:
    if not config.DISCORD_ENABLED or not config.DISCORD_PERP_WEBHOOK_URL:
        return
    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        config.DISCORD_PERP_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("perp_notifier: Discordへの通知送信に失敗しました: %s", exc)


def notify_signal(signal: PerpSignal) -> None:
    """LONG/SHORTシグナルが出た時に呼び出す(NEUTRALは呼び出し側で除外する想定)。"""
    emoji = _DIRECTION_EMOJI.get(signal.direction, "⚪")
    reasons = "\n".join(f"・{r}" for r in signal.reasons)
    content = (
        f"{emoji} {signal.symbol} {signal.direction}シグナル(強度{signal.score:+d})\n"
        f"価格: ${signal.price:,.4f}\n{reasons}"
    )
    _send(content)


def notify_paper_trade_opened(position: PaperPosition) -> None:
    content = (
        f"📝 [ペーパートレード] {position.symbol} {position.direction}を建てました\n"
        f"エントリー価格: ${position.entry_price:,.4f} / レバレッジ: {position.leverage}倍\n"
        f"(実資金は動いていません。モックです)"
    )
    _send(content)


def notify_paper_trade_closed(position: PaperPosition) -> None:
    reason_label = {"take_profit": "利確", "stop_loss": "損切り", "max_hold": "最大保有時間超過"}.get(
        position.close_reason, position.close_reason
    )
    emoji = "🔵" if position.pnl_pct >= 0 else "🟠"
    content = (
        f"{emoji} [ペーパートレード] {position.symbol} {position.direction}を決済({reason_label})\n"
        f"損益(レバレッジ込み): {position.pnl_pct:+.1f}% "
        f"(${position.entry_price:,.4f} → ${position.exit_price:,.4f})\n"
        f"(実資金は動いていません。モックです)"
    )
    _send(content)
