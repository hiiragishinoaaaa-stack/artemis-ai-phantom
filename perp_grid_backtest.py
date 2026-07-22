"""グリッドトレード戦略のバックテストツール(実験的機能)。

ユーザーが見つけたedgeXのグリッドトレード解説記事を参考にした、
「一定の価格レンジを固定グリッドで区切り、下がったら買い・少し上がったら
利確売り、を機械的に繰り返す」戦略の過去データ検証。`perp_backtest.py`
(EMA/RSIでロング/ショートの方向を予測する戦略)とは全く逆の考え方: 方向を
当てにいかず、レンジ内の小さな往復差益を積み重ねる(相場が一方向に大きく
動かない前提)。買い(ロング)グリッドのみのシンプルな実装(記事の
利確0.2%/損切り0.1%という数値もこの想定)。

⚠️ 過去データでの検証結果は将来の成績を一切保証しない。参考にした記事
自身も「6ヶ月の理論上のバックテストでトントン(±100USD以内)」という
結果を報告しており、グリッド戦略だから安全というわけでもない。特に、
価格がレンジを大きく超えて一方向に走ると、買いグリットの含み損が
どんどん積み上がるだけで一切利確できなくなる(いわゆる「握り込み」)
リスクがある。本物の資金投入前に、複数銘柄・複数期間・複数パラメータで
確認すること。

1本のローソク足の中でTP(利確)とSL(損切り)の両方に価格が触れた場合、
実際にどちらが先に起きたかはローソク足データだけでは分からない(tick
データが無いため)。このツールはTPを優先して判定する(やや楽観的な
見積もりになる可能性がある点に注意)。

既定は手数料0%(未考慮)。グリッドトレードは取引回数が非常に多くなり
やすく(1000〜2000件超も珍しくない)、手数料の有無で結果が全く変わる。
必ず`--fee-pct-per-side`を使って手数料を加味した結果も確認すること
(取引所のMaker/Taker手数料率を調べて指定する)。

使い方:
  .venv/bin/python perp_grid_backtest.py --symbol BTCUSDT
  .venv/bin/python perp_grid_backtest.py --symbol BTCUSDT --interval 4h --limit 1500 --range-pct 15 --grid-count 50
  .venv/bin/python perp_grid_backtest.py --symbol BTCUSDT --daily-loss-limit -20
  .venv/bin/python perp_grid_backtest.py --symbol BTCUSDT --fee-pct-per-side 0.02
"""
from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass, field

import perp_market_data
from grid_trading import compute_grid_levels


@dataclass
class GridTrade:
    entry_price: float
    exit_price: float
    opened_at: float
    closed_at: float
    reason: str  # "take_profit" | "stop_loss"
    pnl_pct: float


@dataclass
class GridBacktestResult:
    trades: list[GridTrade] = field(default_factory=list)
    center_price: float = 0.0
    lower_bound: float = 0.0
    upper_bound: float = 0.0
    grid_step_pct: float = 0.0
    still_open_count: int = 0
    buy_and_hold_pnl_pct: float = 0.0
    buy_and_hold_leveraged_pnl_pct: float = 0.0

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl_pct > 0)
        return wins / len(self.trades) * 100

    @property
    def total_pnl_pct(self) -> float:
        """全グリッド往復トレードの損益率(%)の単純合計(常に同じサイズで
        毎回賭けたと仮定した場合の目安。実際の資金配分は記事の
        「証拠金の0.5%×レバレッジ」のように1回あたりを小さくするのが
        前提なので、この合計値をそのまま口座の損益と読み替えないこと)。
        """
        return sum(t.pnl_pct for t in self.trades)

    @property
    def max_drawdown_pct(self) -> float:
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in self.trades:
            cumulative += t.pnl_pct
            peak = max(peak, cumulative)
            max_dd = min(max_dd, cumulative - peak)
        return max_dd


def _day_key(unix_seconds: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(unix_seconds))


