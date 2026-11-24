# 交审前自检（2026-11-01 从《别墅地下室防水》合成/交审前自检.py 收进来）：气口、音效对画面、动画闪烁、露脸段空白、代码动画标签压物体、人挡字。
# 结果写 合成/自检结果.json；有不过的返回 False。气口那项 review 建审核线前也会查，不过就不建。
import os, re, subprocess
from pathlib import Path
from .common import Job, die, rj, wj, say

MAX_GAP = 0.27          # 秒：成片上相邻两字之间的停顿上限（用户：气口要干净）
BLANK = 24              # 帧：露脸段里没人说话、也没有花字超过这么久算空白（约 0.8 秒）
KEY_TOL = 0.15          # 秒：大字、结论音效的起点和关键词起点最多差这么多（2026-11-21 新12）
BIG = ('koubo', '人挡字')  # 大字：口播大字、人挡字（人后大字）
# 确认原话里明显表示“用户没看过”的字样（2026-11-21 新21：/goal 下会话自己记样张确认，words 写“用户未逐张看”）
NOT_SEEN = re.compile(r'未逐张|没逐张|没有逐张|没看|没有看|未看|未过目|没过目|未确认|没确认|没有确认|未经用户|用户未(?:看|确认|逐|过目)')


def not_seen(words):
    """用户原话里写着用户没看过（anim-confirm 普通样张确认遇到就该拒收、改用 --免看；check、export 当成没看过）。"""
    return bool(NOT_SEEN.search(words or ''))


def unseen_samples(job: Job):
    """用户没看过样张的动画镜头：[(工程名, 镜头, [图片], 原因)]（2026-11-21 新21）。check 汇总列出，export 前拦。两种：
    ① 授权免看（anim-confirm --免看）放行、还没补看的；② 普通样张确认，但原话里写着用户没看（如“用户未逐张看”）。
    之后有一条真确认（不带免看、原话不是“没看”）且对得上当前代码的就算看过；代码改过、老确认已作废的不列（渲染前 gate 会拦）。"""
    from . import anim
    out = []
    for p, s, files in anim.unreviewed_waivers(job):
        recs = [x for x in anim._records(job)['items'] if x['kind'] == '样张' and x['project'] == p and s in x['shots']]
        if any(not x.get('免看') and not not_seen(x.get('words')) for x in recs): continue    # 补看过（unreviewed_waivers 把“没看”的确认也当成补看）
        out.append((p, s, files, '授权免看放行，用户还没补看'))
    for r in anim._records(job)['items']:
        if r['kind'] != '样张' or r.get('免看') or not not_seen(r.get('words')): continue
        proj = Path(r.get('工程路径') or job.dir / r['project'])
        if not proj.is_dir(): continue
        for s in r['shots']:
            fp, fs = anim.fingerprint(job, proj, r['shots']), anim.fingerprint(job, proj, [s])
            if not (r.get('fingerprint') == fp or (r.get('fingerprints') or {}).get(s) == fs): continue
            if any(not x.get('免看') and not not_seen(x.get('words')) for x in anim._confirmed(job, '样张', proj, s, fp, fs)): continue
            if any((p, x) == (r['project'], s) for p, x, *_ in out): continue
            out.append((r['project'], s, r['files'], f"确认原话写着用户没看：“{r['words'][:40]}”"))
    return out


def _spoken(tokens):
    text = ''.join(t['c'] for t in tokens); pos = []
    for i, t in enumerate(tokens): pos += [i] * len(t['c'])
    return text, pos


def _starts(text, pos, tokens, word, a, b):
    """word 在口播里每次开口的时刻（只要 a–b 秒之间的）。"""
    w = re.sub(r'\s', '', word or ''); out, k = [], 0
    while w and (k := text.find(w, k)) >= 0:
        t0 = tokens[pos[k]]['t0']
        if a - 1e-6 <= t0 <= b + 1e-6: out.append(t0)
        k += 1
    return out


def _flower_keys(it):
    """花字.json 一条里的关键词：口播大字 lines 里【】〖〗括起来的词（按出现顺序）；人挡字是它的字。"""
    if it.get('use') == '人挡字': return [it.get('text') or it.get('字') or '']
    return [m.group(1) or m.group(2) for m in re.finditer(r'【(.+?)】|〖(.+?)〗', ' '.join(it.get('lines') or []))]


