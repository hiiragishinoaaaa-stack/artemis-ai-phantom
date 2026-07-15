"""dexscreener_client.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import dexscreener_client


def _response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_fetch_best_pair_returns_none_when_no_pairs():
    with patch("urllib.request.urlopen", return_value=_response({"pairs": []})):
        assert dexscreener_client.fetch_best_pair("MINT1") is None


def test_fetch_best_pair_returns_none_when_pairs_key_missing():
    with patch("urllib.request.urlopen", return_value=_response({})):
        assert dexscreener_client.fetch_best_pair("MINT1") is None


def test_fetch_best_pair_filters_to_solana_chain():
    payload = {
        "pairs": [
            {"chainId": "ethereum", "liquidity": {"usd": 999999.0}},
            {"chainId": "solana", "liquidity": {"usd": 500.0}, "url": "https://dexscreener.com/solana/x"},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        pair = dexscreener_client.fetch_best_pair("MINT1")
        assert pair is not None
        assert pair["chainId"] == "solana"


def test_fetch_best_pair_picks_highest_liquidity_among_solana_pairs():
    payload = {
        "pairs": [
            {"chainId": "solana", "liquidity": {"usd": 100.0}, "url": "low"},
            {"chainId": "solana", "liquidity": {"usd": 9000.0}, "url": "high"},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        pair = dexscreener_client.fetch_best_pair("MINT1")
        assert pair["url"] == "high"


def test_fetch_best_pair_returns_none_on_network_error():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert dexscreener_client.fetch_best_pair("MINT1") is None


def test_fetch_best_pair_returns_none_on_invalid_json():
    resp = MagicMock()
    resp.read.return_value = b"not json"
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=resp):
        assert dexscreener_client.fetch_best_pair("MINT1") is None
