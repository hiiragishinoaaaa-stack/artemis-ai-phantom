"""hyperliquid_client.py の単体テスト。Hyperliquid SDKへの実際のネットワーク
送信はモックする。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import hyperliquid_client
from hyperliquid_client import OrderResult, _parse_order_response, to_hyperliquid_symbol


def test_to_hyperliquid_symbol_strips_usdt_suffix():
    assert to_hyperliquid_symbol("BTCUSDT") == "BTC"
    assert to_hyperliquid_symbol("ETHUSDT") == "ETH"


def test_to_hyperliquid_symbol_passthrough_when_no_usdt_suffix():
    assert to_hyperliquid_symbol("BTC") == "BTC"


def test_parse_order_response_success():
    response = {
        "status": "ok",
        "response": {"data": {"statuses": [{"filled": {"totalSz": "0.01", "avgPx": "65000.5"}}]}},
    }
    result = _parse_order_response(response)
    assert result.success is True
    assert result.filled_size == 0.01
    assert result.avg_price == 65000.5


def test_parse_order_response_error_status_in_statuses():
    response = {"status": "ok", "response": {"data": {"statuses": [{"error": "Insufficient margin"}]}}}
    result = _parse_order_response(response)
    assert result.success is False
    assert "Insufficient margin" in result.error


def test_parse_order_response_top_level_error():
    response = {"status": "err", "response": "Invalid signature"}
    result = _parse_order_response(response)
    assert result.success is False


def test_parse_order_response_unexpected_shape():
    assert _parse_order_response("not a dict").success is False
    assert _parse_order_response({"status": "ok"}).success is False
    assert _parse_order_response({"status": "ok", "response": {"data": {"statuses": []}}}).success is False


def test_fetch_mid_price_returns_value():
    mock_info = MagicMock()
    mock_info.all_mids.return_value = {"BTC": "65000.5"}
    with patch("hyperliquid_client._get_info", return_value=mock_info):
        assert hyperliquid_client.fetch_mid_price("BTC") == 65000.5


def test_fetch_mid_price_returns_none_when_symbol_missing():
    mock_info = MagicMock()
    mock_info.all_mids.return_value = {"ETH": "3000.0"}
    with patch("hyperliquid_client._get_info", return_value=mock_info):
        assert hyperliquid_client.fetch_mid_price("BTC") is None


def test_fetch_mid_price_returns_none_when_info_unavailable():
    with patch("hyperliquid_client._get_info", return_value=None):
        assert hyperliquid_client.fetch_mid_price("BTC") is None


def test_open_long_returns_failure_when_exchange_unavailable():
    with patch("hyperliquid_client._get_exchange", return_value=None):
        result = hyperliquid_client.open_long("BTC", 0.01, 0.01)
    assert result.success is False


def test_open_long_success():
    mock_exchange = MagicMock()
    mock_exchange.market_open.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"filled": {"totalSz": "0.01", "avgPx": "65000.0"}}]}},
    }
    with patch("hyperliquid_client._get_exchange", return_value=mock_exchange):
        result = hyperliquid_client.open_long("BTC", 0.01, 0.01)
    assert result.success is True
    assert result.avg_price == 65000.0
    mock_exchange.market_open.assert_called_once_with("BTC", True, 0.01, slippage=0.01)


def test_open_long_handles_exception():
    mock_exchange = MagicMock()
    mock_exchange.market_open.side_effect = RuntimeError("network down")
    with patch("hyperliquid_client._get_exchange", return_value=mock_exchange):
        result = hyperliquid_client.open_long("BTC", 0.01, 0.01)
    assert result.success is False
    assert "network down" in result.error


def test_close_long_success():
    mock_exchange = MagicMock()
    mock_exchange.market_close.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"filled": {"totalSz": "0.01", "avgPx": "66000.0"}}]}},
    }
    with patch("hyperliquid_client._get_exchange", return_value=mock_exchange):
        result = hyperliquid_client.close_long("BTC", 0.01, 0.01)
    assert result.success is True
    assert result.avg_price == 66000.0


def test_set_leverage_returns_false_when_exchange_unavailable():
    with patch("hyperliquid_client._get_exchange", return_value=None):
        assert hyperliquid_client.set_leverage("BTC", 3) is False


def test_set_leverage_success():
    mock_exchange = MagicMock()
    with patch("hyperliquid_client._get_exchange", return_value=mock_exchange):
        assert hyperliquid_client.set_leverage("BTC", 3) is True
    mock_exchange.update_leverage.assert_called_once_with(3, "BTC")
