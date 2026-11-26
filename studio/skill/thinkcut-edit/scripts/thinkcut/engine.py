# -*- coding: utf-8 -*-
"""AI 引擎：审片台后台直接调这台 Mac 上已登录的 AI 命令行处理用户提交的意见（2026-11-26 用户定）。

用户原话大意：只用 ThinkCut 一个程序，后台用 Claude Code，也能切换成 Codex，自由切换；像“AI 登录”一样调用本机已登录的 AI，
用他们的会员直接用全部功能；剪辑、动画、审片都在 ThinkCut 里完成，不用再去 Claude 桌面端挂着。

- 适配层：每种命令行 AI 一个 Adapter（找程序、查登录、拼命令、读它吐出来的 JSON 事件）。以后加别的 AI 照样加一个类、登记进 ENGINES。
- 每个项目记住自己那条会话（`审片工作台/AI引擎.json` 的 sessions），下次提交接着用；换了引擎就新开一条（新引擎不知道上一个说过什么，项目文件照常读）。
- 工作目录＝项目文件夹；提示里带上 thinkcut-edit 技能和审片台说明的位置。
- 权限收住（硬规矩：整片导出只认用户在审片台亲手点的定稿，发布由用户在蚁小二客户端点，AI 不能替用户确认）：
  Claude：--permission-mode dontAsk（没列在允许里的一律拒绝、不弹问）＋ --allowedTools 白名单 ＋ --disallowedTools 黑名单，只读用户设置以外的项目设置；
  Codex：--sandbox workspace-write（只能写项目文件夹、ThinkCut 程序数据文件夹、系统临时目录）、approval never（不卡在等批准）。
  两边都不用 --dangerously-bypass-approvals-and-sandbox / bypassPermissions。确认、采纳、定稿、发布只收审片台页面带口令（HttpOnly cookie，只在服务内存里）的请求，AI 拿不到。
- 旧的 `workbench … --wait` 挂对话方式照旧：对话里的 AI 挂着等时，提交的事交给它，不起后台 AI。
测试：THINKCUT_AI_PATH 指一个放假命令行的文件夹（空字符串＝一个 AI 都没装）；设了 THINKCUT_NO_OPEN 又没设 THINKCUT_AI_PATH 时当一个都没装（测试不会真起 AI）。
"""
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
SKILL = HERE.parents[2]                    # studio/skill/thinkcut-edit
PROGRAM = HERE.parents[5]                  # 程序根目录（core、studio、projects… 在这一层）
SCRIPT = SKILL / 'scripts' / 'thinkcut_edit.py'
STATES = ('未装', '未登录', '在线', '处理中', '出错')
FILE = 'AI引擎.json'                       # 项目里：审片工作台/AI引擎.json（会话 id、最近一次处理的过程要点）
STEPS = 40                                 # 过程要点最多留几条


def now(): return time.strftime('%Y-%m-%d %H:%M:%S')


# ---------- 本机设置（选哪个引擎；只存名字，不存任何账号和钥匙） ----------
def settings_file():
    if os.environ.get('THINKCUT_AI_SETTINGS'): return Path(os.environ['THINKCUT_AI_SETTINGS'])
    import sys
    sys.path.insert(0, str(PROGRAM / 'bin'))
    import thinkcut_config
    return thinkcut_config.app_dir() / 'AI引擎设置.json'


def read_settings():
    try: d = json.loads(settings_file().read_text(encoding='utf-8'))
    except (OSError, ValueError): d = {}
    return d if isinstance(d, dict) else {}


def write_settings(**kw):
    d = {**read_settings(), **kw}; f = settings_file(); f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + '.tmp'); tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8'); os.replace(tmp, f); return d


