"""perp_market_data.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

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


def test_fetch_ohlc_with_time_parses_all_fields():
    klines = [
        [1000000, "1.0", "1.1", "0.9", "1.05", "100"],
    ]
    with patch("urllib.request.urlopen", return_value=_response(klines)):
        candles = perp_market_data.fetch_ohlc_with_time("BTCUSDT", "1h", 1)
    assert candles == [(1000.0, 1.0, 1.1, 0.9, 1.05)]


def test_fetch_ohlc_with_time_returns_none_on_empty_response():
    with patch("urllib.request.urlopen", return_value=_response([])):
        assert perp_market_data.fetch_ohlc_with_time("BTCUSDT", "1h", 2) is None


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


def test_fetch_funding_rate_history_parses_time_and_rate():
    entries = [
        {"fundingTime": 1000000, "fundingRate": "0.0001"},
        {"fundingTime": 1028800000, "fundingRate": "-0.0002"},
    ]
    with patch("urllib.request.urlopen", return_value=_response(entries)):
        history = perp_market_data.fetch_funding_rate_history("BTCUSDT", 0, 2_000_000_000)
    assert history == [(1000.0, 0.0001), (1028800.0, -0.0002)]


def test_fetch_funding_rate_history_returns_none_on_empty_response():
    with patch("urllib.request.urlopen", return_value=_response([])):
        assert perp_market_data.fetch_funding_rate_history("BTCUSDT", 0, 2_000_000_000) is None


def test_fetch_funding_rate_history_returns_none_on_non_list_response():
    with patch("urllib.request.urlopen", return_value=_response({"error": "bad symbol"})):
        assert perp_market_data.fetch_funding_rate_history("BTCUSDT", 0, 2_000_000_000) is None


def test_estimate_funding_cost_pct_sums_rates_within_window():
    entries = [{"fundingTime": 500000, "fundingRate": "0.0001"}]
    with patch("urllib.request.urlopen", return_value=_response(entries)):
        cost = perp_market_data.estimate_funding_cost_pct("BTCUSDT", opened_at=0.0, closed_at=1000.0, leverage=3.0)
    assert cost == pytest.approx(0.0001 * 100 * 3.0)


def test_estimate_funding_cost_pct_returns_zero_on_fetch_failure():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        cost = perp_market_data.estimate_funding_cost_pct("BTCUSDT", opened_at=0.0, closed_at=1000.0, leverage=3.0)
    assert cost == 0.0


def test_fetch_funding_rate_history_paginates_when_page_is_full():
    # 1ページ目がちょうど1000件(=まだ続きがある可能性)、2ページ目は2件で終わる
    first_page = [{"fundingTime": i * 1000, "fundingRate": "0.0001"} for i in range(1000)]
    second_page = [
        {"fundingTime": 1000000, "fundingRate": "0.0002"},
        {"fundingTime": 1001000, "fundingRate": "0.0003"},
    ]
    with patch("urllib.request.urlopen", side_effect=[_response(first_page), _response(second_page)]) as mock_urlopen:
        history = perp_market_data.fetch_funding_rate_history("BTCUSDT", 0, 2_000_000_000)
    assert history is not None
    assert len(history) == 1002
    assert history[-1] == (1001.0, 0.0003)
    assert mock_urlopen.call_count == 2
