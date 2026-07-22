"""パーペチュアルのペーパートレード(モック実行、実資金は一切動かさない)。

perp_signals.pyが出したシグナルを、実際の取引所に発注する代わりに
「もし建てていたら」というシミュレーションとして記録する。レバレッジを
かけた場合の損益率をシミュレートし、Discordへ通知するだけの機能
(config.PERP_PAPER_TRADING_ENABLED、既定true。実資金を動かさないため
既定ONにしている)。

実際の取引所APIへの発注(本物のパーペチュアル自動売買)は未実装。
将来実装する場合は、このモジュールと同じインターフェース
(open_position/close_position)を持つ実発注版クラスに差し替える設計を
想定している(README.mdの「パーペチュアル(実験的機能)」参照)。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

import config

logger = logging.getLogger("phantom_sniper")


@dataclass
class PaperPosition:
    symbol: str
    direction: str  # "LONG" | "SHORT"
    entry_price: float
    leverage: float
    opened_at: float
    closed: bool = False
    close_reason: str = ""
    exit_price: float = 0.0
    pnl_pct: float = 0.0  # レバレッジ適用後の損益率(%)
    closed_at: float = 0.0


def _raw_change_pct(direction: str, entry_price: float, current_price: float) -> float:
    """レバレッジ適用前の値動き率(%)。LONGは値上がりが利益、SHORTは値下がりが利益。"""
    if entry_price <= 0:
        return 0.0
    change = (current_price - entry_price) / entry_price * 100
    return change if direction == "LONG" else -change


def compute_pnl_pct(direction: str, entry_price: float, exit_price: float, leverage: float) -> float:
    """レバレッジ適用後の損益率(%)。perp_backtest.pyからも同じ計算式を使うための公開関数。"""
    return _raw_change_pct(direction, entry_price, exit_price) * leverage


def decide_exit_reason(
    direction: str,
    entry_price: float,
    current_price: float,
    leverage: float,
    opened_at: float,
    now: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_seconds: int,
) -> str | None:
    """レバレッジ適用後の損益率から手仕舞い判定する(純粋関数)。position_tracker.
    decide_exit_reasonと同じ設計方針。
    """
    if entry_price <= 0:
        return None
    pnl_pct = compute_pnl_pct(direction, entry_price, current_price, leverage)
    if pnl_pct >= take_profit_pct:
        return "take_profit"
    if pnl_pct <= stop_loss_pct:
        return "stop_loss"
    if now - opened_at >= max_hold_seconds:
        return "max_hold"
    return None


class PaperPerpTracker:
    """symbol -> PaperPosition のマッピングを保持し、JSONへ永続化するクラス。"""

    def __init__(self) -> None:
        self._positions: dict[str, PaperPosition] = {}
        self._load()

    def has_open_position(self, symbol: str) -> bool:
        position = self._positions.get(symbol)
        return position is not None and not position.closed

    def open_positions(self) -> list[PaperPosition]:
        return [p for p in self._positions.values() if not p.closed]

    def open_position(self, symbol: str, direction: str, entry_price: float, leverage: float, now: float) -> PaperPosition:
        position = PaperPosition(
            symbol=symbol, direction=direction, entry_price=entry_price, leverage=leverage, opened_at=now
        )
        self._positions[symbol] = position
        self._save()
        return position

    def close_position(self, position: PaperPosition, exit_price: float, reason: str, now: float) -> None:
        position.closed = True
        position.close_reason = reason
        position.exit_price = exit_price
        position.closed_at = now
        position.pnl_pct = round(compute_pnl_pct(position.direction, position.entry_price, exit_price, position.leverage), 2)
        self._save()

    def _load(self) -> None:
        path = config.PERP_POSITIONS_FILE_PATH
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._positions = {symbol: PaperPosition(**fields) for symbol, fields in data.items()}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("perp_paper_trader: 読み込みに失敗しました: %s", exc)

    def _save(self) -> None:
        path = config.PERP_POSITIONS_FILE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            data = {symbol: asdict(position) for symbol, position in self._positions.items()}
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            tmp_path.replace(path)
        except OSError as exc:
            logger.warning("perp_paper_trader: 保存に失敗しました: %s", exc)
