# -*- coding: utf-8 -*-
"""AI 对话框（2026-11-27 用户要）：程序里随时跟 AI 说一句。

用户原话：“加一个我跟AI的对话框，但平时对话框可以隐藏起来，我可以随时在程序里跟AI说一句话。比如说，我现在这个最新的项目我要开项，
或者我已经开完项了，但是我想往里添加资料，比如说我的B roll、A roll什么的，我直接跟AI说，它就直接能帮我整理好……
也有一些可能其他的事儿想调用AI能力，比如说我让它把这个东西存档一下，存到我们这规范路径里。”

- 两种范围：
  · 项目（审片台里）：对话属于这个项目，接着这个项目“提交 AI”的同一条会话（审片工作台/AI引擎.json 的 sessions），AI 记得前面做过什么。
    记录在 <项目>/审片工作台/对话.json。工作目录、能写哪里、代办和“提交 AI”一样，代办另用一个文件夹（审片工作台/代办-对话），那边不跑导出、上传。
  · 首页：不属于某个项目，开项、在项目之间查、存档、整理。记录和会话在程序数据文件夹（ThinkCut/首页对话/对话.json）。
    AI 只能写自己的工作区和临时目录；开项、存档走代办里的 project.py（审片台代办同一套规矩，只放这一个程序和挪进回收站）。
  · 首页专管首页，项目专管这个项目，不混（2026-11-27 用户补充：“不能把所有项目混在一个对话里，容易影响上下文”）：
    首页的 AI 开项或认准某个项目后，回复最后一行写“转到项目：<项目文件夹>”，程序核对是真项目（片子文件夹里、有 项目.json）后，
    把用户这句话带进那个项目自己的对话（一条“从首页转过来”，点“接着做这件事”才发给项目的 AI），首页给“进入项目”按钮；首页对话不因此能写那个项目。
- 用哪个 AI、哪个模型：就是右上角／设置里选的（engine.chosen、model_of），每句话现读；用户不换，对话就不换（2026-11-27 用户定）。
- 后台引擎、沙盒、钥匙代办都和“提交 AI”一样（engine.py）；Claude 加 --include-partial-messages，回复一个字一个字出来。
- 整片导出、上传发布、定稿只认用户在页面上亲手点：对话里说“导出吧”“发布吧”“定稿”程序直接回一句并给“去那一栏”的按钮，不交给 AI；
  万一 AI 自己去跑，对话的代办也不跑（thinkcut_broker.chat_policy），导出、上传的闸也照旧在程序里。删东西只能挪进回收站（bin/thinkcut_trash.py）。
- AI 要用户选：回复最后一行写“选项：A …｜B …”，页面上变成按钮，点了就当用户回了那句。
- 拖文件进来：项目里直接放进 01_原始素材（materials.receive：同名不覆盖、大文件边收边写、半截不留，和文稿栏“素材”同一套、同一份 备料.json），
  再把放在哪告诉 AI；首页对话不收文件（放素材是项目里的事）。用户说了文件在哪：AI 用 workbench <项目> --add-material 路径（同一套规矩）。
"""
import contextlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from . import engine

FILE = '对话.json'
KEEP = 300                       # 记录最多留多少条
STEPS = 30                       # 每条回复的过程要点最多留几条
OPT = re.compile(r'^\s*(?:选项|请选)[:：]\s*(.+?)\s*$')
FOCUS = re.compile(r'^\s*(?:转到|接到)项目[:：]\s*(.+?)\s*$')

# 只认页面上亲手点的三件事：对话里说了，程序直接回一句（不交给 AI）。问问题（吗、怎么、哪……）照常交给 AI 回答。
GATES = [
    ('export', re.compile(r'导出(?:整片|成片|视频|片子|4\s*K|吧|一下|出来|了吧)|(?:出|导)成片|渲染成片|^\s*导出\s*$'),
     '导出整片要你在页面上亲手点：到审片栏看完，点“这一栏定了”，AI 才会导出。对话里说“导出”不算。', '审核线', '去审片栏'),
    ('publish', re.compile(r'发布|发到(?:各|平台|抖音|[Bb]站|小红书|视频号|快手|油管|YouTube)|上传到?(?:蚁小二|平台|各平台|抖音|[Bb]站|小红书|视频号)|存(?:到|进)?(?:蚁小二)?草稿'),
     '上传、发布要你在页面上亲手点：到发布栏看好标题简介，点“存到蚁小二草稿”；正式发布在蚁小二客户端里点。对话里说了不算。', '上传文案', '去发布栏'),
    ('settle', re.compile(r'定稿|这一栏定了|替我(?:确认|点)|帮我(?:确认|点(?:可以|定))'),
     '定稿、确认要你在页面上亲手点（每一栏的“定稿”“这一栏定了”“可以”）。对话里说了不算，AI 也不能替你点。', '', '我知道了'),
]
ASK = re.compile(r'[?？吗呢么]|怎么|如何|哪|什么|能不能|可不可以|是不是|多少|为啥|为什么|没有')


