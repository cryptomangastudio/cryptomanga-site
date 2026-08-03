#!/usr/bin/env python3
"""Webtoon原稿PDFをレビュー用のページ画像(PNG)に変換する。

使い方:
    python3 pdf_to_pages.py <input.pdf> <output_dir> [--width 1400] [--max-height 2400]

縦長ページ(Webtoonの縦スクロール原稿)は、上下10%オーバーラップ付きで
複数タイルに分割する。出力例: page003.png / page003_t01.png, page003_t02.png ...
最後に「ページ番号 → 画像ファイル」の一覧を標準出力に出す。
"""
import argparse
import os
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF がありません。先に `pip install pymupdf` を実行してください。")


def render(pdf_path: str, out_dir: str, target_width: int, max_height: int) -> None:
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    manifest = []
    for i, page in enumerate(doc):
        num = i + 1
        zoom = target_width / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        if pix.height <= max_height:
            name = f"page{num:03d}.png"
            pix.save(os.path.join(out_dir, name))
            manifest.append((num, [name]))
            continue
        # 縦長ページはオーバーラップ付きでタイル分割
        overlap = max_height // 10
        step = max_height - overlap
        tiles = []
        top, t = 0, 1
        while top < pix.height:
            bottom = min(top + max_height, pix.height)
            clip = fitz.Rect(0, top / zoom, page.rect.width, bottom / zoom)
            tile = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
            name = f"page{num:03d}_t{t:02d}.png"
            tile.save(os.path.join(out_dir, name))
            tiles.append(name)
            if bottom >= pix.height:
                break
            top += step
            t += 1
        manifest.append((num, tiles))
    doc.close()
    print(f"pages={len(manifest)} out_dir={out_dir}")
    for num, files in manifest:
        print(f"  P{num}: {' '.join(files)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out_dir")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--max-height", type=int, default=2400)
    a = ap.parse_args()
    render(a.pdf, a.out_dir, a.width, a.max_height)
