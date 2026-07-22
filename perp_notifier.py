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


def _send(content: str, webhook_url: str = "") -> None:
    url = webhook_url or config.DISCORD_PERP_WEBHOOK_URL
    if not config.DISCORD_ENABLED or not url:
        return
    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
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


def notify_grid_summary(
    symbol: str, open_count: int, closed_count: int, win_rate: float, total_pnl_pct: float
) -> None:
    """グリッドトレードは取引回数が非常に多くなりやすく、1件ごとに通知すると
    スパムになるため、1件ずつではなく定期的な集計を送る
    (PERP_GRID_SUMMARY_INTERVAL_SECONDS間隔、perp_sniper.py参照)。
    """
    emoji = "🔵" if total_pnl_pct >= 0 else "🟠"
    content = (
        f"{emoji} [グリッド・ペーパートレード集計] {symbol}\n"
        f"保有中: {open_count}件 / 決済済み: {closed_count}件 / 勝率: {win_rate:.1f}%\n"
        f"累計損益(単純合計、複利無し): {total_pnl_pct:+.1f}%\n"
        f"(実資金は動いていません。モックです)"
    )
    _send(content)


# --- グリッドトレードの実発注(grid_live_trader.py)の通知 ---
# ⚠️ ここから下は実際に資金を動かす。1件ごとの取引でもDISCORD_TRADE_
# WEBHOOK_URL(trade_executor.pyの現物自動売買と同じ、実資金が動いた
# ことを一元的に確認できるチャンネル)へ通知する(ペーパートレードのように
# 集計にまとめない。実際のお金の動きは都度確認できる方が安全なため)。


def notify_grid_live_opened(symbol: str, level_index: int, entry_price: float, size: float) -> None:
    content = (
        f"🟢 [グリッド実発注] {symbol} レベル{level_index}を買いました\n"
        f"約定価格: ${entry_price:,.4f} / 数量: {size}"
    )
    _send(content, config.DISCORD_TRADE_WEBHOOK_URL)


def notify_grid_live_closed(
    symbol: str, level_index: int, reason: str, pnl_pct: float, entry_price: float, exit_price: float
) -> None:
    reason_label = {"take_profit": "利確", "stop_loss": "損切り"}.get(reason, reason)
    emoji = "🔵" if pnl_pct >= 0 else "🟠"
    content = (
        f"{emoji} [グリッド実発注] {symbol} レベル{level_index}を決済({reason_label})\n"
        f"損益(レバレッジ・手数料込み): {pnl_pct:+.2f}% (${entry_price:,.4f} → ${exit_price:,.4f})"
    )
    _send(content, config.DISCORD_TRADE_WEBHOOK_URL)


def notify_grid_live_failure(symbol: str, level_index: int, action: str, error: str) -> None:
    content = f"⚠️ [グリッド実発注] {symbol} レベル{level_index}の{action}に失敗しました\n理由: {error}"
    _send(content, config.DISCORD_TRADE_WEBHOOK_URL)