def _sentence_rows(job: Job):
    """结论音效要找“那一句”。字幕表是 subs 才写的，而流程里 sfx 在 subs 之前：以前 sfx 时字幕表还没有，结论音效差多少都查不到、
    说明里写的“sfx 直接停”落空，到 check 才报（2026-11-24 初稿模式第二次实测，差 0.32 秒）。字幕表没有就按交付 SRT 的句子顺着查。"""
    p = job.p('合成', '字幕', '字幕表.json')
    if p.exists(): return rj(p)
    srt = job.get('srt')
    if not srt or not Path(srt).is_file(): return []
    from .subs import read_srt
    fps = float(job.fps)
    return [{'start': round(a * fps), 'end': round(b * fps), 'text': re.sub(r'[，。；：,;、？！?!\s/]', '', raw), 'keys': []} for a, b, raw in read_srt(srt)]


def keyword_offsets(job: Job, rows=None):
    """大字和结论类音效该卡在关键词（被高亮的词）的起点，不是句首（2026-11-21 新12：“但那是营销行为”卡在句首，音和字早约 0.3 秒）。
    大字＝花字表里的口播大字、人挡字，关键词取 花字.json 里【】〖〗括起来的词（人挡字取它的字）；
    音效＝beat 是“结论”的，和落在大字出场 ±0.5 秒内的；关键词取同一个大字的，否则取那句字幕的蓝色重点词（字幕表 keys 和 job 重点词里这句有的）。
    人挡字是铺在人身后的背景字，可以比关键词早出现（跟着整句话出来），只查晚了的；落在它出场上的音效卡它的出场或卡关键词都算对（2026-11-21 用户定放宽：“设计经验”早 0.8 秒是过审的）。
    返回 ([(秒, 什么, 关键词, 关键词起点, 差秒)], [大字配了“唰”的音效])。关键词没说出来、那句没有重点词的不判。
    rows：还没写进 合成/音效表.json 的音效表（sfx 写之前先查，2026-11-22 升成硬拦）；不给就读 音效表.json。"""
    from .resolve import flower_rows
    f = job.p('口播字时间.json')
    if not f.exists(): return [], []
    tok = rj(f)['tokens']; text, pos = _spoken(tok); fps = float(job.fps)
    spec_p = job.dir / '花字.json'; spec = rj(spec_p) if spec_p.exists() else []
    spec = spec['items'] if isinstance(spec, dict) else spec
    bigs = []
    for x in flower_rows(job):
        if x.get('use') not in BIG or not 1 <= int(x.get('n', 0)) <= len(spec) or spec[int(x['n']) - 1].get('use') != x['use']: continue
        a = int(x['start']) / fps; b = float(x.get('until') or int(x['end']) / fps)
        for k in _flower_keys(spec[int(x['n']) - 1]):
            hits = _starts(text, pos, tok, k, a - 1.0, b)
            if hits: h = min(hits, key=lambda h: abs(h - a)); bigs.append((a, f"第 {x['n']} 个花字（{x['use']}）", k, h, x['use'] == '人挡字' and a < h)); break
    out = [(round(a, 2), what, k, round(h, 2), round(a - h, 2)) for a, what, k, h, early in bigs if abs(a - h) > KEY_TOL and not early]
    subs = _sentence_rows(job)
    kws = list(job.get('subtitle_keywords') or []); whoosh = []
    big_at = [int(x['start']) / fps for x in flower_rows(job) if x.get('use') in BIG]
    sp = job.p('合成', '音效表.json')
    for e in (rows if rows is not None else rj(sp) if sp.exists() else []):
        t = float(e['at']) if 'at' in e else (int(e['record']) + int(e.get('sync_frame_in_sel', 0))) / fps
        name = os.path.basename(e['file'])
        if any(abs(a - t) <= 0.5 for a in big_at) and '唰' in name: whoosh.append((round(t, 2), name, '大字不配“唰”：营销类结论用错误音，别的用叮、点击这类'))
        big = next((g for g in bigs if abs(g[0] - t) <= 0.5), None)
        if big: cand = [(big[3], big[2])] + ([(big[0], big[2])] if big[4] else [])
        elif e.get('beat') == '结论':
            row = next((r for r in subs if r['start'] - 4 <= t * fps < r['end']), None)
            plain = row['text'].replace(' / ', '').replace(' ', '') if row else ''
            keys = (set(row.get('keys') or []) | {k for k in kws if k in plain}) if row else set()
            cand = [(h, k) for k in keys for h in _starts(text, pos, tok, k, row['start'] / fps - 0.2, row['end'] / fps)]
        else: continue
        if not cand: continue
        h, k = min(cand, key=lambda c: abs(c[0] - t))
        if abs(t - h) > KEY_TOL: out.append((round(t, 2), f"音效 {name}（{e.get('beat') or '大字'}）", k, round(h, 2), round(t - h, 2)))
    return sorted(out), whoosh


