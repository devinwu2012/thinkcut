# 包装系统·渲染器（花字＝文字类，卡片＝信息类）。2026-11-12 用户定为默认，取代 11-01 的“蓝橙对比、主题橙、冬青黑体”。
# 来源：《261110 水电阶段没做好这4样》的花字组件库（学 andyhuo520/digital-human-koubo（MIT）的组件分类和节奏，代码自己重写），
#   并入根目录旧花字库的口播大字、评论弹窗（改成苹方和统一节奏），加上 A20 定稿的“及格线”“网速对比”两个卡片。
# 全库统一：一种字体（苹方 SC）、蓝白两色（正文白、唯一强调色 #64A9E8）、一套动画节奏（见 RHYTHM：进 0.40 秒、出 0.30 秒）。
# 每个花字实例出一段带透明通道的 4K 片段（ProRes 4444，2160×2880，29.97），放在达芬奇单独一条“花字”轨。
# 用法：python3 huazi.py <片子目录>       读 <片子目录>/花字.json → 合成/花字/NN_组件.mov ＋ 合成/花字/花字表.json（说明见 README.md）
#       "use": "人挡字" 的条目交给 rendang.py 出不透明整幅片段（人挡字风格库）
#       库：from huazi import render_item, still
import glob, math, os, re, json, sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rendang                      # 人挡字风格库（2026-11-13）：整幅片段，见 rendang.py
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 2160, 2880
FPS_S, FPS = '30000/1001', 30000 / 1001
FONT_FILE = '/System/Library/AssetsV2/com_apple_MobileAsset_Font8/86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc'
WEIGHT = {'regular': 3, 'medium': 7, 'semibold': 11, 'light': 15}           # 只用苹方 SC 这四个字重
# 2026-11-21 体检：FONT_FILE 是带哈希的系统路径，系统更新后可能换地方。它在就照旧用；不在就到下面这些地方按文件名找，并核对字体名确实是苹方（见 find_pingfang）
PINGFANG_GLOBS = ('/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc', '/System/Library/Fonts/PingFang.ttc',
                  '/System/Library/Fonts/Supplemental/PingFang.ttc', '/Library/Fonts/PingFang.ttc', '~/Library/Fonts/PingFang.ttc')
STYLE = {'regular': 'Regular', 'medium': 'Medium', 'semibold': 'Semibold', 'light': 'Light'}   # WEIGHT 里每个序号应该是 PingFang SC 的哪个字重
WHITE, SUB, DIM = (255, 255, 255), (255, 255, 255, 218), (255, 255, 255, 120)  # 正文白 / 次要 85% / 弱化 47%（浅色墙前也要读得清）
CONTRA = (255, 92, 92, 255)   # 口播大字〖〗“反面”词红 #FF5C5C（10-31 用户：材料一个色、工艺一个色；11-12 看样张后定仍用红）
ACCENT = (100, 169, 232)                                                       # #64A9E8，和字幕重点词、导航条同色
CARD = (16, 18, 22, 168)                                                       # 深色半透明卡片（66%）
SAFE = dict(left=120, right=W - 120, top=420, bottom=2400)                     # 导航条下、字幕上
# 统一节奏（秒）：进场 0.40（缓出：透明→不透明、上移 28px、模糊 10→0），多项依次间隔 0.12，出场 0.30（缓入：变透明、模糊 0→8，不移动），
# 数字滚动 0.80，线条生长 0.50；最短停留 1.5；没写 until 时停 3.0。没有弹跳、回弹、抖动、闪白。
RHYTHM = dict(inn=0.40, rise=28, blur=10, stagger=0.12, out=0.30, out_blur=8, count=0.80, draw=0.50, hold_min=1.5, hold=3.0)
SIZE = dict(number=230, unit=96, label=70, key=104, body=76, small=64, statement=124)
# 三种画幅的版式（2026-11-14 用户看三画幅动效样片后定，台账 Q068）。上面的 W/H/SAFE/SIZE 是 3:4 的值，别的画幅由 set_format 换掉。
#   safe 文字类安全区（左、右、上、下）；k 字号倍率；koubo 口播大字默认中心高度（它顶替字幕，不用给字幕让位）；
#   topc 默认“顶部居中”的卡片（步骤、对比）放哪：横屏人在中间，放左上；
#   card_k / card_w 整屏卡片（及格线、网速对比）整体缩放和内容最宽（横屏不拉满通栏）。
#   9:16 最上面（y<780，手机状态栏）和最下面（y>3100，平台按钮、文案）不放东西。
BASE_SIZE = dict(SIZE)
FORMATS = {
    '3:4':  dict(W=2160, H=2880, safe=(120, 2040, 420, 2400), k=1.0, koubo=2000, topc='TC', card_k=1.0, card_w=None),
    '9:16': dict(W=2160, H=3840, safe=(120, 2040, 1200, 2760), k=1.0, koubo=2860, topc='TC', card_k=0.92, card_w=None),
    '16:9': dict(W=3840, H=2160, safe=(160, 3680, 280, 1800), k=0.92, koubo=1640, topc='TL', card_k=0.9, card_w=2600),
}
FMT = FORMATS['3:4']


def set_format(fmt):
    """按片子的画幅（job.json 的 format：3:4、3:4满屏、9:16、16:9）换掉画面尺寸、安全区和字号。返回用的是哪一套。"""
    global W, H, SAFE, SIZE, FMT
    key = next((k for k in FORMATS if str(fmt).startswith(k)), None)
    if key is None: raise SystemExit(f'停止：包装系统不认识画幅 “{fmt}”（可用：{"、".join(FORMATS)}）')
    FMT = FORMATS[key]; W, H = FMT['W'], FMT['H']; l, r, t, b = FMT['safe']
    SAFE = dict(left=l, right=r, top=t, bottom=b); SIZE = {k: round(v * FMT['k']) for k, v in BASE_SIZE.items()}
    return key


def _face(path, i):
    try: return ImageFont.truetype(path, 20, index=i).getname()
    except Exception: return None


def find_pingfang(fixed=None, globs=None):
    """找苹方字体文件，返回 (文件, {字重: 序号})；找不到返回 None。先用写死的 FONT_FILE，不在再按文件名找。
    每个字重先看 WEIGHT 里写的序号是不是 PingFang SC 的那个字重（核对字体名），不是就在文件里按名字找；四个字重找不齐的文件不用。"""
    fixed = FONT_FILE if fixed is None else fixed; globs = PINGFANG_GLOBS if globs is None else globs
    for path in [str(fixed)] + sorted(p for g in globs for p in glob.glob(os.path.expanduser(g))):
        if not os.path.isfile(path): continue
        idx, faces = {}, None
        for w, st in STYLE.items():
            i = WEIGHT[w]
            if _face(path, i) != ('PingFang SC', st):
                if faces is None:
                    faces = []
                    while len(faces) < 200 and _face(path, len(faces)): faces.append(_face(path, len(faces)))
                i = next((k for k, n in enumerate(faces) if n == ('PingFang SC', st)), None)
            if i is None: break
            idx[w] = i
        else: return path, idx
    return None


_PINGFANG = {}


def pingfang():
    """花字用的苹方：(文件, {字重: 序号})，一个进程只找一次；找不到停下说怎么补。"""
    if FONT_FILE not in _PINGFANG:
        got = find_pingfang()
        if not got: raise SystemExit('停止：找不到花字用的苹方字体（PingFang.ttc，要有 PingFang SC 的常规、中黑、中粗、细体）：打开“字体册”，搜“苹方”，点下载，下载完再跑')
        _PINGFANG[FONT_FILE] = got
    return _PINGFANG[FONT_FILE]


def font(size, weight='semibold'): f, idx = pingfang(); return ImageFont.truetype(f, int(size), index=idx[weight])
def eo(u): u = max(0.0, min(1.0, u)); return 1 - (1 - u) ** 3
def ei(u): u = max(0.0, min(1.0, u)); return u ** 3
def eio(u): u = max(0.0, min(1.0, u)); return 4 * u ** 3 if u < .5 else 1 - (-2 * u + 2) ** 3 / 2


