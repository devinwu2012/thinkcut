# 首页（2026-11-24 用户定：“这个像工作树一样或者工作流一样”“最终的软件还没做出来呀”）：一个固定入口看到所有片子、新建片子，点一条就进它的审片台。
# 一个常驻的本机小服务（只听 127.0.0.1，固定端口 8760，thinkcut_edit.py home 起），页面 assets/首页/index.html。以后套程序外壳，窗口里装的就是它。
# - 片子列表从放片子的文件夹现读（默认 视频项目管理 的 VIDEO_ROOT，即配置档里默认 IP 的片子文件夹；--root 或环境变量 THINKCUT_VIDEO_ROOT 改）：每条片子自己的 项目.json、审片工作台/ 下的记录、job.json，
#   不另存索引。先出列表（每条只看几个文件的修改时间），每条的阶段和“轮到你／AI 在改”再一条条补（同审片台页面用的 workbench.page_data 算，数字和审片台顶栏一样）。
# - 点一条：首页按需起（或复用）那条片子的审片台服务（workbench.start，端口照旧自动找），双击就在同一个窗口里进到它（一次只开一条片子，2026-11-24 第二轮）。
#   2026-11-24 用户定方向 C：首页是四步看板（看板／列表两种看法），不再在首页里套内嵌框。
# - 右键一条片子（2026-11-24 用户：“片子删不掉”）：打开、在 Finder 中显示、重命名、移到“做完的”／移回来、移到回收站。
#   删除不是永久删除：整个片子文件夹挪到同一块盘的 NAS 回收站 #recycle/口播台/<项目名>_<时间>（macOS 废纸篓在网络盘上常常变成“立即删除”），能拿回来；回收站不在就停下不删。
# - 新建片子：只要一个题目（空着叫“未命名”），按 视频项目管理 project.py 的规矩建项目文件夹（编号、文件夹结构、项目.json），不登记达芬奇库（那是 AI 的事），直接进“文案”栏。
# - AI 在不在等：首页顶上看 AI 的“等所有片子”（home --wait）和每条片子的 --wait；home --wait 一条命令挂着，任何一条片子有人点“交给 AI”就打印是哪条片子的什么事并退出。
# 新建、打开、改名、移动、删除这些只收首页页面自己发的请求（带页面口令），命令行不能替用户点。
import contextlib, json, os, re, signal, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .common import ASSETS, rj, say, die
from . import board, workbench as WB

PAGE = ASSETS / '首页' / 'index.html'
PORT = WB.HOME_PORT
FINISHED = 'finish'
STAGES = ('写文案', '出初稿', '改初稿', '发布')
TOKEN = os.urandom(12).hex()
HEARD = 8                    # AI 的 home --wait 两秒来问一次；8 秒没来就算没在等


class Oops(Exception):
    pass


class Refuse(Exception):
    pass


def video_root(root=None):
    """放片子的文件夹：--root ＞ 环境变量 THINKCUT_VIDEO_ROOT ＞ 视频项目管理 project.py 的 VIDEO_ROOT（配置档里默认 IP 的片子文件夹）。"""
    if root: return Path(root).expanduser().resolve()
    if os.environ.get('THINKCUT_VIDEO_ROOT'): return Path(os.environ['THINKCUT_VIDEO_ROOT']).expanduser().resolve()
    return Path(board._pm('project').VIDEO_ROOT).resolve()


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
    if d.parent not in (root, root / FINISHED) or not (d / '项目.json').is_file(): raise Oops(f'没有这条片子：{fid}')
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
    step('写文案', s1, True, (uc.get(board.WRITE) or {}).get('state', '') if not s1 else '', '在“文案”栏写，写好点“定稿”')
    if not has_job and s1 and not kick.get('at'):
        t2, n2 = ('待你看', '项目开好了：备料，点“开工”') if kick.get('opened') else ('AI 在改', 'AI 在按标题开项')
    elif not s2: t2, n2 = 'AI 在改', 'AI 在出初稿'
    else: t2, n2 = '', ''
    step('出初稿', s2, s1, t2, n2)
    step('改初稿', s3, s2, _pending(uc, other), '在审核线上提意见，没问题点“确定此栏”')
    step('发布', s4, s3, _pending(uc, ('封面', '上传文案')), '导出、存蚁小二草稿')
    return out


COVER_EXT = ('.jpg', '.jpeg', '.png', '.webp')


