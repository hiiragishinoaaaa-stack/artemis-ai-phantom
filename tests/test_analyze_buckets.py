"""analyze_buckets.py の単体テスト。ネットワーク不要で実行できる。

特に、集計を壊す2種類のデータ異常を確実に分離できているかを見る:

1. 桁違いの時価総額(供給量の取得ミス)。実測で1件が全体平均を-20%から
   +5195%へ押し上げた。
2. 現在時価総額がちょうど0の記録。本物のラグと、DexScreenerが値を返さな
   かっただけの取得失敗が、どちらも「-100.0%」として混ざっている。
"""
from __future__ import annotations

import json

from analyze_buckets import bucket_by_mcap, expectancy_with_stop, load_records, summarise


def _write(tmp_path, records: list[dict]) -> str:
    path = tmp_path / "outcomes.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return str(path)


def _record(change_pct, notify_mcap=50_000.0, now_mcap=None, checkpoint=3600, **extra) -> dict:
    if now_mcap is None and change_pct is not None:
        now_mcap = notify_mcap * (1 + change_pct / 100)
    record = {
        "mint": extra.pop("mint", "MINT"),
        "checkpoint_seconds": checkpoint,
        "market_cap_at_notify_usd": notify_mcap,
        "market_cap_now_usd": now_mcap,
        "change_pct": change_pct,
    }
    record.update(extra)
    return record


def test_load_records_filters_by_checkpoint(tmp_path):
    path = _write(tmp_path, [_record(10.0, checkpoint=1800), _record(20.0, checkpoint=3600)])
    rows, _ = load_records(path, 3600, 1_000_000_000.0)
    assert [r["change_pct"] for r in rows] == [20.0]


def test_load_records_drops_absurd_market_caps(tmp_path):
    path = _write(
        tmp_path,
        [_record(10.0), _record(5195.0, notify_mcap=73_000_000_000.0, now_mcap=73_000_000_000.0)],
    )
    rows, counts = load_records(path, 3600, 1_000_000_000.0)
    assert len(rows) == 1
    assert counts["dropped"] == 1


def test_load_records_counts_null_change_as_unresolved(tmp_path):
    """取得失敗としてnullで記録された分は、0%でも-100%でもなく単に集計外。"""
    path = _write(tmp_path, [_record(10.0), _record(None, now_mcap=None)])
    rows, counts = load_records(path, 3600, 1_000_000_000.0)
    assert len(rows) == 1
    assert counts["unresolved"] == 1
    assert counts["zero_now"] == 0


def test_load_records_counts_zero_now_but_keeps_it_by_default(tmp_path):
    path = _write(tmp_path, [_record(10.0), _record(-100.0, now_mcap=0.0)])
    rows, counts = load_records(path, 3600, 1_000_000_000.0)
    assert len(rows) == 2
    assert counts["zero_now"] == 1


def test_exclude_zero_now_removes_the_suspicious_records(tmp_path):
    """取得失敗の疑いを外すと、-100%に引きずられていた中央値が戻る。"""
    records = [_record(-100.0, now_mcap=0.0) for _ in range(3)] + [_record(20.0), _record(40.0)]
    path = _write(tmp_path, records)

    kept, counts = load_records(path, 3600, 1_000_000_000.0)
    _, median_with, _, _ = summarise(kept)
    assert median_with == -100.0
    assert counts["zero_now"] == 3

    cleaned, _ = load_records(path, 3600, 1_000_000_000.0, exclude_zero_now=True)
    n, median_without, win_rate, _ = summarise(cleaned)
    assert n == 2
    assert median_without == 30.0
    assert win_rate == 100.0


def test_summarise_reports_median_win_rate_and_trimmed_mean():
    rows = [_record(c) for c in [-90.0, -50.0, 10.0, 30.0]]
    n, median, win_rate, _ = summarise(rows)
    assert n == 4
    assert median == -20.0
    assert win_rate == 50.0


def test_expectancy_with_stop_caps_losses_and_keeps_upside():
    """負けを損切り幅で打ち切ると、持ちっぱなしより期待値が上がる。"""
    rows = [_record(c) for c in [-100.0, -100.0, -100.0, 500.0]]
    held = summarise(rows)[3]
    stopped_ev, win_median = expectancy_with_stop(rows, 30.0)

    assert held == 50.0  # 買って持ちっぱなし(4件では刈込が効かず生の平均)
    assert stopped_ev == 102.5  # (-30 -30 -30 +500) / 4
    assert win_median == 500.0


def test_expectancy_with_stop_does_not_touch_shallow_losses():
    rows = [_record(c) for c in [-10.0, -20.0]]
    stopped_ev, win_median = expectancy_with_stop(rows, 30.0)
    assert stopped_ev == -15.0
    assert win_median == 0.0  # 勝ちが1件も無い場合


def test_expectancy_with_stop_handles_empty_group():
    assert expectancy_with_stop([], 30.0) == (0.0, 0.0)


def test_bucket_by_mcap_splits_on_notify_time_market_cap():
    rows = [
        _record(10.0, notify_mcap=5_000.0),
        _record(20.0, notify_mcap=50_000.0),
        _record(30.0, notify_mcap=5_000_000.0),
    ]
    groups, labels = bucket_by_mcap(rows, [10_000, 1_000_000])
    assert len(labels) == 3
    assert [len(groups[label]) for label in labels] == [1, 1, 1]
