#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试（2026-11-21《261118 装修最后那点细节》反馈单 新1、新8）：不连达芬奇、不跑真 RVM（用现造的“人形”遮罩）、不碰 NAS 片子。
新1（Q166 复发）：出任何片段之前按人像量头顶空间，放不下的人挡字全部列出、停下，建议改人前口播大字，不出半截产物；
新8：aroll-render 同一遍出的 A肉 和 A肉_人像下移<值> 有先后，不算旧（按批次标记，不按修改时间）；没有标记的老产物照旧按修改时间。"""
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
RW, RH = 240, 320


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


def person(top, W=2160, H=2880, hw=.37, s=4):
    """“人形”遮罩（同 test_occlusion_step_cards.py）：top＝头顶 y（4K）。返回 1/s 尺寸的 0/255 图。"""
    k = W / 2160; im = Image.new('L', (W // s, H // s), 0); d = ImageDraw.Draw(im); cx = W / 2 / s; a = W * hw / 2 / s; hh = 1000 * k / s; t = top * k / s
    d.rounded_rectangle((cx - a, t, cx + a, t + hh), radius=max(1, int(a * .7)), fill=255); d.rectangle((cx - a * .45, t + hh * .8, cx + a * .45, t + hh * 1.15), fill=255)
    d.rounded_rectangle((cx - W * .42 / s, t + hh * 1.05, cx + W * .42 / s, H / s + 50), radius=max(1, int(200 * k / s)), fill=255)
    return np.asarray(im)


def fake_person(top_of, calls):
    """top_of(start 帧) → 头顶 y（4K）：不同时刻头顶高低不同。"""
    def m(src, start, n, W, H, work_dir):
        a = np.asarray(Image.fromarray(person(top_of(start), 2160, 2880, .36, 9)).resize((W, H))); calls.append(start)
        low = np.stack([np.asarray(Image.fromarray(a).resize((W // 2, H // 2), Image.BOX))] * n)
        return rendang.Matte(low, lambda: (a for _ in range(n)))
    return m


def film(t, name, items, job=None, secs=12.0):
    d = Path(t) / name; (d / '合成').mkdir(parents=True); n = round(secs * FR)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f"color=c=gray:s={RW}x{RH}:r=30000/1001", '-frames:v', str(n), '-c:v', 'libx264', '-qp', '0',
                    '-pix_fmt', 'yuv420p', str(d / '合成' / 'A肉.mov')], check=True, stdin=subprocess.DEVNULL)
    wj(d / 'job.json', dict(dict(format='3:4', fps='30000/1001', nav_h=224, 人挡字间隔秒=.3), **(job or {})))
    wj(d / '口播字时间.json', dict(tokens=[dict(c=c, t0=.5 + i * .5, t1=.9 + i * .5) for i, c in enumerate('一二三四五六七八九十')]))
    wj(d / '分镜.json', dict(anchors={}, shots=[dict(id='F_0', kind='F', a=0.0, b=secs)]))
    wj(d / '花字.json', list(items)); return d


def run(d, only=None):
    try: return huazi.render_list(d, only=only), ''
    except SystemExit as e: return None, str(e)


def rd(at, until, text='设计经验', **k): return dict(use='人挡字', at=at, until=until, text=text, 风格=2, **k)


# —— 新1：出片前预查，放不下的全部列出、停下 ——
with tempfile.TemporaryDirectory() as t:
    calls = []
    # 1–3.6 秒头顶 700（放得下），7 秒以后头顶 250（贴上沿）。普通花字至少占 2.2 秒（进＋最短停留＋出），和人挡字不能重叠，所以排在 4–6.2 秒
    rendang.MATTE = fake_person(lambda s: 700 if s < round(4.5 * FR) else 250, calls)
    items = [rd(1.0, 3.6), dict(use='number', at=4.0, until=4.6, value='3', unit='次', label='跑现场'), rd(7.0, 9.6, text='补不回来'), rd(10.5, 13.0, text='再也做不到')]
    d = film(t, '预查', items, secs=14.0); res, e = run(d)
    lines = [x for x in e.splitlines() if x.strip().startswith('第')]
    head = int(lines[0].split('头顶离画面上沿 ')[1].split(' ')[0]) if lines and '头顶离画面上沿 ' in lines[0] else -1
    check(res is None and '有 2 条人挡字放不下' in e and len(lines) == 2 and '第 3 条“补不回来”' in lines[0] and '第 4 条“再也做不到”' in lines[1]
          and '7.00–9.60 秒' in lines[0] and abs(head - 250) <= 20 and '才放得下' in lines[0] and '口播大字' in e and 'koubo' in e
          and e.count('试过的放法：') == 2 and '默认（头顶）' in e,
          f'两条人挡字头顶贴上沿：出片前一次列出两条（第几条、时间、字、头顶离上沿多少（量到 {head}，实际 250）、要多少、试过哪些放法），建议改人前口播大字 koubo'
          + ('' if res is None else '：没拦下'))
    out = d / '合成' / '花字'
    check(not list(out.glob('*.mov')) and not (out / '花字表.json').exists(), '放不下时一个片段都不出（放得下的第 1 条、普通花字也不出），不写花字表')
    check('人挡字风格' not in rj(d / 'job.json') or rj(d / 'job.json').get('人挡字风格') == 2, '预查不改 job.json')
    need = int(lines[0].split('要约 ')[1].split(' ')[0]) if lines and '要约 ' in lines[0] else 0
    check(need > 250, f'估出要多少头顶空间：要约 {need} 像素（比现在的 250 多）')
    # 改掉放不下的两条 → 照常出，每条只抠一次（预查抠好的出片直接用）
    wj(d / '花字.json', items[:2]); calls.clear(); res, e = run(d)
    check(res and len(calls) == 1 and res[0][0]['遮挡']['通过'], f'只剩放得下的：照常出，预查抠的遮罩出片直接用，不抠第二遍（抠了 {len(calls)} 次）' + ('' if res else '：' + e))
    calls.clear(); run(d); check(len(calls) == 0, '什么都没改再跑：沿用上次的片段，预查也不再抠')
    calls.clear(); run(d, only={1}); check(len(calls) == 1, '--only 指定重出这一条：预查它并出片，只抠一次')
    # 2026-11-22 试验田查出：口播画面重出过（比人挡字片段新）就不沿用，和交审自检、建审核线同一条规则，不再绕不出去
    clip = next(out.glob('01_*.mov')); base = d / '合成' / 'A肉.mov'; earlier = base.stat().st_mtime - 30
    os.utime(clip, (earlier, earlier)); calls.clear(); run(d)      # 片段比口播画面早 30 秒＝口播画面后来重出过
    check(len(calls) == 1 and next(out.glob('01_*.mov')).stat().st_mtime > base.stat().st_mtime,
          f'口播画面比人挡字片段新（剪口播改过、aroll-render 重出过）：不沿用、重出这一条（抠了 {len(calls)} 次）；之后自检不会再说它旧')
    from thinkcut import check as dcheck
    stale = [x for x in dcheck.rendang_problems(Job(d)) if '比这段人挡字新' in x]
    check(not stale, '重出之后交审自检（建审核线也用它）不再报“口播画面比这段人挡字新”' + ('' if not stale else '：' + stale[0]))
rendang.MATTE = rendang.rvm

# —— 新8：同一遍渲染的产物不算旧 ——
with tempfile.TemporaryDirectory() as t:
    t = Path(t); src = t / '源.mov'; ar = t / 'aroll'; (ar / '审核').mkdir(parents=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:s=216x384:r=30000/1001', '-f', 'lavfi', '-i', 'sine=frequency=300:sample_rate=48000',
                    '-frames:v', '8', '-t', str(8 / FR), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'pcm_s16le', str(src)], check=True, stdin=subprocess.DEVNULL)
    wj(ar / '审核' / 'trial_keep_segments.json', dict(segments=[dict(src_in=0, src_out=6, tl_in=0)]))
    F = t / '片'; F.mkdir()
    wj(F / 'job.json', dict(format='3:4', fps='30000/1001', aroll=str(ar), source=str(src), 人像下移=230)); aroll.render(Job(F))
    mk = F / '合成' / aroll.BATCH; A0, A1 = F / '合成' / 'A肉.mov', F / '合成' / 'A肉_人像下移230.mov'
    rec = rj(mk) if mk.exists() else {}
    check(aroll.BATCH == rendang.BATCH and set(rec.get('文件', {})) == {'A肉.mov', 'A肉_人像下移230.mov'} and rendang.same_batch(F, A0, A1),
          'aroll-render 写人像下移时：同一遍出的两份记进批次标记，人挡字认得是同一批')
    wj(F / 'job.json', dict(format='3:4', fps='30000/1001', aroll=str(ar), source=str(src))); aroll.render(Job(F))
    check(set(rj(mk)['文件']) == {'A肉.mov'} and not rendang.same_batch(F, A0, A1), '之后只出 A肉（没写人像下移）：标记只记 A肉，旧的下移那份不再算同一批')

with tempfile.TemporaryDirectory() as t:
    calls = []; rendang.MATTE = fake_person(lambda s: 700, calls)
    d = film(t, '批次', [rd(1.0, 3.6)], job={'人像下移': 230})
    A0, A1 = d / '合成' / 'A肉.mov', d / '合成' / 'A肉_人像下移230.mov'; subprocess.run(['cp', str(A0), str(A1)], check=True)
    now = time.time(); os.utime(A1, (now - 41, now - 41)); os.utime(A0, (now, now))      # 261118：下移那份早写完 41 秒
    _, e = run(d); check('比 A肉.mov 旧' in e, '没有批次标记的老产物：仍按修改时间判，下移那份早 41 秒就停下（老行为不变）')
    wj(d / '合成' / rendang.BATCH, dict(批次='x', 文件={p.name: [p.stat().st_size, int(p.stat().st_mtime)] for p in (A0, A1)}))
    res, e = run(d); check(res and '旧' not in e, '同一遍出的（批次标记对得上）：下移那份早 41 秒也不算旧，直接出人挡字' + ('' if res else '：' + e))
    os.utime(A0, (now + 100, now + 100)); _, e = run(d)
    check('比 A肉.mov 旧' in e, '之后 A肉 又变了（和标记对不上）：照旧停下叫重跑 aroll-render')
rendang.MATTE = rendang.rvm

print('人挡字预查/批次测试全部通过' if not fails else f'人挡字预查/批次测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
