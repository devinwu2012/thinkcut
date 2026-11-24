# 新样式字幕：每条一张与画面同尺寸的透明 PNG → 按帧拼成一条 ProRes 4444 透明「字幕整轨」→ 达芬奇里按每条起止切开放。
# 规则（用户定）：去掉逗号句号（句中换成空格、句尾删掉），保留顿号、问号、感叹号；白字＋半透明黑底圆角；
# 重点词用蓝色系 #64A9E8（和导航条进度同色；2026-10-31 用户：全片蓝色系，不再用橙色）。每条最多 2 处，同一个词 12 秒内只标一次，一行大半是重点词就只留第一个；放在动画卡片下方的空带。
import re, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .common import Job, die, rj, wj, run, say, nframes, probe, local_tmp, local_rm, guard_overwrite

KEY_DEFAULT, WHITE = '#64A9E8', (255, 255, 255, 255)   # 重点词默认蓝色系；job.json 可写 subtitle_key_color 覆盖（老片子烟道是 #FF6F00）
PRORES = ['-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le', '-alpha_bits', '16', '-vendor', 'apl0']


def clean(t, rep):
    t = re.sub(r'[，。；：,;]+(?=\S)', ' ', t)
    t = re.sub(r'[，。；：,;\s]+$', '', t)
    for a, b in rep.items(): t = t.replace(a, b)
    return re.sub(r' {2,}', ' ', t).strip()


def read_srt(path):
    def sec(x):
        h, m, s = x.strip().replace(',', '.').split(':'); return int(h) * 3600 + int(m) * 60 + float(s)
    out = []
    for blk in re.split(r'\n\s*\n', Path(path).read_text(encoding='utf-8').strip()):
        L = blk.splitlines(); a, b = (sec(v) for v in L[1].split('-->'))
        out.append((a, b, ''.join(L[2:]) if len(L) > 3 else L[2]))
    return out


