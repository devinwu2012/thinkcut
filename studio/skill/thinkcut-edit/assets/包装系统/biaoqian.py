# 包装系统·标签排版（动画里的名称标签：字＋引线＋目标小圆环）。2026-11-12 用户定（问题台账 Q010 复发）：
#   “加黑色底就是用一个错误掩盖另一个错误……把这个文字标签放到一个合适的位置”“如果你不确定放在哪里合适，你可以过来问我”。
# 规矩：默认不加底；位置三种——
#   fixed.world  ：字的中心钉在一个世界坐标点上（Blender 每帧投影成 <点>_text，随镜头走；例：贴在某块墙砖正中）
#   fixed.region ：字放进画面里指定的一块区域（4K 像素 [x0,y0,x1,y1]，字在区域里居中）
#   自动         ：在标签出现那一刻的画面里找“最空、最暗、离目标最近”的位置，整段固定不动
# 自动选位拿不准（最佳位置也压到模型、或前几名分不出高低）→ 出 2–3 个位置样张问用户，定了写成 fixed.region。
# 交审前逐帧自检：字压模型（字下面有细节或太亮，见 model_mask）、字压字，报重叠帧数；非 0 不交审。
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
import numpy as np
import huazi
from huazi import WHITE, ACCENT, text_img, RHYTHM

SAFE_4K = (120, 420, 2040, 2400)        # 3:4 竖版 4K：左右留 120，导航条下、字幕上
MODEL_HIT = 0.02                        # 字的方框里模型像素超过 2% 算压模型
UNSURE = 0.12                           # 前两名得分差不到 12% 算拿不准
FAR = 0.30                              # 离目标超过 30% 画宽算太远


def scale_of(size): return size[0] / 2160


def text_box(lab, size):
    """标签文字的像素尺寸（不含投影边）。"""
    s = scale_of(size); im, pad = text_img(lab['text'], huazi.SIZE['body'], 'semibold', WHITE)
    return (im.width - 2 * pad) * s, (im.height - 2 * pad) * s


def model_mask(frame, bg=None, edge=10, bright=200, sat=0.35):
    """“压不得”的像素：物体轮廓（先模糊去掉渲染噪点再找边缘，稍微加粗）、信息色（水蓝、气流紫这类饱和色）、太亮的地方（白字读不清）。
    纯色暗底、干净的墙砖面不算——字可以放在上面（用户 11-12 给的做法就是字贴在墙砖正中）。重点物体整块不让压：用 protected 方框传进来。"""
    k = frame.size[0] / 2160; g = frame.convert('L'); d = max(1, round(4 * k)) * 2 + 1
    e = np.asarray(g.filter(ImageFilter.GaussianBlur(3 * k)).filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(d)), dtype=np.int16) > edge
    hsv = np.asarray(frame.convert('RGB').convert('HSV'), dtype=np.float32) / 255
    return e | (np.asarray(g, dtype=np.int16) > bright) | ((hsv[..., 1] > sat) & (hsv[..., 2] > 0.35))


def _frac(mask, r):
    x0, y0, x1, y1 = [int(round(v)) for v in r]; h, w = mask.shape
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0: return 1.0
    return float(mask[y0:y1, x0:x1].mean())


def _lum(gray, r):
    x0, y0, x1, y1 = [int(round(v)) for v in r]; h, w = gray.shape
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    return float(gray[y0:y1, x0:x1].mean()) / 255 if x1 > x0 and y1 > y0 else 1.0


def overlap(a, b, pad=0): return a[0] < b[2] + pad and a[2] > b[0] - pad and a[1] < b[3] + pad and a[3] > b[1] - pad


