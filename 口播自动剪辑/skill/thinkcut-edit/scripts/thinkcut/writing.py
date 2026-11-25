# 审片台第一栏“文案”（2026-11-24 用户定：“就差一个文案工作了……写好文案，跟Claude一起改文案……改完文案再去做视频”）。
# 命令、数据里这一栏叫 写稿（老栏目名 文案 页面上是“口播剪辑”，老数据在用，不动）；页面上显示“文案”。
# 用户在页面里写标题、封面文字（一行一行）、口播正文（按自然段），读写的就是项目文案 03_成片/<项目名>.md（视频项目管理的 script_doc，只改动的那一节）。
# 和 AI 一起改：用户选一段字（或针对全文）提意见 → “交给 AI” → AI 用 workbench --rewrite 交改写建议（把哪几段改成什么）→ 页面逐处“原文 → 改后”，
# 用户逐处点“采纳／不要”，采纳才写进文件。AI 不能直接改文案文件、不能替用户采纳或定稿（采纳、定稿只收页面带口令的请求，见 workbench.py）。
# 每次采纳、手改一段时间、退回都留一个版本（审片工作台/文案版本/），能看改了哪里、能退回。定稿＝一件交给 AI 的事（--wait 打印“文案定稿了”）。
# 数据：审片工作台/写稿.json（意见、对话、改写建议、版本目录）。说明见 references/审片工作台.md「文案栏」。
import threading
import difflib, re, time
from pathlib import Path
from .common import keep_backup, read_data, rj, wj, say, die
from . import board
from .board import Oops

COL = '写稿'
FIELDS = ('标题', '封面文字', '口播正文')
RATE = 340               # 估时长用的语速：每分钟多少字（几条成片实测 305–370）
MERGE = 600              # 手改：10 分钟内接着改算同一个版本
VIDEO = ('.mp4', '.mov', '.m4v', '.mxf', '.mts', '.avi')
RULES = ('【文案栏的规矩】先用 Read 把文案全文读一遍再动（下面也打印了带段号的全文）；【选中的字只是定位，不是修改范围】（2026-11-24 用户：“它改的时候并不只改这段，甚至给你上下文意思改”）：'
         '按用户的意思判断要改多大范围——只这一句／连同上下文／别处同类的地方／和前后呼应的地方一起改，需要时可以调整段落顺序；交回的是改后的稿子，改到的每一处都写进 --rewrite（离评论位置很远的也行），'
         'note 先写“我理解你的意思是……，所以改了这几处：……”；不要点哪改哪，也不要借题改用户没说的；'
         '不许直接改文案文件：用 --rewrite 交改写建议，用户在页面上逐处点“采纳”才写进文件；用大白话，保留用户的口语风格和事实，不许编数字和案例；'
         '拿不准（两种以上合理理解，或要动用户没提的大段内容）就不改，--reply 稿N --status 问你 --note "问题" --options "A …|B …" 回问')


def now(): return time.strftime('%Y-%m-%d %H:%M:%S')
def data_file(job): return board.home(job) / '写稿.json'
def ver_dir(job): return board.home(job) / '文案版本'
def lite(job): return getattr(job, 'lite', False)


def load(job):
    f = data_file(job); d = read_data(f)
    d.setdefault('说明', '审片台文稿栏（数据里叫 写稿）：用户的意见、和 AI 的对话、AI 的改写建议、文案的版本目录。格式见 skill/thinkcut-edit/references/审片工作台.md')
    d.setdefault('schema', 1); d.setdefault('notes', []); d.setdefault('versions', [])
    return d


def edit(job, fn):
    """和 标记.json、审片台.json 同一把文件锁。不要嵌套着调（同一把锁会等死）。"""
    from .workbench import _flock
    with _flock(job):
        d = load(job); out = fn(d); keep_backup(data_file(job)); wj(data_file(job), d); return out


def note(d, nid):
    n = next((x for x in d['notes'] if f"稿{x['id']}" == str(nid)), None)
    if not n: raise Oops(f'没有 {nid} 这条意见')
    return n


# ---------- 文案文件：三类字、自然段 ----------
def sd(): return board._pm('script_doc')


def path(job):
    f = board.script_path(job)
    if not f: raise Oops('找不到项目文件夹（往上没有 项目.json）：先用 thinkcut-project 建项目')
    return f


def read(job):
    """{'path', 'exists', 'text': 整份文件, 'fields': {标题, 封面文字（几行连成一段字）, 口播正文（这一节原样，带 ### 小标题和 > 备注）}, 'why': {哪类不能改的原因}}。"""
    f = path(job)
    if not f.is_file(): return {'path': f, 'exists': False, 'text': '', 'fields': dict.fromkeys(FIELDS, ''), 'why': {}}
    info = board.script_info(job)
    return {'path': f, 'exists': True, 'text': f.read_text(encoding='utf-8'), 'fields': {k: info['fields'][k]['value'] for k in FIELDS},
            'why': {k: info['fields'][k]['why'] for k in FIELDS if info['fields'][k]['why']}}


def blocks(body):
    """口播正文的自然段：每个非空行一段 → [{'k': 第几段（从 1 起）, 'line': 这一节里第几行, 'text', 'kind': 段／小标题／备注／分隔线}]。"""
    S = sd(); out = []
    for i, l in enumerate(str(body or '').split('\n')):
        if not l.strip(): continue
        kind = '小标题' if S.SUBHEAD.match(l) or S.H1.match(l) else '备注' if l.strip().startswith('>') else '分隔线' if S.RULE.match(l) or S.FENCE.match(l) else '段'
        out.append({'k': len(out) + 1, 'line': i, 'text': l.strip(), 'kind': kind})
    return out


