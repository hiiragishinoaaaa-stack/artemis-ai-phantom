"""過去の通知実績を分析し、どのスコア項目が実際の値上がり(またはラグ)と
相関しているかを集計する、人間が読むためのレポートツール。

⚠️ ここで出す「相関」はあくまで参考情報。自動でscoring.pyの重みを書き
換えたりは一切しない(サンプル数が少ない段階(目安として数十〜100件未満)
では簡単に過学習・ノイズを拾ってしまい、下手に重みを変えるとかえって
質を落としかねないため。scoring.pyの重みを実際に変えるかどうかは、この
レポートの内容と件数を見た上で人間が判断すること)。

使い方(VPS上、Supabaseを設定している場合。venv環境で):
  .venv/bin/python analyze_outcomes.py
  .venv/bin/python analyze_outcomes.py --checkpoint 3600   # 1時間後の結果だけで分析
  .venv/bin/python analyze_outcomes.py --min-samples 20    # この件数未満の項目は「参考にならない」として除外(既定10)

Supabase未設定の場合は、logs/outcomes.jsonl(tier/scoreの粒度のみ、個別の
スコア項目までは分からない)を使った簡易分析にフォールバックする。
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict

import config
import supabase_client

# scoring.pyの各スコア項目に対応する、notificationsテーブルの数値カラム。
# (列名, 表示名)のペア。将来scoring.pyへ項目を追加したら、ここにも
# 対応する列を追加すると分析対象に入る。
_NUMERIC_COLUMNS = [
    ("score", "総合スコア"),
    ("buys_m5", "直近5分の買い件数"),
    ("sells_m5", "直近5分の売り件数"),
    ("unique_buyers_m5", "直近5分のユニーク買い手数"),
    ("volume_m5_usd", "直近5分の出来高(USD)"),
    ("liquidity_usd", "流動性(USD)"),
    ("price_change_m5_pct", "直近5分の価格変動(%)"),
    ("rugcheck_warn_count", "RugCheck注意フラグ件数"),
    ("top10_holders_pct", "上位10保有者集中度(%)"),
    ("elapsed_seconds", "通知時点のDEX卒業からの経過秒数"),
]
_BOOLEAN_COLUMNS = [
    ("has_twitter", "X(Twitter)リンクあり"),
    ("has_telegram", "Telegramリンクあり"),
]


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """標準ライブラリのみでピアソン相関係数を計算する(numpy不使用)。

    データ数が2件未満、またはどちらかの分散が0(全部同じ値)の場合は
    計算できないためNoneを返す。
    """
    n = len(xs)
    if n < 2:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x**0.5 * var_y**0.5)


def _fetch_supabase_rows(checkpoint_seconds: int | None) -> list[dict] | None:
    if not supabase_client.is_configured():
        return None

    columns = ",".join(c for c, _ in _NUMERIC_COLUMNS + _BOOLEAN_COLUMNS)
    notifications = supabase_client.fetch(
        f"notifications?select=mint,tier,{columns}&notification_type=eq.primary&limit=5000"
    )
    outcomes = supabase_client.fetch("outcomes?select=mint,checkpoint_seconds,change_pct&limit=20000")
    if notifications is None or outcomes is None:
        print("Supabaseからの取得に失敗しました(ネットワーク/認証エラー)。logs/outcomes.jsonlでの簡易分析にフォールバックします。")
        return None

    notif_by_mint = {row["mint"]: row for row in notifications if isinstance(row, dict) and row.get("mint")}

    rows = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        if checkpoint_seconds is not None and outcome.get("checkpoint_seconds") != checkpoint_seconds:
            continue
        notif = notif_by_mint.get(outcome.get("mint"))
        if notif is None:
            continue
        merged = dict(notif)
        merged["change_pct"] = outcome.get("change_pct")
        merged["checkpoint_seconds"] = outcome.get("checkpoint_seconds")
        rows.append(merged)
    return rows


def _fetch_local_fallback_rows(checkpoint_seconds: int | None) -> list[dict]:
    """Supabase未設定/取得失敗時、logs/outcomes.jsonlから簡易的な行を作る
    (tier/scoreのみ、個別スコア項目までは分からない)。
    """
    if not config.OUTCOMES_FILE_PATH.exists():
        return []
    rows = []
    with config.OUTCOMES_FILE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if checkpoint_seconds is not None and entry.get("checkpoint_seconds") != checkpoint_seconds:
                continue
            rows.append(
                {
                    "mint": entry.get("mint"),
                    "tier": entry.get("notified_tier"),
                    "score": entry.get("notified_score"),
                    "change_pct": entry.get("change_pct"),
                    "checkpoint_seconds": entry.get("checkpoint_seconds"),
                }
            )
    return rows


def _print_report(rows: list[dict], min_samples: int, rich: bool) -> None:
    if not rows:
        print("分析できるデータがありませんでした(通知後の結果がまだ十分溜まっていない可能性があります)。")
        return

    by_checkpoint: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        cp = row.get("checkpoint_seconds")
        if cp is not None:
            by_checkpoint[cp].append(row)

    for checkpoint_seconds in sorted(by_checkpoint):
        group = by_checkpoint[checkpoint_seconds]
        changes = [r["change_pct"] for r in group if isinstance(r.get("change_pct"), (int, float))]
        if not changes:
            continue
        win_rate = sum(1 for c in changes if c > 0) / len(changes) * 100
        print(f"\n=== チェックポイント: {checkpoint_seconds}秒後 (n={len(changes)}件) ===")
        print(f"勝率(通知時点よりプラスだった割合): {win_rate:.1f}%")
        print(f"変化率の平均: {statistics.fmean(changes):+.1f}% / 中央値: {statistics.median(changes):+.1f}%")

        if len(changes) < min_samples:
            print(f"(サンプル数がmin_samples={min_samples}件未満のため、項目別の相関分析はスキップします)")
            continue
        if not rich:
            continue

        print("\n--- スコア項目ごとの相関(change_pctとの相関係数。1に近いほど「高いほど後で伸びた」、-1に近いほど逆) ---")
        results = []
        for col, label in _NUMERIC_COLUMNS:
            pairs = [
                (r[col], r["change_pct"])
                for r in group
                if isinstance(r.get(col), (int, float)) and isinstance(r.get("change_pct"), (int, float))
            ]
            if len(pairs) < min_samples:
                continue
            xs, ys = zip(*pairs)
            corr = _pearson_correlation(list(xs), list(ys))
            if corr is not None:
                results.append((label, corr, len(pairs)))
        results.sort(key=lambda item: abs(item[1]), reverse=True)
        for label, corr, n in results:
            print(f"  {label}: r={corr:+.2f} (n={n})")

        print("\n--- 有無(bool)項目ごとの平均変化率の差 ---")
        for col, label in _BOOLEAN_COLUMNS:
            with_flag = [r["change_pct"] for r in group if r.get(col) is True]
            without_flag = [r["change_pct"] for r in group if r.get(col) is False]
            if len(with_flag) < min_samples or len(without_flag) < min_samples:
                continue
            print(
                f"  {label}: あり平均{statistics.fmean(with_flag):+.1f}%(n={len(with_flag)}) / "
                f"なし平均{statistics.fmean(without_flag):+.1f}%(n={len(without_flag)})"
            )

    print(
        "\n※ この結果はあくまで参考情報です。件数が少ないうちは偶然のブレが大きいため、"
        "scoring.pyの重みを変える判断は人間が最終確認してから行ってください。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="通知後の実績を分析するツール")
    parser.add_argument("--checkpoint", type=int, default=None, help="このチェックポイント秒数だけに絞る(既定: 全部)")
    parser.add_argument("--min-samples", type=int, default=10, help="この件数未満の項目は相関分析から除外する(既定10)")
    args = parser.parse_args()

    rows = _fetch_supabase_rows(args.checkpoint)
    rich = rows is not None
    if rows is None:
        rows = _fetch_local_fallback_rows(args.checkpoint)
        if rows:
            print("(Supabase未設定/取得失敗のため、logs/outcomes.jsonlによる簡易分析(tier/scoreのみ)を行います)")

    _print_report(rows, args.min_samples, rich)


if __name__ == "__main__":
    main()
