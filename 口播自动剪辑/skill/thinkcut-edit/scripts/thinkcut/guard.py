# -*- coding: utf-8 -*-
# 交审前程序硬拦（2026-11-22，用户：“我觉得这个都做吧”）。依据：5 条片子的返工记录，用户先发现约 200 条问题，
# 其中“规矩写了没照做”约 34 条、音效约 22 条、口播声音约 10 条，圈／花字挡脸挡字也反复出现。三类：
#   ① 用户已定规矩：status 开头打印规矩短清单（从 内部/架构审查交接.md 第 3 节生成，不另抄一份）；check 拦规矩定了以后还写的给用户看的清单类 MD。
#   ② 几何：小圆圈、花字、字幕、人脸互相遮挡就停，报第几秒、哪两个东西、重叠多少；人脸用现成的 RVM 抠像（和人挡字同一套）量头的位置。
#   ③ 声音：人声响度偏离 -12.5 LUFS 超过 2、底噪高于 -55 dB 就停（复用 voice-polish、sfx 量过的数）；用户否掉过的音效（黑名单）选到就停。
# 这里只查、不改任何东西；check 把这些列进自检结果，有就不过。
import re, subprocess, time
from pathlib import Path
import numpy as np
from .common import Job, THINKCUT_DIR, rj, wj, run, probe, local_tmp, local_rm

VOICE_LUFS, VOICE_TOL = -12.5, 2.0      # 剪口播定的人声响度（约 -12.5 LUFS），偏离超过 2 就停
FLOOR_MAX = -55.0                       # 底噪验收线（和 voice-polish 同一条）
MD_RULE_DAY = '2026-11-21'              # 用户第二次说“少用 md 文档跟我沟通”：这天以后写的给用户看的清单类 MD 算违规（以前的只提醒）
SCALE = 4                               # 几何检查按 1/4 尺寸的遮罩算（4K → 540×720），面积换回 4K 像素报
MIN_OVERLAP = 1500                      # 两样东西的笔画/圆圈真的叠上（4K 像素）才算，避开抗锯齿的一圈虚边
FACE_FRAC = 0.01                        # 花字压到人脸框的面积超过 1% 算压脸
PIP_CUT = 0.10                          # 小圆圈里头被圆边切掉超过 10% 算“头被卡出血”（261118 改前 0.26–0.36、改后 ≤0.03）
FACE_H = 1.25                           # 人脸框高 = 头宽 × 1.25（从头顶算起，到下巴附近）


# ───────── ① 用户已定规矩 ─────────
def rules_digest(root=None):
    """内部/架构审查交接.md 第 3 节“用户定过的规矩”表 → ['主题：规矩', …]（去掉括号里的日期和原话，只留能照做的句子），
    再加根目录 AGENTS.md 的“两条通用做法”（不写给用户看的 MD、用满电脑性能）。文件不在（代码被拷到别处）返回 []。"""
    root = Path(root or THINKCUT_DIR); out = []
    f = root / '内部' / '架构审查交接.md'
    if f.exists():
        sec = re.split(r'\n## ', f.read_text(encoding='utf-8'))
        part = next((s for s in sec if s.startswith('3.')), '')
        for line in part.splitlines():
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) < 3 or cells[0] in ('主题', '') or set(cells[0]) <= set('-: '): continue
            rule = re.sub(r'（[^（）]*(?:20\d\d-\d\d-\d\d|用户|原话)[^（）]*）', '', cells[1]).strip()
            out.append(f'{cells[0]}：{rule}')
    a = root / 'AGENTS.md'
    if a.exists():
        m = re.search(r'\*\*两条通用做法\*\*\n((?:- .*\n?)+)', a.read_text(encoding='utf-8'))
        for line in (m.group(1).splitlines() if m else []):
            head = re.match(r'- \*\*(.+?)\*\*', line)
            if head: out.append('通用：' + head.group(1))
    return out


