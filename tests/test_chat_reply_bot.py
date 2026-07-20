"""chat_reply_bot.py の単体テスト(ネットワーク非依存の判定ロジックのみ)。"""
from __future__ import annotations

from chat_reply_bot import _should_reply


def test_replies_when_target_user_says_trigger_word():
    assert _should_reply(111, "そろそろおやすみするわ", target_user_id=111, trigger_word="おやすみ") is True


def test_does_not_reply_to_other_users():
    assert _should_reply(222, "おやすみ", target_user_id=111, trigger_word="おやすみ") is False


def test_does_not_reply_when_trigger_word_absent():
    assert _should_reply(111, "こんにちは", target_user_id=111, trigger_word="おやすみ") is False


def test_matches_trigger_word_as_substring():
    assert _should_reply(111, "みんなおやすみー", target_user_id=111, trigger_word="おやすみ") is True


def test_does_not_reply_when_trigger_word_empty():
    assert _should_reply(111, "おやすみ", target_user_id=111, trigger_word="") is False
