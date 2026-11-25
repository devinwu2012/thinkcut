# 审片台（2026-11-22 由审片工作台扩成；审片工作台是 2026-11-21 别墅装灯反馈单 新9）：一条片子一个本机网页，左边 8 个栏目（写稿＝页面上的“文案”、文案＝页面上的“口播剪辑”、口播试听、动画、审核线、导航文字、封面、上传文案）。
# 本文件管服务、页面请求和命令行；“审核线”一栏就是原来的审片工作台：用户在达芬奇里看审核线，网页上打标记（读播放头、点字跳转、截当前帧、按轮次、AI 回写“已改/没改”）。
# 其余栏目（发布的项、用户点的确认、改字）在 board.py。数据在 片子目录/审片工作台/（标记.json、审片台.json、图/、审片台/、服务.json），格式见 references/审片工作台.md。
# “口播剪辑”栏下半是剪口播的文字剪辑器（2026-11-22 用户定，最早的审核网页并进来）：页面 文案剪辑.html，请求转给剪口播后台（koubo.py）。
# 还没 init 建 job.json 的片子也能开审片台（Lite，见 open_film）：只有项目文件夹时只有“文案”栏（写稿，writing.py）能用；剪完口播多一个“口播剪辑”栏。
# 纯标准库 HTTP 服务，只听 127.0.0.1；用户的确认、改字只收本页面带口令的请求（命令行只能发布和回复，不能替用户点）。
import contextlib, fcntl, hashlib, http.cookies, json, os, re, shutil, signal, socket, subprocess, sys, threading, time, traceback, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from fractions import Fraction
from .common import Job, ASSETS, DEFAULTS, Broken, backup_of, cutdesk_config, die, keep_backup, read_data, rj, wj, say
from . import board, formats, koubo, materials, publish, writing

PAGE = ASSETS / '审片工作台' / 'index.html'
EDITOR = ASSETS / '审片工作台' / '文案剪辑.html'          # “口播剪辑”栏下半的文字剪辑器（内嵌在审片台页面里）
PORTS = range(8765, 8785)                      # 不写 --port 时从 8765 起找空的
STATUS = ('待处理', '已改', '没改', '问你')     # 问你：AI 拿不准范围、回问用户（2026-11-22 用户定，见 references/审片工作台.md「选中的字只是定位」）


def home(job): return job.dir / '审片工作台'
def data_file(job): return home(job) / '标记.json'
def now(): return time.strftime('%Y-%m-%d %H:%M:%S')


# ---------- 口播阶段：还没有 job.json 的片子（2026-11-22 用户定：口播剪完就用审片台“口播剪辑”栏改文字剪辑） ----------
class Lite(Job):
    """还没 init 的片子目录：只有“文案”栏（写稿）和“口播剪辑”栏（有剪口播工程时）能用，其余栏目“还没到”。不建 合成/ 这些目录、不连达芬奇。
    init 建 job.json 时用的就是这个目录（标准项目 = <项目>/02_制作/自动剪辑），审片台的数据（审片工作台/）接着用。"""
    lite = True

    def __init__(self, d, aroll=None):
        self.dir = Path(d).expanduser().resolve(); self.aroll = Path(aroll).resolve() if aroll else None
        self.cfg = dict(DEFAULTS); self.cfg.update({'frames': 0, 'title': self.title_guess()})
        if self.aroll: self.cfg['aroll'] = str(self.aroll)
        self.fmt = formats.get(self.cfg['format']); self.fps = Fraction(self.cfg['fps'])

    def title_guess(self):
        root = project_root(self.dir) or (project_root(self.aroll) if self.aroll else None)
        if root:
            with contextlib.suppress(Exception): return rj(root / '项目.json').get('title') or root.name
            return root.name
        return re.sub(r'_AI剪辑$', '', self.aroll.name) if self.aroll else self.dir.name

    def p(self, *parts): return self.dir.joinpath(*parts)          # 口播阶段只读，不建目录
    def save(self): die('口播阶段还没有 job.json，先 init')


def project_root(d):
    """标准项目的项目根（有 项目.json 和 01_原始素材 的那一层）；不是返回 None。"""
    if not d: return None
    for q in (Path(d), *Path(d).parents):
        if (q / '项目.json').is_file() and (q / '01_原始素材').is_dir(): return q
    return None


def is_aroll(d): return (Path(d) / '审核' / 'project_runtime.json').is_file()


def film_dir_for(aroll):
    """剪口播工程 → 它的审片台放在哪个片子目录：剪口播工程里记过（审核/审片台.json，目录还在）就用它；
    标准项目 → <项目>/02_制作/自动剪辑（init 建 job.json 的地方，数据不用搬）；不是标准项目 → 剪口播工程自己。"""
    a = Path(aroll).resolve(); fd = koubo.pointer(a).get('film_dir')
    if fd and Path(fd).is_dir(): return Path(fd)
    root = project_root(a)
    return root / '02_制作' / '自动剪辑' if root else a


def open_film(d, aroll=None):
    """workbench 命令的片子：有 job.json → Job（口播剪辑栏用 job.json 的 aroll，命令给的不算）；
    没有 job.json → Lite。d 可以是片子目录、剪口播工程（<片名>_AI剪辑）或项目根；aroll 是命令给的剪口播工程。"""
    d = Path(d).expanduser().resolve()
    if not (d / 'job.json').is_file():
        if aroll is None and is_aroll(d): aroll = d
        if aroll is not None and d == Path(aroll).expanduser().resolve(): d = film_dir_for(aroll)
        elif (d / '项目.json').is_file(): d = d / '02_制作' / '自动剪辑'
    if (d / 'job.json').is_file():
        job = Job(d)
        if aroll and job.get('aroll') and Path(job['aroll']).resolve() != Path(aroll).expanduser().resolve():
            say(f"提醒：这个项目的 job.json 记的剪口播工程是 {job['aroll']}，审片台口播精剪栏用它（不是 {aroll}）")
        return job
    rec = d / '审片工作台' / '口播工程.json'
    if aroll: aroll = Path(aroll).expanduser().resolve()
    elif rec.is_file():
        with contextlib.suppress(Exception): aroll = Path(rj(rec)['aroll'])
    if not aroll and not project_root(d):
        die(f'{d} 不是项目文件夹：没有 job.json，也不是剪口播工程（<项目名>_AI剪辑）或项目。剪口播用 edit_project.py serve --project-dir <剪口播工程> 打开审片台')
    job = Lite(d, aroll)
    if aroll and (not rec.is_file() or rj(rec).get('aroll') != str(aroll)):
        rec.parent.mkdir(parents=True, exist_ok=True)
        wj(rec, {'说明': '口播阶段（还没 init）这个项目的剪口播工程；有 job.json 以后以 job.json 的 aroll 为准', 'aroll': str(aroll)})
    return job


def lite(job): return getattr(job, 'lite', False)


def aroll_of(job):
    a = job.get('aroll'); return Path(a) if a else None


def adopt(job):
    """不是标准项目时，口播阶段的审片台数据在剪口播工程里；init 建到别处以后，第一次开就整个搬过来（只有一份，不会分成两份）。"""
    a = aroll_of(job)
    if lite(job) or not a or (home(job)).exists(): return
    src = film_dir_for(a)
    if src == job.dir or (src / 'job.json').is_file() or not (src / '审片工作台').is_dir(): return
    old = Lite(src, a)
    if running(old): stop(old)
    import shutil
    shutil.move(str(src / '审片工作台'), str(home(job)))
    say(f'口播阶段的审片台数据从 {src} 搬进了项目文件夹 {job.dir}（标记、确认、改字记录接着用）')


# ---------- 数据：标记.json（服务和命令行都经过这把文件锁读改写，不会互相盖掉） ----------
_locks = {}; _locks_guard = threading.Lock()


@contextlib.contextmanager
def _flock(job):
    home(job).mkdir(parents=True, exist_ok=True)          # 口播阶段的片子目录（02_制作/自动剪辑）可能还没建
    with _locks_guard: mine = _locks.setdefault(str(home(job)), threading.Lock())
    with mine, open(home(job) / '.标记.lock', 'w') as f:   # 同一个进程里先用线程锁排队：网络盘上的文件锁不一定管用（2026-11-25 NAS 实测两个请求同时写 标记.json）
        fcntl.flock(f, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)


DATA_FILES = {'标记': lambda job: data_file(job), '审片台': lambda job: board.board_file(job), '写稿': lambda job: writing.data_file(job), '备料': lambda job: materials.record_file(job)}


def repair(job, which):
    """坏掉的数据文件：原样改名留着（<名字>.坏的_时间），有上一版就退回上一版，没有就从空的开始。返回说明。"""
    if which not in DATA_FILES: raise Oops('不认识的数据文件')
    f = DATA_FILES[which](job)
    with _flock(job):
        try: read_data(f); return {'ok': True, 'restored': False, 'note': f'{f.name} 没坏'}
        except Broken: pass
        keep = f.with_name(f'{f.name}.坏的_{time.strftime("%Y%m%d-%H%M%S")}'); os.replace(f, keep); b = backup_of(f); restored = False
        if b.is_file():
            try: read_data(b); shutil.copyfile(b, f); restored = True
            except Broken: pass
    return {'ok': True, 'restored': restored, 'kept': str(keep)}


def load(job):
    f = data_file(job)
    d = read_data(f)
    d.setdefault('说明', '审片工作台的标记（thinkcut_edit.py workbench）。格式见 skill/thinkcut-edit/references/审片工作台.md')
    d.setdefault('schema', 1); d.setdefault('round', 1); d.setdefault('markers', []); d.setdefault('nav_labels', {})
    return d


def edit(job, fn):
    """锁住 → 读 → fn(data) 改 → 写回；返回 fn 的结果。"""
    with _flock(job):
        d = load(job); out = fn(d); keep_backup(data_file(job)); wj(data_file(job), d); return out


def marker(d, mid):
    m = next((x for x in d['markers'] if x['id'] == mid), None)
    if not m: raise Oops(f'没有第 {mid} 条标记')
    return m


class Oops(Exception):
    """给页面看的中文错误。"""


class Refuse(Exception):
    """不是本页面发来的用户操作（403）。"""


def _prog():
    """这份审片台程序是哪一版：程序文件夹＋代码和页面的指纹（首页复用别人起的审片台前比对，不是同一版就换掉重起，2026-11-24）。"""
    h = hashlib.sha256(); here = Path(__file__).resolve().parent
    for f in sorted(x for x in here.glob('*.py') if not x.name.startswith('test_')) + [PAGE, EDITOR]:
        with contextlib.suppress(OSError): h.update(f.name.encode() + f.read_bytes())
    return {'dir': str(here.parents[1]), 'sig': h.hexdigest()[:12]}


PROG = _prog()
TOKEN = os.urandom(12).hex()               # 页面口令：服务每次启动换一个；只放在 HttpOnly cookie 里（QA 第四节：以前嵌在页面和 /api/data 里，AI curl 一下就能拿到、冒充用户点“确定此栏”）
SESSION = hashlib.sha256(TOKEN.encode()).hexdigest()[:16]    # 公开的“这一次服务”的编号：页面里、/api/data 的 board.token 给的是它（页面拿它判断服务重启了没有），不能拿来确认
COOKIE = 'thinkcut_wb'                     # cookie 名字后面接端口（127.0.0.1 上不同端口的 cookie 是共用的）
IDLE = float(os.environ.get('THINKCUT_WB_IDLE') or 45 * 60)    # 审片台闲这么久（秒；页面没开、没人来问）自己退出（QA 1.2）

