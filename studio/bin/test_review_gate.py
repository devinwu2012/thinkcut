#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试：交审前程序硬拦（2026-11-22，用户：“我觉得这个都做吧”）。不连达芬奇、不跑真抠像（人脸用替身）。
① 规矩：status 开头的已定规矩清单从 internal/架构审查交接.md 第 3 节生成；规矩定了以后写的给用户看的 MD 拦、以前的提醒；审片轮 4K 提醒；旧模板 出片.py 提醒。
② 几何：花字压脸、小圆圈里头被切、头进导航条、花字压字幕 → 停；小圆圈压花字/字幕 → 只提醒；量人脸结果按 A肉 记在 合成/人像框.json。
③ 声音：人声响度偏 -12.5 超过 2、底噪高于 -55 停；黑名单音效 sfx 停、--allow-sounds 放行并记下；音效和关键词差 >0.15 秒 sfx 停、放行后 check 不报。"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'; S = SK / 'scripts/thinkcut_edit.py'
sys.path.insert(0, str(SK / 'scripts'))
from thinkcut import guard, sfx, status, check as CK      # noqa: E402
from thinkcut.common import Job, rj, wj                    # noqa: E402
fails = []


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


def said(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try: r = fn(*a, **k)
        except SystemExit as e: r = e
    return r, buf.getvalue()


FF = ['ffmpeg', '-v', 'error', '-y']


def film(tmp, **job):
    root = Path(tmp) / '项目'; (root / '02_制作' / '素材_音效').mkdir(parents=True, exist_ok=True); wj(root / '项目.json', {'name': '项目'})
    d = root / '02_制作' / '自动剪辑'; d.mkdir(exist_ok=True); (d / '合成').mkdir(exist_ok=True)
    wj(d / 'job.json', dict({'format': '3:4满屏', 'nav_h': 224, 'frames': 600, 'main_timeline': 'v1', 'source': '/x', 'project': 'p'}, **job))
    return d


# ───────── ① 规矩 ─────────
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    (t / 'internal').mkdir(exist_ok=True); (t / 'internal' / '架构审查交接.md').write_text('# x\n## 2. 别的\n| a | b | c |\n## 3. 用户定过的规矩（细节在对应文件）\n\n| 主题 | 规矩 | 细节在 |\n|---|---|---|\n'
                                      '| 音效 | 只放重点，全片平均每分钟 ≤8 个（2026-11-12 用户定）；新音效先下候选 | `x.md` |\n| 封面 | 先 3:4 确认 | `y.md` |\n\n## 4. 当前状态\n| 不要 | 这个 | 表 |\n', encoding='utf-8')
    (t / 'AGENTS.md').write_text('**两条通用做法**\n- **给用户看的内容不写成 MD 文件**（原话）。\n- **默认用满电脑性能**（x）\n\n## 别的\n', encoding='utf-8')
    got = guard.rules_digest(t)
    check(got == ['音效：只放重点，全片平均每分钟 ≤8 个；新音效先下候选', '封面：先 3:4 确认', '通用：给用户看的内容不写成 MD 文件', '通用：默认用满电脑性能'],
          '已定规矩清单从交接文件第 3 节的表生成（去掉日期括号，别的节的表不算），再加 AGENTS.md 两条通用做法')
real = guard.rules_digest()
if (guard.THINKCUT_DIR / 'internal' / '架构审查交接.md').exists():      # 内部记录只在内部版；导出的干净版没有，状态里就不打这一段
  check(len(real) >= 10 and any(x.startswith('音效：') and '≤8' in x for x in real) and any('不写成 MD' in x for x in real), '真的交接文件读得出规矩（音效 ≤8 个、不写 MD 等）')
  _, out = said(status.rules_banner)
  check(out.startswith('【这个项目要遵守的已定规矩】') and '  - 音效：' in out, 'status 开头打印“这个项目要遵守的已定规矩”')

with tempfile.TemporaryDirectory() as t:
    d = film(t); job = Job(d); mk = d.parent
    (mk / 'V2交付清单.md').write_text('x'); (mk / 'V1目标.md').write_text('x'); (mk / '反馈记录.md').write_text('x'); (mk / '片子复盘.md').write_text('x')
    old = time.mktime(time.strptime('2026-11-20', '%Y-%m-%d')); os.utime(mk / 'V1目标.md', (old, old))
    hard, soft = guard.user_md(job)
    check([f.name for f in hard] == ['V2交付清单.md'] and [f.name for f in soft] == ['V1目标.md'], '给用户看的清单类 MD：11-21 规矩定了以后写的拦，以前留下的只提醒；反馈记录、片子复盘不算')
    # 审片轮 4K 提醒
    wj(d / '合成' / '动画清单.json', {'projects': [{'name': 'HF_x', 'kind': 'hyperframes'}, {'name': '实拍镜头', 'kind': 'live'}], 'shots': {}})
    wj(d / '合成' / '渲染尺寸.json', {'items': [{'name': 'HF_x', 'shot': 'B01', 'scale': 100}, {'name': '实拍镜头', 'shot': 'R01', 'scale': 100}]})
    h = guard.preview_4k(job)
    check(h is not None and 'B01' in h and 'R01' not in h, '审片轮登记了 4K 动画（用户还没说 OK）：提醒用 1080P；实拍镜头本来就是 100% 不算')
    cfg = rj(d / 'job.json'); cfg['export_ok'] = {'user_said': 'OK'}; wj(d / 'job.json', cfg)
    check(guard.preview_4k(Job(d)) is None, '用户说过 OK（有 export_ok）：不提醒')
    (d / 'HF_x').mkdir(); (d / 'HF_x' / '出片.py').write_text("p.add_argument('--scale')\n", encoding='utf-8')
    (d / 'HF_y').mkdir(); shutil.copy(SK / 'assets' / 'hyperframes_template' / '出片.py', d / 'HF_y' / '出片.py')
    check(guard.old_templates(job) == ['HF_x'], '片子里复制过去的旧模板 出片.py（没有 --4k）被认出来，新模板不算')

# ───────── ③ 声音 ─────────
with tempfile.TemporaryDirectory() as t:
    d = film(t, voice_polish={'file': '/x/口播_隔离修音.wav', 'lufs': -12.0, 'floor_db': -58.0}); job = Job(d)
    check(guard.voice(job)[0] == [], '修过音：用 voice_polish 记的响度 -12.0、底噪 -58（261118 试验田的数）不报')
    for lufs, floor, want in ((-15.0, -58.0, '响度'), (-12.0, -50.0, '底噪')):
        cfg = rj(d / 'job.json'); cfg['voice_polish'].update(lufs=lufs, floor_db=floor); wj(d / 'job.json', cfg)
        b = guard.voice(Job(d))[0]; check(len(b) == 1 and want in b[0], f'修过音：{want}超线就停（{lufs} LUFS、{floor} dB）')
    cfg = rj(d / 'job.json'); cfg.pop('voice_polish'); wj(d / 'job.json', cfg)
    (d / '合成' / 'A肉.mov').write_bytes(b'x'); wj(d / '合成' / '音量.json', {'voice_db': 0.4, 'voice_lufs': -12.0})
    b, info = guard.voice(Job(d), measure_floor=lambda f: -43.9)
    check(len(b) == 1 and '-43.5' in b[0] and 'voice-polish' in b[0] and '原口播' in info, '没修音：底噪量 A肉（-43.9）加人声增益（+0.4）= -43.5 高于 -55 → 停，叫先 voice-polish；响度用 sfx 记的 voice_lufs')

# 黑名单
from thinkcut.common import THINKCUT_DIR                       # noqa: E402  仓库被拷到别处（开发用的 git worktree）时和程序一样找程序目录
fav = rj(THINKCUT_DIR / 'core' / 'engine' / 'config' / 'sfx_favorites.json')
check('唰音效刷出标题.mp3' in {b['filename'] for b in fav.get('blacklist', [])}, '音效收藏表的黑名单里有“唰音效刷出标题.mp3”（261110、261117 用户三次否掉）')
with tempfile.TemporaryDirectory() as t:
    d = film(t, frames=1200); lib = Path(t) / '库'; lib.mkdir()
    subprocess.run(FF + ['-f', 'lavfi', '-i', 'sine=frequency=880:duration=0.5', '-af', 'adelay=100:all=1', str(lib / '唰.wav')], check=True)
    shutil.copy(lib / '唰.wav', lib / '叮.wav')
    wj(lib / '收藏.json', {'favorites': [{'filename': n, 'duration_frames': 20} for n in ('唰.wav', '叮.wav')], 'blacklist': [{'filename': '唰.wav', 'why': '用户说难听'}]})
    cfg = rj(d / 'job.json'); cfg.update(sfx_library=str(lib), sfx_favorites=str(lib / '收藏.json')); wj(d / 'job.json', cfg)
    subprocess.run(FF + ['-f', 'lavfi', '-i', 'sine=frequency=220:duration=3', '-c:a', 'pcm_s16le', str(d / '合成' / 'A肉.mov')], check=True)
    wj(d / '分镜.json', {'shots': [], 'sfx': [{'t': 5.0, 'file': '唰.wav'}, {'t': 15.0, 'file': '叮.wav'}]})
    r, out = said(sfx.run, Job(d))
    check(isinstance(r, SystemExit) and '唰.wav' in str(r) and '用户说难听' in str(r) and '--allow-sounds' in str(r) and not (d / '合成' / '音效表.json').exists(),
          'sfx 选到黑名单音效：停下说是哪个、为什么，音效表不写')
    r, out = said(sfx.run, Job(d), True); rows = rj(d / '合成' / '音效表.json')
    check(not isinstance(r, SystemExit) and '放行' in rows[0] and '黑名单' in rows[0]['放行'] and '放行' not in rows[1] and '【放行】' in out, 'sfx --allow-sounds：照写，放行的那条在音效表里记“放行”')
    check([x[1] for x in guard.black_hits(Job(d), [e['file'] for e in rows])] == ['用户说难听'] and guard.user_allowed(rows) and list(guard.user_allowed(rows))[0][1] == '唰.wav',
          'check 认得出音效表里的黑名单音效和用户放行记录')
    p = subprocess.run([sys.executable, '-B', str(S), 'sfx', '-h'], capture_output=True, text=True)
    check('--allow-sounds' in p.stdout, '命令行 sfx 有 --allow-sounds')

# 关键词偏差：sfx 写音效表前就停；放行后 check 那项不报
FPS = 30000 / 1001
TEXT = '但那是营销行为不是设计行为最后定在门套上沿齐平结束'
with tempfile.TemporaryDirectory() as t:
    d = film(t, frames=1200); lib = Path(t) / '库'; lib.mkdir()
    subprocess.run(FF + ['-f', 'lavfi', '-i', 'sine=frequency=880:duration=0.5', str(lib / '错误音.wav')], check=True)
    wj(lib / '收藏.json', {'favorites': [{'filename': '错误音.wav', 'duration_frames': 15}]})
    cfg = rj(d / 'job.json'); cfg.update(sfx_library=str(lib), sfx_favorites=str(lib / '收藏.json')); wj(d / 'job.json', cfg)
    subprocess.run(FF + ['-f', 'lavfi', '-i', 'sine=frequency=220:duration=3', '-c:a', 'pcm_s16le', str(d / '合成' / 'A肉.mov')], check=True)
    at = lambda w: round(1.0 + TEXT.index(w) * 0.3, 3)
    wj(d / '口播字时间.json', {'tokens': [{'c': c, 't0': round(1.0 + i * 0.3, 3), 't1': round(1.25 + i * 0.3, 3)} for i, c in enumerate(TEXT)]})
    wj(d / '花字.json', [{'use': 'koubo', 'at': '但那是营销行为', 'lines': ['那是〖营销行为〗']}])
    (d / '合成' / '花字').mkdir(); k = at('营销行为')
    s0 = at('但那是')        # 261117 那次：大字和音效都卡在句首“但那是”，比“营销行为”早约 0.3 秒（这里早 0.9）
    wj(d / '合成' / '花字' / '花字表.json', {'items': [{'n': 1, 'use': 'koubo', 'kind': '文字', 'file': '/k.mov', 'start': round(s0 * FPS), 'end': round(s0 * FPS) + 60}]})
    wj(d / '分镜.json', {'shots': [], 'sfx': [{'t': at('但那是'), 'file': '错误音.wav', 'beat': '结论'}]})
    r, out = said(sfx.run, Job(d))
    check(isinstance(r, SystemExit) and '营销行为' in str(r) and '早 0.9' in str(r) and not (d / '合成' / '音效表.json').exists(), '音效比关键词早 0.9 秒（卡在句首）：sfx 停下（以前只提醒），音效表不写')
    r, out = said(sfx.run, Job(d), True); rows = rj(d / '合成' / '音效表.json')
    off, _ = CK.keyword_offsets(Job(d))
    check(not isinstance(r, SystemExit) and '比关键词「营销行为」早 0.9 秒' in rows[0].get('放行', '') and [x[1].split('（')[0] for x in off] == ['第 1 个花字', '音效 错误音.wav'] and [x[1].split('（')[0] for x in guard.drop_allowed(off, guard.user_allowed(rows))] == ['第 1 个花字'],
          '用户听过认可（--allow-sounds）：照写并记放行；check 的“没卡在关键词上”去掉放行过的这条音效（大字那半照报）')
    wj(d / '分镜.json', {'shots': [], 'sfx': [{'t': k, 'file': '错误音.wav', 'beat': '结论'}]})
    r, out = said(sfx.run, Job(d)); rows = rj(d / '合成' / '音效表.json')
    check(not isinstance(r, SystemExit) and '放行' not in rows[0], '卡在关键词上：照常写，不用放行')

# ───────── ② 几何 ─────────
def flower_mov(path, box, W=2160, H=2880, n=10):
    """整帧透明 mov（按 1/10 尺寸出，check 会放大到整帧），box 处一块不透明。"""
    png = Path(str(path) + '.png'); sub_png(png, box, W, H)
    subprocess.run(FF + ['-loop', '1', '-framerate', '30000/1001', '-i', str(png), '-frames:v', str(n), '-c:v', 'qtrle', '-pix_fmt', 'argb', str(path)], check=True)


def sub_png(path, box, W=2160, H=2880):
    from PIL import Image, ImageDraw
    im = Image.new('RGBA', (W // 10, H // 10), (0, 0, 0, 0)); ImageDraw.Draw(im).rectangle([v // 10 for v in box], fill=(255, 255, 255, 255)); im.save(path)


with tempfile.TemporaryDirectory() as t:
    d = film(t, pip_body=[80, 0, 2000], 人像下移=0); c = d / '合成'
    (c / 'A肉.mov').write_bytes(b'x'); (c / '花字').mkdir(); (c / '字幕').mkdir()
    # 0–10 秒露脸、10–20 秒动画带左下小圆圈
    wj(d / '分镜.json', {'shots': [{'id': 'F_0', 'kind': 'F', 'a': 0.0, 'b': 10.0}, {'id': 'B01', 'kind': 'B', 'a': 10.0, 'b': 20.0, 'pip': 'L'}]})
    HEAD = (100, 640, 1400)        # 头顶 y、头左、头右（261118 量出来的样子）
    calls = []
    def fake_faces(job, frames): calls.append(list(frames)); return {f: HEAD for f in frames}
    def setup(flowers, subs):
        rows = []
        for i, (box, a, b) in enumerate(flowers, 1):
            f = c / '花字' / f'{i:02d}.mov'; flower_mov(f, box, n=b - a); rows.append({'n': i, 'use': 'keywords', 'kind': '文字', 'file': str(f), 'start': a, 'end': b})
        wj(c / '花字' / '花字表.json', {'items': rows}); srows = []
        for i, (box, a, b) in enumerate(subs, 1):
            f = c / '字幕' / f'{i}.png'; sub_png(f, box); srows.append({'n': i, 'file': str(f), 'start': a, 'end': b, 'text': f'第{i}句'})
        wj(c / '字幕' / '字幕表.json', srows)
    # 花字压脸（左上角，261118 第 1 轮“蓝白大字不要挡住人的脸”）、花字在脸下面不报
    setup([((120, 420, 1300, 900), 30, 60), ((120, 1800, 1300, 2300), 90, 120)], [])
    bad, warn, notes = guard.geometry(Job(d), fake_faces)
    check(len(bad) == 1 and '第 1 个花字' in bad[0] and '压到人脸' in bad[0] and '1.50 秒' in bad[0] and '像素' in bad[0], '露脸段花字压到人脸：停，报第几秒、哪个花字、重叠多少像素；在脸下面的不报')
    check(any('没有登记主体' in x for x in notes), '说明里写清：动画主体/标签没登记位置，小圆圈压动画主体这次没查')
    # 小圆圈里头被切（pip_body 还是默认的 (80,340,2000)，头顶在 100：261118“头被卡出血”）
    setup([], []); cfg = rj(d / 'job.json'); cfg.pop('pip_body'); wj(d / 'job.json', cfg)
    bad, warn, notes = guard.geometry(Job(d), fake_faces)
    check(bad and all('B01 的小圆圈里人头被圆边切掉' in x for x in bad), '小圆圈取景（pip_body 默认 80,340,2000）切掉头顶：停（261118 改成 80,0,2000 前就是这样）')
    cfg['pip_body'] = [80, 0, 2000]; wj(d / 'job.json', cfg)
    check(guard.geometry(Job(d), fake_faces)[0] == [], 'pip_body 改成 80,0,2000（261118 用户认可的）：不报')
    # 花字压字幕 → 停；小圆圈压字幕、压花字 → 只提醒
    setup([((300, 2400, 1800, 2700), 30, 60), ((300, 1600, 600, 1700), 330, 360)], [((200, 2450, 1900, 2650), 40, 80), ((300, 2000, 700, 2150), 340, 380)])
    bad, warn, notes = guard.geometry(Job(d), fake_faces)
    check(len(bad) == 1 and '第 1 个花字' in bad[0] and '第 1 条字幕' in bad[0] and '重叠' in bad[0], '花字和字幕叠在一起：停')
    check(len(warn) == 2 and all('小圆圈和' in x for x in warn) and not any('小圆圈' in x for x in bad), '小圆圈压花字、压字幕：只提醒（字在圆圈上面，261118 认可的成片里有一处）')
    # 头进导航条（有导航条时）
    setup([], []); (c / '导航').mkdir(exist_ok=True); (c / '导航' / '导航整帧.mov').write_bytes(b'x')
    bad, _, _ = guard.geometry(Job(d), lambda job, fr: {f: (100, 640, 1400) for f in fr})
    check(len(bad) == 1 and '进了导航条' in bad[0] and '人像下移' in bad[0], '露脸段人头顶（y=100）进了导航条（下沿 224）：停，叫加大人像下移')
    cfg['人像下移'] = 230; wj(d / 'job.json', cfg)
    check(guard.geometry(Job(d), lambda job, fr: {f: (100, 640, 1400) for f in fr})[0] == [], '人像下移 230 后头顶在 330：不报')
    def broken(job, fr): raise RuntimeError('抠像环境没装')
    bad, _, notes = guard.geometry(Job(d), broken)
    check(bad == [] and any('人脸没量' in x and '抠像环境没装' in x for x in notes), '抠像用不了：人脸那几项不查、写进说明，不算不过也不崩')

# 量人脸：一次取样抠完，结果按 A肉 记在 合成/人像框.json，A肉 没变不再抠
with tempfile.TemporaryDirectory() as t:
    d = film(t); a = d / '合成' / 'A肉.mov'
    subprocess.run(FF + ['-f', 'lavfi', '-i', 'color=c=gray:s=216x288:r=30000/1001:d=2', str(a)], check=True)
    seen = []
    class M:
        def __init__(self, n): self.low = np.zeros((n, 96, 72), np.uint8)
    def matte(src, start, n, W, H, wd): seen.append((Path(src).name, n, W, H)); return M(n)
    def analyse(low, W, H): return {'head_top': 10.0, 'head_l': 20.0, 'head_r': 30.0}
    got = guard.faces(Job(d), [5, 30, 5], matte, analyse)
    check(got == {5: [10, 20, 30], 30: [10, 20, 30]} and seen == [('取样.mp4', 2, 216, 288)] and (d / '合成' / '人像框.json').exists(), '要量的几帧取成一小段一次抠完，结果记进 合成/人像框.json')
    guard.faces(Job(d), [5, 30], matte, analyse)
    check(len(seen) == 1, 'A肉 没变：直接用记下的，不再抠')
    os.utime(a, (time.time() + 5, time.time() + 5)); guard.faces(Job(d), [5], matte, analyse)
    check(len(seen) == 2, 'A肉 重出过：重新量')

print('全部测试通过' if not fails else f'有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