def cover_of(d):
    """卡片上的封面小图：这条片子最新的一张 3:4 封面（登记的 03_成片/封面 和 02_制作/封面制作 下各方向出的图里挑最新改的），没有返回 None。"""
    c = [*(d / '03_成片' / '封面').glob('*3x4*'), *(d / '02_制作' / '封面制作').glob('**/*3x4*')]
    c = [x for x in c if x.suffix.lower() in COVER_EXT and not x.name.startswith('.') and x.is_file()]
    return max(c, key=_mt) if c else None


def status(root, fid):
    """一条片子的完整状态（首页一条条补）：四个阶段、轮到你／AI 在改、审片台开着没有、AI 在不在等这条片子。"""
    d = film_dir(root, fid); job = open_job(d); srv = server_of(job)
    data = WB.page_data(job, bool(srv and srv['listening']))
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


def new_film(root, title):
    """新建片子：题目（空着叫“未命名”）→ 项目名“今天的 6 位编号＋空格＋题目”（编号撞了按 project.py 的规矩往后顺延）→ 建项目文件夹（不登记达芬奇库）。返回 id。"""
    pm = board._pm('project'); root = Path(root).resolve(); t = re.sub(r'[/\\:\x00-\x1f]', '', str(title or ''))
    t = re.sub(r'\s+', ' ', t).strip(' .')[:60] or '未命名'
    name, _ = pm.free_name(time.strftime('%y%m%d') + ' ' + t, root)
    if (root / name).exists() or (root / FINISHED / name).exists(): raise Oops(f'已经有一条叫“{name}”的片子了：换个题目')
    root.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(sys.stderr):
        d, meta = pm.initialize(root / name, register=False)
    if t != '未命名': meta['title'] = t; pm.write_json(d / '项目.json', meta)
    return d.name


def clean_title(title):
    t = re.sub(r'[/\\:\x00-\x1f]', '', str(title or '')); return re.sub(r'\s+', ' ', t).strip(' .')[:60]


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
    if not t: raise Oops('片名不能空着')
    new = f'{n} {t}' if n else t
    if new == d.name: raise Oops('片名和现在的一样')
    if (d.parent / new).exists(): raise Oops(f'已经有一条叫“{new}”的片子了：换个名字')
    pm = board._pm('project'); stop_film(d)
    try:
        with contextlib.redirect_stdout(sys.stderr): pm.rename(d, new, user_ok=user_ok, library=in_library(d))
    except RuntimeError as e:
        m = str(e)
        if '先问用户' in m: raise Refuse(m.split('先问用户')[0].rstrip('。；') + '。确定要改，改完让 AI 逐项核对一遍。') from None
        raise Oops(m) from None
    return str((d.parent / new).relative_to(Path(root).resolve()))


def finish_film(root, fid, back=False):
    """移到“做完的”（finish/）或移回来：照 project.py finish／reopen。返回新 id。"""
    d = film_dir(root, fid)
    if (d.parent.name == FINISHED) != bool(back): raise Oops('这条片子已经在“做完的”里了' if not back else '这条片子不在“做完的”里')
    pm = board._pm('project'); stop_film(d)
    try:
        with contextlib.redirect_stdout(sys.stderr): r = pm.finish(d, back=back, library=in_library(d))
    except RuntimeError as e: raise Oops(str(e)) from None
    return str(Path(r['新位置']).relative_to(Path(root).resolve()))


RECYCLE_BOX = '口播台'


def recycle_root(d):
    """片子所在那块盘的 NAS 回收站：/Volumes/<盘>/#recycle（环境变量 THINKCUT_RECYCLE_ROOT 可指定，测试用）。片子不在 /Volumes 下返回 None。"""
    if os.environ.get('THINKCUT_RECYCLE_ROOT'): return Path(os.environ['THINKCUT_RECYCLE_ROOT'])
    parts = Path(d).resolve().parts
    return Path('/', parts[1], parts[2], '#recycle') if len(parts) > 3 and parts[1] == 'Volumes' else None