def user_md(job: Job):
    """给用户看的清单类 MD（status.user_md 同一套认法）分两种：(规矩定了以后写的＝违规, 以前留下的＝只提醒)。"""
    from .status import user_md as found
    cut = time.mktime(time.strptime(MD_RULE_DAY, '%Y-%m-%d'))
    files = found(job)
    return [f for f in files if f.stat().st_mtime >= cut], [f for f in files if f.stat().st_mtime < cut]


def preview_4k(job: Job):
    """审片轮登记了 4K 动画（2026-11-21 Q159→Q185 复发：审片阶段要 1080P）。用户还没说 OK（job.json 没有 export_ok）时返回提醒。
    只提醒不拦：用户说 OK 之后、导出之前那遍 4K 重出也会被认成这样。"""
    if job.get('export_ok'): return None
    rs = job.p('合成', '渲染尺寸.json'); lst = job.p('合成', '动画清单.json')
    if not rs.exists() or not lst.exists(): return None
    kinds = {p['name']: p.get('kind') for p in rj(lst).get('projects', [])}
    big = [x['shot'] for x in rj(rs).get('items', []) if int(x.get('scale') or 0) >= 100 and kinds.get(x.get('name')) in ('code', 'blender', 'hyperframes')]
    return (f"审片轮登记的是 4K 动画（{'、'.join(big[:8])}）：用户说 OK 之前用 1080P 审片版（出片.py 不带参数）。"
            '用户已经说了 OK、这是导出前的 4K 重出就不用管') if big else None


def old_templates(job: Job):
    """片子里复制过去的旧模板 出片.py（没有 --4k 参数，不带参数就出 4K；261118 实测）：[(工程名)]。status 提醒。"""
    return [p.parent.name for p in sorted(list(job.dir.glob('HF_*/出片.py')) + list(job.dir.glob('Blender_*/出片.py')))
            if '--4k' not in p.read_text(encoding='utf-8', errors='replace')]


# ───────── ③ 声音 ─────────
def voice(job: Job, measure_floor=None):
    """人声响度和底噪。修过音（job.json 有 voice_polish）用它记的响度、底噪（voice-polish 量过，不再量）；
    没修过：响度用 合成/音量.json 的 voice_lufs（sfx 量的），底噪量 合成/A肉.mov（加上人声增益）。返回 ([问题], 说明)。"""
    from . import voice as V
    vp = job.get('voice_polish')
    if vp:
        lufs, floor, src = vp.get('lufs'), vp.get('floor_db'), f"修音口播 {Path(vp.get('file', '')).name}"
    else:
        a = job.p('合成', 'A肉.mov')
        if not a.exists(): return [], '没有 合成/A肉.mov，人声没量'
        vol = job.p('合成', '音量.json'); v = rj(vol) if vol.exists() else {}
        try:
            if 'voice_lufs' in v: lufs, g = v['voice_lufs'], float(v.get('voice_db') or 0)
            else:
                from .sfx import voice_gain
                g, lufs = voice_gain(job)
            floor = round((measure_floor or V.noise_floor)(a) + g, 1)
        except SystemExit as e:          # A肉 没声音、太短：量不了不算不过，照实说
            return [], f'原口播量不了响度和底噪（{str(e)[:80]}），交审前听一遍'
        src = '原口播（没做降噪修音）'
    bad = []
    if lufs is not None and abs(float(lufs) - VOICE_LUFS) > VOICE_TOL:
        bad.append(f'{src} 响度 {lufs} LUFS，偏离 {VOICE_LUFS} 超过 {VOICE_TOL:g}：回剪口播查人声处理（应在约 {VOICE_LUFS} LUFS）')
    if floor is not None and float(floor) > FLOOR_MAX:
        bad.append(f'{src} 底噪 {floor} dB，高于 {FLOOR_MAX:g} dB：' + ('review 建好审核线后跑 voice-polish 降噪修音，再跑 check' if not vp else '加大人声隔离重跑 voice-polish --amount 80'))
    if bad and (job.get('placeholder') or {}).get('kind') == '机器配音':      # 机器配音占位：合成的声音不按真口播的响度、底噪标准拦（试验田实测 -17.6 LUFS、底噪 -44 dB），只照实说；换成真口播后照常查
        return [], f'{src}：响度 {lufs} LUFS，底噪 {floor} dB（机器配音占位，不按真口播的标准拦；换成真口播后要修音、再查）'
    return bad, f'{src}：响度 {lufs} LUFS，底噪 {floor} dB（要 {VOICE_LUFS}±{VOICE_TOL:g} LUFS、≤ {FLOOR_MAX:g} dB）'


