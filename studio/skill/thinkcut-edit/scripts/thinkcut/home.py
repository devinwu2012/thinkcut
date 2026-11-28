# 首页（2026-11-24 用户定：“这个像工作树一样或者工作流一样”“最终的软件还没做出来呀”）：一个固定入口看到所有片子、新建片子，点一条就进它的审片台。
# 一个常驻的本机小服务（只听 127.0.0.1，固定端口 8760，thinkcut_edit.py home 起），页面 assets/首页/index.html。以后套程序外壳，窗口里装的就是它。
# - 片子列表从放片子的文件夹现读（默认 视频项目管理 的 VIDEO_ROOT，即配置档里默认 IP 的片子文件夹；--root 或环境变量 THINKCUT_VIDEO_ROOT 改）：每条片子自己的 项目.json、审片工作台/ 下的记录、job.json，
#   不另存索引。先出列表（每条只看几个文件的修改时间），每条的阶段和“轮到你／AI 在改”再一条条补（同审片台页面用的 workbench.page_data 算，数字和审片台顶栏一样）。
# - 点一条：首页按需起（或复用）那条片子的审片台服务（workbench.start，端口照旧自动找），双击就在同一个窗口里进到它（一次只开一条片子，2026-11-24 第二轮）。
#   2026-11-24 用户定方向 C：首页是四步看板（看板／列表两种看法），不再在首页里套内嵌框。
# - 右键一条片子（2026-11-24 用户：“片子删不掉”）：打开、在访达中显示、重命名、移到“做完的”／移回来、移到回收站。
#   删除不是永久删除：整个片子文件夹挪到同一块盘的 NAS 回收站 #recycle/ThinkCut/<项目名>_<时间>（macOS 废纸篓在网络盘上常常变成“立即删除”），能还原；回收站不在就停下不删。
# - 新建片子：只要一个题目（空着叫“未命名”），按 视频项目管理 project.py 的规矩建项目文件夹（编号、文件夹结构、项目.json），不登记达芬奇库（那是 AI 的事），直接进“文案”栏。
# - AI 在不在等：首页顶上看 AI 的“等所有片子”（home --wait）和每条片子的 --wait；home --wait 一条命令挂着，任何一条片子有人点“交给 AI”就打印是哪条片子的什么事并退出。
# - 切 IP（2026-11-24 用户定）：左上角菜单列出 profiles/ 里所有 IP，点一个就看那个 IP 的片子文件夹（配置档里各 IP 的“片子文件夹”）；菜单最下面“新建 IP…”只填名字，
#   建一层空白配置（其余跟公共层）并切过去。新建片子归当前 IP（项目.json 记 ip）。选的 IP 记在 profiles/本机.json 的 "首页IP"（不进存档），下次打开还是它；
#   没选过＝默认 IP（本机.json／公共.json 的 "默认IP"）。已经开着的审片台不受影响（各是各的服务）。
#   菜单里“删除 IP…”（2026-11-25 用户要）：IP 的配置挪到回收站（能撤销），它的片子文件夹不动；默认 IP 不能删；删的是正在看的就回默认 IP。
#   菜单里“回收站…”（2026-11-25 用户要）：列出回收站里删掉的片子（#recycle/ThinkCut/）和 IP（<回收站>/ThinkCut配置档/），点“还原”放回原处；
#   片子回到它 项目.json 里记的那个 IP 的片子文件夹（那个 IP 也被删了就先还原 IP）。永久删除不做，要清空在访达里自己处理。
#   给了 --root（或环境变量 THINKCUT_VIDEO_ROOT）＝钉死只看这一个文件夹，不能切（以前的用法、测试照旧）。
#   AI 的 home --wait 看所有 IP 的片子（不只当前 IP）：用户切到哪个 IP 点“交给 AI”，AI 都接得到。
# 新建、打开、改名、移动、删除、切 IP、新建 IP 这些只收首页页面自己发的请求（带页面口令），命令行不能替用户点。
import contextlib, fcntl, hashlib, html, http.cookies, json, os, re, signal, subprocess, sys, tempfile, threading, time, traceback, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .common import ASSETS, rj, say, die
from . import chat, design, board, engine, materials, settings, workbench as WB

PAGE = ASSETS / '首页' / 'index.html'
PORT = WB.HOME_PORT
FINISHED = 'finish'
STAGES = ('写文案', '出初稿', '改初稿', '发布')
TOKEN = os.urandom(12).hex()          # 页面口令：只放在 HttpOnly cookie 里（打开首页时发），页面脚本和命令行的普通请求都拿不到
SESSION = hashlib.sha256(TOKEN.encode()).hexdigest()[:16]    # 公开的“这一次首页服务”的编号：嵌在页面里，页面拿它判断首页重启了没有；不能拿来点东西
COOKIE = 'thinkcut_home'
HEARD = 8                    # AI 的 home --wait 两秒来问一次；8 秒没来就算没在等
MAX_TITLE = 120              # 题目最多多少字节（UTF-8）：文件夹名＝编号＋空格＋题目，删的时候还要加 _时间，不能超过文件名上限 255（QA 2.3）
SAME_NEW = 10                # 同一个题目这么多秒内又点了一次“新建”：给刚建的那条，不再建第二条（QA 2.1）

def cookie_header(x):
    """测试、自检用：带上本服务页面的 cookie（x＝端口或网址）。浏览器打开页面时服务自己发这个 cookie。"""
    port = x if isinstance(x, int) else int(str(x).rstrip('/').rsplit(':', 1)[1].split('/')[0])
    return f'{COOKIE}_{port}={TOKEN}'



class Oops(Exception):
    """给页面的中文错误；code 是给页面认的错误种类（页面按它决定怎么显示），extra 一起交给页面。"""
    def __init__(self, msg, code='', **extra):
        super().__init__(msg); self.code = code; self.extra = extra


class Refuse(Exception):
    pass


def video_root(root=None):
    """放片子的文件夹：--root ＞ 环境变量 THINKCUT_VIDEO_ROOT ＞ projects/bin/project.py 的 VIDEO_ROOT（配置档里默认 IP 的片子文件夹）。"""
    if root: return Path(root).expanduser().resolve()
    if os.environ.get('THINKCUT_VIDEO_ROOT'): return Path(os.environ['THINKCUT_VIDEO_ROOT']).expanduser().resolve()
    return Path(board._pm('project').VIDEO_ROOT).resolve()


def pinned(root=None):
    """给了 --root 或 THINKCUT_VIDEO_ROOT：首页钉死看这一个文件夹（不能切 IP）。"""
    return bool(root or os.environ.get('THINKCUT_VIDEO_ROOT'))


def cfg():
    """配置档模块（bin/thinkcut_config.py，project.py 导入时已经放进 sys.path）。"""
    return board._pm('project').thinkcut_config


