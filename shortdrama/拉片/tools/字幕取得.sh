#!/usr/bin/env bash
# 公式クリップの自動生成字幕(セリフ+タイムスタンプ)だけを取得する。動画本体は落とさない。
# 使い方(ローカルPC/Macで):
#   1) yt-dlp を入れる: pip install yt-dlp  (または brew install yt-dlp)
#   2) このフォルダで: bash 字幕取得.sh
#   3) 素材/ フォルダに .vtt が溜まるので、まとめてcommit & push → Claudeが解析
set -u
cd "$(dirname "$0")"
mkdir -p ../素材
grep -v '^\s*#' 対象クリップURL.txt | grep -v '^\s*$' | while read -r url; do
  echo "=== $url"
  yt-dlp \
    --skip-download \
    --write-auto-sub --write-sub \
    --sub-lang "ja,ja-orig" --sub-format vtt \
    --restrict-filenames \
    -o "../素材/%(title).80s [%(id)s].%(ext)s" \
    "$url" || echo "!! 取得失敗: $url (字幕なし or 接続エラー)"
done
echo "完了。../素材/ の .vtt をコミットしてください。"
