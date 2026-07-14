# ARTEMIS Phantom Sniper

Solana上のミームコイン発射台「[pump.fun](https://pump.fun)」で新しく作られた
トークンを、[PumpPortal](https://pumpportal.fun/)の無料WebSocket API経由で
リアルタイムに検知し、作成後20/40/60/90/120秒の各時点で初動(買い件数・
ユニークな買い手・売買比率・出来高・時価総額)を100点満点でスコアリングし、
スコアが通知ラインを超えたものだけDiscordへ通知するbot。

## できること・できないこと

- ✅ pump.fun上の新規トークン作成をブロック確定後ほぼリアルタイムで検知
- ✅ 作成後20/40/60/90/120秒の5チェックポイントで繰り返しスコアを再計算し、
  通知ライン(WATCH以上)を初めて超えた瞬間、またはより高いティア
  (LOW→WATCH→HIGH)へ上昇した瞬間だけDiscordへ通知。120秒までは通知後も
  観察を継続する
- ✅ 通知にはpump.fun/DexScreenerへのリンク付き
- ✅ 通知したトークンは30分/1時間/24時間後の時価総額変化を`logs/outcomes.jsonl`
  へ記録(将来、どのスコア項目が実際に有効だったか分析するため)
- ❌ **自動売買・ウォレット操作は一切行わない。** あくまで人間が判断する
  ための情報提供ツール。実際に買うかどうかはDiscord通知を見た人が
  Phantom等のウォレットで手動判断・手動操作する。
- ❌ 詐欺・ラグプル(即座に流動性を抜かれる)の検出は保証しない。フィルター
  は「作成直後に誰も買ってない・即座に投げ売りされている」ような
  明らかに無価値なものを減らすだけで、巧妙な詐欺は通過し得る。**通知が
  来ても必ず自分で内容を確認すること。**

## なぜDexScreenerではなくPumpPortalなのか

DexScreenerの公開APIにも「最新のトークンプロフィール」を取得する
エンドポイントはあるが、これは各プロジェクトが自分で登録した"プロフィール"
を拾うだけで、pump.fun上で匿名に大量発生するミームコインの大半はそもそも
登録されないため、真の意味での「新規ペアの全量」を拾うのには向いていない。
PumpPortalはpump.fun自体のオンチェーンイベント(トークン作成・売買)を
直接ストリーミングしているため、より早く・より網羅的に新規トークンを
検知できる。

## 仕組み

1. `pumpportal_client.py` がPumpPortalのWebSocket(`wss://pumpportal.fun/api/data`)
   へ接続し、`subscribeNewToken`で新規トークン作成イベントを受信する。
2. 新規トークンを検知したら `token_watcher.py` が観察を開始し、同時に
   そのトークンの売買イベント(`subscribeTokenTrade`)を追加購読する。
3. 作成後20/40/60/90/120秒(`config.EVALUATION_CHECKPOINTS_SECONDS`)の各
   チェックポイントで、`scoring.py`がその時点の状態から100点満点のスコアを
   計算する(`scoring.compute_score()`)。全項目が独立した関数として実装
   されており、将来RugCheck/DexScreener/Birdeye/Solscan/AIスコアリング等を
   追加する場合は`scoring._SCORERS`に関数を1つ足すだけでよい。

   | 項目 | 加点条件 |
   | --- | --- |
   | Buy件数 | 3件+10 / 5件+20 / 10件以上+30 |
   | ユニークBuyウォレット | 2人+10 / 5人+20 / 10人以上+30(同一ウォレットの連続買いは1人扱い) |
   | Buy/Sell比率 | Buy>Sell+10 / 2倍以上+20 / 3倍以上+30 |
   | Volume | `MIN_VOLUME_SOL_FOR_SCORE`以上で+10 |
   | Market Cap | `MIN_MARKET_CAP_SOL_FOR_SCORE`以上で+10 |

4. スコアから通知ティアを判定する(`scoring.tier_for_score()`)。
   - **HIGH**(`HIGH_SCORE_THRESHOLD`以上、既定85): 🚨 Discord通知
   - **WATCH**(`WATCH_SCORE_THRESHOLD`以上、既定75): ⚠ Discord通知
   - **LOW**(`LOW_SCORE_THRESHOLD`以上、既定65): ログ保存のみ(Discordへは送らない)
   - それ未満: 何もしない(デバッグログにのみ未加点理由を残す)

   ティアが初めてWATCH以上になった瞬間、またはより高いティアへ上昇した
   瞬間(LOW→WATCH、WATCH→HIGH)だけ通知する(`scoring.is_upgrade()`)。
   120秒の最終チェックポイントまでは通知後も観察を継続する。
5. 通知したトークンは `outcome_tracker.py` が引き続き売買イベントを受信し
   続け、通知時点からの30分/1時間/24時間後の時価総額変化率を
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
| `MAX_TRACKED_TOKENS` | `500` | 同時観察数の上限(超過分は最古から間引く) |
| `MIN_VOLUME_SOL_FOR_SCORE` | `5.0` | これ以上のVolume(SOL)で加点 |
| `MIN_MARKET_CAP_SOL_FOR_SCORE` | `15.0` | これ以上のMarket Cap(SOL)で加点 |
| `HIGH_SCORE_THRESHOLD` | `85` | これ以上のスコアでHIGH通知 |
| `WATCH_SCORE_THRESHOLD` | `75` | これ以上のスコアでWATCH通知 |
| `LOW_SCORE_THRESHOLD` | `65` | これ以上のスコアでログ保存(Discordへは送らない) |
| `OUTCOMES_FILE_PATH` | `logs/outcomes.jsonl` | 通知後の結果トラッキングの出力先 |

**通知が多すぎる/少なすぎる場合は、`WATCH_SCORE_THRESHOLD`/
`HIGH_SCORE_THRESHOLD`を`.env`で調整してから再起動してください。**
「もっと厳しく絞りたい」→閾値を上げる。「もっと通知が欲しい」→閾値を
下げる、または`MIN_VOLUME_SOL_FOR_SCORE`/`MIN_MARKET_CAP_SOL_FOR_SCORE`を
下げる。チェックポイント秒数(20/40/60/90/120)自体は`config.py`の
`EVALUATION_CHECKPOINTS_SECONDS`を直接編集する。

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

ネットワーク不要な部分(`token_watcher.py`のフィルターロジック、
`discord_notifier.py`の通知メッセージ組み立て)は全てモック・フェイクで
テストしている。`pumpportal_client.py`の実際の接続・再接続ループは実サーバー
が必要なため単体テスト対象外(VPSで実際に動かして`journalctl`で確認する)。

```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 免責事項

このツールは公開されているオンチェーン情報をもとにした**情報提供のみ**を
行う。投資助言ではない。ミームコインは非常に高いリスク(詐欺・ラグプル・
価値のほぼ全損)を伴う。通知が来たトークンであっても、実際に売買するか
どうかの判断・実行・その結果については自己責任で行うこと。
