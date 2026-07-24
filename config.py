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

# --- RugCheck REST API接続(rugcheck_client.py) ---
# 無料・APIキー不要(https://api.rugcheck.xyz/swagger/index.html)。
# 未認証だと10req/minとDexScreenerよりレート制限が厳しいため、トークン
# 1件につき最初のチェックポイント(0秒)で1回だけ取得し、結果を
# TrackedToken.rugcheck_checkedへキャッシュして使い回す(main.py参照)。
RUGCHECK_API_BASE_URL = os.getenv("RUGCHECK_API_BASE_URL", "https://api.rugcheck.xyz")

# --- Solana RPC接続(solana_client.py) ---
# ユニーク買い手数(★表示)を、DexScreenerではなくSolanaブロックチェーンから
# 直接取引を読んで自前で集計するために使う(2026-07、DexScreenerの公開API
# にはそもそもこのデータが無いことが判明したため、オンチェーンで数える方式に
# 変更した)。既定は無料の公開エンドポイントだが、レート制限が厳しいため、
# 安定運用したい場合はHelius(https://www.helius.dev/、無料枠あり・
# クレジットカード不要)等でAPIキー付きのURLを取得して設定することを推奨する
# (README.mdの「Solana RPC連携」参照)。
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
# 1回のユニーク買い手数集計につき取得する取引署名の上限(RPC呼び出し回数を
# 抑えるための上限。これを超える分は集計対象外になる=取りこぼす場合がある)。
SOLANA_MAX_SIGNATURES_PER_CHECKPOINT = _env_int("SOLANA_MAX_SIGNATURES_PER_CHECKPOINT", 20)
# getTransactionの同時並列リクエスト数(初動の通知速度に影響しないよう、
# チェックポイント処理を遅らせすぎない範囲で並列化する)。
SOLANA_RPC_CONCURRENCY = _env_int("SOLANA_RPC_CONCURRENCY", 5)
# ユニーク買い手数を集計する時間幅(秒)。DexScreenerの「直近5分」指標
# (buys_m5等)と揃えるため既定300秒(5分)。
SOLANA_UNIQUE_BUYERS_WINDOW_SECONDS = _env_int("SOLANA_UNIQUE_BUYERS_WINDOW_SECONDS", 300)

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
# 1回のポーリングでチェックポイントを迎えたトークンを、同時に何件まで
# 並行処理するか(main.py:_checkpoint_loop)。1件ずつ順番に処理する設計
# だと、DexScreener/RugCheck/Solana RPC/Discord/Supabaseへの複数回の
# ネットワーク往復(数秒かかることがある)が積み重なり、卒業数が多い
# 時間帯に処理が追いつかず「何時間も前に卒業したトークンの通知が今頃
# 届く」という遅延が発生する(2026-07判明)。並行化することで、この
# 遅延を大きく減らす。上げすぎると各APIのレート制限に引っかかりやすく
# なるため注意。
CHECKPOINT_CONCURRENCY = _env_int("CHECKPOINT_CONCURRENCY", 8)

# --- スコアリングの閾値(scoring.py、いずれもDexScreenerの直近5分集計) ---
# 出来高(USD建て)が一定以上なら加点する。
MIN_VOLUME_USD_FOR_SCORE = _env_float("MIN_VOLUME_USD_FOR_SCORE", 300.0)
# 流動性(USD建て)がこれ未満だと加点しない(極端に薄い=すぐ引き抜かれる
# 可能性が高い)。
MIN_LIQUIDITY_USD_FOR_SCORE = _env_float("MIN_LIQUIDITY_USD_FOR_SCORE", 2000.0)
# RugCheckのtopHolders[]から計算した上位10保有者の合計保有率(%)がこれ
# 以上なら集中しすぎ(⚠️)とみなし減点する。
HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT = _env_float("HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT", 50.0)
# 上記の合計保有率がこれ未満なら健全に分散している(✅)とみなし加点する。
HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT = _env_float("HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT", 20.0)