def ip_root(ip):
    """这个 IP 的片子文件夹（配置档里的“片子文件夹”）。"""
    try: return Path(cfg().load(ip=ip).path('片子文件夹')).resolve()
    except SystemExit as e: raise Oops(str(e).replace('停止：', '')) from None


def first_ip():
    """首页一打开看哪个 IP：环境变量 THINKCUT_IP ＞ 上次在首页选的（本机.json 的 "首页IP"）＞ 默认 IP。"""
    c = cfg(); return os.environ.get('THINKCUT_IP') or c.home_ip() or c.default_ip()


def all_roots():
    """所有 IP 的片子文件夹（AI 的 home --wait 都看）。"""
    out = []
    for n in cfg().ips():
        with contextlib.suppress(Oops):
            r = ip_root(n)
            if r not in out: out.append(r)
    return out


def number(name):
    m = re.match(r'(\d{6})(?!\d)', name); return m.group(1) if m else ''


def _mt(p):
    try: return p.stat().st_mtime
    except OSError: return 0


def touched(d):
    """最后改动时间：项目.json、项目文案、审片台的几份记录、job.json 里最新的那个（只看修改时间，不读内容，NAS 上也快）。"""
    w = d / '02_制作' / '自动剪辑'; h = w / '审片工作台'
    return max(_mt(x) for x in (d / '项目.json', d / '03_成片' / f'{d.name}.md', w / 'job.json', h / '审片台.json', h / '标记.json', h / '写稿.json', h / '备料.json'))


def scan(root):
    """先出列表：每条片子的编号、标题、做完没有（在 finish/ 里）、最后改动时间。正在做的在前，各自按最后改动从新到旧。"""
    out = []; root = Path(root).resolve()
    for place, done in ((root, False), (root / FINISHED, True)):
        if not place.is_dir(): continue
        for d in place.iterdir():
            if d.name.startswith('.') or d.name == FINISHED or not (d / '项目.json').is_file(): continue
            try: meta = rj(d / '项目.json')
            except Exception: meta = {}
            n = number(d.name)       # 片名跟文件夹名走（首页能改名）；文件夹名只有编号时才用 项目.json 的发布标题
            out.append({'id': str(d.relative_to(root)), 'name': d.name, 'num': n, 'title': (d.name[len(n):].strip() if n else d.name) or meta.get('title') or d.name,
                        'done': done, 't': touched(d)})
    out.sort(key=lambda x: (x['done'], -x['t']))
    return out


def film_dir(root, fid):
    """首页只认放片子的文件夹里（和它的 finish/ 里）的项目，不收别的路径。"""
    root = Path(root).resolve(); d = (root / str(fid)).resolve()
    if d.parent not in (root, root / FINISHED) or not (d / '项目.json').is_file(): raise Oops(f'没有这个项目：{fid}')
    return d


def open_job(d):
    try: return WB.open_film(d)
    except SystemExit as e: raise Oops(str(e).replace('停止：', '')) from None


def server_of(job):
    """这条片子的审片台在不在跑（只读：不清 服务.json）：{port, url, listening} 或 None。"""
    f = WB.home(job) / '服务.json'
    if not f.is_file(): return None
    with contextlib.suppress(Exception):
        info = rj(f); pg = WB.ping(info['port'], 0.4)
        if pg and pg.get('app') == '审片工作台' and pg.get('dir') == str(job.dir): return {'port': info['port'], 'url': info['url'], 'listening': bool(pg.get('listening'))}
    return None


def uploaded(pd):
    """存进蚁小二内部草稿了没有（05_上传记录/*/回执.json 的 state 是 draft_saved）；正式发布用户在客户端点，程序看不到。"""
    for r in (pd / '05_上传记录').glob('*/回执.json'):
        with contextlib.suppress(Exception):
            if rj(r).get('state') == 'draft_saved': return True
    return False


def _pending(ucols, cols):
    """几栏合起来的状态（4 个状态词里的一个，没事是 ''）：待你看 ＞ 待发送 ＞ AI 在改。"""
    sts = [(ucols.get(c) or {}).get('state') for c in cols if not (ucols.get(c) or {}).get('off')]
    return next((s for s in ('待你看', '待发送', 'AI 在改') if s in sts), '')


def stages(job, data, done):
    """四个阶段（写文案 → 出初稿 → 改初稿 → 发布）各自的状态。每步：state＝'done'（✓ 已定）／'now'（正在这一步）／'todo'（还没到）；
    tag＝正在这一步时的状态词（待你看／待发送／AI 在改，没事是 ''）；note＝一句大白话。判断只看片子自己的文件：
    写文案：用户点了“定稿”（审片台.json settled.写稿），或片子已经建了 job.json（以前的片子没用文案栏）；
    出初稿：定稿以后到有审核线（job.json 的 review_timeline）为止；没点“开工”前是等用户备料；
    改初稿：有审核线以后，到用户在审核线栏点“这一栏定了”（导出许可）或已导出为止；
    发布：之后到存进蚁小二草稿（05_上传记录 回执 draft_saved）或项目移进 finish/ 为止。"""
    b = data['board']; uc = b['ucols']; stl = b['settle']; ok = lambda c: (stl.get(c) or {}).get('state') == 'ok'
    has_job = not WB.lite(job); kick = (b.get('writing') or {}).get('kick') or {}
    s1 = ok(board.WRITE) or has_job or done
    s2 = bool(data.get('review_timeline')) or done
    s3 = ok('审核线') or bool(job.get('export_ok')) or done
    pd = board.project_dir(job); s4 = done or bool(pd and uploaded(pd))
    other = [c for c in board.COLUMNS if c != board.WRITE]
    out = []
    def step(name, is_done, is_now, tag='', note=''):
        out.append({'name': name, 'state': 'done' if is_done else 'now' if is_now else 'todo', 'tag': tag if is_now and not is_done else '', 'note': note if is_now and not is_done else ''})
    step('写文案', s1, True, (uc.get(board.WRITE) or {}).get('state', '') if not s1 else '', '在文稿栏撰写，完成后定稿')
    if not has_job and s1 and not kick.get('at'):
        t2, n2 = ('待你看', '项目已建立：请准备素材，然后点“开始制作”') if kick.get('opened') else ('AI 在改', 'AI 正在按标题建立项目')
    elif not s2: t2, n2 = 'AI 在改', 'AI 正在初剪'
    else: t2, n2 = '', ''
    step('出初稿', s2, s1, t2, n2)
    step('改初稿', s3, s2, _pending(uc, other), '在审片栏提意见，审阅无误后定稿')
    step('发布', s4, s3, _pending(uc, ('封面', '上传文案')), '导出成片、存入蚁小二草稿')
    return out


