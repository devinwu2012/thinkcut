#!/usr/bin/env python3
"""封面：单线插画、只给主角上色的封面（3:4、4:3、16:9）。

  init   DIR --line1 主标题第一行 [--line2 第二行 [--line3 第三行]] [--sub 副标题] [--circle 标题关键词] [--arrow-at 0.5,0.9] --scene "<英文场景>" --subject "<英文主角>"
  init   DIR --layout grid --line1 … --sub … --circle 关键词 \
             --cell "格子标签第一行|第二行" --cell-scene "<英文画面>" --cell-subject "<英文上色部分>"   # 重复 4 次（四宫格）
  init   DIR --layout compare|steps|zoom --line1 … --sub … [--circle 关键词] --cell … --cell-scene … --cell-subject …   # 2 / 3 / 2 组
  init   DIR --layout number --line1 … --sub … --number 400 [--unit 块钱] --scene "…" --subject "…"
  gen    DIR portrait|landscape|grid|compare|steps|zoom [--task-only] # 写生图任务单；不加 --task-only 就调 codex exec 生图（多小图的版式三种比例共用一张）
  render DIR [--ratios 3:4 | 4:3,16:9 | all]      # 排版、检查，把成图 JPG 放进 DIR

实拍留色画风（2026-11-20）：init 加 --style photo（默认 line 线稿）。版式 single|grid|compare|number|zoom，另有只给实拍留色的
full 竖图满版、triptych 三联竖条、polaroid 拍立得拼贴、six 六宫格；三步流程不支持。底图种类：
  single|number|zoom → gen DIR photo；grid|compare|triptych|six|polaroid → gen DIR <版式名>；full → gen DIR portrait（3:4）/ landscape（横版）

只给 --line1 且里面有逗号时，按逗号自动拆成两行（逗号不上封面）。主标题要三行时再加 --line3（--line2 也要给）。
--sub 可以不给（2026-11-21 用户：封面文字不分主副）：不画色块，红圈圈标题关键词（不给 --circle 时默认圈最后一行，
最后一行中间有标点时圈到第一个标点为止），红箭头从圈指向主角；--arrow-at 指定箭头落在主角上色部分的哪里（左右,上下 各 0–1）。
单主角、数字大字的横版默认复用竖版画（不用 gen landscape）；另生了横版底图时 render 会提醒“横版和竖版不是同一张画”。

DIR 是交付文件夹，中间文件在 DIR/_制作文件。
"""
import argparse, json, math, random, subprocess, sys, time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

SKILL = Path(__file__).resolve().parent.parent
ASSETS = SKILL / "assets"
sys.path.append(str(SKILL.parents[2] / "bin")); import cutdesk_config      # noqa: E402  配置档：公共层＋每个 IP 一层（2026-11-24）
# 下面这几项（字体、颜色、主角色相、标题压窄）是 IP 的封面风格，值从配置档 封面.* 来（每个 IP 的见 配置档/<IP>/ip.json）；apply_ip() 按片子所属 IP 换
SONG = HEI = None      # 标题宋体（index 6 = SC Regular, 3 = SC Light）、副标题冬青黑体（index 2 = W6），macOS 自带
STYLE_REF = ASSETS / "style_ref.png"

