# 分镜：把 AI 写的「分镜规格.json」（卡点词、动画镜头、音效）编译成带秒数的「分镜.json」，动画、小圆圈、音效、达芬奇都读它。
# 规格写法见 references/分镜规格.md。
import json, re, subprocess, sys
from .common import Job, die, rj, wj, say

# 音效只放重点（2026-11-12《水电阶段》用户：“每句话都有就等于每句话没有，所以你应该在重点位置加”）：
# 新写的 sfx 要说明是哪类重点；“画面重点”从当前进成片那一版动画的事件表取时间（anim.current_events）。
SFX_BEATS = ('开场钩子', '主题', '章节转场', '关键数字', '结论', '画面重点')
PEAK_MINUTE = 12        # 任意连续 60 秒超过这么多个音效只提醒“注意”，不拦（全片平均每分钟 8 个才拦）
SHORTEST = 2            # 卡点原话找不到时，最多缩短到原话的一半、且不少于这么多字
SHORT_WINDOW = 60.0     # 缩短后的词只在起找点之后这么多秒里找，免得对到很远的同字


def find_word(text, pos, tokens, word, after, end=False):
    """在 after 秒之后找原话，返回 (秒, 实际用的词)；找不到就去掉头尾的字逐步缩短再找（只在之后 60 秒内），都找不到返回 (None, word)。"""
    def first(w, limit=None):
        k = 0
        while True:
            k = text.find(w, k)
            if k < 0: return None
            t0 = tokens[pos[k]]['t0']
            if t0 >= after - 1e-6:
                if limit is not None and t0 > after + limit: return None
                return tokens[pos[k + len(w) - 1]]['t1'] if end else t0
            k += 1
    w = re.sub(r'\s', '', word)
    t = first(w)
    if t is not None: return t, w
    for size in range(len(w) - 1, max(SHORTEST, (len(w) + 1) // 2) - 1, -1):   # 最短缩到一半（至少 2 个字），再短容易对错
        # 取词尾时优先保留词尾，否则优先保留词头
        starts = range(len(w) - size, -1, -1) if end else range(0, len(w) - size + 1)
        for st in starts:
            t = first(w[st:st + size], SHORT_WINDOW)
            if t is not None: return t, w[st:st + size]
    return None, w


def live_fields(s, sh, T):
    """镜头里的实拍/图片（2026-11-21 新16，写法见 references/分镜规格.md「实拍和图片」），编进 分镜.json 给总装用（anim.prepare_live）：
    "实拍": 路径 或 {"文件": 路径, "入点": 秒}       整个镜头放一段做好的实拍
    "图片": 路径 或 [路径, …]，"铺法": "完整"/"裁满"   整个镜头放图（几张平分时长）
    "盖实拍": [{"从": 卡点/秒, "到": 卡点/秒, "文件": 路径, "入点": 秒}, …]   动画镜头里这一段换成实拍（从、到不写就是镜头开头、结尾）"""
    sid, out = s['id'], {}
    def src(v, what):
        if isinstance(v, str) and v: return {'文件': v, '入点': 0.0}
        if isinstance(v, dict) and isinstance(v.get('文件'), str) and v['文件']:
            return {'文件': v['文件'], '入点': float(v.get('入点') or 0)}
        die(f'镜头 {sid} 的「{what}」要写文件路径，或 {{"文件": 路径, "入点": 秒}}：{v}')
    if '实拍' in s and '图片' in s: die(f'镜头 {sid} 不能同时写 实拍 和 图片：整段实拍写 实拍，整段放图写 图片')
    if '占位图' in s:      # 缺画面时先用生成图占位（2026-11-22 用户定）：只写画面描述，图由 placeholder-images 生成到 实拍/占位图/
        from .draft import placeholder_files
        if '实拍' in s or '图片' in s: die(f'镜头 {sid} 写了 占位图 就不要再写 实拍/图片：有了真素材就把 占位图 删掉、换成 实拍 或 图片')
        ds = s['占位图'] if isinstance(s['占位图'], list) else [s['占位图']]
        if not ds or not all(isinstance(x, str) and x.strip() for x in ds): die(f'镜头 {sid} 的「占位图」要写画面描述，几个场景写成列表（一个场景一张）：{s["占位图"]}')
        s = dict(s, 图片=placeholder_files(sid, len(ds)), 铺法=s.get('铺法', '裁满')); out['占位图'] = ds
    if '文字卡' in s:      # 2026-11-23 初稿模式实测：机器配音片子里自我介绍、“关注我”这类本该露脸的句子，放一张中性的文字卡，不再生成不相干的图
        from .draft import text_card_file
        if any(k in s for k in ('实拍', '图片', '占位图')): die(f'镜头 {sid} 写了 文字卡 就不要再写 实拍/图片/占位图')
        ls = s['文字卡'] if isinstance(s['文字卡'], list) else [s['文字卡']]
        if not ls or len(ls) > 3 or not all(isinstance(x, str) and x.strip() for x in ls): die(f'镜头 {sid} 的「文字卡」写一到三行字（列表）：{s["文字卡"]}')
        s = dict(s, 图片=[text_card_file(sid)], 铺法='裁满'); out['文字卡'] = [x.strip() for x in ls]
    if '实拍' in s: out['实拍'] = src(s['实拍'], '实拍')
    if '图片' in s:
        fs = s['图片'] if isinstance(s['图片'], list) else [s['图片']]
        if not fs or not all(isinstance(f, str) and f for f in fs): die(f'镜头 {sid} 的「图片」要写图片路径，几张写成列表：{s["图片"]}')
        if s.get('铺法', '完整') not in ('完整', '裁满'): die(f'镜头 {sid} 的「铺法」只能是 完整（整张放进画面区）或 裁满（铺满画面区、裁掉多的）')
        out['图片'] = {'文件': fs, '铺法': s.get('铺法', '完整')}
    if s.get('盖实拍'):
        if out: die(f'镜头 {sid} 整段已经是实拍/图片，不用再写 盖实拍')
        cs, res = s['盖实拍'] if isinstance(s['盖实拍'], list) else [s['盖实拍']], []
        for c in cs:
            if not isinstance(c, dict): die(f'镜头 {sid} 的「盖实拍」每一段写成 {{"从": 卡点, "到": 卡点, "文件": 路径}}：{c}')
            a = T(c['从']) if c.get('从') is not None else sh['a']; b = T(c['到']) if c.get('到') is not None else sh['b']
            if not (sh['a'] - 1e-6 <= a < b <= sh['b'] + 1e-6): die(f'镜头 {sid} 盖实拍的区间 {a}–{b} 秒不在镜头 {sh["a"]}–{sh["b"]} 秒里（或起点不早于终点）')
            res.append(dict(a=max(a, sh['a']), b=min(b, sh['b']), **src(c, '盖实拍')))
        res.sort(key=lambda c: c['a'])
        for x, y in zip(res, res[1:]):
            if y['a'] < x['b'] - 1e-6: die(f'镜头 {sid} 的两段盖实拍重叠了（{x["a"]}–{x["b"]} 和 {y["a"]}–{y["b"]} 秒）')
        out['盖实拍'] = res
    return out


BADGE = ('_标注', '角标', 'AI生成', 'AI 生成')      # 文件名带这些字的图，多半是加了“示意图·AI 生成”角标的那一版


def picture_notes(job, shots, tokens):
    """编分镜时对实拍/图片镜头的两条提醒（2026-11-22，261122 反馈单；只提醒不拦）：
    1. AI 生成的图不加“示意图·AI 生成”角标（用户：“所有AI生成的都去掉，右上角‘AI生成’提示标签都去掉，以后都注意”）：镜头用的文件名带 _标注／角标／AI生成 的列出来。
    2. 口播在列举几个场景时一个场景一张图（用户：“2个场景你还用1张图，那就不合适了”）：花字.json 里“关键词条”（use 是 keywords）逐个出的词，
       有 2 个以上落在同一个图片镜头里、而这个镜头的图比词少，就列出来。"""
    notes = []
    for s in shots:
        fs = list((s.get('图片') or {}).get('文件') or []) + [x['文件'] for x in ([s['实拍']] if s.get('实拍') else []) + list(s.get('盖实拍') or [])]
        bad = [n for n in (f.rsplit('/', 1)[-1] for f in fs) if any(k in n for k in BADGE) and not any(k in n for k in ('无角标', '去角标', '不带角标', '无标注'))]
        if bad: notes.append(f"镜头 {s['id']} 用的 {'、'.join(bad)} 文件名像是加了角标的那一版：AI 生成的图不加“示意图／AI 生成”角标（2026-11-22 用户定），换成没角标的原图")
    f = job.dir / '花字.json'
    if not f.exists(): return notes
    spec = rj(f); items = spec.get('items', []) if isinstance(spec, dict) else spec
    text = ''.join(t['c'] for t in tokens); pos = []
    for i, t in enumerate(tokens): pos += [i] * len(t['c'])
    for it in items:
        subs = [x for x in (it.get('items') or []) if isinstance(x, dict) and (x.get('at') or x.get('t'))] if isinstance(it, dict) and it.get('use') == 'keywords' else []
        if len(subs) < 2: continue
        t0, _ = find_word(text, pos, tokens, str(it.get('at') or subs[0].get('at') or subs[0]['t']), 0.0)
        at = []
        for x in subs:
            t, _ = find_word(text, pos, tokens, str(x.get('at') or x['t']), (at[-1][0] if at else t0 or 0.0))
            if t is not None: at.append((t, str(x.get('t') or x.get('at'))))
        for s in shots:
            pics = (s.get('图片') or {}).get('文件')
            inside = [w for t, w in at if s['a'] - 1e-6 <= t < s['b'] - 1e-6]
            if pics and len(inside) >= 2 and len(pics) < len(inside):
                notes.append(f"镜头 {s['id']} 用 {len(pics)} 张图配了口播列举的 {len(inside)} 个场景（{'、'.join(inside)}）：一个场景一张图，卡在说到它的那个词；"
                             "同一组对比用同一个机位、只换要比的那一项（2026-11-22 用户定）")
    return notes


def compile_spec(spec, tokens, end_default, pip_min=6.0, pushes=None, events=None, notes=None, previous=None, no_face=False):
    # no_face：口播是机器配音占位（没有人脸，2026-11-22 用户定）——镜头要把全片铺满（不能有露脸段），也不放人脸小圆圈。
    """previous：上次 分镜.json 的卡点时间（顺序找时排先后用）。notes：传一个列表进来，会收到“缩短匹配了哪些词”这类提醒（beats 命令打印）。events：当前进成片的动画画面事件。"""
    text = ''.join(t['c'] for t in tokens); pos = []
    for i, t in enumerate(tokens):
        pos += [i] * len(t['c'])
    notes = notes if notes is not None else []

    # 卡点：[词, 从哪之后找, 偏移秒, 取词尾]（2026-11-12《水电阶段》：换口播后“第几秒之后找”全错）。
    # 默认“顺序找”：每个卡点在上一个卡点之后找原话；“上一个”按上次 分镜.json 里的时间排（previous），新加的卡点按写的秒数排。
    #   “从哪之后找”写卡点名 = 在那个卡点之后找；写数字只用来给新卡点排位置。
    # 规格里写 "找法": "秒数" 时照老办法：数字 = 第几秒之后找（烟道等老片子）。
    raw = {name: list(v) + [None, 0.0, False][len(v) - 1:] for name, v in spec['anchors'].items()}
    by_seconds = spec.get('找法', '顺序' if previous else '秒数') == '秒数'
    prev = previous or {}
    # 2026-11-22 新6：没写“从哪之后找”（null）的新卡点，从上一个镜头的卡点往后找（镜头 b 用同一镜头的 a；镜头 a 用前一个镜头的 b/a），
    # 不再只跟着书写顺序的前一个——新卡点插在中间时，书写顺序的前一个常常正好写着“在它之后找”，就绕成了圈
    implicit = lambda n: n not in prev and raw[n][1] is None
    shot_base, sp_shots = {}, spec.get('shots', [])
    for k, sh in enumerate(sp_shots):
        for side in ('a', 'b'):
            n = sh.get(side)
            if not isinstance(n, str) or n not in raw or n in shot_base or not implicit(n): continue
            cand = [sh['a']] if side == 'b' else []
            for q in reversed(sp_shots[:k]): cand += [q.get('b'), q.get('a')]
            base = next((c for c in cand if isinstance(c, str) and c in raw and c != n), None)
            if base: shot_base[n] = base
    def key(n, i):
        if n in prev: return (prev[n] - (raw[n][2] or 0.0), i)
        a = raw[n][1]
        if isinstance(a, (int, float)) and not isinstance(a, bool): return (float(a), i)
        return (None, i)
    names = list(raw); keys = [key(n, i) for i, n in enumerate(names)]
    for _ in range(len(names)):                     # 新卡点排在“上一个镜头的卡点”后面
        for i, n in enumerate(names):
            b = shot_base.get(n)
            if keys[i][0] is None and b and keys[names.index(b)][0] is not None: keys[i] = (keys[names.index(b)][0] + 1e-6, i)
    for i, (k, _) in enumerate(keys):              # 还是没有时间可排的：跟在书写顺序的前一个后面
        if k is None: keys[i] = ((keys[i - 1][0] if i else 0.0) + 1e-6, i)
    order = [n for _, n in sorted(zip(keys, names))]
    A, START, busy = {}, {}, []

    def near(t, n=10):
        """t 秒前后各 n 个字的口播原话，“｜”是 t 的位置。"""
        k = next((j for j, c in enumerate(text) if tokens[pos[j]]['t0'] >= t - 1e-6), len(text))
        return f"{text[max(0, k - n):k]}｜{text[k:k + n]}"
    def seen(word):
        """原话（找不到就用前半截）在口播里每次出现的秒数和前后文：[(秒, 前后文)]，最多 4 处。"""
        w = re.sub(r'\s', '', str(word))
        for size in range(len(w), max(SHORTEST, (len(w) + 1) // 2) - 1, -1):
            hits = [m.start() for m in re.finditer(re.escape(w[:size]), text)]
            if hits: return [(tokens[pos[k]]['t0'], near(tokens[pos[k]]['t0'])) for k in hits[:4]]
        return []
    def fix(name, word, at, d, end):
        """能照抄的写法。"""
        tail = ([d] if d or end else []) + ([True] if end else [])
        return '"' + name + '": ' + json.dumps([word, at] + tail, ensure_ascii=False)
    def before(t):
        """已对上、开口不晚于 t 秒的卡点里最晚的那个（照抄写法里用它当“从哪之后找”）。"""
        c = [(START[n], n) for n in START if START[n] <= t + 1e-6]
        return max(c)[1] if c else '开头'

    def leads_to(a, target):
        """a 顺着写明的“从哪之后找”（卡点名）一路找下去会不会找到 target（会的话拿 a 当 target 的起找点就绕圈）。"""
        hop = set()
        while isinstance(a, str) and a in raw and a not in hop:
            if a == target: return True
            hop.add(a); a = raw[a][1]
        return False

    def resolve(name):
        if name in A: return START[name]
        word, after, d, end = raw[name][:4]
        if name in busy:
            ring = busy[busy.index(name):] + [name]; occ = seen(word)
            die(f'卡点「{name}」的“从哪之后找”绕成了圈：{" → ".join(ring)}。改法（照抄一种）：\n  {fix(name, word, None, d, end)}   （null＝自动从上一个镜头的卡点往后找）'
                + ''.join(f'\n  {fix(name, word, before(t - 0.01), d, end)}   （口播 {t:.2f} 秒：{c}）' for t, c in occ[:2])
                + ('' if occ else f'\n  口播里没找到「{word}」，照原话改词'))
        busy.append(name)
        if isinstance(after, str) and after == '开头' and after not in raw:
            base, label = 0.0, '开头'
        elif isinstance(after, str) and after:
            if after not in raw: die(f'卡点「{name}」写在「{after}」之后找，但没有这个卡点。改法（照抄）：{fix(name, word, None, d, end)}（自动从上一个镜头的卡点往后找），或写已有的卡点名、"开头"')
            base, label = resolve(after), f'卡点「{after}」之后'
        elif by_seconds and isinstance(after, (int, float)) and not isinstance(after, bool):
            base, label = float(after), f'{after} 秒之后'
        elif name in shot_base and implicit(name) and shot_base[name] not in busy and not leads_to(shot_base[name], name):
            base, label = resolve(shot_base[name]), f'上一个镜头的卡点「{shot_base[name]}」之后'
        else:
            i = order.index(name); j = i - 1
            # 前一个正在等它（写着在它之后找）：再往前找一个，不绕圈；上次对上过的卡点也不跟在“按上一个镜头排位置”的新卡点后面（新卡点的位置是估的）
            while j >= 0 and (order[j] in busy or leads_to(order[j], name) or (name in prev and order[j] in shot_base and implicit(order[j]))): j -= 1
            base, label = (resolve(order[j]), f'上一个卡点「{order[j]}」之后') if j >= 0 else (0.0, '开头')
            # 和上一个卡点同一个词、同样取词头/词尾：是下一次说到这个词，不是同一处
            if j >= 0 and re.sub(r'\s', '', str(raw[order[j]][0])) == re.sub(r'\s', '', str(word)) and bool(raw[order[j]][3]) == bool(end): base += 0.001
        t0, used = find_word(text, pos, tokens, str(word), base, False)
        if t0 is None:
            occ = seen(word)
            die(f'口播里找不到「{word}」（卡点「{name}」，{label}，即 {base:.2f} 秒之后；缩短到一半也找不到）。\n'
                f'  {base:.2f} 秒附近的口播原话：{near(base)}'
                + ''.join(f'\n  「{word}」在 {t:.2f} 秒说过（{c}），在起找点之前。改法（照抄）：{fix(name, word, before(t - 0.01), d, end)}' for t, c in occ[:3])
                + ('' if occ else f'\n  口播里没有这句：照上面的原话改词，例如 {fix(name, near(base).split("｜")[1][:4] or str(word), after, d, end)}'))
        t = find_word(text, pos, tokens, used, t0, True)[0] if end else t0
        if used != re.sub(r'\s', '', str(word)): notes.append(f'卡点「{name}」原话「{word}」没找到，缩短成「{used}」对上 {t:.2f} 秒，核对一下（那里的原话：{near(t0)}）')
        START[name] = t0; A[name] = round(t + (d or 0.0), 3); busy.remove(name)
        return t0
    for name in order: resolve(name)
    A = {name: A[name] for name in raw}

    end = spec.get('end', end_default)
    def T(x):
        if isinstance(x, str):
            if x not in A and x in ('片尾', '开头'): return end if x == '片尾' else 0.0      # 2026-11-23：镜头要铺到片尾时写 "b": "片尾"，不用抄成片秒数
            if x not in A: die(f'分镜规格里用了没定义的卡点「{x}」')
            return A[x]
        return x
    BS = []
    for s in spec['shots']:
        sh = dict(id=s['id'], kind='B', a=T(s['a']), b=T(s['b']), warp=[[T(p[0]), p[1], p[2]] for p in s['warp']] if s.get('warp') else None)
        sh.update(live_fields(s, sh, T))
        BS.append(sh)
    BS.sort(key=lambda s: s['a'])
    SHOTS, t = [], 0.0
    for s in BS:
        if s['a'] > t + 1e-6: SHOTS.append(dict(id=f'F_{len(SHOTS)}', kind='F', a=t, b=s['a']))
        SHOTS.append(s); t = s['b']
    if t < end: SHOTS.append(dict(id=f'F_{len(SHOTS)}', kind='F', a=t, b=end))
    if no_face and any(s['kind'] == 'F' for s in SHOTS):
        die('口播是机器配音占位，没有人脸画面，分镜不能留露脸段：这几段还没有画面——' + '、'.join(f"{s['a']:.2f}–{s['b']:.2f} 秒" for s in SHOTS if s['kind'] == 'F')
            + '。用动画、实拍、图片（没有素材就写 "占位图": "画面描述" 生成占位图）把全片铺满，镜头首尾相接、第一个从 0 开始、最后一个到片尾')
    # 连续动画超过 pip_min 秒：整段放人脸小圆圈；right 里的镜头所在段放右下
    P = spec.get('pip', {}); right, never = set(P.get('right', [])), set(P.get('never', []))
    i = 0
    while i < len(SHOTS):
        j = i
        while j < len(SHOTS) and SHOTS[j]['kind'] == 'B': j += 1
        if j > i and SHOTS[j - 1]['b'] - SHOTS[i]['a'] > P.get('min_seconds', pip_min):
            side = 'R' if any(SHOTS[k]['id'] in right for k in range(i, j)) else 'L'
            for k in range(i, j): SHOTS[k]['pip'] = side
        i = max(j, i + 1)
    for s in SHOTS:
        if s['id'] in never: s.pop('pip', None)
    for s in SHOTS:
        if not s['b'] > s['a']: die(f"镜头 {s['id']} 起点不早于终点（{s['a']}–{s['b']}）")
    for x, y in zip(SHOTS, SHOTS[1:]):
        if abs(x['b'] - y['a']) > 1e-6: die(f"镜头 {x['id']} 和 {y['id']} 之间有缝或重叠")
    # 小圆圈位置（2026-11-12《水电阶段》用户：圆圈要躲开画面主体，放在任意位置都可以）：
    # pip.pos = {镜头: 位置}；位置 = TL/TC/TR/ML/MC/MR/BL/BC/BR（上中下×左中右）、[x, y]（4K 圆心坐标）或 "none"（这段不放）；
    # 镜头内分段换位置写 [[到哪, 位置], ...]，“到哪”是镜头时长的几成（0–1）或卡点名/秒。
    POSN = {'TL', 'TC', 'TR', 'ML', 'MC', 'MR', 'BL', 'BC', 'BR', 'none'}
    def place(p, where):
        if isinstance(p, str) and p in POSN: return p
        if isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) for x in p): return [round(p[0]), round(p[1])]
        die(f'小圆圈位置写错了（{where}）：{p}；要写 TL/TC/TR/ML/MC/MR/BL/BC/BR、[x, y] 或 "none"')
    byid0 = {x['id']: x for x in SHOTS}
    for sid, v in (P.get('pos') or {}).items():
        sh = byid0.get(sid) or die(f'pip.pos 里的镜头 {sid} 不在分镜里')
        if sh['kind'] != 'B': die(f'pip.pos 里的 {sid} 是露脸镜头，露脸镜头不放小圆圈')
        segs = v if isinstance(v, list) and v and isinstance(v[0], list) else [[1, v]]
        t0, out = sh['a'], []
        for to, p in segs:
            t1 = sh['a'] + (sh['b'] - sh['a']) * to if isinstance(to, (int, float)) and 0 <= to <= 1 else T(to)
            t1 = min(max(t1, sh['a']), sh['b'])
            if t1 > t0 + 1e-6: out.append([round(t0, 3), round(t1, 3), place(p, sid)])
            t0 = t1
        if out and out[-1][1] < sh['b'] - 1e-6: out[-1][1] = round(sh['b'], 3)
        if all(x[2] == 'none' for x in out): sh.pop('pip', None); sh.pop('pip_at', None); continue
        sh['pip_at'] = out
        sh.setdefault('pip', 'L')
    if no_face:
        for s in SHOTS: s.pop('pip', None); s.pop('pip_at', None)
    snd, gain = spec.get('sounds', {}), spec.get('gain', {})
    SFX = []
    ev = sorted(events or [], key=lambda e: e['t'])
    for e in spec.get('sfx', []):
        extra = {}
        if isinstance(e, dict):
            beat = e.get('beat') or die(f'音效 {e} 没写 beat（是哪类重点：' + '、'.join(SFX_BEATS) + '）')
            if beat not in SFX_BEATS: die(f'音效 {e} 的 beat「{beat}」不是重点类：' + '、'.join(SFX_BEATS))
            key, d, role = e.get('sound') or die(f'音效 {e} 没写 sound'), float(e.get('d', 0.0)), e.get('role', '')
            if e.get('event'):
                # 画面事件：只认当前进成片那一版动画登记的事件表，名字或镜头对不上就停（防止读到旧动画的时间）
                named = [x for x in ev if e['event'] in str(x.get('name') or x.get('text') or '') and (not e.get('shot') or x.get('shot') == e['shot'])]
                # 事件时间还得落在它那个镜头里（换口播/换动画后没重出，旧事件表的时间会跑到镜头外）
                inside = lambda x: x.get('shot') in byid0 and byid0[x['shot']]['a'] - 0.034 <= x['t'] <= byid0[x['shot']]['b'] + 0.034
                hit = [x for x in named if inside(x)]
                if named and not hit:
                    x = named[0]; die(f"画面事件「{e['event']}」在事件表里是 {x['t']:.2f} 秒，不在镜头 {x.get('shot')} 里（{x.get('project')} 的事件表是旧的？重出动画并登记后再 beats）")
                if not hit: die(f"音效要卡的画面事件「{e['event']}」{'（' + e['shot'] + '）' if e.get('shot') else ''}不在当前动画的事件表里"
                                '（合成/动画清单.json 登记的那一版）：动画换过版本就按新事件表改名字，或先出片登记')
                k = int(e.get('nth', 1)) - 1
                if not 0 <= k < len(hit): die(f"画面事件「{e['event']}」只有 {len(hit)} 个，nth={e.get('nth')} 不对")
                t = hit[k]['t']; extra = {'event': hit[k].get('name') or hit[k].get('text'), 'shot': hit[k].get('shot'), 'project': hit[k].get('project')}
            else:
                t = T(e.get('at') if e.get('at') is not None else die(f'音效 {e} 要写 at（卡点名或秒）或 event（画面事件）'))
            extra.update(beat=beat, why=e.get('why', ''))
        else:
            e = list(e) + [0.0, ''][len(e) - 2:]
            key, d, role = e[1], e[2], e[3]; t = T(e[0])
        f = snd.get(key, key)
        SFX.append((t + d, f, role, gain.get(key, gain.get(f, gain.get('default'))), extra))
    # 小圆圈按时段平滑缩小（镜头推近讲重点时给画面让位）：pip.shrink = [[起, 止], ...]，卡点名或秒
    shrink = [{'a': round(T(x), 3), 'b': round(T(y), 3)} for x, y in P.get('shrink', [])]
    # 推拉文件（2026-10-24《精装房》：手写缩小时段和“唰”容易漏、错位）：[{shot, t0, t1, kind: in|out}]
    # 每次推拉起点自动加一声推拉音效；带小圆圈的镜头，从推近起点到拉回终点（没拉回就到镜头结束）自动缩小。手写的照样保留，重复的跳过。
    if pushes:
        zoom = snd.get('ZOOM') or (die('分镜规格.json 的 sounds 里要有 ZOOM（推拉音效，默认 唰音效刷出标题.mp3）') if spec.get('push_sound') else None)
        byid = {x['id']: x for x in SHOTS}
        for m in sorted(pushes, key=lambda m: m['t0']):
            if m.get('shot') not in byid: die(f"推拉文件里的镜头 {m.get('shot')} 不在分镜里")
            # 推拉默认不响（2026-11-12：音效只放重点，“唰”少用）；要每次推拉都响写 "push_sound": true
            if spec.get('push_sound') and not any(f == zoom and abs(t - m['t0']) < 0.15 for t, f, *_ in SFX):
                SFX.append((float(m['t0']), zoom, 'lead', gain.get('ZOOM', gain.get(zoom, gain.get('default'))), {}))
            sh = byid[m['shot']]
            if m['kind'] != 'in' or not sh.get('pip'): continue
            # 拉回可以在后面的镜头里（推近一直延续到下一镜）：取这次推近之后、下一次推近之前最近的拉回；
            # 没有拉回就缩到这段连续小圆圈的末尾（相邻、同一侧小圆圈的镜头算一段）
            later = sorted((x for x in pushes if float(x['t0']) > float(m['t0'])), key=lambda x: float(x['t0']))
            nxt = next((x for x in later if x['kind'] == 'out'), None)
            if nxt and any(x['kind'] == 'in' and float(x['t0']) < float(nxt['t0']) for x in later): nxt = None
            i = SHOTS.index(sh); end = sh['b']
            while i + 1 < len(SHOTS) and SHOTS[i + 1].get('pip') == sh.get('pip') and abs(SHOTS[i + 1]['a'] - SHOTS[i]['b']) < 1e-6:
                i += 1; end = SHOTS[i]['b']
            shrink.append({'a': round(float(m['t0']), 3), 'b': round(min(float(nxt['t1']), end) if nxt else end, 3)})
        # 手写的和自动的时段有重叠就合并成一段（重叠处不能缩两次）
        merged = []
        for w in sorted(shrink, key=lambda w: w['a']):
            if merged and w['a'] <= merged[-1]['b'] + 1e-6: merged[-1]['b'] = max(merged[-1]['b'], w['b'])
            else: merged.append(dict(w))
        shrink[:] = merged
    SFX.sort(key=lambda x: (x[0], x[1], x[2]))
    for w in shrink:
        if not w['b'] > w['a']: die(f"小圆圈缩小时段 {w} 起点不早于终点")
    out = {'note': '由 thinkcut-edit beats 从 分镜规格.json 生成，不要手改', 'range': [SHOTS[0]['a'], SHOTS[-1]['b']], 'anchors': A, 'shots': SHOTS,
           'sfx': [{'t': round(t, 3), 'file': f, 'gain_db': g, 'role': r, **x} for t, f, r, g, x in SFX]}
    if shrink:
        out['pip_shrink'] = {'windows': shrink, 'to': float(P.get('shrink_to', 0.64)), 'ramp': float(P.get('ramp', 0.7))}
    return out


def per_minute(times, window=60.0):
    """任意连续 60 秒里最多几个音效，返回 (个数, 从第几秒起)。"""
    times = sorted(times); best, at, j = 0, 0.0, 0
    for i, t in enumerate(times):
        while times[j] < t - window + 1e-9: j += 1
        if i - j + 1 > best: best, at = i - j + 1, times[j]
    return best, at


def run(job: Job):
    spec = rj(job.p('分镜规格.json'))
    tok = rj(job.p('口播字时间.json'))['tokens']
    pf = spec.get('推拉文件'); pushes = rj(job.p(pf)) if pf else None
    if pf and not isinstance(pushes, list): die(f'推拉文件 {pf} 要是列表')
    from .anim import current_events
    notes = []
    old = rj(job.p('分镜.json')) if job.p('分镜.json').exists() else {}
    from .draft import machine_voice
    out = compile_spec(spec, tok, round(job['frames'] / float(job.fps), 3), job['pip_min_seconds'], pushes, current_events(job), notes, old.get('anchors'),
                       no_face=bool(machine_voice(job)))
    # 2026-11-13 审查：没写“找法”的规格，第一次按“秒数”找、重跑按“顺序”找，同一个卡点两次可能对到不同的地方——以前不吭声，现在列出来
    moved = sorted((n, t0, out['anchors'][n]) for n, t0 in (old.get('anchors') or {}).items() if n in out['anchors'] and abs(out['anchors'][n] - t0) > 1.0)
    if moved:
        notes.append(f"{len(moved)} 个卡点和上次差了 1 秒以上（换口播、删句后正常；口播没变就要核对，同一句话在口播里出现两次时容易对错）："
                     + '、'.join(f'{n} {a:.1f}→{b:.1f} 秒' for n, a, b in moved[:8]) + ('…' if len(moved) > 8 else '')
                     + ('。想固定按“第几秒之后找”，在分镜规格里写 "找法": "秒数"' if '找法' not in spec else ''))
    script = spec.get('音效脚本')
    # 改分镜重跑 beats 不能把音效冲掉（2026-11-12《水电阶段》：加 R36 后重跑，审核线 0 个音效）
    if not out['sfx'] and old.get('sfx') and not script:
        out['sfx'] = old['sfx']
        notes.append(f"分镜规格.json 没写 sfx，沿用上次 分镜.json 的 {len(old['sfx'])} 个音效（卡点变了位置会错：把音效写进分镜规格的 sfx，"
                     "或写 \"音效脚本\": \"合成/音效节拍表.py\" 让 beats 每次自动重跑）")
    wj(job.p('分镜.json'), out)
    from .draft import text_cards
    made = text_cards(job, out['shots'])
    if made: say(f"文字卡 {len(made)} 张 → 实拍/文字卡/（{'、'.join(made)}）")
    notes += picture_notes(job, out['shots'], tok)
    from .draft import animation_notes      # 2026-11-23 初稿模式实测：讲尺寸、位置、步骤的镜头却全用了占位图
    notes += animation_notes(spec)
    from . import anim      # 2026-11-21 新3：画面写到地图的镜头，编分镜时就提醒（出样张、出片前会硬拦），别等画完才知道不能用
    for sid, mw in anim.map_unconfirmed(job, anim._spec_director(job.dir)):
        notes.append(f'镜头 {sid} 的画面写到“{"、".join(mw)}”：' + anim.MAP_HINT)
    if script:
        sp = job.p(*script.split('/'))
        if not sp.exists(): die(f'分镜规格里的音效脚本 {script} 不存在')
        say(f'重跑音效脚本 {script}（它往 分镜.json 写 sfx）…')
        r = subprocess.run([sys.executable, str(sp)], cwd=str(job.dir), stdin=subprocess.DEVNULL)
        if r.returncode: die(f'音效脚本 {script} 出错了，分镜.json 的音效可能是空的：先修好再 beats')
        out = rj(job.p('分镜.json'))
        if not out.get('sfx'): die(f'音效脚本 {script} 跑完 分镜.json 还是 0 个音效')
    B = [s for s in out['shots'] if s['kind'] == 'B']
    say(f"{len(out['anchors'])} 个卡点，{len(out['shots'])} 个镜头（动画 {len(B)}，带小圆圈 {sum(1 for s in B if s.get('pip'))}），{len(out['sfx'])} 个音效 → 分镜.json")
    for s in out['shots']:
        say(f"{s['a']:7.2f}-{s['b']:7.2f} {s['kind']} {s['id']}{'  +小圆圈' + s['pip'] if s.get('pip') else ''}"
            + ('  实拍' if s.get('实拍') else '  占位图' if s.get('占位图') else '  文字卡' if s.get('文字卡') else '  图片' if s.get('图片') else '') + ''.join(f"  盖实拍 {c['a']:.2f}-{c['b']:.2f}" for c in s.get('盖实拍') or [])
            + (' ' + ' → '.join(f"{x[2] if isinstance(x[2], str) else tuple(x[2])}" for x in s['pip_at']) if s.get('pip_at') else ''))
    n, at = per_minute([e['t'] for e in out['sfx']]); limit = int(job.get('sfx_per_minute', 8))
    avg = len(out['sfx']) / (job['frames'] / float(job.fps) / 60)
    say(f'音效平均每分钟 {avg:.1f} 个（上限 {limit}），最密的一分钟（{at:.0f} 秒起）{n} 个' + ('【超了：只留重点，sfx 那步会停】' if avg > limit else '') + (f'【注意：这一分钟超过 {PEAK_MINUTE} 个，听一下是不是太密】' if n > PEAK_MINUTE else ''))
    for x in notes: say('【注意】' + x)
