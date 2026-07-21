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

初回通知の時点(卒業直後)はDexScreenerの直近5分ウィンドウがまだ始まった
ばかりで、★0のまま通知されることが多い。後のチェックポイントで実際に
人が買い始めて★1つ以上が確認できたら、通常通知とは別のDISCORD_
FOLLOWUP_WEBHOOK_URLへ1トークンにつき最大1回だけ追い通知する
(_decide_notification_action参照)。

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
import solana_client
import supabase_client
from creator_blocklist import CreatorBlocklist
from logger import setup_logger
import trade_executor
from outcome_tracker import OutcomeTracker, TrackedOutcome
from position_tracker import PositionTracker
from pumpportal_client import PumpPortalClient
from token_name_history import TokenNameHistory
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


async def _consume_loop(
    client: PumpPortalClient,
    watcher: TokenWatcher,
    recent_names: _RecentTokenNames,
    name_history: TokenNameHistory,
) -> None:
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
        is_new = watcher.get(mint) is None
        token = watcher.start_tracking(mint=mint, name=name, symbol=symbol, now=time.time())
        logger.info(
            "main: DEX卒業を検知しました mint=%s name=%s symbol=%s",
            token.mint,
            token.name,
            token.symbol,
        )

        if is_new:
            duplicate_reason = name_history.check_and_record(mint, name, symbol)
            if duplicate_reason:
                watcher.apply_duplicate_name(token, duplicate_reason)
                logger.info(
                    "main: 名前/ティッカーの重複を検出しました mint=%s reason=%s", mint, duplicate_reason
                )


def _decide_notification_action(
    is_tier_upgrade: bool,
    tier: str | None,
    discord_notified: bool,
    stars_followup_sent: bool,
    star_count: int,
) -> str | None:
    """このチェックポイントで取るべきDiscord通知アクションを返す。

    ネットワーク・時刻取得に一切依存しない純粋関数にして、_checkpoint_loop
    本体を実際に動かさずに分岐の妥当性を単体テストできるようにしている
    (tests/test_main.py参照。token_watcher.py等と同じ設計方針)。

    戻り値:
    - "primary": 初めてHIGH/WATCHへ到達した瞬間の通常通知
      (notify_score_update)を送る。
    - "followup": 既に通知済みのトークンが後のチェックポイントで初めて
      ユニーク買い手★1つ以上を確認できた瞬間の追い通知
      (notify_star_upgrade)を1トークンにつき最大1回だけ送る。
      discord_notified(=実際にHIGH/WATCH通知を送ったことがある)を
      条件にしているため、LOW止まりで一度もDiscordへ送っていない
      トークンには発火しない。
    - None: 何もしない。
    """
    if is_tier_upgrade:
        return "primary" if tier in ("HIGH", "WATCH") else None
    if discord_notified and not stars_followup_sent and star_count >= 1:
        return "followup"
    return None


def _build_notification_row(
    token: TrackedToken, score: scoring.ScoreResult, tier: str, elapsed_seconds: int, notification_type: str
) -> dict:
    """supabase_client.insert_notification()へ渡す行を組み立てる(supabase_schema.sql
    のnotificationsテーブル参照)。ネットワーク非依存の純粋関数(tests/test_main.py参照)。
    """
    return {
        "mint": token.mint,
        "name": token.name,
        "symbol": token.symbol,
        "notification_type": notification_type,
        "tier": tier,
        "score": score.total,
        "unique_buyers_m5": token.unique_buyers_m5,
        "star_count": scoring.star_count_for_unique_buyers(token.unique_buyers_m5),
        "buys_m5": token.buys_m5,
        "sells_m5": token.sells_m5,
        "volume_m5_usd": token.volume_m5_usd,
        "liquidity_usd": token.liquidity_usd,
        "price_change_m5_pct": token.price_change_m5_pct,
        "market_cap_usd": token.market_cap_usd,
        "rugcheck_danger": token.rugcheck_danger,
        "rugcheck_warn_count": token.rugcheck_warn_count,
        "top10_holders_pct": token.top10_holders_pct,
        "has_twitter": token.has_twitter,
        "has_telegram": token.has_telegram,
        "creator": token.creator,
        "duplicate_name_reason": token.duplicate_name_reason,
        "elapsed_seconds": elapsed_seconds,
    }


