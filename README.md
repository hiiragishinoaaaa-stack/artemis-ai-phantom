# ARTEMIS Phantom Sniper

Solana上のミームコイン発射台「[pump.fun](https://pump.fun)」のトークンが、
ボンディングカーブを卒業して実際のDEX(Raydium等)へ移行した瞬間を
[PumpPortal](https://pumpportal.fun/)の無料WebSocket API経由でリアルタイム
検知し、卒業後の実際のDEX取引状況([DexScreener](https://dexscreener.com/)
の無料公開APIから取得)を0/60/300/900秒の各時点で100点満点でスコアリングし、
スコアが通知ラインを超えたものだけDiscordへ通知するbot。

## できること・できないこと

- ✅ pump.fun上のトークンが実際のDEXへ卒業(migration)した瞬間をほぼ
  リアルタイムで検知
- ✅ 卒業後0/60/300/900秒の4チェックポイントで、DexScreenerから取得した
  実際の売買件数・出来高・価格変動・流動性をもとに繰り返しスコアを
  再計算し、通知ライン(WATCH以上)を初めて超えた瞬間、またはより高い
  ティア(LOW→WATCH→HIGH)へ上昇した瞬間だけDiscordへ通知
- ✅ 通知にはpump.fun/DexScreenerへのリンク付き
- ✅ 通知したトークンは30分/1時間/24時間後の時価総額変化を`logs/outcomes.jsonl`
  へ記録(将来、どのスコア項目が実際に有効だったか分析するため)
- ✅ **完全無料で動く。** PumpPortal・DexScreenerともにAPIキー不要の
  公開エンドポイントのみを使用(詳細は下記「なぜmigration+DexScreenerなのか」
  参照)
- ❌ **自動売買・ウォレット操作は一切行わない。** あくまで人間が判断する
  ための情報提供ツール。実際に買うかどうかはDiscord通知を見た人が
  Phantom等のウォレットで手動判断・手動操作する。
- ❌ pump.fun上でまだボンディングカーブ段階(卒業前)のトークンの初動
  (作成直後数十秒の値動き)は捕捉しない(下記トレードオフ参照)。
- ❌ 詐欺・ラグプル(即座に流動性を抜かれる)の検出は保証しない。フィルター
  は明らかに無価値なものを減らすだけで、巧妙な詐欺は通過し得る。**通知が
  来ても必ず自分で内容を確認すること。**

## なぜmigration+DexScreenerなのか(2026-07、設計変更)

当初はpump.fun上の個別トークンの売買イベントをPumpPortalの
`subscribeTokenTrade`でリアルタイム受信し、作成直後20〜120秒の初動を見る
設計だった。しかし`subscribeTokenTrade`(および`subscribeAccountTrade`)は
**PumpPortal公式のAPIキー+SOL入りウォレットが必要な従量課金機能**である
ことが判明し(無料なのは`subscribeNewToken`と`subscribeMigration`のみ)、
無料運用の方針と合わなかった。

そこで、無料の`subscribeMigration`イベント(トークンが実際のDEXへ卒業した
瞬間)をトリガーに切り替え、卒業後の実際の取引状況は無料・APIキー不要の
DexScreener公開APIから取得する設計に変更した。DexScreenerはそもそも
卒業前のpump.funトークン(ボンディングカーブ上の仮想的な取引のみ)を
一切表示しないため、この2つの組み合わせは自然に噛み合う。

**トレードオフ**: 通知のタイミングが「作成から20〜120秒」ではなく「DEX
卒業した瞬間」になる(卒業は早くて数分、遅いと数時間後)。ただし卒業自体が
「ある程度本気で買われた証拠」でもあるため、質の面では悪くない。

## 仕組み

1. `pumpportal_client.py` がPumpPortalのWebSocket(`wss://pumpportal.fun/api/data`)
   へ接続し、`subscribeNewToken`(新規トークン作成、ログのみ)と
   `subscribeMigration`(実DEXへの卒業、観察開始のトリガー)を受信する。
   どちらも無料・APIキー不要。
2. 卒業を検知したら `token_watcher.py` が観察を開始する。
3. 卒業後0/60/300/900秒(`config.MIGRATION_CHECKPOINTS_SECONDS`)の各
   チェックポイントで、`dexscreener_client.py`がそのmintのDexScreenerペア
   情報を取得し(`GET /latest/dex/tokens/{mint}`、無料・APIキー不要)、
   `scoring.py`がその時点の状態から100点満点のスコアを計算する
   (`scoring.compute_score()`)。全項目が独立した関数として実装されており、
   将来Birdeye/Solscan/AIスコアリング等を追加する場合は`scoring._SCORERS`
   に関数を1つ足すだけでよい。

   | 項目 | 加点条件 |
   | --- | --- |
   | 買い件数(直近5分、DexScreener) | 5件+10 / 10件+20 / 20件以上+30 |
   | Buy/Sell比率(直近5分、DexScreener) | Buy>Sell+10 / 2倍以上+20 / 3倍以上+30 |
   | ユニーク買い手数(直近5分、DexScreener) | 2人+5 / 5人+10 / 10人以上+20 |
   | 出来高(直近5分、DexScreener) | `MIN_VOLUME_USD_FOR_SCORE`(USD)以上で+10 |
   | 流動性(DexScreener) | `MIN_LIQUIDITY_USD_FOR_SCORE`(USD)以上で+10 |
   | 価格変動(直近5分、DexScreener) | プラス+5 / 20%以上+10 / 50%以上+20 |
   | RugCheckセーフティ | 危険フラグなし+10 / **"danger"フラグ検出時はスコアを強制的に0点にする** |
   | 発行者ブラックリスト | 該当なし0 / **過去に問題のあった発行者による再発行を検出時はスコアを強制的に0点にする** |

   **ユニーク買い手数**は、少数のウォレットが自作自演(ウォッシュトレード)
   で買い件数だけを水増しして見かけの活気を演出するケースへの対策。
   買い件数(Buy/Sell比率含む)は「何回買われたか」しか見ないため、同じ
   ウォレットが連続で買っても加点されてしまう。DexScreenerのAPIレスポンス
   に元々含まれる`buyers.m5`フィールド(直近5分の実際に異なるウォレット数)
   を別項目として加点することで、件数は多いが実質1〜2人しか買っていない
   トークンの評価を下げる。

   卒業直後でDexScreenerのインデックスがまだ追いついていない場合は
   ペアが見つからず(`has_pair_data=False`)、その回は該当項目が0点で次の
   チェックポイントを待つ。RugCheck([rugcheck.xyz](https://rugcheck.xyz/)、
   無料・APIキー不要)は`rugcheck_client.py`がトークン1件につき1回だけ
   取得し(未認証だと10req/minとDexScreenerより厳しいレート制限がある
   ため)、mint権限が発行者に残っている・上位保有者への極端な集中等の
   `"danger"`レベルのリスクフラグを1件でも検出したら、他の項目がどれだけ
   高くても通知させない(他の項目の合計がどれだけ増えても確実に相殺
   できる大きな負の点数を加える設計。`scoring._score_rugcheck_safety`
   参照)。RugCheckの判定を過信しないこと(あくまで一つの参考情報)。

   **発行者ブラックリスト**(`creator_blocklist.py`)は、外部サービスへの
   登録不要でうち自身が観察結果から学習していく仕組み。RugCheckのレポート
   には発行者(creator)のウォレットアドレスも含まれており(無料)、以下の
   場合にその発行者を`logs/creator_blocklist.json`へ記録する:
   - RugCheckで`"danger"`フラグを検出した瞬間
   - 通知後、`CREATOR_BLOCKLIST_CRASH_THRESHOLD_PCT`(既定-90%)以上
     時価総額が下落したと`outcome_tracker.py`が検出した瞬間(=同じ発行者
     が「初動は良さそうに見えて実は大暴落した」トークンを出したケース)

   一度記録された発行者は、**別の名前・別のトークンで再発行してきても**
   次回から即座にスコア0点になる(名前だけを見て判定する方式だと、
   無関係な人が偶然似た名前を使っただけのケースまで誤って弾いてしまう
   ため、名前ではなく発行者のウォレットアドレスで判定する)。サービス
   再起動を挟んでも記憶は保持される。
4. スコアから通知ティアを判定する(`scoring.tier_for_score()`)。
   - **HIGH**(`HIGH_SCORE_THRESHOLD`以上、既定75): 🚨 Discord通知
   - **WATCH**(`WATCH_SCORE_THRESHOLD`以上、既定70): ⚠ Discord通知
   - **LOW**(`LOW_SCORE_THRESHOLD`以上、既定35): ログ保存のみ(Discordへは送らない)
   - それ未満: 何もしない(デバッグログにのみ未加点理由を残す)

   ティアが初めてWATCH以上になった瞬間、またはより高いティアへ上昇した
   瞬間(LOW→WATCH、WATCH→HIGH)だけ通知する(`scoring.is_upgrade()`)。
   最終チェックポイント(900秒)までは通知後も観察を継続する。

   Discordの通知本文はスコア・銘柄名・mintアドレス・Phantomアプリで直接
   開くリンクのみの最小限にしている(コピペ・タップだけで済むことを想定。
   出来高・注意書き等の詳細はログ側にのみ残す)。Phantomリンクには
   `PHANTOM_REFERRAL_ID`(`.env`、任意)が設定されていれば紹介コードを
   付与する。例:
   ```
   🚨 HIGH Score: 80/100 ⭐⭐
   Some Coin ($SOME)
   GwTGo5T58zBxzDv825rV2LUdUesGsEgpSQPt3jnxpump
   https://phantom.com/tokens/solana/GwTGo5T58zBxzDv825rV2LUdUesGsEgpSQPt3jnxpump
   ```
   スコア行の末尾の★は、直近5分のユニーク買い手数を表す(2人以上★1つ/
   5人以上★2つ/10人以上★3つ、`scoring.UNIQUE_BUYERS_M5_TIER_THRESHOLDS`
   参照)。スコアの内訳を見なくても、少数のウォレットの自作自演ではなく
   実際に多くの人が買っているかどうかが一目でわかる。
   スコアが**100点満点**の場合、上記の通常チャンネルに加えて
   `DISCORD_PERFECT_SCORE_WEBHOOK_URL`(`.env`、任意)で指定した別の
   Webhookにも同じ内容を送る。満点だけを集めた専用チャンネルを別に
   用意したい場合に使う(未設定なら送らない)。
5. 通知したトークンは `outcome_tracker.py` が引き続き、通知時点からの
   30分/1時間/24時間後にDexScreenerから時価総額を取得し直し、変化率を
   `logs/outcomes.jsonl`へ1行ずつ追記する(24時間経過後に追跡終了)。

## セットアップ

Python 3.11以上を推奨。

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# .envを編集してDISCORD_WEBHOOK_URLを設定し、DISCORD_ENABLED=trueにする
.venv/bin/python main.py
```

## 設定項目(`.env`)

`.env.example`参照。主なもの:

| 変数名 | 既定値 | 説明 |
| --- | --- | --- |
| `DISCORD_ENABLED` | `false` | trueにしないと通知しない |
| `DISCORD_WEBHOOK_URL` | (空) | DiscordのWebhook URL |
| `DISCORD_PERFECT_SCORE_WEBHOOK_URL` | (空) | スコア100点満点だけを追加通知する専用チャンネルのWebhook URL(任意) |
| `MAX_TRACKED_TOKENS` | `500` | 同時観察数の上限(超過分は最古から間引く) |
| `MIN_VOLUME_USD_FOR_SCORE` | `300.0` | これ以上の直近5分出来高(USD)で加点 |
| `MIN_LIQUIDITY_USD_FOR_SCORE` | `2000.0` | これ以上の流動性(USD)で加点 |
| `HIGH_SCORE_THRESHOLD` | `75` | これ以上のスコアでHIGH通知 |
| `WATCH_SCORE_THRESHOLD` | `70` | これ以上のスコアでWATCH通知 |
| `LOW_SCORE_THRESHOLD` | `35` | これ以上のスコアでログ保存(Discordへは送らない) |
| `OUTCOMES_FILE_PATH` | `logs/outcomes.jsonl` | 通知後の結果トラッキングの出力先 |
| `CREATOR_BLOCKLIST_FILE_PATH` | `logs/creator_blocklist.json` | 発行者ブラックリストの出力先 |
| `CREATOR_BLOCKLIST_CRASH_THRESHOLD_PCT` | `-90.0` | 通知後この割合以上下落したら発行者をブロックリストへ追加 |

**通知が多すぎる/少なすぎる場合は、`WATCH_SCORE_THRESHOLD`/
`HIGH_SCORE_THRESHOLD`を`.env`で調整してから再起動してください。**
「もっと厳しく絞りたい」→閾値を上げる。「もっと通知が欲しい」→閾値を
下げる、または`MIN_VOLUME_USD_FOR_SCORE`/`MIN_LIQUIDITY_USD_FOR_SCORE`を
下げる。チェックポイント秒数(0/60/300/900)自体は`config.py`の
`MIGRATION_CHECKPOINTS_SECONDS`を直接編集する。

## VPSへのデプロイ(systemd)

```
# 1. VPS上でこのリポジトリをclone
git clone https://github.com/hiiragishinoaaaa-stack/artemis-ai-phantom.git /opt/artemis/artemis-ai-phantom
cd /opt/artemis/artemis-ai-phantom

# 2. venv作成・依存インストール
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. .env作成(DISCORD_WEBHOOK_URL等を設定)
cp .env.example .env
nano .env

# 4. systemdサービスを設置(ユーザー名・パスを環境に合わせて置換)
sudo cp systemd/phantom-sniper.service /etc/systemd/system/
sudo sed -i "s|__PHANTOM_USER__|$(whoami)|g; s|__PHANTOM_HOME__|/opt/artemis|g" /etc/systemd/system/phantom-sniper.service
sudo systemctl daemon-reload
sudo systemctl enable --now phantom-sniper

# 5. ログ確認
journalctl -u phantom-sniper -f
```

MT5(mt5_ai_trader)とは完全に独立したプロセスなので、同じVPS上で並行して
動かして問題ない(ファイル・ポートの衝突なし)。

### 更新(コードを変更した後の再デプロイ)

既にVPS上にデプロイ済みの場合は、clone/venv/systemdの設置をやり直す必要は
ない。最新コードを取得して依存関係を更新し、サービスを再起動するだけでよい。

```
cd /opt/artemis/artemis-ai-phantom
git pull origin main
.venv/bin/pip install -r requirements.txt
sudo systemctl restart phantom-sniper
journalctl -u phantom-sniper -f
```

## テスト

ネットワーク不要な部分(`token_watcher.py`のチェックポイント管理、
`scoring.py`のスコア計算、`discord_notifier.py`/`dexscreener_client.py`/
`rugcheck_client.py`のリクエスト組み立て、`creator_blocklist.py`の
永続化)は全てモック・フェイクでテストしている。`pumpportal_client.py`の
実際の接続・再接続ループは実サーバーが必要なため単体テスト対象外
(VPSで実際に動かして`journalctl`で確認する)。

```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 免責事項

このツールは公開されているオンチェーン情報をもとにした**情報提供のみ**を
行う。投資助言ではない。ミームコインは非常に高いリスク(詐欺・ラグプル・
価値のほぼ全損)を伴う。通知が来たトークンであっても、実際に売買するか
どうかの判断・実行・その結果については自己責任で行うこと。