def count(body):
    """口播字数（小标题、备注不算，不算空白）和按语速估的秒数。"""
    S = sd(); n = S.count(S.body_text(str(body or '').split('\n')) or ''); return n, round(n * 60 / RATE)


def clock(sec): return f'{sec // 60} 分 {sec % 60} 秒' if sec >= 60 else f'{sec} 秒'


def _rows(text): return [x.strip() for x in str(text or '').replace('\r', '').split('\n') if x.strip()]


def _write(job, field, value):
    """把一类字写进文案文件（没有文件先建一份空的标准写法）。只改这一节。"""
    f = path(job); S = sd()
    if not f.is_file():
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(f'# {f.stem}\n\n## 标题\n\n## 封面文字\n\n## 口播正文\n', encoding='utf-8')
    why = board.script_info(job)['fields'][field]['why']
    if why: raise Oops(why)
    value = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    if field == '标题': value = ' '.join(value.split())
    if field == '口播正文' and any(S.H2.match(l) for l in value.split('\n')): raise Oops('正文里不能有以 ## 开头的行（那是文案的小节标题）：小标题用 ### 开头')
    try: S.set_section(f, field, _rows(value) if field == '封面文字' else value.strip('\n'))
    except (RuntimeError, ValueError, OSError) as e: raise Oops(str(e))


# ---------- 版本：每次采纳、手改一段时间、退回、贴进来都留一个；存整份文案 ----------
def _vfile(job, v): return ver_dir(job) / f"v{int(v):04d}.md"


def _vtext(job, v):
    f = _vfile(job, v); return f.read_text(encoding='utf-8') if f.is_file() else None


def _snap(job, text, kind, why, merge=False):
    """留一个版本（和上一版一字不差就不留）。merge：上一版也是手改、10 分钟内，就接着记在那一版上。返回这一版的记录。"""
    S = sd(); n = S.count(S.parse(text).get('口播正文') or '')
    def g(d):
        vs = d['versions']; last = vs[-1] if vs else None
        if last and _vtext(job, last['v']) == text: return last
        ver_dir(job).mkdir(parents=True, exist_ok=True)
        if merge and last and last['kind'] == '手改' and time.time() - last.get('ts', 0) < MERGE:
            _vfile(job, last['v']).write_text(text, encoding='utf-8'); last.update(at=now(), ts=time.time(), chars=n); return last
        rec = {'v': (last['v'] if last else 0) + 1, 'at': now(), 'ts': time.time(), 'kind': kind, 'why': why, 'chars': n}
        _vfile(job, rec['v']).write_text(text, encoding='utf-8'); vs.append(rec); return rec
    return edit(job, g)


def _change(job, kind, why, fn, merge=False):
    """改文案文件的统一入口：先把改之前的样子留一版（页面第一次改、或在页面以外改过），做 fn，再留一版；片子已经在做了（有 job.json）就记一条改字给 AI 跟着改。"""
    before = read(job)
    if before['exists']: _snap(job, before['text'], '原来', '改之前的样子（第一次在这里改，或在别处改过）')
    out = fn()
    after = read(job); rec = _snap(job, after['text'], kind, why, merge) if after['exists'] else None
    if not lite(job):
        diff = [k for k in FIELDS if before['fields'][k] != after['fields'][k]]
        if diff: board.edit(job, lambda d: [board._log_edit(d, COL, k, before['fields'][k], after['fields'][k], True) for k in diff])
    return out, rec


_save_lock = threading.Lock()       # 页面离开时标题、正文几段同时存（keepalive 并发到）：文案文件是“读—改一节—写回”，不排队会互相盖掉（2026-11-25 修页面实测）


def save(job, field, value):
    """页面上手改一类字（自动保存）。"""
    if field not in FIELDS: raise Oops('文案只能改 标题、封面文字、口播正文')
    with _save_lock: _change(job, '手改', '你手改的', lambda: _write(job, field, value), merge=True)
    return {'ok': True}


def split_pasted(text):
    """贴进来的一大段字 → (标题, 封面文字几行, 口播正文)。带「## 标题」这类小节的按小节取；否则第一行短、不像一句话就当标题，其余每行一段。"""
    S = sd(); text = str(text or '').replace('\r\n', '\n').replace('\r', '\n'); lines = text.split('\n')
    if any(S.H2.match(l) for l in lines):
        p = S.parse(text); body = next(('\n'.join(b).strip('\n') for name, b in S.sections(lines) if name == '口播正文'), p.get('口播正文') or '')
        return p.get('标题') or '', p.get('封面文字') or [], body
    rows = [S.clean(l) if S.H1.match(l) else l.strip() for l in lines if l.strip()]; title = ''
    if len(rows) > 1:
        first = re.sub(r'^(标题|题目)\s*[：:]\s*', '', re.sub(r'^#+\s*', '', rows[0])).strip()
        if first and len(first) <= 40 and first[-1] not in '。，；、': title, rows = first, rows[1:]
    return title, [], '\n\n'.join(rows)