# ---------- 适配层 ----------
def search_dirs():
    """去哪找 AI 命令行。Mac 程序外壳起的服务 PATH 很短，常见安装位置都看一遍。"""
    if 'THINKCUT_AI_PATH' in os.environ: return [Path(x) for x in os.environ['THINKCUT_AI_PATH'].split(os.pathsep) if x]
    if os.environ.get('THINKCUT_NO_OPEN'): return []          # 测试：不碰本机真的 AI
    h = Path.home()
    extra = [h / '.local/bin', h / '.local/node-system/bin', h / '.claude/local', h / '.npm-global/bin', h / '.bun/bin', h / '.volta/bin',
             Path('/opt/homebrew/bin'), Path('/usr/local/bin')]
    return [Path(x) for x in os.environ.get('PATH', '').split(os.pathsep) if x] + extra


class Adapter:
    id = name = binary = ''
    login_args = ()

    def find(self):
        for d in search_dirs():
            f = d / self.binary
            if f.is_file() and os.access(f, os.X_OK): return f
        return None

    def env(self, exe, job=None):
        """给 AI 子进程的环境：程序所在文件夹放进 PATH（node 写的命令行要找得到 node）；标明是后台 AI。
        拿不到钥匙（2026-11-26）：钥匙串当作没有、老位置的钥匙文件指到一个空文件夹；豆包识别／合成、蚁小二存草稿由审片台代办（bin/thinkcut_broker.py）。"""
        e = {k: v for k, v in os.environ.items() if k not in ('THINKCUT_SECRETS_DIR', 'THINKCUT_BROKER_PROGRAM')}
        e['PATH'] = os.pathsep.join([str(exe.parent), e.get('PATH', '/usr/bin:/bin')]); e['THINKCUT_AI_BACKGROUND'] = '1'; e['THINKCUT_NO_KEYCHAIN'] = '1'
        if job is not None:
            e['THINKCUT_BROKER_DIR'] = str(broker_dir(job)); e['THINKCUT_SECRETS_DIR'] = str(broker_dir(job) / '没有钥匙')
        return e

    def logged_in(self, exe):
        raise NotImplementedError

    def status(self):
        exe = self.find()
        if not exe: return {'id': self.id, 'name': self.name, 'installed': False, 'logged_in': False, 'state': '未装', 'path': '',
                            'why': f'这台 Mac 上没找到 {self.name} 的命令行（{self.binary}）。装好后点“重新检测”。'}
        try: ok, why = self.logged_in(exe)
        except Exception as e: ok, why = False, f'查不了登录状态：{type(e).__name__}'
        return {'id': self.id, 'name': self.name, 'installed': True, 'logged_in': ok, 'path': str(exe), 'state': '在线' if ok else '未登录',
                'why': '' if ok else (why or f'{self.name} 的命令行还没登录：点“登录”，在弹出的终端里用你自己的账号登录。')}

    def login_cmd(self, exe): return ' '.join([_q(str(exe)), *self.login_args])

    def command(self, exe, prompt, cwd, session, writable, model=''):
        """返回 (命令, 新会话 id 或 None)。session：接着用的会话 id（None＝新开）；model：设置里填的模型（空＝用这个 AI 自己的默认）。"""
        raise NotImplementedError

    def parse(self, ev):
        """一条 JSON 事件 → [(种类, 内容)]：session 会话 id；step 过程要点；final 最后说的话；error 出错原话。"""
        raise NotImplementedError


def _q(s): return "'" + s.replace("'", "'\\''") + "'"


def _run(argv, env, timeout=15):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)