def trash_film(root, fid):
    """移到回收站（不是永久删除）：整个片子文件夹挪到同一块盘的 #recycle/口播台/<项目名>_<时间>，同盘只是换位置、不复制。
    回收站不在、不在同一块盘：停下不删。登记过达芬奇库的从库列表里去掉（先备份库列表），达芬奇开着就停下。"""
    d = film_dir(root, fid); rb = recycle_root(d)
    if not rb or not rb.is_dir(): raise Oops(f'没找到 NAS 的回收站（{rb or "这条片子不在网络盘上"}），没删。要删请在 Finder 里自己处理，或让 AI 看看')
    if os.stat(rb).st_dev != os.stat(d).st_dev: raise Oops(f'回收站 {rb} 和片子不在同一块盘上，挪过去等于复制，没删')
    rl = board._pm('resolve_library'); lib = in_library(d)
    if lib and rl.resolve_running(): raise Oops('这条片子登记了达芬奇项目库，达芬奇开着时改不了库列表：先保存并退出达芬奇，再删')
    stop_film(d)
    box = rb / RECYCLE_BOX; box.mkdir(exist_ok=True)
    to = box / f'{d.name}_{time.strftime("%Y%m%d-%H%M%S")}'
    if to.exists(): raise Oops('回收站里已经有同名的了，过一秒再试')
    os.rename(d, to); note = ''
    if lib:
        try: board._pm('project').backup_dblist('删除'); rl.unregister(d.name, rl.folder(d)); note = '，达芬奇库列表里也去掉了'
        except Exception as e: note = f'；达芬奇库列表没改成（{e}），达芬奇里会剩一个打不开的库'
    return {'name': d.name, 'to': str(to), 'from': str(d.relative_to(Path(root).resolve())), 'note': note}


def restore_film(root, to, fid):
    """从回收站拿回来（“移到回收站”后的撤销）：只认这块盘回收站 口播台/ 下、名字是“<原项目名>_<时间>”的文件夹，放回原处；原处有同名的不覆盖。"""
    root = Path(root).resolve(); src = Path(str(to or '')); dest = (root / str(fid or '')).resolve()
    rb = recycle_root(root / 'x')
    if not rb or src.parent != rb / RECYCLE_BOX or not (src / '项目.json').is_file(): raise Oops('回收站里没有这条片子了')
    if dest.parent not in (root, root / FINISHED) or not re.fullmatch(re.escape(dest.name) + r'_\d{8}-\d{6}', src.name): raise Oops('对不上原来的位置，没动')
    if dest.exists(): raise Oops(f'原来的位置已经有“{dest.name}”了，没覆盖')
    os.rename(src, dest); note = ''
    if any((dest / '02_制作' / '达芬奇工程').glob('**/Project.db')):
        try: board._pm('resolve_library').register(dest); note = '，达芬奇库也重新登记了'
        except Exception as e: note = f'；达芬奇库没登记上（{e}），下次 AI 开项时会登记'
    return {'id': str(dest.relative_to(root)), 'note': note}


def reveal(root, fid):
    d = film_dir(root, fid); subprocess.run(['open', '-R', str(d)], check=False, timeout=10); return {}


def film_ports(text):
    if not text: return None
    a, _, b = str(text).partition('-'); a = int(a); b = int(b or a)
    if not (1024 < a <= b < 65536): die('--film-ports 写成 8765-8784')
    return range(a, b + 1)


