---
name: romance-fantasy
description: ロマンスファンタジー(ロマファン/悪役令嬢もの/婚約破棄/契約結婚/転生令嬢/韓国発の「회빙환」系ジャンル)に特化した執筆スキル。ジャンルの定義・歴史・韓国発の成り立ち、王道トロープと構造、男女主人公のアーキタイプ、貴族社会・学園などの世界観の型、韓国/日本双方のプラットフォームとレーベルの傾向、話数・文字数などの連載作法、そしてこのジャンル特有の「AI臭」を消して人間の書き手が書いたように見せる技術と、一人で長編を最初から最後まで独立して書き上げるための実務フローをカバーする。「ロマンスファンタジーを書いて」「ロマファン」「悪役令嬢もの」「婚約破棄もの」「契約結婚もの」「転生令嬢」「乙女ゲーム世界に転生」「異世界の令嬢/公爵令嬢の話」「アリアンローズ/フェアリーキス系の作品」「なろう系の女性向け転生ファンタジー」といった依頼が来たら必ず先に読むこと。CryptoManga Studiosでのロマンスファンタジー領域の企画・執筆・編集で使う想定。
---

# ロマンスファンタジー(ロマファン)専門スキル

このスキルは `creative-writing` スキルの一般的な創作技術(プロット構築・キャラ設計・文章推敲・AI臭抜き)を前提に、**ロマンスファンタジーというジャンル固有の様式・市場・読者心理**を深掘りするための補助知識ベース。creative-writingが「どう書くか」の一般技術なら、このスキルは「このジャンルでは何が正解とされているか」の専門知識にあたる。

## 前提: creative-writingスキルとセットで使う

このスキル単体では完結しない。実際の執筆は必ず `.claude/skills/creative-writing/` の以下と組み合わせる。

- `story-structure.md`(プロット・話数構成の一般原則)
- `character-craft.md`(Want/Need/Woundなどキャラ設計の一般原則)
- `prose-and-dialogue.md`(文章・セリフの一般的な書き方)
- `editing-checklist.md`(推敲の一般手順)
- `anti-ai-tells.md`(AI臭を抜く一般技術。**このスキルの `human-voice-for-ro-fan.md` はこれの派生版であり、これを読んでいる前提で書かれている**)

順番の目安: 一般原則(creative-writing) → ジャンル固有の様式(このスキル) → 一般原則に戻って自己レビュー、という往復。

## 依頼タイプ → 参照ガイド

| 依頼の種類 | 読むファイル |
|---|---|
| ジャンルの定義・成り立ち・韓国発の経緯・隣接ジャンルとの違いを知りたい | `references/genre-deep-dive.md` |
| 王道トロープ(悪役令嬢・婚約破棄・契約結婚・ざまぁ・追放・회빙환)の構造を使いたい/外したい | `references/tropes-and-formulas.md` |
| 男主人公/女主人公のキャラクター類型を設計したい | `references/character-archetypes.md` |
| 貴族社会・学園(アカデミー)・宮廷政治・魔法/聖女システムなどの世界観を作りたい | `references/worldbuilding-conventions.md` |
| どのプラットフォーム/レーベル向けか、1話の文字数・話数設計・連載ペースを決めたい | `references/serial-format-and-platforms.md` |
| このジャンル特有の「AIっぽさ」「テンプレ感」を消したい、納品前の最終チェック | `references/human-voice-for-ro-fan.md`(**必ず最後にかける**) |
| 一人で長編を最初から最後まで独立して書き切りたい(企画〜完結までの全工程) | `references/independent-writing-workflow.md` |

## このジャンルを扱う上での大原則

1. **「予想は裏切り、期待は裏切らない」の最先鋭ジャンル。** ロマファン読者はジャンルへの期待値がきわめて明確(甘さ・カタルシス・身分差の解消)。型を外すのは経路であって、感情の着地点ではない。
2. **회빙환(回帰・憑依・転生)は前提条件であって物語そのものではない。** 「なぜ元の運命を回避したいのか」「憑依/転生した自分だからこそ取れる選択」が薄いと、ただのステータス確認ゲームになる。`tropes-and-formulas.md` 参照。
3. **1話ずつ課金/クリックされる媒体であることを忘れない。** 起承転結を全体だけでなく各話単位でも成立させる。`serial-format-and-platforms.md` 参照。
4. **「整いすぎ」がこのジャンルでは特に起きやすい。** 男主人公の溺愛度、女主人公の有能さ、対比構造の美しさが、そのまま量産型テンプレ感=AI臭に直結する。`human-voice-for-ro-fan.md` で必ず崩す。
5. **一人称/三人称どちらでも、心理描写の密度がジャンルの生命線。** 事件の外し方よりも、主人公が「何を感じ、何を我慢し、何に気づいていないか」の解像度が読者を掴む。

## 進め方の型(このジャンルでの新規プロット依頼の場合)

1. ログライン確定:**「型」×「男女アーキタイプ」×「差別化ポイント1つ」** の掛け合わせで決める(`tropes-and-formulas.md` + `character-archetypes.md`)
2. 媒体とプラットフォームの確認(書籍化狙いのなろう系か、Web連載か、韓国式カカオページ/노벨피아想定か)→ `serial-format-and-platforms.md`
3. キャラクター・世界観の骨格(`character-archetypes.md` + `worldbuilding-conventions.md`、creative-writingの `character-craft.md`/`worldbuilding.md` と併用)
4. 話数割・プロット(creative-writingの `story-structure.md` に、このジャンルの話単位の起承転結パターンを乗せる)
5. 執筆(creative-writingの `prose-and-dialogue.md`)
6. 自己レビュー2段:①creative-writingの `editing-checklist.md` → ②このスキルの `human-voice-for-ro-fan.md`

長編を一人で最初から最後まで独立して仕上げたい場合は、上記を工程化した `references/independent-writing-workflow.md` をそのまま手順書として使う。
