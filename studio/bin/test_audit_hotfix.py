#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试（2026-11-21 体检“止血和清理”）：不连达芬奇（一律用替身）、不启动 Blender / HyperFrames / Chrome（用假程序）、不碰本机真的达芬奇写入锁、不弹窗。
1 渲染等超时/出错先叫停达芬奇；达芬奇正在渲染时写入口不动它。 2 HyperFrames、Blender 子进程有时限，超时结束子进程。
3 写死的路径改成自动找（NAS 路径、苹方字体、Chrome），体检加苹方和环境比对两项。 4 字幕表为空、达芬奇返回空不崩。
5 审片工作台只接受本页面的请求（用例在 scripts/thinkcut/test_workbench.py，这里跑那一条）。 6 老工程的 build.py 不读 vendor/pp，照常 build（模板第二批已下线，用最小夹具）。 7 文档标注。
第二批（下线、小修、临时文件放本机、说明瘦身）的测试在 bin/test_audit_batch2.py。"""
import contextlib, importlib.util, inspect, io, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'; A = SK / 'assets'
TOOLS = SK / 'scripts' / 'tools'      # 截图小工具（老代码动画模板 2026-11-21 下线后从模板搬到这里）
TMP = Path(tempfile.mkdtemp(prefix='体检止血_'))
os.environ['THINKCUT_SKILL'] = str(SK); os.environ['THINKCUT_NO_OPEN'] = '1'
os.environ['THINKCUT_RESOLVE_LOCK'] = str(TMP / '锁' / 'resolve-write.lock')        # 不碰本机真的达芬奇写入锁
os.environ.pop('THINKCUT_TIMEOUT_X', None); os.environ.pop('THINKCUT_FONT_DIR', None)
sys.path.insert(0, str(SK / 'scripts')); sys.path.insert(0, str(A / '包装系统'))
from thinkcut import resolve, voice, doctor, common, test_tools_1112 as T12      # noqa: E402
from thinkcut.common import Job, wj, rj                                          # noqa: E402
import huazi, rendang                                                        # noqa: E402
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


def fake_sync(name, code, text):
    """假的 sync_env.sh：只认 --check，打印一句话、按给的退出码退出。"""
    f = TMP / name; f.write_text(f'#!/usr/bin/env bash\n[[ "$1" == "--check" ]] || exit 9\necho "{text}"\nexit {code}\n', encoding='utf-8'); return str(f)


os.environ['THINKCUT_SYNC_ENV'] = fake_sync('默认一致.sh', 0, '环境一致：假的')      # 体检的环境比对一律用假脚本，不跑真的 sync_env.sh


def alive(pid):
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True


# ═════════ 1. 渲染等待：超时/出错先叫停达芬奇 ═════════
class FakeP:
    """假达芬奇工程：polls = 还要“渲染中”几次（None = 一直渲到被叫停）；stop_works = 叫停管不管用；boom = 第几次查询时抛异常。"""
    def __init__(s, polls=None, stop_works=True, boom=None, name='p'):
        s.polls, s.stop_works, s.boom, s.name = polls, stop_works, boom, name; s.asked = s.stopped = s.saves = 0; s.started = None; s.deleted = []; s.rendering = True
    def GetName(s): return s.name
    def StartRendering(s, ids, interactive): s.started = (ids, interactive); return True
    def IsRenderingInProgress(s):
        s.asked += 1
        if s.boom and s.asked == s.boom: raise RuntimeError('达芬奇不回话')
        if s.polls is not None and s.asked > s.polls: s.rendering = False
        return s.rendering
    def StopRendering(s):
        s.stopped += 1
        if s.stop_works: s.rendering = False
    def GetRenderJobStatus(s, j): return {'JobStatus': 'Complete'}
    def DeleteRenderJob(s, j): s.deleted.append(j); return True
    # 兼当 Resolve / ProjectManager（project() 用）
    def GetProjectManager(s): return s
    def GetCurrentDatabase(s): return {}
    def GetDatabaseList(s): return []
    def GetCurrentProject(s): return s
    def SaveProject(s): s.saves += 1; return True


p = FakeP(); msg = stops(resolve.wait_render, p, 'j1', 0.05, '导出', 0.01)
check(msg and '导出超过' in msg and '已叫停达芬奇渲染' in msg and p.stopped == 1 and p.started == (['j1'], False), f'等渲染超时：先叫停达芬奇（StopRendering 调了 {p.stopped} 次）再停下，提示里写“已叫停达芬奇渲染”')
keep_sleep = resolve.time.sleep; resolve.time.sleep = lambda s: None
try:
    p = FakeP(stop_works=False); msg = stops(resolve.wait_render, p, 'j1', 0.05, '导出', 0.01)
finally: resolve.time.sleep = keep_sleep
check(msg and p.stopped == 1 and '请去达芬奇里手动停止渲染' in msg and '已叫停' not in msg, '等渲染超时而叫停没成功：提示“请去达芬奇里手动停止渲染”，不谎称已叫停')
p = FakeP(boom=3); err = None
try: said(resolve.wait_render, p, 'j1', 60, '导出', 0.01)
except RuntimeError as e: err = e
check(err is not None and p.stopped == 1 and not p.rendering, '轮询中达芬奇出错（抛异常）：先叫停渲染，再把原来的错误抛出')
p = FakeP(); err = None; resolve.time.sleep = lambda s: (_ for _ in ()).throw(KeyboardInterrupt())
try:
    try: said(resolve.wait_render, p, 'j1', 60, '导出', 0.01)
    except KeyboardInterrupt as e: err = e
finally: resolve.time.sleep = keep_sleep
check(err is not None and p.stopped == 1, '等渲染时被 Ctrl-C 打断：先叫停渲染再退出')
p = FakeP(polls=3); r, _ = said(resolve.wait_render, p, 'j1', 60, '导出', 0.01)
check(p.stopped == 0 and p.started == (['j1'], False) and p.asked == 4, '正常渲完：不调用 StopRendering，流程和以前一样')
p = FakeP(); msg = stops(voice._wait, p, 'j2', 0.05)
p2 = FakeP(polls=2); said(voice._wait, p2, 'j2')
check(msg and '渲纯口播超过' in msg and p.stopped == 1 and p.deleted == ['j2'] and '已经删掉' in msg and p2.stopped == 0 and p2.deleted == ['j2'], '纯口播渲染（voice._wait）走同一个等待：超时叫停并删掉队列里这次的任务；正常渲完照旧取状态、删任务')
src_e, src_w = inspect.getsource(resolve.export), inspect.getsource(voice._wait)
check("wait_render(p, jid, 7200, '导出', 3)" in src_e and 'IsRenderingInProgress' not in src_e and "wait_render(p, jid, limit, '渲纯口播', 2)" in src_w
      and inspect.signature(voice._wait).parameters['limit'].default == 3600, '时限和轮询间隔没变：导出 7200 秒/3 秒一查，纯口播 3600 秒/2 秒一查')

# project(write=True) 遇到达芬奇正在渲染
film = TMP / '项目' / '02_制作' / '自动剪辑'; film.mkdir(parents=True)
wj(film / 'job.json', {'format': '3:4', 'frames': 1200, 'main_timeline': 'v1', 'source': '/x', 'project': 'p'})
job = Job(film); keep_connect = resolve.connect
try:
    busy = FakeP(); resolve.connect = lambda: busy
    msg = stops(resolve.project, job)
    ro = resolve.project(job, write=False)
    check(msg and '达芬奇正在渲染' in msg and '没动它' in msg and busy.stopped == 0 and busy.saves == 0, '达芬奇正在渲染时，写达芬奇的入口 project(write=True) 被拦，说清楚这次没动它（也不替用户叫停）')
    check(ro[2] is busy, '只读查询 project(write=False)：达芬奇在渲染也不拦')
    other = FakeP(name='别的工程'); resolve.connect = lambda: other
    msg = stops(resolve.project, job)
    check(msg and '达芬奇正在渲染' in msg and other.saves == 0, '达芬奇开着别的工程在渲染：同样被拦，而且拦在保存、切工程之前')
    idle = FakeP(polls=0); resolve.connect = lambda: idle
    check(resolve.project(job)[2] is idle and Path(os.environ['THINKCUT_RESOLVE_LOCK']).exists(), '达芬奇没在渲染：project(write=True) 照常拿到工程（锁用的是测试的临时文件）')
finally:
    resolve.connect = keep_connect; resolve.unlock()
check(all('project(job)' in inspect.getsource(f) for f in (resolve._review, resolve.subfix, resolve.export, resolve.stills, resolve.set_format)) and 'R.project(job)' in inspect.getsource(voice.run_cmd)
      and 'write=False' in inspect.getsource(resolve.snap), 'review、subfix、export、stills、set-format、voice-polish 都经过 project(write=True)；snap 是只读')

# ═════════ 4. 达芬奇返回空不崩 ═════════
t = T12.ResolveGuardTests('test_export_refuses_main_timeline'); t.setUp()
try:
    called = []
    resolve.project = lambda job: called.append(1) or (_ for _ in ()).throw(AssertionError('不该开达芬奇'))
    wj(t.d / '合成' / '字幕' / '字幕表.json', [])
    msg = stops(resolve.review, t.job)
    check(msg and '字幕表.json 是空的' in msg and '重跑 subs' in msg and not called, '字幕表是空的（0 条）：建审核线前就停下说原因，不再 IndexError，也没开达芬奇')
finally:
    t.doCleanups()
src_r = inspect.getsource(resolve._review)
bare = [l.strip()[:60] for l in src_r.splitlines() if 'GetItemListInTrack(' in l and l.count('GetItemListInTrack(') != l.count(') or []')]
check(not bare and "sorted(tl.GetItemListInTrack('audio', ai) or []" in src_r, '_review 里每一处 GetItemListInTrack 都带 or []（达芬奇返回空不崩）' + (f'：还有 {bare}' if bare else ''))

# ═════════ 2. 外部程序有时限 ═════════
HF = load(A / 'hyperframes_template' / '出片.py', 'hf_chupian')
r = HF.call([sys.executable, '-c', 'import sys; print("好"); sys.stderr.write("错")'], 30, '试')
check(r == (0, '好\n', '错'), 'HyperFrames 出片：正常结束的命令照常拿到退出码和输出')
pidf = TMP / 'pids.txt'
hang = f"import os, subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); open({str(pidf)!r}, 'w').write(f'{{os.getpid()}} {{c.pid}}'); time.sleep(120)"
t0 = time.time(); msg = stops(HF.call, [sys.executable, '-c', hang], 1.5, 'B01：hyperframes check'); dt = time.time() - t0; time.sleep(0.3)
pids = [int(x) for x in pidf.read_text().split()] if pidf.exists() else []
check(msg and 'B01：hyperframes check 超过' in msg and 'THINKCUT_TIMEOUT_X' in msg and dt < 20 and len(pids) == 2 and not any(alive(x) for x in pids),
      f'HyperFrames 出片：命令卡住超过时限就停下说原因，并把它和它开的子进程一起结束（{dt:.1f} 秒，进程 {pids}）')
seen = []; keep_call, keep_here = HF.call, HF.HERE
try:      # 用临时工程真的走一遍 page()：它生成页面时把这个镜头整段出片的时限算好
    HF.call = lambda cmd, limit, what: seen.append((cmd[1], limit)) or (0, json.dumps({'runtime': {'findings': []}}), '')
    hf_film = TMP / 'HF片'; hf_proj = hf_film / 'HF_试'; shutil.copytree(A / 'hyperframes_template', hf_proj, ignore=shutil.ignore_patterns('__pycache__', 'index.html*', 'node_modules'))
    shutil.copy(hf_proj / '镜头' / '示例.html', hf_proj / '镜头' / 'B01.html')
    wj(hf_film / 'job.json', dict(fps='30', format='3:4', frames=900, project='p', main_timeline='v1', source='/x'))
    HF.HERE = hf_proj; hf_job = Job(hf_film); hf_B = dict(anchors={}, shots=[dict(id='B01', kind='B', a=1.0, b=11.0)])
    HF.preflight('hyperframes', 'B01'); HF.run(['hyperframes', 'snapshot'])
    HF.page(hf_job, hf_B, 'B01', 50); HF.run(['hyperframes', 'render']); HF.page(hf_job, hf_B, 'B01', 100, True); HF.run(['hyperframes', 'render'])
finally: HF.call, HF.HERE = keep_call, keep_here
src_h = (A / 'hyperframes_template' / '出片.py').read_text(encoding='utf-8')
check(seen == [('check', 300), ('snapshot', 300), ('render', 600 + 300 * 5), ('render', 600 + 300 * 20)] and HF.render_limit(300, 100) == 6600 and HF.TIMEOUT_X == 1
      and 'subprocess.run([exe' not in src_h and src_h.count("run([exe, 'render'") == 2 and src_h.count("run([exe, 'snapshot'") == 1,
      f'HyperFrames 时限：check 300 秒、snapshot 300 秒、render 600 秒＋每帧 5 秒（4K 每帧 20 秒；300 帧的镜头 = 2100 / 6600 秒）；check、snapshot、两处 render 都走限时的 call：{seen}')

BL = load(A / 'blender_template' / '出片.py', 'bl_chupian')
fake = TMP / '假Blender.sh'
fake.write_text('#!/bin/sh\nfor a; do f=$a; done\nif grep -q 卡住 "$f"; then sleep 120; fi\nif grep -q 出错 "$f"; then exit 1; fi\nexit 0\n', encoding='utf-8'); fake.chmod(0o755)
procs = []; keep_popen = subprocess.Popen; keep_bl = (BL.BLENDER, BL.BLENDER_BASE, BL.BLENDER_PER_FRAME)


class Rec(keep_popen):
    def __init__(s, *a, **k): super().__init__(*a, **k); procs.append(s)


try:
    BL.BLENDER = str(fake); BL.subprocess.Popen = Rec
    cache = TMP / '缓存1'; cache.mkdir(); t0 = time.time()
    ok_none = stops(BL.run_blender, [dict(shot='好', indices=[0, 1]), dict(shot='好', indices=[2])], cache)
    msg_bad = stops(BL.run_blender, [dict(shot='好', indices=[0]), dict(shot='出错', indices=[1])], cache)
    check(ok_none is None and msg_bad and 'Blender 进程出错' in msg_bad and 'blender_出错_1.log' in msg_bad and time.time() - t0 < 30, 'Blender 出片：正常渲完、有进程出错这两种情况和以前一样')
    BL.BLENDER_BASE, BL.BLENDER_PER_FRAME = 1.5, 0; procs.clear(); t0 = time.time()
    msg = stops(BL.run_blender, [dict(shot='卡住', indices=[0, 1]), dict(shot='好', indices=[2]), dict(shot='卡住', indices=[3])], cache); dt = time.time() - t0
    check(msg and '还没结束' in msg and '2 个 Blender 进程全部结束' in msg and 'blender_卡住_0.log' in msg and 'blender_卡住_2.log' in msg and 'blender_好_1.log' not in msg
          and len(procs) == 3 and all(q.poll() is not None for q in procs) and dt < 20, f'Blender 出片：等超过时限就把还在跑的 Blender 进程全部结束，说清楚卡住的是哪几个日志（{dt:.1f} 秒）')
finally:
    BL.subprocess.Popen = keep_popen; BL.BLENDER, BL.BLENDER_BASE, BL.BLENDER_PER_FRAME = keep_bl
check(BL.blender_limit([dict(indices=list(range(150)))] * 3 + [dict(indices=list(range(140)))]) == 600 + 150 * 120 and BL.blender_limit([dict(indices=[0])]) == 720 and BL.TIMEOUT_X == 1,
      'Blender 时限：600 秒＋最大那一份的帧数×120 秒（150 帧一份 = 18600 秒；冒烟 1 帧 = 720 秒）')
r = subprocess.run([sys.executable, '-B', '-c', 'import importlib.util as u, sys\nfor n in sys.argv[1:]:\n    s = u.spec_from_file_location("m", n); m = u.module_from_spec(s); s.loader.exec_module(m); print(m.TIMEOUT_X)',
                    str(A / 'hyperframes_template' / '出片.py'), str(A / 'blender_template' / '出片.py')], capture_output=True, text=True, env=dict(os.environ, THINKCUT_TIMEOUT_X='2.5'), stdin=subprocess.DEVNULL)
check(r.stdout.split() == ['2.5', '2.5'], '两个模板的时限都能用环境变量 THINKCUT_TIMEOUT_X 整体放大' + ('' if r.returncode == 0 else '：' + r.stderr[-300:]))

# ═════════ 3. 写死的路径改成自动找 ═════════
vol = TMP / 'Volumes'; alt = vol / 'work-1' / 'ThinkCut' / 'studio' / 'skill' / 'thinkcut-edit'; alt.mkdir(parents=True); (vol / 'workshop').mkdir()
for name, M in (('HyperFrames', HF), ('Blender', BL)):
    got_env = M.find_skill(env='/别处/thinkcut-edit', fixed=str(TMP / '没有'), volumes=str(vol))
    got_fixed = M.find_skill(env='', fixed=str(SK), volumes=str(vol))
    got_alt = M.find_skill(env='', fixed=str(TMP / '没有'), volumes=str(vol))
    msg = stops(M.find_skill, env='', fixed=str(TMP / '没有'), volumes=str(TMP / '空'))
    check(got_env == Path('/别处/thinkcut-edit') and got_fixed == SK and got_alt == alt and msg and 'NAS 没挂上' in msg and 'THINKCUT_SKILL' in msg and M.SKILL == SK,
          f'{name} 模板的 出片.py 找能力目录：环境变量 → 写死的 NAS 路径 → /Volumes/work*（work-1 也行）→ 都没有就说 NAS 没挂上')
AI = ROOT.parent if ROOT.name in ('studio', '口播自动剪辑') else (Path.home() / '.claude' / 'skills' / 'thinkcut-edit').resolve().parents[2]     # 仓库被拷到别处时按技能链接找
check(common.THINKCUT_DIR == AI and common.DEFAULTS['sfx_library'] == str(AI / '素材库' / '音效素材')
      and common.DEFAULTS['sfx_favorites'] == str(AI / 'core' / 'engine' / 'config' / 'sfx_favorites.json')
      and rendang.FONT_DIR == AI / '素材库' / '字体' and rendang.MATTING_REPO == str(AI / 'core'),
      '音效库、音效收藏表、人挡字字体、抠人程序的默认位置都按本仓库所在的程序目录推（现在这台和原来写死的一样）')
c2 = alt / 'scripts' / 'thinkcut'; c2.mkdir(parents=True); p2 = alt / 'assets' / '包装系统'; p2.mkdir(parents=True)
for f in ('common.py', 'formats.py'): shutil.copy(SK / 'scripts' / 'thinkcut' / f, c2 / f)
(c2 / '__init__.py').write_text(''); shutil.copy(A / '包装系统' / 'rendang.py', p2 / 'rendang.py')
# 2026-11-24 配置档：common.py 要从 <程序目录>/bin/thinkcut_config.py 读配置，模拟的 work-1/ThinkCut 也带上这个模块和 profiles/（只补齐假目录，检查的内容不变）
(vol / 'work-1' / 'ThinkCut' / 'bin').mkdir(parents=True); shutil.copy(ROOT.parent / 'bin' / 'thinkcut_config.py', vol / 'work-1' / 'ThinkCut' / 'bin')
shutil.copytree(ROOT.parent / 'profiles', vol / 'work-1' / 'ThinkCut' / 'profiles')
far = TMP / '别处' / 'x' / 'y' / 'z' / 'w' / 'v'; far.mkdir(parents=True); shutil.copy(A / '包装系统' / 'rendang.py', far / 'rendang.py')
code = ('import sys, json; sys.path[:0] = sys.argv[1:4]\nfrom thinkcut import common\nimport rendang\n'
        'print(json.dumps([common.DEFAULTS["sfx_library"], common.DEFAULTS["sfx_favorites"], str(rendang.FONT_DIR), rendang.MATTING_REPO]))')
env = {k: v for k, v in os.environ.items() if k != 'THINKCUT_FONT_DIR'}
r1 = subprocess.run([sys.executable, '-B', '-c', code, str(alt / 'scripts'), str(p2), ''], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
r2 = subprocess.run([sys.executable, '-B', '-c', code, str(alt / 'scripts'), str(far), ''], capture_output=True, text=True, env=dict(env, THINKCUT_FONT_DIR='/我的字体'), stdin=subprocess.DEVNULL)
ai2 = (vol / 'work-1' / 'ThinkCut').resolve()
g1 = json.loads(r1.stdout) if r1.returncode == 0 else r1.stderr[-300:]; g2 = json.loads(r2.stdout) if r2.returncode == 0 else r2.stderr[-300:]
check(g1 == [str(ai2 / '素材库' / '音效素材'), str(ai2 / 'core' / 'engine' / 'config' / 'sfx_favorites.json'), str(ai2 / '素材库' / '字体'), str(ai2 / 'core')],
      'NAS 挂成 work-1（模拟）：这些默认位置跟着变，不再指着 /Volumes/work' + ('' if isinstance(g1, list) and g1[3] == str(ai2 / 'core') else f'：{g1}'))
check(isinstance(g2, list) and g2[2:] == ['/我的字体', str((Path.home() / '.claude' / 'skills' / 'thinkcut-edit').resolve().parents[2] / 'core')], f'环境变量 THINKCUT_FONT_DIR 照旧优先；代码被拷到别处（不在程序目录里）时按这台 Mac 的技能链接找：{g2[2:] if isinstance(g2, list) else g2}')

# 花字苹方
FACES = {}      # 假字体：路径 → 各序号的 (字体名, 字重)
keep_face, keep_ff, keep_globs = huazi._face, huazi.FONT_FILE, huazi.PINGFANG_GLOBS
STD = [(fam, st) for st in ('Regular', 'Medium', 'Semibold', 'Light') for fam in ('PingFang HK', 'PingFang MO', 'PingFang TC', 'PingFang SC')]
fd = TMP / '字体'; made = {}
for sub, faces in (('原位', STD), ('a', [('Hiragino Sans GB', 'W3'), ('Hiragino Sans GB', 'W6')]), ('b', [('PingFang SC', 'Light'), ('PingFang SC', 'Semibold'), ('PingFang SC', 'Medium'), ('PingFang SC', 'Regular'), ('PingFang HK', 'Regular')]),
                   ('c', [('PingFang SC', 'Regular'), ('PingFang SC', 'Medium')])):
    (fd / sub).mkdir(parents=True); f = fd / sub / 'PingFang.ttc'; f.write_bytes(b'x'); FACES[str(f)] = faces; made[sub] = str(f)
try:
    real = huazi.find_pingfang()
    check(real is None or (real[1] == huazi.WEIGHT if real[0] == keep_ff else set(real[1]) == set(huazi.WEIGHT)) , f"这台 Mac 上的苹方：{'没装（跳过）' if real is None else real[0][-40:] + ' 序号 ' + str(real[1])}"
          + ('；写死的路径在，照旧用它、序号不变' if real and real[0] == keep_ff else ''))
    huazi._face = lambda path, i: (FACES.get(path) or [])[i] if 0 <= i < len(FACES.get(path) or []) else None
    g_fixed = huazi.find_pingfang(fixed=made['原位'], globs=(str(fd / '[ab]' / 'PingFang.ttc'),))
    g_found = huazi.find_pingfang(fixed=str(fd / '没有.ttc'), globs=(str(fd / '[abc]' / 'PingFang.ttc'),))
    g_none = huazi.find_pingfang(fixed=str(fd / '没有.ttc'), globs=(str(fd / '[ac]' / 'PingFang.ttc'),))
    check(g_fixed == (made['原位'], huazi.WEIGHT), '花字苹方：写死的路径在就用它，字重序号还是 3/7/11/15（结果不变）')
    check(g_found == (made['b'], {'regular': 3, 'medium': 2, 'semibold': 1, 'light': 0}), f'花字苹方：写死的路径不在，按文件名找；文件名叫 PingFang.ttc 但字体名不是苹方的不用；序号对不上的按字体名找：{g_found and g_found[1]}')
    huazi.FONT_FILE = str(fd / '没有.ttc'); huazi.PINGFANG_GLOBS = (str(fd / '[ac]' / 'PingFang.ttc'),)
    msg = stops(huazi.font, 64)
    check(g_none is None and msg and '苹方' in msg and '字体册' in msg, '花字苹方：都找不到（或字重不齐）时停下说缺苹方、去“字体册”下载')
    huazi.PINGFANG_GLOBS = (str(fd / '[b]' / 'PingFang.ttc'),)
    ok_missing, o_missing = said(doctor.run)
finally:
    huazi._face, huazi.FONT_FILE, huazi.PINGFANG_GLOBS = keep_face, keep_ff, keep_globs; huazi._PINGFANG.clear()

# Chrome
if shutil.which('node'):
    js = ("const m = await import(process.argv[1]); const c = m.chromeCandidates('/家');"
          "console.log(JSON.stringify([m.findChrome(c, p => true), m.findChrome(c, p => p.startsWith('/家') && p.includes('Chrome')), m.findChrome(c, p => p.includes('Chromium')),"
          "m.findChrome(c, p => p.includes('Edge')), m.findChrome(c, p => false), c.length, m.NO_CHROME]))")
    r = subprocess.run(['node', '--input-type=module', '-e', js, str(TOOLS / 'chrome.mjs')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    g = json.loads(r.stdout) if r.returncode == 0 else [r.stderr[-300:]]
    check(g[:5] == ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/家/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Chromium.app/Contents/MacOS/Chromium',
                    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', None] and '找不到 Chrome' in g[6],
          f'截图工具找浏览器：先 /Applications 的 Chrome（和以前一样），再 ~/Applications、Chromium、Edge；都没有返回空：{g[:5]}')
    r = subprocess.run(['node', '--check', str(TOOLS / 'cap.mjs')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    check(r.returncode == 0, 'cap.mjs 语法没问题' + r.stderr[-200:])
else:
    check(True, '（这台没有 node，跳过找浏览器的运行检查）')
cap = (TOOLS / 'cap.mjs').read_text(encoding='utf-8')
check('const CHROME = findChrome();' in cap and "const CHROME = '/Applications" not in cap and 'if (!CHROME) { console.error(NO_CHROME' in cap and 'process.exit(1)' in cap.split('if (!CHROME)')[1].split('\n')[0]
      and cap.index('if (!CHROME)') < cap.index('mkdtempSync(join('), 'cap.mjs 不再写死 Chrome 路径；找不到时先中文报错退出，不留临时目录')

# 体检：苹方、环境比对
runs = {}
try:
    for key, script in (('一致', fake_sync('一致.sh', 0, '环境一致：假的')), ('不一致', fake_sync('不一致.sh', 4, '环境不一致：只在 NAS 原件：torch-9.9')), ('读不到', fake_sync('读不到.sh', 1, '读不到 NAS 上的环境原件')),
                        ('没脚本', str(TMP / '没有这个脚本.sh'))):
        os.environ['THINKCUT_SYNC_ENV'] = script; runs[key] = said(doctor.run)
finally: os.environ['THINKCUT_SYNC_ENV'] = fake_sync('默认一致.sh', 0, '环境一致：假的')
line = lambda out, word: next((l for l in out.splitlines() if word in l), '')
envl = {k: line(o, '程序环境') for k, (_, o) in runs.items()}
check(envl['一致'].startswith('✓ ') and envl['不一致'].startswith('· ') and 'torch-9.9' in envl['不一致'] and envl['读不到'].startswith('· ') and '读不到 NAS' in envl['读不到'] and envl['没脚本'].startswith('· ')
      and len({ok for ok, _ in runs.values()}) == 1 and len({o.splitlines()[-1] for _, o in runs.values()}) == 1,
      '体检：比对 NAS 环境原件和 Mac 运行副本（用假脚本）；不一致、读不到、脚本不在都只是提醒（·），把脚本的输出显示出来，不改变“就绪/没就绪”的结论')
pf_ok, pf_bad = line(runs['一致'][1], '苹方'), line(o_missing, '苹方')
keep_fp = doctor.find_pingfang
try:
    doctor.find_pingfang = lambda: None; ok_no, o_no = said(doctor.run)
    doctor.find_pingfang = lambda: '/x/PingFang.ttc'; ok_yes, o_yes = said(doctor.run)
finally: doctor.find_pingfang = keep_fp
check(line(o_no, '苹方').startswith('✗ ') and '字体册' in line(o_no, '苹方') and ok_no is False and line(o_yes, '苹方').startswith('✓ ') and pf_bad.startswith('✓ ')
      and (pf_ok.startswith('✓ ') if real else pf_ok.startswith('✗ ')), '体检：花字用的苹方字体找不找得到（找不到算没就绪，提示去“字体册”下载；用的是花字同一个找法，换了位置的苹方也认）')

# ═════════ 5. 审片工作台只接受本页面的请求 ═════════
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_workbench.ApiTests.test_post_only_from_own_page'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
check(r.returncode == 0, '审片工作台 POST：本页面来源放行、别的网站来源 403（没跳转、没删标记）、没有 Origin 放行、Host 不是本机 403（test_workbench.test_post_only_from_own_page）'
      + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-600:]))

# ═════════ 6. 老工程的 build.py 不读 vendor/pp（模板本身 2026-11-21 第二批已整个下线，这里用最小的老工程夹具；下线和重出老片子的测试在 test_audit_batch2.py） ═════════
T = ROOT / 'samples' / '老代码动画工程_最小' / '动画'; bsrc = (T / 'build.py').read_text(encoding='utf-8')
check('vendor/pp' not in bsrc and 'PP_ORDER' not in bsrc and 'data-pp' not in bsrc and not (T / 'vendor' / 'pp').exists()
      and (T / 'vendor' / 'three.module.min.js').exists() and (T / 'vendor' / 'RoomEnvironment.js').exists(), '老代码动画工程（最小夹具）：build.py 不读 vendor/pp，three.js（占位）和环境光文件在')
w = TMP / 'job'; w.mkdir(); shutil.copytree(T, w / '动画'); shutil.copy(ROOT / 'samples' / '烟道反味' / '分镜.json', w / '分镜.json')
r = subprocess.run([sys.executable, '-B', str(w / '动画' / 'build.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
html = (w / '动画' / '动画.html').read_text(encoding='utf-8') if (w / '动画' / '动画.html').exists() else ''
check(r.returncode == 0 and 'window.BEATS_DATA' in html and 'RoomEnvironment' in html and 'data-pp' not in html, '老代码动画工程（最小夹具）照常 build 出 动画.html（没有 vendor/pp 也行）' + r.stderr[-300:])

# ═════════ 7. 文档 ═════════
R = SK / 'references'; rd = lambda f: Path(f).read_text(encoding='utf-8')
fg, zf, hist = rd(R / '动画风格.md'), rd(R / '动画做法.md'), rd(R / '历史做法.md')      # 2026-11-21 第二批：老做法和旧风格搬到 历史做法.md（代码动画工程.md 整篇并进去）
sk, ag = rd(SK / 'SKILL.md'), rd(ROOT / 'AGENTS.md')
check('下文是 10-23 上午定的旧风格，仅作历史参考。' not in fg and '10-23 上午定的旧风格三节（电影暗调、暗场悬浮爆炸）只是历史参考，已搬到 历史做法.md' in fg
      and '「物理必须对」「定稿规则」「被否掉的方向」三节仍然有效' in fg and all(h in fg for h in ('## 物理必须对', '## 定稿规则', '## 被否掉的方向'))
      and '## 主风格：电影暗调' not in fg and '### 主风格：电影暗调' in hist and '仅作历史参考' in hist,
      '说明 a：动画风格.md 写清了哪三节只是历史参考（已搬到 历史做法.md）、哪三节（含定稿规则）仍然有效、还在原处')
check('HyperFrames 的 `出片.py` 没有标签逐帧自检' in fg and '（Blender）标签逐帧自检' in fg, '说明 b：动画风格.md 不再说两种做法都有标签逐帧自检（HyperFrames 没有，和动画做法.md 一致）')
check('普通花字优先剪辑页 Text+。' not in sk and '要在达芬奇里能改字、要跟着物体走的花字' in sk and '其余花字和卡片用包装系统' in sk,
      '说明 c：SKILL.md 写清 Text+ 只管要在达芬奇里改字、跟随物体的字，其余花字和卡片用包装系统')
check(hist.count('仅老片子') >= 6 and '**仅老片子。**' in hist and '仅老片子' not in zf and not (R / '代码动画工程.md').exists()
      and any('animation_template' in l and '下线' in l and '历史做法.md' in l for l in rd(ROOT / 'README.md').split('\n')),
      '说明 d：讲老代码动画的段落（原来标“仅老片子”的 6 处、代码动画工程.md 整篇）都搬到了 历史做法.md，内容没删；动画做法.md 里不再有；README 写明模板已下线')
check('需要 `danger-full-access`' not in ag and any('`anim-*`' in l and '都由 Claude 跑，不交给 Codex' in l for l in ag.split('\n')) and 'Codex 只用于生图' in ag
      and 'Claude 和 Codex 用同一套' not in ag, '说明 e：AGENTS.md 的 anim-* 等命令写成由 Claude 跑，和“Codex 只用于生图”一致（不再说“Claude 和 Codex 用同一套”）')

shutil.rmtree(TMP, ignore_errors=True)
print('体检止血测试全部通过' if not fails else f'体检止血测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
