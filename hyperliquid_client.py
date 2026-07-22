"""Hyperliquid公式Python SDK(hyperliquid-python-sdk)を使った、実際の
パーペチュアル注文の実行(grid_live_trader.py用)。

⚠️⚠️⚠️ 実際にオンチェーンで資金を動かすモジュール。Hyperliquidの注文は
EIP-712形式のトランザクション署名が必要で、自前実装すると署名ミスに
よる誤発注・拒否のリスクが高いため、Hyperliquid社自身がメンテナンスする
公式SDKをそのまま使う(jupiter_client.py[Solana/Jupiter]は自前実装だが、
そちらは単純なHTTP POST+solders署名で完結する分リスクが低いため方針が
異なる。Hyperliquidは取引所側のオーダーブックに直接載せる分、より慎重な
実装が必要と判断した)。

グリッドの新規建玉(買い)・利確決済(売り)は`place_post_only_buy`/
`place_post_only_sell`(Alo=Add Liquidity Only、板に並べるだけで即座に
約定しない指値注文)を使い、perp_grid_backtest.pyで検証したMaker手数料
(0.015%)が実際に適用されるようにしている。損切り決済だけは緊急性が
あるため`close_long`(成行相当、Taker扱い)のまま残している(損切りは
「早く確実に出る」ことが目的で、指値で約定を待っていると損失が
さらに拡大するリスクがあるため)。

Aloの指値注文は、送信直後に約定せず「板に並んだ(resting)」状態で
返ってくることが多い。約定したかどうかは`query_order_status`で
別途確認する必要がある(grid_live_trader.pyの保留中注文の確認ループ
参照)。

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


@dataclass
class PostOnlyResult:
    """Alo(指値・Maker専用)注文の送信結果。

    successは「注文の送信自体が受理されたか」を表す。即座に約定した
    場合はfilled=True、板に並んだだけの場合はresting=True(oidに
    注文ID)になる。両方Falseならsuccess=Falseのはず(念のため両方
    見て判定すること)。
    """

    success: bool
    filled: bool = False
    resting: bool = False
    oid: int = 0
    avg_price: float = 0.0
    filled_size: float = 0.0
    error: str = ""


@dataclass
class OrderStatusResult:
    """query_order_statusの解釈結果。foundがFalseの場合、注文IDが
    見つからなかった/応答の解析に失敗したことを示す(この場合、
    filled/openのどちらとも断定しない。呼び出し側は「まだ分からない」
    として扱うこと)。
    """

    found: bool
    is_filled: bool = False
    is_open: bool = False
    avg_price: float = 0.0
    filled_size: float = 0.0


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


def _parse_post_only_response(response: object) -> PostOnlyResult:
    """Alo(指値・Maker専用)注文の送信結果を解釈する。

    想定形式は_parse_order_responseと同じ(statuses配列)だが、各要素が
    {"resting": {"oid": ...}} になり得る点が異なる(即座に約定しなかった
    ことを示す。Aloはスプレッドを跨いで即約定する注文は拒否されるため、
    正常な指値注文はほとんどの場合この"resting"になる)。
    """
    if not isinstance(response, dict):
        return PostOnlyResult(success=False, error=f"予期しない応答形式: {response!r}")
    if response.get("status") != "ok":
        return PostOnlyResult(success=False, error=str(response))

    try:
        statuses = response["response"]["data"]["statuses"]
    except (KeyError, TypeError):
        return PostOnlyResult(success=False, error=f"応答の解析に失敗しました: {response!r}")

    if not statuses:
        return PostOnlyResult(success=False, error="statusesが空です")

    status = statuses[0]
    if isinstance(status, dict) and "resting" in status:
        resting = status["resting"]
        try:
            return PostOnlyResult(success=True, resting=True, oid=int(resting.get("oid", 0)))
        except (TypeError, ValueError):
            return PostOnlyResult(success=False, error=f"resting情報の解析に失敗しました: {resting!r}")
    if isinstance(status, dict) and "filled" in status:
        filled = status["filled"]
        try:
            return PostOnlyResult(
                success=True,
                filled=True,
                avg_price=float(filled.get("avgPx", 0.0)),
                filled_size=float(filled.get("totalSz", 0.0)),
            )
        except (TypeError, ValueError):
            return PostOnlyResult(success=False, error=f"filled情報の解析に失敗しました: {filled!r}")
    if isinstance(status, dict) and "error" in status:
        return PostOnlyResult(success=False, error=str(status["error"]))
    return PostOnlyResult(success=False, error=f"未対応の応答: {status!r}")


def _parse_order_status_response(response: object) -> OrderStatusResult:
    """query_order_statusの戻り値(Hyperliquidの/info orderStatus)を解釈する。

    想定形式: {"status": "order", "order": {"status": "open"|"filled"|
    "canceled"|..., "order": {...}}}。約定済みの場合、"order"の中に
    平均約定価格・数量が入っていることを期待するが、フィールド名が
    ドキュメント通りでない可能性もあるため、見つからない場合は0扱いに
    する(found/is_filledの判定自体は壊さない)。想定外の形式は
    found=False(「まだ分からない」)として扱い、約定/未約定のどちらとも
    断定しない。
    """
    if not isinstance(response, dict):
        return OrderStatusResult(found=False)
    if response.get("status") != "order":
        return OrderStatusResult(found=False)

    order_info = response.get("order")
    if not isinstance(order_info, dict):
        return OrderStatusResult(found=False)

    status_str = order_info.get("status")
    if not isinstance(status_str, str):
        return OrderStatusResult(found=False)

    inner = order_info.get("order")
    avg_price = 0.0
    filled_size = 0.0
    if isinstance(inner, dict):
        try:
            if inner.get("avgPx") is not None:
                avg_price = float(inner["avgPx"])
            if inner.get("sz") is not None:
                filled_size = float(inner["sz"])
        except (TypeError, ValueError):
            pass

    if status_str == "filled":
        return OrderStatusResult(found=True, is_filled=True, avg_price=avg_price, filled_size=filled_size)
    if status_str == "open":
        return OrderStatusResult(found=True, is_open=True)
    # canceled/rejected/marginCanceled等、それ以外は「約定していない」扱い
    return OrderStatusResult(found=True, is_filled=False, is_open=False)


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
    """保有中のロングを成行相当(IoC指値、Taker扱い)で全量(またはsize分)決済する。
    損切りなど、約定を待たずすぐに出たい場合に使う(指値だと価格が
    戻ってこない限り約定せず、含み損が拡大し続けるリスクがあるため)。
    """
    exchange = _get_exchange()
    if exchange is None:
        return OrderResult(success=False, error="ウォレット秘密鍵が未設定/不正、またはExchange初期化に失敗しました")
    try:
        response = exchange.market_close(symbol, sz=size, slippage=slippage)
    except Exception as exc:  # noqa: BLE001
        logger.error("hyperliquid_client: 決済発注に失敗しました symbol=%s size=%s: %s", symbol, size, exc)
        return OrderResult(success=False, error=str(exc))
    return _parse_order_response(response)


def _round_price(info: Info, symbol: str, price: float) -> float:
    """Hyperliquidの価格精度ルール(有効数字5桁、かつ小数点以下は
    銘柄のszDecimalsに応じた桁数まで)に合わせて価格を丸める。
    これに従わない価格は発注時にエラーで拒否される。SDK内部の
    Exchange._slippage_price()と同じ計算式(private methodのため直接
    呼ばず、Infoが保持する公開属性から同じ式を再現している)。
    """
    asset = info.coin_to_asset[symbol]
    is_spot = asset >= 10_000
    decimals = (6 if not is_spot else 8) - info.asset_to_sz_decimals[asset]
    return round(float(f"{price:.5g}"), decimals)


def place_post_only_buy(symbol: str, size: float, price: float) -> PostOnlyResult:
    """指値(Alo=Add Liquidity Only、板に並べるだけでスプレッドを跨いで
    即約定はしない)で買い注文を送る。sizeはコイン数量、priceは希望する
    指値価格(取引所の価格精度ルールに合わせて内部で丸める)。

    Aloはスプレッドを跨いで即約定してしまう価格だと拒否される
    (Makerであることを保証する仕組みのため)。呼び出し側は
    resting=Trueの場合、oidを使ってquery_order_statusで約定を
    確認すること。
    """
    exchange = _get_exchange()
    info = _get_info()
    if exchange is None or info is None:
        return PostOnlyResult(success=False, error="ウォレット秘密鍵が未設定/不正、またはクライアント初期化に失敗しました")
    try:
        rounded_price = _round_price(info, symbol, price)
        response = exchange.order(symbol, True, size, rounded_price, order_type={"limit": {"tif": "Alo"}})
    except Exception as exc:  # noqa: BLE001
        logger.error("hyperliquid_client: 指値買い注文に失敗しました symbol=%s size=%s price=%s: %s", symbol, size, price, exc)
        return PostOnlyResult(success=False, error=str(exc))
    return _parse_post_only_response(response)


def place_post_only_sell(symbol: str, size: float, price: float) -> PostOnlyResult:
    """指値(Alo)で売り注文(利確決済用)を送る。reduce_only=Trueで、
    既存のロングを減らす方向にのみ働かせる(誤ってショートを新規に
    建ててしまわないようにするため)。
    """
    exchange = _get_exchange()
    info = _get_info()
    if exchange is None or info is None:
        return PostOnlyResult(success=False, error="ウォレット秘密鍵が未設定/不正、またはクライアント初期化に失敗しました")
    try:
        rounded_price = _round_price(info, symbol, price)
        response = exchange.order(
            symbol, False, size, rounded_price, order_type={"limit": {"tif": "Alo"}}, reduce_only=True
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("hyperliquid_client: 指値売り注文に失敗しました symbol=%s size=%s price=%s: %s", symbol, size, price, exc)
        return PostOnlyResult(success=False, error=str(exc))
    return _parse_post_only_response(response)


def query_order_status(oid: int) -> OrderStatusResult:
    """注文IDから、約定済み(is_filled)か、まだ板に残っているか(is_open)
    かを確認する。foundがFalseの場合は「確認できなかった」ことを示し、
    約定/未約定のどちらとも断定しない(呼び出し側は今回はスキップし、
    次回また確認すること)。
    """
    info = _get_info()
    account = hyperliquid_wallet.get_account()
    if info is None or account is None:
        return OrderStatusResult(found=False)
    try:
        response = info.query_order_by_oid(account.address, oid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hyperliquid_client: 注文状態の確認に失敗しました oid=%s: %s", oid, exc)
        return OrderStatusResult(found=False)
    return _parse_order_status_response(response)


def cancel_order(symbol: str, oid: int) -> bool:
    """指値注文をキャンセルする。既に約定済み/存在しない注文をキャンセル
    しようとした場合はFalseを返す(呼び出し側でエラー扱いにしなくてよい、
    「もう対象が無かった」というだけの場合が多いため)。
    """
    exchange = _get_exchange()
    if exchange is None:
        return False
    try:
        response = exchange.cancel(symbol, oid)
    except Exception as exc:  # noqa: BLE001
        logger.error("hyperliquid_client: 注文キャンセルに失敗しました symbol=%s oid=%s: %s", symbol, oid, exc)
        return False
    return isinstance(response, dict) and response.get("status") == "ok"
