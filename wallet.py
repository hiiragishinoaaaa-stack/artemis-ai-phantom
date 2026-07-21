"""自動売買(trade_executor.py)用のSolanaウォレット秘密鍵の読み込み。

⚠️ 実際に資金を動かす機能の一部。SOLANA_WALLET_PRIVATE_KEY(.envにのみ
設定、絶対にリポジトリへコミットしない)には、Phantom等のウォレットアプリの
「秘密鍵をエクスポート」で得られるbase58文字列をそのまま設定する。この値を
持つ者はウォレットの資金を全て動かせるため、取り扱いに注意すること
(ログには絶対に出力しない。公開鍵(アドレス)はログに出しても問題ない)。

このモジュール自体はネットワーク通信を一切行わない(鍵の読み込みのみ)。
"""
from __future__ import annotations

import logging

from solders.keypair import Keypair

import config

logger = logging.getLogger("phantom_sniper")

_cached_keypair: Keypair | None = None
_load_attempted = False


def get_keypair() -> Keypair | None:
    """config.SOLANA_WALLET_PRIVATE_KEYからKeypairを読み込む(1プロセスにつき1回だけ)。

    未設定、または形式が不正な場合はNoneを返す(呼び出し側は自動売買を
    無効化すること。trade_executor.py参照)。
    """
    global _cached_keypair, _load_attempted
    if _load_attempted:
        return _cached_keypair

    _load_attempted = True
    raw = config.SOLANA_WALLET_PRIVATE_KEY
    if not raw:
        return None
    try:
        _cached_keypair = Keypair.from_base58_string(raw)
    except Exception as exc:  # noqa: BLE001 - 鍵の値そのものはログに出さない
        logger.error("wallet: SOLANA_WALLET_PRIVATE_KEYの読み込みに失敗しました(形式を確認してください): %s", exc)
        _cached_keypair = None
    return _cached_keypair


def public_key_str() -> str:
    """公開鍵(ウォレットアドレス)を文字列で返す(ログ表示用、未設定なら空文字)。"""
    keypair = get_keypair()
    return str(keypair.pubkey()) if keypair is not None else ""
