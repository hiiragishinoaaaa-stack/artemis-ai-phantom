"""グリッドトレードの実発注(Hyperliquid、⚠️⚠️⚠️実際に資金を動かす)。

`grid_paper_trader.py`(モック、実資金は一切動かさない)と同じ「symbol +
level_index ごとに1つの建玉」という設計だが、こちらは`hyperliquid_client.py`
経由で実際にHyperliquidへ成行相当(IoC指値、Taker扱い)の注文を送信する。

config.PERP_GRID_LIVE_ENABLED=true かつ config.PERP_GRID_LIVE_CONFIRMED_
RISK=true の両方が揃っていない限り、should_open_position()は常にFalseを
返すため何も実行されない(main.pyのtrade_executor.pyと同じ二重ゲート
設計)。既定は完全にOFF。有効化する前に必ずREADME.mdの「パーペチュアル
実発注(Hyperliquid、実験的機能)」を読み、少額のテスト専用ウォレット
(できればHYPERLIQUID_USE_TESTNET=trueのテストネット)で試すこと。

perp_grid_backtest.pyで検証したのはMaker手数料(0.015%)込みの結果だが、
market_open/market_closeはTaker扱い(0.045%)になるため、検証結果より
実際の収益は悪化する可能性が高い。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

import config
import hyperliquid_client
import hyperliquid_wallet
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
        self._positions: dict[str, dict[int, GridLivePosition]] = {}
        self._load()

    def center_price(self, symbol: str) -> float | None:
        return self._centers.get(symbol)

    def set_center_price(self, symbol: str, price: float) -> None:
        if symbol not in self._centers:
            self._centers[symbol] = price
            self._save()

    def has_open_position(self, symbol: str, level_index: int) -> bool:
        pos = self._positions.get(symbol, {}).get(level_index)
        return pos is not None and not pos.closed

    def open_positions(self, symbol: str | None = None) -> list[GridLivePosition]:
        result = []
        for sym, levels in self._positions.items():
            if symbol is not None and sym != symbol:
                continue
            result.extend(p for p in levels.values() if not p.closed)
        return result

    def all_positions(self, symbol: str) -> list[GridLivePosition]:
        return list(self._positions.get(symbol, {}).values())

    def record_open(self, symbol: str, level_index: int, entry_price: float, size: float, avg_price: float, now: float) -> GridLivePosition:
        """実際の発注が成功した後に、その結果を建玉として記録する
        (execute_open()から呼ぶ想定。このメソッド自体は発注を行わない)。
        """
        position = GridLivePosition(
            symbol=symbol, level_index=level_index, entry_price=entry_price, size=size, opened_at=now, open_avg_price=avg_price
        )
        self._positions.setdefault(symbol, {})[level_index] = position
        self._save()
        return position

    def record_close(self, position: GridLivePosition, exit_price: float, reason: str, now: float, leverage: float, fee_pct_per_side: float) -> None:
        """実際の決済発注が成功した後に、その結果を反映する
        (execute_close()から呼ぶ想定。このメソッド自体は発注を行わない)。
        """
        position.closed = True
        position.close_reason = reason
        position.exit_price = exit_price
        position.closed_at = now
        position.pnl_pct = round(compute_grid_pnl_pct(position.entry_price, exit_price, leverage, fee_pct_per_side), 4)
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
    """実際にHyperliquidへ買い注文を送信し、成功したら建玉を記録する。

    呼び出し前にshould_open_position()がTrueであることを確認しておくこと
    (この関数自体は再チェックしない)。
    """
    size = round(config.PERP_GRID_LIVE_ORDER_USD / mid_price, 6) if mid_price > 0 else 0.0
    if size <= 0:
        logger.error("grid_live_trader: 発注サイズの計算に失敗しました symbol=%s mid_price=%s", symbol, mid_price)
        return None

    result = hyperliquid_client.open_long(symbol, size, config.PERP_GRID_LIVE_SLIPPAGE)
    if not result.success:
        logger.error("grid_live_trader: 買い発注に失敗しました symbol=%s level=%s error=%s", symbol, level_index, result.error)
        perp_notifier.notify_grid_live_failure(symbol, level_index, "買い", result.error)
        return None

    logger.info(
        "grid_live_trader: 買い発注が成功しました symbol=%s level=%s size=%s avg_price=%s",
        symbol,
        level_index,
        result.filled_size,
        result.avg_price,
    )
    position = tracker.record_open(symbol, level_index, level_price, result.filled_size or size, result.avg_price, now)
    perp_notifier.notify_grid_live_opened(symbol, level_index, position.entry_price, position.size)
    return position


def execute_close(tracker: GridLiveTracker, position: GridLivePosition, reason: str, now: float) -> bool:
    """実際にHyperliquidへ決済注文を送信し、成功したら建玉を決済済みにする。"""
    result = hyperliquid_client.close_long(position.symbol, position.size, config.PERP_GRID_LIVE_SLIPPAGE)
    if not result.success:
        logger.error(
            "grid_live_trader: 決済発注に失敗しました symbol=%s level=%s error=%s", position.symbol, position.level_index, result.error
        )
        perp_notifier.notify_grid_live_failure(position.symbol, position.level_index, "決済", result.error)
        return False

    exit_price = result.avg_price or position.entry_price
    tracker.record_close(
        position, exit_price, reason, now, config.PERP_GRID_LEVERAGE, config.PERP_GRID_LIVE_FEE_PCT_PER_SIDE
    )
    perp_notifier.notify_grid_live_closed(position.symbol, position.level_index, reason, position.pnl_pct, position.entry_price, exit_price)
    logger.info(
        "grid_live_trader: 決済が成功しました symbol=%s level=%s reason=%s pnl_pct=%s",
        position.symbol,
        position.level_index,
        reason,
        position.pnl_pct,
    )
    return True
