#!/usr/bin/env python3
"""設定の機械チェック。canon.json の内容と本文を照合し、表記ゆれ・禁止語・語り手の代名詞違反を探す。

使い方: python3 canon_check.py canon.json 第01話.md [第02話.md ...]

canon.json の例(canon.template.json 参照):
{
  "narrator_pronoun": "僕",
  "forbidden_in_narration": ["私", "俺"],
  "spelling": {"グラーツ": ["グラッツ", "グラーツェ"]},
  "forbidden_words": ["メンタル", "ストレス"],
  "forbidden_phrases": ["田中先輩", "田中君"]
}
"""
import json
import re
import sys

QUOTE = re.compile(r"「[^」]*」|『[^』]*』")


def strip_dialogue(line):
    return QUOTE.sub("", line)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    with open(argv[0], encoding="utf-8") as f:
        canon = json.load(f)
    problems = 0
    for path in argv[1:]:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        pron = canon.get("narrator_pronoun")
        pron_count = 0
        narration_count = 0
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if not s or s.startswith(("#", "|", "---", "```")):
                continue
            narr = strip_dialogue(s)
            narration_count += len(narr)
            if pron:
                pron_count += narr.count(pron)
            for word in canon.get("forbidden_in_narration", []):
                if word in narr:
                    print(f"{path}:{i}: 地の文に禁止の語「{word}」 | {s[:40]}")
                    problems += 1
            for good, wrongs in canon.get("spelling", {}).items():
                for w in wrongs:
                    if w in s:
                        print(f"{path}:{i}: 表記ゆれ「{w}」→正しくは「{good}」 | {s[:40]}")
                        problems += 1
            for w in canon.get("forbidden_words", []):
                if w in s:
                    print(f"{path}:{i}: 禁止語「{w}」 | {s[:40]}")
                    problems += 1
            for p in canon.get("forbidden_phrases", []):
                if p in s:
                    print(f"{path}:{i}: 禁止の呼称・表現「{p}」 | {s[:40]}")
                    problems += 1
        if pron and narration_count:
            print(f"{path}: 地の文の「{pron}」{pron_count}回 / 地の文{narration_count}字 ({pron_count * 1000 / narration_count:.1f}/1000字)")
    print(f"問題 {problems} 件")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
