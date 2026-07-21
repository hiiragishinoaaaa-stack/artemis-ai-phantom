#!/usr/bin/env bash
# phantom-chat-reply サービスを再起動して状態を確認するだけのスクリプト。
#
# モバイルのブラウザ端末で長いコマンドを毎回貼り付けると途中で文字が
# 欠けたり壊れたりしやすいため、一度だけこのスクリプトを実行してもらい、
# 以降は短い `botrestart` コマンド1つで済むようにする。
#
# 使い方(VPSのターミナルで1行だけ実行):
#   curl -fsSL https://raw.githubusercontent.com/hiiragishinoaaaa-stack/artemis-ai-phantom/main/scripts/restart_chat_reply.sh | sudo bash
set -euo pipefail

echo "[1/3] phantom-chat-reply を再起動します..."
systemctl restart phantom-chat-reply

sleep 2

echo "[2/3] 状態を確認します..."
systemctl is-active phantom-chat-reply || true

echo "[3/3] 直近のログ(20行)を表示します..."
journalctl -u phantom-chat-reply -n 20 --no-pager || true

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
BASHRC="$REAL_HOME/.bashrc"
ALIAS_LINE="alias botrestart='sudo systemctl restart phantom-chat-reply && sleep 2 && systemctl is-active phantom-chat-reply'"
if [ -f "$BASHRC" ] && ! grep -qxF "$ALIAS_LINE" "$BASHRC"; then
    echo "$ALIAS_LINE" >> "$BASHRC"
    echo "次回からは botrestart とだけ打てば再起動できます(ターミナルを開き直すと使えます)。"
fi

echo "完了しました。"
