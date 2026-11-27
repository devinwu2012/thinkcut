#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审片台代办（2026-11-26 架构审查定：后台 AI 不拿钥匙原文；Codex 命令关网，连本机回环也不通）。

后台 AI（审片台起的 Claude Code／Codex，环境里有 THINKCUT_AI_BACKGROUND）跑下面这些我们自己的程序时，程序入口不在 AI 那边跑，
而是把“跑哪个程序、什么参数、在哪个文件夹”写成一张单子放进代办文件夹（THINKCUT_BROKER_DIR），
审片台服务（不在 AI 的沙盒里：能读钥匙串、能连本机达芬奇、能联网）照单跑，把输出和退出码写回来，AI 只拿到结果。
走文件不走网络。

- 只代办白名单里的程序（SCRIPTS）：豆包识别／合成、蚁小二（yxer_secure 自己拒 publish）、所有要连达芬奇的入口、生图入口。
  程序路径由服务自己定，AI 给的只是名字。项目里复制的 出片.py 只有和程序里的模板一字不差才代跑（AI 改过的不代跑）。
- 工作目录和参数里的路径必须在项目文件夹（和临时目录）里。
- 环境用服务自己的，AI 那边的环境变量一个都不带过去（不能借环境变量换程序、换生图命令）。
- 整片导出、上传的闸（export_gate／final_gate）在程序里，代办照样过闸：只认用户在审片台页面上点的定稿。
- 输出里如果出现钥匙原文，换成 [已隐藏]。
- 每张单子一个线程（`edit_project.py serve` 这种常驻的不会堵住后面的）；用户点“停止”时 stop_all 结束代办起的进程。
"""
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ENV_DIR = 'THINKCUT_BROKER_DIR'
BACKGROUND = 'THINKCUT_AI_BACKGROUND'
PROGRAM = Path(__file__).resolve().parents[1]
EDIT = 'studio/skill/thinkcut-edit/scripts/thinkcut_edit.py'
COVER = 'cover/skill/thinkcut-cover/scripts/thinkcut_cover.py'
SCRIPTS = {
    # 钥匙：豆包识别、合成，蚁小二存草稿
    'doubao_tts.py': 'core/bin/doubao_tts.py', 'doubao_direct_asr.py': 'core/bin/doubao_direct_asr.py',
    'transcribe_video.py': 'core/bin/transcribe_video.py', 'yxer_secure.py': 'projects/bin/yxer_secure.py',
    # 达芬奇（本机脚本接口）：剪口播放进达芬奇、B-roll、花字音效、审核线／字幕／导航／导出等、存档
    'edit_project.py': 'core/bin/edit_project.py', 'broll.py': 'core/bin/broll.py', 'fx.py': 'core/bin/fx.py',
    'thinkcut_edit.py': EDIT, 'project.py': 'projects/bin/project.py',
    # 生图（命令里起 codex exec）
    'thinkcut_cover.py': COVER,
    # 挪进回收站（2026-11-27 对话框：不永久删，回收站在项目文件夹以外，AI 自己写不了）
    'thinkcut_trash.py': 'bin/thinkcut_trash.py',
}
TEMPLATES = {'出片.py': ('studio/skill/thinkcut-edit/assets/hyperframes_template', 'studio/skill/thinkcut-edit/assets/blender_template')}
WAIT_TAKEN = float(os.environ.get('THINKCUT_BROKER_TAKE') or 30)      # 这么久审片台还没接单：多半审片台没在跑
LIMIT = 8 * 3600
_WB_LOCAL = ('--reply', '--rewrite', '--publish', '--withdraw', '--list', '--round', '--stop', '--wait', '--add-material')      # --add-material：放素材只写项目文件夹（2026-11-27）


def background(environ=None):
    return bool((os.environ if environ is None else environ).get(BACKGROUND))


def active():
    return background() and bool(os.environ.get(ENV_DIR))


def local_ok(name, argv):
    """不用代办、就在 AI 那边跑的：审片台 workbench 的回写、发布、读清单（只读写项目里的文件）。"""
    return name == 'thinkcut_edit.py' and argv[:1] == ['workbench'] and any(a in argv for a in _WB_LOCAL)


def hook(script):
    """程序入口第一句：后台 AI 里就交给审片台代办并退出；不是后台 AI、或这一种不用代办，照常往下跑。"""
    argv = sys.argv[1:]
    if active() and not local_ok(Path(script).resolve().name, argv): sys.exit(forward(script, argv))


def _write(f, data):
    tmp = f.with_name(f.name + '.tmp'); tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8'); os.replace(tmp, f)


# ---------- AI 这边 ----------
def forward(script, argv, out=sys.stdout, err=sys.stderr):
    """把这次运行交给审片台代办，等结果；返回退出码。"""
    name = Path(script).resolve().name      # 旧名入口是软链接：按它指向的程序名认（名单里只列新名）
    if name not in SCRIPTS and name not in TEMPLATES: print(f'停止：{name} 不在审片台代办的名单里', file=err); return 2
    q = Path(os.environ[ENV_DIR]); q.mkdir(parents=True, exist_ok=True); rid = uuid.uuid4().hex[:12]
    _write(q / f'req-{rid}.json', {'id': rid, 'script': name, 'path': str(Path(script).resolve()), 'argv': [str(a) for a in argv], 'cwd': os.getcwd(), 'at': time.time()})
    print(f'（后台 AI 不直接跑 {name}：交给审片台代办（钥匙、达芬奇、联网都在那边），等结果…）', file=err, flush=True)
    t0 = time.time(); res = q / f'res-{rid}.json'; taken = q / f'run-{rid}.json'; said = 0
    while not res.exists():
        time.sleep(0.3)
        if not taken.exists() and time.time() - t0 > WAIT_TAKEN:
            with contextlib.suppress(OSError): (q / f'req-{rid}.json').unlink()
            print('停止：审片台没有接这张代办单（审片台服务可能没在跑）。请用户打开这个项目的审片台后再提交。', file=err); return 75
        if time.time() - t0 > LIMIT: print('停止：审片台代办超过 8 小时没结束', file=err); return 124
        if taken.exists() and time.time() - said > 60:
            said = time.time(); print(f'（审片台代办在跑 {name}，已 {int(time.time() - t0)} 秒）', file=err, flush=True)
    try: r = json.loads(res.read_text(encoding='utf-8'))
    except ValueError: r = {'code': 1, 'stdout': '', 'stderr': '审片台代办的结果读不了'}
    with contextlib.suppress(OSError): res.unlink()
    out.write(r.get('stdout') or ''); err.write(r.get('stderr') or ''); out.flush(); err.flush()
    return int(r.get('code', 1))


# ---------- 服务这边（审片台） ----------
def _inside(p, roots):
    p = Path(p).expanduser().resolve()
    return any(p == r or r in p.parents for r in roots)


def _sha(f): return hashlib.sha256(Path(f).read_bytes()).hexdigest()


def _template_copy(path, roots, program):
    """项目里复制的 出片.py：在项目里，且它和同一文件夹里与模板同名的 .py 都和程序里的某一套模板一字不差，才代跑（AI 改过的不代跑）。"""
    f = Path(path).resolve()
    if not _inside(f, roots) or f.name != '出片.py' or not f.is_file(): raise ValueError(f'出片.py 不在项目文件夹里：{f}')
    for t in TEMPLATES['出片.py']:
        tdir = program / t
        if (tdir / '出片.py').is_file() and all(_sha(x) == _sha(f.parent / x.name) for x in tdir.glob('*.py') if (f.parent / x.name).is_file()) \
                and _sha(tdir / '出片.py') == _sha(f):
            return f
    raise ValueError('项目里的 出片.py（或它旁边的 .py）和程序里的模板不一样（改过，或是旧版模板）：不代跑。请用户在对话里让 AI 跑，或先用新模板重建')


def check(req, roots, policy=None):
    """单子合不合规矩：白名单程序、工作目录和参数里的路径都在允许的文件夹里。返回 (程序, 工作目录) 或报 ValueError。
    policy(单子)：这个代办文件夹另加的限制（对话框的代办不跑导出、上传，2026-11-27），不合就报 ValueError。"""
    name = str(req.get('script') or '')
    if policy: policy(req)
    program = Path(os.environ.get('THINKCUT_BROKER_PROGRAM') or PROGRAM)       # 测试用替身程序目录
    roots = [Path(r).resolve() for r in roots]
    if name in TEMPLATES: prog = _template_copy(req.get('path') or '', roots, program)
    elif name in SCRIPTS: prog = program / SCRIPTS[name]
    else: raise ValueError(f'{name} 不在代办名单里')
    cwd = Path(str(req.get('cwd') or '')).resolve()
    if not _inside(cwd, roots): raise ValueError(f'工作目录不在项目文件夹里：{cwd}')
    for a in req.get('argv') or []:
        for v in (str(a), str(a).split('=', 1)[-1]):
            if v.startswith(('/', '~')) or v.startswith(('./', '../')) or '/../' in v or v == '..':
                if not _inside(v if v.startswith(('/', '~')) else cwd / v, roots): raise ValueError(f'参数里的路径不在项目文件夹里：{v}')
    return prog, cwd


def _secrets():
    """服务这边知道的钥匙（只用来从输出里抹掉）。"""
    out = []
    with contextlib.suppress(Exception):
        sys.path.insert(0, str(PROGRAM / 'bin')); sys.path.insert(0, str(PROGRAM / 'core' / 'bin'))
        import doubao_credentials
        k = doubao_credentials.load_key()
        if k: out.append(k)
    with contextlib.suppress(Exception):
        import keychain
        k = keychain.get('cn.thinkcut.yixiaoer.cli', 'api-key')
        if k: out.append(k)
    return out


_procs = {}            # 代办文件夹 → 正在跑的进程
_lock = threading.Lock()


def _run_one(q, rid, req, roots, log, policy=None):
    taken = q / f'run-{rid}.json'
    try:
        prog, cwd = check(req, roots, policy)
        env = {k: v for k, v in os.environ.items() if k not in (BACKGROUND, ENV_DIR, 'THINKCUT_BROKER_PROGRAM')}
        if log: log(f"代办 {req.get('script')} {' '.join(req.get('argv', [])[:2])}")
        p = subprocess.Popen([sys.executable, str(prog), *req.get('argv', [])], cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', start_new_session=True)
        with _lock: _procs.setdefault(str(q), set()).add(p)
        try: so, se = p.communicate(timeout=LIMIT); code = p.returncode
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception): os.killpg(p.pid, signal.SIGKILL)
            so, se = p.communicate(); code, se = 124, (se or '') + '停止：审片台代办超时\n'
        finally:
            with _lock: _procs.get(str(q), set()).discard(p)
        if code < 0: se = (se or '') + '停止：用户点了“停止”，这一步被结束了\n'
    except ValueError as e: code, so, se = 2, '', f'停止：审片台代办拒绝了这张单子：{e}\n'
    except Exception as e: code, so, se = 1, '', f'停止：审片台代办出错：{type(e).__name__}\n'
    for k in _secrets(): so, se = (so or '').replace(k, '[已隐藏]'), (se or '').replace(k, '[已隐藏]')
    _write(q / f'res-{rid}.json', {'code': code, 'stdout': so or '', 'stderr': se or ''})
    with contextlib.suppress(OSError): taken.unlink()


def serve_once(queue, roots, log=None, wait=False, policy=None):
    """接代办文件夹里还没接的单子，每张一个线程跑。返回接了几张（wait=True 时等它们跑完，测试用）。"""
    q = Path(queue)
    if not q.is_dir(): return 0
    threads = []
    for f in sorted(q.glob('req-*.json')):
        rid = f.stem[4:]
        try: req = json.loads(f.read_text(encoding='utf-8')); f.unlink()
        except (OSError, ValueError): continue
        _write(q / f'run-{rid}.json', {'at': time.time(), 'script': req.get('script')})
        t = threading.Thread(target=_run_one, args=(q, rid, req, roots, log, policy), daemon=True); t.start(); threads.append(t)
    if wait:
        for t in threads: t.join()
    return len(threads)


def stop_all(queue):
    """用户点“停止”：结束这个项目代办起的所有进程（连同它们的子进程）。"""
    with _lock: ps = list(_procs.get(str(Path(queue)), set()))
    for p in ps:
        with contextlib.suppress(Exception): os.killpg(p.pid, signal.SIGTERM)
    return len(ps)


# ---------- 对话框的代办（2026-11-27）：整片导出、上传发布、定稿只认用户在页面上亲手点，对话里说了也不跑 ----------
PAGE_ONLY = '只认你在页面上亲手点：整片导出在审片栏点“这一栏定了”，上传到蚁小二在发布栏点“存到蚁小二草稿”。对话里说了不算'


def chat_policy(req):
    """项目里的对话框：别的照常代办，导出、上传不跑。"""
    name = str(req.get('script') or ''); argv = [str(a) for a in req.get('argv') or []]
    sub = next((a for a in argv if not a.startswith('-')), '')
    if name == 'thinkcut_edit.py' and sub.startswith('export'): raise ValueError('导出整片' + PAGE_ONLY)
    if name == 'project.py' and sub in ('upload', 'copy-apply'): raise ValueError('上传' + PAGE_ONLY)
    if name == 'yxer_secure.py': raise ValueError('蚁小二' + PAGE_ONLY)


def home_policy(req):
    """首页的对话框：只代办建项目、整理、存档这些（project.py，除了上传）和挪进回收站；要剪辑、做封面先“接到”那个项目，用项目的对话。"""
    name = str(req.get('script') or '')
    if name not in ('project.py', 'thinkcut_trash.py'):
        raise ValueError(f'首页对话只代办 project.py（开项、整理、存档）和挪进回收站；{name} 要在项目里做：先进那个项目，在审片台的对话里说')
    chat_policy(req)