def gaps(job: Job):
    f = job.p('口播字时间.json')
    if not f.exists(): die('没有 口播字时间.json：先跑 words')
    tok = rj(f)['tokens']; lim = float(job.get('max_gap', MAX_GAP))
    return [(round(a['t1'], 2), round(b['t0'] - a['t1'], 2), a['c'] + '|' + b['c']) for a, b in zip(tok, tok[1:]) if b['t0'] - a['t1'] >= lim]


def gate(job: Job, allow=False):
    """review 用：有长停顿就停下（人工气口精修被跳过时用户两次听出来，2026-11-01）"""
    g = gaps(job)
    if not g: return
    msg = f'成片上有 {len(g)} 处 ≥{job.get("max_gap", MAX_GAP)} 秒的停顿（前几处：' + '；'.join(f'{t} 秒「{w}」{d} 秒' for t, d, w in g[:6]) + '）'
    if allow: say('【注意】' + msg + '；按 --allow-gaps 照建'); return
    die(msg + '\n人工气口精修没做完：回 a-roll 按“气口精修与手改学习”做完、重新交付并 words；确实要留（比如用户要的停顿）加 --allow-gaps')


def rendang_problems(job: Job):
    """人挡字（包装系统 rendang.py 出的整幅片段，花字表 kind=人挡字）还能不能放：遮挡比例通过、还在露脸段（分镜可能改过）、
    片段比 合成/A肉.mov 新（口播画面重出过就要重出人挡字）。review 有问题就不建，check 列为一项。"""
    from .resolve import flower_rows
    rows = [x for x in flower_rows(job) if x.get('kind') == '人挡字']
    if not rows: return []
    out = []; fps = float(job.fps); aroll = job.p('合成', 'A肉.mov')
    shots = rj(job.p('分镜.json'))['shots'] if job.p('分镜.json').exists() else None
    for x in rows:
        name = f"第 {x['n']} 个人挡字“{x.get('字', '')}”（{x['start'] / fps:.2f} 秒）"
        cov = x.get('遮挡') or {}
        if not cov.get('通过'): out.append(name + (cov['说明'] if '说明' in cov else f"被挡 {cov.get('平均', 0):.0%}，不在范围 {cov.get('范围')}") + f"：{cov.get('建议', '')}")
        if shots is None: out.append(f'{name}：没有 分镜.json，确认不了是不是露脸段')
        else:
            bad = [s['id'] for s in shots if s['kind'] != 'F' and round(s['a'] * fps) < int(x['end']) and int(x['start']) < round(s['b'] * fps)]
            if bad: out.append(f"{name}压到了非露脸镜头 {'、'.join(map(str, bad))}（分镜改过？），人挡字只能放露脸段")
        f = Path(x['file']); a2 = Path(x.get('底片') or aroll)      # 每条用它自己的底片（写了 人像下移 时是 A肉_人像下移<值>.mov，2026-11-21 新10）
        if f.exists() and a2.exists() and a2.stat().st_mtime > f.stat().st_mtime + 1:
            out.append(f'{name}：合成/{a2.name} 比这段人挡字新（口播画面重出过），重跑 huazi.py 重出')
    return out


FLOWER_GAP = 3.0        # 两个花字之间至少隔几秒（包装系统“克制档”，huazi.rules 同一个数）
LIGHT_BG = 165          # 白字底下的画面平均亮度（0–255）超过它就算“压在浅色上看不清”


