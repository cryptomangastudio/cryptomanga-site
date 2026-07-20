const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  PageBreak, LevelFormat, BorderStyle, Bookmark, InternalHyperlink
} = require('docx');

const BASE = '/home/user/cryptomanga-site/kindle/企画';
const OUT = '/tmp/claude-0/-home-user-cryptomanga-site/159b757c-f6e0-5a14-8533-6079fb04f4a7/scratchpad/入稿docx';
fs.mkdirSync(OUT, { recursive: true });

const SERIES = '消耗しない所得の増やし方';
const AUTHOR = '軌道キャリ';
const PUBLISHER = 'CryptoManga Studios';
const BODYFONT = 'Yu Mincho';
const HEADFONT = 'Yu Gothic';

const VOLS = [
  { n: 1, main: '副業はするな！', sub: '1000万稼いで700万溶かし、家族まで失いかけた僕が、あなたに副業を勧めない理由' },
  { n: 2, main: '副業＜転職', sub: '数千時間がマイナス100万、十数時間が年収プラス300万' },
  { n: 3, main: '地味昇給のススメ', sub: '「頑張りました」では、上がらない。静かに昇給する人の習慣' },
  { n: 4, main: '副業崩壊', sub: '「家族のため」はすぐやめろ。夫婦のお金と時間の話' },
  { n: 5, main: '時給300円', sub: '損をしない副業を始める前の損得計算' },
  { n: 6, main: '副業の罠', sub: '1000万稼いで分かった"一生楽にならない"真実' },
];

const clean = s => s.replace(/【要確認[^】]*】/g, '').replace(/[　 ]+$/, '');

// フロント・バックから n番目のコードフェンス内容を取り出す（0=扉,1=免責,2=プロフィール,3=奥付,4=AI一文）
function codeBlocks(md) {
  const blocks = [];
  const re = /```\r?\n([\s\S]*?)```/g;
  let m;
  while ((m = re.exec(md)) !== null) blocks.push(m[1].replace(/\s+$/, '').split('\n'));
  return blocks;
}

function p(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, font: opts.font || BODYFONT, size: opts.size || 21, bold: !!opts.bold, color: opts.color })],
    alignment: opts.align,
    spacing: { after: opts.after == null ? 160 : opts.after, line: 360, ...(opts.before ? { before: opts.before } : {}) },
  });
}
function bullet(text) {
  return new Paragraph({
    numbering: { reference: 'b', level: 0 },
    children: [new TextRun({ text, font: BODYFONT, size: 21 })],
    spacing: { after: 100, line: 360 },
  });
}
function h1(text, bmId) {
  const run = new TextRun({ text, font: HEADFONT, size: 30, bold: true });
  const children = bmId ? [new Bookmark({ id: bmId, children: [run] })] : [run];
  return new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 480, after: 220 }, children });
}
// クリック可能な目次ページ（KDPの「目次がありません」対策）
function tocPage(toc) {
  const out = [];
  out.push(new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 240, after: 220 },
    children: [new TextRun({ text: '目次', font: HEADFONT, size: 30, bold: true })] }));
  for (const e of toc) {
    out.push(new Paragraph({ spacing: { after: 120, line: 360 },
      children: [new InternalHyperlink({ anchor: e.id, children: [new TextRun({ text: e.title, font: BODYFONT, size: 22 })] })] }));
  }
  out.push(new Paragraph({ children: [new PageBreak()] }));
  return out;
}
function h2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 300, after: 140 },
    children: [new TextRun({ text, font: HEADFONT, size: 24, bold: true })] });
}

