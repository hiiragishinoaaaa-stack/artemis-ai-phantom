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


def fetch_best_pair(mint: str) -> dict | None:
    """指定したmintの、DexScreener上で最も流動性の高いSolanaペア情報を返す。

    まだDEXに存在しない(卒業直後でDexScreenerのインデックスが追いついて
    いない等)場合や、取得に失敗した場合はNoneを返す(呼び出し側は
    「まだデータなし」として扱い、例外は送出しない)。
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
    solana_pairs = [p for p in pairs if isinstance(p, dict) and p.get("chainId") == "solana"]
    if not solana_pairs:
        return None

    return max(solana_pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