def cookie_header(x):
    """测试、自检用：带上本服务页面的 cookie（x＝端口或网址）。浏览器打开页面时服务自己发这个 cookie。"""
    port = x if isinstance(x, int) else int(str(x).rstrip('/').rsplit(':', 1)[1].split('/')[0])
    return f'{COOKIE}_{port}={TOKEN}'

MEDIA = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif',
         '.m4a': 'audio/mp4', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.aac': 'audio/aac',
         '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm'}


def mark_kind(k):
    k = str(k or '').strip()
    if k and k not in board.MARK_KINDS: raise Oops(f'类型只能是：{"、".join(board.MARK_KINDS)}（也可以不选）')
    return k


# ---------- 口播：标记时间点正在说的那句 ----------
def quote_at(job, t):
    f = job.dir / '口播字时间.json'
    if not f.exists(): return ''
    tok = rj(f).get('tokens') or []
    if not tok: return ''
    i = max([k for k, x in enumerate(tok) if x['t0'] <= t] or [0])
    if 'u' in tok[i]: s = [x['c'] for x in tok if x.get('u') == tok[i]['u']]
    else: s = [x['c'] for x in tok[max(0, i - 8):i + 9]]
    s = ''.join(s); return s if len(s) <= 60 else s[:58] + '…'


def script_files(job):
    d = job.p('合成', '字幕', 'x').parent; return d / '字幕表_全部.json', d / '字幕表.json', job.dir / '口播字时间.json'


def script(job):
    """整条口播全文，给页面排成文章（照 261117《别墅装灯前》试用的审片台：按章节连成大段，选一段字写要求；2026-11-21 用户定）。
    返回 (来源, [{t, t1, text, ct}])：一项是一句，ct 是这句每个字的成片秒数（和 text 一样长，标点取前一个字的时间）。
    字从 口播字时间.json 来（每字时间，已含字幕纠错）；没有标点，按句（u）结尾补“。”，按审核线字幕的换条处补“，”。
    没有 口播字时间.json 时用审核线字幕（字幕表_全部.json → 字幕表.json，帧号换秒），每条当一句、字的时间在这条里均分。"""
    fps = float(job.fps); *subs, words = script_files(job)
    cuts = []
    for f in subs:
        if f.exists(): cuts = sorted(rj(f) or [], key=lambda r: r['start']); break
    tok = rj(words).get('tokens') or [] if words.exists() else []
    if tok:
        starts = sorted({round(r['start'] / fps, 3) for r in cuts}); out, k = [], 0
        for x in tok:
            new_u = not out or x.get('u') != out[-1]['u'] or 'u' not in x
            while k < len(starts) and starts[k] <= x['t0'] + 0.04: k += 1       # 过了一条字幕的开头 → 前面补逗号
            if out and not new_u and k != out[-1]['k']: out[-1]['text'] += '，'; out[-1]['ct'].append(out[-1]['ct'][-1])
            if new_u:
                if out: out[-1]['text'] += '。'; out[-1]['ct'].append(out[-1]['ct'][-1])
                out.append({'u': x.get('u'), 't': x['t0'], 't1': x['t1'], 'text': '', 'ct': [], 'k': k})
            o = out[-1]; c = x['c'] or ''; o['text'] += c; o['ct'] += [round(x['t0'] + (x['t1'] - x['t0']) * i / len(c), 3) for i in range(len(c))];   # 纠错并过的一个 token 可能是几个字
            o['t1'] = x['t1']; o['k'] = k
        if out: out[-1]['text'] += '。'; out[-1]['ct'].append(out[-1]['ct'][-1])
        return '口播字时间', [{'t': round(o['t'], 3), 't1': round(o['t1'], 3), 'text': o['text'], 'ct': o['ct']} for o in out]
    out = []
    for r in cuts:
        txt = str(r.get('text', '')).replace(' / ', '').replace(' ', '，'); a, b = r['start'] / fps, r['end'] / fps
        if not txt: continue
        out.append({'t': round(a, 3), 't1': round(b, 3), 'text': txt + '。', 'ct': [round(a + (b - a) * i / len(txt), 3) for i in range(len(txt))] + [round(b, 3)]})
    return ('审核线字幕' if out else ''), out


# ---------- 导航条：章节和用户改的字（只存，不改 导航/project.json；AI 下一轮改片子时照着改） ----------
def chapters(job):
    f = job.dir / '导航' / 'project.json'
    if not f.exists():
        vs = sorted((job.dir / '导航').glob('v*/project.json'), key=lambda p: p.stat().st_mtime)
        f = vs[-1] if vs else None
    if not f: return []
    out = []
    for s in rj(f).get('segments') or []:
        if out and out[-1]['id'] == s.get('chapter'): out[-1]['b'] = s['end']
        else: out.append({'id': s.get('chapter'), 'a': s['start'], 'b': s['end'], 'label': s.get('label') or s.get('title') or ''})
    return out


def placeholders_of(job):
    """这条片子的占位（机器配音、占位图），给页面在审核线栏头下面写一行（2026-11-22 用户定：占位的东西标清楚，“我有了之后我再替换”）。读不了就是 []。"""
    if lite(job): return []
    try:
        from . import draft
        return [{k: v for k, v in p.items() if k in ('kind', 'where', 'text')} for p in draft.placeholders(job)] + [{'kind': '假设', 'where': '全片', 'text': x} for x in draft.assumptions(job)]
    except Exception: return []


# ---------- 全片时间条上的镜头区间（审片台 v2，2026-11-22 用户：“如何更方便地审核动画线……让我和AI都能看清楚审核线”） ----------
def shot_spans(job):
    """片子目录 分镜.json 的 shots 里的 B 镜头 → [{id, kind: 动画／实拍／图片, a, b}]（成片秒）。整段实拍、整段图片按镜头算；动画镜头里“盖实拍”的那几段单独算实拍。
    露脸镜头（F）不画。没有 分镜.json 或读不了就是 []（页面就不画）。"""
    f = job.dir / '分镜.json'
    if not f.is_file(): return []
    try: shots = rj(f).get('shots') or []
    except Exception: return []
    out = []
    for s in shots:
        try:
            if s.get('kind') != 'B': continue
            a, b = round(float(s['a']), 3), round(float(s['b']), 3)
            kind = '实拍' if s.get('实拍') else '占位图' if s.get('占位图') else '图片' if s.get('图片') else '动画'      # 占位图＝AI 生成的占位画面（2026-11-22），时间条上单独一种
            out.append({'id': s.get('id', ''), 'kind': kind, 'a': a, 'b': b})
            for c in s.get('盖实拍') or []:
                out.append({'id': s.get('id', ''), 'kind': '实拍', 'a': round(float(c['a']), 3), 'b': round(float(c['b']), 3)})
        except (KeyError, TypeError, ValueError): continue
    return out


# ---------- 达芬奇：所有和达芬奇打交道的都在这里，测试时整个换成替身 ----------
@contextlib.contextmanager
def dv_lock(job, what):
    """达芬奇写入锁（反馈单 新17，resolve.lock）：只在跳转/截图这一下拿着，做完马上放（服务一直开着，不能拿到进程结束）。
    别的命令正在写达芬奇时 resolve.lock 直接停（SystemExit，写清谁在用），页面把这句显示给用户，这次不动达芬奇。"""
    from . import resolve
    resolve.lock(job, what)
    try: yield
    finally: resolve.unlock()


class DV:
    """读播放头（只读，不加锁）；跳转、切回剪辑页、截图算写（动达芬奇的状态），走达芬奇锁。
    不切时间线、不切工程、不保存工程。开着的不是这条片子的工程就不跳不截。"""
    def __init__(self, job):
        self.job, self.r = job, None

    def _ctx(self):
        from . import resolve
        if self.r is None:
            try: self.r = resolve.connect()
            except SystemExit as e: raise Oops(str(e).replace('停止：', ''))
        try:
            p = self.r.GetProjectManager().GetCurrentProject(); tl = p and p.GetCurrentTimeline()
        except Exception:
            self.r = None; raise Oops('和达芬奇的连接断了，再试一次')
        if not p: raise Oops('达芬奇里没有打开工程')
        if p.GetName() != self.job['project']: raise Oops(f"达芬奇开着的工程是「{p.GetName()}」，不是这个项目的「{self.job['project']}」")
        if not tl: raise Oops('达芬奇里没有打开时间线')
        return self.r, p, tl, float(tl.GetSetting('timelineFrameRate'))

    @staticmethod
    def _frames(tc, fps):
        h, m, s, f = [int(x) for x in re.split('[:;]', tc)]; n = round(fps); return ((h * 60 + m) * 60 + s) * n + f

    def status(self):
        r, p, tl, fps = self._ctx(); st = self._frames(tl.GetStartTimecode(), fps)
        return {'timeline': tl.GetName(), 'page': r.GetCurrentPage(), 'rendering': bool(p.IsRenderingInProgress()),
                't': round((self._frames(tl.GetCurrentTimecode(), fps) - st) / fps, 3), 'dur': round((tl.GetEndFrame() - tl.GetStartFrame()) / fps, 3)}

    def goto(self, t):
        from . import resolve
        r, p, tl, fps = self._ctx()
        if p.IsRenderingInProgress(): raise Oops('达芬奇正在渲染，先不跳')
        fr = max(0, min(tl.GetEndFrame() - tl.GetStartFrame() - 1, round(float(t) * fps)))
        with dv_lock(self.job, '审片工作台：跳转'):
            if not tl.SetCurrentTimecode(resolve.tcode(self._frames(tl.GetStartTimecode(), fps) + fr, fps)): raise Oops('达芬奇没跳过去')
        return {'t': round(fr / fps, 3)}

    def toggle_play(self):
        """网页里按空格 → 给达芬奇发一个空格键（达芬奇自己的播放/暂停，再按一次就停；2026-11-21 用户：“我每次在网页里按空格，它自动在达芬奇里就空格播放了”）。
        达芬奇脚本接口没有播放/暂停，只能发按键：把达芬奇切到前面按空格再切回浏览器（见 Keys）。
        要 macOS“辅助功能”权限（给跑这个服务的 Python）；没有就停下说清楚。只认这条片子的工程，渲染时不发，走达芬奇写入锁。"""
        r, p, tl, fps = self._ctx()
        if p.IsRenderingInProgress(): raise Oops('达芬奇正在渲染，先不播')
        if r.GetCurrentPage() not in ('edit', 'cut', 'color', 'fairlight'): raise Oops('达芬奇不在“剪辑”页，空格播不了：先在达芬奇里切到剪辑页')
        with dv_lock(self.job, '审片工作台：播放/暂停'):
            KEYS.space()
        return {'ok': True}

    def still(self, out: Path):
        """当前这一帧存成 out（jpg，长边缩到 1440）。渲完小样停在交付页时先切回剪辑页（用户 2026-11-21 补充）。"""
        r, p, tl, fps = self._ctx()
        if p.IsRenderingInProgress(): raise Oops('达芬奇正在渲染，截不了图')
        page = r.GetCurrentPage()
        if page not in ('edit', 'color', 'deliver'): raise Oops('请把达芬奇切到“剪辑”页再截图（现在在别的页面）')
        out.parent.mkdir(parents=True, exist_ok=True); raw = out.with_name('.截图中.jpg')
        if raw.exists(): raw.unlink()
        with dv_lock(self.job, '审片工作台：截图'):
            if page == 'deliver':
                r.OpenPage('edit')
                for _ in range(20):
                    if r.GetCurrentPage() == 'edit': break
                    time.sleep(0.1)
            if not p.ExportCurrentFrameAsStill(str(raw)) or not raw.exists(): raise Oops('达芬奇没截出图')
        shrink(raw, out); return out