class Claude(Adapter):
    id, name, binary = 'claude', 'Claude Code', 'claude'
    login_args = ('auth', 'login')
    ALLOW = ['Read', 'Glob', 'Grep', 'Edit(./**)', 'Skill', 'TodoWrite',
             'Bash(python3 *)', 'Bash(ls *)', 'Bash(ffprobe *)', 'Bash(ffmpeg *)', 'Bash(mkdir *)', 'Bash(yxer-safe *)']
    DENY = ['Bash(*yxer publish*)', 'Bash(*yxer-safe publish*)', 'Bash(* publish *)', 'Bash(security *)', 'Bash(*security find*)', 'Bash(*keychain*)',
            'Bash(curl *)', 'Bash(wget *)', 'Bash(rm *)', 'WebFetch', 'Read(~/.ssh/**)', 'Read(~/.yxer/**)', 'Read(**/secrets/**)']

    def logged_in(self, exe):
        r = _run([str(exe), 'auth', 'status'], self.env(exe))
        try: d = json.loads(r.stdout)
        except ValueError: d = {}
        if d.get('loggedIn'): return True, ''
        return False, 'Claude Code 的命令行还没登录（Claude 桌面端的登录它用不了）：点“登录”，在弹出的终端里登录你的 Claude 账号。'

    def command(self, exe, prompt, cwd, session, writable, model=''):
        new = None
        if not session: new = session = str(uuid.uuid4())
        argv = [str(exe), '-p', prompt, '--output-format', 'stream-json', '--verbose',
                '--permission-mode', 'dontAsk', '--permission-prompts', 'none', '--setting-sources', 'project',
                '--add-dir', str(PROGRAM), '--allowedTools', *self.ALLOW, '--disallowedTools', *self.DENY,
                *(['--session-id', session] if new else ['--resume', session]), *(['--model', model] if model else [])]
        return argv, new

    def parse(self, ev):
        t = ev.get('type'); out = []
        if ev.get('session_id') and t in ('system', 'result'): out.append(('session', ev['session_id']))
        if t == 'assistant':
            for c in (ev.get('message') or {}).get('content') or []:
                if c.get('type') == 'text' and c.get('text', '').strip(): out.append(('say', c['text']))
                elif c.get('type') == 'tool_use': out.append(('step', tool_step(c.get('name', ''), c.get('input') or {})))
        elif t == 'result':
            if ev.get('is_error') or ev.get('subtype') not in (None, 'success'): out.append(('error', str(ev.get('result') or ev.get('subtype') or '')))
            elif ev.get('result'): out.append(('final', str(ev['result'])))
        return out


class Codex(Adapter):
    id, name, binary = 'codex', 'Codex', 'codex'
    login_args = ('login',)

    def logged_in(self, exe):
        r = _run([str(exe), 'login', 'status'], self.env(exe))
        text = (r.stdout + r.stderr).strip()
        if r.returncode == 0 and 'logged in' in text.lower() and 'not logged' not in text.lower(): return True, ''
        return False, 'Codex 的命令行还没登录：点“登录”，在弹出的终端里登录你的 ChatGPT 账号。'

    def command(self, exe, prompt, cwd, session, writable, model=''):
        cfg = [*(['-m', model] if model else []), '-c', 'sandbox_mode="workspace-write"', '-c', 'approval_policy="never"', '-c', 'sandbox_workspace_write.network_access=false',
               '-c', 'sandbox_workspace_write.writable_roots=' + json.dumps([str(x) for x in writable], ensure_ascii=False)]
        if session: return [str(exe), 'exec', 'resume', session, '--json', '--skip-git-repo-check', *cfg, prompt], None
        return [str(exe), 'exec', '--json', '--skip-git-repo-check', '--sandbox', 'workspace-write', '-C', str(cwd), *cfg, prompt], None

    def parse(self, ev):
        t = ev.get('type'); it = ev.get('item') or {}; k = it.get('type') or it.get('item_type'); out = []
        if t == 'thread.started' and ev.get('thread_id'): out.append(('session', ev['thread_id']))
        elif t == 'item.started' and k == 'command_execution': out.append(('step', cmd_step(it.get('command', ''))))
        elif t == 'item.completed':
            if k == 'agent_message' and it.get('text', '').strip(): out.append(('say', it['text']))
            elif k == 'file_change': out.append(('step', '改了 ' + '、'.join(Path(str(c.get('path', ''))).name for c in it.get('changes') or [] if c.get('path'))))
            elif k == 'command_execution' and it.get('exit_code') not in (None, 0) and 'workbench' in str(it.get('command', '')):
                out.append(('step', '回写没成功，AI 在换办法'))
        elif t in ('turn.failed', 'error'):
            e = ev.get('error'); out.append(('error', str((e or {}).get('message') if isinstance(e, dict) else e or ev.get('message') or '')))
        return out


