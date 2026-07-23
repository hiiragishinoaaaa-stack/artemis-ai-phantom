"""グリッドトレードのTP/SL幅・グリッド分割数をまとめて比較するスイープツール。

`perp_grid_backtest.py`は1回の実行で1パラメータの組み合わせしか検証できず、
「SL幅(-0.1%)が実際のノイズ幅に対して狭すぎるのではないか」という仮説を
確認するには何度も手動で実行し直す必要があった。このツールはローソク足
データ(とオプションでファンディングレート履歴)を1回だけ取得し、その同じ
データに対してTP/SL/グリッド分割数の組み合わせを総当たりで
`run_grid_backtest()`(純粋関数、ネットワーク非依存)へ渡して結果を一覧表示する。

各行に「損益分岐勝率」(この手数料・レバレッジ設定でトントンになるために
必要な勝率)も併記する。実際の勝率がこれを下回っていれば、そのパラメータの
組み合わせは(過去データ上は)期待値マイナスだったことを意味する。

⚠️ このツールは分析専用で、既存のライブ/ペーパートレードの設定
(PERP_GRID_*系の環境変数)には一切影響しない。結果を見て実際にTP/SL/
グリッド分割数を変更する場合は、必ず複数銘柄・複数期間で確認したうえで
手動で`.env`を編集すること。

使い方(sandbox環境ではfapi.binance.comへのアクセスがブロックされている
ため実行できない。VPS等、Binance Futures APIへ到達できる環境で実行する):
  .venv/bin/python perp_grid_backtest_sweep.py --symbol BTCUSDT
  .venv/bin/python perp_grid_backtest_sweep.py --symbol BTCUSDT --interval 15m --limit 1500 \
      --grid-counts 50,100,300 --take-profits 0.2,0.3,0.4 --stop-losses -0.1,-0.15,-0.2,-0.3 \
      --fee-pct-per-side 0.015
"""
from __future__ import annotations

import argparse

import perp_market_data
from perp_grid_backtest import run_grid_backtest


def _parse_float_list(raw: str) -> list[float]:
    return [float(v.strip()) for v in raw.split(",") if v.strip()]


def _parse_int_list(raw: str) -> list[int]:
    return [int(v.strip()) for v in raw.split(",") if v.strip()]


def breakeven_win_rate_pct(take_profit_pct: float, stop_loss_pct: float, leverage: float, fee_pct_per_side: float) -> float | None:
    """この手数料・レバレッジ設定でトントンになるために必要な勝率(%)。

    stop_loss_pctはマイナス値(例: -0.1)。TP_net/SL_netはcompute_grid_pnl_pct
    相当(手数料は往復・レバレッジ込みで両方の結果から等しく差し引かれる、
    ファンディングコストは考慮しない解析的な近似値)。
    """
    round_trip_fee_pct = 2 * fee_pct_per_side * leverage
    tp_net = take_profit_pct * leverage - round_trip_fee_pct
    sl_net = stop_loss_pct * leverage - round_trip_fee_pct  # stop_loss_pct < 0
    denom = tp_net - sl_net
    if denom <= 0:
        return None  # SLの方がTPより有利、という異常な設定(通常は起きない)
    return -sl_net / denom * 100