def _build_outcome_row(outcome: TrackedOutcome, checkpoint_seconds: int, change_pct: float) -> dict:
    """supabase_client.insert_outcome()へ渡す行を組み立てる(supabase_schema.sqlの
    outcomesテーブル参照)。ネットワーク非依存の純粋関数(tests/test_main.py参照)。
    """
    return {
        "mint": outcome.mint,
        "name": outcome.name,
        "symbol": outcome.symbol,
        "notified_tier": outcome.notified_tier,
        "notified_score": outcome.notified_score,
        "checkpoint_seconds": checkpoint_seconds,
        "market_cap_at_notify_usd": outcome.market_cap_at_notify_usd,
        "market_cap_now_usd": outcome.last_market_cap_usd,
        "change_pct": round(change_pct, 2),
    }


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


async def _process_token_checkpoint(
    token: TrackedToken,
    watcher: TokenWatcher,
    outcomes: OutcomeTracker,
    stats: DailyStats,
    blocklist: CreatorBlocklist,
    positions: PositionTracker,
    now: float,
) -> None:
    """1トークン分のチェックポイント処理(DexScreener再取得〜通知判定まで)。

    _checkpoint_loopから、チェックポイントを迎えた各トークンについて並行
    起動される(asyncio.gather、CHECKPOINT_CONCURRENCYで同時実行数を制限)。
    """
    elapsed = watcher.current_checkpoint_seconds(token)

    pair = await asyncio.to_thread(dexscreener_client.fetch_best_pair, token.mint)
    if pair is not None:
        watcher.apply_snapshot(token, pair)

    if elapsed > 0 and pair is not None:
        # ユニーク買い手数はSolanaのオンチェーンデータから集計する
        # (DexScreenerには無い。solana_client.py参照)。0秒チェック
        # ポイント(卒業直後)では行わない。取引の署名取得+複数の
        # getTransaction呼び出しが必要で数秒かかることがあり、初動の
        # 通知速度を落としたくないため(★の情報は後の追い通知で
        # 補う設計。discord_notifier.notify_star_upgrade参照)。
        pool_address = pair.get("pairAddress")
        since_unix = now - config.SOLANA_UNIQUE_BUYERS_WINDOW_SECONDS
        unique_buyers = await asyncio.to_thread(
            solana_client.count_unique_buyers, pool_address, token.mint, since_unix
        )
        watcher.apply_unique_buyers(token, unique_buyers)

    if not token.rugcheck_checked:
        # トークン1件につき1回だけ取得する(レート制限がDexScreener
        # より厳しいため)。取得に失敗した場合はrugcheck_checkedを
        # Trueにしないため、次のチェックポイントで再試行される。
        report = await asyncio.to_thread(rugcheck_client.fetch_risk_report, token.mint)
        if report is not None:
            danger_reason = rugcheck_client.extract_danger_reason(report)
            creator = rugcheck_client.extract_creator(report)
            warn_count = rugcheck_client.extract_warn_count(report)
            top10_holders_pct = rugcheck_client.extract_top_holders_pct(report)
            watcher.apply_rugcheck_report(token, danger_reason, creator, warn_count, top10_holders_pct)
            if danger_reason:
                logger.info(
                    "main: RugCheckで危険フラグを検出しました mint=%s symbol=%s reason=%s",
                    token.mint,
                    token.symbol,
                    danger_reason,
                )
                if token.creator:
                    # 発行者をブロックリストへ記録しておき、名前を
                    # 変えて再発行してきても次回から即0点にする。
                    reason = f"RugCheck危険フラグ: {danger_reason}"
                    blocklist.record(token.creator, reason)
                    await asyncio.to_thread(supabase_client.upsert_creator_blocklist, token.creator, reason)

    if token.creator:
        block_reason = blocklist.is_blocked(token.creator)
        watcher.apply_creator_block(token, block_reason)
        if block_reason and not token.rugcheck_danger:
            logger.info(
                "main: ブロックリスト登録済みの発行者による再発行を検出しました "
                "mint=%s symbol=%s creator=%s reason=%s",
                token.mint,
                token.symbol,
                token.creator,
                block_reason,
            )

    score = scoring.compute_score(token)
    tier = scoring.tier_for_score(score.total)
    _log_score(token, score, elapsed, tier)

    # ⚠️ 自動売買(既定OFF、config.AUTO_TRADE_ENABLED/AUTO_TRADE_CONFIRMED_
    # RISKの両方がtrueでない限りtrade_executor.should_auto_buy()は常に
    # Falseを返すため何もしない。詳細はtrade_executor.py・README.md参照)。
    if not positions.has_any_position(token.mint):
        should_buy, _reason = trade_executor.should_auto_buy(token, elapsed, score.total, positions.open_count())
        if should_buy:
            await asyncio.to_thread(trade_executor.execute_buy, token, positions, now)

    is_tier_upgrade = tier is not None and scoring.is_upgrade(token.notified_tier, tier)
    star_count = scoring.star_count_for_unique_buyers(token.unique_buyers_m5)
    action = _decide_notification_action(
        is_tier_upgrade, tier, token.discord_notified, token.stars_followup_sent, star_count
    )

    if is_tier_upgrade:
        token.notified_tier = tier

    if action == "primary":
        logger.info(
            "main: 通知ラインを超えました mint=%s symbol=%s score=%d tier=%s elapsed=%d秒",
            token.mint,
            token.symbol,
            score.total,
            tier,
            elapsed,
        )
        await asyncio.to_thread(discord_notifier.notify_score_update, token, score, tier, elapsed)
        await asyncio.to_thread(
            supabase_client.insert_notification,
            _build_notification_row(token, score, tier, elapsed, "primary"),
        )
        token.discord_notified = True
        if star_count >= 1:
            # 初回通知の時点で既に★1つ以上なら、通知本文に含まれて
            # いるため追い通知は不要(二重送信防止)。
            token.stars_followup_sent = True
        outcomes.register(
            mint=token.mint,
            name=token.name,
            symbol=token.symbol,
            tier=tier,
            score=score.total,
            market_cap_usd=token.market_cap_usd,
            now=now,
            creator=token.creator,
        )
    elif action == "followup":
        logger.info(
            "main: ユニーク買い手を確認しました(追い通知) mint=%s symbol=%s elapsed=%d秒",
            token.mint,
            token.symbol,
            elapsed,
        )
        token.stars_followup_sent = True
        await asyncio.to_thread(discord_notifier.notify_star_upgrade, token, score, token.notified_tier, elapsed)
        await asyncio.to_thread(
            supabase_client.insert_notification,
            _build_notification_row(token, score, token.notified_tier, elapsed, "followup"),
        )

    watcher.mark_checkpoint_done(token)

    if token.finished:
        stats.record_final(token.notified_tier, score.total)
        watcher.forget(token.mint)