ENGINES = {a.id: a for a in (Claude(), Codex())}       # 以后加别的命令行 AI：写一个 Adapter、登记在这里


# ---------- 过程要点：把工具调用说成人话（不贴原始日志） ----------
def cmd_step(cmd):
    c = cmd if isinstance(cmd, str) else ' '.join(map(str, cmd))
    m = re.match(r'\s*(?:/bin/)?(?:ba|z)?sh\s+-l?c\s+(["\'])(.*)\1\s*$', c, re.S)
    if m: c = m[2]                                     # Codex 把命令包在 /bin/zsh -lc "…" 里
    m = re.search(r'--(reply|rewrite|publish|withdraw)\s+["\']?([^\s"\']+)', c)
    if 'workbench' in c and m:
        what, x = m[1], m[2]
        if what == 'reply' and '问你' in c: return f'向你提了个问题（{x}），等你在审片台回答'
        return {'reply': f'回复了 {x}', 'rewrite': f'交了 {x} 的改写建议，等你逐处采纳', 'publish': f'发布到“{x}”栏', 'withdraw': f'撤回了 {x}'}[what]
    if 'workbench' in c and '--list' in c: return '读了审片台上的意见'
    if 'thinkcut_edit.py' in c:
        m = re.search(r'thinkcut_edit\.py["\']?\s+(\S+)', c); return f'运行剪辑程序：{m[1]}' if m else '运行剪辑程序'
    if re.search(r'\b(cat|sed|head|tail|rg|grep|ls|find|nl|wc)\b', c):
        names = [Path(x).name for x in re.findall(r"[\w./\u4e00-\u9fff -]*?[\w\u4e00-\u9fff-]+\.(?:md|json|py|txt|srt|html)\b", c)]
        names = list(dict.fromkeys(n.strip() for n in names if n.strip()))
        return ('读了 ' + '、'.join(names[:3])) if names else ('查了 ' + ('项目里的文字' if re.search(r'\b(rg|grep)\b', c) else '项目文件夹'))
    if re.match(r'\s*python3?\s+(-\s|-c\b|-\s*<<)', c): return '运行一小段脚本核对结果'
    return '运行：' + re.sub(r'\s+', ' ', c)[:60]


def tool_step(name, inp):
    if name == 'Bash': return inp.get('description') or cmd_step(inp.get('command', ''))
    if name in ('Read', 'Glob', 'Grep'): return '读了 ' + (Path(str(inp.get('file_path') or inp.get('path') or inp.get('pattern') or '')).name or '项目文件')
    if name in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'): return '改了 ' + Path(str(inp.get('file_path') or '')).name
    if name == 'Skill': return f"看了技能说明 {inp.get('skill') or inp.get('name') or ''}".strip()
    if name == 'TodoWrite': return '列了要做的几件事'
    return f'用了 {name}'


def first_line(text, n=80):
    t = re.sub(r'\s+', ' ', str(text or '')).strip()
    return t if len(t) <= n else t[:n - 1] + '…'


def _inner(s):
    """AI 命令行常把服务端的 JSON 报错原样包一层：取最里面那句 message。"""
    for _ in range(3):
        try: d = json.loads(s)
        except ValueError: break
        m = d.get('error') if isinstance(d, dict) else None
        m = m.get('message') if isinstance(m, dict) else d.get('message') if isinstance(d, dict) else None
        if not isinstance(m, str): break
        s = m
    return s


