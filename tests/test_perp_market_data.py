"""perp_market_data.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import perp_market_data


def _response(payload) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_fetch_closes_parses_kline_close_prices():
    klines = [
        [1000, "1.0", "1.1", "0.9", "1.05", "100"],
        [2000, "1.05", "1.2", "1.0", "1.15", "120"],
    ]
    with patch("urllib.request.urlopen", return_value=_response(klines)):
        closes = perp_market_data.fetch_closes("BTCUSDT", "1h", 2)
    assert closes == [1.05, 1.15]


def test_fetch_closes_returns_none_on_empty_response():
    with patch("urllib.request.urlopen", return_value=_response([])):
        assert perp_market_data.fetch_closes("BTCUSDT", "1h", 2) is None


def test_fetch_closes_returns_none_on_non_list_response():
    with patch("urllib.request.urlopen", return_value=_response({"error": "bad symbol"})):
        assert perp_market_data.fetch_closes("BTCUSDT", "1h", 2) is None


def test_fetch_klines_with_time_parses_time_and_close():
    klines = [
        [1000000, "1.0", "1.1", "0.9", "1.05", "100"],
        [2000000, "1.05", "1.2", "1.0", "1.15", "120"],
    ]
    with patch("urllib.request.urlopen", return_value=_response(klines)):
        candles = perp_market_data.fetch_klines_with_time("BTCUSDT", "1h", 2)
    assert candles == [(1000.0, 1.05), (2000.0, 1.15)]


def test_fetch_klines_with_time_returns_none_on_empty_response():
    with patch("urllib.request.urlopen", return_value=_response([])):
        assert perp_market_data.fetch_klines_with_time("BTCUSDT", "1h", 2) is None


def test_fetch_latest_funding_rate_parses_value():
    with patch("urllib.request.urlopen", return_value=_response([{"fundingRate": "0.0001"}])):
        rate = perp_market_data.fetch_latest_funding_rate("BTCUSDT")
    assert rate == 0.0001


def test_fetch_latest_funding_rate_returns_none_on_empty_list():
    with patch("urllib.request.urlopen", return_value=_response([])):
        assert perp_market_data.fetch_latest_funding_rate("BTCUSDT") is None


def test_fetch_mark_price_parses_value():
    with patch("urllib.request.urlopen", return_value=_response({"markPrice": "65000.5"})):
        price = perp_market_data.fetch_mark_price("BTCUSDT")
    assert price == 65000.5


def test_fetch_mark_price_returns_none_on_malformed_response():
    with patch("urllib.request.urlopen", return_value=_response({"unexpected": True})):
        assert perp_market_data.fetch_mark_price("BTCUSDT") is None
