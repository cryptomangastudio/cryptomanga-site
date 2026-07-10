# CryptoBot — 現物のみ・少額運用ボット(土台)

元手10万円・**現物(スポット)取引のみ**を前提にした自動売買botの土台です。
「まず安全に、小さく、記録を残しながら」を最優先に設計しています。

## 設計原則(このbotが絶対に守ること)

1. **現物のみ**: レバレッジ・信用・先物・スワップに関わる注文はコードレベルで拒否します(`bot/exchange.py`)。
2. **デフォルトはペーパートレード**: 設定 `mode: paper` が初期値。実弾(`mode: live`)にするには、設定変更に加えて環境変数 `CRYPTOBOT_LIVE=YES` が必要です(二重ロック)。
3. **リスク上限が先、戦略は後**: どんな戦略のシグナルも `RiskManager` の承認なしには発注されません。
   - 1回の注文上限 / 保有上限 / 総予算上限
   - 1日の損失上限(超えたらその日は停止)
   - 最大ドローダウン(超えたらbot全停止)
   - 連続発注のクールダウン
4. **全取引を記録**: すべての約定はCSVに記帳され、取得単価(移動平均法)と実現損益を自動計算します。確定申告の基礎資料になります。

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
# 1回だけ判断・実行して終了(動作確認用)
python main.py --config config.yaml --once

# 常駐実行(interval_seconds ごとに判断)
python main.py --config config.yaml

# バックテスト(OHLCVのCSVを用意して)
python backtest.py --config config.yaml --data data/BTC_JPY_1h.csv
```

ペーパートレードの残高・取引履歴は `data/` 以下に保存されます。

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
├── main.py              # エントリポイント
├── backtest.py          # バックテスター
├── config.example.yaml  # 設定サンプル(コピーして config.yaml に)
├── bot/
│   ├── config.py        # 設定の読み込みと検証
│   ├── exchange.py      # 取引所ラッパー(現物のみ強制・live二重ロック)
│   ├── risk.py          # リスク管理(全注文の関所)
│   ├── strategy.py      # 戦略(DCA / MAクロス)
│   ├── paper.py         # ペーパートレード用ブローカー
│   ├── journal.py       # 取引記帳(移動平均法・実現損益)
│   └── runner.py        # メインループ
└── tests/               # ユニットテスト
```

## 初期戦略

- **DCA(定期積立)**: 一定間隔で固定額を買うだけ。基準(ベンチマーク)戦略。
- **MAクロス**: 短期SMAが長期SMAを上抜けで買い、下抜けで全売却。必ずバックテストしてから。

## ロードマップ(土台の次)

- [ ] 取引所の本番接続テスト(bitFlyer / GMOコイン)
- [ ] バックテスト結果レポートの自動生成(Driveへ月次アップ)
- [ ] 通知(約定・停止イベントをメール/LINE通知)
- [ ] 戦略の追加(RSI逆張り、グリッドなど)と比較検証
