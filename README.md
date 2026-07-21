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

**「卒業前(作成直後)のトークンが来なくなった」のはこの変更が原因で、
バグではなく意図した設計変更。** 卒業前の状況を見るには有料の
`subscribeTokenTrade`(PumpPortal公式APIキー+SOL入りウォレットが必要)が
必須で、DexScreenerは卒業前のトークンを一切扱っていないため無料では
代替できない。元に戻すには実際に金銭コスト・ウォレットのリスクが伴う
ため、やる場合は事前に相談してから(`.env`を書き換えるだけでは済まない)。

なお、2026-07に発覚した別のバグとして、DexScreenerは同じmintに対して
「卒業前のpump.funボンディングカーブ自体のペア(`dexId=pumpfun`、卒業の
何時間も前に作られている)」と「卒業後の実際のDEXペア」の**2つ**を返す
ことがあり、`dexscreener_client.py`が単純に「最も流動性の高いペア」を
選んでいたため、稀に前者(卒業よりずっと前のペア)を選んでしまい、
出来高・価格変動・詳細リンクの行き先が不安定になっていた
(`_EXCLUDED_DEX_IDS`で修正済み)。「何時間も前のコインが今頃通知される」
「通知時点で既に大きく値動きしている」という体感の一部はこれが原因
だった可能性が高い(卒業から通知までの実際の遅延ではなく、参照していた
データが古かった)。

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
   | ユニーク買い手数(直近5分、Solanaオンチェーン) | 2人+5 / 5人+10 / 10人以上+20 |
   | 出来高(直近5分、DexScreener) | `MIN_VOLUME_USD_FOR_SCORE`(USD)以上で+10 |
   | 流動性(DexScreener) | `MIN_LIQUIDITY_USD_FOR_SCORE`(USD)以上で+10 |
   | 価格変動(直近5分、DexScreener) | プラス+5 / 20%以上+10 / 50%以上+20 |
   | RugCheckセーフティ | 危険フラグなし+10 / **"danger"フラグ検出時はスコアを強制的に0点にする** |
   | RugCheck注意フラグ | "warn"レベルのリスク1件ごとに-5点(3件以上は-15点で頭打ち、通知は止めない) |
   | 上位10保有者集中度(RugCheck) | 合計保有率が`HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT`以上で-10(⚠️) / `HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT`未満で+10(✅) |
   | 発行者ブラックリスト | 該当なし0 / **過去に問題のあった発行者による再発行を検出時はスコアを強制的に0点にする** |

   **ユニーク買い手数**は、少数のウォレットが自作自演(ウォッシュトレード)
   で買い件数だけを水増しして見かけの活気を演出するケースへの対策。
   買い件数(Buy/Sell比率含む)は「何回買われたか」しか見ないため、同じ
   ウォレットが連続で買っても加点されてしまう。当初DexScreenerのAPIに
   この値があると想定していたが、実際には存在しないことが判明したため
   (2026-07)、`solana_client.py`がSolanaブロックチェーンから直接、対象
   プールの直近の取引を読んで別ウォレット数を自前で集計する方式に変更した
   (詳細は次の「Solana RPC連携」参照)。件数は多いが実質1〜2人しか
   買っていないトークンの評価を下げる。

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

   `"danger"`未満の`"warn"`レベルのリスク(LP未ロック・上位保有者の
   集中度が中程度、等)は、通知自体は止めずに件数に応じて少し減点する
   だけにしている(`scoring._score_rugcheck_warnings`参照)。ラグプルの
   可能性があっても初動の伸びを狙いたい場面があるため、danger相当の
   確実な危険とは分けて扱う設計。

   **上位10保有者集中度**は、RugCheckレポートの`topHolders[]`(各保有者の
   保有率%)を合計10件分足し合わせたもの。極端な集中(例えば単一保有者が
   大半保有)は既にRugCheckの`"danger"`フラグで強制0点になるため、これは
   それより緩やかな「気になる程度の集中」を検出する追加シグナル
   (`scoring._score_holder_concentration`参照)。通知本文のスコア行末尾に
   ⚠️(集中しすぎ)/✅(健全に分散)としても表示される。

   **X(Twitter)/Telegramリンクの検出**はスコアには影響しない表示専用の
   情報。DexScreenerのペア情報に含まれる`info.socials[]`から検出し、
   見つかった場合は通知本文の銘柄名の隣にアイコンを付ける
   (`token_watcher.apply_snapshot`参照)。

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

   同じタイミングでチェックポイントを迎えたトークンは、最大
   `CHECKPOINT_CONCURRENCY`(既定8)件まで並行処理する(1件ずつ順番に
   処理すると、卒業数が多い時間帯にDexScreener/RugCheck/Solana RPC等への
   ネットワーク往復の待ち時間が積み重なり、処理が実時間に追いつかず
   「何時間も前に卒業したトークンの通知が今頃届く」という遅延が発生する
   ため、2026-07に並行化した)。

   Discordの通知本文はスコア・銘柄名のみの最小限にしている(コピペ・
   タップだけで済むことを想定。mintアドレス・出来高・注意書き等の詳細は
   「詳細」ボタンの遷移先とログ側にのみ残す)。本文の下に「詳細」
   「Phantomで開く」のリンクボタンが付く(URLを開くだけのボタンなので
   Discord Bot不要、Webhookのみで送信できる)。「詳細」ボタンは、まず
   DexScreenerの当該ペアページ(出来高・チャート・保有者リンク等が見れる
   外部サービス。SupabaseやダッシュボードがVPS側で落ちていても常に動く)
   を優先し、それが無い場合のみ`DASHBOARD_PUBLIC_URL`(`.env`、任意)が
   設定されていればダッシュボードのその銘柄の詳細ページ(`/token/{mint}`)
   にフォールバックする(`discord_notifier._detail_url`参照)。
   「Phantomで開く」は常に付き、`PHANTOM_REFERRAL_ID`(`.env`、任意)が
   設定されていれば紹介コードを付与する。例:
   ```
   ⭐⭐ 80/100 ✅
   Some Coin ($SOME) 🐦✈️
   [詳細] [Phantomで開く]
   ```
   スコア行の先頭の★は、直近5分のユニーク買い手数を表す(2人以上★1つ/
   5人以上★2つ/10人以上★3つ、`scoring.UNIQUE_BUYERS_M5_TIER_THRESHOLDS`
   参照)。スコアの内訳を見なくても、少数のウォレットの自作自演ではなく
   実際に多くの人が買っているかどうかが一目でわかる。続く⚠️/✅は上位10
   保有者集中度、名前行の🐦✈️はX/Telegramリンクの検出を表す(いずれも
   `.env`の`DISCORD_*_EMOJI`でDiscordサーバーのカスタム絵文字
   (`<:名前:ID>`形式)に差し替え可能)。
   スコアが**100点満点**の場合、上記の通常チャンネルに加えて
   `DISCORD_PERFECT_SCORE_WEBHOOK_URL`(`.env`、任意)で指定した別の
   Webhookにも同じ内容を送る(★の数は問わない、未設定なら送らない)。

   最初の通知時点(卒業直後)はDexScreenerの直近5分ウィンドウがまだ始まった
   ばかりで、★0のまま通知されることが多い。そこで、既に通知済みの
   トークンが**後のチェックポイントで初めてユニーク買い手★1つ以上を
   確認できた瞬間**、通常とは別の`DISCORD_FOLLOWUP_WEBHOOK_URL`(`.env`、
   任意)へ「🔥 ユニーク買い手★を確認」という追い通知を1トークンにつき
   最大1回だけ送る(`main._decide_notification_action`参照。未設定なら
   送らない。一度もHIGH/WATCH通知していないトークンには発火しない)。
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
| `DISCORD_FOLLOWUP_WEBHOOK_URL` | (空) | 通知済みトークンが後からユニーク買い手★1つ以上を確認できた時だけ送る追い通知のWebhook URL(任意) |
| `MAX_TRACKED_TOKENS` | `500` | 同時観察数の上限(超過分は最古から間引く) |
| `CHECKPOINT_CONCURRENCY` | `8` | チェックポーントを迎えたトークンを同時に何件まで並行処理するか(上げすぎると各APIのレート制限に引っかかりやすくなる) |
| `MIN_VOLUME_USD_FOR_SCORE` | `300.0` | これ以上の直近5分出来高(USD)で加点 |
| `MIN_LIQUIDITY_USD_FOR_SCORE` | `2000.0` | これ以上の流動性(USD)で加点 |
| `HIGH_SCORE_THRESHOLD` | `75` | これ以上のスコアでHIGH通知 |
| `WATCH_SCORE_THRESHOLD` | `70` | これ以上のスコアでWATCH通知 |
| `LOW_SCORE_THRESHOLD` | `35` | これ以上のスコアでログ保存(Discordへは送らない) |
| `OUTCOMES_FILE_PATH` | `logs/outcomes.jsonl` | 通知後の結果トラッキングの出力先 |
| `CREATOR_BLOCKLIST_FILE_PATH` | `logs/creator_blocklist.json` | 発行者ブラックリストの出力先 |
| `CREATOR_BLOCKLIST_CRASH_THRESHOLD_PCT` | `-90.0` | 通知後この割合以上下落したら発行者をブロックリストへ追加 |
| `HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT` | `50.0` | 上位10保有者の合計保有率がこれ以上で⚠️・減点 |
| `HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT` | `20.0` | 同・これ未満で✅・加点 |
| `SOLANA_RPC_URL` | 公開エンドポイント | Solana RPCのURL(詳細は下記「Solana RPC連携」参照) |
| `SOLANA_MAX_SIGNATURES_PER_CHECKPOINT` | `20` | ユニーク買い手数集計1回あたりに読む取引の上限 |
| `SUPABASE_URL` | (空) | SupabaseプロジェクトのURL(任意、詳細は下記「Supabase連携」参照) |
| `SUPABASE_SERVICE_ROLE_KEY` | (空) | Supabaseのservice_roleキー(任意、秘密鍵) |
| `DASHBOARD_SERVER_PORT` | `8790` | ダッシュボード(`dashboard_server.py`)の待受ポート |
| `DASHBOARD_API_TOKEN` | (空) | ダッシュボードAPIの簡易保護トークン(任意) |
| `DASHBOARD_PUBLIC_URL` | (空) | ダッシュボードの外部公開URL(例`http://76.13.180.239:8790`)。設定するとDiscord通知に「詳細」ボタンが付く(任意) |