def now(): return time.strftime('%Y-%m-%d %H:%M:%S')


def gate(text):
    """用户的话是不是在要求导出、发布、定稿（不是问问题）：返回说到的每一件 [(种类, 回话, 栏目, 按钮字)]，没有就是空的。"""
    t = str(text or '')
    if ASK.search(t): return []
    return [(kind, say, col, label) for kind, rx, say, col, label in GATES if rx.search(t)]


def parse_tail(text):
    """从 AI 最后说的话里取出“选项：…”“接到项目：…”两行（页面上不显示原文）。返回 (正文, 选项, 项目路径)。"""
    lines = str(text or '').rstrip().split('\n'); opts, focus = [], ''
    keep = []
    for ln in lines:
        m = OPT.match(ln.replace('*', ''))
        if m:
            opts = [x.strip() for x in re.split(r'[｜|]', m[1]) if x.strip()][:6]; continue
        m = FOCUS.match(ln.replace('*', '').replace('`', ''))
        if m: focus = m[1].strip().strip('"“”「」'); continue
        keep.append(ln)
    return '\n'.join(keep).strip(), opts, focus


class Oops(Exception):
    pass


class Chat:
    """一段对话（一个项目一个；首页一个）。页面每半秒来拿一次 state()，说话 send()、停 stop()、清空 clear()。"""

    def __init__(self, kind, file, workdir, writable, broker, sessions, *, job=None, roots=lambda: [], runner=None, alive=lambda: None, open_job=None, current=lambda: (None, None)):
        self.kind, self.file, self.job, self.roots, self.runner, self.alive, self.open_job, self.current = kind, Path(file), job, roots, runner, alive, open_job, current
        self._workdir, self._writable, self.broker, self.sessions = workdir, writable, Path(broker), sessions
        self.lock = threading.RLock(); self.thread = None; self.proc = None; self.stopping = False

    # ---------- 记录 ----------
    def load(self):
        try: d = json.loads(self.file.read_text(encoding='utf-8'))
        except (OSError, ValueError): d = {}
        if not isinstance(d, dict): d = {}
        d.setdefault('messages', []); return d

    def save(self, d):
        self.file.parent.mkdir(parents=True, exist_ok=True); d['messages'] = d['messages'][-KEEP:]
        tmp = self.file.with_name(self.file.name + f'.{os.getpid()}.tmp'); tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8'); os.replace(tmp, self.file)

    def edit(self, fn):
        with self.lock:
            d = self.load(); out = fn(d); self.save(d); return out

    def add(self, **m):
        m = {'id': uuid.uuid4().hex[:10], 'at': now(), **m}
        self.edit(lambda d: d['messages'].append(m)); return m

    def update(self, mid, fn):
        def f(d):
            for m in d['messages']:
                if m['id'] == mid: fn(m); return m
        return self.edit(f)

    # ---------- 状态 ----------
    def busy(self): return bool(self.thread and self.thread.is_alive())

    def focus(self):
        """首页对话刚转过去的项目（只用来在标题下面显示、给“进入项目”按钮；首页对话不因此能写它。不在了就当没有）。"""
        if self.kind != 'home': return None
        f = self.load().get('focus')
        p = Path(f) if f else None
        return p if p and (p / '项目.json').is_file() else None

    def workdir(self): return Path(self._workdir())

    def writable(self): return list(self._writable())

    def state(self):
        d = self.load(); eid = engine.chosen(); st = engine.engine_status(eid)
        f = self.focus(); other = bool(self.runner and self.runner.busy())
        pd = self.material_project() if self.kind == 'project' else None
        return {'ok': True, 'kind': self.kind, 'busy': self.busy(), 'other': other, 'messages': d['messages'][-120:], 'project': Path(pd).name if pd else '',
                'focus': {'path': str(f), 'name': f.name} if f else None,
                'engine': {'id': eid, 'name': st['name'], 'state': '处理中' if self.busy() or other else st['state'], 'why': st['why'], 'online': st['logged_in']}}

    # ---------- 说话 ----------
    def check_files(self, files):
        """页面先传好的文件（拖进来的）：只认这段对话能放文件的项目里的 01_原始素材。"""
        out = []; base = self.material_project()
        for x in files or []:
            if not isinstance(x, dict): continue
            p = Path(str(x.get('path') or ''))
            full = (base / p).resolve() if base and not p.is_absolute() else p.resolve()
            if not base or (base / '01_原始素材').resolve() not in full.parents or not full.exists(): continue
            out.append({'name': str(x.get('name') or full.name), 'path': str(full.relative_to(base.resolve())), 'size': int(x.get('size') or 0)})
        return out

    def material_project(self):
        """拖进来的文件放哪个项目：只有项目对话能放（放进这个项目）；首页对话不管某个项目里的事。"""
        if self.kind == 'project':
            from . import board
            return board.project_dir(self.job)
        return None

    def material_job(self):
        if self.kind == 'project': return self.job
        raise Oops('首页的对话只管首页（开项、找项目、存档这些）：放素材请进那个项目，在项目里的对话框拖进来')

    def send(self, text, files=None):
        text = str(text or '').strip(); files = self.check_files(files)
        if not text and not files: raise Oops('先写一句话')
        if len(text) > 8000: raise Oops('一次说的太长了（超过 8000 字）：分几次说，或者把长文放进文件里告诉 AI 在哪')
        with self.lock:
            if self.busy(): raise Oops('AI 还在回上一句：等它说完，或者先点“停止”')
            if self.runner and self.runner.busy(): raise Oops('AI 正在处理你在栏目里提交的意见（同一条会话）：等它做完再说，或先在右下角点“停止”')
            me = self.add(role='me', text=text, **({'files': files} if files else {}))
            g = gate(text) if text else []
            if g:
                act = [{'label': label, 'col': col} for _, _, col, label in g if col]
                self.add(role='note', text='\n\n'.join(x[1] for x in g), gate=g[0][0], actions=act); return {'gate': g[0][0], 'id': me['id']}
            eid = engine.chosen(); st = engine.engine_status(eid)
            if not st['logged_in']:
                self.add(role='err', text=st['why'] or f'{st["name"]} 现在用不了', actions=[{'label': '打开设置', 'go': 'settings'}], retry=me['id'])
                return {'id': me['id'], 'offline': True}
            self.stopping = False
            ai = self.add(role='ai', text='', live='', steps=[], state='回复中', engine=eid, name=st['name'])
            self.thread = threading.Thread(target=self._run, args=(ai['id'], text, files, eid), daemon=True); self.thread.start()
        return {'id': me['id'], 'reply': ai['id']}

    def stop(self):
        """用户点“停止”：结束 AI 进程（连同它起的子进程）和对话代办起的程序。"""
        self.stopping = True; p = self.proc
        with contextlib.suppress(Exception):
            import sys
            sys.path.insert(0, str(engine.PROGRAM / 'bin')); import thinkcut_broker
            thinkcut_broker.stop_all(self.broker)
        engine.kill(p)
        if self.thread: self.thread.join(10)
        return {'stopped': True}

    def clear(self):
        """清空这段对话的记录（首页的连会话、接到的项目一起清；项目的会话和“提交 AI”共用，不清）。"""
        if self.busy(): raise Oops('AI 正在回复：先点“停止”再清空')
        def f(d):
            n = len(d['messages']); d['messages'] = []
            if self.kind == 'home': d.pop('sessions', None); d.pop('engine_last', None); d.pop('focus', None)
            return n
        return {'cleared': self.edit(f)}

    # ---------- 后台跑 AI ----------
    def prompt(self, eid, text, files, cont=False):
        a = engine.ENGINES[eid]; skill = engine.SKILL; prog = engine.PROGRAM
        edit = engine.SCRIPT; proj = prog / 'projects' / 'bin' / 'project.py'; trash = prog / 'bin' / 'thinkcut_trash.py'
        common = f"""规矩（必须照做）：
1. 先读要用的技能说明：项目（开项、整理素材、存档） {prog}/projects/skills/thinkcut-project/SKILL.md，剪辑 {skill}/SKILL.md。
2. 加素材：用户说了文件在哪，python3 "{edit}" workbench "<项目文件夹>" --add-material "路径1" "路径2"（复制进 01_原始素材，同名不覆盖、半截不留），再按 thinkcut-project 的规矩整理（分 A roll／B roll／音乐、改规范名字、登记素材清单、materials-check）。用户拖进来的文件程序已经放进 01_原始素材，下面会列出来。
3. 只认用户在页面上亲手点的：整片导出、上传／发布（蚁小二）、定稿、确认（可以／选这个／采纳）。用户在对话里要求这些，你不做，告诉他去哪一栏点哪个按钮。
4. 不永久删除任何东西（不用 rm）：要挪走就 python3 "{trash}" 路径，挪进回收站。不读、不打印、不改钥匙和密钥。命令不联网；豆包识别／合成、要连达芬奇的、生图、project.py 这些我们自己的程序照常运行，会自动交给审片台代办。
5. 要用户选：回复最后单独一行写“选项：A 的说法｜B 的说法｜C 的说法”（每项是用户点了就会回你的那句话，写成大白话）。
6. 一条命令只做一件事：不要用 cd、&&、;、| 把几条拼在一起（工作目录已经是对的；拼起来的命令会被权限规则拒绝），路径写完整。
7. 用中文大白话、简短，先说结果；运行命令时附的 description 也用中文（会显示在过程要点里）。临时文件放 {tempfile.gettempdir()}。"""
        if self.kind == 'project':
            from . import board
            pd = board.project_dir(self.job) or self.job.dir
            head = f"""你是 ThinkCut · 思刻 的 AI 助手（{a.name}）。用户在审片台的对话框里跟你说话，你说的话会一边出一边显示给他。
这段对话属于项目「{Path(pd).name}」（项目文件夹 {pd}，审片台数据 {self.job.dir}），和用户在栏目里点“提交 AI”是同一条会话：前面做过的事你都记得。
这段对话只管这个项目：不读、不动别的项目的文件，也不聊别的项目（用户问别的项目或要开新项目，请他回首页的对话说）。这个项目里各栏的事你都能串起来管：文稿（口播文案）、口播精剪、动画、审片、封面、发布。
审片台命令：python3 "{edit}" workbench "{self.job.dir}" <参数>（读各栏意见 --list、发布、回复、交文稿改写建议 --rewrite 都用它）；这个项目能写的只有项目文件夹和临时目录。"""
        else:
            roots = '、'.join(str(r) for r in self.roots()) or '（没找到）'; ip, here = self.current()
            where = f' --root "{here}"' + (f' --ip "{ip}"' if ip else '') if here else ''
            head = f"""你是 ThinkCut · 思刻 的 AI 助手（{a.name}）。用户在首页的对话框里跟你说话（不在某个项目里），你说的话会一边出一边显示给他。
放项目的文件夹（各个 IP）：{roots}。项目文件夹里有 项目.json；做完的在 finish/ 里。
这段对话专门管首页：开项、在项目之间查找和比较、看哪些项目在等用户、存档、移到 finish 这些跨项目的事。你在这里只能读项目文件，不能写任何项目文件夹（开项、存档交给代办里的 project.py）。
首页现在看的是 IP「{ip or '默认'}」，项目在 {here or roots}。
开项：python3 "{proj}" init "6 位编号 题目"{where}（编号用今天的日期 {time.strftime('%y%m%d')}，撞号程序自动顺延；建好文件夹和达芬奇库；它会交给审片台代办跑，打印出项目文件夹）。存档、整理检查也用 python3 "{proj}" <命令> "<项目文件夹>"。
要动哪个项目，先说清楚是哪个（拿不准就用“选项：”让用户挑）。某一个项目里面的事（加素材、整理素材、文稿、口播精剪、动画、审片、封面、发布）不在首页做，免得几个项目混在一段对话里：开项以后、或认准是哪个项目以后，在回复最后单独一行写“转到项目：<项目文件夹完整路径>”，程序会把用户这句话带进那个项目自己的对话，用户点“进入项目”就在那边接着做。"""
        lines = [head, common, '']
        if cont: lines.append(text)
        else:
            lines.append('用户说：' + (text or '（没写字，只放了文件）'))
            if files:
                pd = self.material_project()
                lines.append(f'用户拖进来的文件，程序已经放进项目「{Path(pd).name}」：')
                lines += [f'  {pd}/{x["path"]}（{x["size"] / 1024 / 1024:.1f} MB）' for x in files]
        return '\n'.join(lines)

    def _run(self, mid, text, files, eid, cont=False):
        try:
            self._turn(mid, text, files, eid, cont)
        except Exception as e:          # 出了没料到的错：这一条写出错，不卡在“回复中”
            self.update(mid, lambda m: m.update(state='出错', text=m.get('text') or '', error=f'对话出错：{type(e).__name__}：{e}', live=''))
        finally:
            self.alive()

    def _turn(self, mid, text, files, eid, cont):
        a = engine.ENGINES[eid]; exe = a.find()
        sess = self.sessions.get(eid); session = sess
        self.update(mid, lambda m: m.update(resumed=bool(session)))
        p = self.prompt(eid, text, files, cont)
        state, err, final = self._exec(a, exe, p, session, eid, mid)
        if state == '出错' and session and not (self.load_msg(mid) or {}).get('said'):          # 接不上上次的会话：新开一条再试一次
            self.sessions.drop(eid); self._step(mid, '上次的会话接不上，新开一条重试')
            state, err, final = self._exec(a, exe, p, None, eid, mid)
        body, opts, focus = parse_tail(final)
        new_focus = None
        if focus and self.kind == 'home' and state == '完成':
            new_focus = self.set_focus(focus)
            if isinstance(new_focus, str): self._step(mid, new_focus); new_focus = None
        def done(m):
            m['state'] = state; m['live'] = ''; m['ended'] = now()
            if body: m['text'] = body
            elif state == '完成' and not m.get('text'): m['text'] = '（做完了，没多说什么）'
            if opts: m['options'] = opts
            if err: m['error'] = err
            if err and state == '出错': m['actions'] = [{'label': '打开设置', 'go': 'settings'}]
        self.update(mid, done)
        if new_focus:                       # 转到项目（2026-11-27 用户：首页对话专管首页，项目的事在项目自己的对话里，不混）
            carried = self.handoff(new_focus, text) if not cont else False
            self.add(role='note', text=f'项目「{new_focus.name}」里面的事转到它自己的对话了' + ('（你刚才这句话已经带过去）' if carried else '') + '：点“进入项目”，在那边接着说。',
                     actions=[{'label': '进入项目', 'open': str(new_focus), 'chat': 1}])

    def load_msg(self, mid):
        return next((m for m in self.load()['messages'] if m['id'] == mid), None)

    def handoff(self, pd, text):
        """首页 → 项目：把用户在首页说的这句话带进那个项目的对话记录（一条“从首页转过来”，带“接着做这件事”按钮，点了才发给项目的 AI）。"""
        if not self.open_job: return False
        try:
            job = self.open_job(pd); f = job.dir / '审片工作台' / FILE
            other = Chat('project', f, lambda: pd, lambda: [], f.parent / '代办-对话', None, job=job)
            other.add(role='note', text=f'从首页转过来：你在首页说的「{engine.first_line(text, 200)}」。这个项目的事在这里接着做。',
                      actions=[{'label': '接着做这件事', 'send': text}], origin='首页')
            return True
        except Exception:
            return False

    def set_focus(self, path):
        """首页对话转到一个项目：只认片子文件夹里的真项目（有 项目.json）。返回项目路径，或一句不行的原因。"""
        p = Path(os.path.expanduser(path))
        try: p = p.resolve()
        except OSError: return f'没接上：{path} 不对'
        if not (p / '项目.json').is_file(): return f'没接上：{p} 不是项目文件夹（没有 项目.json）'
        roots = [Path(r).resolve() for r in self.roots()]
        if not any(r in p.parents for r in roots): return f'没接上：{p} 不在放项目的文件夹里'
        self.edit(lambda d: d.update(focus=str(p))); return p

    def _step(self, mid, text, kind='step'):
        def f(m):
            st = m.setdefault('steps', []); t = engine.first_line(text, 140)
            if st and st[-1]['kind'] == kind and st[-1]['text'].split(' ×')[0] == t:
                n = int((st[-1]['text'].split(' ×')[1:] or ['1'])[0]) + 1; st[-1].update(at=now(), text=f'{t} ×{n}')
            else: st.append({'at': now(), 'kind': kind, 'text': t})
            m['steps'] = st[-STEPS:]; m['said'] = True
        self.update(mid, f); self.alive()

    def _exec(self, a, exe, text, session, eid, mid):
        argv, new = a.command(exe, text, self.workdir(), session, self.writable(), engine.model_of(eid), stream=True)
        if new: self.sessions.put(eid, new)
        log = self.file.parent / 'AI记录' / f'{time.strftime("%Y%m%d-%H%M%S")}-对话-{eid}.jsonl'
        got = {'final': '', 't': 0.0, 'live': ''}

        def flush(force=False):
            if force or time.time() - got['t'] > 0.25:
                got['t'] = time.time(); v = got['live']; self.update(mid, lambda m: m.update(live=v))

        def on(kind, val):
            if kind == 'session': self.sessions.put(eid, val)
            elif kind == 'delta': got['live'] += val; flush()
            elif kind == 'say':
                got['final'] = val; got['live'] = ''
                def f(m):
                    if m.get('text'): m.setdefault('steps', []).append({'at': now(), 'kind': 'say', 'text': engine.first_line(m['text'], 140)})
                    m['text'] = val; m['live'] = ''; m['said'] = True
                self.update(mid, f)
            elif kind == 'step' and val:
                self._step(mid, val)
            elif kind == 'final': got['final'] = val
        self.workdir().mkdir(parents=True, exist_ok=True); self.broker.mkdir(parents=True, exist_ok=True)
        code, err, tail = engine.run_once(a, argv, self.workdir(), a.env(exe, self.job, self.broker), log, on, lambda p: setattr(self, 'proc', p))
        if code is None: return '出错', engine.humane(eid, err), ''
        if self.stopping: return '已停止', '', got['final']
        if code != 0 or err: return '出错', engine.humane(eid, err or '\n'.join(tail), code), got['final']
        return '完成', '', got['final']