# --- 通知レベルのスコア閾値 ---
# score >= HIGH_SCORE_THRESHOLD: 🚨 HIGH(Discord通知)
# score >= WATCH_SCORE_THRESHOLD: ⚠ WATCH(Discord通知)
# score >= LOW_SCORE_THRESHOLD: ログ保存のみ(Discordへは送らない)
# score < LOW_SCORE_THRESHOLD: 何もしない(デバッグログにのみ理由を残す)
HIGH_SCORE_THRESHOLD = _env_int("HIGH_SCORE_THRESHOLD", 75)
WATCH_SCORE_THRESHOLD = _env_int("WATCH_SCORE_THRESHOLD", 70)
LOW_SCORE_THRESHOLD = _env_int("LOW_SCORE_THRESHOLD", 35)

# --- 発行者ブラックリスト(creator_blocklist.py) ---
# RugCheckで危険判定が出た、または通知後に大暴落したトークンの発行者
# ウォレットアドレスを記録し、次回以降は名前を変えて再発行されても即座に
# スコアを0点にする(外部サービス不要、うち自身の観察結果のみで完結)。
_creator_blocklist_file_path_env = os.getenv("CREATOR_BLOCKLIST_FILE_PATH")
CREATOR_BLOCKLIST_FILE_PATH = (
    Path(_creator_blocklist_file_path_env)
    if _creator_blocklist_file_path_env
    else BASE_DIR / "logs" / "creator_blocklist.json"
)
# 通知後、時価総額がこの割合(マイナス値)以上下落したら「暴落(ラグ濃厚)」
# とみなし、その発行者をブロックリストへ追加する(outcome_tracker連携、
# main.py参照)。
CREATOR_BLOCKLIST_CRASH_THRESHOLD_PCT = _env_float("CREATOR_BLOCKLIST_CRASH_THRESHOLD_PCT", -90.0)

# --- 名前/ティッカー重複履歴(token_name_history.py) ---
# 既に観測した名前/ティッカーを別mintが後から名乗った場合(なりすまし
# 対策)を検出するための記録先。
_token_name_history_file_path_env = os.getenv("TOKEN_NAME_HISTORY_FILE_PATH")
TOKEN_NAME_HISTORY_FILE_PATH = (
    Path(_token_name_history_file_path_env)
    if _token_name_history_file_path_env
    else BASE_DIR / "logs" / "token_name_history.json"
)

