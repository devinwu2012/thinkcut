# 叠加类素材：动画镜头里的人脸小圆圈（烧进 B 肉）、章节导航条（转成整帧透明片，放达芬奇不用调位置）
import json, shutil
from PIL import Image, ImageDraw
from .common import Job, die, rj, run, say, probe, nframes, duration, guard_overwrite


def _disc(size, r, alpha, ss=4):
    im = Image.new('L', (size * ss, size * ss), 0); c = size * ss / 2
    ImageDraw.Draw(im).ellipse((c - r * ss, c - r * ss, c + r * ss, c + r * ss), fill=alpha)
    return im.resize((size, size), Image.LANCZOS)


def anchors(job: Job):
    """小圆圈 9 个命名位置 → (参考点 x, y, 横向对齐 0/0.5/1, 纵向对齐 0/0.5/1)，4K 像素。
    左下/右下和原来完全一样（圆圈外框左下角/右下角对齐，缩小时角不动）；上排在导航条/卡片上沿下 70 像素，中间一排居中。"""
    P = job.fmt['pip']; d, ring = P['d'], P['ring']; R = d + 2 * ring
    (lx, ly), (rx, _) = P['L'], P['R']
    left, right, bottom = lx - ring, rx + d + ring, ly + d + ring
    x, y, w, h = job.fmt['card']; nav_bottom = job.fmt['nav'][1] + job.fmt['nav'][3]
    top = max(y, nav_bottom) + 70; cx = job.W / 2; cy = (top + bottom) / 2
    col = {'L': (left, 0), 'C': (cx, 0.5), 'R': (right, 1)}; row = {'T': (top, 0), 'M': (cy, 0.5), 'B': (bottom, 1)}
    return {r + c: (col[c][0], row[r][0], col[c][1], row[r][1]) for r in row for c in col}


def frame_edge(x, fps):
    """x 秒所在那一帧（round(x×帧率)）开始前半帧的时刻：拿它当开关的边界，这一帧一定算在里面。"""
    return (round(x * fps) - 0.5) / fps


def segments(job: Job, shots):
    """每个带圆圈的镜头 → [(起, 止, 位置)]；没写 pip_at 的按 pip：L=左下、R=右下（原来的做法）。"""
    out = []
    for s in shots:
        if s.get('pip_at'): out += [(a, b, p if isinstance(p, str) else tuple(p)) for a, b, p in s['pip_at'] if p != 'none']
        else: out.append((s['a'], s['b'], 'BR' if s['pip'] == 'R' else 'BL'))
    return out


def shrink_factor(B, t):
    """t 秒时小圆圈的缩放系数（平时 1，pip_shrink 时段内平滑缩到 to）。和 pip() 里写给 ffmpeg 的式子是同一个算法。"""
    S = B.get('pip_shrink') or {}; to, rp = S.get('to', 1.0), max(S.get('ramp', 0.8), 0.04)
    sm = lambda x: x * x * (3 - 2 * x); cl = lambda x: min(max(x, 0.0), 1.0)
    win = sum(sm(cl((t - w['a']) / rp)) * (1 - sm(cl((t - w['b']) / rp))) for w in S.get('windows', []))
    return 1 - (1 - to) * min(win, 1)


def pip_box(job: Job, B, t):
    """t 秒那一刻小圆圈（含白边）在画面上占的方框 (x0, y0, x1, y1)，4K 像素；这一刻没有圆圈返回 None。
    和 pip() 烧圆圈用同一套：segments（pip.pos 的位置、分段、none）、anchors（9 个命名位置）、帧边界开关、推近时缩小。subs-check 用它判字幕压没压圆圈。"""
    P = job.fmt['pip']; R = P['d'] + 2 * P['ring']; fps = float(job.fps)
    segs = segments(job, [s for s in B['shots'] if s['kind'] == 'B' and s.get('pip')])
    place = next((p for a, b, p in segs if frame_edge(a, fps) <= t <= frame_edge(b, fps) - 0.0005), None)
    if place is None: return None
    px, py, ax, ay = (place[0], place[1], 0.5, 0.5) if isinstance(place, tuple) else anchors(job)[place]
    Rt = int(R * shrink_factor(B, t) / 2) * 2
    return (px - ax * Rt, py - ay * Rt, px - ax * Rt + Rt, py - ay * Rt + Rt)


