# CryptoBot ワンライナーセットアップ(Windows用)
# 使い方: PowerShellに次の1行を貼り付けてEnter
#   irm https://raw.githubusercontent.com/cryptomangastudio/cryptomanga-site/claude/crypto-bot-foundation-ioefuq/cryptobot/setup.ps1 | iex
# ダウンロード → 展開 → Python環境構築 → ダッシュボード起動までを全自動で行う。
# 何度実行してもよい(取引記録 data/ は上書きされない)。

$ErrorActionPreference = "Stop"
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    Write-Host ""
    Write-Host "=== CryptoBot セットアップを開始します(すべて無料・仮想売買モード) ===" -ForegroundColor Cyan

    $zipUrl  = "https://github.com/cryptomangastudio/cryptomanga-site/archive/refs/heads/claude/crypto-bot-foundation-ioefuq.zip"
    $zipPath = Join-Path $env:TEMP "cryptobot.zip"
    $appDir  = Join-Path $HOME "cryptobot-app"

    Write-Host "[1/5] プログラムをダウンロード中..."
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing

    Write-Host "[2/5] 展開中..."
    Expand-Archive -Path $zipPath -DestinationPath $appDir -Force

    $botDir = Get-ChildItem -Path $appDir -Directory -Recurse -Filter cryptobot | Select-Object -First 1
    if (-not $botDir) { throw "展開先に cryptobot フォルダが見つかりませんでした" }
    Set-Location $botDir.FullName
    Write-Host "[3/5] インストール先: $($botDir.FullName)"

    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $py) {
        throw "Pythonが見つかりません。https://www.python.org/downloads/ からインストールしてください(「Add Python to PATH」にチェック)"
    }

    Write-Host "[4/5] 部品をインストール中(初回は1〜3分かかります。そのままお待ちください)..."
    & $py.Source -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
    # 旧形式の設定(トップレベルの fee_rate)は新形式(execution.maker_fee_rate等)に
    # 置き換わったため退避して作り直す。行頭一致にしないと execution.maker_fee_rate /
    # taker_fee_rate(新形式の正規のキー)まで誤検知して、更新のたびに正常な設定
    # (notify.format等のユーザー設定)が消えてしまう
    if ((Test-Path "config.yaml") -and (Select-String -Path "config.yaml" -Pattern "^fee_rate:" -Quiet)) {
        Move-Item "config.yaml" "config_old.yaml" -Force
        Write-Host "旧形式の config.yaml を config_old.yaml に退避し、新しい設定で作り直しました" -ForegroundColor Yellow
    }
    if (-not (Test-Path "config.yaml")) { Copy-Item config.example.yaml config.yaml }

    Write-Host ""
    Write-Host "[5/5] 起動します。まもなくブラウザに管理画面が開きます。" -ForegroundColor Green
    Write-Host "      開かない場合はブラウザで http://localhost:8765 を開いてください。"
    Write-Host "      ★この窓は閉じないでください(閉じるとbotが止まります)★" -ForegroundColor Yellow
    Write-Host ""
    & ".venv\Scripts\python.exe" dashboard.py
}
catch {
    Write-Host ""
    Write-Host "エラーが発生しました:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "この画面のスクリーンショットを撮って、Claudeに送ってください。"
}
Read-Host "Enterキーを押すと閉じます"
