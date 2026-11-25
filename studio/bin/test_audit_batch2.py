#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试（2026-11-21 体检第二批：下线、小修、说明瘦身）：不连达芬奇（用替身）、不启动 Blender / HyperFrames / Chrome（用假程序）、不碰本机真的达芬奇写入锁、不弹窗，
本机临时目录用测试自己的文件夹（THINKCUT_LOCAL_TMP）。
1 老代码动画模板下线：anim-new 说明已下线；已有老工程照旧 build、走到出片和总装（截图小工具搬到 scripts/tools/）。
2 小修：a 字幕表为空/改字时达芬奇返回空不崩 b 只读 snap 在达芬奇渲染别的工程时不切 c 体检找 Chrome 和 chrome.mjs 一套 d 审片工作台 GET 查 Host
  e 等渲染超时后删掉队列里这次的任务 f ffmpeg 合成、ffprobe 核对有时限 g 案例索引登记 261117 h AGENTS.md 说法一致。
3 用完即删的渲染临时文件放本机：公用小工具、字幕整轨、字幕检查、HyperFrames 样张截图、Blender 逐帧图（失败保留能续渲，成功后删，空间不够先停）。
4 说明瘦身：老做法搬到 历史做法.md，必读说明里不再讲；重复的规矩只留一处。"""
import contextlib, importlib.util, inspect, io, json, os, shutil, subprocess, sys, tempfile, time, unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'; A = SK / 'assets'; TOOLS = SK / 'scripts' / 'tools'; S = SK / 'scripts' / 'thinkcut_edit.py'
TMP = Path(tempfile.mkdtemp(prefix='体检第二批_')).resolve(); LT = TMP / '本机临时'
os.environ['THINKCUT_SKILL'] = str(SK); os.environ['THINKCUT_NO_OPEN'] = '1'; os.environ['THINKCUT_LOCAL_TMP'] = str(LT)
os.environ['THINKCUT_RESOLVE_LOCK'] = str(TMP / '锁' / 'resolve-write.lock')        # 不碰本机真的达芬奇写入锁
os.environ.pop('THINKCUT_TIMEOUT_X', None)
sys.path.insert(0, str(SK / 'scripts'))
from thinkcut import anim, common, doctor, resolve, subs, voice, test_tools_1112 as T12      # noqa: E402
from thinkcut.common import Job, wj, rj                                                      # noqa: E402
from PIL import Image                                                                    # noqa: E402
fails = []


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


def said(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): r = fn(*a, **k)
    return r, buf.getvalue()


def stops(fn, *a, **k):
    """跑 fn，返回它停下时说的话（SystemExit 的内容）；没停返回 None。"""
    try:
        with contextlib.redirect_stdout(io.StringIO()): fn(*a, **k)
    except SystemExit as e: return str(e.code)
    return None


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def cli(*a):
    r = subprocess.run([sys.executable, '-B', str(S), *[str(x) for x in a]], capture_output=True, text=True, stdin=subprocess.DEVNULL); return r.returncode, r.stdout + r.stderr


def left(d):
    """文件夹里还剩哪些文件（相对路径）。"""
    return sorted(str(f.relative_to(d)) for f in Path(d).rglob('*') if f.is_file()) if Path(d).exists() else []


def video(path, n, w, h, fps='30'):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size={w}x{h}:rate={fps}', '-frames:v', str(n), '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(path)],
                   check=True, stdin=subprocess.DEVNULL)


rd = lambda f: Path(f).read_text(encoding='utf-8')

# ═════════ 1. 老代码动画模板下线，重出老片子的能力保留 ═════════
src_all = ''.join(rd(f) for f in [*(SK / 'scripts').rglob('*.py'), *A.glob('*_template/*.py'), *(A / '包装系统').glob('*.py')] if not f.name.startswith('test_'))
check(not (A / 'animation_template').exists() and (TOOLS / 'cap.mjs').is_file() and (TOOLS / 'chrome.mjs').is_file() and anim.CAP == TOOLS / 'cap.mjs'
      and not hasattr(anim, 'TEMPLATE') and 'animation_template' not in src_all.replace('assets/animation_template/` 2026', ''),
      '老代码动画模板 assets/animation_template/ 已下线；还在用的截图小工具 cap.mjs、chrome.mjs 搬到 scripts/tools/，程序里不再引用模板')
cap_src = rd(TOOLS / 'cap.mjs')
ok_node = True
if shutil.which('node'):
    r1 = subprocess.run(['node', '--check', str(TOOLS / 'cap.mjs')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    r2 = subprocess.run(['node', '--input-type=module', '-e', "const m = await import(process.argv[1]); console.log(typeof m.findChrome, m.chromeCandidates('/家').length)", str(TOOLS / 'chrome.mjs')],
                        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    ok_node = r1.returncode == 0 and r2.stdout.split() == ['function', '6']
check(ok_node and "from './chrome.mjs'" in cap_src and 'A.page' in cap_src and '--frames' in cap_src, 'cap.mjs 在新位置语法没问题，同一文件夹里的 chrome.mjs 能载入（node 跑，不开浏览器）')

old = TMP / '老片子'; old.mkdir()
wj(old / 'job.json', dict(fps='30', format='3:4', frames=20, project='p', main_timeline='v1', source='/x'))
wj(old / '分镜.json', dict(anchors={}, shots=[dict(id='F_0', kind='F', a=0.0, b=0.2), dict(id='B_old', kind='B', a=0.2, b=0.6), dict(id='F_1', kind='F', a=0.6, b=20 / 30)]))
wj(old / '分镜规格.json', dict(shots=[dict(id='B_old', 讲什么='结构', 口播要点=['烟道用了几十年'])]))
code, out = cli('anim-new', old)
code2, out2 = cli('anim-new', old, '--old')
check(code != 0 and '老代码动画已下线' in out and 'blender-new' in out and 'hf-new' in out and 'anim-render' in out and not (old / '动画').exists()
      and code2 != 0 and not (old / '动画').exists() and list(inspect.signature(anim.new).parameters) == ['job'],
      'anim-new：停下并用中文说清“老代码动画已下线，新片子用 blender-new、hf-new，老片子重出用 anim-render”；--old（新建老工程）这个入口没有了')
code, out = cli('-h')
check(code == 0 and all(c in out for c in ('anim-build', 'anim-stills', 'anim-render', 'anim-labelcheck', 'anim-assemble', 'blender-new', 'hf-new')), '重出老片子的命令都还在：anim-build、anim-stills、anim-render、anim-labelcheck、anim-assemble')

# 已有老工程（最小夹具：真的 build.py、shell.html、timeline.js，three.js 是占位）：build → 出片（截图用假的，不开 Chrome）→ 登记 → 总装
shutil.copytree(ROOT / 'samples' / '老代码动画工程_最小' / '动画', old / '动画'); job = Job(old)
now = time.time(); os.utime(old / '分镜.json', (now - 60, now - 60)); os.utime(old / '动画' / 'layout_pb.json', (now, now))      # 标签排版比分镜新：不用重排（重排要开 Chrome）
said(anim.build, job); html = rd(old / '动画' / '动画.html') if (old / '动画' / '动画.html').exists() else ''
check('window.BEATS_DATA' in html and 'B_old' in html and 'window.LAYOUTS' in html, '已有老工程：anim-build 照旧把 动画/ 拼成 动画.html，读到片子目录的分镜和标签排版')
calls = []


def fake_group(cmd, timeout, **kw):
    """假的无头截图：记下命令，按 --frames、--w 出一段测试画面（3:4）。"""
    cmd = [str(c) for c in cmd]; calls.append(cmd); arg = lambda k: cmd[cmd.index(k) + 1]
    w = int(arg('--w')); video(arg('--video'), int(arg('--frames')), w, w * 4 // 3, arg('--fps'))


keep = (anim._run_group, anim.require)
try:
    anim._run_group = fake_group; anim.require = lambda *a, **k: None
    rep, out = said(anim.render, job, n_workers=2)
finally: anim._run_group, anim.require = keep
reg = rj(old / '合成' / '动画清单.json')['shots'].get('B_old', {})
c0 = calls[0] if calls else []
check(bool(calls) and all(c[0] == 'node' and c[1] == str(TOOLS / 'cap.mjs') and c[c.index('--page') + 1] == str(old / '动画' / '动画.html') and 'mode=beats' in c[c.index('--q') + 1] for c in calls)
      and c0[c0.index('--w') + 1] == '1080' and c0[c0.index('--frames') + 1] == '12',
      f'已有老工程：anim-render 用 scripts/tools/cap.mjs 去截这条片子自己的 动画.html（{len(calls)} 段，1080 宽、12 帧）')
check(reg.get('file', '').endswith('动画/出片/B_old_预览50.mp4') and reg.get('scale') == 50 and Path(reg.get('file', '/x')).exists() and anim._ffprobe_frames(old / '合成' / 'B肉.mp4') == 20
      and not rep['bad'] and rep['shots'] == ['B_old'], '已有老工程：出片 → 登记进动画清单（50% 审片版）→ 总装出 合成/B肉.mp4（20 帧，逐镜头比对通过），整条路走得通')
code, out = cli('anim-build', TMP / '没有老工程') if (TMP / '没有老工程').mkdir() is None and wj(TMP / '没有老工程' / 'job.json', dict(fps='30', format='3:4', frames=20)) is None else (0, '')
check(code != 0 and '没有老的代码动画工程' in out and 'hf-new' in out, '没有老工程的片子跑 anim-build：照旧停下说明（新片子用 hf-new）')

# ═════════ 2a. 字幕表为空、改字时达芬奇返回空 ═════════
ej = TMP / '空字幕'; (ej / '合成' / '字幕').mkdir(parents=True); wj(ej / 'job.json', dict(fps='30', format='3:4', frames=30)); wj(ej / '合成' / '字幕' / '字幕表.json', [])
msg = stops(subs.track, Job(ej))
check(msg and '字幕表是空的' in msg and '重跑 subs' in msg and not (ej / '合成' / '字幕' / '字幕整轨.mov').exists() and left(LT) == [], '字幕表是空的（0 条）时拼字幕整轨：停下说清原因，不再 IndexError，也没建临时文件')
src_f = inspect.getsource(resolve.subfix)
bare = [l.strip()[:60] for l in src_f.splitlines() if 'GetItemListInTrack(' in l and l.count('GetItemListInTrack(') != l.count(') or []')]
check(not bare and src_f.count('GetItemListInTrack(') == 4, 'subfix 里每一处 GetItemListInTrack 都带 or []' + (f'：还有 {bare}' if bare else ''))


class It:
    def __init__(s, name, a, b, off=0): s.n, s.a, s.b, s.off = name, a, b, off
    def GetName(s): return s.n
    def GetStart(s): return s.a
    def GetEnd(s): return s.b
    def GetLeftOffset(s): return s.off
    def GetProperty(s, k=None): return {} if k is None else None
    def SetProperty(s, k, v): return True


class FixTL:
    """假时间线：第 1 次问字幕轨给出原片段；之后（放上新片段以后）达芬奇返回空。"""
    def __init__(s): s.asked = 0
    def GetStartFrame(s): return 0
    def GetItemListInTrack(s, kind, i):
        s.asked += 1; return [It('字幕整轨.mov', 100, 130, 100)] if s.asked == 1 else None
    def DeleteClips(s, items, ripple): return True


class FixPool:
    def GetRootFolder(s): return s
    def GetSubFolderList(s): return []
    def AddSubFolder(s, root, name): return s
    def SetCurrentFolder(s, f): return True
    def ImportMedia(s, paths): return [s]
    def GetClipProperty(s, k): return '30'
    def AppendToTimeline(s, items): return True


class FixP:
    def SetCurrentTimeline(s, tl): return True
    def GetMediaPool(s): return FixPool()


keep = (subs.refix, resolve.project, resolve.timeline, resolve.track_index)
try:
    subs.refix = lambda job, n, o, w, srt=False: ({'start': 100, 'end': 130, 'file': '/x/字幕_aab.png'}, Path('/x/字幕_aab_a.mov'))
    resolve.project = lambda job: (None, None, FixP()); resolve.timeline = lambda p, name: FixTL(); resolve.track_index = lambda tl, kind, label: 3
    err = None
    try: msg = stops(resolve.subfix, Job(ej), 2, '错', '对', name='审核线')
    except Exception as e: err = e; msg = None
finally: subs.refix, resolve.project, resolve.timeline, resolve.track_index = keep
check(err is None and msg and '读不到' in msg and '第 2 条' in msg, f'改字：新片段放上去后达芬奇返回空，停下说清原因（不再 TypeError / StopIteration）' + (f'：{type(err).__name__} {err}' if err else ''))


# ═════════ 2b. 只读 snap：达芬奇正在渲染别的工程时不保存、不切工程 ═════════
class DV:
    """假达芬奇（兼当 Resolve / 工程管理器 / 当前工程）：name＝当前开着的工程名，rendering＝在不在渲染。"""
    def __init__(s, name, rendering): s.name, s.rendering = name, rendering; s.saves = 0; s.loaded = []; s.asked = 0
    def GetProjectManager(s): return s
    def GetCurrentDatabase(s): return {'DbName': '本机', 'DbType': 'Disk'}
    def GetDatabaseList(s): return [{'DbName': '本机', 'DbType': 'Disk'}]
    def SetCurrentDatabase(s, db): return True
    def GetCurrentProject(s): return s
    def GetName(s): return s.name
    def IsRenderingInProgress(s): s.asked += 1; return s.rendering
    def SaveProject(s): s.saves += 1; return True
    def GotoRootFolder(s): return True
    def GetProjectListInCurrentFolder(s): return ['p', '别的工程']
    def LoadProject(s, name): s.loaded.append(name); return DV(name, False)


pj = TMP / '只读片'; pj.mkdir(); wj(pj / 'job.json', dict(fps='30', format='3:4', frames=30, project='p', main_timeline='v1', source='/x'))
pjob = Job(pj); keep_connect = resolve.connect
try:
    busy_other = DV('别的工程', True); resolve.connect = lambda: busy_other
    msg = stops(resolve.project, pjob, write=False)
    check(msg and '达芬奇正在渲染' in msg and '别的工程' in msg and '等渲完再看' in msg and busy_other.saves == 0 and busy_other.loaded == [],
          '只读查询（snap 用的 project(write=False)）：达芬奇正在渲染别的工程时停下说明，不保存、不切工程')
    msg = stops(resolve.snap, pjob, '审核线')
    check(msg and '达芬奇正在渲染' in msg and busy_other.saves == 0 and busy_other.loaded == [] and not (pj / '快照').exists(), 'snap 命令遇到同样情况：停下，没切工程、没写快照')
    busy_mine = DV('p', True); resolve.connect = lambda: busy_mine
    check(resolve.project(pjob, write=False)[2] is busy_mine and busy_mine.saves == 0, '只读查询：达芬奇在渲染的就是这条片子的工程，照常读（不用切工程）')
    idle_other = DV('别的工程', False); resolve.connect = lambda: idle_other
    got = resolve.project(pjob, write=False)
    check(idle_other.saves == 1 and idle_other.loaded == ['p'] and got[2].GetName() == 'p', '只读查询：达芬奇没在渲染、开着别的工程，和以前一样先保存再切到这条片子的工程')
    idle_w = DV('别的工程', False); resolve.connect = lambda: idle_w
    got = resolve.project(pjob)
    check(idle_w.saves == 1 and idle_w.loaded == ['p'] and idle_w.asked == 1, '写入口 project(write=True)：没在渲染时行为和以前一样（保存、切工程；只问一次是否在渲染）')
finally:
    resolve.connect = keep_connect; resolve.unlock()

# ═════════ 2c. 体检找 Chrome 和 chrome.mjs 同一套 ═════════
cands = doctor.chrome_candidates('/家')
if shutil.which('node'):
    r = subprocess.run(['node', '--input-type=module', '-e', "const m = await import(process.argv[1]); console.log(JSON.stringify(m.chromeCandidates('/家')))", str(TOOLS / 'chrome.mjs')],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    same = r.returncode == 0 and json.loads(r.stdout) == cands
else: same = all(q.split('/Applications/')[-1] in rd(TOOLS / 'chrome.mjs') for q in cands)
check(same and len(cands) == 6 and cands[0] == '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '体检找浏览器的清单和 chrome.mjs 一模一样（6 个位置、顺序相同）')
check(doctor.find_chrome(cands, lambda q: False) is None and doctor.find_chrome(cands, lambda q: 'Edge' in q and q.startswith('/家')) == '/家/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
      and doctor.find_chrome(cands, lambda q: True) == cands[0], '体检找浏览器：按顺序取第一个存在的（~/Applications 里的 Edge 也认），都没有返回空')
os.environ['THINKCUT_SYNC_ENV'] = str(TMP / '没有这个脚本.sh'); keep_fc = doctor.find_chrome
line = lambda out, word: next((l for l in out.splitlines() if word in l), '')
try:
    doctor.find_chrome = lambda: None; _, o_no = said(doctor.run)
    doctor.find_chrome = lambda: '/家/Applications/Chromium.app/Contents/MacOS/Chromium'; _, o_yes = said(doctor.run)
finally: doctor.find_chrome = keep_fc; os.environ.pop('THINKCUT_SYNC_ENV', None)
check(line(o_no, 'Chrome').startswith('✗ ') and 'Chromium、Microsoft Edge 也可以' in line(o_no, 'Chrome') and line(o_yes, 'Chrome').startswith('✓ ')
      and "Path('/Applications/Google Chrome.app').exists()" not in rd(SK / 'scripts/thinkcut/doctor.py'), '体检的 Chrome 一项不再写死路径：装在别处、装的是 Chromium 也算有；都没有才报缺')

# ═════════ 2d. 审片工作台 GET 也查 Host ═════════
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_workbench.ApiTests.test_get_checks_host'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
check(r.returncode == 0, '审片工作台 GET：本机地址加本端口放行（页面、数据、图片、程序自己的 ping），没有 Host 头放行，别的 Host 403（test_workbench.test_get_checks_host）'
      + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-600:]))


# ═════════ 2e. 等渲染超时：叫停后删掉队列里这次的任务 ═════════
class RP:
    """假工程：一直在渲，直到被叫停（stop_works）；delete＝删任务时怎样（True/False/'boom'）。"""
    def __init__(s, stop_works=True, delete=True, polls=None): s.stop_works, s.delete, s.polls = stop_works, delete, polls; s.rendering = True; s.stopped = 0; s.deleted = []; s.asked = 0
    def StartRendering(s, ids, interactive): return True
    def IsRenderingInProgress(s):
        s.asked += 1
        if s.polls is not None and s.asked > s.polls: s.rendering = False
        return s.rendering
    def StopRendering(s):
        s.stopped += 1
        if s.stop_works: s.rendering = False
    def DeleteRenderJob(s, j):
        if s.delete == 'boom': raise RuntimeError('达芬奇不回话')
        s.deleted.append(j); return s.delete
    def GetRenderJobStatus(s, j): return {'JobStatus': 'Complete'}


p = RP(); msg = stops(resolve.wait_render, p, 'j1', 0.05, '导出', 0.01)
check(msg and '导出超过' in msg and '已叫停达芬奇渲染' in msg and p.deleted == ['j1'] and '任务已经删掉' in msg and '还留' not in msg, '等渲染超时：叫停之后把这次加进渲染队列的任务删掉，提示里说已删掉')
keep_sleep = resolve.time.sleep; resolve.time.sleep = lambda s: None
try: p = RP(stop_works=False); msg = stops(resolve.wait_render, p, 'j1', 0.05, '导出', 0.01)
finally: resolve.time.sleep = keep_sleep
check(msg and '导出超过' in msg and '请去达芬奇里手动停止渲染' in msg and p.deleted == [] and '还留在达芬奇交付页的渲染队列里' in msg, '等渲染超时但没叫停成：不删任务（还在渲），提示去达芬奇手动停、手动删')
m2 = {}
for key, how in (('删不掉', False), ('删时出错', 'boom')):
    p = RP(delete=how); m2[key] = stops(resolve.wait_render, p, 'j1', 0.05, '导出', 0.01)
check(all(m and '导出超过 0.05 秒' in m and '已叫停达芬奇渲染' in m and '请去达芬奇手动删掉' in m and '已经删掉' not in m for m in m2.values()),
      '等渲染超时、删任务失败（返回失败或抛错）：超时的原因照样说清楚，另外提示手动删，不谎称已删')
p = RP(); err = None; resolve.time.sleep = lambda s: (_ for _ in ()).throw(KeyboardInterrupt())
try:
    try: said(resolve.wait_render, p, 'j1', 60, '导出', 0.01)
    except KeyboardInterrupt as e: err = e
finally: resolve.time.sleep = keep_sleep
p3 = RP(polls=2); said(resolve.wait_render, p3, 'j1', 60, '导出', 0.01)
check(err is not None and p.stopped == 1 and p.deleted == [] and p3.stopped == 0 and p3.deleted == [], '被 Ctrl-C 打断（叫停、不删任务）和正常渲完（不叫停，任务由调用方照旧删）都和以前一样')

# ═════════ 2f. ffmpeg 合成视频、ffprobe 核对有时限 ═════════
BL = load(A / 'blender_template' / '出片.py', 'bl2'); HF = load(A / 'hyperframes_template' / '出片.py', 'hf2')
keep = (BL.ENCODE_BASE, BL.ENCODE_PER_FRAME)
try:
    lim = (BL.encode_limit(600), BL.encode_limit(1))
    BL.ENCODE_BASE, BL.ENCODE_PER_FRAME = 1.5, 0; t0 = time.time()
    msg = stops(BL.encode, [sys.executable, '-c', 'import time; time.sleep(120)'], 600, 'A10', Path('/本机/A10')); dt = time.time() - t0
    ok = stops(BL.encode, [sys.executable, '-c', 'pass'], 600, 'A10')
finally: BL.ENCODE_BASE, BL.ENCODE_PER_FRAME = keep
src_b = rd(A / 'blender_template' / '出片.py')
check(lim == (300 + 5 * 600, 305) and msg and 'A10：ffmpeg 把逐帧图合成视频超过' in msg and 'THINKCUT_TIMEOUT_X' in msg and '/本机/A10' in msg and dt < 20 and ok is None
      and "subprocess.run(['ffmpeg'" not in src_b and "encode(['ffmpeg'" in src_b,
      f'Blender 出片：ffmpeg 合成视频限时 300 秒＋每帧 5 秒（600 帧 = 3300 秒），卡住就结束它、中文说原因和逐帧图留在哪（{dt:.1f} 秒）；正常结束不受影响')
fakebin = TMP / '假命令'; fakebin.mkdir(); fp_ = fakebin / 'ffprobe'; fp_.write_text('#!/bin/sh\nsleep 120\n'); fp_.chmod(0o755)
tiny = TMP / '小.mp4'; video(tiny, 5, 64, 48)
good = HF.probe(tiny); keep_path, keep_lim = os.environ['PATH'], HF.PROBE_LIMIT
try:
    os.environ['PATH'] = f'{fakebin}:{keep_path}'; HF.PROBE_LIMIT = 1.5; t0 = time.time()
    msg = stops(HF.probe, tiny); dt = time.time() - t0
finally: os.environ['PATH'] = keep_path; HF.PROBE_LIMIT = keep_lim
check(good == (5, 64, 48) and HF.PROBE_LIMIT == 300 and msg and '核对出片' in msg and '超过 2 秒' in msg and 'THINKCUT_TIMEOUT_X' in msg and str(tiny) in msg and dt < 20,
      f'HyperFrames 出片：ffprobe 核对帧数限时 300 秒，卡住就结束它、中文说原因和新出的文件在哪（{dt:.1f} 秒）；正常时照常返回帧数和尺寸')
r = subprocess.run([sys.executable, '-B', '-c', 'import importlib.util as u, sys\ns = u.spec_from_file_location("m", sys.argv[1]); m = u.module_from_spec(s); s.loader.exec_module(m); print(m.encode_limit(600))',
                    str(A / 'blender_template' / '出片.py')], capture_output=True, text=True, env=dict(os.environ, THINKCUT_TIMEOUT_X='2'), stdin=subprocess.DEVNULL)
check(r.stdout.strip() == '6600.0', '新加的时限同样能用环境变量 THINKCUT_TIMEOUT_X 整体放大' + ('' if r.returncode == 0 else '：' + r.stderr[-300:]))

# ═════════ 3. 用完即删的渲染临时文件放本机 ═════════
os.environ.pop('THINKCUT_LOCAL_TMP'); default_root = common.local_root(); os.environ['THINKCUT_LOCAL_TMP'] = str(LT)
proj = TMP / '项目甲' / '02_制作' / '自动剪辑'; proj.mkdir(parents=True); (TMP / '项目甲' / '项目.json').write_text('{}')
other = TMP / '别处' / '自动剪辑'; other.mkdir(parents=True)
k1, k2, k3 = common.film_key(proj), common.film_key(other), common.film_key(TMP / '另一个' / '自动剪辑')
check(default_root == Path(tempfile.gettempdir()) / 'ThinkCut临时' and common.local_root() == LT and k1.startswith('项目甲_') and k2.startswith('自动剪辑_') and k3.startswith('自动剪辑_') and k2 != k3,
      '本机临时文件夹：默认在系统临时目录下的“ThinkCut临时”；按片子分开（用项目文件夹名，同名的片子靠路径指纹区分）')
d = common.local_tmp(proj, 'Blender_试', '50_ab', 'A10'); (d / '残留.png').write_text('x')
d_keep = common.local_tmp(proj, 'Blender_试', '50_ab', 'A10', clean=False); kept = (d / '残留.png').exists()
d_clean = common.local_tmp(proj, 'Blender_试', '50_ab', 'A10'); cleaned = not (d / '残留.png').exists()
check(d == LT / k1 / 'Blender_试' / '50_ab' / 'A10' == d_keep == d_clean and d.is_dir() and kept and cleaned, '本机临时文件夹：按片子、工程、镜头分开；默认开始时清掉上次留下的，要续用的（clean=False）不清')
msg = stops(common.local_tmp, proj, '字幕整轨', need=3 * 1024 ** 3, what='字幕整轨的分段片段', free=7 * 1024 ** 3)
ok_d = common.local_tmp(proj, '字幕整轨', need=3 * 1024 ** 3, free=9 * 1024 ** 3)
check(msg and '磁盘空间不够' in msg and '没开始写' in msg and '字幕整轨的分段片段估计要写 3.0 GB' in msg and '只剩 7.0 GB' in msg and '5 GB 余量' in msg and ok_d.is_dir(),
      '本机空间不够（要写的＋5 GB 余量放不下）：开始前就停下说明要多少、剩多少；够就照常')
outside = TMP / '不相干' / 'x'; outside.mkdir(parents=True); common.local_rm(outside); common.local_rm(ok_d); common.local_rm(d)
check(outside.exists() and LT.is_dir() and not (LT / k1).exists(), '用完删：删掉自己的文件夹和上面空了的各层，专用文件夹本身留着；不在专用文件夹里的不碰')

# 字幕整轨、字幕检查
sample = ROOT / 'samples' / '烟道反味'; sj = TMP / '字幕片'; sj.mkdir(); shutil.copy(sample / '字幕前12条.srt', sj / 'x.srt')
wj(sj / 'job.json', dict(rj(sample / 'job_样例.json'), format='3:4', srt=str(sj / 'x.srt'), frames=120, aroll='', source='', project='', main_timeline=''))
sjob = Job(sj); rows, _ = said(subs.render, sjob); rows = [r for r in rows if r['end'] <= 120][:3]; wj(sj / '合成' / '字幕' / '字幕表.json', rows)
seen = []; clip0 = subs._clip; free0 = common.local_free
try:
    subs._clip = lambda src, n, dst, fps: (seen.append(Path(dst)), clip0(src, n, dst, fps))[1]
    said(subs.track, sjob); n_ok = subs.packets(sj / '合成' / '字幕' / '字幕整轨.mov')
    in_local = bool(seen) and all(LT / common.film_key(sj) / '字幕整轨' in q.parents for q in seen)
    stray = [f.name for f in (sj / '合成' / '字幕').iterdir() if f.is_dir() or f.suffix == '.txt']
    check(n_ok == 120 and in_local and stray == [] and left(LT) == [], f'字幕整轨：分段片段写在本机临时目录（{len(seen)} 段），拼好的整轨照旧在 合成/字幕/；做完本机不留东西，片子目录里不再有临时文件夹')
    (sj / '合成' / '字幕' / '字幕整轨.mov').unlink(); seen.clear(); common.local_free = lambda q: 5 * 1024 ** 3 + 1000
    msg = stops(subs.track, sjob)
    check(msg and '磁盘空间不够' in msg and '字幕整轨的分段片段' in msg and seen == [] and not (sj / '合成' / '字幕' / '字幕整轨.mov').exists() and left(LT) == [], '字幕整轨：本机空间不够时开始前就停，什么都没写')
finally: subs._clip = clip0; common.local_free = free0
grabbed = []; grab0 = subs._grab
try:
    subs._grab = lambda video_, frames, outdir, fps, workers=6: (grabbed.append(Path(outdir)), grab0(video_, frames, outdir, fps, workers))[1]
    res = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromName('thinkcut.test_tools_1112.SubsPipTests.test_sheet_uses_real_pip_position'))
finally: subs._grab = grab0
check(res.wasSuccessful() and res.testsRun == 1 and len(grabbed) == 2 and all(LT in q.parents and q.parent.name == '字幕检查' for q in grabbed) and left(LT) == [],
      '字幕检查（subs-check）：取出来的画面放本机临时目录，检查结果和以前一样（原有用例通过），做完本机不留东西')

# HyperFrames 样张截图
hfilm = TMP / 'HF片'; hfilm.mkdir()
wj(hfilm / 'job.json', dict(fps='30', format='3:4', frames=300, project='p', main_timeline='v1', source='/x'))
wj(hfilm / '分镜.json', dict(anchors={}, shots=[dict(id='B01', kind='B', a=1.0, b=9.0)])); wj(hfilm / '分镜规格.json', dict(shots=[dict(id='B01', 工程='HF_试', 讲什么='过程', 口播要点=['一句'])]))
hjob = Job(hfilm); said(anim.hf_new, hjob, '试'); hproj = hfilm / 'HF_试'; shutil.copy(hproj / '镜头' / '示例.html', hproj / '镜头' / 'B01.html')
HFM = load(hproj / '出片.py', 'hf_local'); outs = []


def fake_hf_run(cmd):      # 2026-11-22 起一个镜头的几个时刻一次截完，按 --json 报结果
    o = Path(cmd[cmd.index('-o') + 1]); outs.append((o, left(o))); (o / 'sub').mkdir(parents=True, exist_ok=True); fr = []
    for k, t in enumerate(cmd[cmd.index('--at') + 1].split(',')):
        f = o / 'sub' / f'frame-{k}.png'; Image.new('RGB', (54, 72), (20, 30, 60)).save(f); fr.append({'path': str(f), 'time': float(t)})
    return json.dumps({'ok': True, 'frames': fr})


stale = common.local_tmp(hfilm, 'HF_试', '样张_B01'); (stale / '上次崩溃留下的.png').write_bytes(b'x')
keep = (anim.require, sys.argv)
try:
    HFM.hf = lambda: 'hyperframes'; HFM.run = fake_hf_run; HFM.preflight = lambda exe, sid: None; anim.require = lambda *a, **k: None
    sys.argv = ['出片.py', '--样张', '2.0,5.5', '--shot', 'B01']; said(HFM.main)
finally: anim.require, sys.argv = keep
check(len(outs) == 1 and all(o == LT / common.film_key(hfilm) / 'HF_试' / '样张_B01' and before == [] for o, before in outs) and not (hproj / '缓存').exists()
      and sorted(f.name for f in (hproj / '样张').glob('B01_*.png')) == ['B01_2.00.png', 'B01_5.50.png'] and left(LT) == [],
      'HyperFrames 样张：截图先出到本机临时目录（开始时清掉上次的残留）、再挪进工程的 样张/；工程里不再建 缓存/，做完本机不留东西')

# Blender 逐帧图：用假 Blender 走一遍 出片.py
bfilm = TMP / 'BL片'; bfilm.mkdir()
wj(bfilm / 'job.json', dict(fps='30', format='3:4', frames=60, project='p', main_timeline='v1', source='/x'))
wj(bfilm / '分镜.json', dict(anchors={}, shots=[dict(id='A01', kind='B', a=1.0, b=1.2)])); wj(bfilm / '分镜规格.json', dict(shots=[dict(id='A01', 工程='Blender_试', 讲什么='结构', 口播要点=['一句'])]))
bjob = Job(bfilm); said(anim.blender_new, bjob, '试'); bproj = bfilm / 'Blender_试'; wj(bproj / '标签.json', dict(labels=[]))
BLM = load(bproj / '出片.py', 'bl_local'); blog = TMP / 'blender跑过的帧.txt'; blog.write_text('')
fake_bl = TMP / '假Blender.py'
fake_bl.write_text(f'''#!{sys.executable}
# -*- coding: utf-8 -*-
import json, os, sys, time
from pathlib import Path
from PIL import Image
task = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
W, H = task['resolution']; raw = Path(task['raw']); raw.mkdir(parents=True, exist_ok=True); recs = []; done = 0
fail = int(os.environ.get('FAKE_BL_FAIL_AFTER') or -1); fps = task['fps_num'] / task['fps_den']
Path(os.environ['TMPDIR'], 'blender临时文件').write_text('x')
with open({str(blog)!r}, 'a') as f: f.write('env ' + os.environ['TMPDIR'] + chr(10))
if os.environ.get('FAKE_BL_HANG'): time.sleep(120)
for i in task['indices']:
    recs.append(dict(i=i, t=round((task['frame0'] + i) / fps, 5), points={{}}, protected=[]))
    out = raw / f'{{i:06d}}.png'
    if out.exists() and out.stat().st_size > 0: continue
    if 0 <= fail <= done: sys.exit(1)
    Image.new('RGB', (W, H), (40 + i * 20, 60, 90)).save(out); done += 1
    with open({str(blog)!r}, 'a') as f: f.write(f'frame {{i}}' + chr(10))
Path(task['records']).write_text(json.dumps(dict(shot=task['shot'], size=[W, H], frames=recs, events=[])), encoding='utf-8')
''', encoding='utf-8'); fake_bl.chmod(0o755)
fp = anim.fingerprint(bjob, bproj, ['A01']); home = LT / common.film_key(bfilm) / 'Blender_试' / f'50_{fp}' / 'A01'; cache = bproj / '缓存' / f'50_{fp}' / 'A01'
old_fp = common.local_tmp(bfilm, 'Blender_试', '50_0123456789abcdef', 'A01', 'raw'); (old_fp / '000000.png').write_bytes(b'x')          # 代码指纹对不上的旧帧
other_shot = common.local_tmp(bfilm, 'Blender_试', '50_0123456789abcdef', 'B99', 'raw'); (other_shot / '000000.png').write_bytes(b'x')   # 别的镜头的：不动
frames_run = lambda: sorted(int(l.split()[1]) for l in blog.read_text().splitlines() if l.startswith('frame'))
envs_run = lambda: sorted({l.split(' ', 1)[1] for l in blog.read_text().splitlines() if l.startswith('env')})
keep = (anim.require, sys.argv, BLM.BLENDER, BLM.ProcessPoolExecutor, BLM.BLENDER_BASE, BLM.BLENDER_PER_FRAME, common.local_free)
try:
    BLM.BLENDER = str(fake_bl); BLM.ProcessPoolExecutor = ThreadPoolExecutor; anim.require = lambda *a, **k: None; sys.argv = ['出片.py', '--shot', 'A01', '--jobs', '2']
    check(BLM.frame_home(50, fp, 'A01') == home and [d.parent.name for d in BLM.stale_frames(fp, 'A01')] == ['50_0123456789abcdef'], 'Blender 逐帧图的位置：本机临时目录/片子/工程/分辨率_代码指纹/镜头；指纹对不上的旧帧认得出来')
    common.local_free = lambda q: 5 * 1024 ** 3 + 1000
    msg = stops(BLM.main)
    check(msg and '磁盘空间不够' in msg and 'A01 的逐帧图（6 帧 1080×1440）' in msg and frames_run() == [] and envs_run() == [], 'Blender 出片：本机空间不够时在启动 Blender 之前就停下，说清是哪个镜头、要写多少')
    common.local_free = keep[6]
    os.environ['FAKE_BL_FAIL_AFTER'] = '1'; msg = stops(BLM.main); os.environ.pop('FAKE_BL_FAIL_AFTER')
    got1 = sorted(f.name for f in (home / 'raw').glob('*.png'))
    check(msg and 'Blender 进程出错' in msg and str(cache) in msg and got1 == ['000000.png', '000003.png'] and frames_run() == [0, 3] and not old_fp.exists() and (other_shot / '000000.png').exists(),
          f'Blender 出片中途出错：已渲出的原始帧留在本机（{got1}），日志照旧在片子项目的 缓存/；开始时清掉了这个镜头指纹对不上的旧帧，别的镜头的没动')
    in_proj = [f for f in left(bproj / '缓存') if f.endswith('.png') or '运行环境' in f]
    logs = sorted(f.name for f in cache.iterdir())
    check(in_proj == [] and all(any(n.startswith(pre) for n in logs) for pre in ('blender_A01_0', 'blender_A01_1', '任务_A01_0')) and envs_run() == [str(home / '运行环境' / 'TMPDIR')]
          and (home / '运行环境' / 'TMPDIR' / 'blender临时文件').exists(), '片子项目的 缓存/ 里不再有逐帧图和 Blender 运行环境（都在本机）；出错时本机的运行环境留着给人看，日志、任务单照旧在 缓存/')
    _, out = said(BLM.main)
    reg = rj(bfilm / '合成' / '动画清单.json')['shots'].get('A01', {}); mp4 = bproj / '出片' / 'A01_预览50.mp4'
    check(frames_run() == [0, 1, 2, 3, 4, 5] and mp4.exists() and anim._ffprobe_frames(mp4) == 6 and reg.get('file') == str(mp4) and reg.get('scale') == 50,
          f'Blender 重跑接着渲：只补渲缺的 4 帧（每帧只渲过一次：{frames_run()}），出片、登记和以前一样（{mp4.name}，6 帧，50%）')
    check(not home.exists() and not (LT / common.film_key(bfilm) / 'Blender_试' / f'50_{fp}').exists() and (other_shot / '000000.png').exists() and (cache / '记录_0.json').exists()
          and [f for f in left(LT) if 'B99' not in f] == [], 'Blender 整段出片成功并登记后：这个镜头在本机的逐帧图、运行环境都删掉；每帧记录、日志照旧留在片子项目')
    sys.argv = ['出片.py', '--冒烟', '--shot', 'A01']; _, out = said(BLM.main); smoke = LT / common.film_key(bfilm) / 'Blender_试' / f'25_{fp}' / 'A01' / 'raw' / '000000.png'
    check(smoke.exists() and str(smoke) in out and not (smoke.parent.parent / '运行环境').exists(), '冒烟（一帧）：帧留在本机给人看，提示里写的就是这个位置；Blender 正常结束后运行环境删掉')
    sys.argv = ['出片.py', '--shot', 'A01', '--jobs', '2']; said(BLM.main)
    check(not smoke.exists() and not home.exists(), '之后整段出片成功：冒烟留下的那一帧也一起删掉')
    os.environ['FAKE_BL_HANG'] = '1'; BLM.BLENDER_BASE, BLM.BLENDER_PER_FRAME = 1.5, 0; (bproj / '场景.py').write_text(rd(bproj / '场景.py') + '\n# 改了一下\n', encoding='utf-8')
    fp2 = anim.fingerprint(bjob, bproj, ['A01']); t0 = time.time(); msg = stops(BLM.main); dt = time.time() - t0; os.environ.pop('FAKE_BL_HANG')
    raw2 = LT / common.film_key(bfilm) / 'Blender_试' / f'50_{fp2}' / 'A01' / 'raw'
    check(fp2 != fp and msg and '还没结束' in msg and f'已渲出的帧留在本机 {raw2}' in msg and '重跑接着渲' in msg and raw2.is_dir() and dt < 30, f'Blender 等超时：提示里写明已渲出的帧留在本机哪里、重跑接着渲（{dt:.1f} 秒）')
finally:
    anim.require, sys.argv, BLM.BLENDER, BLM.ProcessPoolExecutor, BLM.BLENDER_BASE, BLM.BLENDER_PER_FRAME, common.local_free = keep
    os.environ.pop('FAKE_BL_FAIL_AFTER', None); os.environ.pop('FAKE_BL_HANG', None)
keepers = '成品、登记文件、日志照旧在片子项目'
check((bproj / '出片' / 'A01_预览50.mp4').exists() and (bfilm / '合成' / '动画清单.json').exists() and (bfilm / '合成' / '渲染尺寸.json').exists() and (bproj / '自查' / '标签重叠.json').exists()
      and "tmp = out.with_name(out.stem + '.拼接中.mp4')" in inspect.getsource(anim.assemble), f'{keepers}：出片、动画清单、渲染尺寸、自查结果都在片子目录；总装的“拼接中”文件是成品本身，没挪')

# ═════════ 2g / 2h / 4. 说明 ═════════
R = SK / 'references'; sk, ag, readme = rd(SK / 'SKILL.md'), rd(ROOT / 'AGENTS.md'), rd(ROOT / 'README.md')
zf, fg, cp, idx, hist = rd(R / '动画做法.md'), rd(R / '动画风格.md'), rd(R / '动画成片.md'), rd(R / '动画案例索引.md'), rd(R / '历史做法.md')
sec6 = idx.split('### 6. 别墅装灯前，一定要先到现场试一遍')[-1] if '### 6. 别墅装灯前，一定要先到现场试一遍' in idx else ''
check(all(f'| {b} |' in sec6 for b in ('B01', 'B03', 'B04', 'B05')) and '261117 别墅装灯前，一定要先到现场试一遍/02_制作/自动剪辑/动画/' in sec6 and '模型/挑空_硬装.glb' in sec6 and '没写片子复盘' in sec6
      and all(f'别墅装灯 {b}' in idx.split('## 案例')[0] for b in ('B01', 'B03', 'B05')),
      '说明 g：动画案例索引登记了《261117 别墅装灯前》B01、B03、B04、B05、代码和模型位置，“按画过的东西找”里查得到；没有片子复盘这件事写明了')
check('Claude 和 Codex 用同一套' not in ag and '命令入口只有这一个' in ag and 'Codex 只用于生图' in ag and any('`anim-*`' in l and '都由 Claude 跑，不交给 Codex' in l for l in ag.split('\n')),
      '说明 h：AGENTS.md 不再说“Claude 和 Codex 用同一套”，写成命令入口同一个、Codex 只用于生图')
must = {'SKILL.md': sk, '动画成片.md': cp, '动画做法.md': zf, '动画风格.md': fg}
old_words = ('renderAt', 'timeline.js', 'layout.mjs', 'OV.pList', 'mode=beats', 'hotObjects', 'anim-new', 'anim-stills', 'anim-labelcheck', 'anim-build', '代码动画工程.md', '仅老片子', '## 主风格：电影暗调', '## 讲部件、讲材料', '黑色镜面', '光束用叠加混合')
hits = {n: [w for w in old_words if w in t] for n, t in must.items()}
check(not any(hits.values()) and not (R / '代码动画工程.md').exists() and '历史做法' not in sk and [t.count('历史做法.md') for t in (cp, zf, fg)] == [1, 1, 1],
      '说明瘦身：必读说明（SKILL.md、动画成片、动画做法、动画风格）里不再讲老代码动画和旧风格，各只留一句“老片子重出见 历史做法.md”；SKILL.md 不指向它'
      + (f'：还有 {hits}' if any(hits.values()) else ''))
check(all(w in hist for w in ('renderAt', 'layout.mjs', 'OV.pList', 'anim-labelcheck', 'anim-stills', 'anim-render', '### 主风格：电影暗调', '### 讲部件、讲材料：暗场悬浮爆炸', '`src/75_layout.js`', '通用画面手法'))
      and hist.count('仅老片子') >= 6 and '**仅老片子。**' in hist and '仅作历史参考' in hist and 'scripts/tools/cap.mjs' in hist,
      '说明瘦身：搬走的内容都在 历史做法.md（老代码动画的规则、做法、文件分工和画面手法表、三节旧风格、重出老片子用哪些命令）')
check(all(h in fg for h in ('## 物理必须对', '## 定稿规则（用户定的，不要改）', '## 被否掉的方向')) and '「物理必须对」「定稿规则」「被否掉的方向」三节仍然有效' in fg
      and '水面永远是水平的' in fg and '小圆圈：连续动画超过 6 秒才加' in fg and '## 6. 两种动画（Blender、HyperFrames）都要走的硬步骤' in zf and '## 7b. HyperFrames 网页动画模板' in zf
      and '## 8. 派子助手做动画的派单模板' in zf and '**必须 0 帧**' in zf and '授权免看' in zf and '画面不用地图' in zf,
      '说明瘦身：仍有效的规矩还在必读说明里（动画风格的物理必须对、定稿规则、被否掉的方向；动画做法第 6、7、7b、8 节和硬步骤）')
codex = {n: t.count('只用于生图') + t.count('只用来生图') + t.count('只有生图交给他') for n, t in {**must, 'AGENTS.md': ag}.items()}
check(codex['SKILL.md'] == 1 and codex['动画成片.md'] == 0 and codex['动画做法.md'] == 0 and codex['AGENTS.md'] >= 1 and 'SKILL.md「所有步骤都遵守」' in cp and 'SKILL.md「所有步骤都遵守」' in zf,
      f'说明瘦身：“Codex 只生图”这条规矩只在 AGENTS.md（带用户原话）和 SKILL.md（一句）写，动画成片、动画做法改成指路：{codex}')
check(cp.count('审片轮一律 1080P，导出前才 4K') == 1 and '默认都是 50%，样张也是' not in zf and '审片轮只出 1080P' not in zf and '规矩见 动画成片.md「准备」' in zf and '导出前会被拦' in zf,
      '说明瘦身：“审片 1080P、导出前 4K”只在 动画成片.md 讲一遍，动画做法第 6 节第 8 条改成指路（程序拦的那句留着）')
check('scripts/tools/' in readme and 'animation_template/` 2026-11-21 下线' in readme and '历史做法.md' in readme, 'README：模板那一行改成 scripts/tools/ 的截图小工具，写明模板已下线')

shutil.rmtree(TMP, ignore_errors=True)
print('体检第二批测试全部通过' if not fails else f'体检第二批测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
