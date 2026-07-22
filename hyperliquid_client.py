"""Hyperliquid公式Python SDK(hyperliquid-python-sdk)を使った、実際の
パーペチュアル注文の実行(grid_live_trader.py用)。

⚠️⚠️⚠️ 実際にオンチェーンで資金を動かすモジュール。Hyperliquidの注文は
EIP-712形式のトランザクション署名が必要で、自前実装すると署名ミスに
よる誤発注・拒否のリスクが高いため、Hyperliquid社自身がメンテナンスする
公式SDKをそのまま使う(jupiter_client.py[Solana/Jupiter]は自前実装だが、
そちらは単純なHTTP POST+solders署名で完結する分リスクが低いため方針が
異なる。Hyperliquidは取引所側のオーダーブックに直接載せる分、より慎重な
実装が必要と判断した)。

market_open/market_close(成行相当、実際にはスリッページ許容付きの
IoC指値注文)を使う。指値(Maker)注文を出して約定を待つ方式ではないため、
perp_grid_backtest.pyで検証したMaker手数料(0.015%)ではなく、より高い
Taker手数料(0.045%、2026-07時点)が適用される点に注意
(README.mdの「パーペチュアル実発注(Hyperliquid、実験的機能)」参照)。

このリポジトリの開発環境には実際に資金の入ったHyperliquidアカウントが
無いため、本番のメインネットに対するエンドツーエンドの検証(実際に
発注してみるところまで)はできていない。少額・テストネットから試すこと。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

import config
import hyperliquid_wallet

logger = logging.getLogger("phantom_sniper")


@dataclass
class OrderResult:
    success: bool
    avg_price: float = 0.0
    filled_size: float = 0.0
    error: str = ""


def to_hyperliquid_symbol(binance_symbol: str) -> str:
    """"BTCUSDT"のようなBinance Futures表記から、Hyperliquidの銘柄表記
    ("BTC")へ変換する(perp_sniper.py・perp_market_data.pyはBinance表記の
    まま使っているため、実発注する直前だけ変換する)。"USDT"で終わらない
    場合はそのまま返す。
    """
    return binance_symbol[: -len("USDT")] if binance_symbol.endswith("USDT") else binance_symbol


def _base_url() -> str:
    return constants.TESTNET_API_URL if config.HYPERLIQUID_USE_TESTNET else constants.MAINNET_API_URL


def _get_exchange() -> Exchange | None:
    account = hyperliquid_wallet.get_account()
    if account is None:
        return None
    try:
        return Exchange(account, base_url=_base_url())
    except Exception as exc:  # noqa: BLE001 - SDK内部の例外型が多岐にわたるため
        logger.error("hyperliquid_client: Exchangeクライアントの初期化に失敗しました: %s", exc)
        return None


def _get_info() -> Info | None:
    try:
        return Info(base_url=_base_url(), skip_ws=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("hyperliquid_client: Infoクライアントの初期化に失敗しました: %s", exc)
        return None


def fetch_mid_price(symbol: str) -> float | None:
    """指定銘柄の現在の中間価格(mid price)を返す(失敗時はNone)。

    symbolはHyperliquidの銘柄表記("BTC"等、Binance Futuresの"BTCUSDT"とは
    異なる)。perp_sniper.py側で変換すること。
    """
    info = _get_info()
    if info is None:
        return None
    try:
        mids = info.all_mids()
        price = mids.get(symbol)
        return float(price) if price is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("hyperliquid_client: mid価格取得に失敗しました symbol=%s: %s", symbol, exc)
        return None


def _parse_order_response(response: object) -> OrderResult:
    """Hyperliquid SDKのorder/market_open/market_closeの戻り値を解釈する。

    想定形式: {"status": "ok", "response": {"data": {"statuses": [{"filled": {...}} | {"error": "..."}]}}}
    想定外の形式が返ってきた場合は、安全側に倒して失敗扱いにする
    (実際に約定したかどうか不明な場合、成功と誤認するよりは失敗として
    扱い、人間が確認できるようにする方が安全なため)。
    """
    if not isinstance(response, dict):
        return OrderResult(success=False, error=f"予期しない応答形式: {response!r}")
    if response.get("status") != "ok":
        return OrderResult(success=False, error=str(response))

    try:
        statuses = response["response"]["data"]["statuses"]
    except (KeyError, TypeError):
        return OrderResult(success=False, error=f"応答の解析に失敗しました: {response!r}")

    if not statuses:
        return OrderResult(success=False, error="statusesが空です")

    status = statuses[0]
    if isinstance(status, dict) and "filled" in status:
        filled = status["filled"]
        try:
            return OrderResult(
                success=True, avg_price=float(filled.get("avgPx", 0.0)), filled_size=float(filled.get("totalSz", 0.0))
            )
        except (TypeError, ValueError):
            return OrderResult(success=False, error=f"filled情報の解析に失敗しました: {filled!r}")
    if isinstance(status, dict) and "error" in status:
        return OrderResult(success=False, error=str(status["error"]))
    return OrderResult(success=False, error=f"未対応の応答: {status!r}")


def set_leverage(symbol: str, leverage: int) -> bool:
    exchange = _get_exchange()
    if exchange is None:
        return False
    try:
        exchange.update_leverage(leverage, symbol)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("hyperliquid_client: レバレッジ設定に失敗しました symbol=%s leverage=%s: %s", symbol, leverage, exc)
        return False


def open_long(symbol: str, size: float, slippage: float) -> OrderResult:
    """成行相当(IoC指値、Taker扱い)でロングを建てる。sizeはコイン数量
    (USD建てではない、呼び出し側でmid価格から換算すること)。
    """
    exchange = _get_exchange()
    if exchange is None:
        return OrderResult(success=False, error="ウォレット秘密鍵が未設定/不正、またはExchange初期化に失敗しました")
    try:
        response = exchange.market_open(symbol, True, size, slippage=slippage)
    except Exception as exc:  # noqa: BLE001
        logger.error("hyperliquid_client: 買い発注に失敗しました symbol=%s size=%s: %s", symbol, size, exc)
        return OrderResult(success=False, error=str(exc))
    return _parse_order_response(response)


def close_long(symbol: str, size: float, slippage: float) -> OrderResult:
    """保有中のロングを成行相当(IoC指値、Taker扱い)で全量(またはsize分)決済する。"""
    exchange = _get_exchange()
    if exchange is None:
        return OrderResult(success=False, error="ウォレット秘密鍵が未設定/不正、またはExchange初期化に失敗しました")
    try:
        response = exchange.market_close(symbol, sz=size, slippage=slippage)
    except Exception as exc:  # noqa: BLE001
        logger.error("hyperliquid_client: 決済発注に失敗しました symbol=%s size=%s: %s", symbol, size, exc)
        return OrderResult(success=False, error=str(exc))
    return _parse_order_response(response)
