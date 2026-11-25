"""审片台“发布”栏（数据、命令里还叫 上传文案；2026-11-24 用户：“我想加一个发布功能，就上传到蚁小二的草稿……定时发送可以在这里写……直接发布到草稿”）。

页面上：标题、简介、标签照旧；下面“发布设置”——什么时候发（只存草稿／定时发布＋日期时间，按钮选，不让 AI 猜时间）、
给发布 AI 的话（只发哪几个平台、某个平台的标题短一点……一句话，AI 照着办）；右上角主按钮“存到蚁小二草稿”。
点了＝定稿这一栏＋一件交给 AI 的事（--wait 打印“用户要存到蚁小二草稿”），AI 照 thinkcut-upload 做：导出（要用户在审核线点过“确定此栏”）→
登记定稿、存档、封存 → upload --execute（定时就加 --schedule）→ --reply 发布 --note 存进了哪些平台。
两道闸照旧：整片导出只认审核线“确定此栏”；真正发出去还是用户在蚁小二客户端点。AI 只存草稿。
数据在 审片台.json：publish（设置）、publish_req（这一次的请求，AI 回话记 handled）。
"""
import time
from datetime import datetime
from . import board
from .common import die, say

COL = '上传文案'
MODES = ('draft', 'schedule')


class Oops(board.Oops):
    pass


def settings(d):
    p = d.get('publish') or {}
    return {'mode': p.get('mode') if p.get('mode') in MODES else 'draft', 'time': p.get('time') or '', 'note': p.get('note') or ''}


def _clean(q, d):
    s = settings(d)
    if 'mode' in q:
        if q['mode'] not in MODES: raise Oops('什么时候发只能选“只存草稿”或“定时发布”')
        s['mode'] = q['mode']
    if 'time' in q: s['time'] = str(q.get('time') or '').strip().replace('T', ' ')[:16]
    if 'note' in q: s['note'] = str(q.get('note') or '').strip()[:2000]
    return s


def save(job, q):
    """页面改发布设置（自动保存，只收页面的请求）。"""
    def g(d):
        s = _clean(q, d); d['publish'] = {**s, 'at': board.now()}; return {'ok': True, 'publish': s}
    return board.edit(job, g)


def check_time(s, now=None):
    """定时发布的时间：要写、要是将来至少 10 分钟以后（平台要有时间准备）。返回 (毫秒, 说明)；不对就停。"""
    if s['mode'] != 'schedule': return None
    if not s['time']: raise Oops('选了“定时发布”，还没填日期和时间')
    try: t = datetime.strptime(s['time'], '%Y-%m-%d %H:%M')
    except ValueError: raise Oops(f'定时发布的时间看不懂：{s["time"]}')
    now = now or time.time()
    if t.timestamp() < now + 600: raise Oops('定时发布的时间要在 10 分钟以后（现在填的已经过了或太近）')
    return int(t.timestamp() * 1000)


def gate(job):
    """整片导出的闸：审核线点过“确定此栏”且之后没变。返回 (能不能, 为什么不能)。"""
    if not job.get('review_timeline'): return False, '这个项目还没有审核线（还没初剪）'
    info = (board.settle_info(job) or {}).get('审核线') or {}
    if info.get('state') == 'ok': return True, ''
    return False, '先在审片栏点“定稿”（整片导出只认这个）'


def key(job, d=None):
    d = d or board.load(job); r = d.get('publish_req')
    if not r or r.get('handled') or r.get('cancelled'): return None
    return f"发布@{board._h([r.get('at'), r.get('mode'), r.get('time'), r.get('note')])}"


def request(job, q, ua=''):
    """用户点“存到蚁小二草稿”（只收页面的请求）：存设置 → 查时间、查导出闸 → 定稿这一栏 → 记一件交给 AI 的事。"""
    d = board.load(job); s = _clean(q, d); check_time(s)
    ok, why = gate(job)
    if not ok: raise Oops(why)
    def g(d): d['publish'] = {**s, 'at': board.now()}
    board.edit(job, g)
    info = (board.settle_info(job) or {}).get(COL) or {}
    if info.get('state') != 'ok': board.settle(job, COL, True, ua)          # 发布＝这一栏定稿（字是用户自己写的；改的字一起交给 AI）
    def h(d):
        d['publish_req'] = {'at': board.now(), **s, 'who': board.WHO}
        k = key(job, d); sent = board._sent(d); sent[k] = {'at': board.now(), 'picked': None}; d['sent'] = sent; d.pop('send', None)
        d.setdefault('sends', []).append({'at': board.now(), 'column': COL, 'n': 1, 'who': board.WHO}); d['sends'] = d['sends'][-50:]
    board.edit(job, h)
    return {'ok': True}


