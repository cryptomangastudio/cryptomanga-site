#!/usr/bin/env python3
"""小説の新旧2版を比べ、変更・追加した文字を紫にしたHTMLを作る(Googleドキュメントに変換して使う)。
使い方: python3 diff_html.py 旧.md 新.md 出力.html
- 段落(空行区切り)ごとに照合し、新しく入った文字を紫にする。削除は表示しない。
- ◇ の場面転換は、そのまま出す。
"""
import sys, difflib, html

def paras(path):
    t = open(path, encoding="utf-8").read().strip("\n")
    return [p for p in t.split("\n\n")]

def span(txt, changed):
    e = html.escape(txt)
    return f'<span style="color:#7b2fbf;">{e}</span>' if changed else e

def render(old_p, new_p):
    sm = difflib.SequenceMatcher(a=old_p, b=new_p, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out += [f"<p>{html.escape(p)}</p>" for p in new_p[j1:j2]]
        elif tag in ("insert",):
            out += [f"<p>{span(p, True)}</p>" for p in new_p[j1:j2]]
        elif tag == "delete":
            continue
        else:  # replace: 文字単位で比べる
            olds, news = old_p[i1:i2], new_p[j1:j2]
            for k, n in enumerate(news):
                o = olds[k] if k < len(olds) else ""
                cm = difflib.SequenceMatcher(a=o, b=n, autojunk=False)
                seg = []
                for t, a1, a2, b1, b2 in cm.get_opcodes():
                    if t == "equal":
                        seg.append(span(n[b1:b2], False))
                    elif t in ("insert", "replace"):
                        seg.append(span(n[b1:b2], True))
                out.append("<p>" + "".join(seg) + "</p>")
    return "\n".join(out)

if __name__ == "__main__":
    old, new, dst = sys.argv[1:4]
    body = render(paras(old), paras(new))
    note = '<p><span style="color:#7b2fbf;">※ 紫の文字が、前の版から変更・追加した箇所です。</span></p>'
    open(dst, "w", encoding="utf-8").write(f'<html><head><meta charset="utf-8"></head><body>{note}{body}</body></html>')
    print("ok", dst)