class ProjectSessions:
    """项目对话的会话号：和“提交 AI”共用 审片工作台/AI引擎.json 的 sessions（换了引擎就新开，和 Runner 一样）。"""

    def __init__(self, job): self.job = job

    def get(self, eid):
        rec = engine.load(self.job)
        if rec.get('engine_last') and rec.get('engine_last') != eid: return None
        return ((rec.get('sessions') or {}).get(eid) or {}).get('id')

    def put(self, eid, sid):
        rec = engine.load(self.job)
        if rec.get('engine_last') and rec['engine_last'] != eid: rec['sessions'] = {}
        rec.setdefault('sessions', {})[eid] = {'id': sid, 'at': now()}; rec['engine_last'] = eid; engine.save(self.job, rec)

    def drop(self, eid):
        rec = engine.load(self.job); (rec.get('sessions') or {}).pop(eid, None); engine.save(self.job, rec)


class HomeSessions:
    """首页对话的会话号：存在首页对话记录里。"""

    def __init__(self, chat): self.chat = chat

    def get(self, eid):
        d = self.chat.load()
        if d.get('engine_last') and d['engine_last'] != eid: return None
        return ((d.get('sessions') or {}).get(eid) or {}).get('id')

    def put(self, eid, sid):
        def f(d):
            if d.get('engine_last') and d['engine_last'] != eid: d['sessions'] = {}
            d.setdefault('sessions', {})[eid] = {'id': sid, 'at': now()}; d['engine_last'] = eid
        self.chat.edit(f)

    def drop(self, eid): self.chat.edit(lambda d: (d.get('sessions') or {}).pop(eid, None))