**通知が多すぎる/少なすぎる場合は、`WATCH_SCORE_THRESHOLD`/
`HIGH_SCORE_THRESHOLD`を`.env`で調整してから再起動してください。**
「もっと厳しく絞りたい」→閾値を上げる。「もっと通知が欲しい」→閾値を
下げる、または`MIN_VOLUME_USD_FOR_SCORE`/`MIN_LIQUIDITY_USD_FOR_SCORE`を
下げる。チェックポイント秒数(0/60/300/900)自体は`config.py`の
`MIGRATION_CHECKPOINTS_SECONDS`を直接編集する。

## Solana RPC連携(ユニーク買い手数=★表示の集計方法)

★表示・追い通知の元になる「直近5分のユニーク買い手数」は、DexScreenerの
APIには存在しないため(2026-07判明)、`solana_client.py`がSolanaの
ブロックチェーンから直接、対象プールの直近の取引を読んで集計している。

`SOLANA_RPC_URL`は既定で無料・APIキー不要の公開エンドポイント
(`api.mainnet-beta.solana.com`)を使うが、**レート制限が厳しく不安定な
ことがある**(取得に失敗した場合は前回の値を維持するだけで、通知自体は
止まらない)。安定させたい場合は以下の手順で無料のAPIキーを取得して
`.env`に設定することを推奨する:

1. [helius.dev](https://www.helius.dev/)で無料アカウントを作成(クレジット
   カード不要、月100万クレジットまで無料)。
2. ダッシュボードでプロジェクトを作成し、表示されるRPC URL
   (`https://mainnet.helius-rpc.com/?api-key=...`の形式)をコピー。
3. `.env`の`SOLANA_RPC_URL`に貼り付けて、`phantom-sniper`を再起動する。

この集計は卒業直後(0秒)のチェックポイントでは行わない(取引の署名取得+
複数の取引詳細取得が必要で数秒かかることがあり、初動の通知速度を落とし
たくないため)。そのため最初の通知は★0のことが多く、後で人が買い始めた
ことが確認できたタイミングで`DISCORD_FOLLOWUP_WEBHOOK_URL`へ追い通知する
設計になっている(前述の「仕組み」参照)。

## Supabase連携(通知履歴・結果の永続化、任意)

`logs/outcomes.jsonl`や`logs/creator_blocklist.json`はローカルファイルで
完結しているため、Supabaseを使わなくてもボット自体は普通に動く。ただし
以下がしたい場合はSupabaseの設定を推奨する:

- スマホからいつでも見れる**ダッシュボード**(次のセクション)
- SQLで自由に**解析**(発行者ごとの成績、★の数と勝率の相関、等)

### セットアップ手順(5分程度)

