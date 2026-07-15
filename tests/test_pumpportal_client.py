"""pumpportal_client.py の単体テスト。

messages()自体の接続・再接続ループ(subscribeNewToken/subscribeMigrationの
送信を含む)は実サーバーが必要なため単体テスト対象外(VPSで実際に動かして
journalctlで確認する)。ここではクライアントが未接続状態で安全に構築
できることだけを確認する。
"""
from __future__ import annotations

from pumpportal_client import PumpPortalClient


def test_client_starts_disconnected():
    client = PumpPortalClient()
    assert client._ws is None


def test_client_accepts_custom_url():
    client = PumpPortalClient(url="wss://example.invalid/api/data")
    assert client._url == "wss://example.invalid/api/data"