NEED_AX = ('要先给“辅助功能”权限才能让达芬奇播放：系统设置 → 隐私与安全性 → 辅助功能，打开 Python（列表里没有就点 + 加 '
          + str(Path(sys.base_prefix) / 'Resources' / 'Python.app') + '），开好后再按一次空格就行。没给权限也照样能在达芬奇里自己按空格。')


HELPER = r"""
# 审片工作台的按键小服务（thinkcut workbench 自动放在这里、用 open 交给系统启动的 Python.app 跑；只听本机，一直开着，闲 3 小时自己退）。
import json, os, socket, subprocess, sys, time
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
import Quartz
from AppKit import NSWorkspace, NSRunningApplication
from Foundation import NSRunLoop, NSDate
INFO, VER = sys.argv[1], sys.argv[2]
ws = NSWorkspace.sharedWorkspace()

def front(): return ws.frontmostApplication()

def pump(sec):                                                # 常驻进程要转一下系统消息，“谁在前面”才会更新（只 sleep 会一直是旧的）
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(sec))

def bring(app, wait=1.0):
    pid = app.processIdentifier()
    if front().processIdentifier() == pid: return True
    app.activateWithOptions_(3)                               # 先用系统接口切；新版 macOS 有时不让切，再用 open -a（系统启动 App 的路子，切得动）
    for k in range(2):
        t0 = time.time()
        while time.time() - t0 < wait:
            if front().processIdentifier() == pid: return True
            pump(0.02)
        if k == 0 and app.bundleURL(): subprocess.run(['open', '-a', app.bundleURL().path()], capture_output=True, timeout=5)
    return False

def space(pid):
    if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}): return 'noax'
    dv = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if dv is None: return 'err 找不到达芬奇进程'
    pump(0.01); prev = front(); away = prev.processIdentifier() != pid
    if away:
        if not bring(dv): return 'err 达芬奇切不到前面'
        pump(0.12)                                            # 达芬奇刚到前面，窗口要一下才接键盘
    for down in (True, False): Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, 49, down)); time.sleep(0.03)
    if away: time.sleep(0.05); bring(prev, 0.5)               # 切回原来在前面的浏览器
    return 'ok'

srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(('127.0.0.1', 0)); srv.listen(4); srv.settimeout(3 * 3600)
json.dump({'port': srv.getsockname()[1], 'pid': os.getpid(), 'ver': VER}, open(INFO, 'w'))
while True:
    try: c, _ = srv.accept()
    except socket.timeout: break
    with c:
        c.settimeout(5)
        try:
            cmd = c.recv(100).decode().split()
            if cmd[:1] == ['quit']: c.sendall(b'ok'); break
            res = space(int(cmd[1])) if cmd[:1] == ['space'] else 'err 不认识的命令'
        except Exception as e: res = 'err ' + str(e)
        try: c.sendall(res.encode())
        except Exception: pass
"""


class Keys:
    """给达芬奇发空格键：先把达芬奇切到前面、按空格、再切回原来的浏览器（屏幕会闪一下）。测试时整个换成替身。
    2026-11-21 261118 实测的坑：①服务是从 AI 的命令行起的，macOS 把“辅助功能”权限算在 Claude 头上，用户给 Python 的权限不认；
    ②交给系统启动（open -a Python.app）的 Python 才认这个权限，但它读不了 NAS（服务和片子都在 NAS 上）；
    ③达芬奇在后面时直接发给它进程的按键它不认（用户：“没反应”），要切到前面按；
    ④每按一次都新起一个 Python，载入系统库有时要 1–12 秒，按键晚到，用户觉得“不是每次都成功”；新版 macOS 有时不让切到达芬奇。
    所以：按键这一小步是本机常驻小服务（~/Library/Application Support/ThinkCut/审片工作台/按空格.py，文件夹名见 cutdesk_config.app_dir，系统启动的 Python.app 跑，只听本机），
    服务第一次用时起它、以后直接连；切前台先用系统接口，切不动再 open -a。"""
    APP = Path(sys.base_prefix) / 'Resources' / 'Python.app'
    HOME = cutdesk_config.app_dir() / '审片工作台'

    def _ask(self, cmd, timeout=6):
        info = self.HOME / '按键服务.json'
        d = json.loads(info.read_text()) if info.exists() else {}
        with socket.create_connection(('127.0.0.1', int(d['port'])), timeout=0.5) as c:
            c.settimeout(timeout); c.sendall(cmd.encode()); return c.recv(400).decode(), d

    def _helper(self):
        """连上常驻小服务；没开、或是旧版本就（重）起。"""
        import hashlib
        ver = hashlib.md5(HELPER.encode()).hexdigest()[:10]; info = self.HOME / '按键服务.json'
        with contextlib.suppress(Exception):
            if json.loads(info.read_text()).get('ver') == ver: return
            self._ask('quit', 2)
        if not self.APP.is_dir(): raise Oops(f'找不到 {self.APP}，空格播放用不了；在达芬奇里自己按空格')
        self.HOME.mkdir(parents=True, exist_ok=True); helper = self.HOME / '按空格.py'
        helper.write_text(HELPER, encoding='utf-8'); info.unlink(missing_ok=True)
        subprocess.run(['open', '-n', '-g', '-a', str(self.APP), '--stdout', str(self.HOME / '按键服务.log'), '--stderr', str(self.HOME / '按键服务.log'),
                        '--args', str(helper), str(info), ver], capture_output=True, timeout=10)
        for _ in range(300):                                   # 第一次载入系统库可能要十几秒
            if info.exists(): return
            time.sleep(0.05)
        raise Oops('按键小服务没起来（15 秒），看 ' + str(self.HOME / '按键服务.log'))

    def space(self):
        pid = next((int(x) for x in subprocess.run(['pgrep', '-x', 'Resolve'], capture_output=True, text=True).stdout.split()), None)
        if not pid: raise Oops('找不到达芬奇进程')
        self._helper()
        try: res, _ = self._ask(f'space {pid}')
        except (OSError, ValueError, KeyError):                  # 小服务被关了（重启、闲太久自己退）：重起一次再试
            (self.HOME / '按键服务.json').unlink(missing_ok=True); self._helper(); res, _ = self._ask(f'space {pid}')
        if res == 'ok': return
        if res == 'noax': raise Oops(NEED_AX)
        raise Oops('空格没发出去：' + (res or '按键小服务没回话'))


KEYS = Keys()


def shrink(src, dst, edge=1440):
    from PIL import Image
    with Image.open(src) as im: im = im.convert('RGB')
    im.thumbnail((edge, edge)); im.save(dst, quality=88); Path(src).unlink()


# ---------- 页面数据（服务的 /api/data；首页也用它算每条片子的状态） ----------
def page_data(job, heard=False):
    """页面要的全部数据（/api/data）；首页给每条片子算状态也用它（heard＝AI 的 --wait 正在等）。"""
    d = load(job); f = data_file(job); W, H = job.W, job.H; src, lines = script(job)
    d.update(script=lines, script_src=src)
    sp = board.script_path(job); pd = board.project_dir(job); pj = pd / '项目.json' if pd else None
    watch = (f, board.board_file(job), writing.data_file(job), materials.record_file(job), *script_files(job), *(x for x in (sp, pj) if x), job.dir / '分镜.json',
             job.dir / 'job.json', job.dir / '导航' / 'project.json')
    kb = koubo.info(aroll_of(job))
    d.update(version=sum(x.stat().st_mtime_ns for x in watch if x.exists()) + int(kb['ready']) + 3 * int(heard), title=job.get('title', job.dir.name), W=W, H=H, nav=list(job.fmt['nav']),
             dur=round(job.get('frames', 0) / float(job.fps), 3), chapters=chapters(job), review_timeline=job.get('review_timeline', ''), lite=lite(job), koubo=kb, shots=shot_spans(job), placeholders=placeholders_of(job))
    d['board'] = board_meta(job, d, heard)
    return d

def board_meta(job, d, heard=False):
    """审片台其余栏目：每栏的状态字、项（带算好的状态）、用户改字记录、文案和上传文案的字。"""
    b = board.load(job); items = []; b['items'] = board.live(b['items'])      # 撤回的不显示
    for it in b['items']:
        st, todo = board.state(it); items.append({**it, 'state': st, 'todo': todo})
    try: sc = board.script_info(job)
    except Exception as e: sc = {'exists': False, 'why': f'读不了项目文案：{e}'}
    try: up = board.upload_info(job)
    except Exception as e: up = {'exists': False, 'why': f'读不了 项目.json：{e}'}
    pd = board.project_dir(job); name = ''
    if pd:
        try: name = rj(pd / '项目.json').get('title') or pd.name
        except Exception: name = pd.name
    extra = {'写稿': bool(pd), '文案': sc.get('exists') or d['koubo']['ready'], '导航文字': bool(d['chapters']), '上传文案': up.get('exists')}
    cols = {c: board.column_badge(c, b['items'], extra.get(c)) for c in board.COLUMNS if c != '审核线'}
    if d['lite']:                       # 还没建片子目录：只有文案栏（写稿）、口播剪辑栏能用
        cols.update({c: {'text': '还没到', 'kind': 'none', 'n': 0} for c in cols if c not in ('写稿', '文案')})
    cur = [m for m in d['markers'] if m['round'] == d['round']]
    cols['审核线'] = ({'text': f"第 {d['round']} 轮 · {len(cur)} 条", 'kind': 'wait' if any(m['status'] == '待处理' for m in cur) else 'done', 'n': 0}
                    if d['review_timeline'] or d['markers'] else {'text': '还没到', 'kind': 'none', 'n': 0})
    snd = {**board.send_info(job), 'listening': heard}
    for c, v in snd['cols'].items():                 # 每栏一个明确的状态：有等你看的先写等你看；再是 没发给 AI → 已发给 AI → AI 在处理
        if c not in cols or cols[c]['kind'] == 'none' or cols[c]['n']: continue
        word = f"{v['unsent']} 条没提交 AI" if v['unsent'] else '已提交 AI' if v['sent'] else f"AI 在处理 {v['doing']} 条"
        cols[c] = {**cols[c], 'text': (f"第 {d['round']} 轮 · " if c == '审核线' else '') + word, 'kind': 'wait' if v['unsent'] else 'ai'}
    stl = board.settle_info(job, {c: v['n'] for c, v in cols.items()})
    for c, v in cols.items():           # “已定”只认用户的确认（2026-11-22 用户定）：点过“这一栏定了”，或这一栏每一项都点过可以／选这个；否则写“没有待办”
        if v['kind'] != 'done': continue
        mine = [x for x in items if x['column'] == c]
        if (stl.get(c) or {}).get('state') == 'ok': cols[c] = {**v, 'text': ('第 %s 轮 · ' % d['round'] if c == '审核线' else '') + '已定稿 ✓'}
        elif mine and all(x['state'] == '你已定' for x in mine): cols[c] = {**v, 'text': '你已定'}
        else: cols[c] = {**v, 'text': v['text'] if c == '审核线' else '没有待办', 'kind': 'idle'}
    # 审片台 v2（2026-11-22 用户定）：页面只用 4 个状态。上面的 badges 等老字段照旧给（命令行、老页面、测试认它们），页面用下面算好的 ustate／ucols／totals
    ids = snd['ids']
    for x in items: x['ustate'] = board.ustate_item(x, ids); x['decided'] = x['state'] == '你已定'; x['opts'] = board.item_opts(x)
    ok = lambda c: (stl.get(c) or {}).get('state') == 'ok'
    for m in d['markers']: m['ustate'] = board.ustate_marker(m, d['round'], ids, ok('审核线')); m['opts'] = marker_opts(m)
    wr = writing.page_info(job, ids)      # 文案栏（写稿）：文案的三类字、意见和对话、AI 的改写建议、版本
    ucols = {}
    for c in board.COLUMNS:
        sc_ = snd['cols'].get(c) or {}; mine = [x['ustate'] for x in items if x['column'] == c]
        if c == '审核线': mine_seen = [m['ustate'] for m in d['markers'] if m['ustate']]
        elif c == board.WRITE: mine_seen = [n['ustate'] for n in wr['notes'] if n['ustate']]
        else: mine_seen = mine
        counts = {'待你看': sum(s == '待你看' for s in mine_seen), '待发送': sc_.get('unsent', 0), 'AI 在改': sc_.get('sent', 0) + sc_.get('doing', 0)}
        off = (cols.get(c) or {}).get('kind') == 'none' or (c == '文案' and not d['koubo']['ready'] and not mine and not any(counts.values()))   # 文案的字挪到“文案”栏改了：没有剪口播工程、也没有卡片时口播剪辑栏还没到
        ucols[c] = {'state': board.ucolumn(counts, ok(c), mine, off), 'off': off, 'counts': counts, 'n': len(mine) if c != '审核线' else len([m for m in d['markers'] if m['round'] == d['round']])}
    vis = board.visible_columns(b['items'])
    totals = {'轮到你': sum(ucols[c]['counts']['待你看'] + ucols[c]['counts']['待发送'] for c in vis if not ucols[c]['off']),
              'AI 在改': sum(ucols[c]['counts']['AI 在改'] for c in vis if not ucols[c]['off'])}
    return {'send': snd, 'settle': stl, 'columns': vis, 'badges': cols, 'items': items, 'edits': b['edits'][-60:], 'drafts': b['drafts'], 'script': sc, 'upload': up,
            'name': name, 'waiting': sum(v['n'] for v in cols.values()), 'token': SESSION, 'ucols': ucols, 'totals': totals, 'writing': wr, 'publish': publish.info(job),
            'materials': materials.info(job) if wr.get('kick') else None}     # 备料（定稿以后）：收到了哪些、放在哪、正在传的进度