COVER_EXT = ('.jpg', '.jpeg', '.png', '.webp')


def cover_of(d):
    """卡片上的封面小图：这条片子最新的一张 3:4 封面（登记的 03_成片/封面 和 02_制作/封面制作 下各方向出的图里挑最新改的），没有返回 None。"""
    c = [*(d / '03_成片' / 'cover').glob('*3x4*'), *(d / '02_制作' / '封面制作').glob('**/*3x4*')]
    c = [x for x in c if x.suffix.lower() in COVER_EXT and not x.name.startswith('.') and x.is_file()]
    return max(c, key=_mt) if c else None


def status(root, fid):
    """一条片子的完整状态（首页一条条补）：四个阶段、轮到你／AI 在改、审片台开着没有、AI 在不在等这条片子。"""
    d = film_dir(root, fid); job = open_job(d); srv = server_of(job)
    try: data = WB.page_data(job, bool(srv and srv['listening']))
    except WB.Broken as e: raise Oops(str(e), 'broken_file', file=e.file.name, can_restore=e.backup) from None
    tot = data['board']['totals']; cv = cover_of(d)
    # url：审片台在跑、而且是同一版程序起的，页面直接开它（不是就走 /open，首页起好或换成新版再跳过去）
    return {'id': fid, 'cover': int(_mt(cv)) if cv else 0, 'url': srv['url'] if srv and WB.same_program(srv['port']) else '', 'stages': stages(job, data, d.parent.name == FINISHED), 'turn': tot.get('轮到你', 0), 'ai': tot.get('AI 在改', 0),
            'open': bool(srv), 'listening': bool(srv and srv['listening']), 't': touched(d), 'title': data['board'].get('name') or d.name}


def ensure(job, ports=None):
    """起（或复用）这条片子的审片台服务，返回网址。和 workbench 命令起的是同一个服务。"""
    if not WB.lite(job): WB.adopt(job)
    if not WB.data_file(job).exists(): WB.edit(job, lambda d: None)
    try: url, _ = WB.start(job, None, ports, fresh=True)
    except SystemExit as e: raise Oops(str(e).replace('停止：', '')) from None
    return url


def cut_bytes(t, n):
    """按 UTF-8 字节截断（不截半个字）。"""
    b = str(t).encode('utf-8'); return t if len(b) <= n else b[:n].decode('utf-8', 'ignore')


