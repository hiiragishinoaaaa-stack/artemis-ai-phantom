"""famous_name.py の単体テスト。

実際に候補チャンネルへ流れてしまった「Doge Head Coin」が起点。過去に観測した
名前と一致しない**初出の便乗名**は、token_name_history.py の重複検出では
捕まえられない。
"""
from __future__ import annotations

import config
import famous_name


def test_catches_the_case_that_actually_slipped_through():
    assert famous_name.matched_term("Doge Head Coin", "DHC") == "doge"


def test_ignores_spacing_and_case():
    assert famous_name.is_derivative("DOGEHEAD", "X")
    assert famous_name.is_derivative("d o g e head", "X")
    assert famous_name.is_derivative("Doge-Head_Coin!", "X")


def test_matches_on_the_ticker_too():
    assert famous_name.is_derivative("Something Else", "PEPE2")


def test_leaves_unrelated_names_alone():
    for name in ("Artemis", "Solar Panel", "MoonRabbit", "Quantum Leap", "Robot Cat"):
        assert not famous_name.is_derivative(name, "XYZ"), name


def test_short_terms_are_never_used(monkeypatch):
    """『sol』のような短い語を入れても、無関係な名前を巻き込まないこと。

    入れてしまうと "Solar"、"Console"、"Absolute" 等が全部便乗扱いになる。
    """
    monkeypatch.setattr(config, "FAMOUS_COIN_NAMES", ("sol", "btc", "doge"))
    assert famous_name.famous_terms() == ["doge"]
    assert not famous_name.is_derivative("Solar Panel", "SOLAR")


def test_empty_name_and_symbol_do_not_match():
    assert not famous_name.is_derivative("", "")


def test_term_list_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "FAMOUS_COIN_NAMES", ("artemis",))
    assert famous_name.is_derivative("Artemis Prime", "AP")
    assert not famous_name.is_derivative("Doge Head Coin", "DHC")
