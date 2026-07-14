"""アプリケーション全体の設定値。

pump.fun(Solana上のミームコイン発射台)の新規トークン作成をPumpPortalの
無料WebSocket API経由でリアルタイム監視し、一定時間の初動を見て条件を
満たしたものだけDiscordへ通知する「フィルター付きスナイパー通知bot」。

自動売買・ウォレット操作は一切行わない(あくまで通知のみ)。実際に買うか
どうかはDiscord通知を見た人間が判断し、Phantom等のウォレットで手動操作する
想定。
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
# subscribeNewTokenで新規トークン作成イベントを、subscribeTokenTradeで
# 個別トークンの売買イベントをリアルタイムに受信する。
PUMPPORTAL_WS_URL = os.getenv("PUMPPORTAL_WS_URL", "wss://pumpportal.fun/api/data")
# WebSocketが切断された場合に再接続を試みるまでの待機秒数。
RECONNECT_DELAY_SECONDS = _env_int("RECONNECT_DELAY_SECONDS", 5)

# --- 初動観察・スコアリング(token_watcher.py / scoring.py) ---
# 「条件を全部満たさなければ即破棄」ではなく、100点満点のスコア方式で
# 評価する。新規トークン作成を検知してから、以下の秒数(created_atからの
# 経過秒)ごとに繰り返しスコアを再計算し、その時点でスコアが通知ライン
# (WATCH_SCORE_THRESHOLD以上)を超えた瞬間に通知する。最後の秒数
# (リストの最大値)で観察を打ち切る。
EVALUATION_CHECKPOINTS_SECONDS: tuple[int, ...] = (20, 40, 60, 90, 120)
# 同時に観察できるトークン数の上限(メモリ・購読数の暴走防止)。
# これを超えた場合、最も古い(作成が古い)ものから観察を打ち切る。
MAX_TRACKED_TOKENS = _env_int("MAX_TRACKED_TOKENS", 500)

# --- スコアリングの閾値(scoring.py) ---
# Volumeが一定以上(SOL建て、買い+売りの合計)なら加点する。
MIN_VOLUME_SOL_FOR_SCORE = _env_float("MIN_VOLUME_SOL_FOR_SCORE", 5.0)
# 時価総額(SOL建て)がこれ未満だと加点しない(極端に低い=誰も相手にして
# いない可能性が高い)。
MIN_MARKET_CAP_SOL_FOR_SCORE = _env_float("MIN_MARKET_CAP_SOL_FOR_SCORE", 15.0)

# --- 通知レベルのスコア閾値 ---
# score >= HIGH_SCORE_THRESHOLD: 🚨 HIGH PRIORITY(Discord + スマホ通知推奨)
# score >= WATCH_SCORE_THRESHOLD: ⚠ WATCH(Discord通常通知)
# score >= LOW_SCORE_THRESHOLD: ログ保存のみ(Discordへは送らない)
# score < LOW_SCORE_THRESHOLD: 何もしない(デバッグログにのみ理由を残す)
HIGH_SCORE_THRESHOLD = _env_int("HIGH_SCORE_THRESHOLD", 90)
WATCH_SCORE_THRESHOLD = _env_int("WATCH_SCORE_THRESHOLD", 80)
LOW_SCORE_THRESHOLD = _env_int("LOW_SCORE_THRESHOLD", 70)

# --- 通知後の結果トラッキング(outcome_tracker.py) ---
# WATCH/HIGHとして通知したトークンは、それ以降もこの秒数リストの経過時点
# ごとに時価総額を記録し、通知時点からの変化率をlogs/outcomes.jsonlへ
# 追記する(将来、どのスコア項目が実際に有効だったか分析するため)。
# 最後の秒数(既定24時間)を過ぎたら追跡を終了する。
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