@contextlib.contextmanager
def _new_lock(root):
    """新建片子的锁：同一个片子文件夹一次只建一条（本机所有进程共用：正式版、开发版、命令行），免得两条撞进同一个文件夹（QA 2.1）。锁文件放本机临时目录（网络盘上的锁不可靠）。"""
    f = Path(tempfile.gettempdir()) / f'ThinkCut新建片子_{hashlib.sha256(str(root).encode()).hexdigest()[:12]}.lock'
    with _local_lock, open(f, 'a') as x:
        fcntl.flock(x, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(x, fcntl.LOCK_UN)


_local_lock = threading.Lock()


def recycled_numbers(root):
    """回收站里删掉的片子用过的编号（还原回来不撞号；QA 2.7：删了的编号当天又给了新片子）。"""
    rb = recycle_root(Path(root) / 'x'); out = set()
    for box in (boxes(rb) if rb else []):
        if box.is_dir():
            for d in box.iterdir():
                n = number(d.name)
                if n: out.add(n)
    return out


def new_film(root, title, ip=None, existed=None):
    """新建片子：题目（空着叫“未命名”）→ 项目名“今天的 6 位编号＋空格＋题目”（编号撞了按 project.py 的规矩往后顺延，回收站里的编号也算）→ 建项目文件夹（不登记达芬奇库）。
    ip＝这条片子归哪个 IP（写进 项目.json；不给＝默认 IP，和以前一样）。同一个题目 SAME_NEW 秒内又来一次：给刚建的那条（existed 列表里记一下）。返回 id。"""
    pm = board._pm('project'); root = Path(root).resolve(); t = clean_title(title) or '未命名'
    with _new_lock(root):
        if root.is_dir():
            for d in root.iterdir():
                n = number(d.name)
                if n and d.name[len(n):].strip() == t and (d / '项目.json').is_file() and time.time() - _mt(d / '项目.json') < SAME_NEW:
                    if existed is not None: existed.append(d.name)
                    return d.name
        name, _ = pm.free_name(time.strftime('%y%m%d') + ' ' + t, root, also=recycled_numbers(root))
        if (root / name).exists() or (root / FINISHED / name).exists(): raise Oops(f'已有名为“{name}”的项目，请换一个题目', 'exists')
        root.mkdir(parents=True, exist_ok=True)
        with contextlib.redirect_stdout(sys.stderr):
            d, meta = pm.initialize(root / name, register=False, ip=ip)
        if t != '未命名': meta['title'] = t; pm.write_json(d / '项目.json', meta)
    return d.name


def clean_title(title):
    t = re.sub(r'[/\\:\x00-\x1f]', '', str(title or '')); t = re.sub(r'\s+', ' ', t).strip(' .')
    return cut_bytes(t, MAX_TITLE).strip(' .')


def in_library(d):
    """这条片子登记过达芬奇库没有（库列表里有“库名＝文件夹名、路径＝它的 02_制作/达芬奇工程”那一行）。首页新建、还没开项的没登记。"""
    rl = board._pm('resolve_library'); f = rl.PREFS / 'dblist.conf'
    if not f.is_file(): return False
    want = str(rl.folder(d))
    return any(l.split(':')[0] == d.name and l.split(':')[1] == want for l in f.read_text(encoding='utf-8', errors='replace').splitlines() if l.count(':') >= 1)


def stop_film(d):
    """改名、移动、删除前先停掉这条片子在跑的审片台（它记着片子的路径）。"""
    with contextlib.suppress(Oops, SystemExit), contextlib.redirect_stdout(sys.stderr): WB.stop(open_job(d))


def rename_film(root, fid, title, user_ok=False):
    """改片名：照 project.py rename 的规矩（项目名＝6 位编号＋空格＋标题；文件夹名、项目.json、文案文件名、标题行一起改，出错恢复原样）。
    已有达芬奇工程、剪辑工程、成片的，project.py 要用户点头（user_ok）才改。返回新 id。"""
    d = film_dir(root, fid); n = number(d.name); t = clean_title(title)
    if not t: raise Oops('项目名不能为空')
    new = f'{n} {t}' if n else t
    if new == d.name: raise Oops('项目名与当前相同')
    if (d.parent / new).exists() and not same_file(d.parent / new, d): raise Oops(f'已有名为“{new}”的项目，请换一个名字', 'exists')     # 只改大小写：Mac／NAS 上是同一个文件夹，放行（QA 2.7）
    pm = board._pm('project'); stop_film(d)
    try:
        with contextlib.redirect_stdout(sys.stderr): pm.rename(d, new, user_ok=user_ok, library=in_library(d))
    except RuntimeError as e:
        m = str(e)
        if '先问用户' in m: raise Refuse(m.split('先问用户')[0].rstrip('。；') + '。确定要改，改完让 AI 逐项核对一遍。') from None
        raise Oops(m) from None
    return str((d.parent / new).relative_to(Path(root).resolve()))


def same_file(a, b):
    try: return os.path.samefile(a, b)
    except OSError: return False


def finish_film(root, fid, back=False):
    """移到“做完的”（finish/）或移回来：照 project.py finish／reopen。返回新 id。"""
    d = film_dir(root, fid)
    if (d.parent.name == FINISHED) != bool(back): raise Oops('该项目已在“已完成”中' if not back else '该项目不在“已完成”中')
    pm = board._pm('project'); stop_film(d)
    try:
        with contextlib.redirect_stdout(sys.stderr): r = pm.finish(d, back=back, library=in_library(d))
    except RuntimeError as e: raise Oops(str(e)) from None
    return str(Path(r['新位置']).relative_to(Path(root).resolve()))


RECYCLE_BOX = 'ThinkCut'          # 2026-11-25 架构审查：回收站里放删掉片子的文件夹 口播台/ 改名 ThinkCut/
OLD_BOXES = ('口播台',)           # 改名前删的还在 #recycle/ThinkCut/ 里：还原、列回收站也认它（文件夹空了也不删）


def boxes(rb):
    """这块盘回收站里放删掉片子的文件夹：新的 ThinkCut/ 在前，再是旧名字的。"""
    return [rb / b for b in (RECYCLE_BOX, *OLD_BOXES)]


def recycle_root(d):
    """片子所在那块盘的 NAS 回收站：/Volumes/<盘>/#recycle（环境变量 THINKCUT_RECYCLE_ROOT 可指定，测试用）。片子不在 /Volumes 下返回 None。"""
    if os.environ.get('THINKCUT_RECYCLE_ROOT'): return Path(os.environ['THINKCUT_RECYCLE_ROOT'])
    parts = Path(d).resolve().parts
    return Path('/', parts[1], parts[2], '#recycle') if len(parts) > 3 and parts[1] == 'Volumes' else None


def trash_film(root, fid):
    """移到回收站（不是永久删除）：整个片子文件夹挪到同一块盘的 #recycle/ThinkCut/<项目名>_<时间>，同盘只是换位置、不复制。
    回收站不在、不在同一块盘：停下不删。登记过达芬奇库的从库列表里去掉（先备份库列表），达芬奇开着就停下。"""
    d = film_dir(root, fid); rb = recycle_root(d)
    if not rb or not rb.is_dir(): raise Oops(f'没找到 NAS 的回收站（{rb or "这条片子不在网络盘上"}），没删。要删请在访达里自己处理，或让 AI 看看')
    if os.stat(rb).st_dev != os.stat(d).st_dev: raise Oops(f'回收站 {rb} 和项目不在同一块盘上，挪过去等于复制，没删')
    rl = board._pm('resolve_library'); lib = in_library(d)
    if lib and rl.resolve_running(): raise Oops('这个项目登记了达芬奇项目库，达芬奇开着时改不了库列表：先保存并退出达芬奇，再删')
    stop_film(d)
    box = rb / RECYCLE_BOX; box.mkdir(exist_ok=True)
    try: ip = rj(d / '项目.json').get('ip') or ''
    except Exception: ip = ''
    to = box / box_name(d.name, time.strftime("%Y%m%d-%H%M%S"))
    if to.exists(): raise Oops('回收站里已经有同名的了，过一秒再试')
    os.rename(d, to); note = ''
    if lib:
        try: board._pm('project').backup_dblist('删除'); rl.unregister(d.name, rl.folder(d)); note = '，达芬奇库列表里也去掉了'
        except Exception as e: note = f'；达芬奇库列表没改成（{e}），达芬奇里会剩一个打不开的库'
    return {'name': d.name, 'to': str(to), 'from': str(d.relative_to(Path(root).resolve())), 'note': note, 'ip': ip}      # ip：删的时候它属于哪个 IP（撤销按它放回）


def restore_film(root, to, fid):
    """从回收站还原（“移到回收站”后的撤销）：只认这块盘回收站 ThinkCut/（和改名前的 口播台/）下、名字是“<原项目名>_<时间>”的文件夹，放回原处；原处有同名的不覆盖。"""
    root = Path(root).resolve(); src = Path(str(to or '')); dest = (root / str(fid or '')).resolve()
    rb = recycle_root(root / 'x')
    if not rb or src.parent not in boxes(rb) or not (src / '项目.json').is_file(): raise Oops('回收站里没有这个项目了')
    m = STAMP.fullmatch(src.name)
    if dest.parent not in (root, root / FINISHED) or not m or src.name != box_name(dest.name, m[2]): raise Oops('对不上原来的位置，没动')
    if dest.exists(): raise Oops(f'原来的位置已经有“{dest.name}”了，没覆盖')
    os.rename(src, dest); note = ''
    if any((dest / '02_制作' / '达芬奇工程').glob('**/Project.db')):
        try: board._pm('resolve_library').register(dest); note = '，达芬奇库也重新登记了'
        except Exception as e: note = f'；达芬奇库没登记上（{e}），下次 AI 开项时会登记'
    return {'id': str(dest.relative_to(root)), 'note': note}


STAMP = re.compile(r'(.+)_(\d{8}-\d{6})')


def box_name(name, stamp):
    """回收站里的名字：<项目名>_<时间>；项目名太长（加上时间超过文件名上限）就按字节截短（QA 2.3：以前长题目建得出来删不掉）。"""
    return f'{cut_bytes(name, 255 - len(stamp) - 1)}_{stamp}'


def orig_name(d):
    """回收站里一条片子原来的名字：项目.json 的 name 对得上就用它（名字被截短过也能还原成原名），否则就是去掉 _时间 的部分。"""
    m = STAMP.fullmatch(Path(d).name)
    if not m: return None
    with contextlib.suppress(Exception):
        n = rj(Path(d) / '项目.json').get('name')
        if n and box_name(n, m[2]) == Path(d).name: return n
    return m[1]


def recycled(roots, ip_box=None, default=''):
    """回收站里能还原的东西（新删的在前）：片子（各片子文件夹所在盘的 #recycle/ThinkCut/<项目名>_<时间>）、IP（ip_box/<名字>_<时间>）。
    片子带它属于哪个 IP（项目.json 的 ip，以前的片子没写就算默认 IP）。只读。"""
    out = []; seen = set()
    for r in roots:
        rb = recycle_root(Path(r) / 'x')
        for box in (boxes(rb) if rb else []):
            if box in seen or not box.is_dir(): continue
            seen.add(box)
            for d in box.iterdir():
                m = STAMP.fullmatch(d.name)
                if not m or not (d / '项目.json').is_file(): continue
                try: ip = rj(d / '项目.json').get('ip') or default
                except Exception: ip = default
                out.append({'kind': 'film', 'name': orig_name(d), 'at': m[2], 'ip': ip, 'to': str(d)})
    if ip_box and Path(ip_box).is_dir():
        for d in Path(ip_box).iterdir():
            m = STAMP.fullmatch(d.name)
            if m and (d / 'ip.json').is_file(): out.append({'kind': 'ip', 'name': m[1], 'at': m[2], 'ip': m[1], 'to': str(d)})
    out.sort(key=lambda x: x['at'], reverse=True)
    return out


def reveal(root, fid):
    d = film_dir(root, fid); subprocess.run(['open', '-R', str(d)], check=False, timeout=10); return {}


def film_ports(text):
    if not text: return None
    a, _, b = str(text).partition('-'); a = int(a); b = int(b or a)
    if not (1024 < a <= b < 65536): die('--film-ports 写成 8765-8784')
    return range(a, b + 1)


def make_handler(root, ports=None):
    """root 给了＝钉死看这个文件夹（不能切 IP）；root=None＝跟着当前 IP 的片子文件夹走（左上角切）。"""
    ears = {'t': 0.0}; started = {}            # AI 的 home --wait 最近一次来问的时刻；首页起过的审片台（端口 → 片子 id）
    pin = Path(root).resolve() if root else None
    if pin: S = {'ip': cfg().which_ip(), 'root': pin}          # 钉死时新片子照旧归默认 IP（THINKCUT_IP 可换）
    else: ip0 = first_ip(); S = {'ip': ip0, 'root': ip_root(ip0)}
    lock = threading.Lock()

    def here():
        with lock: return S['ip'], S['root']

    def switch(name):
        if pin: raise Oops(f'首页是用 --root 打开的，只看 {pin}，不能切 IP')
        if name not in cfg().ips(): raise Oops(f'没有叫「{name}」的 IP')
        r = ip_root(name)
        with lock: S.update(ip=name, root=r)
        try: cfg().set_home_ip(name)
        except SystemExit as e: raise Oops('切过去了，但没记住（下次打开还是原来的）：' + str(e).replace('停止：', '')) from None
        return {'ip': name, 'root': str(r)}

    def new_ip(q):
        if pin: raise Oops(f'首页是用 --root 打开的，只看 {pin}，不能新建 IP')
        try: cfg().new_ip(q.get('name'), None)
        except SystemExit as e: raise Oops(str(e).replace('停止：', '')) from None
        return switch(str(q.get('name')).strip())

    def ip_list():
        ip, r = here(); names = cfg().ips() if not pin else [ip]
        return {'ok': True, 'current': ip, 'default': cfg().default_ip(), 'pinned': bool(pin), 'root': str(r), 'ips': names}

    def ip_info(name):
        """删之前给用户看：这个 IP 的片子文件夹在哪、里面几条片子（删 IP 不动它们）。"""
        r = ip_root(name); return {'ok': True, 'name': name, 'root': str(r), 'films': len(scan(r)) if r.is_dir() else 0}

    def del_ip(q):
        if pin: raise Oops(f'首页是用 --root 打开的，只看 {pin}，不能删 IP')
        name = str(q.get('name') or '').strip()
        try: to = cfg().remove_ip(name)
        except SystemExit as e: raise Oops(str(e).replace('停止：', '')) from None
        if here()[0] == name:
            d = cfg().default_ip(); r = ip_root(d)
            with lock: S.update(ip=d, root=r)
        ip, r = here(); return {'ip': ip, 'root': str(r), 'to': str(to), 'name': name}

    def ip_box():
        with contextlib.suppress(SystemExit, Oops): return cfg().load(ip=cfg().default_ip()).path('回收站') / 'ThinkCut配置档'
        return None

    def bin_list():
        if pin: return {'ok': True, 'pinned': True, 'items': [x for x in recycled([pin], None, here()[0])]}
        return {'ok': True, 'pinned': False, 'items': recycled(all_roots(), ip_box(), cfg().default_ip())}

    def bin_restore(q):
        """回收站里点“还原”：IP 挪回 profiles/；片子放回它那个 IP 的片子文件夹（不在“做完的”里，要的话再右键移过去）。"""
        to = Path(str(q.get('to') or '')); m = STAMP.fullmatch(to.name)
        if not m: raise Oops('回收站里没有这个了')
        if q.get('kind') == 'ip': return undel_ip(q)
        if pin: root = pin
        else:
            try: ip = rj(to / '项目.json').get('ip') or cfg().default_ip()
            except Exception: raise Oops('回收站里没有这个项目了') from None
            if ip not in cfg().ips(): raise Oops(f'这个项目属于 IP「{ip}」，它也在回收站里：先把这个 IP 还原')
            root = ip_root(ip)
        root.mkdir(parents=True, exist_ok=True)
        name = orig_name(to); r = restore_film(root, str(to), name); return {**r, 'ip': here()[0] if pin else ip, 'name': name}

    def restore_q(q):
        """“移到回收站”后的撤销：放回这条片子自己那个 IP 的片子文件夹（项目.json 记的 ip），不管首页现在切到了哪个 IP（QA 2.2）。"""
        if pin: return restore_film(pin, q.get('to'), q.get('from'))
        to = Path(str(q.get('to') or ''))
        try: ip = rj(to / '项目.json').get('ip') or cfg().default_ip()
        except Exception: raise Oops('回收站里没有这个项目了', 'gone') from None
        if ip not in cfg().ips(): raise Oops(f'这个项目属于 IP「{ip}」，它也在回收站里：先把这个 IP 还原', 'ip_gone')
        root = ip_root(ip); return {**restore_film(root, str(to), q.get('from')), 'ip': ip}

    def undel_ip(q):
        if pin: raise Oops('首页是用 --root 打开的，不能改 IP')
        try: name = cfg().restore_ip(q.get('to'))
        except SystemExit as e: raise Oops(str(e).replace('停止：', '')) from None
        return {'name': name}

    def heard(): return time.time() - ears['t'] < HEARD

    def ai_info():
        """AI 在不在等：等所有片子（home --wait）＋各条片子的审片台报的 --wait（只问在跑的服务，本机很快）。"""
        films = []; _, root = here()
        for p in sorted(set(ports or WB.PORTS) | set(started)):
            pg = WB.ping(p, 0.3)
            if pg and pg.get('app') == '审片工作台' and pg.get('listening'):
                with contextlib.suppress(ValueError): films.append(str(Path(pg['dir']).resolve().relative_to(root)).split('/02_制作')[0])
        try: st = engine.engine_status(engine.chosen()); eng = {'id': st['id'], 'name': st['name'], 'state': st['state'], 'why': st['why']}
        except Exception as e: eng = {'id': '', 'name': 'AI', 'state': '出错', 'why': f'查不了本机 AI：{type(e).__name__}'}
        return {'all': heard(), 'films': films, 'engine': eng, 'cmd': f'python3 "{Path(__file__).resolve().parents[1] / "thinkcut_edit.py"}" home --wait' + (f' --root "{root}"' if pin and root != video_root() else '')}

    opening = {}; opening_guard = threading.Lock()

    def do_open(q):
        _, root = here(); d = film_dir(root, q.get('id'))
        with opening_guard: one = opening.setdefault(str(d), threading.Lock())
        with one: job = open_job(d); url = ensure(job, ports)         # 同一条片子同时点两次：排队，第二次直接复用第一次起好的
        started[int(url.rsplit(':', 1)[1].strip('/'))] = q.get('id'); return {'url': url, 'id': q.get('id')}

    def do_new(q):
        ip, root = here(); had = []; fid = new_film(root, q.get('title') if 'title' in q else q.get('new'), ip, had)
        try: return {**do_open({'id': fid}), 'name': Path(fid).name, 'existed': bool(had)}       # existed：刚才已经建过同题目的，这次没再建
        except Oops as e: raise Oops(f'项目已建立（{Path(fid).name}），但审片台未能打开：{e}', 'open_failed', id=fid, name=Path(fid).name) from None

    def same_ip(q):
        """页面带着它看的 IP 来：首页已经切到别的 IP（比如另一个窗口切的）就不动，免得改到别的 IP 的同名片子。"""
        ip, root = here()
        if q.get('ip') and q['ip'] != ip: raise Oops(f'首页已经切到「{ip}」了：刷新一下再点')
        return root

    STRICT = {'/api/open': do_open, '/api/new': do_new,
              '/api/rename': lambda q: {'id': rename_film(same_ip(q), q.get('id'), q.get('title'), bool(q.get('user_ok')))},
              '/api/finish': lambda q: {'id': finish_film(same_ip(q), q.get('id'), bool(q.get('back')))},
              '/api/trash': lambda q: trash_film(same_ip(q), q.get('id')),
              '/api/restore': restore_q,
              '/api/reveal': lambda q: reveal(same_ip(q), q.get('id')),
              '/api/ip': lambda q: switch(q.get('name')), '/api/ip-new': new_ip, '/api/ip-del': del_ip, '/api/ip-undel': undel_ip,
              '/api/bin-restore': bin_restore}
    for _p, _f in settings.POST.items(): STRICT[_p] = _f            # 设置页（AI、豆包、蚁小二）：和别的操作一样只收首页页面带口令的请求

    # AI 对话侧栏（2026-11-27）：首页的对话不属于某个项目；开项、存档走代办里的 project.py，AI 说清楚以后“接到”一个项目才能写它
    def chat_roots(): return [pin] if pin else all_roots()
    talk = chat.for_home(chat_roots, open_job, current=here)

    def chat_open(q):
        """对话里点“进入项目”：起（或复用）那个项目的审片台，返回网址（只认放项目的文件夹里的真项目）。"""
        d = Path(str(q.get('path') or '')).expanduser().resolve()
        if not (d / '项目.json').is_file() or not any(Path(r).resolve() in d.parents for r in chat_roots()): raise Oops('这个项目找不到了')
        url = ensure(open_job(d), ports); root = here()[1].resolve()
        if root in d.parents: started[int(url.rsplit(':', 1)[1].strip('/'))] = str(d.relative_to(root))      # ⌘R 找回、AI 的 home --wait 要用
        return {'url': url}

    def chat_call(fn):
        def g(q):
            try: return fn(q)
            except chat.Oops as e: raise Oops(str(e)) from None
        return g
    STRICT.update({'/api/chat/send': chat_call(lambda q: talk.send(q.get('text'), q.get('files'))), '/api/chat/stop': chat_call(lambda q: talk.stop()),
                   '/api/chat/clear': chat_call(lambda q: talk.clear()), '/api/chat/open': chat_call(chat_open)})

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send(self, body, kind='application/json; charset=utf-8', code=200, cookie=False):
            if not isinstance(body, bytes): body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body)))
            if cookie: self.send_header('Set-Cookie', f'{COOKIE}_{self.server.server_address[1]}={TOKEN}; Path=/; HttpOnly; SameSite=Strict')
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def to(self, where):
            self.send_response(302); self.send_header('Location', where); self.send_header('Content-Length', '0'); self.end_headers()

        def cookie(self):
            try: c = http.cookies.SimpleCookie(self.headers.get('Cookie') or '')
            except http.cookies.CookieError: return None
            v = c.get(f'{COOKIE}_{self.server.server_address[1]}'); return v.value if v else None

        def guarded(self, fn):
            """出了没料到的错：回一句中文、记日志，不断开连接（QA 2.5）。"""
            try: return fn()
            except (BrokenPipeError, ConnectionResetError): return None
            except Exception as e:
                traceback.print_exc()
                with contextlib.suppress(Exception): return self.send({'ok': False, 'code': 'internal', 'error': f'首页出错：{type(e).__name__}：{e}'}, code=500)

        def do_GET(self): return self.guarded(self._get)
        def do_POST(self): return self.guarded(self._post)

        def mine(self): port = self.server.server_address[1]; return {f'127.0.0.1:{port}', f'localhost:{port}'}
        def own_host(self): h = self.headers.get('Host'); return h is None or h.strip().lower() in self.mine()

        def from_page(self):
            o = self.headers.get('Origin'); site = self.headers.get('Sec-Fetch-Site')
            return (self.own_host() and o is not None and o.strip().lower() in {'http://' + x for x in self.mine()}
                    and self.cookie() == TOKEN and (site is None or site == 'same-origin'))

        def _get(self):
            from urllib.parse import parse_qs, unquote, urlsplit
            if not self.own_host(): return self.send({'ok': False, 'error': '只接受本机地址的请求'}, code=403)
            u = urlsplit(self.path); p = unquote(u.path); q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            ip, root = here()
            if p in ('/', '/index.html'): return self.send(design.inject(PAGE.read_bytes().replace(b'__SHENPIAN_TOKEN__', SESSION.encode())), 'text/html; charset=utf-8', cookie=True)
            if p == '/api/ping': return self.send({'app': '首页', 'root': str(root), 'ip': ip, 'pin': str(pin or ''), 'pid': os.getpid(), 'ai_all': heard(), 'session': SESSION, 'prog': WB.PROG})
            if p == '/api/films': return self.send({'ok': True, 'root': str(root), 'ip': ip, 'pinned': bool(pin), 'exists': root.is_dir(), 'films': scan(root) if root.is_dir() else []})
            if p == '/api/ips': return self.send(ip_list())
            if p == '/api/bin': return self.send(bin_list())
            if p == '/api/ip-info':
                try: return self.send(ip_info(q.get('name', '')))
                except Oops as e: return self.send({'ok': False, 'error': str(e)})
            if p == '/api/film':
                try: return self.send({'ok': True, **status(root, q.get('id', ''))})
                except Oops as e: return self.send({'ok': False, 'id': q.get('id'), 'code': e.code, 'error': str(e), **e.extra})
                except Exception as e: return self.send({'ok': False, 'id': q.get('id'), 'error': f'读取该项目出错：{e}'})
            if p == '/api/ai': return self.send({'ok': True, **ai_info()})
            if p == '/api/chat': return self.send(talk.state())
            if p == '/settings': return self.send(settings.page(SESSION), 'text/html; charset=utf-8', cookie=True)
            if p == '/api/settings': return self.send(settings.info())
            if p == '/api/settings/tools': return self.send(settings.tools_info())
            if p == '/api/cover':                  # 卡片上的封面小图
                try: cv = cover_of(film_dir(root, q.get('id', '')))
                except Oops: cv = None
                if not cv: return self.send({'ok': False, 'error': '还没有封面'}, code=404)
                kind = {'.png': 'image/png', '.webp': 'image/webp'}.get(cv.suffix.lower(), 'image/jpeg')
                return self.send(cv.read_bytes(), kind)
            if p == '/open':                       # 首页进一条片子：审片台没在跑时先到这里，起好再 302 跳过去（这一步不进浏览器历史）
                if self.cookie() != TOKEN: return self.to('/')          # 首页重启过（口令换了）、或不是从首页页面点的：回首页重新打开一次（QA 2.6：以前一点就 403）
                try: same_ip(q); url = (do_new(q) if 'new' in q else do_open(q))['url']      # new＝新建片子（题目在 new 里），建好直接开
                except (Oops, RuntimeError, OSError, WB.Broken) as e:
                    return self.send(f'<!doctype html><meta charset="utf-8"><title>打不开</title><p style="font:15px -apple-system;padding:40px">打不开这个项目：{html.escape(str(e))}</p>'
                                     f'<p style="font:15px -apple-system;padding:0 40px"><a href="/">← 全部项目</a></p>'.encode(), 'text/html; charset=utf-8')
                from urllib.parse import quote
                to = url + '?home=' + quote(f'http://{self.headers.get("Host") or "127.0.0.1"}', safe='') + ('#' + quote(q['col'], safe='/') if q.get('col') else '')
                self.send_response(302); self.send_header('Location', to); self.send_header('Content-Length', '0'); self.end_headers(); return
            if p == '/reopen':                     # 外壳 ⌘R：审片台连不上时，按端口找回是哪条片子，起好再跳过去（首页也重启过、不认识这个端口就回首页）
                try: fid = started[int(q.get('port', ''))]
                except (ValueError, KeyError): return self.to('/')
                if self.cookie() != TOKEN: return self.to('/')
                try: return self.to(do_open({'id': fid})['url'])
                except (Oops, RuntimeError, OSError): return self.to('/')
            if p == '/api/wait-all':               # AI 的 home --wait 两秒来问一次：记下“AI 在等所有片子”，告诉它首页起过哪些审片台
                ears['t'] = time.time(); return self.send({'ok': True, 'root': str(root), 'ports': sorted(started)})
            self.send({'ok': False, 'error': '没有这个地址'}, code=404)

        def chat_upload(self):
            """对话侧栏拖进来的文件：放进对话接到的那个项目的 01_原始素材（和审片台备料同一套：同名不覆盖、边收边写、半截不留）。只收首页页面带口令的。"""
            from urllib.parse import parse_qs, urlsplit
            if not self.from_page():
                self.close_connection = True; return self.send({'ok': False, 'error': '放文件只能在首页页面上拖（这个请求不是页面发的）'}, code=403)
            name = (parse_qs(urlsplit(self.path).query).get('name') or [''])[0]
            try: n = int(self.headers.get('Content-Length') or -1)
            except ValueError: n = -1
            try: rec = materials.receive(talk.material_job(), name, self.rfile, n)
            except (materials.Oops, chat.Oops, Oops) as e:
                self.close_connection = True; return self.send({'ok': False, 'error': str(e)})
            self.send({'ok': True, 'file': rec})

        def _post(self):
            if self.path.split('?')[0] == '/api/chat/upload': return self.chat_upload()
            try: raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            except ValueError: return self.send({'ok': False, 'error': '格式不对'}, code=400)
            fn = STRICT.get(self.path.split('?')[0])
            if not fn: return self.send({'ok': False, 'error': '没有这个地址'}, code=404)
            if not self.from_page():
                stale = self.cookie() is not None and self.cookie() != TOKEN        # 带着上一次首页服务的 cookie：首页重启过，页面刷新一下就好
                return self.send({'ok': False, 'code': 'stale_page' if stale else 'not_page', 'error': '新建、打开、改名、移动、删除项目只能在首页页面上点（这个请求不是页面发的）'}, code=403)
            try: q = json.loads(raw or b'{}')
            except ValueError: return self.send({'ok': False, 'error': '格式不对'}, code=400)
            if not isinstance(q, dict): return self.send({'ok': False, 'error': '格式不对'}, code=400)
            try: return self.send({'ok': True, **fn(q)})
            except Refuse as e: return self.send({'ok': False, 'need_ok': True, 'error': str(e)})
            except SystemExit as e: return self.send({'ok': False, 'error': str(e).replace('停止：', '')})
            except Oops as e: return self.send({'ok': False, 'code': e.code, 'error': str(e), **e.extra})
            except settings.Oops as e: return self.send({'ok': False, 'error': str(e)})
            except WB.Broken as e: return self.send({'ok': False, 'code': 'broken_file', 'file': e.file.name, 'error': str(e)})
            except (RuntimeError, OSError) as e: return self.send({'ok': False, 'code': 'os_error', 'error': str(e)})
    H.chat = talk
    return H


