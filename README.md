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
- ✅/❌ **既定では自動売買・ウォレット操作は一切行わない。** あくまで
  人間が判断するための情報提供ツール。実際に買うかどうかはDiscord通知を
  見た人がPhantom等のウォレットで手動判断・手動操作する想定。ただし
  2026-07に、明示的に有効化した場合のみ動く⚠️実験的な自動売買機能
  (`trade_executor.py`、既定完全OFF・二重ゲート)を追加した。詳細・
  リスクは下記「自動売買(実験的機能)」参照。
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
   | 名前/ティッカー重複(なりすまし対策) | 該当なし0 / 既出の名前・ティッカーを別mintが名乗っている場合-50点(通知は止めない) |

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

   **名前/ティッカー重複検出**(`token_name_history.py`、2026-07追加)は、
   pump.funで頻発する「既に伸びたトークンと同じ名前・同じティッカー
   ($SYMBOL)を付けた別mintのなりすましトークン」対策。DEX卒業を検知した
   瞬間に、これまで観測した名前/ティッカーの記録(`logs/token_name_
   history.json`)と照合し、別mintが同じ名前またはティッカーを既に
   使っていたら-50点する。発行者ブラックリストと違い、一般的な単語の
   名前(例: "Doge"や"Trump")が無関係な人同士で偶然重複するだけの
   ケースもあるため、通知自体は止めない(あくまで100点/★★★には
   到達しにくくする程度の減点)。検出時はDiscord通知本文にも
   🚨(`DISCORD_DUPLICATE_NAME_EMOJI`)付きの警告行が追加され、先行mint
   アドレスが表示されるので、人間が最終確認できる。
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
| `TOKEN_NAME_HISTORY_FILE_PATH` | `logs/token_name_history.json` | 名前/ティッカー重複履歴(なりすまし対策)の出力先 |
| `DISCORD_DUPLICATE_NAME_EMOJI` | `🚨` | 名前/ティッカー重複を検出した通知に付ける警告絵文字 |
| `HOLDER_CONCENTRATION_WARN_THRESHOLD_PCT` | `50.0` | 上位10保有者の合計保有率がこれ以上で⚠️・減点 |
| `HOLDER_CONCENTRATION_HEALTHY_THRESHOLD_PCT` | `20.0` | 同・これ未満で✅・加点 |
| `SOLANA_RPC_URL` | 公開エンドポイント | Solana RPCのURL(詳細は下記「Solana RPC連携」参照) |
| `SOLANA_MAX_SIGNATURES_PER_CHECKPOINT` | `20` | ユニーク買い手数集計1回あたりに読む取引の上限 |
| `SUPABASE_URL` | (空) | SupabaseプロジェクトのURL(任意、詳細は下記「Supabase連携」参照) |
| `SUPABASE_SERVICE_ROLE_KEY` | (空) | Supabaseのservice_roleキー(任意、秘密鍵) |
| `DASHBOARD_SERVER_PORT` | `8790` | ダッシュボード(`dashboard_server.py`)の待受ポート |
| `DASHBOARD_API_TOKEN` | (空) | ダッシュボードAPIの簡易保護トークン(任意) |
| `DASHBOARD_PUBLIC_URL` | (空) | ダッシュボードの外部公開URL(例`http://76.13.180.239:8790`)。設定するとDiscord通知に「詳細」ボタンが付く(任意) |
| `AUTO_TRADE_ENABLED` / `AUTO_TRADE_CONFIRMED_RISK` | `false` / `false` | ⚠️自動売買の二重ゲート(詳細は下記「自動売買(実験的機能)」参照) |
| `PERP_ENABLED` | `false` | パーペチュアルのロング/ショートシグナル機能(詳細は下記「パーペチュアル・ロング/ショートシグナル」参照) |

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