def cancel(job):
    """用户取消这次“存到蚁小二草稿”（只收页面的请求）：AI 接了没回话、或不想发了。已经回过话的不用取消。之后能重新点“存到蚁小二草稿”（QA 1.5）。"""
    def g(d):
        r = d.get('publish_req')
        if not r or r.get('handled') or r.get('cancelled'): raise Oops('当前没有等待 AI 处理的存草稿请求')
        r['cancelled'] = {'at': board.now(), 'who': board.WHO}
        return {'ok': True}
    return board.edit(job, g)


def info(job):
    """给页面：设置、这一次请求（AI 回了什么／接了没有／是不是很久没动静／取消了没有）、导出闸。
    req.state：waiting 等 AI 接／doing AI 接了在做／stale 接了（或发出）超过 board.STALE 秒没回话（页面给“再叫一次”“取消”）／done 回过话／cancelled 用户取消了。"""
    d = board.load(job); r = d.get('publish_req'); ok, why = gate(job); req = None
    if r:
        k = key(job, d); s = board._sent(d).get(k) if k else None
        state = 'cancelled' if r.get('cancelled') else 'done' if r.get('handled') else 'doing' if s and s.get('picked') else 'waiting'
        age = board._age((s or {}).get('picked') or (s or {}).get('at') or r['at'])
        if state in ('waiting', 'doing') and age > board.STALE: state = 'stale'
        req = {'at': r['at'], 'mode': r.get('mode'), 'time': r.get('time'), 'note': r.get('note'), 'handled': r.get('handled'), 'cancelled': r.get('cancelled'),
               'picked': (s or {}).get('picked'), 'state': state, 'minutes': int(age // 60)}
    return {'settings': settings(d), 'gate': {'ok': ok, 'why': why}, 'req': req}


def listing(job, only=None):
    """给 AI（--wait、--list）：用户要存到蚁小二草稿。"""
    k = key(job)
    if not k or (only is not None and '发布' not in only): return
    r = board.load(job)['publish_req']
    when = f"定时发布：{r['time']}（upload 加 --schedule \"{r['time']}\"）" if r.get('mode') == 'schedule' else '只存草稿（不定时，用户自己在蚁小二客户端点发布）'
    say(f"【用户要存到蚁小二草稿】{r['at'][5:16]} 在审片台“发布”栏点了“存到蚁小二草稿”。{when}")
    say('给发布 AI 的话：' + (r['note'].replace('\n', '⏎') if r.get('note') else '（没写；按这个 IP 配好的平台全发）'))
    say('照 thinkcut-upload 技能做：① 用户已在审片栏点过“定稿”，导出 4K 成片（export）；② register-final 登记定稿、标题简介标签用“发布”栏里定稿的那一版（项目.json）；'
        '③ archive → archive-stills 看图验收 → seal；④ upload 预检 → upload --execute' + (f' --schedule "{r["time"]}"' if r.get('mode') == 'schedule' else '')
        + '；给发布 AI 的话里说只发哪几个平台的，临时只开那几个 target。只存草稿，绝不调用发布。')
    say(f'做完（或卡住）回一句，显示在“发布”栏：workbench {job.dir} --reply 发布 --note "存好了：抖音、快手…（N 个平台），定时 …；在蚁小二客户端点发布"')


def reply(job, text):
    if not text: die('回复要带 --note（存进了哪些平台、定时几点，或卡在哪）')
    def g(d):
        if not d.get('publish_req'): raise Oops('用户没有点过“存到蚁小二草稿”')
        if d['publish_req'].get('cancelled'): raise Oops('用户已经取消了这次“存到蚁小二草稿”：别再存了；用户重新点了会再交给你')
        d['publish_req']['handled'] = {'at': board.now(), 'note': text}
    try: board.edit(job, g)
    except Oops as e: die(str(e))
    say('发布已回复')
