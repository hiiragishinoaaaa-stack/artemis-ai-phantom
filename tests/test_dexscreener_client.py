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


def test_fetch_best_pair_excludes_pumpfun_bonding_curve_pair():
    """卒業前のpump.funボンディングカーブ自体のペア(dexId=pumpfun)は、
    流動性が高くても除外し、卒業後の実際のDEXペアだけを対象にする
    (2026-07判明。同じmintに両方のペアが並存し得るため)。"""
    payload = {
        "pairs": [
            {"chainId": "solana", "dexId": "pumpfun", "liquidity": {"usd": 99999.0}, "url": "bonding-curve"},
            {"chainId": "solana", "dexId": "pumpswap", "liquidity": {"usd": 100.0}, "url": "post-migration"},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        pair = dexscreener_client.fetch_best_pair("MINT1")
        assert pair["url"] == "post-migration"


def test_fetch_best_pair_returns_none_when_only_pumpfun_pair_exists():
    payload = {"pairs": [{"chainId": "solana", "dexId": "pumpfun", "liquidity": {"usd": 500.0}, "url": "x"}]}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        assert dexscreener_client.fetch_best_pair("MINT1") is None


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


def test_best_pair_from_ignores_pairs_of_other_tokens():
    """複数mintを一度に問い合わせると、別トークンのペアが同じ応答に混ざる。"""
    pairs = [
        {"chainId": "solana", "dexId": "raydium", "baseToken": {"address": "OTHER"},
         "liquidity": {"usd": 999999}},
        {"chainId": "solana", "dexId": "raydium", "baseToken": {"address": "MINE"},
         "liquidity": {"usd": 100}},
    ]
    best = dexscreener_client._best_pair_from(pairs, "MINE")
    assert best["baseToken"]["address"] == "MINE"


def test_best_pair_from_still_excludes_the_bonding_curve_pair():
    pairs = [
        {"chainId": "solana", "dexId": "pumpfun", "baseToken": {"address": "MINE"},
         "liquidity": {"usd": 999999}},
        {"chainId": "solana", "dexId": "raydium", "baseToken": {"address": "MINE"},
         "liquidity": {"usd": 100}},
    ]
    assert dexscreener_client._best_pair_from(pairs, "MINE")["dexId"] == "raydium"


def test_market_cap_of_treats_a_missing_value_as_zero():
    assert dexscreener_client.market_cap_of(None) == 0.0
    assert dexscreener_client.market_cap_of({}) == 0.0
    assert dexscreener_client.market_cap_of({"fdv": 1234.0}) == 1234.0
    assert dexscreener_client.market_cap_of({"marketCap": 50.0, "fdv": 1234.0}) == 50.0


def test_fetch_best_pairs_splits_each_token_out_of_one_response():
    payload = {
        "pairs": [
            {"chainId": "solana", "dexId": "raydium", "baseToken": {"address": "A"},
             "liquidity": {"usd": 10}, "marketCap": 1000.0},
            {"chainId": "solana", "dexId": "raydium", "baseToken": {"address": "B"},
             "liquidity": {"usd": 10}, "marketCap": 2000.0},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        results = dexscreener_client.fetch_best_pairs(["A", "B", "C"])

    assert urlopen.call_count == 1  # 3件を1リクエストにまとめている
    assert set(results) == {"A", "B"}  # 見つからなかったCはキーごと含めない
    assert dexscreener_client.market_cap_of(results["B"]) == 2000.0


def test_fetch_best_pairs_batches_requests_at_the_documented_limit():
    with patch("urllib.request.urlopen", return_value=_response({"pairs": []})) as urlopen:
        dexscreener_client.fetch_best_pairs([f"M{i}" for i in range(61)])
    assert urlopen.call_count == 3  # 30 + 30 + 1


def test_fetch_best_pairs_survives_a_failed_batch():
    """1回分の取得に失敗しても例外は投げず、残りの取得を続ける。"""
    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        assert dexscreener_client.fetch_best_pairs(["A"]) == {}