def humane(engine, raw, code=None):
    """出错说人话。"""
    s = _inner(str(raw or '')); low = s.lower(); nm = ENGINES[engine].name if engine in ENGINES else 'AI'
    m = re.search(r"['‘`]([\w.:-]+)['’`] model is not supported|model[^\w]+([\w.:-]+)[^\w]+(?:is )?not (?:supported|found|available)", s, re.I)
    if m or 'model_not_found' in low or 'model not found' in low:
        x = (m and (m[1] or m[2])) or ''
        return f'{nm} 用的模型{f"（{x}）" if x else ""}你的账号用不了：打开“设置”，在 {nm} 的“模型”里填一个能用的（留空＝用 {nm} 自己的默认）。'
    if any(k in low for k in ('not logged in', '/login', 'please log in', 'authentication', 'unauthorized', '401', 'login required')):
        return f'{nm} 没登录或登录过期了：打开“设置”，点 {nm} 旁边的“登录”。'
    if any(k in low for k in ('usage limit', 'rate limit', 'quota', '429', 'too many requests', 'limit reached')):
        return f'{nm} 的会员额度暂时用完了（或用得太快）：等一会儿再提交，或到“设置”里换一个 AI。'
    if any(k in low for k in ('enotfound', 'network', 'connection', 'timed out', 'econnrefused', 'offline')):
        return f'{nm} 连不上网络：检查一下网络，再点“提交 AI”。'
    if s.strip(): return f'{nm} 出错了：{first_line(s, 120)}'
    return f'{nm} 意外退出了（返回码 {code}）。再点一次“提交 AI”；还不行就到“设置”里换一个 AI。'


# ---------- 状态（带缓存：查登录要起一次命令行） ----------
_cache = {}
_cache_lock = threading.Lock()
TTL = 30


_refreshing = set()


def _refresh(eid):
    try:
        st = ENGINES[eid].status()
        with _cache_lock: _cache[eid] = (time.time(), st)
    finally:
        with _cache_lock: _refreshing.discard(eid)


def engine_status(eid, fresh=False):
    """查一个引擎装没装、登没登录。查过的先用旧结果，过期了在后台再查一次（页面每 3 秒来拿数据，不让它等命令行）；fresh＝现在就查。"""
    with _cache_lock:
        c = _cache.get(eid)
        if c and not fresh:
            if time.time() - c[0] >= TTL and eid not in _refreshing:
                _refreshing.add(eid); threading.Thread(target=_refresh, args=(eid,), daemon=True).start()
            return c[1]
        _refreshing.add(eid)
    _refresh(eid)
    with _cache_lock: return _cache[eid][1]


def all_status(fresh=False):
    return [engine_status(e, fresh) for e in ENGINES]


def chosen():
    """用哪个引擎：设置里选的；没选过就用第一个已登录的，再没有就第一个装了的，都没有给 claude。"""
    s = read_settings().get('engine')
    if s in ENGINES: return s
    sts = all_status()
    return next((x['id'] for x in sts if x['logged_in']), None) or next((x['id'] for x in sts if x['installed']), None) or 'claude'


def choose(eid):
    if eid not in ENGINES: raise ValueError('不认识的 AI：' + str(eid))
    write_settings(engine=eid); return engine_status(eid, fresh=True)


MODEL_OK = re.compile(r'[A-Za-z0-9._:\-\[\]]{1,80}')


def model_of(eid): return str((read_settings().get('models') or {}).get(eid) or '')


def set_model(eid, model):
    """设置里给某个 AI 指定模型（空＝用它自己的默认）。只存模型名字。"""
    if eid not in ENGINES: raise ValueError('不认识的 AI：' + str(eid))
    model = str(model or '').strip()
    if model and not MODEL_OK.fullmatch(model): raise ValueError('模型名字只能是字母、数字和 . _ - : 这几个符号')
    ms = dict(read_settings().get('models') or {}); ms[eid] = model
    if not model: ms.pop(eid)
    write_settings(models=ms); return model


def ready():
    st = engine_status(chosen()); return st if st['logged_in'] else None