def make_server(root, port, ports=None):
    """root=None：跟着当前 IP 走（能切）；给了 root：钉死看这个文件夹。"""
    H = make_handler(Path(root).resolve() if root else None, ports)
    srv = ThreadingHTTPServer(('127.0.0.1', port), H); srv.chat = H.chat
    threading.Thread(target=chat_broker, args=(H.chat,), daemon=True).start()
    return srv


def chat_broker(talk, every=0.4):
    """首页对话的代办（2026-11-27）：后台 AI 开项、存档要写到片子文件夹，AI 自己写不了；只代办 project.py（除了上传）和挪进回收站，
    路径只收工作区、临时目录和放项目的文件夹。"""
    sys.path.insert(0, str(engine.PROGRAM / 'bin')); import thinkcut_broker
    while True:
        with contextlib.suppress(Exception): thinkcut_broker.serve_once(talk.broker, chat.broker_roots_home(talk), policy=thinkcut_broker.home_policy)
        time.sleep(every)


def serve(root, port, ports=None):
    srv = make_server(root or None, port, film_ports(ports))
    def bye(*_):
        with contextlib.suppress(Exception): srv.chat.stop()          # 首页停了：对话里的 AI 一起停
        os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    say(f'首页 http://127.0.0.1:{port}/  {root or "跟着当前 IP"}'); srv.serve_forever()