POLISH_RULE_AT = '2026-11-22 17:26'     # 用户定“每条片子都修音”（原话：“用达芬奇修音呗”，261122 交接）：这之前已经导出过的老片子不拦


def polish_missing(job: Job):
    """每条片子都修音（2026-11-22 261122 反馈单 新4）：job.json 没有 voice_polish 就返回 (要拦的话, None)；放行的返回 (None, 说明)，修过返回 (None, None)。
    放行办法：① job.json 写 "免修音": "用户原话"（用户说这条不修）；② 规矩定之前就导出过的老片子（export_ok 的时间早于 2026-11-22 17:26）自动不拦。"""
    if job.get('voice_polish'): return None, None
    if (job.get('placeholder') or {}).get('kind') == '机器配音': return None, '口播是机器配音占位，不用修音（换成真口播后要修）'
    why = str(job.get('免修音') or '').strip()
    if why: return None, f'这条片子没修音，按 job.json 的 免修音 放行：{why}'
    ok = job.get('export_ok') or {}
    if str(ok.get('at') or '9') < POLISH_RULE_AT: return None, f"老片子（{ok.get('at')} 导出过，在“每条都修音”定下之前）没修音，不拦"
    return ('口播还没修音（job.json 没有 voice_polish）：每条片子都修音（2026-11-22 用户：“用达芬奇修音呗”）。审核线建好后先跑 voice-polish，再 check／export。'
            '用户明确说这条不修音时，在 job.json 写 "免修音": "用户原话" 放行'), None


def pm_bin():
    """视频项目管理的程序目录：环境变量 THINKCUT_PM 优先（开发、测试用），否则 <程序目录>/视频项目管理/bin（和审片台读项目文案同一种找法）。"""
    import os
    return Path(os.environ.get('THINKCUT_PM') or THINKCUT_DIR / '视频项目管理' / 'bin')


def shot_files(job: Job):
    """分镜里实拍、图片、盖实拍引用的文件（找得到的，绝对路径）：[(镜头, 路径)]。"""
    bf = job.p('分镜.json')
    if not bf.exists(): return []
    from .sfx import local_sound
    out = []
    for s in rj(bf).get('shots', []):
        names = ([s['实拍']['文件']] if s.get('实拍') else []) + list((s.get('图片') or {}).get('文件') or []) + [c['文件'] for c in s.get('盖实拍') or []]
        out += [(s['id'], p) for p in (local_sound(job, n) for n in names) if p]
    return out


