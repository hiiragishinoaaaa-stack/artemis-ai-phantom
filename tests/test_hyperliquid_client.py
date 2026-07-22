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


def _mock_info(coin_to_asset=None, asset_to_sz_decimals=None):
    info = MagicMock()
    info.coin_to_asset = coin_to_asset or {"BTC": 0}
    info.asset_to_sz_decimals = asset_to_sz_decimals or {0: 5}
    return info


def test_round_price_matches_sdk_precision_rule():
    info = _mock_info(coin_to_asset={"BTC": 0}, asset_to_sz_decimals={0: 5})
    # 有効数字5桁+ (6 - szDecimals)桁の小数、というSDKの丸めルールと一致するか
    assert hyperliquid_client._round_price(info, "BTC", 65432.987) == 65433.0


def test_parse_post_only_response_resting():
    response = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 42}}]}}}
    result = hyperliquid_client._parse_post_only_response(response)
    assert result.success is True
    assert result.resting is True
    assert result.oid == 42
    assert result.filled is False


def test_parse_post_only_response_filled():
    response = {
        "status": "ok",
        "response": {"data": {"statuses": [{"filled": {"totalSz": "0.01", "avgPx": "65000.0"}}]}},
    }
    result = hyperliquid_client._parse_post_only_response(response)
    assert result.success is True
    assert result.filled is True
    assert result.avg_price == 65000.0


def test_parse_post_only_response_error():
    response = {"status": "ok", "response": {"data": {"statuses": [{"error": "Post only order would have matched"}]}}}
    result = hyperliquid_client._parse_post_only_response(response)
    assert result.success is False
    assert "Post only" in result.error


def test_parse_post_only_response_unexpected_shape():
    assert hyperliquid_client._parse_post_only_response("not a dict").success is False
    assert hyperliquid_client._parse_post_only_response({"status": "err"}).success is False


def test_place_post_only_buy_returns_failure_when_client_unavailable():
    with patch("hyperliquid_client._get_exchange", return_value=None):
        result = hyperliquid_client.place_post_only_buy("BTC", 0.01, 65000.0)
    assert result.success is False


def test_place_post_only_buy_success_resting():
    mock_exchange = MagicMock()
    mock_exchange.order.return_value = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 99}}]}}}
    info = _mock_info()
    with patch("hyperliquid_client._get_exchange", return_value=mock_exchange), patch(
        "hyperliquid_client._get_info", return_value=info
    ):
        result = hyperliquid_client.place_post_only_buy("BTC", 0.01, 65000.0)
    assert result.success is True
    assert result.resting is True
    assert result.oid == 99
    mock_exchange.order.assert_called_once()
    args, kwargs = mock_exchange.order.call_args
    assert args[0] == "BTC"
    assert args[1] is True
    assert kwargs["order_type"] == {"limit": {"tif": "Alo"}}


def test_place_post_only_sell_uses_reduce_only():
    mock_exchange = MagicMock()
    mock_exchange.order.return_value = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 5}}]}}}
    info = _mock_info()
    with patch("hyperliquid_client._get_exchange", return_value=mock_exchange), patch(
        "hyperliquid_client._get_info", return_value=info
    ):
        result = hyperliquid_client.place_post_only_sell("BTC", 0.01, 66000.0)
    assert result.success is True
    args, kwargs = mock_exchange.order.call_args
    assert args[1] is False  # is_buy=False(売り)
    assert kwargs["reduce_only"] is True


def test_parse_order_status_response_filled():
    response = {"status": "order", "order": {"status": "filled", "order": {"avgPx": "65000.0", "sz": "0.01"}}}
    result = hyperliquid_client._parse_order_status_response(response)
    assert result.found is True
    assert result.is_filled is True
    assert result.avg_price == 65000.0
    assert result.filled_size == 0.01


def test_parse_order_status_response_open():
    response = {"status": "order", "order": {"status": "open", "order": {}}}
    result = hyperliquid_client._parse_order_status_response(response)
    assert result.found is True
    assert result.is_open is True
    assert result.is_filled is False


def test_parse_order_status_response_canceled():
    response = {"status": "order", "order": {"status": "canceled", "order": {}}}
    result = hyperliquid_client._parse_order_status_response(response)
    assert result.found is True
    assert result.is_filled is False
    assert result.is_open is False


def test_parse_order_status_response_unexpected_shape():
    assert hyperliquid_client._parse_order_status_response("not a dict").found is False
    assert hyperliquid_client._parse_order_status_response({"status": "err"}).found is False
    assert hyperliquid_client._parse_order_status_response({"status": "order"}).found is False


def test_query_order_status_returns_not_found_when_wallet_unavailable():
    with patch("hyperliquid_client._get_info", return_value=MagicMock()), patch(
        "hyperliquid_wallet.get_account", return_value=None
    ):
        result = hyperliquid_client.query_order_status(42)
    assert result.found is False


def test_query_order_status_success():
    mock_info = MagicMock()
    mock_info.query_order_by_oid.return_value = {
        "status": "order",
        "order": {"status": "filled", "order": {"avgPx": "65000.0", "sz": "0.01"}},
    }
    mock_account = MagicMock()
    mock_account.address = "0xabc"
    with patch("hyperliquid_client._get_info", return_value=mock_info), patch(
        "hyperliquid_wallet.get_account", return_value=mock_account
    ):
        result = hyperliquid_client.query_order_status(42)
    assert result.found is True
    assert result.is_filled is True
    mock_info.query_order_by_oid.assert_called_once_with("0xabc", 42)


def test_cancel_order_returns_false_when_exchange_unavailable():
    with patch("hyperliquid_client._get_exchange", return_value=None):
        assert hyperliquid_client.cancel_order("BTC", 42) is False


def test_cancel_order_success():
    mock_exchange = MagicMock()
    mock_exchange.cancel.return_value = {"status": "ok"}
    with patch("hyperliquid_client._get_exchange", return_value=mock_exchange):
        assert hyperliquid_client.cancel_order("BTC", 42) is True
    mock_exchange.cancel.assert_called_once_with("BTC", 42)


def test_cancel_order_handles_exception():
    mock_exchange = MagicMock()
    mock_exchange.cancel.side_effect = RuntimeError("network down")
    with patch("hyperliquid_client._get_exchange", return_value=mock_exchange):
        assert hyperliquid_client.cancel_order("BTC", 42) is False
