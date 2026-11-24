# -*- coding: utf-8 -*-
# “人挡字”第三轮样张（11-13 用户：第二轮只换字体不算风格；按调研的 4 个方向做，字体用下载的开源字体）。
# 风格不只靠字：每套还定 背景处理（压暗/虚化）、整体调色（胶片颗粒、暗角）、出场方式（慢浮现+缓推、硬切、对焦、遮罩升起）。
# 合成顺序：底片（按风格处理背景）→ 大字 → 人物（原片 RGB × RVM 遮罩，不被处理）→ 整体调色。参数是调研的行业经验值，非实测。
import json, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

D = Path(__file__).resolve().parents[6] / '_草稿' / '人挡字_1113'; W, H = 2160, 2880; FPS_S, FPS = '30000/1001', 30000 / 1001
FT = D / '字体'
FONTS = {'黑体特粗': FT / 'SourceHanSansSC-Heavy.otf', '黑体细': FT / 'SourceHanSansSC-Light.otf', '宋体特粗': FT / 'SourceHanSerifSC-Heavy.otf',
         'Inter细': FT / 'Inter/InterDisplay-Light.ttf'}
CLIPS = {
    '室内': dict(bg='素材/段1b_水电_万兆_高取景.mp4', person='RVM/段1b_水电_万兆_高取景/抠像/7b3d2654e2bfdc6f027b66a321a800c60c2f833f11ba8328918d268140f4165d.mov',
               word='万兆', small='第二件事 · 网络', t_in=1.45, still=2.75),
    '户外': dict(bg='素材/段3_地下室_工艺.mp4', person='RVM/段3_地下室_工艺/抠像/ebaa38c0a038a097c9975eaa5771101ced5693115afdd19b91147be8de308d55.mov',
               word='工艺', small='真正的差异 到底在哪', t_in=1.70, still=3.05),
}
def eo(u): u = max(0.0, min(1.0, u)); return 1 - (1 - u) ** 3
def ei(u): u = max(0.0, min(1.0, u)); return u ** 3


def chars(font, word, size, rgba, track=0.0, shadow=0.0):
    """逐字画（方便逐字动画）。返回 [(图, 字墨, x)]、整行宽、字墨顶到图顶的距离 pad。"""
    f = ImageFont.truetype(str(FONTS[font]), size); pad = int(size * .3)
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
    return out, x - track * size, pad


def fit(font, word, width_px, track=0.0):
    f = ImageFont.truetype(str(FONTS[font]), 100)
    return int(width_px / (sum(f.getlength(c) for c in word) + track * 100 * (len(word) - 1)) * 100)


# ───────── 4 套风格 ─────────
def style_apple(c):
    """苹果式静默大字：细黑体纯白、不描边不发光；背景压暗 15%；0.8 秒浮现＋上移 2%；停留时每秒放大 1%。"""
    size = fit('黑体细', c['word'], W * .58)
    return dict(lines=[dict(font='黑体细', word=c['word'], size=size, rgba=(255, 255, 255, 255), shadow=.25, top=430)],
                bg_dark=.15, motion='apple', out=.30)


def style_magazine(c):
    """杂志刊名压头：宋体特粗撑满画宽（两边各留 5%）；上方一行很小的说明（黑体细、字距拉开）；硬切出现；整体胶片调色＋颗粒＋暗角。"""
    size = fit('宋体特粗', c['word'], W * .90)
    return dict(lines=[dict(font='黑体细', word=c['small'], size=64, rgba=(255, 255, 255, 235), track=.35, shadow=.35, top=440),
                       dict(font='宋体特粗', word=c['word'], size=size, rgba=(255, 255, 255, 255), shadow=.30, top=545)],
                motion='cut', grade='film')


