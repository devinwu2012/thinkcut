# -*- coding: utf-8 -*-
# 包装系统·人挡字风格库（2026-11-13 用户定）：特别重点的大字放在人后面、人挡住字的一部分。
# 4 套风格（第三轮样张，用户：“这 4 个都可以作为风格库放在里面”），每条片子用一套：
#   1 苹果式静默大字  2 杂志刊名压头  3 镜头对焦字  4 超大出画面＋遮罩升起
# 每套＝字（字体、字号占画宽、颜色、可选上方小字）＋背景处理（压暗/虚化）＋整体调色（胶片、颗粒）＋出场动画。
# 这些处理作用在整个画面上，所以每处人挡字出一段**不透明的整幅片段**：合成/A肉.mov（job.json 写了 人像下移 就用 A肉_人像下移<值>.mov，和审核线同取景）那一段 → 处理过的底 → 大字 → 抠出来的人（RVM 遮罩）→ 调色，
# 和 A肉 同帧率、同尺寸、逐帧对齐，放审核线“花字”轨盖住那一段口播画面，声音仍用口播原声。由 huazi.py 调度（花字.json 里 "use": "人挡字"）。
# 例外（2026-11-13 用户定）：人挡字可以不用苹方、不限蓝白两色，用 素材库/字体/ 里的开源字体（思源黑体/宋体、Inter，OFL），按文件路径加载，不装进系统。
# 参数来自 人挡字样张/调研和定稿过程.md 第三轮和生成器 25_风格样张v3.py（行业经验值，样张经用户认可）。
import json, os, subprocess, sys, time
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FPS_S, FPS = '30000/1001', 30000 / 1001
REF_W = 2160                                     # 参数按 4K 3:4（2160×2880）定，别的尺寸（测试用小图）按宽度等比
# 程序目录按这个文件自己的位置推（…/<程序目录>/studio/skill/thinkcut-edit/assets/包装系统/），NAS 换了挂载名 也能用（2026-11-21 体检）；
# 这份代码被拷到别处时按这台 Mac 的技能链接 ~/.claude/skills/thinkcut-edit 找（2026-11-24 起不写死盘符路径）
_HERE = Path(__file__).resolve()
THINKCUT_DIR = _HERE.parents[5] if len(_HERE.parents) > 5 and _HERE.parents[4].name in ('studio', '口播自动剪辑') else (Path.home() / '.claude' / 'skills' / 'thinkcut-edit').resolve().parents[2]
FONT_DIR = Path(os.environ.get('THINKCUT_FONT_DIR') or THINKCUT_DIR / '素材库' / '字体')
FONTS = {'黑体特粗': 'SourceHanSansSC-Heavy.otf', '黑体细': 'SourceHanSansSC-Light.otf', '宋体特粗': 'SourceHanSerifSC-Heavy.otf',
         'Inter细': 'Inter/InterDisplay-Light.ttf', 'Inter特粗': 'Inter/InterDisplay-Black.ttf'}
FONT_SOURCE = ('思源黑体 https://github.com/adobe-fonts/source-han-sans/releases（SourceHanSansSC-Heavy/Light.otf）、'
               '思源宋体 https://github.com/adobe-fonts/source-han-serif/releases（SourceHanSerifSC-Heavy.otf）、'
               'Inter https://github.com/rsms/inter/releases（InterDisplay-*.ttf 放 Inter/ 子文件夹），均 SIL OFL 免费商用')
MATTING_REPO = str(THINKCUT_DIR / 'core')             # 抠人用口播剪辑现成的 RVM（engine/fx/matte.py 的 generate），只调用不修改
LIMIT = dict(count=3, gap=20.0)                    # 默认：一条片子最多 3 处、两处之间至少 20 秒（job.json 的 人挡字上限 / 人挡字间隔秒 可调）
Y_BOTTOM = 2400                                    # 大字底边不低于字幕上沿
SIDE_MARGIN = 120                                  # 头侧放法：离画面左右边至少这么多
SMALL = dict(font='黑体细', size=64, rgba=(255, 255, 255, 235), track=.35, shadow=.35, above=105)   # 上方小字（样张：小字顶 440、大字顶 545）
# 遮挡判定（2026-11-21 用户：“中间那 2 个字被稍微挡住一点，比如挡住一半或者 1/3”“虽然要挡住，但是不能被挡住的字完全读不出来”）：
# 按每个字算，逐帧、去掉出场段，每个字取最挡的那 5% 帧以外的最大值（95 分位，抠像偶尔一两帧抖不算），都不超过一半才算通过。
CHAR_MAX, CHAR_Q = .50, 95
NAV_GAP = 26                                       # 上移放法：大字字墨顶最高到导航条下沿以下这么多（job.json 的 nav_h，没写 340）
MIN_SIZE = 200                                     # 大字字号（4K 像素）不小于它：要缩得更小才放得下就停下说，不悄悄缩成看不清的小字
JOIN = 2                                           # 两处人挡字前后相隔不超过 2 帧算“紧挨”（连着换字）：算一处，不查间隔

