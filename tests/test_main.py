"""main.py の単体テスト(ネットワーク非依存の部分のみ)。"""
from __future__ import annotations

from main import _RecentTokenNames


def test_remember_and_get_round_trip():
    cache = _RecentTokenNames()
    cache.remember("MINT1", "Test Coin", "TEST")
    assert cache.get("MINT1") == ("Test Coin", "TEST")


def test_get_returns_empty_tuple_for_unknown_mint():
    cache = _RecentTokenNames()
    assert cache.get("UNKNOWN") == ("", "")


def test_remember_ignores_entries_with_no_name_and_no_symbol():
    cache = _RecentTokenNames()
    cache.remember("MINT1", "", "")
    assert cache.get("MINT1") == ("", "")


def test_remember_evicts_oldest_when_over_capacity():
    cache = _RecentTokenNames(max_size=2)
    cache.remember("OLD", "Old Coin", "OLD")
    cache.remember("MID", "Mid Coin", "MID")
    cache.remember("NEW", "New Coin", "NEW")  # 上限超過、最古(OLD)が間引かれる

    assert cache.get("OLD") == ("", "")
    assert cache.get("MID") == ("Mid Coin", "MID")
    assert cache.get("NEW") == ("New Coin", "NEW")