1. [supabase.com](https://supabase.com/)で無料アカウントを作成し、新規
   プロジェクトを作成する(リージョンは適当でよい、プロジェクト作成に
   1〜2分かかる)。
2. 作成できたら、左メニューの**SQL Editor** → **New query**を開き、この
   リポジトリの`supabase_schema.sql`の中身を全部貼り付けて**Run**を押す。
   これで`notifications`/`outcomes`/`creator_blocklist`の3テーブルと、
   分析用のビュー(`v_notification_latest_outcome`)が1回で全部できる。
3. 左メニューの**Settings** → **API**を開き、以下2つをコピーして`.env`へ
   設定する:
   - **Project URL** → `SUPABASE_URL`
   - **service_role secret**(下の方にある、`anon`ではなく`service_role`
     の方) → `SUPABASE_SERVICE_ROLE_KEY`(**秘密鍵。他人に絶対共有しない**)
4. `phantom-sniper`サービスを再起動する(`sudo systemctl restart phantom-sniper`)。
   以降、通知・結果・ブロックリスト登録のたびに自動でSupabaseへも書き込まれる
   (書き込み失敗時はログに警告が出るだけで、ボット本体の動作は止まらない)。

### SQLでの解析例

Supabaseの**SQL Editor**(または**Table Editor**)からいつでも自由に
クエリできる。例:

```sql
-- ★の数ごとの、30分後の平均変化率・勝率
select
  n.star_count,
  count(*) as n,
  avg(o.change_pct) as avg_change_pct,
  round(100.0 * avg((o.change_pct > 0)::int), 1) as win_rate_pct
from notifications n
join outcomes o on o.mint = n.mint and o.checkpoint_seconds = 1800
where n.notification_type = 'primary'
group by n.star_count
order by n.star_count desc;

-- 発行者ごとの通知回数(繰り返し良いコインを出している発行者を探す)
select creator, count(*) as notif_count, avg(score) as avg_score
from notifications
where creator <> ''
group by creator
order by notif_count desc
limit 20;
```

## ダッシュボード(任意、Supabase連携が必要)

`dashboard_server.py`は、Supabaseに溜まったデータをスマホのブラウザで
見れるようにするだけの、読み取り専用の軽量サーバー(外部ライブラリ不使用、
ビルド不要)。止まっていても本体のボット(`main.py`)には一切影響しない。

表示内容: 総通知数・HIGH/WATCH件数・★分布・チェックポイント別勝率
(30分/1時間/24時間後、`change_pct > 0`の割合)・発行者ブラックリスト件数・
直近の通知一覧(追い通知も含む)。30秒ごとに自動更新される。

各銘柄の詳細ページ(`/token/<mint>`)は、Discord通知の「詳細」ボタンから
遷移できる(`DASHBOARD_PUBLIC_URL`を`.env`に設定しておく必要がある。
下記デプロイ手順の6を参照)。その銘柄の通知履歴(通常/追い通知それぞれ)・
出来高・上位10保有者集中度・X/Telegram連携・発行者アドレス・通知後の
時価総額推移をまとめて表示する。

```
# systemdサービスとして起動(phantom-sniperと同じ.envを共有する)
sudo cp systemd/phantom-dashboard.service /etc/systemd/system/
sudo sed -i "s|__PHANTOM_USER__|$(whoami)|g; s|__PHANTOM_HOME__|/opt/artemis|g" /etc/systemd/system/phantom-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now phantom-dashboard

# ポートを開ける(VPSのファイアウォール。自分のIPだけに絞るとより安全)
sudo ufw allow 8790/tcp
```

起動したら、スマホのブラウザで`http://<VPSのIPアドレス>:8790/`を開く。
`DASHBOARD_API_TOKEN`を`.env`で設定している場合は、ページ上部の入力欄に
同じ値を貼って「保存」を押すと認証される(未設定なら何もしなくてよい)。

## チャット返信Bot(任意、遊び機能)

`chat_reply_bot.py`は、特定の1人が特定の言葉を発言したら固定の返信を
送るだけの、スキャナー本体とは無関係な遊び機能(止まっていても本体
[`main.py`]には一切影響しない)。通知用のWebhookは送信専用で他人の
発言を読み取れないため、これだけは読み取り権限を持つ普通のDiscord Bot
として実装している(`discord.py`使用)。

1. [Discord Developer Portal](https://discord.com/developers/applications)
   を開き、「New Application」で適当な名前のアプリを作成する。
2. 左メニューの「Bot」→「Reset Token」でBotトークンを発行し、コピーする
   (秘密鍵、`.env`にのみ設定する)。
3. 同じ「Bot」ページの「Privileged Gateway Intents」で
   **MESSAGE CONTENT INTENT** をONにする(発言内容を読み取るために必須)。
4. 左メニューの「OAuth2」→「URL Generator」で、SCOPESに`bot`、
   BOT PERMISSIONSに`Send Messages`・`Read Messages/View Channels`を
   チェックし、生成されたURLを開いて自分のサーバーに招待する。
5. 反応してほしい相手のDiscordユーザーIDを確認する(Discordアプリの
   設定→詳細設定→開発者モードをON→相手のアイコンを長押し/右クリック→
   「IDをコピー」)。

`.env`に以下を設定する。「この言葉を含む発言→この返信」のペアは
`CHAT_REPLY_TRIGGER_WORD_1`/`CHAT_REPLY_MESSAGE_1`のように番号付きで
最大10個まで好きなだけ登録できる(1つの発言に複数の言葉が含まれる場合は
番号の小さいペアが優先される):

```
CHAT_REPLY_ENABLED=true
DISCORD_BOT_TOKEN=(手順2で発行したトークン)
CHAT_REPLY_TARGET_USER_ID=(手順5で確認したユーザーID)
CHAT_REPLY_TRIGGER_WORD_1=おはよう
CHAT_REPLY_MESSAGE_1=おはよー!♥️
CHAT_REPLY_TRIGGER_WORD_2=おやすみ
CHAT_REPLY_MESSAGE_2=おやすみ~
CHAT_REPLY_TRIGGER_WORD_3=可愛い
CHAT_REPLY_MESSAGE_3=ありがとー!!
```

```
# systemdサービスとして起動(phantom-sniperと同じ.envを共有する)
sudo cp systemd/phantom-chat-reply.service /etc/systemd/system/
sudo sed -i "s|__PHANTOM_USER__|$(whoami)|g; s|__PHANTOM_HOME__|/opt/artemis|g" /etc/systemd/system/phantom-chat-reply.service
sudo systemctl daemon-reload
sudo systemctl enable --now phantom-chat-reply
```

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

6. ダッシュボードを使っていて、Discord通知に「詳細」ボタンを出したい場合は、
   `.env`に`DASHBOARD_PUBLIC_URL=http://<VPSのIPアドレス>:8790`を追加して
   `sudo systemctl restart phantom-sniper`(未設定でもボット自体は問題なく
   動く。「詳細」ボタンが付かないだけ)。

MT5(mt5_ai_trader)とは完全に独立したプロセスなので、同じVPS上で並行して
動かして問題ない(ファイルの衝突なし。ダッシュボードを使う場合のポート
`8790`もMT5側の`5173`/`8787`とは別なので衝突しない)。

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

`git pull`のたびに`.env.example`へ新しい設定項目が増えていることがある
(既定値が使われるだけなので気付きにくい)。以下で、`.env.example`には
あるのに自分の`.env`には無いキーだけを一覧できる:

```
comm -23 <(grep -oE '^[A-Z_]+=' .env.example | sort -u) <(grep -oE '^[A-Z_]+=' .env | sort -u)
```

何か出てきたら、`.env.example`の該当行を見てから自分の`.env`にも追記する
(値を秘密にする必要が無いものは既定値のままコピーでよい)。

## テスト

ネットワーク不要な部分(`token_watcher.py`のチェックポイント管理、
`scoring.py`のスコア計算、`discord_notifier.py`/`dexscreener_client.py`/
`rugcheck_client.py`/`supabase_client.py`のリクエスト組み立て、
`creator_blocklist.py`の永続化、`dashboard_analytics.py`の集計ロジック、
`main.py`の通知アクション判定(`_decide_notification_action`))は全て
モック・フェイクでテストしている。`pumpportal_client.py`の実際の接続・
再接続ループと`dashboard_server.py`のHTTPサーバー本体は実サーバーが必要な
ため単体テスト対象外(VPSで実際に動かして`journalctl`/ブラウザで確認する)。

```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 免責事項

このツールは公開されているオンチェーン情報をもとにした**情報提供のみ**を
行う。投資助言ではない。ミームコインは非常に高いリスク(詐欺・ラグプル・
価値のほぼ全損)を伴う。通知が来たトークンであっても、実際に売買するか
どうかの判断・実行・その結果については自己責任で行うこと。