def text_img(s, size, weight='semibold', color=WHITE, shadow=True):
    f = font(size, weight); x0, y0, x1, y1 = f.getbbox(s); pad = 40
    im = Image.new('RGBA', (x1 - x0 + pad * 2, y1 - y0 + pad * 2), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    if shadow:   # 柔和投影，保证亮背景上也读得清；不描边
        sh = Image.new('RGBA', im.size, (0, 0, 0, 0)); ImageDraw.Draw(sh).text((pad - x0, pad - y0 + 6), s, font=f, fill=(0, 0, 0, 170))
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(12))); d = ImageDraw.Draw(im)
    d.text((pad - x0, pad - y0), s, font=f, fill=color if len(color) == 4 else color + (255,))
    return im, pad   # pad：图边到字的距离（排版时扣掉）


def card_img(w, h, r=40):
    im = Image.new('RGBA', (w + 80, h + 80), (0, 0, 0, 0)); sh = Image.new('RGBA', im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((40, 52, 40 + w, 52 + h), r, fill=(0, 0, 0, 90)); im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(24)))
    d = ImageDraw.Draw(im); d.rounded_rectangle((40, 40, 40 + w, 40 + h), r, fill=CARD, outline=(255, 255, 255, 36), width=2)
    return im, 40


class Layer:
    """一个元素：一张图 + 位置 + 自己的进场时刻（相对花字开始）。"""
    def __init__(self, im, x, y, t0=0.0, pad=0): self.im, self.x, self.y, self.t0, self.pad = im, x - pad, y - pad, t0, pad


def compose(layers, t, dur, dyn=()):
    """t：花字内的秒；dur：花字总长。dyn：每帧重画的元素（数字滚动、线条生长），返回 Layer 列表。"""
    R = RHYTHM; canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    out = ei((t - (dur - R['out'])) / R['out'])
    for L in list(layers) + [l for fn in dyn for l in fn(t)]:
        u = eo((t - L.t0) / R['inn'])
        a = u * (1 - out)
        if a <= 0.003: continue
        im = L.im; b = R['blur'] * (1 - u) + R['out_blur'] * out
        if b > 0.3: im = im.filter(ImageFilter.GaussianBlur(b))
        if a < 0.999: im = im.copy(); im.putalpha(im.getchannel('A').point(lambda v: int(v * a)))
        canvas.alpha_composite(im, (int(L.x), int(L.y + R['rise'] * (1 - u))))
    return canvas


# ───────────────────────── 组件 ─────────────────────────
# 每个组件：build(spec) → (layers, dyn)。spec 里位置用 anchor：TR 右上 / TL 左上 / TC 上中 / C 中 / BL 左下 / BR 右下（都在安全区内）。

