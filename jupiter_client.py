"""Jupiter Swap API(Solana上のDEXアグリゲーター、無料・APIキー不要)を使った
トークンの売買実行。

⚠️ 実際にオンチェーンで資金を動かすモジュール(trade_executor.pyから
呼ばれる)。このファイル単体はネットワーク通信とトランザクション署名を
行うが、いつ・いくら売買するかの判断は一切行わない(呼び出し側の責務)。

流れ(buy/sell共通):
1. `/quote` でレート(スリッページ込みの見積もり)を取得
2. `/swap` にその見積もりと自分のウォレットアドレスを渡し、署名前の
   トランザクション(base64)を受け取る
3. solders(Rust製の軽量なSolana SDK)でウォレットの秘密鍵を使い署名
4. 署名済みトランザクションをSolana RPC(config.SOLANA_RPC_URL)へ
   `sendTransaction`で送信し、トランザクション署名(tx signature)を得る

送信が成功した(RPCがtx signatureを返した)ことは、そのトランザクションが
実際にブロックに取り込まれ成功したことを保証しない(Solanaではtx送信後に
失敗/ドロップすることがある)。position_tracker.py側は、買い注文成功と
みなす前に実際のトークン残高を再確認する設計にしている(get_token_balance_
raw参照)。
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

import config

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 15
_USER_AGENT = "Mozilla/5.0 (compatible; ARTEMIS-Phantom-Sniper/1.0)"

_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
_SWAP_URL = "https://quote-api.jup.ag/v6/swap"

# Solanaのネイティブトークン(SOL)をJupiter APIで指定する際の特別なmintアドレス
# (Wrapped SOLのmint。実際にはwrapAndUnwrapSol=trueにより自動でラップ/アン
# ラップされるため、呼び出し側は素のSOL残高だけ気にすればよい)。
SOL_MINT = "So11111111111111111111111111111111111111112"
_LAMPORTS_PER_SOL = 1_000_000_000


@dataclass
class SwapResult:
    success: bool
    tx_signature: str = ""
    out_amount_raw: int = 0  # 買い: 受け取ったトークンの最小単位数、売り: 受け取ったSOLのlamports数
    error: str = ""


def _get(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("jupiter_client: GET %sに失敗しました: %s", url, exc)
        return None


def _post(url: str, payload: dict) -> dict | None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("jupiter_client: POST %sに失敗しました: %s", url, exc)
        return None


def get_quote(input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict | None:
    """指定トークン間のスワップ見積もりを取得する(失敗時はNone)。

    amount_rawは入力トークンの最小単位(SOLならlamports)。slippage_bpsは
    ベーシスポイント(100 = 1%)。
    """
    if amount_raw <= 0:
        return None
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_raw),
        "slippageBps": str(slippage_bps),
    }
    url = f"{_QUOTE_URL}?{urllib.parse.urlencode(params)}"
    quote = _get(url)
    if not isinstance(quote, dict) or "outAmount" not in quote:
        logger.warning("jupiter_client: 見積もり取得に失敗しました input=%s output=%s", input_mint, output_mint)
        return None
    return quote


def _build_swap_transaction_b64(quote: dict, user_pubkey: str) -> str | None:
    payload = {
        "quoteResponse": quote,
        "userPublicKey": user_pubkey,
        "wrapAndUnwrapSol": True,
        # 優先度手数料をJupiter側の推奨値で自動設定する(pump.fun卒業直後の
        # 混雑したブロックでも通りやすくするため)。
        "prioritizationFeeLamports": "auto",
    }
    result = _post(_SWAP_URL, payload)
    if not isinstance(result, dict):
        return None
    swap_tx = result.get("swapTransaction")
    return str(swap_tx) if swap_tx else None


def _sign_transaction_b64(swap_tx_b64: str, keypair: Keypair) -> bytes | None:
    try:
        raw = base64.b64decode(swap_tx_b64)
        unsigned = VersionedTransaction.from_bytes(raw)
        signed = VersionedTransaction(unsigned.message, [keypair])
        return bytes(signed)
    except Exception as exc:  # noqa: BLE001 - solders例外の型が多岐にわたるため
        logger.error("jupiter_client: トランザクションの署名に失敗しました: %s", exc)
        return None


def _send_raw_transaction(signed_tx_bytes: bytes) -> str | None:
    """署名済みトランザクションをSolana RPCへ送信し、tx signatureを返す(失敗時はNone)。"""
    b64 = base64.b64encode(signed_tx_bytes).decode("utf-8")
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [b64, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        config.SOLANA_RPC_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.error("jupiter_client: トランザクション送信に失敗しました: %s", exc)
        return None

    if "error" in payload:
        logger.error("jupiter_client: トランザクション送信がエラーを返しました: %s", payload["error"])
        return None
    signature = payload.get("result")
    return str(signature) if signature else None


def execute_swap(input_mint: str, output_mint: str, amount_raw: int, keypair: Keypair, slippage_bps: int) -> SwapResult:
    """quote取得→トランザクション組み立て→署名→送信までを一括で行う。

    失敗した場合はSwapResult.success=False、SwapResult.errorに理由を入れて
    返す(例外は投げない。呼び出し側=trade_executor.pyが一律にハンドリング
    できるようにするため)。
    """
    quote = get_quote(input_mint, output_mint, amount_raw, slippage_bps)
    if quote is None:
        return SwapResult(success=False, error="見積もり取得に失敗しました")

    user_pubkey = str(keypair.pubkey())
    swap_tx_b64 = _build_swap_transaction_b64(quote, user_pubkey)
    if swap_tx_b64 is None:
        return SwapResult(success=False, error="スワップトランザクションの組み立てに失敗しました")

    signed_bytes = _sign_transaction_b64(swap_tx_b64, keypair)
    if signed_bytes is None:
        return SwapResult(success=False, error="トランザクションの署名に失敗しました")

    signature = _send_raw_transaction(signed_bytes)
    if signature is None:
        return SwapResult(success=False, error="トランザクション送信に失敗しました")

    try:
        out_amount_raw = int(quote.get("outAmount", 0))
    except (TypeError, ValueError):
        out_amount_raw = 0

    return SwapResult(success=True, tx_signature=signature, out_amount_raw=out_amount_raw)


def sol_to_lamports(sol_amount: float) -> int:
    return int(sol_amount * _LAMPORTS_PER_SOL)


def lamports_to_sol(lamports: int) -> float:
    return lamports / _LAMPORTS_PER_SOL


def get_token_balance_raw(owner_pubkey: str, mint: str) -> int | None:
    """指定オーナーが保有する指定mintトークンの残高(最小単位、複数トークン
    アカウントがあれば合算)を返す。取得失敗時はNone。

    自前のbuy時記録(quoteのoutAmount)を信用せず、実際の残高を都度
    再確認するために使う(スリッページ・部分約定・Transfer税トークン等の
    ズレを吸収するため。position_tracker.py参照)。
    """
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [owner_pubkey, {"mint": mint}, {"encoding": "jsonParsed"}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        config.SOLANA_RPC_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("jupiter_client: 残高取得に失敗しました: %s", exc)
        return None

    result = payload.get("result") if isinstance(payload, dict) else None
    accounts = result.get("value") if isinstance(result, dict) else None
    if not isinstance(accounts, list):
        return None

    total = 0
    for account in accounts:
        try:
            info = account["account"]["data"]["parsed"]["info"]
            total += int(info["tokenAmount"]["amount"])
        except (KeyError, TypeError, ValueError):
            continue
    return total
