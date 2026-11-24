# 审片台（2026-11-22 用户定，审片工作台扩成的）：一条片子一个本机网页，所有要用户看、选、确认、改字的东西都在上面。
# 左边按做片子的顺序 7 个栏目；AI 用命令把一项“发布”到某个栏目（图、试听、多选一），用户在页面上点“可以”“选这个”、写要求、直接改字；
# 用户点的就是确认（记下谁、什么时候、点了什么、对应哪一版文件的哈希），AI 用 workbench --list 读、--reply 回。
# 审核线那一栏是原来的审片工作台（标记.json，workbench.py 管）；本文件管其余栏目：审片台.json、发布的文件、确认、改字。
# 规则：确认只能来自页面（命令行只能发布和回复，页面请求要带本页面的来源和口令，见 workbench.py）；动画样张/草图的确认同时记进 合成/确认记录.json，
# 和以前 anim-confirm 记的一样，出片拦截（anim.gate）认它。说明见 references/审片工作台.md。
import hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path
from .common import Job, THINKCUT_DIR, die, rj, wj, say

COLUMNS = ('写稿', '文案', '口播试听', '动画', '审核线', '导航文字', '封面', '上传文案')
OPTIONAL = ('口播试听',)     # 2026-11-22 用户试用：“这干嘛的……不要也行吧”——只有 AI 发布了要用户听的东西时才出现，平时不占位置
WRITE = '写稿'               # 第一栏“文案”（2026-11-24 用户定）：命令、数据里叫 写稿（老栏目名 文案 页面上是“口播剪辑”，老数据在用）；这一栏的事在 writing.py
PREFIX = {'文案': '文', '口播试听': '听', '动画': '动', '导航文字': '导', '封面': '封', '上传文案': '传'}   # 项的编号：栏目一个字＋数字
KINDS = ('多选一', '可以', '试听', '改字')
IMAGE = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
AUDIO = ('.m4a', '.mp3', '.wav', '.aac')
VIDEO = ('.mp4', '.mov', '.webm', '.m4v')        # 2026-11-22 反馈单（261122 别墅大平层）：动画直接发视频，不用转动图；页面原样播放、几版并排一起播
WHO = '用户（审片台页面）'
EDIT_MERGE = 600          # 同一处字 10 分钟内接着改，算同一次改动（自动保存一直在存，不每存一下记一条）


def now(): return time.strftime('%Y-%m-%d %H:%M:%S')
def home(job): return job.dir / '审片工作台'          # 数据和原来的审片工作台放一起（老片子的 标记.json 照用）
def board_file(job): return home(job) / '审片台.json'
def store(job): return home(job) / '审片台'


def sha(f):
    h = hashlib.sha256()
    with open(f, 'rb') as x:
        for b in iter(lambda: x.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def load(job):
    f = board_file(job); d = rj(f) if f.exists() else {}
    d.setdefault('说明', '审片台的项（发布的图、试听、多选一）和用户改的字（thinkcut_edit.py workbench）。格式见 skill/thinkcut-edit/references/审片工作台.md')
    d.setdefault('schema', 1); d.setdefault('items', []); d.setdefault('edits', []); d.setdefault('drafts', {})
    return d


def edit(job, fn):
    """和 标记.json 同一把文件锁：锁住 → 读 → 改 → 写回。"""
    from .workbench import _flock
    with _flock(job):
        d = load(job); out = fn(d); wj(board_file(job), d); return out


class Oops(Exception):
    """给页面/命令行看的中文错误。"""


def live(items):
    """没撤回的项（撤回的留在 审片台.json 里当记录，页面和 --list 都不显示）。"""
    return [x for x in items if not x.get('撤回')]


def visible_columns(items):
    """页面左边显示哪些栏目：可选栏目（口播试听）只在有没撤回的项时显示。"""
    on = {x['column'] for x in live(items)}
    return tuple(c for c in COLUMNS if c not in OPTIONAL or c in on)


def withdraw(job, iid, note=''):
    """撤回一项（AI 发布错了、或已经用不上了，例如用户已经在对话里定了）。不删，只标记，页面和 --list 不再显示。"""
    def fn(d):
        it = item(d, iid); it['撤回'] = {'at': now(), 'note': note or ''}; return it['title']
    title = edit(job, fn); print(f'已撤回 {iid}（{title}）' + (f'：{note}' if note else '')); return title


def item(d, iid):
    it = next((x for x in d['items'] if x['id'] == iid), None)
    if not it: raise Oops(f'没有 {iid} 这一项')
    return it


# ---------- 项的状态 ----------
def state(it):
    """(给用户看的状态, AI 还没处理吗)。状态：等你看／你已定／AI 改中／AI 已回复。
    按事件顺序推：发布 → 等你看（用户写了要求之后再发布 → AI 已回复）；用户点可以/选这个 → 你已定；用户写要求 → AI 改中；AI 回复 → AI 改中变 AI 已回复，你已定不变。
    AI 还没处理：最后一件事是用户做的（点了、选了、写了要求）。"""
    st, last_user = '等你看', False
    for e in it['events']:
        t = e['type']
        if t == 'publish': st = 'AI 已回复' if st == 'AI 改中' else '等你看'; last_user = False
        elif t == 'reply': st = 'AI 已回复' if st == 'AI 改中' else st; last_user = False
        elif t == 'user':
            last_user = True
            st = 'AI 改中' if e['action'] == '写要求' else '你已定'
    return st, last_user


def latest(it): return it['versions'][-1]


# ---------- 发给 AI（2026-11-22 用户定：“我写好了这个修改页，我一点发送给AI，他就直接把这个问题都反馈给AI，然后他就开始改了”） ----------
# 以前用户写完要求页面就显示“AI 改中”，可 AI 要等对话里有人叫它 --list 才读得到：261122 那条片子导航、封面、一条标记写了没人改。
# 现在：用户点、写的都先存着（todo_keys 里每件事一个键，内容变了键就变）；点页面顶上的“发给 AI”记一次发送（send）；
# AI 那边挂着 workbench --wait 的，命令马上打印清单并退出（Claude Code 后台命令退出会叫醒 AI）；AI 读过（--wait 或 --list）记 picked。
def _h(x): return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]


