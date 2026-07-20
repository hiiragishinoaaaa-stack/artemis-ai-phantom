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

# --- ログ ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "phantom_sniper.log"