# ---------- 页面服务 ----------
def page_cookie(h):
    """请求带的本服务 cookie（没有给 None）。"""
    try: c = http.cookies.SimpleCookie(h.headers.get('Cookie') or '')
    except http.cookies.CookieError: return None
    v = c.get(f'{COOKIE}_{h.server.server_address[1]}'); return v.value if v else None


_refresh_lock = threading.Lock()


def refresh(job):
    """job.json 改了（AI 建了第二轮审核线、换了导出设置）就重读（QA 1.4：以前服务一直用启动时读到的，“确定此栏”和导出对不上）。返回是不是换了。"""
    if lite(job): return False
    try: mt = (job.dir / 'job.json').stat().st_mtime_ns
    except OSError: return False
    if getattr(job, '_mt', None) == mt: return False
    with _refresh_lock:
        if getattr(job, '_mt', None) == mt: return False
        first = getattr(job, '_mt', None) is None
        try: Job.__init__(job, job.dir)
        except (SystemExit, ValueError, KeyError): return False       # 正写到一半：下次再读
        job._mt = mt; return not first


activity = {'t': time.time()}              # 最近一次有人用（页面拿数据、点东西）；ping、AI 的 --wait 不算


def make_handler(job, dv):
    big = threading.Lock()                 # 达芬奇脚本接口一次只走一个请求
    refresh(job)
    def dv_reset():                        # job.json 换了：达芬奇子进程按新的 job.json 重开（它读审核线名字）
        if hasattr(dv, 'reset'): dv.reset()
    backends = {}                          # 剪口播后台（按剪口播工程目录，一个就够）
    ears = {'t': 0.0}                      # AI 的 --wait 最近一次来问的时刻（页面据此写“AI 在等”还是“AI 没在等”）

    def listening(): return time.time() - ears['t'] < 8

    def backend(a):
        return backends.setdefault(str(a), koubo.Backend(a))

    def call(fn, *a):
        with big:
            try: return fn(*a)
            except Oops: raise
            except SystemExit as e: raise Oops(str(e).replace('停止：', ''))
            except Exception as e: raise Oops(f'达芬奇出错：{e}')

    seen = {'t': None}                     # 页面最后一次来拿数据的时间（出样张时判断页面开着没有：开着就不再弹“预览”）

    def meta():
        d = page_data(job, listening()); d['home'] = home_url(); d['wait_cmd'] = wait_cmds(job); return d

    def mark(_):
        s = call(dv.status)
        def add(d):
            n = max([m['id'] for m in d['markers']] or [0]) + 1; R = d['round']
            img = f"图/第{R}轮_{int(s['t'] // 60)}分{s['t'] % 60:04.1f}秒_{n}.jpg"
            call(dv.still, home(job) / img)
            m = {'id': n, 'round': R, 't': s['t'], 'timeline': s['timeline'], 'quote': quote_at(job, s['t']), 'frame': img, 'imgs': [],
                 'text': '', 'status': '待处理', 'reply': '', 'of': None, 'at': now()}
            d['markers'].append(m); return m
        return edit(job, add)

    def span_fields(q):
        """页面上选的那段字：span 是全文第几个字到第几个字（页面画标记用），t/t_end 是这段的成片起止秒，quote 是这段字。"""
        sp = q.get('span')
        out = {'quote': str(q.get('quote', ''))[:200], 't_end': round(float(q['t_end']), 3)}
        if isinstance(sp, list) and len(sp) == 2: out['span'] = [int(sp[0]), int(sp[1])]
        return out

    def mark_range(q):
        """在全文上选一段字写要求：时间取这段开头，quote 填这段字；不截图（卡片上点“截达芬奇当前画面”再截）。"""
        f = span_fields(q); t = round(float(q['t']), 3)
        def add(d):
            n = max([m['id'] for m in d['markers']] or [0]) + 1
            m = {'id': n, 'round': d['round'], 't': t, 'timeline': job.get('review_timeline', ''), 'frame': '', 'imgs': [],
                 'text': str(q.get('text', '')), 'status': '待处理', 'reply': '', 'of': None, 'at': now(), **f}
            if q.get('kind'): m['kind'] = mark_kind(q['kind'])
            d['markers'].append(m); return m
        return edit(job, add)

    def set_kind(q):
        """审片台 v2：提意见时点的类型（字幕／花字／动画／B-roll／节奏／声音／其它，可以不选）。存进标记的 kind；只改这一轮的。不算改了意见（不变回“待发送”）。"""
        k = mark_kind(q.get('kind') or '')
        def g(d):
            m = marker(d, int(q['id']))
            if m['round'] != d['round']: raise Oops('旧轮次的标记不能改')
            if k: m['kind'] = k
            else: m.pop('kind', None)
            return {'ok': True, 'kind': k}
        return edit(job, g)

    def set_range(q):
        """拖选区两头的拖动条、或“这一帧有问题”之后选中正在说的那一小句。截过图的标记不改 t（t 是截图那一刻）。"""
        f = span_fields(q)
        def g(d):
            m = marker(d, int(q['id']))
            if m['round'] != d['round']: raise Oops('旧轮次的标记不能改')
            m.update(f)
            if 't' in q and not m.get('frame'): m['t'] = round(float(q['t']), 3)
            return {'ok': True}
        return edit(job, g)

    def add_img(m, name):
        if m.get('frame'): m.setdefault('imgs', []).append(name)
        else: m['frame'] = name

    def shot(q):
        """给已有的标记再截一张达芬奇当前画面。"""
        mid = int(q['id']); d0 = load(job); m0 = marker(d0, mid)
        if m0['round'] != d0['round']: raise Oops('旧轮次的标记不能加图')
        s = call(dv.status); img = f"图/第{m0['round']}轮_{int(s['t'] // 60)}分{s['t'] % 60:04.1f}秒_{mid}_{int(time.time() * 1000) % 100000}.jpg"
        call(dv.still, home(job) / img)
        def g(d): add_img(marker(d, mid), img); return {'ok': True, 'name': img}
        return edit(job, g)

    def upload(q):
        """卡片里 ⌘V 贴的图、拖进来的图。"""
        import base64
        mt = re.match(r'data:image/(png|jpeg|gif|webp);base64,(.+)', q.get('data') or '', re.S)
        if not mt: raise Oops('只能放图片')
        mid = int(q['id']); img = f"图/你贴的_{time.strftime('%m%d-%H%M%S')}_{os.urandom(2).hex()}.{'jpg' if mt[1] == 'jpeg' else mt[1]}"
        def g(d):
            m = marker(d, mid)
            if m['round'] != d['round']: raise Oops('旧轮次的标记不能加图')
            (home(job) / '图').mkdir(exist_ok=True); (home(job) / img).write_bytes(base64.b64decode(mt[2])); add_img(m, img); return {'ok': True, 'name': img}
        return edit(job, g)

    def del_img(q):
        def g(d):
            m = marker(d, int(q['id'])); name = q.get('name')
            if m['round'] != d['round']: raise Oops('旧轮次的标记不能改')
            if m.get('frame') == name: m['frame'] = (m.get('imgs') or [''])[0]; m['imgs'] = (m.get('imgs') or [])[1:]
            elif name in (m.get('imgs') or []): m['imgs'].remove(name)
            return {'ok': True}
        return edit(job, g)

    def set_text(q):
        def f(d):
            m = marker(d, int(q['id']))
            if m['round'] != d['round']: raise Oops('旧轮次的标记不能改；还不行就点“这条还不行”')
            m['text'] = str(q.get('text', ''))
            if m['status'] != '待处理': m['status'] = '待处理'          # AI 回过以后用户又改了字：重新等 AI 处理
            return {'ok': True}
        return edit(job, f)

    def answer(q):
        """用户在卡片上回复 AI（AI 回过话以后随时能回：问你、已改、没改都行）：文字、点的选项（picked）、带的图（imgs，先用 /api/replyimg 存好）记进 thread，
        这条回到“待处理”（页面上是待发送，和别的一起由“交给 AI”发出去）。AI 改了用户又回＝不满意接着改，这一轮里不用新开一条。"""
        text = str(q.get('text', '')).strip(); picked = str(q.get('picked') or '').strip(); imgs = [str(x) for x in (q.get('imgs') or [])]
        for x in imgs:
            if not re.fullmatch(r'图/[^/]+', x) or not (home(job) / x).is_file(): raise Oops('图不对：先截图、贴图再回复')
        if not (text or picked or imgs): raise Oops('回复是空的')
        def f(d):
            m = marker(d, int(q['id']))
            if m['round'] != d['round']: raise Oops('旧轮次的标记不能改；还不行就点“这条还不行”')
            if not (m.get('reply') or m.get('thread')): raise Oops('AI 还没回这条：直接改上面你写的话就行')
            if picked and picked not in marker_opts(m): raise Oops('这个选项已经不在了（AI 又回了话）：刷新看看')
            x = {'who': '用户', 'text': text or (f'选了：{picked}' if picked else '（见图）'), 'at': now()}
            if picked: x['picked'] = picked
            if imgs: x['imgs'] = imgs
            m.setdefault('thread', []).append(x); m['status'] = '待处理'
            return {'ok': True}
        return edit(job, f)

    def reply_img(q):
        """“回复 AI”带的图：截达芬奇当前画面（shot）或 ⌘V／拖进来的（data），先存进 图/，回复时带名字；不挂到标记自己的图上。"""
        mid = int(q['id']); d0 = load(job); m0 = marker(d0, mid)
        if m0['round'] != d0['round']: raise Oops('旧轮次的标记不能回复；还不行就点“这条还不行”')
        (home(job) / '图').mkdir(exist_ok=True)
        if q.get('shot'):
            s = call(dv.status); img = f"图/回复_第{m0['round']}轮_{int(s['t'] // 60)}分{s['t'] % 60:04.1f}秒_{mid}_{int(time.time() * 1000) % 100000}.jpg"
            call(dv.still, home(job) / img); return {'ok': True, 'name': img}
        import base64
        mt = re.match(r'data:image/(png|jpeg|gif|webp);base64,(.+)', q.get('data') or '', re.S)
        if not mt: raise Oops('只能放图片')
        img = f"图/回复_你贴的_{time.strftime('%m%d-%H%M%S')}_{os.urandom(2).hex()}.{'jpg' if mt[1] == 'jpeg' else mt[1]}"
        (home(job) / img).write_bytes(base64.b64decode(mt[2])); return {'ok': True, 'name': img}

    def delete(q):
        def f(d):
            m = marker(d, int(q['id']))
            if m['round'] != d['round']: raise Oops('旧轮次的标记不能删')
            d['markers'].remove(m); return {'ok': True}
        return edit(job, f)

    def again(q):
        def f(d):
            o = marker(d, int(q['id'])); n = max(m['id'] for m in d['markers']) + 1
            m = {**{k: o[k] for k in ('t', 'timeline', 'quote', 'frame')}, **{k: o[k] for k in ('t_end', 'span', 'imgs', 'kind') if k in o}, 'id': n, 'round': d['round'], 'text': '', 'status': '待处理', 'reply': '', 'of': o['id'], 'at': now()}
            d['markers'].append(m); return m
        return edit(job, f)

    def nav(q):
        base = {c['id']: c['label'] for c in chapters(job)}
        def f(d):
            for k, v in (q.get('labels') or {}).items():
                if k not in base: continue
                v = str(v).strip()
                if v and v != base[k]: d['nav_labels'][k] = v
                else: d['nav_labels'].pop(k, None)
            return {'ok': True, 'nav_labels': d['nav_labels']}
        return edit(job, f)

    def navshot(_):
        f = home(job) / 'tmp' / '导航底图.jpg'; call(dv.still, f); return f

    def from_page(h):
        """请求是本页面发的吗：有来源（own_page 已查过是本服务）、带着打开页面时发的 HttpOnly cookie（页面脚本也读不到，只有浏览器自己带）、浏览器标的是同源。"""
        o = h.headers.get('Origin'); site = h.headers.get('Sec-Fetch-Site')
        return o is not None and page_cookie(h) == TOKEN and (site is None or site == 'same-origin')

    def strict(fn):
        """用户的确认、改字：只收本页面发来的（带本页面的来源和页面里的口令）。命令行、curl 这类没有来源或口令的一律不收——AI 不能替用户点。"""
        def g(q, h):
            if not from_page(h): raise Refuse('确认、改字只能用户在审片台页面上点（这个请求不是页面发的）')
            try: return fn(q, h)
            except board.Oops as e: raise Oops(str(e))
        return g

    STRICT = {'/api/item/act': strict(lambda q, h: board.act(job, q, h.headers.get('User-Agent') or '')),
              '/api/script': strict(lambda q, h: board.set_script(job, str(q['field']), q['value'], str(q.get('column') or '文案'))),
              # 文案栏（写稿）：提意见、回复、采纳／不要、退回版本、贴一大段字——都只收页面的（AI 不能替用户采纳）
              '/api/draft/note': strict(lambda q, h: writing.note_add(job, q)), '/api/draft/text': strict(lambda q, h: writing.note_text(job, q)),
              '/api/draft/delete': strict(lambda q, h: writing.note_del(job, q)), '/api/draft/answer': strict(lambda q, h: writing.answer(job, q)),
              '/api/draft/decide': strict(lambda q, h: writing.decide(job, q)), '/api/draft/restore': strict(lambda q, h: writing.restore(job, q['v'])),
              '/api/draft/import': strict(lambda q, h: writing.import_text(job, q.get('text'))), '/api/draft/kickoff': strict(lambda q, h: writing.kickoff(job, q)),
              '/api/upload-copy': strict(lambda q, h: board.set_upload(job, q)),
              '/api/publish/set': strict(lambda q, h: publish.save(job, q)), '/api/publish/request': strict(lambda q, h: publish.request(job, q, h.headers.get('User-Agent') or '')),
              '/api/item/replyimg': strict(lambda q, h: board.reply_img(job, q))}

    def send_ai(q, h):
        r = board.send(job, str(q.get('column') or '')); return {**r, 'listening': listening()}

    STRICT['/api/send'] = strict(send_ai)
    STRICT['/api/settle'] = strict(lambda q, h: board.settle(job, str(q.get('column') or ''), bool(q.get('on', True)), h.headers.get('User-Agent') or ''))
    STRICT['/api/material/paths'] = strict(lambda q, h: materials.add_paths(job, q.get('text')))      # 备料：登记本机路径，程序在后台复制
    STRICT['/api/unsend'] = strict(lambda q, h: board.unsend(job, str(q.get('column') or '')))          # “AI 在处理”撤回：这一栏发出去的收回来，回到待发送（QA 1.5）
    STRICT['/api/send/cancel'] = STRICT['/api/unsend']                                                  # 同一个，页面用的名字
    STRICT['/api/publish/cancel'] = strict(lambda q, h: publish.cancel(job))                            # 发布栏：取消这次“存到蚁小二草稿”
    STRICT['/api/repair'] = strict(lambda q, h: repair(job, str(q.get('file') or '')))                  # 数据文件坏了：留着坏的、退回上一版（QA 2.4）
    POST = {'/api/mark': mark, '/api/markrange': mark_range, '/api/range': set_range, '/api/shot': shot, '/api/upload': upload, '/api/delimg': del_img, '/api/text': set_text, '/api/delete': delete, '/api/again': again, '/api/nav': nav, '/api/markkind': set_kind, '/api/answer': answer, '/api/replyimg': reply_img,
            '/api/goto': lambda q: call(dv.goto, float(q['t'])), '/api/play': lambda q: call(dv.toggle_play), '/api/navshot': navshot}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send(self, body, kind='application/json; charset=utf-8', code=200, cookie=False):
            if not isinstance(body, bytes): body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body)))
            if cookie: self.send_header('Set-Cookie', f'{COOKIE}_{self.server.server_address[1]}={TOKEN}; Path=/; HttpOnly; SameSite=Strict')
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def guarded(self, fn):
            """每个请求：记活动时间（ping、AI 的 --wait 不算：页面关了没人能点）；job.json 变了就重读（QA 1.4）；出了没料到的错回一句中文 500、记日志，不断开（QA 2.4/2.5）。"""
            p = self.path.split('?')[0]
            if p not in ('/api/ping', '/api/wait'): activity['t'] = time.time()
            try:
                if refresh(job): dv_reset()
                return fn()
            except Broken as e:
                return self.send({'ok': False, 'code': 'broken_file', 'file': e.file.name, 'which': next((k for k, v in DATA_FILES.items() if v(job) == e.file), ''),
                                  'can_restore': e.backup, 'error': str(e)}, code=500)
            except (BrokenPipeError, ConnectionResetError): return None
            except Exception as e:
                traceback.print_exc()
                with contextlib.suppress(Exception): return self.send({'ok': False, 'code': 'internal', 'error': f'审片台出错：{type(e).__name__}：{e}'}, code=500)

        def media(self, rel):
            """发布到审片台的图片、视频、音频（审片工作台/审片台/ 下面）；视频、音频支持分段读（Range），页面里能拖进度条。"""
            root = board.store(job).resolve(); f = (root / rel).resolve()
            if root not in f.parents or not f.is_file(): return self.send({'ok': False, 'error': '找不到这个文件'}, code=404)
            ext = f.suffix.lower(); kind = MEDIA.get(ext, 'application/octet-stream'); size = f.stat().st_size
            m = re.match(r'bytes=(\d*)-(\d*)$', self.headers.get('Range') or '')
            if m and not (m[1] or m[2]): m = None
            if not m: a, b, code = 0, size - 1, 200
            else:
                a = int(m[1]) if m[1] else max(0, size - int(m[2])); b = min(size - 1, int(m[2])) if m[1] and m[2] else size - 1; code = 206
                if a >= size or b < a:            # 反着写的范围（bytes=500-100）、超出文件的：416，不断开（QA 2.5）
                    self.send_response(416); self.send_header('Content-Range', f'bytes */{size}'); self.send_header('Content-Length', '0'); self.end_headers(); return
            self.send_response(code); self.send_header('Content-Type', kind)
            if code == 206: self.send_header('Content-Range', f'bytes {a}-{b}/{size}')
            self.send_header('Accept-Ranges', 'bytes'); self.send_header('Content-Length', str(b - a + 1)); self.send_header('Cache-Control', 'no-store'); self.end_headers()
            with open(f, 'rb') as x:                 # 分块写出，不把整个视频读进内存
                x.seek(a); left = b - a + 1
                while left > 0:
                    chunk = x.read(min(left, 1 << 20))
                    if not chunk: break
                    self.wfile.write(chunk); left -= len(chunk)

        def image(self, f):
            if not f.is_file(): return self.send({'ok': False, 'error': '找不到这张图'}, code=404)
            self.send(f.read_bytes(), 'image/jpeg')

        # —— 口播剪辑栏的文字剪辑器：页面和转给剪口播后台的请求 ——
        def koubo_ready(self):
            a = aroll_of(job)
            if koubo.ready(a): return a
            self.send({'ok': False, 'error': koubo.info(a)['why']}, code=404)

        def editor_page(self):
            a = self.koubo_ready()
            if not a: return
            page = EDITOR.read_text(encoding='utf-8').replace('__SHENPIAN_TOKEN__', SESSION)
            pi = backend(a).page_info()                      # 预览片段多长、原片多长（剪口播后台给；起不来就不放预览，页面上会显示后台的报错）
            if pi.get('segment_seconds'):
                page = page.replace('src="/source.mp4"', f'data-segment-seconds="{pi["segment_seconds"]}" data-source-duration="{pi["source_duration"]}"')
            self.send(page.encode(), 'text/html; charset=utf-8', cookie=True)

        def proxy(self, method, path, body=None):
            """原样转给剪口播后台，回话原样交回（状态码、409 的 reload、报错文字、预览片段的分段读）。"""
            a = self.koubo_ready()
            if not a: return
            rng = self.headers.get('Range')
            try: c, r = backend(a).open(method, path, body, {'Range': rng} if rng else None)
            except koubo.Fail as e: return self.send({'ok': False, 'error': str(e)}, code=503)
            with contextlib.closing(c):
                self.send_response(r.status)
                for k in koubo.PASS:
                    v = r.getheader(k)
                    if v: self.send_header(k, v)
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    while True:
                        chunk = r.read(262144)
                        if not chunk: break
                        self.wfile.write(chunk)

        def do_GET(self): return self.guarded(self._get)
        def do_POST(self): return self.guarded(self._post)

        def _get(self):
            from urllib.parse import unquote
            p = unquote(self.path.split('?')[0])
            # GET 也查 Host（2026-11-21）：必须是本机地址加本端口，防 DNS 重绑定（别的网站把自己的域名指到 127.0.0.1 来读标记和截图）。
            # 浏览器打开页面、页面拿数据和图片、程序自己 ping 的 Host 都是 127.0.0.1:端口 或 localhost:端口；没有 Host 头的照旧放行
            if not self.own_host(): return self.send({'ok': False, 'error': '只接受本机地址的请求（Host 不是本机的审片工作台）'}, code=403)
            if p in ('/', '/index.html'): return self.send(PAGE.read_bytes().replace(b'__SHENPIAN_TOKEN__', SESSION.encode()), 'text/html; charset=utf-8', cookie=True)
            if p == '/api/ping': return self.send({'app': '审片工作台', 'dir': str(job.dir), 'pid': os.getpid(), 'lite': lite(job), 'page_seen': None if seen['t'] is None else round(time.time() - seen['t'], 1),
                                                   'listening': listening(), 'prog': PROG, 'idle': round(time.time() - activity['t'], 1)})
            if p == '/api/material': return self.send(materials.info(job))       # 备料：正在传、复制的进度（页面传东西时每秒来问）
            if p == '/api/data':
                seen['t'] = time.time(); return self.send(meta())
            if p == '/api/wait':                # AI 的 --wait 每 2 秒来问一次：记下“AI 在等”
                ears['t'] = time.time(); return self.send({'ok': True, 'send': board.load(job).get('send')})
            if p == '/api/draft/diff':          # 文案栏：某一版和前一版比改了哪里
                try: return self.send(writing.version_diff(job, int(self.path.split('v=')[-1])))
                except (board.Oops, ValueError) as e: return self.send({'ok': False, 'error': str(e) if isinstance(e, board.Oops) else '版本号不对'})
            if p == '/api/animclip':            # 动画栏“带口播播放”：这一项在成片里的那一段（画面＋口播声音）和这一段每个字的时间（2026-11-24 用户定）
                from urllib.parse import parse_qs, urlsplit
                from . import animclip
                iid = (parse_qs(urlsplit(self.path).query).get('id') or [''])[0]
                try: return self.send(animclip.clip(job, iid))
                except (animclip.Oops, board.Oops) as e: return self.send({'ok': False, 'error': str(e)})
                except Exception as e: return self.send({'ok': False, 'error': f'切带口播的小样出错：{e}'})
            if p.startswith('/审片台/'): return self.media(p[len('/审片台/'):])
            if p == '/文案剪辑.html': return self.editor_page()
            if koubo.GET.match(p): return self.proxy('GET', self.path)
            if p == '/api/dv':
                if lite(job): return self.send({'ok': False, 'lite': True, 'error': '口播阶段：还没建项目文件夹，审核线还没到'})
                try: return self.send({'ok': True, **call(dv.status)})
                except Oops as e: return self.send({'ok': False, 'error': str(e)})
            if p.startswith('/图/') and '/' not in p[3:] and not p[3:].startswith('.'): return self.image(home(job) / '图' / p[3:])
            self.send({'ok': False, 'error': '没有这个地址'}, code=404)

        def material_upload(self):
            """备料：页面拖进来的一个文件（请求体就是文件本身，?name=文件名）。和确认一样只收本页面带口令的；边收边写，不整个读进内存。"""
            from urllib.parse import parse_qs, urlsplit
            if not (self.own_page() and from_page(self)):
                self.close_connection = True
                return self.send({'ok': False, 'error': '放素材只能用户在审片台页面上拖（这个请求不是页面发的）'}, code=403)
            name = (parse_qs(urlsplit(self.path).query).get('name') or [''])[0]
            try: n = int(self.headers.get('Content-Length') or -1)
            except ValueError: n = -1
            try: rec = materials.receive(job, name, self.rfile, n)
            except materials.Oops as e:
                self.close_connection = True; return self.send({'ok': False, 'error': str(e)})
            self.send({'ok': True, 'file': rec})

        def own_page(self):
            """只接受本页面自己发来的请求（2026-11-21 体检：以前本机任何网页都能发请求让达芬奇跳转、删标记）。
            带 Origin 头的（浏览器）必须是本服务的地址；Host 头必须是本机地址加本端口（防 DNS 重绑定）。
            没有 Origin 头的（curl、测试、程序自己调）照旧放行。"""
            origin = self.headers.get('Origin')
            return self.own_host() and (origin is None or origin.strip().lower() in {'http://' + x for x in self.mine()})

        def mine(self): port = self.server.server_address[1]; return {f'127.0.0.1:{port}', f'localhost:{port}'}

        def own_host(self):
            """Host 头是本机地址加本端口（GET、POST 都查）；没有 Host 头的放行。"""
            host = self.headers.get('Host'); return host is None or host.strip().lower() in self.mine()

        def _post(self):
            if self.path.split('?')[0] == '/api/material/upload': return self.material_upload()
            try: raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            except ValueError: return self.send({'ok': False, 'error': '格式不对'}, code=400)
            if not self.own_page(): return self.send({'ok': False, 'error': '只接受审片工作台页面自己发来的请求（来源不是本页面）'}, code=403)
            if self.path.split('?')[0] in koubo.POST:        # 文字剪辑器的改动：和确认、改字一样只收本页面带口令的
                if not from_page(self): return self.send({'ok': False, 'error': '改口播文字只能用户在审片台页面上操作（这个请求不是页面发的）'}, code=403)
                return self.proxy('POST', self.path.split('?')[0], raw)
            try: q = json.loads(raw or b'{}')
            except ValueError: return self.send({'ok': False, 'error': '格式不对'}, code=400)
            if not isinstance(q, dict): return self.send({'ok': False, 'error': '格式不对'}, code=400)
            path = self.path.split('?')[0]; fn = POST.get(path)
            if path in STRICT: fn = lambda q, f=STRICT[path]: f(q, self)
            if not fn: return self.send({'ok': False, 'error': '没有这个地址'}, code=404)
            try: out = fn(q)
            except Refuse as e: return self.send({'ok': False, 'code': 'stale_page' if page_cookie(self) is not None and page_cookie(self) != TOKEN else 'not_page', 'error': str(e)}, code=403)
            except (Oops, board.Oops) as e: return self.send({'ok': False, 'error': str(e)})
            except (KeyError, ValueError, TypeError): return self.send({'ok': False, 'error': '请求缺内容'}, code=400)
            if isinstance(out, Path): return self.image(out)
            self.send(out if 'ok' in out else {'ok': True, **out})
    return H


