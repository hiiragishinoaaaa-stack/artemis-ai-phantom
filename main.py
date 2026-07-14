"""ARTEMIS Phantom Sniper のエントリーポイント。

PumpPortalのWebSocketでpump.fun上の新規トークン作成をリアルタイムに検知し、
config.EVALUATION_CHECKPOINTS_SECONDS(既定20/40/60/90/120秒)の各時点で
scoring.py(100点満点スコア)により繰り返し再評価する。スコアが通知ライン
(WATCH以上)を初めて超えた瞬間、またはより高いティアへ上昇した瞬間だけ
Discordへ通知する(120秒までは通知後も監視を継続する)。

通知したトークンはoutcome_tracker.pyにより30分/1時間/24時間後の時価総額
変化も記録する(将来、どのスコア項目が実際に有効だったか分析するため)。

自動売買・ウォレット操作は一切行わない。あくまで人間が判断するための
情報提供ツール(詳細はREADME.md参照)。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config
import discord_notifier
import scoring
from logger import setup_logger
from outcome_tracker import OutcomeTracker
from pumpportal_client import PumpPortalClient
from token_watcher import TokenWatcher, TrackedToken

logger = logging.getLogger("phantom_sniper")

_POLL_INTERVAL_SECONDS = 5
_STATS_LOG_INTERVAL_SECONDS = 1800


@dataclass
class DailyStats:
    """当日(UTC日付)分の監視統計。定期的にログへ出力する。"""

    date: str = field(default_factory=lambda: _utc_date_str(time.time()))
    watched: int = 0
    high: int = 0
    watch: int = 0
    low: int = 0
    none_count: int = 0
    score_sum: int = 0

    def maybe_reset(self, now: float) -> None:
        today = _utc_date_str(now)
        if today != self.date:
            self.date = today
            self.watched = 0
            self.high = 0
            self.watch = 0
            self.low = 0
            self.none_count = 0
            self.score_sum = 0

    def record_final(self, tier: str | None, score: int) -> None:
        self.watched += 1
        self.score_sum += score
        if tier == "HIGH":
            self.high += 1
        elif tier == "WATCH":
            self.watch += 1
        elif tier == "LOW":
            self.low += 1
        else:
            self.none_count += 1

    def average_score(self) -> float:
        return self.score_sum / self.watched if self.watched else 0.0

    def summary_line(self) -> str:
        return (
            f"stats: date={self.date} 監視={self.watched} HIGH={self.high} "
            f"WATCH={self.watch} LOW={self.low} 圏外={self.none_count} "
            f"平均Score={self.average_score():.1f}"
        )


def _utc_date_str(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


def _safe_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


async def _consume_loop(client: PumpPortalClient, watcher: TokenWatcher, outcomes: OutcomeTracker) -> None:
    """PumpPortalからのメッセージを受け取り、TokenWatcher/OutcomeTrackerの状態を更新し続ける。"""
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
            mint_str = str(mint)
            market_cap_sol = _safe_float(message.get("marketCapSol"))
            watcher.on_trade(
                mint=mint_str,
                tx_type=tx_type,
                trader=str(message.get("traderPublicKey", "")),
                market_cap_sol=market_cap_sol,
                sol_amount=_safe_float(message.get("solAmount")),
            )
            outcomes.update_market_cap(mint_str, market_cap_sol)


def _log_score(token: TrackedToken, score: scoring.ScoreResult, elapsed: int, tier: str | None) -> None:
    reasons = "; ".join(c.detail for c in score.components if c.points == 0)
    logger.debug(
        "main: checkpoint mint=%s symbol=%s elapsed=%d秒 score=%d tier=%s 未加点理由=[%s]",
        token.mint,
        token.symbol,
        elapsed,
        score.total,
        tier or "圏外",
        reasons or "なし",
    )


async def _evaluation_loop(
    client: PumpPortalClient, watcher: TokenWatcher, outcomes: OutcomeTracker, stats: DailyStats
) -> None:
    """定期的にチェックポイントを迎えたトークンを再評価し、通知・統計を更新する。"""
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        now = time.time()
        stats.maybe_reset(now)

        for token in watcher.due_for_checkpoint(now):
            elapsed = watcher.current_checkpoint_seconds(token)
            score = scoring.compute_score(token)
            tier = scoring.tier_for_score(score.total)
            _log_score(token, score, elapsed, tier)

            if tier is not None and scoring.is_upgrade(token.notified_tier, tier):
                token.notified_tier = tier
                if tier in ("HIGH", "WATCH"):
                    logger.info(
                        "main: 通知ラインを超えました mint=%s symbol=%s score=%d tier=%s elapsed=%d秒",
                        token.mint,
                        token.symbol,
                        score.total,
                        tier,
                        elapsed,
                    )
                    discord_notifier.notify_score_update(token, score, tier, elapsed)
                    outcomes.register(
                        mint=token.mint,
                        name=token.name,
                        symbol=token.symbol,
                        tier=tier,
                        score=score.total,
                        market_cap_sol=token.last_market_cap_sol,
                        now=now,
                    )

            watcher.mark_checkpoint_done(token)

            if token.finished:
                stats.record_final(token.notified_tier, score.total)
                if not outcomes.is_tracking(token.mint):
                    await client.unsubscribe_token_trade([token.mint])
                watcher.forget(token.mint)


async def _outcome_loop(client: PumpPortalClient, outcomes: OutcomeTracker) -> None:
    """通知済みトークンの結果(30分/1時間/24時間後の時価総額変化)を記録し続ける。"""
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        now = time.time()
        for outcome in outcomes.due_for_checkpoint(now):
            outcomes.record_and_advance(outcome)
            if outcome.finished:
                await client.unsubscribe_token_trade([outcome.mint])
                outcomes.forget(outcome.mint)


async def _stats_loop(stats: DailyStats) -> None:
    """定期的に当日の統計をログへ出力する。"""
    while True:
        await asyncio.sleep(_STATS_LOG_INTERVAL_SECONDS)
        stats.maybe_reset(time.time())
        logger.info("main: %s", stats.summary_line())


async def async_main() -> None:
    client = PumpPortalClient()
    watcher = TokenWatcher()
    outcomes = OutcomeTracker()
    stats = DailyStats()
    logger.info(
        "main: 監視を開始します checkpoints=%s秒 high>=%s watch>=%s low>=%s discord_enabled=%s",
        config.EVALUATION_CHECKPOINTS_SECONDS,
        config.HIGH_SCORE_THRESHOLD,
        config.WATCH_SCORE_THRESHOLD,
        config.LOW_SCORE_THRESHOLD,
        config.DISCORD_ENABLED,
    )
    if not config.DISCORD_ENABLED or not config.DISCORD_WEBHOOK_URL:
        logger.warning(
            "main: DISCORD_ENABLED=falseまたはDISCORD_WEBHOOK_URL未設定のため、"
            "スコアが通知ラインを超えても実際には通知されません(ログにのみ記録されます)"
        )

    await asyncio.gather(
        _consume_loop(client, watcher, outcomes),
        _evaluation_loop(client, watcher, outcomes, stats),
        _outcome_loop(client, outcomes),
        _stats_loop(stats),
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