def tag_of(n):      # 只用字母命名：文件名带数字会被达芬奇当成图片序列
    return ''.join(chr(97 + (n - 1) // 26 ** k % 26) for k in (2, 1, 0))


class Painter:
    def __init__(self, job: Job):
        f = job.fmt; s = f['sub']
        self.W, self.H, self.cy, self.size0, self.min, self.maxw = f['W'], f['H'], s['cy'], s['size'], s['min'], s['maxw']
        c = str(job.get('subtitle_key_color') or KEY_DEFAULT).lstrip('#'); self.key = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
        self.k = s['size'] / 104            # 底框留白按 3:4 定稿时的 104 号字等比
        self.font = job['subtitle_font']
        self.keys = sorted(job['subtitle_keywords'], key=len, reverse=True)
        self.last = {}

    def F(self, size): return ImageFont.truetype(self.font, size, index=1)

    def spans(self, t, now):
        got = []
        for k in self.keys:
            if now - self.last.get(k, -99) < 12: continue
            for m in re.finditer(re.escape(k), t):
                a, b = m.span()
                if all(b <= x or a >= y for x, y in got): got.append((a, b)); self.last[k] = now; break
            if len(got) >= 2: break
        got = got[:2]
        if len(got) > 1 and sum(y - x for x, y in got) > len(t.replace(' ', '')) * 0.5: got = got[:1]
        return sorted(got)

    def layout(self, text):
        size = self.size0; font = self.F(size)
        while font.getlength(text) > self.maxw and size > self.min: size -= 4; font = self.F(size)
        lines = [text]
        if font.getlength(text) > self.maxw:
            cand = [i + 1 for i, c in enumerate(text) if c in ' 、的'] or [len(text) // 2]
            cut = min(cand, key=lambda i: abs(i - len(text) / 2))
            lines = [text[:cut].strip(), text[cut:].strip()]
        return size, font, lines

    def draw(self, lines, size, font, SP):
        W, H, k = self.W, self.H, self.k
        im = Image.new('RGBA', (W, H), (0, 0, 0, 0)); g = ImageDraw.Draw(im)
        lh = int(size * 1.32); wmax = max(font.getlength(l) for l in lines); hh = lh * len(lines); top = self.cy - hh / 2
        g.rounded_rectangle((W / 2 - wmax / 2 - 56 * k, top - 26 * k, W / 2 + wmax / 2 + 56 * k, top + hh + 18 * k), radius=36 * k, fill=(0, 0, 0, 150))
        for i, line in enumerate(lines):
            x = W / 2 - font.getlength(line) / 2; y = top + i * lh; j = 0
            for a2, b2 in SP[i] + [(len(line), len(line))]:
                if j < a2: g.text((x, y), line[j:a2], font=font, fill=WHITE); x += font.getlength(line[j:a2])
                if a2 < b2: g.text((x, y), line[a2:b2], font=font, fill=self.key); x += font.getlength(line[a2:b2])
                j = b2
        return im


def _norm(t):
    return re.sub(r'[\s/，。；：,;、？！?!]', '', t or '')


def hidden(job: Job, d):
    """“大字顶替字幕”时拿掉的字幕：job.json 的 subtitle_hide（这句字幕的字，标点、空格不计）。返回规整过的集合。
    手删过 字幕表.json 的（全表在 字幕表_全部.json，里面没标 hidden 的那几条）先补记进 subtitle_hide，重跑 subs 不会加回来（2026-11-21 新18）。"""
    hide = list(job.get('subtitle_hide') or []); have = {_norm(x) for x in hide}
    tab, full = d / '字幕表.json', d / '字幕表_全部.json'
    if tab.exists() and full.exists():
        kept = {r['n'] for r in rj(tab)}          # 按第几条比（subfix 改过字的那条，两份表字不一样，不能按字比）
        gone = [r['text'] for r in rj(full) if not r.get('hidden') and r['n'] not in kept and _norm(r['text']) not in have]
        if gone:
            hide += gone; job.cfg['subtitle_hide'] = hide; job.save()
            say(f'字幕表里手删过 {len(gone)} 条（大字顶替）：已记进 job.json 的 subtitle_hide，重跑不会加回来：' + '、'.join(gone))
    return {_norm(x) for x in hide}


def render(job: Job, srt=None, with_track=False):
    """SRT → 合成/字幕/字幕_aaa.png … ＋ 字幕表.json（帧号是成片时间线帧）。
    job.json 的 subtitle_hide 里的句子（大字顶替）不进字幕表，全表写 字幕表_全部.json（导出 SRT 用，标 hidden）。
    with_track：画完接着重拼字幕整轨（subs 命令默认这样，2026-11-21 新18：以前改字幕后要手动再跑 subs-track）。"""
    out = job.p('合成', '字幕', 'x').parent; P = Painter(job); fps = float(job.fps); rep = job.get('subtitle_replace', {})
    if with_track: guard_overwrite(job, out / '字幕整轨.mov')      # 先查再画：当前审核线用着字幕整轨就什么都不动（2026-11-22 新3）
    hide = hidden(job, out)
    for old in out.glob('字幕_*.png'): old.unlink()
    rows = []
    fixes = _fixes(job)          # 没写进 SRT 的改字（subfix 不带 --srt）：重画时照样套上，不丢
    # 有花字的那几句字幕不标蓝（包装系统 README：不重复强调；2026-11-23 初稿模式实测漏了，做成自动）：按花字表的起止帧，没有花字表就照常标
    from .resolve import flower_rows
    flowers = [(int(r['start']), int(r['end'])) for r in flower_rows(job) if not r.get('bg')]; plain = 0
    for n, (a, b, raw) in enumerate(read_srt(srt or job['srt']), 1):
        text = clean(_apply(raw, fixes.get(str(n))), rep); size, font, lines = P.layout(text)
        covered = any(x < round(b * fps) and round(a * fps) < y for x, y in flowers)
        SP = [[] if covered else P.spans(l, a) for l in lines]; plain += covered
        path = out / f'字幕_{tag_of(n)}.png'; P.draw(lines, size, font, SP).save(path)
        rows.append({'n': n, 'file': str(path), 'start': round(a * fps), 'end': round(b * fps), 'text': ' / '.join(lines), 'keys': [l[x:y] for l, sp in zip(lines, SP) for x, y in sp]})
    for x, y in zip(rows, rows[1:]):
        if y['start'] < x['end']: die(f"第 {x['n']} 条和第 {y['n']} 条字幕时间重叠")
    for r in rows:
        if _norm(r['text']) in hide: r['hidden'] = True
    miss = hide - {_norm(r['text']) for r in rows}
    wj(out / '字幕表_全部.json', rows)
    rows = [{k: v for k, v in r.items() if k != 'hidden'} for r in rows if not r.get('hidden')]
    wj(out / '字幕表.json', rows)
    say(f'{len(rows)} 条字幕 → 合成/字幕/（{job.W}×{job.H}）；带蓝色重点词 {sum(1 for r in rows if r["keys"])} 条'
        + (f'；{plain} 条同时有花字，没标蓝' if plain else '')
        + (f'；按 subtitle_hide 拿掉 {len(hide) - len(miss)} 条（大字顶替）' if hide else '')
        + (f'【subtitle_hide 里有 {len(miss)} 句在字幕里找不到（字改过？），核对 job.json】' if miss else ''))
    if with_track: track(job)
    return rows


def _clip(png, n, dst, fps):
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-loop', '1', '-framerate', fps, '-i', png, '-frames:v', str(n), *PRORES, dst])


def frame_md5(path, k, fps=None):
    """第 k 帧的 md5。给 fps 时直接跳到那一帧（落点比帧时刻早 0.3 帧）；不给时从头解码数到第 k 帧（4K 整轨后段一次要 6 秒多）。"""
    pre = ['-ss', f'{max(0.0, k - 0.3) / float(Fraction(fps)):.6f}'] if fps else []
    vf = [] if fps else ['-vf', f'select=eq(n\\,{k})']
    return subprocess.run(['ffmpeg', '-v', 'error', *pre, '-i', str(path), *vf, '-frames:v', '1', '-f', 'md5', '-'], capture_output=True, text=True).stdout.strip()


def packets(path):
    """视频帧数：数包、不解码（ProRes 一帧一个包）。4K 整轨用 nframes 逐帧解码要 95 秒，这个 0.1 秒（2026-11-21 新4 实测）。"""
    from .common import packets as count      # 读 JSON 的第一条视频流（2026-11-22 新5：csv 遇到 tmcd 会多打一行）
    return count(path)


def track(job: Job, workers=6):
    """字幕表 → 合成/字幕/字幕整轨.mov：总帧数＝成片帧数，第 n 条占 [start, end)，空档完全透明。
    2026-11-21 新4 实测（《别墅装灯》117 条）：以前 217 秒，大半耗在逐帧解码整条 4K 数帧数、抽查；改成数包、跳帧抽查后 70 秒。
    编码 6 路和 18 路一样快（ProRes 编码自己就多线程），所以仍是 6 路。"""
    d = job.p('合成', '字幕', 'x').parent; rows = rj(d / '字幕表.json'); N = int(job['frames']); fps = job['fps']
    if not rows: die('字幕表是空的（合成/字幕/字幕表.json 里一条字幕都没有），拼不了字幕整轨：先看 SRT 里有没有字幕，重跑 subs 再来')
    if rows[-1]['end'] > N: die(f"最后一条字幕结束在 {rows[-1]['end']} 帧，超过成片 {N} 帧")
    guard_overwrite(job, d / '字幕整轨.mov')                         # 当前审核线用着字幕整轨就先停（新3）
    # 分段片段用完就删：放本机临时目录（2026-11-21 用户定；以前建在 NAS 上的 合成/字幕/ 里）。实测 4K 整轨每帧约 0.22 MB，按两倍估
    tmp = local_tmp(job.dir, '字幕整轨', need=int(N * job.W * job.H * 0.08), what='字幕整轨的分段片段')
    try:
        Image.new('RGBA', (job.W, job.H), (0, 0, 0, 0)).save(tmp / 'blank.png')
        parts, jobs, t = [], [], 0
        for r in rows:
            if r['start'] > t: parts.append(('gap', r['start'] - t))
            parts.append((r['file'], r['end'] - r['start'])); t = r['end']
        if t < N: parts.append(('gap', N - t))
        gaps = {}
        for i, (src, n) in enumerate(parts):
            if src == 'gap':
                if n not in gaps: gaps[n] = tmp / f'gap_{n}.mov'; jobs.append((tmp / 'blank.png', n, gaps[n]))
                parts[i] = (gaps[n], n)
            else:
                dst = tmp / f'{i:04d}.mov'; jobs.append((src, n, dst)); parts[i] = (dst, n)
        say(f'编码 {len(jobs)} 段透明片段（ProRes 4444，{job.W}×{job.H}）…')
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(lambda j: _clip(j[0], j[1], j[2], fps), jobs))
        lst = tmp / 'list.txt'; lst.write_text(''.join(f"file '{p}'\n" for p, _ in parts), encoding='utf-8')
        out = d / '字幕整轨.mov'
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', out])
        got = packets(out)
        if got != N: die(f'字幕整轨帧数 {got}，应为 {N}')
        # 抽查：首、中、末三条的首尾帧与单条片段一致
        def one(r):
            o = tmp / f"one_{r['n']}.mov"; _clip(r['file'], 1, o, fps); ref = frame_md5(o, 0)
            return [(r['n'], k) for k in (r['start'], r['end'] - 1) if frame_md5(out, k, fps) != ref]
        with ThreadPoolExecutor(3) as ex:
            bad = sum(ex.map(one, (rows[0], rows[len(rows) // 2], rows[-1])), [])
        if bad: die(f'字幕整轨抽查不符：{bad}')
        say(f'→ 合成/字幕/字幕整轨.mov（{N} 帧，抽查 3 条首尾帧一致）')
    finally:
        local_rm(tmp)


def _fixes(job):
    """合成/字幕/改字记录.json：{第几条: [[旧字, 新字], …]}，只记还没写进 SRT 的改字。"""
    p = job.p('合成', '字幕', '改字记录.json'); return rj(p) if p.exists() else {}


def _apply(raw, pairs):
    for o, w in pairs or []:
        if o in raw: raw = raw.replace(o, w)
    return raw


def refix(job: Job, n, old, new, write_srt=False):
    """只改一条字幕里的字（old → new）：重画这一条 PNG、出一个同长度透明片段，返回 (行, 片段路径)。
    write_srt=True 时同时改 SRT 里这一条（先备份成 .改字前.srt）。达芬奇里换片段由 resolve.subfix 做。"""
    d = job.p('合成', '字幕', 'x').parent; rows = rj(d / '字幕表.json')
    r = next((x for x in rows if x['n'] == n), None)
    if not r: die(f'字幕表里没有第 {n} 条')
    srt = Path(job['srt']); blocks = re.split(r'(\n\s*\n)', srt.read_text(encoding='utf-8').strip())
    bi = 2 * (n - 1); L = blocks[bi].splitlines()
    # 2026-11-13 审查：以前每次都从 SRT 原文重新取字，同一条不带 --srt 改第二次，第一次的改动被悄悄改回
    fixes = _fixes(job); mine = fixes.get(str(n), [])
    raw = _apply(''.join(L[2:]) if len(L) > 3 else L[2], mine)          # 这一条现在的字（含之前没写进 SRT 的改字）
    if old in raw: mine = mine + [[old, new]]
    elif new and new in raw: say(f'第 {n} 条已经是改后的字了（上次改到一半停了），接着把片段出完')   # 达芬奇那步失败后重试
    else: die(f'第 {n} 条现在是「{raw}」，里面没有「{old}」')
    raw = _apply(raw, [[old, new]])
    if write_srt:
        bak = srt.with_name(srt.stem + '.改字前.srt')
        if not bak.exists(): shutil.copy2(srt, bak)
        blocks[bi] = '\n'.join(L[:2] + [_apply(x, mine) for x in L[2:]])
        srt.write_text(''.join(blocks) + '\n', encoding='utf-8'); mine = []       # 都写进 SRT 了，不用再记
    fixes[str(n)] = mine; fixes = {k: v for k, v in fixes.items() if v}; wj(job.p('合成', '字幕', '改字记录.json'), fixes)
    P = Painter(job); text = clean(raw, job.get('subtitle_replace', {})); size, font, lines = P.layout(text)
    SP = []
    for l in lines:          # 保留这一条原来的橙色重点词（新字里还在的话）
        sp = []
        for k in r['keys']:
            i = l.find(k)
            if i >= 0 and all(i + len(k) <= x or i >= y for x, y in sp): sp.append((i, i + len(k)))
        SP.append(sorted(sp))
    P.draw(lines, size, font, SP).save(r['file'])
    before = r['text']; r['text'] = ' / '.join(lines); r['keys'] = [l[x:y] for l, sp in zip(lines, SP) for x, y in sp]
    k = len(list((d / '改字').glob(f'字幕_{tag_of(n)}_*.mov'))) + 1 if (d / '改字').exists() else 1
    clip = job.p('合成', '字幕', '改字', f'字幕_{tag_of(n)}_{"abcdefghijklmnopqrstuvwxyz"[k - 1]}.mov')
    _clip(r['file'], r['end'] - r['start'], clip, job['fps'])
    wj(d / '字幕表.json', rows)
    say(f'第 {n} 条：「{before}」→「{r["text"]}」，新片段 {clip.name}（{r["end"] - r["start"]} 帧）' + ('；SRT 已同步' if write_srt else ''))
    return r, clip


def _grab(video, frames, outdir, fps, workers=6):
    """按帧号取图：每帧单独精确定位（落点比帧时刻早 0.3 帧，正好取到这一帧）→ {帧号: 图片路径}"""
    outdir.mkdir(parents=True, exist_ok=True); frames = sorted(set(frames))
    def one(f):
        out = outdir / f'{f:05d}.jpg'
        run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{max(0.0, f - 0.3) / fps:.6f}', '-i', video, '-frames:v', '1', '-q:v', '3', out]); return f, out
    with ThreadPoolExecutor(workers) as ex:
        return dict(ex.map(one, frames))


def _flower_layer(job: Job, flowers, m, tmp):
    """这一帧（成片帧号 m）上正在显示的花字，叠成一张透明图；没有或取不出来返回 None。
    2026-11-24 初稿模式第二次实测：总览不叠花字，花字压人脸看不出来。花字片段整段对齐花字表的 start，取第 m-start 帧。"""
    layer = None
    for x in flowers:
        a, b = int(x['start']), int(x['end'])
        f = Path(x['file']) if Path(x['file']).is_absolute() else job.dir / x['file']
        if not a <= m < b or not f.exists(): continue
        out = tmp / f'花字_{m}_{a}.png'; out.parent.mkdir(parents=True, exist_ok=True)
        try: run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{max(0.0, m - a - 0.3) / float(job.fps):.6f}', '-i', f, '-frames:v', '1', '-pix_fmt', 'rgba', out])
        except SystemExit: continue
        except Exception: continue
        if not out.exists(): continue
        im = Image.open(out).convert('RGBA').resize((job.W, job.H))
        if layer is None: layer = im
        else: layer.alpha_composite(im)
    return layer


def sheet(job: Job):
    """逐条检查：每条字幕取中间帧，叠在当时的画面上（动画镜头用 B 肉，其余用 A 肉），量字幕框和动画卡片、小圆圈有没有相交；
    出 合成/字幕/检查/总览_NN.jpg（每张 12 条）和 检查表.json"""
    from PIL import ImageDraw as D
    d = job.p('合成', '字幕', '检查', 'x').parent; rows = rj(job.p('合成', '字幕', '字幕表.json'))
    B = rj(job.p('分镜.json')); shots = [s for s in B['shots'] if s['kind'] == 'B']
    a_v = job.p('合成', 'A肉.mov'); b_v = next((x for x in (job.p('合成', 'B肉_小圆圈.mp4'), job.p('合成', 'B肉.mp4')) if x.exists()), None)
    if not a_v.exists(): die('缺 合成/A肉.mov，先运行 aroll-render')
    fps = float(job.fps); f = job.fmt
    nb = int(probe(b_v).get('nb_frames') or 0) if b_v else 0     # 动画只出了一部分时，后面的按口播画面查
    def shot_at(m): return next((s for s in shots if job.f(s['a']) <= m < job.f(s['b']) and m < nb), None)
    mids = [(r, (r['start'] + r['end']) // 2) for r in rows]
    tmp = local_tmp(job.dir, '字幕检查', need=len(mids) * 2 * 1024 ** 2, what='字幕检查取的画面')      # 取出来的画面拼完总览就删：放本机临时目录（以前在 合成/字幕/检查/tmp_a、tmp_b）
    ga = _grab(a_v, [m for r, m in mids if not (b_v and shot_at(m))], tmp / 'tmp_a', fps)
    gb = _grab(b_v, [m for r, m in mids if shot_at(m)], tmp / 'tmp_b', fps) if b_v else {}
    cx, cy, cw, ch = f['card']
    from .overlay import pip_box
    def hit(a, b): return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
    from .resolve import flower_rows
    flowers = flower_rows(job); nflower = 0
    res, tiles = [], []; lab = ImageFont.truetype(job['subtitle_font'], 20, index=1)
    for r, m in mids:
        s = shot_at(m) if b_v else None
        base = Image.open((gb if s else ga)[m]).convert('RGBA').resize((job.W, job.H))
        sub = Image.open(r['file']); box = sub.getchannel('A').getbbox()
        bad = []
        if s:
            if not f.get('sub_over_card') and hit(box, (cx, cy, cx + cw, cy + ch)): bad.append('压到动画卡片')
            # 小圆圈位置和 pip 烧进去的用同一套算（2026-11-13 C14：以前只按左下/右下两个固定位置判，不认 pip.pos 的新位置、分段、none 和推近时的缩小）
            pb = pip_box(job, B, m / fps) if s.get('pip') else None
            if pb and hit(box, pb): bad.append('压到小圆圈')
        lay = _flower_layer(job, flowers, m, tmp / 'tmp_f') if flowers else None
        if lay is not None: base.alpha_composite(lay); nflower += 1          # 花字在字幕下面（审核线上也是花字轨在字幕轨下面）
        base.alpha_composite(sub)
        im = base.convert('RGB').resize((360, round(360 * job.H / job.W)))
        D.Draw(im).text((8, 6), f"#{r['n']} {'动画' if s else '口播'}{' ✗' if bad else ''}", fill=(255, 80, 60) if bad else (255, 255, 255), font=lab, stroke_width=2, stroke_fill=(0, 0, 0))
        tiles.append(im)
        res.append({'n': r['n'], 'm': m, '类型': '动画' if s else '口播', '字幕框': list(box), '问题': bad, **({'小圆圈': [round(v) for v in pb]} if s and pb else {})})
    tw, th = tiles[0].size
    for k in range(0, len(tiles), 12):
        sh = Image.new('RGB', (tw * 4, th * 3), (20, 20, 20))
        for i, t in enumerate(tiles[k:k + 12]): sh.paste(t, ((i % 4) * tw, (i // 4) * th))
        sh.save(d / f'总览_{k // 12 + 1:02d}.jpg', quality=88)
    local_rm(tmp)
    wj(d / '检查表.json', res)
    bad = [x for x in res if x['问题']]
    say(f"{len(res)} 条字幕检查完（总览已叠上花字，{nflower} 条画面上有花字）：{len(bad)} 条有问题" + ('：' + '；'.join(f"#{x['n']} {'、'.join(x['问题'])}" for x in bad[:10]) if bad else '') + f' → {d}')
    return bad