PLAYING = '达芬奇正在播放：播放时达芬奇不回话，读不到位置、不能跳转截图；按空格（或在达芬奇里）暂停后就好'


def _dv_worker(job_dir, conn):
    """子进程：真正和达芬奇打交道的都在这里做（DV 类）。"""
    dv = DV(Job(job_dir))
    while True:
        try: name, args = conn.recv()
        except EOFError: return
        try: conn.send(('ok', getattr(dv, name)(*args)))
        except Oops as e: conn.send(('err', str(e)))
        except SystemExit as e: conn.send(('err', str(e).replace('停止：', '')))
        except Exception as e: conn.send(('err', f'达芬奇出错：{e}'))


class DVProxy:
    """服务用的达芬奇：每次调用交给子进程、限时等。2026-11-21 261118 实测：达芬奇播放时脚本接口完全不回话（读播放头一直卡到播完），
    而且卡住时整个 Python 都停住（服务连网页都不回），用户按的“暂停”空格排在后面发不出去——“不是每次都成功”。
    现在：子进程卡住就当“正在播放”，页面照常回话；这时按空格不再问达芬奇，直接把空格发过去（暂停），达芬奇一停子进程就回来。"""
    LIMIT = {'status': 1.5, 'goto': 4, 'still': 15, 'toggle_play': 8}

    def __init__(self, job):
        self.job, self.p, self.conn, self.busy = job, None, None, False

    def _start(self):
        import multiprocessing as mp
        ctx = mp.get_context('spawn'); a, b = ctx.Pipe()
        self.p = ctx.Process(target=_dv_worker, args=(str(self.job.dir), b), daemon=True); self.p.start(); self.conn, self.busy = a, False

    def _call(self, name, *args):
        if self.p is None or not self.p.is_alive(): self._start()
        if self.busy:                                           # 上一次还卡着：回来了就收掉，没回来就还在播
            if not self.conn.poll(0): raise Oops(PLAYING)
            self.conn.recv(); self.busy = False
        self.conn.send((name, args))
        if not self.conn.poll(self.LIMIT[name]): self.busy = True; raise Oops(PLAYING)
        kind, val = self.conn.recv()
        if kind == 'err': raise Oops(val)
        return val

    def reset(self):
        p, self.p = self.p, None
        if p is not None and p.is_alive(): p.terminate()

    def status(self): return self._call('status')
    def goto(self, t): return self._call('goto', t)
    def still(self, out): return self._call('still', out)

    def toggle_play(self):
        if self.busy and self.conn.poll(0): self.conn.recv(); self.busy = False
        if self.busy:
            with dv_lock(self.job, '审片工作台：暂停'):     # 正在播放（达芬奇不回话）：不问达芬奇，直接发空格暂停
                KEYS.space()
            return {'ok': True}
        return self._call('toggle_play')


