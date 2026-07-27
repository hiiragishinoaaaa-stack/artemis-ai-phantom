"""analyze_drawdown.py の単体テスト。ネットワーク不要で実行できる。

このツールの目的は、analyze_buckets.py の『損切りEV』が持つ構造的な水増しを
測ること。したがってテストの主眼は「途中で下がってから戻した勝ち銘柄を、
ちゃんと負けとして数え直せているか」に置く。
"""
from __future__ import annotations

import json

from analyze_drawdown import (
    load_pairs,
    load_single,
    naive_outcomes,
    exact_outcomes,
    path_checked_outcomes,
    rows_with_measured_low,
    sweep_table,
    winner_survival,
)


def _write(tmp_path, records: list[dict]) -> str:
    path = tmp_path / "outcomes.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return str(path)


def _rec(mint: str, checkpoint: int, change_pct, notify_mcap=50_000.0) -> dict:
    now = None if change_pct is None else notify_mcap * (1 + change_pct / 100)
    return {
        "mint": mint,
        "checkpoint_seconds": checkpoint,
        "market_cap_at_notify_usd": notify_mcap,
        "market_cap_now_usd": now,
        "change_pct": change_pct,
    }


def _pair(mint: str, early: float, late: float, notify_mcap=50_000.0, min_pct=None) -> dict:
    return {
        "mint": mint,
        "early_pct": early,
        "late_pct": late,
        "notify_mcap": notify_mcap,
        "min_pct": min_pct,
    }


def test_load_pairs_joins_both_checkpoints_of_the_same_mint(tmp_path):
    path = _write(tmp_path, [_rec("A", 1800, -10.0), _rec("A", 3600, 40.0)])
    pairs, _ = load_pairs(path, 1800, 3600, 1_000_000_000.0)
    assert pairs == [
        {
            "mint": "A",
            "early_pct": -10.0,
            "late_pct": 40.0,
            "notify_mcap": 50_000.0,
            "notify_score": 0,
            "min_pct": None,
            "max_pct": None,
        }
    ]


def test_load_pairs_skips_mints_missing_one_checkpoint(tmp_path):
    """片方しか無い銘柄は経路の検算ができないので、比較の対象から外す。"""
    path = _write(
        tmp_path,
        [_rec("A", 1800, -10.0), _rec("A", 3600, 40.0), _rec("B", 3600, 90.0), _rec("C", 1800, 5.0)],
    )
    pairs, _ = load_pairs(path, 1800, 3600, 1_000_000_000.0)
    assert [p["mint"] for p in pairs] == ["A"]


def test_load_pairs_counts_exact_zero_change_as_suspicious(tmp_path):
    path = _write(tmp_path, [_rec("A", 1800, 0.0), _rec("A", 3600, 40.0)])
    _, counts = load_pairs(path, 1800, 3600, 1_000_000_000.0)
    assert counts["exact_zero_change"] == 1


def test_path_check_counts_a_recovered_winner_as_stopped_out():
    """これがこのツールの存在理由。

    -60%まで落ちてから+200%に戻した銘柄は、-30%の損切りを置いていたら
    途中で切られている。最終結果だけを見る計算はこれを勝ちとして数えて
    しまう。
    """
    pairs = [_pair("A", early=-60.0, late=200.0)]
    assert naive_outcomes(pairs, 30.0, 30.0) == [200.0]
    assert path_checked_outcomes(pairs, 30.0, 30.0) == [-30.0]


def test_path_check_keeps_a_winner_that_never_dipped():
    pairs = [_pair("A", early=10.0, late=200.0)]
    assert path_checked_outcomes(pairs, 30.0, 30.0) == [200.0]


def test_path_check_agrees_with_naive_on_plain_losers():
    pairs = [_pair("A", early=-50.0, late=-90.0)]
    assert naive_outcomes(pairs, 30.0, 30.0) == [-30.0]
    assert path_checked_outcomes(pairs, 30.0, 30.0) == [-30.0]


def test_slippage_makes_the_stop_fill_worse_than_the_stop_level():
    pairs = [_pair("A", early=-50.0, late=-90.0)]
    assert path_checked_outcomes(pairs, 30.0, 45.0) == [-45.0]


def test_shallow_loss_inside_the_stop_is_left_alone():
    pairs = [_pair("A", early=-5.0, late=-20.0)]
    assert path_checked_outcomes(pairs, 30.0, 30.0) == [-20.0]


def test_winner_survival_separates_dippers_from_clean_runners():
    pairs = [
        _pair("A", early=-60.0, late=200.0),  # 途中で切られた勝ち
        _pair("B", early=10.0, late=50.0),  # 無傷の勝ち
        _pair("C", early=-90.0, late=-95.0),  # ただの負け
    ]
    winners, survived = winner_survival(pairs, 30.0)
    assert winners == 2
    assert survived == 1


