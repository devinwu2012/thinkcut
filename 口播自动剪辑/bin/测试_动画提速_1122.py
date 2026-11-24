#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试（2026-11-22 动画提速，试验田 261118 副本上实测）：不启动 HyperFrames / Chrome（用假程序），本机临时目录用测试自己的文件夹。
1 HyperFrames 样张：一个镜头的几个时刻开一次浏览器截完（snapshot --at 多个时刻 --json），按它报的时刻对文件，不靠文件名和顺序；少了、没给结果就停。
2 没采用的（实测结果会变）：镜头之间同时出片（不关 HyperFrames 自己的出片排队、不指定 --workers）、--variables 传参。"""
import contextlib, importlib.util, io, json, os, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'; A = SK / 'assets'
TMP = Path(tempfile.mkdtemp(prefix='动画提速_')).resolve(); LT = TMP / '本机临时'
os.environ['THINKCUT_SKILL'] = str(SK); os.environ['THINKCUT_NO_OPEN'] = '1'; os.environ['THINKCUT_LOCAL_TMP'] = str(LT)
sys.path.insert(0, str(SK / 'scripts'))
from thinkcut import anim                    # noqa: E402
from thinkcut.common import Job, wj          # noqa: E402
from PIL import Image                    # noqa: E402
fails = []


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


def stops(fn, *a, **k):
    try:
        with contextlib.redirect_stdout(io.StringIO()): fn(*a, **k)
    except SystemExit as e: return str(e.code)
    return None


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def left(d): return sorted(str(f.relative_to(d)) for f in Path(d).rglob('*') if f.is_file()) if Path(d).exists() else []


film = TMP / 'HF片'; film.mkdir()
wj(film / 'job.json', dict(fps='30', format='3:4', frames=600, project='p', main_timeline='v1', source='/x'))
wj(film / '分镜.json', dict(anchors={}, shots=[dict(id='B01', kind='B', a=1.0, b=9.0), dict(id='B02', kind='B', a=9.0, b=15.0)]))
wj(film / '分镜规格.json', dict(shots=[dict(id=s, 工程='HF_试', 讲什么='过程', 口播要点=['一句']) for s in ('B01', 'B02')]))
with contextlib.redirect_stdout(io.StringIO()): anim.hf_new(Job(film), '试')
proj = film / 'HF_试'
for s in ('B01', 'B02'): shutil.copy(proj / '镜头' / '示例.html', proj / '镜头' / f'{s}.html')
HF = load(proj / '出片.py', 'hf_1122'); calls = []
COLOR = lambda t: (int(round(t * 10)) % 256, 40, 90)      # 每个时刻一种颜色，用来核对文件对没对上时刻


def fake(mode):
    def run(cmd):
        o = Path(cmd[cmd.index('-o') + 1]); calls.append(cmd); (o / 'sub').mkdir(parents=True, exist_ok=True); fr = []
        ts = cmd[cmd.index('--at') + 1].split(',')
        for k, t in enumerate(ts):      # 文件名故意和时刻顺序反着来，报的顺序也反着
            f = o / 'sub' / f'frame-{len(ts) - 1 - k:02d}.png'; Image.new('RGB', (6, 8), COLOR(float(t))).save(f); fr.append({'path': str(f), 'time': float(t)})
        if mode == 'lose': fr = fr[1:]
        if mode == 'junk': return 'Error: Chrome 没起来'
        return json.dumps({'ok': True, 'frames': fr[::-1]})
    return run


def still(times, mode='ok', shot='B01,B02'):
    calls.clear(); shutil.rmtree(proj / '样张', ignore_errors=True)
    keep = (HF.hf, HF.run, HF.preflight, anim.require, anim.still_sheet, sys.argv)
    try:
        HF.hf = lambda: 'hyperframes'; HF.run = fake(mode); HF.preflight = lambda exe, sid: None; anim.require = lambda *a, **k: None; anim.still_sheet = lambda *a, **k: None
        sys.argv = ['出片.py', '--样张', times, '--shot', shot]; return stops(HF.main)
    finally: HF.hf, HF.run, HF.preflight, anim.require, anim.still_sheet, sys.argv = keep


msg = still('2.0,5.5,3.25,10.0,30.0')
got = {f.name: Image.open(f).getpixel((0, 0)) for f in sorted((proj / '样张').glob('B0*.png'))}
want = {'B01_2.00.png': COLOR(1.0), 'B01_5.50.png': COLOR(4.5), 'B01_3.25.png': COLOR(2.25), 'B02_10.00.png': COLOR(1.0)}
check(msg is None and len(calls) == 2 and [c[c.index('--at') + 1] for c in calls] == ['1.000,4.500,2.250', '1.000']
      and all('--no-end' in c and '--json' in c and c[1] == 'snapshot' for c in calls) and got == want and left(LT) == [],
      '样张：每个镜头只开一次浏览器，把这个镜头里的几个时刻（镜头里的秒）一次截完；按报回来的时刻对文件，文件名和顺序反着也对得上；镜头外的时刻不截，本机不留东西'
      + ('' if msg is None else '：' + msg))
msg = still('2.0,2.0,2.0004', shot='B01')
check(msg is None and len(calls) == 1 and calls[0][calls[0].index('--at') + 1] == '1.000' and sorted([f.name for f in (proj / '样张').glob('B01_*.png')]) == ['B01_2.00.png'],
      '同一时刻写了几遍（含差不到千分之一秒的）：只截一张')
msg = still('2.0,5.5', 'lose', 'B01')
check(msg and '少了' in msg and 'B01' in msg and not list((proj / '样张').glob('B01_*.png')), '报回来的少了一个时刻：停下说少了哪一秒，不拿错的图顶上')
msg = still('2.0', 'junk', 'B01')
check(msg and '没给出结果' in msg and 'Chrome 没起来' in msg, 'snapshot 没报出结果（不是 JSON）：停下并把它的原话显示出来')

src = (A / 'hyperframes_template' / '出片.py').read_text(encoding='utf-8')
check('HYPERFRAMES_RENDER_LOCK' not in src and "'--workers'" not in src and '--variables' not in src and 'def locked()' in src,
      '镜头之间不同时出片：不关 HyperFrames 自己的出片排队、不改它自动定的路数、不用 --variables（2026-11-22 试验田实测：B04 4K 同时出时 47 帧和单独出不一样）')

shutil.rmtree(TMP, ignore_errors=True)
print('动画提速测试全部通过' if not fails else f'动画提速测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
