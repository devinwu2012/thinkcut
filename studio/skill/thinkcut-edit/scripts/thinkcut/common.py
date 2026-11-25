# 公用：片子目录（job.json）、帧与秒换算、ffmpeg/ffprobe、读写 json
import contextlib, hashlib, json, os, shutil, subprocess, sys, tempfile, threading, time
from fractions import Fraction
from pathlib import Path
from . import formats

SKILL = Path(__file__).resolve().parents[2]          # skill/thinkcut-edit
ASSETS = SKILL / 'assets'
# 程序目录（本仓库的上一级）：按这个文件自己的位置推，NAS 换了挂载名 也能用（2026-11-21 体检）。
# 这份代码不在 <程序目录>/studio/skill/thinkcut-edit 这个位置（被拷到别处）时，按这台 Mac 的技能链接 ~/.claude/skills/thinkcut-edit 找（2026-11-24 起不再写死盘符路径）。
def _program_dir(skill=SKILL, link=Path.home() / '.claude' / 'skills' / 'thinkcut-edit'):
    if skill.parents[1].name in ('studio', '口播自动剪辑') and skill.parents[0].name == 'skill': return skill.parents[2]
    return link.resolve().parents[2]


THINKCUT_DIR = _program_dir()
sys.path.append(str(THINKCUT_DIR / 'bin')); import thinkcut_config      # noqa: E402  配置档：公共层＋每个 IP 一层（2026-11-24）


def ip_config(where=None):
    """某条片子（片子目录在 项目/02_制作/自动剪辑 下，往上找 项目.json 的 ip）所属 IP 的配置；不给就是默认 IP。"""
    return thinkcut_config.load(project=where)


_IP = ip_config()
DEFAULTS = {
    'fps': '30000/1001',
    'format': _IP.get('剪辑.画幅'),                  # 默认 IP 的画幅；init 时按片子所属 IP 写进 job.json
    'voice_db': 'auto',                              # 人声增益：auto=口播峰值调到 voice_peak_db；写数字则固定
    'voice_peak_db': -1.0,
    'sfx_peak_db': -5.0,                             # 音效规范：选段峰值约 -5 dBFS
    'sfx_under_voice_lu': 6.0,                       # 持续声最响处比人声平均响度低这么多
    'sfx_library': str(THINKCUT_DIR / '素材库' / '音效素材'),
    'sfx_favorites': str(THINKCUT_DIR / 'core' / 'engine' / 'config' / 'sfx_favorites.json'),
    'subtitle_font': _IP.get('剪辑.字幕字体'),       # 冬青黑体（配置档 剪辑.字幕字体）
    'subtitle_keywords': [],
    'pip_min_seconds': 6.0,
}


def say(*a):
    print(*a, flush=True)


def die(msg):
    raise SystemExit('停止：' + msg)


def rj(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def wj(p, obj, indent=1):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f'.{p.name}.{os.getpid()}.{os.urandom(3).hex()}.tmp')     # 临时文件名不固定：两个进程同时写同一个文件不会撞（QA 2.1）
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        for k in range(6):          # 网络盘（SMB）上目标正被别人读写时 rename 会报“已存在”／“没权限”：等一下再试（2026-11-25 在 NAS 上实测）
            try: os.replace(tmp, p); break
            except (FileExistsError, PermissionError):
                if k == 5: raise
                time.sleep(0.05 * (k + 1))
    finally:
        with contextlib.suppress(FileNotFoundError): tmp.unlink()


class Broken(Exception):
    """片子的数据文件坏了（不是合法的 JSON）：页面显示是哪个文件、能不能从上一版恢复（QA 2.4）。"""
    def __init__(self, f, err):
        self.file = Path(f); self.err = err; self.backup = backup_of(f).is_file()
        super().__init__(f'{self.file.name} 已损坏，无法读取（{err}）')


def backup_of(f): f = Path(f); return f.with_name('.' + f.name + '.上一版')


def read_data(f):
    """读一个数据文件（标记／审片台／写稿／备料）：没有给 {}；坏了抛 Broken（不再让整个服务断开）。"""
    f = Path(f)
    if not f.exists(): return {}
    try: d = rj(f)
    except (ValueError, UnicodeDecodeError) as e: raise Broken(f, e) from None
    if not isinstance(d, dict): raise Broken(f, '最外层不是 {…}')
    return d


def keep_backup(f):
    """改之前留一份上一版（已经读成功过的那份）：文件坏了能退回去。"""
    f = Path(f)
    with contextlib.suppress(OSError):
        if f.is_file(): shutil.copyfile(f, backup_of(f))




