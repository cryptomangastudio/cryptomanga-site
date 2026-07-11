#!/bin/bash
# CryptoBot かんたん起動(Mac用)。ダブルクリックで起動。
# 開けない場合はターミナルで: bash start.command
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python3が見つかりません。https://www.python.org/downloads/ からインストールしてください。"
  read -r -p "Enterキーで閉じます"
  exit 1
fi

[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -q -r requirements.txt
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo "ブラウザが自動で開きます。このウィンドウは閉じないでください(閉じるとbotが止まります)。"
python dashboard.py
