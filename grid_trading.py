"""グリッドトレード戦略の共通ロジック(価格レンジ・グリッド水準の計算、
利確/損切り判定)。`perp_grid_backtest.py`(過去のローソク足で検証)と
`grid_paper_trader.py`(perp_sniper.pyのライブ・ペーパートレードで使用)の
両方から使う、ネットワーク・時刻取得に依存しない純粋関数のみのモジュール。
"""
from __future__ import annotations


def compute_grid_levels(center_price: float, range_pct: float, grid_count: int) -> list[float]:
    """中心価格からrange_pct(±%)の範囲をgrid_count等分したグリッド水準の
    一覧(安い順)を返す。center_price<=0またはgrid_count<=0なら空リスト。
    """
    if center_price <= 0 or grid_count <= 0:
        return []
    lower = center_price * (1 - range_pct / 100)
    upper = center_price * (1 + range_pct / 100)
    if upper <= lower:
        return []
    step = (upper - lower) / grid_count
    return [lower + i * step for i in range(grid_count + 1)]


def decide_grid_exit_reason(
    entry_price: float, current_price: float, take_profit_pct: float, stop_loss_pct: float
) -> str | None:
    """1つのグリッド建玉について、現在価格(単一のスナップショット)から
    利確/損切りに達したかを判定する(純粋関数)。

    perp_grid_backtest.run_grid_backtest()はローソク足の高値・安値で
    「その足の間に触れたか」を判定するのに対し、こちらはライブでの
    ポーリング時点の単一価格で判定する(ポーリング間隔中の細かい値動きは
    見えないため、バックテストよりやや保守的=利確/損切りの検知が遅れ
    得る見積もりになる)。
    """
    if entry_price <= 0:
        return None
    change_pct = (current_price - entry_price) / entry_price * 100
    if change_pct >= take_profit_pct:
        return "take_profit"
    if change_pct <= stop_loss_pct:
        return "stop_loss"
    return None


def compute_grid_pnl_pct(entry_price: float, exit_price: float, leverage: float, fee_pct_per_side: float = 0.0) -> float:
    """1回のグリッド往復(買い→売り)の損益率(%)。レバレッジ適用後、
    往復手数料(fee_pct_per_side×2×leverage)を差し引く。
    """
    if entry_price <= 0:
        return 0.0
    raw_pct = (exit_price - entry_price) / entry_price * 100
    return raw_pct * leverage - 2 * fee_pct_per_side * leverage
