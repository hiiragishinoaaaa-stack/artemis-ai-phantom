"""grid_live_trader.py(⚠️実際の資金を動かす)用のHyperliquidウォレット
秘密鍵の読み込み。

HyperliquidはEthereum互換の署名方式を使うため、Solana用のwallet.py
(trade_executor.py/ミームコイン用)とは全く別の鍵形式(0xで始まる16進数の
秘密鍵)になる。HYPERLIQUID_PRIVATE_KEY(.envにのみ設定、絶対にリポジトリへ
コミットしない)には、Hyperliquidの取引に使うウォレットの秘密鍵をそのまま
設定する。この値を持つ者はウォレットの資金を全て動かせるため、取り扱いに
注意すること(ログには絶対に出力しない。アドレスはログに出しても問題ない)。

このモジュール自体はネットワーク通信を一切行わない(鍵の読み込みのみ)。
"""
from __future__ import annotations

import logging

from eth_account import Account
from eth_account.signers.local import LocalAccount

import config

logger = logging.getLogger("phantom_sniper")

_cached_account: LocalAccount | None = None
_load_attempted = False


def get_account() -> LocalAccount | None:
    """config.HYPERLIQUID_PRIVATE_KEYからLocalAccountを読み込む(1プロセスにつき1回だけ)。

    未設定、または形式が不正な場合はNoneを返す(呼び出し側はHyperliquidへの
    実発注を無効化すること。grid_live_trader.py参照)。
    """
    global _cached_account, _load_attempted
    if _load_attempted:
        return _cached_account

    _load_attempted = True
    raw = config.HYPERLIQUID_PRIVATE_KEY
    if not raw:
        return None
    try:
        _cached_account = Account.from_key(raw)
    except Exception as exc:  # noqa: BLE001 - 鍵の値そのものはログに出さない
        logger.error("hyperliquid_wallet: HYPERLIQUID_PRIVATE_KEYの読み込みに失敗しました(形式を確認してください): %s", exc)
        _cached_account = None
    return _cached_account


def address_str() -> str:
    """ウォレットアドレスを文字列で返す(ログ表示用、未設定なら空文字)。"""
    account = get_account()
    return account.address if account is not None else ""
