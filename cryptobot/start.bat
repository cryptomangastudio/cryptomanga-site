@echo off
rem CryptoBot かんたん起動(Windows用)。ダブルクリックするだけでOK。
cd /d "%~dp0"
chcp 65001 >nul

where python >nul 2>nul
if errorlevel 1 (
  echo Pythonが見つかりません。
  echo https://www.python.org/downloads/ からインストールしてください。
  echo ※インストール画面で「Add Python to PATH」に必ずチェックを入れること
  pause
  exit /b 1
)

if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
if not exist config.yaml copy config.example.yaml config.yaml >nul

echo ブラウザが自動で開きます。この黒い画面は閉じないでください(閉じるとbotが止まります)。
python dashboard.py
pause
