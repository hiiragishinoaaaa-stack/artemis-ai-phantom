"""新規トークンの初動観察・フィルター判定ロジック。

PumpPortalから届くイベント(トークン作成・売買)を受け取り、
OBSERVATION_WINDOW_SECONDS秒だけ様子を見た上で、MIN_BUY_COUNT等の条件を
満たしたものだけを「通知対象」として返す。

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
    evaluated: bool = False


class TokenWatcher:
    """新規トークンを観察し、フィルターを通過したものを判定するクラス。"""

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

    def on_trade(self, mint: str, tx_type: str, trader: str, market_cap_sol: float) -> None:
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
        token.last_market_cap_sol = market_cap_sol

    def due_for_evaluation(self, now: float) -> list[TrackedToken]:
        """観察期間(OBSERVATION_WINDOW_SECONDS秒)が経過し、まだ判定していない
        トークンの一覧を返す(呼び出し側が定期的にポーリングする想定)。
        """
        return [
            t
            for t in self._tokens.values()
            if not t.evaluated and now - t.created_at >= config.OBSERVATION_WINDOW_SECONDS
        ]

    def evaluate(self, token: TrackedToken) -> bool:
        """観察終了時に1回呼び出し、フィルター条件を満たすか判定する。

        条件(config.py参照):
        - 買い件数がMIN_BUY_COUNT以上
        - ユニークな買い手がMIN_UNIQUE_BUYERS以上
        - 売り件数が買い件数×MAX_SELL_TO_BUY_RATIOを超えていない
        - (MIN_MARKET_CAP_SOL>0の場合)時価総額がその値以上
        """
        token.evaluated = True

        if token.buy_count < config.MIN_BUY_COUNT:
            return False
        if len(token.unique_buyers) < config.MIN_UNIQUE_BUYERS:
            return False
        if token.sell_count > token.buy_count * config.MAX_SELL_TO_BUY_RATIO:
            return False
        if config.MIN_MARKET_CAP_SOL > 0 and token.last_market_cap_sol < config.MIN_MARKET_CAP_SOL:
            return False
        return True

    def forget(self, mint: str) -> None:
        """観察を終了し、状態を破棄する(評価後、購読解除とセットで呼ぶ)。"""
        self._tokens.pop(mint, None)

    def _evict_oldest_if_over_capacity(self) -> None:
        """MAX_TRACKED_TOKENSを超えたら、最も古いものから間引く
        (購読・メモリの際限ない増加を防ぐ安全弁)。
        """
        if len(self._tokens) <= config.MAX_TRACKED_TOKENS:
            return
        oldest = min(self._tokens.values(), key=lambda t: t.created_at)
        self._tokens.pop(oldest.mint, None)
