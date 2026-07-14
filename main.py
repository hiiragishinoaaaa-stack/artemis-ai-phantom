"""ARTEMIS Phantom Sniper のエントリーポイント。

PumpPortalのWebSocketでpump.fun上の新規トークン作成をリアルタイムに検知し、
OBSERVATION_WINDOW_SECONDS秒だけ初動(買い件数・ユニーク買い手・売買比率)を
観察した上で、条件を満たしたものだけDiscordへ通知する。

自動売買・ウォレット操作は一切行わない。あくまで人間が判断するための
情報提供ツール(詳細はREADME.md参照)。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

import config
import discord_notifier
from logger import setup_logger
from pumpportal_client import PumpPortalClient
from token_watcher import TokenWatcher

logger = logging.getLogger("phantom_sniper")

_EVALUATION_POLL_INTERVAL_SECONDS = 5


async def _consume_loop(client: PumpPortalClient, watcher: TokenWatcher) -> None:
    """PumpPortalからのメッセージを受け取り、TokenWatcherの状態を更新し続ける。"""
    async for message in client.messages():
        tx_type = message.get("txType")
        mint = message.get("mint")
        if not mint or not tx_type:
            continue

        if tx_type == "create":
            token = watcher.on_token_created(
                mint=str(mint),
                name=str(message.get("name", "")),
                symbol=str(message.get("symbol", "")),
                creator=str(message.get("traderPublicKey", "")),
                market_cap_sol=_safe_float(message.get("marketCapSol")),
                now=time.time(),
            )
            logger.info(
                "main: 新規トークンを検知しました mint=%s name=%s symbol=%s",
                token.mint,
                token.name,
                token.symbol,
            )
            await client.subscribe_token_trade([token.mint])
        elif tx_type in ("buy", "sell"):
            watcher.on_trade(
                mint=str(mint),
                tx_type=tx_type,
                trader=str(message.get("traderPublicKey", "")),
                market_cap_sol=_safe_float(message.get("marketCapSol")),
            )


async def _evaluation_loop(client: PumpPortalClient, watcher: TokenWatcher) -> None:
    """定期的に観察期間が終わったトークンを判定し、通過したものをDiscordへ通知する。"""
    while True:
        await asyncio.sleep(_EVALUATION_POLL_INTERVAL_SECONDS)
        now = time.time()
        for token in watcher.due_for_evaluation(now):
            passed = watcher.evaluate(token)
            if passed:
                logger.info(
                    "main: 通知条件を満たしました mint=%s symbol=%s buy=%d unique_buyers=%d sell=%d",
                    token.mint,
                    token.symbol,
                    token.buy_count,
                    len(token.unique_buyers),
                    token.sell_count,
                )
                discord_notifier.notify_token_passed_filter(token)
            else:
                logger.debug(
                    "main: 条件未達のため見送り mint=%s symbol=%s buy=%d unique_buyers=%d sell=%d",
                    token.mint,
                    token.symbol,
                    token.buy_count,
                    len(token.unique_buyers),
                    token.sell_count,
                )
            await client.unsubscribe_token_trade([token.mint])
            watcher.forget(token.mint)


def _safe_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


async def async_main() -> None:
    client = PumpPortalClient()
    watcher = TokenWatcher()
    logger.info(
        "main: 監視を開始します observation_window=%s秒 min_buy=%s min_unique_buyers=%s "
        "max_sell_to_buy_ratio=%s discord_enabled=%s",
        config.OBSERVATION_WINDOW_SECONDS,
        config.MIN_BUY_COUNT,
        config.MIN_UNIQUE_BUYERS,
        config.MAX_SELL_TO_BUY_RATIO,
        config.DISCORD_ENABLED,
    )
    if not config.DISCORD_ENABLED or not config.DISCORD_WEBHOOK_URL:
        logger.warning(
            "main: DISCORD_ENABLED=falseまたはDISCORD_WEBHOOK_URL未設定のため、"
            "条件を満たしても実際には通知されません(ログにのみ記録されます)"
        )

    await asyncio.gather(
        _consume_loop(client, watcher),
        _evaluation_loop(client, watcher),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ARTEMIS Phantom Sniper")
    parser.add_argument("--debug", action="store_true", help="DEBUGレベルの詳細ログを出力する")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logger(debug=args.debug)
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("main: ユーザーにより停止されました")


if __name__ == "__main__":
    main()
