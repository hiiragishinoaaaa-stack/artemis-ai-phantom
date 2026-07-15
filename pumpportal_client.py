"""PumpPortalのWebSocket API(既定: wss://pumpportal.fun/api/data)への接続。

無料・APIキー不要の公開WebSocketの範囲内だけを使う。以下の2つを購読する:
    - subscribeNewToken: 新規トークン作成イベント(無料)
    - subscribeMigration: トークンがpump.fun内のボンディングカーブから
      Raydium等の実際のDEXへ"卒業"した瞬間のイベント(無料)

個別トークンの売買イベント(subscribeTokenTrade/subscribeAccountTrade)は
PumpPortal公式のAPIキー+SOL入りウォレットが必要な従量課金機能のため、
このbotでは使わない(2026-07、無料運用の方針としてsubscribeMigration+
DexScreenerの組み合わせに切り替えた。dexscreener_client.py参照)。
このbotは読み取り専用の購読しか使わない(実際の取引実行は一切行わない
ため、取引用のAPIキー・ウォレット連携は不要)。

接続が切れた場合はconfig.RECONNECT_DELAY_SECONDS待ってから自動的に
再接続する(subscribeNewToken/subscribeMigrationはどちらも「全量」の
購読で個別キー指定が不要なため、再接続時に送り直すだけで元通りになる)。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import websockets

import config

logger = logging.getLogger("phantom_sniper")


class PumpPortalClient:
    """PumpPortal WebSocketへの接続を管理し、受信メッセージをdictとして流すクラス。"""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or config.PUMPPORTAL_WS_URL
        self._ws: websockets.ClientConnection | None = None

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
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
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