# ───────── 4 套风格（数值都按 4K 3:4）─────────
# width 大字占画宽；top_min 默认放法时大字字墨顶最高放到哪（样张位置）；overlap 默认放法时大字下部压到头顶以下的比例（按第三轮室内样张量的）；
# cover_min 被挡最多的那个字至少挡这么多（再少不像人挡字；每个字最多 CHAR_MAX）；fallback 默认放法不行时依次试的放法：
# 头后＝放在 top_min；上移＝往上挪，最高到导航条下面；放大/缩字＝换字号再按“头顶→头后→上移”找位置；头侧＝整个词放头的一边（不拆到头两侧）。
H_BACK, UP, SIDE = {'放法': '头后'}, {'放法': '上移'}, {'放法': '头侧'}
SHRINK, GROW = (lambda s: {'放法': '缩字', '比例': s}), (lambda s: {'放法': '放大', '比例': s})
STYLES = {
    1: dict(name='苹果式静默大字', font='黑体细', width=.58, rgba=(255, 255, 255, 255), shadow=.25, top_min=430, overlap=.30,
            bg_dark=.15, motion='apple', inn=.8, rise=58, grow=.01, out=.30, cover_min=.06,
            fallback=[H_BACK, UP, GROW(1.2), SHRINK(.85), SHRINK(.7), SIDE],
            特点='细黑体纯白、背景压暗 15%，0.8 秒慢慢浮现上移，停留时每秒放大 1%', 适合='干净、偏暗的室内背景；讲一个概念、一个结论的“定场”时刻'),
    2: dict(name='杂志刊名压头', font='宋体特粗', width=.90, rgba=(255, 255, 255, 255), shadow=.30, top_min=545, overlap=.70,
            motion='cut', inn=0.0, out=0.0, grade='film', cover_min=.08,
            fallback=[H_BACK, UP, GROW(1.05), SHRINK(.85), SHRINK(.72)],       # 放大 1.05＝94% 画宽（《别墅装灯》用户要的“字更大”）
            特点='宋体特粗撑满 90% 画宽、上方可加一行小字，硬切出现，整体胶片调色＋颗粒＋暗角', 适合='章节开头、“第二件事”这类刊名式标题；背景亮或乱也压得住'),
    3: dict(name='镜头对焦字', font='黑体特粗', width=.70, rgba=(255, 255, 255, 255), shadow=.45, top_min=450, overlap=.48,
            bg_blur=16, motion='focus', inn=.6, blur=60, zoom=.15, out=.30, cover_min=.08,
            fallback=[H_BACK, UP, GROW(1.2), SHRINK(.85), SHRINK(.7), SIDE],
            特点='黑体特粗白字，0.6 秒从模糊放大收到清晰，背景同时虚化到 16px', 适合='背景杂乱（窗外、工地、柜子多）时，虚化把背景压下去；强调“关键在这”'),
    4: dict(name='超大出画面＋遮罩升起', font='黑体特粗', width=1.18, rgba=(26, 34, 48, 225), shadow=0.0, top_min=380, overlap=.63,
            motion='mask_rise', inn=.4, stagger=.08, out=.30, cover_min=.08,
            fallback=[H_BACK, UP, SHRINK(.85)],
            特点='深蓝灰巨字左右出画（118% 画宽），每个字从底线下面升起', 适合='亮背景（白墙、亮窗）、头顶空间紧；2–3 个字的短词'),
}
ALIASES = {**{str(k): k for k in STYLES}, **{v['name']: k for k, v in STYLES.items()}, **{f"{k}_{v['name']}": k for k, v in STYLES.items()},
           '超大出画面_遮罩升起': 4, '4_超大出画面_遮罩升起': 4, '超大出画面': 4}

# ───────── 没写风格时自动选（2026-11-14 用户定：不停下问，按这条片子的画面自动选一套写回 job.json，不合适再换）─────────
# 看第一处人挡字那一段露脸画面：大字要放的那一带（导航条下到头顶下面一点）去掉人以后的背景，量“亮度”“杂乱”，再看头顶空间和字数。
# 从上往下第一条成立的就选它（同样的画面永远选同一套，不随机）。阈值按第三轮样张素材量过：《水电》室内浅灰墙 亮度≈160、杂乱≈2.2；
# 《地下室》户外窗前 亮度≈79、杂乱≈3.7；头顶 y：高取景 825、正常成片取景 573–615（默认头顶放法要 ≥825 才放得下）。
BRIGHT, BUSY, TIGHT, MANY = 150, 3.0, 700, 4        # 背景平均亮度 0–255 / 背景平均梯度（按 720 宽算）/ 头顶 y 小于它算紧（4K 像素）/ 字数
AUTO_RULES = [
    ('背景杂乱', lambda f: f['杂乱'] >= BUSY, 3, '背景乱（窗外、杂物多），镜头对焦字会把背景虚化，大字才突出；细字、深色字在乱背景上容易被吃掉（结论.md：户外细字在窗前偏弱）'),
    ('背景亮、字少', lambda f: f['亮度'] >= BRIGHT and f['字数'] < MANY, 4, '背景亮，白字会发虚（结论.md：白字在亮背景前偏淡）；超大出画面用深色字，亮底上最清楚'),
    ('背景亮、字多', lambda f: f['亮度'] >= BRIGHT, 2, '背景亮且字多：杂志刊名有胶片调色压暗、宋体特粗笔画粗，字多也读得出（超大出画面字多会切掉太多）'),
    ('头顶紧、字少', lambda f: f['头顶'] < TIGHT and f['字数'] < MANY, 4, '头顶空间紧，超大出画面本来就压在头后、放得最高，最不用退'),
    ('头顶紧、字多', lambda f: f['头顶'] < TIGHT, 2, '头顶空间紧且字多：杂志刊名本来就压头，字多也撑得住'),
    ('其余（背景干净偏暗、头顶有空）', lambda f: True, 1, '背景干净、不亮、头顶有空间，最克制的苹果式静默大字最合适'),
]


def eo(u): u = max(0.0, min(1.0, u)); return 1 - (1 - u) ** 3
def ei(u): u = max(0.0, min(1.0, u)); return u ** 3


def stop(msg): raise SystemExit('停止：' + msg)


def style_list():
    return '；'.join(f"{k} {v['name']}（{v['特点']}；适合{v['适合']}）" for k, v in STYLES.items())


def style_of(v):
    """1–4、套名或“1_套名”→ 编号；认不出就停下列出 4 套。"""
    k = ALIASES.get(str(v).strip())
    if k is None: stop(f'人挡字风格“{v}”认不出。4 套：{style_list()}。job.json 写 "人挡字风格": 1–4 或套名')
    return k


def font_path(name):
    p = FONT_DIR / FONTS[name]
    if not p.is_file():
        stop(f'缺人挡字字体：{p}。人挡字用 素材库/字体/ 里的开源字体（按文件路径加载、不装进系统）；'
             f'原件在使用版程序目录的 素材库/字体/，丢了就从官方下载放回去：{FONT_SOURCE}')
    return p


def check_fonts(styles, small=False):
    for k in styles:
        font_path(STYLES[k]['font'])
    if small: font_path(SMALL['font'])


# ───────── 排字 ─────────
def chars(font, word, size, rgba, track=0.0, shadow=0.0):
    """逐字画（方便逐字动画）。返回 [(字图, 字墨, x)]、整行宽、字墨顶到图顶的距离 pad、字墨高。"""
    f = ImageFont.truetype(str(font_path(font)), max(4, int(size))); pad = int(size * .3)
    top = min(f.getbbox(c)[1] for c in word); bot = max(f.getbbox(c)[3] for c in word)
    out, x = [], 0.0
    for c in word:
        w, h = int(f.getlength(c)) + 2 * pad, bot - top + 2 * pad; org = (pad, pad - top)
        ink = Image.new('L', (w, h), 0); ImageDraw.Draw(ink).text(org, c, font=f, fill=255)
        im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        if shadow:
            s = Image.new('RGBA', (w, h), (0, 0, 0, 0)); s.putalpha(ink.point(lambda v: int(v * shadow)))
            im.alpha_composite(s.filter(ImageFilter.GaussianBlur(size * .04)), (0, int(size * .02)))
        fill = Image.new('RGBA', (w, h), tuple(rgba[:3]) + (0,)); fill.putalpha(ink.point(lambda v: int(v * rgba[3] / 255)))
        im.alpha_composite(fill)
        out.append((im, ink, x)); x += f.getlength(c) + track * size
    return out, x - track * size, pad, bot - top


