"""過去の価格データに対して`perp_signals.compute_signal()`の判定ロジックを
機械的に適用し、実際にそのシグナル通り取引していたら勝てていたかを検証する
バックテストツール(実験的機能)。

⚠️ 過去データでの検証結果は将来の成績を一切保証しない(オーバーフィッ
ティング・相場のレジーム変化のリスクは常にある)。ここでの結果だけを
根拠に実資金の自動売買(Hyperliquid等への実発注)を有効化しないこと。
複数銘柄・複数期間で試し、一貫して結果が悪くないことを確認してから
初めて検討すること(それでも保証にはならない)。

各時点のシグナルは、その時点までのローソク足データだけを使って計算する
(未来の値を覗き見ない設計。run_backtest参照)。ファンディングレートの
履歴は考慮していない(過去分をまとめて安価に取得できる無料APIが無いため。
perp_signals.compute_signalのファンディングレート由来の加減点は、この
バックテストでは常に発生しない=実運用よりやや保守的な検証になる)。

`--daily-loss-limit`で、参考にした戦略記事([edgeX BTCパーペチュアル
Grid Trading]のリスク管理節)にあった「日次ドローダウン制限」(その日の
損失が一定%を超えたら新規エントリーを停止する)を再現できる。

使い方:
  .venv/bin/python perp_backtest.py --symbol BTCUSDT
  .venv/bin/python perp_backtest.py --symbol ETHUSDT --interval 4h --limit 1000 --leverage 3
  .venv/bin/python perp_backtest.py --symbol BTCUSDT --daily-loss-limit -20
"""
from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass, field

import config
import perp_market_data
from perp_paper_trader import compute_pnl_pct, decide_exit_reason
from perp_signals import compute_signal


@dataclass
class BacktestTrade:
    direction: str
    entry_price: float
    exit_price: float
    opened_at: float
    closed_at: float
    reason: str
    pnl_pct: float


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    # 検証期間の最初から最後まで、シグナルを無視してただロング(買い持ち)
    # し続けていた場合の値上がり率(%)。戦略の成績がシグナルの予測力による
    # ものか、単に検証期間中の全体的な値動き(上昇トレンド)に乗っかって
    # いただけかを見分けるための比較対象(buy_and_hold_leveraged_pnl_pctは
    # 同じレバレッジをかけた場合の参考値)。
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
        """全取引の損益率(%)の単純合計(複利では計算しない、常に同じ証拠金
        サイズで取引し続けたと仮定した場合の目安)。
        """
        return sum(t.pnl_pct for t in self.trades)

    @property
    def max_drawdown_pct(self) -> float:
        """累積損益(単純合計)のこれまでのピークからの最大下落幅(マイナス値)。"""
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


def run_backtest(
    symbol: str,
    candles: list[tuple[float, float]],
    leverage: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_seconds: int,
    daily_loss_limit_pct: float | None = None,
    ema_short_period: int = 20,
    ema_long_period: int = 50,
    rsi_period: int = 14,
) -> BacktestResult:
    """candles((時刻, 終値)のペア、古い順)に対してシグナルロジックを
    機械的に適用し、シミュレーション結果を返す(純粋関数、ネットワーク
    非依存。単体テストしやすい設計)。

    各時点のシグナルは`candles[:i+1]`(その時点までのデータ)だけを使って
    計算するため、未来の値を覗き見ることはない。1つのポジションが
    決済されるまでは次のシグナルを待つ(同時に複数持たない、シンプルな
    検証のため)。
    """
    result = BacktestResult()
    warmup = max(ema_long_period, rsi_period + 1)
    if len(candles) <= warmup:
        return result

    start_price = candles[warmup][1]
    end_price = candles[-1][1]
    if start_price > 0:
        result.buy_and_hold_pnl_pct = (end_price - start_price) / start_price * 100
        result.buy_and_hold_leveraged_pnl_pct = result.buy_and_hold_pnl_pct * leverage

    position: dict | None = None
    daily_pnl: dict[str, float] = {}

    for i in range(warmup, len(candles)):
        now, price = candles[i]

        if position is not None:
            reason = decide_exit_reason(
                position["direction"],
                position["entry_price"],
                price,
                leverage,
                position["opened_at"],
                now,
                take_profit_pct,
                stop_loss_pct,
                max_hold_seconds,
            )
            if reason is not None:
                pnl_pct = compute_pnl_pct(position["direction"], position["entry_price"], price, leverage)
                result.trades.append(
                    BacktestTrade(
                        direction=position["direction"],
                        entry_price=position["entry_price"],
                        exit_price=price,
                        opened_at=position["opened_at"],
                        closed_at=now,
                        reason=reason,
                        pnl_pct=pnl_pct,
                    )
                )
                day_key = _day_key(now)
                daily_pnl[day_key] = daily_pnl.get(day_key, 0.0) + pnl_pct
                position = None
            continue

        day_key = _day_key(now)
        if daily_loss_limit_pct is not None and daily_pnl.get(day_key, 0.0) <= daily_loss_limit_pct:
            continue  # 日次ドローダウン制限に達した日は新規エントリーしない

        closes_so_far = [c for _, c in candles[: i + 1]]
        signal = compute_signal(
            symbol,
            closes_so_far,
            funding_rate=None,
            ema_short_period=ema_short_period,
            ema_long_period=ema_long_period,
            rsi_period=rsi_period,
        )
        if signal is not None and signal.direction != "NEUTRAL":
            position = {"direction": signal.direction, "entry_price": price, "opened_at": now}

    return result


