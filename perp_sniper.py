"""ARTEMIS Phantom Sniper のパーペチュアル(先物)シグナル機能のエントリーポイント。

⚠️ 実験的機能。ミームコインのスキャナー本体(main.py)とは完全に独立した
別プロセス(動いていなくても本体には一切影響しない)。config.PERP_SYMBOLS
で指定した銘柄(既定BTCUSDT/ETHUSDT/SOLUSDT)について、一定間隔
(config.PERP_POLL_INTERVAL_SECONDS)でBinance Futuresの公開市場データから
EMAトレンド・RSI・モメンタム・ファンディングレートを組み合わせた簡易的な
ロング/ショートシグナルを計算し、閾値を超えたらDiscordへ通知する
(perp_signals.compute_signal参照)。

実際の取引所への発注は一切行わない。PERP_PAPER_TRADING_ENABLED=true
(既定)の場合、「もし建てていたら」のペーパートレード(モック、実資金は
動かない)としてレバレッジ込みの損益をシミュレートし、その結果も
Discordへ通知する。本物の自動売買に拡張する場合の設計はREADME.mdの
「パーペチュアル(実験的機能)」参照。

PERP_GRID_ENABLED=true(既定false)にすると、上記のトレンド追従とは別に
グリッドトレード(perp_grid_backtest.pyで検証した、一定レンジを固定
グリッドで区切り下がったら買い・少し上がったら利確売りを繰り返す戦略)も
同時にライブ・ペーパートレードする(こちらも実資金は動かさない)。
取引件数が非常に多くなりやすいため、1件ごとではなくPERP_GRID_SUMMARY_
INTERVAL_SECONDS間隔で集計をDiscordへ通知する(grid_paper_trader.py参照)。

PERP_GRID_LIVE_ENABLED=true(既定false、⚠️⚠️⚠️実際に資金を動かす)にすると、
グリッドトレードをHyperliquidへ実発注する(grid_live_trader.py参照)。
PERP_GRID_LIVE_ENABLED/PERP_GRID_LIVE_CONFIRMED_RISKの両方がtrueでない
限り何もしない(main.pyのtrade_executor.pyと同じ二重ゲート設計)。
PERP_GRID_ENABLED(ペーパートレード)とは独立して動く(両方同時に有効化
すれば、実発注と並行してペーパートレードの記録も見比べられる)。

PERP_ENABLED=falseの場合は何もせず終了する(既定OFF)。

grid_live_trader.py・hyperliquid_client.py(Hyperliquid公式SDK・eth_account
に依存)は、PERP_GRID_LIVE_ENABLED=trueの場合のみ遅延importする。既定の
シグナル通知・ペーパートレードだけを使う場合、これらの依存関係の
インストールは不要(requirements.txtには含めているが、実発注を使わない
なら実質使われない)。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

import config
import perp_market_data
import perp_notifier
from grid_paper_trader import GridPaperTracker
from grid_trading import compute_grid_levels, decide_grid_exit_reason, level_touched_on_dip
from logger import setup_logger
from perp_paper_trader import PaperPerpTracker, decide_exit_reason
from perp_signals import compute_signal

logger = logging.getLogger("phantom_sniper")

# symbol -> 直近にグリッド集計通知を送った時刻(プロセスの生存期間中のみ
# 保持すればよいため、あえて永続化していない。再起動直後は次のポーリングで
# すぐ1回送られるだけで実害は無い)。
_last_grid_summary_at: dict[str, float] = {}


def _process_symbol(symbol: str, positions: PaperPerpTracker, now: float) -> None:
    closes = perp_market_data.fetch_closes(symbol, config.PERP_KLINE_INTERVAL, config.PERP_KLINE_LIMIT)
    if closes is None:
        logger.warning("perp_sniper: %sの価格データ取得に失敗しました", symbol)
        return
    funding_rate = perp_market_data.fetch_latest_funding_rate(symbol)

    signal = compute_signal(symbol, closes, funding_rate)
    if signal is None:
        return

    logger.info(
        "perp_sniper: symbol=%s direction=%s score=%d price=%s", symbol, signal.direction, signal.score, signal.price
    )

    if signal.direction == "NEUTRAL":
        return

    perp_notifier.notify_signal(signal)

    if not config.PERP_PAPER_TRADING_ENABLED:
        return
    if positions.has_open_position(symbol):
        return  # 既にこの銘柄でペーパー建玉を持っている間は増し玉しない
    position = positions.open_position(symbol, signal.direction, signal.price, config.PERP_PAPER_LEVERAGE, now)
    perp_notifier.notify_paper_trade_opened(position)


def _monitor_paper_positions(positions: PaperPerpTracker, now: float) -> None:
    for position in positions.open_positions():
        current_price = perp_market_data.fetch_mark_price(position.symbol)
        if current_price is None:
            continue
        reason = decide_exit_reason(
            direction=position.direction,
            entry_price=position.entry_price,
            current_price=current_price,
            leverage=position.leverage,
            opened_at=position.opened_at,
            now=now,
            take_profit_pct=config.PERP_PAPER_TAKE_PROFIT_PCT,
            stop_loss_pct=config.PERP_PAPER_STOP_LOSS_PCT,
            max_hold_seconds=config.PERP_PAPER_MAX_HOLD_SECONDS,
        )
        if reason is not None:
            positions.close_position(position, current_price, reason, now)
            perp_notifier.notify_paper_trade_closed(position)


async def _signal_loop(positions: PaperPerpTracker) -> None:
    while True:
        now = time.time()
        for symbol in config.PERP_SYMBOLS:
            await asyncio.to_thread(_process_symbol, symbol, positions, now)
        if config.PERP_PAPER_TRADING_ENABLED:
            await asyncio.to_thread(_monitor_paper_positions, positions, now)
        await asyncio.sleep(config.PERP_POLL_INTERVAL_SECONDS)


def _process_grid_symbol(symbol: str, grid_positions: GridPaperTracker, now: float) -> None:
    """1銘柄分のグリッド処理(新規約定判定+利確/損切り判定)。

    ポーリング間隔ごとの単一価格(マーク価格)から、「前回ポーリング時の
    価格〜今回の価格」の区間に水準が入っていたか(=実際に価格がそこを
    通過したか)を見る。前回価格が無い(起動直後の初回ポーリング)場合は、
    まだ何も「通過」していないので一切約定させず、今回価格を基準として
    記録するだけにとどめる(単純に「現在価格 <= 水準」だけで判定すると、
    中心価格より上にある水準は起動した瞬間にすべてtrueになってしまい、
    グリッドの半分近くが初回ポーリングで一斉に約定したように誤判定して
    しまう、という過去のバグへの対策)。

    さらに、買い(ロング)専用グリッドは「下がったら買い」が前提なので、
    level_touched_on_dip()により**値下がりで水準に触れた場合のみ**買う
    (上昇中に水準をまたいだだけでは買わない。詳細は同関数のdocstring
    参照。上昇相場でこの判定が無いと見かけ上の勝率が実力以上に高く出て、
    相場反転時に逆回転して含み損が積み上がるという実害が過去に発生した)。
    """
    current_price = perp_market_data.fetch_mark_price(symbol)
    if current_price is None:
        logger.warning("perp_sniper: %sの価格取得に失敗しました(グリッド)", symbol)
        return

    levels = grid_positions.get_or_init_levels(symbol, current_price, config.PERP_GRID_RANGE_PCT, config.PERP_GRID_COUNT)

    for position in grid_positions.open_positions(symbol):
        reason = decide_grid_exit_reason(
            position.entry_price, current_price, config.PERP_GRID_TAKE_PROFIT_PCT, config.PERP_GRID_STOP_LOSS_PCT
        )
        if reason is not None:
            funding_cost_pct = perp_market_data.estimate_funding_cost_pct(
                symbol, position.opened_at, now, config.PERP_GRID_LEVERAGE
            )
            grid_positions.close_position(
                position,
                current_price,
                reason,
                now,
                config.PERP_GRID_LEVERAGE,
                config.PERP_GRID_FEE_PCT_PER_SIDE,
                funding_cost_pct,
            )

    previous_price = grid_positions.last_price(symbol)
    grid_positions.set_last_price(symbol, current_price)
    if previous_price is not None:
        for level_index, level_price in enumerate(levels):
            if grid_positions.has_open_position(symbol, level_index):
                continue
            if level_touched_on_dip(previous_price, current_price, level_price):
                grid_positions.open_position(symbol, level_index, level_price, now)

    last_summary_at = _last_grid_summary_at.get(symbol, 0.0)
    if now - last_summary_at >= config.PERP_GRID_SUMMARY_INTERVAL_SECONDS:
        _last_grid_summary_at[symbol] = now
        _send_grid_summary(symbol, grid_positions)


def _send_grid_summary(symbol: str, grid_positions: GridPaperTracker) -> None:
    all_positions = grid_positions.all_positions(symbol)
    closed = [p for p in all_positions if p.closed]
    open_count = len(all_positions) - len(closed)
    win_rate = (sum(1 for p in closed if p.pnl_pct > 0) / len(closed) * 100) if closed else 0.0
    total_pnl_pct = sum(p.pnl_pct for p in closed)
    perp_notifier.notify_grid_summary(symbol, open_count, len(closed), win_rate, total_pnl_pct)


async def _grid_loop(grid_positions: GridPaperTracker) -> None:
    while True:
        now = time.time()
        for symbol in config.PERP_SYMBOLS:
            await asyncio.to_thread(_process_grid_symbol, symbol, grid_positions, now)
        await asyncio.sleep(config.PERP_GRID_POLL_INTERVAL_SECONDS)


def _process_grid_symbol_live(symbol: str, tracker: "GridLiveTracker", now: float) -> None:
    """⚠️⚠️⚠️ 1銘柄分のグリッド実発注処理(Hyperliquid、実際に資金を動かす)。

    _process_grid_symbol(ペーパートレード版)と同じロジックだが、実際に
    hyperliquid_client.py経由で発注する。symbolはBinance Futures表記
    ("BTCUSDT")のまま受け取り、Hyperliquidへの問い合わせ直前だけ変換する。

    grid_live_trader.py・hyperliquid_client.pyは、実発注が無効な環境でも
    import時点でHyperliquid SDK/eth_accountの不在によるエラーが起きない
    よう、ここで遅延importする(この関数自体もPERP_GRID_LIVE_ENABLED=true
    の場合しか呼ばれない、async_main参照)。
    """
    import hyperliquid_client
    from grid_live_trader import check_pending_closes, check_pending_opens, execute_close, execute_open
    from grid_live_trader import should_open_position as should_open_live_position

    hl_symbol = hyperliquid_client.to_hyperliquid_symbol(symbol)
    mid_price = hyperliquid_client.fetch_mid_price(hl_symbol)
    if mid_price is None:
        logger.warning("perp_sniper: %sの価格取得に失敗しました(グリッド実発注)", hl_symbol)
        return

    # 指値注文(買い・利確売り)が板に並んだままになっている分の約定確認を
    # 毎回まず行う(新規判定より前に行い、約定済みなら決済判定の対象に
    # 含められるようにする)。
    check_pending_opens(tracker, now)
    check_pending_closes(tracker, now)

    if tracker.center_price(hl_symbol) is None:
        tracker.set_center_price(hl_symbol, mid_price)
    levels = compute_grid_levels(tracker.center_price(hl_symbol), config.PERP_GRID_RANGE_PCT, config.PERP_GRID_COUNT)

    # open_positions()は買いが約定済みの建玉のみ(pending_openは含まない、
    # まだ約定していない建玉に利確/損切り判定をしても意味が無いため)。
    for position in tracker.open_positions(hl_symbol):
        reason = decide_grid_exit_reason(
            position.entry_price, mid_price, config.PERP_GRID_TAKE_PROFIT_PCT, config.PERP_GRID_STOP_LOSS_PCT
        )
        if reason is not None:
            execute_close(tracker, position, reason, now)

    # _process_grid_symbol(ペーパートレード版)と同じ理由で、値下がりで
    # 水準に触れた場合のみ約定させる(level_touched_on_dip参照)。前回価格が
    # 無ければ何も発注しない。
    previous_price = tracker.last_price(hl_symbol)
    tracker.set_last_price(hl_symbol, mid_price)
    if previous_price is None:
        return

    # active_positions()は指値待ち・保有中・決済指値待ちを全て含む
    # (同時保有数の上限は、まだ約定していない発注中の分も含めて数える)。
    open_count = len(tracker.active_positions())
    for level_index, level_price in enumerate(levels):
        if tracker.has_open_position(hl_symbol, level_index):
            continue
        if not level_touched_on_dip(previous_price, mid_price, level_price):
            continue
        should_open, _reason = should_open_live_position(open_count)
        if not should_open:
            break
        if execute_open(tracker, hl_symbol, level_index, level_price, mid_price, now) is not None:
            open_count += 1


async def _live_grid_loop(tracker: "GridLiveTracker") -> None:
    while True:
        now = time.time()
        for symbol in config.PERP_SYMBOLS:
            await asyncio.to_thread(_process_grid_symbol_live, symbol, tracker, now)
        await asyncio.sleep(config.PERP_GRID_POLL_INTERVAL_SECONDS)


async def async_main() -> None:
    if not config.PERP_ENABLED:
        logger.warning("perp_sniper: PERP_ENABLED=falseのため起動しません")
        return
    positions = PaperPerpTracker()
    logger.info(
        "perp_sniper: 監視を開始します symbols=%s interval=%s秒 paper_trading=%s grid_enabled=%s grid_live_enabled=%s",
        config.PERP_SYMBOLS,
        config.PERP_POLL_INTERVAL_SECONDS,
        config.PERP_PAPER_TRADING_ENABLED,
        config.PERP_GRID_ENABLED,
        config.PERP_GRID_LIVE_ENABLED,
    )

    tasks = [_signal_loop(positions)]
    if config.PERP_GRID_ENABLED:
        grid_positions = GridPaperTracker()
        tasks.append(_grid_loop(grid_positions))
    if config.PERP_GRID_LIVE_ENABLED:
        from grid_live_trader import GridLiveTracker, is_ready

        live_ready, live_status = is_ready()
        logger.info("perp_sniper: グリッド実発注 status=%s ready=%s", live_status, live_ready)
        live_tracker = GridLiveTracker()
        tasks.append(_live_grid_loop(live_tracker))
    await asyncio.gather(*tasks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ARTEMIS Phantom Sniper - Perpetual Signals (実験的機能)")
    parser.add_argument("--debug", action="store_true", help="DEBUGレベルの詳細ログを出力する")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logger(debug=args.debug)
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("perp_sniper: ユーザーにより停止されました")


if __name__ == "__main__":
    main()