def test_path_check_can_flip_a_positive_expectancy_negative():
    """勝ちの大半が途中で切られていれば、プラスに見えたEVは消える。"""
    pairs = [_pair(f"W{i}", early=-70.0, late=400.0) for i in range(2)]
    pairs += [_pair(f"L{i}", early=-80.0, late=-95.0) for i in range(8)]

    naive = sum(naive_outcomes(pairs, 30.0, 30.0)) / len(pairs)
    checked = sum(path_checked_outcomes(pairs, 30.0, 30.0)) / len(pairs)

    assert naive == 56.0  # (400*2 - 30*8) / 10
    assert checked == -30.0


def test_sweep_table_drops_small_buckets_but_always_keeps_the_total():
    pairs = [_pair(f"A{i}", -5.0, 50.0, notify_mcap=20_000.0) for i in range(5)]
    pairs += [_pair(f"B{i}", -5.0, 50.0, notify_mcap=2_000_000.0) for i in range(3)]

    order, rows = sweep_table(pairs, [30.0], slippage=0.0, min_count=4)

    assert order == ["$10,000〜$30,000", "全体"]
    assert rows["全体"] == [50.0]


def test_sweep_table_applies_slippage_to_every_stop_level():
    pairs = [_pair("A", -90.0, -95.0)]
    _, rows = sweep_table(pairs, [30.0, 50.0], slippage=20.0, min_count=1)
    assert rows["全体"] == [-50.0, -70.0]


def test_rows_with_measured_low_keeps_only_records_that_have_a_low():
    """安値の記録は途中から始まったので、古い記録には入っていない。"""
    pairs = [_pair("A", -10.0, 40.0, min_pct=-55.0), _pair("B", -10.0, 40.0)]
    assert [p["mint"] for p in rows_with_measured_low(pairs)] == ["A"]


def test_exact_outcomes_uses_the_measured_low_not_the_checkpoints():
    """チェックポイントでは無傷に見えても、実測の安値が切られていれば負け。

    30分でも1時間でも-10%にしか見えないが、途中で-55%まで落ちていた銘柄。
    推定(path_checked_outcomes)はこれを勝ちのまま通してしまう。
    """
    pairs = [_pair("A", -10.0, 300.0, min_pct=-55.0)]
    assert path_checked_outcomes(pairs, 30.0, 30.0) == [300.0]
    assert exact_outcomes(pairs, 30.0, 30.0) == [-30.0]


def test_exact_outcomes_keeps_a_winner_whose_low_never_reached_the_stop():
    pairs = [_pair("A", -10.0, 300.0, min_pct=-25.0)]
    assert exact_outcomes(pairs, 30.0, 30.0) == [300.0]


def test_load_single_does_not_require_a_pair(tmp_path):
    """確定計算に早い時点は要らない。ペアを求めると待ち時間もサンプルも倍損する。"""
    records = [
        {**_rec("A", 1800, -40.0), "min_change_pct": -55.0, "max_change_pct": 10.0},
        {**_rec("B", 1800, 120.0), "min_change_pct": -5.0, "max_change_pct": 130.0},
    ]
    path = _write(tmp_path, records)

    pairs, _ = load_pairs(path, 1800, 3600, 1_000_000_000.0)
    assert pairs == []  # 60分の記録がまだ無いのでペアは1件も作れない

    rows, counts = load_single(path, 1800, 1_000_000_000.0)
    assert [r["mint"] for r in rows] == ["A", "B"]
    assert counts["no_low"] == 0


def test_load_single_skips_records_without_a_measured_low(tmp_path):
    """安値が無い古い記録を混ぜると、確定計算ではなくなる。"""
    records = [
        {**_rec("A", 1800, -40.0), "min_change_pct": -55.0},
        _rec("B", 1800, -40.0),  # 記録開始前の分
    ]
    path = _write(tmp_path, records)

    rows, counts = load_single(path, 1800, 1_000_000_000.0)
    assert [r["mint"] for r in rows] == ["A"]
    assert counts["no_low"] == 1


def test_load_single_carries_the_fields_the_filters_need(tmp_path):
    records = [
        {
            **_rec("A", 1800, -40.0, notify_mcap=2_000_000.0),
            "notified_score": 100,
            "min_change_pct": -55.0,
        }
    ]
    rows, _ = load_single(_write(tmp_path, records), 1800, 1_000_000_000.0)
    assert rows[0]["notify_mcap"] == 2_000_000.0
    assert rows[0]["notify_score"] == 100
    assert exact_outcomes(rows, 30.0, 30.0) == [-30.0]