def main() -> None:
    parser = argparse.ArgumentParser(description="グリッドトレードのTP/SL幅・グリッド分割数のスイープ比較ツール")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--limit", type=int, default=1500, help="取得するローソク足の本数(Binance Futuresの上限は1500)")
    parser.add_argument("--range-pct", type=float, default=10.0)
    parser.add_argument("--grid-counts", type=_parse_int_list, default=[50, 100, 300])
    parser.add_argument("--take-profits", type=_parse_float_list, default=[0.2, 0.3, 0.4])
    parser.add_argument("--stop-losses", type=_parse_float_list, default=[-0.1, -0.15, -0.2, -0.3, -0.5])
    parser.add_argument("--leverage", type=float, default=3.0)
    parser.add_argument("--fee-pct-per-side", type=float, default=0.015, help="片道の取引手数料率(%%、既定0.015=Maker想定)")
    parser.add_argument("--include-funding", action="store_true")
    parser.add_argument("--min-trades", type=int, default=5, help="この件数未満の取引数だった組み合わせは表から除く(既定5)")
    parser.add_argument(
        "--sort-by",
        choices=("total_pnl", "win_rate_margin", "win_rate"),
        default="win_rate_margin",
        help="結果の並び替え基準(既定: 損益分岐勝率との差が大きい順)",
    )
    args = parser.parse_args()

    print(f"{args.symbol} {args.interval} 足を{args.limit}本取得しています...")
    candles = perp_market_data.fetch_ohlc_with_time(args.symbol, args.interval, args.limit)
    if candles is None:
        print("価格データの取得に失敗しました(ネットワーク/シンボル名を確認してください)。")
        return

    funding_rate_history = None
    if args.include_funding:
        start_ms = int(candles[0][0] * 1000)
        end_ms = int(candles[-1][0] * 1000)
        funding_rate_history = perp_market_data.fetch_funding_rate_history(args.symbol, start_ms, end_ms)
        if funding_rate_history is None:
            print("ファンディングレート履歴の取得に失敗しました。ファンディングコストを含めずに続行します。")

    rows: list[dict] = []
    for grid_count in args.grid_counts:
        for take_profit in args.take_profits:
            for stop_loss in args.stop_losses:
                result = run_grid_backtest(
                    candles,
                    args.range_pct,
                    grid_count,
                    take_profit,
                    stop_loss,
                    args.leverage,
                    fee_pct_per_side=args.fee_pct_per_side,
                    funding_rate_history=funding_rate_history,
                )
                if len(result.trades) < args.min_trades:
                    continue
                breakeven = breakeven_win_rate_pct(take_profit, stop_loss, args.leverage, args.fee_pct_per_side)
                rows.append(
                    {
                        "grid_count": grid_count,
                        "take_profit": take_profit,
                        "stop_loss": stop_loss,
                        "trades": len(result.trades),
                        "win_rate": result.win_rate,
                        "breakeven": breakeven,
                        "margin": (result.win_rate - breakeven) if breakeven is not None else None,
                        "total_pnl": result.total_pnl_pct,
                        "max_dd": result.max_drawdown_pct,
                    }
                )

    if not rows:
        print(f"どの組み合わせも取引数が{args.min_trades}件未満でした(レンジ幅・グリッド分割数を見直してください)。")
        return

    sort_key = {"total_pnl": "total_pnl", "win_rate_margin": "margin", "win_rate": "win_rate"}[args.sort_by]
    rows.sort(key=lambda r: (r[sort_key] if r[sort_key] is not None else float("-inf")), reverse=True)

    fee_note = f"片道手数料{args.fee_pct_per_side:.3f}%、レバレッジ{args.leverage}倍"
    funding_note = "ファンディングコスト考慮済み" if funding_rate_history is not None else "ファンディングコストは未考慮(損益分岐勝率にも含まれない)"
    print(f"\n=== {args.symbol} {args.interval} グリッドスイープ結果({fee_note}、{funding_note}) ===")
    print(
        f"{'grid':>5} {'TP%':>6} {'SL%':>6} {'件数':>5} {'勝率%':>7} {'損益分岐%':>9} "
        f"{'差(勝率-損益分岐)':>16} {'合計損益%':>9} {'最大DD%':>8}"
    )
    for r in rows:
        breakeven_str = f"{r['breakeven']:.1f}" if r["breakeven"] is not None else "N/A"
        margin_str = f"{r['margin']:+.1f}" if r["margin"] is not None else "N/A"
        print(
            f"{r['grid_count']:>5} {r['take_profit']:>6.2f} {r['stop_loss']:>6.2f} {r['trades']:>5} "
            f"{r['win_rate']:>7.1f} {breakeven_str:>9} {margin_str:>16} {r['total_pnl']:>+9.1f} {r['max_dd']:>8.1f}"
        )

    print(
        "\n※「差(勝率-損益分岐)」がプラスの行は過去データ上は期待値プラス、マイナスは期待値マイナスだったことを意味する"
        "(手数料込み・ファンディング抜きの解析的な損益分岐勝率との比較。実際の値動きは1銘柄・1期間の結果に過ぎず、"
        "将来の成績を保証しない。複数銘柄・複数期間で確認してから実際のパラメータ変更を検討すること)。"
    )


if __name__ == "__main__":
    main()