def todo_keys(job):
    """AI 还没处理的事 → {键: (栏目, 页面上的编号)}：其余栏目里最后一件事是用户做的项、用户改的字、审核线这一轮待处理的标记、用户改的导航条文字。"""
    from . import workbench as W
    d = load(job); out = {}
    for it in live(d['items']):
        if state(it)[1]:
            e = it['events'][-1]; out[f"{it['id']}@{_h([e.get('at'), e.get('action'), e.get('pick'), e.get('text')])}"] = (it['column'], it['id'])
    for e in d['edits']:
        if not e.get('handled'): out[f"{e['id']}@{_h([e.get('at'), e.get('new')])}"] = (e['column'], e['id'])
    m = W.load(job)
    for x in m['markers']:
        if x['round'] == m['round'] and x['status'] == '待处理' and (x.get('text') or x.get('frame') or x.get('imgs')):
            th = x.get('thread')     # 用户回答了 AI 的回问：算新的一件（老标记没有 thread，键和以前一样）
            out[f"#{x['id']}@{_h([x.get('text'), x.get('frame'), x.get('imgs'), x.get('t'), x.get('t_end')] + ([len(th)] if th else []))}"] = ('审核线', f"#{x['id']}")
    # 用户改的导航条文字：导航条已经是这些字了（AI 照改完）或 AI 回复过（--reply 导航条）就算处理完，不再挂着（261122：改完没法标，页面一直“1 条没发给 AI”）
    try: now_labels = {c['id']: c['label'] for c in W.chapters(job)}
    except Exception: now_labels = {}
    if any(k in now_labels and now_labels[k] != v for k, v in m['nav_labels'].items()) and (m.get('nav_handled') or {}).get('key') != _h(m['nav_labels']):
        out[f"导航条@{_h(m['nav_labels'])}"] = ('导航文字', '导航条')
    from . import writing as X                    # 文案栏（写稿）：用户提的意见（待处理）、点了“定稿”AI 还没回话
    try:
        for n in X.load(job)['notes']:
            if n['status'] == '待处理' and n['text']: out[f"稿{n['id']}@{_h([n['text'], n['quote'], len(n['thread'])])}"] = (WRITE, f"稿{n['id']}")
        fk = X.final_key(job, d)
        if fk: out[fk] = (WRITE, '定稿')
        kk = X.kick_key(job, d)
        if kk: out[kk] = (WRITE, '开工')
    except Exception: pass
    try:                                          # “发布”栏：用户点了“存到蚁小二草稿”、AI 还没回话（2026-11-24）
        from . import publish as P; pk = P.key(job, d)
        if pk: out[pk] = ('上传文案', '发布')
    except Exception: pass
    return out


LABEL = {'写稿': '文案', '文案': '口播剪辑'}          # 页面上的叫法（命令、数据里的栏目名不变）
SETTLE = ('写稿', '动画', '审核线', '导航文字', '封面', '上传文案')     # 能点“这一栏定了”的栏目（口播剪辑栏的确认是“保存到达芬奇”）


# ---------- 这一栏定了（2026-11-22 用户：“‘已定’是怎么确定的？……是不是应该有个标签或者有方法能让我确定啊？”） ----------
# 以前栏目下面的“已定”只是“没有等你看的、也没有等 AI 的”，不是用户确认过。现在栏目写“你已定”只有两种情况：
# 这一栏的每一项用户都点过“可以／选这个”；或用户点了“这一栏定了”（记时间；之后这一栏的内容一变就不算了，要重新点）。
def col_sig(job, column):
    """这一栏现在的内容指纹（用户看到、要定的东西）：变了，之前点的“这一栏定了”就不算。AI 只回一句话、不换新版不算变。"""
    from . import workbench as W
    d = load(job); sig = []
    for it in live(d['items']):
        if it['column'] != column: continue
        u = next((e for e in reversed(it['events']) if e['type'] == 'user'), {})
        sig.append([it['id'], latest(it)['v'], u.get('action'), u.get('pick'), u.get('text')])
    sig.append([[e['id'], e.get('new')] for e in d['edits'] if e['column'] == column])
    if column == '审核线':
        m = W.load(job); sig.append([m['round'], job.get('review_timeline', ''), [[x['id'], x['status'], x.get('text')] for x in m['markers'] if x['round'] == m['round']]])
    if column == '导航文字':
        m = W.load(job)
        try: labels = [[c['id'], m['nav_labels'].get(c['id'], c['label'])] for c in W.chapters(job)]
        except Exception: labels = []
        sig.append(labels)
    if column == WRITE:
        from . import writing as X; sig.append(X.sig(job))
    if column == '上传文案':
        try: u = upload_info(job); sig.append([u.get('title'), u.get('description'), u.get('tags')])
        except Exception: pass
    return _h(sig)


OWN_TEXT = ('导航文字', '上传文案')     # 这两栏的字是用户自己在文本框里写的：点“这一栏定了”直接定；只把自己写的字发给 AI 也算定了


def _waiting(d, column): return [x for x in live(d['items']) if x['column'] == column and state(x)[0] in ('等你看', 'AI 已回复')]


def _asked(d, column):
    """这一栏里用户写了要求、AI 还没改完的项（那是让 AI 改，不是定了）。"""
    return [x['id'] for x in live(d['items']) if x['column'] == column and state(x) == ('AI 改中', True)]


def settle_info(job, waiting=None):
    """给页面：每一栏 {state: 'ok' 你定了（at 时间）／'can' 现在可以点／'no' 还不能点（why）}。
    导航文字、上传文案（字是用户自己写的）：只要没有“让 AI 改”的要求、没有要选的多选一就能点，点了顺带把等你看的卡片确认掉、把写的字发给 AI。
    其余栏目：还有等你看的、没发给 AI 的、AI 在处理的时不能点。"""
    d = load(job); rec = d.get('settled') or {}; snd = send_info(job)['cols']; out = {}
    for c in SETTLE:
        r = rec.get(c)
        if r and r.get('sig') == col_sig(job, c): out[c] = {'state': 'ok', 'at': r['at'], **({'note': r['handled']['note']} if r.get('handled') else {})}; continue
        wait = _waiting(d, c); busy = snd.get(c) or {}
        if c == WRITE:
            from . import writing as X; why = X.settle_why(job, busy)
        elif c in OWN_TEXT:
            why = ('你写了要求让 AI 改：等它改完你看过再定' if _asked(d, c) else
                   '这一栏有几个方案要你选：先点“选这个”' if any(latest(x)['kind'] == '多选一' for x in wait) else '')
        else:
            # 2026-11-24 用户：“交给AI和确定、不确定不应该是绑定的……没有交给AI的情况下，我也可以点确定”：还没交给 AI 的不再挡“这一栏定了”（定的时候一起交）
            why = (f'这一栏还有 {len(wait)} 项等你看：先点“可以”“选这个”或写要求' if wait else
                   'AI 还在处理这一栏：等它改完你看过再定' if busy.get('sent') or busy.get('doing') else '')
        out[c] = {'state': 'no', 'why': why} if why else {'state': 'can'}
    return out


def _mark_settled(job, column):
    sig = col_sig(job, column)
    def g(d): d.setdefault('settled', {})[column] = {'at': now(), 'sig': sig, 'who': WHO}
    edit(job, g)