def import_text(job, text):
    """把一大段文字贴进来，自动分成标题和正文（正文还空着时才行，不盖已经写的）。"""
    if not str(text or '').strip(): raise Oops('贴进来的是空的')
    cur = read(job)
    if cur['fields']['口播正文'].strip(): raise Oops('已经有正文了：直接贴进正文里就行')
    title, cover, body = split_pasted(text)
    if not body.strip(): raise Oops('没分出正文：贴进来的只有一行？直接写在标题或正文里就行')
    def fn():
        if title and not cur['fields']['标题'].strip(): _write(job, '标题', title)
        if cover and not cur['fields']['封面文字'].strip(): _write(job, '封面文字', '\n'.join(cover))
        _write(job, '口播正文', body)
    _change(job, '贴进来', '贴进来的一大段字，自动分成标题和正文', fn)
    return {'ok': True, 'title': title, 'paras': len(blocks(body))}


def block_diff(old, new):
    """两份自然段（文字列表）逐段比 → [{'op': 改／加／删, 'k': 原来第几段（加＝插在第 k 段后面，0 是最前面）, 'old', 'new', 'anchor': 加在哪一段后面（那段的字，最前面是 None）}]。"""
    out = []
    for tag, a, b, c, e in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == 'equal': continue
        i = a
        for op, x, y in _align(old[a:b], new[c:e]):
            if op == '改': out.append({'op': '改', 'k': a + x + 1, 'old': old[a + x], 'new': new[c + y]}); i = a + x + 1
            elif op == '删': out.append({'op': '删', 'k': a + x + 1, 'old': old[a + x], 'new': ''}); i = a + x + 1
            else: out.append({'op': '加', 'k': i, 'old': '', 'new': new[c + y], 'anchor': old[i - 1] if i else None})
    return out


def _align(xs, ys):
    """一处改动里的几段旧字、几段新字怎么对上：先把像的配成“改”（顺序不乱、相似的优先），剩下挨着的旧段、新段按顺序配成“改”，再剩的是“删”“加”。
    返回 [(改, 第几段旧的, 第几段新的) / (删, 旧, None) / (加, None, 新)]，按文章顺序。"""
    n, m = len(xs), len(ys); R = [[difflib.SequenceMatcher(None, x, y, autojunk=False).ratio() for y in ys] for x in xs]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = max(best[i + 1][j], best[i][j + 1], best[i + 1][j + 1] + R[i][j] if R[i][j] >= 0.3 else -1)
    ops, i, j = [], 0, 0
    while i < n and j < m:
        if R[i][j] >= 0.3 and best[i][j] == best[i + 1][j + 1] + R[i][j]: ops.append(('改', i, j)); i += 1; j += 1
        elif best[i][j] == best[i + 1][j]: ops.append(('删', i, None)); i += 1
        else: ops.append(('加', None, j)); j += 1
    ops += [('删', k, None) for k in range(i, n)] + [('加', None, k) for k in range(j, m)]
    out, run = [], []
    def flush():
        ds = [o for o in run if o[0] == '删']; as_ = [o for o in run if o[0] == '加']; k = min(len(ds), len(as_))
        out.extend([('改', ds[t][1], as_[t][2]) for t in range(k)] + ds[k:] + as_[k:]); run.clear()
    for o in ops:
        if o[0] == '改': flush(); out.append(o)
        else: run.append(o)
    flush(); return out


def versions_info(job, d=None):
    return [{k: x.get(k) for k in ('v', 'at', 'kind', 'why', 'chars')} for x in reversed((d or load(job))['versions'])]


def version_diff(job, v):
    """第 v 版和它前一版比，改了哪里（给页面“看改了哪里”）。"""
    vs = load(job)['versions']; i = next((k for k, x in enumerate(vs) if x['v'] == int(v)), None)
    if i is None: raise Oops(f'没有第 {v} 版')
    S = sd(); new = _vtext(job, vs[i]['v']); old = _vtext(job, vs[i - 1]['v']) if i else ''
    if new is None or old is None: raise Oops('这一版的文件不在了（审片工作台/文案版本/ 被动过）')
    def parts(t):
        p = S.parse(t) if t else {}; ls = t.split('\n'); body = '\n'.join(next((b for name, b in S.sections(ls) if name == '口播正文'), [])) if p else ''
        return p.get('标题') or '', '\n'.join(p.get('封面文字') or []), [b['text'] for b in blocks(body or p.get('口播正文') or '')]
    a, b = parts(old), parts(new)
    return {'ok': True, 'v': vs[i]['v'], 'prev': vs[i - 1]['v'] if i else None, 'at': vs[i]['at'], 'why': vs[i]['why'],
            'fields': [{'field': k, 'old': x, 'new': y} for k, x, y in (('标题', a[0], b[0]), ('封面文字', a[1], b[1])) if x != y], 'body': block_diff(a[2], b[2])}


def restore(job, v):
    """退回到第 v 版（只收页面的请求）：整份文案换成那一版；退回本身也留一个版本，不丢东西。"""
    text = _vtext(job, v)
    if text is None: raise Oops(f'没有第 {v} 版')
    f = path(job)
    if f.is_file() and f.read_text(encoding='utf-8') == text: raise Oops(f'现在就是第 {v} 版的样子')
    def fn(): f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text, encoding='utf-8')
    _, rec = _change(job, '退回', f'退回到第 {int(v)} 版', fn)
    return {'ok': True, 'v': rec['v'] if rec else None}


# ---------- 意见（每条是一段对话）：只收页面的请求 ----------
def note_add(job, q):
    """提一条意见：选了一段字（quote、para 第几段）或针对全文（不带 quote）。字可以先空着，边写边存（note_text）。"""
    quote = str(q.get('quote') or '').strip()[:300]; text = str(q.get('text') or '').strip()
    def g(d):
        n = {'id': 1 + max([x['id'] for x in d['notes']] or [0]), 'at': now(), 'scope': '选段' if quote else '全文', 'quote': quote, 'text': text, 'status': '待处理', 'thread': []}
        if quote and str(q.get('para') or '').isdigit(): n['para'] = int(q['para'])
        if quote and q.get('field') in FIELDS: n['field'] = q['field']
        d['notes'].append(n); return {'ok': True, 'id': f"稿{n['id']}"}
    return edit(job, g)


def note_text(job, q):
    def g(d):
        n = note(d, q['id'])
        if n['thread']: raise Oops('AI 已经回过这条了：在下面“回复 AI”里接着说')
        n['text'] = str(q.get('text') or '').strip(); n['at'] = now(); return {'ok': True}
    return edit(job, g)


def note_del(job, q):
    def g(d): d['notes'].remove(note(d, q['id'])); return {'ok': True}
    return edit(job, g)


def opts_of(n):
    """最后说话的是 AI、在问你时，给页面画的选项按钮。"""
    th = n.get('thread') or []
    if not th or th[-1]['who'] != 'AI': return []
    return th[-1].get('options') or (board.auto_options(th[-1]['text']) if n['status'] == '问你' else [])


def answer(job, q):
    """用户回复 AI（AI 回过话以后）：写的话、点的选项。这条回到待处理（页面上是待发送）。"""
    text = str(q.get('text') or '').strip(); picked = str(q.get('picked') or '').strip()
    if not (text or picked): raise Oops('回复是空的')
    def g(d):
        n = note(d, q['id'])
        if not any(x['who'] == 'AI' for x in n['thread']): raise Oops('AI 还没回这条：直接改上面你写的话就行')
        if picked and picked not in opts_of(n): raise Oops('这个选项已经不在了（AI 又回了话）：刷新看看')
        n['thread'].append({'who': '用户', 'text': text or f'选了：{picked}', 'at': now(), **({'picked': picked} if picked else {})}); n['status'] = '待处理'
        return {'ok': True}
    return edit(job, g)


def pending(n): return [c for c in (n.get('proposal') or {}).get('changes', []) if c['state'] == '待定']


def ustate(n, ids):
    """一条意见 → 页面的 4 个状态之一（还没写字的、AI 只回了一句说明的是 ''）。"""
    s = ids.get(f"稿{n['id']}")
    if s == 'unsent': return '待发送'
    if s in ('sent', 'doing'): return 'AI 在改'
    if n['status'] == '问你' or pending(n): return '待你看'
    if n['status'] == '改写': return '已定'
    return ''


# ---------- AI 用命令：交改写建议、回话 ----------
def _spec_changes(job, spec, cur):
    """命令给的改写（见 references/审片工作台.md「文案栏」）→ 逐处改动。"""
    bl = blocks(cur['fields']['口播正文']); out = []; whole = spec.get('全文')
    if whole is not None and not isinstance(whole, dict): raise Oops('“全文”要写成 {"标题": "…", "封面文字": ["…"], "口播正文": "…"}（只写要改的那几类）')
    fields = []
    for k in ('标题', '封面文字'):
        if whole and whole.get(k) is not None: fields.append((k, whole[k]))
    seen = set(); body_ops = []
    for c in spec.get('changes') or []:
        if not isinstance(c, dict): raise Oops('changes 里每一处写成 {"para": 3, "new": "改后的这一段"}')
        if c.get('field') in ('标题', '封面文字'): fields.append((c['field'], c.get('new'))); continue
        if c.get('field') not in (None, '口播正文'): raise Oops(f'field 只能是 标题、封面文字、口播正文：{c.get("field")}')
        new = '\n'.join(_rows(c.get('new')))
        if 'after' in c:
            k = c['after']
            if not isinstance(k, int) or isinstance(k, bool) or not 0 <= k <= len(bl): raise Oops(f'"after": {k} 不对：正文现在有 {len(bl)} 段，写 0（最前面）到 {len(bl)}')
            if not new: raise Oops(f'第 {k} 段后面要加的一段是空的')
            body_ops.append({'op': '加', 'k': k, 'old': '', 'new': new, 'anchor': bl[k - 1]['text'] if k else None}); continue
        k = c.get('para')
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(bl): raise Oops(f'"para": {k} 不对：正文现在有 {len(bl)} 段（--wait、--list 打印的段号）；要加新的一段写 "after": 段号')
        if k in seen: raise Oops(f'第 {k} 段写了两次')
        seen.add(k)
        if c.get('delete') or not new: body_ops.append({'op': '删', 'k': k, 'old': bl[k - 1]['text'], 'new': ''})
        elif new != bl[k - 1]['text']: body_ops.append({'op': '改', 'k': k, 'old': bl[k - 1]['text'], 'new': new})
    if whole and whole.get('口播正文') is not None:
        if body_ops: raise Oops('“全文”里给了口播正文，就不要再在 changes 里逐段改正文')
        body_ops = block_diff([b['text'] for b in bl], _rows(whole['口播正文']))
    for k, v in fields:
        v = '\n'.join(_rows('\n'.join(v) if isinstance(v, list) else v)); v = ' '.join(v.split()) if k == '标题' else v
        if not v: raise Oops(f'{k}改成空的？不改就别写这一项')
        if any(x['field'] == k for x in out): raise Oops(f'{k}写了两次')
        if v != '\n'.join(_rows(cur['fields'][k])): out.append({'field': k, 'op': '改', 'k': 0, 'old': cur['fields'][k], 'new': v})
    S = sd()
    for c in body_ops:
        if any(S.H2.match(l) for l in c['new'].split('\n')): raise Oops('正文里不能有以 ## 开头的行（那是文案的小节标题）：小标题用 ### 开头')
        out.append({'field': '口播正文', **c})
    return [{'i': i, **c, 'state': '待定'} for i, c in enumerate(sorted(out, key=lambda c: (FIELDS.index(c['field']), c['k'], c['op'] == '加')))]