def fit(font, word, width_px, track=0.0):
    f = ImageFont.truetype(str(font_path(font)), 100)
    return int(width_px / (sum(f.getlength(c) for c in word) + track * 100 * (len(word) - 1)) * 100)


def layout(st, word, small, W, scale=1.0):
    """排好大字（和可选小字）的字形，位置之后由 place() 定。返回 dict(lines=[小字?, 大字], big=大字)。"""
    k = W / REF_W; size = fit(st['font'], word, W * st['width'] * scale)
    big = dict(font=st['font'], word=word, size=size, rgba=st['rgba'], track=0.0, shadow=st['shadow'])
    big['G'], big['w'], big['pad'], big['h'] = chars(big['font'], word, size, big['rgba'], 0.0, big['shadow'])
    lines = [big]
    if small:
        sm = dict(font=SMALL['font'], word=small, size=max(8, round(SMALL['size'] * k * max(scale, .85))), rgba=SMALL['rgba'], track=SMALL['track'], shadow=SMALL['shadow'])
        sm['G'], sm['w'], sm['pad'], sm['h'] = chars(sm['font'], small, sm['size'], sm['rgba'], sm['track'], sm['shadow'])
        lines = [sm, big]
    return dict(lines=lines, big=big, scale=scale, k=k)


def set_pos(lay, W, x0, top):
    """大字字墨左边 x0、顶 top；小字居中在大字上方。"""
    big = lay['big']; big['x0'], big['top'] = x0, top
    for ln in lay['lines']:
        if ln is big: continue
        ln['x0'] = x0 + (big['w'] - ln['w']) / 2; ln['top'] = top - SMALL['above'] * lay['k'] * max(lay['scale'], .85)
    return lay


def ink_canvas(lay, W, H):
    """大字最终状态（出场完成后）的字墨，用来估遮挡。"""
    big = lay['big']; c = Image.new('L', (W, H), 0)
    for im, ink, gx in big['G']:
        c.paste(255, (int(big['x0'] + gx - big['pad']), int(big['top'] - big['pad'])), ink)
    return c


def text_layer(st, lay, t, dur, W, H, glyphs=None):
    """第 t 秒（从人挡字开始算）的字层 RGBA、大字字墨 L、出场进度 0–1。glyphs 给一个列表时，把大字每个字这一帧的 (x, y, 字墨) 放进去（逐字算遮挡用）。"""
    canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0)); inkc = Image.new('L', (W, H), 0); k = lay['k']
    m, o = st['motion'], st['out']
    out = ei((t - (dur - o)) / o) if o else 0.0
    prog = 1.0
    for ln in lay['lines']:
        is_big = ln is lay['big']
        for j, (im, ink, gx) in enumerate(ln['G']):
            scale, blur, dy, alpha, crop = 1.0, 0.0, 0.0, 1.0, None
            if m == 'apple':
                u = eo(t / st['inn']); alpha, dy = u, st['rise'] * k * (1 - u); scale = 1 + st['grow'] * max(0, t - st['inn']); prog = u
            elif m == 'focus':
                u = eo(t / st['inn']); alpha = eo(t / (st['inn'] / 2)); blur = st['blur'] * k * (1 - u); scale = 1 + st['zoom'] * (1 - u); prog = u
            elif m == 'mask_rise':
                u = eo((t - st['stagger'] * j) / st['inn']); crop = u; prog = min(prog, u) if j else u
            alpha *= (1 - out)
            if alpha <= .003 or (crop is not None and crop <= 0): continue
            g, gi = im, ink
            cx_line = ln['x0'] + ln['w'] / 2; cy_line = ln['top'] + (g.height - 2 * ln['pad']) / 2
            if crop is not None:   # 遮罩升起：字从自己底边（看不见的线）下面升上来，线以下不显示
                vis = max(1, int(g.height * crop)); g = g.crop((0, 0, g.width, vis)); gi = gi.crop((0, 0, gi.width, vis))
                px, py = ln['x0'] + gx - ln['pad'], ln['top'] - ln['pad'] + (im.height - vis)
            else:
                if scale != 1.0:
                    g = g.resize((max(1, round(g.width * scale)), max(1, round(g.height * scale))), Image.BICUBIC); gi = gi.resize(g.size, Image.BICUBIC)
                gx0 = ln['x0'] + gx - ln['pad'] + im.width / 2; gy0 = ln['top'] - ln['pad'] + im.height / 2
                px = cx_line + (gx0 - cx_line) * scale - g.width / 2; py = cy_line + (gy0 - cy_line) * scale - g.height / 2 + dy
            if blur > .3: g = g.filter(ImageFilter.GaussianBlur(blur))
            if alpha < .999:
                g = g.copy(); g.putalpha(g.getchannel('A').point(lambda v: int(v * alpha))); gi = gi.point(lambda v: int(v * alpha))
            canvas.paste(g, (int(px), int(py)), g) if px < 0 or py < 0 else canvas.alpha_composite(g, (int(px), int(py)))
            if is_big:
                inkc.paste(gi, (int(px), int(py)), gi)
                if glyphs is not None: glyphs.append((j, int(px), int(py), np.asarray(gi, np.float32) / 255))
    return canvas, inkc, prog * (1 - out)


def entrance(st, word):
    """出场段长度（秒）：遮挡比例从这之后才算。"""
    if st['motion'] == 'mask_rise': return st['inn'] + st['stagger'] * (len(word) - 1)
    return st['inn']


# ───────── 遮罩分析、放法 ─────────
def analyse(low, W, H):
    """低清遮罩（n×h×w，0–255）→ 头顶 y、头的左右（4K 像素）、平均遮罩（估遮挡用）。"""
    n, h, w = low.shape; kx, ky = W / w, H / h
    u = (low > 127).max(0); rows = np.where(u.sum(1) >= max(1, int(w * .005)))[0]
    if not len(rows): stop('这一段遮罩里没认出人：人挡字要有人在画面里（露脸段）')
    top = int(rows[0]); band = u[top:top + max(1, int(.10 * h))]; cols = np.where(band.any(0))[0]
    pick = low[np.linspace(0, n - 1, min(n, 30)).round().astype(int)]          # 逐字估遮挡用：最多均匀取 30 帧
    return dict(head_top=top * ky, head_l=cols[0] * kx, head_r=(cols[-1] + 1) * kx, mean=low.astype(np.float32).mean(0) / 255, frames=pick.astype(np.float32) / 255)


def coverage(ink, alpha):
    """字墨被人挡住的比例＝Σ(字墨×人物遮罩)/Σ字墨（都按 0–1）。"""
    ink = np.asarray(ink, np.float32); s = ink.sum()
    return float((ink * np.asarray(alpha, np.float32)).sum() / s) if s else 0.0


