"""ロギング設定。

コンソールと logs/phantom_sniper.log の両方に出力するロガーを提供する
(mt5-ai-traderのlogger.pyと同じ方式)。ログファイルは一定サイズで
ローテーションし、肥大化を防ぐ。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import config

_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_BACKUP_COUNT = 5


def setup_logger(name: str = "phantom_sniper", debug: bool = False) -> logging.Logger:
    """コンソール+ファイル出力を設定したロガーを返す(ハンドラ登録は冪等)。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if debug else config.LOG_LEVEL)

    if logger.handlers:
        return logger  # ハンドラの二重登録を防ぐ(レベルは上で更新済み)

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger
