#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试（2026-11-21《别墅装灯》反馈单 新10、新11、新19）：不连达芬奇、不跑真 RVM（用现造的“人形”遮罩）、不碰 NAS 片子。
新10 人挡字：人像下移出同取景底片、逐字遮挡不过半、单条 字顶/字宽、头顶紧的取景、签名含风格参数、紧挨算一处；
新11 花字表每条记类型、各类型条数；新19 步骤卡并排样式（新片子默认，老片子不变）。"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'
sys.path.insert(0, str(SK / 'scripts')); sys.path.insert(0, str(SK / 'assets/包装系统'))
from thinkcut.common import Job, wj, rj      # noqa: E402
from thinkcut import aroll                   # noqa: E402
import huazi, rendang                    # noqa: E402
from PIL import Image, ImageDraw         # noqa: E402
fails = []
FR = 30000 / 1001


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


def err_of(fn, *a, **k):
    try: fn(*a, **k); return ''
    except SystemExit as e: return str(e)


def person(top, W=2160, H=2880, hw=.37, s=4):
    """“人形”遮罩：圆角的头（连头发，宽 hw 画宽、高约 1000 4K 像素）＋脖子＋肩膀到底；top＝头顶 y（4K）。返回 1/s 尺寸的 0/255 图。
    头宽、头顶 408 照《别墅装灯》真实抠像量的（头宽 804 像素≈37%）。"""
    k = W / 2160; im = Image.new('L', (W // s, H // s), 0); d = ImageDraw.Draw(im); cx = W / 2 / s; a = W * hw / 2 / s; hh = 1000 * k / s; t = top * k / s
    d.rounded_rectangle((cx - a, t, cx + a, t + hh), radius=max(1, int(a * .7)), fill=255); d.rectangle((cx - a * .45, t + hh * .8, cx + a * .45, t + hh * 1.15), fill=255)
    d.rounded_rectangle((cx - W * .42 / s, t + hh * 1.05, cx + W * .42 / s, H / s + 50), radius=max(1, int(200 * k / s)), fill=255)
    return np.asarray(im)


def A_of(top, n=3): return rendang.analyse(np.stack([person(top)] * n), 2160, 2880)


# —— 新10①：人像下移 → aroll-render 同一遍出同取景的底片 ——
with tempfile.TemporaryDirectory() as t:
    t = Path(t); src = t / '源.mov'; ar = t / 'aroll'; (ar / '审核').mkdir(parents=True)
    # 216×384（9:16）源，每一行亮度＝行号（看得出上下挪了多少）；裁成 3:4 时上下各裁 48 行
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', "color=c=black:s=216x384:r=30000/1001,geq=lum='Y*0.6':cb=128:cr=128",
                    '-f', 'lavfi', '-i', 'sine=frequency=300:sample_rate=48000', '-frames:v', '20', '-t', str(20 / FR), '-c:v', 'libx264', '-qp', '0', '-pix_fmt', 'yuv444p',
                    '-c:a', 'pcm_s16le', str(src)], check=True, stdin=subprocess.DEVNULL)
    wj(ar / '审核' / 'trial_keep_segments.json', dict(segments=[dict(src_in=0, src_out=6, tl_in=0)]))
    F = t / '片'; F.mkdir()
    def mk(**kw): wj(F / 'job.json', dict(format='3:4', fps='30000/1001', aroll=str(ar), source=str(src), **kw)); return Job(F)
    def col(p):    # 第 3 帧中间一列的亮度（4K 行）
        o = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(p), '-vf', 'select=eq(n\\,3),format=gray', '-frames:v', '1', '-f', 'rawvideo', '-'], capture_output=True).stdout
        return np.frombuffer(o, np.uint8).reshape(2880, 2160)[:, 1080].astype(float)
    aroll.render(mk()); g0 = (F / '合成' / 'A肉_拼接.txt').read_text(encoding='utf-8')
    check(g0.endswith('concat=n=1:v=1:a=1[vc][ac];[vc]crop=216:288:0:48,scale=2160:2880:flags=lanczos,setsar=1[vo]') and not list((F / '合成').glob('A肉_人像下移*')),
          '没写 人像下移：滤镜和以前一字不差（老片子 A肉 不变），不多出文件')
    a0 = col(F / '合成' / 'A肉.mov')
    aroll.render(mk(人像下移=230)); sh = F / '合成' / 'A肉_人像下移230.mov'; a1 = col(F / '合成' / 'A肉.mov'); s1 = col(sh)
    d = np.abs(s1[600:2600] - a0[600 - 230:2600 - 230]).mean(); d0 = np.abs(s1[600:2600] - a0[600:2600]).mean()
    pr = rendang.probe(sh)
    check(sh.exists() and np.abs(a1 - a0).mean() < 1 and d < 2.5 and d0 > 8 and (int(pr['width']), int(pr['height'])) == (2160, 2880) and pr['r_frame_rate'] == '30000/1001'
          and int(pr['nb_frames']) == 6, f'写 人像下移 230：A肉.mov 不变，另出 A肉_人像下移230.mov，画面整体下移 230 像素（和下移后对齐差 {d:.1f}，不挪差 {d0:.1f}），4K、同帧率、同帧数')
    aroll.render(mk(人像下移=600)); s2 = col(F / '合成' / 'A肉_人像下移600.mov')
    check(s2[:100].max() < 20 and s2[200:300].mean() < 20 and abs(s2[1000] - a0[400]) < 4, '下移超过源画面上方余量：露出的部分补黑（和达芬奇里一样），其余照挪')
    check('整数' in err_of(aroll.shift_of, {'人像下移': 'abc'}) and '整数' in err_of(aroll.shift_of, {'人像下移': 2.5}) and aroll.shift_of({}) == 0, '人像下移写错（不是整数）：中文停下')
    # 人挡字读同一个值、用同一个文件名
    check(rendang.aroll_of(F, {'人像下移': 230}) == sh and rendang.aroll_of(F, {}).name == 'A肉.mov' and aroll.shifted_name(230) == sh.name,
          '人挡字底片：写了 人像下移 就用 A肉_人像下移<值>.mov（和 aroll-render 出的同名），没写用 A肉.mov')

# —— 新10②③：逐字遮挡、头顶紧、单条 字顶/字宽 ——
st2 = rendang.STYLES[2]
lay = rendang.layout(st2, '设计经验', None, 2160); rendang.set_pos(lay, 2160, (2160 - lay['big']['w']) / 2, 545)
A408 = A_of(408); cs = rendang.char_cover(lay, A408['frames'], 2160, 2880); ok, why = rendang.judge('设计经验', cs, .08)
check(not ok and '超过一半' in why and cs[0] < .1 and cs[3] < .1 and max(cs[1:3]) > .5,
      f'以前的放法（杂志刊名头后放在 545、头顶 408）：整体只挡 {rendang.est_cover(lay, A408, 2160, 2880):.0%}（旧判定 8%–50% 算合格），但中间的字被挡 {max(cs):.0%}，新判定判不合格（{why}）')
r = rendang.place(st2, '设计经验', None, A408, 2160, 2880, floor=250)
ok, why = rendang.judge('设计经验', rendang.char_cover(r[0], A408['frames'], 2160, 2880), .08)
check(ok and r[2] <= .5 and r[0]['big']['top'] >= 250 and r[0]['big']['size'] >= rendang.MIN_SIZE, f'头顶离上沿很近（头顶 y=408、导航条 224）：自动往上挪放下了，每个字都不过半（{r[1]}，{why}）')
e = err_of(rendang.place, st2, '设计经验', None, A_of(250), 2160, 2880, floor=250)
check('放不下' in e and '超过一半' in e and '字顶' in e and '每个字' in e, '头顶贴着导航条、4 个字怎么放都有字挡过一半：停下，写清每种放法每个字挡多少、可以写 字顶/字宽，不硬放')
e = err_of(rendang.place, rendang.STYLES[1], '一二三四五六', None, rendang.analyse(np.stack([person(330, hw=.6)] * 3), 2160, 2880), 2160, 2880)
check('缩字 85%' in e and '小于 200' in e, '缩字不缩到字号 200 以下：放不下就停下说（不悄悄缩成看不清的小字）')
r = rendang.place(st2, '设计经验', None, A_of(700), 2160, 2880, top=450, floor=250)
check(abs(r[0]['big']['top'] - 450) < 1e-6 and '字顶 450' in r[1], '单条写 字顶：大字顶就放在那（4K y=450）')
r1 = rendang.place(st2, '设计经验', None, A_of(700), 2160, 2880); r2 = rendang.place(st2, '设计经验', None, A_of(700), 2160, 2880, width=.96)
check(r2[0]['big']['size'] > r1[0]['big']['size'] and abs(r2[0]['big']['w'] - .96 * 2160) < 60 and '字宽 96%' in r2[1], '单条写 字宽：字按写的宽度出（更大），位置照样自己找')
e = err_of(rendang.place, st2, '设计经验', None, A408, 2160, 2880, top=700)
check('按花字.json 写的“字顶”放不下' in e and '超过一半' in e, '写的 字顶 让字挡过一半：停下说清楚，不自己改位置')
check(rendang.cover_at(np.ones((4, 4)), np.ones((10, 10)), -2, 0) == 1.0 and rendang.cover_at(np.ones((4, 4)), np.zeros((10, 10)), 8, 8) == 0.0
      and rendang.cover_at(np.ones((4, 4)), np.ones((10, 10)), 20, 20) == 0.0, '逐字遮挡：出画的那部分字墨不算（看不见的不算被挡）')

