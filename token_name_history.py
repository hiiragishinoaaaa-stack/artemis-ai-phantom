"""同じ名前/ティッカーを使い回すなりすましトークンの検出。

pump.funでは、既に伸びたトークンと同じ名前・同じティッカー($SYMBOL)を
付けた別mintのトークンを後から出す「なりすまし」が頻発する(元のトークンが
既に注目されていることに便乗し、無関係の新規トークンへ買いを誘導する手口)。
名前だけを見ているとどちらも同じに見えてしまい、なりすましの方が高スコア・
高優先度で通知されてしまうことがある。

このモジュールは、これまでに観測した「名前/ティッカー → 最初に見たmint」の
対応を記録しておき、後から同じ名前/ティッカーを名乗る別mintが現れたら
検出できるようにする(creator_blocklist.pyと同じ考え方。外部サービス不要、
うち自身の観測結果のみで完結する)。config.TOKEN_NAME_HISTORY_FILE_PATH
(JSON)へ永続化するため、サービス再起動を挟んでも記憶が保持される。

なお、ミームコイン界隈では"Doge"や"Trump"のような一般的な単語の名前が
偶然重複することも珍しくないため、これは「絶対にラグ」という確定的な
判定ではなく、あくまで注意を促すための減点シグナルとして扱う
(scoring._score_duplicate_name参照。通知自体は止めない)。
"""
from __future__ import annotations

import json
import logging

import config

logger = logging.getLogger("phantom_sniper")


def _normalize(value: str) -> str:
    return value.strip().casefold()


class TokenNameHistory:
    """正規化した名前/ティッカー -> 最初に観測したmint のマッピングを保持するクラス。"""

    def __init__(self) -> None:
        self._by_name: dict[str, str] = {}
        self._by_symbol: dict[str, str] = {}
        self._load()

    def check_and_record(self, mint: str, name: str, symbol: str, now: float | None = None) -> str | None:
        """この(name, symbol)が過去に別mintで観測済みなら、その理由を返す。

        初めて見る名前/ティッカーの場合はNoneを返し、以後の比較対象として
        このmintを記録する(既に記録済みの名前/ティッカーは上書きしない。
        常に「最初に見たmint」と比較し続けるため)。
        """
        del now  # 現状は時刻を記録に使わないが、将来の拡張(初観測日時の表示等)用に受け取れるようにしている。
        reason = self._check(mint, name, symbol)
        self._record(mint, name, symbol)
        return reason

    def _check(self, mint: str, name: str, symbol: str) -> str | None:
        normalized_name = _normalize(name)
        normalized_symbol = _normalize(symbol)

        if normalized_name:
            original_mint = self._by_name.get(normalized_name)
            if original_mint and original_mint != mint:
                return f"同じ名前「{name.strip()}」を名乗るトークンが既出です(先行mint: {original_mint})"

        if normalized_symbol:
            original_mint = self._by_symbol.get(normalized_symbol)
            if original_mint and original_mint != mint:
                return f"同じティッカー「${symbol.strip()}」を名乗るトークンが既出です(先行mint: {original_mint})"

        return None

    def _record(self, mint: str, name: str, symbol: str) -> None:
        normalized_name = _normalize(name)
        normalized_symbol = _normalize(symbol)
        changed = False

        if normalized_name and normalized_name not in self._by_name:
            self._by_name[normalized_name] = mint
            changed = True
        if normalized_symbol and normalized_symbol not in self._by_symbol:
            self._by_symbol[normalized_symbol] = mint
            changed = True

        if changed:
            self._save()

    def _load(self) -> None:
        path = config.TOKEN_NAME_HISTORY_FILE_PATH
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                by_name = data.get("by_name")
                by_symbol = data.get("by_symbol")
                if isinstance(by_name, dict):
                    self._by_name = {str(k): str(v) for k, v in by_name.items()}
                if isinstance(by_symbol, dict):
                    self._by_symbol = {str(k): str(v) for k, v in by_symbol.items()}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("token_name_history: 読み込みに失敗しました: %s", exc)

    def _save(self) -> None:
        path = config.TOKEN_NAME_HISTORY_FILE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {"by_name": self._by_name, "by_symbol": self._by_symbol},
                    f,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            tmp_path.replace(path)
        except OSError as exc:
            logger.warning("token_name_history: 保存に失敗しました: %s", exc)
