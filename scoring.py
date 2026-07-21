"""スコアリングロジック(0-100点)。

token_watcher.TrackedTokenの状態(DexScreenerから取得した直近5分の
売買件数・出来高・価格変動・流動性)から、複数の独立した「スコア項目」を
計算して合算する。将来RugCheck/Birdeye/Solscan/AIスコアリング等の項目を
追加しやすいよう、各項目は「TrackedTokenを受け取りScoreComponent(点数+
説明文)を返す関数」として独立させている(_SCORERS参照。追加する場合は
ここに関数を1つ足すだけでよい)。

DexScreenerにまだペアが見つかっていない(has_pair_data=False)トークンは
全項目0点になる(全フィールドが既定値0のため、特別扱いは不要)。
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


def _score_buys_m5(token: TrackedToken) -> ScoreComponent:
    count = token.buys_m5
    if count >= 20:
        return ScoreComponent("直近5分の買い件数", 30, f"買い{count}件(20件以上: +30)")
    if count >= 10:
        return ScoreComponent("直近5分の買い件数", 20, f"買い{count}件(10件以上: +20)")
    if count >= 5:
        return ScoreComponent("直近5分の買い件数", 10, f"買い{count}件(5件以上: +10)")
    return ScoreComponent("直近5分の買い件数", 0, f"買い{count}件(5件未満: 加点なし)")


def _score_buy_sell_ratio(token: TrackedToken) -> ScoreComponent:
    buy, sell = token.buys_m5, token.sells_m5
    if buy > 0 and sell == 0:
        return ScoreComponent("直近5分のBuy/Sell比率", 30, f"買い{buy}件/売り0件(売りなし: +30)")
    if sell > 0 and buy >= sell * 3:
        return ScoreComponent("直近5分のBuy/Sell比率", 30, f"買い{buy}/売り{sell}(3倍以上: +30)")
    if sell > 0 and buy >= sell * 2:
        return ScoreComponent("直近5分のBuy/Sell比率", 20, f"買い{buy}/売り{sell}(2倍以上: +20)")
    if buy > sell:
        return ScoreComponent("直近5分のBuy/Sell比率", 10, f"買い{buy}/売り{sell}(買い優勢: +10)")
    return ScoreComponent("直近5分のBuy/Sell比率", 0, f"買い{buy}/売り{sell}(売り優勢または同数: 加点なし)")


# discord_notifier.pyの★表示もこの区切りに合わせているため、変更する場合は
# 両方一致させること。
UNIQUE_BUYERS_M5_TIER_THRESHOLDS: tuple[int, int, int] = (2, 5, 10)


def _score_unique_buyers_m5(token: TrackedToken) -> ScoreComponent:
    count = token.unique_buyers_m5
    tier2, tier5, tier10 = UNIQUE_BUYERS_M5_TIER_THRESHOLDS
    if count >= tier10:
        return ScoreComponent("直近5分のユニーク買い手", 20, f"ユニーク買い手{count}人({tier10}人以上: +20)")
    if count >= tier5:
        return ScoreComponent("直近5分のユニーク買い手", 10, f"ユニーク買い手{count}人({tier5}人以上: +10)")
    if count >= tier2:
        return ScoreComponent("直近5分のユニーク買い手", 5, f"ユニーク買い手{count}人({tier2}人以上: +5)")
    return ScoreComponent("直近5分のユニーク買い手", 0, f"ユニーク買い手{count}人({tier2}人未満: 加点なし)")


def star_count_for_unique_buyers(unique_buyers_m5: int) -> int:
    """直近5分のユニーク買い手数を0〜3の★段階に変換する(UNIQUE_BUYERS_M5_TIER_THRESHOLDS基準)。

    discord_notifier.py(通知本文の★表示)とmain.py(追い通知の判定)の
    両方から使う共通ロジック。
    """
    tier2, tier5, tier10 = UNIQUE_BUYERS_M5_TIER_THRESHOLDS
    if unique_buyers_m5 >= tier10:
        return 3
    if unique_buyers_m5 >= tier5:
        return 2
    if unique_buyers_m5 >= tier2:
        return 1
    return 0


def _score_volume_m5(token: TrackedToken) -> ScoreComponent:
    volume = token.volume_m5_usd
    threshold = config.MIN_VOLUME_USD_FOR_SCORE
    if volume >= threshold:
        return ScoreComponent("直近5分の出来高", 10, f"出来高${volume:,.0f}({threshold:,.0f}以上: +10)")
    return ScoreComponent("直近5分の出来高", 0, f"出来高${volume:,.0f}({threshold:,.0f}未満: 加点なし)")


def _score_liquidity(token: TrackedToken) -> ScoreComponent:
    liquidity = token.liquidity_usd
    threshold = config.MIN_LIQUIDITY_USD_FOR_SCORE
    if liquidity >= threshold:
        return ScoreComponent("流動性", 10, f"流動性${liquidity:,.0f}({threshold:,.0f}以上: +10)")
    return ScoreComponent("流動性", 0, f"流動性${liquidity:,.0f}({threshold:,.0f}未満: 加点なし)")


def _score_price_change_m5(token: TrackedToken) -> ScoreComponent:
    change = token.price_change_m5_pct
    if change >= 50:
        return ScoreComponent("直近5分の価格変動", 20, f"価格変動{change:+.1f}%(50%以上: +20)")
    if change >= 20:
        return ScoreComponent("直近5分の価格変動", 10, f"価格変動{change:+.1f}%(20%以上: +10)")
    if change > 0:
        return ScoreComponent("直近5分の価格変動", 5, f"価格変動{change:+.1f}%(プラス: +5)")
    return ScoreComponent("直近5分の価格変動", 0, f"価格変動{change:+.1f}%(プラスなし: 加点なし)")


# RugCheckが"danger"レベルのリスク(mint権限が発行者に残っている、上位
# 保有者への極端な集中、単一保有者が大半保有、等)を検出した場合、他の
# 項目がどれだけ高くても通知させないための強いペナルティ。他の項目の
# 合計がどれだけ増えても(将来項目が増えても)確実に相殺できるよう、
# 現実的に届く範囲より大きく負にしている(compute_score()がmax(0, ...)
# でクランプするため、実際のスコア下限は常に0)。
_RUGCHECK_DANGER_PENALTY = -1000


def _score_rugcheck_safety(token: TrackedToken) -> ScoreComponent:
    if not token.rugcheck_checked:
        return ScoreComponent("RugCheckセーフティ", 0, "RugCheck未取得(判定なし)")
    if token.rugcheck_danger:
        return ScoreComponent(
            "RugCheckセーフティ",
            _RUGCHECK_DANGER_PENALTY,
            f"危険フラグ検出: {token.rugcheck_danger_reason}(スコアを強制的に0点扱いにします)",
        )
    return ScoreComponent("RugCheckセーフティ", 10, "危険フラグなし(+10)")


# RugCheckの"warn"レベル(dangerほど致命的ではないが注意が必要)のリスク
# フラグ1件ごとの減点。_RUGCHECK_DANGER_PENALTYと違い、これは通知自体を
# 止めるほどの強さにはしない(初動の伸びを狙う都合上、疑わしい程度で
# 機会を潰したくないため、あくまでスコアを少し下げるだけの参考情報)。
_RUGCHECK_WARN_PENALTY_PER_RISK = -5
# 件数が増えても際限なく下がらないよう、頭打ちにする(3件以上は同じ扱い)。
_RUGCHECK_WARN_PENALTY_CAP = -15


def _score_rugcheck_warnings(token: TrackedToken) -> ScoreComponent:
    if not token.rugcheck_checked:
        return ScoreComponent("RugCheck注意フラグ", 0, "RugCheck未取得(判定なし)")
    count = token.rugcheck_warn_count
    if count <= 0:
        return ScoreComponent("RugCheck注意フラグ", 0, "warn相当のリスクなし")
    points = max(_RUGCHECK_WARN_PENALTY_CAP, _RUGCHECK_WARN_PENALTY_PER_RISK * count)
    return ScoreComponent("RugCheck注意フラグ", points, f"warn相当のリスク{count}件({points}点)")


# 上位10保有者の合計保有率(%)がこの値以上なら集中しすぎ(⚠️)とみなし
# 減点、この値未満なら健全に分散している(✅)とみなし加点する
# (config.pyで調整可能)。RugCheckの"danger"レベルの極端な集中(例えば
# 単一保有者が大半保有)は既にrugcheck_dangerで強制0点になるため、これは
# それより緩やかな「気になる程度」の集中度への追加シグナル。
_HOLDER_CONCENTRATION_WARN_PENALTY = -10
_HOLDER_CONCENTRATION_HEALTHY_BONUS = 10


def _score_holder_concentration(token: TrackedToken) -> ScoreComponent:
    pct = token.top10_holders_pct
    if pct is None:
        return ScoreComponent("上位10保有者集中度", 0, "RugCheck未取得(判定なし)")
    if pct >= config.HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT:
        return ScoreComponent(
            "上位10保有者集中度",
            _HOLDER_CONCENTRATION_WARN_PENALTY,
            f"上位10人で{pct:.1f}%保有({config.HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT:.0f}%以上: "
            f"{_HOLDER_CONCENTRATION_WARN_PENALTY}点)",
        )
    if pct < config.HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT:
        return ScoreComponent(
            "上位10保有者集中度",
            _HOLDER_CONCENTRATION_HEALTHY_BONUS,
            f"上位10人で{pct:.1f}%保有({config.HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT:.0f}%未満: "
            f"+{_HOLDER_CONCENTRATION_HEALTHY_BONUS})",
        )
    return ScoreComponent("上位10保有者集中度", 0, f"上位10人で{pct:.1f}%保有(中間: 加点なし)")


# 過去に危険判定・大暴落があったトークンの発行者が、名前を変えて別の
# トークンを再発行してきた場合、他の項目がどれだけ高くても通知させない
# ための強いペナルティ(_RUGCHECK_DANGER_PENALTYと同じ考え方)。
_CREATOR_BLOCKLIST_PENALTY = -1000


def _score_creator_blocklist(token: TrackedToken) -> ScoreComponent:
    if token.blocked_creator_reason:
        return ScoreComponent(
            "発行者ブラックリスト",
            _CREATOR_BLOCKLIST_PENALTY,
            f"過去に問題のあった発行者による再発行を検出: {token.blocked_creator_reason}"
            "(スコアを強制的に0点扱いにします)",
        )
    return ScoreComponent("発行者ブラックリスト", 0, "ブラックリスト該当なし")


# 過去に既出の名前/ティッカーを別mintが後から名乗っている(なりすまし
# 疑い、token_name_history.py参照)場合の減点。RugCheck危険/発行者
# ブラックリストと違い通知自体は止めないほど強くはしない(一般的な単語の
# 名前が偶然重複するだけの場合もあり、機会を潰したくないため)。ただし
# 「見た瞬間に100点⭐⭐⭐が付く」のを防ぐには十分な大きさにしている。
_DUPLICATE_NAME_PENALTY = -50


def _score_duplicate_name(token: TrackedToken) -> ScoreComponent:
    if token.duplicate_name_reason:
        return ScoreComponent(
            "名前/ティッカー重複",
            _DUPLICATE_NAME_PENALTY,
            f"{token.duplicate_name_reason}({_DUPLICATE_NAME_PENALTY}点)",
        )
    return ScoreComponent("名前/ティッカー重複", 0, "重複なし")


# 将来項目を追加する場合はここに関数を1つ足すだけでよい(TrackedTokenを
# 受け取りScoreComponentを返す関数であること。他の項目とは完全に独立)。
_SCORERS: list[Callable[[TrackedToken], ScoreComponent]] = [
    _score_buys_m5,
    _score_buy_sell_ratio,
    _score_unique_buyers_m5,
    _score_volume_m5,
    _score_liquidity,
    _score_price_change_m5,
    _score_rugcheck_safety,
    _score_rugcheck_warnings,
    _score_holder_concentration,
    _score_creator_blocklist,
    _score_duplicate_name,
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
