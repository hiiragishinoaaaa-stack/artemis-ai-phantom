"""DEX卒業(migration)後のトークンの観察・チェックポイント管理ロジック。

pumpportal_clientのsubscribeMigrationイベントを受けたトークンについて、
config.MIGRATION_CHECKPOINTS_SECONDS(既定0/60/300/900秒)の各時点で
DexScreenerのスナップショットを取り込めるよう状態を保持する。実際の
DexScreener取得はmain.pyが行い、取得結果を`apply_snapshot()`でこの
モジュールへ反映する(このモジュール自体はHTTP通信を一切行わない)。

このモジュールはネットワーク・WebSocket・時刻取得(time.time())に一切
依存しない(呼び出し側が現在時刻を明示的に渡す)。そのためMT5に依存しない
mt5_ai_trader/risk_manager.py等と同じく、単体テストがネットワークなしで
書ける設計にしている(詳細はtests/test_token_watcher.py参照)。
"""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass
class TrackedToken:
    """DEX卒業後、観察中(または観察済み)の1トークンの状態。"""

    mint: str
    name: str
    symbol: str
    migrated_at: float
    # DexScreenerから取得できた最新のペアURL(通知メッセージ用)。
    dexscreener_url: str = ""
    # DexScreenerにまだペアが見つかっていない(卒業直後でインデックスが
    # 追いついていない等)場合はFalseのまま。scoring.pyはこの場合、全項目
    # 0点として扱う。
    has_pair_data: bool = False
    buys_m5: int = 0
    sells_m5: int = 0
    # 直近5分のユニークな買い手数(DexScreenerのbuyersフィールド)。
    # 少数のウォレットが自作自演で買い増して見かけの活気を演出する
    # ボット/ウォッシュトレード対策として、買い件数とは別に見る。
    unique_buyers_m5: int = 0
    volume_m5_usd: float = 0.0
    price_change_m5_pct: float = 0.0
    liquidity_usd: float = 0.0
    market_cap_usd: float = 0.0
    # RugCheckレポートを取得済みかどうか(1トークンにつき1回だけ取得する
    # ため、main.pyがこのフラグで二重取得を防ぐ)。
    rugcheck_checked: bool = False
    # RugCheckが"danger"レベルのリスクフラグを検出した場合True。
    # scoring.pyはこの場合スコアを強制的に0点にする。
    rugcheck_danger: bool = False
    rugcheck_danger_reason: str = ""
    # RugCheckレポートに含まれる発行者(creator)のウォレットアドレス。
    # creator_blocklist.CreatorBlocklistでの照合に使う。
    creator: str = ""
    # creator_blocklistで「過去に問題のあった発行者」と判定された場合、
    # その理由が入る(空文字ならブロック対象外)。scoring.pyはこの場合
    # スコアを強制的に0点にする(名前を変えて再発行されても検出できる)。
    blocked_creator_reason: str = ""
    # config.MIGRATION_CHECKPOINTS_SECONDSのうち、次に処理すべきチェック
    # ポイントのインデックス。最後まで処理し終えるとfinished=Trueになる。
    checkpoint_index: int = 0
    finished: bool = False
    # これまでに通知した最高の通知レベル("LOW"/"WATCH"/"HIGH"またはNone)。
    # スコアが上昇して通知ラインを更新した瞬間だけ再通知するために使う。
    notified_tier: str | None = None


