"""自動売買(trade_executor.py)で保有中の建玉(ポジション)管理。

⚠️ 実際に資金を動かす機能の一部。買った価格・数量・時刻を記録し、利確
(AUTO_TRADE_TAKE_PROFIT_PCT)・損切り(AUTO_TRADE_STOP_LOSS_PCT)・最大保有
時間(AUTO_TRADE_MAX_HOLD_SECONDS)のいずれかに達したかどうかを判定する。

判定ロジック(decide_exit_reason)はネットワーク・時刻取得に一切依存しない
純粋関数にしている(token_watcher.py等と同じ設計方針。単体テストしやすい
ようにするため)。実際に売る判断が正しいかどうかはtrade_executor.pyが
このモジュールの判定結果を使って行う。

config.POSITIONS_FILE_PATH(JSON)へ永続化するため、サービス再起動を挟んでも
保有中の建玉を見失わない(見失うとポジションを放置してしまい損失が
青天井になりかねないため、これは安全上重要)。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

import config

logger = logging.getLogger("phantom_sniper")


@dataclass
class Position:
    mint: str
    name: str
    symbol: str
    entry_price_usd: float
    entry_amount_sol: float
    token_amount_raw: int
    opened_at: float
    open_tx_signature: str
    closed: bool = False
    close_reason: str = ""  # "take_profit" | "stop_loss" | "max_hold" | "manual"
    close_tx_signature: str = ""
    exit_price_usd: float = 0.0
    pnl_pct: float = 0.0
    closed_at: float = 0.0


def decide_exit_reason(
    entry_price_usd: float,
    current_price_usd: float,
    opened_at: float,
    now: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_seconds: int,
) -> str | None:
    """現在の状況から手仕舞いすべきかどうかを判定する(純粋関数)。

    優先順位: 利確 > 損切り > 最大保有時間超過。entry_price_usdが0以下
    (未取得等)の場合は判定不能としてNoneを返す(ゼロ除算防止)。
    """
    if entry_price_usd <= 0:
        return None
    change_pct = (current_price_usd - entry_price_usd) / entry_price_usd * 100
    if change_pct >= take_profit_pct:
        return "take_profit"
    if change_pct <= stop_loss_pct:
        return "stop_loss"
    if now - opened_at >= max_hold_seconds:
        return "max_hold"
    return None


class PositionTracker:
    """mint -> Position のマッピングを保持し、JSONへ永続化するクラス。"""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._load()

    def open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if not p.closed]

    def open_count(self) -> int:
        return len(self.open_positions())

    def has_open_position(self, mint: str) -> bool:
        position = self._positions.get(mint)
        return position is not None and not position.closed

    def has_any_position(self, mint: str) -> bool:
        """過去に(既に決済済みのものも含めて)一度でも建てたことがあるか。

        同じトークンを何度も自動購入してしまわないようにするための
        ガード(should_auto_buyの呼び出し側=main.pyが使う)。
        """
        return mint in self._positions

    def open_position(
        self,
        mint: str,
        name: str,
        symbol: str,
        entry_price_usd: float,
        entry_amount_sol: float,
        token_amount_raw: int,
        open_tx_signature: str,
        now: float,
    ) -> Position:
        position = Position(
            mint=mint,
            name=name,
            symbol=symbol,
            entry_price_usd=entry_price_usd,
            entry_amount_sol=entry_amount_sol,
            token_amount_raw=token_amount_raw,
            opened_at=now,
            open_tx_signature=open_tx_signature,
        )
        self._positions[mint] = position
        self._save()
        return position

    def close_position(
        self, position: Position, exit_price_usd: float, close_tx_signature: str, reason: str, now: float
    ) -> None:
        position.closed = True
        position.close_reason = reason
        position.close_tx_signature = close_tx_signature
        position.exit_price_usd = exit_price_usd
        position.closed_at = now
        if position.entry_price_usd > 0:
            position.pnl_pct = round((exit_price_usd - position.entry_price_usd) / position.entry_price_usd * 100, 2)
        self._save()

    def _load(self) -> None:
        path = config.POSITIONS_FILE_PATH
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._positions = {mint: Position(**fields) for mint, fields in data.items()}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("position_tracker: 読み込みに失敗しました: %s", exc)

    def _save(self) -> None:
        path = config.POSITIONS_FILE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            data = {mint: asdict(position) for mint, position in self._positions.items()}
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            tmp_path.replace(path)
        except OSError as exc:
            logger.warning("position_tracker: 保存に失敗しました: %s", exc)