def settle(job, column, on=True, ua=''):
    """用户点“这一栏定了”／取消（只收页面的请求）。导航文字、上传文案：顺带替用户把等你看的卡片点成“可以”（他直接改字就是对卡片的回答），
    并把这一栏写的字、点的确认发给 AI。"""
    if column not in SETTLE: raise Oops('这一栏不用点“定了”')
    if not on:
        def g(d): (d.get('settled') or {}).pop(column, None); return {'ok': True, 'column': column, 'on': False}
        return edit(job, g)
    info = settle_info(job)[column]
    if info['state'] == 'no': raise Oops(info['why'])
    sent = 0
    if column not in OWN_TEXT and column != WRITE and any(c == column for c, _ in todo_keys(job).values()):
        try: sent = send(job, column, settle_after=False)['n']           # 你点的、写的还没交给 AI：定的时候一起交（AI 才知道你选了哪个方向）
        except Oops: sent = 0
    if column in OWN_TEXT:
        for it in _waiting(load(job), column): act(job, {'id': it['id'], 'v': latest(it)['v'], 'action': '可以'}, ua)
        if any(c == column for c, _ in todo_keys(job).values()):
            try: sent = send(job, column, settle_after=False)['n']
            except Oops: sent = 0
    _mark_settled(job, column)
    if column == WRITE:
        from . import writing as X; X.on_settle(job)       # 文案定稿＝一件交给 AI 的事（--wait 打印“文案定稿了：按初稿模式出初稿”）
    return {'ok': True, 'column': column, 'on': True, 'sent': sent}


def _sent(d):
    """已发给 AI 的事 {键: {at: 什么时候发的, picked: AI 什么时候接的}}（2026-11-22 当天早些时候的老格式 send.keys 也认）。"""
    if isinstance(d.get('sent'), dict): return d['sent']
    s = d.get('send') or {}
    return {k: {'at': s.get('at'), 'picked': s.get('picked')} for k in s.get('keys') or []}


def send_info(job):
    """给页面：每件还没处理的事走到哪一步——unsent 还没发给 AI／sent 已发、AI 还没接／doing AI 接了在处理；按页面编号（ids）和按栏目（cols）各一份。
    2026-11-22 用户定：在哪一栏点“发给 AI”就只发这一栏，“总是要给我一个状态”。"""
    keys = todo_keys(job); sent = _sent(load(job)); ids = {}; cols = {}
    for k, (c, i) in keys.items():
        r = sent.get(k); st = 'unsent' if not r else 'doing' if r.get('picked') else 'sent'
        ids[i] = st; cols.setdefault(c, {'unsent': 0, 'sent': 0, 'doing': 0})[st] += 1
    return {'pending': len(keys), 'unsent': sum(v['unsent'] for v in cols.values()), 'ids': ids, 'cols': cols,
            'unsent_ids': sorted(i for i, x in ids.items() if x == 'unsent')}


def send(job, column, settle_after=True):
    """用户在某一栏点“发给 AI”（只收页面的请求）：只发这一栏还没处理的事。发了 AI 还没接的，再点一次就是再叫一遍。
    导航文字、上传文案：发的都是用户自己写好的字（没有让 AI 改的要求、没有等你看的卡片）时，这一栏同时记成“你定了”（用户：“我发给AI基本上也就定了，因为内容都是我写完了的”）。"""
    if column not in COLUMNS: raise Oops('不认识的栏目')
    keys = todo_keys(job); mine = [k for k, (c, _) in keys.items() if c == column]; name = LABEL.get(column, column)
    if not mine: raise Oops(f'“{name}”这一栏现在没有要 AI 处理的：先点“可以”“选这个”、写要求、改字或记标记')
    at = now()
    def g(d):
        sent = {k: v for k, v in _sent(d).items() if k in keys}          # 已经处理完的不留
        go = [k for k in mine if not (sent.get(k) or {}).get('picked')]
        if not go: raise Oops(f'“{name}”这一栏的都已经发给 AI、它接了在处理')
        for k in go: sent[k] = {'at': at, 'picked': None}
        d['sent'] = sent; d.pop('send', None)
        d.setdefault('sends', []).append({'at': at, 'column': column, 'n': len(go), 'who': WHO}); d['sends'] = d['sends'][-50:]
        return {'ok': True, 'n': len(go), 'column': column}
    out = edit(job, g)
    if settle_after and column in OWN_TEXT:
        d = load(job)
        if not _waiting(d, column) and not _asked(d, column): _mark_settled(job, column); out['settled'] = True
    return out


def pick(job):
    """AI 接了（--wait 等到了，或 --list）：已发、还没接的都记上时间。返回这次接到的 [(栏目, 页面编号)]，没有就是 []。"""
    keys = todo_keys(job)
    if not any(k in keys and not v.get('picked') for k, v in _sent(load(job)).items()): return []
    def g(d):
        sent = _sent(d); got = []; at = now()
        for k, v in sent.items():
            if k in keys and not v.get('picked'): v['picked'] = at; got.append(keys[k])
        d['sent'] = sent; d.pop('send', None); return got
    return edit(job, g)


# ---------- 对话（2026-11-22 夜用户定：“AI回复我了，但我要回复AI……给我个地方让我能回复他……可以加截图回复他……他给我几个选择，我可以在他回复里直接点”） ----------
# 每条意见、每张卡片都是一段对话：AI 回过话以后用户随时能回（文字＋图），AI 给的选项画成按钮，点了就是用这个选项回答。
def parse_options(x):
    """--options "A 片尾多留 1 秒|B 不加时长|C 只闪一下" → ['A 片尾多留 1 秒', …]；竖线分（全角竖线也认），至少 2 个，不能重复。"""
    opts = [o.strip() for o in re.split(r'[|｜]', str(x or '')) if o.strip()]
    if len(opts) < 2: raise Oops('--options 至少要 2 个选项，用竖线 | 分开：如 "A 片尾多留 1 秒|B 不加时长、提前出"')
    if len(set(opts)) != len(opts): raise Oops('--options 里有重复的选项')
    if any(len(o) > 80 for o in opts): raise Oops('--options 每个选项写成一句大白话（80 字以内）')
    return opts


_OPT = (r'[A-H]', r'[1-9]', r'[①-⑨]')


def auto_options(text):
    """AI 没给 --options 时，从它的话里拆选项按钮：“A：…；B：…；C：…”“1. … 2. …”“①…②…”这种（从 A／1／① 起连续、至少 2 个）。拆不准就返回 []。"""
    t = str(text or '')
    for pat in _OPT:
        marks = [m for m in re.finditer(rf'(?:(?<=^)|(?<=[\s；;。，,、：:（(？?！!”"]))({pat})[：:.．、)）]\s*', t, re.M)]   # 字母、数字后面要跟冒号／点／顿号／括号，免得把“A 是对的”当选项
        if pat == _OPT[2]: marks = [m for m in re.finditer(r'([①-⑨])\s*', t)]
        if len(marks) < 2: continue
        first = {'[A-H]': 'A', '[1-9]': '1', '[①-⑨]': '①'}[pat]; seq = [m[1] for m in marks]
        # 取从 first 开始的连续一串（A B C …）
        k = next((i for i, c in enumerate(seq) if c == first), None)
        if k is None: continue
        run = [marks[k]]
        for m in marks[k + 1:]:
            if ord(m[1]) == ord(run[-1][1]) + 1: run.append(m)
            elif ord(m[1]) == ord(first): break
        if len(run) < 2: continue
        out = []
        for i, m in enumerate(run):
            end = run[i + 1].start() if i + 1 < len(run) else len(t)
            seg = re.split(r'[。？?！!\n]', t[m.start():end])[0]      # 选项到句号、问号、换行为止（后面常跟“你看选哪个？”）
            seg = re.sub(r'[\s；;。，,]+$', '', seg.strip())
            if not seg or len(seg) > 120: return []
            out.append(seg)
        if len(out) >= 2: return out
    return []


