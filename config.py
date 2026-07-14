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

# --- 初動観察・フィルター条件(token_watcher.py) ---
# 新規トークン作成を検知してから、この秒数だけ売買を観察してから判定する
# (即時通知ではなく、多少の初動データを見てから絞り込む)。
OBSERVATION_WINDOW_SECONDS = _env_int("OBSERVATION_WINDOW_SECONDS", 45)
# 観察期間中の買い(buy)件数がこれ未満なら通知しない。
MIN_BUY_COUNT = _env_int("MIN_BUY_COUNT", 5)
# 観察期間中のユニークな買い手(traderPublicKeyの重複除外)がこれ未満なら
# 通知しない(同一アドレスの自作自演連続買いだけで件数を稼ぐケースを除外)。
MIN_UNIQUE_BUYERS = _env_int("MIN_UNIQUE_BUYERS", 3)
# 観察期間中の売り件数が「買い件数×この倍率」を超えたら通知しない
# (作成直後から売り優勢=初動で投げ売りされている=避けたい状態)。
MAX_SELL_TO_BUY_RATIO = _env_float("MAX_SELL_TO_BUY_RATIO", 1.0)
# 観察終了時点の時価総額(SOL建て)がこれ未満なら通知しない。0以下で無効。
MIN_MARKET_CAP_SOL = _env_float("MIN_MARKET_CAP_SOL", 0.0)
# 同時に観察できるトークン数の上限(メモリ・購読数の暴走防止)。
# これを超えた場合、最も古い(作成が古い)ものから観察を打ち切る。
MAX_TRACKED_TOKENS = _env_int("MAX_TRACKED_TOKENS", 500)

# --- Discord通知 ---
DISCORD_ENABLED = _env_bool("DISCORD_ENABLED", False)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# --- ログ ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "phantom_sniper.log"