def candidates(frame, lab, pt, size, taken=(), protected=(), bg=None, safe=SAFE_4K):
    """frame 可以是一帧，也可以是标签出现期间的几帧（镜头在动时必须给几帧，位置要整段都不压东西）。自动选位：画面网格上逐个试，压模型/压别的字/压保护区（人脸圆圈、字幕、导航）的直接排除；
    其余按 得分 = 1.0×模型占比 + 0.6×亮度 + 0.8×离目标距离（占画宽）排序，越小越好。返回 [(得分, 方框, 明细)]。"""
    s = scale_of(size); bw, bh = text_box(lab, size); fr = frame if isinstance(frame, (list, tuple)) else [frame]
    mask = np.logical_or.reduce([model_mask(f, bg) for f in fr]); gray = np.max([np.asarray(f.convert('L'), dtype=np.float32) for f in fr], axis=0)   # 镜头在动：几帧合起来看，哪一帧压到都算
    sx0, sy0, sx1, sy1 = [v * s for v in safe]; step = max(8, int(40 * s)); out = []
    for y in range(int(sy0), int(sy1 - bh) + 1, step):
        for x in range(int(sx0), int(sx1 - bw) + 1, step):
            r = (x, y, x + bw, y + bh)
            if any(overlap(r, t, 20 * s) for t in taken) or any(overlap(r, p, 12 * s) for p in protected): continue
            m = _frac(mask, (x - 16 * s, y - 16 * s, x + bw + 16 * s, y + bh + 16 * s)); lum = _lum(gray, r)
            dx = max(r[0] - pt[0], 0, pt[0] - r[2]); dy = max(r[1] - pt[1], 0, pt[1] - r[3]); dist = math.hypot(dx, dy) / size[0]
            if dist < 0.03 * s: continue                     # 太贴着目标，引线没长度、字会压到目标
            out.append((1.0 * m + 0.6 * lum + 0.8 * dist, r, dict(model=round(m, 3), lum=round(lum, 3), dist=round(dist, 3))))
    out.sort(key=lambda c: c[0])
    return out


def distinct(cands, k=3, size=(2160, 2880)):
    """挑 k 个彼此离得开的候选位置（当位置样张 A/B/C）。"""
    pick = []
    for c in cands:
        if all(math.hypot((c[1][0] - p[1][0]), (c[1][1] - p[1][1])) > 0.18 * size[0] for p in pick): pick.append(c)
        if len(pick) == k: break
    return pick


def choose(frame, lab, pt, size, taken=(), protected=(), bg=None, safe=SAFE_4K):
    """一个标签选位。返回 dict(rect, mode, unsure, why, options)。fixed 的不选，直接算。"""
    fx = lab.get('fixed') or {}; bw, bh = text_box(lab, size)
    if 'region' in fx:
        x0, y0, x1, y1 = fx['region']; s = scale_of(size); cx, cy = (x0 + x1) / 2 * s, (y0 + y1) / 2 * s
        return dict(rect=(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2), mode='画面区域', unsure=False, why='', options=[])
    if 'world' in fx:
        return dict(rect=None, mode='世界坐标', unsure=False, why='', options=[])
    cands = candidates(frame, lab, pt, size, taken, protected, bg, safe)
    if not cands: return dict(rect=None, mode='自动', unsure=True, why='安全区里没有不压别的字/保护区的位置', options=[])
    clean = [c for c in cands if c[2]['model'] <= MODEL_HIT / 4]          # 候选优先给“整段都不压东西”的位置
    opts = distinct(clean + [c for c in cands if c not in clean] if len(distinct(clean, 3, (99999, 1))) < 3 else clean, 3, frame[0].size if isinstance(frame, (list, tuple)) else frame.size); best = opts[0]; why = []
    if best[2]['model'] > MODEL_HIT: why.append(f"最好的位置也压到东西 {best[2]['model']:.0%}")
    if best[2]['dist'] > FAR: why.append(f"最好的位置离目标 {best[2]['dist']:.0%} 画宽，引线太长")
    if len(opts) > 1 and opts[1][0] - best[0] < UNSURE * max(best[0], 1e-3): why.append('前两个位置分不出高低')
    return dict(rect=best[1], mode='自动', unsure=bool(why), why='；'.join(why), options=[dict(rect=o[1], score=round(o[0], 3), **o[2]) for o in opts])


def rect_at(lab, rec, place, size):
    """这一帧字的方框：世界坐标的每帧跟投影走，其余用选好的固定位置。"""
    if place['mode'] == '世界坐标':
        q = rec['points'][lab['fixed']['world']]; bw, bh = text_box(lab, size)
        return (q['x'] - bw / 2, q['y'] - bh / 2, q['x'] + bw / 2, q['y'] + bh / 2)
    return place['rect']


