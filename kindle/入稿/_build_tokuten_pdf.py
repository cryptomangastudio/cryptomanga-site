# -*- coding: utf-8 -*-
import re, io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
                                Table, TableStyle, HRFlowable)

pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))   # gothic
pdfmetrics.registerFont(UnicodeCIDFont('HeiseiMin-W3'))      # mincho
G, M = 'HeiseiKakuGo-W5', 'HeiseiMin-W3'
ACC = colors.HexColor('#2f6d6a')
INK = colors.HexColor('#242019')
SOFT = colors.HexColor('#5c5648')

def esc(s):
    s = s.replace('`','')
    s = s.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    return s

st_title = ParagraphStyle('t', fontName=G, fontSize=15, leading=22, textColor=INK, spaceAfter=4)
st_h1    = ParagraphStyle('h1', fontName=G, fontSize=13, leading=19, textColor=colors.white,
                          backColor=ACC, borderPadding=(5,6,5,6), spaceBefore=14, spaceAfter=8, leftIndent=0)
st_h2    = ParagraphStyle('h2', fontName=G, fontSize=11.5, leading=17, textColor=ACC,
                          spaceBefore=10, spaceAfter=4)
st_body  = ParagraphStyle('b', fontName=M, fontSize=10, leading=16.5, textColor=INK, spaceAfter=5)
st_note  = ParagraphStyle('n', fontName=M, fontSize=8.6, leading=13.5, textColor=SOFT, spaceAfter=3)
st_chk   = ParagraphStyle('c', fontName=G, fontSize=10, leading=16, textColor=INK,
                          leftIndent=14, firstLineIndent=-14, spaceAfter=4)
st_arrow = ParagraphStyle('a', fontName=M, fontSize=9.2, leading=14.5, textColor=SOFT,
                          leftIndent=14, spaceAfter=5)
st_cell  = ParagraphStyle('cell', fontName=G, fontSize=9.5, leading=14, textColor=INK)

def parse(md, drop_leading_bq=True):
    lines = md.split('\n')
    flow = []
    i = 0
    tbl = []
    def flush_table():
        nonlocal tbl
        if not tbl: return
        rows = [r for r in tbl if not re.match(r'^\s*\|[\s:|-]+\|\s*$', r)]
        data = []
        for r in rows:
            cells = [c.strip() for c in r.strip().strip('|').split('|')]
            data.append([Paragraph(esc(c), st_cell) for c in cells])
        if data:
            ncol = max(len(r) for r in data)
            data = [r+['']*(ncol-len(r)) for r in data]
            w = (170*mm)/ncol
            t = Table(data, colWidths=[w]*ncol)
            t.setStyle(TableStyle([
                ('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#cfd8d7')),
                ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e4efee')),
                ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),
                ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
            ]))
            flow.append(Spacer(1,3)); flow.append(t); flow.append(Spacer(1,6))
        tbl = []
    started = False
    seen_hr = False
    for raw in lines:
        t = raw.rstrip()
        s = t.strip()
        if s.startswith('|'):
            tbl.append(t); continue
        else:
            flush_table()
        if s.startswith('```'):
            continue
        if s == '':
            continue
        if s.startswith('> '):
            if not seen_hr:  # 最初の --- より前の編集メモは全部捨てる
                continue
            flow.append(Paragraph(esc(s[2:]), st_note)); continue
        if s == '---':
            seen_hr = True
            if started: flow.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#e0dccf'), spaceBefore=6, spaceAfter=8))
            continue
        if s.startswith('# '):
            head = re.sub(r'^#\s+','',s)
            if not started:
                flow.append(Paragraph(esc(head), st_title)); started=True
            else:
                flow.append(Paragraph(esc(head), st_h1))
            started=True; continue
        if s.startswith('## '):
            flow.append(Paragraph(esc(s[3:]), st_h1 if s[3:].startswith(('①','②','③','STEP')) else st_h2)); started=True; continue
        if s.startswith('### '):
            flow.append(Paragraph(esc(s[4:]), st_h2)); started=True; continue
        started=True
        if s.startswith('- □') or s.startswith('□'):
            txt = s.lstrip('-').strip()
            txt = txt[1:].strip() if txt.startswith('□') else txt
            flow.append(Paragraph('☐　'+esc(txt), st_chk)); continue
        if s.startswith('- '):
            flow.append(Paragraph('・　'+esc(s[2:]), st_chk)); continue
        if re.match(r'^\d+\.\s', s):
            flow.append(Paragraph('　'+esc(s), st_body)); continue
        if s.startswith('→') or s.startswith('※'):
            flow.append(Paragraph(esc(s), st_arrow if s.startswith('→') else st_note)); continue
        if s.startswith('（') and s.endswith('）'):
            flow.append(Paragraph(esc(s), st_note)); continue
        flow.append(Paragraph(esc(s), st_body))
    flush_table()
    return flow

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(G, 7.5); canvas.setFillColor(SOFT)
    canvas.drawCentredString(A4[0]/2, 12*mm, 'シリーズ「消耗しない所得の増やし方」／軌道キャリ・CryptoManga Studios　—　無料特典（LINE配布用）')
    canvas.drawRightString(A4[0]-18*mm, 12*mm, str(doc.page))
    canvas.restoreState()

def build(src, out, footer_extra=None):
    md = io.open(src, encoding='utf-8').read()
    flow = parse(md)
    if footer_extra:
        flow.append(Spacer(1,10))
        for ln in footer_extra:
            flow.append(Paragraph(esc(ln), st_note))
    doc = BaseDocTemplate(out, pagesize=A4,
                          leftMargin=20*mm, rightMargin=20*mm, topMargin=18*mm, bottomMargin=20*mm,
                          title='無料特典')
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='f')
    doc.addPageTemplates([PageTemplate(id='p', frames=[frame], onPage=footer)])
    doc.build(flow)
    print('wrote', out)

B = '/home/user/cryptomanga-site/kindle/企画'
O = '/tmp/claude-0/-home-user-cryptomanga-site/159b757c-f6e0-5a14-8533-6079fb04f4a7/scratchpad/特典PDF'
import os; os.makedirs(O, exist_ok=True)
build(f'{B}/第1巻_特典PDF_無料チェックリスト.md', f'{O}/特典1_転職・年収交渉チェックリスト.pdf')
build(f'{B}/特典セット_第4-6巻ワークシート.md', f'{O}/特典2_第4-6巻ワークシート集.pdf')