def flower_spacing(rows, fps):
    """花字同时出现、或两个之间不到 3 秒（2026-11-23 初稿模式实测：huazi.py 只提示，初稿里被略过了；现在 check 不过）。
    整屏卡片（bg）和人挡字不参加（人挡字有自己的间隔规矩）。返回 [(秒, 说明)]。"""
    rows = sorted((r for r in rows if not r.get('bg') and r.get('kind') != '人挡字'), key=lambda r: r['start']); out = []
    for a, b in zip(rows, rows[1:]):
        gap = (int(b['start']) - int(a['end'])) / fps
        if gap < 0: out.append((round(int(b['start']) / fps, 2), f"第 {a.get('n')} 和第 {b.get('n')} 个花字同时出现：同一时刻最多 1 个，合并成一个或拿掉一个"))
        elif gap < FLOWER_GAP: out.append((round(int(b['start']) / fps, 2), f"第 {a.get('n')} 和第 {b.get('n')} 个花字只隔 {gap:.1f} 秒（至少 {FLOWER_GAP:.0f} 秒）：留更重要的那个、合并成一个，或把前一个的 until 提前"))
    return out


def flower_few(rows, frames, fps):
    """花字偏少的提醒（不拦）：包装规矩是全片大约每 20–30 秒一个，按 30 秒一个算下限。
    2026-11-24 初稿模式第二次实测：全是动画的片子只出了 1 个花字，因为动画镜头里“已有大字”就没人去配；
    其实选点不依赖露脸或实拍——口播说到数字、并列列举、注意事项的那一刻，只要这一刻画面上动画自己没有大字就能配（keywords、number、note、statement 四种）。"""
    dur = frames / fps; need = int(-(-dur // 30)); have = len(rows)
    if dur < 30 or have >= need: return None
    return f'花字偏少：全片 {dur:.0f} 秒，包装规矩大约每 20–30 秒一个（至少 {need} 个），现在 {have} 个。动画镜头不是不能配：口播说到数字、并列列举、注意事项的那一刻，动画自己没有大字就配 keywords、number、note、statement（详见 初稿模式.md“全是动画的片子”）；确实配不了的写进全片假设'


def flower_blue(rows, subs):
    """有花字的那几句，字幕不再标蓝（包装系统 README：不重复强调）。subs 在有花字表时自动不标；先出字幕后出花字的会漏，这里报出来。返回 [(秒, 说明)]。"""
    rows = [r for r in rows if not r.get('bg')]
    return [(x['n'], f"第 {x['n']} 条字幕「{x['text']}」标蓝了 {'、'.join(x['keys'])}，同时有花字：重跑 subs（有花字表时它自动不标蓝）")
            for x in subs if x.get('keys') and any(int(r['start']) < x['end'] and x['start'] < int(r['end']) for r in rows)]


def _rgba(path, f, fps, W=216, H=288):
    import numpy as np
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, f - 0.3) / fps:.6f}', '-i', str(path), '-frames:v', '1', '-vf', f'scale={W}:{H},format=rgba', '-f', 'rawvideo', '-'])
    return np.frombuffer(raw, np.uint8)[:W * H * 4].reshape(H, W, 4).astype(float) if len(raw) >= W * H * 4 else None


def flower_contrast(rows, shots, broll, fps):
    """白字花字压在浅色画面上看不清（2026-11-23 初稿模式实测：白色数字花字压在浅色墙面的占位图上，check 没报）。
    只查叠在动画／图片镜头上的文字类花字：取花字中间那一帧里不透明的白字像素，看底下画面（B肉）同一位置的平均亮度。返回 [(秒, 说明)]。"""
    out = []
    for r in rows:
        if r.get('bg') or r.get('kind') != '文字' or not Path(str(r.get('file'))).is_file(): continue
        mid = (int(r['start']) + int(r['end'])) // 2
        sh = next((s for s in shots if s['a'] * fps <= mid < s['b'] * fps), None)
        if not sh or sh['kind'] != 'B': continue
        try: fl = _rgba(r['file'], mid - int(r['start']), fps); bg = _frames(broll, mid, 1, fps)
        except (subprocess.CalledProcessError, OSError): continue
        if fl is None or not len(bg): continue
        lum = fl[..., :3].mean(axis=2); white = (fl[..., 3] > 200) & (lum > 215)
        if white.sum() < 12: continue
        under = float(bg[0][white].mean())
        if under > LIGHT_BG:
            out.append((round(mid / fps, 2), f"第 {r.get('n')} 个花字（{r.get('类型') or r.get('use')}）的白字压在浅色画面上（底下平均亮度 {under:.0f}/255，超过 {LIGHT_BG}）：换到画面里深色的位置（anchor）、换带底的卡片组件，或把这个镜头的图换成深一点的"))
    return out