def ping(port=PORT, timeout=0.5):
    pg = WB.ping(port, timeout); return pg if pg and pg.get('app') == '首页' else None


def start(root, port=PORT, ports=None):
    """起首页服务（已在跑就复用；钉死的文件夹不一样就停下说清）。root=None＝跟着当前 IP 走。返回 (网址, 是不是复用)。"""
    pg = ping(port)
    if pg:
        want = str(root or '')
        if 'pin' in pg: bad = pg['pin'] != want                         # 能切 IP 的这一版
        else: bad = pg['root'] != str(root or video_root())            # 切 IP 以前那一版首页（只看一个文件夹）
        if bad: die(f"首页已经在 {port} 端口跑着，看的是 {pg['pin'] or pg['root'] if 'pin' in pg else pg['root']}（不是 {root or '跟着当前 IP'}）：先 home --stop，或换 --port")
        return f'http://127.0.0.1:{port}/', True
    if not WB.port_free(port): die(f'端口 {port} 被别的程序占了：换一个 --port')
    log = Path(os.environ.get('TMPDIR', '/tmp')) / f'ThinkCut首页_{port}.log'
    args = [sys.executable, '-B', '-m', 'thinkcut.home', 'serve', str(root or ''), str(port)] + ([f'{ports[0]}-{ports[-1]}'] if ports else [])
    with open(log, 'a') as lf:
        subprocess.Popen(args, cwd=str(Path(__file__).resolve().parents[1]), stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}, start_new_session=True)
    for _ in range(100):
        if ping(port, 0.3): return f'http://127.0.0.1:{port}/', False
        time.sleep(0.1)
    die(f'首页没起来，看日志 {log}：\n' + log.read_text(encoding='utf-8', errors='replace')[-600:])