def unregistered_materials(job: Job, check=None):
    """分镜引用了项目 01_原始素材/ 下没登记进素材清单的文件（2026-11-22 261122 反馈单 新7：以前到 archive 才拦）。返回 ([要拦的], [提醒])。
    登记检查用视频项目管理现成的 project.check_materials（不另写一套）；引用的是别的项目的原始素材，就查那个项目。check 只给测试替换。"""
    root_of = {}
    for sid, p in shot_files(job):
        p = Path(p).resolve()
        q = next((q for q in Path(p).parents if q.name == '01_原始素材' and (q.parent / '项目.json').is_file()), None)
        if q: root_of.setdefault(q.parent, []).append((sid, Path(p)))
    if not root_of: return [], []
    if check is None:
        import importlib, sys
        b = pm_bin()
        if not (b / 'project.py').is_file(): return [], [f'没找到视频项目管理（{b}），分镜引用的原始素材有没有登记没查；存档前会查']
        if str(b) not in sys.path: sys.path.insert(0, str(b))
        check = importlib.import_module('project').check_materials
    bad, hints = [], []
    for root, refs in root_of.items():
        try: check(root); continue
        except RuntimeError as e: msg = str(e)
        except Exception as e: hints.append(f'{root.name} 的素材登记没查成（{type(e).__name__}: {str(e)[:80]}）'); continue
        miss = [(sid, p) for sid, p in refs if msg.startswith('原始素材存在未登记文件') and str(p.relative_to(root)) in msg]
        for sid, p in miss:
            bad.append(f"{sid} 用的 {p.relative_to(root)} 没登记进 {root.name} 的素材清单：先照 thinkcut-project「整理素材」登记（内容_用途_序号、素材清单.json、素材目录.md），再 materials-check")
        if not miss: hints.append(f'{root.name} 的原始素材检查没过（存档会拦）：{msg[:160]}')
    return bad, hints


LIVE_UPSCALE = 1.5     # 实拍源要放大超过这么多倍才铺满画面就报（1080P 横拍放进 3:4 4K 要 2.7 倍；4K 横拍 1.33 倍，是裁切不是不清楚，不报）


def live_resolution(job: Job):
    """分镜里“实拍／盖实拍”镜头的源文件清晰度（2026-11-22 261122 反馈单 新10 前一半：Motion、HyperFrames 的 1080P 版当实拍引用，export 不知道）。
    总装时实拍按本片画幅等比放大铺满、居中裁（anim._clip），放大倍数＝max(成片宽/源宽, 成片高/源高)，超过 LIVE_UPSCALE 的列出来。只提醒不拦；check、export 都打印。"""
    bf = job.p('分镜.json')
    if not bf.exists(): return []
    from .sfx import local_sound
    bad, seen = [], {}
    for s in rj(bf).get('shots', []):
        for name in ([s['实拍']['文件']] if s.get('实拍') else []) + [c['文件'] for c in s.get('盖实拍') or []]:
            p = local_sound(job, name)
            if not p: continue
            if p not in seen: st = probe(p); seen[p] = (int(st.get('width') or 0), int(st.get('height') or 0))
            w, h = seen[p]
            if w and h and max(job.W / w, job.H / h) > LIVE_UPSCALE:
                bad.append(f"{s['id']}（{Path(p).name}，{w}×{h}，要放大 {max(job.W / w, job.H / h):.1f} 倍）")
    if not bad: return []
    return [f"这几镜实拍的源文件清晰度不够（铺满 {job.W}×{job.H} 要放大超过 {LIVE_UPSCALE:g} 倍，多半还是 1080P）：{'；'.join(bad)}。"
            '先换成 4K 源再导出，或跟用户确认就用这个（只提醒不拦，2026-11-22 新10）']


def blacklist(job: Job):
    """音效收藏表（sfx_favorites.json）里的黑名单：{文件名: 为什么}。用户否掉过的音效，sfx 选到就停（2026-11-22）。"""
    p = Path(job['sfx_favorites'])
    if not p.exists(): return {}
    return {x['filename']: x.get('why', '') for x in rj(p).get('blacklist', [])}


def black_hits(job: Job, names):
    """names 里哪些是黑名单的音效（按文件名，项目里的路径也只看文件名）：[(名字, 为什么)]。"""
    bl = blacklist(job)
    return [(n, bl[Path(n).name]) for n in names if Path(n).name in bl]


def user_allowed(rows):
    """音效表里用户放行过的（sfx --allow-sounds 记的 放行）：{(秒, 文件名): 放行原话}。"""
    return {(round(e['at'], 2), Path(e['file']).name): e['放行'] for e in rows if e.get('放行') and 'at' in e}