def propose(job, nid, spec, say_note=''):
    """AI 针对一条意见交改写建议：把哪几段改成什么（可以一次多处、整篇）。只是建议——用户在页面上逐处点“采纳”才写进文案文件。"""
    if not isinstance(spec, dict): raise Oops('改写要写成一个 JSON 对象：{"note": "…", "changes": [{"para": 3, "new": "…"}]}')
    text = str(say_note or spec.get('note') or '').strip()
    if not text: raise Oops('要带说明（--note 或 JSON 里的 "note"）：先写“我理解你的意思是……，所以改了这几处：……”')
    note(load(job), nid); cur = read(job)
    if cur['why']: raise Oops('这份文案现在不能在页面上改：' + '；'.join(cur['why'].values()))
    changes = _spec_changes(job, spec, cur)
    if not changes: raise Oops('没有要改的地方（和现在的文案一样）：只想说明、回问就用 --reply ' + str(nid) + ' --note "…"')
    def g(d):
        n = note(d, nid); at = now(); n['proposal'] = {'at': at, 'note': text, 'changes': changes}; n['status'] = '改写'
        n['thread'].append({'who': 'AI', 'text': text, 'at': at, 'kind': '改写', 'n': len(changes)}); return len(changes)
    return edit(job, g)


def reply(job, nid, text, status=None, options=None):
    """AI 回一句话：问你（拿不准回问，可带选项按钮）或说明（没改、为什么）。"""
    if not text: die('回复要带 --note')
    if status == '已改': die('文案栏不能回“已改”：AI 不直接改文案文件，改写用 --rewrite 交建议，用户点“采纳”才算改')
    try:
        opts = board.parse_options(options) if options else None
        st = '问你' if status == '问你' or opts else '已回'
        def g(d):
            n = note(d, nid); n['status'] = st
            n['thread'].append({'who': 'AI', 'text': text, 'at': now(), 'kind': st, **({'options': opts} if opts else {})})
        edit(job, g)
    except Oops as e: die(str(e))
    say(f'{nid} 已回复' + ('；页面上这条变成“待审阅”，等用户回答（回答后他点“提交 AI”，--wait 会打印）' if st == '问你' else ''))


def rewrite_cli(job, nid, file, note_text_=''):
    import json
    if not file: die('--rewrite 要带 --json 改写.json（格式见 references/审片工作台.md「文案栏」）')
    try: spec = json.loads(Path(file).expanduser().read_text(encoding='utf-8'))
    except (OSError, ValueError) as e: die(f'读不了 {file}：{e}')
    try: n = propose(job, nid, spec, note_text_)
    except Oops as e: die(str(e))
    say(f'{nid} 的改写建议已交到审片台文稿栏：{n} 处。用户逐处点“采纳／不要”，采纳的才写进文案文件；页面 3 秒内自动显示。接着挂 --wait 等。')


# ---------- 用户点采纳／不要（只收页面的请求） ----------
def _find(bl, text, near):
    hit = [i for i, b in enumerate(bl) if b['text'] == text]
    return min(hit, key=lambda i: abs(i - near)) if hit else None


def _lines(new):
    out = []
    for r in _rows(new): out += ['', r] if out else [r]
    return out


def apply_change(body, c, changes):
    """把一处改动做到口播正文（这一节的原样文字）上，返回新的。按文字找段（用户可能在别处改过，段号会变）；找不到就报错，让用户自己看着办。"""
    lines = str(body or '').split('\n'); bl = blocks(body)
    if c['op'] in ('改', '删'):
        i = _find(bl, c['old'], c['k'] - 1)
        if i is None: raise Oops('这一段你后来改过了，和 AI 看到的原文对不上：照右边的“改后”自己手改，或让 AI 重新给')
        ln = bl[i]['line']
        if c['op'] == '改': lines[ln:ln + 1] = _lines(c['new'])
        else:
            del lines[ln]
            if ln < len(lines) and not lines[ln].strip() and (ln == 0 or not lines[ln - 1].strip()): del lines[ln]
        return '\n'.join(lines)
    pos = 0
    if c.get('anchor') is not None:
        took = [x['new'].split('\n')[-1] for x in changes if x['field'] == '口播正文' and x['op'] == '改' and x['old'] == c['anchor'] and x['state'] == '采纳']   # 前面那段刚被采纳改掉了：按改后的找
        i = next((k for k in (_find(bl, t, c['k'] - 1) for t in [c['anchor']] + took) if k is not None), None)
        if i is None: raise Oops('它前面那一段你后来改过了，不知道该加在哪：照右边的字自己加，或让 AI 重新给')
        pos = i + 1
    for x in changes:                      # 同一处要加的好几段：前面已经采纳的排在它前头
        if x is c: break
        if x['field'] == '口播正文' and x['op'] == '加' and x['k'] == c['k'] and x['state'] == '采纳':
            for r in _rows(x['new']):
                if pos < len(bl) and bl[pos]['text'] == r: pos += 1
    new = _lines(c['new'])
    if pos < len(bl): ln = bl[pos]['line']; lines[ln:ln] = new + ['']
    else:
        while lines and not lines[-1].strip(): lines.pop()
        lines += ([''] if lines else []) + new
    return '\n'.join(lines)