def login(eid):
    """在终端里跑登录命令（账号由用户自己登，程序不碰密码）。测试（THINKCUT_NO_OPEN）时只返回命令不弹终端。"""
    if eid not in ENGINES: raise ValueError('不认识的 AI：' + str(eid))
    a = ENGINES[eid]; exe = a.find()
    if not exe: raise ValueError(f'这台 Mac 上没装 {a.name} 的命令行，装好再登录')
    cmd = a.login_cmd(exe); opened = False
    if not os.environ.get('THINKCUT_NO_OPEN'):
        path = os.pathsep.join([str(exe.parent), '/usr/bin', '/bin'])
        script = f'export PATH={_q(path)}:"$PATH"; {cmd}'
        with contextlib.suppress(Exception):
            opened = subprocess.run(['osascript', '-e', 'tell application "Terminal" to activate', '-e',
                                     f'tell application "Terminal" to do script {json.dumps(script)}'], timeout=15, capture_output=True).returncode == 0
    with _cache_lock: _cache.pop(eid, None)
    return {'cmd': cmd, 'opened': opened}


# ---------- 项目里的记录 ----------
def project_file(job): return job.dir / '审片工作台' / FILE


def load(job):
    try: d = json.loads(project_file(job).read_text(encoding='utf-8'))
    except (OSError, ValueError): d = {}
    return d if isinstance(d, dict) else {}


def save(job, d):
    f = project_file(job); f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + '.tmp'); tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8'); os.replace(tmp, f)


def broker_dir(job): return job.dir / '审片工作台' / '代办'        # 后台 AI 交给审片台代办的单子（见 bin/thinkcut_broker.py）


def workdir(job):
    """AI 的工作目录：项目文件夹（标准项目是 02_制作/自动剪辑 往上两层；口播阶段就是给的那个文件夹）。"""
    from . import board
    with contextlib.suppress(Exception):
        pd = board.project_dir(job)
        if pd: return Path(pd)
    return job.dir


def writable_dirs(job):
    import sys
    sys.path.insert(0, str(PROGRAM / 'bin'))
    out = [workdir(job), job.dir, Path(tempfile.gettempdir())]
    with contextlib.suppress(Exception):
        import thinkcut_config
        out.append(thinkcut_config.app_dir())
    seen = []
    for x in out:
        if x not in seen: seen.append(x)
    return seen


RULES = """你是 ThinkCut · 思刻 审片台的后台 AI（{name}）。用户刚在审片台点了“提交 AI”，现在没有人在对话里看着你，你做的事会实时显示在审片台“AI 正在做什么”里。
规矩（必须照做）：
1. 只处理下面清单里的事；逐项处理、逐项用命令回写（--reply／--rewrite／--publish），回写后页面上马上能看到。
2. 要问用户：用 --reply 编号 --status 问你 --note "问题" --options "A …|B …"（文稿栏是 --reply 稿N --status 问你 …），问完这一件就算处理了，接着做别的，不要等回答。用户在审片台回答后会再提交给你。
3. 不能替用户确认（可以／选这个／定稿／采纳）；不导出整片；不发布（禁止 yxer publish，上传只存蚁小二草稿，正式发布由用户在蚁小二客户端点）；不读、不打印、不改任何钥匙和密钥（钥匙串、secrets 文件夹）；不用 curl 冒充审片台页面。
   你这里拿不到钥匙、命令不联网、连不上本机达芬奇：我们自己的程序照常运行就行——豆包识别／合成（transcribe_video.py、doubao_tts.py、doubao_direct_asr.py）、yxer-safe、要连达芬奇的（thinkcut_edit.py 除 workbench 回写以外的命令、edit_project.py、broll.py、fx.py、project.py）、生图（thinkcut_cover.py）、项目里没改过的 出片.py——它们会自动交给审片台代办、把结果给你。不要想办法自己去读钥匙、自己连达芬奇或联网；你自己写的临时脚本连不上这些。代办拒绝了的（比如 出片.py 被改过），--reply 告诉用户原因，接着做别的。
4. 不直接改文案文件 03_成片/*.md：文稿的改写一律用 --rewrite 交建议，用户点“采纳”才写进去。
5. 先读剪辑技能说明 {skill}/SKILL.md，和 {skill}/references/审片工作台.md 里“每一栏怎么用”“文稿栏”两节；涉及哪一步再读那一步的说明。
6. 审片台命令：python3 "{script}" workbench "{jobdir}" <参数>（工作目录就是项目文件夹 {cwd}）。临时文件（比如 --rewrite 用的改写 JSON）放 {tmp}，别放在项目文件夹别处。
7. 全部做完后，用一两句中文大白话说你做了什么、还有什么要用户看（这句话会显示给用户）。
"""