def reply_img(job, q):
    """其余栏目卡片“回复 AI”带的图（⌘V 贴的、拖进来的）：先存进 审片台/<编号>/回复/，回复时带着这些名字。"""
    import base64
    iid = str(q.get('id', '')); item(load(job), iid)
    mt = re.match(r'data:image/(png|jpeg|gif|webp);base64,(.+)', q.get('data') or '', re.S)
    if not mt: raise Oops('只能放图片')
    d = store(job) / iid / '回复'; d.mkdir(parents=True, exist_ok=True)
    f = d / f"你贴的_{time.strftime('%m%d-%H%M%S')}_{os.urandom(2).hex()}.{'jpg' if mt[1] == 'jpeg' else mt[1]}"; f.write_bytes(base64.b64decode(mt[2]))
    return {'ok': True, 'name': str(f.relative_to(home(job)))}


def item_opts(it):
    """卡片最后一件事是 AI 回的话时，给页面画的选项按钮（--options 给的优先，其次从话里拆）。"""
    e = it['events'][-1] if it['events'] else None
    if not e or e['type'] != 'reply': return []
    return e.get('options') or auto_options(e.get('text'))


# ---------- 给用户看的 4 个状态（2026-11-22 用户定：“总是要给我一个状态吧，我觉得得有个逻辑在吧”；审片台 v2） ----------
# 页面上全站只用这 4 个词：待你看（AI 发来新东西、新版或回复）／待发送（你点了、写了，还没交给 AI）／AI 在改（已交给 AI，不管它接没接）／已定（你点了“可以”“选这个”或“这一栏定了／定稿”）。
# 没事做的是 ''（不挂标签）。这只是显示层的归并：state()、send_info()、settle_info() 和数据文件都不变，发出／接收／处理这些细节照旧留在数据里。
USTATES = ('待你看', '待发送', 'AI 在改', '已定')
MARK_KINDS = ('字幕', '花字', '动画', 'B-roll', '节奏', '声音', '其它')     # 审核线标记的类型（标记.json 的 kind，可以不选）：让 AI 读到有结构的意见


def ustate_item(it, ids):
    """其余栏目的一项 → 4 个状态之一。ids：send_info()['ids']（unsent／sent／doing）。"""
    s = ids.get(it['id'])
    if s == 'unsent': return '待发送'
    if s in ('sent', 'doing'): return 'AI 在改'
    st = state(it)[0]
    if st in ('等你看', 'AI 已回复'): return '待你看'
    if st == '你已定': return '已定'
    return 'AI 在改'


def ustate_marker(m, cur_round, ids, settled):
    """审核线的一条标记 → 4 个状态之一；以前轮次的、还没写字的是 ''。AI 回了（已改／没改）是待你看；用户点了“这一栏定了”就是已定。"""
    if m['round'] != cur_round: return ''
    s = ids.get(f"#{m['id']}")
    if s == 'unsent': return '待发送'
    if s in ('sent', 'doing'): return 'AI 在改'
    if m.get('status') == '问你': return '待你看'          # AI 回问了：等用户在卡片上回答
    if m.get('status') in ('已改', '没改'): return '已定' if settled else '待你看'
    return ''


def ucolumn(counts, settled, item_states, off=False):
    """一栏的状态：还没到（off）→ ''；有待你看的 → 待你看；有待发送的 → 待发送；用户定了 → 已定；AI 在改；这一栏每一项都已定 → 已定；其余 ''。"""
    if off: return ''
    if counts.get('待你看'): return '待你看'
    if counts.get('待发送'): return '待发送'
    if settled: return '已定'
    if counts.get('AI 在改'): return 'AI 在改'
    if item_states and all(s == '已定' for s in item_states): return '已定'
    return ''


def column_badge(col, items, extra):
    """栏目旁边的字：等你看 N 项／AI 改中／已定／还没到（灰）。extra：这一栏有没有可以直接改的字（文案、导航、上传文案）。"""
    sts = [state(x)[0] for x in items if x['column'] == col]
    n = sum(s in ('等你看', 'AI 已回复') for s in sts)
    if n: return {'text': f'等你看 {n} 项', 'kind': 'wait', 'n': n}
    if not sts and not extra: return {'text': '还没到', 'kind': 'none', 'n': 0}
    if any(s == 'AI 改中' for s in sts): return {'text': 'AI 改中', 'kind': 'ai', 'n': 0}
    return {'text': '已定', 'kind': 'done', 'n': 0}


# ---------- 发布（AI 用命令） ----------
def _copy_in(job, iid, v, files, labels):
    out = []; dst = store(job) / iid / f'v{v}'; dst.mkdir(parents=True, exist_ok=True)
    for k, (f, lab) in enumerate(zip(files, labels)):
        f = Path(f); name = f'{k + 1}_{f.name}'; shutil.copy2(f, dst / name)       # 拷一份：之后原文件被覆盖（重出样张），卡片历史里还是用户当时看的那张
        out.append({'name': f.name, 'label': lab, 'path': str((dst / name).relative_to(home(job))), 'src': str(f.resolve()), 'sha256': sha(dst / name),
                    'type': 'audio' if f.suffix.lower() in AUDIO else 'video' if f.suffix.lower() in VIDEO else 'image'})
    return out


def _parse_checks(checks, labels):
    """草图逐条对照：'要点=画面里怎么表现' 对每个选项都算；'A:要点=画面' 只算标签以 A 开头的那个选项。返回 {选项标签: [要点=画面, …]}。"""
    out = {l: [] for l in labels}
    for c in checks or []:
        m = re.match(r'^\s*([A-Za-z0-9一二三]{1,3})\s*[:：]\s*(.+=.+)$', c)
        if m:
            hit = [l for l in labels if l.upper().startswith(m[1].upper())]
            if not hit: raise Oops(f'--check “{c}” 前面的 {m[1]} 对不上哪个选项（选项：{"、".join(labels)}）')
            for l in hit: out[l].append(m[2].strip())
        elif '=' in c:
            for l in labels: out[l].append(c.strip())
        else: raise Oops(f'--check 要写成“口播要点=画面里怎么表现”：{c}')
    return out


