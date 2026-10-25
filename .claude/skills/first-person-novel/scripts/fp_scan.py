#!/usr/bin/env python3
"""一人称小説の原稿から、機械的に数えられる癖を数える簡易スキャナ。判定器ではなく当たりをつける道具。

使い方: python3 fp_scan.py 原稿.txt [原稿2.md ...]   (ファイル名に - で標準入力)
"""
import re
import statistics
import sys

PRONOUN = r"(?:私|わたし|僕|ぼく|俺|おれ|あたし|自分|うち|わたくし)"

# (名前, 正規表現, 1000字あたりの目安上限)
CHECKS = [
    ("知覚・思考の動詞(フィルター語)", r"(?:が|を|と)?(?:見え|聞こえ|気づ|感じ|思っ|考え|分かっ|わかっ|気が付|気がつ)(?:た|ていた|ました)", 4.0),
    ("推量の曖昧表現(ような気がした 等)", r"ような気が|気がした|かもしれないと思", 1.5),
    ("感情の名指し", r"(?:悲し|嬉し|寂し|楽し|怖|恐ろし|悔し|切な|愛おし|苦し|不安|安堵|絶望|幸せ)(?:かった|い気持ち|み|さ|く思)", 2.0),
    ("感情を身体で締める定型", r"胸が(?:締め付|痛|熱|高鳴|ざわ)|心臓が(?:跳|高鳴|早鐘)|頭が真っ白|背筋が凍", 0.8),
    ("定型比喩", r"まるで|かのよう|ような(?:瞳|笑顔|声)", 1.0),
    ("動作に貼る副詞", r"そっと|静かに|ふと|ふいに|ゆっくりと|優しく|小さく|わずかに", 3.0),
    ("総括・予告の締め", r"まだ知らなかった|知る由もなかった|始まりだった|運命が(?:大きく)?動き|瞬間だった", 0),
    ("括弧の心の声", r"[（(][^）)\n]{2,40}[）)]と(?:思|心の中)", 0.5),
    ("読者への語りかけ・説明", r"ちなみに|ここで説明|説明しておく|言っておく|断っておく", 1.0),
]

SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+")


def body_text(raw):
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith(">"):
            s = s.lstrip(">").strip()
        if s.startswith("#") or s.startswith("|") or s.startswith("---") or s.startswith("```"):
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

    sentences = [s.strip() for s in SENTENCE_SPLIT.split(text) if len(s.strip()) >= 2]
    narration = [s for s in sentences if not re.search(r"[「」『』]", s)]

    if narration:
        starts = [s for s in narration if re.match(PRONOUN + r"(?:は|が|も|の)", s)]
        ratio = len(starts) / len(narration)
        mark = "要確認" if ratio > 0.15 else "ok"
        print(f"  [{mark}] 一人称代名詞で始まる地の文: {len(starts)}/{len(narration)}文 ({ratio:.0%}, 目安 15%以下)")
        for s in starts[:3]:
            print(f"      「{s[:30]}」")

    for label, pat, limit in CHECKS:
        rx = re.compile(pat)
        hits = [(i + 1, m.group(0), line) for i, line in enumerate(lines) for m in rx.finditer(line)]
        if not hits:
            continue
        rate = len(hits) * per_k
        over = rate > limit if limit else True
        print(f"  [{'要確認' if over else 'ok'}] {label}: {len(hits)}件 ({rate:.1f}/1000字, 目安 {limit or '0件'})")
        if over:
            for ln, word, line in hits[:4]:
                print(f"      L{ln} 「{word}」 {line[:40]}")

    if len(sentences) >= 8:
        lens = [len(s) for s in sentences]
        cv = statistics.pstdev(lens) / statistics.mean(lens)
        print(f"  [{'要確認' if cv < 0.5 else 'ok'}] 文の長さのばらつき(変動係数): {cv:.2f} (0.5未満は均一すぎる傾向)")

    # 地の文の「た」止めの連続。台詞を含む文は連続を区切る
    flags = [None if re.search(r"[「」『』]", s) else s.endswith("た") for s in sentences]
    run, worst = 0, 0
    for f in flags:
        run = run + 1 if f else 0
        worst = max(worst, run)
    if worst >= 5:
        print(f"  [要確認] 「〜た。」で終わる地の文が最大{worst}文連続(「る」形・体言止め・倒置を混ぜる)")

    # 地の文だけの段落が連続して長い(内省の長さ)
    paras = [p for p in text.split("\n") if p.strip()]
    run, worst = 0, 0
    for p in paras:
        run = run + 1 if not re.search(r"[「」『』]", p) else 0
        worst = max(worst, run)
    if worst >= 6:
        print(f"  [要確認] 台詞のない段落が最大{worst}段落連続(内省・説明が長い。外の出来事を入れる)")
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