def est_cover(lay, A, W, H):
    mean = A['mean']; c = ink_canvas(lay, W, H).resize((mean.shape[1], mean.shape[0]), Image.BOX)
    return coverage(np.asarray(c, np.float32) / 255, mean)


def cover_at(g, mask, x, y):
    """一个字的字墨 g（0–1 小图）左上角放在遮罩 mask 的 (x, y)：被挡比例。出画的那部分字墨看不见，不算。"""
    h, w = mask.shape; x0, y0 = max(0, x), max(0, y); x1, y1 = min(w, x + g.shape[1]), min(h, y + g.shape[0])
    if x1 <= x0 or y1 <= y0: return 0.0
    gg = g[y0 - y:y1 - y, x0 - x:x1 - x]; s = gg.sum()
    return float((gg * mask[y0:y1, x0:x1]).sum() / s) if s else 0.0


def char_cover(lay, masks, W, H):
    """每个大字（最终状态）被挡住的比例，按字的顺序。masks：几帧低清遮罩（n×h×w，0–1），每个字逐帧算、取 CHAR_Q 分位（和出片后的判定一样）。"""
    big = lay['big']; n, h, w = masks.shape; kx, ky = w / W, h / H; out = []
    for im, ink, gx in big['G']:
        g = np.asarray(ink.resize((max(1, round(ink.width * kx)), max(1, round(ink.height * ky))), Image.BOX), np.float32) / 255
        x, y = round((big['x0'] + gx - big['pad']) * kx), round((big['top'] - big['pad']) * ky)
        out.append(float(np.percentile([cover_at(g, m, x, y) for m in masks], CHAR_Q)))
    return out


def judge(word, cs, lo):
    """每个字的被挡比例 → (通过, 说明)。每个字都不超过 CHAR_MAX，最挡的那个字至少 lo。"""
    j = max(range(len(cs)), key=lambda i: cs[i]); m = cs[j]
    each = '、'.join(f'{c}{v:.0%}' for c, v in zip(word, cs))
    if m > CHAR_MAX: return False, f'“{word[j]}”被挡 {m:.0%}，超过一半读不出（每个字：{each}）'
    if m < lo: return False, f'被挡最多的“{word[j]}”也只挡 {m:.0%}，不到 {lo:.0%}，不像人挡字（每个字：{each}）'
    return True, f'被挡最多的“{word[j]}” {m:.0%}（每个字：{each}）'


def place(st, word, small, A, W, H, top=None, width=None, floor=None):
    """找大字的位置和字号：每个字被挡不超过一半、最挡的字至少 cover_min，字号不小于 MIN_SIZE，大字底不压字幕。
    先试默认放法（头顶，大字下部压进头里 overlap），不行按这套风格的 fallback 依次试；都不行就停下说明，不硬放。
    top / width：花字.json 单条写的 "字顶"（大字字墨顶 y，4K）/ "字宽"（占画宽比例），写了就按它放，不再自己找。floor：上移最高到哪（4K，导航条下）。
    返回 (lay, 放法说明, 估计最挡那个字的被挡比例, 试过的记录)。"""
    k = W / REF_W; lo = st['cover_min']; top_min = st['top_min'] * k; tried = []
    st = dict(st, width=width) if width else st
    hi_top = min(top_min, ((floor if floor is not None else 340 + NAV_GAP) + (SMALL['above'] if small else 0)) * k)   # 有上方小字时小字也要在导航条下

    def test(lay, x0, t, how, shrink=False):
        big = lay['big']
        if shrink and big['size'] < MIN_SIZE * k:
            tried.append(f'{how}：字号只有 {big["size"] / k:.0f}（4K），小于 {MIN_SIZE}，太小看不清'); return None
        if t + big['h'] > Y_BOTTOM * k:
            tried.append(f'{how}：大字底到 y={(t + big["h"]) / k:.0f}，压到字幕'); return None
        set_pos(lay, W, x0, t); cs = char_cover(lay, A['frames'], W, H); ok, why = judge(word, cs, lo)
        if not ok: tried.append(f'{how}（字顶 y={t / k:.0f}）：{why}'); return None
        return lay, how, max(cs), tried

    def tops(lay):
        """这个字号下依次试的字顶：头顶（默认位置放得下时）或头后（这套最高的 top_min）→ 一步步往上挪，最高到导航条下。"""
        h = lay['big']['h']; pref = A['head_top'] - (1 - st['overlap']) * h
        out = [(pref, '头顶') if pref >= top_min else (top_min, '头后')]; t = min(pref, top_min) - .08 * h
        while t > hi_top: out.append((t, '上移')); t -= .08 * h
        return out + [(hi_top, '上移到导航条下')]

    def first(lay, x0, cands, name, shrink=False):
        for t, kind in cands:
            r = test(lay, x0, t, f'{name}（{kind}）' if name else kind, shrink)
            if r: return r

    if top is not None or width:                               # 单条手写了位置/字宽：只按写的放，不行就停下说清楚
        lay = layout(st, word, small, W); x0 = (W - lay['big']['w']) / 2
        r = first(lay, x0, [(top * k, f'字顶 {top}')] if top is not None else tops(lay), f'手写（字宽 {st["width"]:.0%}）' if width else '手写')
        if r: return r
        stop(f'人挡字“{word}”按花字.json 写的' + ('“字顶”' if top is not None else '') + ('“字宽”' if width else '') + '放不下：' + '；'.join(tried) +
             '。字顶写小一点（字往上）、字宽写大一点（字更大，人挡住的那几个字挡得少），或删掉这两项让程序自己找')
    for cand in [{'放法': '默认'}] + st['fallback']:
        how = cand['放法']; scale = cand.get('比例', 1.0)
        lay = layout(st, word, small, W, scale); big = lay['big']; x0 = (W - big['w']) / 2
        pref = A['head_top'] - (1 - st['overlap']) * big['h']
        if how == '头侧':
            gl, gr = A['head_l'] - SIDE_MARGIN * k, W - SIDE_MARGIN * k - A['head_r']; side = 'right' if gr >= gl else 'left'; gap = max(gl, gr)
            tuck = .15; need = gap / (1 - tuck); s = min(1.0, need / (W * st['width']))
            if s < .45:
                tried.append(f'头侧：头两边最宽只空 {gap / k:.0f} 像素（4K），字要缩到原来的 {s:.0%}，太小'); continue
            lay = layout(st, word, small, W, s); big = lay['big']
            x0 = A['head_r'] - tuck * big['w'] if side == 'right' else A['head_l'] - (1 - tuck) * big['w']
            r = test(lay, x0, max(top_min, A['head_top']), f"头侧（{'右' if side == 'right' else '左'}，字 {s:.0%}）", True)
        elif how == '默认':
            if pref < top_min:
                tried.append(f'默认（头顶）：放不下，头顶在 y={A["head_top"] / k:.0f}，大字顶要到 y={pref / k:.0f}，这套最高只放到 {st["top_min"]}'); continue
            r = test(lay, x0, pref, '默认（头顶）')
        elif how == '头后':
            if pref >= top_min: continue                          # 默认位置放得下时，头后就是往下挪，只会挡得更多
            r = test(lay, x0, top_min, '头后（下移到头后）')
        elif how == '上移':
            r = first(lay, x0, tops(lay)[1:], '')                 # 第一个（头顶/头后）前面试过了
        else:
            r = first(lay, x0, tops(lay), f'{how} {scale:.0%}', how == '缩字')
        if r: return r
    stop(f'人挡字“{word}”（{st["name"]}）放不下：' + '；'.join(tried) +
         '。办法：在花字.json 这一条写 "字顶"（大字顶的 y，4K，越小越高）/ "字宽"（占画宽比例，越大人挡住的字挡得越少）自己定；'
         '或换个头顶空间多一点、人更靠下的时刻；换短一点的词；或换一套风格（job.json 的 "人挡字风格"，单条写 "风格"）。不硬放、不缩成看不清的小字')