`.env`を書き換えた後にBotへ反映させるには再起動が必要。スマホのブラウザ
端末では長いコマンドの貼り付けが途中で欠けやすいため、以下の短い1行
だけで「再起動→状態確認→直近ログ表示」までまとめて行うスクリプトを
用意している(初回だけ実行すれば、以降は`botrestart`という短い
コマンド1つで再起動できるようになる):

```
curl -fsSL https://raw.githubusercontent.com/hiiragishinoaaaa-stack/artemis-ai-phantom/main/scripts/restart_chat_reply.sh | sudo bash
```

## ⚠️⚠️⚠️ 自動売買(実験的機能、既定OFF)

`trade_executor.py`は、スコアが**満点(既定100点)**かつ**RugCheck危険・
発行者ブラックリスト・なりすまし検出のいずれにも該当せず**、**DEX卒業
からまだ間もない(既定60秒以内)**トークンだけを対象に、Jupiter Swap
(Solana上のDEXアグリゲーター、無料・APIキー不要)経由で少額の自動購入を
行い、利確(%)・損切り(%)・最大保有時間のいずれかに達したら自動的に
売却する実験的な機能。**本体[main.py]と同じプロセス内で動く**(別サービス
不要、`.env`の設定だけで有効/無効を切り替えられる)。

**これは実際のお金を動かす機能です。** 以下を必ず理解した上で使うこと:

- スコア100点というフィルターがあっても、詐欺・ラグプル・スリッページに
  よる損失は防ぎきれない(「できること・できないこと」参照)。
- このリポジトリの開発環境には実際に資金の入ったSolanaウォレットが無いため、
  **本番のメインネットに対するエンドツーエンド検証(実際に買って売って
  みるところまで)はできていない。** コードレビューと単体テスト(モック)
  だけを通した状態。
- 秘密鍵(`SOLANA_WALLET_PRIVATE_KEY`)を持つ者はウォレットの全資金を
  動かせる。**普段使いのメインウォレットの鍵は絶対に使わないこと。**
  この機能専用に、少額だけ入金した新しいウォレットを作ること。

### 有効化するには(全て揃わないと動かない、事故防止の二重ゲート)

1. Phantom等で**この機能専用の新しいウォレット**を作る(既存のメイン
   ウォレットは絶対に使わない)。
2. そのウォレットに、試したい分だけ少額のSOLを送金する(例: 0.1〜0.3 SOL
   程度。`AUTO_TRADE_BUY_AMOUNT_SOL`×`AUTO_TRADE_MAX_OPEN_POSITIONS`の
   目安以上を入れておく)。
3. そのウォレットの「秘密鍵をエクスポート」でbase58形式の文字列を取得する
   (Phantom: 設定→セキュリティとプライバシー→秘密鍵を表示)。
4. `.env`に以下を設定する(`set_env`関数等で追記でも、直接書き換えでもよい):

```
AUTO_TRADE_ENABLED=true
AUTO_TRADE_CONFIRMED_RISK=true
SOLANA_WALLET_PRIVATE_KEY=(手順3で取得した値)
DISCORD_TRADE_WEBHOOK_URL=(買い/売りの結果を通知したいチャンネルのWebhook URL)
```

5. 必要なら以下も好みに合わせて調整する(既定値のままでも動く):
   `AUTO_TRADE_BUY_AMOUNT_SOL`(1回の購入額、既定0.02 SOL)・
   `AUTO_TRADE_MAX_OPEN_POSITIONS`(同時保有数上限、既定3)・
   `AUTO_TRADE_TAKE_PROFIT_PCT`(利確%、既定50)・
   `AUTO_TRADE_STOP_LOSS_PCT`(損切り%、既定-30)・
   `AUTO_TRADE_MAX_HOLD_SECONDS`(強制手仕舞いまでの秒数、既定3600)。
6. `sudo systemctl restart phantom-sniper`で反映。起動ログに
   `自動売買 status=ready ready=True`と出れば有効化成功(`ready=False`
   の場合はstatusに理由が出る)。