def _anim_check(job, anim, kind, files, labels, checks):
    """动画项发布前先查：工程在、镜头在分镜规格里；草图每个选项都逐条对照了口播要点（用户点了才不会因为缺东西记不上）。返回存进这一版的动画信息（含当时代码指纹）。"""
    from . import anim as A
    proj = Path(anim['project']).expanduser().resolve(); shots = [s for s in anim['shots'] if s]
    if anim['kind'] not in ('样张', '草图'): raise Oops('动画项只能是 样张 或 草图')
    if not proj.is_dir(): raise Oops(f'动画工程文件夹不存在：{proj}')
    if not shots: raise Oops('动画项要写镜头（--shots）')
    if not any(Path(f).suffix.lower() in IMAGE for f in files): raise Oops(f'{anim["kind"]}要带图片（确认记录要存一张图；视频可以一起发，单独比几版视频就不写 --anim）')
    if anim['kind'] == '样张' and kind != '可以': raise Oops('样张发布成“可以”（--kind 可以）')
    if anim['kind'] == '草图' and kind not in ('多选一', '可以'): raise Oops('草图发布成“多选一”（2–3 张不同画法）')
    info = {'kind': anim['kind'], 'project': str(proj), 'shots': shots,
            'fingerprint': A.fingerprint(job, proj, shots), 'fingerprints': {s: A.fingerprint(job, proj, [s]) for s in shots}}
    if anim['kind'] == '草图':
        doc = A.shots_doc(proj, job.dir)
        if not doc: raise Oops('分镜规格.json 里这些镜头还没写口播要点，先写再发布草图')
        per = _parse_checks(checks, labels)
        for s in shots:
            pts = doc[0].get(s, {}).get('口播要点')
            if not pts or not isinstance(pts, list): raise Oops(f'{s} 在 分镜规格.json 里还没写口播要点（要写成列表），先写再发布草图')
            for lab, cs in per.items():
                got = {c.split('=', 1)[0].strip() for c in cs}; miss = [p for p in pts if p not in got]
                if miss: raise Oops(f'草图选项“{lab}”还没逐条对照口播要点，缺：' + '；'.join(miss) + '（--check "要点=画面里怎么表现"，只对某个选项的写 "A:要点=画面"）')
        info['checks'] = per
    return info


def audio_of(video):
    """试听给的是视频（机器配音的 配音.mov、口播成片）时，抽出音轨成 mp3 再发（2026-11-23 初稿模式实测：--kind 试听 给 .mov 被拦，AI 只好自己转）。
    没有音轨就停。抽出来的放系统临时目录，发布时照常拷进审片台。"""
    import tempfile
    out = Path(tempfile.mkdtemp(prefix='试听_')) / (Path(video).stem + '.mp3')
    r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(video), '-vn', '-c:a', 'libmp3lame', '-q:a', '2', str(out)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode or not out.is_file() or not out.stat().st_size:
        raise Oops(f'试听要带音频：{Path(video).name} 里抽不出声音（没有音轨？）{(r.stderr or "").strip()[-200:]}')
    return out


def publish(job, column, title, look, kind, files=(), labels=None, key=None, iid=None, note='', anim=None, checks=None, assume='', draft=False, placeholder=False):
    """发布一项给用户看（AI 用）。同一项（iid 或 key 相同）再发布＝换新版，旧版留在卡片历史里。返回这一项。
    初稿模式（2026-11-22 用户定“先做出来再改”）：draft＝这一版是 AI 没问用户、按自己判断做的初稿；placeholder＝里面是占位的东西（机器配音、生成图）；
    assume＝“我是这么假设的：……”（AI 拿不准时不问，按最合理的做并写在这里）。三样只是卡片上的小标签和一句话，不是第五种状态。"""
    if column not in COLUMNS: raise Oops(f'栏目只能是：{"、".join(COLUMNS)}')
    if column == WRITE: raise Oops('“文案”栏（写稿）不发布项：用户在页面上写、提意见，AI 用 --rewrite 稿N --json 改写.json 交改写建议（要用户过一眼老文案用 --publish 文案 --kind 改字）')
    if column == '审核线': raise Oops('审核线那一栏是用户在达芬奇里看片、在全文上打标记，不发布项')
    if kind not in KINDS: raise Oops(f'类型只能是：{"、".join(KINDS)}')
    files = [Path(f).expanduser() for f in files if str(f).strip()]
    for f in files:
        if not f.is_file(): raise Oops(f'文件不存在：{f}')
        if f.suffix.lower() not in IMAGE + AUDIO + VIDEO: raise Oops(f'只能发图片（{"/".join(IMAGE)}）、视频（{"/".join(VIDEO)}）或音频（{"/".join(AUDIO)}）：{f.name}')
    if column == '上传文案' and files:      # 2026-11-22 用户：“你不用非得给我一张截图，你就直接写在里边，有问题我直接在里边改了，或者我直接给你写要求”
        raise Oops('上传文案栏不发截图：标题、简介、标签就在这一栏的文本框里（读的是 项目.json），用户直接改；要用户过目就发 --kind 改字（不带 --files）')
    if kind == '多选一' and len(files) < 2: raise Oops('多选一至少要 2 个选项（--files 两个以上）')
    if kind == '试听': files = [audio_of(f) if f.suffix.lower() in VIDEO else f for f in files]      # 2026-11-23 初稿模式实测：机器配音给的是 .mov，自动抽出音轨
    if kind == '试听' and not any(f.suffix.lower() in AUDIO for f in files): raise Oops('试听要带音频（新旧对比就是两段）；给视频也行，会自动抽出里面的声音')
    if kind in ('可以', '多选一') and not files: raise Oops(f'{kind}要带图片、视频或音频')
    if not title or not look: raise Oops('要写标题（--title）和“看哪里、确认什么”（--look）')
    labels = list(labels or [])
    if labels and len(labels) != len(files): raise Oops(f'--labels 要和文件一样多（{len(files)} 个）')
    if not labels: labels = [chr(65 + k) for k in range(len(files))] if kind == '多选一' else [f.stem for f in files]
    if kind == '多选一' and len(set(labels)) != len(labels): raise Oops('多选一的选项名不能重复')
    ainfo = _anim_check(job, anim, kind, files, labels, checks) if anim else None

    def f(d):
        it = None
        if iid: it = item(d, iid)
        elif key: it = next((x for x in d['items'] if x.get('key') == key), None)
        if it and it['column'] != column: raise Oops(f'{it["id"]} 在“{it["column"]}”栏，不在“{column}”栏')
        if not it:
            n = 1 + max([int(re.sub(r'\D', '', x['id']) or 0) for x in d['items'] if x['id'].startswith(PREFIX[column])] or [0])
            it = {'id': f'{PREFIX[column]}{n}', 'column': column, 'key': key, 'created': now(), 'versions': [], 'events': []}
            d['items'].append(it)
        v = len(it['versions']) + 1
        ver = {'v': v, 'at': now(), 'title': title, 'look': look, 'kind': kind, 'note': note or '', 'files': _copy_in(job, it['id'], v, files, labels)}
        if ainfo: ver['anim'] = ainfo
        if assume and assume.strip(): ver['assume'] = assume.strip()
        if draft: ver['draft'] = True
        if placeholder: ver['placeholder'] = True
        it['versions'].append(ver); it['events'].append({'type': 'publish', 'v': v, 'at': ver['at'], 'note': note or ''})
        it.update(title=title, kind=kind); return it
    return edit(job, f)


