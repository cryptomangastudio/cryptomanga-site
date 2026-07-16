# プロンプト/設定ライブラリ：わりぴ

> 再現性のための記録。作画時にモデル/seed/確定プロンプトを必ず埋める。
> まず参照ベース（Midjourney omni-ref / Ideogram Character / Nano Banana）で master → キャラシートを作り、当たったら LoRA 化（`../../production/tools.md`）。
> ⚠️ 実在スーパーのシール意匠・ロゴを入れない。「〇〇風」で既存作家を指名しない。生成物は人の手で加工し、LINE申請時は[AI]ラベル運用。

## デザインの核（絶対に外さない）
- 生成り色の**まるいおにぎり型**の生きもの、2頭身弱、下がやや平ら
- 背中に**オレンジの割引タグ**が1枚（正面からも端がのぞく）。剥がせない設定
- 点目、代表顔は**半目のうすい笑み（まあいっか顔）**、ほほにうすいピンク一点
- フラット3色＋線（クリーム／オレンジ／ピンク）。影は最小
- かわいい・ゆるい・素朴。写実にしない

## master 生成プロンプト（英語ベース・コピペ用）
```
a simple flat kawaii mascot character, a small round rice-ball-shaped blob creature,
cream/off-white body, chubby 2-head-tall, tiny stubby arms and feet,
a small orange discount price tag stuck on its back (peeking from the side), can't be removed,
sleepy half-closed eyes, faint gentle smile, small pink blush,
minimal flat colors (cream, orange, pink), clean thick outline, soft, plain white background,
sticker/plush toy friendly design, original character
--no text, logos, brand names, realistic, gradient-heavy shading
```
※Midjourneyは末尾に `--niji 6 --style cute`（版は要確認）等を試す。Ideogram/Nano Bananaはそのまま。

## キャラシート（一貫性ロック）
masterを参照画像に固定し、以下を生成 → `assets/references/` に保存:
- ターンアラウンド（前・横・後ろ。※後ろ＝タグが主役）
- 表情差分：無 / ぽわっ（シールを貼られ光る）/ しょんぼり / まあいっか
- 基本ポーズ：座る、カゴの中、台紙を握る、手をふる

## 収集バリエーション（色替え）
タグの色と文字だけ替える：3割引（黄）/ 2割引 / タイムセール（赤）/ 本日限り / ポイント5倍。
シークレット：**タグなし＝定価のわりぴ**（レア）。

## 記録欄（生成したら埋める）
| 項目 | 値 |
|---|---|
| ツール/モデル | |
| 一貫性方式 | 参照ベース / LoRA(名・版) |
| 基準画 | `assets/references/master.png` |
| seed | |
| パラメータ | |

## NG
実在人物名・既存キャラ名・特定絵師名・実在店舗ロゴ・R18：すべて禁止。
