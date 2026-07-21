#!/usr/bin/env python3
"""YouTube自動字幕(.vtt)を、拉片用の「秒 + セリフ」テーブル(Markdown)に整形する。
自動字幕特有のロールアップ重複(同じ行が2キューにまたがる)を除去する。

使い方: python3 vtt整形.py ../素材/*.vtt  → 同名の .md を隣に出力
"""
import re
import sys
from pathlib import Path

TS = re.compile(r"(\d+):(\d+):(\d+)\.(\d+)\s+-->")
TAG = re.compile(r"<[^>]+>")


def parse(path: Path) -> list[tuple[float, str]]:
    rows, seen_tail = [], ""
    t = 0.0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = TS.match(raw.strip())
        if m:
            h, mi, s, ms = map(int, m.groups())
            t = h * 3600 + mi * 60 + s + ms / 1000
            continue
        line = TAG.sub("", raw).strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if line == seen_tail:  # ロールアップ重複
            continue
        seen_tail = line
        rows.append((t, line))
    return rows


def main() -> None:
    for arg in sys.argv[1:]:
        p = Path(arg)
        rows = parse(p)
        out = p.with_suffix(".md")
        lines = [f"# セリフ書き起こし: {p.stem}", "", "※自動字幕由来。誤認識あり。内部分析専用・転載禁止。", "", "| 秒 | セリフ |", "|---|---|"]
        lines += [f"| {t:6.1f} | {text} |" for t, text in rows]
        chars = sum(len(x) for _, x in rows)
        dur = rows[-1][0] if rows else 0
        lines += ["", f"総行数: {len(rows)} / 総字数: {chars} / 尺: {dur:.0f}秒 / 字数密度: {chars / dur:.1f}字/秒" if dur else ""]
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"{p.name} -> {out.name} ({len(rows)}行)")


if __name__ == "__main__":
    main()
