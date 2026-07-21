"""chat_reply_bot.py の単体テスト(ネットワーク非依存の判定ロジックのみ)。"""
from __future__ import annotations

from chat_reply_bot import _find_reply

_PAIRS = [
    ("おはよう", "おはよー!♥️"),
    ("おやすみ", "おやすみ~"),
    ("可愛い", "ありがとー!!"),
]


def test_replies_when_target_user_says_trigger_word():
    assert _find_reply(111, "そろそろおやすみするわ", target_user_id=111, pairs=_PAIRS) == "おやすみ~"


def test_does_not_reply_to_other_users():
    assert _find_reply(222, "おやすみ", target_user_id=111, pairs=_PAIRS) is None


def test_returns_none_when_no_trigger_word_matches():
    assert _find_reply(111, "こんにちは", target_user_id=111, pairs=_PAIRS) is None


def test_matches_trigger_word_as_substring():
    assert _find_reply(111, "ノルンは今日も可愛いねw", target_user_id=111, pairs=_PAIRS) == "ありがとー!!"


def test_returns_none_when_no_pairs_configured():
    assert _find_reply(111, "おやすみ", target_user_id=111, pairs=[]) is None


def test_ignores_pairs_with_empty_trigger_word():
    assert _find_reply(111, "hello", target_user_id=111, pairs=[("", "should not match")]) is None


def test_first_matching_pair_wins_when_message_contains_multiple_trigger_words():
    assert _find_reply(111, "おはよう、そして可愛い", target_user_id=111, pairs=_PAIRS) == "おはよー!♥️"