# ───────── 自动选风格 ─────────
def features(gray, low, A, W, H, word):
    """gray：这一段口播画面的低清灰度（n×h×w，和遮罩同尺寸）。量大字那一带去掉人以后的背景亮度、杂乱度。"""
    n, h, w = low.shape; ky = H / h
    y0 = int(380 / REF_W * W / ky); y1 = max(y0 + 2, min(h, int((A['head_top'] + .12 * H) / ky)))
    g = gray[:, y0:y1].astype(np.float32); bg = low[:, y0:y1] < 77
    gx = np.abs(np.diff(g, axis=2))[:, :-1, :]; gy = np.abs(np.diff(g, axis=1))[:, :, :-1]; m2 = bg[:, :-1, :-1]
    bright = float(g[bg].mean()) if bg.any() else 0.0
    busy = float((gx + gy)[m2].mean()) * (w / 720) if m2.any() else 0.0     # 梯度按 720 宽归一，换分辨率阈值不变
    return dict(亮度=round(bright, 1), 杂乱=round(busy, 2), 头顶=round(A['head_top'] * REF_W / W), 字数=len(word))


def auto_pick(f):
    for name, cond, k, why in AUTO_RULES:
        if cond(f): return k, name, why


# ───────── 抠人（可替换：测试里换成假遮罩）─────────
class Matte:
    """low：低清遮罩 n×h×w（0–255，分析放法用）；frames()：逐帧全尺寸遮罩（H×W uint8）。"""
    def __init__(self, low, frames, info=None): self.low, self.frames, self.info = low, frames, info or {}


def rvm(src, start, n, W, H, work_dir):
    sys.path.insert(0, MATTING_REPO)
    from engine.fx import matte as M                       # 口播剪辑现成的 RVM，缓存在 work_dir/抠像/（同一段第二次直接用）
    r = M.generate(str(Path(src).resolve()), start, start + n, W, H, str(Path(work_dir).resolve()))   # 绝对路径：抠像子进程在 core 目录里跑
    with np.load(r['masks']) as d: low = np.asarray(d['alpha'])

    def frames():
        p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', r['path'], '-vf', 'alphaextract,format=gray', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'],
                             stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        yield from _read(p, W * H, (H, W))
    return Matte(low, frames, dict(cache_hit=r.get('cache_hit'), path=r['path']))


MATTE = rvm


def _read(p, size, shape, ch=None):
    while True:
        b = p.stdout.read(size)
        if len(b) < size: break
        yield np.frombuffer(b, np.uint8).reshape(shape if ch is None else shape + (ch,))
    p.stdout.close(); p.wait()


def decode(src, start, n, W, H, fmt='rgb24', size=None):
    """从 A肉 取第 start 帧起的 n 帧（-ss 落在两帧中间，保证第一帧就是 start，不早不晚）。"""
    w, h = size or (W, H); ch = 3 if fmt == 'rgb24' else 1
    vf = ['-vf', f'scale={w}:{h}:flags=area'] if size else []
    # passthrough：不按固定帧率补帧（实测 -ss 落在半帧处时，默认会把第一帧重复一次，整段晚一帧）
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, (start - .5) / FPS):.6f}', '-i', str(src), '-frames:v', str(n)] + vf +
                         ['-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', fmt, '-'], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    return _read(p, w * h * ch, (h, w), ch if ch > 1 else None)


# ───────── 调色 ─────────
def film(W, H):
    """胶片感：黑位抬一点、略暖、暗角、颗粒（颗粒随机数固定种子，同样输入出同样画面）。"""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    vig = np.clip(1 - .22 * np.clip(r - .5, 0, None) ** 1.5 * 2, 0, 1)[..., None]; rng = np.random.default_rng(1)
    gs = max(1, round(2 * W / REF_W))

    def grade(a):
        a = 12 + a * (238 / 255); a[..., 0] *= 1.03; a[..., 2] *= .96; a *= vig
        g = rng.normal(0, 5, (-(-H // gs), -(-W // gs), 1)).astype(np.float32).repeat(gs, 0).repeat(gs, 1)[:H, :W]
        return a + g
    return grade


# ───────── 出一段 ─────────
def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate,nb_frames', '-of', 'json', str(path)],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    return (json.loads(out or '{}').get('streams') or [{}])[0]


def render(spec, out, aroll, style, work_dir, auto=None):
    """spec 要带 _start、_end（成片帧号）、text、可选 小字。aroll＝合成/A肉.mov。返回记录（帧数、遮挡、放法、耗时）。
    auto：没定风格时传一个回调 auto(特征) → 风格编号（第一处用画面特征自动选）。"""
    t0 = time.perf_counter(); s = probe(aroll); W, H = int(s['width']), int(s['height'])
    start, end = int(spec['_start']), int(spec['_end']); n = end - start; word = spec['text']; small = spec.get('小字')
    M = spec.get('_M') or MATTE(aroll, start, n, W, H, work_dir); low = np.asarray(M.low)     # _M：出片前预查时已抠好的（precheck）
    if len(low) != n: stop(f'人挡字遮罩 {len(low)} 帧，这一段 {n} 帧，对不上')
    A = analyse(low, W, H); feat = None
    if style is None:
        gray = np.stack(list(decode(aroll, start, n, W, H, 'gray', (low.shape[2], low.shape[1]))))
        feat = features(gray, low, A, W, H, word); style = auto(feat)
    st = STYLES[style]; check_fonts([style], bool(small))
    lay, how, est_c, tried = place(st, word, small, A, W, H, spec.get('字顶'), spec.get('字宽'), spec.get('_floor'))
    st = dict(st, width=spec['字宽']) if spec.get('字宽') else st; est = est_cover(lay, A, W, H)
    dur = n / FPS; ent = entrance(st, word); grade = film(W, H) if st.get('grade') == 'film' else None
    tmp = Path(out).with_name(Path(out).stem + '.出片中.mov')
    enc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', FPS_S, '-i', '-',
                            '-c:v', 'prores_ks', '-profile:v', '2', '-pix_fmt', 'yuv422p10le', '-vendor', 'apl0',
                            '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                            '-fps_mode', 'passthrough', str(tmp)],      # 一帧进一帧出，不补帧不丢帧
                           stdin=subprocess.PIPE)
    ratios, per, got = [], [[] for _ in word], 0
    try:
        for i, (b, m) in enumerate(zip(decode(aroll, start, n, W, H), M.frames())):
            got = i + 1
            t = i / FPS; gl = []; txt, ink, on = text_layer(st, lay, t, dur, W, H, gl)
            if st['motion'] == 'cut': on = 1.0
            base = b
            if st.get('bg_blur') and on > 0: base = np.asarray(Image.fromarray(b).filter(ImageFilter.GaussianBlur(st['bg_blur'] * lay['k'] * on)))
            f = base.astype(np.float32)
            if st.get('bg_dark') and on > 0: f *= 1 - st['bg_dark'] * on
            box = txt.getbbox()
            if box:
                x0, y0, x1, y1 = box; ta = np.asarray(txt.crop(box), np.float32); al = ta[..., 3:] / 255
                f[y0:y1, x0:x1] = f[y0:y1, x0:x1] * (1 - al) + ta[..., :3] * al
            a = m.astype(np.float32)[..., None] / 255; f = f * (1 - a) + b.astype(np.float32) * a    # 人在最上面（用原画面，不被压暗/虚化）
            if grade: f = grade(f)
            enc.stdin.write(np.clip(f + .5, 0, 255).astype(np.uint8).tobytes())
            if ent - 1e-6 <= t < dur - st['out'] and box:            # 去掉出场段、出场淡出段
                r = coverage(np.asarray(ink, np.float32) / 255, a[..., 0])
                if np.asarray(ink).any(): ratios.append(r)
                for j, x, y, g in gl: per[j].append(cover_at(g, a[..., 0], x, y))
    finally:
        enc.stdin.close(); enc.wait()
    if enc.returncode or got != n:
        tmp.unlink(missing_ok=True); stop(f'人挡字出片失败：读到 {got} 帧，应为 {n} 帧（A肉 是否够长、抠像是否完整）')
    back = probe(tmp)
    if (int(back['width']), int(back['height']), int(back.get('nb_frames') or 0)) != (W, H, n):
        tmp.unlink(missing_ok=True); stop(f'人挡字片段读回不符：{back}')
    tmp.replace(out)
    lo = st['cover_min']; avg = sum(ratios) / len(ratios) if ratios else 0.0
    cs = [float(np.percentile(v, CHAR_Q)) if v else 0.0 for v in per]; ok, why = judge(word, cs, lo); ok = ok and bool(ratios)
    worst = max(cs) if cs else 0.0
    advice = '' if ok else ('字几乎没被人挡住，不像人挡字：在花字.json 这一条把 "字顶" 写大一点（字往下），换一个人头更高的时刻，或换一套字更大的风格' if worst < lo else
                            '有字被挡过一半、读不出：在花字.json 这一条写 "字顶"（写小，字往上）或 "字宽"（写大，字更大、挡住的那几个字挡得少），或换一个人头更低、头顶空间更多的时刻')
    return dict(风格=style, 套名=st['name'], 字=word, 小字=small, 帧数=n, 尺寸=[W, H], 放法=how, 退路记录=tried, 底片=str(aroll),
                遮挡=dict(平均=round(avg, 3), 最大=round(max(ratios), 3) if ratios else 0.0, 估计=round(est, 3), 每字=[[c, round(v, 3)] for c, v in zip(word, cs)],
                         最挡=round(worst, 3), 每字估计=round(est_c, 3), 范围=[lo, CHAR_MAX], 通过=ok, 说明=why, 建议=advice),
                入场帧=round(ent * FPS), 头顶=round(A['head_top'] * REF_W / W), 特征=feat, 抠像缓存=M.info.get('cache_hit'), 耗时秒=round(time.perf_counter() - t0, 1))


# ───────── 片子里：huazi.render_list 调这里 ─────────
def aroll_of(film_dir, job_raw):
    """人挡字的底片：和审核线同取景。job.json 写了 "人像下移"（审核线 V1 整体下移多少，4K 像素）就用 aroll-render 同时出的
    合成/A肉_人像下移<值>.mov（名字和 thinkcut/aroll.py 的 shifted_name 一致），没写用 合成/A肉.mov。"""
    v = job_raw.get('人像下移') or 0
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v): stop(f'job.json 的 "人像下移" 要写整数像素（4K），现在是 {v!r}')
    return Path(film_dir) / '合成' / (f'A肉_人像下移{int(v)}.mov' if v else 'A肉.mov')


BATCH = 'A肉_批次.json'          # aroll-render 每遍写的批次标记（thinkcut/aroll.py 的 BATCH，同名）


def same_batch(film_dir, *files):
    """2026-11-21 261118 新8：这几个文件是不是 aroll-render 同一遍出的（批次标记里记的大小、修改时间和现在一样）。
    同一遍出的 A肉 和 A肉_人像下移<值> 写完有先后（实测差 41 秒），不能按修改时间判谁旧。没有标记的老产物返回 False（照旧按修改时间比）。"""
    try: rec = json.loads((Path(film_dir) / '合成' / BATCH).read_text(encoding='utf-8')).get('文件') or {}
    except (OSError, ValueError): return False
    for p in files:
        try: s = Path(p).stat()
        except OSError: return False
        if rec.get(Path(p).name) != [s.st_size, int(s.st_mtime)]: return False
    return True


def plan(film_dir, items, job_raw):
    """出片前把规矩全查一遍（不合格就停，不出任何片段）：A肉、帧率、画幅、露脸段、处数、间隔、和别的花字重叠、字体。
    items 是 huazi.resolve 过的全部花字（带 _at/_until）。给人挡字条目补 _start/_end/_style（None＝要自动选）。"""
    from huazi import RHYTHM
    d = Path(film_dir); rd = [it for it in items if it['use'] == '人挡字']
    if not rd: return []
    if str(job_raw.get('fps', FPS_S)) != FPS_S: stop(f'人挡字只支持 29.97 帧（{FPS_S}），这条片子是 {job_raw.get("fps")}')
    aroll = aroll_of(d, job_raw)
    if not aroll.exists():
        stop(f'没有 合成/{aroll.name}：人挡字从口播画面出片' + ('（job.json 写了 "人像下移"，要用和审核线同取景的那一份）' if aroll.name != 'A肉.mov' else '') +
             '，先跑 thinkcut_edit.py aroll-render <片子目录>')
    A0 = d / '合成' / 'A肉.mov'
    if aroll != A0 and A0.exists() and A0.stat().st_mtime > aroll.stat().st_mtime + 1 and not same_batch(d, A0, aroll):
        stop(f'合成/{aroll.name} 比 A肉.mov 旧（口播画面重出过），先重跑 thinkcut_edit.py aroll-render <片子目录>')
    s = probe(aroll); W, H = int(s.get('width', 0)), int(s.get('height', 0))
    if W * 4 != H * 3: stop(f'合成/A肉.mov 是 {W}×{H}，不是 3:4：人挡字现在只支持 3:4')
    if s.get('r_frame_rate') != FPS_S: stop(f'合成/A肉.mov 帧率 {s.get("r_frame_rate")}，应为 {FPS_S}')
    total = int(s.get('nb_frames') or 0)
    sb = d / '分镜.json'
    if not sb.exists(): stop('片子目录里没有 分镜.json：人挡字只能放在露脸段（分镜里的 F 镜头），没有分镜没法确认。先跑 thinkcut_edit.py beats 出分镜，再出人挡字')
    shots = json.loads(sb.read_text(encoding='utf-8'))['shots']
    js = job_raw.get('人挡字风格'); base = style_of(js) if js is not None else None
    for it in rd:
        if not str(it.get('text', '')).strip(): stop(f'人挡字（{it["at"]}）没写 text（要显示的大字）')
        if '时长' in it and 'until' not in it: it['_until'] = it['_at'] + float(it['时长'])
        it['_start'], it['_end'] = round(it['_at'] * FPS), round(it['_until'] * FPS)
        it['_style'] = style_of(it['风格']) if it.get('风格') is not None else base
        n = it['_end'] - it['_start']; st = STYLES[it['_style'] or 1]; need = entrance(st, it['text']) + st['out'] + 1.0
        where = f'人挡字“{it["text"]}”（{it["_at"]:.2f}–{it["_until"]:.2f} 秒）'
        it['_floor'] = int(job_raw.get('nav_h') or 340) + NAV_GAP
        for key, lo_, hi_ in (('字顶', it['_floor'], Y_BOTTOM), ('字宽', .2, 1.3)):
            v = it.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not lo_ <= v <= hi_):
                stop(f'{where}的 "{key}" 写成了 {v!r}：' + ('大字字墨顶的 y（4K 像素），在导航条下 ' if key == '字顶' else '大字占画宽的比例，') + f'{lo_:g}–{hi_:g} 之间')
        if n < round(need * FPS): stop(f'{where}太短：至少 {need:.1f} 秒（出场＋停留 1 秒＋淡出）')
        if total and it['_end'] > total: stop(f'{where}超出 A肉（{total} 帧）')
        a, b = it['_start'], it['_end']
        hit = [x for x in shots if round(x['a'] * FPS) < b and a < round(x['b'] * FPS)]
        bad = [x for x in hit if x.get('kind') != 'F']
        if bad: stop(f'{where}不是露脸段：压到了 {"、".join(str(x["id"]) for x in bad)}（B-roll/动画镜头）。人挡字只能放在 F 镜头（上面没有 B-roll/动画）')
        cov = sorted((max(a, round(x['a'] * FPS)), min(b, round(x['b'] * FPS))) for x in hit); t = a
        for x, y in cov:
            if x > t: break
            t = max(t, y)
        if t < b: stop(f'{where}有一部分不在分镜的任何镜头里（分镜到 {max([x["b"] for x in shots] or [0]):.2f} 秒）')
    lim, gap = int(job_raw.get('人挡字上限', LIMIT['count'])), float(job_raw.get('人挡字间隔秒', LIMIT['gap']))
    rs = sorted(rd, key=lambda x: x['_start']); groups = [[rs[0]]]
    for x, y in zip(rs, rs[1:]):
        if y['_start'] < x['_end']: stop(f'人挡字“{x["text"]}”和“{y["text"]}”时间重叠（花字轨上重叠的后一条放不上去）：前一条的 until 写到后一条的 at')
        if y['_start'] - x['_end'] <= JOIN: groups[-1].append(y); continue      # 紧挨＝连着换字（《别墅装灯》“设计经验”接“现场调试”），算一处
        g = (y['_start'] - x['_end']) / FPS
        if g < gap: stop(f'人挡字“{x["text"]}”和“{y["text"]}”只隔 {g:.1f} 秒，至少 {gap:g} 秒。想连着换字就让两条紧挨（前一条的 until 写成后一条的 at，'
                         f'程序算一处、不查间隔）；确实要隔这么近，job.json 写 "人挡字间隔秒"')
        groups.append([y])
    if len(groups) > lim: stop(f'人挡字 {len(groups)} 处，超过一条片子最多 {lim} 处（紧挨着连续换字的算一处；只给特别重点用；确实要多，job.json 写 "人挡字上限"）')
    for it in rd:          # 花字轨只有一条：人挡字和别的花字不能重叠（重叠的放不上去）
        for o in items:
            if o['use'] == '人挡字': continue
            oa = round(o['_at'] * FPS); ob = oa + round(max(RHYTHM['hold_min'] + RHYTHM['inn'] + RHYTHM['out'], o['_until'] - o['_at']) * FPS)
            if oa < it['_end'] and it['_start'] < ob:
                stop(f'人挡字“{it["text"]}”和花字 {o["use"]}（{o["_at"]:.2f} 秒）同时出现：人挡字是整幅画面，同一时刻不再放别的花字')
    styles = {it['_style'] for it in rd}
    check_fonts(sorted(STYLES) if None in styles else sorted(styles), any(it.get('小字') for it in rd))
    return rd