無効化するにはいつでも`AUTO_TRADE_ENABLED=false`に戻して再起動すればよい
(保有中の建玉があっても、監視ループ自体は`AUTO_TRADE_ENABLED`を見て
スキップするだけなので、既存の建玉情報は`logs/positions.json`に残る。
手動で売る場合はPhantomアプリから直接操作すること)。

### 仕組み(内部の流れ)

1. `main.py`のチェックポイント処理で、スコア計算直後に
   `trade_executor.should_auto_buy()`が全条件を満たすか判定する
   (二重ゲート・RugCheck危険/発行者ブラックリスト/なりすまし検出の
   除外・満点判定・経過時間・同時保有数上限、全て純粋関数で単体テスト
   済み)。
2. 満たせば`jupiter_client.py`がJupiter Swap APIで見積もり→トランザクション
   組み立て→`solders`(軽量なSolana署名ライブラリ)で署名→Solana RPCへ
   送信、までを行う。
3. 購入が成功したら`position_tracker.py`が建玉(エントリー価格・数量・
   時刻)を`logs/positions.json`へ記録し、Discordへ通知する
   (`DISCORD_TRADE_WEBHOOK_URL`)。
4. 別ループ(`_position_monitor_loop`、`AUTO_TRADE_POSITION_POLL_SECONDS`
   間隔、既定15秒)が保有中の建玉を巡回し、利確/損切り/最大保有時間
   超過のいずれかに達したら、実際のオンチェーン残高を再取得した上で
   全量売却する(自前の記録した数量は信用しない。スリッページ等による
   ズレを吸収するため)。

## パーペチュアル・ロング/ショートシグナル(実験的機能、`perp_sniper.py`)

ミームコインのスキャナー本体(`main.py`)とは**完全に独立した別プロセス**
(動いていなくても本体には一切影響しない)。BTCUSDT/ETHUSDT/SOLUSDT等の
パーペチュアル先物について、Binance Futuresの公開market data(無料・
APIキー不要)からEMAトレンド・RSI・モメンタム・ファンディングレートを
組み合わせた簡易的なロング/ショートシグナルを計算し、`PERP_POLL_INTERVAL_
SECONDS`(既定15分)ごとにDiscordへ通知する(`perp_signals.compute_signal`
参照)。

**トレンド追従シグナル(EMA/RSI)の実発注は未実装。** `PERP_PAPER_TRADING_
ENABLED=true`(既定ON、実資金を動かさないため安全側の既定にしている)に
すると、「もし建てていたら」のペーパートレード(モック)としてレバレッジ
込みの損益をシミュレートし、その結果もDiscordへ通知する
(`perp_paper_trader.py`)。2026-07のバックテストで長期間(8〜9ヶ月)の
検証をした結果、勝率約50%・ほぼ無風・最大ドローダウン-63.5%という結果に
なり実資金投入に値しないと判断したため、こちらは実発注を実装していない
(詳細は下記「バックテスト」参照)。

**グリッドトレード戦略は実発注を実装済み(Hyperliquid、既定OFF)。**
`PERP_GRID_ENABLED=true`でペーパートレード、`PERP_GRID_LIVE_ENABLED=true`
(+二重ゲート)で実際にHyperliquidへ発注する。こちらはバックテストで
BTC/ETH/SOL全銘柄・複数期間・実手数料込みで一貫してBuy & Holdを上回る
結果が出たため実装した(詳細は下記「グリッドトレードのバックテスト」
「パーペチュアル実発注」参照)。

### 使い方

`.env`に以下を設定する:

```
PERP_ENABLED=true
PERP_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
DISCORD_PERP_WEBHOOK_URL=(シグナル通知を送りたいチャンネルのWebhook URL)
PERP_PAPER_TRADING_ENABLED=true
```

systemdサービスとして起動する(`phantom-sniper`/`phantom-chat-reply`と
同じ`.env`を共有する):

