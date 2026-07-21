"""特定の1人が特定の言葉を発言したら固定の返信を送るだけの、ちょっとした
遊び機能(ARTEMIS Phantom Sniper本体のスキャナー機能とは完全に独立した
別プロセス・別Discordアプリ。動いていなくても本体には一切影響しない)。

Webhook(送信専用)では他人の発言を検知できないため、メッセージ内容を
読み取れる権限を持つ普通のDiscord Botとして実装している(discord.pyを
使用。本体側はurllib.requestのみで完結させる方針だが、Gatewayの接続・
ハートビート・再接続を自前実装するのは車輪の再発明でバグの温床になる
ため、この遊び機能に限り例外的にdiscord.pyへ依存する)。

CHAT_REPLY_ENABLED=false、またはDISCORD_BOT_TOKEN未設定の場合は何もせず
終了する(既定OFF)。実行方法・Bot作成手順はREADME.md参照。
"""
from __future__ import annotations

import logging

import discord

import config
from logger import setup_logger

logger = logging.getLogger("phantom_sniper")


def _find_reply(
    message_author_id: int, message_content: str, target_user_id: int, pairs: list[tuple[str, str]]
) -> str | None:
    """指定した相手の発言に、登録された言葉のいずれかが含まれていれば、
    対応する返信を返す(部分一致、登録順で最初に一致したものを優先)。
    どれにも一致しなければNoneを返す。

    Discord APIやネットワークに依存しない純粋関数にして、単体テストできる
    ようにしている(discord_notifier.py等と同じ設計方針)。
    """
    if message_author_id != target_user_id:
        return None
    for trigger_word, reply_message in pairs:
        if trigger_word and trigger_word in message_content:
            return reply_message
    return None


class ChatReplyBot(discord.Client):
    async def on_ready(self) -> None:
        logger.info("chat_reply_bot: ログインしました user=%s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if self.user is not None and message.author.id == self.user.id:
            return
        reply = _find_reply(
            message.author.id, message.content, config.CHAT_REPLY_TARGET_USER_ID, config.CHAT_REPLY_PAIRS
        )
        if reply:
            await message.channel.send(reply)


def main() -> None:
    setup_logger()

    if not config.CHAT_REPLY_ENABLED or not config.DISCORD_BOT_TOKEN:
        logger.warning(
            "chat_reply_bot: CHAT_REPLY_ENABLED=falseまたはDISCORD_BOT_TOKEN未設定のため起動しません"
        )
        return
    if not config.CHAT_REPLY_TARGET_USER_ID:
        logger.warning("chat_reply_bot: CHAT_REPLY_TARGET_USER_ID未設定のため起動しません")
        return
    if not config.CHAT_REPLY_PAIRS:
        logger.warning("chat_reply_bot: CHAT_REPLY_TRIGGER_WORD_1等が1件も設定されていないため起動しません")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    client = ChatReplyBot(intents=intents)
    client.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
