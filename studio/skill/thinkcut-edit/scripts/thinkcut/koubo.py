# -*- coding: utf-8 -*-
# 审片台“口播剪辑”栏的文字剪辑器（2026-11-22 用户定：最早剪 A-roll 的审核网页并进审片台“口播剪辑”栏，用改文字的方法做剪辑）。
# 删句、恢复、调顺序、改字幕、分段、撤销重做、预览片段、保存到达芬奇，全部还是口播剪辑（core/engine/src/review_v2_server.py，下称“剪口播后台”）做；
# 本文件只管：按需起／复用剪口播后台（edit_project.py serve --backend，一个项目一个进程），把审片台页面发来的请求转给它，
# 回话（含 409 冲突、保存进度、报错文字、预览片段的分段读）原样交回页面。审片台自己先查同源＋口令，转发时按后台要求带 Host／Origin。
import contextlib, http.client, json, os, re, socket, subprocess, sys, threading
from pathlib import Path
from .common import THINKCUT_DIR

# 页面要的地址：照剪口播后台原样（页面里的写法不用改）
GET = re.compile(r'^/(editor-api|resolve-status|editor-page-info|caption-engine\.js|caption-text\.js|preview-source\.js|resolve-sync\.js|source-part/\d+\.mp4)$')
POST = ('/editor-action', '/subtitle-corrections', '/paragraph-break', '/save-to-resolve')
PASS = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Cache-Control')


class Fail(Exception):
    """起不来、连不上剪口播后台（给页面看的中文）。"""


def bin_dir():
    """口播剪辑的程序（edit_project.py）：环境变量 THINKCUT_AROLL_BIN 优先（开发时指到 worktree 的 bin），否则是兄弟目录 <程序目录>/core/bin。"""
    return Path(os.environ.get('THINKCUT_AROLL_BIN') or THINKCUT_DIR / 'core' / 'bin').expanduser()


def ready(aroll):
    """这个剪口播工程能开文字剪辑器吗：审核状态和工程设置都在（剪过、出过审核）。"""
    if not aroll: return False
    r = Path(aroll) / '审核'
    return (r / 'review_v2_state.json').is_file() and (r / 'project_runtime.json').is_file()


def info(aroll):
    """给页面的口播信息：有没有剪口播工程、在哪、没有的话为什么。"""
    if not aroll: return {'ready': False, 'why': '这条片子还没有剪口播工程（还没录、还没剪）'}
    a = Path(aroll)
    if not a.is_dir(): return {'ready': False, 'dir': str(a), 'why': f'剪口播工程不在了：{a}'}
    if not ready(a): return {'ready': False, 'dir': str(a), 'why': '剪口播工程还没剪完（还没有审核状态），剪完自动显示文字剪辑器'}
    return {'ready': True, 'dir': str(a), 'name': a.name}


class Backend:
    """一条片子的剪口播后台。端口从后台自己写的 审核/审核网址.txt 读；没在跑（或连不上）就用 edit_project.py serve --backend 起。"""
    def __init__(self, aroll):
        self.aroll = Path(aroll); self.run = self.aroll / '审核'; self.starting = threading.Lock()

    def port(self):
        try: d = json.loads((self.run / '审核网址.txt').read_text())
        except (OSError, ValueError): return None
        if d.get('host') not in (None, socket.gethostname()): return None       # 在别的 Mac 上跑：交给 start 说清楚
        m = re.match(r'http://127\.0\.0\.1:(\d+)/', d.get('url') or '')
        return int(m[1]) if m else None

    def start(self, seen=None):
        """起后台（已在跑就复用）。seen：调用方刚连不上的端口；别的线程已经起好了就不再起。"""
        with self.starting:
            p = self.port()
            if p and p != seen and alive(p): return p
            entry = bin_dir() / 'edit_project.py'
            if not entry.is_file(): raise Fail(f'找不到口播剪辑的程序 {entry}（开发时用环境变量 THINKCUT_AROLL_BIN 指到它的 bin）')
            env = {k: v for k, v in os.environ.items() if k not in ('AI_EDIT_RUN_DIR', 'AI_EDIT_PORT')}
            try:
                r = subprocess.run([sys.executable, '-B', str(entry), 'serve', '--backend', '--no-open', '--project-dir', str(self.aroll)],
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180, env=env)
            except subprocess.TimeoutExpired: raise Fail('剪口播后台 3 分钟没起来（NAS 慢或原片很大），稍等刷新再试') from None
            if r.returncode:
                msg = (r.stderr.strip() or r.stdout.strip() or f'退出码 {r.returncode}').splitlines()
                raise Fail('剪口播后台没起来：' + ' '.join(msg[-3:])[:400])
            p = self.port()
            if not p: raise Fail('剪口播后台起了，但没留下地址（审核/审核网址.txt）')
            return p

    def open(self, method, path, body=None, headers=None, timeout=200):
        """发给后台，返回 (连接, 回话)。连不上（还没发出去）才起后台再发一次：写入类请求不会重复。"""
        p = self.port() or self.start()
        for k in range(2):
            c = http.client.HTTPConnection('127.0.0.1', p, timeout=timeout)
            h = {'Host': f'127.0.0.1:{p}', **(headers or {})}
            if method == 'POST': h.update({'Origin': f'http://127.0.0.1:{p}', 'Content-Type': 'application/json'})
            try:
                c.connect()
            except OSError:
                c.close()
                if k: raise Fail('连不上剪口播后台，稍等刷新再试') from None
                p = self.start(seen=p); continue
            try:
                c.request(method, path, body=body, headers=h); return c, c.getresponse()
            except (OSError, http.client.HTTPException) as e:
                c.close(); raise Fail(f'剪口播后台没回话（{e.__class__.__name__}）：刷新页面核对改动有没有存上') from None

    def page_info(self):
        try:
            c, r = self.open('GET', '/editor-page-info', timeout=30)
            with contextlib.closing(c): return json.loads(r.read()) if r.status == 200 else {}
        except (Fail, ValueError): return {}


def alive(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(('127.0.0.1', port)) == 0


def pointer(aroll):
    """剪口播工程里记的“这条片子的审片台在哪”：审核/审片台.json（审片台写；剪口播后台直接打开时据此给出审片台地址）。"""
    f = Path(aroll) / '审核' / '审片台.json'
    try: return json.loads(f.read_text())
    except (OSError, ValueError): return {}


def write_pointer(aroll, film_dir, url):
    if not aroll or not (Path(aroll) / '审核').is_dir(): return
    f = Path(aroll) / '审核' / '审片台.json'
    try:
        f.write_text(json.dumps({'说明': '这条剪口播工程属于哪个审片台（thinkcut_edit.py workbench 写）；剪口播的网页已并入审片台“口播剪辑”栏（2026-11-22）',
                                 'film_dir': str(film_dir), 'url': url}, ensure_ascii=False, indent=1), encoding='utf-8')
    except OSError: pass