def reply(job, iid, note, files=None, labels=None, options=None):
    """AI 回复一项（像标记的 --reply）。带文件＝改完换新版（旧版留历史）；不带＝只回一句话。options：给用户点的选项按钮（--options）。"""
    if not note: die('回复要带 --note（改了什么／为什么没改／接下来做什么）')
    try:
        opts = parse_options(options) if options else None
        if opts and files: raise Oops('--options 是让用户挑做法的：不要和 --files（换新版）一起用')
        if files:
            d = load(job); it = item(d, iid); last = latest(it)
            a = last.get('anim'); anim = {'kind': a['kind'], 'project': a['project'], 'shots': a['shots']} if a else None
            checks = [f'{lab}:{c}' for lab, cs in (a or {}).get('checks', {}).items() for c in cs] if a and a.get('checks') and not labels else None
            publish(job, it['column'], it['title'], last['look'], it['kind'], files, labels, iid=iid, note=note, anim=anim, checks=checks)
        else:
            def f(d): item(d, iid)['events'].append({'type': 'reply', 'at': now(), 'text': note, **({'options': opts} if opts else {})})
            edit(job, f)
    except Oops as e: die(str(e))
    say(f'{iid} 已回复' + ('（换了新版）' if files else '') + (f'（{len(opts)} 个选项按钮，用户点了会记成回复）' if opts else ''))


def reply_edit(job, eid, note):
    if not note: die('回复要带 --note')
    def f(d):
        e = next((x for x in d['edits'] if x['id'] == eid), None)
        if not e: raise Oops(f'没有 {eid} 这条改字记录')
        e['handled'] = {'at': now(), 'note': note}
    try: edit(job, f)
    except Oops as e: die(str(e))
    say(f'{eid} 已回复')


# ---------- 用户在页面上点的（只能来自页面，见 workbench.py 的口令检查） ----------
def _anim_words(action, pick, at):
    return f'审片台：用户点了可以（{at}）' if action == '可以' else f'审片台：用户选了 {pick}（{at}）'


def _anim_confirm(job, ver, files, pick, words):
    """用户在审片台点了“可以”/“选这个”：记一条和 anim-confirm 一样的确认（合成/确认记录.json），anim.gate 认它。
    发布之后动画代码或镜头起止改过（指纹对不上）就不记：用户看的不是现在的代码。返回 (记上了吗, 说明)。"""
    from . import anim as A
    a = ver['anim']; proj = Path(a['project'])
    if not proj.is_dir(): return False, f'动画工程不在了：{proj}'
    now_fp = {s: A.fingerprint(job, proj, [s]) for s in a['shots']}
    changed = [s for s in a['shots'] if now_fp[s] != a['fingerprints'].get(s)]
    if changed: return False, f'{"、".join(changed)} 的动画在发布之后改过（代码或镜头起止变了），这次确认没记进确认记录：AI 要重新出{a["kind"]}发布'
    checks = None
    if a['kind'] == '草图': checks = a.get('checks', {}).get(pick) or []
    import io, contextlib
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rec = A.confirm(job, a['kind'], proj, a['shots'], [str(home(job) / x['path']) for x in files], words, checks)
    except SystemExit as e: return False, '没记进确认记录：' + str(e).replace('停止：', '')
    return True, f'已记进 合成/确认记录.json（{rec["kind"]}，{rec["at"]}）'


def act(job, q, ua=''):
    """用户在页面上点：可以／选这个／写要求。q = {id, v, action, pick?, text?}。v 要是最新一版（AI 换了新版就要用户看新版再点）。"""
    iid, action, v = str(q['id']), str(q['action']), int(q['v'])
    if action not in ('可以', '选这个', '写要求'): raise Oops('不认识的操作')
    d0 = load(job); it0 = item(d0, iid); ver = latest(it0)
    if v != ver['v']: raise Oops(f'AI 已经换了第 {ver["v"]} 版，先看新版再点')
    kind = ver['kind']; at = now(); pick = None
    if action == '可以' and kind == '多选一': raise Oops('这一项是多选一：在选项下面点“选这个”')
    if action == '选这个':
        if kind != '多选一': raise Oops('这一项不是多选一')
        pick = str(q.get('pick', ''))
        if pick not in [x['label'] for x in ver['files']]: raise Oops('没有这个选项')
    if action == '写要求':
        text = str(q.get('text', '')).strip(); imgs = [str(x) for x in (q.get('imgs') or [])]; picked = str(q.get('picked') or '').strip()
        for x in imgs:
            if not x.startswith(f'审片台/{iid}/回复/') or '..' in x or not (home(job) / x).is_file(): raise Oops('图不对：先贴图、拖图再回复')
        if picked:
            if picked not in item_opts(item(d0, iid)): raise Oops('这个选项已经不在了（AI 又回了话）：刷新看看')
        if q.get('append'):        # 对话里“回复 AI”：每次都是新的一条（可以带图、带点的选项）
            if not (text or imgs or picked): raise Oops('回复是空的')
            def g2(d):
                it = item(d, iid)
                if latest(it)['v'] != v: raise Oops('AI 刚换了新版，先看新版再回复')
                ev = {'type': 'user', 'action': '写要求', 'v': v, 'at': at, 'who': WHO, 'text': text or (f'选了：{picked}' if picked else '（见图）'), 'ua': ua[:120]}
                if imgs: ev['imgs'] = imgs
                if picked: ev['picked'] = picked
                it['events'].append(ev); return {'ok': True}
            return edit(job, g2)
        def g(d):
            it = item(d, iid); last = it['events'][-1]
            if last['type'] == 'user' and last['action'] == '写要求' and last['v'] == v and not last.get('imgs') and not last.get('picked'):      # 还在写同一条要求（自动保存）：改这条
                if text: last.update(text=text, at=at)
                else: it['events'].pop()
            elif text: it['events'].append({'type': 'user', 'action': '写要求', 'v': v, 'at': at, 'who': WHO, 'text': text, 'ua': ua[:120]})
            return {'ok': True}
        return edit(job, g)
    files = [x for x in ver['files'] if pick is None or x['label'] == pick]
    ev = {'type': 'user', 'action': action, 'v': v, 'at': at, 'who': WHO, 'pick': pick, 'files': [{'name': x['name'], 'label': x['label'], 'sha256': x['sha256']} for x in files], 'ua': ua[:120]}
    if ver.get('anim'):
        ok, msg = _anim_confirm(job, ver, files, pick, _anim_words(action, pick, at))
        ev['anim'] = {'ok': ok, 'msg': msg}
    def g(d):
        it = item(d, iid)
        if latest(it)['v'] != v: raise Oops('AI 刚换了新版，先看新版再点')
        it['events'].append(ev); return {'ok': True, 'event': ev}
    return edit(job, g)


# ---------- 能直接改字的：文案、上传文案（导航文字在 workbench.py，和原来一样） ----------
def pm_bin():
    """视频项目管理的程序（script_doc.py、project.py）：环境变量 THINKCUT_PM 优先（开发、测试用），否则在 <程序目录>/视频项目管理/bin。"""
    return Path(os.environ.get('THINKCUT_PM') or THINKCUT_DIR / '视频项目管理' / 'bin')


def _pm(name):
    b = str(pm_bin())
    if b not in sys.path: sys.path.insert(0, b)
    import importlib; return importlib.import_module(name)


def project_dir(job):
    """片子目录在 <项目>/02_制作/自动剪辑 时，项目文件夹（有 项目.json）；找不到返回 None。"""
    for q in job.dir.parents:
        if (q / '项目.json').is_file(): return q
        if q.name == '02_制作': break
    q = job.dir.parent.parent
    return q if (q / '项目.json').is_file() else None


