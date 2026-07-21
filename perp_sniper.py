"""ARTEMIS Phantom Sniper のパーペチュアル(先物)シグナル機能のエントリーポイント。

⚠️ 実験的機能。ミームコインのスキャナー本体(main.py)とは完全に独立した
別プロセス(動いていなくても本体には一切影響しない)。config.PERP_SYMBOLS
で指定した銘柄(既定BTCUSDT/ETHUSDT/SOLUSDT)について、一定間隔
(config.PERP_POLL_INTERVAL_SECONDS)でBinance Futuresの公開市場データから
EMAトレンド・RSI・モメンタム・ファンディングレートを組み合わせた簡易的な
ロング/ショートシグナルを計算し、閾値を超えたらDiscordへ通知する
(perp_signals.compute_signal参照)。

実際の取引所への発注は一切行わない。PERP_PAPER_TRADING_ENABLED=true
(既定)の場合、「もし建てていたら」のペーパートレード(モック、実資金は
動かない)としてレバレッジ込みの損益をシミュレートし、その結果も
Discordへ通知する。本物の自動売買に拡張する場合の設計はREADME.mdの
「パーペチュアル(実験的機能)」参照。

PERP_ENABLED=falseの場合は何もせず終了する(既定OFF)。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

import config
import perp_market_data
import perp_notifier
from logger import setup_logger
from perp_paper_trader import PaperPerpTracker, decide_exit_reason
from perp_signals import compute_signal

logger = logging.getLogger("phantom_sniper")


def _process_symbol(symbol: str, positions: PaperPerpTracker, now: float) -> None:
    closes = perp_market_data.fetch_closes(symbol, config.PERP_KLINE_INTERVAL, config.PERP_KLINE_LIMIT)
    if closes is None:
        logger.warning("perp_sniper: %sの価格データ取得に失敗しました", symbol)
        return
    funding_rate = perp_market_data.fetch_latest_funding_rate(symbol)

    signal = compute_signal(symbol, closes, funding_rate)
    if signal is None:
        return

    logger.info(
        "perp_sniper: symbol=%s direction=%s score=%d price=%s", symbol, signal.direction, signal.score, signal.price
    )

    if signal.direction == "NEUTRAL":
        return

    perp_notifier.notify_signal(signal)

    if not config.PERP_PAPER_TRADING_ENABLED:
        return
    if positions.has_open_position(symbol):
        return  # 既にこの銘柄でペーパー建玉を持っている間は増し玉しない
    position = positions.open_position(symbol, signal.direction, signal.price, config.PERP_PAPER_LEVERAGE, now)
    perp_notifier.notify_paper_trade_opened(position)


def _monitor_paper_positions(positions: PaperPerpTracker, now: float) -> None:
    for position in positions.open_positions():
        current_price = perp_market_data.fetch_mark_price(position.symbol)
        if current_price is None:
            continue
        reason = decide_exit_reason(
            direction=position.direction,
            entry_price=position.entry_price,
            current_price=current_price,
            leverage=position.leverage,
            opened_at=position.opened_at,
            now=now,
            take_profit_pct=config.PERP_PAPER_TAKE_PROFIT_PCT,
            stop_loss_pct=config.PERP_PAPER_STOP_LOSS_PCT,
            max_hold_seconds=config.PERP_PAPER_MAX_HOLD_SECONDS,
        )
        if reason is not None:
            positions.close_position(position, current_price, reason, now)
            perp_notifier.notify_paper_trade_closed(position)


async def _signal_loop(positions: PaperPerpTracker) -> None:
    while True:
        now = time.time()
        for symbol in config.PERP_SYMBOLS:
            await asyncio.to_thread(_process_symbol, symbol, positions, now)
        if config.PERP_PAPER_TRADING_ENABLED:
            await asyncio.to_thread(_monitor_paper_positions, positions, now)
        await asyncio.sleep(config.PERP_POLL_INTERVAL_SECONDS)


async def async_main() -> None:
    if not config.PERP_ENABLED:
        logger.warning("perp_sniper: PERP_ENABLED=falseのため起動しません")
        return
    positions = PaperPerpTracker()
    logger.info(
        "perp_sniper: 監視を開始します symbols=%s interval=%s秒 paper_trading=%s",
        config.PERP_SYMBOLS,
        config.PERP_POLL_INTERVAL_SECONDS,
        config.PERP_PAPER_TRADING_ENABLED,
    )
    await _signal_loop(positions)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ARTEMIS Phantom Sniper - Perpetual Signals (実験的機能)")
    parser.add_argument("--debug", action="store_true", help="DEBUGレベルの詳細ログを出力する")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logger(debug=args.debug)
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("perp_sniper: ユーザーにより停止されました")


if __name__ == "__main__":
    main()