def drop_allowed(offsets, allowed):
    """check 的“音效没卡在关键词上”去掉用户放行过的那几条（花字那半不算音效，照报）。"""
    return [x for x in offsets if not any(x[0] == t and x[1].startswith('音效 ' + n) for t, n in allowed)]


# ───────── ② 几何 ─────────
def _alpha(path, idxs, fps, w, h):
    """花字片段（带透明通道的整帧 mov）第 idxs 帧的透明通道，合成一张（任一帧有字就算）。"""
    out = []
    for i in idxs:
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, i - 0.3) / fps:.5f}', '-i', str(path), '-frames:v', '1',
                              '-vf', f'scale={w}:{h},format=rgba,alphaextract,format=gray', '-f', 'rawvideo', '-'], capture_output=True, stdin=subprocess.DEVNULL).stdout
        if len(raw) == w * h: out.append(np.frombuffer(raw, np.uint8).reshape(h, w) > 96)
    return np.any(out, axis=0) if out else np.zeros((h, w), bool)


def _png_alpha(path, w, h):
    from PIL import Image
    im = Image.open(path).convert('RGBA').getchannel('A').resize((w, h))
    return np.asarray(im) > 96


def _disc(box, w, h):
    x0, y0, x1, y1 = [v / SCALE for v in box]; yy, xx = np.mgrid[0:h, 0:w]
    cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2
    return (xx + .5 - cx) ** 2 + (yy + .5 - cy) ** 2 <= r * r


def _rect(box, w, h):
    m = np.zeros((h, w), bool); x0, y0, x1, y1 = [int(round(v / SCALE)) for v in box]
    m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = True; return m


