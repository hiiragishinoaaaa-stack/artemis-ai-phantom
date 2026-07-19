"""solana_client.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import config
import solana_client

_MINT = "TargetMint111"


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(config, "SOLANA_RPC_URL", "https://rpc.example.com")
    monkeypatch.setattr(config, "SOLANA_MAX_SIGNATURES_PER_CHECKPOINT", 20)
    monkeypatch.setattr(config, "SOLANA_RPC_CONCURRENCY", 5)


def _response(payload) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _dispatcher(handlers: dict):
    """methodごとにハンドラ(callable(body)->payload、または例外インスタンス)を振り分ける
    urlopenのside_effectを作る。"""

    def _urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        method = body["method"]
        handler = handlers[method]
        if isinstance(handler, BaseException):
            raise handler
        return _response(handler(body))

    return _urlopen


def _sig_entry(signature: str, block_time: float, err=None) -> dict:
    return {"signature": signature, "blockTime": block_time, "err": err}


def _tx_with_buyer(mint: str, owner: str, pre: float, post: float) -> dict:
    return {
        "meta": {
            "preTokenBalances": [{"mint": mint, "owner": owner, "uiTokenAmount": {"uiAmount": pre}}],
            "postTokenBalances": [{"mint": mint, "owner": owner, "uiTokenAmount": {"uiAmount": post}}],
        }
    }


# --- _rpc_call ---


def test_rpc_call_returns_result_on_success():
    with patch("urllib.request.urlopen", side_effect=_dispatcher({"foo": lambda b: {"result": {"ok": True}}})):
        assert solana_client._rpc_call("foo", []) == {"ok": True}


def test_rpc_call_returns_none_on_network_error():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert solana_client._rpc_call("foo", []) is None


def test_rpc_call_returns_none_when_response_has_error_key():
    with patch(
        "urllib.request.urlopen",
        side_effect=_dispatcher({"foo": lambda b: {"error": {"code": -32000, "message": "boom"}}}),
    ):
        assert solana_client._rpc_call("foo", []) is None


# --- _recent_signatures ---


def test_recent_signatures_filters_by_time_and_error():
    now = 1_700_000_000.0
    handlers = {
        "getSignaturesForAddress": lambda b: {
            "result": [
                _sig_entry("recent_ok", now - 10),
                _sig_entry("too_old", now - 1000),
                _sig_entry("failed_tx", now - 5, err={"InstructionError": []}),
                _sig_entry("no_blocktime", None),
            ]
        }
    }
    with patch("urllib.request.urlopen", side_effect=_dispatcher(handlers)):
        result = solana_client._recent_signatures("Pool1", since_unix=now - 60)
    assert result == ["recent_ok"]


def test_recent_signatures_returns_none_when_rpc_fails():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert solana_client._recent_signatures("Pool1", since_unix=0) is None


def test_recent_signatures_returns_empty_list_when_none_match():
    handlers = {"getSignaturesForAddress": lambda b: {"result": []}}
    with patch("urllib.request.urlopen", side_effect=_dispatcher(handlers)):
        assert solana_client._recent_signatures("Pool1", since_unix=0) == []


# --- _buyer_owner_from_transaction ---


def test_buyer_owner_from_transaction_detects_positive_delta():
    tx = _tx_with_buyer(_MINT, "WalletA", pre=0.0, post=100.0)
    assert solana_client._buyer_owner_from_transaction(tx, _MINT) == "WalletA"


def test_buyer_owner_from_transaction_returns_none_for_sell():
    tx = _tx_with_buyer(_MINT, "WalletA", pre=100.0, post=0.0)
    assert solana_client._buyer_owner_from_transaction(tx, _MINT) is None


def test_buyer_owner_from_transaction_ignores_other_mints():
    tx = _tx_with_buyer("OtherMint", "WalletA", pre=0.0, post=100.0)
    assert solana_client._buyer_owner_from_transaction(tx, _MINT) is None


def test_buyer_owner_from_transaction_handles_missing_meta():
    assert solana_client._buyer_owner_from_transaction({}, _MINT) is None


# --- count_unique_buyers ---


def test_count_unique_buyers_returns_none_when_pool_address_missing():
    assert solana_client.count_unique_buyers("", _MINT, since_unix=0) is None


def test_count_unique_buyers_returns_none_when_signatures_fetch_fails():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert solana_client.count_unique_buyers("Pool1", _MINT, since_unix=0) is None


def test_count_unique_buyers_returns_zero_when_no_recent_signatures():
    handlers = {"getSignaturesForAddress": lambda b: {"result": []}}
    with patch("urllib.request.urlopen", side_effect=_dispatcher(handlers)):
        assert solana_client.count_unique_buyers("Pool1", _MINT, since_unix=0) == 0


def test_count_unique_buyers_counts_distinct_wallets_only(monkeypatch):
    now = 1_700_000_000.0

    def _signatures(body):
        return {
            "result": [
                _sig_entry("sig1", now - 5),
                _sig_entry("sig2", now - 4),
                _sig_entry("sig3", now - 3),
            ]
        }

    def _get_transaction(body):
        signature = body["params"][0]
        tx_by_sig = {
            "sig1": _tx_with_buyer(_MINT, "WalletA", 0.0, 10.0),
            "sig2": _tx_with_buyer(_MINT, "WalletB", 0.0, 5.0),
            "sig3": _tx_with_buyer(_MINT, "WalletA", 10.0, 20.0),  # 同じウォレットの2回目の買い
        }
        return {"result": tx_by_sig[signature]}

    handlers = {"getSignaturesForAddress": _signatures, "getTransaction": _get_transaction}
    with patch("urllib.request.urlopen", side_effect=_dispatcher(handlers)):
        count = solana_client.count_unique_buyers("Pool1", _MINT, since_unix=now - 60)

    assert count == 2  # WalletAは2回買ってるが1人として数える


def test_count_unique_buyers_skips_failed_transaction_fetches():
    now = 1_700_000_000.0

    def _signatures(body):
        return {"result": [_sig_entry("sig1", now - 5), _sig_entry("sig2", now - 4)]}

    def _get_transaction(body):
        signature = body["params"][0]
        if signature == "sig1":
            return {"error": {"message": "not found"}}
        return {"result": _tx_with_buyer(_MINT, "WalletB", 0.0, 5.0)}

    handlers = {"getSignaturesForAddress": _signatures, "getTransaction": _get_transaction}
    with patch("urllib.request.urlopen", side_effect=_dispatcher(handlers)):
        count = solana_client.count_unique_buyers("Pool1", _MINT, since_unix=now - 60)

    assert count == 1