# --- 自動売買(trade_executor.py、jupiter_client.py、wallet.py) ---
# ⚠️ 実際にウォレットの資金を使って売買する機能。既定は完全にOFF。
# 有効化するには以下を全て満たす必要がある(事故防止の二重ゲート):
#   1. AUTO_TRADE_ENABLED=true
#   2. AUTO_TRADE_CONFIRMED_RISK=true(「自動売買のリスクを理解した」の
#      明示的な確認。どちらか片方だけでは動かない)
#   3. SOLANA_WALLET_PRIVATE_KEY設定(base58形式、Phantom等の「秘密鍵を
#      エクスポート」で取得。ウォレットの全資金を動かせる値のため、少額
#      専用の別ウォレットを新規に作ることを強く推奨。既存のメインウォレット
#      の鍵は絶対に使わないこと)
# 詳細・注意事項はREADME.mdの「自動売買(実験的機能)」参照。
AUTO_TRADE_ENABLED = _env_bool("AUTO_TRADE_ENABLED", False)
AUTO_TRADE_CONFIRMED_RISK = _env_bool("AUTO_TRADE_CONFIRMED_RISK", False)
SOLANA_WALLET_PRIVATE_KEY = os.getenv("SOLANA_WALLET_PRIVATE_KEY", "")
# このスコア以上(既定100=満点、実質RugCheck危険/発行者ブラックリスト/
# なりすまし検出のいずれも無いこと)のトークンだけを自動購入の対象にする。
AUTO_TRADE_MIN_SCORE = _env_int("AUTO_TRADE_MIN_SCORE", 100)
# DEX卒業からの経過秒数がこれ以下のチェックポイントでのみ自動購入する
# (「なるべく上がっていない初期のコイン」を狙うため。既定60秒=2番目の
# チェックポイントまで。MIGRATION_CHECKPOINTS_SECONDS参照)。
AUTO_TRADE_MAX_ELAPSED_SECONDS_FOR_ENTRY = _env_int("AUTO_TRADE_MAX_ELAPSED_SECONDS_FOR_ENTRY", 60)
# 1回の購入に使うSOL量(既定0.02 SOL、少額から。価格次第だが数百〜数千円
# 程度を想定)。
AUTO_TRADE_BUY_AMOUNT_SOL = _env_float("AUTO_TRADE_BUY_AMOUNT_SOL", 0.02)
# 同時に保有できる建玉数の上限(資金を1つのコインに集中させないため)。
AUTO_TRADE_MAX_OPEN_POSITIONS = _env_int("AUTO_TRADE_MAX_OPEN_POSITIONS", 3)
# Jupiterスワップのスリッページ許容(ベーシスポイント、100=1%)。卒業直後は
# 値動きが激しいためやや広め。
AUTO_TRADE_SLIPPAGE_BPS = _env_int("AUTO_TRADE_SLIPPAGE_BPS", 500)
# 含み益がこの%以上になったら自動的に利確売りする。
AUTO_TRADE_TAKE_PROFIT_PCT = _env_float("AUTO_TRADE_TAKE_PROFIT_PCT", 50.0)
# 含み損がこの%(マイナス値)以下になったら自動的に損切り売りする。
AUTO_TRADE_STOP_LOSS_PCT = _env_float("AUTO_TRADE_STOP_LOSS_PCT", -30.0)
# 利確・損切りのどちらにも達しないまま、これ以上の秒数保有し続けたら
# 強制的に手仕舞いする(塩漬け防止、既定1時間)。
AUTO_TRADE_MAX_HOLD_SECONDS = _env_int("AUTO_TRADE_MAX_HOLD_SECONDS", 3600)
# 建玉の状態を監視する間隔(秒)。
AUTO_TRADE_POSITION_POLL_SECONDS = _env_int("AUTO_TRADE_POSITION_POLL_SECONDS", 15)
_positions_file_path_env = os.getenv("POSITIONS_FILE_PATH")
POSITIONS_FILE_PATH = (
    Path(_positions_file_path_env) if _positions_file_path_env else BASE_DIR / "logs" / "positions.json"
)
_trades_file_path_env = os.getenv("TRADES_FILE_PATH")
TRADES_FILE_PATH = Path(_trades_file_path_env) if _trades_file_path_env else BASE_DIR / "logs" / "trades.jsonl"
# 自動売買の実行結果(買い/売り)を通知する専用のWebhook URL(任意、通常の
# DISCORD_WEBHOOK_URLとは別チャンネル推奨)。
DISCORD_TRADE_WEBHOOK_URL = os.getenv("DISCORD_TRADE_WEBHOOK_URL", "")

