"""PumpPortalのWebSocket API(既定: wss://pumpportal.fun/api/data)への接続。

無料・APIキー不要の公開WebSocket。新規トークン作成イベント
(subscribeNewToken)と、個別トークンの売買イベント(subscribeTokenTrade)を
受信する。このbotは読み取り専用の購読しか使わない(実際の取引実行は
一切行わないため、取引用のAPIキー・ウォレット連携は不要)。

接続が切れた場合はconfig.RECONNECT_DELAY_SECONDS待ってから自動的に
再接続する。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable

import websockets

import config

logger = logging.getLogger("phantom_sniper")


class PumpPortalClient:
    """PumpPortal WebSocketへの接続を管理し、受信メッセージをdictとして流すクラス。"""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or config.PUMPPORTAL_WS_URL
        self._ws: websockets.ClientConnection | None = None
        # 現在購読しているはずのmint一覧(接続が切れて再接続したときに
        # subscribeTokenTradeを送り直すために保持する。詳細はmessages()参照)。
        self._subscribed_mints: set[str] = set()

    async def messages(self) -> AsyncIterator[dict]:
        """接続・再接続を繰り返しながら、受信したメッセージを順にdictとして返す。

        呼び出し側は`async for message in client.messages():`で使う。
        接続が切れても内部で自動再接続するため、このジェネレータ自体は
        (呼び出し側がキャンセルしない限り)止まらない。
        """
        while True:
            try:
                async with websockets.connect(self._url) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    if self._subscribed_mints:
                        # 再接続の場合、切断前に購読していた個別トークンの売買
                        # イベント購読は接続と一緒に失われている(PumpPortal側は
                        # 新しいWebSocket接続を全くの別セッションとして扱う)ため、
                        # ここで送り直さないと、それらのトークンは以後ずっと
                        # buy_count/sell_count が0のまま(=一生スコアが上がらない)
                        # になってしまう。
                        await ws.send(
                            json.dumps(
                                {"method": "subscribeTokenTrade", "keys": list(self._subscribed_mints)}
                            )
                        )
                        logger.info(
                            "pumpportal_client: 再接続に伴い%d件のトークンの売買購読を復元しました",
                            len(self._subscribed_mints),
                        )
                    logger.info("pumpportal_client: 接続しました url=%s", self._url)
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.debug("pumpportal_client: JSONとして解釈できないメッセージを無視しました")
                            continue
                        if isinstance(data, dict):
                            yield data
            except (websockets.exceptions.WebSocketException, OSError) as exc:
                logger.warning(
                    "pumpportal_client: 接続が切断されました(%s秒後に再接続します): %s",
                    config.RECONNECT_DELAY_SECONDS,
                    exc,
                )
            finally:
                self._ws = None
            await asyncio.sleep(config.RECONNECT_DELAY_SECONDS)

    async def subscribe_token_trade(self, mints: Iterable[str]) -> None:
        """指定したmintアドレス群の売買イベント購読を追加する。

        接続の有無に関わらず、まず`_subscribed_mints`へ記録する(現在未接続
        でも、次に接続(再接続)したときにmessages()がまとめて送り直すため)。
        """
        keys = list(mints)
        if not keys:
            return
        self._subscribed_mints.update(keys)
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": keys}))

    async def unsubscribe_token_trade(self, mints: Iterable[str]) -> None:
        """指定したmintアドレス群の売買イベント購読を解除する。"""
        keys = list(mints)
        if not keys:
            return
        self._subscribed_mints.difference_update(keys)
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": keys}))