def run_grid_backtest(
    candles: list[tuple[float, float, float, float, float]],
    range_pct: float,
    grid_count: int,
    take_profit_pct: float,
    stop_loss_pct: float,
    leverage: float,
    daily_loss_limit_pct: float | None = None,
    fee_pct_per_side: float = 0.0,
) -> GridBacktestResult:
    """candles((時刻, 始値, 高値, 安値, 終値)のペア、古い順)に対して
    買いグリッド戦略を機械的に適用する(純粋関数、ネットワーク非依存)。

    価格レンジは最初の足の終値を中心に固定する(グリッドトレードは
    「レンジ内で動き続ける」前提の戦略のため、EMA等で動的に追従させる
    トレンド戦略とは考え方が異なる)。各グリッド水準は同時に1つの建玉しか
    持たない(利確/損切りで決済されたら、その水準はまた買える状態に戻る)。

    fee_pct_per_side(片道の手数料率、既定0=手数料なし)は、1回の往復
    (買い+売り)につき`2 * fee_pct_per_side * leverage`をpnl_pctから
    差し引く(グリッドトレードは取引回数が非常に多くなりやすく、手数料が
    利益を大きく削る可能性がある。参考にしたgrid trading記事でも
    「週の手数料だけで数千USD、結果はトントン」と報告されている)。
    """
    result = GridBacktestResult()
    if not candles or grid_count <= 0:
        return result

    center_price = candles[0][4]
    result.center_price = center_price
    levels = compute_grid_levels(center_price, range_pct, grid_count)
    if not levels:
        return result

    result.lower_bound = levels[0]
    result.upper_bound = levels[-1]
    step = (result.upper_bound - result.lower_bound) / grid_count
    result.grid_step_pct = step / center_price * 100 if center_price else 0.0

    open_positions: dict[int, dict] = {}
    daily_pnl: dict[str, float] = {}
    round_trip_fee_pct = 2 * fee_pct_per_side * leverage

    for now, _open_price, high, low, _close in candles:
        day_key = _day_key(now)

        for level_index in list(open_positions.keys()):
            pos = open_positions[level_index]
            entry = pos["entry_price"]
            tp_price = entry * (1 + take_profit_pct / 100)
            sl_price = entry * (1 + stop_loss_pct / 100)  # stop_loss_pctはマイナス値
            if high >= tp_price:
                pnl_pct = take_profit_pct * leverage - round_trip_fee_pct
                result.trades.append(GridTrade(entry, tp_price, pos["opened_at"], now, "take_profit", pnl_pct))
                daily_pnl[day_key] = daily_pnl.get(day_key, 0.0) + pnl_pct
                del open_positions[level_index]
            elif low <= sl_price:
                pnl_pct = stop_loss_pct * leverage - round_trip_fee_pct
                result.trades.append(GridTrade(entry, sl_price, pos["opened_at"], now, "stop_loss", pnl_pct))
                daily_pnl[day_key] = daily_pnl.get(day_key, 0.0) + pnl_pct
                del open_positions[level_index]

        if daily_loss_limit_pct is not None and daily_pnl.get(day_key, 0.0) <= daily_loss_limit_pct:
            continue

        for i, level in enumerate(levels):
            if i in open_positions:
                continue
            if low <= level <= high:
                open_positions[i] = {"entry_price": level, "opened_at": now}

    result.still_open_count = len(open_positions)

    start_price = candles[0][4]
    end_price = candles[-1][4]
    if start_price > 0:
        result.buy_and_hold_pnl_pct = (end_price - start_price) / start_price * 100
        result.buy_and_hold_leveraged_pnl_pct = result.buy_and_hold_pnl_pct * leverage

    return result