class NoDV:
    """口播阶段（Lite）：还没有审核线，不连达芬奇。"""
    def __getattr__(self, name):
        def no(*_): raise Oops('口播阶段：还没建项目文件夹（init），审核线、达芬奇这些还没到')
        return no


def make_server(job, port, dv=None):
    return ThreadingHTTPServer(('127.0.0.1', port), make_handler(job, dv or (NoDV() if lite(job) else DVProxy(job))))


def serve(d, port):
    """后台服务进程的入口（workbench 命令起的）。"""
    job = open_film(d); srv = make_server(job, port); info = home(job) / '服务.json'
    wj(info, {'pid': os.getpid(), 'port': port, 'url': f'http://127.0.0.1:{port}/', 'dir': str(job.dir), 'started': now()})
    if koubo.ready(aroll_of(job)): koubo.write_pointer(aroll_of(job), job.dir, f'http://127.0.0.1:{port}/')   # 剪口播后台直接打开时据此指到审片台
    def bye(*_):
        with contextlib.suppress(Exception):
            if rj(info).get('pid') == os.getpid(): info.unlink()
        os._exit(0)
    signal.signal(signal.SIGTERM, bye); signal.signal(signal.SIGINT, bye)
    def idle():                            # 闲太久（页面关了、没人来用）自己退出，端口还回去（QA 1.2）；下次首页点开会重起，数据都在片子文件里
        while True:
            time.sleep(max(1.0, min(30.0, IDLE / 4)))
            if time.time() - activity['t'] > IDLE: say(f'审片台闲了 {int(IDLE)} 秒，自己退出'); bye()
    if IDLE > 0: threading.Thread(target=idle, daemon=True).start()
    say(f'审片台 http://127.0.0.1:{port}/  {job.dir}'); srv.serve_forever()


# ---------- 命令行：起服务 / 停服务 / 换轮次 / 列标记 / 回写 ----------
def ping(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=timeout) as r: return json.loads(r.read())
    except Exception: return None


def port_free(port):
    """和服务本身一样带 SO_REUSEADDR 试绑：刚 --stop 的端口还有一堆 TIME_WAIT 连接，服务能绑上，不能当成被占（2026-11-21 实测）。"""
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s.bind(('127.0.0.1', port)); return True
        except OSError: return False


def running(job):
    """在跑的本片服务的 服务.json；没在跑返回 None（顺手清掉过期的 服务.json）。"""
    f = home(job) / '服务.json'
    if not f.exists(): return None
    info = rj(f); pg = ping(info.get('port'))
    if pg and pg.get('app') == '审片工作台' and pg.get('dir') == str(job.dir): return info
    f.unlink(); return None


