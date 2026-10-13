#!/usr/bin/env python3
"""日本語小説原稿の「AIっぽい癖」を数える簡易スキャナ。判定器ではなく推敲の当たりをつける道具。

使い方: python3 ai_tell_scan.py 原稿.txt [原稿2.md ...]   (ファイル名に - で標準入力)
"""
import re
import statistics
import sys

# (名前, 正規表現, 1000字あたりの目安上限。0 は1件でも表示)
CHECKS = [
    ("定型比喩(まるで/かのよう 等)", r"まるで|かのよう|ような瞳|宝石のよう|月光を|星屑", 1.0),
    ("使い古された反応描写", r"凍りつ|時が止ま|息を呑|胸の奥|締め付け|高鳴|頭が真っ白|じんわり|胸が熱く|温かいものが", 1.0),
    ("動作に貼る副詞", r"そっと|静かに|ふと|ふいに|小さく|わずかに|ゆっくりと|優雅に|優しく", 3.0),
    ("否定を重ねて本命を出す構文", r"(?:ではない|でもない|じゃない)。[^。\n]{0,15}(?:ではない|でもない|じゃない)。|(?:ではない|でもない)。(?:それは|——)", 0),
    ("総括の締め(始まりだった 等)", r"始まりだった|瞬間だった|まだ知らなかった|知る由もなかった|運命が(?:大きく)?動き|物語は(?:ここから)?始まる", 0),
    ("溺愛の定番決めゼリフ", r"俺のもの|僕のもの|誰にも渡さない|離さない", 0),
    ("「のだった」文末", r"(?<!も)のだった。", 1.0),
    ("英語式の引用符", r"[“”\"]", 0),
    ("ダッシュ(——)", r"——", 3.0),
]

SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+")


def body_text(raw):
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith(">"):
            s = s.lstrip(">").strip()
        if s.startswith("#") or s.startswith("|") or s.startswith("---"):
            continue
        lines.append(s)
    return "\n".join(lines)


def scan(name, raw):
    text = body_text(raw)
    n = len(re.sub(r"\s", "", text))
    if n == 0:
        print(f"== {name}: 本文が空です")
        return
    per_k = 1000 / n
    print(f"== {name}  ({n}字)")
    lines = text.splitlines()
    for label, pat, limit in CHECKS:
        rx = re.compile(pat)
        hits = [(i + 1, m.group(0), line) for i, line in enumerate(lines) for m in rx.finditer(line)]
        if not hits:
            continue
        rate = len(hits) * per_k
        over = rate > limit if limit else True
        mark = "要確認" if over else "ok"
        print(f"  [{mark}] {label}: {len(hits)}件 ({rate:.1f}/1000字, 目安 {limit or '0件'})")
        if over:
            for ln, word, line in hits[:5]:
                print(f"      L{ln} 「{word}」 {line[:40]}")

    sentences = [s.strip() for s in SENTENCE_SPLIT.split(text) if len(s.strip()) >= 2]
    if len(sentences) >= 8:
        lens = [len(s) for s in sentences]
        cv = statistics.pstdev(lens) / statistics.mean(lens)
        mark = "要確認" if cv < 0.5 else "ok"
        print(f"  [{mark}] 文の長さのばらつき(変動係数): {cv:.2f} (0.5未満は長さが均一すぎる傾向)")

    # 台詞を含む文は地の文の連続を区切る
    endings = [None if re.search(r"[「」『』]", s) else s[-1] for s in sentences]
    run, worst = 1, 1
    for a, b in zip(endings, endings[1:]):
        run = run + 1 if a is not None and a == b else 1
        worst = max(worst, run)
    if worst >= 5:
        print(f"  [要確認] 同じ文末の文字が最大{worst}文連続(「〜た。」の連打など)")
    print()


def main(paths):
    if not paths:
        print(__doc__)
        return 1
    for p in paths:
        raw = sys.stdin.read() if p == "-" else open(p, encoding="utf-8").read()
        scan(p, raw)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