async def _checkpoint_loop(
    watcher: TokenWatcher,
    outcomes: OutcomeTracker,
    stats: DailyStats,
    blocklist: CreatorBlocklist,
    positions: PositionTracker,
) -> None:
    """定期的にチェックポイントを迎えたトークンをDexScreenerで再取得・再評価する。

    該当トークンが複数ある場合、CHECKPOINT_CONCURRENCYで上限を設けつつ
    並行処理する(1件ずつ順番に処理すると、卒業数が多い時間帯にネット
    ワーク往復の待ち時間が積み重なり、処理が実時間に追いつかなくなる
    ため。2026-07判明、_process_token_checkpoint参照)。
    """
    semaphore = asyncio.Semaphore(config.CHECKPOINT_CONCURRENCY)

    async def _run_bounded(token: TrackedToken, now: float) -> None:
        try:
            async with semaphore:
                await _process_token_checkpoint(token, watcher, outcomes, stats, blocklist, positions, now)
        finally:
            watcher.clear_in_flight(token)

    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        now = time.time()
        stats.maybe_reset(now)

        due_tokens = watcher.due_for_checkpoint(now)
        if due_tokens:
            # in_flightは(セマフォ待ちの間も含めて)スケジュールした瞬間に
            # 同期的に立てる。そうしないと、セマフォの空き待ちで実処理が
            # まだ始まっていないトークンを、次のポーリングでdue_for_
            # checkpoint()がもう一度返してしまい、同じチェックポイントを
            # 二重に処理してしまう(token_watcher.py参照)。
            for token in due_tokens:
                watcher.mark_in_flight(token)
            await asyncio.gather(*(_run_bounded(token, now) for token in due_tokens))


async def _process_outcome_checkpoint(
    outcome: TrackedOutcome, outcomes: OutcomeTracker, blocklist: CreatorBlocklist
) -> None:
    """1トークン分の結果チェックポイント処理。_outcome_loopから並行起動される。"""
    pair = await asyncio.to_thread(dexscreener_client.fetch_best_pair, outcome.mint)
    if pair is not None:
        market_cap = float(pair.get("marketCap") or pair.get("fdv") or 0.0)
        outcomes.update_market_cap(outcome.mint, market_cap)

    checkpoint_seconds = config.OUTCOME_CHECKPOINTS_SECONDS[outcome.checkpoint_index]
    change_pct = outcomes.record_and_advance(outcome)
    await asyncio.to_thread(
        supabase_client.insert_outcome, _build_outcome_row(outcome, checkpoint_seconds, change_pct)
    )
    if outcome.creator and change_pct <= config.CREATOR_BLOCKLIST_CRASH_THRESHOLD_PCT:
        logger.info(
            "main: 通知後の大暴落を検出しました mint=%s symbol=%s change_pct=%.1f%%",
            outcome.mint,
            outcome.symbol,
            change_pct,
        )
        reason = f"通知後に{change_pct:.0f}%下落"
        blocklist.record(outcome.creator, reason)
        await asyncio.to_thread(supabase_client.upsert_creator_blocklist, outcome.creator, reason)

    if outcome.finished:
        outcomes.forget(outcome.mint)