def _frames(path, f0, n, fps, W=216, H=288, alpha=False):
    import numpy as np
    vf = f'scale={W}:{H}' + (',format=rgba' if alpha else ''); pf = [] if alpha else ['-pix_fmt', 'gray']
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, f0 - 0.3) / fps:.6f}', '-i', str(path), '-frames:v', str(n), '-vf', vf] + pf + ['-f', 'rawvideo', '-'])
    c = 4 if alpha else 1; a = np.frombuffer(raw, np.uint8); m = len(a) // (W * H * c); a = a[:m * W * H * c].reshape(m, H, W, c).astype(float)
    return (a[..., :3] * a[..., 3:] / 255).mean(axis=3) if alpha else a[..., 0]     # 花字：颜色×透明度，变色也算变化


def flicker(frames, jump=8.0):
    """单帧闪：返回帧序号。frames 是灰度帧（0–255）。整幅平均亮度这一帧和前后两帧都差 jump 以上、方向相同，
    而前后两帧之间差不到一半；或者这一帧接近全黑而前后正常（代码动画首帧黑那种）。"""
    import numpy as np
    L = frames.reshape(len(frames), -1).mean(axis=1)
    if len(L) < 3: return []
    a, b, back = L[1:-1] - L[:-2], L[2:] - L[1:-1], np.abs(L[2:] - L[:-2])
    spike = (np.abs(a) > jump) & (np.abs(b) > jump) & (a * b < 0) & (back < 0.5 * np.minimum(np.abs(a), np.abs(b)))
    black = (L[1:-1] < 0.4 * np.minimum(L[:-2], L[2:])) & (np.minimum(L[:-2], L[2:]) > 20)
    out = [int(i) + 1 for i in np.flatnonzero(spike | black)]
    if L[0] < 0.4 * L[1] and L[1] > 20: out.insert(0, 0)                                 # 第一帧黑
    return out