def _anchor(spec, w, h, default='TR'):
    a = spec.get('anchor') or (FMT['topc'] if default == 'TC' else default); m = SAFE
    x = {'L': m['left'], 'R': m['right'] - w, 'C': (W - w) // 2}[a[1] if len(a) == 2 else 'C']
    y = {'T': m['top'], 'B': m['bottom'] - h, 'C': (H - h) // 2}[a[0]]
    return x + spec.get('dx', 0), y + spec.get('dy', 0)


def number(spec):
    """数字强调（取自 NumberSpike / Counter / Odometer）：大数字＋小单位＋一行说明，数字从 0 滚到终值。"""
    val = str(spec['value']); unit = spec.get('unit', ''); label = spec.get('label', '')
    num = float(val.replace(',', '')) if val.replace(',', '').replace('.', '').isdigit() else None
    dec = len(val.split('.')[1]) if num is not None and '.' in val else 0
    fN = font(SIZE['number']); wN = fN.getlength(val); fU = font(SIZE['unit'], 'medium'); wU = fU.getlength(unit) + (24 if unit else 0)
    bw = max(wN + wU, font(SIZE['label'], 'regular').getlength(label)); bh = SIZE['number'] + (SIZE['label'] + 30 if label else 0)
    x, y = _anchor(spec, int(bw), bh, 'TR'); right = x + bw if spec.get('anchor', 'TR')[-1] == 'R' else None
    layers = []
    if label:
        li, p = text_img(label, SIZE['label'], 'regular', SUB); lx = (right - li.width + 2 * p) if right else x
        layers.append(Layer(li, lx, y, 0, p))
    ny = y + (SIZE['label'] + 30 if label else 0)
    if unit:
        ui, p = text_img(unit, SIZE['unit'], 'medium', ACCENT); ux = (right - ui.width + 2 * p) if right else x + wN + 24
        layers.append(Layer(ui, ux, ny + SIZE['number'] - SIZE['unit'] - 18, RHYTHM['stagger'], p))
    nx_right = (right - wU) if right else x + wN
    cache = {}
    def dyn(t):
        k = eo(t / RHYTHM['count']) if num is not None and spec.get('count', True) else 1
        s = val if k >= 1 else (f'{num * k:,.{dec}f}' if ',' in val else f'{num * k:.{dec}f}')
        if s not in cache: cache[s] = text_img(s, SIZE['number'])
        im, p = cache[s]; return [Layer(im, nx_right - (im.width - 2 * p), ny, 0, p)]
    return layers, [dyn]


def spec_card(spec):
    """参数卡（取自 SpecCard / GlassPanel）：深色半透明卡片，2–4 行 名称｜数值＋单位，逐行出现。"""
    rows = spec['rows']; title = spec.get('title', ''); fk, fv, fu = font(SIZE['small'], 'regular'), font(SIZE['body'] + 20), font(SIZE['small'], 'medium')
    rh = SIZE['body'] + 70; kw = max(fk.getlength(r['k']) for r in rows); vw = max(fv.getlength(str(r['v'])) + fu.getlength(r.get('u', '')) + 16 for r in rows)
    cw = int(max(kw + vw + 120, 760)) + 120; ch = len(rows) * rh + 90 + (SIZE['small'] + 40 if title else 0)
    x, y = _anchor(spec, cw, ch, 'TR'); ci, cp = card_img(cw, ch); layers = [Layer(ci, x, y, 0, cp)]
    yy = y + 60
    if title:
        ti, p = text_img(title, SIZE['small'], 'medium', SUB, False); layers.append(Layer(ti, x + 60, yy, 0, p)); yy += SIZE['small'] + 40
    for i, r in enumerate(rows):
        t0 = RHYTHM['stagger'] * (i + 1); hi = r.get('hi')
        ki, p = text_img(r['k'], SIZE['small'], 'regular', SUB, False); layers.append(Layer(ki, x + 60, yy + 30, t0, p))
        ui, pu = text_img(r.get('u', ''), SIZE['small'], 'medium', ACCENT if hi else SUB, False) if r.get('u') else (None, 0)
        vi, pv = text_img(str(r['v']), SIZE['body'] + 20, 'semibold', ACCENT if hi else WHITE, False)
        right = x + cw - 60; uw = (ui.width - 2 * pu + 14) if ui else 0
        layers.append(Layer(vi, right - uw - (vi.width - 2 * pv), yy, t0, pv))
        if ui: layers.append(Layer(ui, right - (ui.width - 2 * pu), yy + 34, t0, pu))
        if i < len(rows) - 1:
            ln = Image.new('RGBA', (cw - 120, 2), (255, 255, 255, 30)); layers.append(Layer(ln, x + 60, yy + rh - 14, t0))
        yy += rh
    return layers, []


def keywords(spec):
    """关键词（取自 KeywordStack / RetentionCaption）：说到哪个词哪个词出现，竖排，前面一个小圆点；最新的词用强调色，之前的回到白色。"""
    items = spec['items']; f = font(SIZE['key']); lh = SIZE['key'] + 46
    w = int(max(f.getlength(i['t']) for i in items)) + 70; h = lh * len(items)
    x, y = _anchor(spec, w, h, 'TR'); start = spec['_at']
    imgs = [(text_img(i['t'], SIZE['key'], 'semibold', WHITE), text_img(i['t'], SIZE['key'], 'semibold', ACCENT)) for i in items]
    dot = Image.new('RGBA', (22, 22), (0, 0, 0, 0)); ImageDraw.Draw(dot).ellipse((0, 0, 21, 21), fill=ACCENT + (255,))
    rel = [max(0.0, (i.get('_at', start) - start)) for i in items]
    def dyn(t):
        out, cur = [], max([k for k, r in enumerate(rel) if t >= r] or [0])
        for k, ((wi, p), (ai, _)) in enumerate(imgs):
            im = ai if k == cur else wi
            out.append(Layer(dot, x, y + k * lh + SIZE['key'] * .5 - 6, rel[k])); out.append(Layer(im, x + 54, y + k * lh, rel[k], p))
        return out
    return [], [dyn]


def compare(spec):
    """对比（取自 vs / BarCompare / CompareWipe）：左边“不推荐”弱化＋划线，右边“推荐”白字＋强调色下划线；两栏在一张卡里。"""
    L, R_ = spec['left'], spec['right']; fT, fS = font(SIZE['body'] + 10), font(SIZE['small'] - 6, 'regular')
    colw = int(max(fT.getlength(L['t']), fT.getlength(R_['t']), fS.getlength(L.get('sub', '')), fS.getlength(R_.get('sub', '')))) + 40
    cw, ch = colw * 2 + 200, SIZE['body'] + 210
    x, y = _anchor(spec, cw, ch, 'TC'); ci, cp = card_img(cw, ch); layers = [Layer(ci, x, y, 0, cp)]
    for k, (side, col, t0) in enumerate(((L, DIM, 0.0), (R_, WHITE + (255,), RHYTHM['stagger'] * 2))):
        cx = x + 70 + k * (colw + 60)
        ti, p = text_img(side['t'], SIZE['body'] + 10, 'semibold', col, False); layers.append(Layer(ti, cx, y + 56, t0, p))
        if side.get('sub'):
            si, ps = text_img(side['sub'], SIZE['small'] - 6, 'regular', SUB, False); layers.append(Layer(si, cx, y + 56 + SIZE['body'] + 34, t0, ps))
    div = Image.new('RGBA', (2, ch - 80), (255, 255, 255, 40)); layers.append(Layer(div, x + colw + 100, y + 40, 0))
    tw = fT.getlength(L['t']); rw = fT.getlength(R_['t']); lx = x + 70; rx = x + 70 + colw + 60; ly = y + 56 + (SIZE['body'] + 10) * .55; uy = y + 56 + SIZE['body'] + 22
    def dyn(t):
        a = eio((t - .25) / RHYTHM['draw']); b = eio((t - .55) / RHYTHM['draw']); out = []
        if a > 0: s = Image.new('RGBA', (max(1, int(tw * a)), 6), (255, 255, 255, 150)); out.append(Layer(s, lx, ly, -1))
        if b > 0: u = Image.new('RGBA', (max(1, int(rw * b)), 8), ACCENT + (255,)); out.append(Layer(u, rx, uy, -1))
        return out
    return layers, [dyn]


STEP_STYLES = ('并排', '连线')


def steps(spec):
    """步骤卡。"样式"：并排（2026-11-21 起新片子默认，见 steps_row）／连线（老样式，下面这段）。库里直接调、没写样式时按老样式（老片子、老样张不变）。"""
    if spec.get('样式', '连线') == '并排': return steps_row(spec)
    if spec.get('样式', '连线') != '连线': raise SystemExit(f'停止：步骤卡的 "样式" 写成了 “{spec["样式"]}”（可用：{"、".join(STEP_STYLES)}）')
    return steps_line(spec)


def steps_row(spec):
    """步骤卡·并排（《别墅装灯》用户定：“先出现一。出现二的时候，一和二并排出现……每次并排都只在居中……每个上一个出现也都保留”）：
    每步一张小卡（蓝色序号圆＋步骤名），说到哪步加哪步，已出的留着，整排始终水平居中、跟着平移；不画后面还没说到的步骤（不露总共几步）。
    默认放在胸口高度（和口播大字一样，FORMATS 的 koubo），写了 anchor/dy 就按写的；太宽时整体缩到安全区里。"""
    st = spec['steps']; n = len(st); start = spec['_at']; rel = [max(0.0, s.get('_at', start) - start) for s in st]
    k = FMT['k']; fT = font(SIZE['small'], 'medium'); gap = max(360 * k, max(fT.getlength(s['t']) for s in st) + 70 * k)
    f = min(1.0, (SAFE['right'] - SAFE['left']) / (n * gap)); gap *= f; ch = round(300 * k * f)
    def cell(j):
        ti, p = text_img(st[j]['t'], SIZE['small'] * f, 'medium', WHITE); tw = ti.width - 2 * p; w = round(gap - 30 * k * f); s = k * f
        im = Image.new('RGBA', (w, ch), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, w - 1, ch - 1), round(44 * s), fill=(16, 18, 22, 185), outline=(255, 255, 255, 34), width=2)
        d.ellipse((w / 2 - 60 * s, 28 * s, w / 2 + 60 * s, 148 * s), fill=ACCENT + (255,)); fn = font(60 * s, 'semibold'); c = str(j + 1)
        d.text((w / 2 - fn.getlength(c) / 2, 48 * s), c, font=fn, fill=(255, 255, 255, 255))
        im.alpha_composite(ti, (int((w - tw) / 2 - p), int(176 * s - p))); return im
    cells = [cell(j) for j in range(n)]
    y0 = _anchor(spec, round(gap), ch, 'C')[1] if spec.get('anchor') else FMT['koubo'] - ch / 2 + spec.get('dy', 0)
    def dyn(t):
        m = 1.0
        for j in range(1, n): m += eio((t - rel[j]) / RHYTHM['inn'])          # 现在排了几张（带过渡），整排始终居中
        left = (W - m * gap) / 2 + 15 * k * f
        return [Layer(cells[j], left + j * gap, y0, rel[j] if j else 0) for j in range(n) if t >= rel[j]]
    return [], [dyn]


def steps_line(spec):
    """步骤·连线（老样式，取自 ProgressTracker）：①—②—③ 细线圆圈连成一行，下面写步骤名；说到哪一步那一步实心强调色，连线生长过去。"""
    st = spec['steps']; n = len(st); fT = font(SIZE['small'], 'medium'); gap = max(360, int(max(fT.getlength(s['t']) for s in st)) + 80)
    w = gap * (n - 1) + 120; h = 120 + 30 + SIZE['small'] + 20
    x, y = _anchor(spec, w + 200, h, 'TC'); x += 100; start = spec['_at']; rel = [max(0.0, s.get('_at', start) - start) for s in st]
    labels = [text_img(s['t'], SIZE['small'], 'medium', WHITE) for s in st]
    def circle(k, on):
        im = Image.new('RGBA', (120, 120), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        if on: d.ellipse((6, 6, 113, 113), fill=ACCENT + (255,))
        else: d.ellipse((6, 6, 113, 113), outline=(255, 255, 255, 200), width=5)
        f = font(56, 'semibold'); s = str(k + 1); d.text((60 - f.getlength(s) / 2, 22), s, font=f, fill=(255, 255, 255, 255)); return im
    circ = [(circle(k, False), circle(k, True)) for k in range(n)]
    def dyn(t):
        out = []; cur = max([k for k, r in enumerate(rel) if t >= r] or [0])
        for k in range(n):
            cx = x + k * gap
            if k > 0:
                g = eio((t - rel[k]) / RHYTHM['draw']); base = Image.new('RGBA', (gap - 140, 4), (255, 255, 255, 60)); out.append(Layer(base, cx - gap + 130, y + 58, 0))
                if g > 0: out.append(Layer(Image.new('RGBA', (max(1, int((gap - 140) * g)), 4), ACCENT + (255,)), cx - gap + 130, y + 58, -1))
            out.append(Layer(circ[k][1 if k <= cur and t >= rel[k] else 0], cx, y, rel[k] if k else 0))
            li, p = labels[k]; out.append(Layer(li, cx + 60 - (li.width - 2 * p) / 2, y + 150, rel[k] if k else 0, p))
        return out
    return [], [dyn]


def note(spec):
    """提示（取自 CalloutPill / SourceTag）：小胶囊，左边一个强调色圆点。kind=tip 提醒（白字）；kind=source 出处（更小、灰字、无圆点）。"""
    src = spec.get('kind') == 'source'; sz = SIZE['small'] - (12 if src else 0); f = font(sz, 'regular' if src else 'medium')
    tw = int(f.getlength(spec['text'])); pw, ph = tw + (70 if src else 130), sz + 50
    x, y = _anchor(spec, pw, ph, 'BL' if src else 'TL')
    pill = Image.new('RGBA', (pw, ph), (0, 0, 0, 0)); d = ImageDraw.Draw(pill); d.rounded_rectangle((0, 0, pw - 1, ph - 1), ph // 2, fill=(16, 18, 22, 150 if src else 176), outline=(255, 255, 255, 30), width=2)
    if not src: d.ellipse((38, ph / 2 - 11, 60, ph / 2 + 11), fill=ACCENT + (255,))
    ti, p = text_img(spec['text'], sz, 'regular' if src else 'medium', SUB if src else WHITE, False)
    pill.alpha_composite(ti, (int((35 if src else 88) - p), int((ph - (ti.height - 2 * p)) / 2 - p - 4)))
    return [Layer(pill, x, y, 0)], []


def leader(spec):
    """引线标注（取自 LeaderLabel / HandDraw）：目标点一个小圆环，细线生长到旁边，末端出标签（可带一行小字）。点坐标用 4K 画面像素。"""
    px, py = spec['point']; dx, dy = spec.get('to', (360, -220)); ex, ey = px + dx, py + dy
    ti, p = text_img(spec['text'], SIZE['body'], 'semibold', WHITE); si, ps = text_img(spec['sub'], SIZE['small'] - 8, 'regular', SUB) if spec.get('sub') else (None, 0)
    left = dx < 0; tx = (ex - (ti.width - 2 * p) - 24) if left else ex + 24; ty = ey - SIZE['body'] * .62
    ring = Image.new('RGBA', (64, 64), (0, 0, 0, 0)); ImageDraw.Draw(ring).ellipse((6, 6, 57, 57), outline=ACCENT + (255,), width=6); ImageDraw.Draw(ring).ellipse((24, 24, 39, 39), fill=(255, 255, 255, 255))
    layers = [Layer(ring, px - 32, py - 32, 0), Layer(ti, tx, ty, RHYTHM['draw'] * .8, p)]
    if si: layers.append(Layer(si, tx + (0 if not left else (ti.width - 2 * p) - (si.width - 2 * ps)), ty + SIZE['body'] + 26, RHYTHM['draw'] * .8 + RHYTHM['stagger'], ps))
    def dyn(t):
        g = eio((t - .15) / RHYTHM['draw'])
        if g <= 0: return []
        cx, cy = px + dx * g, py + dy * g; x0, y0 = min(px, cx) - 6, min(py, cy) - 6
        im = Image.new('RGBA', (int(abs(cx - px)) + 14, int(abs(cy - py)) + 14), (0, 0, 0, 0))
        ImageDraw.Draw(im).line((px - x0, py - y0, cx - x0, cy - y0), fill=(255, 255, 255, 230), width=5)
        return [Layer(im, x0, y0, -1)]
    return layers, [dyn]


def statement(spec):
    """金句（取自 SlamText / TiltLine，改成克制版）：1–2 行居中大字，逐行上浮出现，关键词用强调色；全片最多 2 次。"""
    lines = spec['lines']; keys = spec.get('keys', []); f = font(SIZE['statement']); lh = SIZE['statement'] + 56
    h = lh * len(lines); x0, y = _anchor(spec, 10, h, 'BC'); layers = []   # 默认在字幕上方，不挡脸
    for i, s in enumerate(lines):
        w = f.getlength(s); im = Image.new('RGBA', (int(w) + 120, SIZE['statement'] + 120), (0, 0, 0, 0)); d = ImageDraw.Draw(im); cx = 60
        sh = Image.new('RGBA', im.size, (0, 0, 0, 0)); ImageDraw.Draw(sh).text((60, 46), s, font=f, fill=(0, 0, 0, 140)); im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
        d = ImageDraw.Draw(im); k = 0
        while k < len(s):
            hit = next((kw for kw in keys if s.startswith(kw, k)), None); seg = hit or s[k]
            d.text((cx, 40), seg, font=f, fill=(ACCENT if hit else WHITE) + (255,)); cx += f.getlength(seg); k += len(seg)
        layers.append(Layer(im, (W - im.width) / 2, y + i * lh - 40, RHYTHM['stagger'] * 2 * i))
    return layers, []


# ───────────── 文字类：口播大字、评论弹窗（2026-11-12 从根目录旧花字库并入，改成苹方＋统一节奏）─────────────
# 旧样式“冬青黑体 W6＋10px 黑描边、静态不淡”按用户 11-12 新规（花字以包装系统为准）改成：苹方中粗、柔和投影不描边、0.40 秒进 0.30 秒出。
# 【】里的词用强调色；〖〗里的“反面”词用红 #FF5C5C（11-12 用户看了红和弱化白的对比样张，定用红）。

def _segs(line):
    """'却很少讲【工艺】？' → [('却很少讲', 白), ('工艺', 蓝), ('？', 白)]；〖〗里的词红"""
    out, col, buf = [], WHITE + (255,), ''
    for ch in line:
        if ch in '【〖':
            if buf: out.append((buf, col))
            buf, col = '', (ACCENT + (255,)) if ch == '【' else CONTRA
        elif ch in '】〗':
            if buf: out.append((buf, col))
            buf, col = '', WHITE + (255,)
        else: buf += ch
    if buf: out.append((buf, col))
    return out


def _line_img(line, size):
    """一行带颜色分段的字，柔和投影；行首开引号、行尾闭引号挂在行外（返回图、pad、挂出去的左右宽度）。"""
    f = font(size); segs = _segs(line); txt = ''.join(t for t, _ in segs); pad = 60
    w = f.getlength(txt); im = Image.new('RGBA', (int(w) + 2 * pad, int(size * 1.4) + 2 * pad), (0, 0, 0, 0))
    sh = Image.new('RGBA', im.size, (0, 0, 0, 0)); ImageDraw.Draw(sh).text((pad, pad + 8), txt, font=f, fill=(0, 0, 0, 190))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14))); d = ImageDraw.Draw(im); x = pad
    for t, c in segs: d.text((x, pad), t, font=f, fill=c); x += f.getlength(t)
    a = len(txt) - len(txt.lstrip('“‘「『《（(')); b = len(txt) - len(txt.rstrip('”’」』》）)'))
    return im, pad, f.getlength(txt[:a]), (f.getlength(txt[len(txt) - b:]) if b else 0.0), w


