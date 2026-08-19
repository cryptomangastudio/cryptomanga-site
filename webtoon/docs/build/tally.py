# -*- coding: utf-8 -*-
"""1話の型_実作調査.md の標本リストから、切れ方の型を集計する。
分類は手で割り当てるが、**数えるのは機械**（手で書いた数字を表に載せない）。"""
import io, re, collections

DOC = '/home/user/cryptomanga-site/webtoon/docs/1話の型_実作調査.md'

TYPE = {
 'A': ('関係が成立する', '結婚・婚約・契約・雇用・弟子入り・同居・所属が、1話の中で確定する'),
 'B': ('主人公が自分から動く', '決断・宣言・要求・依頼・告白。誰かに求められたのではなく、本人が踏み出す'),
 'C': ('死んで時が戻る／転生に気づく', 'やり直しの起点が置かれる'),
 'D': ('突き落とされる', '喪失・退学・追放・拒絶。状況が悪いほうへ確定する'),
 'E': ('出会う（関係はまだ）', '惹かれる・再会する。取り決めも約束もまだ何もない'),
 'F': ('正体・秘密が露見する', '隠していたものが相手に知られる'),
 'G': ('謎か情報が投げ込まれる', '依頼・噂・手紙・読者だけが知る事実。主人公はまだ動いていない'),
}

CLS = {
 1:'A',2:'B',3:'C',4:'B',5:'A',6:'B',7:'C',8:'A',9:'G',10:'A',
 11:'A',12:'G',13:'D',14:'B',15:'A',16:'G',17:'G',18:'C',19:'G',20:'G',
 21:'D',22:'B',23:'A',24:'E',25:'E',26:'E',27:'E',28:'A',29:'D',30:'A',
 31:'B',32:'C',33:'G',34:'C',35:'E',36:'B',37:'C',38:'G',39:'D',40:'E',
 41:'A',42:'B',43:'G',44:'B',46:'A',47:'G',48:'D',49:'A',50:'C',
 51:'C',52:'A',53:'A',54:'A',55:'G',56:'B',57:'G',58:'D',59:'A',60:'E',
 61:'B',62:'G',63:'F',64:'D',65:'A',66:'B',67:'G',68:'A',69:'G',70:'B',
 71:'G',72:'D',73:'B',75:'A',76:'B',77:'B',78:'B',79:'A',80:'B',
 81:'D',82:'E',83:'G',84:'B',85:'G',86:'B',87:'D',88:'B',89:'G',90:'E',
 91:'A',92:'G',93:'G',94:'B',95:'A',96:'A',97:'A',98:'A',99:'E',100:'G',
 101:'A',102:'E',
}
# 切れ方を取得できず、集計から外した標本
EXCLUDED = {45: 'ハズレ姫は意外と愛されている？', 74: 'シュガーアップル・フェアリーテイル'}

# ロマンスファンタジー／転生・時戻り系（webtoon 縦読み市場の中心）に該当する標本
ROMFAN = {1,2,3,4,5,6,7,8,9,10,11,17,18,19,20,21,22,23,30,31,32,33,34,36,44,
          46,47,48,49,50,51,52,53,54,55,56,65,66,68,75,76,77,89,90,91}

names = {}
for line in io.open(DOC, encoding='utf-8'):
    m = re.match(r'^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|', line)
    if m:
        names[int(m.group(1))] = m.group(2)

assert set(names) == set(CLS) | set(EXCLUDED), \
    '表と分類がずれている: %s' % (set(names) ^ (set(CLS) | set(EXCLUDED)))
assert ROMFAN <= set(CLS), 'ロマファン指定に未分類がある'

cnt = collections.Counter(CLS.values())
rcnt = collections.Counter(CLS[i] for i in ROMFAN)
N, R = len(CLS), len(ROMFAN)

out = []
out.append('')
out.append('---')
out.append('')
out.append('## 集計（この節は `webtoon/docs/build/tally.py` が上の表から生成する。手で書かない）')
out.append('')
out.append('- 収集できた作品数: **%d本**' % len(names))
out.append('- うち「1話の切れ方」まで取れた本数: **%d本**（取れなかった%d本 %s は集計から外した）'
           % (N, len(EXCLUDED), '／'.join(EXCLUDED.values())))
out.append('- **目標の100本には、切れ方まで取れた本数で到達した。収集自体は%d本。**' % len(names))
out.append('')
out.append('### 1話がどう終わるか（%d本）' % N)
out.append('')
out.append('| 型 | 内容 | 本数 | 割合 | 該当番号 |')
out.append('|---|---|---:|---:|---|')
for k in 'ABCDEFG':
    ids = sorted(i for i, v in CLS.items() if v == k)
    out.append('| %s | %s — %s | %d | %d%% | %s |'
               % (k, TYPE[k][0], TYPE[k][1], cnt[k], round(100*cnt[k]/N),
                  ', '.join(map(str, ids))))
out.append('')
out.append('### ロマンスファンタジー／転生・時戻り系だけで見る（%d本）' % R)
out.append('')
out.append('| 型 | 本数 | 割合 |')
out.append('|---|---:|---:|')
for k in 'ABCDEFG':
    out.append('| %s %s | %d | %d%% |' % (k, TYPE[k][0], rcnt[k], round(100*rcnt[k]/R)))
out.append('')
ab = cnt['A'] + cnt['B']
rab = rcnt['A'] + rcnt['B']
out.append('**A＋B（関係が成立するか、主人公が自分から動く）= 全体 %d/%d（%d%%）、ロマファン %d/%d（%d%%）**'
           % (ab, N, round(100*ab/N), rab, R, round(100*rab/R)))
out.append('')
out.append('**E（出会っただけで終わる）= 全体 %d/%d（%d%%）、ロマファン %d/%d（%d%%）**'
           % (cnt['E'], N, round(100*cnt['E']/N), rcnt['E'], R, round(100*rcnt['E']/R)))
out.append('')
out.append('E に入った%d本の内訳: %s'
           % (cnt['E'], '／'.join(names[i] for i in sorted(CLS) if CLS[i]=='E')))

# 追記ではなく置換する。二度走らせても同じ結果になるようにする。
MARK = '\n---\n\n## 集計（この節は'
src = io.open(DOC, encoding='utf-8').read()
i = src.find(MARK)
assert src.count(MARK) <= 1, '集計節が二つある'
if i >= 0:
    j = src.find('\n---\n', i + len(MARK))   # 集計節の直後の区切り
    assert j > 0, '集計節の終わりが見つからない'
    src = src[:i] + '\n'.join(out) + '\n' + src[j:]
else:
    src = src.rstrip('\n') + '\n' + '\n'.join(out) + '\n'
io.open(DOC, 'w', encoding='utf-8').write(src)
print('\n'.join(out))