function parseBody(md) {
  const lines = md.split('\n');
  const out = [];
  const toc = [];
  let n = 0;
  let started = false;
  for (let i = 1; i < lines.length; i++) {
    const raw = lines[i];
    const t = raw.trim();
    if (!started) { if (t === '---') started = true; continue; }
    if (t.startsWith('## 出版前')) break;
    if (t === '' ) continue;
    if (t === '---') continue;
    if (t.startsWith('> ')) continue;
    if (t.startsWith('## ')) {
      const title = clean(t.slice(3).trim());
      const id = `ch${n++}`;
      toc.push({ id, title });
      out.push(h1(title, id));
      continue;
    }
    if (t.startsWith('### ')) { out.push(h2(clean(t.slice(4).trim()))); continue; }
    if (t.startsWith('- ')) { out.push(bullet(clean(t.slice(2).trim()))); continue; }
    if (t.startsWith('▶') || t.startsWith('　▶')) { out.push(p(clean(t))); continue; }
    out.push(p(clean(t)));
  }
  return { children: out, toc };
}

function titlePage(v) {
  const blank = () => new Paragraph({ children: [], spacing: { after: 240 } });
  return [
    blank(), blank(), blank(),
    p(v.main, { align: AlignmentType.CENTER, size: 44, bold: true, font: HEADFONT, after: 200 }),
    p('──' + v.sub, { align: AlignmentType.CENTER, size: 22, font: HEADFONT, after: 600 }),
    p(AUTHOR + '　著', { align: AlignmentType.CENTER, size: 24, after: 200 }),
    p(`シリーズ「${SERIES}」　第${v.n}巻`, { align: AlignmentType.CENTER, size: 20, color: '555555' }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

function fromBlock(lines) { return lines.filter(l => l.length).map(l => p(l, { after: 120 })); }

function build(v) {
  const bodyMd = fs.readFileSync(path.join(BASE, `第${v.n}巻_Kindle版_本文.md`), 'utf8');
  const fb = codeBlocks(fs.readFileSync(path.join(BASE, `第${v.n}巻_入稿用フロント・バック.md`), 'utf8'));
  const disclaimer = fb[1] || [];
  const aiNote = fb[4] || [];
  const profile = fb[2] || [];

  const { children: bodyChildren, toc } = parseBody(bodyMd);

  const children = [];
  children.push(...titlePage(v));
  // 免責
  children.push(h1('はじめにお読みください'));
  children.push(...fromBlock(disclaimer.filter(l => !l.startsWith('■'))));
  if (aiNote.length) { children.push(p('', { after: 120 })); children.push(...fromBlock(aiNote)); }
  children.push(new Paragraph({ children: [new PageBreak()] }));
  // 目次（クリック可能・KDPの「目次がありません」対策）
  children.push(...tocPage(toc));
  // 本文（まえがき〜おわりに〜出典）
  children.push(...bodyChildren);
  // 著者プロフィール
  children.push(new Paragraph({ children: [new PageBreak()] }));
  children.push(h1('著者について'));
  children.push(...fromBlock(profile));
  // 奥付
  children.push(new Paragraph({ children: [new PageBreak()] }));
  children.push(h1('奥付'));
  children.push(p(`${v.main}──${v.sub}`, { after: 200 }));
  children.push(p('［発売日を記載］　初版発行', { after: 120 }));
  children.push(p(`著　者　${AUTHOR}`, { after: 60 }));
  children.push(p(`発　行　${PUBLISHER}`, { after: 200 }));
  children.push(p('本書の内容の無断転載・複製を禁じます。', { after: 60 }));
  children.push(p(`© 2026 ${AUTHOR} / ${PUBLISHER}`));

  const doc = new Document({
    creator: PUBLISHER,
    title: `${v.main}──${v.sub}`,
    styles: { default: { document: { run: { font: BODYFONT, size: 21 } } } },
    numbering: { config: [{ reference: 'b', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 400, hanging: 200 } } } }] }] },
    sections: [{ properties: {}, children }],
  });
  return doc;
}

(async () => {
  for (const v of VOLS) {
    const doc = build(v);
    const buf = await Packer.toBuffer(doc);
    const fn = path.join(OUT, `第${v.n}巻_${v.main}.docx`);
    fs.writeFileSync(fn, buf);
    console.log('wrote', fn, buf.length, 'bytes');
  }
})();
