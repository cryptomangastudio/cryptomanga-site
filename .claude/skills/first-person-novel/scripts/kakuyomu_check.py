#!/usr/bin/env python3
"""カクヨム投稿前の記法・表記チェッカー。原稿の本文だけを検査する簡易ツール(公式の検証ではない)。

使い方: python3 kakuyomu_check.py 原稿.md [--skip-header]
  --skip-header  最初の「---」行より前(設計メモなど)を検査対象から外す

検査: ルビ記法(《》と縦棒)、傍点、三点リーダ・ダッシュ、数字表記の混在と長い漢数字、
      半角記号、「。」で閉じる括弧、字下げの混在、連続空行、本文の文字数(ルビ除く)
"""
import re
import sys

KANJI = r"一-鿿々〆ヶ"
NUM_KANJI = "一二三四五六七八九十百千万〇"


def load(path, skip_header):
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines()
    if skip_header:
        for i, line in enumerate(lines):
            if line.strip() == "---":
                lines = lines[i + 1:]
                break
    return lines


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1
    lines = load(args[0], "--skip-header" in argv)
    warn = []

    def w(ln, msg, line):
        warn.append(f"  L{ln}: {msg} | {line.strip()[:36]}")

    emphasis = re.compile(r"《《(.+?)》》")
    ruby = re.compile(r"(?:[|｜]([^《》|｜\n]+?)|([" + KANJI + r"]+))《([^《》\n]*)》")
    blank_run = 0
    indented = plain = 0
    half_digit_lines = 0
    kanji_num_lines = 0

    for ln, line in enumerate(lines, 1):
        s = line.rstrip()
        if not s.strip():
            blank_run += 1
            if blank_run == 3:
                w(ln, "空行が3行以上続く(スマホでは大きな空白になる)", "")
            continue
        blank_run = 0

        # 傍点・ルビを一度取り除いた文字列で他の検査をする
        tmp = emphasis.sub(lambda m: m.group(1), s)
        tmp = ruby.sub(lambda m: (m.group(1) or m.group(2)), tmp)

        s_plain = emphasis.sub(lambda m: m.group(1), s)
        kana_flag = False
        for m in re.finditer(r"([ぁ-んァ-ヶー]+)《", s_plain):
            if not re.search(r"[|｜][^《》]*" + re.escape(m.group(1)) + r"《", s_plain):
                w(ln, "親文字にかな・カタカナが混ざるなら、先頭に縦棒｜が必要", s)
                kana_flag = True
        if ("《" in tmp or "》" in tmp) and not kana_flag:
            w(ln, "ルビ・傍点として解釈できない《》がある(親文字が漢字だけか、縦棒｜で始点を指定したか確認)", s)
        for m in ruby.finditer(s_plain):
            if m.group(3) == "":
                w(ln, "ルビが空", s)

        if re.search(r"\.\.\.|。。。|・・・", tmp):
            w(ln, "三点リーダは「……」(2つ重ね)にする", s)
        if re.search(r"(?<!…)…(?!…)", tmp):
            w(ln, "「…」が1つだけ。「……」(2つ重ね)にする", s)
        if re.search(r"--|―(?!―)|(?<!—)—(?!—)", tmp):
            w(ln, "ダッシュは「――」か「——」(2つ重ね)で統一する", s)
        if re.search(r"[!?]", tmp):
            w(ln, "半角の!?がある。全角の！？にする", s)
        if re.search(r"。」|。』", tmp):
            w(ln, "括弧の中の最後に句点を打っている(通常は打たない)", s)
        if re.search(r"[,.](?![0-9])", tmp):
            w(ln, "半角の,.がある。、。にする(数字の小数点を除く)", s)
        if re.search(r"[" + NUM_KANJI + r"]{4,}", tmp):
            w(ln, "4文字以上続く漢数字。読みにくければ概数にするか算用数字にする", s)

        if re.search(r"[0-9]", tmp):
            half_digit_lines += 1
        if re.search(r"[" + NUM_KANJI + r"]", tmp):
            kanji_num_lines += 1

        if s.startswith("　"):
            indented += 1
        elif not s.startswith(("「", "『", "（", "(", "〈", "＊", "*", "◇", "◆", "＃", "#")):
            plain += 1

    if half_digit_lines and kanji_num_lines:
        warn.append(f"  数字表記が混在:算用数字を含む行 {half_digit_lines} / 漢数字を含む行 {kanji_num_lines}(作品内で統一する)")
    if indented and plain:
        warn.append(f"  字下げが混在:字下げあり {indented}段落 / なし {plain}段落")

    body = "\n".join(lines)
    body = emphasis.sub(lambda m: m.group(1), body)
    body = ruby.sub(lambda m: (m.group(1) or m.group(2)), body)
    count = len(re.sub(r"\s", "", body))
    print(f"== {args[0]}")
    print(f"  本文の文字数(ルビ・空白を除く): {count}字")
    if count < 2000:
        print("  参考:2,000字未満。短めの話数(非公式の目安では2,000〜3,000字以上が読み応えの下限とされる)")
    if warn:
        print(f"  要確認 {len(warn)} 件")
        print("\n".join(warn[:40]))
    else:
        print("  記法・表記の問題は検出されなかった")
    return 1 if warn else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