# 2026-10-24 用户：IP 风格色（主角色相、标题色）由配置定，例如蓝色调就整体换成蓝。底图仍让 AI 画陶土色（识别主角最稳），排版时整体换成蓝色。
TITLE_INK = CREAM = ACCENT_HUE = None                        # 标题色（和主角同色相、更深）/ 副标题字色 / 主角色相 215°（蓝）
# 2026-11-12 用户（水电那期四宫格定稿）："以后这个线箭头颜色可以按照你这个来，包括画的方法也按这个来"
RED = None                                                   # 手画圈和箭头的颜色
CIRCLED = "①②③④⑤⑥"                                         # ⑤⑥ 给实拍留色的六宫格（2026-11-20）
TEXT_SCALE = {"16:9": 1.45}                                  # 2026-10-24 用户：16:9 标题太靠边太小，放大往中间伸
SQUEEZE = OUT_PREFIX = None                                  # 标题横向压窄；成图文件名开头（如 0.88 → 瘦高宋体）
RATIOS = {"3:4": (1080, 1440, "portrait"), "4:3": (1440, 1080, "landscape"), "16:9": (1920, 1080, "landscape")}
CODEX = ["codex", "exec", "--skip-git-repo-check", "-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="high"']

# 2026-11-21（261117 反馈单新7）：光被画成柔和渐变，换色后边缘留粉橙色 → 线稿提示词都写明平涂、清晰边线、无渐变无光晕
FLAT = "Flat solid colour fills with crisp drawn outline edges: no gradient, no glow, no halo, no soft shading, no airbrush fade (light beams too)."
STYLE = ("Style: modern minimal line illustration — thin uniform dark-grey lines on off-white, with a single accent colour "
         "(muted terracotta) filling just {subject} and at most one small object; everything else — cabinet doors, cabinet interior, walls, floor, "
         "bottles, towels, plants — stays uncoloured (white paper with grey lines, no tinted fills). Very clean, lots of white space. " + FLAT + " "
         "Fully commit to this style — NOT an ordinary photograph.")
# 2026-11-21（261118 反馈单新11）B 梯子、挑空筒灯两张底图都被画了整圈外框，3:4 下边被裁 → 线稿提示词写明不要外框、四周留纸边
NO_FRAME = "Do NOT draw any frame, border or outline rectangle around the picture; leave plain off-white paper margins on all four sides."
NO_TEXT = "Absolutely no text, letters, numbers, dimension labels, logos, signs, people or watermarks. " + NO_FRAME
# 2026-11-21（261118 反馈单新5）用户再次强调每次都按文字新生成、不用图改图 → 每张生图任务单开头都带这句中文
FRESH = "不打开、不参考任何旧图，按文字新生成。\n"
PORTRAIT = FRESH + """Use your built-in image generation tool to create ONE brand-new image from the text prompt below (generate mode).
Do NOT open, view, attach or edit any existing image file, and do not use any image as an input or reference — 2026-10-24 the user found that editing an earlier picture makes the result blurrier each round. Do not draw with code, do not search the web, do not resize, upscale or re-save the output with other tools (just copy the PNG).
Size: portrait 1024x1536 (or the closest portrait size your tool offers).
PROMPT:
Subject: {scene}; {subject} clearly visible as the key element.
Composition: portrait. The UPPER 45% of the frame must be calm, mostly empty background with NO objects or strong detail — it is reserved for a title. The subject sits entirely in the lower 55%. Draw every fixture exactly where it is really installed (e.g. pipes and valves stay inside the vanity cabinet); never move a part to make empty space. {no_text}
{style}

Save the PNG exactly at: {out}
(copy it there if your tool saves elsewhere). Make exactly one image-generation call. Reply in max 2 lines: file path and pixel size.
"""
LANDSCAPE = FRESH + """Use your built-in image generation tool to create ONE brand-new image from the text prompt below (generate mode).
Do NOT open, view, attach or edit any existing image file, and do not use any image as an input or reference — 2026-10-24 the user found that editing an earlier picture makes the result blurrier each round. Do not draw with code, do not search the web, do not resize, upscale or re-save the output with other tools (just copy the PNG).
Subject: {scene}; {subject} clearly visible as the key element (same content as this video's portrait cover, drawn fresh as a landscape picture).
Size: landscape 1536x1024 (or the closest landscape size your tool offers).
Composition (it will also be cropped to 16:9, so follow this closely): the UPPER 40% of the frame is calm empty background across the full width (reserved for a title). The whole scene — including the countertop and the bottom of {subject} — sits in the band between 40% and 80% of the image height; the bottom 20% shows only plain cabinet fronts or floor. Place {subject} right of centre, drawn where it is really installed; keep the outer 8% at left and right free of key details, and keep the LEFT 45% of the upper half completely empty (no shower, plant, tall object or wall edge there) because the title sits top-left. {no_text}
{style}

Save the PNG exactly at: {out}
(copy it there if your tool saves elsewhere). Make exactly one image-generation call. Reply in max 2 lines: file path and pixel size.
"""
# 2026-11-12 水电那期四宫格：一张横版图里画四张独立小图，切成四格；三种比例共用这一张，只生 1 次图
GRID = FRESH + """Use your built-in image generation tool to create ONE brand-new image from the text prompt below (generate mode).
Do NOT open, view, attach or edit any existing image file, and do not use any image as an input or reference — 2026-10-24 the user found that editing an earlier picture makes the result blurrier each round. Do not draw with code, do not search the web, do not resize, upscale or re-save the output with other tools (just copy the PNG).
Size: landscape 1536x1024 (or the closest landscape size your tool offers).
PROMPT:
A sheet of FOUR separate small illustrations arranged in a 2x2 grid (two on top, two below), each centred in its own quarter of the image, with clear empty off-white gutters between them. Do NOT draw any frames, borders, panel lines or dividers. Each illustration is large and fills most of its quarter (about 85% of the quarter's width), a simple close-up with almost no background or props.
{cells}
{no_text}
Style: modern minimal line illustration — thin uniform dark-grey lines on off-white, with a single accent colour (muted terracotta) filling only the accent parts named above; everything else — walls, floor, furniture, fixtures, wires, water — stays uncoloured (white paper with grey lines, no tinted fills, water drawn with grey lines only). Very clean. """ + FLAT + """ Fully commit to this style — NOT an ordinary photograph.

Save the PNG exactly at: {out}
(copy it there if your tool saves elsewhere). Make exactly one image-generation call. Reply in max 2 lines: file path and pixel size.
"""
GRID_POS = ("Top-left", "Top-right", "Bottom-left", "Bottom-right")
# 2026-11-14 用户在 Design 画板上确认四种新版式都要：左右对比、三步流程、局部放大各用一张横版图画几张小图（写法同 GRID），三种比例共用
PANEL_HEAD = FRESH + """Use your built-in image generation tool to create ONE brand-new image from the text prompt below (generate mode).
Do NOT open, view, attach or edit any existing image file, and do not use any image as an input or reference — 2026-10-24 the user found that editing an earlier picture makes the result blurrier each round. Do not draw with code, do not search the web, do not resize, upscale or re-save the output with other tools (just copy the PNG).
Size: landscape 1536x1024 (or the closest landscape size your tool offers).
PROMPT:
"""
PANEL_TAIL = """{no_text}
Style: modern minimal line illustration — thin uniform dark-grey lines on off-white, with a single accent colour (muted terracotta) filling only the accent parts named above; everything else — walls, floor, furniture, fixtures, wires, water — stays uncoloured (white paper with grey lines, no tinted fills, water drawn with grey lines only). Very clean. """ + FLAT + """ Fully commit to this style — NOT an ordinary photograph.

Save the PNG exactly at: {out}
(copy it there if your tool saves elsewhere). Make exactly one image-generation call. Reply in max 2 lines: file path and pixel size.
"""
COMPARE = PANEL_HEAD + """A sheet of TWO separate small illustrations side by side — one centred in the LEFT half of the image, one centred in the RIGHT half — with a clear empty off-white gutter (at least 8% of the image width) down the middle. Do NOT draw any frames, borders, panel lines, dividers, ticks, crosses or arrows. Draw both at the same scale and from the same viewpoint so they can be compared directly. Each illustration is large and fills most of its half (about 85% of the half's width), a simple close-up with almost no background or props.
Left half (the wrong way): {c0}
Right half (the right way): {c1}
""" + PANEL_TAIL
STEPS = PANEL_HEAD + """A sheet of THREE separate small illustrations in one row — one centred in the left third, one in the middle third, one in the right third — with clear empty off-white gutters between them. Do NOT draw any frames, borders, panel lines, dividers, arrows or numbers. They are three steps of the same job: same scale, same viewpoint. Each illustration is large and fills most of its third (about 85% of the third's width), a simple close-up with almost no background or props.
Left third (step 1): {c0}
Middle third (step 2): {c1}
Right third (step 3): {c2}
""" + PANEL_TAIL
ZOOM = PANEL_HEAD + """A sheet of TWO separate illustrations side by side with a clear empty off-white gutter (at least 8% of the image width) down the middle. Do NOT draw any frames, borders, circles, magnifying glasses, arrows or connecting lines.
Left half — the whole scene as context, a complete view that fills most of the half: {c0}
Right half — a big close-up of one detail of that same scene, centred and compact (about as tall as it is wide, because it will be shown inside a circle), filling about 80% of the half's width: {c1}
""" + PANEL_TAIL
OWN_BG = {"grid": 4, "compare": 2, "steps": 3, "zoom": 2}      # 这些版式用自己的一张横版底图（三种比例共用）；数字是几张小图
LAYOUT_CN = {"single": "单主角", "grid": "四宫格", "compare": "左右对比", "number": "数字大字", "steps": "三步流程", "zoom": "局部放大",
             "full": "竖图满版", "triptych": "三联竖条", "polaroid": "拍立得拼贴", "six": "六宫格"}

# 2026-11-20 实拍留色画风（《261114 千万的房子》试出、用户确认固化，规格见那期的“实拍留色风格说明.md”）：
# 底图是黑白照片、只有问题部位保留彩色；排版沿用上面的标题、色块、红橙手画圈和箭头。照片不调色，只等比缩放加裁切。
PHOTO_ONLY = ("full", "triptych", "polaroid", "six")           # 只有实拍留色画风有的摆法
PHOTO_CELLS = {"grid": 4, "compare": 2, "triptych": 3, "six": 6, "polaroid": 4}   # 这些摆法用一张拼好几张照片的底图
PHOTO_SHEET = {"grid": (2, 2), "compare": (2, 1), "triptych": (3, 1), "six": (3, 2), "polaroid": (2, 2)}   # 底图里几列几行
PHOTO_PAPER = (253, 252, 250)                                # 纸色（当时成图右上角的中位数）
PHOTO_HEAD = FRESH + """Use your built-in image generation tool to create ONE brand-new image from the text prompt below (generate mode).
Do NOT open, view, attach or edit any existing image file, and do not use any image as an input or reference. Do not draw with code, do not search the web, do not resize, upscale or re-save the output with other tools (just copy the PNG).
Size: {size}.
PROMPT:
"""
PHOTO_TAIL = """Realistic phone-camera documentary photo, natural light, believable construction-site details, no people, no faces, no hands. Absolutely no text, letters, numbers, logos, signs, labels, watermarks.

Save the PNG exactly at: {out}
(copy it there if your tool saves elsewhere). Make exactly one image-generation call. Reply in max 2 lines: file path and pixel size.
"""
PHOTO_ONE = PHOTO_HEAD + """ONE realistic documentary PHOTOGRAPH taken inside a finished apartment in China: {scene}. {comp}
SELECTIVE COLOUR: the whole photo is black-and-white (neutral grey); ONLY {subject} keep(s) its natural saturated colour. Nothing else has any colour{grey}.

""" + PHOTO_TAIL
PHOTO_COMP = {"photo": "The coloured problem part is large and clearly readable, and does not touch the edges of the frame.",
              "portrait": "The TOP 40% of the frame is calm and empty (a plain smooth wall and a simple ceiling, no objects, no texture) — it is reserved for a title; the coloured problem part is large, in the lower-middle area, clearly readable.",
              "landscape": "The LEFT 45% of the upper half of the frame is calm and empty (a plain smooth wall, no objects, no texture) — it is reserved for a title; the coloured problem part is large, right of centre, clearly readable."}
PHOTO_SIZE = {"portrait": "portrait 1024x1536 (or the closest portrait size your tool offers)"}
PHOTO_MANY = PHOTO_HEAD + """A sheet of {n} separate realistic documentary PHOTOGRAPHS taken on a real renovation site inside a finished apartment in China, {arrange}, separated by thin plain white gutters. No frames, borders or text.{extra}
Every photo uses SELECTIVE COLOUR: the whole photo is black-and-white (neutral grey), and ONLY the problem part named below keeps its natural saturated real colour. Nothing else may have any colour.
{cells}
""" + PHOTO_TAIL
PHOTO_ARRANGE = {
    "grid": ("FOUR", "arranged in a 2x2 grid (two on top, two below), each filling its own quarter", "", GRID_POS),
    "polaroid": ("FOUR", "arranged in a 2x2 grid (two on top, two below), each filling its own quarter", "", GRID_POS),
    "six": ("SIX", "arranged in a 3x2 grid (three on top, three below), each filling its own sixth", "", ("Top-left", "Top-middle", "Top-right", "Bottom-left", "Bottom-middle", "Bottom-right")),
    "triptych": ("THREE", "arranged as three TALL vertical photos side by side in one row (left, middle, right), each filling its own third of the image from top to bottom",
                 " Each photo is a tall vertical composition with its subject in the vertical middle.", ("Left", "Middle", "Right")),
    "compare": ("TWO", "side by side (left half and right half), same viewpoint and same framing so they can be compared directly", "", ("Left photo", "Right photo"))}
NO_COLOUR = ("none", "no", "-", "无", "")                    # 左右对比左边那张：--cell-subject none（全黑白、不留色）


# ---------- job files ----------
def work(d): p = Path(d) / "_制作文件"; p.mkdir(parents=True, exist_ok=True); return p
def load_spec(d): return json.loads((work(d) / "spec.json").read_text())
def bg_path(d, kind): return work(d) / f"bg_{kind}.png"


def split_title(text):
    """主标题两行。一行时按逗号拆（2026-11-12 水电反馈单新12）：有几个逗号就挑拆完两边字数最接近的那个，逗号不上封面。"""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if len(lines) != 1:
        return lines
    t = lines[0]; cut = [i for i, c in enumerate(t) if c in "，,"]
    if not cut:
        return lines
    i = min(cut, key=lambda i: abs(len(t[:i].strip()) - len(t[i + 1:].strip())))
    return [x for x in (t[:i].strip(" ，,"), t[i + 1:].strip(" ，,")) if x]


def has_sub(spec): return bool(str(spec.get("sub") or "").strip())


PUNCT = "？！，。、；：?!,.;:"


def default_circle(lines):
    """不给副标题、也没给 --circle 时默认圈的词（2026-11-21）：最后一行；最后一行中间有标点时圈到第一个标点为止（连标点）。
    例：“灯装哪？才够亮？”→“灯装哪？”；“住进去就反味”→整行。"""
    t = lines[-1]; i = next((n for n, c in enumerate(t) if c in PUNCT), None)
    return t[:i + 1] if i is not None and 0 < i < len(t) - 1 else t


def circle_word(spec):
    """要圈的标题关键词：--circle 给的；没给、又没有副标题时用 default_circle（数字大字不圈，它有红下划线）。"""
    if spec.get("circle"):
        return spec["circle"]
    if has_sub(spec) or spec.get("layout") == "number":
        return None
    return default_circle(title_lines(spec))


def parse_xy(text, name, what):
    try:
        fx, fy = (float(v) for v in text.replace("，", ",").split(","))
    except ValueError:
        sys.exit(f"{name} 写成两个 0 到 1 之间的数，用逗号隔开，如 \"0.5,0.6\"（左右位置,上下位置）")
    if not (0 <= fx <= 1 and 0 <= fy <= 1):
        sys.exit(f"{name} 的两个数都要在 0 到 1 之间（0 是{what}的左边或上边，1 是右边或下边）")
    return [fx, fy]


def cmd_init(a):
    if a.line3 and not a.line2:                                    # 三行要三行都显式给，逗号只拆两行
        sys.exit("给了 --line3 就要同时给 --line2（三行主标题要 --line1、--line2、--line3 都写上）")
    lines = [a.line1.strip(), a.line2.strip()] if a.line2 else split_title(a.line1)
    if len(lines) != 2:
        sys.exit("主标题要两行：给 --line2，或者在 --line1 里用逗号隔开（三行再加 --line3）")
    if a.line3 and a.line3.strip():                                # 2026-11-20 用户：主标题一般两行，三行也可以；两行时不写这个键
        lines.append(a.line3.strip())
    spec = {"layout": a.layout, "line1": lines[0], "line2": lines[1], **({"line3": lines[2]} if len(lines) > 2 else {}), "sub": a.sub or ""}
    sub = has_sub(spec)                                            # 2026-11-21 用户：封面文字不分主副，副标题可以不给
    photo = a.style == "photo"                                     # 2026-11-20 实拍留色画风
    if a.layout in PHOTO_ONLY and not photo:
        sys.exit(f"{LAYOUT_CN[a.layout]}只有实拍留色画风有，加 --style photo")
    if photo and a.layout == "steps":
        sys.exit("三步流程暂时不支持实拍留色画风：换别的版式，或去掉 --style photo 用线稿")
    if photo and a.zoom_at:
        sys.exit("实拍留色的局部放大自动找留色区域，不用 --zoom-at")
    if a.grey and not (photo and a.layout not in PHOTO_CELLS):
        sys.exit("--grey 只给实拍留色画风的单张照片版式（单主角、数字大字、局部放大、竖图满版）用")
    if photo:
        spec["style"] = "photo"
        if a.grey:
            spec["grey"] = a.grey
    if a.circle:
        if not any(a.circle in l for l in lines):
            sys.exit(f"--circle「{a.circle}」不在主标题里")
        spec["circle"] = a.circle
    if (a.layout == "number" or (a.layout == "zoom" and not photo and sub)) and a.circle:
        sys.exit(f"{LAYOUT_CN[a.layout]}版式不圈关键词，去掉 --circle" + ("（不给副标题时局部放大可以圈）" if a.layout == "zoom" else ""))
    if not sub and not a.circle and a.layout != "number":          # 没有副标题：红圈＋箭头从圈出发，保持封面的红线风格
        spec["circle"] = default_circle(lines)
        print(f"没给副标题和 --circle：默认圈最后一行的「{spec['circle']}」（要换就加 --circle 重新 init）")
    if getattr(a, "small_subject", False): spec["small_subject"] = True      # 2026-11-23：主角本来就是小物件（开关、插座）
    if a.arrow_at:                                                 # 箭头落点：主角上色部分（实拍是留色区）里的位置
        if a.layout not in ("single", "full"):
            sys.exit("--arrow-at 只给单主角（和实拍留色的竖图满版）用：箭头落在主角上色部分里的这个位置")
        spec["arrow_at"] = parse_xy(a.arrow_at, "--arrow-at", "主角上色部分")
    if a.layout != "number" and (a.number or a.unit):
        sys.exit("--number / --unit 只给数字大字版式（--layout number）用")
    if a.layout != "zoom" and a.zoom_at:
        sys.exit("--zoom-at 只给局部放大版式（--layout zoom）用")
    if photo and a.layout in PHOTO_CELLS:
        n = PHOTO_CELLS[a.layout]; cells = list(zip(a.cell or [], a.cell_scene or [], a.cell_subject or []))
        order = {"grid": "左上、右上、左下、右下", "compare": "先表面看着没问题的（--cell-subject none，全黑白）、后露出问题的",
                 "triptych": "左、中、右", "six": "从左到右、从上到下", "polaroid": "第一张是主角（放最上面、画圈和箭头），其余三张随后"}[a.layout]
        if len(cells) != n or not (len(a.cell) == len(a.cell_scene) == len(a.cell_subject)):
            sys.exit(f"实拍留色的{LAYOUT_CN[a.layout]}要 {n} 组 --cell / --cell-scene / --cell-subject，顺序是{order}")
        if a.layout in ("grid", "triptych", "six") and not spec.get("circle"):
            sys.exit(f"{LAYOUT_CN[a.layout]}要用 --circle 圈住标题关键词（箭头从圈出发指向照片）")
        spec["cells"] = [{"label": l, "scene": s, "subject": u} for l, s, u in cells]
    elif a.layout == "grid":
        cells = list(zip(a.cell or [], a.cell_scene or [], a.cell_subject or []))
        if len(cells) != 4 or not (len(a.cell) == len(a.cell_scene) == len(a.cell_subject)):
            sys.exit("四宫格要 4 组 --cell / --cell-scene / --cell-subject，按顺序是左上、右上、左下、右下")
        if not spec.get("circle"):
            sys.exit("四宫格要用 --circle 圈住标题关键词（箭头从圈出发指向格子）")
        spec["cells"] = [{"label": l, "scene": s, "subject": u} for l, s, u in cells]
    elif a.layout in ("compare", "steps", "zoom") and not photo:   # 2026-11-14 新版式（实拍留色的局部放大只要一张照片，走下面）
        n = OWN_BG[a.layout]; cells = list(zip(a.cell or [], a.cell_scene or [], a.cell_subject or []))
        order = {"compare": "先错的、后对的", "steps": "第一步、第二步、第三步", "zoom": "先整体场景、后局部特写"}[a.layout]
        if len(cells) != n or not (len(a.cell) == len(a.cell_scene) == len(a.cell_subject)):
            sys.exit(f"{LAYOUT_CN[a.layout]}要 {n} 组 --cell / --cell-scene / --cell-subject，顺序是{order}")
        if a.layout == "compare" and not spec.get("circle"):
            sys.exit("左右对比要用 --circle 圈住标题关键词（箭头从圈出发指向右边“对的”那格）")
        spec["cells"] = [{"label": l, "scene": s, "subject": u} for l, s, u in cells]
        if a.layout == "zoom":
            spec["zoom_at"] = parse_xy(a.zoom_at or "0.5,0.5", "--zoom-at", "整体小图")
    else:
        if a.layout == "number":
            if not a.number:
                sys.exit("数字大字版式要 --number（比如 --number 400）")
            spec.update(number=a.number.strip(), unit=(a.unit or "").strip())
        if not (a.scene and a.subject):
            sys.exit(f"{LAYOUT_CN[a.layout]}版式要 --scene 和 --subject")
        spec.update(scene=a.scene, subject=a.subject)
    spec["created"] = datetime.now().isoformat(timespec="seconds")
    (work(a.dir) / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    print("已写封面文字：", work(a.dir) / "spec.json")


def photo_bg(d, kind): return work(d) / ("bg_photo.png" if kind == "photo" else f"bg_photo_{kind}.png")


def photo_kinds(s):
    """实拍留色要的底图种类：竖图满版竖版、横版各一张；拼图版式用自己名字；其余共用一张横版照片 photo。"""
    lay = s.get("layout", "single")
    return ("portrait", "landscape") if lay == "full" else (lay,) if lay in PHOTO_CELLS else ("photo",)


def write_photo_task(d, s, kind):
    """实拍留色的生图任务单（提示词模板照“实拍留色风格说明”第 4 节）。"""
    kinds = photo_kinds(s); lay = s.get("layout", "single")
    if kind not in kinds:
        sys.exit(f"实拍留色的{LAYOUT_CN[lay]}版式用 gen DIR {' / '.join(kinds)}")
    if kind == "landscape" and not photo_bg(d, "portrait").exists():
        sys.exit("先完成并确认竖版（3:4），再出横版")
    out = photo_bg(d, kind); t = work(d) / ("task_photo.md" if kind == "photo" else f"task_photo_{kind}.md")
    size = PHOTO_SIZE.get(kind, "landscape 1536x1024 (or the closest landscape size your tool offers)")
    if kind in PHOTO_CELLS:
        n, arrange, extra, pos = PHOTO_ARRANGE[kind]
        cells = "\n".join(f"{p}: {c['scene']}. " + ("Entirely black-and-white, no colour at all." if c["subject"].strip().lower() in NO_COLOUR else f"Colour only: {c['subject']}.")
                          for p, c in zip(pos, s["cells"]))
        t.write_text(PHOTO_MANY.format(size=size, n=n, arrange=arrange, extra=extra, cells=cells, out=out))
    else:
        t.write_text(PHOTO_ONE.format(size=size, scene=s["scene"].rstrip(". "), comp=PHOTO_COMP[kind], subject=s["subject"],
                                      grey=f" ({s['grey']} are grey)" if s.get("grey") else "", out=out))
    return t, out


def write_task(d, kind):
    s = load_spec(d)
    if s.get("style") == "photo":                                  # 2026-11-20 实拍留色
        return write_photo_task(d, s, kind)
    if kind in ("photo", "triptych", "six", "polaroid"):
        sys.exit(f"gen {kind} 只给实拍留色画风用（init 时加 --style photo）")
    out = bg_path(d, kind); t = work(d) / f"task_{kind}.md"
    own = s.get("layout") if s.get("layout") in OWN_BG else None   # 四宫格和 2026-11-14 三种新版式各用自己的一张底图
    if (kind in OWN_BG or own) and kind != own:
        sys.exit(f"{LAYOUT_CN[own]}版式用 gen DIR {own}" if own else "单主角和数字大字版式用 gen DIR portrait / landscape")
    if kind in ("compare", "steps", "zoom"):
        cells = {f"c{i}": f"{c['scene']}. Accent colour: {c['subject']}." for i, c in enumerate(s["cells"])}
        t.write_text({"compare": COMPARE, "steps": STEPS, "zoom": ZOOM}[kind].format(out=out, no_text=NO_TEXT, **cells))
        return t, out
    if kind == "grid":
        cells = "\n".join(f"{p}: {c['scene']}. Accent colour: {c['subject']}." for p, c in zip(GRID_POS, s["cells"]))
        t.write_text(GRID.format(cells=cells, out=out, no_text=NO_TEXT))
        return t, out
    if kind == "landscape" and not bg_path(d, "portrait").exists():
        sys.exit("先完成并确认竖版（3:4），再出横版")
    tpl, ref = (PORTRAIT, STYLE_REF) if kind == "portrait" else (LANDSCAPE, bg_path(d, "portrait"))
    t.write_text(tpl.format(ref=ref, scene=s["scene"], subject=s["subject"], out=out, no_text=NO_TEXT,
                            style=STYLE.format(subject=s["subject"])))
    return t, out


def cmd_gen(a):
    t, out = write_task(a.dir, a.kind)
    print("生图任务单：", t)
    if a.kind == "landscape" and load_spec(a.dir).get("style") != "photo":
        print("提醒：单主角、数字大字的横版默认复用竖版画，不用另生；另生的横版和竖版不是同一张画，render 时会提醒，出图后先给用户看")
    if a.task_only:
        return
    w = work(a.dir); log = w / f"log_{a.kind}.txt"
    if out.exists():
        out.rename(out.with_name(f"{out.stem}_{int(time.time())}.png"))   # keep the previous attempt
    with open(log, "w") as f:
        rc = subprocess.call(CODEX + ["-C", str(w), "-s", "workspace-write", f"Read {t} and do the task exactly as specified."],
                             stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
    with open(w / "gen_count.log", "a") as f:
        f.write(f"{datetime.now():%F %T}\t{a.kind}\trc={rc}\t{'ok' if out.exists() else 'missing'}\n")
    if rc or not out.exists():
        sys.exit(f"生图失败（rc={rc}），看日志 {log}；Codex 拒绝或失败也算一次额度，先告诉用户再决定是否重试")
    print("底图已生成：", out, "尺寸 %d×%d" % Image.open(out).size)
    if load_spec(a.dir).get("style") != "photo":                   # 2026-11-21（新11）出图后马上查有没有整圈外框
        sides = frame_sides(out)
        if len(sides) >= 3:
            print("警告：" + FRAME_MSG.format(sides="、".join(sides)))


# ---------- image analysis ----------
def fit(img, W, H):
    # portrait keeps the top (the approved 3:4); landscape 16:9 crops evenly from a 3:2 background
    return ImageOps.fit(img.convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.0 if H > W else 0.5))


def to_blue(img):
    """把底图里的陶土色（橙红色相、有一定饱和度）换成同明暗的蓝色；米白纸面、灰线、绿叶不动。"""
    hsv = np.asarray(img.convert("HSV")).astype(np.float32) / 255
    h, s_, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    warm = ((h < 45 / 360) | (h > 345 / 360)) & (s_ > 0.14)
    w = np.clip((s_ - 0.14) / 0.12, 0, 1) * warm          # 抗锯齿边缘按饱和度渐变过渡，不留橙边
    hsv[..., 0] = np.where(w > 0, ACCENT_HUE, h)
    hsv[..., 1] = s_ * (1 - w) + np.minimum(s_ * 1.05, 0.62) * w
    hsv[..., 2] = v * (1 - w) + v * 0.96 * w
    out = (hsv * 255).round().astype(np.uint8)
    # 原来是 Image.fromarray(out, "HSV")：mode 参数 Pillow 13 起移除。frombytes 按同样的字节顺序读，像素完全一样
    return Image.frombytes("HSV", (out.shape[1], out.shape[0]), out.tobytes()).convert("RGB")


WARM_LEFT = 0.005     # 换色后残留粉橙色像素占比的报警线：合格样例都在 0.07% 以下，261117 那几张画成渐变的在 2.5%–5%


def warm_left(bg):
    """底图换色（to_blue）后还剩多少粉橙色：暖色相、比纸色明显更饱和（饱和度 > 纸色 + 0.04 且至少 0.07）、不太暗的像素占比。
    看的是底图本身，红橙圈和箭头还没画上去，不会算进去。"""
    img = Image.open(bg).convert("RGB"); a = np.asarray(img).astype(int)
    paper = np.median(a[: a.shape[0] // 10].reshape(-1, 3), axis=0); ps = (paper.max() - paper.min()) / max(paper.max(), 1)
    hsv = np.asarray(to_blue(img).convert("HSV")).astype(np.float32) / 255; h, s_, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return float((((h < 45 / 360) | (h > 345 / 360)) & (s_ > max(0.07, ps + 0.04)) & (v > 0.5)).mean())


def frame_sides(bg):
    """2026-11-21（261118 反馈单新11）底图四周有没有 AI 自己画的整圈外框：每条边往里 8% 的范围内，
    有一条深灰线连续占满这条边 9 成以上就算这一边有框。返回有框的边（三边以上就当整圈外框）。
    本片两张带框底图四边都在 98% 左右；不带框的（含画到图边的满幅场景）最多 70%。"""
    a = np.asarray(Image.open(bg).convert("RGB")).astype(int)
    paper = np.median(a[: a.shape[0] // 10].reshape(-1, 3), axis=0)
    dark = paper.mean() - a.mean(axis=2) > 60; H, W = dark.shape; bh, bw = max(3, int(H * 0.08)), max(3, int(W * 0.08))
    out = []
    for name, m in (("上", dark[:bh]), ("下", dark[::-1][:bh]), ("左", dark.T[:bw]), ("右", dark.T[::-1][:bw])):
        mm = m.copy()
        for s_ in (1, 2):                     # 线稍有倾斜或只有 1px 粗：上下各并 2 行再量
            mm[:-s_] |= m[s_:]; mm[s_:] |= m[:-s_]
        if mm.mean(axis=1).max() >= 0.9:
            out.append(name)
    return out


FRAME_MSG = "底图被画了一圈外框（{sides}边都有连续的框线），排版时会被裁、边上露出硬线。先给用户看底图、问是否重出（提示词已写明不要外框、四周留纸边）"


def busy_mask(arr):
    """Drawn pixels (lines + colour) vs the off-white paper."""
    paper = np.median(arr[: arr.shape[0] // 10].reshape(-1, 3), axis=0)
    return np.abs(arr.astype(int) - paper).max(axis=2) > 28


SUBJECT_MIN = 0.004   # 主角上色部分至少占画面的比例（几个同类小主角按合计面积算）
SUBJECT_PEER = 0.5    # 面积至少有最大那块一半的同色块，算“同类主角”（那期 3 个、4 个筒灯互相都在 97% 以上）
# 2026-11-23 初稿模式实测（Q193 又复发）：主角本来就小的东西（墙上的开关面板、插座）上色部分只占 0.20%、0.32%，不到 0.4% 的门槛，
# 报“没找到陶土色主角”、不画箭头，其实底图画对了。门槛是防“底图没给主角上色、只剩零星色点”，默认不动（2026-11-21 定：主角太小照旧报警）；
# spec 里写了 "small_subject": true（init --small-subject：主角本来就是小物件）时放宽成：上色部分至少有门槛的 1/8，
# 而且画面里的陶土色基本都在这一块（或这几块同类）上，就认它是主角。
SUBJECT_SMALL = 0.125  # 小主角：面积至少是 SUBJECT_MIN 的这么多（0.05%）
SUBJECT_ALONE = 0.8    # 小主角：它占全部陶土色的比例至少这么多（别处没有别的上色块）


SMALL_HINT = "主角本来就是小物件（开关、插座、龙头这类）时不用重出：在 _制作文件/spec.json 里加一行 \"small_subject\": true（或 init 时加 --small-subject）再 render"


def subject_box(arr, min_frac=SUBJECT_MIN, near=None, small=False):
    """Largest terracotta blob (thin drawn lines inside it bridged) → (x0, y0, x1, y1, top_x0, top_x1) or None.
    top_x0/top_x1 = horizontal extent of the blob's top edge, so a touching coloured pot does not shift the arrow.
    2026-11-21（261118 反馈单新10）几个同类小主角（挑空筒灯 4 个各约 0.16%、3 个大筒灯各约 0.31%）单块不够 min_frac：
    和最大那块差不多大（面积 ≥ 一半）的同色块合计面积够了也认，返回其中最靠边的一块（箭头指它）——这几块排开方向上两头的那两块里，
    离 near（箭头出发点，标题红圈；没给就当右上角，副标题色块一般靠右）近的那一头，箭头不横穿另外几个（那期 16:9 箭头横穿了另外两个筒灯）。
    只有一块上色时和以前完全一样：只认最大的一块。"""
    r, g, b = (arr[..., i].astype(int) for i in range(3))
    m = (r - b > 45) & (r > 140) & (g > b) & (r - g > 20)
    k = 4; sm = m[::k, ::k]
    sm = sm | np.roll(sm, 1, 1) | np.roll(sm, -1, 1) | np.roll(sm, 1, 0) | np.roll(sm, -1, 0)   # bridge 1-cell gaps (edge lines)
    h, w = sm.shape; seen = np.zeros_like(sm); blobs = []
    for y0, x0 in zip(*np.nonzero(sm)):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]; seen[y0, x0] = 1; pts = []
        while stack:
            y, x = stack.pop(); pts.append((y, x))
            for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= yy < h and 0 <= xx < w and sm[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = 1; stack.append((yy, xx))
        blobs.append(pts)
    if not blobs:
        return None
    best = max(blobs, key=len); group = [p for p in blobs if len(p) >= SUBJECT_PEER * len(best)]
    cells = sum(map(len, group)); total = arr.shape[0] * arr.shape[1]
    if cells * k * k < min_frac * total and not (small and cells * k * k >= SUBJECT_SMALL * min_frac * total and cells >= SUBJECT_ALONE * sum(map(len, blobs))):
        return None
    if len(group) > 1:
        cs = [np.array(p).mean(axis=0)[::-1] * k for p in group]                      # 各块中心 (x, y)
        ax = int(np.ptp([c[1] for c in cs]) > np.ptp([c[0] for c in cs]))             # 这几块横着排开还是竖着排开
        ref = near or (arr.shape[1], 0)
        best = group[min((min(range(len(cs)), key=lambda i: cs[i][ax]), max(range(len(cs)), key=lambda i: cs[i][ax])),
                         key=lambda i: math.hypot(cs[i][0] - ref[0], cs[i][1] - ref[1]))]
    ys, xs = np.array(best).T
    top = xs[ys <= ys.min() + 6]                     # top 24px of the blob
    return tuple(int(v) for v in (xs.min() * k + k, ys.min() * k + k, xs.max() * k, ys.max() * k,
                                   top.min() * k + k, top.max() * k))


# ---------- typesetting ----------
FONT_WANT, _FONT_IDX = {}, {}


def apply_ip(where=None, ip=None):
    """按 IP 配置（片子所属 IP；where 是交付文件夹，往上找 项目.json）换上封面风格参数。"""
    global SONG, HEI, TITLE_INK, CREAM, ACCENT_HUE, RED, SQUEEZE, FONT_WANT, OUT_PREFIX
    c = cutdesk_config.load(ip=ip, project=where).get("封面")
    SONG, HEI = c["标题字体"], c["副标题字体"]
    TITLE_INK, CREAM, RED = tuple(c["标题色"]), tuple(c["副标题字色"]), tuple(c["圈箭头色"])
    ACCENT_HUE, SQUEEZE = c["主角色相"] / 360, c["标题横向压缩"]
    OUT_PREFIX = c["文件名前缀"]                                # 成图文件名开头（公共层“封面_”）
    FONT_WANT = {(SONG, 6): ("Songti SC", "Regular"), (SONG, 3): ("Songti SC", "Light"), (HEI, 2): ("Hiragino Sans GB", "W6")}
    _FONT_IDX.clear()


apply_ip()


def font_index(path, idx):
    """字体文件里的序号会随 macOS 版本变（2026-11-21 体检）：先核对这个序号是不是要的那款，不是就按名字在文件里找；文件没了、找不到都直接说清楚。"""
    key = (path, idx)
    if key not in _FONT_IDX:
        want = FONT_WANT.get(key)
        if not Path(path).exists():
            sys.exit(f"缺字体文件 {path}（macOS 自带）：打开“字体册”下载“{want[0] if want else Path(path).stem}”后重试")

        def name(i):
            try: return ImageFont.truetype(path, 20, index=i).getname()
            except OSError: return None
        got = idx
        if want and name(idx) != want:
            got = next((i for i in range(32) if name(i) == want), None)
            if got is None:
                sys.exit(f"字体文件 {path} 里找不到“{want[0]} {want[1]}”：系统字体变了，把这句话发给 AI 处理")
        _FONT_IDX[key] = got
    return _FONT_IDX[key]


def font(path, size, idx=0): return ImageFont.truetype(path, size, index=font_index(path, idx))


def line_img(text, f, track, fill, stroke=0, squeeze=None):
    adv = [f.getlength(c) for c in text]
    w = round(sum(adv) + track * (len(text) - 1)) + 2 * stroke; h = round(f.size * 1.3) + 2 * stroke
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im); x = stroke
    for c, a in zip(text, adv):
        d.text((x, stroke), c, font=f, fill=fill, stroke_width=stroke, stroke_fill=fill); x += a + track
    q = SQUEEZE if squeeze is None else squeeze
    return im if q == 1 else im.resize((round(w * q), h), Image.LANCZOS)


def ink(im):  # visible ink box, ignoring faint anti-aliasing
    return im.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()


# 2026-11-20 用户：封面主标题一般两行，三行也可以。第一行常规、第二三行细体，行距、压窄、边距同两行。
def title_lines(spec):
    return [spec["line1"], spec["line2"]] + ([spec["line3"]] if spec.get("line3") else [])


TITLE3_SCALE = 0.9      # 三行时主标题字号乘这个数：整块标题不至于把插图、照片挤得太小（3:4 时 104px，手机上仍清楚）


def title_px(spec, S):
    """主标题字号：两行原样，三行乘 TITLE3_SCALE。"""
    return S if len(title_lines(spec)) < 3 else round(S * TITLE3_SCALE)


def title_font(S, i): return font(SONG, S, 6 if i == 0 else 3)      # 第一行常规（index 6），其余细体（index 3）


def title_imgs(spec, S):
    return [line_img(t, title_font(S, i), S * 0.03, TITLE_INK) for i, t in enumerate(title_lines(spec))]


def find_oval(spec, S, x, ys, k):
    """在主标题各行里找要圈的关键词（取第一处），返回 keyword_oval 的结果加 rows（那一行的上下范围）和 line（第几行，0 起）。"""
    word = circle_word(spec)
    for i, (txt, yy) in enumerate(zip(title_lines(spec), ys)) if word else ():
        ov = keyword_oval(txt, title_font(S, i), S, word, x, yy, k)
        if ov:
            ov["rows"] = (yy, yy + round(S * 1.3)); ov["line"] = i
            return ov
    return None


def place_label(busy, title_box, target, bw, bh, W, H, M):
    """Best empty spot for the label near the target; prefers the right margin and sitting ~150px above the target."""
    tx, ty = target; best = None
    for by in range(M, H - M - bh, 5):
        for bx in sorted({W - M - bw, M, *range(M, W - M - bw, 20)}):
            r = (bx - 12, by - 12, bx + bw + 12, by + bh + 12)
            if r[1] < title_box[3] + 40 and r[0] < title_box[2] + 30:      # keep clear of the title
                continue
            if r[0] < tx < r[2] and r[1] < ty < r[3]:
                continue
            if busy[max(r[1], 0):r[3], max(r[0], 0):r[2]].mean() > 0.02:
                continue
            dx = max(bx - tx, 0, tx - (bx + bw)); dy = max(by - ty, 0, ty - (by + bh)); dist = math.hypot(dx, dy)
            if not 90 <= dist <= 900:
                continue
            near_title = max(0, 3.7 * M - (bx - title_box[2])) if by < title_box[3] + 60 else 0   # same band as the title → keep ~300px away
            steep = 0.6 * max(0, dx - dy)                      # prefer arrows that point down rather than sideways
            # 2026-10-24 用户：色块放在标题和插图之间的空白里（偏上约 3 成处）更好看，箭头拉长走弧线
            cy_want = title_box[3] + 0.3 * (ty - title_box[3])
            score = abs(by + bh / 2 - cy_want) + (0 if bx == W - M - bw else 120) + (0 if by + bh < ty else 80) + near_title + steep
            if best is None or score < best[0]:
                best = (score, bx, by)
    return best


# ---------- 红橙手画圈和箭头（2026-11-12 水电那期定稿，单主角和四宫格都用） ----------
def bezier(p0, c1, c2, p1, n=60):
    return [tuple((1 - t) ** 3 * p0[i] + 3 * (1 - t) ** 2 * t * c1[i] + 3 * (1 - t) * t * t * c2[i] + t ** 3 * p1[i] for i in (0, 1))
            for t in (j / n for j in range(n + 1))]


def stroke_width(k): return max(6, round(7 * k))


def stroke(draws, pts, lw, ends=True):
    """一笔：线宽 lw，两头画圆点当笔头。draws = [(ImageDraw, 颜色), …]（第二个一般是检查用的蒙版）。"""
    r = lw / 2
    for d, col in draws:
        d.line(pts, fill=col, width=lw, joint="curve")
        for q in ((pts[0], pts[-1]) if ends else ()):
            d.ellipse((q[0] - r, q[1] - r, q[0] + r, q[1] + r), fill=col)


def hand_arrow(draws, p0, c1, c2, tip, k):
    """弯箭头：线身带一点手抖、越靠近箭头越稳；箭头是两条一样长的短线，沿线尾切线左右对称各 30°（开口 60°）。"""
    lw = stroke_width(k); n = 60
    pts = [(x + 1.5 * k * math.sin(j / 6) * (1 - j / n), y) for j, (x, y) in enumerate(bezier(p0, c1, c2, tip, n))]
    ang = math.atan2(tip[1] - c2[1], tip[0] - c2[0]); hl = 40 * k        # 三次曲线终点的切线方向就是 c2→tip
    stroke(draws, pts, lw)
    for s in (-math.pi / 6, math.pi / 6):
        stroke(draws, [(tip[0] - hl * math.cos(ang + s), tip[1] - hl * math.sin(ang + s)), tip], lw)
    return pts, math.degrees(ang)


def hand_oval(L, R, cy, ry):
    """手画圈：圆角椭圆（超椭圆 n=3）。左边正好落在 L、不抖（L 取关键词和前一个字之间的空隙中点，不碰前一个字）；
    右边带一点手抖、略往外胀，笔画多绕一小段，两头在右边交叠，像一笔画成。返回 (点列, 取点函数 at(t))。"""
    ecx, rx = (L + R) / 2, (R - L) / 2
    def at(t, grow=1.0):
        c, s = math.cos(t), math.sin(t); wr = (1 + c) / 2                # 左边 0、右边 1
        wob = 1 + wr * (0.02 * math.sin(3 * t + 0.6) + 0.01 * math.sin(7 * t)); g = 1 + (grow - 1) * wr
        return (ecx + rx * wob * g * math.copysign(abs(c) ** (2 / 3), c), cy + ry * wob * g * math.copysign(abs(s) ** (2 / 3), s))
    return [at(-0.9 + j / 160 * (2 * math.pi + 1.25), 1 + 0.025 * j / 160) for j in range(161)], at


def keyword_oval(text, f, S, keyword, x0, y0, k):
    """在一行标题里找关键词，算圈的位置。返回 dict(L, R, cy, ry, keep=(关键词可碰的左右范围)) 或 None。"""
    i = text.find(keyword)
    if i < 0:
        return None
    full = np.asarray(line_img(text, f, S * 0.03, TITLE_INK).getchannel("A")) > 128
    on = full.any(0); cols = np.nonzero(on)[0]
    starts = cols[(cols == 0) | ~on[np.maximum(cols - 1, 0)]]       # 每段墨迹的起点
    def right_of(n): return np.nonzero((np.asarray(line_img(text[:n], f, S * 0.03, TITLE_INK).getchannel("A")) > 128).any(0))[0].max()
    # 下一个字的墨迹起点：取 right_of 之后新起的一段墨迹。前半句单独排时压窄的取整和整行差 1px，以前直接取“右边下一列墨迹”，
    # 会取到这个字自己的最后一列（全角问号这种窄字墨迹后面一大段空，圈就收在问号中间，2026-11-21 用户发现）
    def next_ink(r): return starts[starts > r].min()
    j = i + len(keyword)
    if i > 0:
        zr = right_of(i); L = (zr + next_ink(zr)) / 2               # 前一个字和关键词之间的空隙中点
    else:
        L = cols.min() - 30 * k
    kr = right_of(j)
    if j < len(text):                                              # 后面还有字：右边的抖动和外胀也不能碰到它
        G = (kr + next_ink(kr)) / 2; R = L + (G - L) / 1.056
    else:
        G = None; R = kr + 30 * k
    return dict(L=x0 + L, R=x0 + R, cy=y0 + 84 / 116 * S, ry=82 / 116 * S, keep=(x0 + L, x0 + (G if G is not None else R + 10 * k)))


def title_mask(size, parts):
    m = Image.new("L", size)
    for img, pos in parts:
        m.paste(img.getchannel("A"), pos, img.getchannel("A"))
    return m


def red_on_text(redmask, title_alpha, keep, line_rows):
    """红线压到关键词以外的标题字的像素数。keep = 关键词可碰的左右范围，line_rows = 关键词那一行的上下范围。"""
    R_ = np.asarray(redmask) > 0; T = np.asarray(title_alpha) > 128
    hit = R_ & T
    hit[line_rows[0]:line_rows[1], max(0, int(keep[0])):int(keep[1])] = False
    return int(hit.sum())


def arrow_geom(label, target, W_IMG):
    """2026-10-24 用户画的样子：箭头从色块朝向主角的那一侧水平出发，向外甩出一道大弧，再回钩着指进主角。
    主角在色块左右两侧时从侧边出；主角在色块正下方时从底边出、往右鼓再钩回来。返回贝塞尔四个控制点。"""
    bx, by, bw, bh = label; tx, ty = target
    if tx > bx + bw + 20 or tx < bx - 20:
        sg = 1 if tx > bx + bw else -1
        p0 = (bx + bw + 12 if sg > 0 else bx - 12, by + bh / 2)
        dx, dy = abs(tx - p0[0]), ty - p0[1]
        c1 = (p0[0] + sg * 0.8 * dx, p0[1] - 0.05 * abs(dy))                 # 先水平往外走
        c2 = (tx + sg * max(90, 0.4 * dx), ty - 0.6 * dy)                     # 甩过主角再回钩
    else:
        p0 = (min(max(tx, bx + 40), bx + bw - 40), by + bh + 12)
        dy = ty - p0[1]
        sg = 1 if tx < W_IMG / 2 else -1                                   # 往画面中间鼓，不往边上鼓（避免出界）
        c1 = (p0[0] + sg * 0.45 * dy, p0[1] + 0.1 * dy)
        c2 = (tx + sg * 0.45 * dy, ty - 0.5 * dy)
    return p0, c1, c2, target


# ---------- 2026-11-21（261117 反馈单新5）不给副标题：箭头从标题关键词的红圈出发 ----------
def circle_arrow(o, tip, W, H, k, hits=None, back=0.0, around=None):
    """红箭头从关键词红圈指向 tip：tip 在圈的下方时从圈的右下方出发、往外鼓再钩进去（同本片 render_无副标题.py）；
    tip 在圈的右边、差不多同高时从圈的右边水平出发、拱起再钩进去（同 arrow_block）。控制点夹在画面里。
    around = 主角外框：落点在主角里面（--arrow-at）时，先绕到主角外面空的那一侧，再横着钩进去，不穿过主角（同本片指灯的做法）。
    hits(控制点) → 会不会压到别的字；压到就换下一种走法（弧再往外甩一点），都压到就用第一种、由检查报警。
    back：箭头尖沿来向往回收这么多（落点是主角里的一点时不盖住它）。"""
    at = hand_oval(o["L"], o["R"], o["cy"], o["ry"])[1]
    def down(m=1.0):
        p0 = at(0.62); L = math.hypot(tip[0] - p0[0], tip[1] - p0[1]); sg = 1 if tip[0] >= p0[0] else -1
        return p0, (p0[0] + sg * 0.10 * L, p0[1] + 0.40 * L), (tip[0] + sg * 0.22 * m * L, tip[1] - 0.45 * L)
    def side(m=1.0):
        p0 = at(0.0); p0 = (p0[0] + 12 * k, p0[1]); dx = tip[0] - p0[0]; sg = 1 if dx >= 0 else -1
        dy = max(abs(tip[1] - p0[1]), 0.45 * abs(dx))
        return p0, (p0[0] + sg * (0.55 * abs(dx) + 0.35 * dy), p0[1] - 15 * k), (tip[0] + sg * m * min(140 * k, 0.3 * dy), tip[1] - 0.55 * dy)
    def outside(m=1.0):
        p0 = at(0.62); dy = tip[1] - p0[1]; right = W - around[2] >= around[0]
        x = min(W - 70 * k, around[2] + 90 * m * k) if right else max(70 * k, around[0] - 90 * m * k)
        return p0, (x, p0[1] + 0.10 * dy), (x, tip[1] - 0.20 * dy)
    p = at(0.62)
    if around:
        ways = (outside,)
    else:
        ways = (down, side) if tip[1] - p[1] > 0.6 * abs(tip[0] - p[0]) else (side, down)
    cands = []
    for m in (1.0, 2.0, 3.0):
        for f in ways:
            p0, c1, c2 = f(m)
            c1, c2 = [(min(max(c[0], 70 * k), W - 70 * k), min(max(c[1], 70 * k), H - 70 * k)) for c in (c1, c2)]
            t = tip
            if back:
                L = math.hypot(tip[0] - c2[0], tip[1] - c2[1]) or 1; t = (tip[0] - back * (tip[0] - c2[0]) / L, tip[1] - back * (tip[1] - c2[1]) / L)
            cands.append((p0, c1, c2, t))
    return next((c for c in cands if not (hits and hits(c))), cands[0])


def text_hits(tm, keep, rows, k, W, H, labels=None):
    """给 circle_arrow 用：在空白蒙版上试画箭头，压到关键词以外的标题字（或 labels 里的小标签）就算压到。"""
    lab = np.asarray(title_mask((W, H), labels)) > 128 if labels else None
    def f(ctrl):
        m = Image.new("L", (W, H)); hand_arrow([(ImageDraw.Draw(m), 255)], *ctrl, k)
        return red_on_text(m, tm, keep, rows) > 0 or (lab is not None and bool(np.any((np.asarray(m) > 0) & lab)))
    return f


def point_in(box, at):
    """主角上色部分（外框 x0,y0,x1,y1）里 --arrow-at 指的点。"""
    return (box[0] + at[0] * (box[2] - box[0]), box[1] + at[1] * (box[3] - box[1]))


# ---------- 2026-11-21（261117 反馈单新6）画被画面边缘裁掉就缩小上提；横版复用竖版画 ----------
def ink_mask(arr, paper=None):
    paper = np.median(arr[: arr.shape[0] // 10].reshape(-1, 3), axis=0) if paper is None else paper
    return np.abs(arr.astype(int) - paper).max(axis=2) > 40, paper


def art_cut(src, base):
    """排好的底图里整幅画或主角有没有被画面边缘裁掉。只看底图里画的四周本来留着纸边的那几侧
    （场景本来就画到图边的，比如满幅的厨房，那一侧不算）。返回被裁的说明列表。"""
    a = np.asarray(src); m, paper = ink_mask(a); ys, xs = np.nonzero(m)
    if not len(xs):
        return []
    sh, sw = m.shape; pad = 0.01 * min(sw, sh)
    open_ = {"左": xs.min() >= pad, "上": ys.min() >= pad, "右": sw - 1 - xs.max() >= pad, "下": sh - 1 - ys.max() >= pad}
    b, _ = ink_mask(np.asarray(base), paper)
    strip = {"左": b[:, :3], "上": b[:3], "右": b[:, -3:], "下": b[-3:]}
    out = [f"画的{s}边" for s, ok in open_.items() if ok and strip[s].mean() > 0.002]
    s0, s1 = subject_box(a), subject_box(np.asarray(base))
    if s0 and s1:
        H, W = b.shape
        src_touch = (s0[0] <= 8, s0[1] <= 8, s0[2] >= sw - 8, s0[3] >= sh - 8)
        now_touch = (s1[0] <= 8, s1[1] <= 8, s1[2] >= W - 8, s1[3] >= H - 8)
        if any(n and not o for n, o in zip(now_touch, src_touch)):
            out.append("主角")
    return out


def lay_drawing(src, W, H, regions, cap, floor=0.0):
    """把底图里的整幅画（墨迹外框）抠出来，等比缩放放进 regions 里放得最大的那个：水平居中、贴区域底边，四周补纸色。
    正片叠底贴上去，画里的留白比纸面亮一点也不显方块。cap = 最大缩放倍数，floor = 最小（放不下也不再缩）。返回 (底图, 缩放倍数, 放得下)。"""
    a = np.asarray(src); m, paper = ink_mask(a); ys, xs = np.nonzero(m); paper = tuple(int(v) for v in paper)
    art = src.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    sh_, sw_ = m.shape; edges = (xs.min() <= 3, ys.min() <= 3, xs.max() >= sw_ - 4, ys.max() >= sh_ - 4)
    if any(edges):   # 场景画到了图边：缩小后那一侧会露出硬边，用约 8% 的宽度渐渐淡成纸色（同数字大字）；陶土色不淡，免得换色后留粉边
        f = np.ones(art.size[::-1], np.float32); hh, ww = f.shape; rx, ry = max(1, round(0.08 * ww)), max(1, round(0.08 * hh))
        ramp_x, ramp_y = np.linspace(0, 1, rx, dtype=np.float32), np.linspace(0, 1, ry, dtype=np.float32)
        if edges[0]: f[:, :rx] *= ramp_x
        if edges[2]: f[:, -rx:] *= ramp_x[::-1]
        if edges[1]: f[:ry] *= ramp_y[:, None]
        if edges[3]: f[-ry:] *= ramp_y[::-1, None]
        q = np.asarray(art).astype(np.float32); r_, b_ = q[..., 0], q[..., 2]
        f = np.where((r_ - b_ > 30) & (r_ > 120), 1.0, f)[..., None]
        art = Image.fromarray((q * f + np.array(paper, np.float32) * (1 - f)).round().astype(np.uint8))
    s, r = max((min((r[2] - r[0]) / art.width, (r[3] - r[1]) / art.height, cap), r) for r in regions)
    fits = s >= floor; s = max(s, floor)
    q = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.LANCZOS)
    im = Image.new("RGB", (W, H), paper)
    paste_art(im, q, r[0] + (r[2] - r[0] - q.width) // 2, r[3] - q.height, paper)
    return im, s, fits


def min_frac(src, scale, W, H):
    """底图缩放 scale 倍排进 W×H 后，认主角的面积门槛：按底图原来的 0.4% 换算到成图（横版复用竖版画、缩小上提时画变小，门槛跟着降），不高于原门槛。"""
    return min(SUBJECT_MIN, SUBJECT_MIN * scale * scale * src.width * src.height / (W * H))


LIFT_MIN = 0.6     # 缩小上提最多缩到原来的 6 成，再小就报警（画太小看不清）


def render_one(spec, bg, ratio, out, ts=None):
    W, H, _ = RATIOS[ratio]
    short = min(W, H); M = round(short * 0.075); k = short / 1080
    qc = {"ratio": ratio, "warnings": []}
    # title: ink sits exactly M from the left and the top
    ts = TEXT_SCALE.get(ratio, 1.0) if ts is None else ts       # 16:9 封面图小时字不显眼 → 放大
    S = title_px(spec, round(116 * k * ts))
    while True:
        ls = title_imgs(spec, S)                                # 主标题各行（两行或三行）
        if max(l.width for l in ls) <= W - 2 * M or S < 60 * k:
            break
        S -= 4
    x = M - min(ink(l)[0] for l in ls); ys = [M - ink(ls[0])[1] + i * round(S * 1.3) for i in range(len(ls))]
    title_box = (M, M, max(x + ink(l)[2] for l in ls), ys[-1] + ink(ls[-1])[3])
    if title_box[2] > W - M:                                    # 缩到下限还放不下：字会顶到甚至超出画面右边
        qc["warnings"].append(f"主标题太长，缩到最小字号（{S}px）仍超出右边距，请改短")
    oval = find_oval(spec, S, x, ys, k)                         # 圈住标题关键词（红橙手画）；色块和箭头躲开圈
    if circle_word(spec):
        if oval:
            title_box = (title_box[0], title_box[1], max(title_box[2], round(oval["R"] * 1.03)), max(title_box[3], round(oval["cy"] + oval["ry"])))
    src = Image.open(bg).convert("RGB"); e = round(0.04 * short)
    need = title_box[3] + round(0.9 * S)                       # about one character of air between title and drawing
    if W > H and src.height > src.width:
        # 2026-11-21（新6）横版复用用户确认过的竖版画：整幅画抠出来，放在标题下面或标题右边，挑画放得大的
        base, sc_, _ = lay_drawing(src, W, H, [(e, need, W - e, H - e), (title_box[2] + S, e, W - e, H - e)], max(W / src.width, H / src.height))
        qc["reused_portrait"] = round(sc_, 3); busy = busy_mask(np.asarray(base)); scale = sc_
    else:
        base = fit(src, W, H); busy = busy_mask(np.asarray(base)); scale = max(W / src.width, H / src.height)
        rows = np.nonzero(busy[:, title_box[0]:title_box[2]].mean(axis=1) > 0.005)[0]
        top = int(rows[0]) if len(rows) else H
        if top < need and ratio in TEXT_SCALE:                     # 放大字号的比例：先把字缩到不需要下移太多（最低回到 1 倍）
            while top < need - round(0.12 * H) and ts > 1.0:
                ts = round(ts - 0.05, 2); return render_one(spec, bg, ratio, out, ts)
        if top < need:   # slide the drawing down; the new strip is the mirrored empty paper above it, so no seam
            sb0 = subject_box(np.asarray(base), min_frac(src, scale, W, H))
            room = H - 10 - sb0[3] if sb0 else H                     # 下移不能把主角推出画面底边
            shift = max(0, min(need - top, round(0.2 * H), top, room))
            base = Image.fromarray(np.vstack([np.asarray(base)[:shift][::-1], np.asarray(base)[:H - shift]]))
            qc["shifted_down"] = shift; busy = busy_mask(np.asarray(base))
        cut = art_cut(src, base)
        if cut:      # 2026-11-21（新6）画或主角被画面边缘裁掉：整幅画抠出来缩小、上提到标题下面（最多缩到 6 成）
            f = max(W / src.width, H / src.height)
            base, sc_, fits = lay_drawing(src, W, H, [(e, need, W - e, H - e)], f, LIFT_MIN * f)
            qc["lifted"] = {"cut": cut, "scale": round(sc_ / f, 3)}; busy = busy_mask(np.asarray(base)); scale = sc_
            still = art_cut(src, base)
            if still or not fits:
                qc["warnings"].append(f"{'、'.join(cut)}被画面边缘裁掉，缩到 {LIFT_MIN:.0%} 仍放不下，检查成图；必要时重出底图（画要紧凑、四周留纸边）")
    arr = np.asarray(base)                                      # 识别主角用原陶土色
    im = to_blue(base).convert("RGBA"); d = ImageDraw.Draw(im)
    parts = [(l, (x, y)) for l, y in zip(ls, ys)]
    for l, pos in parts:
        im.alpha_composite(l, pos)
    tb = busy[title_box[1]:need, title_box[0]:title_box[2]]
    if tb.mean() > 0.005:
        qc["warnings"].append(f"标题下方留白不够（{tb.mean():.1%} 被画面占用），可能压到插图")
    # label + arrow to the coloured subject
    # 副标题：2026-10-24 用户：细仿宋手机上看不清、发虚 → 黑体（冬青黑体 W6）60px，不压窄（压窄重采样会发虚）
    # 2026-10-31 用户：字要填满深蓝色块，白字和蓝底面积差不多 → 字号 60→76、字距收紧、按实际字墨留窄边
    fs = 76 * k * ts; sub = has_sub(spec); bw = bh = 0
    while sub:                                    # 字多时整体缩小，色块不超出左右边距
        # 10-31 用户第二次：字几乎顶到色块边、笔画再粗 → W6 外加白描边加粗，四周只留很窄的边
        s = line_img(spec["sub"], font(HEI, round(fs), 2), fs * 0.08, CREAM, stroke=max(1, round(fs * 0.016)), squeeze=1)
        s = s.crop(ink(s)); padx, pady = round(fs * 0.05), round(fs * 0.045)
        bw, bh = s.width + 2 * padx, s.height + 2 * pady
        if bw <= W - 2 * M or fs < 40 * k: break
        fs *= 0.95
    if bw > W - 2 * M:
        qc["warnings"].append(f"副标题太长，缩到最小字号（{round(fs)}px）色块仍超出左右边距，请改短")
    box = subject_box(arr, min_frac(src, scale, W, H), (oval["R"], oval["cy"]) if oval and not sub else None, small=bool(spec.get("small_subject"))); qc["subject_box"] = box
    if box and (box[3] >= H - 8 or box[0] <= 8 or box[2] >= W - 8):
        qc["warnings"].append("主角被画面边缘裁掉了一部分，这个比例的底图需要重出")
    at = spec.get("arrow_at")                                   # 2026-11-21 箭头落点可指定（主角上色部分里的位置）
    if box is None:
        qc["warnings"].append("没找到陶土色主角：底图没按要求给主角上色，建议重出底图" + ("" if spec.get("small_subject") else "；" + SMALL_HINT))
        lx, ly = W - M - bw, title_box[3] + round(0.6 * S); target = None
    elif not sub:                                 # 不给副标题：没有色块，箭头从关键词红圈出发（下面画）
        lx = ly = None; target = point_in(box, at) if at else ((box[4] + box[5]) // 2, box[1] - 14)
    else:
        cand = []
        tops = (("right", box[5] + 12), ("left", box[4] - 12), ("centre", (box[4] + box[5]) // 2))   # top corners / top centre
        for side, tx in ((("at", None),) if at else tops):
            t = point_in(box, at) if at else (tx, box[1] - 14)
            p = place_label(busy, title_box, t, bw, bh, W, H, M)
            if p:
                sc, bx_, by_ = p
                wrong_side = (side == "right" and bx_ + bw / 2 < tx) or (side == "left" and bx_ + bw / 2 > tx)
                cand.append((sc + (60 if wrong_side else 0) + (20 if side == "centre" else 0), (bx_, by_), t))
        if cand:
            # 10-31：先试画箭头，伸出画面的位置不要，选下一个
            def inside(c):
                pts = bezier(*arrow_geom((c[1][0], c[1][1], bw, bh), c[2], W))
                return min(x for x, _ in pts) >= 0.5 * M and max(x for x, _ in pts) <= W - 0.5 * M and min(y for _, y in pts) >= 0.5 * M and max(y for _, y in pts) <= H - 0.5 * M
            cand.sort(key=lambda c: c[0])
            ok = [c for c in cand if inside(c)]
            if not ok:                                   # 都出界：放标题下方靠左，箭头指主角顶部中间
                fb = (0, (M, title_box[3] + round(0.5 * S)), ((box[4] + box[5]) // 2, box[1] - 14))
                ok = [fb] if inside(fb) else [cand[0]]
            _, (lx, ly), target = ok[0]
        else:
            qc["warnings"].append("画面里找不到放副标题色块的空位，已放在标题下方，箭头可能穿过画面")
            lx, ly = M, title_box[3] + round(0.6 * S); target = (box[4] + box[5]) // 2, box[1] - 14
    if sub:
        d.rectangle((lx, ly, lx + bw, ly + bh), fill=TITLE_INK)
        im.alpha_composite(s, (lx + padx, ly + pady))
    redmask = Image.new("L", (W, H)); draws = [(d, RED), (ImageDraw.Draw(redmask), 255)]
    if oval:
        stroke(draws, hand_oval(oval["L"], oval["R"], oval["cy"], oval["ry"])[0], stroke_width(k))
        qc["red_on_other_text"] = red_on_text(redmask, title_mask((W, H), parts), oval["keep"], oval["rows"])
        if qc["red_on_other_text"]:
            qc["warnings"].append("红圈压到了关键词以外的字")
    elif circle_word(spec):
        qc["warnings"].append(f"标题里找不到要圈的「{circle_word(spec)}」")
    if target and sub:   # 2026-11-12 起箭头改红橙手画（原深蓝实心三角）
        pts, _ = hand_arrow(draws, *arrow_geom((lx, ly, bw, bh), target, W), k)
    elif target and oval:
        tm = title_mask((W, H), parts)
        ctrl = circle_arrow(oval, target, W, H, k, text_hits(tm, oval["keep"], oval["rows"], k, W, H), 14 * k if at else 0, box[:4] if at else None)
        pts, _ = hand_arrow(draws, *ctrl, k); target = tuple(round(v) for v in ctrl[3])
        qc["red_on_other_text"] = red_on_text(redmask, tm, oval["keep"], oval["rows"])
        if qc["red_on_other_text"]:
            qc["warnings"].append("红圈或箭头压到了关键词以外的字")
    if target and (sub or oval):
        if min(x for x, _ in pts) < 0.5 * M or max(x for x, _ in pts) > W - 0.5 * M or max(y for _, y in pts) > H - 0.5 * M or min(y for _, y in pts) < 0.5 * M:
            qc["warnings"].append("箭头弧线伸出画面边缘，检查成图；必要时换底图构图")
    qc.update(title_px=S, margin=M, label=(lx, ly, bw, bh) if sub else None, target=target)
    im.convert("RGB").save(out, quality=93)
    return qc


# ---------- 多主角：四宫格（2026-11-12 水电那期定稿） ----------
# 版式：副标题色块在左上，两行宋体标题在它下面，红橙手画圈圈住标题关键词，弯箭头从圈的右下方出发指向格子；
# 格子紧凑：圆角外框 + 细分隔线，每格左上写 ①… 两行黑体标签，下面放这一格的小图。
# 一律 2×2。3:4 固定上文字下格子；横版在“文字在上/在左 × 标签在格子上方/左边”里挑小图最大的排法。
def grid_arts(bg):
    """四宫格底图按四等分切成四张小图，再裁到墨迹边，陶土色换蓝。返回 (小图, 纸色, 警告)。"""
    src = Image.open(bg).convert("RGB"); sw, sh = src.size; a = np.asarray(src).astype(int)
    paper = tuple(int(v) for v in np.median(a[: sh // 10].reshape(-1, 3), axis=0))
    m = np.abs(a - paper).max(axis=2) > 40
    warns, arts, band = [], [], max(3, sh // 200)          # 只看正中间一窄条：有画就说明画跨过了切线
    if m[:, sw // 2 - band: sw // 2 + band].mean() > 0.005 or m[sh // 2 - band: sh // 2 + band].mean() > 0.005:
        warns.append("四张小图之间没留空，切格时可能把画切开，建议重出底图")
    for i in range(4):
        x0, y0 = (i % 2) * sw // 2, (i // 2) * sh // 2
        ys, xs = np.nonzero(m[y0: y0 + sh // 2, x0: x0 + sw // 2])
        if not len(xs):
            sys.exit(f"四宫格底图第 {i + 1} 格是空的，要重出底图")
        q = src.crop((x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1))
        if subject_box(np.asarray(q)) is None:
            warns.append(f"第 {i + 1} 格没找到陶土色部分（这一格不会上蓝色）")
        arts.append(to_blue(q))
    return arts, paper, warns


def grid_text(spec, S):
    """副标题色块 + 主标题各行（两行或三行）+ 圈的位置，坐标相对色块左上角。l2_bottom 是最后一行墨迹的底。
    k 按传进来的字号算（副标题、线宽、间距都跟它走）；返回的 S 是主标题实际字号（三行时略小）。"""
    S = round(S); k = S / 116; fs = 76 * k; S = title_px(spec, S)
    s, bw, bh, padx, pady = sub_block(spec["sub"], fs)
    ls = title_imgs(spec, S)
    top = bh + round(70 * k) if s else 10          # 不给副标题：没有色块，标题墨迹顶在边距上（文字区上沿是 M-10）
    ax = -min(ink(l)[0] for l in ls); ays = [top - ink(ls[0])[1] + i * round(S * 1.3) for i in range(len(ls))]
    oval = find_oval(spec, S, ax, ays, k)                          # 三步流程、数字大字可以不圈
    if oval is None and circle_word(spec):
        sys.exit(f"标题里找不到要圈的「{circle_word(spec)}」")
    return dict(s=s, ls=ls, S=S, k=k, bw=bw, bh=bh, padx=padx, pady=pady, ax=ax, ays=ays, oval=oval,
                width=round(max(bw, *(ax + ink(l)[2] for l in ls), oval["R"] + 4 * k if oval else 0)),
                height=round(ays[-1] + ink(ls[-1])[3]), l2_bottom=ays[-1] + ink(ls[-1])[3])


def text_parts(g, ox, oy):
    """grid_text 的主标题各行贴在画面上的位置：[(行图, (x, y)), …]。"""
    return [(l, (ox + g["ax"], oy + y)) for l, y in zip(g["ls"], g["ays"])]


def cell_labels(spec, tf, numbered=True):
    out = []
    for i, c in enumerate(spec["cells"]):
        parts = c["label"].split("|")
        if numbered and parts[0][:1] not in CIRCLED:          # 左右对比不加 ①②
            parts[0] = f"{CIRCLED[i]} {parts[0]}"
        out.append([line_img(t, font(HEI, tf, 2), 1, TITLE_INK, squeeze=1) for t in parts])
    return out


def cell_boxes(gx0, gy0, gx1, gy1, cols, rows, n=4):
    xs = [round(gx0 + (gx1 - gx0) * c / cols) for c in range(cols + 1)]
    ys = [round(gy0 + (gy1 - gy0) * r / rows) for r in range(rows + 1)]
    return [(xs[i % cols], ys[i // cols], xs[i % cols + 1], ys[i // cols + 1]) for i in range(n)], xs, ys


def art_area(cell, mode, tf, lab):
    cx, cy, ex, ey = cell
    if mode == "top":   # 标签在格子上方，小图在下面
        return (cx + 18, cy + 22 + (len(lab) - 1) * round(tf * 1.32) + tf + 14, ex - 18, ey - 16)
    return (cx + 24 + round(tf * 5.4) + 14, cy + 18, ex - 16, ey - 16)   # 标签在左边一栏（约 5 个字宽），小图在右边；压字由 render 检查


def grid_plan(spec, arts, W, H, M, k):
    e = round(40 * k); best = None
    if H > W:
        opts = [("top", ts, (2, 2), "top") for ts in (1.0, 0.9, 0.8, 0.7)]
    else:
        opts = [(sd, ts, cr, md) for sd in ("left", "top") for ts in (1.0, 0.9, 0.8, 1.2, 1.3)
                for cr in ((2, 2),) for md in ("top", "side")]      # 只排 2×2：水电那期横排 4×1 被否
    for side, ts, (cols, rows), mode in opts:
        g = grid_text(spec, 116 * k * ts)
        if side == "left":
            gx0, gy0, gx1, gy1 = M + g["width"] + round(110 * g["k"]), e, W - e, H - e
            if g["height"] + M > H - M:
                continue
        else:
            gx0, gx1, gy0, gy1 = e, W - e, M - 10 + g["height"] + round(95 * g["k"]), H - e
            if g["width"] + M > W - M:
                continue
        if gx1 - gx0 < 300 * k or gy1 - gy0 < 250 * k:
            continue
        tf = round((38 if ts >= 1 else 34) * k); labs = cell_labels(spec, tf)
        cells = cell_boxes(gx0, gy0, gx1, gy1, cols, rows)[0]; sc = []
        for c, art, lab in zip(cells, arts, labs):
            x0, y0, x1, y1 = art_area(c, mode, tf, lab)
            sc.append(0 if x1 - x0 < 50 or y1 - y0 < 50 else min((x1 - x0) / art.width, (y1 - y0) / art.height))
        area = min(s_ ** 2 * art.width * art.height for s_, art in zip(sc, arts))
        plan = dict(side=side, ts=ts, cols=cols, rows=rows, mode=mode, gx0=gx0, gy0=gy0, gx1=gx1, gy1=gy1, tf=tf)
        if H > W:
            return plan                                   # 竖版：字号能保持就保持
        score = area * (1 + 0.15 * min(ts, 1.2))          # 横版：小图最大，标题大一点略加分
        if area and (best is None or score > best[0]):
            best = (score, plan)
    return best and best[1]


def render_grid(spec, bg, ratio, out):
    W, H, _ = RATIOS[ratio]; short = min(W, H); M = round(short * 0.075); k = short / 1080
    arts, paper, warns = grid_arts(bg)
    qc = {"ratio": ratio, "layout": "grid", "warnings": list(warns)}
    p = grid_plan(spec, arts, W, H, M, k)
    if p is None:
        sys.exit(f"{ratio} 四宫格排不下：主标题或副标题太长，改短再出")
    g = grid_text(spec, 116 * k * p["ts"]); gk = g["k"]
    im = Image.new("RGBA", (W, H), paper + (255,)); d = ImageDraw.Draw(im)
    ox, oy = M, M - 10
    if p["side"] == "left":                                # 文字在左：整块文字竖直居中略偏上
        oy = max(M - 10, (H - g["height"]) // 2 - round(60 * gk))
    tm = draw_text_block(im, d, g, ox, oy)
    o = g["oval"]
    redmask = Image.new("L", (W, H)); draws = [(d, RED), (ImageDraw.Draw(redmask), 255)]
    gx0, gy0, gx1, gy1 = p["gx0"], p["gy0"], p["gx1"], p["gy1"]
    pts, ctrl = oval_arrow(g, ox, oy, p["side"], gx0, gy0, gx1, k, gy1)     # 从圈的右下方出发指向格子（和实拍四宫格同一套）
    stroke(draws, pts, stroke_width(gk)); tip = ctrl[3]
    ap, head = hand_arrow(draws, *ctrl, gk)
    cells, xs, ys = cell_boxes(gx0, gy0, gx1, gy1, p["cols"], p["rows"])
    d.rounded_rectangle((gx0, gy0, gx1, gy1), radius=round(18 * k), outline=TITLE_INK, width=max(3, round(4 * k)))
    for x_ in xs[1:-1]:
        d.line((x_, gy0 + 2, x_, gy1 - 2), fill=TITLE_INK, width=max(2, round(2 * k)))
    for y_ in ys[1:-1]:
        d.line((gx0 + 2, y_, gx1 - 2, y_), fill=TITLE_INK, width=max(2, round(2 * k)))
    labs = cell_labels(spec, p["tf"]); pitch = round(p["tf"] * 1.32); qc["cells"] = []
    for i, (c, art, lab) in enumerate(zip(cells, arts, labs)):
        cx, cy, ex, ey = c
        for j, t in enumerate(lab):
            bb = ink(t); im.alpha_composite(t, (cx + round(24 * k) - bb[0], cy + round(22 * k) + j * pitch - bb[1]))
            if p["mode"] == "top" and bb[2] - bb[0] > ex - cx - 48 * k:
                qc["warnings"].append(f"第 {i + 1} 格标签太长，超出格子")
        x0, y0, x1, y1 = art_area(c, p["mode"], p["tf"], lab)
        sc = min((x1 - x0) / art.width, (y1 - y0) / art.height)
        q = art.resize((round(art.width * sc), round(art.height * sc)), Image.LANCZOS)
        px = x0 + (x1 - x0 - q.width) // 2; py = y1 - q.height if p["mode"] == "top" else y0 + (y1 - y0 - q.height) // 2
        reg = np.asarray(im.crop((px, py, px + q.width, py + q.height)).convert("RGB")).astype(float)
        mul = np.minimum(np.asarray(q).astype(float) / np.array(paper), 1)    # 正片叠底：小图的纸面不会显出方块
        im.paste(Image.fromarray(np.clip(reg * mul, 0, 255).astype(np.uint8)), (px, py))
        lab_box = (cx + 24 * k, cy + 22 * k, cx + 24 * k + max(ink(t)[2] - ink(t)[0] for t in lab), cy + 22 * k + (len(lab) - 1) * pitch + p["tf"])
        art_ink = np.nonzero(np.asarray(q.convert("L")) < 200)
        if len(art_ink[0]) and px + art_ink[1].min() < lab_box[2] + 6 and py + art_ink[0].min() < lab_box[3] + 6 \
                and np.any((px + art_ink[1] < lab_box[2] + 6) & (py + art_ink[0] < lab_box[3] + 6)):
            qc["warnings"].append(f"第 {i + 1} 格小图压到了标签")
        qc["cells"].append({"cell": i + 1, "art": [px, py, q.width, q.height], "fill_w": round(q.width / (ex - cx), 2), "fill_h": round(q.height / (ey - cy), 2)})
    qc["red_on_other_text"] = red_on_text(redmask, tm, (ox + o["keep"][0], ox + o["keep"][1]), (oy + o["rows"][0], oy + o["rows"][1]))
    if qc["red_on_other_text"]:
        qc["warnings"].append("红圈或箭头压到了关键词以外的字")
    if (p["side"] == "top" and not tip[1] < gy0 - 2) or (p["side"] == "left" and not tip[0] < gx0 - 2):
        qc["warnings"].append("箭头尖进了格子框")
    allp = ap + pts
    if min(x for x, _ in allp) < 10 or max(x for x, _ in allp) > W - 10 or min(y for _, y in allp) < 10:
        qc["warnings"].append("红线出画面")
    qc.update(plan=p, title_px=g["S"], margin=M, tip=[round(tip[0]), round(tip[1])], head_dir_deg=round(head, 1),
              oval=[round(ox + o["L"]), round(ox + o["R"]), round(oy + o["cy"]), round(o["ry"])])
    im.convert("RGB").save(out, quality=93)
    return qc


# ---------- 2026-11-14 四种新版式（用户在 Design 画板上确认“四种都要”） ----------
# 风格不变，只加摆法：compare 左右对比（先错后对）、number 数字大字、steps 三步流程、zoom 局部放大（先整体后特写）。
def ts_options(W, H, ratio):
    """标题字号倍数的候选：竖版从大往小试，取第一个排得下的；横版都试，挑小图最大的（同四宫格），16:9 多试一个 1.45 倍。"""
    if H > W:
        return (1.0, 0.9, 0.8, 0.7)
    return (1.0, 0.9, 0.8, 1.2, 1.3) + ((TEXT_SCALE["16:9"],) if ratio == "16:9" else ())


def to_grey(img, paper, keep=0.35):
    """去色（“错的”那张、局部放大的整体图）：陶土色部分换成和纸色同色调的浅灰，不上蓝；keep 越小越淡，0 就是纸色、只剩灰线。
    哪些算陶土色和 to_blue 的判断一样，抗锯齿边缘同样按饱和度渐变。"""
    hsv = np.asarray(img.convert("HSV")).astype(np.float32) / 255
    h, s_ = hsv[..., 0], hsv[..., 1]
    w = (np.clip((s_ - 0.14) / 0.12, 0, 1) * (((h < 45 / 360) | (h > 345 / 360)) & (s_ > 0.14)))[..., None]
    rgb = np.asarray(img.convert("RGB")).astype(np.float32)
    lum = (rgb @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
    grey = np.array(paper, np.float32) * (1 - keep * (1 - lum / 255))
    return Image.fromarray(np.clip(rgb * (1 - w) + grey * w, 0, 255).round().astype(np.uint8))


def strip_arts(bg, n, name, blue):
    """横版底图按 n 等分竖切（左右对比、局部放大 2 张，三步流程 3 张），每张裁到墨迹边。
    blue = 要上蓝的那几张的序号，只检查它们有没有陶土色。返回 (原色小图, 纸色, 警告)；换蓝还是去色由各版式决定。"""
    src = Image.open(bg).convert("RGB"); sw, sh = src.size; a = np.asarray(src).astype(int)
    paper = tuple(int(v) for v in np.median(a[: sh // 10].reshape(-1, 3), axis=0))
    m = np.abs(a - paper).max(axis=2) > 40
    warns, arts, band = [], [], max(3, sh // 200)
    cuts = [sw * i // n for i in range(n + 1)]
    if any(m[:, c - band: c + band].mean() > 0.005 for c in cuts[1:-1]):
        warns.append(f"{n} 张小图之间没留空，切开时可能把画切开，建议重出底图")
    for i in range(n):
        ys, xs = np.nonzero(m[:, cuts[i]: cuts[i + 1]])
        if not len(xs):
            sys.exit(f"{name}底图第 {i + 1} 张是空的，要重出底图")
        q = src.crop((cuts[i] + xs.min(), ys.min(), cuts[i] + xs.max() + 1, ys.max() + 1))
        if i in blue and subject_box(np.asarray(q)) is None:
            warns.append(f"第 {i + 1} 张没找到陶土色部分（这一张不会上蓝色）")
        arts.append(q)
    return arts, paper, warns


def paste_art(im, q, px, py, paper):
    """正片叠底贴小图：小图的纸面不会显出方块（同四宫格）。"""
    reg = np.asarray(im.crop((px, py, px + q.width, py + q.height)).convert("RGB")).astype(float)
    mul = np.minimum(np.asarray(q.convert("RGB")).astype(float) / np.array(paper), 1)
    im.paste(Image.fromarray(np.clip(reg * mul, 0, 255).astype(np.uint8)), (px, py))


def wobble_line(p, q, k, bow, phase, n=40):
    """手画直线：中段略弯（bow 像素），沿法线方向带一点手抖。"""
    L = math.hypot(q[0] - p[0], q[1] - p[1]) or 1; nx, ny = -(q[1] - p[1]) / L, (q[0] - p[0]) / L; pts = []
    for j in range(n + 1):
        t = j / n; off = bow * math.sin(math.pi * t) + 1.6 * k * math.sin(j / 3 + phase)
        pts.append((p[0] + (q[0] - p[0]) * t + nx * off, p[1] + (q[1] - p[1]) * t + ny * off))
    return pts


def hand_cross(draws, cx, cy, size, k):
    """红橙手画叉（左右对比“错的”那张）：两笔，略弯带一点手抖，线宽同箭头。返回所有点。"""
    h = size / 2; lw = stroke_width(k)
    a = wobble_line((cx - h, cy - h), (cx + h, cy + h * 0.94), k, 0.04 * size, 0.0)
    b = wobble_line((cx + h * 0.96, cy - h * 1.02), (cx - h * 0.98, cy + h), k, -0.035 * size, 1.7)
    stroke(draws, a, lw); stroke(draws, b, lw)
    return a + b


def hand_underline(draws, x0, x1, y, k):
    """数字下面的红橙手画下划线：一笔往右划过去、再往回带一段，略抖（照画板上的样子）。"""
    w = x1 - x0; v = 1.6 * k
    go = bezier((x0 + 0.013 * w, y + 16 * v), (x0 + 0.233 * w, y + 6 * v), (x0 + 0.5 * w, y + 22 * v), (x0 + 0.987 * w, y + 10 * v), 50)
    back = bezier((x0 + 0.987 * w, y + 10 * v), (x0 + 0.767 * w, y + 18 * v), (x0 + 0.5 * w, y + 26 * v), (x0 + 0.2 * w, y + 24 * v), 40)[1:]
    pts = [(px, py + 1.2 * k * math.sin(j / 4)) for j, (px, py) in enumerate(go + back)]
    stroke(draws, pts, stroke_width(k))
    return pts


def sub_block(text, fs):
    """副标题：米白黑体 + 描边加粗，四周窄边（和单主角、四宫格同一套数）。返回 (字图, 色块宽, 色块高, 左右边, 上下边)。
    不给副标题时（2026-11-21）返回 (None, 0, 0, 0, 0)：不画色块、不占位。"""
    if not str(text or "").strip():
        return None, 0, 0, 0, 0
    s = line_img(text, font(HEI, round(fs), 2), fs * 0.08, CREAM, stroke=max(1, round(fs * 0.016)), squeeze=1)
    s = s.crop(ink(s)); padx, pady = round(fs * 0.05), round(fs * 0.045)
    return s, s.width + 2 * padx, s.height + 2 * pady, padx, pady


def draw_text_block(im, d, g, ox, oy):
    """副标题色块在左上、主标题各行在它下面（四宫格那套文字区，grid_text 算好的位置）。返回标题蒙版。"""
    if g["s"]:                                     # 不给副标题时没有色块
        d.rectangle((ox, oy, ox + g["bw"], oy + g["bh"]), fill=TITLE_INK); im.alpha_composite(g["s"], (ox + g["padx"], oy + g["pady"]))
    parts = text_parts(g, ox, oy)
    for l, pos in parts:
        im.alpha_composite(l, pos)
    return title_mask(im.size, parts)


def red_out(pts, W, H, pad=10):
    return min(x for x, _ in pts) < pad or max(x for x, _ in pts) > W - pad or min(y for _, y in pts) < pad or max(y for _, y in pts) > H - pad


# ----- A 左右对比、C 三步流程：文字区同四宫格，下面圆角外框分格 -----
def panel_layout(p, labs, k, n, arrow_room):
    """每格的标签位置、标签框和小图区域。mode=top：标签在格子左上、小图在下面；mode=side：标签在左边一栏、竖直居中，小图在右边。
    arrow_room：三步流程横排时格子左右给小箭头留的空。"""
    cells = cell_boxes(p["gx0"], p["gy0"], p["gx1"], p["gy1"], p["cols"], p["rows"], n)[0]
    tf = p["tf"]; pitch = round(tf * 1.32); out = []
    lws = [max(ink(t)[2] - ink(t)[0] for t in lab) for lab in labs]
    for c, lab, lw in zip(cells, labs, lws):
        cx, cy, ex, ey = c; lh = (len(lab) - 1) * pitch + tf
        if p["mode"] == "top":
            lx, ly = cx + round(24 * k), cy + round(22 * k)
            art = (cx + round(18 * k) + arrow_room, ly + lh + round(14 * k), ex - round(18 * k) - arrow_room, ey - round(16 * k))
        else:
            lx, ly = cx + round(24 * k), (cy + ey - lh) // 2
            art = (lx + max(lws) + round(28 * k), cy + round(16 * k), ex - round(16 * k), ey - round(16 * k))
        out.append(dict(cell=c, lab=(lx, ly), lab_box=(lx, ly, lx + lw, ly + lh), art=art))
    return out


def panel_plan(spec, arts, ratio, W, H, M, k):
    lay = spec["layout"]; n = len(arts); e = round(40 * k); best = None
    if H > W:
        configs = [("top", 2, 1, "top")] if lay == "compare" else [("top", 1, 3, "side")]
    elif lay == "compare":       # 左右对比始终 1 行 2 列、文字在上：文字在左时箭头要横穿“错的”那格才能指到右格
        configs = [("top", 2, 1, "top"), ("top", 2, 1, "side")]
    else:
        configs = [(sd, c, r, md) for sd in ("left", "top") for c, r, md in ((1, 3, "side"), (3, 1, "top"))]
    for ts in ts_options(W, H, ratio):
        g = grid_text(spec, 116 * k * ts)
        tf = round((38 if ts >= 1 else 34) * k); labs = cell_labels(spec, tf, numbered=lay != "compare")
        for side, cols, rows, mode in configs:
            if side == "left":
                gx0, gy0, gx1, gy1 = M + g["width"] + round(110 * g["k"]), e, W - e, H - e
                if g["height"] + M > H - M:
                    continue
            else:
                gx0, gx1, gy0, gy1 = e, W - e, M - 10 + g["height"] + round(95 * g["k"]), H - e
                if g["width"] + M > W - M:
                    continue
            if gx1 - gx0 < 300 * k or gy1 - gy0 < 250 * k:
                continue
            p = dict(side=side, ts=ts, cols=cols, rows=rows, mode=mode, gx0=gx0, gy0=gy0, gx1=gx1, gy1=gy1, tf=tf)
            room = round(44 * k) if lay == "steps" and cols > 1 else 0
            sc = []
            for c, art in zip(panel_layout(p, labs, k, n, room), arts):
                x0, y0, x1, y1 = c["art"]
                sc.append(0 if x1 - x0 < 50 or y1 - y0 < 50 else min((x1 - x0) / art.width, (y1 - y0) / art.height))
            area = min(s_ ** 2 * art.width * art.height for s_, art in zip(sc, arts))
            if not area:
                continue
            if H > W:
                return p                                    # 竖版：字号能保持就保持
            score = area * (1 + 0.15 * min(ts, 1.2))        # 横版：小图最大，标题大一点略加分（同四宫格）
            if best is None or score > best[0]:
                best = (score, p)
    return best and best[1]


def render_panels(spec, bg, ratio, out):
    lay = spec["layout"]; n = len(spec["cells"]); cn = LAYOUT_CN[lay]
    W, H, _ = RATIOS[ratio]; short = min(W, H); M = round(short * 0.075); k = short / 1080
    raw, paper, warns = strip_arts(bg, n, cn, blue=range(1, n) if lay == "compare" else range(n))
    # 左右对比：左边“错的”去色成浅灰、不上蓝；右边“对的”主角上蓝。三步流程每张都上蓝
    arts = [to_grey(raw[0], paper, 0.35)] + [to_blue(q) for q in raw[1:]] if lay == "compare" else [to_blue(q) for q in raw]
    qc = {"ratio": ratio, "layout": lay, "warnings": list(warns)}
    p = panel_plan(spec, arts, ratio, W, H, M, k)
    if p is None:
        sys.exit(f"{ratio} {cn}排不下：主标题或副标题太长，改短再出")
    g = grid_text(spec, 116 * k * p["ts"]); gk = g["k"]
    im = Image.new("RGBA", (W, H), paper + (255,)); d = ImageDraw.Draw(im)
    ox, oy = M, M - 10
    if p["side"] == "left":                                # 文字在左：整块文字竖直居中略偏上（同四宫格）
        oy = max(M - 10, (H - g["height"]) // 2 - round(60 * gk))
    tm = draw_text_block(im, d, g, ox, oy)
    redmask = Image.new("L", (W, H)); draws = [(d, RED), (ImageDraw.Draw(redmask), 255)]
    o = g["oval"]; allp = []
    if o:
        pts, at = hand_oval(ox + o["L"], ox + o["R"], oy + o["cy"], o["ry"])
        stroke(draws, pts, stroke_width(gk)); allp += pts
    gx0, gy0, gx1, gy1 = p["gx0"], p["gy0"], p["gx1"], p["gy1"]
    cells, xs, ys = cell_boxes(gx0, gy0, gx1, gy1, p["cols"], p["rows"], n)
    d.rounded_rectangle((gx0, gy0, gx1, gy1), radius=round(18 * k), outline=TITLE_INK, width=max(3, round(4 * k)))
    for x_ in xs[1:-1]:
        d.line((x_, gy0 + 2, x_, gy1 - 2), fill=TITLE_INK, width=max(2, round(2 * k)))
    for y_ in ys[1:-1]:
        d.line((gx0 + 2, y_, gx1 - 2, y_), fill=TITLE_INK, width=max(2, round(2 * k)))
    labs = cell_labels(spec, p["tf"], numbered=lay != "compare"); pitch = round(p["tf"] * 1.32)
    room = round(44 * k) if lay == "steps" and p["cols"] > 1 else 0
    lay_cells = panel_layout(p, labs, k, n, room); qc["cells"] = []
    for i, (c, art, lab) in enumerate(zip(lay_cells, arts, labs)):
        cx, cy, ex, ey = c["cell"]; lx, ly = c["lab"]
        for j, t in enumerate(lab):
            bb = ink(t); im.alpha_composite(t, (lx - bb[0], ly + j * pitch - bb[1]))
        if c["lab_box"][2] > (ex - round(20 * k) if p["mode"] == "top" else c["art"][0]):
            qc["warnings"].append(f"第 {i + 1} 格标签太长，超出格子")
        x0, y0, x1, y1 = c["art"]
        sc = min((x1 - x0) / art.width, (y1 - y0) / art.height)
        q = art.resize((round(art.width * sc), round(art.height * sc)), Image.LANCZOS)
        px = x0 + (x1 - x0 - q.width) // 2
        py = y0 + (y1 - y0 - q.height) // 2                 # 小图在格子里上下居中（竖版格子高，贴底会空一大块）
        paste_art(im, q, px, py, paper)
        lb = c["lab_box"]; art_ink = np.nonzero(np.asarray(q.convert("L")) < 200)
        if len(art_ink[0]) and np.any((px + art_ink[1] > lb[0] - 6) & (px + art_ink[1] < lb[2] + 6) & (py + art_ink[0] > lb[1] - 6) & (py + art_ink[0] < lb[3] + 6)):
            qc["warnings"].append(f"第 {i + 1} 格小图压到了标签")
        c["q"] = (px, py, q.width, q.height)
        qc["cells"].append({"cell": i + 1, "art": [px, py, q.width, q.height], "label": list(lb),
                            "fill_w": round(q.width / (ex - cx), 2), "fill_h": round(q.height / (ey - cy), 2)})
    tip = head = None
    if lay == "compare":
        # 左格“错的”上面压一个红橙手画叉（约小图短边的 6 成）
        px, py, qw, qh = lay_cells[0]["q"]
        allp += hand_cross(draws, px + qw / 2, py + qh / 2, 0.6 * min(qw, qh), gk)
        # 弯箭头从圈的右下方出发，往右甩再往下钩，箭头尖停在框外、朝着右边“对的”那格
        p0 = at(0.62); mid = xs[1]
        tip = (min(max(p0[0] + 230 * gk, mid + 90 * k), gx1 - 120 * k), gy0 - 16 * k)
        c1 = (p0[0] + 130 * gk, p0[1] + 30 * gk); c2 = (tip[0] + 18 * gk, tip[1] - 140 * gk)
        p0, c1, c2, tip = arrow_3line(g, ox, oy, "top", (p0, c1, c2, tip), k, gx1 - 120 * k)    # 三行主标题：让开下面的行
        ap, head = hand_arrow(draws, p0, c1, c2, tip, gk); allp += ap
        if not (tip[1] < gy0 - 2 and mid < tip[0] < gx1):
            qc["warnings"].append("箭头没指向右边那格")
    else:
        # 三步之间压一个红橙手画小箭头把三步串起来：竖排在标签那一栏、朝下；横排在两张小图之间、朝右
        sk = 0.85 * gk; L = round(46 * k)
        for i in range(n - 1):
            if p["rows"] > 1:
                y_ = ys[i + 1]; x_ = lay_cells[i]["lab"][0] + round(2.2 * p["tf"])
                a0, a1, a2, a3 = (x_, y_ - L), (x_ - 8 * k, y_ - 12 * k), (x_ + 8 * k, y_ + 10 * k), (x_, y_ + L)
            else:
                x_ = xs[i + 1]; y_ = round((lay_cells[i]["art"][1] + lay_cells[i]["art"][3]) / 2)
                a0, a1, a2, a3 = (x_ - L, y_), (x_ - 12 * k, y_ - 8 * k), (x_ + 10 * k, y_ + 8 * k), (x_ + L, y_)
            ap, _ = hand_arrow(draws, a0, a1, a2, a3, sk); allp += ap
            bx0, by0 = min(x for x, _ in ap) - 14 * k, min(y for _, y in ap) - 14 * k
            bx1, by1 = max(x for x, _ in ap) + 14 * k, max(y for _, y in ap) + 14 * k
            for c in lay_cells:
                lb = c["lab_box"]
                if bx0 < lb[2] and lb[0] < bx1 and by0 < lb[3] and lb[1] < by1:
                    qc["warnings"].append(f"第 {i + 1}、{i + 2} 步之间的小箭头压到了标签")
    keep = (ox + o["keep"][0], ox + o["keep"][1]) if o else (0, 0)
    rows_ = (oy + o["rows"][0], oy + o["rows"][1]) if o else (0, 0)
    qc["red_on_other_text"] = red_on_text(redmask, tm, keep, rows_)
    if qc["red_on_other_text"]:
        qc["warnings"].append("红圈或箭头压到了关键词以外的字")
    if allp and red_out(allp, W, H):
        qc["warnings"].append("红线出画面")
    qc.update(plan=p, title_px=g["S"], margin=M, tip=tip and [round(tip[0]), round(tip[1])], head_dir_deg=head and round(head, 1),
              oval=o and [round(ox + o["L"]), round(ox + o["R"]), round(oy + o["cy"]), round(o["ry"])], box=[gx0, gy0, gx1, gy1])
    im.convert("RGB").save(out, quality=93)
    return qc


# ----- B 数字大字：单主角底图，副标题色块左上 → 两行标题 → 特大数字 + 单位 + 红橙下划线 → 插图 -----
def number_block(spec, S, maxw):
    """特大宋体深蓝数字（字号约主标题 3.5 倍，画板上 190:54）+ 细体单位（约主标题 1.2 倍，和数字底线对齐跟在右边）。
    太宽就缩，最小缩到主标题的 2 倍；位置都相对数字墨迹左上角。"""
    fn = 3.5 * S; su = round(1.2 * S); fu = font(SONG, su, 3)
    u = line_img(spec["unit"], fu, su * 0.03, TITLE_INK) if spec.get("unit") else None
    gap = round(0.12 * S)
    while True:
        fnum = font(SONG, round(fn), 6)
        num = line_img(spec["number"], fnum, -0.02 * fn, TITLE_INK); nb = ink(num)
        width = nb[2] - nb[0] + ((gap + ink(u)[2] - ink(u)[0]) if u else 0)
        if width <= maxw or fn < 2 * S:
            break
        fn *= 0.95
    # 单位和数字“底线对齐”：汉字墨迹比西文底线略往下伸，按基线对齐看着像掉下去，所以让两者的墨迹底边齐平
    return dict(num=num, nb=nb, u=u, ux=nb[2] - nb[0] + gap - (ink(u)[0] if u else 0), uy=(nb[3] - nb[1]) - (ink(u)[3] if u else 0),
                fn=round(fn), width=width, height=nb[3] - nb[1], too_wide=width > maxw)


def render_number(spec, bg, ratio, out):
    W, H, _ = RATIOS[ratio]; short = min(W, H); M = round(short * 0.075); k = short / 1080; e = round(40 * k)
    src = Image.open(bg).convert("RGB"); a = np.asarray(src).astype(int)
    paper = tuple(int(v) for v in np.median(a[: a.shape[0] // 10].reshape(-1, 3), axis=0))
    ys, xs = np.nonzero(np.abs(a - paper).max(axis=2) > 40)
    if not len(xs):
        sys.exit(f"底图 {bg} 是空的，要重出底图")
    raw = src.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))     # 整个插图裁到墨迹边，下移、缩小放到数字下面
    # 单主角底图的场景常常画到图边，缩小后会露出一条硬边：哪条边画到了图边，就在那一侧用约 8% 的宽度渐渐淡成纸色
    sh_, sw_ = a.shape[:2]; edges = (xs.min() <= 3, ys.min() <= 3, xs.max() >= sw_ - 4, ys.max() >= sh_ - 4)
    qc = {"ratio": ratio, "layout": "number", "warnings": []}
    if subject_box(np.asarray(raw), small=bool(spec.get("small_subject"))) is None:
        qc["warnings"].append("没找到陶土色主角：底图没按要求给主角上色，建议重出底图" + ("" if spec.get("small_subject") else "；" + SMALL_HINT))
    # 单主角底图里场景的留白常比图上方的纸面亮一点，直接贴会显出一个方块：整张封面改用场景自己的留白色当纸色
    paper = tuple(int(v) for v in np.median(np.asarray(raw).reshape(-1, 3), axis=0))
    art = to_blue(raw); best = None
    if any(edges):
        f = np.ones(art.size[::-1], np.float32); hh, ww = f.shape; rx, ry = max(1, round(0.08 * ww)), max(1, round(0.08 * hh))
        ramp_x, ramp_y = np.linspace(0, 1, rx, dtype=np.float32), np.linspace(0, 1, ry, dtype=np.float32)
        if edges[0]: f[:, :rx] *= ramp_x
        if edges[2]: f[:, -rx:] *= ramp_x[::-1]
        if edges[1]: f[:ry] *= ramp_y[:, None]
        if edges[3]: f[-ry:] *= ramp_y[::-1, None]
        arr_ = np.asarray(art).astype(np.float32); f = f[..., None]
        art = Image.fromarray((arr_ * f + np.array(paper, np.float32) * (1 - f)).round().astype(np.uint8))
    for ts in ts_options(W, H, ratio):
        g = grid_text(spec, 116 * k * ts); S = g["S"]
        if g["width"] + M > W - M:
            continue
        for side in (("top",) if H > W else ("left", "top")):
            nb = number_block(spec, S, W - 2 * M if side == "top" else 0.55 * (W - 2 * M))
            ny = M - 10 + round(g["l2_bottom"] + 0.35 * S)            # 数字墨迹顶：标题下面留约 1/3 字高
            uly = ny + nb["height"] + round(0.05 * nb["fn"])           # 下划线
            bottom = uly + round(42 * g["k"])
            colw = max(g["width"], nb["width"])
            if side == "top":            # 插图在数字下面，中间至少留一个主标题字高
                area = (e, bottom + S, W - e, H - e)
            else:                        # 横版也可以文字一栏在左、插图在右
                if bottom > H - M:
                    continue
                area = (M + colw + S, M, W - e, H - e)
            aw, ah = area[2] - area[0], area[3] - area[1]
            if aw < 50 or ah < 50:
                continue
            sc = min(aw / art.width, ah / art.height)
            if art.height * sc < 0.22 * H or art.width * sc < 0.3 * W:  # 插图太小就算放不下，不硬放
                continue
            plan = dict(side=side, ts=ts, area=area, sc=sc, ny=ny, uly=uly)
            if H > W:
                best = (0, plan, g, nb); break
            score = sc * sc * art.width * art.height * (1 + 0.15 * min(ts, 1.2))
            if best is None or score > best[0]:
                best = (score, plan, g, nb)
        if best and H > W:
            break
    if best is None:
        sys.exit(f"{ratio} 数字大字排不下：主标题、副标题或数字太长，插图放不进去，改短再出")
    _, p, g, nb = best; gk = g["k"]
    if nb["too_wide"]:
        qc["warnings"].append(f"数字太长，缩到 {nb['fn']}px 仍超出右边距，请改短")
    im = Image.new("RGBA", (W, H), paper + (255,)); d = ImageDraw.Draw(im)
    tm = draw_text_block(im, d, g, M, M - 10)
    im.alpha_composite(nb["num"], (M - nb["nb"][0], p["ny"] - nb["nb"][1]))
    if nb["u"] is not None:
        im.alpha_composite(nb["u"], (M + nb["ux"], p["ny"] + nb["uy"]))
    redmask = Image.new("L", (W, H)); draws = [(d, RED), (ImageDraw.Draw(redmask), 255)]
    ul = hand_underline(draws, M, M + nb["nb"][2] - nb["nb"][0], p["uly"], gk)
    x0, y0, x1, y1 = p["area"]; q = art.resize((round(art.width * p["sc"]), round(art.height * p["sc"])), Image.LANCZOS)
    px = x0 + (x1 - x0 - q.width) // 2
    py = y1 - q.height if p["side"] == "top" else y0 + (y1 - y0 - q.height) // 2
    paste_art(im, q, px, py, paper)
    num_box = (M, p["ny"], M + nb["width"], p["ny"] + nb["height"])
    qc["red_on_other_text"] = red_on_text(redmask, tm, (0, 0), (0, 0))
    if qc["red_on_other_text"]:
        qc["warnings"].append("红线压到了标题")
    if red_out(ul, W, H) or num_box[2] > W - M + 2:
        qc["warnings"].append("数字或下划线出了边距")
    qc.update(plan={k_: v for k_, v in p.items() if k_ != "sc"}, title_px=g["S"], number_px=nb["fn"], margin=M, number_box=list(num_box),
              number_ink_h=nb["height"], underline_bottom=round(max(y for _, y in ul)), art=[px, py, q.width, q.height])
    im.convert("RGB").save(out, quality=93)
    return qc


# ----- D 局部放大：标题左上，色块在标题下方靠右，左下整体小图（去色）+ 小圆环，右边大圆里放特写（上蓝） -----
def zoom_plan(spec, over, ratio, W, H, M, k):
    e = round(40 * k); best = None
    for ts in ts_options(W, H, ratio):
        S = title_px(spec, round(116 * k * ts))
        ls = title_imgs(spec, S)
        lx = min(ink(l)[0] for l in ls); tw = max(ink(l)[2] for l in ls) - lx
        th = (len(ls) - 1) * round(S * 1.3) + ink(ls[-1])[3] - ink(ls[0])[1]
        s, bw, bh, padx, pady = sub_block(spec["sub"], 76 * k * ts)
        by = M + th + round(0.5 * S)
        if H > W:            # 竖版：色块靠右；大圆在右下，整体小图在左下
            if max(tw, bw) > W - 2 * M:
                continue
            bx = W - M - bw; top = by + bh + max(round(1.4 * S), round(170 * k))
            D = min(round(0.48 * W), H - e - top)
            cx, cy = W - e - D / 2, (top + H - e) / 2
            ov = (e, top, round(cx - D / 2 - 30 * k), H - e)
        else:                # 横版：标题、色块、整体小图一栏在左；大圆在右边竖直居中
            colw = max(tw, bw); gap = max(round(1.2 * S), round(160 * k))
            D = min(H - 2 * e, W - e - (M + colw + gap))
            bx = M + colw - bw
            cx, cy = W - e - D / 2, H / 2
            ov = (M, by + bh + round(0.8 * S), M + colw, H - e)
        aw, ah = ov[2] - ov[0], ov[3] - ov[1]
        if D < 0.38 * min(W, H) or aw < 50 or ah < 50:
            continue
        sc = min(aw / over.width, ah / over.height)
        if over.height * sc < 0.15 * H:
            continue
        p = dict(ts=ts, S=S, ls=ls, lx=lx, s=s, bw=bw, bh=bh, padx=padx, pady=pady, bx=bx, by=by, D=round(D), cx=round(cx), cy=round(cy), ov=ov, sc=sc)
        if H > W:
            return p
        score = D * math.sqrt(sc * sc * over.width * over.height) * (1 + 0.15 * min(ts, 1.2))
        if best is None or score > best[0]:
            best = (score, p)
    return best and best[1]


def render_zoom(spec, bg, ratio, out):
    W, H, _ = RATIOS[ratio]; short = min(W, H); M = round(short * 0.075); k = short / 1080
    raw, paper, warns = strip_arts(bg, 2, "局部放大", blue=(1,))
    over = to_grey(raw[0], paper, 0.0)           # 整体场景：去色，只剩灰线
    close = to_blue(raw[1])                      # 局部特写：主角上蓝
    qc = {"ratio": ratio, "layout": "zoom", "warnings": list(warns)}
    p = zoom_plan(spec, over, ratio, W, H, M, k)
    if p is None:
        sys.exit(f"{ratio} 局部放大排不下：主标题或副标题太长，改短再出")
    S = p["S"]; tk = S / 116
    im = Image.new("RGBA", (W, H), paper + (255,)); d = ImageDraw.Draw(im)
    x = M - p["lx"]; ys = [M - ink(p["ls"][0])[1] + i * round(S * 1.3) for i in range(len(p["ls"]))]
    parts = [(l, (x, y)) for l, y in zip(p["ls"], ys)]
    for l, pos in parts:
        im.alpha_composite(l, pos)
    tm = title_mask((W, H), parts)
    bx, by, bw, bh = p["bx"], p["by"], p["bw"], p["bh"]
    put_block(im, d, (p["s"], bw, bh, p["padx"], p["pady"]), bx, by)
    oval = None if p["s"] else find_oval(spec, S, x, ys, k)      # 不给副标题：圈标题关键词，箭头从圈出发（2026-11-21）
    # 左下整体小图 + 指定位置的深蓝小圆环
    x0, y0, x1, y1_ = p["ov"]; q = over.resize((round(over.width * p["sc"]), round(over.height * p["sc"])), Image.LANCZOS)
    px = x0 + (x1 - x0 - q.width) // 2 if H > W else x0; py = y1_ - q.height      # 横版和标题左对齐
    paste_art(im, q, px, py, paper)
    fx, fy = spec.get("zoom_at", [0.5, 0.5]); rr = max(round(22 * k), round(0.07 * min(q.width, q.height)))
    rc = (px + fx * q.width, py + fy * q.height)
    cx, cy, D = p["cx"], p["cy"], p["D"]; R_ = D / 2
    # 小圆环和大圆之间连一条深蓝细线（先画线，大圆再盖上去）
    dx, dy = cx - rc[0], cy - rc[1]; L = math.hypot(dx, dy) or 1; ux, uy = dx / L, dy / L
    d.line((rc[0] + ux * rr, rc[1] + uy * rr, cx - ux * R_, cy - uy * R_), fill=TITLE_INK, width=max(2, round(3 * k)))
    d.ellipse((rc[0] - rr, rc[1] - rr, rc[0] + rr, rc[1] + rr), outline=TITLE_INK, width=max(3, round(4 * k)))
    # 右边大圆：米白底，特写圆形裁切，深蓝描边（画板上 3px，半尺寸 → 6px×比例）
    lwc = max(4, round(6 * k)); box = (round(cx - R_), round(cy - R_), round(cx + R_), round(cy + R_))
    d.ellipse(box, fill=paper)
    f = 0.8 * D; sc = min(f / close.width, f / close.height)
    c2 = close.resize((round(close.width * sc), round(close.height * sc)), Image.LANCZOS)
    qx, qy = round(cx - c2.width / 2), round(cy - c2.height / 2)
    before = im.copy(); paste_art(im, c2, qx, qy, paper)
    mask = Image.new("L", (W, H)); ImageDraw.Draw(mask).ellipse((box[0] + lwc // 2, box[1] + lwc // 2, box[2] - lwc // 2, box[3] - lwc // 2), fill=255)
    im = Image.composite(im, before, mask); d = ImageDraw.Draw(im)
    d.ellipse(box, outline=TITLE_INK, width=lwc)
    ci = np.nonzero(np.asarray(c2.convert("L")) < 200)
    cut = float(np.mean(np.hypot(qx + ci[1] - cx, qy + ci[0] - cy) > R_ - lwc)) if len(ci[0]) else 0
    if cut > 0.03:
        qc["warnings"].append(f"特写小图被圆裁掉了 {cut:.0%} 的线条，看看主角有没有被切")
    # 弯箭头从色块指进放大圆：竖版从色块底边出、往右鼓，从圆的上方指进去；横版从色块右侧出、从圆的左上方指进去
    redmask = Image.new("L", (W, H)); draws = [(d, RED), (ImageDraw.Draw(redmask), 255)]
    th_ = math.radians(-80 if H > W else 205); ox_, oy_ = math.cos(th_), math.sin(th_)
    tip = (cx + (R_ + 14 * k) * ox_, cy + (R_ + 14 * k) * oy_)
    keep = ((0, 0), (0, 0))
    if oval:
        stroke(draws, hand_oval(oval["L"], oval["R"], oval["cy"], oval["ry"])[0], stroke_width(tk)); keep = (oval["keep"], oval["rows"])
        p0, c1, c2_, tip = circle_arrow(oval, tip, W, H, tk, text_hits(tm, *keep, tk, W, H))
    else:
        p0 = (bx + 0.72 * bw, by + bh + 12 * k) if H > W else (bx + bw + 12 * k, by + bh / 2)
        L = math.hypot(tip[0] - p0[0], tip[1] - p0[1])
        c1 = (p0[0] + 0.45 * L, p0[1] + 0.15 * L) if H > W else (p0[0] + 0.45 * L, p0[1] - 0.1 * L)
        c2_ = (tip[0] + ox_ * 0.45 * L, tip[1] + oy_ * 0.45 * L)
    ap, head = hand_arrow(draws, p0, c1, c2_, tip, tk)
    qc["red_on_other_text"] = red_on_text(redmask, tm, *keep)
    if qc["red_on_other_text"]:
        qc["warnings"].append("箭头压到了标题")
    if red_out(ap, W, H):
        qc["warnings"].append("箭头弧线伸出画面边缘")
    if box[0] < 0 or box[1] < 0 or box[2] > W or box[3] > H:
        qc["warnings"].append("放大圆出了画面")
    if math.hypot(rc[0] - cx, rc[1] - cy) < R_ + rr:
        qc["warnings"].append("小圆环被放大圆盖住了，换个 --zoom-at 或重出底图")
    qc.update(plan={k_: p[k_] for k_ in ("ts", "D", "cx", "cy", "ov", "bx", "by", "bw", "bh")}, title_px=S, margin=M,
              title_box=[M, M, M + max(ink(l)[2] for l in p["ls"]) - p["lx"], ys[-1] + ink(p["ls"][-1])[3]],
              circle=[cx, cy, round(R_)], ring=[round(rc[0]), round(rc[1]), rr], over=[px, py, q.width, q.height],
              tip=[round(tip[0]), round(tip[1])], head_dir_deg=round(head, 1), cut=round(cut, 3))
    im.convert("RGB").save(out, quality=93)
    return qc


# ---------- 2026-11-20 实拍留色画风（--style photo） ----------
# 《261114 千万的房子》试出、用户确认固化。照片不调色（不换蓝、不去色），只等比缩放加裁切，绝不拉伸；
# 留色区域用饱和度自动找，红圈圈住它、箭头尖吸到彩色像素上（风格说明第 3 节）。标题、色块、圈、箭头、检查都用上面的函数。
def _dilate(m, n):
    for _ in range(n):                                       # 十字形一圈（同 scipy 默认结构元）
        p = np.pad(m, 1)
        m = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return m


def _erode(m, n):
    for _ in range(n):                                       # 图外当作没颜色（同 scipy 的 border_value=0）
        p = np.pad(m, 1)
        m = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return m


def _blobs(m):
    """4 邻接连通块，按行程合并（不依赖 scipy）。返回 [(像素数, [(行, 起, 止), …]), …]。"""
    parent, runs, prev = [], [], []
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for y in range(m.shape[0]):
        dv = np.diff(np.concatenate(([0], m[y].astype(np.int8), [0])))
        cur, j = [], 0
        for s_, e_ in zip(np.nonzero(dv == 1)[0], np.nonzero(dv == -1)[0]):
            i = len(runs); runs.append((y, int(s_), int(e_))); parent.append(i); cur.append((s_, e_, i))
            while j < len(prev) and prev[j][1] <= s_:
                j += 1
            q = j
            while q < len(prev) and prev[q][0] < e_:         # 和上一行重叠的行程连成一块
                a_, b_ = find(prev[q][2]), find(i)
                if a_ != b_:
                    parent[a_] = b_
                q += 1
        prev = cur
    groups = {}
    for i, r in enumerate(runs):
        groups.setdefault(find(i), []).append(r)
    return [(sum(e_ - s_ for _, s_, e_ in rs), rs) for rs in groups.values()]


def colour_mask(img, thr=40, keep_frac=0.15):
    """留色区域：HSV 饱和度 > thr → 开运算 2 次去噪 → 闭运算 6 次连成块 → 只留面积 ≥ 最大块 15% 的连通块（风格说明 3.1）。"""
    sat = np.asarray(img.convert("HSV"))[..., 1]
    m = _erode(_dilate(_dilate(_erode(sat > thr, 2), 2), 6), 6)
    out = np.zeros_like(m); bl = _blobs(m)
    if bl:
        big = max(n for n, _ in bl)
        for n, rs in bl:
            if n >= keep_frac * big:
                for y, s_, e_ in rs:
                    out[y, s_:e_] = True
    return out


def find_colour(img, thr=40):
    """(留色蒙版, 外接框 x0,y0,x1,y1)；几乎没颜色（不到 0.3%）时框是 None。"""
    m = colour_mask(img, thr)
    if m.mean() < 0.003:
        return m, None
    ys, xs = np.nonzero(m)
    return m, (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def photo_warnings(img, box, who=""):
    """验底图里程序能判断的两条：有没有留色、彩色是不是太多（照片质感、无人无字要人看，见 SKILL.md）。"""
    sat = np.asarray(img.convert("HSV"))[..., 1]; w = []
    if box is None:
        w.append(f"{who}没找到留色区域（底图几乎没有颜色，或彩色块太小），红圈和箭头没法定位，要重出底图")
    elif (sat > 40).mean() > 0.35:
        w.append(f"{who}彩色面积占 {(sat > 40).mean():.0%}，不像只有问题部位留色，检查底图")
    if np.median(sat) > 15:
        w.append(f"{who}照片整体不是黑白（灰的部分也带颜色），检查底图")
    return w


def box_centre(img, box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box else (img.width / 2, img.height / 2)


def crop_window(size, aw, ah, focus):
    """按 aw:ah 取最大的窗口，中心对准 focus，再夹回照片内。返回 (x, y, 宽, 高)。"""
    Wi, Hi = size; cw, ch = Wi, round(Wi * ah / aw)
    if ch > Hi:
        ch = Hi; cw = round(Hi * aw / ah)
    return int(min(max(focus[0] - cw / 2, 0), Wi - cw)), int(min(max(focus[1] - ch / 2, 0), Hi - ch)), cw, ch


def crop_for(img, rect, focus):
    """照片等比裁切后缩放到 rect（不拉伸）。返回 (图, 源图窗口)。"""
    aw, ah = rect[2] - rect[0], rect[3] - rect[1]; win = crop_window(img.size, aw, ah, focus)
    return img.crop((win[0], win[1], win[0] + win[2], win[1] + win[3])).resize((aw, ah), Image.LANCZOS), win


def map_box(b, win, rect):
    """源图坐标的框 → 画布坐标（经过裁切窗口和缩放）。"""
    wx, wy, cw, _ = win; s = (rect[2] - rect[0]) / cw
    return [rect[0] + (b[0] - wx) * s, rect[1] + (b[1] - wy) * s, rect[0] + (b[2] - wx) * s, rect[1] + (b[3] - wy) * s]


def fit_score(img, box, rect, focus=None):
    """横版挑排法用：照片裁进 rect 后留色框显示的面积 × 留色框留在窗口里的比例²（被裁掉的扣分）。"""
    aw, ah = rect[2] - rect[0], rect[3] - rect[1]
    if box is None or aw <= 0 or ah <= 0:
        return max(aw, 0) * max(ah, 0) * 1e-6
    wx, wy, cw, ch = crop_window(img.size, aw, ah, focus or box_centre(img, box)); s = aw / cw
    iw = max(0, min(box[2] + 1, wx + cw) - max(box[0], wx)); ih = max(0, min(box[3] + 1, wy + ch) - max(box[1], wy))
    inside = iw * ih / ((box[2] - box[0] + 1) * (box[3] - box[1] + 1))
    return iw * ih * s * s * inside * inside


CROP_LOSS_WARN = 0.08            # 留色框被裁出画面的比例超过这个就警告（2026-11-24）


def photo_oval(draws, cb, k, clip=None, expand=0.10, pad=8, qc=None):
    """红圈圈住留色框：半宽/半高 = 框的一半 ×(1+外扩) + pad；clip 时收在照片里（离边至少 12px，给线宽和手抖）。
    收边后的圈比照片里看得见的那部分留色框的 6 成还窄或还矮（留色区贴着照片边）就报警。"""
    x0, y0, x1, y1 = cb; cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw = (x1 - x0) / 2 * (1 + expand) + pad * k; hh = (y1 - y0) / 2 * (1 + expand) + pad * k
    if clip:                  # 手画圈右半边会抖、外胀约 5.5%，收边时一起算上，不让圈伸出照片
        hw = min(hw, (cx - clip[0] - 12 * k) / 1.055, (clip[2] - cx - 12 * k) / 1.055); hh = min(hh, (cy - clip[1] - 12 * k) / 1.055, (clip[3] - cy - 12 * k) / 1.055)
        vw, vh = min(x1, clip[2]) - max(x0, clip[0]), min(y1, clip[3]) - max(y0, clip[1])
        if qc is not None and (x1 - x0) > 0 and (y1 - y0) > 0:      # 2026-11-24 初稿模式第二次实测：留色主体被裁出画面，以前不报
            lost = 1 - max(vw, 0) * max(vh, 0) / ((x1 - x0) * (y1 - y0))
            if lost > CROP_LOSS_WARN:
                qc["warnings"].append(f"留色主体被画面边缘裁掉约 {lost:.0%}（照片比例和这个版式不合），主体或照片上的字可能不完整，检查成图；必要时重出底图（主体别靠边、留足边距）")
        if qc is not None and (hw < 0.3 * vw or hh < 0.3 * vh):
            qc["warnings"].append("留色区域贴着照片边，红圈圈不住它，检查成图；必要时重出底图（问题部位别贴边）")
        hw, hh = max(hw, 12 * k), max(hh, 12 * k)
    pts = hand_oval(cx - hw, cx + hw, cy, hh)[0]; stroke(draws, pts, stroke_width(k))
    return pts


def tip_in(cb, fy=0.12):
    return ((cb[0] + cb[2]) / 2, cb[1] + fy * (cb[3] - cb[1]))


def snap_tip(mask, win, rect, tip):
    """箭头尖吸到真正的彩色像素上：留色蒙版腐蚀 20 次（源图像素）后，找离 tip 最近的那个（风格说明 3.4）。"""
    wx, wy, cw, _ = win; s = (rect[2] - rect[0]) / cw
    tx, ty = (tip[0] - rect[0]) / s + wx, (tip[1] - rect[1]) / s + wy
    er = _erode(mask, 20); m = er if er.any() else mask
    ys, xs = np.nonzero(m); i = np.argmin((xs - tx) ** 2 + (ys - ty) ** 2)
    return (rect[0] + (xs[i] - wx) * s, rect[1] + (ys[i] - wy) * s)


def arrow_block(draws, blk, tip, W, k, side=None):
    """箭头从副标题色块指向 tip。主角在色块左/右：从侧边水平出发、往外甩弧再回钩；在正下方：从底边 30% 处出、往左鼓再钩回。
    控制点夹在 [70, W-70]，不甩出画面（风格说明 3.5）。"""
    bx, by, bw, bh = blk      # 横版主角常和色块差不多高：上下差按至少横向距离的 45% 算，箭头仍是甩弧回钩、不成一条直线
    if side == "left" or (side is None and tip[0] < bx + 0.15 * bw):
        p0 = (bx - 12 * k, by + bh / 2); dx = p0[0] - tip[0]; dy = max(abs(tip[1] - p0[1]), 0.45 * dx)
        c1 = (max(70 * k, p0[0] - 0.55 * dx - 0.35 * dy), p0[1] - 15 * k); c2 = (tip[0] - min(140 * k, 0.3 * dy), tip[1] - 0.55 * dy)
    elif side == "right" or (side is None and tip[0] > bx + 0.85 * bw):
        p0 = (bx + bw + 12 * k, by + bh / 2); dx = tip[0] - p0[0]; dy = max(abs(tip[1] - p0[1]), 0.45 * dx)
        c1 = (min(W - 70 * k, p0[0] + 0.55 * dx + 0.35 * dy), p0[1] - 15 * k); c2 = (tip[0] + min(140 * k, 0.3 * dy), tip[1] - 0.55 * dy)
    else:
        p0 = (bx + 0.3 * bw, by + bh + 12 * k); L = math.hypot(tip[0] - p0[0], tip[1] - p0[1])
        c1 = (p0[0] - 0.35 * L, p0[1] + 0.30 * L); c2 = (tip[0] - 0.12 * L, tip[1] - 0.45 * L)
    return hand_arrow(draws, p0, c1, c2, tip, k)[0]


def arrow_from(draws, blk, o, tip, W, H, k, tm, keep, side=None, labels=None):
    """有副标题色块就从色块出发（arrow_block）；不给副标题（色块宽 0，2026-11-21）时从关键词红圈出发（circle_arrow），躲开标题字和 labels 小标签。"""
    if blk[2]:
        return arrow_block(draws, blk, tip, W, k, side)
    return hand_arrow(draws, *circle_arrow(o, tip, W, H, k, text_hits(tm, *keep, k, W, H, labels)), k)[0] if o else []


def split_sheet(img, cols, rows, thr=235):
    """拼了几张照片的底图按白缝切开（整列或整行 97% 以上的像素近白就算缝），每张向内缩 3px。
    返回 (照片，按从左到右、从上到下, 警告)。缝的条数对不上时：多了就并掉最窄的缝，少了就等分切并报警。"""
    a = np.asarray(img.convert("L")) > thr; warns = set()
    def segs(white, n, L):
        out, st = [], None
        for i, wv in enumerate(np.append(white, True)):
            if not wv and st is None:
                st = i
            if wv and st is not None:
                if i - st > 100:
                    out.append([st, i])
                st = None
        while len(out) > n:                                  # 照片里有一整列近白：把最窄的缝并回去
            g = min(range(len(out) - 1), key=lambda j: out[j + 1][0] - out[j][1]); out[g][1] = out.pop(g + 1)[1]
        if len(out) < n:
            warns.add(f"底图里没找到 {cols * rows} 张照片之间的白缝，已按等分切开，检查成图里照片有没有切歪")
            out = [[L * j // n, L * (j + 1) // n] for j in range(n)]
        return out
    xs = segs(a.mean(0) > 0.97, cols, a.shape[1]); bands = [segs(a[:, x0:x1].mean(1) > 0.97, rows, a.shape[0]) for x0, x1 in xs]
    photos = [img.crop((xs[c][0] + 3, bands[c][r][0] + 3, xs[c][1] - 3, bands[c][r][1] - 3)) for r in range(rows) for c in range(cols)]
    return photos, sorted(warns)


def photo_title(spec, W, M, k, ts=1.0):
    """主标题各行（两行或三行），墨迹离左边、上边都是 M（同单主角）；太宽就缩。圈关键词的位置一起算好。"""
    S = title_px(spec, round(116 * k * ts))
    while True:
        ls = title_imgs(spec, S)
        if max(l.width for l in ls) <= W - 2 * M or S < 60 * k:
            break
        S -= 4
    x = M - min(ink(l)[0] for l in ls); ys = [M - ink(ls[0])[1] + i * round(S * 1.3) for i in range(len(ls))]
    box = (M, M, max(x + ink(l)[2] for l in ls), ys[-1] + ink(ls[-1])[3])
    return dict(ls=ls, x=x, ys=ys, box=box, oval=find_oval(spec, S, x, ys, k), S=S, k=S / 116)


def put_title(im, draws, t, qc, W, M):
    """贴标题、画关键词的圈。返回 (标题蒙版, 圈的点, 关键词可碰的范围)。"""
    parts = [(l, (t["x"], y)) for l, y in zip(t["ls"], t["ys"])]
    for l, pos in parts:
        im.alpha_composite(l, pos)
    if t["box"][2] > W - M:
        qc["warnings"].append(f"主标题太长，缩到最小字号（{t['S']}px）仍超出右边距，请改短")
    o = t["oval"]; pts = []
    if o:
        pts = hand_oval(o["L"], o["R"], o["cy"], o["ry"])[0]; stroke(draws, pts, stroke_width(t["k"]))
    return title_mask(im.size, parts), pts, (o["keep"], o["rows"]) if o else ((0, 0), (0, 0))


def put_block(im, d, sb, bx, by):
    s, bw, bh, padx, pady = sb
    if s:                                          # 不给副标题时没有色块，返回宽高为 0 的框
        d.rectangle((bx, by, bx + bw, by + bh), fill=TITLE_INK); im.alpha_composite(s, (bx + padx, by + pady))
    return (bx, by, bw, bh)


def photo_label(text, tf):
    return line_img(text, font(HEI, tf, 2), 1, TITLE_INK, squeeze=1)


def photo_done(im, qc, rm, tm, keep, allp, out, labels=None, **extra):
    """统一检查后存图：红线压到关键词以外的标题字、压到小标签、伸出画面都报警（同线稿的规则）。"""
    W, H = im.size
    qc["red_on_other_text"] = red_on_text(rm, tm, *keep)
    if qc["red_on_other_text"]:
        qc["warnings"].append("红圈或箭头压到了关键词以外的字")
    if labels and np.any((np.asarray(rm) > 0) & (np.asarray(title_mask((W, H), labels)) > 128)):
        qc["warnings"].append("红线压到了小标签")
    if allp and red_out(allp, W, H):
        qc["warnings"].append("箭头或红圈伸出画面边缘，检查成图")
    qc["warnings"] = list(dict.fromkeys(qc["warnings"]))          # 同一条警告只报一次
    qc.update(extra); im.convert("RGB").save(out, quality=93)
    return qc


def photo_new(W, H):
    im = Image.new("RGBA", (W, H), PHOTO_PAPER + (255,)); rm = Image.new("L", (W, H)); d = ImageDraw.Draw(im)
    return im, rm, d, [(d, RED), (ImageDraw.Draw(rm), 255)]


def ratio_ctx(ratio):
    W, H, _ = RATIOS[ratio]; short = min(W, H)
    return W, H, round(short * 0.075), short / 1080


def load_photo(bg, who="", thr=40):
    img = Image.open(bg).convert("RGB"); mask, box = find_colour(img, thr)
    return img, mask, box, photo_warnings(img, box, who)


def ts_factor(ts): return 1 + 0.15 * min(ts, 1.2)                 # 横版挑排法时标题大一点略加分（同四宫格）


# ----- 单主角（02/05–07）：标题左上，照片满宽贴底，色块在标题和照片之间靠右，箭头从色块指进留色区 -----
def photo_single(spec, bg, ratio, out):
    W, H, M, k = ratio_ctx(ratio)
    img, mask, box, warns = load_photo(bg)
    qc = {"ratio": ratio, "layout": "single", "style": "photo", "warnings": warns}; best = None
    for ts in ((1.0,) if H > W else ts_options(W, H, ratio)):
        t = photo_title(spec, W, M, k, ts); sb = sub_block(spec["sub"], 76 * k * ts); bw, bh = sb[1], sb[2]
        for side in (("top",) if H > W else ("top", "left")):
            if side == "top":     # 照片满宽贴底，能不裁就不裁（3:4 就是整张照片）
                top = max(H - round(img.height * W / img.width), t["box"][3] + bh + round(110 * k))
                rect, blk = (0, top, W, H), (W - M - bw, round((t["box"][3] + top) / 2 - bh / 2 - 10 * k))
            else:                 # 横版也可以文字一栏在左（色块在标题下面约 3 成处，箭头才有弧度），照片在右边贴满上中下
                rect, blk = (max(t["box"][2], M + bw) + round(0.6 * t["S"]), 0, W, H), (M, round(t["box"][3] + 0.3 * (H - M - t["box"][3])))
            if rect[3] - rect[1] < 0.3 * H or rect[2] - rect[0] < 0.3 * W:
                continue
            sc = fit_score(img, box, rect) * ts_factor(ts)
            if best is None or sc > best[0]:
                best = (sc, side, t, sb, rect, blk)
    _, side, t, sb, rect, blk = best
    im, rm, d, draws = photo_new(W, H)
    q, win = crop_for(img, rect, box_centre(img, box)); im.paste(q, rect[:2])
    tm, allp, keep = put_title(im, draws, t, qc, W, M)
    blk = put_block(im, d, sb, *blk)
    if box:
        cb = map_box(box, win, rect); allp = allp + photo_oval(draws, cb, k, rect, qc=qc); want = tip_in(cb)
        if side == "left":    # 文字在左：箭头尖取留色区里和色块同高的地方，箭头水平出发、拱起再钩进去
            want = (want[0], min(max(blk[1] + blk[3] / 2, cb[1] + 0.12 * (cb[3] - cb[1])), cb[3] - 0.12 * (cb[3] - cb[1])))
        tip = point_in(cb, spec["arrow_at"]) if spec.get("arrow_at") else snap_tip(mask, win, rect, want)
        allp += arrow_from(draws, blk, t["oval"], tip, W, H, k, tm, keep)
        qc.update(colour_boxes=[[round(v) for v in cb]], tip=[round(tip[0]), round(tip[1])])
    return photo_done(im, qc, rm, tm, keep, allp, out, plan={"side": side, "ts": round(t["S"] / 116 / k, 2)}, photos=[list(rect)], label=list(blk), title_px=t["S"], margin=M)


# ----- 数字大字（04/14）：文字区 → 特大数字 + 单位 + 红下划线 → 照片满宽贴底；照片上红圈，不画箭头 -----
def photo_number(spec, bg, ratio, out):
    W, H, M, k = ratio_ctx(ratio)
    img, mask, box, warns = load_photo(bg)
    qc = {"ratio": ratio, "layout": "number", "style": "photo", "warnings": warns}; best = None
    if H > W:   # 3:4：照片满宽贴底不裁，字和数字一起缩到放得下、中间留一个字高（同当时成图）
        top = H - round(img.height * W / img.width); three = len(title_lines(spec)) > 2
        for ts in [1.0 - 0.02 * i for i in range(25)]:
            if three and ts < 0.8:       # 三行主标题：字最多缩到 0.8 倍，再小手机上看不清，改走下面让照片往下
                break
            g = grid_text(spec, 116 * k * ts); S = g["S"]
            if g["width"] + M > W - M:
                continue
            ny = M - 10 + round(g["l2_bottom"] + 0.35 * S)
            for maxw in range(W - 2 * M, 300, -20):
                nb = number_block(spec, S, maxw)
                if nb["fn"] < 2.2 * S:
                    break
                uly = ny + nb["height"] + round(0.05 * nb["fn"])
                if uly + round(42 * g["k"]) + S <= top:
                    best = (0, "top", g, nb, ny, uly, (0, top, W, H)); break
            if best:
                break
        if best is None and three:       # 三行：照片仍满宽贴底，上边往下让（等比裁掉一点上下），照片至少留画面高度的 4 成
            for ts in (1.0, 0.9, 0.8):
                g = grid_text(spec, 116 * k * ts); S = g["S"]
                if g["width"] + M > W - M:
                    continue
                ny = M - 10 + round(g["l2_bottom"] + 0.35 * S); nb = number_block(spec, S, W - 2 * M)
                uly = ny + nb["height"] + round(0.05 * nb["fn"]); ptop = uly + round(42 * g["k"]) + S
                if H - ptop >= 0.4 * H:
                    best = (0, "top", g, nb, ny, uly, (0, ptop, W, H)); break
    else:       # 横版：数字在文字一栏里、照片在右边，或数字在标题下、照片在下面，挑留色区显示大的
        for ts in ts_options(W, H, ratio):
            g = grid_text(spec, 116 * k * ts); S = g["S"]
            if g["width"] + M > W - M:
                continue
            ny = M - 10 + round(g["l2_bottom"] + 0.35 * S)
            for side in ("top", "left"):
                nb = number_block(spec, S, W - 2 * M if side == "top" else 0.45 * (W - 2 * M))
                uly = ny + nb["height"] + round(0.05 * nb["fn"]); bottom = uly + round(42 * g["k"])
                rect = (0, bottom + S, W, H) if side == "top" else (M + max(g["width"], nb["width"]) + S, 0, W, H)
                if bottom > H - M or rect[3] - rect[1] < 0.3 * H or rect[2] - rect[0] < 0.3 * W:
                    continue
                sc = fit_score(img, box, rect) * ts_factor(ts) * (0.2 if nb["too_wide"] else 1)   # 数字缩到最小还超宽的排法尽量不用
                if best is None or sc > best[0]:
                    best = (sc, side, g, nb, ny, uly, rect)
    if best is None:
        sys.exit(f"{ratio} 数字大字排不下：主标题、副标题或数字太长，改短再出")
    _, side, g, nb, ny, uly, rect = best
    if nb["too_wide"]:
        qc["warnings"].append(f"数字太长，缩到 {nb['fn']}px 仍超出右边距，请改短")
    im, rm, d, draws = photo_new(W, H)
    tm = draw_text_block(im, d, g, M, M - 10)
    im.alpha_composite(nb["num"], (M - nb["nb"][0], ny - nb["nb"][1]))
    if nb["u"] is not None:
        im.alpha_composite(nb["u"], (M + nb["ux"], ny + nb["uy"]))
    q, win = crop_for(img, rect, box_centre(img, box)); im.paste(q, rect[:2])
    allp = hand_underline(draws, M, M + nb["nb"][2] - nb["nb"][0], uly, g["k"])
    if box:
        cb = map_box(box, win, rect); allp = allp + photo_oval(draws, cb, k, rect, qc=qc)
        qc["colour_boxes"] = [[round(v) for v in cb]]
    return photo_done(im, qc, rm, tm, ((0, 0), (0, 0)), allp, out, plan={"side": side}, photos=[list(rect)], title_px=g["S"], number_px=nb["fn"], margin=M,
                      number_box=[M, ny, M + nb["width"], ny + nb["height"]], underline_bottom=round(uly + 42 * g["k"]))


# ----- 局部放大（03/13）：标题左上，色块靠右；左下照片小图 + 深蓝小圈，右边大圆里是从同一张照片裁的留色区特写 -----
def photo_zoom(spec, bg, ratio, out):
    W, H, M, k = ratio_ctx(ratio)
    img, mask, box, warns = load_photo(bg)
    qc = {"ratio": ratio, "layout": "zoom", "style": "photo", "warnings": warns}
    if H > W:   # 3:4 照风格说明 03：小图宽 560 贴左边距和底边距，大圆直径 520、圆心 (800,1020)
        t = photo_title(spec, W, M, k); sb = sub_block(spec["sub"], 76 * k)
        bx, by = W - M - sb[1], t["box"][3] + round(60 * k)
        sw = round(560 * k); sh = round(img.height * sw / img.width); px, py = M, H - M - sh
        D = round(520 * k); cx, cy = round(800 * k), round(1020 * k)
    else:       # 横版用线稿局部放大的排法：标题、色块、小图一栏在左，大圆在右边竖直居中
        p = zoom_plan(spec, img, ratio, W, H, M, k)
        if p is None:
            sys.exit(f"{ratio} 局部放大排不下：主标题或副标题太长，改短再出")
        t = photo_title(spec, W, M, k, p["ts"]); sb = sub_block(spec["sub"], 76 * k * p["ts"])
        bx, by, D, cx, cy = p["bx"], p["by"], p["D"], p["cx"], p["cy"]
        sw, sh = round(img.width * p["sc"]), round(img.height * p["sc"]); px, py = p["ov"][0], p["ov"][3] - sh
    R_ = D / 2; lwc = max(4, round(6 * k))
    im, rm, d, draws = photo_new(W, H)
    tm, allp, keep = put_title(im, draws, t, qc, W, M)
    blk = put_block(im, d, sb, bx, by)
    im.paste(img.resize((sw, sh), Image.LANCZOS), (px, py))
    if box:
        cb = map_box(box, (0, 0, img.width, img.height), (px, py, px + sw, py + sh)); rc = ((cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2)
        dist = math.hypot(cx - rc[0], cy - rc[1])
        rr = max(30 * k, min(0.55 * max(cb[2] - cb[0], cb[3] - cb[1]), dist - R_ - 8 * k))   # 小圈圈住留色区，但不让大圆盖住
        if dist - R_ < rr + 2:
            qc["warnings"].append("小圆圈被放大圆盖住了，检查成图")
        ux, uy = (cx - rc[0]) / dist, (cy - rc[1]) / dist
        d.line((rc[0] + ux * rr, rc[1] + uy * rr, cx - ux * R_, cy - uy * R_), fill=TITLE_INK, width=max(2, round(3 * k)))
        d.ellipse((rc[0] - rr, rc[1] - rr, rc[0] + rr, rc[1] + rr), outline=TITLE_INK, width=max(3, round(4 * k)))
        side = min(max(box[2] - box[0], box[3] - box[1]) * 1.25, img.height)                  # 特写：留色框长边 ×1.25 的方块（风格说明 13）
        zx = min(max((box[0] + box[2]) / 2, side / 2), img.width - side / 2); zy = min(max((box[1] + box[3]) / 2, side / 2), img.height - side / 2)
        crop = img.crop((round(zx - side / 2), round(zy - side / 2), round(zx + side / 2), round(zy + side / 2))).resize((D, D), Image.LANCZOS)
        mk = Image.new("L", (D * 4, D * 4)); ImageDraw.Draw(mk).ellipse((0, 0, D * 4 - 1, D * 4 - 1), fill=255)
        im.paste(crop, (round(cx - R_), round(cy - R_)), mk.resize((D, D), Image.LANCZOS))
        qc.update(colour_boxes=[[round(v) for v in cb]], ring=[round(rc[0]), round(rc[1]), round(rr)])
    d.ellipse((round(cx - R_), round(cy - R_), round(cx - R_) + D, round(cy - R_) + D), outline=TITLE_INK, width=lwc)
    # 弯箭头从色块指进大圆：3:4 从色块底边 30% 处出、从圆的上方（-80°）指进圆里 30px；横版从色块右边出、从圆的左上方指进去
    if H > W:
        th = math.radians(-80); tip = (cx + (R_ - 30 * k) * math.cos(th), cy + (R_ - 30 * k) * math.sin(th))
        p0 = (bx + 0.30 * sb[1], by + sb[2] + 12 * k); L = math.hypot(tip[0] - p0[0], tip[1] - p0[1])
        c1, c2 = (p0[0] - 0.25 * L, p0[1] + 0.35 * L), (tip[0] - 0.05 * L, tip[1] - 0.45 * L)
    else:
        th = math.radians(205); ox_, oy_ = math.cos(th), math.sin(th); tip = (cx + (R_ - 30 * k) * ox_, cy + (R_ - 30 * k) * oy_)
        p0 = (bx + sb[1] + 12 * k, by + sb[2] / 2); L = math.hypot(tip[0] - p0[0], tip[1] - p0[1])
        c1, c2 = (p0[0] + 0.45 * L, p0[1] - 0.1 * L), (tip[0] + ox_ * 0.6 * L, tip[1] + oy_ * 0.6 * L)
    if not sb[0] and t["oval"]:                    # 不给副标题：箭头从关键词红圈出发（2026-11-21）
        p0, c1, c2, tip = circle_arrow(t["oval"], tip, W, H, k, text_hits(tm, *keep, k, W, H))
    allp = allp + hand_arrow(draws, p0, c1, c2, tip, k)[0]
    if cx - R_ < 0 or cy - R_ < 0 or cx + R_ > W or cy + R_ > H:
        qc["warnings"].append("放大圆出了画面")
    return photo_done(im, qc, rm, tm, keep, allp, out, circle=[cx, cy, round(R_)], photos=[[px, py, px + sw, py + sh]], label=list(blk),
                      tip=[round(tip[0]), round(tip[1])], title_px=t["S"], margin=M)


def oval_arrow(g, ox, oy, side, gx0, gy0, gx1, k, gy1=None):
    """线稿四宫格那套箭头：从圈的右下方出发；文字在上时往右甩再往下钩、尖停在照片上方 16px，文字在左时往下再往右钩。
    返回 (圈的点, 箭头四个控制点)。三行主标题再按 arrow_3line 让开别的行。"""
    gk = g["k"]; o = g["oval"]; pts, at = hand_oval(ox + o["L"], ox + o["R"], oy + o["cy"], o["ry"]); p0 = at(0.62)
    if side == "top":
        tip = (min(gx1 - 160 * k, p0[0] + 230 * gk), gy0 - 16 * k)
        c1 = (p0[0] + 130 * gk, p0[1] + 30 * gk); c2 = (tip[0] + 18 * gk, tip[1] - 140 * gk)
    else:
        tip = (gx0 - 16 * k, max(p0[1] + 190 * gk, oy + g["l2_bottom"] + 40 * gk))
        c1 = (p0[0] + 60 * gk, p0[1] + 90 * gk); c2 = (tip[0] - 140 * gk, tip[1] - 10 * gk)
    return pts, arrow_3line(g, ox, oy, side, (p0, c1, c2, tip), k, gx1 - 100 * k, gy1)


def arrow_3line(g, ox, oy, side, ctrl, k, xmax, ybot=None):
    """三行主标题时从圈出发的箭头（两行原样返回）：
    1. 文字在左时箭头尖最低到格子底边上方 60px（三行文字高，原来的“标题下面再往下”会甩出画面）；
    2. 圈不在最后一行、原箭头会压到下面的字时，箭头从圈右下出发，贴着这一行和下一行之间的空隙横着出去，
       绕过下面几行的右端再拐向格子（文字在上）或直接横着指向格子（文字在左）。几种弧度都压字就不改，由检查报警。"""
    ls = g["ls"]
    if len(ls) < 3:
        return ctrl
    p0, c1, c2, tip = ctrl; gk = g["k"]; S = g["S"]; i = g["oval"]["line"]
    if side != "top" and ybot is not None and tip[1] > ybot - 60 * k:
        tip = (tip[0], ybot - 60 * k); c2 = (tip[0] - 140 * gk, tip[1] - 10 * gk); ctrl = (p0, c1, c2, tip)
    hits = lambda c: arrow_hits_title(g, ox, oy, c, 2000, 1500)
    if i == len(ls) - 1 or not hits(ctrl):
        return ctrl
    ys = g["ays"]; gap = oy + (ys[i] + ink(ls[i])[3] + ys[i + 1] + ink(ls[i + 1])[1]) / 2
    xr = ox + g["ax"] + max(ink(l)[2] for l in ls[i + 1:])          # 下面几行的右端
    for f in (0.6, 0.9, 1.3, 1.8):
        if side == "top":
            tx = min(max(tip[0], xr + f * S), xmax)
            cand = (p0, (xr + 0.5 * f * S, gap), (tx + 18 * gk, max(gap + 0.5 * S, tip[1] - 140 * gk)), (tx, tip[1]))
        else:
            cand = (p0, (p0[0] + f * S, gap), (tip[0] - 140 * gk, gap), (tip[0], gap))
        if not hits(cand):
            return cand
    return ctrl


def arrow_hits_title(g, ox, oy, ctrl, W, H):
    """先在空白蒙版上试画箭头：会压到关键词以外的标题字（照片离标题太近时）就换排法。"""
    m = Image.new("L", (W, H)); hand_arrow([(ImageDraw.Draw(m), 255)], *ctrl, g["k"]); o = g["oval"]
    tm = title_mask((W, H), text_parts(g, ox, oy))
    return red_on_text(m, tm, (ox + o["keep"][0], ox + o["keep"][1]), (oy + o["rows"][0], oy + o["rows"][1])) > 0


# ----- 四宫格（01）、六宫格（12）：文字区同线稿四宫格（色块左上、两行标题、红圈），圆角外框分格，每格左上标签、下面照片 -----
def photo_cells(spec, bg, ratio, out):
    lay = spec["layout"]; n = len(spec["cells"]); W, H, M, k = ratio_ctx(ratio); e = round(40 * k)
    photos, warns = split_sheet(Image.open(bg).convert("RGB"), *PHOTO_SHEET[lay])
    found = [find_colour(p, 70) for p in photos]                  # 小格子噪声多，饱和度门槛 70（风格说明 3.1）
    for i, (p, (_, b)) in enumerate(zip(photos, found)):
        warns += photo_warnings(p, b, f"第 {i + 1} 张：")
    qc = {"ratio": ratio, "layout": lay, "style": "photo", "warnings": warns}; best = None
    if lay == "grid":
        configs = [("top", 2, 2, "top")] if H > W else [(sd, 2, 2, md) for sd in ("left", "top") for md in ("top", "side")]
    else:
        configs = [("top", 2, 3, "top")] if H > W else [(sd, 3, 2, "top") for sd in ("left", "top")]
    for ts in ((1.0, 0.9, 0.8, 0.7) if H > W else ts_options(W, H, ratio)):
        g = grid_text(spec, 116 * k * ts)
        tf = round((38 if ts >= 1 else 34) * k) if lay == "grid" else round(34 * k); labs = cell_labels(spec, tf)
        for side, cols, rows, mode in configs:
            if side == "left":
                gx0, gy0, gx1, gy1 = M + g["width"] + round(110 * g["k"]), e, W - e, H - e
                if g["height"] + M > H - M:
                    continue
            else:
                gx0, gx1, gy0, gy1 = e, W - e, M - 10 + g["height"] + round(95 * g["k"]), H - e
                if g["width"] + M > W - M:
                    continue
            cbx = cell_boxes(gx0, gy0, gx1, gy1, cols, rows, n)[0]; arts = [art_area(c, mode, tf, lab) for c, lab in zip(cbx, labs)]
            if any(a_[2] - a_[0] < 50 or a_[3] - a_[1] < 50 for a_ in arts):
                continue
            plan = dict(side=side, ts=ts, cols=cols, rows=rows, mode=mode, gx0=gx0, gy0=gy0, gx1=gx1, gy1=gy1, tf=tf)
            if H > W:
                best = (0, plan); break
            oy = max(M - 10, (H - g["height"]) // 2 - round(60 * g["k"])) if side == "left" else M - 10
            if arrow_hits_title(g, M, oy, oval_arrow(g, M, oy, side, gx0, gy0, gx1, k, gy1)[1], W, H):
                continue
            over = any(ink(t_)[2] - ink(t_)[0] > (c[2] - c[0] - 48 * k if mode == "top" else round(tf * 5.4)) for c, lab in zip(cbx, labs) for t_ in lab)
            sc = min(fit_score(p, b, a_) for p, (_, b), a_ in zip(photos, found, arts)) * ts_factor(ts) * (0.2 if over else 1)   # 标签超出格子的排法尽量不用
            if best is None or sc > best[0]:
                best = (sc, plan)
        if best and H > W:
            break
    if best is None:
        sys.exit(f"{ratio} {LAYOUT_CN[lay]}排不下：主标题或副标题太长，改短再出")
    p = best[1]; g = grid_text(spec, 116 * k * p["ts"]); gk = g["k"]
    im, rm, d, draws = photo_new(W, H)
    ox, oy = M, M - 10
    if p["side"] == "left":
        oy = max(M - 10, (H - g["height"]) // 2 - round(60 * gk))
    tm = draw_text_block(im, d, g, ox, oy)
    gx0, gy0, gx1, gy1 = p["gx0"], p["gy0"], p["gx1"], p["gy1"]; o = g["oval"]
    pts, ctrl = oval_arrow(g, ox, oy, p["side"], gx0, gy0, gx1, k, gy1); stroke(draws, pts, stroke_width(gk)); tip = ctrl[3]
    allp = pts + hand_arrow(draws, *ctrl, gk)[0]
    cells, xs, ys = cell_boxes(gx0, gy0, gx1, gy1, p["cols"], p["rows"], n)
    d.rounded_rectangle((gx0, gy0, gx1, gy1), radius=round(18 * k), outline=TITLE_INK, width=max(3, round(4 * k)))
    for x_ in xs[1:-1]:
        d.line((x_, gy0 + 2, x_, gy1 - 2), fill=TITLE_INK, width=max(2, round(2 * k)))
    for y_ in ys[1:-1]:
        d.line((gx0 + 2, y_, gx1 - 2, y_), fill=TITLE_INK, width=max(2, round(2 * k)))
    labs = cell_labels(spec, p["tf"]); pitch = round(p["tf"] * 1.32); lab_parts, cbs = [], []
    for i, (c, ph, (mask, box), lab) in enumerate(zip(cells, photos, found, labs)):
        cx, cy, ex, ey = c
        for j, t_ in enumerate(lab):
            bb = ink(t_); pos = (cx + round(24 * k) - bb[0], cy + round(22 * k) + j * pitch - bb[1]); im.alpha_composite(t_, pos); lab_parts.append((t_, pos))
            if bb[2] - bb[0] > (ex - cx - 48 * k if p["mode"] == "top" else round(p["tf"] * 5.4)):
                qc["warnings"].append(f"第 {i + 1} 格标签太长，超出格子")
        rect = art_area(c, p["mode"], p["tf"], lab)
        q, win = crop_for(ph, rect, box_centre(ph, box)); im.paste(q, rect[:2])
        if box:
            cb = map_box(box, win, rect); allp = allp + photo_oval(draws, cb, k, rect, 0.06, 6 if lay == "six" else 8, qc); cbs.append([round(v) for v in cb])
    if (p["side"] == "top" and not tip[1] < gy0 - 2) or (p["side"] == "left" and not tip[0] < gx0 - 2):
        qc["warnings"].append("箭头尖进了格子框")
    keep = ((ox + o["keep"][0], ox + o["keep"][1]), (oy + o["rows"][0], oy + o["rows"][1]))
    return photo_done(im, qc, rm, tm, keep, allp, out, lab_parts, plan=p, colour_boxes=cbs, box=[gx0, gy0, gx1, gy1], title_px=g["S"], margin=M,
                      photos=[list(art_area(c, p["mode"], p["tf"], lab)) for c, lab in zip(cells, labs)], tip=[round(tip[0]), round(tip[1])])


# ----- 三联竖条（09）：文字区同四宫格；三条竖照片中间留细白缝，每条下面一行标签、每条红圈；箭头同四宫格 -----
def photo_triptych(spec, bg, ratio, out):
    W, H, M, k = ratio_ctx(ratio)
    photos, warns = split_sheet(Image.open(bg).convert("RGB"), 3, 1)
    found = [find_colour(p) for p in photos]
    for i, (p, (_, b)) in enumerate(zip(photos, found)):
        warns += photo_warnings(p, b, f"第 {i + 1} 张：")
    qc = {"ratio": ratio, "layout": "triptych", "style": "photo", "warnings": warns}
    tf = round(38 * k); pitch = round(tf * 1.32); labs = cell_labels(spec, tf); lab_h = (max(len(l) for l in labs) - 1) * pitch + tf
    gap = round(14 * k); best = None
    for ts in ((1.0, 0.9, 0.8, 0.7) if H > W else ts_options(W, H, ratio)):
        g = grid_text(spec, 116 * k * ts)
        for side in (("top",) if H > W else ("left", "top")):
            if side == "top":
                if g["width"] + M > W - M:
                    continue
                oy = M - 10; gx0, gx1, gy0 = round(40 * k), W - round(40 * k), oy + g["height"] + round(110 * g["k"])
            else:
                if g["height"] + M > H - M:
                    continue
                oy = max(M - 10, (H - g["height"]) // 2 - round(60 * g["k"])); gx0, gx1, gy0 = M + g["width"] + round(110 * g["k"]), W - round(40 * k), round(40 * k)
            gy1 = H - round(40 * k) - lab_h - round(30 * k); sw = (gx1 - gx0 - 2 * gap) // 3
            rects = [(gx0 + i * (sw + gap), gy0, gx0 + i * (sw + gap) + sw, gy1) for i in range(3)]
            if sw < 80 * k or gy1 - gy0 < 250 * k:
                continue
            if H > W:
                best = (0, side, ts, oy, rects); break
            if arrow_hits_title(g, M, oy, oval_arrow(g, M, oy, side, gx0, gy0, gx1, k, gy1)[1], W, H):
                continue
            over = any(ink(t_)[2] - ink(t_)[0] > sw for lab in labs for t_ in lab)
            sc = min(fit_score(p, b, r) for p, (_, b), r in zip(photos, found, rects)) * ts_factor(ts) * (0.2 if over else 1)   # 标签比照片宽的排法尽量不用
            if best is None or sc > best[0]:
                best = (sc, side, ts, oy, rects)
        if best and H > W:
            break
    if best is None:
        sys.exit(f"{ratio} 三联竖条排不下：主标题或副标题太长，改短再出")
    _, side, ts, oy, rects = best; g = grid_text(spec, 116 * k * ts); gk = g["k"]; ox = M
    im, rm, d, draws = photo_new(W, H)
    tm = draw_text_block(im, d, g, ox, oy)
    o = g["oval"]; pts, ctrl = oval_arrow(g, ox, oy, side, rects[0][0], rects[0][1], rects[2][2], k, rects[0][3]); stroke(draws, pts, stroke_width(gk)); allp = list(pts)
    lab_parts, cbs = [], []
    for i, (ph, (mask, box), r, lab) in enumerate(zip(photos, found, rects, labs)):
        q, win = crop_for(ph, r, box_centre(ph, box)); im.paste(q, r[:2])
        if box:
            cb = map_box(box, win, r); allp += photo_oval(draws, cb, k, r, 0.06, qc=qc); cbs.append([round(v) for v in cb])
        for j, t_ in enumerate(lab):                               # 标签在每条正下方居中
            bb = ink(t_); pos = (r[0] + (r[2] - r[0] - (bb[2] - bb[0])) // 2 - bb[0], r[3] + round(22 * k) + j * pitch - bb[1])
            im.alpha_composite(t_, pos); lab_parts.append((t_, pos))
            if bb[2] - bb[0] > r[2] - r[0]:
                qc["warnings"].append(f"第 {i + 1} 条标签太长，比照片还宽")
    allp += hand_arrow(draws, *ctrl, gk)[0]; tip = ctrl[3]
    keep = ((ox + o["keep"][0], ox + o["keep"][1]), (oy + o["rows"][0], oy + o["rows"][1]))
    return photo_done(im, qc, rm, tm, keep, allp, out, lab_parts, plan={"side": side, "ts": ts}, colour_boxes=cbs, photos=[list(r) for r in rects],
                      title_px=g["S"], margin=M, tip=[round(tip[0]), round(tip[1])])


# ----- 左右对比（10）：标题左上；两张照片同样裁法并排贴底，上方各一行标签；左边全黑白不画红线，箭头从色块右边甩出去再钩进右图留色区 -----
def photo_compare(spec, bg, ratio, out):
    W, H, M, k = ratio_ctx(ratio)
    photos, warns = split_sheet(Image.open(bg).convert("RGB"), 2, 1)
    found = [find_colour(p) for p in photos]; plain = [c["subject"].strip().lower() in NO_COLOUR for c in spec["cells"]]
    for i, (p, (_, b), bw_) in enumerate(zip(photos, found, plain)):
        if bw_ and b:
            warns.append(f"第 {i + 1} 张应该全黑白，却找到了彩色，检查底图")
        elif not bw_:
            warns += photo_warnings(p, b, f"第 {i + 1} 张：")
    qc = {"ratio": ratio, "layout": "compare", "style": "photo", "warnings": warns}
    tf = round(46 * k); pitch = round(tf * 1.32); labs = [[photo_label(x, tf) for x in c["label"].split("|")] for c in spec["cells"]]
    lab_h = (max(len(l) for l in labs) - 1) * pitch + tf; gap = round(20 * k); bottom = H - round(40 * k); best = None
    for ts in ((1.0,) if H > W else ts_options(W, H, ratio)):
        t = photo_title(spec, W, M, k, ts); sb = sub_block(spec["sub"], 76 * k * ts); bw, bh = sb[1], sb[2]
        pw = (W - round(80 * k) - gap) // 2; bx = W - round(230 * k) - bw            # 色块右边留约 230px，让箭头从右边甩出去再钩回
        three = len(title_lines(spec)) > 2          # 三行主标题高、窄：色块和标题隔 150px 就可以并排（两行仍要 300px）
        if bx > t["box"][2] + (150 if three and H < W else 300) * k:    # 横版：色块和标题同一行，照片从标题下面开始；照片最宽 1.25 倍高，靠右
            by = round((M + t["box"][3] - bh) / 2); lab_y = t["box"][3] + round(70 * k); ph_top = lab_y + lab_h + round(22 * k)
            ph_h = bottom - ph_top; pw = min(pw, round(1.25 * ph_h))
        else:                                     # 3:4：照片按原比例最高 760，色块在标题和标签之间居中
            ph_h = min(round(pw * photos[0].height / photos[0].width), round(760 * k)); ph_top = bottom - ph_h
            lab_y = ph_top - round(22 * k) - lab_h; by = round((t["box"][3] + lab_y) / 2 - bh / 2)
            if by < t["box"][3] + 20 * k:
                if not three:
                    continue
                # 三行主标题：标题高了放不下原高度的照片 → 照片矮一点（等比裁切），色块仍在标题和标签之间居中，
                # 上下各留约半个色块高，箭头才有地方从右边甩出去再钩回、不穿右图的标签
                lab_y = t["box"][3] + 2 * bh + round(40 * k); by = round((t["box"][3] + lab_y) / 2 - bh / 2)
                ph_top = lab_y + lab_h + round(22 * k); ph_h = bottom - ph_top
        if ph_h < 0.2 * H:
            continue
        xr = W - round(40 * k); rects = [(xr - 2 * pw - gap, ph_top, xr - pw - gap, bottom), (xr - pw, ph_top, xr, bottom)]
        sc = min(fit_score(p, b, r, box_centre(p, None)) for p, (_, b), r, bw_ in zip(photos, found, rects, plain) if not bw_) if not all(plain) else 1
        sc *= ts_factor(ts)
        if best is None or sc > best[0]:
            best = (sc, t, sb, bx, by, lab_y, rects)
    if best is None:
        sys.exit(f"{ratio} 左右对比排不下：主标题或副标题太长，改短再出")
    _, t, sb, bx, by, lab_y, rects = best
    im, rm, d, draws = photo_new(W, H)
    tm, allp, keep = put_title(im, draws, t, qc, W, M)
    blk = put_block(im, d, sb, bx, by); lab_parts, cbs = [], []; tip = None
    for i, (ph, (mask, box), r, lab, bw_) in enumerate(zip(photos, found, rects, labs, plain)):
        q, win = crop_for(ph, r, box_centre(ph, None)); im.paste(q, r[:2])          # 两张同样裁法（都对准照片中心），方便对比
        for j, t_ in enumerate(lab):
            bb = ink(t_); pos = (r[0] + (r[2] - r[0] - (bb[2] - bb[0])) // 2 - bb[0], lab_y + j * pitch - bb[1])
            im.alpha_composite(t_, pos); lab_parts.append((t_, pos))
        if box and not bw_:
            cb = map_box(box, win, r); allp = allp + photo_oval(draws, cb, k, r, 0.06, qc=qc); cbs.append([round(v) for v in cb])
            tip = snap_tip(mask, win, r, tip_in(cb, 0.10))
    if tip:                                                       # 箭头强制从色块右边出、绕过右图的标签（风格说明“踩过的坑”2）
        allp += arrow_from(draws, blk, t["oval"], tip, W, H, k, tm, keep, side="right", labels=lab_parts)
    return photo_done(im, qc, rm, tm, keep, allp, out, lab_parts, colour_boxes=cbs, photos=[list(r) for r in rects], label=list(blk),
                      tip=tip and [round(tip[0]), round(tip[1])], title_px=t["S"], margin=M)


# ----- 竖图满版（08）：照片铺满，标题压在上方安静区，垫米白撕纸块；色块在标题下左对齐，箭头甩到墙面再钩进留色区 -----
def torn_patch(x0, y0, x1, y1, k, seed=3):
    """米白撕纸块的轮廓：四边每 16px 一个点、沿法线方向抖 ±7px。"""
    rnd = random.Random(seed); pts = []; amp, step = 7 * k, 16 * k
    c = [(x0, y0 + 6 * k), (x1, y0), (x1 - 8 * k, y1), (x0 + 4 * k, y1 + 10 * k)]
    for i in range(4):
        a_, b_ = c[i], c[(i + 1) % 4]; n_ = max(2, int(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) / step))
        nx, ny = b_[1] - a_[1], -(b_[0] - a_[0]); L = math.hypot(nx, ny)
        for j in range(n_):
            t_ = j / n_; jit = rnd.uniform(-amp, amp)
            pts.append((a_[0] + (b_[0] - a_[0]) * t_ + jit * nx / L, a_[1] + (b_[1] - a_[1]) * t_ + jit * ny / L))
    return pts


def photo_full(spec, bg, ratio, out):
    W, H, M, k = ratio_ctx(ratio)
    img, mask, box, warns = load_photo(bg)
    qc = {"ratio": ratio, "layout": "full", "style": "photo", "warnings": warns}
    s_ = max(W / img.width, H / img.height); big = img.resize((round(img.width * s_), round(img.height * s_)), Image.LANCZOS)
    fx = box_centre(img, box)[0] * s_
    offx = int(min(max(fx - W / 2, 0), big.width - W)); offy = min(round(60 * k), big.height - H)   # 上面只裁 60px：安静区留给标题（踩过的坑 3）
    im, rm, d, draws = photo_new(W, H); im.paste(big.crop((offx, offy, offx + W, offy + H)), (0, 0))
    win = (offx / s_, offy / s_, W / s_, H / s_); rect = (0, 0, W, H)
    ts = TEXT_SCALE.get(ratio, 1.0); t = photo_title(spec, W, M, k, ts); sb = sub_block(spec["sub"], 76 * k * ts)
    by = t["box"][3] + round(40 * k); py1 = by + sb[2] + round(36 * k)
    poly = torn_patch(M - 44 * k, M - 40 * k, max(t["box"][2], M + sb[1]) + 60 * k, py1, k)
    sh = Image.new("L", (W, H)); ImageDraw.Draw(sh).polygon([(x + 6 * k, y + 9 * k) for x, y in poly], fill=110)
    im.alpha_composite(Image.merge("RGBA", [Image.new("L", (W, H), 0)] * 3 + [sh.filter(ImageFilter.GaussianBlur(10 * k))]))
    pm = Image.new("L", (W, H)); ImageDraw.Draw(pm).polygon(poly, fill=248)
    im.alpha_composite(Image.merge("RGBA", [Image.new("L", (W, H), c) for c in PHOTO_PAPER] + [pm]))
    tm, allp, keep = put_title(im, draws, t, qc, W, M)
    blk = put_block(im, d, sb, M, by)
    if box:
        cb = map_box(box, win, rect); allp = allp + photo_oval(draws, cb, k, rect, qc=qc)
        tip = point_in(cb, spec["arrow_at"]) if spec.get("arrow_at") else snap_tip(mask, win, rect, tip_in(cb))
        allp += arrow_from(draws, blk, t["oval"], tip, W, H, k, tm, keep)
        qc.update(colour_boxes=[[round(v) for v in cb]], tip=[round(tip[0]), round(tip[1])])
        if cb[1] < py1 and cb[0] < max(t["box"][2], M + sb[1]) + 60 * k:
            qc["warnings"].append("留色区域伸到了标题纸块下面，检查成图；必要时重出底图（上方要留安静区）")
    return photo_done(im, qc, rm, tm, keep, allp, out, photos=[list(rect)], label=list(blk), patch_bottom=py1, title_px=t["S"], margin=M)


# ----- 拍立得拼贴（11）：标题左上，色块靠右；四张白边拍立得错落叠放带淡投影，只给最上面一张（第 1 张）画红圈和箭头 -----
POLAROID = ((600, 400, -4, 690, 820), (470, 330, 5, 300, 770), (480, 336, 6, 305, 1150), (450, 316, -7, 790, 1195))   # 第 1–4 张：宽、高、角度、中心（3:4）
POLAROID_GROUP = (33, 549, 1040, 1400)                        # 3:4 时四张整体的外框，横版按它等比缩放平移


def photo_polaroid(spec, bg, ratio, out):
    W, H, M, k = ratio_ctx(ratio)
    photos, warns = split_sheet(Image.open(bg).convert("RGB"), 2, 2)
    found = [find_colour(p) for p in photos]
    for i, (p, (_, b)) in enumerate(zip(photos, found)):
        warns += photo_warnings(p, b, f"第 {i + 1} 张：")
    qc = {"ratio": ratio, "layout": "polaroid", "style": "photo", "warnings": warns}
    gx0, gy0, gx1, gy1 = POLAROID_GROUP; best = None
    for ts in ((1.0,) if H > W else ts_options(W, H, ratio)):
        t = photo_title(spec, W, M, k, ts); sb = sub_block(spec["sub"], 76 * k * ts); bw, bh = sb[1], sb[2]
        for side in (("top",) if H > W else ("left", "top")):
            if H > W:
                blk, sc, dx, dy = (W - M - bw, t["box"][3] + round(70 * k)), 1.0, 0, 0
                if blk[1] + bh + 30 * k > gy0 * k:  # 三行主标题时色块会压到最上面那张：整组等比缩小、往下让（两行碰不到这里）
                    reg = (round(40 * k), round(blk[1] + bh + 80 * k), W - round(40 * k), H - round(40 * k))   # 下面留 80px 给箭头拐弯
                    sc = min((reg[2] - reg[0]) / (gx1 - gx0), (reg[3] - reg[1]) / (gy1 - gy0))
                    dx = reg[0] + ((reg[2] - reg[0]) - (gx1 - gx0) * sc) / 2 - gx0 * sc; dy = reg[1] + ((reg[3] - reg[1]) - (gy1 - gy0) * sc) / 2 - gy0 * sc
            else:
                if side == "left":                  # 文字一栏在左（色块在标题下面），四张在右边
                    blk = (M, t["box"][3] + round(0.5 * t["S"])); reg = (max(t["box"][2], M + bw) + round(0.6 * t["S"]), round(40 * k), W - round(40 * k), H - round(40 * k))
                else:                               # 色块和标题同一行（右边），四张在标题下面
                    bx = W - M - bw
                    if bx < t["box"][2] + 300 * k:
                        continue
                    blk = (bx, round((M + t["box"][3] - bh) / 2)); reg = (round(40 * k), t["box"][3] + round(60 * k), W - round(40 * k), H - round(40 * k))
                sc = min((reg[2] - reg[0]) / (gx1 - gx0), (reg[3] - reg[1]) / (gy1 - gy0))
                dx = reg[0] + ((reg[2] - reg[0]) - (gx1 - gx0) * sc) / 2 - gx0 * sc; dy = reg[1] + ((reg[3] - reg[1]) - (gy1 - gy0) * sc) / 2 - gy0 * sc
            score = sc * ts_factor(ts)
            if best is None or score > best[0]:
                best = (score, side, t, sb, blk, sc, dx, dy)
    _, side, t, sb, blk, sc, dx, dy = best
    im, rm, d, draws = photo_new(W, H)
    for i in (2, 3, 1, 0):                                         # 后面的先贴，第 1 张在最上面
        w_, h_, ang, cx, cy = POLAROID[i]; w_, h_ = round(w_ * sc * k), round(h_ * sc * k); bd, bt = round(16 * sc * k), round(54 * sc * k)
        if side == "left" and H < W:                               # 文字在左：整组左右镜像，主角那张挨着色块，箭头不横穿别的照片
            cx, ang = gx0 + gx1 - cx, -ang
        mask, box = found[i]; q, win = crop_for(photos[i], (0, 0, w_, h_), box_centre(photos[i], box))
        fr = Image.new("RGBA", (w_ + 2 * bd, h_ + bd + bt), (252, 252, 250, 255)); fr.paste(q, (bd, bd))
        m = Image.new("L", fr.size)
        if box:
            ImageDraw.Draw(m).rectangle([round(v) for v in map_box(box, win, (bd, bd, bd + w_, bd + h_))], fill=255)
        fr = fr.rotate(ang, Image.BICUBIC, expand=True); m = m.rotate(ang, Image.NEAREST, expand=True)
        px, py = round(cx * sc + dx - fr.width / 2), round(cy * sc + dy - fr.height / 2)
        shd = fr.getchannel("A").filter(ImageFilter.GaussianBlur(9 * sc)).point(lambda v: v * 0.38)
        im.alpha_composite(Image.merge("RGBA", [Image.new("L", fr.size, 30)] * 3 + [shd]), (px + round(8 * sc), py + round(12 * sc)))
        im.alpha_composite(fr, (px, py)); top = (m, px, py)
    d = ImageDraw.Draw(im); draws = [(d, RED), draws[1]]
    tm, allp, keep = put_title(im, draws, t, qc, W, M)
    blk = put_block(im, d, sb, *blk)
    m, px, py = top; b = m.getbbox()
    if b:
        cb = [px + b[0], py + b[1], px + b[2], py + b[3]]; allp = allp + photo_oval(draws, cb, k, None, 0.06)
        tip = tip_in(cb); allp += arrow_from(draws, blk, t["oval"], tip, W, H, k, tm, keep); qc.update(colour_boxes=[cb], tip=[round(tip[0]), round(tip[1])])
    return photo_done(im, qc, rm, tm, keep, allp, out, plan={"side": side, "scale": round(sc, 3)}, label=list(blk), title_px=t["S"], margin=M)


def render_photo(spec, bg, ratio, out):
    return {"single": photo_single, "number": photo_number, "zoom": photo_zoom, "grid": photo_cells, "six": photo_cells, "compare": photo_compare,
            "triptych": photo_triptych, "full": photo_full, "polaroid": photo_polaroid}[spec.get("layout", "single")](spec, bg, ratio, out)


def cmd_render(a):
    # 比例之间多打空格、用了中文逗号或冒号都认（"4:3, 16:9" 以前会报英文 KeyError）
    text = "".join(a.ratios.split()).replace("，", ",").replace("：", ":")
    ratios = list(RATIOS) if text == "all" else list(dict.fromkeys(r for r in text.split(",") if r))
    bad = [r for r in ratios if r not in RATIOS]
    if bad or not ratios:
        sys.exit(f"不认识的比例：{'、'.join(bad) or '（没写）'}。只能用 {'、'.join(RATIOS)}，多个用逗号隔开（如 4:3,16:9），或写 all")
    spec = load_spec(a.dir)
    name = "".join(c for c in spec["line1"] if c not in '/\\:*?"<>|')
    lay = spec.get("layout", "single")
    draw = {"grid": render_grid, "compare": render_panels, "steps": render_panels, "zoom": render_zoom, "number": render_number}.get(lay, render_one)
    photo = spec.get("style") == "photo"                           # 2026-11-20 实拍留色
    if photo:
        draw = render_photo
    report = []
    for r in ratios:
        if photo:
            kind = RATIOS[r][2] if lay == "full" else photo_kinds(spec)[0]
            bg = photo_bg(a.dir, kind)
        else:
            kind = lay if lay in OWN_BG else RATIOS[r][2]
            bg = bg_path(a.dir, kind)
            if kind == "landscape" and not bg.exists():            # 2026-11-21（新6）横版默认复用用户确认过的竖版画
                bg, kind = bg_path(a.dir, "portrait"), "portrait"
        if not bg.exists():
            sys.exit(f"缺少底图 {bg}，先运行 gen {kind}")
        sides = [] if photo else frame_sides(bg)                    # 2026-11-21（261118 新11）整圈外框：报警不拦（反馈单定的是自检报警）
        out = Path(a.dir) / f"{OUT_PREFIX}{name}_{r.replace(':', 'x')}.jpg"
        qc = draw(spec, bg, r, out); qc["file"] = str(out); report.append(qc)
        if len(sides) >= 3:
            qc["warnings"].append(FRAME_MSG.format(sides="、".join(sides)))
        if not photo and kind == "landscape":                       # 另生的横版底图：和竖版不是同一张画（261117 横版画成了另一张图）
            qc["warnings"].append("横版和竖版不是同一张画（用的是另生的 bg_landscape.png），先给用户看（要和竖版一致：把它移走再 render，横版就复用竖版画）")
        if not photo:                                               # 2026-11-21（新7）换色后残留的陶土/橙色（光画成渐变、光晕时会有）
            left = warm_left(bg)
            if left > WARM_LEFT:
                qc["warnings"].append(f"底图换色后还残留 {left:.1%} 的粉橙色（多半是光被画成了渐变或光晕），成图会发粉橙，建议重出底图")
        print("封面已出：", out, ("| 警告：" + "；".join(qc["warnings"])) if qc["warnings"] else "")
    (work(a.dir) / f"qc_{'_'.join(x.replace(':', 'x') for x in ratios)}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    done = [p for p in (Path(a.dir) / f"{OUT_PREFIX}{name}_{r.replace(':', 'x')}.jpg" for r in RATIOS) if p.exists()]
    if len(done) > 1:   # overview of every ratio made so far
        hs = 720; tiles = [Image.open(p) for p in done]; tiles = [t.resize((round(t.width * hs / t.height), hs), Image.LANCZOS) for t in tiles]
        ov = Image.new("RGB", (sum(t.width for t in tiles) + 24 * (len(tiles) + 1), hs + 48), "white"); x = 24
        for t in tiles:
            ov.paste(t, (x, 24)); x += t.width + 24
        ov.save(Path(a.dir) / "总览.jpg", quality=88); print("总览图：", Path(a.dir) / "总览.jpg")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("init", help="写封面文字和场景"); i.add_argument("dir")
    i.add_argument("--line1", required=True, help="主标题第一行；不给 --line2 时按逗号拆成两行")
    i.add_argument("--sub", help="副标题（可不给：不画色块，红箭头从标题关键词的红圈出发指向主角）")
    for k in ("line2", "scene", "subject"):
        i.add_argument("--" + k)
    i.add_argument("--line3", help="主标题第三行（可不给；给了就要同时给 --line2）")
    i.add_argument("--circle", help="主标题里要用红橙手画圈圈住的关键词（四宫格、左右对比必填；三步流程可选）")
    i.add_argument("--layout", choices=["single", "grid", "compare", "number", "steps", "zoom", "full", "triptych", "polaroid", "six"], default="single",
                   help="single 单主角（默认）；grid 四宫格；compare 左右对比；number 数字大字；steps 三步流程；zoom 局部放大；"
                        "只给实拍留色：full 竖图满版、triptych 三联竖条、polaroid 拍立得拼贴、six 六宫格")
    i.add_argument("--style", choices=["line", "photo"], default="line", help="画风：line 线稿插画（默认）；photo 实拍留色（黑白照片、只有问题部位留色）")
    i.add_argument("--grey", help="实拍留色：提示词里点名要保持灰色、容易被误上色的东西（英文，如 \"veneer, bed and walls\"）")
    i.add_argument("--cell", action="append", help="每格标签，两行用 | 隔开；四宫格、三步流程自动加 ①②③④，左右对比不加")
    i.add_argument("--cell-scene", action="append", help="每格画什么（英文）")
    i.add_argument("--cell-subject", action="append", help="每格上色的部分（英文）")
    i.add_argument("--number", help="数字大字版式的大数字，如 400")
    i.add_argument("--unit", help="数字后面的单位，如 块钱（可不给）")
    i.add_argument("--zoom-at", help="局部放大：小圆环在整体小图里的位置，左右,上下 各 0–1，默认 0.5,0.5（正中）")
    i.add_argument("--arrow-at", help="单主角：箭头落在主角上色部分（实拍是留色区）里的位置，左右,上下 各 0–1，如 0.5,0.95 指主角最下面；默认指主角顶部")
    i.add_argument("--small-subject", action="store_true", help="主角本来就是小物件（墙上的开关、插座、龙头）：上色部分很小也认它是主角、照画箭头（默认主角上色不到画面 0.4%% 会报“没找到主角”）")
    g = sp.add_parser("gen", help="生成底图（竖版、横版，或四宫格、左右对比、三步流程、局部放大各自的一张图）"); g.add_argument("dir")
    g.add_argument("kind", choices=["portrait", "landscape", "grid", "compare", "steps", "zoom", "photo", "triptych", "six", "polaroid"]); g.add_argument("--task-only", action="store_true", help="只写任务单，不生图")
    r = sp.add_parser("render", help="排版出成图"); r.add_argument("dir"); r.add_argument("--ratios", default="3:4", help="3:4、4:3,16:9 或 all")
    for x in (i, g, r): x.add_argument("--ip", help="用哪个 IP 的封面风格（不写按 DIR 往上找 项目.json 的 ip，再不行用默认 IP）")
    a = p.parse_args()
    apply_ip(a.dir, a.ip)
    {"init": cmd_init, "gen": cmd_gen, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    main()