```
sudo cp systemd/phantom-perp-sniper.service /etc/systemd/system/
sudo sed -i "s|__PHANTOM_USER__|$(whoami)|g; s|__PHANTOM_HOME__|/opt/artemis|g" /etc/systemd/system/phantom-perp-sniper.service
sudo systemctl daemon-reload
sudo systemctl enable --now phantom-perp-sniper
```

### グリッドトレードのライブ・ペーパートレード

`PERP_GRID_ENABLED=true`にすると、上記のトレンド追従シグナルとは別に
グリッドトレード(`perp_grid_backtest.py`で検証した戦略)もリアルタイムに
ペーパートレードする(実資金は動かさない、`grid_paper_trader.py`)。
`PERP_GRID_POLL_INTERVAL_SECONDS`(既定30秒)ごとに現在価格を確認し、
利確/損切りに達した建玉の決済と、新しく到達したグリッド水準への
「購入」を行う。取引件数が非常に多くなりやすいため、1件ごとではなく
`PERP_GRID_SUMMARY_INTERVAL_SECONDS`(既定24時間)ごとに集計をDiscordへ
通知する。

```
PERP_GRID_ENABLED=true
```

買いグリッドは「値下がりで水準に触れた場合のみ」買う(`grid_trading.
level_touched_on_dip`)。単純に「水準をまたいだか」だけで判定すると、
上昇中に水準を通過しただけでも買ってしまい、上昇相場では見かけ上の
勝率が実力以上に高く出て、相場が反転すると同じ歪みが逆に働いて
含み損が積み上がる(実際にこの問題が発生し、一晩で複数銘柄の勝率が
0%近くまで悪化したことがある)。

`PERP_GRID_SHORT_ENABLED=true`にすると、買いグリッドとは独立に売り
(ショート)グリッドも同時にペーパートレードする(「両建て」、
`level_touched_on_rise`で値上がり時のみ売る)。上下どちらの波でも
利確を狙えるが、必要証拠金・同時保有数は実質倍になり、含み損が
積み上がるリスクも買い・売り両方向に広がる点に注意。実発注
(`grid_live_trader.py`)は未対応、ペーパートレードのみ。

```
PERP_GRID_SHORT_ENABLED=true
```

## ⚠️⚠️⚠️ パーペチュアル実発注(Hyperliquid、実験的機能、既定OFF)

`grid_live_trader.py`は、上記グリッドトレード戦略を**実際のHyperliquid
(パーペチュアル取引所、Phantomウォレットの「パーペチュアル」機能も同じ
Hyperliquidを使っている)へ本物の注文として送信する**実験的な機能。
既定では完全に無効(ペーパートレードのまま)で、下記の手順を全て踏まないと
一切動かない。

**これは実際のお金を、しかもレバレッジをかけて動かす機能です。** 以下を
必ず理解した上で使うこと:

- グリッド戦略はバックテストでBuy & Holdを一貫して上回ったが、これは
  過去の値動きに対する検証結果であり、**将来の利益を保証するものでは
  ない。** 相場がレンジの外に大きく飛び出したまま戻らない
  (「握り込み」)場合、含み損を抱えたまま塩漬けになるリスクがある。
- レバレッジをかけている分、清算(ロスカット)は一瞬で・不可逆に起こる。
  「マイナスでも待てばプラスに戻るかもしれない」は清算後には通用しない。
- このリポジトリの開発環境には実際に資金の入ったHyperliquidウォレットが
  無いため、**本番(メインネット)に対するエンドツーエンド検証(実際に
  注文が通り、約定し、決済されるところまで)はできていない。** コード
  レビューと単体テスト(モック)だけを通した状態。まず**テストネット**
  (`HYPERLIQUID_USE_TESTNET=true`、既定値)で実際に注文が通ることを
  確認してから、少額でメインネットへ切り替えることを強く推奨する。