# —— 走 huazi.render_list：小分辨率现造画面＋“人形”假遮罩 ——
RW, RH = 240, 320


def fake_person(top4k, hw=.36, calls=None):
    def m(src, start, n, W, H, work_dir):
        a = np.asarray(Image.fromarray(person(top4k, 2160, 2880, hw, 9)).resize((W, H)))
        if calls is not None: calls.append(start)
        low = np.stack([np.asarray(Image.fromarray(a).resize((W // 2, H // 2), Image.BOX))] * n)
        return rendang.Matte(low, lambda: (a for _ in range(n)))
    return m


def film(t, name, items, job=None, secs=12.0, table=None):
    d = Path(t) / name; (d / '合成').mkdir(parents=True); n = round(secs * FR)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f"color=c=gray:s={RW}x{RH}:r=30000/1001", '-frames:v', str(n), '-c:v', 'libx264', '-qp', '0',
                    '-pix_fmt', 'yuv420p', str(d / '合成' / 'A肉.mov')], check=True, stdin=subprocess.DEVNULL)
    wj(d / 'job.json', dict(dict(format='3:4', fps='30000/1001', nav_h=224), **(job or {})))
    wj(d / '口播字时间.json', dict(tokens=[dict(c=c, t0=.5 + i * .5, t1=.9 + i * .5) for i, c in enumerate('一二三四五六七八九十')]))
    wj(d / '分镜.json', dict(anchors={}, shots=[dict(id='F_0', kind='F', a=0.0, b=secs)]))
    if table is not None: wj(d / '合成' / '花字' / '花字表.json', table)
    wj(d / '花字.json', list(items)); return d


def run(d, only=None):
    try: return huazi.render_list(d, only=only), ''
    except SystemExit as e: return None, str(e)


def rd(at, until, text='设计经验', **k): return dict(use='人挡字', at=at, until=until, text=text, 风格=2, **k)


with tempfile.TemporaryDirectory() as t:
    calls = []; rendang.MATTE = fake_person(408, calls=calls)
    d = film(t, '逐字', [rd(1.0, 3.6)]); res, e = run(d); row = res[0][0] if res else {}
    cv = row.get('遮挡', {})
    check(res and cv.get('通过') and len(cv.get('每字', [])) == 4 and all(v <= .5 for _, v in cv['每字']) and cv['最挡'] == max(v for _, v in cv['每字']) and '被挡最多' in cv['说明'],
          f'人挡字出片后逐帧逐字算遮挡：每个字都不过半才通过，花字表记 每字/最挡/说明（{cv.get("说明", e)}）')
    check(row.get('类型') == '人挡字（人后大字）' and row.get('底片', '').endswith('A肉.mov'), '花字表：人挡字这条类型记“人挡字（人后大字）”，底片记用的哪个文件')
    # 签名：没改不重出；改风格参数、字顶都重出；老签名（没有参数）原样沿用
    n0 = len(calls); run(d); check(len(calls) == n0, '什么都没改：沿用上次的片段，不重出')
    keep = st2['top_min']; st2['top_min'] = 500
    try: run(d)
    finally: st2['top_min'] = keep
    check(len(calls) == n0 + 1, '改了风格参数（top_min）：同名片段自动重出（以前签名不变不重出）')
    side = d / '合成' / '花字' / '01_人挡字.json'; rec = rj(side); rec['签名'] = {k: rec['签名'][k] for k in ('start', 'end', 'text', 'small', 'style')}; wj(side, rec)
    n1 = len(calls); run(d); check(len(calls) == n1, '11-21 以前出的老片段（签名里没有参数）：原样沿用，不因为升级重出')
    rendang.MATTE = fake_person(700, calls=calls); wj(d / '花字.json', [rd(1.0, 3.6, 字顶=450)]); run(d); row = rj(d / '合成/花字/花字表.json')['items'][0]
    check(len(calls) == n1 + 1 and '字顶 450' in row['放法'], '单条加了 字顶：重出，按写的位置放')
    wj(d / '花字.json', [rd(1.0, 3.6, 字顶=100)]); _, e = run(d)
    check('"字顶" 写成了 100' in e and '250' in e, '字顶写到导航条里（nav_h 224 → 最高 250）：停下')
    # 人像下移：用同取景底片；没出就停；比 A肉 旧也停
    d2 = film(t, '下移', [rd(1.0, 3.6)], job={'人像下移': 230}); _, e = run(d2)
    check('A肉_人像下移230.mov' in e and 'aroll-render' in e, 'job.json 写了 人像下移、还没出同取景底片：停下，叫先跑 aroll-render')
    sh = d2 / '合成' / 'A肉_人像下移230.mov'; subprocess.run(['cp', str(d2 / '合成' / 'A肉.mov'), str(sh)], check=True)
    os.utime(sh, (time.time() - 100, time.time() - 100)); _, e = run(d2)
    check('比 A肉.mov 旧' in e, '同取景底片比 A肉.mov 旧（口播画面重出过）：停下')
    os.utime(sh, None); res, e = run(d2)
    check(res and res[0][0]['底片'].endswith('A肉_人像下移230.mov') and rj(d2 / '合成/花字/01_人挡字.json')['签名']['底片'] == sh.name, '写了 人像下移：人挡字从 A肉_人像下移230.mov 出，花字表和签名都记着')
    # 紧挨：算一处、不查间隔；隔 1 秒停下并提示；重叠停下
    d3 = film(t, '紧挨', [rd(1.0, 3.0), rd(3.0, 5.0, text='现场调试')]); res, e = run(d3)
    check(res and len(res[0]) == 2 and res[0][0]['end'] == res[0][1]['start'], f'两处人挡字紧挨（前一条 until＝后一条 at）：不用改 人挡字间隔秒，照出{"" if res else "：" + e}')
    d4 = film(t, '紧挨四处', [rd(.5, 2.0), rd(2.0, 3.5, text='现场调试'), rd(4.0, 5.0, text='一'), rd(5.5, 6.5, text='二')], job={'人挡字间隔秒': .3}); _, e = run(d4)
    check(e == '', '紧挨的两条算一处：4 条里两条紧挨＝3 处，不超上限' + (f'：{e}' if e else ''))
    d5 = film(t, '隔一秒', [rd(1.0, 3.0), rd(4.0, 6.0, text='现场调试')]); _, e = run(d5)
    check('只隔 1.0 秒' in e and '紧挨' in e and '人挡字间隔秒' in e, '两处隔 1 秒：停下，提示让两条紧挨或改 人挡字间隔秒')
    d6 = film(t, '重叠', [rd(1.0, 3.5), rd(3.0, 5.0, text='现场调试')]); _, e = run(d6)
    check('时间重叠' in e, '两处人挡字时间重叠：停下（花字轨上放不上去）')
rendang.MATTE = rendang.rvm

# —— 新11：每条类型、各类型条数 ——
cnt, each = huazi.type_counts(dict(items=[dict(n=1, use='koubo', 类型='口播大字（人前）'), dict(n=2, use='人挡字'), dict(n=3, use='koubo'), dict(n=4, use='steps')]))
check(cnt == {'口播大字（人前）': 2, '人挡字（人后大字）': 1, '步骤卡': 1} and each[1] == (2, '人挡字（人后大字）'), '花字表各类型条数、每条类型（老花字表没记类型的按 use 补）')
check(set(huazi.TYPE) == set(huazi.KIND), '每个组件都有给人看的类型名')
readme = (SK / 'assets/包装系统/README.md').read_text(encoding='utf-8'); head = readme[:readme.index('2026-11-12')]
check('用户怎么说' in head and all(f'`{u}`' in head for u in huazi.KIND) and '人后大字' in head and head.index('人后大字') < head.index('`koubo`'),
      'README 开头有“用户怎么说→用哪个组件”对照表，列全所有组件，人后大字＝人挡字排在前面')

# —— 新19：步骤卡并排 ——
huazi.set_format('3:4')
S4 = [dict(t='水电定点位', _at=0.0), dict(t='吊顶前后试灯', _at=1.0), dict(t='选最好才下单', _at=2.0), dict(t='跑现场3–5次', _at=3.0)]
sp = dict(use='steps', 样式='并排', steps=S4, _at=0.0, _until=6.0)
bb = [huazi.still(sp, t).getchannel('A').point(lambda v: 255 if v > 60 else 0).getbbox() for t in (.6, 1.6, 2.6, 4.0)]
cx = [(b[0] + b[2]) / 2 for b in bb]; ws = [b[2] - b[0] for b in bb]
check(all(abs(c - 1080) < 12 for c in cx) and ws[0] < ws[1] < ws[2] < ws[3] and ws[0] < 520,
      f'并排步骤卡：先只有第 1 步，说到哪步加哪步，每次整排水平居中（宽 {ws}，中心 {[round(c) for c in cx]}），不露后面还有几步')
check(all(abs((b[1] + b[3]) / 2 - huazi.FMT['koubo']) < 40 for b in bb) and bb[3][0] >= huazi.SAFE['left'] - 20 and bb[3][2] <= huazi.SAFE['right'] + 20,
      '并排步骤卡默认在胸口高度（同口播大字），全出完也在安全区里')
L = dict(use='steps', steps=[dict(t='一拽'), dict(t='取下'), dict(t='一卡')], _at=0.0, _until=4.0)
check(huazi.still(L, 2).tobytes() == huazi.still(dict(L, 样式='连线'), 2).tobytes() and huazi.still(L, 2).tobytes() != huazi.still(dict(L, 样式='并排'), 2).tobytes(),
      '库里直接调、没写样式：还是老的连线样式（老样张、老片子一个像素不变）')
long = dict(use='steps', 样式='并排', steps=[dict(t=f'第{i}步写得很长很长的', _at=i * .5) for i in range(6)], _at=0.0, _until=5.0)
b = huazi.still(long, 4.5).getchannel('A').getbbox(); check(b[0] >= huazi.SAFE['left'] - 20 and b[2] <= huazi.SAFE['right'] + 20, '步骤多、字长：整排缩进安全区')
huazi.set_format('16:9'); b = huazi.still(dict(sp), 4.0).getchannel('A').point(lambda v: 255 if v > 60 else 0).getbbox(); huazi.set_format('3:4')
check(abs((b[0] + b[2]) / 2 - 1920) < 15, '16:9 并排步骤卡也水平居中')
with tempfile.TemporaryDirectory() as t:
    st = dict(use='steps', at='一', until='九', steps=[dict(t='甲', at='一'), dict(t='乙', at='三')])
    d = film(t, '新片', [st]); res, e = run(d); tb = rj(d / '合成/花字/花字表.json')
    check(res and tb['items'][0]['样式'] == '并排' and tb['步骤卡默认'] == '并排' and tb['items'][0]['类型'] == '步骤卡', '新片子（还没出过花字）：步骤卡默认并排，花字表记下 步骤卡默认')
    run(d); check(rj(d / '合成/花字/花字表.json')['items'][0]['样式'] == '并排', '新片子重跑：仍是并排')
    d = film(t, '老片', [st], table=dict(items=[], warnings=[])); res, e = run(d); tb = rj(d / '合成/花字/花字表.json')
    check(res and tb['items'][0]['样式'] == '连线' and tb['步骤卡默认'] == '连线', '老片子（花字表里没记 步骤卡默认）：步骤卡仍按连线出，重出不变样')
    d = film(t, '老片手写', [dict(st, 样式='并排')], table=dict(items=[], warnings=[])); res, e = run(d)
    check(res and rj(d / '合成/花字/花字表.json')['items'][0]['样式'] == '并排', '单条写了 "样式" 优先')
    d = film(t, '写错', [dict(st, 样式='横排')]); _, e = run(d); check('"样式" 写成了 “横排”' in e, '样式写错：停下列出可用的')
print('人挡字/步骤卡测试全部通过' if not fails else f'人挡字/步骤卡测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