def auto_choose(film_dir, job_raw):
    """返回 auto(特征) 回调：第一处人挡字按画面选一套，写回 job.json（来源“自动”），之后同一条片子沿用。"""
    jf = Path(film_dir) / 'job.json'; box = {}

    def auto(f):
        if 'k' in box: return box['k']
        k, rule, why = auto_pick(f); box['k'] = k
        cfg = json.loads(jf.read_text(encoding='utf-8')) if jf.exists() else {}
        cfg.update({'人挡字风格': k, '人挡字风格来源': '自动', '人挡字风格依据': dict(f, 规则=rule)})
        tmp = jf.with_name('job.json.tmp'); tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8'); os.replace(tmp, jf)
        job_raw.update(cfg)
        print(f'人挡字风格没写，按画面自动选了第 {k} 套「{STYLES[k]["name"]}」：{rule}——{why}'
              f'（背景亮度 {f["亮度"]}、杂乱 {f["杂乱"]}、头顶 y={f["头顶"]}、{f["字数"]} 个字）。已写进 job.json，整条片子都用这一套。'
              f'觉得不合适：把 job.json 的 "人挡字风格" 改成 1–4 或套名（{"、".join(v["name"] for v in STYLES.values())}）再重出；单条可写 "风格" 覆盖', flush=True)
        return k
    return auto