- 秘密鍵(`HYPERLIQUID_PRIVATE_KEY`)を持つ者はウォレットの全資金を
  動かせる。**普段使いのメインウォレットの鍵は絶対に使わないこと。**
  この機能専用に、少額だけ入金した新しいウォレット(Ethereum形式の
  アドレス。Hyperliquidはアービトラム上で動くため、MetaMask等
  Ethereum系のウォレットで作成できる)を作ること。
- 新規建玉(買い)・利確決済(売り)は指値注文(Alo=Add Liquidity Only、
  Maker確定)で送信するため、バックテストで検証したMaker手数料
  (0.015%)に近い条件で実際に取引される。ただし損切り決済だけは
  緊急性を優先して成行(Taker、0.045%)のまま(指値で約定を待っている
  間に含み損がさらに拡大するリスクを避けるため)。指値注文は送信直後
  すぐには約定しないことが多く、「板に並べました(約定待ち)」→
  「約定しました」の2段階でDiscord通知される。
- レバレッジをかけたロング建玉を保有している間、Hyperliquidでは
  ファンディング(1時間ごと)が発生する。ペーパートレード
  (`grid_paper_trader.py`)・実発注(`grid_live_trader.py`)ともに、
  決済のたびにその建玉の保有期間について実際のファンディングレート
  履歴(Binance Futuresの過去データを参考値として使用)を取得し、
  pnl_pctから差し引く(`perp_market_data.estimate_funding_cost_pct`)。
  ただしBinanceのファンディングは8時間区切りのため、数分〜数十分で
  決済される通常のグリッド取引では保有期間内に区切りイベントを1件も
  含まないことが多く、コストが0と算出されがち(過小評価になりうる)。
  逆に、価格がレンジ外に張り付いて長時間保有し続ける「握り込み」建玉
  ほど、この推定はより正確に効いてくる。
- **利確率が63%を下回るとトータルで損をする計算になる**(利確
  +0.2%×レバレッジ−手数料 ≒ +0.33%、損切り−0.1%×レバレッジ−手数料
  ≒ −0.57%という非対称な設計のため。レバレッジを上げても、勝ち・
  負け・手数料が全部同じ倍率で大きくなるだけでこの比率は変わらない)。
  バックテストの好成績はこの水準を上回る利確率が続いた結果であり、
  実際に同じ利確率が今後も続く保証は無い。

### 有効化するには(全て揃わないと動かない、事故防止の二重ゲート)

1. MetaMask等で**この機能専用の新しいEthereum形式ウォレット**を作る
   (既存のメインウォレットは絶対に使わない)。
2. まずはテストネットで試す。Hyperliquidのテストネット用フォーセット
   (無料のテスト用資金配布)でUSDCを入手し、そのウォレットへ入れる。
   動作確認ができてからメインネットへ切り替える場合は、そのウォレット
   へ試したい分だけ少額のUSDCを送金する。
3. そのウォレットの秘密鍵(0xから始まる16進数文字列)をエクスポート
   する。
4. `.env`に以下を設定する:

```
PERP_GRID_ENABLED=true
PERP_GRID_LIVE_ENABLED=true
PERP_GRID_LIVE_CONFIRMED_RISK=true
HYPERLIQUID_PRIVATE_KEY=(手順3で取得した値)
HYPERLIQUID_USE_TESTNET=true
DISCORD_TRADE_WEBHOOK_URL=(買い/売りの結果を通知したいチャンネルのWebhook URL)
```

5. 必要なら以下も好みに合わせて調整する(既定値のままでも動く):
   `PERP_GRID_LIVE_ORDER_USD`(1回の注文額USD、既定10)・
   `PERP_GRID_LIVE_MAX_OPEN_POSITIONS`(同時保有数上限、既定5)・
   `PERP_GRID_LIVE_SLIPPAGE`(許容スリッページ、既定0.01=1%)。
6. `sudo systemctl restart phantom-perp-sniper`で反映。起動ログに
   `グリッド実発注 status=ready ready=True`と出れば有効化成功
   (`ready=False`の場合はstatusに理由が出る)。