def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], **kw)
    if r.returncode != 0:
        die('命令失败：' + ' '.join(str(c) for c in cmd[:6]) + ' …')
    return r


def probe(path, entries='stream=width,height,r_frame_rate,nb_frames,pix_fmt,codec_name', stream='v:0', count=False):
    cmd = ['ffprobe', '-v', 'error', '-select_streams', stream, '-show_entries', entries, '-of', 'json']
    if count: cmd[1:1] = ['-count_frames']
    out = subprocess.run(cmd + [str(path)], capture_output=True, text=True).stdout
    s = (json.loads(out or '{}').get('streams') or [{}])[0]
    return s


def nframes(path):
    s = probe(path, 'stream=nb_read_frames', count=True)
    return int(s.get('nb_read_frames', 0))


def packets(path):
    """视频（第一条视频流）有几个包＝几帧（不解码）。读 JSON 的 streams：素材带时间码数据流（tmcd）时，ffprobe 9 的 csv 输出会把同一个数
    在 stream_groups 里再打一遍（“6036\\n\\n6036”），以前按 csv 读直接 ValueError（2026-11-22 261122 反馈单 新5：镜前灯往期成片）。"""
    out = subprocess.run(['ffprobe', '-v', 'error', '-count_packets', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_packets', '-of', 'json', str(path)],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    s = (json.loads(out or '{}').get('streams') or [{}])[0]
    return int(s.get('nb_read_packets') or 0)


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


# ---------- 覆盖审核线正在用的文件前先停（2026-11-22 261122 反馈单 新3） ----------
# 建好审核线后又跑 subs 重写了它引用的字幕整轨，用户原话：“哇，整条时间线上有很多离线媒体啊，你检查下什么情况？”
# review 建好时把引用的文件记在 合成/审核线引用.json；subs、subs-track、pip、anim-assemble（anim-render 最后也走它）、nav 写文件前查，
# 要覆盖的文件当前审核线正用着就停。命令行 --force 放行（common.allow_overwrite）；review 自己重拼字幕整轨时用 overwrite_ok()。
_OVERWRITE_OK = [False]


def allow_overwrite(on=True):
    _OVERWRITE_OK[0] = bool(on)


class overwrite_ok:
    """with overwrite_ok(): …  这一段里覆盖审核线引用的文件不拦（review 建新一版时自己重拼字幕整轨）。"""
    def __enter__(self): self.old = _OVERWRITE_OK[0]; _OVERWRITE_OK[0] = True
    def __exit__(self, *a): _OVERWRITE_OK[0] = self.old


def guard_overwrite(job, *paths):
    """paths 里有当前审核线（job.json 的 review_timeline）引用着的文件就停（--force 或 overwrite_ok 时不拦）。老片子没有 合成/审核线引用.json 的不拦。"""
    if _OVERWRITE_OK[0]: return
    f = job.dir / '合成' / '审核线引用.json'; cur = job.get('review_timeline')
    if not cur or not f.exists(): return
    try: refs = rj(f)
    except (ValueError, OSError): return
    if refs.get('时间线') != cur: return
    used = {str(Path(x).resolve()) for x in refs.get('文件') or []}
    hit = [str(Path(p)) for p in paths if p and str(Path(p).resolve()) in used and Path(p).exists()]
    if hit:
        die(f"当前审核线「{cur}」正引用着要被覆盖的文件：{'、'.join(Path(h).name for h in hit)}。覆盖了用户正在看的审核线会离线（或内容悄悄变掉）。"
            '先建新一版审核线（review），或用 --force 覆盖（用户还在看这一版就先别覆盖；覆盖后马上建新一版审核线给用户看）（2026-11-22 新3）')


# ---------- 用完即删的渲染临时文件放本机（2026-11-21 用户定：这类文件不放 NAS 上的片子项目里，慢） ----------
# 只放“用完就删”的中间文件（逐帧图、分段片段、截图）；成品、登记文件、日志、可复用的缓存结果照旧写在片子项目文件夹。
# 位置：<本机系统临时目录>/ThinkCut临时/<片子>/<用途或工程>/…；环境变量 THINKCUT_LOCAL_TMP 可以换成别的文件夹（离线测试、本机盘太小时用）。
LOCAL_TMP_NAME = 'ThinkCut临时'
LOCAL_TMP_RESERVE = 5 * 1024 ** 3      # 写完之后本机至少还要剩这么多（字节），不够就不开始写


def local_root():
    return Path(os.environ.get('THINKCUT_LOCAL_TMP') or Path(tempfile.gettempdir()) / LOCAL_TMP_NAME)


def film_key(film):
    """片子在本机临时目录里的文件夹名：项目文件夹名（往上找有 项目.json 的那一层；没有就用片子目录名）＋片子目录全路径的短指纹（两条同名的片子不混）。"""
    film = Path(film).expanduser().resolve()
    name = next((q.name for q in film.parents if (q / '项目.json').is_file()), film.name)
    safe = ''.join('_' if c in '/\\:%\0' else c for c in name)[:60] or '片子'
    return f"{safe}_{hashlib.sha256(str(film).encode()).hexdigest()[:8]}"


def local_path(film, *parts):
    """本机临时文件夹的位置（只算路径，不建）。"""
    return local_root().joinpath(film_key(film), *[str(x) for x in parts])


def local_free(path):
    """path 所在的盘还剩多少字节（path 还不存在就看它已有的上级）。"""
    q = Path(path)
    while not q.exists() and q != q.parent: q = q.parent
    return shutil.disk_usage(q).free


def local_tmp(film, *parts, need=0, what='临时文件', clean=True, free=None):
    """建好并返回本机临时文件夹 local_path(film, *parts)。clean=True 先清掉里面上次崩溃留下的东西；
    need＝估计这次要写多少字节：本机剩余空间不够 need＋5 GB 余量就停下说明（什么都还没写），不把本机磁盘写满。free 只给测试用（假装还剩多少）。"""
    d = local_path(film, *parts)
    if clean: shutil.rmtree(d, ignore_errors=True)
    have = local_free(d) if free is None else free
    if need and have < need + LOCAL_TMP_RESERVE:
        die(f'这台 Mac 的磁盘空间不够，没开始写：{what}估计要写 {need / 1024 ** 3:.1f} GB，本机临时目录（{local_root()}）所在的盘只剩 {have / 1024 ** 3:.1f} GB'
            f'（还要留 {LOCAL_TMP_RESERVE / 1024 ** 3:.0f} GB 余量）。先清理这台 Mac 的磁盘再跑；或设环境变量 THINKCUT_LOCAL_TMP 指到一个空间够的本机文件夹')
    d.mkdir(parents=True, exist_ok=True); return d


def local_rm(d):
    """删掉本机临时文件夹 d，并把它上面空了的文件夹也删掉（只在专用文件夹里面删，专用文件夹本身和外面的不碰）。"""
    d = Path(d); root = local_root()
    if root not in d.parents: return
    shutil.rmtree(d, ignore_errors=True)
    for q in d.parents:
        if root not in q.parents: break      # 到专用文件夹本身为止（它自己留着）
        try: q.rmdir()
        except OSError: break


class Job:
    """一条片子的工作目录：job.json 记画幅、帧率、口播工程位置、达芬奇工程名，其余文件都在这个目录里。"""
    def __init__(self, d):
        self.dir = Path(d).expanduser().resolve()
        p = self.dir / 'job.json'
        if not p.exists():
            die(f'{self.dir} 里没有 job.json，先运行 init')
        self.cfg = dict(DEFAULTS); self.cfg.update(rj(p))
        self.fmt = formats.get(self.cfg['format'], self.cfg.get('nav_h'))
        self.fps = Fraction(self.cfg['fps'])

    def __getitem__(self, k): return self.cfg[k]
    def get(self, k, d=None): return self.cfg.get(k, d)

    def save(self):
        wj(self.dir / 'job.json', {k: v for k, v in self.cfg.items() if k not in DEFAULTS or v != DEFAULTS[k] or k in ('format', 'fps')})

    def p(self, *parts):
        q = self.dir.joinpath(*parts); q.parent.mkdir(parents=True, exist_ok=True); return q

    def f(self, sec):                       # 秒 → 帧
        return round(sec * float(self.fps))

    def sec(self, frame):
        return frame / float(self.fps)

    @property
    def W(self): return self.fmt['W']
    @property
    def H(self): return self.fmt['H']

    def tc(self, frame, start_frame=None):
        """时间线帧号 → 达芬奇时间码（时间线从 01:00:00:00 起，29.97 不丢帧按 30 计）"""
        n = round(float(self.fps)); f = frame + n * 3600
        return f'{f // (n * 3600):02d}:{f // (n * 60) % 60:02d}:{f // n % 60:02d}:{f % n:02d}'