def pip(job: Job, src=None, out=None):
    """合成/B肉.mp4（动画）＋ 合成/A肉.mov（口播）→ 合成/B肉_小圆圈.mp4：分镜里带 pip 的动画镜头放胸口以上的人脸圆圈。
    位置默认左下（right 里的右下）；分镜规格 pip.pos 可以按镜头、按镜头里的分段放到任意位置（2026-11-12《水电阶段》）。"""
    B = rj(job.p('分镜.json')); P = job.fmt['pip']; d, ring = P['d'], P['ring']
    src = src or job.p('合成', 'B肉.mp4'); ar = job.p('合成', 'A肉.mov'); out = out or job.p('合成', 'B肉_小圆圈.mp4')
    machine = (job.get('placeholder') or {}).get('kind') == '机器配音' and not any(s['kind'] == 'B' and s.get('pip') for s in B['shots'])
    for f in ((src,) if machine else (src, ar)):
        if not f.exists(): die(f'缺 {f}')
    guard_overwrite(job, out)          # 当前审核线用着 B肉_小圆圈.mp4 就先停（2026-11-22 新3）
    if machine:      # 机器配音占位没有人脸：不放小圆圈（也不用先出 A肉），原样拷一份让后面的步骤照常跑
        shutil.copy2(src, out); say(f'口播是机器配音占位（没有人脸），不放小圆圈：{out.name} 和 B肉 一样'); return out
    bx, by, bs = job.get('pip_body') or job.fmt['body']
    mask, rim = job.p('合成', '小圆圈', 'mask.png'), job.p('合成', '小圆圈', 'ring.png')
    _disc(d, d / 2 - 1, 255).save(mask)
    R = d + 2 * ring; a = _disc(R, R / 2 - 1, 235)
    Image.merge('RGBA', (Image.new('L', (R, R), 255),) * 3 + (a,)).save(rim)
    shots = [s for s in B['shots'] if s['kind'] == 'B' and s.get('pip')]
    if not shots: die('分镜里没有带小圆圈的动画镜头')
    segs = segments(job, shots); named = anchors(job)
    if not segs: die('带小圆圈的镜头全都写成了 none')
    places = list(dict.fromkeys(p for _, _, p in segs))
    # 缩放系数 f(t)：平时 1；pip_shrink 时段内平滑缩到 to（两端 smoothstep 过渡 ramp 秒），按位置的对齐点缩（左下就左下角不动）
    S = B.get('pip_shrink') or {}
    to, rp = S.get('to', 1.0), max(S.get('ramp', 0.8), 0.04)
    sm = lambda x: f"(({x})*({x})*(3-2*({x})))"
    win = '+'.join(sm(f"clip((t-{w['a']:.4f})/{rp:.3f},0,1)") + '*(1-' + sm(f"clip((t-{w['b']:.4f})/{rp:.3f},0,1)") + ')' for w in S.get('windows', [])) or '0'
    f = f"(1-{1 - to:.4f}*min({win},1))"      # 时段重叠时也最多缩到 to（2026-10-24 审计）
    Rt = f"trunc({R}*{f}/2)*2"
    g = (f"[1:v]crop={bs}:{bs}:{bx}:{by},scale={d}:{d}:flags=lanczos,format=rgba[face];[2:v]format=gray[m];[face][m]alphamerge[pip];"
         f"[3:v]format=rgba[rim];[rim][pip]overlay={ring}:{ring}:format=auto[c];"
         f"[c]scale=w='{Rt}':h='{Rt}':eval=frame:flags=lanczos,split={len(places)}" + ''.join(f'[c{i}]' for i in range(len(places))) + ';')
    cur = '[0:v]'
    for i, place in enumerate(places):
        if isinstance(place, tuple): px, py, ax, ay = place[0], place[1], 0.5, 0.5          # [x, y] 是圆心
        else: px, py, ax, ay = named[place]
        # 镜头首帧是 round(起点秒×帧率)，那一帧的时刻常比起点早几毫秒：按帧边界开关，首帧不缺圆圈（2026-11-13 审查，烟道 8 段里 6 段首帧没圆圈）
        fr = lambda x: frame_edge(x, float(job.fps))
        en = '+'.join(f"between(t,{fr(a):.5f},{fr(b) - 0.0005:.5f})" for a, b, p in segs if p == place)
        nxt = f'[v{i}]' if i < len(places) - 1 else ''
        g += f"{cur}[c{i}]overlay=x='{px:.1f}-{ax}*overlay_w':y='{py:.1f}-{ay}*overlay_h':eval=frame:enable='{en}'" + (f'{nxt};' if nxt else ',format=yuv420p[vo]')
        cur = nxt
    dur = duration(src); nf = int(probe(src).get('nb_frames') or nframes(src))   # 帧数和动画完全一样
    say(f'烧小圆圈（{len(shots)} 个镜头、{len(segs)} 段、{len(places)} 种位置，直径 {d}' + (f"，{len(S['windows'])} 段推近时缩到 {to:.0%}" if S.get('windows') else '') + '）…')
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', src, '-t', f'{dur:.5f}', '-i', ar, '-loop', '1', '-i', mask, '-loop', '1', '-i', rim,
         '-filter_complex', g, '-map', '[vo]', '-an', '-frames:v', str(nf), '-c:v', 'libx264', '-preset', 'medium', '-crf', '14', '-r', job['fps'], out])
    got = nframes(out)
    if got != nf: die(f'小圆圈成片 {got} 帧，动画是 {nf} 帧')
    say(f'→ {out}（{got} 帧，与动画一致）')


