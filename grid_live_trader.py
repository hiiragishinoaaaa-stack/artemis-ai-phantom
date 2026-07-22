"""グリッドトレードの実発注(Hyperliquid、⚠️⚠️⚠️実際に資金を動かす)。

`grid_paper_trader.py`(モック、実資金は一切動かさない)と同じ「symbol +
level_index ごとに1つの建玉」という設計だが、こちらは`hyperliquid_client.py`
経由で実際にHyperliquidへ注文を送信する。

新規建玉(買い)・利確決済(売り)は指値(Alo、Maker専用)を使い、
perp_grid_backtest.pyで検証したMaker手数料(0.015%)が実際に近い形で
適用されるようにしている。損切り決済だけは緊急性があるため成行
(Taker扱い)のまま。指値注文は送信直後には約定せず「板に並んだ
(pending)」状態になることが多いため、建玉には
pending_open/pending_close という中間状態がある(下記GridLivePosition
参照)。check_pending_opens()/check_pending_closes()を定期的に呼んで
約定を確認すること(perp_sniper.pyの_live_grid_loop参照)。

config.PERP_GRID_LIVE_ENABLED=true かつ config.PERP_GRID_LIVE_CONFIRMED_
RISK=true の両方が揃っていない限り、should_open_position()は常にFalseを
返すため何も実行されない(main.pyのtrade_executor.pyと同じ二重ゲート
設計)。既定は完全にOFF。有効化する前に必ずREADME.mdの「パーペチュアル
実発注(Hyperliquid、実験的機能)」を読み、少額のテスト専用ウォレット
(できればHYPERLIQUID_USE_TESTNET=trueのテストネット)で試すこと。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

import config
import hyperliquid_client
import hyperliquid_wallet
import perp_market_data
import perp_notifier
from grid_trading import compute_grid_pnl_pct

logger = logging.getLogger("phantom_sniper")


@dataclass
class GridLivePosition:
    symbol: str
    level_index: int
    entry_price: float
    size: float
    opened_at: float
    open_avg_price: float = 0.0
    pending_open: bool = False
    open_oid: int = 0
    pending_close: bool = False
    close_oid: int = 0
    pending_close_reason: str = ""
    closed: bool = False
    close_reason: str = ""
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    closed_at: float = 0.0


def is_ready() -> tuple[bool, str]:
    """実発注が実際に動く状態にあるかどうかを返す(perp_sniper.py起動時のログ用)。"""
    if not config.PERP_GRID_LIVE_ENABLED:
        return False, "PERP_GRID_LIVE_ENABLED=false"
    if not config.PERP_GRID_LIVE_CONFIRMED_RISK:
        return False, "PERP_GRID_LIVE_CONFIRMED_RISK=false"
    if hyperliquid_wallet.get_account() is None:
        return False, "HYPERLIQUID_PRIVATE_KEY未設定または不正"
    return True, "ready"


def should_open_position(open_position_count: int) -> tuple[bool, str]:
    """このタイミングで新規にグリッド建玉を発注すべきかを判定する(純粋関数)。"""
    ready, reason = is_ready()
    if not ready:
        return False, reason
    if open_position_count >= config.PERP_GRID_LIVE_MAX_OPEN_POSITIONS:
        return False, "max_positions_open"
    return True, "ok"


class GridLiveTracker:
    """symbol -> {level_index: GridLivePosition} を保持し、JSONへ永続化するクラス。"""

    def __init__(self) -> None:
        self._centers: dict[str, float] = {}
        self._last_prices: dict[str, float] = {}
        self._positions: dict[str, dict[int, GridLivePosition]] = {}
        self._load()

    def center_price(self, symbol: str) -> float | None:
        return self._centers.get(symbol)

    def set_center_price(self, symbol: str, price: float) -> None:
        if symbol not in self._centers:
            self._centers[symbol] = price
            self._save()

    def last_price(self, symbol: str) -> float | None:
        """直前のポーリングで観測した価格(価格が実際にその水準を通過したか
        判定するための基準。Noneなら「まだ一度も観測していない」)。
        """
        return self._last_prices.get(symbol)

    def set_last_price(self, symbol: str, price: float) -> None:
        self._last_prices[symbol] = price
        self._save()

    def has_open_position(self, symbol: str, level_index: int) -> bool:
        """その水準が何らかの形で「使用中」(指値待ち・保有中・決済指値待ち)
        かどうか。新規に買い注文を出すべきでない水準を判定するために使う。
        """
        pos = self._positions.get(symbol, {}).get(level_index)
        return pos is not None and not pos.closed

    def active_positions(self, symbol: str | None = None) -> list[GridLivePosition]:
        """決済済みでない建玉(指値待ち・保有中・決済指値待ち全て含む)。
        同時保有数の上限判定(should_open_position)に使う。
        """
        result = []
        for sym, levels in self._positions.items():
            if symbol is not None and sym != symbol:
                continue
            result.extend(p for p in levels.values() if not p.closed)
        return result

    def open_positions(self, symbol: str | None = None) -> list[GridLivePosition]:
        """実際に買いが約定済みの建玉(pending_openは含まない)。
        利確/損切り判定のループに使う(まだ買いが約定してない建玉に
        対して決済判定をするのは意味が無いため)。
        """
        result = []
        for sym, levels in self._positions.items():
            if symbol is not None and sym != symbol:
                continue
            result.extend(p for p in levels.values() if not p.closed and not p.pending_open)
        return result

    def pending_open_positions(self, symbol: str | None = None) -> list[GridLivePosition]:
        result = []
        for sym, levels in self._positions.items():
            if symbol is not None and sym != symbol:
                continue
            result.extend(p for p in levels.values() if not p.closed and p.pending_open)
        return result

    def pending_close_positions(self, symbol: str | None = None) -> list[GridLivePosition]:
        result = []
        for sym, levels in self._positions.items():
            if symbol is not None and sym != symbol:
                continue
            result.extend(p for p in levels.values() if not p.closed and p.pending_close)
        return result

    def all_positions(self, symbol: str) -> list[GridLivePosition]:
        return list(self._positions.get(symbol, {}).values())

    def record_open(self, symbol: str, level_index: int, entry_price: float, size: float, avg_price: float, now: float) -> GridLivePosition:
        """買いが即座に約定した場合に、その結果を建玉として記録する
        (execute_open()から呼ぶ想定。このメソッド自体は発注を行わない)。
        """
        position = GridLivePosition(
            symbol=symbol, level_index=level_index, entry_price=entry_price, size=size, opened_at=now, open_avg_price=avg_price
        )
        self._positions.setdefault(symbol, {})[level_index] = position
        self._save()
        return position

    def record_pending_open(self, symbol: str, level_index: int, level_price: float, size: float, oid: int, now: float) -> GridLivePosition:
        """指値の買い注文を送信し、板に並んだ(まだ約定していない)状態を
        記録する(execute_open()から呼ぶ想定)。entry_priceは指値価格
        (約定確認時にconfirm_open()で実際の約定価格に更新される)。
        """
        position = GridLivePosition(
            symbol=symbol,
            level_index=level_index,
            entry_price=level_price,
            size=size,
            opened_at=now,
            pending_open=True,
            open_oid=oid,
        )
        self._positions.setdefault(symbol, {})[level_index] = position
        self._save()
        return position

    def confirm_open(self, position: GridLivePosition, avg_price: float, filled_size: float, now: float) -> None:
        """pending_openだった建玉が実際に約定したことを確認できた時に呼ぶ。"""
        position.pending_open = False
        position.open_oid = 0
        position.open_avg_price = avg_price
        position.entry_price = avg_price or position.entry_price
        if filled_size:
            position.size = filled_size
        position.opened_at = now
        self._save()

    def remove_position(self, symbol: str, level_index: int) -> None:
        """pending_openの注文がキャンセル/拒否されて約定しなかった場合、
        建玉として存在しなかったことにする(その水準はまた新規に狙える
        ようにする)。
        """
        levels = self._positions.get(symbol)
        if levels and level_index in levels:
            del levels[level_index]
            self._save()

    def record_pending_close(self, position: GridLivePosition, oid: int, reason: str, now: float) -> None:
        """利確の指値決済注文を送信し、板に並んだ状態を記録する
        (execute_close()から呼ぶ想定)。"""
        position.pending_close = True
        position.close_oid = oid
        position.pending_close_reason = reason
        self._save()

    def cancel_pending_close(self, position: GridLivePosition) -> None:
        """決済の指値注文をキャンセルした(例: 損切りに切り替えるため)場合、
        pending_close状態を解除する。建玉自体は引き続き保有中のまま。
        """
        position.pending_close = False
        position.close_oid = 0
        position.pending_close_reason = ""
        self._save()

    def record_close(
        self,
        position: GridLivePosition,
        exit_price: float,
        reason: str,
        now: float,
        leverage: float,
        fee_pct_per_side: float,
        funding_cost_pct: float = 0.0,
    ) -> None:
        """決済(成行の損切り、または指値利確の約定確認)が完了した後に、
        その結果を反映する。
        """
        position.pending_close = False
        position.close_oid = 0
        position.pending_close_reason = ""
        position.closed = True
        position.close_reason = reason
        position.exit_price = exit_price
        position.closed_at = now
        position.pnl_pct = round(
            compute_grid_pnl_pct(position.entry_price, exit_price, leverage, fee_pct_per_side, funding_cost_pct), 4
        )
        self._save()

    def _load(self) -> None:
        path = config.PERP_GRID_LIVE_POSITIONS_FILE_PATH
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            self._centers = {str(k): float(v) for k, v in (data.get("centers") or {}).items()}
            self._last_prices = {str(k): float(v) for k, v in (data.get("last_prices") or {}).items()}
            positions_raw = data.get("positions") or {}
            self._positions = {
                symbol: {int(level_index): GridLivePosition(**fields) for level_index, fields in levels.items()}
                for symbol, levels in positions_raw.items()
            }
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("grid_live_trader: 読み込みに失敗しました: %s", exc)

    def _save(self) -> None:
        path = config.PERP_GRID_LIVE_POSITIONS_FILE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            data = {
                "centers": self._centers,
                "last_prices": self._last_prices,
                "positions": {
                    symbol: {str(level_index): asdict(pos) for level_index, pos in levels.items()}
                    for symbol, levels in self._positions.items()
                },
            }
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            tmp_path.replace(path)
        except OSError as exc:
            logger.warning("grid_live_trader: 保存に失敗しました: %s", exc)


def execute_open(
    tracker: GridLiveTracker, symbol: str, level_index: int, level_price: float, mid_price: float, now: float
) -> GridLivePosition | None:
    """指値(Alo、Maker)でHyperliquidへ買い注文を送信する。

    ほとんどの場合、すぐには約定せず「板に並んだ」状態(pending_open)に
    なる。約定確認はcheck_pending_opens()が別途行う。呼び出し前に
    should_open_position()がTrueであることを確認しておくこと(この関数
    自体は再チェックしない)。
    """
    if tracker.has_open_position(symbol, level_index):
        return None  # 既にこの水準は使用中(二重発注防止)

    size = round(config.PERP_GRID_LIVE_ORDER_USD / mid_price, 6) if mid_price > 0 else 0.0
    if size <= 0:
        logger.error("grid_live_trader: 発注サイズの計算に失敗しました symbol=%s mid_price=%s", symbol, mid_price)
        return None

    result = hyperliquid_client.place_post_only_buy(symbol, size, level_price)
    if not result.success:
        logger.error("grid_live_trader: 指値買い注文に失敗しました symbol=%s level=%s error=%s", symbol, level_index, result.error)
        perp_notifier.notify_grid_live_failure(symbol, level_index, "買い(指値)", result.error)
        return None

    if result.filled:
        # Aloは通常即約定しないが、万一即約定した場合はそのまま建玉として記録する
        position = tracker.record_open(
            symbol, level_index, result.avg_price or level_price, result.filled_size or size, result.avg_price, now
        )
        perp_notifier.notify_grid_live_opened(symbol, level_index, position.entry_price, position.size)
        return position

    logger.info("grid_live_trader: 指値買い注文が板に並びました symbol=%s level=%s oid=%s price=%s", symbol, level_index, result.oid, level_price)
    position = tracker.record_pending_open(symbol, level_index, level_price, size, result.oid, now)
    perp_notifier.notify_grid_live_order_placed(symbol, level_index, level_price, "買い")
    return position


def check_pending_opens(tracker: GridLiveTracker, now: float) -> None:
    """板に並んだままの買い指値注文について、約定したかどうかを確認する。"""
    for position in tracker.pending_open_positions():
        status = hyperliquid_client.query_order_status(position.open_oid)
        if not status.found:
            continue  # 確認できなかった。次回また試す
        if status.is_filled:
            tracker.confirm_open(position, status.avg_price or position.entry_price, status.filled_size or position.size, now)
            logger.info("grid_live_trader: 買い指値が約定しました symbol=%s level=%s avg_price=%s", position.symbol, position.level_index, position.entry_price)
            perp_notifier.notify_grid_live_opened(position.symbol, position.level_index, position.entry_price, position.size)
        elif not status.is_open:
            # 板から消えていて、約定でもない = キャンセル/拒否された
            logger.warning("grid_live_trader: 買い指値がキャンセル/拒否されました symbol=%s level=%s", position.symbol, position.level_index)
            tracker.remove_position(position.symbol, position.level_index)
            perp_notifier.notify_grid_live_failure(position.symbol, position.level_index, "買い(指値)", "注文がキャンセルまたは拒否されました")


def execute_close(tracker: GridLiveTracker, position: GridLivePosition, reason: str, now: float) -> bool:
    """建玉を決済する。損切りは緊急性があるため成行(Taker)、利確は
    指値(Alo、Maker)で送信する。

    利確の指値が既に板に並んでいる状態で損切り条件に転じた場合は、
    先にその指値をキャンセルしてから成行の損切りに切り替える
    (指値のまま放置すると、価格がさらに逆行しても約定せず損失が
    拡大し続けるリスクがあるため)。
    """
    if position.pending_open:
        return False  # まだ買いが約定していない建玉は決済しようがない

    if reason == "stop_loss":
        if position.pending_close:
            hyperliquid_client.cancel_order(position.symbol, position.close_oid)
            tracker.cancel_pending_close(position)
        result = hyperliquid_client.close_long(position.symbol, position.size, config.PERP_GRID_LIVE_SLIPPAGE)
        if not result.success:
            logger.error("grid_live_trader: 決済(損切り)発注に失敗しました symbol=%s level=%s error=%s", position.symbol, position.level_index, result.error)
            perp_notifier.notify_grid_live_failure(position.symbol, position.level_index, "決済(損切り)", result.error)
            return False
        exit_price = result.avg_price or position.entry_price
        funding_cost_pct = perp_market_data.estimate_funding_cost_pct(
            position.symbol, position.opened_at, now, config.PERP_GRID_LEVERAGE
        )
        tracker.record_close(
            position, exit_price, reason, now, config.PERP_GRID_LEVERAGE, config.PERP_GRID_LIVE_FEE_PCT_PER_SIDE, funding_cost_pct
        )
        perp_notifier.notify_grid_live_closed(position.symbol, position.level_index, reason, position.pnl_pct, position.entry_price, exit_price)
        logger.info("grid_live_trader: 決済(損切り)が成功しました symbol=%s level=%s pnl_pct=%s", position.symbol, position.level_index, position.pnl_pct)
        return True

    # take_profit: 既に利確の指値注文が板にある場合は何もしない(二重発注防止)
    if position.pending_close:
        return False

    tp_price = position.entry_price * (1 + config.PERP_GRID_TAKE_PROFIT_PCT / 100)
    result = hyperliquid_client.place_post_only_sell(position.symbol, position.size, tp_price)
    if not result.success:
        logger.error("grid_live_trader: 決済(利確・指値)注文に失敗しました symbol=%s level=%s error=%s", position.symbol, position.level_index, result.error)
        perp_notifier.notify_grid_live_failure(position.symbol, position.level_index, "決済(利確・指値)", result.error)
        return False

    if result.filled:
        exit_price = result.avg_price or tp_price
        funding_cost_pct = perp_market_data.estimate_funding_cost_pct(
            position.symbol, position.opened_at, now, config.PERP_GRID_LEVERAGE
        )
        tracker.record_close(
            position, exit_price, reason, now, config.PERP_GRID_LEVERAGE, config.PERP_GRID_LIVE_FEE_PCT_PER_SIDE, funding_cost_pct
        )
        perp_notifier.notify_grid_live_closed(position.symbol, position.level_index, reason, position.pnl_pct, position.entry_price, exit_price)
        return True

    logger.info("grid_live_trader: 利確の指値注文が板に並びました symbol=%s level=%s oid=%s price=%s", position.symbol, position.level_index, result.oid, tp_price)
    tracker.record_pending_close(position, result.oid, reason, now)
    perp_notifier.notify_grid_live_order_placed(position.symbol, position.level_index, tp_price, "利確売り")
    return True


def check_pending_closes(tracker: GridLiveTracker, now: float) -> None:
    """板に並んだままの利確指値注文について、約定したかどうかを確認する。"""
    for position in tracker.pending_close_positions():
        status = hyperliquid_client.query_order_status(position.close_oid)
        if not status.found:
            continue
        if status.is_filled:
            tp_price = position.entry_price * (1 + config.PERP_GRID_TAKE_PROFIT_PCT / 100)
            exit_price = status.avg_price or tp_price
            reason = position.pending_close_reason or "take_profit"
            funding_cost_pct = perp_market_data.estimate_funding_cost_pct(
                position.symbol, position.opened_at, now, config.PERP_GRID_LEVERAGE
            )
            tracker.record_close(
                position, exit_price, reason, now, config.PERP_GRID_LEVERAGE, config.PERP_GRID_LIVE_FEE_PCT_PER_SIDE, funding_cost_pct
            )
            logger.info("grid_live_trader: 利確指値が約定しました symbol=%s level=%s pnl_pct=%s", position.symbol, position.level_index, position.pnl_pct)
            perp_notifier.notify_grid_live_closed(position.symbol, position.level_index, reason, position.pnl_pct, position.entry_price, exit_price)
        elif not status.is_open:
            logger.warning("grid_live_trader: 利確指値がキャンセル/拒否されました symbol=%s level=%s", position.symbol, position.level_index)
            tracker.cancel_pending_close(position)