def make_handler(root, ports=None):
    ears = {'t': 0.0}; started = {}            # AI 的 home --wait 最近一次来问的时刻；首页起过的审片台（端口 → 片子 id）

    def heard(): return time.time() - ears['t'] < HEARD

    def ai_info():
        """AI 在不在等：等所有片子（home --wait）＋各条片子的审片台报的 --wait（只问在跑的服务，本机很快）。"""
        films = []
        for p in sorted(set(ports or WB.PORTS) | set(started)):
            pg = WB.ping(p, 0.3)
            if pg and pg.get('app') == '审片工作台' and pg.get('listening'):
                with contextlib.suppress(ValueError): films.append(str(Path(pg['dir']).resolve().relative_to(root)).split('/02_制作')[0])
        return {'all': heard(), 'films': films, 'cmd': f'python3 "{Path(__file__).resolve().parents[1] / "thinkcut_edit.py"}" home --wait' + (f' --root "{root}"' if root != video_root() else '')}

    def do_open(q):
        d = film_dir(root, q.get('id')); job = open_job(d); url = ensure(job, ports)
        started[int(url.rsplit(':', 1)[1].strip('/'))] = q.get('id'); return {'url': url, 'id': q.get('id')}

    def do_new(q):
        fid = new_film(root, q.get('title') if 'title' in q else q.get('new')); return {**do_open({'id': fid}), 'name': Path(fid).name}

    STRICT = {'/api/open': do_open, '/api/new': do_new,
              '/api/rename': lambda q: {'id': rename_film(root, q.get('id'), q.get('title'), bool(q.get('user_ok')))},
              '/api/finish': lambda q: {'id': finish_film(root, q.get('id'), bool(q.get('back')))},
              '/api/trash': lambda q: trash_film(root, q.get('id')),
              '/api/restore': lambda q: restore_film(root, q.get('to'), q.get('from')),
              '/api/reveal': lambda q: reveal(root, q.get('id'))}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send(self, body, kind='application/json; charset=utf-8', code=200):
            if not isinstance(body, bytes): body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def mine(self): port = self.server.server_address[1]; return {f'127.0.0.1:{port}', f'localhost:{port}'}
        def own_host(self): h = self.headers.get('Host'); return h is None or h.strip().lower() in self.mine()

        def from_page(self):
            o = self.headers.get('Origin'); site = self.headers.get('Sec-Fetch-Site')
            return (self.own_host() and o is not None and o.strip().lower() in {'http://' + x for x in self.mine()}
                    and self.headers.get('X-Shenpian-Token') == TOKEN and (site is None or site == 'same-origin'))

        def do_GET(self):
            from urllib.parse import parse_qs, unquote, urlsplit
            if not self.own_host(): return self.send({'ok': False, 'error': '只接受本机地址的请求'}, code=403)
            u = urlsplit(self.path); p = unquote(u.path); q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            if p in ('/', '/index.html'): return self.send(PAGE.read_bytes().replace(b'__SHENPIAN_TOKEN__', TOKEN.encode()), 'text/html; charset=utf-8')
            if p == '/api/ping': return self.send({'app': '首页', 'root': str(root), 'pid': os.getpid(), 'ai_all': heard()})
            if p == '/api/films': return self.send({'ok': True, 'root': str(root), 'exists': root.is_dir(), 'films': scan(root) if root.is_dir() else []})
            if p == '/api/film':
                try: return self.send({'ok': True, **status(root, q.get('id', ''))})
                except Oops as e: return self.send({'ok': False, 'id': q.get('id'), 'error': str(e)})
                except Exception as e: return self.send({'ok': False, 'id': q.get('id'), 'error': f'读这条片子出错：{e}'})
            if p == '/api/ai': return self.send({'ok': True, **ai_info()})
            if p == '/api/cover':                  # 卡片上的封面小图
                try: cv = cover_of(film_dir(root, q.get('id', '')))
                except Oops: cv = None
                if not cv: return self.send({'ok': False, 'error': '还没有封面'}, code=404)
                kind = {'.png': 'image/png', '.webp': 'image/webp'}.get(cv.suffix.lower(), 'image/jpeg')
                return self.send(cv.read_bytes(), kind)
            if p == '/open':                       # 首页进一条片子：审片台没在跑时先到这里，起好再 302 跳过去（这一步不进浏览器历史）
                if q.get('t') != TOKEN: return self.send({'ok': False, 'error': '只能在首页页面上点开'}, code=403)
                try: url = (do_new(q) if 'new' in q else do_open(q))['url']      # new＝新建片子（题目在 new 里），建好直接开
                except (Oops, RuntimeError, OSError) as e:
                    return self.send(f'<!doctype html><meta charset="utf-8"><title>打不开</title><p style="font:15px -apple-system;padding:40px">打不开这条片子：{e}</p>'.encode(), 'text/html; charset=utf-8')
                from urllib.parse import quote
                to = url + '?home=' + quote(f'http://{self.headers.get("Host") or "127.0.0.1"}', safe='') + ('#' + quote(q['col'], safe='/') if q.get('col') else '')
                self.send_response(302); self.send_header('Location', to); self.send_header('Content-Length', '0'); self.end_headers(); return
            if p == '/api/wait-all':               # AI 的 home --wait 两秒来问一次：记下“AI 在等所有片子”，告诉它首页起过哪些审片台
                ears['t'] = time.time(); return self.send({'ok': True, 'root': str(root), 'ports': sorted(started)})
            self.send({'ok': False, 'error': '没有这个地址'}, code=404)

        def do_POST(self):
            try: raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            except ValueError: return self.send({'ok': False, 'error': '格式不对'}, code=400)
            fn = STRICT.get(self.path.split('?')[0])
            if not fn: return self.send({'ok': False, 'error': '没有这个地址'}, code=404)
            if not self.from_page(): return self.send({'ok': False, 'error': '新建、打开、改名、移动、删除片子只能在首页页面上点（这个请求不是页面发的）'}, code=403)
            try: q = json.loads(raw or b'{}')
            except ValueError: return self.send({'ok': False, 'error': '格式不对'}, code=400)
            try: return self.send({'ok': True, **fn(q)})
            except Refuse as e: return self.send({'ok': False, 'need_ok': True, 'error': str(e)})
            except Oops as e: return self.send({'ok': False, 'error': str(e)})
            except (RuntimeError, OSError) as e: return self.send({'ok': False, 'error': str(e)})
    return H


