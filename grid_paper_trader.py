"""グリッドトレードのライブ・ペーパートレード(モック、実資金は一切動かさない)。

perp_sniper.pyのライブループから使う。グリッド戦略は同時に複数の建玉
(グリッド水準ごとに1つ)を持つため、`perp_paper_trader.py`(トレンド戦略、
1銘柄につき同時1建玉)とは別のトラッカーにしている。

各銘柄のグリッド中心価格は、その銘柄を初めて観測した時点の価格に固定する
(perp_grid_backtest.pyと同じ設計。以後は再起動するまで変わらない)。
config.PERP_GRID_POSITIONS_FILE_PATH(JSON)へ永続化するため、サービス
再起動を挟んでも保有中の建玉やグリッド中心を見失わない。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

import config
from grid_trading import compute_grid_levels, compute_grid_pnl_pct, compute_grid_pnl_pct_short

logger = logging.getLogger("phantom_sniper")


@dataclass
class GridPosition:
    symbol: str
    level_index: int
    entry_price: float
    opened_at: float
    side: str = "long"  # "long" | "short"
    closed: bool = False
    close_reason: str = ""
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    closed_at: float = 0.0


class GridPaperTracker:
    """symbolごとのグリッド中心価格と、建玉の履歴(決済済みも含めて全件)を
    保持するクラス。

    グリッドの各水準は決済されると再び使えるようになる設計だが、以前は
    (level_index, side)をキーにした辞書で1建玉しか保持できず、同じ水準を
    再利用するたびに過去の決済記録(勝敗・損益)が新しい建玉で上書きされて
    消えていた(実際にこのバグで、累計勝率・累計損益が時間とともに減る
    という事象が発生した)。symbolごとに追記専用のリストで保持することで、
    過去の記録を消さずに全件残す。
    """

    def __init__(self) -> None:
        self._centers: dict[str, float] = {}
        self._last_prices: dict[str, float] = {}
        self._positions: dict[str, list[GridPosition]] = {}
        self._load()

    def get_or_init_levels(self, symbol: str, current_price: float, range_pct: float, grid_count: int) -> list[float]:
        """このsymbolのグリッド中心価格をまだ決めていなければ、現在価格を
        中心に固定してから、グリッド水準の一覧を返す。
        """
        if symbol not in self._centers:
            self._centers[symbol] = current_price
            self._save()
        return compute_grid_levels(self._centers[symbol], range_pct, grid_count)

    def center_price(self, symbol: str) -> float | None:
        return self._centers.get(symbol)

    def last_price(self, symbol: str) -> float | None:
        """直前のポーリングで観測した価格(価格が実際にその水準を通過したか
        判定するための基準。Noneなら「まだ一度も観測していない」)。
        """
        return self._last_prices.get(symbol)

    def set_last_price(self, symbol: str, price: float) -> None:
        self._last_prices[symbol] = price
        self._save()

    def has_open_position(self, symbol: str, level_index: int, side: str = "long") -> bool:
        return any(
            p.level_index == level_index and p.side == side and not p.closed
            for p in self._positions.get(symbol, [])
        )

    def open_positions(self, symbol: str | None = None) -> list[GridPosition]:
        result = []
        for sym, positions in self._positions.items():
            if symbol is not None and sym != symbol:
                continue
            result.extend(p for p in positions if not p.closed)
        return result

    def all_positions(self, symbol: str) -> list[GridPosition]:
        """指定銘柄の建玉を、決済済み・保有中どちらも含めて全件返す(集計通知用)。"""
        return list(self._positions.get(symbol, []))

    def open_position(
        self, symbol: str, level_index: int, entry_price: float, now: float, side: str = "long"
    ) -> GridPosition:
        position = GridPosition(symbol=symbol, level_index=level_index, entry_price=entry_price, opened_at=now, side=side)
        self._positions.setdefault(symbol, []).append(position)
        self._save()
        return position

    def close_position(
        self,
        position: GridPosition,
        exit_price: float,
        reason: str,
        now: float,
        leverage: float,
        fee_pct_per_side: float,
        funding_cost_pct: float = 0.0,
    ) -> None:
        position.closed = True
        position.close_reason = reason
        position.exit_price = exit_price
        position.closed_at = now
        pnl_fn = compute_grid_pnl_pct_short if position.side == "short" else compute_grid_pnl_pct
        position.pnl_pct = round(pnl_fn(position.entry_price, exit_price, leverage, fee_pct_per_side, funding_cost_pct), 4)
        self._save()

    def _load(self) -> None:
        path = config.PERP_GRID_POSITIONS_FILE_PATH
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
            self._positions = {}
            for symbol, entries in positions_raw.items():
                # 旧形式(level_index/複合キーごとの辞書)のファイルが残っていても
                # 読み込めるよう、辞書ならvalues()をリストとして扱う(過去の記録を
                # 上書きしていた旧設計からの移行。新規保存は常にリスト形式)。
                if isinstance(entries, dict):
                    entries = list(entries.values())
                self._positions[symbol] = [GridPosition(**fields) for fields in entries]
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("grid_paper_trader: 読み込みに失敗しました: %s", exc)

    def _save(self) -> None:
        path = config.PERP_GRID_POSITIONS_FILE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            data = {
                "centers": self._centers,
                "last_prices": self._last_prices,
                "positions": {
                    symbol: [asdict(pos) for pos in positions]
                    for symbol, positions in self._positions.items()
                },
            }
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            tmp_path.replace(path)
        except OSError as exc:
            logger.warning("grid_paper_trader: 保存に失敗しました: %s", exc)