def script_path(job):
    """项目文案：按视频项目管理的规则找（03_成片/<项目名>.md，老项目 02_制作/<项目名>.md）。"""
    pd = project_dir(job)
    if not pd: return None
    try: return _pm('project').script_path(pd)
    except Exception:
        new = pd / '03_成片' / f'{pd.name}.md'; old = pd / '02_制作' / f'{pd.name}.md'
        return old if old.is_file() and not new.exists() else new


SCRIPT_FIELDS = ('标题', '封面文字', '口播正文')


def script_info(job):
    """给页面的文案：三类字、哪类能改（不能改的说原因）。"""
    f = script_path(job)
    if not f or not f.is_file(): return {'path': str(f) if f else '', 'exists': False, 'why': ('找不到项目文件夹（往上没有 项目.json）' if not f else f'还没有项目文案 {f.parent.name}/{f.name}')}
    sd = _pm('script_doc'); text = f.read_text(encoding='utf-8'); parts = sd.parse(text); lines = text.split('\n')
    has_h2 = any(sd.H2.match(l) for l in lines)
    out = {'path': str(f), 'rel': f'{f.parent.name}/{f.name}', 'exists': True, 'fields': {}, 'mtime': f.stat().st_mtime_ns}
    for k in SCRIPT_FIELDS:
        own = has_h2 and sd._span(lines, k) is not None
        why = '' if own or (has_h2 and not parts.get(k)) else ('这份文案是老写法（没有 ## 小标题），先让 AI 整理成标准写法' if not has_h2 else f'「{k}」写在别的小节里（老写法），先让 AI 整理成单独的「## {k}」小节')
        val = sd.section_body(f, k) if (k == '口播正文' and own) else parts.get(k)
        out['fields'][k] = {'value': '\n'.join(val) if isinstance(val, list) else (val or ''), 'why': why}
    return out


def _log_edit(d, column, field, old, new, ok, err=''):
    """记下用户改了什么：同一处字 10 分钟内接着改算一次（old 留最早的）。返回这条记录。"""
    t = time.time()
    last = next((e for e in reversed(d['edits']) if e['column'] == column and e['field'] == field), None)
    if last and not last.get('handled') and t - last.get('ts', 0) < EDIT_MERGE:
        last.update(new=new, at=now(), ts=t, ok=ok, error=err)
        if last['old'] == new and ok: d['edits'].remove(last)           # 改回原样：这次等于没改
        return last
    if old == new: return None
    n = 1 + max([int(e['id'][1:]) for e in d['edits']] or [0])
    e = {'id': f'改{n}', 'column': column, 'field': field, 'old': old, 'new': new, 'at': now(), 'ts': t, 'who': WHO, 'ok': ok, 'error': err, 'handled': None}
    d['edits'].append(e); return e


def set_script(job, field, value, column='文案'):
    """改文案的字：只改 03_成片/<项目名>.md 里对应的那一节（script_doc.set_section），其余一字不动。
    column='写稿'＝页面“文案”栏改的（2026-11-24 起页面只在这一栏改）：还没有文件就建、留版本，见 writing.save。"""
    if column == WRITE:
        from . import writing as X; return X.save(job, field, value)
    if field not in SCRIPT_FIELDS: raise Oops('文案只能改 标题、封面文字、口播正文')
    info = script_info(job)
    if not info['exists']: raise Oops(info['why'])
    if info['fields'][field]['why']: raise Oops(info['fields'][field]['why'])
    old = info['fields'][field]['value']; value = str(value).replace('\r\n', '\n')
    if field == '标题': value = value.strip().replace('\n', '')
    if not value.strip(): raise Oops(f'{field}不能是空的')
    sd = _pm('script_doc')
    if not hasattr(sd, 'set_section'): raise Oops('视频项目管理还没更新到能改文案的版本（script_doc.set_section），先合并')
    val = [x for x in value.split('\n') if x.strip()] if field == '封面文字' else value
    try: sd.set_section(info['path'], field, val)
    except (RuntimeError, ValueError, OSError) as e: raise Oops(str(e))
    edit(job, lambda d: _log_edit(d, '文案', field, old, value, True))
    return {'ok': True}


def upload_info(job):
    """上传文案栏：项目.json 的 title、description、tags（标题以项目文案为准），还没写回的草稿和原因。"""
    pd = project_dir(job)
    if not pd: return {'exists': False, 'why': '找不到项目文件夹（往上没有 项目.json）'}
    meta = rj(pd / '项目.json'); b = load(job)
    doc = {}
    sp = script_path(job)
    if sp and sp.is_file():
        try: doc = _pm('script_doc').read(sp)
        except Exception: doc = {}
    return {'exists': True, 'project': str(pd), 'title': doc.get('标题') or meta.get('title', ''), 'description': meta.get('description', ''), 'tags': meta.get('tags') or [],
            'draft': b['drafts'].get('上传文案'), 'mtime': (pd / '项目.json').stat().st_mtime_ns}