def make_server(root, port, ports=None):
    return ThreadingHTTPServer(('127.0.0.1', port), make_handler(Path(root).resolve(), ports))


def serve(root, port, ports=None):
    srv = make_server(root, port, film_ports(ports))
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    say(f'首页 http://127.0.0.1:{port}/  {root}'); srv.serve_forever()


def ping(port=PORT, timeout=0.5):
    pg = WB.ping(port, timeout); return pg if pg and pg.get('app') == '首页' else None


def start(root, port=PORT, ports=None):
    """起首页服务（已在跑就复用；放片子的文件夹不一样就停下说清）。返回 (网址, 是不是复用)。"""
    pg = ping(port)
    if pg:
        if pg['root'] != str(root): die(f"首页已经在 {port} 端口跑着，看的是 {pg['root']}（不是 {root}）：先 home --stop，或换 --port")
        return f'http://127.0.0.1:{port}/', True
    if not WB.port_free(port): die(f'端口 {port} 被别的程序占了：换一个 --port')
    log = Path(os.environ.get('TMPDIR', '/tmp')) / f'ThinkCut首页_{port}.log'
    args = [sys.executable, '-B', '-m', 'thinkcut.home', 'serve', str(root), str(port)] + ([f'{ports[0]}-{ports[-1]}'] if ports else [])
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
    say(f'首页已停（端口 {port}）。各条片子的审片台还在跑，要停用 workbench <片子目录> --stop'); return True


def wait_all(root, timeout=0, port=PORT):
    """AI 用：一条命令挂着等所有片子。任何一条片子的审片台上有人点“交给 AI”（或定稿、开工），就打印是哪条片子、哪几件事，然后退出。
    只看在跑的审片台（用户能点的都在跑）：8765–8784 端口和首页起过的；只认放片子的文件夹里的片子。每两秒问一次，同时替每个审片台报“AI 在等”。"""
    t0 = time.time(); root = Path(root).resolve()
    while True:
        ports = set(WB.PORTS)
        with contextlib.suppress(Exception):
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/wait-all', timeout=2) as r: h = json.loads(r.read())
            if h.get('root') == str(root): ports |= set(h.get('ports') or [])
        for p in sorted(ports):
            pg = WB.ping(p, 0.3)
            if not pg or pg.get('app') != '审片工作台': continue
            d = Path(pg['dir'])
            if root not in d.parents: continue
            with contextlib.suppress(Exception), urllib.request.urlopen(f'http://127.0.0.1:{p}/api/wait', timeout=2) as r: r.read()
            try: job = WB.open_film(d)
            except SystemExit: continue
            got = board.pick(job)
            if got:
                pd = board.project_dir(job)
                say(f"【片子】{pd.name if pd else d.name}　片子目录：{job.dir}")
                say(f'这条片子的回复、发布照旧用 workbench "{job.dir}" --reply／--rewrite／--publish …')
                WB.report(job, got, again='再起一次 home --wait 接着等所有片子')
                return job
        if timeout and time.time() - t0 > timeout:
            say(f'等了 {int(timeout)} 秒，哪条片子都还没人点“交给 AI”。要接着等就再跑一次 home --wait'); return None
        time.sleep(2)


def cli(a):
    root = video_root(a.root); port = a.port or PORT; ports = film_ports(a.film_ports)
    if a.stop: return stop(port)
    if a.wait: return wait_all(root, float(a.wait_timeout or 0), port)
    if not root.is_dir(): die(f'放片子的文件夹不存在：{root}（用 --root 指定，或设环境变量 THINKCUT_VIDEO_ROOT）')
    url, reused = start(root, port, ports); opened = WB.open_page(url)
    say(f"首页{'已经在跑（复用）' if reused else '已启动'}：{url}（片子在 {root}）" + ('，已在浏览器打开' if opened else '；没能自动打开浏览器：AI 去打开这个网址（或发给用户）') + '\n'
        + '用户在首页看所有片子、点“新建片子”、点一条进它的审片台。\n'
        + f'【等用户】现在就挂着等所有片子：python3 "{Path(__file__).resolve().parents[1] / "thinkcut_edit.py"}" home --wait'
        + (f' --root "{root}"' if a.root else '') + '（Claude Code 放后台跑；任何一条片子有人点“交给 AI”它就打印是哪条片子的什么事并退出；Codex 加 --wait-timeout 1500）。\n'
        + f'停首页：home --stop（各条片子的审片台不受影响）')
    return url


if __name__ == '__main__' and len(sys.argv) >= 4 and sys.argv[1] == 'serve':
    serve(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None)