def _sig(it, job_raw, aroll):
    """(old, full)：沿用判断用的签名。"""
    style = it['_style']
    if style is None and job_raw.get('人挡字风格') is not None: style = style_of(job_raw['人挡字风格'])
    old = dict(start=it['_start'], end=it['_end'], text=it['text'], small=it.get('小字'), style=style)
    def full(s):        # 2026-11-21：签名含这套风格的参数、判定规则、单条的字顶/字宽、底片，改了任何一样都重出
        return json.loads(json.dumps(dict(s, 参数={k: v for k, v in STYLES[s['style']].items() if k not in ('特点', '适合')} if s['style'] else None,
                                          规则=dict(CHAR_MAX=CHAR_MAX, CHAR_Q=CHAR_Q, NAV_GAP=NAV_GAP, MIN_SIZE=MIN_SIZE, Y_BOTTOM=Y_BOTTOM),
                                          字顶=it.get('字顶'), 字宽=it.get('字宽'), floor=it.get('_floor'), 底片=aroll.name), ensure_ascii=False))
    return old, full


def reusable(it, out, film_dir, job_raw):
    """上次出的片段能不能沿用（片段和记录都在、签名没变）：能就返回记录，不能返回 None。"""
    out = Path(out); side = out.with_suffix('.json'); aroll = aroll_of(Path(film_dir), job_raw); old, full = _sig(it, job_raw, aroll)
    # 2026-11-22 试验田查出：底片（口播画面）比片段新＝口播画面重出过（剪口播可能改了），片段里的背景可能已经过时，不沿用。
    # 以前只比名字和参数，审核线和交审自检（thinkcut/check.py，按修改时间比）却说要重出，照提示重跑 huazi 又沿用，绕不出去。两处现在同一条规则。
    if out.exists() and aroll.exists() and aroll.stat().st_mtime > out.stat().st_mtime + 1: return None
    if out.exists() and side.exists():
        rec = json.loads(side.read_text(encoding='utf-8')); s0 = rec.get('签名') or {}
        if ('参数' in s0 and s0 == full(old)) or ('参数' not in s0 and s0 == old and aroll.name == 'A肉.mov' and not any(k in it for k in ('字顶', '字宽'))):
            return rec                                             # 没有“参数”的是 11-21 以前出的：老片子原样沿用，不因为升级重出
    return None


REACH = 1200                                       # 预查放不下时，人像最多再往下挪多少（4K）来估“要多少头顶空间”


def need_head(st, word, small, low, W, H, top, width, floor):
    """人像整体往下挪多少（4K，按 40 一档试）这条才放得下：返回放得下时的头顶 y（4K），挪 REACH 也不行返回 None。"""
    n, h, w = low.shape
    for dy in range(40, REACH + 1, 40):
        r = round(dy * h / H)
        if r >= h: break
        sh = np.zeros_like(low); sh[:, r:] = low[:, :h - r]
        try:
            A = analyse(sh, W, H); place(st, word, small, A, W, H, top, width, floor)
            return round(A['head_top'] * REF_W / W)
        except SystemExit: continue
    return None


def precheck(film_dir, todo, job_raw):
    """2026-11-21 Q166 复发（261118“补不回来”）：出任何片段之前，按 A肉 人像框（抠像遮罩量的头顶，和出片同一套 analyse/place）
    把这次要出的每条人挡字都先找一遍位置；放不下的全部列出（第几条、时间、字、头顶离上沿多少、要多少）并停下，建议改人前口播大字 koubo。
    以前要出到那一条才发现放不下（前面几条已经出了、抠像也跑了），而且只报第一条。todo：[(第几条, 条目)]，只放这次要重出的。
    抠好的遮罩放进条目的 _M，出片时直接用，不再抠一遍。"""
    if not todo: return
    d = Path(film_dir); aroll = aroll_of(d, job_raw); s = probe(aroll); W, H = int(s['width']), int(s['height'])
    js = job_raw.get('人挡字风格'); picked = style_of(js) if js is not None else None; bad = []
    for n, it in todo:
        start, end = it['_start'], it['_end']; word = it['text']; small = it.get('小字')
        M = MATTE(aroll, start, end - start, W, H, d / '合成' / '花字' / '人挡字抠像'); it['_M'] = M
        low = np.asarray(M.low); A = analyse(low, W, H); style = it['_style'] or picked
        if style is None:              # 没写风格：和出片时一样按第一处的画面选（这里只算不写 job.json，出片时 auto_choose 写）
            gray = np.stack(list(decode(aroll, start, end - start, W, H, 'gray', (low.shape[2], low.shape[1]))))
            style = picked = auto_pick(features(gray, low, A, W, H, word))[0]
        st = STYLES[style]; args = (it.get('字顶'), it.get('字宽'), it.get('_floor'))
        try: place(st, word, small, A, W, H, *args); continue
        except SystemExit as e: why = str(e).replace('停止：', '', 1)
        head = round(A['head_top'] * REF_W / W); need = need_head(st, word, small, low, W, H, *args)
        bad.append(f'第 {n} 条“{word}”（{it["_at"]:.2f}–{it["_until"]:.2f} 秒，{st["name"]}）：头顶离画面上沿 {head} 像素，'
                   + (f'要约 {need} 像素才放得下（人像大约还要往下挪 {need - head}）' if need else f'人像再往下挪 {REACH} 像素也放不下')
                   + '\n    试过的放法：' + why)
    if bad:
        stop(f'有 {len(bad)} 条人挡字放不下（出片前按 {aroll.name} 的人像量的头顶空间，4K 像素；还没出任何片段）：\n  ' + '\n  '.join(bad) +
             '\n建议：这几条改成人前的口播大字（花字.json 里这一条改成 {"use": "koubo", "at": …, "until": …, "lines": ["…"]}）。'
             '一定要人挡字：换一个人头更低、头顶空间更多的时刻，或换短一点的词')


def item(it, out, film_dir, job_raw, auto, only_this):
    """出一处（或沿用上次出的：片段和记录都在、帧位置/字/风格没变时）。返回 (帧数, 记录)。"""
    out = Path(out); side = out.with_suffix('.json'); d = Path(film_dir); aroll = aroll_of(d, job_raw)
    old, full = _sig(it, job_raw, aroll); style = old['style']
    if not only_this:
        rec = reusable(it, out, d, job_raw)
        if rec: return rec['帧数'], rec
    rec = render(dict(it, text=it['text']), out, aroll, style, d / '合成' / '花字' / '人挡字抠像', auto)
    rec['签名'] = full(dict(old, style=rec['风格']))
    side.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding='utf-8')
    return rec['帧数'], rec