def style_focus(c):
    """镜头对焦字：黑体特粗白字；出场 0.6 秒从模糊 60px、放大 115% 收到清晰 100%；同时背景虚化到 16px（像焦点从背景拉到字上）。"""
    size = fit('黑体特粗', c['word'], W * .70)
    return dict(lines=[dict(font='黑体特粗', word=c['word'], size=size, rgba=(255, 255, 255, 255), shadow=.45, top=450)],
                bg_blur=16, motion='focus', out=.30)


def style_huge(c):
    """超大出画面＋遮罩升起：黑体特粗，字宽超过画面、左右被切掉；深色 88% 透明度（和背景同一个调子，人亮着压在上面）；
    每个字从自己底边下面升上来，0.4 秒/字，间隔 0.08 秒。"""
    size = fit('黑体特粗', c['word'], W * 1.18)
    return dict(lines=[dict(font='黑体特粗', word=c['word'], size=size, rgba=(26, 34, 48, 225), shadow=0, top=380)],
                motion='mask_rise', out=.30)


STYLES = {'1_苹果式静默大字': style_apple, '2_杂志刊名压头': style_magazine, '3_镜头对焦字': style_focus, '4_超大出画面_遮罩升起': style_huge}


def layout(st):
    for ln in st['lines']:
        ln['G'], ln['w'], ln['pad'] = chars(ln['font'], ln['word'], ln['size'], ln['rgba'], ln.get('track', 0), ln.get('shadow', 0))
        ln['x0'] = (W - ln['w']) / 2


def text_layer(st, t, dur):
    """返回 (字层 RGBA, 字墨 L, 本帧动画进度 0–1)。"""
    canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0)); inkc = Image.new('L', (W, H), 0)
    if t < 0: return canvas, inkc, 0.0
    m = st['motion']; o = st.get('out')
    out = ei((t - (dur - o)) / o) if o else (1.0 if t >= dur - 1 / FPS else 0.0)
    prog = 1.0
    for ln in st['lines']:
        for k, (im, ink, gx) in enumerate(ln['G']):
            scale, blur, dy, alpha, crop = 1.0, 0.0, 0.0, 1.0, None
            if m == 'apple':
                u = eo(t / .8); alpha, dy = u, 58 * (1 - u); scale = 1 + .01 * max(0, t - .8); prog = u
            elif m == 'cut':
                alpha = 1.0
            elif m == 'focus':
                u = eo(t / .6); alpha = eo(t / .3); blur = 60 * (1 - u); scale = 1.15 - .15 * u; prog = u
            elif m == 'mask_rise':
                u = eo((t - .08 * k) / .4); crop = u; prog = min(prog, u) if k else u
            alpha *= (1 - out)
            if alpha <= .003 or (crop is not None and crop <= 0): continue
            g, gi = im, ink
            cx_line = ln['x0'] + ln['w'] / 2; cy_line = ln['top'] + (g.height - 2 * ln['pad']) / 2
            if crop is not None:   # 遮罩升起：字从自己底边（看不见的线）下面升上来，线以下不显示
                vis = int(g.height * crop); g = g.crop((0, 0, g.width, vis)); gi = gi.crop((0, 0, gi.width, vis))
                px, py = ln['x0'] + gx - ln['pad'], ln['top'] - ln['pad'] + (im.height - vis)
            else:
                if scale != 1.0:
                    g = g.resize((round(g.width * scale), round(g.height * scale)), Image.BICUBIC); gi = gi.resize(g.size, Image.BICUBIC)
                gx0 = ln['x0'] + gx - ln['pad'] + im.width / 2; gy0 = ln['top'] - ln['pad'] + im.height / 2   # 字中心
                px = cx_line + (gx0 - cx_line) * scale - g.width / 2; py = cy_line + (gy0 - cy_line) * scale - g.height / 2 + dy
            if blur > .3: g = g.filter(ImageFilter.GaussianBlur(blur))
            if alpha < .999:
                g = g.copy(); g.putalpha(g.getchannel('A').point(lambda v: int(v * alpha))); gi = gi.point(lambda v: int(v * alpha))
            canvas.alpha_composite(g, (int(px), int(py))) if px >= 0 and py >= 0 else canvas.paste(g, (int(px), int(py)), g)
            inkc.paste(gi, (int(px), int(py)), gi)
    return canvas, inkc, prog


