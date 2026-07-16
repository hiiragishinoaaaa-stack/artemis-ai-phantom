"""発行者(トークンのcreatorウォレット)のブラックリスト管理。

RugCheckで"danger"判定が出たトークン、または通知後に大暴落(既定-90%
以上、config.CREATOR_BLOCKLIST_CRASH_THRESHOLD_PCT)したトークンの発行者
ウォレットアドレスを記録しておき、同じ発行者が別の名前で新しいトークンを
出してきても、次回以降は即座にスコアを0点にできるようにする
(token_watcher.apply_creator_block / scoring._score_creator_blocklist参照)。

外部サービスへの登録は一切不要(RugCheckの無料レポートに含まれる
creatorフィールドと、うち自身の観察結果だけで完結する)。
config.CREATOR_BLOCKLIST_FILE_PATH(JSON)へ永続化するため、サービス再起動を
挟んでも記憶が保持される。
"""
from __future__ import annotations

import json
import logging

import config

logger = logging.getLogger("phantom_sniper")


class CreatorBlocklist:
    """発行者ウォレットアドレス -> ブロック理由 のマッピングを保持するクラス。"""

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}
        self._load()

    def __len__(self) -> int:
        return len(self._entries)

    def is_blocked(self, creator: str) -> str | None:
        """creatorがブロックリストに載っていれば理由を返す。載っていなければNone。"""
        if not creator:
            return None
        return self._entries.get(creator)

    def record(self, creator: str, reason: str) -> None:
        """creatorをブロックリストへ追加する(既に登録済みなら上書きしない)。"""
        if not creator or creator in self._entries:
            return
        self._entries[creator] = reason
        logger.info(
            "creator_blocklist: 発行者をブロックリストへ追加しました creator=%s reason=%s",
            creator,
            reason,
        )
        self._save()

    def _load(self) -> None:
        path = config.CREATOR_BLOCKLIST_FILE_PATH
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._entries = {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("creator_blocklist: 読み込みに失敗しました: %s", exc)

    def _save(self) -> None:
        path = config.CREATOR_BLOCKLIST_FILE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(self._entries, f, ensure_ascii=False, indent=2, sort_keys=True)
            tmp_path.replace(path)
        except OSError as exc:
            logger.warning("creator_blocklist: 保存に失敗しました: %s", exc)