7. テストネットで注文が通り、Discordに通知が来ることを確認できたら、
   `HYPERLIQUID_USE_TESTNET=false`に変更し、ウォレットにメインネットの
   実USDCを入金して再起動すれば本番稼働になる。

無効化するにはいつでも`PERP_GRID_LIVE_ENABLED=false`に戻して再起動すれば
よい(保有中の建玉があっても、監視ループ自体は`PERP_GRID_LIVE_ENABLED`を
見てスキップするだけなので、既存の建玉情報は`logs/grid_live_positions.json`
に残る。手動で決済する場合はHyperliquidのアプリ/サイトから直接操作する
こと)。

### バックテスト(`perp_backtest.py`)

実運用(通知・ペーパートレード)の前に、`perp_signals.compute_signal()`の
判定ロジックが過去の値動きに対して実際どうだったかを検証できる。各時点の
シグナルはその時点までのデータだけで計算する(未来を覗き見ない設計)。

```
.venv/bin/python perp_backtest.py --symbol BTCUSDT
.venv/bin/python perp_backtest.py --symbol ETHUSDT --interval 4h --limit 1000 --leverage 3
.venv/bin/python perp_backtest.py --symbol BTCUSDT --daily-loss-limit -20
```

`--daily-loss-limit`は、その日の損益合計が指定した%(マイナス値)を
下回ったらその日は新規エントリーを止める、「日次ドローダウン制限」
(参考にしたgrid trading戦略の記事にあったリスク管理手法)を再現する
オプション。

**⚠️ 過去データでの結果は将来の成績を一切保証しない。** 複数銘柄・複数
期間・複数のパラメータで試し、一貫して悪くない結果が出るかを確認して
から、初めて本物の資金投入(Hyperliquid等への実発注、現状未実装)を
検討すること。ファンディングレートの過去履歴は考慮していない(無料で
まとめて取得できる手段が無いため)ぶん、実運用よりやや保守的な検証になる。

**実際の検証結果(2026-07、BTCUSDT):** 直近2ヶ月(1h足)ではBTC/ETH/SOL
全銘柄でBuy & Holdを大きく上回ったが、直近8〜9ヶ月(4h足、123取引)まで
期間を伸ばすと勝率49.6%・合計損益+4.5%(ほぼ無風)・**最大ドローダウン
-63.5%**という結果になった。つまり2ヶ月の好成績はその期間固有の相場環境
に助けられていただけで、長期では実資金投入に値しないシグナルだった。
**短期間の検証だけで判断しないこと。**

### グリッドトレードのバックテスト(`perp_grid_backtest.py`)

`perp_backtest.py`(トレンド追従、方向を予測する)とは逆のアプローチ。
一定の価格レンジを固定グリッドで区切り、下がったら買い・少し上がったら
利確売り、を機械的に繰り返す(相場が一方向に大きく動かず、レンジ内で
上下する前提の戦略)。ユーザーが見つけたedgeXのgrid trading解説記事を
参考にしている(その記事自身も「6ヶ月の理論上のバックテストでトントン」
という結果を報告しており、グリッドだから安全というわけではない)。

```
.venv/bin/python perp_grid_backtest.py --symbol BTCUSDT
.venv/bin/python perp_grid_backtest.py --symbol BTCUSDT --interval 4h --limit 1500 --range-pct 15 --grid-count 50
.venv/bin/python perp_grid_backtest.py --symbol BTCUSDT --daily-loss-limit -20
.venv/bin/python perp_grid_backtest.py --symbol BTCUSDT --fee-pct-per-side 0.02
```

価格レンジは検証開始時点の価格を中心に固定する。**価格がレンジを大きく
超えて一方向に走ると、買いグリッドの含み損が積み上がるだけで一切利確
できなくなるリスクがある**(いわゆる「握り込み」)。1本のローソク足の
中で利確・損切りの両方に価格が触れた場合、tickデータが無いためどちらが
先だったかは分からず、このツールは利確を優先して判定する(やや楽観的な
見積もりになり得る点に注意)。

