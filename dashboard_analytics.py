"""ダッシュボード用の集計ロジック(ネットワーク非依存の純粋関数のみ)。

dashboard_server.pyがsupabase_client.fetch()で取得した生の行リスト
(list[dict]、Supabaseのnotifications/outcomes/creator_blocklistテーブルの
レスポンスそのまま)を受け取り、画面表示用の集計結果を計算する。HTTP・
Supabase通信は一切行わないため、token_watcher.py/outcome_tracker.pyと
同じくネットワークなしで単体テストできる(tests/test_dashboard_analytics.py
参照)。
"""
from __future__ import annotations


def summarize_notifications(rows: list[dict]) -> dict:
    """notificationsテーブルの行リストから、通知数・ティア別・★分布を集計する。

    tier_counts/star_countsはnotification_type=="primary"の行のみを対象と
    する(followup行は同じトークンの再掲であり、初動時点の分布を歪めるため)。
    """
    primary_rows = [r for r in rows if r.get("notification_type") == "primary"]
    followup_count = sum(1 for r in rows if r.get("notification_type") == "followup")

    tier_counts: dict[str, int] = {}
    star_counts: dict[str, int] = {"0": 0, "1": 0, "2": 0, "3": 0}
    for row in primary_rows:
        tier = row.get("tier")
        if tier:
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        star_count = str(row.get("star_count", 0))
        if star_count in star_counts:
            star_counts[star_count] += 1

    return {
        "total_notifications": len(rows),
        "primary_count": len(primary_rows),
        "followup_count": followup_count,
        "tier_counts": tier_counts,
        "star_counts": star_counts,
    }


def summarize_outcomes(rows: list[dict]) -> dict:
    """outcomesテーブルの行リストから、チェックポイント(経過秒)ごとの
    勝率(change_pct > 0の割合)と平均変化率を集計する。

    戻り値は{"1800": {"count": ..., "win_rate_pct": ..., "avg_change_pct": ...}, ...}
    のように、checkpoint_secondsを文字列キーにした辞書(JSON化しやすいよう)。
    """
    grouped: dict[int, list[float]] = {}
    for row in rows:
        checkpoint_seconds = row.get("checkpoint_seconds")
        change_pct = row.get("change_pct")
        if checkpoint_seconds is None or change_pct is None:
            continue
        grouped.setdefault(int(checkpoint_seconds), []).append(float(change_pct))

    result: dict[str, dict] = {}
    for checkpoint_seconds, changes in sorted(grouped.items()):
        count = len(changes)
        wins = sum(1 for c in changes if c > 0)
        result[str(checkpoint_seconds)] = {
            "count": count,
            "win_rate_pct": round(wins / count * 100, 1) if count else 0.0,
            "avg_change_pct": round(sum(changes) / count, 1) if count else 0.0,
        }
    return result
