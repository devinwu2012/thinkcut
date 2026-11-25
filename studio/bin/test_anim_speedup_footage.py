#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试（2026-11-21《别墅装灯》反馈单 新2、新3、新14、新15、新16）：不开 Chrome、不开 Blender、不连达芬奇、不碰 NAS 片子。
查：标签自检新做法（并行、粗查＋逐帧、缓存、跳过实拍、盖实拍不查）和旧的单路逐帧做法给出同样的重叠帧（含故意压模型的用例）；
anim-render 默认半尺寸登记 50%（--4k 才出 4K）、几段同时出；样张拼图弹出＋“请看哪里、确认什么”；分镜规格的 实拍/图片/盖实拍 编译、总装、登记；标签直上直下（Blender 选位、HyperFrames 组件）。
截图用假的 cap（按时刻画一张合成画面），不启动浏览器。"""
import json, os, subprocess, sys, tempfile, threading, time, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'
sys.path.insert(0, str(SK / 'scripts')); sys.path.insert(0, str(SK / 'assets/包装系统'))
os.environ['THINKCUT_SKILL'] = str(SK)
from thinkcut.common import Job, wj, rj   # noqa: E402
from thinkcut import anim, beats          # noqa: E402
import biaoqian                       # noqa: E402
import numpy as np                    # noqa: E402
from PIL import Image, ImageDraw      # noqa: E402
fails = []


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


# —— 旧的单路逐帧标签自检（2026-11-12 起到 11-21 的 anim.label_check 原样，只去掉写文件和停下），只在这里当对照 ——
def old_label_check(job, step=1, w=1080, shots=None, chunk=100):
    import shutil
    anim.build(job); fps = float(job.fps); tmp = Path(tempfile.mkdtemp(prefix='thinkcut_lab_old_')); bad = {}; n_all = 0
    try:
        for s in anim.anim_shots(job):
            if shots and s['id'] not in shots: continue
            a, b = round(s['a'] * fps), round(s['b'] * fps)
            ts = [f'{max(i / fps, s["a"] + 1e-4):.5f}' for i in range(a, b, step)]; n_all += len(ts)
            for k in range(0, len(ts), chunk):
                part = ts[k:k + chunk]
                for q, tag in ((anim._q(job), 'L'), (anim._q(job, 'clean=1'), 'C')):
                    anim._run_group(anim.cap(job) + ['--q', q, '--w', str(w), '--t', ','.join(part), '--out', str(tmp / f"{s['id']}{tag}"), '--frame-timeout', '30'],
                                    timeout=60 + 2 * len(part), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for t in part:
                    L = Image.open(tmp / f"{s['id']}L_{t}.jpg").convert('RGB'); C = Image.open(tmp / f"{s['id']}C_{t}.jpg").convert('RGB')
                    txt = np.abs(np.asarray(L, np.int16) - np.asarray(C, np.int16)).sum(axis=2) > 90
                    if txt.sum() < 50: continue
                    m = biaoqian.model_mask(C); hit = float((txt & m).sum()) / txt.sum()
                    if hit > 0.08: bad.setdefault(s['id'], []).append(float(t))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dict(查了帧数=n_all, 重叠帧数=sum(len(v) for v in bad.values()), 镜头=bad)


# —— 假的 cap：按时刻画一帧（暗底、蓝色“模型”、白色“标签”），clean=1 不画标签 ——
def scene(t, clean, w):
    h = w * 4 // 3; k = w / 360; im = Image.new('RGB', (w, h), (22, 24, 29)); d = ImageDraw.Draw(im)
    R = lambda *r: [round(v * k) for v in r]
    BLUE = (60, 140, 230)
    if 1.0 <= t < 4.0:                       # B01：模型、标签都不动，标签放在旁边 → 0 帧
        d.rectangle(R(40, 200, 160, 320), fill=BLUE); lab = R(200, 100, 300, 130) if 1.5 <= t < 3.8 else None
    elif 5.0 <= t < 9.0:                     # B02：模型很快横穿过一个不动的标签（只压几帧，起点不在粗查的帧上）
        x0 = -100 + 300 * (t - 5.0); d.rectangle(R(x0, 230, x0 + 30, 330), fill=BLUE); lab = R(200, 250, 300, 280)
    elif 10.0 <= t < 14.0:                   # B03：故意压模型——标签整段压在不动的模型上（11.53 秒起，不对齐粗查）
        d.rectangle(R(100, 200, 260, 330), fill=BLUE); lab = R(120, 240, 220, 270) if 11.53 <= t < 12.71 else None
    elif 15.0 <= t < 17.0:                   # R04：实拍镜头（这里故意也压）：新做法不查
        d.rectangle(R(100, 200, 260, 330), fill=BLUE); lab = R(120, 240, 220, 270)
    elif 18.0 <= t < 21.0:                   # B05：压模型 18.5–20.5 秒，其中 19–20 秒盖了实拍
        d.rectangle(R(100, 200, 260, 330), fill=BLUE); lab = R(120, 240, 220, 270) if 18.5 <= t < 20.5 else None
    else:
        lab = None
    if lab and not clean: d.rectangle(lab, fill=(255, 255, 255))
    return im


CALLS = dict(frames=0, launches=0, live=0, peak=0); LOCK = threading.Lock()
def fake_run_group(cmd, timeout, **kw):
    cmd = [str(c) for c in cmd]; g = lambda k: cmd[cmd.index(k) + 1]
    with LOCK: CALLS['launches'] += 1; CALLS['live'] += 1; CALLS['peak'] = max(CALLS['peak'], CALLS['live'])
    try:
        if '--t' in cmd:
            clean = 'clean=1' in g('--q'); w = int(g('--w'))
            for t in g('--t').split(','):
                scene(float(t), clean, w).save(f"{g('--out')}_{t}.jpg", quality=90)
            with LOCK: CALLS['frames'] += len(g('--t').split(','))
        time.sleep(0.02)
    finally:
        with LOCK: CALLS['live'] -= 1


def film(d, shots, spec_shots=None):
    d.mkdir(parents=True, exist_ok=True)
    wj(d / 'job.json', dict(fps='30000/1001', format='3:4', frames=700, aroll='', source='', project='', main_timeline='', srt=''))
    wj(d / '分镜.json', dict(anchors={'x': 1.0}, shots=shots)); (d / '动画').mkdir(exist_ok=True)
    if spec_shots: wj(d / '分镜规格.json', dict(shots=spec_shots))
    return Job(d)


SH = [dict(id='B01', kind='B', a=1.0, b=4.0), dict(id='B02', kind='B', a=5.0, b=9.0), dict(id='B03', kind='B', a=10.0, b=14.0),
      dict(id='R04', kind='B', a=15.0, b=17.0), dict(id='B05', kind='B', a=18.0, b=21.0)]
build0, rg0 = anim.build, anim._run_group
anim.build = lambda job, relayout=None: None; anim._run_group = fake_run_group
try:
    with tempfile.TemporaryDirectory() as t:
        T = Path(t)
        # ① 新旧同一批镜头（不盖实拍）给出同样的重叠帧
        job = film(T / '甲', SH)
        old = old_label_check(job, w=360); n_old = CALLS['frames']; CALLS.update(frames=0, launches=0, peak=0)
        try: anim.label_check(job, w=360, n_workers=6); new = rj(job.dir / '动画/自查/标签重叠.json')
        except SystemExit: new = rj(job.dir / '动画/自查/标签重叠.json')
        n_new = CALLS['frames']; peak = CALLS['peak']
        same = {k: v for k, v in old['镜头'].items() if k != 'R04'} == new['镜头']
        check(same and len(new['镜头'].get('B02', [])) >= 3 and len(new['镜头'].get('B03', [])) > 30 and 'B01' not in new['镜头'],
              f"标签自检新做法和旧的单路逐帧对同一批镜头给出相同的重叠帧（B02 模型快速横穿只压 {len(new['镜头'].get('B02', []))} 帧、B03 故意压模型 {len(new['镜头'].get('B03', []))} 帧都查到，B01 0 帧）")
        check(n_new < n_old * 0.6 and peak > 1, f'粗查＋只补查有动静的区间：截图从 {n_old} 帧降到 {n_new} 帧；几个 Chrome 同时截（最多同时 {peak} 个）')
        check('R04' in old['镜头'] and 'R04' not in new['镜头'] and 'R04' in new['不查'], 'R 开头的实拍镜头：旧做法白查（还会误报），新做法不查')
        check(new['重叠帧数'] == sum(len(v) for v in new['镜头'].values()) and anim.label_overlap(job)[0], '结果写进 动画/自查/标签重叠.json，check/review 照样认出有压字')
        # ② 逐帧（step=1）和粗查结果一样
        CALLS.update(frames=0); (job.dir / '动画/自查/标签自检缓存.json').unlink()
        try: anim.label_check(job, step=1, w=360, n_workers=4)
        except SystemExit: pass
        r1 = rj(job.dir / '动画/自查/标签重叠.json')
        check(r1['镜头'] == new['镜头'] and CALLS['frames'] == 2 * r1['查了帧数'],
              '--step 1（全部逐帧，跳过实拍镜头）和粗查＋补查结果一样')
        # ③ 缓存：什么都没改再跑，一帧不截；只改一个镜头的起止，只重查这一个
        CALLS.update(frames=0)
        try: anim.label_check(job, w=360)
        except SystemExit: pass
        n_again = CALLS['frames']
        B = rj(job.dir / '分镜.json'); B['shots'][0]['b'] = 4.2; wj(job.dir / '分镜.json', B); CALLS.update(frames=0)
        try: anim.label_check(job, w=360)
        except SystemExit: pass
        c = rj(job.dir / '动画/自查/标签自检缓存.json')
        check(n_again == 0 and 0 < CALLS['frames'] <= round(3.2 * 29.97) and c['B01']['帧数'] == round(4.2 * 29.97) - round(29.97),
              f'按镜头指纹缓存：没改过再跑截 0 帧；只改 B01 起止只重查 B01（截了 {CALLS["frames"]} 帧）')
        # ④ 盖实拍的区间不查（新15）
        sh5 = [dict(s) for s in SH]; sh5[4]['盖实拍'] = [dict(a=19.0, b=20.0, 文件='x.mp4', 入点=0.0)]
        job2 = film(T / '乙', sh5); old2 = old_label_check(job2, w=360)
        try: anim.label_check(job2, w=360)
        except SystemExit: pass
        r2 = rj(job2.dir / '动画/自查/标签重叠.json'); fps = 30000 / 1001
        cut = [x for x in old2['镜头']['B05'] if not (round(19.0 * fps) <= round(x * fps) < round(20.0 * fps))]
        check(r2['镜头']['B05'] == cut and len(cut) < len(old2['镜头']['B05']) and r2['盖实拍不查帧数'] == round(20 * fps) - round(19 * fps),
              f"盖了实拍的 19–20 秒不查（B05 旧做法 {len(old2['镜头']['B05'])} 帧 → 新做法 {len(cut)} 帧，正好去掉被盖住的那些）")
        # ⑤ 只查一个镜头：其余镜头没缓存的列进“没查”，check 提示要重跑
        job3 = film(T / '丙', SH[:2])
        anim.label_check(job3, w=360, shots=['B01'])
        r3 = rj(job3.dir / '动画/自查/标签重叠.json'); hint = anim.label_overlap(job3)[1] or ''
        check(r3['没查'] == ['B02'] and 'B02' in hint and r3['重叠帧数'] == 0, '只查 B01：B02 没查过就记进“没查”，check 提示交审前重跑')
finally:
    anim.build, anim._run_group = build0, rg0

# —— anim-render：并行分段、默认登记 50%（2026-11-21 Q159 复发：不带参数就是 1080P），--4k 才出 4K（假的出段，不开 Chrome）——
cs0, req0, asm0, ff0 = anim._cap_segment, anim.require, anim.assemble, anim._first_frame_check
seen = dict(live=0, peak=0, w=set()); L2 = threading.Lock()
def fake_seg(job, a, b, out, w, **kw):
    with L2: seen['live'] += 1; seen['peak'] = max(seen['peak'], seen['live']); seen['w'].add(w)
    h = round(w * 4 / 3) // 2 * 2
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c=green:s={w // 8 * 2}x{h // 8 * 2}:r=30000/1001', '-frames:v', str(b - a),
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(out)], check=True, stdin=subprocess.DEVNULL)
    time.sleep(0.1)
    with L2: seen['live'] -= 1
anim._cap_segment = fake_seg; anim.require = lambda *a, **k: None; anim.assemble = lambda job, out=None: None; anim._first_frame_check = lambda p: None; anim.build = lambda job, relayout=None: None
try:
    with tempfile.TemporaryDirectory() as t:
        job = film(Path(t) / '片', [dict(id='B01', kind='B', a=1.0, b=4.0), dict(id='R02', kind='B', a=4.0, b=5.0), dict(id='B03', kind='B', a=5.0, b=6.5)])
        anim.render(job, n_workers=4)
        reg = rj(job.dir / '合成/动画清单.json')['shots']; rs = rj(job.dir / '合成/渲染尺寸.json')['items']
        check(set(reg) == {'B01', 'B03'} and reg['B01']['file'].endswith('B01_预览50.mp4') and reg['B01']['scale'] == 50 and seen['w'] == {1080}
              and all(i['scale'] == 50 and i['w'] == 1080 and i['h'] == 1440 for i in rs) and anim._ffprobe_frames(reg['B01']['file']) == round(4 * 30000 / 1001) - round(30000 / 1001)
              and seen['peak'] > 1 and len(anim.render_size_problems(job)) == 2,
              f"anim-render 不带参数：1080 宽、出成 <镜头>_预览50.mp4、登记 50%（导出前被拦 2 个镜头）；分段同时出（最多 {seen['peak']} 段同时）；实拍镜头 R02 不渲")
        probs = anim.render_size_problems(job)
        check(len(probs) == 2 and all('--4k' in x for x in probs) and 'anim-render' in probs[0], 'export 拦预览尺寸时给出每个镜头出 4K 的命令（--4k）')
        seen['w'] = set(); anim.render(job, preview=False)
        reg = rj(job.dir / '合成/动画清单.json')['shots']
        check(seen['w'] == {2160} and reg['B01']['file'].endswith('/B01.mp4') and reg['B01']['scale'] == 100 and anim.render_size_problems(job) == []
              and (job.dir / '动画/出片/B01_预览50.mp4').exists(), '导出前 --4k 重渲 4K（也并行）：登记换成 100%、导出不再拦，预览文件不动')
finally:
    anim._cap_segment, anim.require, anim.assemble, anim._first_frame_check, anim.build = cs0, req0, asm0, ff0, build0

# 命令行：anim-render 不带参数＝1080P，--4k 才是 4K；HF、Blender 出片.py 不带参数 scale=50，--4k=100（Q159 复发）
r = subprocess.run([sys.executable, '-B', str(SK / 'scripts/thinkcut_edit.py'), 'anim-render', '-h'], capture_output=True, text=True)
hfm = load(SK / 'assets/hyperframes_template/出片.py', 'hf_args'); blm = load(SK / 'assets/blender_template/出片.py', 'bl_args'); argv0 = sys.argv
try:
    got = []
    for m in (hfm, blm):
        for extra in ([], ['--4k']):
            sys.argv = ['出片.py', '--shot', 'A01'] + extra; x = m.args(); got.append(100 if x.full4k else x.scale)
finally: sys.argv = argv0
check(r.returncode == 0 and '--4k' in r.stdout and '1080P' in r.stdout and got == [50, 100, 50, 100],
      f'不带参数默认 1080P：anim-render 有 --4k、HyperFrames 和 Blender 出片.py 默认 scale 50、--4k 才 100（{got}）')

# —— 样张拼图（2026-11-21 新4）：拼成一张图、在“预览”里弹出（THINKCUT_NO_OPEN=1 不弹）、打印“请看哪里、确认什么” ——
os.environ['THINKCUT_NO_OPEN'] = '1'
with tempfile.TemporaryDirectory() as t:
    T = Path(t)
    job = film(T / '片', [dict(id='B01', kind='B', a=1.0, b=4.0), dict(id='B02', kind='B', a=5.0, b=9.0)],
               [dict(id='B01', 讲什么='关系', 口播要点=['马桶和淋浴间挨着放', '换气扇在淋浴间正上方'], 画面='全景看卫生间 → 推到换气扇'), dict(id='B02', 讲什么='过程')])
    imgs = []
    for k, (sid, tt) in enumerate((('B01', 1.5), ('B01', 3.2), ('B02', 6.0))):
        f = T / f'{sid}_{tt}.png'; Image.new('RGB', (1080, 1440), (40 * k, 80, 120)).save(f); imgs.append((sid, tt, f))
    sheet, words = anim.still_sheet(job, imgs, T / '样张' / '拼图.jpg', job.dir / '动画')
    im = Image.open(sheet)
    check(sheet.exists() and im.width > 1080 and im.height > 720 and anim.open_file(sheet) is False
          and '请看 B01（第 1.5、3.2 秒，讲关系）' in words and '全景看卫生间 → 推到换气扇' in words and '“马桶和淋浴间挨着放”；“换气扇在淋浴间正上方”' in words
          and '请看 B02（第 6 秒，讲过程）' in words and '确认一眼看得懂' in words and '都对就回“可以”' in words,
          '样张拼成一张图；给用户的话按镜头写清看哪几秒、画面应是什么、确认哪几条口播要点（没写要点的镜头给通用一句）；THINKCUT_NO_OPEN=1 时不弹')
    calls = []; run0, plat0 = anim.subprocess.run, anim.sys.platform
    try:
        del os.environ['THINKCUT_NO_OPEN']; anim.sys.platform = 'darwin'
        anim.subprocess.run = lambda cmd, **kw: (calls.append(cmd), type('R', (), {'returncode': 0})())[1]
        opened = anim.open_file(sheet)
    finally: anim.subprocess.run, anim.sys.platform = run0, plat0; os.environ['THINKCUT_NO_OPEN'] = '1'
    check(opened is True and calls == [['open', '-a', 'Preview', str(sheet)]], '在 Mac 上用 open -a Preview 弹出拼图')
    # anim-stills（老代码动画）：半尺寸截、出完自动拼图（假的截图）
    seen_cmd = []
    def fake_cap(cmd):
        seen_cmd.append(cmd); out = cmd[cmd.index('--out') + 1]
        for tt in cmd[cmd.index('--t') + 1].split(','): Image.new('RGB', (1080, 1440)).save(f'{out}_{tt}.jpg')
    run1, b1, rq1 = anim.run, anim.build, anim.require; anim.run = fake_cap; anim.build = lambda job, relayout=None: None; anim.require = lambda *x, **k: None
    try: sheet2, words2 = anim.stills(job, ['1.5', '6'])
    finally: anim.run, anim.build, anim.require = run1, b1, rq1
    check(seen_cmd and seen_cmd[0][seen_cmd[0].index('--w') + 1] == str(job.fmt['anim_w'] // 2) and sheet2 == job.p('合成', '动画抽帧', '样张拼图.jpg') and sheet2.exists()
          and '请看 B01（第 1.5 秒' in words2 and '请看 B02（第 6 秒' in words2, 'anim-stills 样张是半尺寸，出完自动拼图并给出“请看哪里、确认什么”')

# —— 分镜规格：实拍 / 图片 / 盖实拍 ——
tok = [dict(c=c, t0=i * 0.5, t1=i * 0.5 + 0.5) for i, c in enumerate('甲乙丙丁戊己庚辛壬癸子丑')]
spec = dict(anchors=dict(a=['乙'], b=['丁'], c=['己'], d=['辛'], e=['壬']), shots=[
    dict(id='R01', a='a', b='b', 实拍='实拍/试灯.mp4'), dict(id='R02', a='b', b='c', 图片=['a.png', 'b.png'], 铺法='裁满'),
    dict(id='B03', a='c', b='e', 盖实拍=[dict(从='d', 文件='实拍/白天.mp4', 入点=2)])])
g = beats.compile_spec(spec, tok, 6.0); S = {s['id']: s for s in g['shots']}
check(S['R01']['实拍'] == {'文件': '实拍/试灯.mp4', '入点': 0.0} and S['R02']['图片'] == {'文件': ['a.png', 'b.png'], '铺法': '裁满'}
      and S['B03']['盖实拍'] == [dict(a=3.5, b=4.0, 文件='实拍/白天.mp4', 入点=2.0)], 'beats 把 实拍/图片/盖实拍 编进 分镜.json（盖实拍“到”不写就到镜头结尾）')
def bad_spec(sh):
    try: beats.compile_spec(dict(spec, shots=[sh]), tok, 6.0); return ''
    except SystemExit as e: return str(e)
check('不在镜头' in bad_spec(dict(id='B', a='c', b='d', 盖实拍=[dict(从='a', 文件='x.mp4')])) and '重叠' in bad_spec(dict(id='B', a='a', b='e', 盖实拍=[dict(从='b', 到='d', 文件='x'), dict(从='c', 文件='y')]))
      and '同时写' in bad_spec(dict(id='R', a='a', b='b', 实拍='x', 图片='y')) and '铺法' in bad_spec(dict(id='R', a='a', b='b', 图片='y', 铺法='拉伸')),
      '写错时中文报错：盖实拍不在镜头里、两段重叠、实拍图片同时写、铺法不认识')
check(all('实拍' not in s and '盖实拍' not in s for s in beats.compile_spec(json.loads((ROOT / 'samples/烟道反味/分镜规格.json').read_text(encoding='utf-8')),
      rj(ROOT / 'samples/烟道反味/口播字时间.json')['tokens'], 190.123)['shots']), '不写这些字段的老规格，分镜.json 一个字不多')

# —— 总装：实拍/图片镜头自动做片段并登记，盖实拍那几帧换成实拍（真 ffmpeg，4K，短）——
with tempfile.TemporaryDirectory() as t:
    T = Path(t); F = T / '片'; F.mkdir()
    def color(name, c, n, size='540x960'):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={c}:s={size}:r=30000/1001', '-frames:v', str(n), '-c:v', 'libx264', '-preset', 'ultrafast',
                        '-pix_fmt', 'yuv420p', str(name)], check=True, stdin=subprocess.DEVNULL)
    (F / '实拍').mkdir(); color(F / '实拍/红.mp4', 'red', 120); color(F / '实拍/白.mp4', 'white', 60)
    Image.new('RGB', (300, 200), (0, 0, 255)).save(F / '蓝.png'); Image.new('RGB', (200, 300), (255, 255, 0)).save(F / '黄.png')
    wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=90, aroll='', source='', project='', main_timeline='', srt=''))
    wj(F / '分镜.json', dict(anchors={}, shots=[dict(id='F_0', kind='F', a=0, b=0.5), dict(id='R01', kind='B', a=0.5, b=1.0, 实拍={'文件': '实拍/红.mp4', '入点': 1.0}),
        dict(id='R02', kind='B', a=1.0, b=1.6, 图片={'文件': ['蓝.png', '黄.png'], '铺法': '完整'}),
        dict(id='B03', kind='B', a=1.6, b=2.8, 盖实拍=[dict(a=2.0, b=2.4, 文件='实拍/白.mp4', 入点=0.0)]), dict(id='F_4', kind='F', a=2.8, b=3.003)]))
    job = Job(F); color(T / 'B03.mp4', 'green', 36, '1080x1440'); anim.register(job, F / '动画', 'code', ['B03'], 50, {'B03': T / 'B03.mp4'}, None, [1080, 1440])
    rep = anim.assemble(job)
    reg = rj(F / '合成/动画清单.json'); rs = {i['shot']: i for i in rj(F / '合成/渲染尺寸.json')['items']}; fps = 30000 / 1001
    px = lambda i, x=1080, y=1500: Image.frombytes('RGB', (216, 288), anim._frame(F / '合成/B肉.mp4', i, 216, 288)).getpixel((x // 10, y // 10))
    near = lambda p, c: all(abs(a - b) < 40 for a, b in zip(p, c))
    k_cov = round(2.0 * fps) + 2; k_anim = round(1.6 * fps) + 2; k_blue = round(1.0 * fps) + 2; k_yel = round(1.6 * fps) - 3
    check(reg['shots']['R01']['project'] == '实拍镜头' and reg['shots']['R02']['project'] == '实拍镜头' and rs['R01']['scale'] == 100 and rs['R02']['w'] == 2160
          and reg['shots']['B03']['project'] == '动画', '实拍/图片镜头总装时自动做成片段、登记进 动画清单.json 和 渲染尺寸.json（100%），不用手改')
    check(near(px(round(0.5 * fps) + 2), (255, 0, 0)) and near(px(k_blue), (0, 0, 255)) and near(px(k_yel), (255, 255, 0)) and near(px(k_anim), (0, 128, 0))
          and near(px(k_cov), (255, 255, 255)) and near(px(round(2.4 * fps) + 1), (0, 128, 0)) and near(px(k_blue, 1080, 300), (22, 24, 29)),
          '总装画面：R01 是实拍、R02 两张图平分（图外是动画底色）、B03 中间 2.0–2.4 秒换成实拍、前后还是动画')
    check(rep['bad'] == [] and any(f.get('盖实拍') for f in rep['frames'] if f['shot'] == 'B03') and 'B03' in rep['盖实拍'],
          '逐镜头比对：盖实拍那段和实拍片段比、其余和动画比，全部对得上；总装检查.json 记下盖实拍区间')
    m0 = (F / '合成/实拍镜头/R01.mp4').stat().st_mtime; anim.prepare_live(job)
    check((F / '合成/实拍镜头/R01.mp4').stat().st_mtime == m0 and (F / '合成/实拍镜头/B03_盖1.mp4').exists(), '源文件、入点、帧数没变：再总装不重做片段')
    B = rj(F / '分镜.json'); B['shots'][1]['实拍']['入点'] = 3.8; wj(F / '分镜.json', B)
    try: anim.prepare_live(job); e = ''
    except SystemExit as x: e = str(x)
    check('不够' in e and '入点' in e, '实拍片段从入点起不够镜头长度：停下说明，不拿短片凑')

# —— 标签直上直下（新14）——
OUT = load(SK / 'assets/blender_template/出片.py', 'chupian_zs')
fr = Image.new('RGB', (2160, 2880), (22, 24, 29)); ImageDraw.Draw(fr).rectangle((800, 1700, 1400, 2300), fill=(230, 230, 230))
lab = dict(id='灯', text='壁灯', point='p', _a=0, _b=9)
p = OUT.choose_above(fr, lab, (1100, 1750), fr.size, [], [])
rec = dict(points=dict(p=dict(x=1100, y=1750, in_frame=True)))
im, boxes = biaoqian.draw(fr, [lab], rec, {'灯': p}, 1.0); r = boxes['灯']
check(not p['unsure'] and r[0] <= 1100 <= r[2] and r[3] <= 1750 - 100 and biaoqian.check_frame(fr, boxes, [lab]) == [],
      'Blender 标签默认放在目标点正上方：字的左右盖住目标点，引线是一条竖线（不拐弯、不斜），也不压模型')
q = OUT.choose_above(fr, lab, (1100, 470), fr.size, [], [])
check(q['unsure'] and '正上方放不下' in q['why'], '目标点太靠上、正上方放不下：到整个画面找，并标成拿不准出位置样张问用户')
f2 = OUT.choose_above(fr, dict(lab, fixed=dict(region=[100, 500, 700, 700])), (1100, 1750), fr.size, [], [])
check(f2['mode'] == '画面区域', '写了 fixed 的标签照旧按指定位置')
node_js = r'''
const fs = require("fs"); const made = [];
const el = () => { const e = { style: {}, children: [], className: "", textContent: "", classList: { add() {} }, appendChild(c) { this.children.push(c); } }; made.push(e); return e; };
global.window = { THINKCUT: { shot: "B01", a: 2, b: 8, dur: 6, fps: 29.97, W: 1080, H: 1440, k: 1, card: [0, 112, 1080, 1328], anchors: {}, covered: [[2, 3]] } };
global.document = { getElementById: el, createElement: el }; eval(fs.readFileSync(process.argv[1], "utf8")); const D = window.THINKCUT;
const ops = []; const tl = { fromTo(t, a, b, at) { ops.push(["fromTo", at]); }, to(t, a, at) { ops.push(["to", at]); }, set(t, v, at) { ops.push(["set", v.visibility, at]); } };
const g = D.label(tl, { x: 300, y: 700, text: "壁灯", from: 1, to: 4.5 }); let err = "";
try { D.label(tl, { x: 1, y: 1, text: "太短", from: 1, to: 1.2 }); } catch (e) { err = e.message; }
const [ring, line, text] = g.children;
console.log(JSON.stringify({ left: g.style.left, top: g.style.top, line: line.style.height, bottom: text.style.bottom, text: text.textContent, cls: [g.className, ring.className, line.className, text.className], ops, err }));'''
r = subprocess.run(['node', '-e', node_js, str(SK / 'assets/hyperframes_template/共用/基础.js')], capture_output=True, text=True)
try: jr = json.loads(r.stdout)
except ValueError: jr = {}
css = (SK / 'assets/hyperframes_template/共用/基础.css').read_text(encoding='utf-8')
check(jr.get('left') == '300px' and jr.get('top') == '700px' and jr.get('line') == '107px' and jr.get('bottom') == '126px' and jr.get('text') == '壁灯'
      and jr.get('ops') == [['fromTo', 1], ['to', 4.2], ['set', 'hidden', 2], ['set', 'visible', 3]] and '至少' in jr.get('err', '')
      and '.brand-label .text { position: absolute; left: 0; transform: translateX(-50%)' in css,
      'HyperFrames 标签组件 THINKCUT.label：字在目标点正上方居中、一条竖线；盖实拍那段（D.covered）自动藏起来' + ('' if jr else '：' + (r.stdout + r.stderr)[-300:]))
ex = (SK / 'assets/hyperframes_template/镜头/示例.html').read_text(encoding='utf-8')
import re
fr_ = [float(x) for x in re.findall(r'THINKCUT\.label\(tl, \{[^}]*from: ([\d.]+)', ex)]
check(len(fr_) == 2 and fr_[1] - fr_[0] >= 0.4, '模板示例两个标签先后出现（第二个晚 0.4 秒以上）')

print('动画提速与实拍测试全部通过' if not fails else f'动画提速与实拍测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