def pick_port(job, want=None, ports=None):
    ports = ports or PORTS
    if want:
        if port_free(want): return want
        pg = ping(want)
        die(f"端口 {want} 被占了：" + (f"那是另一个项目的审片工作台（{pg['dir']}）" if pg and pg.get('app') == '审片工作台' else '是别的程序在用') + '；换一个 --port，或不写 --port 自动找空的')
    for p in ports:
        if port_free(p): return p
    p = reclaim(ports)
    if p: return p
    die(f'{ports[0]}–{ports[-1]} 端口都被占了，用 --port 指定一个空的')


RECLAIM_MIN = 120          # 端口满了停最久没用的审片台：至少闲了这么久（秒）的才停，正在用的不停


def reclaim(ports, prog=None):
    """端口都满了：停掉这份程序起的、闲得最久的那个审片台（别的程序文件夹起的不碰），返回空出来的端口；没有能停的给 None。"""
    prog = prog or PROG; best = None
    for p in ports:
        pg = ping(p, 0.3)
        if not pg or pg.get('app') != '审片工作台' or (pg.get('prog') or {}).get('dir') != prog['dir']: continue
        idle = pg.get('idle')
        if idle is None or idle < RECLAIM_MIN: continue
        if not best or idle > best[1]: best = (p, idle, pg)
    if not best: return None
    p, idle, pg = best
    with contextlib.suppress(ProcessLookupError, TypeError): os.kill(pg['pid'], signal.SIGTERM)
    for _ in range(50):
        if port_free(p): say(f"端口都满了：停掉了闲了 {int(idle // 60)} 分钟的审片台（{pg['dir']}），用它的端口 {p}"); return p
        time.sleep(0.1)
    return None


def same_program(port):
    """这个端口上的审片台是不是和现在这份程序同一版（同一个程序文件夹、同样的代码和页面）。发版前的老服务 ping 里没有 prog，算不同。"""
    return (ping(port) or {}).get('prog') == PROG