def decide(job, q):
    """用户在页面上对一条意见的改写建议点“采纳／不要”：which 是第几处的列表，或 'all'（还没点的全部）。采纳才写进文案文件，一次采纳留一个版本。"""
    nid = str(q['id']); take = bool(q.get('accept')); which = q.get('which', 'all')
    n = note(load(job), nid); prop = n.get('proposal') or {}; changes = prop.get('changes') or []
    todo = [c for c in changes if c['state'] == '待定' and (which == 'all' or c['i'] in [int(x) for x in which])]
    if not todo: raise Oops('这几处已经点过了（或 AI 换了新的建议）：刷新看看')
    done, errs = [], []
    def fn():
        for c in todo:
            try:
                if c['field'] == '口播正文': _write(job, '口播正文', apply_change(read(job)['fields']['口播正文'], c, changes))
                else: _write(job, c['field'], c['new'])
                c['state'] = '采纳'; done.append(c['i'])
            except Oops as e: errs.append({'i': c['i'], 'error': str(e)})
    if take:
        _change(job, '采纳', f'采纳了 {nid} 的 {len(todo)} 处改写', fn)
    else: done = [c['i'] for c in todo]
    def g(d):
        m = note(d, nid); p = m.get('proposal') or {}
        if p.get('at') != prop.get('at'): raise Oops('AI 刚换了新的建议：看了新的再点')
        for c in p['changes']:
            if c['i'] in done: c.update(state='采纳' if take else '不要', at=now())
        return {'ok': not errs, 'done': done, 'errors': errs, 'error': '；'.join(e['error'] for e in errs)}
    return edit(job, g)


# ---------- 定稿：用户点了就是一件交给 AI 的事 ----------
def sig(job):
    """这一栏要定的内容（标题、封面文字、口播正文）。变了，之前点的“定稿”就不算。"""
    try: f = read(job)['fields']; return [f[k] for k in FIELDS]
    except Exception: return []


def settle_why(job, busy):
    """现在不能点“定稿”的原因（能点返回 ''）。"""
    try: cur = read(job)
    except Oops as e: return str(e)
    if not cur['fields']['标题'].strip() or not cur['fields']['口播正文'].strip(): return '标题和口播正文完成后方可定稿'
    d = load(job); ask = sum(n['status'] == '问你' for n in d['notes']); wait = sum(len(pending(n)) for n in d['notes'])
    if busy.get('unsent'): return '本栏有待提交的意见或修改：请先点“提交 AI”（不需要的意见可删除）'
    if busy.get('sent') or busy.get('doing'): return 'AI 正在处理：待交回并审阅后再定稿'
    if wait: return f'AI 的改写建议还有 {wait} 处未处理：请逐处点“采纳”或“不要”'
    if ask: return f'AI 提出了 {ask} 个问题：请回答，或删除该意见'
    return ''


def final_key(job, d=None):
    """用户点了“定稿”、内容没再变、AI 还没回话 → 这件事的键；否则 None。"""
    r = ((d or board.load(job)).get('settled') or {}).get(COL)
    if not r or r.get('handled') or r.get('sig') != board.col_sig(job, COL): return None
    return f"定稿@{r['sig']}"


def kick_key(job, d=None):
    """用户点了“开工”、定稿还有效、AI 还没回话 → 这件事的键；否则 None。"""
    d = d or board.load(job); k = d.get('kickoff'); r = (d.get('settled') or {}).get(COL)
    if not k or k.get('handled') or not r or k.get('sig') != r.get('sig') or r.get('sig') != board.col_sig(job, COL): return None
    return f"开工@{board._h([k['sig'], k['at']])}"


def kick_info(job):
    """给页面：定稿以后“备料、开工”那一条（2026-11-24 用户定：定稿后先开项、存资料，“我一点开工就直接开工”）。片子还没建、定稿有效时才有。"""
    d = board.load(job); r = (d.get('settled') or {}).get(COL)
    if not lite(job) or not r or r.get('sig') != board.col_sig(job, COL): return None
    k = d.get('kickoff'); k = k if k and k.get('sig') == r.get('sig') else None
    return {'materials': (k or {}).get('materials', ''), 'at': (k or {}).get('at'), 'note': ((k or {}).get('handled') or {}).get('note'), 'videos': len(recordings(job)),
            'opened': (r.get('handled') or {}).get('note')}