class TokenWatcher:
    """DEX卒業後のトークンを観察し、チェックポイントごとの再評価対象を管理するクラス。"""

    def __init__(self) -> None:
        self._tokens: dict[str, TrackedToken] = {}

    def __len__(self) -> int:
        return len(self._tokens)

    def get(self, mint: str) -> TrackedToken | None:
        return self._tokens.get(mint)

    def start_tracking(self, mint: str, name: str, symbol: str, now: float) -> TrackedToken:
        """DEX卒業(migration)イベントを受けて観察を開始する。

        同じmintが既に観察中の場合は既存のものをそのまま返す(重複した
        migrationイベントへの耐性)。
        """
        existing = self._tokens.get(mint)
        if existing is not None:
            return existing

        token = TrackedToken(mint=mint, name=name, symbol=symbol, migrated_at=now)
        self._tokens[mint] = token
        self._evict_oldest_if_over_capacity()
        return token

    def apply_snapshot(self, token: TrackedToken, pair: dict) -> None:
        """dexscreener_client.fetch_best_pair()が返したペア情報をtokenへ反映する。"""
        txns_m5 = (pair.get("txns") or {}).get("m5") or {}
        token.buys_m5 = int(txns_m5.get("buys") or 0)
        token.sells_m5 = int(txns_m5.get("sells") or 0)
        token.unique_buyers_m5 = int((pair.get("buyers") or {}).get("m5") or 0)
        token.volume_m5_usd = float((pair.get("volume") or {}).get("m5") or 0.0)
        token.price_change_m5_pct = float((pair.get("priceChange") or {}).get("m5") or 0.0)
        token.liquidity_usd = float((pair.get("liquidity") or {}).get("usd") or 0.0)
        token.market_cap_usd = float(pair.get("marketCap") or pair.get("fdv") or 0.0)
        url = pair.get("url")
        if url:
            token.dexscreener_url = str(url)
        token.has_pair_data = True

    def apply_rugcheck_report(self, token: TrackedToken, danger_reason: str | None, creator: str | None) -> None:
        """rugcheck_client.extract_danger_reason()/extract_creator()の結果をtokenへ反映する。

        danger_reasonがNoneでない場合、"danger"レベルのリスクが検出された
        ことを示す(scoring.pyがこの場合スコアを強制的に0点にする)。
        """
        token.rugcheck_checked = True
        token.rugcheck_danger = danger_reason is not None
        token.rugcheck_danger_reason = danger_reason or ""
        if creator:
            token.creator = creator

    def apply_creator_block(self, token: TrackedToken, reason: str | None) -> None:
        """creator_blocklist.CreatorBlocklist.is_blocked()の結果をtokenへ反映する。"""
        token.blocked_creator_reason = reason or ""

    def due_for_checkpoint(self, now: float) -> list[TrackedToken]:
        """次のチェックポイント時刻を過ぎ、まだそのチェックポイントを処理していない
        トークンの一覧を返す(呼び出し側が定期的にポーリングする想定)。
        """
        due = []
        for token in self._tokens.values():
            if token.finished:
                continue
            checkpoint_seconds = config.MIGRATION_CHECKPOINTS_SECONDS[token.checkpoint_index]
            if now - token.migrated_at >= checkpoint_seconds:
                due.append(token)
        return due

    def current_checkpoint_seconds(self, token: TrackedToken) -> int:
        """このトークンが今まさに処理しようとしているチェックポイント(卒業からの経過秒)。"""
        return config.MIGRATION_CHECKPOINTS_SECONDS[token.checkpoint_index]

    def mark_checkpoint_done(self, token: TrackedToken) -> None:
        """チェックポイント処理後に1回呼び出し、次のチェックポイントへ進める。

        最後のチェックポイントを処理し終えたらfinished=Trueにする。
        """
        token.checkpoint_index += 1
        if token.checkpoint_index >= len(config.MIGRATION_CHECKPOINTS_SECONDS):
            token.finished = True

    def forget(self, mint: str) -> None:
        """観察を終了し、状態を破棄する(全チェックポイント処理後に呼ぶ)。"""
        self._tokens.pop(mint, None)

    def _evict_oldest_if_over_capacity(self) -> None:
        """MAX_TRACKED_TOKENSを超えたら、最も古いものから間引く
        (メモリの際限ない増加を防ぐ安全弁)。
        """
        if len(self._tokens) <= config.MAX_TRACKED_TOKENS:
            return
        oldest = min(self._tokens.values(), key=lambda t: t.migrated_at)
        self._tokens.pop(oldest.mint, None)