def _koubo(spec):
    """口播大字：露脸段强调口播要点、替代那几条字幕。lines 多行居中，每行在自己的 _at 出现（没写就一起出），整组位置不跳。
    cy 整组中心高度（默认胸口：3:4 是 y=2000，别的画幅见 FORMATS 的 koubo），scale 整体放大。"""
    lines = spec['lines']; k = spec.get('scale', 1.0); size = round(SIZE['statement'] * k); lh = round(180 * k * FMT['k'])
    imgs = [_line_img(l, size) for l in lines]; maxw = SAFE['right'] - SAFE['left']
    widest = max(w - a - b + 2 * max(a, b) for _, _, a, b, w in imgs)
    if widest > maxw: size = int(size * maxw / widest); lh = int(lh * maxw / widest); imgs = [_line_img(l, size) for l in lines]
    top = spec.get('cy', FMT['koubo']) - (lh * (len(lines) - 1) + size) / 2; start = spec['_at']
    rel = spec.get('line_at') or [max(0.0, x - start) for x in spec.get('_line_at', [start] * len(lines))]
    layers = []
    for i, (im, pad, a, b, w) in enumerate(imgs):
        x = W / 2 - (w - a - b) / 2 - a
        layers.append(Layer(im, x, top + i * lh, rel[i] if i < len(rel) else 0, pad))
    return layers, []


def koubo(lines, W_=2160, shown=None, scale=1.0):
    """兼容旧花字库 huazi.koubo：返回刚好包住整组的透明图（静态，样式按包装系统）。"""
    spec = dict(lines=lines, scale=scale * W_ / 2160, _at=0.0)
    layers, _ = _koubo(spec)                     # 图的大小按整组算，没出现的行留空位，整组位置不跳
    x0 = min(l.x for l in layers); y0 = min(l.y for l in layers)
    x1 = max(l.x + l.im.width for l in layers); y1 = max(l.y + l.im.height for l in layers)
    im = Image.new('RGBA', (int(x1 - x0) + 1, int(y1 - y0) + 1), (0, 0, 0, 0))
    for l in layers[:len(lines) if shown is None else shown]: im.alpha_composite(l.im, (int(l.x - x0), int(l.y - y0)))
    return im


