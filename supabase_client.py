"""Supabase(PostgREST)への読み書きクライアント。

外部ライブラリを追加しないため、urllib.requestで直接Supabaseの自動生成REST
API(PostgREST)を叩く(discord_notifier.py/dexscreener_client.py/rugcheck_
client.pyと同じ方式)。SUPABASE_URLまたはSUPABASE_SERVICE_ROLE_KEYが未設定
の場合は何もしない(既定OFF、ローカルのJSON/JSONLだけで動作は完結する)。
送信の失敗はログに記録するだけで、呼び出し元(監視ループ)には一切影響
させない(Discord通知等と同じ「なくても本体機能は止まらない」設計方針)。

テーブル定義はsupabase_schema.sql参照。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 10


def is_configured() -> bool:
    """SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEYが両方設定されているか。"""
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_ROLE_KEY)


def _headers(extra_prefer: str = "") -> dict:
    prefer = "return=minimal"
    if extra_prefer:
        prefer = f"{prefer},{extra_prefer}"
    return {
        "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _request(method: str, path: str, *, body: dict | list | None = None, extra_prefer: str = "") -> bytes | None:
    if not is_configured():
        return None

    url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(extra_prefer), method=method)
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("supabase_client: %s %sに失敗しました: %s", method, path, exc)
        return None


def insert_notification(row: dict) -> None:
    """notificationsテーブルへ1件挿入する(main.py、通知/追い通知のたびに呼ぶ)。"""
    _request("POST", "notifications", body=row)


def insert_outcome(row: dict) -> None:
    """outcomesテーブルへ1件挿入する(main.py、結果チェックポイントのたびに呼ぶ)。"""
    _request("POST", "outcomes", body=row)


def upsert_creator_blocklist(creator: str, reason: str) -> None:
    """creator_blocklistテーブルへupsertする(creatorが主キーのため、
    既存行があれば上書きせず据え置きたいところだが、PostgRESTのmerge-
    duplicatesはinsert-or-updateなので、reasonが変わった場合は更新される。
    ローカルのcreator_blocklist.CreatorBlocklistは「最初の理由を優先」だが、
    Supabase側は分析用の記録なので直近の理由で上書きされても実害はない)。
    """
    _request(
        "POST",
        "creator_blocklist",
        body={"creator": creator, "reason": reason},
        extra_prefer="resolution=merge-duplicates",
    )


def fetch(path_with_query: str) -> list[dict] | None:
    """任意のテーブル/ビューをGETする(dashboard_server.py参照)。

    path_with_queryはPostgRESTのクエリ構文をそのまま渡す
    (例: "notifications?select=*&order=notified_at.desc&limit=50")。
    取得に失敗した場合、または未設定の場合はNoneを返す。
    """
    data = _request("GET", path_with_query)
    if data is None:
        return None
    try:
        parsed = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("supabase_client: レスポンスのJSON解析に失敗しました: %s", exc)
        return None
    return parsed if isinstance(parsed, list) else None
