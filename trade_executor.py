"""自動売買の実行オーケストレーション(買い判断→購入→建玉監視→利確/損切り売り)。

⚠️⚠️⚠️ 実際にウォレットの資金を使ってオンチェーンで売買するモジュール。
config.AUTO_TRADE_ENABLED=true かつ config.AUTO_TRADE_CONFIRMED_RISK=true の
両方が揃っていない限り、should_auto_buy()が常にFalseを返すため何も実行
されない(main.py側もこの関数の結果だけを見て呼び出すかどうかを決める)。
デフォルトは完全にOFF。有効化する前に必ずREADME.mdの「自動売買
(実験的機能)」を読み、少額のテスト専用ウォレットで試すこと。

このモジュール自身が持つ安全策:
- should_auto_buy(): RugCheck危険・発行者ブラックリスト・なりすまし検出の
  いずれかがあれば即座に拒否する(スコアが閾値を満たしていても)。
- 同時保有数の上限(AUTO_TRADE_MAX_OPEN_POSITIONS)。
- 売却時は自前の記録(買った量)を信用せず、実際のオンチェーン残高を
  再取得してから全量売却する(jupiter_client.get_token_balance_raw)。
- 買い注文・売り注文それぞれの失敗はDiscordへ通知し、例外は握りつぶさず
  ログに残す(ただし監視ループ自体は落とさない。main.py参照)。

それでも、実際のオンチェーン送金を伴うコードであるため、ネットワーク環境
(このリポジトリのCI/開発環境)では実際のSolanaメインネットに対する
エンドツーエンドの検証ができていない(残高もウォレットも無いため)。
少額から試すこと。
"""
from __future__ import annotations

import json
import logging

import config
import dexscreener_client
import discord_notifier
import jupiter_client
import wallet
from position_tracker import Position, PositionTracker, decide_exit_reason
from token_watcher import TrackedToken

logger = logging.getLogger("phantom_sniper")


def should_auto_buy(
    token: TrackedToken, elapsed_seconds: int, score_total: int, open_position_count: int
) -> tuple[bool, str]:
    """このトークンを今すぐ自動購入すべきかどうかを判定する(純粋関数)。

    ネットワーク・時刻取得に一切依存しないため単体テストしやすい
    (tests/test_trade_executor.py参照)。戻り値は(購入すべきか, 理由コード)。
    理由コードはログ・デバッグ用(discord通知には出さない)。
    """
    if not config.AUTO_TRADE_ENABLED or not config.AUTO_TRADE_CONFIRMED_RISK:
        return False, "auto_trade_disabled"
    if token.rugcheck_danger:
        return False, "rugcheck_danger"
    if token.blocked_creator_reason:
        return False, "creator_blocklisted"
    if token.duplicate_name_reason:
        return False, "duplicate_name"
    if not token.has_pair_data or token.liquidity_usd <= 0 or token.price_usd <= 0:
        return False, "no_pair_data"
    if score_total < config.AUTO_TRADE_MIN_SCORE:
        return False, "score_too_low"
    if elapsed_seconds > config.AUTO_TRADE_MAX_ELAPSED_SECONDS_FOR_ENTRY:
        return False, "too_late"
    if open_position_count >= config.AUTO_TRADE_MAX_OPEN_POSITIONS:
        return False, "max_positions_open"
    return True, "ok"


def _log_trade(record: dict) -> None:
    """logs/trades.jsonlへ1行追記する(監査用、失敗しても処理は止めない)。"""
    try:
        config.TRADES_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with config.TRADES_FILE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("trade_executor: 取引ログの書き込みに失敗しました: %s", exc)


def execute_buy(token: TrackedToken, positions: PositionTracker, now: float) -> Position | None:
    """token.mintを自動購入する(成功時はPositionを返す、失敗時はNone)。

    呼び出し前にshould_auto_buy()がTrueであることを確認しておくこと
    (このここ自体は再チェックしない、二重チェックはmain.py側の責務)。
    """
    keypair = wallet.get_keypair()
    if keypair is None:
        logger.error("trade_executor: ウォレット秘密鍵が未設定/不正なため購入を中止しました mint=%s", token.mint)
        return None

    amount_lamports = jupiter_client.sol_to_lamports(config.AUTO_TRADE_BUY_AMOUNT_SOL)
    result = jupiter_client.execute_swap(
        jupiter_client.SOL_MINT, token.mint, amount_lamports, keypair, config.AUTO_TRADE_SLIPPAGE_BPS
    )

    _log_trade(
        {
            "action": "buy",
            "mint": token.mint,
            "name": token.name,
            "symbol": token.symbol,
            "amount_sol": config.AUTO_TRADE_BUY_AMOUNT_SOL,
            "price_usd": token.price_usd,
            "success": result.success,
            "tx_signature": result.tx_signature,
            "error": result.error,
            "at": now,
        }
    )

    if not result.success:
        logger.error("trade_executor: 購入に失敗しました mint=%s error=%s", token.mint, result.error)
        discord_notifier.notify_trade_failure(token, "買い", result.error)
        return None

    position = positions.open_position(
        mint=token.mint,
        name=token.name,
        symbol=token.symbol,
        entry_price_usd=token.price_usd,
        entry_amount_sol=config.AUTO_TRADE_BUY_AMOUNT_SOL,
        token_amount_raw=result.out_amount_raw,
        open_tx_signature=result.tx_signature,
        now=now,
    )
    logger.info(
        "trade_executor: 購入しました mint=%s symbol=%s amount_sol=%s tx=%s",
        token.mint,
        token.symbol,
        config.AUTO_TRADE_BUY_AMOUNT_SOL,
        result.tx_signature,
    )
    discord_notifier.notify_trade_opened(position)
    return position