AVA = [((255, 196, 120), (90, 60, 40)), ((150, 200, 255), (40, 60, 110)), ((255, 170, 190), (110, 40, 60)), ((170, 230, 170), (40, 90, 50)), ((220, 200, 255), (70, 50, 120))]
ANGRY = [((255, 110, 100), (90, 20, 20)), ((235, 80, 90), (80, 10, 20)), ((255, 140, 120), (100, 30, 20)), ((220, 90, 120), (80, 20, 40)), ((240, 100, 80), (90, 25, 15))]


def avatar(i, s=100, mood='smile'):
    """卡通头像（不用真实头像）：mood='angry' 红脸、倒八字眉、撇嘴、怒气符号（2026-10-31 用户定，原样保留）。"""
    bgc, ink = (ANGRY if mood == 'angry' else AVA)[i % len(AVA)]
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0)); d = ImageDraw.Draw(im); w = max(3, s // 14)
    d.ellipse((0, 0, s - 1, s - 1), fill=bgc + (255,))
    d.ellipse((s * .28, s * .40, s * .38, s * .52), fill=ink + (255,)); d.ellipse((s * .62, s * .40, s * .72, s * .52), fill=ink + (255,))
    if mood == 'angry':
        d.line((s * .20, s * .26, s * .42, s * .36), fill=ink + (255,), width=w); d.line((s * .80, s * .26, s * .58, s * .36), fill=ink + (255,), width=w)
        d.arc((s * .30, s * .62, s * .70, s * .92), 200, 340, fill=ink + (255,), width=w)
        c = (s * .80, s * .14); r = s * .10
        for a, b in (((-1, -.3), (-.3, -1)), ((1, -.3), (.3, -1)), ((-1, .3), (-.3, 1)), ((1, .3), (.3, 1))):
            d.line((c[0] + a[0] * r, c[1] + a[1] * r, c[0] + b[0] * r, c[1] + b[1] * r), fill=(200, 20, 20, 255), width=max(2, w - 1))
    else:
        d.arc((s * .30, s * .42, s * .70, s * .78), 20, 160, fill=ink + (255,), width=max(3, s // 16))
    return im


def comment_img(txt, i=0, mood='smile', size=64):
    """一条评论卡：半透明白底、左边卡通头像、深色字（苹方）。"""
    f = font(size, 'semibold' if mood == 'angry' else 'medium'); w = f.getlength(txt)
    h = int(size * 2.97); av = int(size * 1.56)
    im = Image.new('RGBA', (int(w + size * 4.06), h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((10, 10, im.width - 10, h - 10), int(size * .53), fill=(250, 250, 252, 214 if mood == 'angry' else 196))
    im.alpha_composite(avatar(i, av, mood), (int(size * .62), (h - av) // 2))
    d.text((int(size * 2.66), h // 2), txt, font=f, fill=(28, 30, 36), anchor='lm')
    return im


def comment(spec):
    """评论弹窗：开头展示评论区反应。items=[{t, mood, xy:(中心x,中心y), _at}]；放在脸的两边、不盖脸；逐条按统一节奏出现（不再回弹）。"""
    start = spec['_at']; layers = []
    for k, it in enumerate(spec['items']):
        im = comment_img(it['t'], k, it.get('mood', 'smile'), it.get('size', 64))
        cx, cy = it.get('xy', (SAFE['left'] + im.width / 2 if k % 2 == 0 else SAFE['right'] - im.width / 2, SAFE['top'] + 280 + k * 260))
        layers.append(Layer(im, cx - im.width / 2, cy - im.height / 2, max(0.0, it.get('_at', start + k * RHYTHM['stagger'] * 3) - start)))
    return layers, []


# ───────────── 信息类卡片：及格线、网速对比（《水电》A20 用户定稿，2026-11-12 收进包装系统）─────────────
BG = (22, 24, 29)   # 卡片单独占一个镜头时的底色（spec 写 bg=true）


def _icon(kind, r=56):
    """✕ 不合格（灰）/ ✓ 达标（白）/ ★ 推荐（蓝）"""
    s = 2 * r + 12; im = Image.new('RGBA', (s, s), (0, 0, 0, 0)); d = ImageDraw.Draw(im); c = s / 2
    if kind == 'x':
        d.ellipse((c - r, c - r, c + r, c + r), outline=(255, 255, 255, 115), width=6); k = r * .42
        d.line((c - k, c - k, c + k, c + k), fill=(255, 255, 255, 140), width=8); d.line((c - k, c + k, c + k, c - k), fill=(255, 255, 255, 140), width=8)
    elif kind == 'v':
        d.ellipse((c - r, c - r, c + r, c + r), outline=(255, 255, 255, 240), width=6)
        d.line((c - r * .42, c + r * .02, c - r * .1, c + r * .34, c + r * .45, c - r * .32), fill=(255, 255, 255, 255), width=9, joint='curve')
    else:
        d.ellipse((c - r, c - r, c + r, c + r), fill=ACCENT + (255,)); pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5; rr = r * (.55 if i % 2 == 0 else .23); pts.append((c + rr * math.cos(a), c + rr * math.sin(a)))
        d.polygon(pts, fill=(255, 255, 255, 255))
    return im


def _pill(text, size, on):
    f = font(size, 'medium'); w = int(f.getlength(text) + size * 1.1); h = int(size * 1.55)
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h // 2, fill=(ACCENT + (255,)) if on else (255, 255, 255, 36))
    d.text((w / 2, h / 2), text, font=f, fill=(255, 255, 255, 255 if on else 205), anchor='mm')
    return im


def _rel(spec, it, default=0.0):
    return max(0.0, it['_at'] - spec['_at']) if '_at' in it else default


def jigexian(spec):
    """及格线：讲“哪档不能用、哪档是底线、哪档最好”。一条虚线“最低标准”，线下面不合格（灰＋划掉），线上刚好达标（白），往上更好（蓝＋升级箭头）。
    rows=[{t, sub, level: below|on|above, at, tag?, tag_at?}]，说到哪一档出哪一档；line 线名，up 箭头旁的话。"""
    rows = spec['rows']; x = SAFE['left'] + 40; rh = 300; title = spec.get('title', '')
    order = [r for r in rows if r['level'] == 'above'] + [r for r in rows if r['level'] == 'on'] + [r for r in rows if r['level'] == 'below']
    n_up = sum(r['level'] != 'below' for r in rows); total = rh * len(order) + 140
    y0 = spec.get('y', (SAFE['top'] + SAFE['bottom']) / 2 - total / 2 + (60 if title else 0)); layers = []; dyn = []
    if title:
        ti, p = text_img(title, SIZE['body'], 'medium', SUB, False); layers.append(Layer(ti, x, y0 - 200, 0, p))
    line_y = y0 + n_up * rh + 30; t_on = min([_rel(spec, r) for r in rows if r['level'] == 'on'] or [0])
    t_up = min([_rel(spec, r) for r in rows if r['level'] == 'above'] or [None], key=lambda v: 0 if v is None else v)
    fN = font(SIZE['key'] + 8)
    for k, r in enumerate(order):
        y = y0 + k * rh + (140 if r['level'] == 'below' else 0); t0 = _rel(spec, r); kind = {'below': 'x', 'on': 'v', 'above': 's'}[r['level']]
        col = DIM if kind == 'x' else WHITE + (255,); sub_col = (ACCENT + (255,)) if kind == 's' else DIM if kind == 'x' else SUB
        ic = _icon(kind); layers.append(Layer(ic, x, y - 8, t0))
        ni, p = text_img(r['t'], SIZE['key'] + 8, 'semibold', col, False); layers.append(Layer(ni, x + 160, y, t0, p))
        if r.get('sub'):
            si, ps = text_img(r['sub'], SIZE['small'] - 4, 'regular', sub_col, False); layers.append(Layer(si, x + 162, y + SIZE['key'] + 40, t0, ps))
        tw = fN.getlength(r['t'])
        if kind == 'x':
            def strike(t, t0=t0, y=y, tw=tw):
                g = eio((t - t0 - .25) / RHYTHM['draw'])
                return [Layer(Image.new('RGBA', (max(1, int((tw + 24) * g)), 8), (255, 255, 255, 150)), x + 150, y + (SIZE['key'] + 8) * .58, -1)] if g > 0 else []
            dyn.append(strike)
        if r.get('tag'):
            pi = _pill(r['tag'], 60, True); layers.append(Layer(pi, x + 160 + tw + 40, y + 6, _rel(spec, dict(_at=r['_tag_at'])) if '_tag_at' in r else t0 + .3))
    lw = SAFE['right'] - x - 40
    def dash(t):
        g = eio((t - t_on) / RHYTHM['draw'])
        if g <= 0: return []
        im = Image.new('RGBA', (lw, 8), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        for xx in range(0, int(lw * g), 46): d.line((xx, 4, min(xx + 24, lw * g), 4), fill=(255, 255, 255, 160), width=5)
        return [Layer(im, x, line_y, -1)]
    dyn.append(dash)
    pl = _pill(spec.get('line', '最低标准'), 46, False); layers.append(Layer(pl, SAFE['right'] - 40 - pl.width, line_y + 26, t_on + .35))
    if t_up is not None and n_up > 1:
        ax = SAFE['right'] - 120; ya = y0 + (n_up - 1) * rh + 60; yb = y0 + 80
        def arrow(t):
            g = eio((t - t_up) / RHYTHM['draw'])
            if g <= 0: return []
            yt = ya - (ya - yb) * g; im = Image.new('RGBA', (60, int(ya - yb) + 80), (0, 0, 0, 0)); d = ImageDraw.Draw(im); oy = yb - 40
            d.line((30, ya - oy, 30, yt - oy + 20), fill=ACCENT + (255,), width=8); d.polygon([(4, yt - oy + 30), (56, yt - oy + 30), (30, yt - oy - 20)], fill=ACCENT + (255,))
            return [Layer(im, ax - 30, oy, -1)]
        dyn.append(arrow)
        if spec.get('up'):
            ui, p = text_img(spec['up'], SIZE['small'] - 8, 'medium', ACCENT + (255,), False)
            layers.append(Layer(ui, ax - 50 - (ui.width - 2 * p), (ya + yb) / 2 - 30, t_up + .3, p))
    return layers, dyn


def wangsu_lengths(spec, L=None):
    """网速对比横条的像素长度：按真实数值等比（最长那条 = L）。测试和排版共用。"""
    L = L or (SAFE['right'] - SAFE['left'] - 160); top = spec.get('max') or max(b['v'] for b in spec['bars'])
    return [L * b['v'] / top for b in spec['bars']]


def wangsu(spec):
    """网速对比：上面几根横条按真实数值等比（外网 2000 兆、5 类线 100 兆、内网万兆……），说到哪根长哪根；
    下面可选“进度条赛跑”（race：传同一个文件，各档用时 sec，最快那档 dur 秒跑满，其余按用时等比只跑到对应进度）。"""
    X = SAFE['left'] + 80; L = SAFE['right'] - SAFE['left'] - 160; bars = spec['bars']; lens = wangsu_lengths(spec, L)
    race = spec.get('race'); bh = 300; total = bh * len(bars) + (180 + 140 * len(race['lanes']) + 120 if race else 0)
    y = spec.get('y', (SAFE['top'] + SAFE['bottom']) / 2 - total / 2 + 60); layers = []; dyn = []
    if spec.get('title'):
        ti, p = text_img(spec['title'], SIZE['body'], 'medium', SUB, False); layers.append(Layer(ti, X, y - 180, 0, p))
    for k, (b, ln) in enumerate(zip(bars, lens)):
        yy = y + k * bh; t0 = _rel(spec, b, k * RHYTHM['stagger']); dim = b.get('dim'); hi = b.get('hi')
        col = DIM if dim else WHITE + (255,); vcol = (ACCENT + (255,)) if hi else col
        li, p = text_img(b['t'], SIZE['unit'], 'semibold', col, False); layers.append(Layer(li, X, yy, t0, p))
        vi, pv = text_img(b.get('show', str(b['v'])), SIZE['unit'], 'semibold', vcol, False); layers.append(Layer(vi, X + L - (vi.width - 2 * pv), yy, t0, pv))
        if b.get('sub'):
            si, ps = text_img(b['sub'], SIZE['small'] - 10, 'regular', DIM if dim else SUB, False); layers.append(Layer(si, X, yy + 196, t0, ps))
        track = Image.new('RGBA', (L, 30), (0, 0, 0, 0)); ImageDraw.Draw(track).rounded_rectangle((0, 0, L - 1, 29), 15, fill=(255, 255, 255, 26))
        layers.append(Layer(track, X, yy + 140, t0)); fill = (ACCENT + (255,)) if hi else (255, 255, 255, 120) if dim else (255, 255, 255, 235)
        def grow(t, t0=t0, ln=ln, yy=yy, fill=fill):
            g = eio((t - t0 - .15) / RHYTHM['count'])
            if g <= 0: return []
            w = max(30, int(ln * g)); im = Image.new('RGBA', (w, 30), (0, 0, 0, 0)); ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, 29), 15, fill=fill)
            return [Layer(im, X, yy + 140, -1)]
        dyn.append(grow)
        if b.get('strike'):
            tw = font(SIZE['unit']).getlength(b['t'])
            def strike(t, t0=t0, yy=yy, tw=tw):
                g = eio((t - t0 - .35) / RHYTHM['draw'])
                return [Layer(Image.new('RGBA', (max(1, int((tw + 20) * g)), 8), (255, 255, 255, 150)), X - 10, yy + SIZE['unit'] * .62, -1)] if g > 0 else []
            dyn.append(strike)
    if race:
        ry = y + len(bars) * bh + 60; t0 = _rel(spec, race, len(bars) * RHYTHM['stagger']); fast = min(l['sec'] for l in race['lanes'])
        rt, p = text_img(race['title'], SIZE['small'], 'medium', SUB, False); layers.append(Layer(rt, X, ry, t0, p))
        lx, lw = X + 260, L - 260 - 360
        for k, ln in enumerate(race['lanes']):
            yy = ry + 150 + k * 140; hi = ln.get('hi'); col = (ACCENT + (255,)) if hi else SUB
            ni, p = text_img(ln['t'], SIZE['small'] + 4, 'semibold', col, False); layers.append(Layer(ni, X, yy - 10, t0 + .2, p))
            ti, p2 = text_img(ln['time'], SIZE['small'] + 4, 'semibold', col, False); layers.append(Layer(ti, X + L - (ti.width - 2 * p2), yy - 10, t0 + .2, p2))
            tr = Image.new('RGBA', (lw, 30), (0, 0, 0, 0)); ImageDraw.Draw(tr).rounded_rectangle((0, 0, lw - 1, 29), 15, fill=(255, 255, 255, 26)); layers.append(Layer(tr, lx, yy + 30, t0 + .2))
            def run(t, frac=fast / ln['sec'], yy=yy, hi=hi):
                g = eio((t - t0 - .4) / race.get('dur', 1.3))
                if g <= 0: return []
                w = max(30, int(lw * frac * g)); im = Image.new('RGBA', (w, 30), (0, 0, 0, 0))
                ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, 29), 15, fill=(ACCENT + (255,)) if hi else (255, 255, 255, 150))
                return [Layer(im, lx, yy + 30, -1)]
            dyn.append(run)
        if race.get('uses'):
            xx = X
            for w in race['uses']:
                pi = _pill(w, 46, False); layers.append(Layer(pi, xx, ry + 150 + len(race['lanes']) * 140 + 20, t0 + 1.6)); xx += pi.width + 22
    return layers, dyn


def _fit(build):
    """整屏卡片（及格线、网速对比）的行高是按 3:4 写死的：别的画幅先在“放大的虚拟画面”里按原尺寸排，再整体缩回去（倍率 card_k），
    横屏内容最宽 card_w、居中。3:4 原样不动。"""
    def wrapped(spec):
        global SAFE, SIZE
        k, col = FMT['card_k'], FMT['card_w']
        if k == 1 and not col: return build(spec)
        real, size = SAFE, SIZE; l, r = real['left'], real['right']
        if col and r - l > col: l, r = (W - col) / 2, (W + col) / 2
        SAFE = dict(left=round(l / k), right=round(r / k), top=round(real['top'] / k), bottom=round(real['bottom'] / k)); SIZE = dict(BASE_SIZE)
        try: layers, dyn = build(dict(spec, y=spec['y'] / k) if 'y' in spec else spec)
        finally: SAFE, SIZE = real, size
        cache = {}
        def sc(L):
            key = (id(L.im), L.im.size)
            if key not in cache: cache[key] = (L.im, L.im.resize((max(1, round(L.im.width * k)), max(1, round(L.im.height * k))), Image.LANCZOS))
            return Layer(cache[key][1], L.x * k, L.y * k, L.t0)
        return [sc(L) for L in layers], [(lambda t, fn=fn: [Layer(L.im.resize((max(1, round(L.im.width * k)), max(1, round(L.im.height * k))), Image.LANCZOS), L.x * k, L.y * k, L.t0) for L in fn(t)]) for fn in dyn]
    return wrapped


COMPONENTS = {'number': number, 'spec': spec_card, 'keywords': keywords, 'compare': compare, 'steps': steps, 'note': note, 'leader': leader,
              'statement': statement, 'koubo': _koubo, 'comment': comment, 'jigexian': _fit(jigexian), 'wangsu': _fit(wangsu)}
KIND = {'number': '文字', 'keywords': '文字', 'note': '文字', 'leader': '文字', 'statement': '文字', 'koubo': '文字', 'comment': '文字',
        'spec': '卡片', 'compare': '卡片', 'steps': '卡片', 'jigexian': '卡片', 'wangsu': '卡片', '人挡字': '人挡字'}
# 每条花字的类型（给人看的名字，2026-11-21 新11：用户说“人后大字”被做成了人前的 koubo）：花字表每条记 类型，check 汇总各类型条数
TYPE = {'number': '数字强调', 'keywords': '关键词', 'note': '提示', 'leader': '引线标注', 'statement': '金句', 'koubo': '口播大字（人前）',
        'comment': '评论弹窗', 'spec': '参数卡', 'compare': '对比', 'steps': '步骤卡', 'jigexian': '及格线', 'wangsu': '网速对比', '人挡字': '人挡字（人后大字）'}


def type_counts(table):
    """花字表（花字表.json 读出来的 dict，或 items 列表）→ (各类型条数 {类型: 条数}，每条 [(n, 类型)])。老花字表没记 类型 的按 use 补。"""
    items = table.get('items', []) if isinstance(table, dict) else table
    each = [(r.get('n'), r.get('类型') or TYPE.get(r.get('use'), r.get('use'))) for r in items]; cnt = {}
    for _, t in each: cnt[t] = cnt.get(t, 0) + 1
    return cnt, each


def frames(spec):
    """spec 必须带 _at、_until（秒，绝对时间）。返回 (总帧数, 取第 i 帧的函数)。bg=true 时铺满不透明底色（卡片单独占一个镜头）。"""
    dur = max(RHYTHM['hold_min'] + RHYTHM['inn'] + RHYTHM['out'], spec['_until'] - spec['_at']); n = round(dur * FPS)
    layers, dyn = COMPONENTS[spec['use']](spec)
    if spec.get('bg'):
        def get(i):
            im = Image.new('RGBA', (W, H), BG + (255,)); im.alpha_composite(compose(layers, i / FPS, dur, dyn)); return im
        return n, get
    return n, (lambda i: compose(layers, i / FPS, dur, dyn))


def still(spec, t=None):
    n, get = frames(spec); return get(min(n - 1, round((t if t is not None else 1.6) * FPS)))


def render_item(spec, out):
    """出一段透明 4K ProRes 4444（只把有内容的区域送进 ffmpeg，再垫回整幅透明画面，快很多）。"""
    n, get = frames(spec); probe = [get(i) for i in range(0, n, max(1, n // 12))] + [get(n - round(RHYTHM['out'] * FPS) - 1)]
    box = None
    for im in probe:
        b = im.getbbox()
        if b: box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    x0, y0, x1, y1 = box or (0, 0, 2, 2); x0, y0 = max(0, x0 - 60) // 2 * 2, max(0, y0 - 60) // 2 * 2; x1, y1 = min(W, x1 + 60), min(H, y1 + 60)
    w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{w}x{h}', '-r', FPS_S, '-i', '-',
                          '-vf', f'pad={W}:{H}:{x0}:{y0}:color=black@0,format=yuva444p12le', '-c:v', 'prores_ks', '-profile:v', '4444', '-r', FPS_S, str(out)], stdin=subprocess.PIPE)
    for i in range(n): p.stdin.write(get(i).crop((x0, y0, x0 + w, y0 + h)).tobytes())
    p.stdin.close(); p.wait()
    if p.returncode: raise RuntimeError(f'花字出片失败：{out}')
    return n


# ───────────── 兼容旧花字库（huazi.paste / write_mov）─────────────
def paste(base, img, cx, cy, s=1.0, alpha=1.0):
    """把花字图以 (cx, cy) 为中心贴到帧上；s 缩放、alpha 透明度"""
    if s <= 0 or alpha <= 0: return
    if s != 1: img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
    if alpha < 1: img = img.copy(); img.putalpha(img.getchannel('A').point(lambda v: int(v * alpha)))
    base.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2)))


def write_mov(out, n, draw, W_=W, H_=H, fps=FPS_S):
    """出 n 帧透明 ProRes 4444：draw(k, 空白帧) 往第 k 帧上画"""
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{W_}x{H_}', '-r', fps, '-i', '-',
                          '-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le', '-r', fps, str(out)], stdin=subprocess.PIPE)
    for k in range(n):
        fr = Image.new('RGBA', (W_, H_), (0, 0, 0, 0)); draw(k, fr); p.stdin.write(fr.tobytes())
    p.stdin.close(); assert p.wait() == 0, f'ffmpeg 出错：{out}'
    return out


# ───────────── 片子里怎么用：花字.json → 合成/花字/ ─────────────
class Words:
    """口播字时间.json：按原话找开口时刻（在上一处之后找；数字直接当秒）。"""
    def __init__(self, tokens):
        self.tok = tokens; self.text = ''.join(t['c'] for t in tokens); self.pos = []
        for i, t in enumerate(tokens): self.pos += [i] * len(t['c'])

    def find(self, w, after=0.0):
        if isinstance(w, (int, float)): return float(w)
        w = re.sub(r'\s', '', w); k = 0
        while (k := self.text.find(w, k)) >= 0:
            if self.tok[self.pos[k]]['t0'] >= after - 1e-6: return self.tok[self.pos[k]]['t0']
            k += 1
        raise SystemExit(f'停止：口播里找不到“{w}”（{after:.2f} 秒之后）')


def resolve(items, words):
    """把每条的 at/until 和子项（items/steps/rows/bars/race/lines_at/tag_at）的原话换成秒（_at/_until）。"""
    out, last = [], 0.0
    for it in items:
        it = json.loads(json.dumps(it)); it['_at'] = words.find(it['at'], last)
        it['_until'] = words.find(it['until'], it['_at'] + 0.1) if 'until' in it else it['_at'] + RHYTHM['hold']
        a = it['_at']
        for key in ('items', 'steps', 'rows', 'bars'):
            for x in it.get(key, []):
                if 'at' in x: x['_at'] = a = words.find(x['at'], a)
                if 'tag_at' in x: x['_tag_at'] = words.find(x['tag_at'], x.get('_at', a))
        if 'race' in it and 'at' in it['race']: it['race']['_at'] = words.find(it['race']['at'], a)
        if 'lines_at' in it:
            b = it['_at']; it['_line_at'] = [b := words.find(w, b) for w in it['lines_at']]
        last = it['_at']; out.append(it)
    return out


def rules(rows):
    """克制档规矩：同一时刻最多 1 个、两个之间至少 3 秒、金句全片 ≤ 2。返回提示（不拦，交审前看）。
    bg=true 的整屏卡片自己占一个镜头，不算叠在画面上的花字，不参加间隔检查。"""
    warn = []; st = [r for r in rows if r['use'] == 'statement']; rows = [r for r in rows if not r.get('bg')]
    if len(st) > 2: warn.append(f'金句 {len(st)} 次，全片最多 2 次')
    for a, b in zip(rows, rows[1:]):
        if b['start'] < a['end']: warn.append(f"第 {a['n']} 和第 {b['n']} 个花字同时出现（同一时刻最多 1 个）")
        elif (b['start'] - a['end']) / FPS < 3: warn.append(f"第 {a['n']} 和第 {b['n']} 个花字只隔 {(b['start'] - a['end']) / FPS:.1f} 秒（至少 3 秒）")
    return warn


def overrun(rows, total):
    """花字拖出片尾（最后一条从片尾前不到最短时长处起，审核线会变长；2026-11-21 261118：koubo 最短 66 帧，97.83 秒起的拖出片尾，到 voice-polish 才拦下）。
    返回说明，没超出返回空。"""
    if not total: return ''
    bad = [r for r in rows if int(r['end']) > int(total)]
    return ('这几条花字超出片尾（成片 %d 帧）：' % int(total) + '；'.join(f"第 {r['n']} 条 {r['use']} 到第 {r['end']} 帧（多 {int(r['end']) - int(total)} 帧）" for r in bad)
            + '。往前挪、换短的组件或拿掉') if bad else ''


def render_list(film_dir, items=None, only=None):
    """<片子目录>/花字.json（列表，或 {"items": [...]}）→ 合成/花字/NN_组件.mov ＋ 花字表.json。
    花字表每条：n、use、kind（文字/卡片）、file、start/end（成片帧号）、at/until（秒）；审核线“花字”轨按它放。only=只重出这几号。"""
    d = Path(film_dir).resolve()         # 花字表里的 file 写完整路径：相对路径时 review 导入达芬奇失败（2026-11-21 261118）
    job_raw = json.loads((d / 'job.json').read_text(encoding='utf-8')) if (d / 'job.json').exists() else {}
    fmt = job_raw.get('format', '3:4')
    key = set_format(fmt)              # 2026-11-14：三种画幅各有版式（FORMATS）；以前只认 3:4，别的画幅停下
    spec = json.loads((d / '花字.json').read_text(encoding='utf-8')) if items is None else items
    items = spec['items'] if isinstance(spec, dict) else spec
    if key != '3:4' and any(it.get('use') == '人挡字' for it in items):
        raise SystemExit(f'停止：人挡字现在只支持 3:4，这条片子是 {fmt}。别的花字可以照出，人挡字那几条先拿掉或跟用户说。')
    words = Words(json.loads((d / '口播字时间.json').read_text(encoding='utf-8'))['tokens'])
    out = d / '合成' / '花字'; rows = []
    all_items = resolve(items, words)
    # 步骤卡默认样式（2026-11-21）：新片子并排；11-21 以前出过花字的老片子（花字表里没记“步骤卡默认”）仍是连线，重出不变样。单条写 "样式" 优先。
    old_tb = out / '花字表.json'
    step_default = (json.loads(old_tb.read_text(encoding='utf-8')).get('步骤卡默认', '连线') if old_tb.exists() else '并排')
    for it in all_items:
        if it['use'] not in KIND: raise SystemExit(f'停止：花字.json 里有不认识的组件 “{it["use"]}”（可用：{"、".join(KIND)}）')
        if it['use'] == 'steps':
            it['样式'] = it.get('样式') or step_default
            if it['样式'] not in STEP_STYLES: raise SystemExit(f'停止：步骤卡（{it["at"]}）的 "样式" 写成了 “{it["样式"]}”（可用：{"、".join(STEP_STYLES)}）')
    rd = rendang.plan(d, all_items, job_raw)      # 人挡字（2026-11-13）：出片前先查完规矩（露脸段、处数、间隔、重叠、字体），不合格不出任何片段
    auto = rendang.auto_choose(d, job_raw) if rd else None
    # 2026-11-21 Q166 复发：出任何片段之前，先按 A肉 人像框量每条要出的人挡字放不放得下，放不下的全部列出、停下（不出半截产物）
    rendang.precheck(d, [(n, it) for n, it in enumerate(all_items, 1) if it['use'] == '人挡字'
                         and ((only is not None and n in only) or not rendang.reusable(it, out / f'{n:02d}_{it["use"]}.mov', d, job_raw))], job_raw)
    out.mkdir(parents=True, exist_ok=True)
    for n, it in enumerate(all_items, 1):
        f = out / f'{n:02d}_{it["use"]}.mov'
        if it['use'] == '人挡字':                  # 整幅不透明片段（处理过的口播画面＋大字＋人），由 rendang.py 出
            nf, rr = rendang.item(it, f, d, job_raw, auto, only is not None and n in only)
            rows.append(dict(n=n, use=it['use'], kind='人挡字', 类型=TYPE['人挡字'], bg=True, file=str(f), start=it['_start'], end=it['_start'] + nf, at=round(it['_at'], 3), until=round(it['_until'], 3),
                             风格=rr['风格'], 套名=rr['套名'], 字=rr['字'], 小字=rr['小字'], 放法=rr['放法'], 遮挡=rr['遮挡'], 入场帧=rr['入场帧'], 耗时秒=rr['耗时秒'],
                             底片=rr.get('底片', str(d / '合成' / 'A肉.mov'))))
            cv = rr['遮挡']
            print(f'{n:02d} 人挡字    {it["_at"]:7.2f}–{it["_until"]:7.2f}s  {nf} 帧  {rr["套名"]}  {rr["放法"]}  '
                  + (cv['说明'] if '说明' in cv else f'被挡 {cv["平均"]:.0%}') + ('' if cv['通过'] else '，不通过') + f'  {rr["耗时秒"]} 秒', flush=True)
            continue
        nf = render_item(it, f) if (only is None or n in only or not f.exists()) else frames(it)[0]
        rec = round(it['_at'] * FPS)
        rows.append(dict(n=n, use=it['use'], kind=KIND[it['use']], 类型=TYPE[it['use']], bg=bool(it.get('bg')), file=str(f), start=rec, end=rec + nf, at=round(it['_at'], 3), until=round(it['_until'], 3),
                         **({'样式': it['样式']} if it['use'] == 'steps' else {})))
        print(f'{n:02d} {it["use"]:9s} {it["_at"]:7.2f}–{it["_until"]:7.2f}s  {nf} 帧', flush=True)
    warn = rules(rows)
    bad = [r for r in rows if r['kind'] == '人挡字' and not r['遮挡']['通过']]
    warn += [f"第 {r['n']} 个人挡字“{r['字']}”" + (r['遮挡']['说明'] if '说明' in r['遮挡'] else f"被挡 {r['遮挡']['平均']:.0%}，不在 {r['套名']} 的范围 {r['遮挡']['范围'][0]:.0%}–{r['遮挡']['范围'][1]:.0%}")
             + f"：{r['遮挡']['建议']}" for r in bad]
    (out / '花字表.json').write_text(json.dumps(dict(items=rows, warnings=warn, 步骤卡默认=step_default), ensure_ascii=False, indent=1), encoding='utf-8')
    over = overrun(rows, job_raw.get('frames'))
    if over: raise SystemExit('停止：' + over + '（片段和花字表已写出；审核线建不了）')
    print(f'共 {len(rows)} 个 → {out / "花字表.json"}（' + '、'.join(f'{k} {v}' for k, v in type_counts(rows)[0].items()) + '）' + ''.join('\n提示：' + w for w in warn))
    if bad: raise SystemExit('停止：人挡字遮挡比例不通过（片段和花字表已写出，可以先看；审核线不会放不通过的人挡字）：' + '；'.join(warn[-len(bad):]))
    return rows, warn


if __name__ == '__main__':
    if len(sys.argv) < 2: raise SystemExit('用法：python3 huazi.py <片子目录> [--only 3,5]')
    render_list(sys.argv[1], only={int(x) for x in sys.argv[sys.argv.index('--only') + 1].split(',')} if '--only' in sys.argv else None)