def prompt(job, eid, brief):
    tmp = job.dir / '审片工作台' / 'tmp'; tmp.mkdir(parents=True, exist_ok=True)
    return RULES.format(name=ENGINES[eid].name, skill=SKILL, script=SCRIPT, jobdir=job.dir, cwd=workdir(job), tmp=tmp) + '\n清单：\n' + brief


# ---------- 后台处理（审片台服务里一个项目一个） ----------
class Runner:
    """用户点“提交 AI”（且没有对话里的 AI 挂着 --wait）→ 起后台 AI 处理这一批；处理中又提交的，处理完接着做下一批。
    brief(got)：把这一批要处理的事印成清单（workbench.report 的字），pick()：标记 AI 接了并返回这一批；unsend(col)：停了／出错时退回“待提交”。"""

    def __init__(self, job, brief, pick, pending, unsend, alive=lambda: None):
        self.job, self.brief, self.pick, self.pending, self.unsend, self.alive = job, brief, pick, pending, unsend, alive
        self.lock = threading.Lock(); self.proc = None; self.thread = None; self.stopping = False

    def busy(self):
        return bool(self.thread and self.thread.is_alive())

    def kick(self, wait=False):
        """有已提交、还没人接的事就开始处理。返回 True＝交给后台 AI 了（或它正在处理，做完会接着做）。"""
        with self.lock:
            if self.busy(): return True
            if not ready(): return False
            self.stopping = False
            self.thread = threading.Thread(target=self._loop, daemon=True); self.thread.start()
        if wait: self.thread.join()
        return True

    def stop(self):
        """用户点“停止”：结束 AI 进程（连同它起的子进程）和审片台替它代办的程序，这一批没处理完的退回“待提交”。"""
        self.stopping = True; p = self.proc
        with contextlib.suppress(Exception):
            import sys
            sys.path.insert(0, str(PROGRAM / 'bin')); import thinkcut_broker
            thinkcut_broker.stop_all(broker_dir(self.job))
        if p and p.poll() is None:
            with contextlib.suppress(Exception): os.killpg(p.pid, signal.SIGTERM)
            try: p.wait(5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception): os.killpg(p.pid, signal.SIGKILL)
        if self.thread: self.thread.join(10)
        return {'stopped': True}

    def _loop(self):
        while not self.stopping:
            got = self.pick()
            if not got: return
            self._run(got)

    def _record(self, fn):
        d = load(self.job); fn(d); save(self.job, d); return d

    def _step(self, kind, text):
        def f(d):
            r = d.setdefault('run', {}); st = r.setdefault('steps', []); t = first_line(text, 140)
            if st and st[-1]['kind'] == kind and st[-1]['text'].split(' ×')[0] == t:          # 一样的事连着做：合成一条“×N”
                n = int((st[-1]['text'].split(' ×')[1:] or ['1'])[0]) + 1; st[-1].update(at=now(), text=f'{t} ×{n}')
            else: st.append({'at': now(), 'kind': kind, 'text': t})
            r['steps'] = st[-STEPS:]
        self._record(f); self.alive()

    def _run(self, got):
        eid = chosen(); a = ENGINES[eid]; exe = a.find(); cols = sorted({c for c, _ in got}); items = [i for _, i in got]
        rec = load(self.job); sess = rec.get('sessions') or {}
        if rec.get('engine_last') and rec.get('engine_last') != eid: sess = {}          # 换了引擎：新开会话（项目文件照常读）
        session = (sess.get(eid) or {}).get('id')
        n = int((rec.get('run') or {}).get('n') or 0) + 1
        run = {'n': n, 'engine': eid, 'name': a.name, 'columns': cols, 'items': items, 'state': '处理中', 'started': now(), 'steps': [], 'resumed': bool(session)}
        self._record(lambda d: d.update(run=run, engine_last=eid, sessions=sess))
        self._step('step', f"{a.name} 接手：{'、'.join(items)}" + ('（接着上次的会话）' if session else '（新开会话）'))
        text = prompt(self.job, eid, self.brief(got))
        state, err, final, said = self._exec(a, exe, text, session, eid)
        if state == '出错' and session and not said:          # 接不上上次的会话（被删了、过期了）：新开一条再试一次
            self._step('step', '上次的会话接不上，新开一条重试')
            self._record(lambda d: d.setdefault('sessions', {}).pop(eid, None))
            state, err, final, said = self._exec(a, exe, text, None, eid)
        left = [i for i in items if i in self.pending()]
        if state != '完成' or left:
            for c in cols: self._safe_unsend(c)
        def f(d):
            r = d.setdefault('run', {}); r.update(state=state, ended=now(), summary=first_line(final, 300) if final else '', error=err, left=left)
        self._record(f); self.alive()

    def _safe_unsend(self, c):
        with contextlib.suppress(Exception): self.unsend(c)

    def _exec(self, a, exe, text, session, eid):
        argv, new = a.command(exe, text, workdir(self.job), session, writable_dirs(self.job), model_of(eid))
        if new: self._record(lambda d: d.setdefault('sessions', {}).__setitem__(eid, {'id': new, 'at': now()}))
        log = self.job.dir / '审片工作台' / 'AI记录' / f'{time.strftime("%Y%m%d-%H%M%S")}-{eid}.jsonl'
        log.parent.mkdir(parents=True, exist_ok=True)
        err = ''; final = ''; said = False; tail = []
        try:
            self.proc = subprocess.Popen(argv, cwd=str(workdir(self.job)), env=a.env(exe, self.job), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', start_new_session=True, bufsize=1)
        except OSError as e:
            return '出错', humane(eid, str(e)), '', False
        with open(log, 'w', encoding='utf-8') as lf:
            for line in self.proc.stdout:
                lf.write(line); line = line.strip()
                if not line: continue
                try: ev = json.loads(line)
                except ValueError:
                    tail = (tail + [line])[-5:]; continue
                if not isinstance(ev, dict): continue
                for kind, val in a.parse(ev):
                    if kind == 'session':
                        self._record(lambda d, v=val: d.setdefault('sessions', {}).__setitem__(eid, {'id': v, 'at': now()}))
                    elif kind == 'step' and val: said = True; self._step('step', val)
                    elif kind == 'say': said = True; final = val; self._step('say', val)
                    elif kind == 'final': final = val
                    elif kind == 'error': err = val
        code = self.proc.wait(); self.proc.stdout.close()
        if self.stopping: return '已停止', '你点了“停止”：没处理完的已退回“待提交”，改好可以再提交。', final, said
        if code != 0 or err: return '出错', humane(eid, err or '\n'.join(tail), code), final, said
        return '完成', '', final, said


def page_info(job, waiter=False, runner=None):
    """页面右上角和“AI 正在做什么”要的：当前引擎、状态（未装／未登录／在线／处理中／出错）、人话原因、最近一次处理的要点。"""
    eid = chosen(); st = engine_status(eid); rec = load(job); run = rec.get('run') or {}
    busy = bool(runner and runner.busy()) or (run.get('state') == '处理中' and not runner)
    if busy: state = '处理中'
    elif waiter: state = '在线'
    else: state = st['state']
    why = st['why'] if state in ('未装', '未登录') else ''
    if run.get('state') == '出错' and not busy and state == '在线': state, why = '出错', run.get('error') or ''
    if run.get('state') == '处理中' and runner and not runner.busy(): run = {**run, 'state': '已中断'}     # 服务重启过：上次没做完
    return {'engine': eid, 'name': st['name'], 'state': state, 'why': why, 'waiter': bool(waiter), 'online': bool(waiter or st['logged_in']),
            'run': {k: run.get(k) for k in ('n', 'engine', 'name', 'columns', 'items', 'state', 'started', 'ended', 'steps', 'summary', 'error', 'left')} if run else None}