def stop(port=PORT):
    pg = ping(port)
    if not pg: say('首页没在跑'); return False
    with contextlib.suppress(ProcessLookupError): os.kill(pg['pid'], signal.SIGTERM)
    for _ in range(50):
        if not ping(port, 0.2): break
        time.sleep(0.1)
    say(f'首页已停（端口 {port}）。各个项目的审片台还在跑，要停用 workbench <项目文件夹> --stop'); return True


def wait_all(root, timeout=0, port=PORT):
    """AI 用：一条命令挂着等所有片子。任何一条片子的审片台上有人点“交给 AI”（或定稿、开工），就打印是哪条片子、哪几件事，然后退出。
    只看在跑的审片台（用户能点的都在跑）：8765–8784 端口和首页起过的；只认放片子的文件夹里的片子。每两秒问一次，同时替每个审片台报“AI 在等”。
    root 可以是一个文件夹或几个（不带 --root 时是所有 IP 的片子文件夹）。"""
    t0 = time.time(); roots = [Path(r).resolve() for r in (root if isinstance(root, (list, tuple, set)) else [root])]
    while True:
        ports = set(WB.PORTS)
        with contextlib.suppress(Exception):
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/wait-all', timeout=2) as r: h = json.loads(r.read())
            if h.get('root') in {str(r) for r in roots}: ports |= set(h.get('ports') or [])
        for p in sorted(ports):
            pg = WB.ping(p, 0.3)
            if not pg or pg.get('app') != '审片工作台': continue
            d = Path(pg['dir'])
            if not any(r in d.parents for r in roots): continue
            with contextlib.suppress(Exception), urllib.request.urlopen(f'http://127.0.0.1:{p}/api/wait', timeout=2) as r: r.read()
            try: job = WB.open_film(d)
            except SystemExit: continue
            got = board.pick(job)
            if got:
                pd = board.project_dir(job)
                say(f"【项目】{pd.name if pd else d.name}　项目文件夹：{job.dir}")
                say(f'这个项目的回复、发布照旧用 workbench "{job.dir}" --reply／--rewrite／--publish …')
                WB.report(job, got, again='再起一次 home --wait 接着等所有项目')
                return job
        if timeout and time.time() - t0 > timeout:
            say(f'等了 {int(timeout)} 秒，哪个项目都还没人点“提交 AI”。要接着等就再跑一次 home --wait'); return None
        time.sleep(2)


