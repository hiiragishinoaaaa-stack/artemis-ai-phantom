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


def level_touched_on_dip(previous_price: float, current_price: float, level_price: float) -> bool:
    """価格が下落してこの水準に触れた(通過した)場合のみtrueを返す(純粋関数)。

    このグリッド戦略は「下がったら買い、少し上がったら利確売り」が前提
    (買い(ロング)専用)。だが、単純に「前回価格〜今回価格の区間に水準が
    入っていたか」だけで判定すると、価格が上昇して水準をまたいだ場合にも
    「買い」が発生してしまう。これは実際の指値注文の仕組みとも合わない
    (買い指値は現在価格より下に置くものであり、価格が上がって初めて
    通過した水準に、後から买い注文が入ることはない)。

    上昇相場が続く間はこの歪みのおかげで見かけ上勝率が高くなるが
    (常に「今動いている方向」に沿って建玉が開かれるため)、相場が反転
    すると同じ歪みが逆に働き、下落中にも買い増しを続けてしまい、含み損
    ばかりが積み上がる(実際にこの問題が発生し、一晩で複数銘柄の勝率が
    0%近くまで悪化した)。

    値下がりが実際に起きて(previous_price > current_price)、かつ
    その値下がり区間にlevel_priceが入っている場合のみtrueを返す。
    """
    if previous_price <= current_price:
        return False
    return current_price <= level_price <= previous_price


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


def compute_grid_pnl_pct(
    entry_price: float,
    exit_price: float,
    leverage: float,
    fee_pct_per_side: float = 0.0,
    funding_cost_pct: float = 0.0,
) -> float:
    """1回のグリッド往復(買い→売り)の損益率(%)。レバレッジ適用後、
    往復手数料(fee_pct_per_side×2×leverage)と、保有中に発生したファンディング
    コスト(funding_cost_pct、既定0。funding_cost_pct()で計算した値をそのまま
    渡す想定)を差し引く。
    """
    if entry_price <= 0:
        return 0.0
    raw_pct = (exit_price - entry_price) / entry_price * 100
    return raw_pct * leverage - 2 * fee_pct_per_side * leverage - funding_cost_pct


def funding_cost_pct(funding_rate_history: list[tuple[float, float]], opened_at: float, closed_at: float, leverage: float) -> float:
    """建玉を保有していた間(opened_at <= ファンディング発生時刻 < closed_at)
    に発生したファンディングコストの合計(レバレッジ込み、%)を返す。

    買い(ロング)専用のグリッド戦略なので、正のレート(ロングがショートへ
    支払う)はコスト、負のレートは収益として、そのまま符号付きで合算する。
    perp_grid_backtest.py(過去データを一括処理、bisectで高速化)と
    grid_paper_trader.py/grid_live_trader.py(1建玉ごとに都度APIから取得)の
    両方から使う共通関数。
    """
    if not funding_rate_history:
        return 0.0
    total_rate = sum(rate for t, rate in funding_rate_history if opened_at <= t < closed_at)
    return total_rate * 100 * leverage
