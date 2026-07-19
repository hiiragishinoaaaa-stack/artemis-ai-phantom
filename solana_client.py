"""Solanaブロックチェーンから直接、直近の取引を読んでユニーク買い手数を数えるクライアント。

DexScreenerの公開APIには(2026-07に判明した通り)ユニーク買い手数のような
フィールドは存在しない。RugCheck/Solscan/Birdeye等の外部サービスも、この
粒度のリアルタイムデータは有料プランでしか出さない。そのため、Solanaの
RPC(JSON-RPC 2.0、無料の公開エンドポイントまたはHeliusの無料枠で利用可能)
を直接叩き、対象プールの直近の取引履歴から「そのトークンの残高が増えた
(=買った)別ウォレットの数」を自前で集計する。

外部ライブラリを追加しないため、urllib.requestでJSON-RPCを直接POSTする
(discord_notifier.py等と同じ方式)。getTransactionの呼び出しはconcurrent.
futures.ThreadPoolExecutorで少数並列にし(既定5並列)、レイテンシを抑える
(初動の通知速度に影響させないため、main.py側は0秒チェックポイントでは
このモジュールを呼ばない設計にしている。README.md参照)。

RPCの呼び出し回数に上限を設けている(config.SOLANA_MAX_SIGNATURES_PER_
CHECKPOINT、既定20件)。無料の公開RPC(api.mainnet-beta.solana.com)は
レート制限が厳しいため、安定運用したい場合はHelius等の無料APIキーを
SOLANA_RPC_URLに設定することを推奨する(README.md参照)。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import config

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 10
_USER_AGENT = "Mozilla/5.0 (compatible; ARTEMIS-Phantom-Sniper/1.0)"


def _rpc_call(method: str, params: list) -> dict | None:
    """Solana JSON-RPC 2.0を1回呼び出し、"result"部分を返す。失敗時はNone。"""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
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
        logger.warning("solana_client: %sに失敗しました: %s", method, exc)
        return None

    if not isinstance(payload, dict):
        return None
    if "error" in payload:
        logger.warning("solana_client: %sがエラーを返しました: %s", method, payload["error"])
        return None
    return payload.get("result")


def _recent_signatures(pool_address: str, since_unix: float) -> list[str] | None:
    """指定プールアドレス宛の直近の取引署名のうち、since_unix以降・成功した
    もの(err無し)だけを、config.SOLANA_MAX_SIGNATURES_PER_CHECKPOINT件を
    上限に返す(RPC呼び出し数の上限を決める、最も新しいものから取得)。

    RPC呼び出し自体が失敗した場合はNoneを返す(該当署名が0件だった場合の
    空リストと区別するため。count_unique_buyers参照)。
    """
    result = _rpc_call(
        "getSignaturesForAddress",
        [pool_address, {"limit": config.SOLANA_MAX_SIGNATURES_PER_CHECKPOINT}],
    )
    if not isinstance(result, list):
        return None

    signatures = []
    for entry in result:
        if not isinstance(entry, dict):
            continue
        if entry.get("err") is not None:
            continue
        block_time = entry.get("blockTime")
        if block_time is None or block_time < since_unix:
            continue
        signature = entry.get("signature")
        if signature:
            signatures.append(str(signature))
    return signatures


def _buyer_owner_from_transaction(tx: dict, mint: str) -> str | None:
    """1件のgetTransaction結果から、対象mintの残高が増えた(買った)
    ウォレット(owner)を1つ返す(無ければNone)。
    """
    meta = tx.get("meta") if isinstance(tx, dict) else None
    if not isinstance(meta, dict):
        return None

    pre_amounts: dict[str, float] = {}
    for entry in meta.get("preTokenBalances") or []:
        if not isinstance(entry, dict) or entry.get("mint") != mint:
            continue
        owner = entry.get("owner")
        amount = _ui_amount(entry)
        if owner is not None and amount is not None:
            pre_amounts[owner] = pre_amounts.get(owner, 0.0) + amount

    post_amounts: dict[str, float] = {}
    for entry in meta.get("postTokenBalances") or []:
        if not isinstance(entry, dict) or entry.get("mint") != mint:
            continue
        owner = entry.get("owner")
        amount = _ui_amount(entry)
        if owner is not None and amount is not None:
            post_amounts[owner] = post_amounts.get(owner, 0.0) + amount

    for owner, post_amount in post_amounts.items():
        if post_amount - pre_amounts.get(owner, 0.0) > 0:
            return owner  # 最初に見つかった買い手を返す(1トランザクション1買い手想定)
    return None


def _ui_amount(token_balance_entry: dict) -> float | None:
    ui_token_amount = token_balance_entry.get("uiTokenAmount")
    if not isinstance(ui_token_amount, dict):
        return None
    amount = ui_token_amount.get("uiAmount")
    return float(amount) if amount is not None else 0.0


def count_unique_buyers(pool_address: str, mint: str, since_unix: float) -> int | None:
    """指定プールで、since_unix以降に対象mintを買った(残高が増えた)
    別ウォレットの数を返す。取得自体に失敗した場合はNoneを返す
    (呼び出し側は前回の値を維持する。token_watcher.apply_unique_buyers参照)。
    """
    if not pool_address:
        return None

    signatures = _recent_signatures(pool_address, since_unix)
    if signatures is None:
        return None
    if not signatures:
        return 0

    def _fetch(signature: str) -> dict | None:
        return _rpc_call("getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])

    buyers: set[str] = set()
    with ThreadPoolExecutor(max_workers=config.SOLANA_RPC_CONCURRENCY) as executor:
        for tx in executor.map(_fetch, signatures):
            if tx is None:
                continue
            owner = _buyer_owner_from_transaction(tx, mint)
            if owner:
                buyers.add(owner)

    return len(buyers)