def for_project(job, runner=None, alive=lambda: None):
    d = job.dir / '审片工作台'
    c = Chat('project', d / FILE, lambda: engine.workdir(job), lambda: engine.writable_dirs(job), d / '代办-对话', ProjectSessions(job),
             job=job, runner=runner, alive=alive)
    return c


def home_dir():
    """首页对话放哪：程序数据文件夹 ThinkCut/首页对话（THINKCUT_HOME_CHAT 可换，测试用）。"""
    if os.environ.get('THINKCUT_HOME_CHAT'): return Path(os.environ['THINKCUT_HOME_CHAT'])
    return engine.settings_file().parent / '首页对话'


def for_home(roots, open_job, alive=lambda: None, current=lambda: (None, None)):
    """current()：首页现在看的 (IP, 片子文件夹)——开项建在这里。"""
    d = home_dir(); work = d / '工作区'
    c = Chat('home', d / FILE, lambda: work, lambda: [work, Path(tempfile.gettempdir())], d / '代办', None, roots=roots, alive=alive, open_job=open_job, current=current)
    c.sessions = HomeSessions(c)
    return c


def broker_roots_home(c):
    """首页对话的代办收哪些路径：工作区、临时目录、各个放项目的文件夹（开项、存档要写到那里；代办只跑 project.py 和挪进回收站）。"""
    return [c.workdir(), Path(tempfile.gettempdir()), *[Path(r) for r in c.roots()]]
