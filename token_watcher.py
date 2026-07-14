"""新規トークンの初動観察・チェックポイント管理ロジック。

PumpPortalから届くイベント(トークン作成・売買)を受け取り、
config.EVALUATION_CHECKPOINTS_SECONDS(既定20/40/60/90/120秒)の各時点で
繰り返しスコア再計算ができるよう状態を保持する。実際のスコア計算は
scoring.pyが独立して担当し、このモジュールは「いつ・どのトークンを
再評価すべきか」の管理のみを行う。

このモジュールはネットワーク・WebSocket・時刻取得(time.time())に一切
依存しない(呼び出し側が現在時刻を明示的に渡す)。そのためMT5に依存しない
mt5_ai_trader/risk_manager.py等と同じく、単体テストがネットワークなしで
書ける設計にしている(詳細はtests/test_token_watcher.py参照)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config


@dataclass
class TrackedToken:
    """観察中(または観察済み)の1トークンの状態。"""

    mint: str
    name: str
    symbol: str
    creator: str
    created_at: float
    initial_market_cap_sol: float
    last_market_cap_sol: float
    buy_count: int = 0
    sell_count: int = 0
    unique_buyers: set[str] = field(default_factory=set)
    total_volume_sol: float = 0.0
    # config.EVALUATION_CHECKPOINTS_SECONDSのうち、次に処理すべきチェックポイント
    # のインデックス(0=20秒地点、1=40秒地点、...)。最後のチェックポイントまで
    # 処理し終えるとfinished=Trueになる。
    checkpoint_index: int = 0
    finished: bool = False
    # これまでに通知した最高の通知レベル("LOW"/"WATCH"/"HIGH"またはNone)。
    # スコアが上昇して通知ラインを更新した瞬間だけ再通知するために使う。
    notified_tier: str | None = None


class TokenWatcher:
    """新規トークンを観察し、チェックポイントごとの再評価対象を管理するクラス。"""

    def __init__(self) -> None:
        self._tokens: dict[str, TrackedToken] = {}

    def __len__(self) -> int:
        return len(self._tokens)

    def get(self, mint: str) -> TrackedToken | None:
        return self._tokens.get(mint)

    def on_token_created(
        self,
        mint: str,
        name: str,
        symbol: str,
        creator: str,
        market_cap_sol: float,
        now: float,
    ) -> TrackedToken:
        """PumpPortalのtxType="create"イベントを受けて観察を開始する。

        同じmintが既に観察中の場合は既存のものをそのまま返す(重複作成
        イベントへの耐性)。
        """
        existing = self._tokens.get(mint)
        if existing is not None:
            return existing

        token = TrackedToken(
            mint=mint,
            name=name,
            symbol=symbol,
            creator=creator,
            created_at=now,
            initial_market_cap_sol=market_cap_sol,
            last_market_cap_sol=market_cap_sol,
        )
        self._tokens[mint] = token
        self._evict_oldest_if_over_capacity()
        return token

    def on_trade(
        self,
        mint: str,
        tx_type: str,
        trader: str,
        market_cap_sol: float,
        sol_amount: float = 0.0,
    ) -> None:
        """PumpPortalのtxType="buy"/"sell"イベントを受けて観察中トークンの状態を更新する。

        観察対象外(既に忘れた・そもそも知らない)mintのイベントは無視する。
        """
        token = self._tokens.get(mint)
        if token is None:
            return

        if tx_type == "buy":
            token.buy_count += 1
            token.unique_buyers.add(trader)
        elif tx_type == "sell":
            token.sell_count += 1
        token.total_volume_sol += sol_amount
        token.last_market_cap_sol = market_cap_sol

    def due_for_checkpoint(self, now: float) -> list[TrackedToken]:
        """次のチェックポイント時刻を過ぎ、まだそのチェックポイントを処理していない
        トークンの一覧を返す(呼び出し側が定期的にポーリングする想定)。
        """
        due = []
        for token in self._tokens.values():
            if token.finished:
                continue
            checkpoint_seconds = config.EVALUATION_CHECKPOINTS_SECONDS[token.checkpoint_index]
            if now - token.created_at >= checkpoint_seconds:
                due.append(token)
        return due

    def current_checkpoint_seconds(self, token: TrackedToken) -> int:
        """このトークンが今まさに処理しようとしているチェックポイント(経過秒)。"""
        return config.EVALUATION_CHECKPOINTS_SECONDS[token.checkpoint_index]

    def mark_checkpoint_done(self, token: TrackedToken) -> None:
        """チェックポイント処理後に1回呼び出し、次のチェックポイントへ進める。

        最後のチェックポイント(既定120秒)を処理し終えたらfinished=Trueにする。
        """
        token.checkpoint_index += 1
        if token.checkpoint_index >= len(config.EVALUATION_CHECKPOINTS_SECONDS):
            token.finished = True

    def forget(self, mint: str) -> None:
        """観察を終了し、状態を破棄する(全チェックポイント処理後、購読解除とセットで呼ぶ)。"""
        self._tokens.pop(mint, None)

    def _evict_oldest_if_over_capacity(self) -> None:
        """MAX_TRACKED_TOKENSを超えたら、最も古いものから間引く
        (購読・メモリの際限ない増加を防ぐ安全弁)。
        """
        if len(self._tokens) <= config.MAX_TRACKED_TOKENS:
            return
        oldest = min(self._tokens.values(), key=lambda t: t.created_at)
        self._tokens.pop(oldest.mint, None)
