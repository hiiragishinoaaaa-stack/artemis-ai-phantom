"""analyze_outcomes.py の単体テスト。Supabaseへのネットワーク送信はモックする。"""
from __future__ import annotations

import json

import pytest

import analyze_outcomes
import config


def test_pearson_correlation_perfect_positive():
    corr = analyze_outcomes._pearson_correlation([1, 2, 3, 4], [10, 20, 30, 40])
    assert corr == pytest.approx(1.0)


def test_pearson_correlation_perfect_negative():
    corr = analyze_outcomes._pearson_correlation([1, 2, 3, 4], [40, 30, 20, 10])
    assert corr == pytest.approx(-1.0)


def test_pearson_correlation_none_when_too_few_points():
    assert analyze_outcomes._pearson_correlation([1], [1]) is None


def test_pearson_correlation_none_when_no_variance():
    assert analyze_outcomes._pearson_correlation([5, 5, 5], [1, 2, 3]) is None


def test_fetch_local_fallback_rows_reads_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "outcomes.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"mint": "M1", "notified_tier": "HIGH", "notified_score": 90, "change_pct": 12.5, "checkpoint_seconds": 1800}),
                json.dumps({"mint": "M2", "notified_tier": "WATCH", "notified_score": 70, "change_pct": -5.0, "checkpoint_seconds": 1800}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "OUTCOMES_FILE_PATH", path)

    rows = analyze_outcomes._fetch_local_fallback_rows(None)
    assert len(rows) == 2
    assert rows[0]["mint"] == "M1"
    assert rows[0]["change_pct"] == 12.5


def test_fetch_local_fallback_rows_filters_by_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / "outcomes.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"mint": "M1", "change_pct": 1.0, "checkpoint_seconds": 1800}),
                json.dumps({"mint": "M2", "change_pct": 2.0, "checkpoint_seconds": 3600}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "OUTCOMES_FILE_PATH", path)

    rows = analyze_outcomes._fetch_local_fallback_rows(3600)
    assert len(rows) == 1
    assert rows[0]["mint"] == "M2"


def test_fetch_local_fallback_rows_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTCOMES_FILE_PATH", tmp_path / "does_not_exist.jsonl")
    assert analyze_outcomes._fetch_local_fallback_rows(None) == []


def test_fetch_local_fallback_rows_skips_corrupt_lines(tmp_path, monkeypatch):
    path = tmp_path / "outcomes.jsonl"
    path.write_text("not json\n" + json.dumps({"mint": "M1", "change_pct": 1.0, "checkpoint_seconds": 1800}), encoding="utf-8")
    monkeypatch.setattr(config, "OUTCOMES_FILE_PATH", path)

    rows = analyze_outcomes._fetch_local_fallback_rows(None)
    assert len(rows) == 1


def test_print_report_handles_empty_rows(capsys):
    analyze_outcomes._print_report([], min_samples=10, rich=False)
    captured = capsys.readouterr()
    assert "分析できるデータがありませんでした" in captured.out


def test_print_report_prints_win_rate(capsys):
    rows = [
        {"mint": "M1", "change_pct": 10.0, "checkpoint_seconds": 1800},
        {"mint": "M2", "change_pct": -5.0, "checkpoint_seconds": 1800},
    ]
    analyze_outcomes._print_report(rows, min_samples=10, rich=False)
    captured = capsys.readouterr()
    assert "1800秒後" in captured.out
    assert "勝率" in captured.out
