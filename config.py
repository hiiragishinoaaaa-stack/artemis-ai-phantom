"""アプリケーション全体の設定値。

pump.fun(Solana上のミームコイン発射台)の新規トークンが、ボンディング
カーブを卒業してRaydium等の実際のDEXへ移行(migration)したことを
PumpPortalの無料WebSocket API経由でリアルタイム検知し、卒業後の実際の
DEX取引状況(DexScreenerの公開APIから取得)を一定時間観察して、条件を
満たしたものだけDiscordへ通知する「フィルター付きスナイパー通知bot」。

自動売買・ウォレット操作は一切行わない(あくまで通知のみ)。実際に買うか
どうかはDiscord通知を見た人間が判断し、Phantom等のウォレットで手動操作する
想定。

## なぜ「卒業(migration)後」を見るのか(2026-07、設計変更)

当初はpump.fun上の個別トークンの売買イベントをPumpPortalの
subscribeTokenTradeでリアルタイム受信し、作成直後20〜120秒の初動を見る
設計だった。しかしsubscribeTokenTradeはPumpPortal公式のAPIキー+SOL入り
ウォレットが必要な従量課金機能であることが判明し、無料運用の方針と
合わなかった。

そこで、無料のsubscribeMigrationイベント(トークンが実際のDEXへ卒業した
瞬間)をトリガーに切り替え、卒業後の実際の取引状況は無料・APIキー不要の
DexScreener公開APIから取得する設計に変更した。DexScreenerはそもそも
卒業前のpump.funトークン(ボンディングカーブ上の仮想的な取引のみ)を
一切表示しないため、この2つの組み合わせは自然に噛み合う。

トレードオフ: 通知のタイミングが「作成から20〜120秒」ではなく「DEX卒業
した瞬間」になる(卒業は早くて数分、遅いと数時間後)。ただし卒業自体が
「ある程度本気で買われた証拠」でもあるため、質の面では悪くない。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- PumpPortal WebSocket接続 ---
# 無料・APIキー不要の公開WebSocket(https://pumpportal.fun/)。
# subscribeNewTokenで新規トークン作成イベントを、subscribeMigrationで
# 実際のDEXへの卒業イベントを受信する(どちらも無料。pumpportal_client.py
# 参照)。
PUMPPORTAL_WS_URL = os.getenv("PUMPPORTAL_WS_URL", "wss://pumpportal.fun/api/data")
# WebSocketが切断された場合に再接続を試みるまでの待機秒数。
RECONNECT_DELAY_SECONDS = _env_int("RECONNECT_DELAY_SECONDS", 5)

# --- DexScreener REST API接続(dexscreener_client.py) ---
# 無料・APIキー不要(https://docs.dexscreener.com/api/reference)。
# このエンドポイントはおおよそ60req/minのレート制限があるため、
# チェックポイント間隔・同時観察数と合わせて叩きすぎないよう注意。
DEXSCREENER_API_BASE_URL = os.getenv("DEXSCREENER_API_BASE_URL", "https://api.dexscreener.com")

# --- 卒業後の観察・スコアリング(token_watcher.py / scoring.py) ---
# 「条件を全部満たさなければ即破棄」ではなく、100点満点のスコア方式で
# 評価する。DEX卒業(migration)を検知してから、以下の秒数(migrated_atから
# の経過秒)ごとにDexScreenerを再取得してスコアを再計算し、その時点で
# スコアが通知ライン(WATCH_SCORE_THRESHOLD以上)を超えた瞬間に通知する。
# 最後の秒数(リストの最大値)で観察を打ち切る。0を含めているのは、卒業を
# 検知した瞬間に1回目の取得を行うため(DexScreenerのインデックスが
# 追いついていない場合はNoneが返り、次のチェックポイントまで待つ)。
MIGRATION_CHECKPOINTS_SECONDS: tuple[int, ...] = (0, 60, 300, 900)
# 同時に観察できるトークン数の上限(メモリの暴走防止)。
# これを超えた場合、最も古い(卒業が古い)ものから観察を打ち切る。
MAX_TRACKED_TOKENS = _env_int("MAX_TRACKED_TOKENS", 500)

# --- スコアリングの閾値(scoring.py、いずれもDexScreenerの直近5分集計) ---
# 出来高(USD建て)が一定以上なら加点する。
MIN_VOLUME_USD_FOR_SCORE = _env_float("MIN_VOLUME_USD_FOR_SCORE", 300.0)
# 流動性(USD建て)がこれ未満だと加点しない(極端に薄い=すぐ引き抜かれる
# 可能性が高い)。
MIN_LIQUIDITY_USD_FOR_SCORE = _env_float("MIN_LIQUIDITY_USD_FOR_SCORE", 2000.0)

# --- 通知レベルのスコア閾値 ---
# score >= HIGH_SCORE_THRESHOLD: 🚨 HIGH PRIORITY(Discord + スマホ通知推奨)
# score >= WATCH_SCORE_THRESHOLD: ⚠ WATCH(Discord通常通知)
# score >= LOW_SCORE_THRESHOLD: ログ保存のみ(Discordへは送らない)
# score < LOW_SCORE_THRESHOLD: 何もしない(デバッグログにのみ理由を残す)
HIGH_SCORE_THRESHOLD = _env_int("HIGH_SCORE_THRESHOLD", 75)
WATCH_SCORE_THRESHOLD = _env_int("WATCH_SCORE_THRESHOLD", 50)
LOW_SCORE_THRESHOLD = _env_int("LOW_SCORE_THRESHOLD", 35)

# --- 通知後の結果トラッキング(outcome_tracker.py) ---
# WATCH/HIGHとして通知したトークンは、それ以降もこの秒数リストの経過時点
# ごとにDexScreenerから時価総額を取得し、通知時点からの変化率を
# logs/outcomes.jsonlへ追記する(将来、どのスコア項目が実際に有効だったか
# 分析するため)。最後の秒数(既定24時間)を過ぎたら追跡を終了する。
OUTCOME_CHECKPOINTS_SECONDS: tuple[int, ...] = (1800, 3600, 86400)  # 30分/1時間/24時間
_outcomes_file_path_env = os.getenv("OUTCOMES_FILE_PATH")
OUTCOMES_FILE_PATH = Path(_outcomes_file_path_env) if _outcomes_file_path_env else BASE_DIR / "logs" / "outcomes.jsonl"

# --- Discord通知 ---
DISCORD_ENABLED = _env_bool("DISCORD_ENABLED", False)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# --- ログ ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "phantom_sniper.log"