def draw(frame, labs, rec, places, t):
    """把标签画到一帧上（白字＋柔和投影，不加底；引线白 90%，目标点蓝圆环＋白芯）。返回新图和本帧每个字的方框。"""
    im = frame.convert('RGBA'); w, h = im.size; s = scale_of((w, h)); boxes = {}
    for lab in labs:
        if not (lab['_a'] <= t < lab['_b']): continue
        r = rect_at(lab, rec, places[lab['id']], (w, h))
        if r is None: continue
        u = huazi.eo((t - lab['_a']) / RHYTHM['inn']); out = huazi.ei((t - (lab['_b'] - RHYTHM['out'])) / RHYTHM['out']); a = u * (1 - out)
        if a <= 0.003: continue
        rise = RHYTHM['rise'] * (1 - u) * s; ti, pad = text_img(lab['text'], huazi.SIZE['body'], 'semibold', WHITE)
        ti = ti.resize((max(1, round(ti.width * s)), max(1, round(ti.height * s))), Image.LANCZOS)
        layer = Image.new('RGBA', im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        pt = rec['points'].get(lab['point']) if lab.get('point') else None
        if pt and pt.get('in_frame', True) and not lab.get('no_leader'):
            x, y = pt['x'], pt['y']; ex = max(r[0], min(r[2], x)); ey = max(r[1], min(r[3], y))
            d.line((x, y, ex, ey + rise), fill=WHITE + (230,), width=max(1, round(5 * s)))
            rr = 26 * s; d.ellipse((x - rr, y - rr, x + rr, y + rr), outline=ACCENT + (255,), width=max(1, round(6 * s)))
            rr = 8 * s; d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=WHITE + (255,))
        layer.alpha_composite(ti, (round(r[0] - pad * s), round(r[1] - pad * s + rise)))
        if a < 0.999: layer.putalpha(layer.getchannel('A').point(lambda v: round(v * a)))
        im.alpha_composite(layer); boxes[lab['id']] = r
    return im, boxes


def check_frame(raw, boxes, labs, bg=None):
    """本帧自检：字压模型（surface=true 的贴面字不查压模型，改查字下面够不够干净）、字压字。返回问题列表。"""
    mask = model_mask(raw, bg); gray = np.asarray(raw.convert('L'), dtype=np.float32); bad = []; ids = list(boxes)
    by = {l['id']: l for l in labs}
    for k, i in enumerate(ids):
        r = boxes[i]
        if by[i].get('surface'):
            x0, y0, x1, y1 = [int(v) for v in r]; patch = gray[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
            if patch.size and (patch.std() > 28 or patch.mean() > 170): bad.append(f'{i} 贴面的地方太花或太亮，字读不清')
        elif _frac(mask, r) > MODEL_HIT: bad.append(f'{i} 压模型 {_frac(mask, r):.0%}')
        for j in ids[k + 1:]:
            if overlap(r, boxes[j], 0): bad.append(f'{i} 和 {j} 字压字')
    return bad


def position_sheet(frame, lab, pt, place, out, size=None):
    """拿不准时出位置样张：同一帧上把 2–3 个候选位置（A/B/C）各画一遍，拼成一张图给用户挑。"""
    size = size or frame.size; tiles = []
    for k, o in enumerate(place['options'][:3]):
        p = dict(mode='画面区域', rect=o['rect']); l = dict(lab, _a=0, _b=99)
        im, _ = draw(frame, [l], dict(points={lab['point']: dict(pt, in_frame=True)} if lab.get('point') else {}), {lab['id']: p}, 1.0)
        im = im.convert('RGB').resize((size[0] // 3, size[1] // 3)); d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 150, 90), fill=(0, 0, 0)); d.text((30, 8), 'ABC'[k], font=huazi.font(64), fill=(255, 255, 255)); tiles.append(im)
    sheet = Image.new('RGB', (sum(t.width for t in tiles) + 20 * (len(tiles) - 1), tiles[0].height), (10, 10, 12)) if tiles else None
    if sheet:
        x = 0
        for t in tiles: sheet.paste(t, (x, 0)); x += t.width + 20
        Path(out).parent.mkdir(parents=True, exist_ok=True); sheet.save(out, quality=90)
    return out if sheet else None