NAV_BGS = ('opaque', 'dim', 'clear')      # 导航条底：不透明黑底／半透明深色底／完全透明（章节导航 style 的 background_mode）
NAV_BG_NEW = 'dim'      # 2026-11-21 新19：新开的片子默认半透明深色底（init 写进 job.json 的 nav_bg）；老片子 job.json 里没有 nav_bg，仍是不透明黑底


def nav_bg(job: Job):
    """这条片子导航条的底：job.json 的 nav_bg。不写＝老片子，不透明黑底。"""
    v = job.get('nav_bg') or 'opaque'
    if v not in NAV_BGS: die(f'job.json 的 nav_bg 写成了 {v!r}，只能是 ' + '、'.join(NAV_BGS) + '（不写＝不透明黑底）')
    return v


def nav_style_hint(job: Job):
    """出导航条时章节导航 project.json 的 style 该写什么（nav-target 打印用）。不透明底不用写。"""
    v = nav_bg(job)
    return {} if v == 'opaque' else {'background_mode': v}


def nav(job: Job, bar, at=0.0):
    """导航条（章节导航出的 .mp4 或带透明通道的 .mov）→ 合成/导航/导航整帧.mov：画面大小的透明 ProRes 4444，条子在画幅规定的位置。
    半透明／透明底的片子（job.json 的 nav_bg 是 dim/clear）必须给带透明通道的 .mov，给了不透明的 mp4 就拦下，免得悄悄交出黑底条。"""
    guard_overwrite(job, job.dir / '合成' / '导航' / '导航整帧.mov')      # 当前审核线用着导航整帧就先停（2026-11-22 新3）
    s = probe(bar); x, y, w, h = job.fmt['nav']
    if nav_bg(job) != 'opaque' and not str(s.get('pix_fmt', '')).startswith(('yuva', 'rgba', 'argb', 'abgr', 'bgra', 'gbrap')):
        die(f"这条片子的导航条底是「{nav_bg(job)}」（job.json 的 nav_bg），但 {bar} 没有透明通道（{s.get('pix_fmt')}），会变成黑底条。"
            f"出导航条时章节导航 project.json 的 style 写 {json.dumps(nav_style_hint(job), ensure_ascii=False)}，用它出的 navigation-full.mov；要黑底就把 job.json 的 nav_bg 改成 opaque")
    if (int(s['width']), int(s['height'])) != (w, h):
        say(f"提醒：导航条是 {s['width']}×{s['height']}，画幅要求 {w}×{h}，会缩放到这个大小（最好按 {w}×{h} 重出）")
    out = job.p('合成', '导航', '导航整帧.mov')
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', bar, '-filter_complex',
         f'[0:v]scale={w}:{h}:flags=lanczos,format=yuva444p10le,pad={job.W}:{job.H}:{x}:{y}:color=black@0[vo]', '-map', '[vo]', '-an',
         '-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le', '-alpha_bits', '16', '-vendor', 'apl0', '-r', job['fps'], out])
    n = nframes(out); job.cfg['nav_at'] = at; job.save()
    say(f'→ {out}（{n} 帧，从成片第 {at} 秒放起；成片 {job["frames"]} 帧）')