def cli(a):
    port = a.port or PORT; ports = film_ports(a.film_ports); pin = pinned(a.root)
    if a.stop: return stop(port)
    if a.wait: return wait_all(video_root(a.root) if pin else all_roots(), float(a.wait_timeout or 0), port)
    root = video_root(a.root) if pin else None
    if pin and not root.is_dir(): die(f'放项目的文件夹不存在：{root}（用 --root 指定，或设环境变量 THINKCUT_VIDEO_ROOT）')
    url, reused = start(root, port, ports); opened = WB.open_page(url)
    pg = ping(port) or {}
    where = f"项目在 {root}" if pin else f"当前 IP「{pg.get('ip', '?')}」，项目在 {pg.get('root', '?')}；左上角可切 IP"
    say(f"首页{'已经在跑（复用）' if reused else '已启动'}：{url}（{where}）" + ('，已在浏览器打开' if opened else '；没能自动打开浏览器：AI 去打开这个网址（或发给用户）') + '\n'
        + '用户在首页看所有项目、点“新建项目”、双击一个项目进入它的审片台。\n'
        + f'【等用户】现在就挂着等所有项目（不带 --root 时所有 IP 的都等）：python3 "{Path(__file__).resolve().parents[1] / "thinkcut_edit.py"}" home --wait'
        + (f' --root "{root}"' if a.root else '') + '（Claude Code 放后台跑；任何一个项目有人点“提交 AI”它就打印是哪个项目的什么事并退出；Codex 加 --wait-timeout 1500）。\n'
        + f'停首页：home --stop（各个项目的审片台不受影响）')
    return url


if __name__ == '__main__' and len(sys.argv) >= 4 and sys.argv[1] == 'serve':
    serve(Path(sys.argv[2]) if sys.argv[2] else None, int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None)      # 空的＝跟着当前 IP 走
