"""DexScreenerの公開REST APIから、指定トークン(mint)の実際のDEXペア情報
(出来高・売買件数・価格変動・流動性等)を取得するクライアント。

無料・APIキー不要(DexScreener公式ドキュメント参照)。ただしレート制限が
あるため(このエンドポイントは60req/min程度)、呼び出し側は頻繁に叩き
すぎないよう注意すること(main.pyのチェックポイント間隔を参照)。

pump.fun上のトークンは、ボンディングカーブを卒業してRaydium等の実際の
DEXへ移行(migration)するまでDexScreenerには一切表示されない。そのため
このクライアントは、pumpportal_clientのsubscribeMigrationイベントを
受けたトークンに対してだけ呼び出す想定(main.py参照)。

urllib.requestで同期的にHTTP GETするため(discord_notifier.pyと同じ方式、
外部ライブラリ非依存)、呼び出し側(main.pyの非同期ループ)は
asyncio.to_thread()経由で呼ぶこと(イベントループをブロックしないため)。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import config

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 10
# discord_notifier.pyと同じ理由(Cloudflare等がデフォルトUser-Agentを
# 自動化アクセスとみなして弾く場合があるため)、ブラウザ相当を名乗る。
_USER_AGENT = "Mozilla/5.0 (compatible; ARTEMIS-Phantom-Sniper/1.0)"


_EXCLUDED_DEX_IDS = {"pumpfun"}


def fetch_best_pair(mint: str) -> dict | None:
    """指定したmintの、DexScreener上で最も流動性の高いSolanaペア情報を返す。

    まだDEXに存在しない(卒業直後でDexScreenerのインデックスが追いついて
    いない等)場合や、取得に失敗した場合はNoneを返す(呼び出し側は
    「まだデータなし」として扱い、例外は送出しない)。

    卒業(migration)済みのトークンは、DexScreener上に卒業前のpump.fun
    ボンディングカーブ自体のペア(dexId="pumpfun")と、卒業後の実際の
    DEX(Raydium/PumpSwap等)のペアの**2つ**が並存することがある
    (2026-07判明。前者は卒業のずっと前に作成されているため、流動性次第
    ではこちらが「最も流動性の高いペア」として選ばれてしまい、出来高・
    価格変動・詳細リンクの行き先がチェックポイントのたびにどちらの
    ペアかブレる、というバグの原因になっていた)。このbotはそもそも
    「卒業後の実際のDEX取引状況」だけを見る設計のため、pumpfunの
    ボンディングカーブ自体のペアは常に除外する。
    """
    url = f"{config.DEXSCREENER_API_BASE_URL}/latest/dex/tokens/{mint}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("dexscreener_client: mint=%sの取得に失敗しました: %s", mint, exc)
        return None

    if not isinstance(data, dict):
        return None
    pairs = data.get("pairs") or []
    solana_pairs = [
        p
        for p in pairs
        if isinstance(p, dict) and p.get("chainId") == "solana" and p.get("dexId") not in _EXCLUDED_DEX_IDS
    ]
    if not solana_pairs:
        return None

    return max(solana_pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


# DexScreenerのtokensエンドポイントはカンマ区切りで複数アドレスを受け付ける
# (公式の上限は30件)。通知後の値動きを細かく追う用途では1件ずつ叩くと
# レート制限(このエンドポイントは60req/min程度)に当たるため、必ずまとめて
# 取得すること(outcome_tracker.pyの安値追跡を参照)。
_MAX_BATCH_SIZE = 30


def _best_pair_from(pairs: list, mint: str) -> dict | None:
    """1トークン分のペア一覧から、最も流動性の高いSolanaペアを選ぶ。"""
    candidates = [
        p
        for p in pairs
        if isinstance(p, dict)
        and p.get("chainId") == "solana"
        and p.get("dexId") not in _EXCLUDED_DEX_IDS
        and ((p.get("baseToken") or {}).get("address") == mint)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


def fetch_best_pairs(mints: list[str]) -> dict[str, dict]:
    """複数mintのペア情報をまとめて取得し、{mint: ペア} で返す。

    取得できなかったmintはキーごと含めない(呼び出し側が「今回は値が
    取れなかった」と「時価総額0」を取り違えないようにするため。
    outcome_tracker.record_and_advanceのmarket_cap_available参照)。

    fetch_best_pair()と同じくpump.funのボンディングカーブ自体のペアは
    除外し、baseTokenが対象mintであるペアだけを見る(複数トークンを一度に
    問い合わせると、別トークンのペアが同じ応答に混ざって返るため)。
    """
    results: dict[str, dict] = {}
    for start in range(0, len(mints), _MAX_BATCH_SIZE):
        batch = mints[start : start + _MAX_BATCH_SIZE]
        url = f"{config.DEXSCREENER_API_BASE_URL}/latest/dex/tokens/{','.join(batch)}"
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            logger.warning("dexscreener_client: %d件の一括取得に失敗しました: %s", len(batch), exc)
            continue
        if not isinstance(data, dict):
            continue
        pairs = data.get("pairs") or []
        for mint in batch:
            pair = _best_pair_from(pairs, mint)
            if pair is not None:
                results[mint] = pair
    return results


def market_cap_of(pair: dict | None) -> float:
    """ペアから時価総額を取り出す。取得できなければ0を返す。

    marketCapもfdvも無い応答は「時価総額が0」ではなく「取得できなかった」。
    呼び出し側は必ず 0 を『値なし』として扱うこと(main.py参照)。
    """
    if not pair:
        return 0.0
    return float(pair.get("marketCap") or pair.get("fdv") or 0.0)
