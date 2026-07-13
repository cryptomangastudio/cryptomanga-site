# CryptoBot — 現物のみ・少額運用ボット(土台)

元手10万円・**現物(スポット)取引のみ**を前提にした自動売買botの土台です。
「まず安全に、小さく、記録を残しながら」を最優先に設計しています。

## 🔰 いちばんかんたんな始め方(画面つき・ゼロ円)

コマンド操作なしで、ブラウザの管理画面つきで始められます。

1. **Pythonを入れる(最初の1回だけ)**: https://www.python.org/downloads/ から
   ダウンロードしてインストール。Windowsはインストール画面で
   **「Add Python to PATH」に必ずチェック**を入れる
2. **このリポジトリをZIPでダウンロード**: GitHubのリポジトリページ →
   緑の「Code」ボタン →「Download ZIP」→ 展開して `cryptobot` フォルダを開く
3. **起動**:
   - Windows → `start.bat` をダブルクリック
   - Mac → `start.command` をダブルクリック(「開発元が未確認」と出たら右クリック→開く)
4. 黒い画面(ターミナル)が開き、続けてブラウザに管理画面が自動で開きます
   (開かなければ http://localhost:8765 をブラウザに入力)
5. **やめるときは黒い画面を閉じるだけ**。次に起動すると残高・履歴は自動で復元されます

管理画面では仮想の資産評価額・残高・累計損益・取引履歴が見られ、
「今すぐ1回判断する」ボタンで手動実行もできます。
デフォルトはペーパートレード(仮想のお金)なので、**1円も動きません**。

### さらにかんたん: 1行貼るだけの全自動セットアップ(Windows)

ZIPのダウンロードや展開も自動でやりたい場合は、PowerShell
(Windowsキー →「powershell」と入力 → Enter)に次の1行を貼ってEnterするだけです。

```powershell
irm https://raw.githubusercontent.com/cryptomangastudio/cryptomanga-site/claude/crypto-bot-foundation-ioefuq/cryptobot/setup.ps1 | iex
```

ダウンロード → 展開 → 環境構築 → 管理画面の起動まで全部やります(`setup.ps1`)。
2回目以降も同じ1行で起動でき、取引記録は消えません。

## 設計原則(このbotが絶対に守ること)

1. **現物のみ**: レバレッジ・信用・先物・スワップに関わる注文はコードレベルで拒否します(`bot/exchange.py`)。
2. **デフォルトはペーパートレード**: 設定 `mode: paper` が初期値。実弾(`mode: live`)にするには、設定変更に加えて環境変数 `CRYPTOBOT_LIVE=YES` が必要です(二重ロック)。
3. **リスク上限が先、戦略は後**: どんな戦略のシグナルも `RiskManager` の承認なしには発注されません。
   - 1回の注文上限 / 保有上限 / 総予算上限
   - 1日の損失上限(超えたらその日は買い停止)
   - 最大ドローダウン超過でbot全停止。停止は `data/HALTED` ファイルに永続化され、**人間がファイルを削除するまで再起動しても解除されません**
   - 買いの連続発注クールダウン(売り=リスク削減は制限しない)
4. **全取引を記録**: すべての約定はCSVに記帳され、取得単価(移動平均法)と実現損益を自動計算します。確定申告の基礎資料になります。再起動時はCSVから建玉・累計損益を復元します。

## ⚠️ 税金について(重要な誤解の訂正)

- 「買ったまま保有(含み益)」→ 課税されません。
- **「利益を出して売却」→ 日本円に出金していなくても、その時点で課税対象です。**
  仮想通貨同士の交換(BTC→ETH)も売却扱いです。
- 自動売買botは売却を繰り返すため、**利益が出れば必ず課税対象の所得(原則、雑所得)が発生します。**
  「塩漬けで非課税」が成立するのは、買ったまま売らない場合だけです。
- 損失は他の所得と損益通算できず、翌年繰越もできません。
- 詳細は Google Drive の「02_税金メモ」を参照。最終判断は税理士・税務署へ。

## セットアップ

```bash
cd cryptobot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml   # config.yaml はgit管理外
```

## 使い方

```bash
# 実弾前の適合性チェック(最低注文数量と設定の整合を確認。発注しない)
python main.py --config config.yaml --check

# 1回だけ判断・実行して終了(動作確認用)
python main.py --config config.yaml --once

# 常駐実行(interval_seconds ごとに判断)
python main.py --config config.yaml

# バックテスト(OHLCVのCSVを用意して)
python backtest.py --config config.yaml --data data/BTC_JPY_1h.csv

# 月次レポート生成(reports/report_YYYY.md → Driveの運用フォルダへ保管)
python report.py --config config.yaml
```

ペーパートレードの残高・取引履歴は `data/` 以下に保存されます。

### ⚠️ 最低注文数量に注意(少額運用の落とし穴)

取引所には最低注文数量があります(例: bitFlyerのBTC/JPYは0.001 BTC ≒ 1万円超のことが多い)。
**積立額3,000円のような少額注文は取引所によっては通りません。**
`--check` で自分の設定と取引所仕様の整合を必ず確認してください。最低数量の小さい
取引所(例: bitbank)を選ぶか、積立額を調整する必要があります。

### 対応取引所

ccxtに実装のある金融庁登録業者: `bitflyer` / `coincheck` / `bitbank` / `zaif`。
GMOコインはccxt未対応のため、使う場合は専用アダプタの追加実装が必要です。

### 📱 スマホで「実際の画面」を見る(share.ps1・無料)

botを起動した状態で、**もう1つ**PowerShellを開いて次の1行を貼るだけです。

```powershell
irm https://raw.githubusercontent.com/cryptomangastudio/cryptomanga-site/claude/crypto-bot-foundation-ioefuq/cryptobot/share.ps1 | iex
```

Cloudflare Tunnel(無料・アカウント不要)でスマホ用URLが表示されるので、
LINE等で自分に送ってタップすれば、**動いているダッシュボードそのもの**が見られます。

- URLには自動生成のアクセスキーが付いており、URLを知らない第三者は開けません
- botのウィンドウ+共有ウィンドウを開けている間だけ有効。URLは毎回変わります
- PCがスリープすると見えなくなります(電源設定に注意)

### 📱 スマホで確認する(Discord通知・無料・5分で設定)

外出中でもスマホで状況が分かるように、約定・全停止・**1時間ごとの資産レポート**を
Discordに送れます。ダッシュボード(localhost)はセキュリティのため自分のPCから
しか見られない設計なので、外から見る手段はこの通知が正解です。

1. スマホに **Discord** アプリを入れて無料アカウントを作る
2. 「サーバーを追加 → オリジナルの作成」で自分だけのサーバーを作る
3. チャンネルの ⚙(設定)→「連携サービス」→「ウェブフック」→「新しいウェブフック」
   →「ウェブフックURLをコピー」
4. PCの `cryptobot` フォルダにメモ帳で **`notify_url.txt`** というファイルを作り、
   コピーしたURLを貼り付けて保存(このファイルはgitに入りません)
5. `config.yaml` の `notify.format:` を `discord` に変更
6. botを再起動(黒い画面を閉じて、いつもの1行を貼り直す)

以後、スマホのDiscordに「🚀起動」「🟢買付」「📊定期レポート(資産・損益・保有)」
「🛑全停止」が届きます。環境変数 `CRYPTOBOT_WEBHOOK_URL` でも設定できます。

### 社内ネットワーク等のプロキシ環境

ccxtは環境変数のプロキシ/CA設定を無視するため、bot側で `HTTPS_PROXY` と
`REQUESTS_CA_BUNDLE`(または `CRYPTOBOT_CA_BUNDLE`)を明示的に反映します。
TLS検証を無効化する設定は存在しません。

## 実弾運用に進む条件(推奨)

1. ペーパートレードを最低1ヶ月回し、取引記録を確認した
2. バックテストで手数料込みでもプラスを確認した
3. それでも最初は1〜3万円から

実弾化の手順:
```bash
export CRYPTOBOT_API_KEY="取引所のAPIキー(現物取引権限のみ・出金権限はOFF)"
export CRYPTOBOT_API_SECRET="APIシークレット"
export CRYPTOBOT_LIVE=YES
# config.yaml の mode を live に変更してから起動
```

**APIキーには出金権限を絶対に付けないこと。**

## 構成

```
cryptobot/
├── start.bat            # Windows用かんたん起動(ダブルクリック)
├── start.command        # Mac用かんたん起動(ダブルクリック)
├── dashboard.py         # ブラウザ管理画面(ペーパートレード専用)
├── main.py              # エントリポイント(--once / --check)
├── backtest.py          # バックテスター
├── report.py            # 月次レポート生成(Drive保管用)
├── config.example.yaml  # 設定サンプル(コピーして config.yaml に)
├── bot/
│   ├── config.py        # 設定の読み込みと検証
│   ├── exchange.py      # 取引所ラッパー(現物のみ強制・live二重ロック)
│   ├── risk.py          # リスク管理(全注文の関所・停止の永続化)
│   ├── strategy.py      # 戦略(DCA / MAクロス)
│   ├── paper.py         # ペーパートレード用ブローカー
│   ├── journal.py       # 取引記帳(移動平均法・実現損益・再起動復元)
│   ├── notify.py        # Webhook通知(Discord/Slack互換)
│   └── runner.py        # メインループ
└── tests/               # ユニットテスト+結合テスト
```

## リサーチ由来の8つの防御・検証機能

`docs/research/2026-07_勝てる機能リサーチ.md` の結論(必勝は存在しない。負けの主因=コストと
運用事故を構造的に潰すことだけが確実なエッジ)に基づき、以下を実装済み:

1. **メイカー執行**(`execution:`)— bitbank等ではPost-Only指値で手数料を「受け取る」側に。
   liveでは再指値ループ、未約定なら見送り(テイカーに逃げない)
2. **多層サーキットブレーカー**(`risk:`)— 日次/週次損失・連敗数・最大DDの4層+価格異常値防御
3. **発注前コストゲート+頻度ガバナー**(`cost_gate:` `governor:`)— 期待値動きが往復コスト×k未満の
   注文と月間上限超の買いを機械的に拒否
4. **実効コスト台帳** — 全約定のスリッページ・手数料・待ち時間を `data/execution_*.csv` に自動記録
5. **ATRサイジング+クォーターケリー**(`sizing:`)— 1トレードの損失を資金の1%に固定。
   売却実績からのケリー推定が負なら新規買いを自動停止
6. **過学習検出ゲート** — `python backtest.py --walk-forward 5 --trials <試行総数>` で
   ウォークフォワード+Deflated Sharpe を検定。**FAILの戦略は実弾に投入しない**
7. **税引後レポート** — `report.py` が法定デフォルトの総平均法(概算)と移動平均法を併記し、
   税引後の概算も表示
8. **200日MAレジームフィルター+DCA傾斜**(`regime:`)— 下落相場でma_crossの新規買いを停止、
   DCAは「安い時に多く買う」を自動化(日足対応の取引所でのみ有効)

## 初期戦略

- **DCA(定期積立)**: 一定間隔で固定額を買うだけ。基準(ベンチマーク)戦略。
- **MAクロス**: 短期SMAが長期SMAを上抜けで買い、下抜けで全売却。必ずバックテストしてから。

## 複数銘柄運用

`config.yaml` の `symbols` に複数書くと、予算・リスク上限を銘柄数で等分して
銘柄ごとに独立運用します(帳簿・仮想残高・停止判定も銘柄別)。
デフォルトは BTC/JPY・ETH/JPY・XRP/JPY(ETH/XRPはBTCよりボラティリティ高め)。
1銘柄あたりの予算が小さくなるほど取引所の最低注文数量に引っかかりやすくなるので、
実弾前は必ず `--check` で全銘柄の整合を確認してください。

## ロードマップ(土台の次)

- [x] 適合性チェックコマンド(`--check`)
- [x] 通知(約定・停止イベントをDiscord/Slack Webhookへ)
- [x] 月次レポート生成(`report.py` → Driveへ月次アップ)
- [x] 停止状態・帳簿の再起動復元
- [ ] ユーザー環境での取引所本番接続テスト(この開発環境からは取引所APIへの接続が許可されていないため)
- [ ] GMOコイン用アダプタ(ccxt未対応のため必要なら)
- [ ] 戦略の追加(RSI逆張り、グリッドなど)と比較検証