# --- パーペチュアル(perp_sniper.py、実験的機能。ミームコインのスキャナー
# 本体[main.py]とは完全に独立した別プロセス。動いていなくても本体には
# 一切影響しない) ---
# ロング/ショートシグナルの計算・通知を行うかどうか。
PERP_ENABLED = _env_bool("PERP_ENABLED", False)
# 監視する銘柄(カンマ区切り、Binance Futuresのシンボル表記)。
PERP_SYMBOLS: list[str] = [s.strip() for s in os.getenv("PERP_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()]
# 市場データ取得元(既定Binance Futures公開API、無料・APIキー不要)。
PERP_API_BASE_URL = os.getenv("PERP_API_BASE_URL", "https://fapi.binance.com")
# シグナルを再計算する間隔(秒、既定15分)。
PERP_POLL_INTERVAL_SECONDS = _env_int("PERP_POLL_INTERVAL_SECONDS", 900)
# ローソク足の時間足・本数(EMA/RSI計算に使う)。
PERP_KLINE_INTERVAL = os.getenv("PERP_KLINE_INTERVAL", "1h")
PERP_KLINE_LIMIT = _env_int("PERP_KLINE_LIMIT", 100)
# シグナル強度(-100〜100)がこの絶対値以上でLONG/SHORTと判定する
# (未満はNEUTRALとして通知しない)。
PERP_SIGNAL_THRESHOLD = _env_int("PERP_SIGNAL_THRESHOLD", 40)
# シグナル通知専用のWebhook URL(任意、未設定なら送らない)。
DISCORD_PERP_WEBHOOK_URL = os.getenv("DISCORD_PERP_WEBHOOK_URL", "")
# ペーパートレード(モック、実資金は一切動かさない)を行うかどうか。
# レバレッジをかけた場合の損益シミュレーションをDiscordへ通知する。
# 実資金を動かさないため既定ON(PERP_ENABLED=trueが前提)。
PERP_PAPER_TRADING_ENABLED = _env_bool("PERP_PAPER_TRADING_ENABLED", True)
PERP_PAPER_LEVERAGE = _env_float("PERP_PAPER_LEVERAGE", 3.0)
PERP_PAPER_TAKE_PROFIT_PCT = _env_float("PERP_PAPER_TAKE_PROFIT_PCT", 10.0)
PERP_PAPER_STOP_LOSS_PCT = _env_float("PERP_PAPER_STOP_LOSS_PCT", -10.0)
PERP_PAPER_MAX_HOLD_SECONDS = _env_int("PERP_PAPER_MAX_HOLD_SECONDS", 86400)
_perp_positions_file_path_env = os.getenv("PERP_POSITIONS_FILE_PATH")
PERP_POSITIONS_FILE_PATH = (
    Path(_perp_positions_file_path_env) if _perp_positions_file_path_env else BASE_DIR / "logs" / "perp_positions.json"
)

# --- グリッドトレード(ライブ・ペーパートレード、grid_paper_trader.py。
# perp_backtest.py[トレンド追従]のPERP_PAPER_*とは別の戦略・別の建玉管理。
# 実資金は動かさない。既定はperp_grid_backtest.pyでの検証結果(2026-07、
# Hyperliquid実際のMaker手数料0.015%込みでBTC/ETH/SOL全てBuy & Hold超え)
# を踏まえた値) ---
PERP_GRID_ENABLED = _env_bool("PERP_GRID_ENABLED", False)
PERP_GRID_RANGE_PCT = _env_float("PERP_GRID_RANGE_PCT", 10.0)
PERP_GRID_COUNT = _env_int("PERP_GRID_COUNT", 100)
PERP_GRID_TAKE_PROFIT_PCT = _env_float("PERP_GRID_TAKE_PROFIT_PCT", 0.2)
PERP_GRID_STOP_LOSS_PCT = _env_float("PERP_GRID_STOP_LOSS_PCT", -0.1)

# 銘柄ごとのTP/SL/グリッド分割数の上書き(2026-07、perp_grid_backtest_sweep.py
# での検証結果を踏まえて追加)。上記のPERP_GRID_*は全銘柄共通の1セットだが、
# 実際にBTCUSDT/ETHUSDT/SOLUSDTでスイープした結果、銘柄によって最適な
# TP/SL幅がまったく違うことがわかった(BTCはSL-0.1%だとノイズで刈られやすく
# SL-0.5%まで広げた方が良かった一方、ETH/SOLは逆にSL-0.1%のままの方が
# 良く、グリッド分割数を300→50に減らした方がマージンも最大ドローダウンも
# 改善した)。単純に一番合計損益が良かった組み合わせではなく、最大
# ドローダウンが極端に大きい組み合わせ(BTCでSL-0.5%かつgrid=300だと
# 最大DD-92%相当)は避け、値動きの荒さに対してリスクが大きすぎない
# 組み合わせを選んでいる。
#
# 個別の環境変数(PERP_GRID_COUNT_BTCUSDT等)で上書きした場合はそちらが
# 最優先。次にこの一覧(実測に基づく既定)、最後にグローバルなPERP_GRID_*
# (上記)の順で使われる(grid_count_for_symbol等参照)。この一覧に無い
# 銘柄は最初からグローバル既定を使う。
PERP_GRID_SYMBOL_DEFAULTS: dict[str, dict[str, float | int]] = {
    "BTCUSDT": {"count": 100, "take_profit_pct": 0.40, "stop_loss_pct": -0.50},
    "ETHUSDT": {"count": 50, "take_profit_pct": 0.20, "stop_loss_pct": -0.10},
    "SOLUSDT": {"count": 50, "take_profit_pct": 0.20, "stop_loss_pct": -0.10},
}


def _grid_float_for_symbol(env_base: str, symbol: str, key: str, fallback: float) -> float:
    raw = os.getenv(f"{env_base}_{symbol}")
    if raw is not None and raw.strip() != "":
        try:
            return float(raw)
        except ValueError:
            pass
    return float(PERP_GRID_SYMBOL_DEFAULTS.get(symbol, {}).get(key, fallback))


def _grid_int_for_symbol(env_base: str, symbol: str, key: str, fallback: int) -> int:
    raw = os.getenv(f"{env_base}_{symbol}")
    if raw is not None and raw.strip() != "":
        try:
            return int(raw)
        except ValueError:
            pass
    return int(PERP_GRID_SYMBOL_DEFAULTS.get(symbol, {}).get(key, fallback))


def grid_range_pct_for_symbol(symbol: str) -> float:
    return _grid_float_for_symbol("PERP_GRID_RANGE_PCT", symbol, "range_pct", PERP_GRID_RANGE_PCT)


def grid_count_for_symbol(symbol: str) -> int:
    return _grid_int_for_symbol("PERP_GRID_COUNT", symbol, "count", PERP_GRID_COUNT)


def grid_take_profit_pct_for_symbol(symbol: str) -> float:
    return _grid_float_for_symbol("PERP_GRID_TAKE_PROFIT_PCT", symbol, "take_profit_pct", PERP_GRID_TAKE_PROFIT_PCT)


def grid_stop_loss_pct_for_symbol(symbol: str) -> float:
    return _grid_float_for_symbol("PERP_GRID_STOP_LOSS_PCT", symbol, "stop_loss_pct", PERP_GRID_STOP_LOSS_PCT)


PERP_GRID_LEVERAGE = _env_float("PERP_GRID_LEVERAGE", 3.0)
PERP_GRID_FEE_PCT_PER_SIDE = _env_float("PERP_GRID_FEE_PCT_PER_SIDE", 0.015)
# trueにすると、買いグリッド(下がったら買い、戻ったら利確)に加えて
# 売りグリッド(上がったら売り、戻ったら利確)も同時にペーパートレードする
# (「両建て」、既定false=買いのみ)。上下どちらの波でも利確を狙える一方、
# 必要証拠金・同時保有数は実質倍になり、含み損が積み上がるリスクも
# 買い・売り両方向に広がる点に注意(grid_trading.level_touched_on_rise
# 参照)。実発注(grid_live_trader.py)は未対応、ペーパートレードのみ。
PERP_GRID_SHORT_ENABLED = _env_bool("PERP_GRID_SHORT_ENABLED", False)
# 各銘柄のグリッド状態を確認する間隔(秒)。トレンド戦略より頻繁に
# ポーリングする(グリッドは細かい値動きを捉える戦略のため)。
PERP_GRID_POLL_INTERVAL_SECONDS = _env_int("PERP_GRID_POLL_INTERVAL_SECONDS", 30)
# 取引のたびにDiscord通知すると件数が多すぎてスパムになるため、この間隔
# (秒、既定24時間)ごとに集計だけ通知する(perp_notifier.notify_grid_summary)。
PERP_GRID_SUMMARY_INTERVAL_SECONDS = _env_int("PERP_GRID_SUMMARY_INTERVAL_SECONDS", 86400)
_perp_grid_positions_file_path_env = os.getenv("PERP_GRID_POSITIONS_FILE_PATH")
PERP_GRID_POSITIONS_FILE_PATH = (
    Path(_perp_grid_positions_file_path_env)
    if _perp_grid_positions_file_path_env
    else BASE_DIR / "logs" / "grid_positions.json"
)

# --- グリッドトレードの実発注(Hyperliquid、grid_live_trader.py。
# ⚠️⚠️⚠️既定OFF。実際にウォレットの資金を使って売買する機能。有効化する
# には以下を全て満たす必要がある(trade_executor.pyと同じ二重ゲート):
#   1. PERP_GRID_LIVE_ENABLED=true
#   2. PERP_GRID_LIVE_CONFIRMED_RISK=true(「リスクを理解した」の明示的な確認)
#   3. HYPERLIQUID_PRIVATE_KEY設定(Ethereum形式の秘密鍵。少額専用の別
#      ウォレットを新規に作ることを強く推奨)
# 詳細・注意事項はREADME.mdの「パーペチュアル実発注(Hyperliquid、
# 実験的機能)」参照。
PERP_GRID_LIVE_ENABLED = _env_bool("PERP_GRID_LIVE_ENABLED", False)
PERP_GRID_LIVE_CONFIRMED_RISK = _env_bool("PERP_GRID_LIVE_CONFIRMED_RISK", False)
HYPERLIQUID_PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
# true推奨(まずテストネットで動作確認してから本番=falseへ切り替えること)。
HYPERLIQUID_USE_TESTNET = _env_bool("HYPERLIQUID_USE_TESTNET", True)
# 1グリッドあたりの発注額(USD建て。銘柄ごとの単価が違うため、コイン数量
# ではなくUSD額で指定し、発注時に現在価格で換算する)。
PERP_GRID_LIVE_ORDER_USD = _env_float("PERP_GRID_LIVE_ORDER_USD", 10.0)
# 同時に保有できるグリッド建玉数の上限(全資金を一度に晒さないため)。
PERP_GRID_LIVE_MAX_OPEN_POSITIONS = _env_int("PERP_GRID_LIVE_MAX_OPEN_POSITIONS", 5)
# 損切り(成行、close_long)のスリッページ許容(0.01=1%)。新規建玉・利確は
# 指値(Alo)なのでスリッページの概念自体が無い。
PERP_GRID_LIVE_SLIPPAGE = _env_float("PERP_GRID_LIVE_SLIPPAGE", 0.01)
# 新規建玉・利確決済は指値(Alo、Maker確定)なので、perp_grid_backtest.pyで
# 検証したMaker手数料(0.015%、2026-07時点)がそのまま既定値。ただし
# 損切り決済だけは緊急性のため成行(Taker、0.045%)を使っているため、
# 損切りで終わった建玉についてはこの値よりわずかに実際の手数料負けが
# 大きくなる(grid_trading.compute_grid_pnl_pctは往復で同じ料率を
# 使う単純化をしているため、正確な内訳ではなく目安の数字)。
PERP_GRID_LIVE_FEE_PCT_PER_SIDE = _env_float("PERP_GRID_LIVE_FEE_PCT_PER_SIDE", 0.015)
_perp_grid_live_positions_file_path_env = os.getenv("PERP_GRID_LIVE_POSITIONS_FILE_PATH")
PERP_GRID_LIVE_POSITIONS_FILE_PATH = (
    Path(_perp_grid_live_positions_file_path_env)
    if _perp_grid_live_positions_file_path_env
    else BASE_DIR / "logs" / "grid_live_positions.json"
)

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
# スコアが100点満点のトークンだけを追加で通知する専用チャンネルのWebhook
# URL(通常のDISCORD_WEBHOOK_URLとは別)。未設定なら送らない。
DISCORD_PERFECT_SCORE_WEBHOOK_URL = os.getenv("DISCORD_PERFECT_SCORE_WEBHOOK_URL", "")
# 既に通知済みのトークンが、後のチェックポイントでユニーク買い手★3つに
# 到達した瞬間に送る「追い通知」専用のWebhook URL(通常のDISCORD_WEBHOOK_URL
# とは別)。1トークンにつき最大1回だけ送る(discord_notifier.notify_star_
# upgrade/main.py参照)。未設定なら送らない。
DISCORD_FOLLOWUP_WEBHOOK_URL = os.getenv("DISCORD_FOLLOWUP_WEBHOOK_URL", "")
# 通知に含めるPhantomアプリの起動リンク(https://phantom.com/tokens/solana/
# {mint})に付ける紹介コード(個人に紐づく値のため、コードに直書きせず
# .envで設定する。未設定でもリンク自体は生成される)。
PHANTOM_REFERRAL_ID = os.getenv("PHANTOM_REFERRAL_ID", "")

# 通知本文の絵文字。Discordサーバーで作ったカスタム絵文字を使いたい場合、
# `<:名前:ID>`の形式(Discordのメッセージ入力欄で`\:名前:`と打つと出てくる)
# をそのまま値に設定すればよい(既定は標準の絵文字)。
DISCORD_HOLDER_CONCENTRATION_WARN_EMOJI = os.getenv("DISCORD_HOLDER_CONCENTRATION_WARN_EMOJI", "⚠️")
DISCORD_HOLDER_CONCENTRATION_HEALTHY_EMOJI = os.getenv("DISCORD_HOLDER_CONCENTRATION_HEALTHY_EMOJI", "✅")
# 名前/ティッカー重複(なりすまし疑い、token_name_history.py参照)を検出
# した通知に付ける警告絵文字。
DISCORD_DUPLICATE_NAME_EMOJI = os.getenv("DISCORD_DUPLICATE_NAME_EMOJI", "🚨")
DISCORD_TWITTER_EMOJI = os.getenv("DISCORD_TWITTER_EMOJI", "🐦")
DISCORD_TELEGRAM_EMOJI = os.getenv("DISCORD_TELEGRAM_EMOJI", "✈️")

# ダッシュボードの公開URL(例: http://76.13.180.239:8790)。設定すると、
# 通知の「詳細」ボタンがこのURLの/token/{mint}へのリンクになる
# (dashboard_server.py参照)。未設定なら「詳細」ボタンは付けない。
DASHBOARD_PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "")

# --- Supabase(通知履歴・結果トラッキング・発行者ブラックリストの永続化、
# ダッシュボードの分析用データソース。supabase_client.py参照) ---
# プロジェクトのURL(例: https://xxxxxxxx.supabase.co)。SupabaseダッシュボードのSettings
# → API → Project URLで確認できる。未設定ならSupabaseへの書き込み・
# 読み取りは一切行わない(既存のローカルJSON/JSONLだけで動き続ける)。
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
# service_role キー(秘密鍵、Settings → API → service_role secretで確認)。
# RLSを無視して読み書きできる強い権限のため、.envにのみ保存しコードや
# ブラウザに一切埋め込まないこと。anonキーではなくservice_roleを使う
# 理由は、書き込み専用のバックエンド(このボット自体)からのみ使う想定
# のため。
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# --- ダッシュボード(dashboard_server.py、Supabaseのデータを可視化するだけの
# 読み取り専用サーバー。任意、動いていなくても本体のボット機能には影響しない) ---
DASHBOARD_SERVER_HOST = os.getenv("DASHBOARD_SERVER_HOST", "0.0.0.0")
DASHBOARD_SERVER_PORT = _env_int("DASHBOARD_SERVER_PORT", 8790)
# 簡易保護用のトークン(任意)。設定すると、ブラウザ側で入力しない限り
# /api/*が401を返す(settings_server.pyのSETTINGS_API_TOKENと同じ方式)。
# 読み取り専用エンドポイントのみのため未設定でも致命的ではないが、
# ウォレット関連の情報を含むため信頼できるネットワーク以外には公開しない。
DASHBOARD_API_TOKEN = os.getenv("DASHBOARD_API_TOKEN", "")
# ダッシュボードの「直近の通知」一覧に表示する最大件数。
DASHBOARD_RECENT_NOTIFICATIONS_LIMIT = _env_int("DASHBOARD_RECENT_NOTIFICATIONS_LIMIT", 50)

# --- チャット返信Bot(chat_reply_bot.py、任意。本体のスキャナー機能とは
# 完全に独立した別プロセス・別Discordアプリで、無くても本体には一切影響
# しない)。特定の1人が特定の言葉を発言したら固定の返信を送るだけの、
# ちょっとした遊び機能。Webhook(送信専用)では他人の発言を検知できない
# ため、メッセージ内容を読み取れる権限を持つ普通のDiscord Botが必要
# (Discord Developer Portalでアプリ作成→Bot追加→トークン発行→サーバーに
# 招待し、"MESSAGE CONTENT INTENT"を有効化する。README.md参照)。 ---
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CHAT_REPLY_ENABLED = _env_bool("CHAT_REPLY_ENABLED", False)
# 反応する相手のDiscordユーザーID(数値。ユーザー本人の設定で開発者モードを
# 有効にし、自分のアイコンを長押し/右クリック→「IDをコピー」で確認できる)。
CHAT_REPLY_TARGET_USER_ID = _env_int("CHAT_REPLY_TARGET_USER_ID", 0)
# 「この言葉が発言に含まれていたらこう返す」のペアを複数登録できる
# (CHAT_REPLY_TRIGGER_WORD_1/CHAT_REPLY_MESSAGE_1、_2、_3...と番号を振る。
# 最大10個まで、番号は飛んでいても良い。1件も設定しなければ何もしない)。
# 発言に複数の言葉が含まれる場合、番号の小さいペアが優先される。
_CHAT_REPLY_MAX_PAIRS = 10
CHAT_REPLY_PAIRS: list[tuple[str, str]] = [
    (trigger, os.getenv(f"CHAT_REPLY_MESSAGE_{i}", ""))
    for i in range(1, _CHAT_REPLY_MAX_PAIRS + 1)
    if (trigger := os.getenv(f"CHAT_REPLY_TRIGGER_WORD_{i}"))
]

# --- ログ ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "phantom_sniper.log"
