# ARTEMIS Phantom Sniper

Solana上のミームコイン発射台「[pump.fun](https://pump.fun)」で新しく作られた
トークンを、[PumpPortal](https://pumpportal.fun/)の無料WebSocket API経由で
リアルタイムに検知し、一定時間(既定45秒)だけ初動(買い件数・ユニークな
買い手・売買比率)を観察した上で、条件を満たしたものだけDiscordへ通知する
bot。

## できること・できないこと

- ✅ pump.fun上の新規トークン作成をブロック確定後ほぼリアルタイムで検知
- ✅ 作成直後の一定時間だけ売買を観察し、フィルター条件(買い件数・ユニーク
  買い手数・売り優勢でないか・時価総額)を満たしたものだけDiscordへ通知
- ✅ 通知にはpump.fun/DexScreenerへのリンク付き
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
3. `OBSERVATION_WINDOW_SECONDS`(既定45秒)経過したら観察を締め切り、
   以下の条件を**全て**満たした場合のみ通知対象とする(`token_watcher.
   TokenWatcher.evaluate()`参照)。
   - 買い件数が`MIN_BUY_COUNT`(既定5)以上
   - ユニークな買い手(同一アドレスの連続買いは1人とカウント)が
     `MIN_UNIQUE_BUYERS`(既定3)以上
   - 売り件数が「買い件数×`MAX_SELL_TO_BUY_RATIO`」(既定1.0)を超えていない
   - (`MIN_MARKET_CAP_SOL`を設定した場合のみ)時価総額がそれ以上
4. 条件を満たしたものだけ `discord_notifier.py` がDiscordへ通知する。
   満たさなかったものは静かに観察終了・忘れる(通知しない)。

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
| `OBSERVATION_WINDOW_SECONDS` | `45` | 通知判定までに様子を見る秒数 |
| `MIN_BUY_COUNT` | `5` | 最低限必要な買い件数 |
| `MIN_UNIQUE_BUYERS` | `3` | 最低限必要なユニークな買い手数 |
| `MAX_SELL_TO_BUY_RATIO` | `1.0` | これを超えて売られていたら通知しない |
| `MIN_MARKET_CAP_SOL` | `0`(無効) | 観察終了時点の時価総額の下限(SOL) |
| `MAX_TRACKED_TOKENS` | `500` | 同時観察数の上限(超過分は最古から間引く) |

**通知が多すぎる/少なすぎる場合は、これらの数値を`.env`で調整してから
再起動してください。** 「もっと早く欲しい」→`OBSERVATION_WINDOW_SECONDS`
を短く。「もっと厳しく絞りたい」→`MIN_BUY_COUNT`/`MIN_UNIQUE_BUYERS`を
上げる、`MAX_SELL_TO_BUY_RATIO`を下げる。

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
sudo sed -i "s|__PHANTOM_USER__|$(whoami)|; s|__PHANTOM_HOME__|/opt/artemis|" /etc/systemd/system/phantom-sniper.service
sudo systemctl daemon-reload
sudo systemctl enable --now phantom-sniper

# 5. ログ確認
journalctl -u phantom-sniper -f
```

MT5(mt5_ai_trader)とは完全に独立したプロセスなので、同じVPS上で並行して
動かして問題ない(ファイル・ポートの衝突なし)。

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
