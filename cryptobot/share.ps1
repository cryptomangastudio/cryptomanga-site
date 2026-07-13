# CryptoBot スマホ共有(Windows用)
# 使い方: botを起動した状態で、別のPowerShellに次の1行を貼る
#   irm https://raw.githubusercontent.com/cryptomangastudio/cryptomanga-site/claude/crypto-bot-foundation-ioefuq/cryptobot/share.ps1 | iex
# Cloudflare Tunnel(無料・アカウント不要)でダッシュボードのスマホ用URLを発行する。
# URLにはアクセスキーが付いており、URLを知らない第三者は開けない。
# このウィンドウを閉じるとスマホからは見えなくなる(botは止まらない)。

$ErrorActionPreference = "Stop"
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $botDir = Get-ChildItem -Path (Join-Path $HOME "cryptobot-app") -Directory -Recurse -Filter cryptobot -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $botDir) { throw "cryptobotが見つかりません。先にいつもの1行(setup.ps1)でbotをインストール・起動してください" }
    Set-Location $botDir.FullName

    $keyFile = "data\dashboard_key.txt"
    if (-not (Test-Path $keyFile)) {
        throw "アクセスキーがまだありません。先にbot(ダッシュボード)を起動してから、もう一度この1行を実行してください"
    }
    $key = (Get-Content $keyFile -Raw).Trim()

    $exe = ".\cloudflared.exe"
    if (-not (Test-Path $exe)) {
        Write-Host "[1/2] 共有用プログラム(Cloudflare Tunnel・無料)をダウンロード中..."
        Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $exe -UseBasicParsing
    }

    Write-Host "[2/2] スマホ用URLを発行中...(数秒待ってください)"
    Write-Host ""
    & $exe tunnel --url http://127.0.0.1:8765 --no-autoupdate 2>&1 | ForEach-Object {
        if ("$_" -match "https://[a-z0-9-]+\.trycloudflare\.com") {
            $url = $Matches[0] + "/?key=" + $key
            Write-Host ""
            Write-Host "================================================================" -ForegroundColor Green
            Write-Host " スマホで開くURL(LINE等で自分宛てに送ってタップ):" -ForegroundColor Green
            Write-Host ""
            Write-Host "   $url" -ForegroundColor Cyan
            Write-Host ""
            Write-Host " ・botのウィンドウと、このウィンドウを開けている間だけ有効"
            Write-Host " ・URLは毎回変わります(次回はまたこの1行を実行)"
            Write-Host "================================================================" -ForegroundColor Green
            Write-Host ""
        }
    }
}
catch {
    Write-Host ""
    Write-Host "エラー: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "この画面のスクリーンショットをClaudeに送ってください。"
}
Read-Host "Enterキーで閉じます(閉じるとスマホから見えなくなります)"
