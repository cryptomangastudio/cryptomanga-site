# CryptoManga Studios — Character IP Studio (working title)

AIネイティブの「かわいい系キャラクターIPスタジオ」の運営リポジトリ。
Webサイトを作る事業ではなく、**オリジナルキャラIPを量産検証し、当たりをグッズ/ライセンス経済（2.85兆円市場）に載せる**事業の土台。

> ⚠️ このフォルダは「スタジオの運営システム」です。サイトのソースコードではありません。
> 収益 = ライセンス/グッズ/コラボ。集客 = SNS(X/TikTok/LINE)。自社サイトは「ヒット後」の資産。

## いま何をしているか（現在地）
- [x] Xtoon分析・競合調査・海外事例調査（→ `docs/strategy/01-market-research.md`）
- [x] 戦略の確定：AIネイティブのキャラIP、全年齢SFW、AIは開示するが売りにしない
- [ ] **1体目キャラの企画ドラフト**（← 次のアクション）
- [ ] 90日SNS検証（成功/失敗ラインは `docs/playbooks/launch-90day.md`）

## ナビゲーション
| 場所 | 中身 |
|---|---|
| `docs/strategy/` | 事業の核（なぜこれをやるか）。thesis / 市場調査 / 意思決定ログ |
| `docs/playbooks/` | 実務ルール。AI開示方針・炎上回避・法務・90日検証 |
| `production/` | 制作パイプライン（どのツールでどう作るか）・スタイルガイド |
| `characters/` | キャラごとの資産。`_TEMPLATE/` を複製して新キャラを起こす |
| `validation/` | 実験のKPIトラッカー |
| `brand/` | スタジオ全体のブランド資産 |

## 新しいキャラを起こす手順
1. `cp -r characters/_TEMPLATE characters/<character-name>`
2. `character-bible.md` を人間（脚本）で埋める ← ここが魂。AIにやらせない
3. `production/pipeline.md` に従って作画・資産化
4. `validation/tracker.md` に実験を登録
