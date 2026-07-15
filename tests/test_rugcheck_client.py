"""rugcheck_client.py の単体テスト。実際のネットワーク送信はモックする。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import rugcheck_client


def _response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_fetch_risk_report_returns_dict_on_success():
    with patch("urllib.request.urlopen", return_value=_response({"score_normalised": 12, "risks": []})):
        report = rugcheck_client.fetch_risk_report("MINT1")
        assert report == {"score_normalised": 12, "risks": []}


def test_fetch_risk_report_returns_none_on_network_error():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert rugcheck_client.fetch_risk_report("MINT1") is None


def test_fetch_risk_report_returns_none_on_invalid_json():
    resp = MagicMock()
    resp.read.return_value = b"not json"
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=resp):
        assert rugcheck_client.fetch_risk_report("MINT1") is None


def test_extract_danger_reason_returns_none_when_no_risks():
    assert rugcheck_client.extract_danger_reason({"risks": []}) is None
    assert rugcheck_client.extract_danger_reason({}) is None


def test_extract_danger_reason_returns_none_when_only_warn_or_info():
    report = {
        "risks": [
            {"name": "Mutable metadata", "level": "warn"},
            {"name": "Low Liquidity", "level": "info"},
        ]
    }
    assert rugcheck_client.extract_danger_reason(report) is None


def test_extract_danger_reason_returns_name_when_danger_present():
    report = {
        "risks": [
            {"name": "Mutable metadata", "level": "warn"},
            {"name": "Single holder ownership", "level": "danger"},
        ]
    }
    assert rugcheck_client.extract_danger_reason(report) == "Single holder ownership"


def test_extract_danger_reason_is_case_insensitive_on_level():
    report = {"risks": [{"name": "Top 10 holders high ownership", "level": "DANGER"}]}
    assert rugcheck_client.extract_danger_reason(report) == "Top 10 holders high ownership"