def start(job, port=None, ports=None, fresh=False):
    """fresh：首页用。已在跑的是别的版本程序起的（发版前的老服务、或从已删掉的 worktree 起的）就停掉、用现在的程序重起；数据都在片子文件里，不丢。"""
    info = running(job)
    if info and fresh and not same_program(info['port']):
        other = (ping(info['port']) or {}).get('prog') or {}
        if other.get('dir') and other['dir'] != PROG['dir'] and Path(other['dir']).is_dir():
            # 另一个程序文件夹（正式版／开发版）正开着这条片子：不去停它（QA 1.1：以前两边来回互相停，页面上没存的字会丢）
            die(f"该项目正在另一个 ThinkCut 中打开（{other['dir']}，端口 {info['port']}）：请先在那边关闭，或等其闲置自动退出后再打开")
        say(f"这个项目的审片台（端口 {info['port']}）是别的版本的程序起的，换成现在的程序重起（标记、确认、文案都在项目文件里，不丢）"); stop(job); info = None
    if info and (ping(info['port']) or {}).get('lite', False) != lite(job):     # 口播阶段起的服务，init 建了 job.json 以后换成完整的（数据不动）
        say('这个项目已经建好项目文件夹（job.json），审片台换成完整的（其余栏目也能用了）'); stop(job); info = None
    if info:
        if port and port != info['port']: say(f"提醒：本片的审片台已经在 {info['port']} 端口跑着，直接用它（要换端口先 --stop）")
        return info['url'], True
    port = pick_port(job, port, ports); log = home(job) / '服务.log'; home(job).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    with open(log, 'a') as lf:
        subprocess.Popen([sys.executable, '-B', '-m', 'thinkcut.workbench', 'serve', str(job.dir), str(port)], cwd=str(Path(__file__).resolve().parents[1]),
                         stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    for _ in range(100):
        pg = ping(port, 0.3)
        if pg and pg.get('dir') == str(job.dir): return f'http://127.0.0.1:{port}/', False
        time.sleep(0.1)
    die(f'审片台没起来，看日志 {log}：\n' + log.read_text(encoding='utf-8', errors='replace')[-600:])


def stop(job):
    info = running(job)
    if not info: say('本片的审片台没在跑'); return False
    with contextlib.suppress(ProcessLookupError): os.kill(info['pid'], signal.SIGTERM)
    for _ in range(50):
        if not ping(info['port'], 0.2): break
        time.sleep(0.1)
    with contextlib.suppress(FileNotFoundError): (home(job) / '服务.json').unlink()
    say(f"审片台已停（端口 {info['port']}）"); return True


def set_round(job, n):
    if n < 1: die('--round 要从 1 起')
    def f(d):
        old, left = d['round'], [m['id'] for m in d['markers'] if m['round'] == d['round'] and m['status'] == '待处理']
        d['round'] = n; return old, left
    old, left = edit(job, f)
    if n != old: say(f'审片台的审核线换到第 {n} 轮（原来第 {old} 轮）' + (f"；第 {old} 轮还有 {len(left)} 条没回写：{'、'.join(map(str, left))}" if left and n > old else ''))


LOCATE_ONLY = ('【选中的字只是定位】按用户的意思判断要改多大范围（只这一处／这一整句／连同上下句／全片同类的地方／和前后某处呼应着一起改），'
               '不要点哪改哪，也不要借题改用户没说的；回写时 --note 先写“我理解你的意思是……，所以改了这几处：……”，--where 写改了哪几处；'
               '拿不准（两种以上合理理解，或要动用户没提的大段内容）就不改，用 --status 问你 回问，等用户回答')


def context_at(job, t, lines=None, chs=None, labels=None):
    """标记所在的整句、前一句、后一句、章节名（给 AI 判断范围用）；取不到的项不给。"""
    out = {}
    try:
        lines = lines if lines is not None else script(job)[1]
        k = max([i for i, l in enumerate(lines) if l['t'] <= t + 0.05] or [0]) if lines else None
        if k is not None:
            out['整句'] = lines[k]['text']
            if k > 0: out['前一句'] = lines[k - 1]['text']
            if k + 1 < len(lines): out['后一句'] = lines[k + 1]['text']
    except Exception: pass
    try:
        chs = chs if chs is not None else chapters(job)
        c = next((c for c in chs if c['a'] <= t < c['b']), chs[-1] if chs and t >= chs[-1]['a'] else None)
        if c: out['章节'] = (labels or {}).get(c['id']) or c['label']
    except Exception: pass
    return out


def mmss(t): return f"{int(t // 60)}:{t % 60:05.2f}"


def listing(job, all_rounds=False, only=None):
    """only：只列这些（用户这次发来的标记 #编号、导航条）。每条后面带上下文（整句、前后句、章节）、问答、AI 改了哪几处。"""
    d = load(job); ms = [m for m in d['markers'] if (all_rounds or m['round'] == d['round']) and (only is None or f"#{m['id']}" in only)]
    say(f"第 {d['round']} 轮，{len(ms)} 条标记（图在 {home(job)}/）")
    if ms: say(LOCATE_ONLY)
    try: lines = script(job)[1]
    except Exception: lines = []
    try: chs = chapters(job)
    except Exception: chs = []
    for m in sorted(ms, key=lambda m: m['t']):
        mm = f"{int(m['t'] // 60)}:{m['t'] % 60:05.2f}" + (f"–{int(m['t_end'] // 60)}:{m['t_end'] % 60:05.2f}" if m.get('t_end') else '')
        imgs = [x for x in [m.get('frame')] + (m.get('imgs') or []) if x]
        say(f"#{m['id']} 第{m['round']}轮 {mm} [{m['status']}] {m['text'] or '（没写字）'}" + (f"｜类型：{m['kind']}" if m.get('kind') else '') + (f"｜接着第 {m['of']} 条" if m['of'] else '')
            + (f"｜{'选的字' if m.get('span') else '正在说'}：{m['quote']}" if m['quote'] else '') + (f"｜图 {'、'.join(imgs)}" if imgs else '') + (f"｜AI：{m['reply']}" if m['reply'] else ''))
        cx = context_at(job, m['t'], lines, chs, d['nav_labels'])
        if cx: say('   上下文：' + '｜'.join(f'{k}：{cx[k]}' for k in ('章节', '前一句', '整句', '后一句') if k in cx))
        for x in m.get('thread') or []:
            say(f"   问答 {x['at'][5:16]} {'AI 问' if x['who'] == 'AI' and x.get('status') == '问你' else x['who']}：{x['text']}"
                + (f"｜给的选项：{' | '.join(x['options'])}" if x.get('options') else '') + (f"｜选了：{x['picked']}" if x.get('picked') else '')
                + (f"｜图 {'、'.join(str(home(job) / i) for i in x['imgs'])}" if x.get('imgs') else ''))
        if m.get('touched'): say('   AI 改了这几处：' + '、'.join(f"{mmss(x['a'])}–{mmss(x['b'])}" for x in m['touched']))
    if d['nav_labels'] and (only is None or '导航条' in only): say('用户改的导航条文字：' + json.dumps(d['nav_labels'], ensure_ascii=False)
        + (f"｜AI：{d['nav_handled']['note']}" if (d.get('nav_handled') or {}).get('key') == board._h(d['nav_labels']) else '｜处理完回写：--reply 导航条 --note "…"'))


def parse_where(x):
    """--where "0:12-0:15,1:03-1:08" → [{'a': 12.0, 'b': 15.0}, …]。秒或 分:秒（可带小数）；单个时间点 a＝b。写错就停下说清楚，不静默丢。"""
    def sec(v):
        v = v.strip()
        m = re.fullmatch(r'(?:(\d+):)?(\d+(?:\.\d+)?)', v)
        if not m or (m[1] is not None and float(m[2]) >= 60): raise ValueError(v)
        return round(int(m[1] or 0) * 60 + float(m[2]), 3)
    out = []
    for part in [p for p in str(x).replace('，', ',').split(',') if p.strip()]:
        ab = re.split(r'\s*[-–~]\s*', part.strip())
        try:
            if len(ab) == 1: a = b = sec(ab[0])
            elif len(ab) == 2: a, b = sec(ab[0]), sec(ab[1])
            else: raise ValueError(part)
        except ValueError: die(f'--where 写法不对：“{part.strip()}”。写成逗号分隔的时间段，秒或 分:秒，如 "0:12-0:15,1:03-1:08" 或 "12-15.5"')
        if b < a: die(f'--where 时间段“{part.strip()}”结束早于开始')
        out.append({'a': a, 'b': b})
    if not out: die('--where 是空的：写成 "0:12-0:15,1:03-1:08"')
    return out


def marker_opts(m):
    """最后说话的是 AI 时，给页面画的选项按钮：--options 给的优先，其次从 AI 的话里拆（A：…；B：…）。"""
    th = m.get('thread') or []
    if th:
        if th[-1]['who'] != 'AI': return []
        return th[-1].get('options') or board.auto_options(th[-1]['text'])
    return board.auto_options(m.get('reply')) if m.get('reply') and m.get('status') in ('已改', '没改', '问你') else []


def reply(job, mid, status, note, where=None, options=None):
    """AI 回写审核线标记。问你＝拿不准范围回问用户（这条变“待你看”，用户在卡片上回答后回到待发送）；已改／没改可带 --where 写实际改了哪几处（touched）。
    问答按时间顺序记在 thread（[{who: AI/用户, text, at}]）；reply 照旧是 AI 最近一次的话。"""
    if status not in STATUS[1:]: die('--status 只能是 已改、没改 或 问你')
    if not note: die('回写要带 --note 说明（改了什么／为什么没改／问用户什么）')
    if where and status == '问你': die('--where 是写改了哪几处的：回问（问你）时还没改，不要带')
    touched = parse_where(where) if where else None
    try: opts = board.parse_options(options) if options else None
    except board.Oops as e: die(str(e))
    def f(d):
        m = marker(d, mid); at = now(); m['status'] = status; m['reply'] = note; m['replied_at'] = at
        if status == '问你' or opts or m.get('thread'): m.setdefault('thread', []).append({'who': 'AI', 'text': note, 'at': at, 'status': status, **({'options': opts} if opts else {})})
        if touched is not None: m['touched'] = touched
    try: edit(job, f)
    except Oops as e: die(str(e))
    say(f'第 {mid} 条已回写：{status}' + (f'（改了 {len(touched)} 处）' if touched else '') + ('；页面上这条变成“待审阅”，等用户在卡片上回答（回答后用户点“提交 AI”，--wait 会打印回答）' if status == '问你' else ''))


def wait(job, timeout=0):
    """AI 用：挂着等用户在页面上点“发给 AI”。点了就打印清单、退出（Claude Code 里放后台跑，退出会叫醒 AI）。timeout 秒（0 = 一直等）到了没点也退出。"""
    info = running(job) or die(f'这个项目的审片台没在跑：先 workbench {job.dir}')
    t0 = time.time(); miss = 0
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/api/wait", timeout=3) as r: r.read()
            miss = 0
        except Exception:
            miss += 1
            if miss >= 5: die('审片台服务停了（连着 5 次没回话）：重新 workbench 起服务再 --wait')
        got = board.pick(job)
        if got: return report(job, got)
        if timeout and time.time() - t0 > timeout:
            say(f'等了 {int(timeout)} 秒，用户还没点“提交 AI”。要接着等就再跑一次 --wait'); return
        time.sleep(2)


def report(job, got, again='再起一次 --wait 接着等'):
    """用户点了“交给 AI”：打印这次发来的那几件（--wait 和首页的“等所有片子” home --wait 都用它）。"""
    only = {i for _, i in got}; names = [board.LABEL.get(c, c) for c in board.COLUMNS if any(c == x for x, _ in got)]
    say(f"【用户点了“提交 AI”】栏目：{'、'.join(names)}，{len(got)} 件。只处理下面这些（别的栏目用户还没发，可能还在写，先别动）；逐项处理、逐项 --reply，处理完{again}：")
    mine = {i for i in only if i in ('定稿', '开工') or re.fullmatch(r'稿\d+', i)}      # 文案栏（写稿）的意见、定稿
    pub = {i for i in only if i == '发布'}                                             # 发布栏：存到蚁小二草稿
    if any(i.startswith('#') or i == '导航条' for i in only): listing(job, only=only)
    if any(not i.startswith('#') and i != '导航条' for i in only - mine - pub): board.listing(job, only=only - mine - pub)
    if mine: writing.listing(job, only=mine)
    if pub: publish.listing(job, only=pub)


HOME_PORT = int(os.environ.get('THINKCUT_HOME_PORT') or 8760)     # 首页（thinkcut_edit.py home）的固定端口
_home = {'t': 0.0, 'url': None}


def home_url():
    """首页在跑就给它的网址（审片台顶栏“← 全部片子”单独打开时用）；10 秒问一次。"""
    if time.time() - _home['t'] > 10:
        pg = ping(HOME_PORT, 0.3); _home.update(t=time.time(), url=f'http://127.0.0.1:{HOME_PORT}/' if pg and pg.get('app') == '首页' else None)
    return _home['url']


def wait_cmds(job):
    """页面上“AI 没在等”点开时给的两条命令：只等这条片子／等所有片子。"""
    s = Path(__file__).resolve().parents[1] / 'thinkcut_edit.py'
    return {'one': f'python3 "{s}" workbench "{job.dir}" --wait', 'all': f'python3 "{s}" home --wait'}


def reply_nav(job, note):
    """AI 回复“用户改的导航条文字”：照改了，或说明没照改的原因。回复后这件事了结（用户再改又是新的一件）。"""
    if not note: die('回写要带 --note 说明（照改了／哪条没照改、为什么）')
    def f(d):
        if not d['nav_labels']: raise Oops('用户没有改过导航条文字')
        d['nav_handled'] = {'key': board._h(d['nav_labels']), 'note': note, 'at': now()}
    try: edit(job, f)
    except Oops as e: die(str(e))
    say('导航条文字已回写')


def cli(job, a):
    """命令行只能：起/停服务、换轮次、读结果、发布一项、回复。没有替用户点“可以”“选这个”的办法（用户的确认只来自页面）。"""
    if a.stop: return stop(job)
    adopt(job)
    if a.publish: return publish_cli(job, a)
    if getattr(a, 'withdraw', None): return board.withdraw(job, a.withdraw.strip(), a.note)
    if getattr(a, 'rewrite', None): return writing.rewrite_cli(job, a.rewrite.strip(), getattr(a, 'json', None), a.note)   # 文案栏：交改写建议（用户点“采纳”才写进文件）
    if a.reply is not None:
        r = str(a.reply).strip()
        if r == '开工': return writing.reply_kick(job, a.note)                           # 文案栏：用户点了“开工”，AI 回一句
        if r == '定稿': return writing.reply_final(job, a.note)                          # 文案栏：用户点了“定稿”，AI 回一句接下来做什么
        if r == '发布': return publish.reply(job, a.note)                               # 发布栏：存到蚁小二草稿，AI 回一句存进了哪些平台
        if re.fullmatch(r'稿\d+', r): return writing.reply(job, r, a.note, a.status, getattr(a, 'options', None))   # 文案栏的意见：回问／说明
        if getattr(a, 'where', None) and not r.isdigit(): die('--where 只给审核线的标记用（--reply 数字 --status 已改/没改 --where …）')
        if r.isdigit(): return reply(job, int(r), a.status, a.note, getattr(a, 'where', None), getattr(a, 'options', None))   # 审核线的标记（编号是数字）
        if r == '导航条': return reply_nav(job, a.note)                               # 用户改的导航条文字
        if r.startswith('改'): return board.reply_edit(job, r, a.note)                # 用户改字的记录
        return board.reply(job, r, a.note, _split(a.files), _split(a.labels), getattr(a, 'options', None))        # 其余栏目的项
    if a.round is not None: set_round(job, a.round)
    if getattr(a, 'wait', False): return wait(job, float(getattr(a, 'wait_timeout', 0) or 0))
    if a.list:
        board.pick(job)                     # AI 读过了：页面上“已发给 AI”变成“AI 在处理”
        listing(job, a.all); say(''); board.listing(job, a.all); writing.listing(job, all_notes=a.all); publish.listing(job)
        un = board.send_info(job)['unsent_ids']
        if un: say(f"【注意】这几件用户还没点“提交 AI”（可能还在写），先别动：{'、'.join(un)}")
        return
    launch(job, a.port, a.column)


def _split(x): return [v.strip() for v in x.split(',') if v.strip()] if x else None          # 只按英文逗号分（路径、名字里常有中文逗号）


def publish_cli(job, a):
    """发布一项到审片台（AI 用）。"""
    anim = None
    if a.anim:
        if not a.project or not a.shots: die('--anim 要和 --project（动画工程文件夹）、--shots（镜头）一起写')
        anim = {'kind': a.anim, 'project': a.project, 'shots': _split(a.shots)}
    try:
        it = board.publish(job, a.publish, a.title, (a.look or '').replace('\\n', '\n'), a.kind or '可以', _split(a.files) or [], _split(a.labels), key=a.key, iid=a.id, note=a.note or '', anim=anim, checks=a.check,
                           assume=(getattr(a, 'assume', None) or '').replace('\\n', '\n'), draft=bool(getattr(a, 'draft', False)), placeholder=bool(getattr(a, 'placeholder', False)))
    except board.Oops as e: die(str(e))
    v = it['versions'][-1]; info = running(job)
    say(f"已发布到审片台“{it['column']}”栏：{it['id']} {it['title']}（第 {v['v']} 版，{len(v['files'])} 个文件）"
        + ('；页面 3 秒内自动显示' if info else f'；审片台没开着：跑 workbench {job.dir} --column {it["column"]} 打开给用户看')
        + f"\n用户在页面上点{'“选这个”' if v['kind'] == '多选一' else '“可以”'}就是确认" + ('（动画的确认自动记进 合成/确认记录.json，不用再 anim-confirm）' if anim else '')
        + f"；读结果：workbench {job.dir} --list")


def open_page(url):
    """在用户的浏览器里打开页面（用户 2026-11-21：“审片工作台打开……每次都自动打开”）。测试时 THINKCUT_NO_OPEN=1 不开。"""
    if os.environ.get('THINKCUT_NO_OPEN') or sys.platform != 'darwin': return False
    with contextlib.suppress(Exception): return subprocess.run(['open', url], timeout=10).returncode == 0
    return False


def launch(job, port=None, column=None):
    """起服务（已在跑就复用）＋在浏览器打开页面（column 给了就打开到那一栏）＋打印给 AI 的话。workbench 命令和 review 建好审核线后都走这里。"""
    from urllib.parse import quote
    if column and column not in board.COLUMNS: die(f'--column 只能是：{"、".join(board.COLUMNS)}')
    if not data_file(job).exists(): edit(job, lambda d: None)
    url, reused = start(job, port); d = load(job); page = url + ('#' + quote(column) if column else ''); opened = open_page(page)
    if lite(job):
        kb = koubo.info(aroll_of(job))
        say(f"审片台{'已经在跑（复用）' if reused else '已启动'}（还没建项目文件夹，只有文稿栏" + ('和口播精剪栏' if kb['ready'] else '') + f"能用）：{page}"
            + ('，已在浏览器打开' if opened else '；没能自动打开浏览器：AI 去打开这个网址（或发给用户）') + '\n'
            + "文稿栏：用户在页面里写标题、封面文字、口播正文，选一段字或针对全文提意见，点“提交 AI”；AI 用 --rewrite 稿N --json 改写.json 交改写建议，用户逐处点“采纳”才写进文案文件；写好点“定稿”。\n"
            + (f"口播精剪栏：剪口播的文字剪辑器（{kb['dir']}）：删句、恢复、拖小句调顺序、改字幕、分段，点“保存到达芬奇”更新主线和字幕。\n" if kb['ready'] else '')
            + f"【等用户】现在就挂着等 workbench {job.dir} --wait（用户点“提交 AI”或“定稿”它就打印要做的事并退出；Codex 加 --wait-timeout 1500）。\n"
            + f"项目文件夹 {job.dir}：init 建 job.json 后其余栏目才有，审片台的数据接着用。停服务：workbench {job.dir} --stop")
        return page
    say(f"审片台{'已经在跑（复用）' if reused else '已启动'}：{page}  第 {d['round']} 轮"
        + ('，已在浏览器打开' + (f'“{column}”栏' if column else '') if opened else '；没能自动打开浏览器：AI 去打开这个网址（或发给用户）') + '\n'
        f"审核线：用户在达芬奇里看审核线（停在“剪辑”页）；页面上有整条口播全文：点一句达芬奇跳过去，句子上能直接记标记；也能点“这一帧有问题”。\n"
        f"其余栏目：AI 用 --publish 发布要用户看的（图、视频、试听、多选一），用户点“可以”“选这个”就是确认。\n"
        f"【等用户】用户写完、点完会点页面顶上的“提交 AI”：现在就挂着等 workbench {job.dir} --wait（Claude Code 放后台跑，它一退出就是用户发来了，按打印的清单改；Codex 前台跑加 --wait-timeout 1500）。\n"
        f"读结果：workbench {job.dir} --list；回写：--reply 编号 --note 说明（审核线标记再加 --status 已改/没改）；停服务：--stop")
    return page


if __name__ == '__main__' and len(sys.argv) == 4 and sys.argv[1] == 'serve':
    serve(sys.argv[2], int(sys.argv[3]))