def _current_price_usd(mint: str) -> float | None:
    pair = dexscreener_client.fetch_best_pair(mint)
    if pair is None:
        return None
    price = pair.get("priceUsd")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def execute_sell(position: Position, reason: str, positions: PositionTracker, now: float) -> bool:
    """建玉を全量売却する(成功時True)。

    自前の記録(token_amount_raw)は信用せず、実際のオンチェーン残高を
    再取得してから、その全量を売る(スリッページ・部分約定等のズレを
    吸収するため)。
    """
    keypair = wallet.get_keypair()
    if keypair is None:
        logger.error("trade_executor: ウォレット秘密鍵が未設定/不正なため売却を中止しました mint=%s", position.mint)
        return False

    balance_raw = jupiter_client.get_token_balance_raw(str(keypair.pubkey()), position.mint)
    if not balance_raw:
        logger.warning(
            "trade_executor: 売却対象の残高を取得できませんでした(既に売却済み、または残高0の可能性) mint=%s",
            position.mint,
        )
        return False

    result = jupiter_client.execute_swap(
        position.mint, jupiter_client.SOL_MINT, balance_raw, keypair, config.AUTO_TRADE_SLIPPAGE_BPS
    )

    exit_price_usd = _current_price_usd(position.mint) or position.entry_price_usd

    _log_trade(
        {
            "action": "sell",
            "mint": position.mint,
            "name": position.name,
            "symbol": position.symbol,
            "reason": reason,
            "success": result.success,
            "tx_signature": result.tx_signature,
            "error": result.error,
            "exit_price_usd": exit_price_usd,
            "at": now,
        }
    )

    if not result.success:
        logger.error("trade_executor: 売却に失敗しました mint=%s error=%s", position.mint, result.error)
        discord_notifier.notify_trade_failure_by_mint(position.mint, position.name, position.symbol, "売り", result.error)
        return False

    positions.close_position(position, exit_price_usd, result.tx_signature, reason, now)
    logger.info(
        "trade_executor: 売却しました mint=%s symbol=%s reason=%s pnl_pct=%s tx=%s",
        position.mint,
        position.symbol,
        reason,
        position.pnl_pct,
        result.tx_signature,
    )
    discord_notifier.notify_trade_closed(position)
    return True


def check_and_close_positions(positions: PositionTracker, now: float) -> None:
    """保有中の全建玉について、利確/損切り/最大保有時間超過を判定し、
    該当すれば売却する(main.pyの定期ループから呼ばれる想定)。
    """
    for position in positions.open_positions():
        current_price = _current_price_usd(position.mint)
        if current_price is None:
            continue
        reason = decide_exit_reason(
            entry_price_usd=position.entry_price_usd,
            current_price_usd=current_price,
            opened_at=position.opened_at,
            now=now,
            take_profit_pct=config.AUTO_TRADE_TAKE_PROFIT_PCT,
            stop_loss_pct=config.AUTO_TRADE_STOP_LOSS_PCT,
            max_hold_seconds=config.AUTO_TRADE_MAX_HOLD_SECONDS,
        )
        if reason is not None:
            execute_sell(position, reason, positions, now)


def is_ready() -> tuple[bool, str]:
    """自動売買が実際に動く状態にあるかどうかを返す(main.py起動時のログ用)。"""
    if not config.AUTO_TRADE_ENABLED:
        return False, "AUTO_TRADE_ENABLED=false"
    if not config.AUTO_TRADE_CONFIRMED_RISK:
        return False, "AUTO_TRADE_CONFIRMED_RISK=false"
    if wallet.get_keypair() is None:
        return False, "SOLANA_WALLET_PRIVATE_KEY未設定または不正"
    return True, "ready"