def grade_film(img, rng):
    """胶片感：黑位抬一点、略暖、暗角、颗粒。"""
    a = np.asarray(img, np.float32)
    a = 12 + a * (238 / 255); a[..., 0] *= 1.03; a[..., 2] *= .96
    yy, xx = np.mgrid[0:H:8, 0:W:8]; r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    v = np.asarray(Image.fromarray((np.clip(1 - .22 * np.clip(r - .5, 0, None) ** 1.5 * 2, 0, 1) * 255).astype('uint8')).resize((W, H), Image.BILINEAR), np.float32) / 255
    a *= v[..., None]
    a += rng.normal(0, 5, (H // 2, W // 2, 1)).repeat(2, 0).repeat(2, 1)
    return Image.fromarray(np.clip(a, 0, 255).astype('uint8'))


def frames(path):
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'], stdout=subprocess.PIPE)
    while True:
        b = p.stdout.read(W * H * 4)
        if len(b) < W * H * 4: break
        yield Image.frombuffer('RGBA', (W, H), b, 'raw', 'RGBA', 0, 1)
    p.wait()


def run(sid, cname):
    c = CLIPS[cname]; st = STYLES[sid](c); layout(st)
    out = D / '风格套v3' / sid; out.mkdir(parents=True, exist_ok=True); tag = f'{sid}_{cname}'
    n = int(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', str(D / c['bg'])]).decode().split()[0].strip(','))   # v:0、只取第一行（2026-11-22 新5：带 tmcd 时 csv 会多打一行）
    dur = n / FPS - c['t_in']
    enc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '1080x1440', '-r', FPS_S, '-i', '-', '-i', str(D / c['bg']),
                            '-map', '0:v', '-map', '1:a?', '-shortest', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart',
                            str(out / f'{tag}.mp4')], stdin=subprocess.PIPE)
    rng = np.random.default_rng(1); ratios = []; si = round(c['still'] * FPS)
    for i, (b, m) in enumerate(zip(frames(D / c['bg']), frames(D / c['person']))):
        t = i / FPS - c['t_in']; txt, ink, prog = text_layer(st, t, dur)
        on = 0.0 if t < 0 else (prog if st['motion'] != 'cut' else 1.0) * (1 - (ei((t - (dur - st['out'])) / st['out']) if st.get('out') else 0))
        base = b.convert('RGB')
        if st.get('bg_blur') and on > 0: base = base.filter(ImageFilter.GaussianBlur(st['bg_blur'] * on))
        if st.get('bg_dark') and on > 0: base = Image.eval(base, lambda v, k=1 - st['bg_dark'] * on: int(v * k))
        f = base.convert('RGBA'); f.alpha_composite(txt)
        person = b.copy(); person.putalpha(m.getchannel('A')); f.alpha_composite(person); f = f.convert('RGB')
        if st.get('grade') == 'film': f = grade_film(f, rng)
        if t > 1.0 and t < dur - .35:
            inkn = np.asarray(ink, np.float32) / 255; pa = np.asarray(m.getchannel('A'), np.float32) / 255
            if inkn.sum(): ratios.append(float((inkn * pa).sum() / inkn.sum()))
        enc.stdin.write(f.resize((1080, 1440), Image.LANCZOS).tobytes())
        if i == si: f.save(out / f'{tag}_静帧.jpg', quality=92)
    enc.stdin.close(); enc.wait()
    big = st['lines'][-1]
    res = dict(套=sid, 画面=cname, 字=c['word'], 字号px=big['size'], 占画宽=round(big['w'] / W, 2), 被挡平均=round(sum(ratios) / len(ratios), 3), 被挡最大=round(max(ratios), 3))
    (out / f'{tag}_遮挡.json').write_text(json.dumps(res, ensure_ascii=False)); print(res, flush=True)


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