def _print_report(result: GridBacktestResult, symbol: str, leverage: float, fee_pct_per_side: float = 0.0) -> None:
    fee_note = f"、片道手数料{fee_pct_per_side:.3f}%考慮済み" if fee_pct_per_side else "、手数料は未考慮(0%)"
    print(f"=== {symbol} グリッドトレード バックテスト結果(レバレッジ{leverage}倍{fee_note}) ===")
    print(
        f"レンジ: ${result.lower_bound:,.2f} 〜 ${result.upper_bound:,.2f} "
        f"(中心${result.center_price:,.2f}、グリッド間隔約{result.grid_step_pct:.2f}%)"
    )
    if result.still_open_count:
        print(f"検証終了時点でまだ決済されていない建玉: {result.still_open_count}件(集計に含めていない)")

    if not result.trades:
        print("グリッド取引が1件も発生しませんでした(価格がレンジ外に出たまま、またはパラメータが合っていない可能性)。")
        return

    print(f"取引数: {len(result.trades)}件")
    print(f"勝率: {result.win_rate:.1f}%")
    print(f"合計損益(単純合計、複利無し・毎回同サイズ賭けた場合の目安): {result.total_pnl_pct:+.1f}%")
    print(f"平均損益/取引: {statistics.fmean(t.pnl_pct for t in result.trades):+.3f}%")
    print(f"最大ドローダウン: {result.max_drawdown_pct:.1f}%")

    print(
        f"\n--- 比較: シグナルを無視してただ買い持ちしていた場合(Buy & Hold) ---\n"
        f"レバレッジ無し: {result.buy_and_hold_pnl_pct:+.1f}% / "
        f"同じ{leverage}倍のレバレッジをかけた場合: {result.buy_and_hold_leveraged_pnl_pct:+.1f}%"
    )

    print(
        "\n※ 過去データでの結果は将来の成績を保証しません。特に価格がレンジを大きく超えて"
        "一方向に走った場合、買いグリッドの含み損が積み上がるだけで利確できなくなるリスクが"
        "あります。複数銘柄・複数期間・複数パラメータで確認してから実資金投入を検討すること。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="グリッドトレード戦略のバックテストツール(実験的機能)")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--limit", type=int, default=1500, help="取得するローソク足の本数(Binance Futuresの上限は1500)")
    parser.add_argument("--range-pct", type=float, default=10.0, help="価格レンジの幅(中心から±%%、既定10)")
    parser.add_argument("--grid-count", type=int, default=100, help="グリッド分割数(既定100)")
    parser.add_argument("--take-profit", type=float, default=0.2, help="1グリッドあたりの利確%%(既定0.2)")
    parser.add_argument("--stop-loss", type=float, default=-0.1, help="1グリッドあたりの損切り%%(マイナス値、既定-0.1)")
    parser.add_argument("--leverage", type=float, default=3.0)
    parser.add_argument(
        "--daily-loss-limit",
        type=float,
        default=None,
        help="1日の損益(%)がこの値(マイナス)を下回ったら、その日は新規グリッド注文を停止する(既定: 制限なし)",
    )
    parser.add_argument(
        "--fee-pct-per-side",
        type=float,
        default=0.0,
        help="片道の取引手数料率(%%、既定0=手数料なし)。例: Maker手数料0.02%%相当なら0.02を指定。"
        "往復で2倍×レバレッジ分がpnlから差し引かれる",
    )
    args = parser.parse_args()

    candles = perp_market_data.fetch_ohlc_with_time(args.symbol, args.interval, args.limit)
    if candles is None:
        print("価格データの取得に失敗しました(ネットワーク/シンボル名を確認してください)。")
        return

    result = run_grid_backtest(
        candles,
        args.range_pct,
        args.grid_count,
        args.take_profit,
        args.stop_loss,
        args.leverage,
        daily_loss_limit_pct=args.daily_loss_limit,
        fee_pct_per_side=args.fee_pct_per_side,
    )
    _print_report(result, args.symbol, args.leverage, args.fee_pct_per_side)


if __name__ == "__main__":
    main()