def _print_report(result: BacktestResult, symbol: str, leverage: float) -> None:
    if not result.trades:
        print("取引が1件も発生しませんでした(シグナル条件を満たす場面が無かった、またはデータ不足)。")
        return

    print(f"=== {symbol} バックテスト結果(レバレッジ{leverage}倍) ===")
    print(f"取引数: {len(result.trades)}件")
    print(f"勝率: {result.win_rate:.1f}%")
    print(f"合計損益(単純合計、複利無し): {result.total_pnl_pct:+.1f}%")
    print(f"平均損益/取引: {statistics.fmean(t.pnl_pct for t in result.trades):+.2f}%")
    print(f"最大ドローダウン: {result.max_drawdown_pct:.1f}%")

    reasons: dict[str, int] = {}
    for t in result.trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    print(f"決済理由の内訳: {reasons}")

    print(
        f"\n--- 比較: シグナルを無視してただ買い持ちしていた場合(Buy & Hold) ---\n"
        f"レバレッジ無し: {result.buy_and_hold_pnl_pct:+.1f}% / "
        f"同じ{leverage}倍のレバレッジをかけた場合: {result.buy_and_hold_leveraged_pnl_pct:+.1f}%"
    )
    if result.total_pnl_pct <= result.buy_and_hold_leveraged_pnl_pct:
        print(
            "→ 戦略の成績がBuy & Holdを上回っていません。シグナルに予測力があるというより、"
            "検証期間中の全体的な値動き(トレンド)に助けられていただけの可能性が高いです。"
        )
    else:
        print("→ 戦略の成績がBuy & Holdを上回っています(それでもサンプル数・期間が十分かは別途確認すること)。")

    print(
        "\n※ 過去データでの結果は将来の成績を保証しません。複数銘柄・複数期間で確認し、"
        "一貫して悪くない結果が出るかを見てから、実資金投入を検討すること。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="パーペチュアルシグナルのバックテストツール(実験的機能)")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default=config.PERP_KLINE_INTERVAL)
    parser.add_argument("--limit", type=int, default=1500, help="取得するローソク足の本数(Binance Futuresの上限は1500)")
    parser.add_argument("--leverage", type=float, default=config.PERP_PAPER_LEVERAGE)
    parser.add_argument("--take-profit", type=float, default=config.PERP_PAPER_TAKE_PROFIT_PCT)
    parser.add_argument("--stop-loss", type=float, default=config.PERP_PAPER_STOP_LOSS_PCT)
    parser.add_argument("--max-hold-seconds", type=int, default=config.PERP_PAPER_MAX_HOLD_SECONDS)
    parser.add_argument(
        "--daily-loss-limit",
        type=float,
        default=None,
        help="1日の損益(%)がこの値(マイナス)を下回ったら、その日は新規エントリーを停止する(既定: 制限なし)",
    )
    args = parser.parse_args()

    candles = perp_market_data.fetch_klines_with_time(args.symbol, args.interval, args.limit)
    if candles is None:
        print("価格データの取得に失敗しました(ネットワーク/シンボル名を確認してください)。")
        return

    result = run_backtest(
        args.symbol,
        candles,
        args.leverage,
        args.take_profit,
        args.stop_loss,
        args.max_hold_seconds,
        daily_loss_limit_pct=args.daily_loss_limit,
    )
    _print_report(result, args.symbol, args.leverage)


if __name__ == "__main__":
    main()