def set_upload(job, q):
    """上传文案栏改简介、标签：用视频项目管理的 copy-apply 写回 项目.json（它的检查照样管：标签 8–10 个、数字只用口播原话、和文案一致）。
    项目文案里已经有「## 简介」「## 标签」的先改文案那一节（copy-apply 要求和文案一致），copy-apply 没过就把文案恢复原样。
    没过检查时字存成草稿（页面上照样显示），说明原因，不写回。"""
    info = upload_info(job)
    if not info['exists']: raise Oops(info['why'])
    desc = str(q.get('description', info['description'])).strip()
    tags = q.get('tags', info['tags'])
    if isinstance(tags, str): tags = [t for t in (x.strip().lstrip('#').strip() for x in re.split(r'[、，,；;\s#]+', tags)) if t]
    old = {'description': info['description'], 'tags': info['tags']}; new = {'description': desc, 'tags': tags}
    pd = Path(info['project']); meta = rj(pd / '项目.json'); sp = script_path(job)
    fields = {'title': info['title'], 'description': desc, 'tags': tags}
    xhs = None
    if sp and sp.is_file():
        try: xhs = _pm('script_doc').read(sp).get('小红书标题')
        except Exception: xhs = None
    xhs = xhs or ((meta.get('platform_extra') or {}).get('小红书') or {}).get('title')
    if xhs: fields['platform_titles'] = {'小红书': xhs}
    backup = sp.read_text(encoding='utf-8') if sp and sp.is_file() else None
    err = ''
    try:
        if backup is not None:
            sd = _pm('script_doc'); lines = backup.split('\n')
            for name, val in (('简介', desc), ('标签', tags)):
                if sd._span(lines, name) is not None and val: sd.set_section(sp, name, val); lines = sp.read_text(encoding='utf-8').split('\n')
        tmp = home(job) / 'tmp' / '上传文案.json'; wj(tmp, fields)
        r = subprocess.run([sys.executable, '-B', str(pm_bin() / 'project.py'), 'copy-apply', str(pd), '--file', str(tmp)], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        if r.returncode: err = (r.stderr.strip() or r.stdout.strip()).replace('停止：', '').replace('定稿没写进项目.json：', '').strip()
    except (RuntimeError, OSError, subprocess.SubprocessError) as e: err = str(e)
    if err and backup is not None and sp.read_text(encoding='utf-8') != backup: sp.write_text(backup, encoding='utf-8')
    def g(d):
        if err: d['drafts']['上传文案'] = {**new, 'error': err, 'at': now()}
        else: d['drafts'].pop('上传文案', None)
        for k in ('description', 'tags'):
            if new[k] != old[k] or err: _log_edit(d, '上传文案', k, old[k], new[k], not err, err)
    edit(job, g)
    return {'ok': not err, 'error': err, 'saved': not err}


# ---------- 读结果（AI 用 workbench --list） ----------
def convo_lines(it, base=''):
    """这一版发布以后的对话（AI 回的话、给的选项；用户的回复、点的选项、带的图），给 AI 读。"""
    ver = latest(it); k = max([i for i, e in enumerate(it['events']) if e['type'] == 'publish' and e['v'] == ver['v']] or [-1])
    ev = it['events'][k + 1:]
    if not any(e['type'] == 'reply' for e in ev): return []
    out = []
    for e in ev:
        if e['type'] == 'reply': out.append(f"   对话 {e['at'][5:16]} AI：{e['text']}" + (f"｜给的选项：{' | '.join(e['options'])}" if e.get('options') else ''))
        elif e['type'] == 'user':
            w = e['text'] if e['action'] == '写要求' else f"点了“{e['action']}”" + (f"：{e['pick']}" if e.get('pick') else '')
            out.append(f"   对话 {e['at'][5:16]} 用户：{w}" + (f"｜选了：{e['picked']}" if e.get('picked') else '') + (f"｜图 {'、'.join(f'{base}/{x}' for x in e['imgs'])}" if e.get('imgs') else ''))
    return out


def listing(job, all_items=False, only=None):
    """only：只列这些编号（用户这次发来的那几件）。有对话的项下面接着列对话（图的路径相对 审片工作台/）。"""
    d = load(job); rows = []
    for it in live(d['items']):
        st, todo = state(it)
        if not (all_items or todo) or (only is not None and it['id'] not in only): continue
        ver = latest(it); last = it['events'][-1]
        s = f"[{it['id']}] {it['column']}｜{it['title']}｜第 {ver['v']} 版｜{st}"
        users = [e for e in it['events'] if e['type'] == 'user']
        if todo and last['type'] == 'user':
            u = last
            if u['action'] == '写要求': s += f"｜用户写的要求（第 {u['v']} 版）：{u['text']}"
            else:
                s += f"｜用户{u['at']} 点了“{u['action']}”" + (f"：选 {u['pick']}" if u.get('pick') else '') + '（' + '、'.join(f"{x['label']} {x['sha256'][:12]}" for x in u['files']) + '）'
                if u.get('anim'): s += ('｜' if u['anim']['ok'] else '｜【注意】') + u['anim']['msg']
            if u['action'] != '写要求':
                pf = [x for x in latest(it)['files'] if u.get('pick') in (None, x['label'])]
                s += '｜文件 ' + '、'.join(x['src'] for x in pf)
        elif all_items and users:
            u = users[-1]; s += f"｜用户最后：{u['action']}" + (f" {u.get('pick')}" if u.get('pick') else '') + (f" {u.get('text')}" if u.get('text') else '')
        rows.append(s)
        rows.extend(convo_lines(it, home(job)))
    eds = [e for e in d['edits'] if (all_items or not e.get('handled')) and (only is None or e['id'] in only)]
    say(f"审片台其余栏目：{'全部' if all_items else '用户已定或写了要求、AI 还没处理的'} {len(rows)} 项" + (f"，用户改字 {len(eds)} 处" if eds else ''))
    for r in rows: say(r)
    for e in eds:
        say(f"[{e['id']}] {e['column']}·{FIELD.get(e['field'], e['field'])}｜{e['at']}｜{'已写回' if e.get('ok') else '没写回：' + (e.get('error') or '')}"
            + f"｜{change(e['old'], e['new'])}" + (f"｜AI：{e['handled']['note']}" if e.get('handled') else ''))
    if rows or eds: say('处理完逐项回复：workbench <片子目录> --reply 编号 --note "…"（编号是 [] 里的；带 --files 就是换新版）')
    if only is None:
        ok = [f"{LABEL.get(c, c)}（{v['at'][5:16]}）" for c, v in settle_info(job).items() if v['state'] == 'ok']
        if ok: say('用户点过“这一栏定了”的栏目：' + '、'.join(ok) + '（之后这一栏内容一变就不算，要用户重新点）')


FIELD = {'description': '简介', 'tags': '标签'}


def change(old, new, ctx=10, most=12):
    """用户改了哪里（给 AI 看）：标签列出加了、去了哪几个；文字只列改动的地方，前后各带几个字：…上文【原来→改成】下文…。"""
    if isinstance(old, list) or isinstance(new, list):
        o, n = list(old or []), list(new or [])
        add, rm = [x for x in n if x not in o], [x for x in o if x not in n]
        return '改成：' + '、'.join(n) + (f"（加了 {'、'.join(add)}）" if add else '') + (f"（去掉 {'、'.join(rm)}）" if rm else '')
    import difflib
    o, n = str(old or ''), str(new or ''); out = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, o, n, autojunk=False).get_opcodes():
        if tag == 'equal': continue
        out.append(f"…{o[max(0, a - ctx):a]}【{o[a:b] or '（加）'}→{n[c:d] or '（删）'}】{o[b:b + ctx]}…".replace('\n', '⏎'))
    if not out: return '没改'
    return '改了 ' + str(len(out)) + ' 处：' + '；'.join(out[:most]) + ('；…' if len(out) > most else '')


# ---------- 样张自动发布（anim.still_sheet 调） ----------
def page_open(job):
    """审片台页面正开着吗（服务在跑、页面十几秒内来拿过数据）。"""
    from . import workbench as W
    try:
        info = W.running(job)
        if not info: return False
        pg = W.ping(info['port'])
        return bool(pg and pg.get('page_seen') is not None and pg['page_seen'] < 15)
    except Exception: return False


def publish_stills(job, items, project, words):
    """HyperFrames/Blender 出样张后：把样张发到审片台“动画”栏（同一工程同一批镜头再出＝新版）。返回 (这一项, 页面开着吗)。"""
    project = Path(project).resolve() if project else None
    shots = list(dict.fromkeys(s for s, _, _ in items if s != '口播'))
    anim = {'kind': '样张', 'project': str(project), 'shots': shots} if project and shots else None
    title = f"{'、'.join(shots) or '口播'} 样张（{project.name if project else '动画'}）"
    it = publish(job, '动画', title, words, '可以', [f for _, _, f in items], [f'{s} 第 {t:g} 秒' for s, t, _ in items],
                 key=f"样张|{project.name if project else ''}|{','.join(shots)}", anim=anim)
    return it, page_open(job)
