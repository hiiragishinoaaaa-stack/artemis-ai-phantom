"""パーペチュアル先物(BTCUSDT等)の価格・ファンディングレートを取得する
クライアント(perp_sniper.py用)。

Binance Futuresの公開REST API(無料・APIキー不要、板情報の閲覧だけなら
認証不要)を使う。取引所は差し替え可能なようにURLをconfig経由にしている
(config.PERP_API_BASE_URL)。実際の発注は一切行わない(このモジュールは
市場データの取得のみ)。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 10
_USER_AGENT = "Mozilla/5.0 (compatible; ARTEMIS-Phantom-Sniper/1.0)"


def _get(path: str, params: dict) -> object | None:
    url = f"{config.PERP_API_BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("perp_market_data: %sの取得に失敗しました: %s", path, exc)
        return None


def fetch_closes(symbol: str, interval: str, limit: int) -> list[float] | None:
    """直近limit本分のローソク足の終値(古い順)を返す(取得失敗時はNone)。

    Binance Futures klines形式: [open_time, open, high, low, close, volume, ...]
    """
    data = _get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": str(limit)})
    if not isinstance(data, list):
        return None
    closes = []
    for candle in data:
        if not isinstance(candle, list) or len(candle) < 5:
            continue
        try:
            closes.append(float(candle[4]))
        except (TypeError, ValueError):
            continue
    return closes or None


def fetch_klines_with_time(symbol: str, interval: str, limit: int) -> list[tuple[float, float]] | None:
    """直近limit本分の(始値時刻[UNIX秒], 終値)のペアを古い順で返す(perp_backtest.py用)。

    fetch_closes()と違い、バックテストで「何秒後に利確/損切り/最大保有時間
    超過に達したか」を実際のローソク足の時間軸で判定するため時刻も返す。
    """
    data = _get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": str(limit)})
    if not isinstance(data, list):
        return None
    result = []
    for candle in data:
        if not isinstance(candle, list) or len(candle) < 5:
            continue
        try:
            open_time = float(candle[0]) / 1000.0
            close = float(candle[4])
        except (TypeError, ValueError):
            continue
        result.append((open_time, close))
    return result or None


def fetch_latest_funding_rate(symbol: str) -> float | None:
    """直近のファンディングレート(小数、例: 0.0001 = 0.01%)を返す(失敗時はNone)。

    正の値: ロングがショートへ手数料を払う(=ロング過密、逆張り的には
    やや弱気材料)。負の値はその逆。
    """
    data = _get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": "1"})
    if not isinstance(data, list) or not data:
        return None
    entry = data[-1]
    if not isinstance(entry, dict):
        return None
    try:
        return float(entry.get("fundingRate"))
    except (TypeError, ValueError):
        return None


def fetch_mark_price(symbol: str) -> float | None:
    """現在のマーク価格(USDT建て)を返す(失敗時はNone)。"""
    data = _get("/fapi/v1/premiumIndex", {"symbol": symbol})
    if not isinstance(data, dict):
        return None
    try:
        return float(data.get("markPrice"))
    except (TypeError, ValueError):
        return None
