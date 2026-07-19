"""dashboard_server.py の単体テスト。Supabase通信はモックする。"""
from __future__ import annotations

from unittest.mock import patch

import dashboard_server


def test_fetch_summary_returns_not_configured_message(monkeypatch):
    monkeypatch.setattr("supabase_client.is_configured", lambda: False)
    with patch("supabase_client.fetch") as mock_fetch:
        result = dashboard_server._fetch_summary()
        mock_fetch.assert_not_called()
    assert result["supabase_configured"] is False
    assert "error" in result


def test_fetch_summary_aggregates_notifications_and_outcomes(monkeypatch):
    monkeypatch.setattr("supabase_client.is_configured", lambda: True)

    def fake_fetch(query: str):
        if query.startswith("notifications"):
            return [
                {"notification_type": "primary", "tier": "HIGH", "star_count": 3},
                {"notification_type": "primary", "tier": "WATCH", "star_count": 1},
            ]
        if query.startswith("outcomes"):
            return [{"checkpoint_seconds": 1800, "change_pct": 20.0}]
        if query.startswith("creator_blocklist"):
            return [{"creator": "A"}, {"creator": "B"}]
        raise AssertionError(f"unexpected query: {query}")

    with patch("supabase_client.fetch", side_effect=fake_fetch):
        result = dashboard_server._fetch_summary()

    assert result["supabase_configured"] is True
    assert result["notifications"]["total_notifications"] == 2
    assert result["notifications"]["tier_counts"] == {"HIGH": 1, "WATCH": 1}
    assert result["win_rate_by_checkpoint"]["1800"]["count"] == 1
    assert result["blocklist_count"] == 2


def test_fetch_summary_handles_fetch_failures_gracefully(monkeypatch):
    monkeypatch.setattr("supabase_client.is_configured", lambda: True)
    with patch("supabase_client.fetch", return_value=None):
        result = dashboard_server._fetch_summary()

    assert result["notifications"]["total_notifications"] == 0
    assert result["win_rate_by_checkpoint"] == {}
    assert result["blocklist_count"] == 0


def test_fetch_recent_notifications_returns_not_configured(monkeypatch):
    monkeypatch.setattr("supabase_client.is_configured", lambda: False)
    result = dashboard_server._fetch_recent_notifications(50)
    assert result["supabase_configured"] is False
    assert result["notifications"] == []


def test_fetch_recent_notifications_passes_limit_through_query(monkeypatch):
    monkeypatch.setattr("supabase_client.is_configured", lambda: True)
    with patch("supabase_client.fetch", return_value=[{"mint": "MINT1"}]) as mock_fetch:
        result = dashboard_server._fetch_recent_notifications(25)
        query = mock_fetch.call_args[0][0]
        assert "limit=25" in query
    assert result["notifications"] == [{"mint": "MINT1"}]


def test_fetch_token_detail_returns_not_configured(monkeypatch):
    monkeypatch.setattr("supabase_client.is_configured", lambda: False)
    with patch("supabase_client.fetch") as mock_fetch:
        result = dashboard_server._fetch_token_detail("MINT1")
        mock_fetch.assert_not_called()
    assert result["supabase_configured"] is False
    assert result["notifications"] == []
    assert result["outcomes"] == []


def test_fetch_token_detail_queries_both_tables_filtered_by_mint(monkeypatch):
    monkeypatch.setattr("supabase_client.is_configured", lambda: True)

    def fake_fetch(query: str):
        if query.startswith("notifications"):
            assert "mint=eq.MINT1" in query
            assert "order=notified_at.desc" in query
            return [{"mint": "MINT1", "tier": "HIGH", "score": 90}]
        if query.startswith("outcomes"):
            assert "mint=eq.MINT1" in query
            assert "order=checkpoint_seconds.asc" in query
            return [{"checkpoint_seconds": 1800, "change_pct": 12.0}]
        raise AssertionError(f"unexpected query: {query}")

    with patch("supabase_client.fetch", side_effect=fake_fetch):
        result = dashboard_server._fetch_token_detail("MINT1")

    assert result["supabase_configured"] is True
    assert result["notifications"] == [{"mint": "MINT1", "tier": "HIGH", "score": 90}]
    assert result["outcomes"] == [{"checkpoint_seconds": 1800, "change_pct": 12.0}]


def test_fetch_token_detail_handles_fetch_failures_gracefully(monkeypatch):
    monkeypatch.setattr("supabase_client.is_configured", lambda: True)
    with patch("supabase_client.fetch", return_value=None):
        result = dashboard_server._fetch_token_detail("MINT1")

    assert result["supabase_configured"] is True
    assert result["notifications"] == []
    assert result["outcomes"] == []


def test_fetch_token_detail_url_encodes_mint():
    """mintアドレス自体に不正な文字が含まれていてもクエリを壊さないことの確認
    (実際のmintはbase58だが、念のためURLエンコードしていることを検証)。"""
    with patch("supabase_client.is_configured", return_value=True), patch(
        "supabase_client.fetch", return_value=[]
    ) as mock_fetch:
        dashboard_server._fetch_token_detail("mint with space")
        first_query = mock_fetch.call_args_list[0][0][0]
        assert "mint with space" not in first_query
        assert "mint%20with%20space" in first_query
