"""通知後の結果トラッキング(30分/1時間/24時間後の時価総額変化を記録)。

WATCH/HIGHとして通知したトークンについて、通知時点の時価総額を基準に、
config.OUTCOME_CHECKPOINTS_SECONDS(既定30分/1時間/24時間)の各時点での
変化率をconfig.OUTCOMES_FILE_PATH(JSONL)へ追記する。将来、どのスコア項目
が実際に価格上昇と相関していたかを分析するためのデータ収集のみが目的で、
これ自体は通知やスコアリングの判定には一切影響しない。

このモジュール自体はHTTP通信を行わない。各チェックポイントの直前に
dexscreener_client.fetch_best_pair()で最新の時価総額を取得し
`update_market_cap()`で反映するのはmain.py側の役目(2026-07、PumpPortalの
subscribeTokenTradeを使わなくなったため、継続的な受動更新ではなく
チェックポイントごとの能動ポーリング方式に変更)。

TokenWatcherと同様、時刻はすべて呼び出し側から渡す(time.time()に依存
しない)ため、ネットワークなしで単体テストできる。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import config

logger = logging.getLogger("phantom_sniper")


@dataclass
class TrackedOutcome:
    """通知後に結果を追跡中の1トークンの状態。"""

    mint: str
    name: str
    symbol: str
    notified_at: float
    notified_tier: str
    notified_score: int
    market_cap_at_notify_usd: float
    last_market_cap_usd: float
    # RugCheckで判明していた発行者ウォレットアドレス(空の場合もある)。
    # 大暴落を検出した際にcreator_blocklistへ登録するために使う
    # (main.py参照)。
    creator: str = ""
    checkpoint_index: int = 0
    finished: bool = False


class OutcomeTracker:
    """通知済みトークンの市場データを24時間まで追跡し、結果をJSONLへ記録するクラス。"""

    def __init__(self) -> None:
        self._outcomes: dict[str, TrackedOutcome] = {}

    def __len__(self) -> int:
        return len(self._outcomes)

    def is_tracking(self, mint: str) -> bool:
        return mint in self._outcomes

    def register(
        self,
        mint: str,
        name: str,
        symbol: str,
        tier: str,
        score: int,
        market_cap_usd: float,
        now: float,
        creator: str = "",
    ) -> None:
        """通知が発生した瞬間に1回呼び出し、結果追跡を開始する。

        既に追跡中のmint(再通知でティアが上がった場合等)は上書きしない。
        最初の通知時点を基準として結果を評価するため。
        """
        if mint in self._outcomes:
            return
        self._outcomes[mint] = TrackedOutcome(
            mint=mint,
            name=name,
            symbol=symbol,
            notified_at=now,
            notified_tier=tier,
            notified_score=score,
            market_cap_at_notify_usd=market_cap_usd,
            last_market_cap_usd=market_cap_usd,
            creator=creator,
        )

    def update_market_cap(self, mint: str, market_cap_usd: float) -> None:
        """DexScreenerから取得し直した最新の時価総額を反映する。"""
        outcome = self._outcomes.get(mint)
        if outcome is not None:
            outcome.last_market_cap_usd = market_cap_usd

    def due_for_checkpoint(self, now: float) -> list[TrackedOutcome]:
        """次の結果チェックポイント時刻を過ぎた追跡対象の一覧を返す。"""
        due = []
        for outcome in self._outcomes.values():
            if outcome.finished:
                continue
            checkpoint_seconds = config.OUTCOME_CHECKPOINTS_SECONDS[outcome.checkpoint_index]
            if now - outcome.notified_at >= checkpoint_seconds:
                due.append(outcome)
        return due

    def record_and_advance(self, outcome: TrackedOutcome) -> float:
        """チェックポイントの結果を1件JSONLへ追記し、次のチェックポイントへ進める。

        通知時点からの変化率(%)を返す(呼び出し側がcreator_blocklistへの
        登録要否を判断するために使う。main.py参照)。
        """
        checkpoint_seconds = config.OUTCOME_CHECKPOINTS_SECONDS[outcome.checkpoint_index]
        if outcome.market_cap_at_notify_usd > 0:
            change_pct = (
                (outcome.last_market_cap_usd - outcome.market_cap_at_notify_usd)
                / outcome.market_cap_at_notify_usd
                * 100
            )
        else:
            change_pct = 0.0

        record = {
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
        self._append_record(record)

        outcome.checkpoint_index += 1
        if outcome.checkpoint_index >= len(config.OUTCOME_CHECKPOINTS_SECONDS):
            outcome.finished = True

        return change_pct

    def forget(self, mint: str) -> None:
        self._outcomes.pop(mint, None)

    @staticmethod
    def _append_record(record: dict) -> None:
        try:
            config.OUTCOMES_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with config.OUTCOMES_FILE_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("outcome_tracker: 結果の記録に失敗しました: %s", exc)
