"""スコアリングロジック(0-100点)。

token_watcher.TrackedTokenの状態から、複数の独立した「スコア項目」を
計算して合算する。将来RugCheck/DexScreener/Birdeye/Solscan/AIスコアリング
等の項目を追加しやすいよう、各項目は「TrackedTokenを受け取り
ScoreComponent(点数+説明文)を返す関数」として独立させている
(_SCORERS参照。追加する場合はここに関数を1つ足すだけでよい)。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import config
from token_watcher import TrackedToken


@dataclass
class ScoreComponent:
    name: str
    points: int
    detail: str


@dataclass
class ScoreResult:
    total: int  # 0-100にクランプ済み
    components: list[ScoreComponent] = field(default_factory=list)


def _score_buy_count(token: TrackedToken) -> ScoreComponent:
    count = token.buy_count
    if count >= 10:
        return ScoreComponent("買い件数", 30, f"買い{count}件(10件以上: +30)")
    if count >= 5:
        return ScoreComponent("買い件数", 20, f"買い{count}件(5件以上: +20)")
    if count >= 3:
        return ScoreComponent("買い件数", 10, f"買い{count}件(3件以上: +10)")
    return ScoreComponent("買い件数", 0, f"買い{count}件(3件未満: 加点なし)")


def _score_unique_buyers(token: TrackedToken) -> ScoreComponent:
    count = len(token.unique_buyers)
    if count >= 10:
        return ScoreComponent("ユニーク買い手", 30, f"ユニーク買い手{count}人(10人以上: +30)")
    if count >= 5:
        return ScoreComponent("ユニーク買い手", 20, f"ユニーク買い手{count}人(5人以上: +20)")
    if count >= 2:
        return ScoreComponent("ユニーク買い手", 10, f"ユニーク買い手{count}人(2人以上: +10)")
    return ScoreComponent("ユニーク買い手", 0, f"ユニーク買い手{count}人(2人未満: 加点なし)")


def _score_buy_sell_ratio(token: TrackedToken) -> ScoreComponent:
    buy, sell = token.buy_count, token.sell_count
    if buy > 0 and sell == 0:
        return ScoreComponent("Buy/Sell比率", 30, f"買い{buy}件/売り0件(売りなし: +30)")
    if sell > 0 and buy >= sell * 3:
        return ScoreComponent("Buy/Sell比率", 30, f"買い{buy}/売り{sell}(3倍以上: +30)")
    if sell > 0 and buy >= sell * 2:
        return ScoreComponent("Buy/Sell比率", 20, f"買い{buy}/売り{sell}(2倍以上: +20)")
    if buy > sell:
        return ScoreComponent("Buy/Sell比率", 10, f"買い{buy}/売り{sell}(買い優勢: +10)")
    return ScoreComponent("Buy/Sell比率", 0, f"買い{buy}/売り{sell}(売り優勢または同数: 加点なし)")


def _score_volume(token: TrackedToken) -> ScoreComponent:
    volume = token.total_volume_sol
    threshold = config.MIN_VOLUME_SOL_FOR_SCORE
    if volume >= threshold:
        return ScoreComponent("Volume", 10, f"出来高{volume:.2f} SOL({threshold}以上: +10)")
    return ScoreComponent("Volume", 0, f"出来高{volume:.2f} SOL({threshold}未満: 加点なし)")


def _score_market_cap(token: TrackedToken) -> ScoreComponent:
    mcap = token.last_market_cap_sol
    threshold = config.MIN_MARKET_CAP_SOL_FOR_SCORE
    if mcap >= threshold:
        return ScoreComponent("Market Cap", 10, f"時価総額{mcap:.1f} SOL({threshold}以上: +10)")
    return ScoreComponent("Market Cap", 0, f"時価総額{mcap:.1f} SOL({threshold}未満: 加点なし)")


# 将来項目を追加する場合はここに関数を1つ足すだけでよい(TrackedTokenを
# 受け取りScoreComponentを返す関数であること。他の項目とは完全に独立)。
_SCORERS: list[Callable[[TrackedToken], ScoreComponent]] = [
    _score_buy_count,
    _score_unique_buyers,
    _score_buy_sell_ratio,
    _score_volume,
    _score_market_cap,
]


def compute_score(token: TrackedToken) -> ScoreResult:
    """TrackedTokenの現在の状態から0-100点のスコアを計算する。"""
    components = [scorer(token) for scorer in _SCORERS]
    total = max(0, min(100, sum(c.points for c in components)))
    return ScoreResult(total=total, components=components)


def tier_for_score(score: int) -> str | None:
    """スコアから通知レベルを返す("HIGH" > "WATCH" > "LOW" > None)。"""
    if score >= config.HIGH_SCORE_THRESHOLD:
        return "HIGH"
    if score >= config.WATCH_SCORE_THRESHOLD:
        return "WATCH"
    if score >= config.LOW_SCORE_THRESHOLD:
        return "LOW"
    return None


# 通知レベルの優先順位(数値が大きいほど優先度が高い)。アップグレード通知
# ("WATCH→HIGHへ上昇"等)の判定に使う(main.py参照)。
TIER_RANK: dict[str, int] = {"LOW": 1, "WATCH": 2, "HIGH": 3}


def is_upgrade(previous_tier: str | None, new_tier: str | None) -> bool:
    """new_tierがprevious_tierより高い優先度かどうか。"""
    if new_tier is None:
        return False
    if previous_tier is None:
        return True
    return TIER_RANK.get(new_tier, 0) > TIER_RANK.get(previous_tier, 0)