def kickoff(job, q):
    """用户点“开工”（只收页面的请求）：定稿以后才能点；带上他写的“素材在哪、要补充什么”。算一件交给 AI 的事（--wait 打印“用户点了开工”）。"""
    if not lite(job): raise Oops('这个项目已经在做了，不用再点开始制作')
    sg = board.col_sig(job, COL); text = str(q.get('materials') or '').strip()[:4000]
    def g(d):
        r = (d.get('settled') or {}).get(COL)
        if not r or r.get('sig') != sg: raise Oops('文案还没定稿（或定稿后又改过）：先点右上“定稿”')
        d['kickoff'] = {'at': now(), 'sig': sg, 'materials': text, 'who': board.WHO}
    board.edit(job, g); _auto_send(job, kick_key(job)); return {'ok': True}


def reply_kick(job, text):
    if not text: die('回复要带 --note（接下来做什么）')
    def g(d):
        if not d.get('kickoff'): raise Oops('用户没有点过“开始制作”')
        d['kickoff']['handled'] = {'at': now(), 'note': text}
    try: board.edit(job, g)
    except Oops as e: die(str(e))
    say('开始制作已回复')


def on_settle(job):
    """用户点了“定稿”：直接算已经交给 AI（--wait 马上打印“文案定稿了”）。"""
    _auto_send(job, final_key(job))


def _auto_send(job, k):
    if not k: return
    def g(d):
        sent = board._sent(d); sent[k] = {'at': now(), 'picked': None}; d['sent'] = sent; d.pop('send', None)
        d.setdefault('sends', []).append({'at': now(), 'column': COL, 'n': 1, 'who': board.WHO}); d['sends'] = d['sends'][-50:]
    board.edit(job, g)


def reply_final(job, text):
    if not text: die('回复要带 --note（接下来做什么）')
    def g(d):
        r = (d.get('settled') or {}).get(COL)
        if not r: raise Oops('用户没有点过“定稿”（或已经取消了）')
        r['handled'] = {'at': now(), 'note': text}
    try: board.edit(job, g)
    except Oops as e: die(str(e))
    say('定稿已回复')


def recordings(job):
    """项目 01_原始素材 里的视频文件（给 AI 判断有没有口播录像）。"""
    pd = board.project_dir(job); src = pd / '01_原始素材' if pd else None
    if not src or not src.is_dir(): return []
    return sorted(str(p.relative_to(pd)) for p in src.rglob('*') if p.is_file() and p.suffix.lower() in VIDEO and not p.name.startswith('.'))


# ---------- 给页面的数据、给 AI 读的清单 ----------
def page_info(job, ids):
    try: cur = read(job)
    except Oops as e: return {'ok': False, 'why': str(e), 'notes': [], 'versions': []}
    d = load(job); f = cur['path']; n, sec = count(cur['fields']['口播正文'])
    notes = [{**x, 'nid': f"稿{x['id']}", 'ustate': ustate(x, ids), 'opts': opts_of(x)} for x in d['notes']]
    return {'ok': True, 'rel': f'{f.parent.name}/{f.name}', 'exists': cur['exists'], 'fields': cur['fields'], 'why': cur['why'], 'count': n, 'seconds': sec, 'rate': RATE,
            'notes': notes, 'versions': versions_info(job, d), 'early': lite(job), 'kick': kick_info(job)}


def _where(n, bl):
    """这条意见现在落在第几段（按选中的字找，找不到用提的时候记的段号）。"""
    if n['scope'] != '选段' or n.get('field') in ('标题', '封面文字'): return None
    hit = [b['k'] for b in bl if n['quote'] and n['quote'].split('\n')[0] in b['text']]; near = n.get('para') or 1
    return min(hit, key=lambda k: abs(k - near)) if hit else (near if 1 <= near <= len(bl) else None)


