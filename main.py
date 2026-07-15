"""ARTEMIS Phantom Sniper のエントリーポイント。

PumpPortalのWebSocketで、pump.fun上のトークンがボンディングカーブから
実際のDEX(Raydium等)へ卒業(migration)した瞬間をリアルタイムに検知する
(subscribeNewToken/subscribeMigrationはどちらも無料。詳細はpumpportal_
client.pyのdocstring参照)。卒業を検知したら、config.MIGRATION_
CHECKPOINTS_SECONDS(既定0/60/300/900秒)の各時点でDexScreenerの公開API
(無料)から実際のDEX取引状況を取得して繰り返しスコアを再計算し、スコアが
通知ライン(WATCH以上)を初めて超えた瞬間、またはより高いティアへ上昇した
瞬間だけDiscordへ通知する。

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
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config
import dexscreener_client
import discord_notifier
import rugcheck_client
import scoring
from logger import setup_logger
from outcome_tracker import OutcomeTracker
from pumpportal_client import PumpPortalClient
from token_watcher import TokenWatcher, TrackedToken

logger = logging.getLogger("phantom_sniper")

_POLL_INTERVAL_SECONDS = 5
_STATS_LOG_INTERVAL_SECONDS = 1800
_MAX_CACHED_NAMES = 5000


class _RecentTokenNames:
    """"create"イベントで見たmint→(name, symbol)を一定件数だけ覚えておくFIFOキャッシュ。

    subscribeMigrationのイベントには銘柄名/シンボルが含まれないため、
    直前に見たcreateイベントの内容で補う(_consume_loop参照)。無制限に
    貯め続けるとメモリを圧迫するため、古いものから間引く。
    """

    def __init__(self, max_size: int = _MAX_CACHED_NAMES) -> None:
        self._max_size = max_size
        self._cache: OrderedDict[str, tuple[str, str]] = OrderedDict()

    def remember(self, mint: str, name: str, symbol: str) -> None:
        if not name and not symbol:
            return
        self._cache[mint] = (name, symbol)
        self._cache.move_to_end(mint)
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def get(self, mint: str) -> tuple[str, str]:
        return self._cache.get(mint, ("", ""))


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
            f"stats: date={self.date} 卒業検知={self.watched} HIGH={self.high} "
            f"WATCH={self.watch} LOW={self.low} 圏外={self.none_count} "
            f"平均Score={self.average_score():.1f}"
        )


def _utc_date_str(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


async def _consume_loop(client: PumpPortalClient, watcher: TokenWatcher, recent_names: _RecentTokenNames) -> None:
    """PumpPortalからのメッセージを受け取り、卒業(migration)イベントを検知する。

    subscribeTokenTradeは使っていないため、このWebSocket上で流れてくる
    メッセージはsubscribeNewToken由来("create")かsubscribeMigration由来の
    どちらか。PumpPortal側の正確なフィールド名は未確認のため、"create"
    以外は全てmigrationとみなし、生のpayloadをDEBUGログへ残す(想定と
    フィールド名が違った場合、ここを見て調整する)。

    migrationイベント自体には銘柄名/シンボルが含まれないため、直前の
    createイベントで見た内容をrecent_namesから補う。
    """
    async for message in client.messages():
        mint = message.get("mint")
        if not mint:
            continue
        mint = str(mint)

        tx_type = message.get("txType")
        name = str(message.get("name", ""))
        symbol = str(message.get("symbol", ""))

        if tx_type == "create":
            recent_names.remember(mint, name, symbol)
            logger.debug(
                "main: 新規トークン作成を検知(まだDEX卒業前) mint=%s name=%s symbol=%s",
                mint,
                name,
                symbol,
            )
            continue

        if not name and not symbol:
            name, symbol = recent_names.get(mint)

        logger.debug("main: migration想定イベントを受信 raw=%s", message)
        token = watcher.start_tracking(mint=mint, name=name, symbol=symbol, now=time.time())
        logger.info(
            "main: DEX卒業を検知しました mint=%s name=%s symbol=%s",
            token.mint,
            token.name,
            token.symbol,
        )


def _log_score(token: TrackedToken, score: scoring.ScoreResult, elapsed: int, tier: str | None) -> None:
    reasons = "; ".join(c.detail for c in score.components if c.points <= 0)
    logger.debug(
        "main: checkpoint mint=%s symbol=%s elapsed=%d秒 has_pair_data=%s score=%d tier=%s 未加点理由=[%s]",
        token.mint,
        token.symbol,
        elapsed,
        token.has_pair_data,
        score.total,
        tier or "圏外",
        reasons or "なし",
    )


async def _checkpoint_loop(watcher: TokenWatcher, outcomes: OutcomeTracker, stats: DailyStats) -> None:
    """定期的にチェックポイントを迎えたトークンをDexScreenerで再取得・再評価する。"""
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        now = time.time()
        stats.maybe_reset(now)

        for token in watcher.due_for_checkpoint(now):
            elapsed = watcher.current_checkpoint_seconds(token)

            pair = await asyncio.to_thread(dexscreener_client.fetch_best_pair, token.mint)
            if pair is not None:
                watcher.apply_snapshot(token, pair)

            if not token.rugcheck_checked:
                # トークン1件につき1回だけ取得する(レート制限がDexScreener
                # より厳しいため)。取得に失敗した場合はrugcheck_checkedを
                # Trueにしないため、次のチェックポイントで再試行される。
                report = await asyncio.to_thread(rugcheck_client.fetch_risk_report, token.mint)
                if report is not None:
                    danger_reason = rugcheck_client.extract_danger_reason(report)
                    watcher.apply_rugcheck_report(token, danger_reason)
                    if danger_reason:
                        logger.info(
                            "main: RugCheckで危険フラグを検出しました mint=%s symbol=%s reason=%s",
                            token.mint,
                            token.symbol,
                            danger_reason,
                        )

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
                    await asyncio.to_thread(discord_notifier.notify_score_update, token, score, tier, elapsed)
                    outcomes.register(
                        mint=token.mint,
                        name=token.name,
                        symbol=token.symbol,
                        tier=tier,
                        score=score.total,
                        market_cap_usd=token.market_cap_usd,
                        now=now,
                    )

            watcher.mark_checkpoint_done(token)

            if token.finished:
                stats.record_final(token.notified_tier, score.total)
                watcher.forget(token.mint)


async def _outcome_loop(outcomes: OutcomeTracker) -> None:
    """通知済みトークンの結果(30分/1時間/24時間後の時価総額変化)をDexScreenerから取得・記録する。"""
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        now = time.time()
        for outcome in outcomes.due_for_checkpoint(now):
            pair = await asyncio.to_thread(dexscreener_client.fetch_best_pair, outcome.mint)
            if pair is not None:
                market_cap = float(pair.get("marketCap") or pair.get("fdv") or 0.0)
                outcomes.update_market_cap(outcome.mint, market_cap)

            outcomes.record_and_advance(outcome)
            if outcome.finished:
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
    recent_names = _RecentTokenNames()
    logger.info(
        "main: 監視を開始します checkpoints=%s秒(DEX卒業からの経過) high>=%s watch>=%s low>=%s discord_enabled=%s",
        config.MIGRATION_CHECKPOINTS_SECONDS,
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
        _consume_loop(client, watcher, recent_names),
        _checkpoint_loop(watcher, outcomes, stats),
        _outcome_loop(outcomes),
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