def faces(job: Job, frames, matte=None, analyse=None):
    """A肉 第 frames 帧里人头的位置：{帧: (头顶 y, 头左 x, 头右 x) 或 None（没认出人）}，A肉 的像素。
    用人挡字同一套 RVM 抠像和 analyse（包装系统 rendang.py；不另引模型）：要的帧先取成一小段片子一次抠完，结果记在 合成/人像框.json，
    A肉 没变就直接用，不再抠。matte/analyse 只给测试替换。抠像环境不可用时抛 RuntimeError。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'assets' / '包装系统'))
    import rendang
    matte, analyse = matte or rendang.MATTE, analyse or rendang.analyse
    a = job.p('合成', 'A肉.mov'); st = a.stat(); sig = [st.st_size, int(st.st_mtime)]
    cp = job.p('合成', '人像框.json'); c = rj(cp) if cp.exists() else {}
    if c.get('A肉') != sig: c = {'说明': 'check 几何自检量的人头位置（A肉 像素：头顶 y、头左 x、头右 x）；A肉 变了自动重量', 'A肉': sig, 'frames': {}}
    need = sorted({int(f) for f in frames if str(int(f)) not in c['frames']})
    if need:
        s = probe(a); W, H = int(s['width']), int(s['height']); fps = float(job.fps)
        tmp = local_tmp(job.dir, '人像框', need=len(need) * 40 * 1024 ** 2, what='量人脸取的画面')
        try:
            for i, f in enumerate(need):
                run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{max(0.0, f - 0.3) / fps:.6f}', '-i', str(a), '-frames:v', '1', str(tmp / f'{i:04d}.png')], stdin=subprocess.DEVNULL)
            clip = tmp / '取样.mp4'
            run(['ffmpeg', '-v', 'error', '-y', '-framerate', '30', '-i', str(tmp / '%04d.png'), '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', str(clip)], stdin=subprocess.DEVNULL)
            low = np.asarray(matte(str(clip), 0, len(need), W, H, tmp).low)
            for i, f in enumerate(need):
                try: A = analyse(low[i:i + 1], W, H); c['frames'][str(f)] = [round(float(A['head_top'])), round(float(A['head_l'])), round(float(A['head_r']))]
                except SystemExit: c['frames'][str(f)] = None          # 这一帧没认出人
        finally:
            local_rm(tmp)
        wj(cp, c)
    return {int(f): c['frames'].get(str(int(f))) for f in frames}


def face_box(head, dy=0):
    """人头 (头顶, 左, 右) → 脸框 (x0, y0, x1, y1)：头宽 × 1.25 高，从头顶往下；dy＝人像下移。"""
    t, l, r = head
    return (l, t + dy, r, t + dy + FACE_H * (r - l))


def outside_circle(box, body):
    """脸框有多少比例落在小圆圈取景圆（pip_body 方块的内切圆）外面。"""
    x0, y0, x1, y1 = box; bx, by, bs = body; cx, cy, r = bx + bs / 2, by + bs / 2, bs / 2
    xs, ys = np.meshgrid(np.linspace(x0, x1, 41), np.linspace(y0, y1, 41))
    return float(((xs - cx) ** 2 + (ys - cy) ** 2 > r * r).mean())


def geometry(job: Job, face_fn=None):
    """小圆圈、花字、字幕、人脸互相遮挡（2026-11-22；依据：261110 圈挡变压器 3 条、261118“头被卡出血”“蓝白大字不要挡住人的脸”、B01/B03 圆圈挡字）。
    返回 ([问题：“第几秒：A 和 B 重叠多少”], [提醒], [说明／缺什么])。查这几样：
      停：花字×人脸（露脸段）、人头顶进导航条（露脸段）、小圆圈里人头被圆边切掉（pip_body 取景）、花字×字幕。
      只提醒：小圆圈×花字、小圆圈×字幕——字叠在圆圈上面、字还读得出，261118 用户认可的成片里就有一处（32.37 秒“差在”压在圆圈下沿），不拦。
      人挡字（本来就在人后面）、整屏卡片（bg）不查。
    动画里的主体／标签：Blender、HyperFrames 工程没有登记它们在画面上的位置，这次不查小圆圈压动画主体，只在说明里写缺什么。"""
    from .overlay import pip_box
    from .resolve import flower_rows
    face_fn = face_fn or faces
    fps = float(job.fps); W, H = job.W, job.H; w, h = W // SCALE, H // SCALE; px = SCALE * SCALE
    B = rj(job.p('分镜.json')); shots = B['shots']
    def shot_at(f): return next((s for s in shots if round(s['a'] * fps) <= f < round(s['b'] * fps)), None)
    def sec(f): return f'{f / fps:.2f} 秒'
    flowers = [x for x in flower_rows(job) if x.get('kind') != '人挡字' and not x.get('bg') and Path(x['file']).exists()]
    for x in flowers:
        n = int(x['end']) - int(x['start']); x['_m'] = _alpha(x['file'], sorted({int(n * .45), int(n * .75), max(0, n - 3)}), fps, w, h)
        x['_name'] = f"第 {x['n']} 个花字（{x.get('类型') or x.get('use')}）"
    sp = job.p('合成', '字幕', '字幕表.json'); subs = [r for r in (rj(sp) if sp.exists() else []) if Path(r['file']).exists()]
    for r in subs: r['_m'] = _png_alpha(r['file'], w, h)
    bad, warn, notes = [], [], []
    def ov(a, b): return int((a & b).sum()) * px
    # 花字×字幕
    for x in flowers:
        for r in subs:
            a, b = max(int(x['start']), int(r['start'])), min(int(x['end']), int(r['end']))
            if a < b and (o := ov(x['_m'], r['_m'])) >= MIN_OVERLAP:
                bad.append(f"{sec(a)}：{x['_name']}和第 {r['n']} 条字幕“{r['text'][:12]}”重叠 {o} 像素")
    # 小圆圈×花字、小圆圈×字幕（按 pip 烧圆圈同一套位置，分段、缩小都算）
    for name, a, b, m in [(x['_name'], int(x['start']), int(x['end']), x['_m']) for x in flowers] + [(f"第 {r['n']} 条字幕“{r['text'][:12]}”", int(r['start']), int(r['end']), r['_m']) for r in subs]:
        worst = None
        for f in sorted({a, (a + b) // 2, max(a, b - 1)} | set(range(a, b, 15))):
            s = shot_at(f)
            if not s or s['kind'] != 'B' or not s.get('pip'): continue
            pb = pip_box(job, B, f / fps)
            if pb and (o := ov(_disc(pb, w, h), m)) >= MIN_OVERLAP and (worst is None or o > worst[1]): worst = (f, o)
        if worst: warn.append(f"{sec(worst[0])}：小圆圈和{name}重叠 {worst[1]} 像素（字在圆圈上面；压到圆里的脸就挪开）")
    # 要量人脸的帧：露脸段里的花字（取它在露脸段那部分的中间）、每个露脸镜头的中间、每段小圆圈的头中尾
    want = {}
    for x in flowers:
        for s in (s for s in shots if s['kind'] == 'F'):
            a, b = max(int(x['start']), round(s['a'] * fps)), min(int(x['end']), round(s['b'] * fps))
            if a < b: want.setdefault(('花字', x['n'], (a + b) // 2), x)
    for s in shots:
        a, b = round(s['a'] * fps), round(s['b'] * fps)
        if s['kind'] == 'F' and b > a: want.setdefault(('露脸', s['id'], (a + b) // 2), s)
        if s['kind'] == 'B' and s.get('pip') and b - a > 10:
            for f in (a + 5, (a + b) // 2, b - 5):
                if pip_box(job, B, f / fps): want.setdefault(('圆圈', s['id'], f), s)
    if not want: return bad, warn, notes
    if not job.p('合成', 'A肉.mov').exists(): return bad, warn, notes + ['没有 合成/A肉.mov：人脸没量（花字压脸、头被圆圈切掉、头进导航条这几项没查）']
    try: heads = face_fn(job, sorted({k[2] for k in want}))
    except (RuntimeError, OSError, SystemExit) as e:
        return bad, warn, notes + [f'人脸没量（抠像用不了：{str(e)[:120]}）：花字压脸、头被圆圈切掉、头进导航条这几项这次没查，交审前抽帧看']
    dy = int(job.get('人像下移') or 0); nav_b = job.fmt['nav'][1] + job.fmt['nav'][3]; nav_on = job.p('合成', '导航', '导航整帧.mov').exists()
    body = job.get('pip_body') or job.fmt['body']; head_nav = []
    for (kind, who, f), obj in sorted(want.items(), key=lambda k: k[0][2]):
        hd = heads.get(f)
        if not hd: continue
        if kind == '圆圈':
            cut = outside_circle(face_box(hd), body)
            if cut > PIP_CUT: bad.append(f"{sec(f)}：{who} 的小圆圈里人头被圆边切掉 {cut:.0%}（pip_body {list(body)}，头顶在口播画面 y={hd[0]}）：pip_body 往上挪或放大，让整个头在圆里")
            continue
        fb = face_box(hd, dy)
        if nav_on and fb[1] < nav_b: head_nav.append((f, round(fb[1])))
        if kind == '花字':
            o = ov(_rect(fb, w, h), obj['_m']); area = (fb[2] - fb[0]) * (fb[3] - fb[1])
            if o >= MIN_OVERLAP and o > FACE_FRAC * area:
                bad.append(f"{sec(f)}：{obj['_name']}压到人脸 {o} 像素（脸框的 {o / area:.0%}）：挪开或把人缩小（261118 用户：“蓝白大字不要挡住人的脸”）")
    for f, top in head_nav[:1]:
        bad.append(f'{sec(f)} 等 {len(head_nav)} 处：露脸段人头顶（y={top}）进了导航条（下沿 y={nav_b}）：加大 job.json 的 人像下移')
    notes.append(f"量了 {len(heads)} 帧人脸（合成/人像框.json）；小圆圈压动画主体没查：Blender、HyperFrames 工程没有登记主体和标签在画面上的位置（事件表只有时间），要查得先让工程登记每个镜头主体／标签的框")
    return bad, warn, notes
