"""RugCheck(https://rugcheck.xyz/)の公開REST APIから、指定トークン(mint)の
詐欺・ラグプルリスク判定を取得するクライアント。

無料・APIキー不要(https://api.rugcheck.xyz/swagger/index.html)。ただし
未認証だと10req/minとDexScreenerより厳しいレート制限があるため、
呼び出し側(main.py)はトークン1件につき1回しか呼ばない設計にしている。

urllib.requestで同期的にHTTP GETするため(discord_notifier.py/
dexscreener_client.pyと同じ方式、外部ライブラリ非依存)、呼び出し側
(main.pyの非同期ループ)はasyncio.to_thread()経由で呼ぶこと。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import config

logger = logging.getLogger("phantom_sniper")

_REQUEST_TIMEOUT_SECONDS = 10
_USER_AGENT = "Mozilla/5.0 (compatible; ARTEMIS-Phantom-Sniper/1.0)"
# RugCheckが危険とみなすリスクフラグのレベル。このレベルが1件でもあれば
# 危険トークンとみなす(token_watcher.apply_rugcheck_report参照)。
_DANGER_LEVEL = "danger"
# dangerほど致命的ではないが注意が必要なリスクフラグのレベル。1件ごとに
# 小さな減点はするが、通知自体は止めない(scoring._score_rugcheck_warnings
# 参照。初動の伸びを狙う都合上、疑わしい程度で機会を潰したくないため)。
_WARN_LEVEL = "warn"


def fetch_risk_report(mint: str) -> dict | None:
    """指定したmintのRugCheckレポートを返す。取得に失敗した場合はNoneを返す
    (呼び出し側は「判定不能」として扱い、例外は送出しない)。
    """
    url = f"{config.RUGCHECK_API_BASE_URL}/v1/tokens/{mint}/report"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("rugcheck_client: mint=%sの取得に失敗しました: %s", mint, exc)
        return None

    if not isinstance(data, dict):
        return None
    return data


def extract_danger_reason(report: dict) -> str | None:
    """レポートのrisks[]に"danger"レベルのフラグがあれば、その説明を1つ返す。

    danger相当のフラグが無ければNoneを返す(=RugCheck視点では致命的な
    危険は検出されなかった、の意味。安全を保証するものではない)。
    """
    risks = report.get("risks")
    if not isinstance(risks, list):
        return None
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        if str(risk.get("level", "")).lower() == _DANGER_LEVEL:
            name = risk.get("name") or "danger risk"
            return str(name)
    return None


def extract_warn_count(report: dict) -> int:
    """レポートのrisks[]のうち"warn"レベルのフラグの件数を返す(0件ならリスクなし)。

    dangerとは別集計(dangerは別途extract_danger_reason()で強制0点扱いに
    するため、ここでは二重にカウントしない)。
    """
    risks = report.get("risks")
    if not isinstance(risks, list):
        return 0
    return sum(
        1 for risk in risks if isinstance(risk, dict) and str(risk.get("level", "")).lower() == _WARN_LEVEL
    )


def extract_top_holders_pct(report: dict, top_n: int = 10) -> float | None:
    """レポートのtopHolders[](各要素は{"pct": 保有割合(%), ...})から、
    上位top_n件のpctを合計して返す。データが無ければNoneを返す(=判定不能。
    scoring.pyはこの場合加点・減点どちらもしない)。
    """
    top_holders = report.get("topHolders")
    if not isinstance(top_holders, list) or not top_holders:
        return None

    percentages = []
    for holder in top_holders:
        if not isinstance(holder, dict):
            continue
        pct = holder.get("pct")
        if isinstance(pct, (int, float)):
            percentages.append(float(pct))

    if not percentages:
        return None

    percentages.sort(reverse=True)
    return sum(percentages[:top_n])


def extract_creator(report: dict) -> str | None:
    """レポートから発行者(creator)のウォレットアドレスを返す。

    取得できなければNoneを返す(creator_blocklist.py参照。この値を使って
    「同じ発行者が名前を変えて再発行してきた」ケースを検出する)。
    """
    creator = report.get("creator")
    if isinstance(creator, str) and creator:
        return creator
    if isinstance(creator, dict):
        address = creator.get("address")
        if isinstance(address, str) and address:
            return address
    return None