def run(job: Job):
    import numpy as np
    fps = float(job.fps); B = rj(job.p('分镜.json'))['shots']
    broll = next((x for x in (job.p('合成', 'B肉_小圆圈.mp4'), job.p('合成', 'B肉.mp4')) if x.exists()), None) or die('没有 合成/B肉.mp4')
    from .resolve import flower_rows
    H = [dict(file=x['file'], start=int(x['start']), end=int(x['end']), kind=x.get('kind'), inn=int(x.get('入场帧', 0))) for x in flower_rows(job)]   # 合成/花字/花字表.json（新16）
    sf = job.p('合成', '音效表.json'); S = sorted(rj(sf), key=lambda e: e['record']) if sf.exists() else []
    subs = rj(job.p('合成', '字幕', '字幕表.json')) if job.p('合成', '字幕', '字幕表.json').exists() else []
    res = {f'气口≥{job.get("max_gap", MAX_GAP)}秒': gaps(job)}
    # 音效：响的那一刻 ±4 帧内画面上要有一小块明显变化（下划线、小标签只占一小块，按整帧平均会漏）。
    # 画面按审核线上叠的各层看（2026-11-12《水电阶段》新16：只看动画层，露脸段字幕逐词变色、章节导航切换、花字出现都被误报 18 条）：
    # 动画镜头（B肉）、花字层、字幕整轨、导航整帧，哪一层有变化都算。
    subt, navf = job.p('合成', '字幕', '字幕整轨.mov'), job.p('合成', '导航', '导航整帧.mov'); nav_at = job.f(float(job.get('nav_at', 0)))
    def layer(path, start, r, alpha=True):
        o = r - 4 - start
        if o + 9 <= 0: return None
        fr = _frames(path, max(0, o), 9 - max(0, -o), fps, alpha=alpha)
        if o < 0: fr = np.concatenate([np.zeros((-o,) + fr.shape[1:]), fr])
        return fr
    def moved(fr):
        return fr is not None and len(fr) > 2 and float(max(np.percentile(np.abs(x), 99.9) for x in np.diff(fr, axis=0))) > 10
    bad, spoken = [], []
    for e in S:
        r = e['record'] + e.get('sync_frame_in_sel', 0)
        sh = next((s for s in B if s['a'] * fps <= r + 1 < s['b'] * fps), None)
        layers = []
        if sh and sh['kind'] == 'B': layers.append(lambda: _frames(broll, r - 4, 9, fps))
        # 人挡字是整幅口播画面（人一直在动），整段都算“有变化”会把乱放的音效放过：只认大字出场那几帧（2026-11-13）
        if any(h['kind'] == '人挡字' and h['start'] - 4 <= r <= h['start'] + h['inn'] + 4 for h in H): continue
        layers += [lambda h=h: layer(h['file'], h['start'], r) for h in H if h['kind'] != '人挡字' and h['start'] <= r + 4 and r - 4 < h['end']]
        if subt.exists(): layers.append(lambda: layer(subt, 0, r))
        if navf.exists(): layers.append(lambda: layer(navf, nav_at, r))
        if any(moved(get()) for get in layers): continue
        # 露脸段：音效卡在口播重点词上（钩子、反转、结论），那句字幕正显示在屏上就算对上（新1 用户定的重点就是这些口播节拍）
        if (not sh or sh['kind'] == 'F') and any(x['start'] <= r + 4 and r - 4 < x['end'] for x in subs):
            spoken.append((round(r / fps, 2), os.path.basename(e['file']))); continue
        bad.append((round(r / fps, 2), os.path.basename(e['file'])))
    res['音效对不上画面'] = bad
    # 2026-11-12《水电阶段》：画面事件只认当前进成片那一版动画（合成/动画清单.json 登记的）
    from .anim import current_events
    from .beats import per_minute
    listed = job.p('合成', '动画清单.json').exists()
    events = current_events(job) if listed else []
    byid = {s['id']: s for s in B}; out_of_shot = []
    for e in events:
        sh = byid.get(e.get('shot'))
        if not sh or not (sh['a'] - 1 / fps - 1e-6 <= e['t'] <= sh['b'] + 1 / fps + 1e-6):
            out_of_shot.append((round(e['t'], 2), e.get('shot'), e.get('name') or e.get('text'), e.get('project'),
                                '分镜里没有这个镜头' if not sh else f"镜头在 {sh['a']:.2f}–{sh['b']:.2f} 秒"))
    res['画面事件不在对应镜头里'] = out_of_shot
    # 音效表里卡画面事件的那些：事件还在不在当前事件表里、时间有没有变（动画换版后没重跑 beats/sfx 就会错）
    stale = []
    for e in S:
        if not e.get('event'): continue
        same = [x for x in events if (x.get('name') or x.get('text')) == e['event'] and x.get('shot') == e.get('shot')]
        if not any(abs(x['t'] - e['at']) <= 1 / fps + 1e-6 for x in same):
            stale.append((round(e['at'], 2), e.get('shot'), e['event'], '当前事件表里' + (f"在 {same[0]['t']:.2f} 秒" if same else '没有这个事件')))
    res['音效卡的画面事件已过期'] = stale
    # 每分钟音效数（2026-11-12 用户：音效太多；默认上限每分钟 8 个）
    total_min = job['frames'] / fps / 60
    limit = int(job.get('sfx_per_minute', 8)); n, at = per_minute([e['at'] if 'at' in e else e['record'] / fps for e in S])
    avg = len(S) / total_min if S else 0
    res['每分钟音效'] = [] if avg <= limit else [(len(S), round(avg, 1), f'平均每分钟上限 {limit}')]
    from .beats import PEAK_MINUTE
    density = (f"全片 {len(S)} 个音效，平均每分钟 {len(S) / total_min:.1f} 个（上限 {limit}），最密的一分钟（{at:.0f} 秒起）{n} 个"
               + (f"【注意：超过 {PEAK_MINUTE} 个，听一下是不是太密；只提醒不拦】" if n > PEAK_MINUTE else '')) if S else '没有音效'
    # 总装逐镜头比对（anim-assemble 写 合成/总装检查.json）：有动画登记就要有、要比 B肉 新、要全对上
    if listed:
        rp, bm = job.p('合成', '总装检查.json'), job.p('合成', 'B肉.mp4')
        if not rp.exists(): res['总装逐镜头比对'] = ['没有 合成/总装检查.json：用 anim-assemble 总装（逐帧拼接＋比对）']
        elif bm.exists() and bm.stat().st_mtime > rp.stat().st_mtime + 1: res['总装逐镜头比对'] = ['B肉.mp4 比总装检查新：B肉 不是 anim-assemble 出的，重新总装']
        else: res['总装逐镜头比对'] = rj(rp).get('bad', []) + rj(rp).get('补帧', [])
    else:
        res['总装逐镜头比对'] = []
    # 闪烁：只认“单独一帧整幅忽亮忽暗”——这一帧比前后两帧都亮（或都暗）一截，前后两帧之间却差不多；外加夹在正常帧中间的黑帧/空帧。
    # 2026-11-12《水电阶段》：原来按逐像素来回变算，Blender 镜头运镜、穿模线、透明墙反光全被当成闪烁（A10 一段 618 处、全片 729 处），
    # 镜头运动时整幅亮度是连续变的，不会单帧弹回去，所以改看整幅亮度。推拉时段（画面事件 kind=zoom/camera）照样跳过。
    if listed: zooms = events
    else:
        ev = sorted(job.dir.glob('动画/事件*.json'), key=lambda p: p.stat().st_mtime); zooms = rj(ev[-1]) if ev else []
    move = [(e['t'], e['t'] + 0.6) for e in zooms if e.get('kind') in ('zoom', 'camera')]
    fl = []; skip = set(job.get('flicker_skip', []))
    mine = set(rj(job.p('合成', '动画清单.json'))['shots']) if listed else None     # 登记过的才是动画；官方视频、实拍不查
    # R 开头的是实拍（《精装房》R06、《别墅》R01…），画面本来就在动，不查
    for s in (s for s in B if s['kind'] == 'B' and not s['id'].startswith('R') and s['id'] not in skip and (mine is None or s['id'] in mine)):
        a = round(s['a'] * fps); n = round(s['b'] * fps) - a
        if n < 3: continue
        fl += [(s['id'], round((a + i) / fps, 2)) for i in flicker(_frames(broll, a, n, fps)) if not any(x <= (a + i) / fps <= y for x, y in move)]
    res['动画闪烁'] = fl
    # 露脸段空白：没人说话（字幕、花字都没有）又超过 0.8 秒
    cover = sorted([(x['start'], x['end']) for x in subs] + [(h['start'], h['end']) for h in H]); blank = []
    for s in (s for s in B if s['kind'] == 'F'):
        a, b = round(s['a'] * fps), round(s['b'] * fps); t = a
        for x, y in cover:
            if y <= t or x >= b: continue
            if x - t > BLANK: blank.append((round(t / fps, 2), round((x - t) / fps, 2)))
            t = max(t, y)
        if b - t > BLANK: blank.append((round(t / fps, 2), round((b - t) / fps, 2)))
    res['露脸段空白'] = blank
    # 代码动画标签逐帧自检（anim-labelcheck 写 动画/自查/标签重叠.json）：有字压物体的帧就不过（2026-11-13 C7：以前谁都不读这个文件）；
    # 文件没有、但有代码动画镜头登记：只提醒还没跑，不算不过（老片子没有这个文件）
    from .anim import label_overlap
    res['代码动画标签压物体'], lab_hint = label_overlap(job)
    res['人挡字'] = rendang_problems(job); from .resolve import flower_rows; H0 = flower_rows(job)
    # 2026-11-23 初稿模式实测：花字间隔太近初稿里也要处理、有花字的句子字幕不标蓝、白字压浅色看不清——三样都算自检不过
    res['花字间隔太近'] = flower_spacing(H0, fps)
    res['有花字的句子字幕还标蓝'] = flower_blue(H0, subs)
    res['花字白字压在浅色画面上'] = flower_contrast(H0, B, broll, fps)
    # 大字、结论音效卡在关键词起点（差 >0.15 秒报，2026-11-21 新12）；大字配“唰”也报（用户定：大字不要唰，营销类结论用错误音）
    res['大字和结论音效没卡在关键词上'], res['大字配了“唰”'] = keyword_offsets(job)
    # 2026-11-22 交审前硬拦（用户：“我觉得这个都做吧”）：用户放行过的音效（sfx --allow-sounds，音效表里记了 放行）只提醒
    from . import guard
    allowed = guard.user_allowed(S)
    res['大字和结论音效没卡在关键词上'] = guard.drop_allowed(res['大字和结论音效没卡在关键词上'], allowed)
    res['用了用户否掉的音效'] = [(round(e['at'] if 'at' in e else e['record'] / fps, 2), n, w) for e in S if not e.get('放行') for n, w in guard.black_hits(job, [e['file']])]
    res['人声响度和底噪'], voice_info = guard.voice(job)
    no_polish, polish_note = guard.polish_missing(job)          # 每条片子都修音（2026-11-22 新4）：以前底噪没超线、没修音也能过
    res['口播没修音'] = [no_polish] if no_polish else []
    res['分镜用了没登记的原始素材'], mat_hints = guard.unregistered_materials(job)     # 2026-11-22 新7：以前到存档才拦
    hard_md, old_md = guard.user_md(job)
    res['规矩定了以后还写的给用户看的 MD'] = [f.name for f in hard_md]
    try: res['几何遮挡（圈、花字、字幕、人脸）'], geo_warn, geo_notes = guard.geometry(job)
    except Exception as e:      # 几何自检自己出错不挡别的项：照实报出来，算不过（交审前要弄清）
        res['几何遮挡（圈、花字、字幕、人脸）'], geo_warn, geo_notes = [f'几何自检出错：{type(e).__name__}: {str(e)[:200]}'], [], []
    unseen = unseen_samples(job)     # 用户没看过样张的镜头：只列出（不算自检不过），export 前会拦（2026-11-21 新21）
    hints = geo_warn + ([f'以前留下的给用户看的清单类 MD：{"、".join(f.name for f in old_md)}（要不要挪走问用户，别自己删）'] if old_md else []) \
        + ([h] if (h := guard.preview_4k(job)) else []) + [f'用户放行过的音效：{k[1]}（{k[0]} 秒）{v}' for k, v in allowed.items()] \
        + ([polish_note] if polish_note else []) + guard.live_resolution(job) + mat_hints\
        + ([fh] if (fh := flower_few(H0, job['frames'], fps)) else [])
    wj(job.p('合成', '自检结果.json'), dict(res, 音效密度=density, 露脸段卡口播字幕的音效=spoken, 用户没看过样张的镜头=[list(x[:2]) + [x[3]] for x in unseen],
                                          人声=voice_info, 提醒=hints, 几何说明=geo_notes, **({'标签自检提醒': lab_hint} if lab_hint else {})))
    for k, v in res.items(): say(f'{k}：{len(v)}', v[:8] if v else '')
    kinds = {}      # 花字各类型条数（2026-11-21 新11：用户要“人后大字”却做成人前大字，汇总里一眼能看出用了哪些组件）
    for x in H0: k = x.get('类型') or x.get('kind') or x.get('use') or '未标类型'; kinds[k] = kinds.get(k, 0) + 1
    if kinds: say('花字类型：' + '、'.join(f'{k} {v} 条' for k, v in kinds.items()))
    if unseen:
        say(f'【用户没看过样张】{len(unseen)} 个镜头：' + '；'.join(f'{p} {s}（{why}）' for p, s, _, why in unseen)
            + '。交审时把这些样张发到审片台“动画”栏，用户点了“可以”就算看过；不单独拦导出（2026-11-22）：用户点了审核线“这一栏定了”就算整片看过')
    from .draft import placeholders      # 占位（机器配音、占位图）：只列出来，交片时在对话里也要跟用户说清（2026-11-22 用户定：占位标清楚，有了真的再换）
    for ph in placeholders(job): say('【占位】' + ph['text'])
    say('音效密度：' + density)
    say('人声：' + voice_info)
    for h in hints: say('提醒：' + h)
    for x in geo_notes: say('几何自检：' + x)
    if spoken: say(f'露脸段卡在口播字幕上的音效 {len(spoken)} 个（画面没变化，那句字幕在屏上，算对上；听一下是不是卡在重点词上）：', spoken[:8])
    if not listed: say('提醒：没有 合成/动画清单.json（动画出片没登记），音效对画面事件没法核对版本')
    if lab_hint: say('提醒：' + lab_hint)
    say('自检不过的项要先改（或逐条跟用户说清为什么留），再交审' if any(res.values()) else '自检全部通过（不代表听过、看过：AI 没法真的从头播一遍听——交审前照 审核与导出.md「AI 交审前能做的检查」抽帧看，汇报里写明“没有真听，只做了自检和抽帧”）')
    say('下划线压字、截图卡片这类查不出来：每条下划线出现后抽一帧看')
    return not any(res.values())
