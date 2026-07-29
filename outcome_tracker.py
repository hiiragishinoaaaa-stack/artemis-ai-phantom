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
    # 通知した時点で、DEX卒業から何秒経っていたか。
    #
    # 「通知時の時価総額が大きいほど成績が良い」と分かっているが、通知は卒業から
    # 15分以内(config.MIGRATION_CHECKPOINTS_SECONDS)に必ず起きる。つまり時価総額
    # $1,000,000での通知は「大きいトークン」ではなく「**15分以内に卒業時の10倍以上へ
    # 急騰したトークン**」を意味する。効いているのが**規模**なのか**上がる速さ**なのかは
    # この2つを分けて記録しないと区別できない(analyze_filters.pyで層別するため)。
    notified_elapsed_seconds: int = 0
    # 通知した時点の★の数(直近5分のユニーク買い手数から決まる。scoring参照)と、
    # その後の観察で到達した最大の★の数。
    #
    # 初回通知は卒業直後で、DexScreenerの直近5分ウィンドウがまだ始まったばかり
    # なので★0のまま出ることが多い。後から★が付いたトークンには追い通知が飛ぶが、
    # **追い通知は結果記録を新しく作らない**(最初の通知時点を基準に評価するため)。
    # そのため「追い通知が飛んだトークンの成績」が今まで一度も測れていなかった。
    # 最大★を持ち回ることで、★が後から付くことに意味があるのかを層別できる。
    notified_star_count: int = 0
    max_star_count: int = 0
    # 候補(🎯)として通知したか。出口アラートの対象を絞るために使う
    # (全通知に出すと6割以上で発火して実用にならない)。
    is_candidate: bool = False
    # 出口アラートを送ったか。1トークンにつき1回だけ送るためのフラグ。
    exit_alert_sent: bool = False
    # 通知後に観測した時価総額の最安値・最高値。チェックポイントの値だけでは
    # 「途中でどこまで下がったか」が分からず、損切りを入れた場合の成績を
    # 推定でしか出せない(analyze_drawdown.py参照)。これを記録しておくと
    # 「-N%で切っていたら約定したか」が推定ではなく確定で分かる。
    # 初期値は通知時点の時価総額(まだ一度も観測していない状態)。
    min_market_cap_usd: float = 0.0
    max_market_cap_usd: float = 0.0
    last_polled_at: float = 0.0
    checkpoint_index: int = 0
    finished: bool = False
    # main.pyが並行処理中に、同じチェックポイントを二重処理しないための
    # ガード(token_watcher.TrackedToken.in_flightと同じ理由)。
    in_flight: bool = False


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
        elapsed_seconds: int = 0,
        star_count: int = 0,
        is_candidate: bool = False,
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
            notified_elapsed_seconds=elapsed_seconds,
            notified_star_count=star_count,
            max_star_count=star_count,
            is_candidate=is_candidate,
            min_market_cap_usd=market_cap_usd,
            max_market_cap_usd=market_cap_usd,
            last_polled_at=now,
        )

    def update_star_count(self, mint: str, star_count: int) -> None:
        """後のチェックポイントで★が増えたら、その最大値を覚えておく。

        追い通知そのものは結果記録を作らないので、ここで持ち回らないと
        「★が後から付いたトークンは成績が良いのか」を永久に測れない。
        """
        outcome = self._outcomes.get(mint)
        if outcome is not None and star_count > outcome.max_star_count:
            outcome.max_star_count = star_count

    def update_market_cap(self, mint: str, market_cap_usd: float, now: float = 0.0) -> None:
        """DexScreenerから取得し直した最新の時価総額を反映する。

        最新値だけでなく、観測した中での最安値・最高値も更新する。
        market_cap_usd が0以下(取得失敗)の場合は何も更新しない。0を安値と
        して取り込むと、取得失敗が「-100%まで落ちた」という記録に化ける。
        """
        outcome = self._outcomes.get(mint)
        if outcome is None or market_cap_usd <= 0:
            return
        outcome.last_market_cap_usd = market_cap_usd
        if outcome.min_market_cap_usd <= 0 or market_cap_usd < outcome.min_market_cap_usd:
            outcome.min_market_cap_usd = market_cap_usd
        if market_cap_usd > outcome.max_market_cap_usd:
            outcome.max_market_cap_usd = market_cap_usd
        if now:
            outcome.last_polled_at = now

    def change_pct_now(self, outcome: TrackedOutcome) -> float | None:
        """通知時点から現在までの変化率(%)。基準が無ければNone。"""
        if outcome.market_cap_at_notify_usd <= 0 or outcome.last_market_cap_usd <= 0:
            return None
        return (
            (outcome.last_market_cap_usd - outcome.market_cap_at_notify_usd)
            / outcome.market_cap_at_notify_usd
            * 100
        )

    def due_for_exit_alert(self, drop_pct: float, all_notifications: bool) -> list[TrackedOutcome]:
        """通知時点から drop_pct 以上下げた、まだ知らせていない対象を返す。

        呼び出し側は送信後に mark_exit_alert_sent() を呼ぶこと。1トークンに
        つき1回しか送らないため、ここでフラグは立てない(送信に失敗したら
        次の周回で再挑戦できるようにする)。
        """
        due = []
        for outcome in self._outcomes.values():
            if outcome.exit_alert_sent:
                continue
            if not all_notifications and not outcome.is_candidate:
                continue
            change = self.change_pct_now(outcome)
            if change is not None and change <= -abs(drop_pct):
                due.append(outcome)
        return due

    def mark_exit_alert_sent(self, outcome: TrackedOutcome) -> None:
        outcome.exit_alert_sent = True

    def due_for_extremes_poll(self, now: float, interval: float, window: float) -> list[TrackedOutcome]:
        """安値・高値の追跡のために、そろそろ値を取り直したい対象を返す。

        通知から window 秒の間だけ、interval 秒ごとに観測する。損切りの判定に
        必要なのは通知直後の急落なので、24時間の追跡全体を細かく叩く必要は
        ない(DexScreenerのレート制限を無駄に使わないため)。
        """
        due = []
        for outcome in self._outcomes.values():
            if outcome.finished or outcome.in_flight:
                continue
            if now - outcome.notified_at > window:
                continue
            if now - outcome.last_polled_at >= interval:
                due.append(outcome)
        return due

    def due_for_checkpoint(self, now: float) -> list[TrackedOutcome]:
        """次の結果チェックポイント時刻を過ぎ、現在処理中でもない追跡対象の
        一覧を返す(in_flight除外の理由はtoken_watcher.TokenWatcher.
        due_for_checkpointと同じ)。
        """
        due = []
        for outcome in self._outcomes.values():
            if outcome.finished or outcome.in_flight:
                continue
            checkpoint_seconds = config.OUTCOME_CHECKPOINTS_SECONDS[outcome.checkpoint_index]
            if now - outcome.notified_at >= checkpoint_seconds:
                due.append(outcome)
        return due

    def mark_in_flight(self, outcome: TrackedOutcome) -> None:
        """結果チェックポイント処理を開始する直前に呼び出す。呼び出し側は
        必ずtry/finallyでclear_in_flight()と対にすること(main.py参照)。
        """
        outcome.in_flight = True

    def clear_in_flight(self, outcome: TrackedOutcome) -> None:
        """結果チェックポイント処理が終わった(成功・失敗を問わず)直後に
        finally節から呼び出す。
        """
        outcome.in_flight = False

    def record_and_advance(
        self, outcome: TrackedOutcome, market_cap_available: bool = True
    ) -> float | None:
        """チェックポイントの結果を1件JSONLへ追記し、次のチェックポイントへ進める。

        通知時点からの変化率(%)を返す(呼び出し側がcreator_blocklistへの
        登録要否を判断するために使う。main.py参照)。

        market_cap_available=False は「最新の時価総額を取得できなかった」の意味で、
        この場合は変化率を計算できない。change_pct と market_cap_now_usd を
        null で記録し、Noneを返す。

        **取得失敗を数値で埋めてはいけない。** 以前は失敗時に0を入れていたため、
        DexScreenerがmarketCapを返さなかっただけの記録が「ちょうど-100.0%」として
        残り、集計時に本物のラグと区別できなくなっていた(analyze_buckets.py の
        『取得失敗の疑い』欄はこの残骸を数えるためのもの)。
        """
        checkpoint_seconds = config.OUTCOME_CHECKPOINTS_SECONDS[outcome.checkpoint_index]
        change_pct: float | None
        if not market_cap_available or outcome.market_cap_at_notify_usd <= 0:
            change_pct = None
        else:
            change_pct = (
                (outcome.last_market_cap_usd - outcome.market_cap_at_notify_usd)
                / outcome.market_cap_at_notify_usd
                * 100
            )

        record = {
            "mint": outcome.mint,
            "name": outcome.name,
            "symbol": outcome.symbol,
            "notified_tier": outcome.notified_tier,
            "notified_score": outcome.notified_score,
            "checkpoint_seconds": checkpoint_seconds,
            "market_cap_at_notify_usd": outcome.market_cap_at_notify_usd,
            "notified_elapsed_seconds": outcome.notified_elapsed_seconds,
            "notified_star_count": outcome.notified_star_count,
            "max_star_count": outcome.max_star_count,
            "market_cap_now_usd": outcome.last_market_cap_usd if market_cap_available else None,
            "change_pct": None if change_pct is None else round(change_pct, 2),
            # 通知後にどこまで下げ、どこまで上げたか。損切り・利確を入れた
            # 場合の成績を推定ではなく確定で計算するために残す。
            "min_change_pct": self._extreme_pct(outcome, outcome.min_market_cap_usd),
            "max_change_pct": self._extreme_pct(outcome, outcome.max_market_cap_usd),
        }
        self._append_record(record)

        outcome.checkpoint_index += 1
        if outcome.checkpoint_index >= len(config.OUTCOME_CHECKPOINTS_SECONDS):
            outcome.finished = True

        return change_pct

    @staticmethod
    def _extreme_pct(outcome: TrackedOutcome, market_cap_usd: float) -> float | None:
        """安値/高値を通知時点からの変化率(%)に直す。観測が無ければNone。"""
        if outcome.market_cap_at_notify_usd <= 0 or market_cap_usd <= 0:
            return None
        return round(
            (market_cap_usd - outcome.market_cap_at_notify_usd)
            / outcome.market_cap_at_notify_usd
            * 100,
            2,
        )

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
