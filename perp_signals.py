"""パーペチュアル先物のロング/ショート判定ロジック(perp_sniper.py用)。

⚠️ 実験的機能。ここで出す判定は一般的なテクニカル指標(EMAトレンド・RSI・
モメンタム・ファンディングレート)の組み合わせによる簡易的なシグナルで
あり、値上がり/値下がりを保証するものではない。perp_paper_trader.pyの
モック実行以外で実資金の発注には一切使わない設計にしている(実際の
取引所APIとの接続は未実装。README.mdの「パーペチュアル(実験的機能)」
参照)。

このモジュールはネットワーク・時刻取得に一切依存しない純粋関数のみで
構成する(closes/funding_rateは呼び出し側が渡す。単体テストしやすく
するため。perp_market_data.pyが実際のデータ取得を担当する)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config


def compute_ema(values: list[float], period: int) -> float | None:
    """単純な指数移動平均(EMA)の最新値を返す(データ不足ならNone)。"""
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period  # 最初はSMAで初期化
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
    return ema


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI(Relative Strength Index、0〜100)の最新値を返す(データ不足ならNone)。

    70以上: 買われすぎ(反落リスク)、30以下: 売られすぎ(反発余地)、
    それ以外: 中立。
    """
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_gain == 0 and avg_loss == 0:
        return 50.0  # 値動きが全く無い(完全に横ばい) -> 中立
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


@dataclass
class PerpSignal:
    symbol: str
    direction: str  # "LONG" | "SHORT" | "NEUTRAL"
    score: int  # -100(強いショート根拠)〜+100(強いロング根拠)
    price: float
    reasons: list[str] = field(default_factory=list)


# ファンディングレートがこの絶対値を超えたら「過密」とみなし、逆張り方向へ
# 少し加点する(ロング過密なら-、ショート過密なら+)。
_FUNDING_RATE_EXTREME_THRESHOLD = 0.0005  # 0.05%


def compute_signal(
    symbol: str,
    closes: list[float],
    funding_rate: float | None,
    ema_short_period: int = 20,
    ema_long_period: int = 50,
    rsi_period: int = 14,
) -> PerpSignal | None:
    """終値の並び(古い順)とファンディングレートから、ロング/ショートの
    シグナルを組み立てる(純粋関数)。データ不足の場合はNoneを返す。
    """
    if not closes:
        return None
    price = closes[-1]

    ema_short = compute_ema(closes, ema_short_period)
    ema_long = compute_ema(closes, ema_long_period)
    if ema_short is None or ema_long is None:
        return None

    score = 0
    reasons: list[str] = []

    # トレンド: 短期EMAが長期EMAより上なら強気バイアス(完全に同値の場合は中立)。
    if ema_short > ema_long:
        score += 40
        reasons.append(f"短期トレンド上向き(EMA{ema_short_period}>{ema_long_period})")
    elif ema_short < ema_long:
        score -= 40
        reasons.append(f"短期トレンド下向き(EMA{ema_short_period}<{ema_long_period})")

    # モメンタム: 直近の値動き(終値の最初と最後の変化率)。
    momentum_pct = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0.0
    momentum_score = max(-20, min(20, int(momentum_pct)))
    score += momentum_score
    if momentum_score != 0:
        reasons.append(f"モメンタム{momentum_pct:+.1f}%")

    # RSI: 買われすぎ/売られすぎの反転リスクを加味。
    rsi = compute_rsi(closes, rsi_period)
    if rsi is not None:
        if rsi >= 70:
            score -= 20
            reasons.append(f"RSI買われすぎ({rsi:.0f})")
        elif rsi <= 30:
            score += 20
            reasons.append(f"RSI売られすぎ({rsi:.0f})")

    # ファンディングレート: 過密な方向への逆張り根拠。
    if funding_rate is not None and abs(funding_rate) >= _FUNDING_RATE_EXTREME_THRESHOLD:
        if funding_rate > 0:
            score -= 10
            reasons.append(f"ファンディングレートがロング過密({funding_rate:+.3%})")
        else:
            score += 10
            reasons.append(f"ファンディングレートがショート過密({funding_rate:+.3%})")

    score = max(-100, min(100, score))
    if score >= config.PERP_SIGNAL_THRESHOLD:
        direction = "LONG"
    elif score <= -config.PERP_SIGNAL_THRESHOLD:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    return PerpSignal(symbol=symbol, direction=direction, score=score, price=price, reasons=reasons)