def listing(job, only=None, all_notes=False):
    """给 AI：这一栏用户交来的意见（带所在段、前后段、全文字数）、对话、定稿。only：只列这次发来的。"""
    try: cur = read(job)
    except Oops as e: say(f'文案栏：{e}'); return
    d = load(job); bl = blocks(cur['fields']['口播正文']); n, sec = count(cur['fields']['口播正文']); cut = lambda s, m=120: s if len(s) <= m else s[:m] + '…'
    notes = [x for x in d['notes'] if (x['text'] or x['thread']) and (all_notes or x['status'] == '待处理') and (only is None or f"稿{x['id']}" in only)]
    final = final_key(job) if only is None or '定稿' in only else None
    kick = kick_key(job) if only is None or '开工' in only else None
    if not notes and not final and not kick: return
    say(f"文案栏（数据里叫 写稿）{f'：{len(notes)} 条意见' if notes else ''}。文案文件 {cur['path']}" + ('' if cur['exists'] else '（还没有，用户采纳了才会建）')
        + f"｜标题：{cur['fields']['标题'] or '（还没写）'}｜口播正文 {n} 字，按每分钟 {RATE} 字约 {clock(sec)}，{len(bl)} 段")
    if notes:
        say(RULES)
        say('全文（段号就是 --rewrite 里的 para；小标题、备注也各算一段）：' + ('' if bl else '正文还是空的'))
        for b in bl: say(f"  第 {b['k']} 段{'' if b['kind'] == '段' else '（' + b['kind'] + '）'}：{b['text']}")
    for x in notes:
        k = _where(x, bl); nid = f"稿{x['id']}"
        head = '针对全文' if x['scope'] == '全文' else f"选中了{x.get('field') or ''}里的字“{cut(x['quote'], 80)}”" if k is None else f"选中了第 {k} 段里的字“{cut(x['quote'], 80)}”"
        say(f"[{nid}] {head}｜{x['status']}｜用户 {x['at'][5:16]}：{x['text'] or '（没写字）'}")
        if k: say('   上下文：' + '｜'.join(f'{w}（第 {j} 段）：{cut(bl[j - 1]["text"], 300)}' for w, j in (('前一段', k - 1), ('所在段', k), ('后一段', k + 1)) if 1 <= j <= len(bl)))
        for t in x['thread']:
            say(f"   对话 {t['at'][5:16]} {t['who']}：{t['text']}" + (f"（交了 {t['n']} 处改写建议）" if t.get('kind') == '改写' else '')
                + (f"｜给的选项：{' | '.join(t['options'])}" if t.get('options') else '') + (f"｜选了：{t['picked']}" if t.get('picked') else ''))
        p = x.get('proposal')
        if p: say('   上一次改写建议：' + '；'.join(f"{c['field'] if c['field'] != '口播正文' else '第 %s 段%s' % (c['k'], '后加一段' if c['op'] == '加' else '')}〔{c['state']}〕" for c in p['changes']))
    if notes:
        say(f'交改写建议：workbench {job.dir} --rewrite 稿N --json 改写.json   # {{"note": "我理解你的意思是…，所以改了这几处：…", "changes": [{{"para": 3, "new": "改后的这一段"}}, {{"after": 5, "new": "加一段"}}, {{"para": 7, "delete": true}}, {{"field": "标题", "new": "…"}}]}}')
        say('整篇先写一版／大改：{"note": "…", "全文": {"标题": "…", "封面文字": ["一行", "一行"], "口播正文": "一段\\n\\n一段"}}（只写要改的那几类；程序逐段比出改动，用户整篇采纳或逐段挑）。回问：--reply 稿N --status 问你 --note "…" --options "A …|B …"；只说明不改：--reply 稿N --note "…"')
    if final:
        rec = recordings(job); st = (board.load(job).get('settled') or {}).get(COL) or {}
        say(f"【文案定稿了】用户 {st.get('at', '')[5:16]} 在审片台文稿栏点了“定稿”。文案文件 {cur['path']}（标题：{cur['fields']['标题']}；口播正文 {n} 字，约 {clock(sec)}）")
        if lite(job):
            pd = board.project_dir(job)
            say('【先开项，不要开始制作】（2026-11-24 用户定：“定稿之后……按照咱们最定好的这个封面文字，还有那个标题来开项。开完项之后呢，我们再存资料……我一点开工就直接开工”）'
                f'现在只做开项：① 项目名和定稿的标题不一样就按 thinkcut-project 的规矩改成“6 位编号＋空格＋标题”（project.py rename；改名会挪项目文件夹：先 workbench {job.dir} --stop，改完用新的项目文件夹、原来的端口重新 workbench 再 --wait，页面会自己连上）；'
                f'② 把定稿的标题、封面文字登记进 项目.json（{pd / "项目.json" if pd else "项目.json"}）。'
                '做完回一句，告诉用户项目开好了、素材放哪：把 B-roll、口播录像这些放进项目的 01_原始素材，或在文稿栏定稿下面“素材在哪”里写路径，备好了点“开始制作”。**用户点“开始制作”之前不要剪、不要初剪。**')
            say(f'回话：workbench <项目文件夹> --reply 定稿 --note "项目开好了（项目名 …）。素材放进 01_原始素材，或在这里写路径；备好了点“开始制作”"（这句话显示在页面上）')
        else:
            say('这个项目已经在做了：对照文稿栏的版本看改了哪里，把用到这些字的地方（字幕、封面字、导航条、上传文案）跟着改；不用重新初剪。')
            say(f'收到后先回一句：workbench {job.dir} --reply 定稿 --note "收到，跟着改"（回了页面上这件事才了结）')
    if kick:
        rec = recordings(job); k = board.load(job)['kickoff']
        say(f"【用户点了“开始制作”】{k['at'][5:16]}。文案文件 {cur['path']}（标题：{cur['fields']['标题']}；口播正文 {n} 字，约 {clock(sec)}）")
        say('用户写的“素材在哪、要补充什么”：' + (k['materials'].replace('\n', '⏎') if k['materials'] else '（没写）'))
        say((f"项目 01_原始素材 里现在有 {len(rec)} 个视频文件（{'、'.join(rec[:5])}{'…' if len(rec) > 5 else ''}）：先看哪些是口播录像、哪些是 B-roll" if rec
             else '项目 01_原始素材 里现在没有视频文件：用户也没给路径的话就是没有口播录像，按“只有定稿文案”的做法（C：机器配音占位），用户录了再换'))
        from . import materials
        for line in materials.summary(job): say(line)          # 用户在页面上拖进来、或登记路径复制进来的（首页 2026-11-24）
        say('接下来：① 用户给了素材路径的，按 thinkcut-project 的规矩整理进 01_原始素材（看画面、改名、登记，不动用户的原文件）；② 按初稿模式初剪（references/初稿模式.md，文案就用这一份，不再问用户），有什么素材用什么，缺的用占位。')
        say(f'收到后先回一句：workbench {job.dir} --reply 开工 --note "收到，开始整理素材、初剪"（回了页面上这件事才了结）')
