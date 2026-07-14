"""pumpportal_client.py の単体テスト。実際のWebSocket接続はしない
(接続していない状態でのsubscribe/unsubscribeが安全に無視されることのみ検証する。
messages()自体の接続・再接続ループは実サーバーが必要なため単体テスト対象外)。
"""
from __future__ import annotations

import pytest

from pumpportal_client import PumpPortalClient


@pytest.mark.asyncio
async def test_subscribe_token_trade_noop_when_disconnected():
    client = PumpPortalClient()
    # 接続していない(self._ws is None)状態で呼んでも例外にならない。
    await client.subscribe_token_trade(["mint1", "mint2"])


@pytest.mark.asyncio
async def test_unsubscribe_token_trade_noop_when_disconnected():
    client = PumpPortalClient()
    await client.unsubscribe_token_trade(["mint1"])


@pytest.mark.asyncio
async def test_subscribe_token_trade_noop_when_empty_list():
    client = PumpPortalClient()
    await client.subscribe_token_trade([])