**`--fee-pct-per-side`は既定0(手数料なし)。** グリッドトレードは1回
あたりの利益が小さい分、取引回数が数百〜数千件になりやすく、手数料の
有無で結果が全く変わる。**必ず取引所の実際のMaker/Taker手数料率を
指定した結果も確認すること。** 手数料なしの結果だけを見て判断しないこと
(実際の検証(2026-07、BTCUSDT・手数料未考慮)では2ヶ月で取引数698件・
勝率49.1%・合計+99.3%、8〜9ヶ月では取引数2277件・勝率64.1%・合計
+630.9%とBuy & Holdを大きく上回ったが、これは手数料ゼロという非現実的な
前提での数字であり、実際の手数料を加味した結果はまだ確認できていない)。

### グリッドTP/SL幅・分割数のスイープ比較(`perp_grid_backtest_sweep.py`)

実運用の既定値(TP+0.2%/SL-0.1%)は、記事のグリッド幅(20%レンジ÷300分割
≒0.067%)に対してSLがかなり狭く、15分足の通常のノイズ幅の中に収まって
しまい、「本当の反転」ではなく「ただのノイズ」でSLに引っかかっている
可能性が高いという仮説がある(Maker手数料0.015%/側・レバレッジ3倍での
損益分岐勝率は約43.3%だが、実運用の勝率はそれより低い日が多かった)。
`perp_grid_backtest.py`は1回の実行につきTP/SL/グリッド分割数の組み合わせ
を1つしか検証できないため、同じ価格データに対して複数の組み合わせを
一括比較できるスイープツールを追加した。

```
.venv/bin/python perp_grid_backtest_sweep.py --symbol BTCUSDT
.venv/bin/python perp_grid_backtest_sweep.py --symbol BTCUSDT --interval 15m --limit 1500 \
    --grid-counts 50,100,300 --take-profits 0.2,0.3,0.4 --stop-losses -0.1,-0.15,-0.2,-0.3 \
    --fee-pct-per-side 0.015
```

各行に「損益分岐勝率」(その手数料・レバレッジ設定でトントンになるために
必要な勝率、手数料込み・ファンディング抜きの解析値)と、実際の勝率との差を
表示する。差がマイナスの組み合わせは過去データ上は期待値マイナスだった
ことを意味する。**分析専用ツールで、既存のライブ/ペーパートレードの設定
には一切影響しない**(結果を見て実際にパラメータを変える場合は、複数銘柄・
複数期間で確認したうえで手動で`.env`を編集すること)。

⚠️ このリポジトリが動いているsandbox環境からはBinance Futures API
(`fapi.binance.com`)への接続がネットワークポリシーでブロックされており、
このツール・`perp_grid_backtest.py`のいずれも実行できない(2026-07に
確認、`connect_rejected`/`403`)。**VPS等、実際にBinanceへ到達できる環境
で実行すること。**

## 分析ツール(`analyze_outcomes.py`、実験的機能)

過去の通知実績(Supabase設定時: 通知時のスコア項目内訳 × その後の結果、
未設定時: `logs/outcomes.jsonl`のtier/scoreのみ)から、どのスコア項目が
実際の値上がりと相関しているかを集計するレポートツール。**scoring.pyの
重みを自動で書き換えることはしない**(サンプル数が少ないうちは偶然の
ブレを拾ってしまい、下手に重みを変えるとかえって質を落としかねないため。
出てきたレポートを見て、重みを変えるかどうかは人間が判断すること)。

```
.venv/bin/python analyze_outcomes.py
.venv/bin/python analyze_outcomes.py --checkpoint 3600   # 1時間後の結果だけで分析
```

通知件数がまだ少ない(目安で数十件未満)うちは「サンプル数が足りない」旨が
表示され、相関分析はスキップされる(それ自体は正常な動作)。

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