async def _outcome_loop(outcomes: OutcomeTracker, blocklist: CreatorBlocklist) -> None:
    """通知済みトークンの結果(30分/1時間/24時間後の時価総額変化)をDexScreenerから取得・記録する。

    通知時点から大暴落(config.CREATOR_BLOCKLIST_CRASH_THRESHOLD_PCT以上の
    下落)したと判明した場合、その発行者をブロックリストへ追加する。
    _checkpoint_loopと同じ理由でCHECKPOINT_CONCURRENCYを上限に並行処理する。
    """
    semaphore = asyncio.Semaphore(config.CHECKPOINT_CONCURRENCY)

    async def _run_bounded(outcome: TrackedOutcome) -> None:
        try:
            async with semaphore:
                await _process_outcome_checkpoint(outcome, outcomes, blocklist)
        finally:
            outcomes.clear_in_flight(outcome)

    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        now = time.time()
        due_outcomes = outcomes.due_for_checkpoint(now)
        if due_outcomes:
            for outcome in due_outcomes:
                outcomes.mark_in_flight(outcome)
            await asyncio.gather(*(_run_bounded(outcome) for outcome in due_outcomes))


async def _stats_loop(stats: DailyStats) -> None:
    """定期的に当日の統計をログへ出力する。"""
    while True:
        await asyncio.sleep(_STATS_LOG_INTERVAL_SECONDS)
        stats.maybe_reset(time.time())
        logger.info("main: %s", stats.summary_line())


async def _position_monitor_loop(positions: PositionTracker) -> None:
    """⚠️ 自動売買で保有中の建玉を定期的に監視し、利確/損切り/最大保有時間
    超過に該当すれば売却する(trade_executor.check_and_close_positions参照)。

    AUTO_TRADE_ENABLED=falseの場合は何もしない(建玉自体が生まれないため)。
    """
    while True:
        await asyncio.sleep(config.AUTO_TRADE_POSITION_POLL_SECONDS)
        if not config.AUTO_TRADE_ENABLED or not config.AUTO_TRADE_CONFIRMED_RISK:
            continue
        await asyncio.to_thread(trade_executor.check_and_close_positions, positions, time.time())


async def async_main() -> None:
    client = PumpPortalClient()
    watcher = TokenWatcher()
    outcomes = OutcomeTracker()
    stats = DailyStats()
    recent_names = _RecentTokenNames()
    blocklist = CreatorBlocklist()
    name_history = TokenNameHistory()
    positions = PositionTracker()
    auto_trade_ready, auto_trade_status = trade_executor.is_ready()
    logger.info(
        "main: 自動売買 status=%s ready=%s (詳細はREADME.mdの「自動売買(実験的機能)」参照)",
        auto_trade_status,
        auto_trade_ready,
    )
    logger.info(
        "main: 監視を開始します checkpoints=%s秒(DEX卒業からの経過) high>=%s watch>=%s low>=%s "
        "discord_enabled=%s creator_blocklist=%d件 supabase_configured=%s",
        config.MIGRATION_CHECKPOINTS_SECONDS,
        config.HIGH_SCORE_THRESHOLD,
        config.WATCH_SCORE_THRESHOLD,
        config.LOW_SCORE_THRESHOLD,
        config.DISCORD_ENABLED,
        len(blocklist),
        supabase_client.is_configured(),
    )
    if not config.DISCORD_ENABLED or not config.DISCORD_WEBHOOK_URL:
        logger.warning(
            "main: DISCORD_ENABLED=falseまたはDISCORD_WEBHOOK_URL未設定のため、"
            "スコアが通知ラインを超えても実際には通知されません(ログにのみ記録されます)"
        )

    await asyncio.gather(
        _consume_loop(client, watcher, recent_names, name_history),
        _checkpoint_loop(watcher, outcomes, stats, blocklist, positions),
        _outcome_loop(outcomes, blocklist),
        _stats_loop(stats),
        _position_monitor_loop(positions),
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
