#!/usr/bin/env python3
"""Deterministic subtitle preparation, reusable presets and navigation rendering."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import wave
from fractions import Fraction

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("DBS_VIDEO_NAV_HOME", str(ROOT / "配置")))   # 2026-10-29：默认用 NAS 上这份，不读 Mac 的 ~/.dbs

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write(path, data):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def number(value):
    # Only numeric seconds or explicit HH:MM:SS.mmm. Never guess MM:SS vs frames.
    if isinstance(value, str) and ":" in value:
        if not re.fullmatch(r"\d{2,}:\d{2}:\d{2}[.,]\d{3}", value):
            raise ValueError("时间必须为数值秒或 HH:MM:SS.mmm，拒绝歧义时间：" + value)
        h, m, s = value.replace(",", ".").split(":")
        if int(m) >= 60 or float(s) >= 60:
            raise ValueError("非法时间：" + value)
        value = int(h) * 3600 + int(m) * 60 + float(s)
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("时间必须为非负有限值")
    return result

def stamp(value):
    ms = round(value * 1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}"

def subtitles(path):
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    entries = []
    for index, block in enumerate(re.split(r"\n\s*\n", text.strip()), 1):
        lines = block.splitlines()
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            raise ValueError(f"第 {index} 个字幕块没有时间戳")
        match = re.fullmatch(r"\s*(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s*", lines[timing])
        if not match:
            raise ValueError(f"第 {index} 个字幕块时间戳无效")
        start, end = map(number, match.groups())
        content = "\n".join(lines[timing + 1:])
        if end <= start or not content.strip():
            raise ValueError(f"第 {index} 个字幕块为空或持续时间无效")
        entries.append(dict(id=index, start=start, end=end, text=content))
    if not entries:
        raise ValueError("字幕为空")
    warnings = []
    if entries != sorted(entries, key=lambda x: (x["start"], x["id"])):
        warnings.append("字幕原始顺序异常，已按时间排序；原始 id 保留")
    entries.sort(key=lambda x: (x["start"], x["id"]))
    furthest = 0
    for row in entries:
        if row["start"] < furthest:
            warnings.append(f"字幕 {row['id']} 与前文存在时间重叠，需审核板块边界")
        furthest = max(furthest, row["end"])
    return entries, warnings

def executable(name, override=None):
    result = override or os.environ.get(name.upper()) or shutil.which(name)
    if not result and name == "ffmpeg":
        try:
            import imageio_ffmpeg
            result = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            pass
    if not result:
        raise ValueError(f"缺少 {name}，请提供路径或安装依赖")
    return result

def probe(path, tool=None):
    command = [executable("ffprobe", tool), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    result = json.loads(subprocess.check_output(command, text=True))
    return result

def preset(name=None):
    if name:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ValueError("预设名仅支持英文小写、数字、下划线和连字符")
        return read(CONFIG / "presets" / (name + ".json"))
    config = CONFIG / "config.json"
    if config.exists():
        return preset(read(config)["default_preset"])
    return read(ROOT / "assets/blue.json")

def validate(project, base):
    if project.get("schema_version") != 1:
        raise ValueError("不支持的项目版本")
    target = project["target"]
    w, h, nh = (target[k] for k in ("width", "height", "nav_height"))
    if any(type(x) is not int or x <= 0 or x % 2 for x in (w, h, nh)) or nh >= h:
        raise ValueError("画布与导航尺寸必须为正偶数，导航高度小于画布高度")
    try:
        fps = Fraction(str(target["fps"]))
    except (ValueError, ZeroDivisionError):      # "30/0" 分母为零、"abc" 不是数字
        raise ValueError(f"目标帧率写法不对：{target['fps']!r}。请写成数字（如 30）或分数字符串（如 \"30000/1001\"），分母不能是 0") from None
    if fps <= 0 or fps > 240:
        raise ValueError("目标帧率必须在 (0, 240]，分数帧率用 30000/1001 等字符串")
    duration = number(project["duration"])
    start, end = map(number, project["coverage"])
    if not 0 <= start < end <= duration:
        raise ValueError("覆盖时间必须位于原片总时长内")
    if project["source"].get("video"):
        video = (base / project["source"]["video"]).resolve()
        stat = video.stat()
        for key, actual in (("video_size", stat.st_size), ("video_mtime_ns", stat.st_mtime_ns)):
            if key in project["source"] and project["source"][key] != actual:
                raise ValueError("原视频版本已变化，需重新核对时间轴")
    source = (base / project["source"]["subtitles"]).resolve() if project["source"].get("subtitles") else None
    entries, warnings = subtitles(source) if source else ([], [])
    if source and digest(source) != project["source"].get("sha256"):
        raise ValueError("字幕已变化：请重新分析或核对章节，禁止沿用旧时间轴")
    if max((x["end"] for x in entries), default=0) > duration + 1 / float(fps):
        raise ValueError("字幕超出原片总时长，需核对素材版本")
    if warnings and not project.get("subtitle_warnings_reviewed"):
        raise ValueError("字幕异常尚未审核：" + "; ".join(warnings[:5]))
    style = project.get("style") or preset(project.get("preset"))
    default = read(ROOT / "assets/blue.json")
    style = {**default, **style}
    if style["background_mode"] not in BACKGROUND_MODES:
        raise ValueError(f"background_mode 只能是 {'、'.join(BACKGROUND_MODES)}，现在是 {style['background_mode']!r}")
    style.update(BACKGROUND_MODES[style["background_mode"]])      # 底的三种做法一起带上它的字色和阴影；opaque 什么都不改，老项目一个像素不变
    for key in ("background_alpha", "shadow_alpha"):
        if not isinstance(style[key], (int, float)) or isinstance(style[key], bool) or not 0 <= style[key] <= 1:
            raise ValueError(f"{key} 必须是 0 到 1 之间的数")
    if not isinstance(style["label_squeeze_min"], (int, float)) or isinstance(style["label_squeeze_min"], bool) or not 0.3 <= style["label_squeeze_min"] <= 1:
        raise ValueError("label_squeeze_min（字最多压窄到几成）必须在 0.3 到 1 之间，1 就是不压窄")
    for key in ("background", "foreground", "progress", "track", "track_current", "label_dim", "caret"):
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", style[key]):
            raise ValueError(f"{key} 必须为 #RRGGBB")
    for key in default:
        if key.endswith("_ratio") and (not isinstance(style[key], (int, float)) or not 0 < style[key] < 0.5):
            raise ValueError(f"非法布局比例：{key}")
    if style["title_ratio"] <= style["question_ratio"]:
        raise ValueError("第一行字号必须大于第二行")
    def frame(t):
        return math.floor(Fraction(str(t)) * fps + Fraction(1, 2))
    first, last = frame(start), frame(end)
    if last - first < 2:
        raise ValueError("覆盖区间至少需要两帧")
    cursor, rows, closed, active = start, [], set(), None
    for index, segment in enumerate(project["segments"], 1):
        a, b = number(segment["start"]), number(segment["end"])
        if abs(a - cursor) > 0.000001 or b <= a:
            raise ValueError("板块必须连续、无重叠、无缺口；跳过开头用 coverage")
        if any(not isinstance(segment.get(k), str) or not segment[k].strip() or "\n" in segment[k] for k in ("chapter", "title")):
            raise ValueError("章节 id 和标题必须为非空单行文字")
        if not isinstance(segment.get("question", ""), str) or "\n" in segment.get("question", ""):
            raise ValueError("第二行必须为单行文字或空字符串")
        if segment["chapter"] != active:
            if segment["chapter"] in closed:
                raise ValueError("大章节必须连续；回顾内容应使用独立章节 id")
            if active is not None:
                closed.add(active)
            active = segment["chapter"]
        fa, fb = frame(a), frame(b)
        if fb <= fa:
            raise ValueError("存在不足一帧的板块")
        selected = [x for x in entries if a <= x["start"] < b]
        if not selected:
            evidence = segment.get("visual_evidence", [])
            if not evidence or any(not isinstance(e, dict) or not a <= number(e.get("time", -1)) < b or not isinstance(e.get("note"), str) or not e["note"].strip() for e in evidence):
                raise ValueError(f"板块 {index} 没有字幕；必须提供区间内实际查看画面的 visual_evidence 时间和说明")
        rows.append({**segment, "question": segment.get("question", ""), "start": a, "end": b, "frame_start": fa-first, "frame_end": fb-first,
                     "first_subtitle": selected[0] if selected else None, "last_subtitle": selected[-1] if selected else None, "index": index})
        cursor = b
    if not rows or abs(cursor - end) > 0.000001:
        raise ValueError("板块未覆盖全部 coverage 区间")
    chapters = None
    if style["background_alpha"] < 1 and not any("label" in s for s in project["segments"]):
        raise ValueError("半透明／透明底只支持短标签样式（板块写 label）；旧的两行文字样式只能不透明黑底")
    if any("label" in s for s in project["segments"]):      # 短标签样式：进度条按大章节分段，每段下面写几个字
        chapters = []
        for row in rows:
            if chapters and chapters[-1]["chapter"] == row["chapter"]:
                chapters[-1].update(end=row["end"], frame_end=row["frame_end"])
                continue
            label = row.get("label")
            if not isinstance(label, str) or "\n" in label:
                raise ValueError(f"第 {len(chapters)+1} 段缺短标签：每个大章节的第一个板块都要写 label（单行；实在放不下可以写空字符串）")
            chapters.append(dict(chapter=row["chapter"], title=row["title"], label=label.strip(), start=row["start"], end=row["end"],
                                 frame_start=row["frame_start"], frame_end=row["frame_end"], first_subtitle=row["first_subtitle"]))
        unit = min(w, h)      # 字号、线粗、高度按画布短边算：横屏 3840 宽时字和竖屏一样大，不会撑成两倍
        bar(style, w, unit, last-first, chapters)
        font = round(unit*style["label_ratio"])
        smin, pad = style["label_squeeze_min"], squeeze_pad(unit)
        for n, c in enumerate(chapters, 1):
            c["max_chars"] = max(0, int(c["w"] // font))
            c["squeeze"] = 1.0
            wide = label_width(c["label"], font) if c["label"] else 0      # 空标签不占宽度，极短的段也能写
            if wide > c["w"]:      # 放不下先把字横向压窄（字高不变），压到下限还放不下才报错；这里按保守估算，真正压多少 render 时按实测宽度算
                k = (c["w"] - pad) / wide
                if k < smin:
                    more = f"；字太宽时会自动横向压窄，压到最窄 {smin:.0%} 也只能放 {max(0, int((c['w'] - pad) / (font * smin)))} 个汉字" if smin < 1 else ""
                    raise ValueError(f"第 {n} 段短标签「{c['label']}」放不下：这一段最多 {c['max_chars']} 个汉字{more}，请换短一点的词（不缩小字号、不截断）")
                c["squeeze"] = round(k, 4)
        line = math.ceil(font*LABEL_LINE)
        need = round(unit*style["label_top_ratio"]) + line
        if rise(style, unit, nh)[1] + line > nh:
            raise ValueError(f"导航高度不够：短标签样式在这个宽度下至少要 {need + need % 2}，现在是 {nh}")
    return dict(rows=rows, style=style, fps=str(fps), width=w, height=nh, frames=last-first, chapters=chapters, unit=min(w, h),
                placement=first / float(fps), end=last / float(fps), warnings=warnings,
                source_path=str(source) if source else None, source_sha256=digest(source) if source else None)

# 导航条底的三种做法（2026-11-21 新19：261117 用户要“不要黑底”，本片是做成半透明深色底）。background_mode 写进 style，一起决定底的透明度、没播到的标签字色和字的暗影。
# opaque：不透明黑底（老样子）；dim：半透明深色底（45% 黑）＋标签暗影，亮画面上白字也看得清；clear：完全透明底，靠加重的暗影托字。
BACKGROUND_MODES = {
    "opaque": {},
    "dim": {"background_alpha": 0.45, "label_dim": "#C4C4C4", "shadow_alpha": 0.6},
    "clear": {"background_alpha": 0.0, "label_dim": "#E6E6E6", "shadow_alpha": 0.9},
}

def squeeze_pad(unit):
    """字被压窄时，两侧给相邻标签留的总空隙（短边 2160 时 12 像素）。"""
    return max(2, round(unit*0.0055))

LABEL_LINE = 1.45      # 短标签一行占的高度 ÷ 字号：PingFang 在 AppKit 里实测 1.40（含行距，取整后最多约 1.41），留余量；validate、swift、Pillow 都用这一个数

def label_width(text, font):
    """短标签宽度的保守估算（宁大勿小）：按 PingFang 粗体实测每类字符的最宽值再留余量。"""
    total = 0
    for ch in text:
        if ch in "WMmw@%":
            k = 1.0       # 宽字母：W 实测 0.95、% 0.99
        elif ch == " ":
            k = 0.35      # 空格 0.33
        elif "A" <= ch <= "Z":
            k = 0.8       # 其它大写最宽 O/Q 0.78
        elif ch.isascii() and ch.isalnum():
            k = 0.65      # 数字 0.60，其它小写最宽 0.60
        elif ch.isascii() and ch.isprintable():
            k = 0.8       # 其它英文标点最宽 & 0.75
        else:
            k = 1.0       # 汉字、全角标点、emoji 和其它非 ASCII 字符按全宽
        total += font*k
    return total

def bar(style, w, unit, count, chapters):
    """短标签样式的横向位置：算出每段的 x 和宽度，校验、画图、编码共用一套数。"""
    margin = round(w*style["margin_ratio"])
    tw = w-2*margin
    gap = max(2, round(unit*style["label_gap_ratio"]))
    edges = [margin+round(tw*c["frame_start"]/count) for c in chapters] + [margin+tw]
    for i, c in enumerate(chapters):
        c["x"] = edges[i] + (gap-gap//2 if i else 0)
        c["w"] = max(0, edges[i+1] - (gap//2 if i < len(chapters)-1 else 0) - c["x"])      # 比段间小缝还窄的段宽度记 0
    return margin, tw, gap, edges

def rise(style, w, nh):
    """短标签样式的纵向位置：导航条比内容高时（如老片子的 340；新片子是 224，不用挪），整体往下挪到上下居中。返回 (底线 y, 标签顶 y)。"""
    top = style["bar_top_ratio"]+style["track_height_ratio"]-style["caret_height_ratio"]
    shift = max(0, round(nh/2 - w*(top+style["label_top_ratio"]+style["label_ratio"]*1.2)/2))
    return round(w*style["bar_top_ratio"])+shift, round(w*style["label_top_ratio"])+shift

def glow(path, cw, ch, g, color, strength):
    """红色发光光标的小图：中间一条实心竖线，四周一圈渐淡的光。不依赖 Pillow。"""
    import struct, zlib
    rgb = bytes(int(color[i:i+2], 16) for i in (1, 3, 5))
    width, height, raw = cw+2*g, ch+2*g, bytearray()
    for y in range(height):
        raw.append(0)
        dy = max(g-y, 0, y-(g+ch-1))
        for x in range(width):
            d = math.hypot(max(g-x, 0, x-(g+cw-1)), dy)
            raw += rgb + bytes([round(255*strength*(1.0 if d == 0 else 0.55*math.exp(-(d/(g/2.2))**2)))])
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag+data) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))

# ---- 章节文案确认（2026-11-14 用户定）：导航条上的字先出确认单给用户看，确认后才能 render ----
OUTLINE, CONFIRMED = "章节文案.md", "章节文案确认.json"

def wording(project):
    """观众看得到的字的指纹；只改时间不用重新确认。"""
    rows = [[s["chapter"], s["title"], s.get("question", "")] + ([s["label"]] if "label" in s else []) for s in project["segments"]]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()

def confirmed(project, base):
    path = Path(base) / CONFIRMED
    return path.is_file() and read(path).get("wording") == wording(project)

def outline(project_path):
    """生成《章节文案.md》：按大章节分组列出每个小问题、起止时间和这段第一句字幕。"""
    project_path = Path(project_path).resolve()
    project = read(project_path)
    spec = validate(project, project_path.parent)
    clock = lambda t: f"{int(t)//60:02}:{int(t)%60:02}"
    groups = []
    for row in spec["rows"]:
        if not groups or groups[-1][0]["chapter"] != row["chapter"]:
            groups.append([])
        groups[-1].append(row)
    done = confirmed(project, project_path.parent)
    when = read(project_path.parent / CONFIRMED).get("confirmed_at", "") if done else ""
    if spec["chapters"]:
        cell = lambda text: str(text).replace("|", "｜").replace("\n", " ")
        lines = ["# 章节文案确认单", "",
                 "导航条上只显示进度条下面的短标签。请看两样：每段的短标签用词对不对、分段的位置对不对。",
                 f"短标签字数有上限（段越短能写的越少）。字稍多放不下时，字会自动横向压窄（字高不变，最窄压到 {spec['style']['label_squeeze_min']:.0%}）；压到最窄还放不下要换短词，不会缩小字号或截断。" if spec["style"]["label_squeeze_min"] < 1 else "短标签字数有上限（段越短能写的越少），放不下时要换短词，不会缩小或截断。",
                 "", "状态：" + (f"已确认（{when}）" if done else "待确认。确认之前不会做导航条。"),
                 "", f"进度条分 {len(spec['chapters'])} 段。", "",
                 "| 段 | 进度条下的短标签 | 这段最多几个字 | 起止 | 完整章节名（不显示） | 这一段第一句字幕 |", "|---|---|---|---|---|---|"]
        for k, c in enumerate(spec["chapters"], 1):
            first = c["first_subtitle"]["text"] if c["first_subtitle"] else "（这段没有字幕）"
            note = f"（会压窄到 {c['squeeze']:.0%}）" if c["squeeze"] < 1 else ""      # 按保守估算，真实压窄比例通常更小一点
            lines.append(f"| {k} | {cell(c['label'] or '（空着）')}{note} | {c['max_chars']} | {clock(c['start'])}–{clock(c['end'])} | {cell(c['title'])} | {cell(first)} |")
        target = project_path.parent / OUTLINE
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"outline": str(target), "chapters": len(spec["chapters"]), "segments": len(spec["rows"]), "confirmed": done}
    lines = ["# 章节文案确认单", "",
             "导航条上会出现的字都在这里。请看三样：大章节的标题对不对、每个小问题的说法对不对、分段的位置对不对。",
             "", "状态：" + (f"已确认（{when}）" if done else "待确认。确认之前不会做导航条。"),
             "", f"全片 {len(groups)} 个大章节，{len(spec['rows'])} 个小问题。", ""]
    for number_, rows in enumerate(groups, 1):
        lines += [f"## {number_}. {rows[0]['title']}（{clock(rows[0]['start'])}–{clock(rows[-1]['end'])}，{len(rows)} 个小问题）", "",
                  "| 序号 | 导航条第二行 | 起止 | 这一段第一句字幕 |", "|---|---|---|---|"]
        for k, row in enumerate(rows, 1):
            second = row["question"] or "（不显示第二行）"
            if row["title"] != rows[0]["title"]:
                second += f"（第一行换成：{row['title']}）"
            first = row["first_subtitle"]["text"] if row["first_subtitle"] else "（这段没有字幕）"
            cell = lambda text: str(text).replace("|", "｜").replace("\n", " ")
            lines.append(f"| {k}/{len(rows)} | {cell(second)} | {clock(row['start'])}–{clock(row['end'])} | {cell(first)} |")
        lines.append("")
    target = project_path.parent / OUTLINE
    target.write_text("\n".join(lines), encoding="utf-8")
    return {"outline": str(target), "chapters": len(groups), "segments": len(spec["rows"]), "confirmed": done}

# ---- 确认只认审片台（2026-11-22 261122 反馈单 新1，Q177 第二次复发）----
# AI 把用户一句笼统的“导航文字还有封面你都一起都可以出”当成对 6 段标签的确认跑了 confirm，用户其实没看过，后来改成 4 段、自己重写了字。
# 现在标准项目的片子 confirm 不收对话里的原话，只认审片台（<片子>/02_制作/自动剪辑/审片工作台/）上用户自己点的：
#   ① 导航文字栏某一项的最新一版，用户最后一次操作是点“可以”，且每段标签要么写在那一版卡片的字里、要么就是用户在页面上自己改的字；
#   ② 或用户点了这一栏顶上的“这一栏定了”，且点了之后这一栏没变过（和审片台 board.col_sig 同一个算法：卡片换版、用户改字、标签变了都算变）。
# 两种都还要：用户在页面上改过字的章节（标记.json 的 nav_labels），project.json 里得已经是用户写的字。
BOARD = "审片工作台"
PUBLISH_HINT = ("把每段短标签写进卡片发到审片台导航文字栏：python3 thinkcut_edit.py workbench <片子目录> --publish 导航文字 --kind 改字 "
                "--title \"请确认导航短标签\" --look \"进度条下的短标签：A／B／C／D。用词或分段不对直接改；没问题点可以\"，"
                "等用户在页面上点“可以”或“这一栏定了”（workbench --list 能看到），再 confirm。对话里的话（哪怕说“都可以出”）不算确认")

def _h(x):
    """和审片台 board._h 同一个算法（指纹前 10 位）。"""
    return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]

def board_dir(project_path):
    """找这条片子的审片台数据：从 project.json 所在目录往上找 审片工作台/（标准位置是 自动剪辑/导航/project.json → 自动剪辑/审片工作台/）。"""
    for d in list(Path(project_path).resolve().parents)[:4]:
        if (d / BOARD).is_dir():
            return d / BOARD
        if d.name in ("自动剪辑", "02_制作"):
            break
    return None

def thinkcut_project(project_path):
    """是不是标准片子（路径里有 02_制作 或 自动剪辑）：是的话没有审片台也不能按老办法确认。"""
    return any(p.name in ("02_制作", "自动剪辑") for p in Path(project_path).resolve().parents)

def page_chapters(project):
    """审片台导航文字栏上显示的章节（和 workbench.chapters 同一个分法：相邻同 chapter 合成一段，字取第一个板块的 label，没有就 title）。"""
    out = []
    for s in project.get("segments") or []:
        if out and out[-1]["id"] == s.get("chapter"):
            continue
        out.append({"id": s.get("chapter"), "label": s.get("label") or s.get("title") or ""})
    return out

def board_evidence(project, board):
    """按审片台的记录判断导航文字是否算用户确认过。返回 (依据 或 None, 不算的原因列表)。只读。"""
    b = read(board / "审片台.json") if (board / "审片台.json").is_file() else {}
    m = read(board / "标记.json") if (board / "标记.json").is_file() else {}
    nav_labels = m.get("nav_labels") or {}
    chs = page_chapters(project)
    why = []
    for n, c in enumerate(chs, 1):
        mine = nav_labels.get(c["id"])
        if mine is not None and mine != c["label"]:
            why.append(f"用户在审片台把第 {n} 段改成了「{mine}」，project.json 里还是「{c['label']}」：先照用户的字改 project.json、重出 outline")
    if why:
        return None, why
    items = [x for x in b.get("items") or [] if x.get("column") == "导航文字" and not x.get("撤回")]
    # ② 这一栏定了
    rec = (b.get("settled") or {}).get("导航文字")
    if rec:
        sig = []
        for it in items:
            u = next((e for e in reversed(it.get("events") or []) if e.get("type") == "user"), {})
            sig.append([it["id"], it["versions"][-1]["v"], u.get("action"), u.get("pick"), u.get("text")])
        sig.append([[e["id"], e.get("new")] for e in b.get("edits") or [] if e.get("column") == "导航文字"])
        sig.append([[c["id"], nav_labels.get(c["id"], c["label"])] for c in chs])
        if rec.get("sig") == _h(sig):
            return f"审片台：用户点了导航文字栏“这一栏定了”（{rec.get('at', '')}）", []
        why.append(f"用户 {rec.get('at', '')} 点过“这一栏定了”，但之后这一栏变了（卡片换了新版、改了字或标签和当时页面上的不一样），要用户重新看、重新点")
    # ① 最新一版点了“可以”
    for it in items:
        last = it["versions"][-1]
        user = [e for e in it.get("events") or [] if e.get("type") == "user"]
        pubs = [e for e in it.get("events") or [] if e.get("type") == "publish"]
        u = user[-1] if user else None
        if not u or u.get("action") != "可以" or u.get("v") != last["v"] or (pubs and pubs[-1].get("at", "") > u.get("at", "")):
            why.append(f"{it['id']}「{last.get('title', '')}」第 {last['v']} 版用户还没点“可以”" + (f"（最后是{u.get('action')}：{u.get('text', '')}）" if u and u.get("action") != "可以" else ""))
            continue
        text = " ".join(str(last.get(k) or "") for k in ("title", "look", "note"))
        missing = [c["label"] for c in chs if c["label"] and nav_labels.get(c["id"]) != c["label"] and c["label"] not in text]
        if missing:
            why.append(f"用户对 {it['id']} 第 {last['v']} 版点了可以，但那一版卡片里没有这些标签：{'／'.join(missing)}（用户没看过这些字）：把现在的标签写进卡片重新发一版")
            continue
        return f"审片台：用户对导航文字栏 {it['id']} 第 {last['v']} 版点了可以（{u.get('at', '')}）", []
    if not items and not rec:
        why.append("审片台导航文字栏还没有发过要用户确认的卡片，用户也没点过“这一栏定了”")
    return None, why

def confirm(project_path):
    """用户确认过之后才运行；改了任何一个字都要重新确认。标准项目的片子只认审片台上用户点的（新1），不收对话里的原话。"""
    from datetime import datetime
    project_path = Path(project_path).resolve()
    project = read(project_path)
    validate(project, project_path.parent)
    board = board_dir(project_path)
    if board:
        basis, why = board_evidence(project, board)
        if not basis:
            raise ValueError("导航文字还不算用户确认过（只认审片台上用户点的，2026-11-22 261122 反馈单 新1）：" + "；".join(why) + "。" + PUBLISH_HINT)
    elif thinkcut_project(project_path):
        raise ValueError(f"这是标准项目的片子，但没找到审片台数据（{BOARD}/）：先起审片台（python3 thinkcut_edit.py workbench <片子目录>），" + PUBLISH_HINT)
    else:
        basis = "不是标准片子（没有审片台）：按老办法，用户看过《章节文案.md》说了可以"
    record = {"wording": wording(project), "confirmed_at": datetime.now().isoformat(timespec="seconds"), "segments": len(project["segments"]),
              "basis": basis, "labels": [c["label"] for c in page_chapters(project)]}
    (project_path.parent / CONFIRMED).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**record, **outline(project_path)}

def pngs(spec, out):
    rows = []
    for row in spec["rows"]:
        slug = re.sub(r"[^\w\u3400-\u9fff-]+", "_", row["title"])[:36]
        row["image"] = f"{row['index']:02}_{slug}.png"
        rows.append({**row, "png": str(out / row["image"])})
    if spec["chapters"]:
        s, u = spec["style"], spec["unit"]
        (ty, label_top), th = rise(s, u, spec["height"]), max(2, round(u*s["track_height_ratio"]))
        for row in rows:
            row["bar"] = {"y": ty, "h": th, "label_top": label_top, "font": round(u*s["label_ratio"]), "line": math.ceil(round(u*s["label_ratio"])*LABEL_LINE), "segments": [{"x": c["x"], "w": c["w"], "current": c["chapter"] == row["chapter"]} for c in spec["chapters"]],
                          "pad": squeeze_pad(u), "squeeze_min": s["label_squeeze_min"],
                          "shadow": {"alpha": s["shadow_alpha"], "blur": max(1, round(u*s["shadow_blur_ratio"])), "dy": max(1, round(u*s["shadow_offset_ratio"]))}}
            row["labels"] = [{"text": c["label"], "cx": c["x"]+c["w"]//2, "w": c["w"], "current": c["chapter"] == row["chapter"]} for c in spec["chapters"]]
    render = {"width": spec["width"], "height": spec["height"], "style": spec["style"], "rows": rows}
    write(out / "render-spec.json", render)
    if sys.platform == "darwin" and shutil.which("swift") and not spec["style"].get("font_title_path"):
        subprocess.run([executable("swift"), str(ROOT / "scripts/text.swift"), str(out / "render-spec.json")], check=True)
    else:
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont
        except ImportError as exc:
            raise ValueError("文字渲染需要 macOS Swift/AppKit，或 Pillow 和两种字重的字体路径；先运行 doctor") from exc
        if not all(spec["style"].get(f"font_{field}_path") for field in ("title", "question")):
            raise ValueError("请在 style 指定 font_title_path 和 font_question_path，或使用 macOS Swift/AppKit")
        s, w, h = spec["style"], spec["width"], spec["height"]
        translucent = s["background_alpha"] < 1
        for row in rows:
            img = Image.new("RGBA", (w, h), tuple(int(s["background"][i:i+2], 16) for i in (1, 3, 5)) + (round(255*s["background_alpha"]),)) if translucent else Image.new("RGB", (w, h), s["background"])
            draw = ImageDraw.Draw(img)
            bottom = 0
            if spec["chapters"]:
                for seg in row["bar"]["segments"]:
                    if seg["w"] <= 0:
                        continue
                    draw.rectangle([seg["x"], row["bar"]["y"], seg["x"]+seg["w"]-1, row["bar"]["y"]+row["bar"]["h"]-1], fill=s["track_current" if seg["current"] else "track"])
                for item in row["labels"]:
                    field = "title" if item["current"] else "question"
                    font = ImageFont.truetype(s[f"font_{field}_path"], row["bar"]["font"], index=s.get(f"font_{field}_index", 0))
                    box = draw.textbbox((0, 0), item["text"], font=font, anchor="lt")
                    bar = row["bar"]
                    k = 1.0 if box[2] <= item["w"] else (item["w"]-bar["pad"]) / box[2]      # 放不下就横向压窄（字高不变），压到下限还放不下才报错
                    if k < bar["squeeze_min"] or box[3] > bar["line"] or bar["label_top"]+bar["line"] > h:
                        raise ValueError("短标签超宽（压窄到下限也放不下）或导航高度不足：" + item["text"])
                    color = s["foreground" if item["current"] else "label_dim"]
                    if k == 1.0 and not bar["shadow"]["alpha"]:
                        draw.text((item["cx"]-box[2]//2, bar["label_top"]), item["text"], font=font, fill=color, anchor="lt")
                        continue
                    layer = Image.new("RGBA", (box[2]+8, bar["line"]+bar["shadow"]["blur"]*4), (0, 0, 0, 0))      # 单独画一层：先暗影后字，再压窄、叠上去
                    if bar["shadow"]["alpha"]:
                        shade = Image.new("RGBA", layer.size, (0, 0, 0, 0))
                        ImageDraw.Draw(shade).text((4, bar["shadow"]["blur"]*2+bar["shadow"]["dy"]), item["text"], font=font, fill=(0, 0, 0, round(255*bar["shadow"]["alpha"])), anchor="lt")
                        layer = shade.filter(ImageFilter.GaussianBlur(bar["shadow"]["blur"]))
                    ImageDraw.Draw(layer).text((4, bar["shadow"]["blur"]*2), item["text"], font=font, fill=color, anchor="lt")
                    if k < 1.0:
                        layer = layer.resize((max(1, round(layer.width*k)), layer.height), Image.LANCZOS)
                    if translucent:
                        img.alpha_composite(layer, (item["cx"]-layer.width//2, bar["label_top"]-bar["shadow"]["blur"]*2))
                    else:
                        img.paste(layer, (item["cx"]-layer.width//2, bar["label_top"]-bar["shadow"]["blur"]*2), layer)
            for field in () if spec["chapters"] else ("title", "question"):
                if not row[field]:
                    continue
                font = ImageFont.truetype(s[f"font_{field}_path"], round(w*s[f"{field}_ratio"]), index=s.get(f"font_{field}_index", 0))
                x, y = round(w*s["margin_ratio"]), round(w*s[f"{field}_top_ratio"])
                box = draw.textbbox((0, 0), row[field], font=font, anchor="lt")
                if box[2] > w-2*x or y < bottom or y+box[3] > h-w*(s["track_bottom_ratio"]+s["track_height_ratio"])-8:
                    raise ValueError("文字超宽或高度不足，请调整文案或布局")
                draw.text((x, y), row[field], font=font, fill=s["foreground"], anchor="lt")
                bottom = y+box[3]
            img.save(row["png"])

def draft_note(project, directory):
    """章节文案用户还没确认（或确认后又改了字）时给一句提醒；确认过返回 None。
    2026-11-22 用户定“先做出来再改”（原话：“按照AI的想法直接做就行了，不用来问我，等做完了我边看边改”）：以前（2026-11-14 起）没确认 render 直接拒绝，
    现在照出、标成初稿——用户在审片台审核线的章节标题上直接改字，AI 照改重出；整片导出前用户点审核线“这一栏定了”（连带导航文字）才算定。"""
    if confirmed(project, directory): return None
    return ("【初稿·未确认】章节文案用户还没确认（或确认后又改了字）：按现在的分章先出导航条。"
            "用户在审片台审核线的章节标题上直接改字；改了就照着改 project.json 重出。confirm 照旧可以用来记用户的确认（只认审片台上点的）。")


def render(project_path, output, ffmpeg=None):
    project_path, out = Path(project_path).resolve(), Path(output).resolve()
    project = read(project_path)
    spec = validate(project, project_path.parent)
    note = draft_note(project, project_path.parent)
    if note: print(note, file=sys.stderr, flush=True)      # 2026-11-22 用户定“先做出来再改”：没确认不再拦出条，按 AI 分的章先做，用户在审片台事后改
    ff = executable("ffmpeg", ffmpeg)
    final = None
    if out.suffix.lower() in (".mov", ".mp4"):      # 2026-11-21 新9：第二个参数直接给成片文件名时出这个文件，过程文件放旁边的“<文件名>_导航文件”
        if out.exists():
            raise ValueError(f"{out} 已经有了，换个名字或先挪走")
        if spec["style"]["background_alpha"] < 1 and out.suffix.lower() != ".mov":
            raise ValueError("半透明／透明底出的是带透明通道的 .mov，文件名请改成 .mov")
        final, out = out, out.parent / f"{out.stem}_导航文件"
    out.mkdir(parents=True, exist_ok=False)
    snapshot = {**project, "source": {**project["source"], "subtitles": spec["source_path"]}, "style": spec["style"]}
    write(out / "project.json", snapshot)
    pngs(spec, out)
    fps = float(Fraction(spec["fps"]))
    lines = ["ffconcat version 1.0"]
    # Generated ASCII aliases avoid concat quoting issues with user text and paths.
    for row in spec["rows"]:
        alias = f"frame-{row['index']:03}.png"
        shutil.copyfile(out / row["image"], out / alias)
        lines.extend([f"file '{alias}'", f"option framerate {spec['fps']}", f"duration {(row['frame_end']-row['frame_start'])/fps:.12f}"])
    lines.append(f"file 'frame-{spec['rows'][-1]['index']:03}.png'")
    lines.append(f"option framerate {spec['fps']}")
    (out / "frames.txt").write_text("\n".join(lines), encoding="utf-8")
    s, w, h, count = spec["style"], spec["width"], spec["height"], spec["frames"]
    margin = round(w*s["margin_ratio"])
    tw = w-2*margin
    th = max(2, round(w*s["track_height_ratio"]))
    ty = h-round(w*s["track_bottom_ratio"])-th
    gap = max(2, round(w*s["gap_ratio"]))
    # At most one command per pixel; long videos stay inexpensive to encode.
    commands = {}
    for fill in range(1, tw+1):
        f = math.ceil(fill/tw*(count-1))
        commands[f] = fill
    (out / "progress.txt").write_text("\n".join(f"{f/fps:.12f} drawbox@progress w {fill};" for f, fill in sorted(commands.items())), encoding="utf-8")
    alpha = s["background_alpha"] < 1      # 半透明／透明底：全程带透明通道，出 ProRes 4444 的 .mov；不透明还是老样子的 mp4
    hole = f"{s['background'].replace('#', '0x')}@{s['background_alpha']}:replace=1" if alpha else s["background"]      # 段间小缝填回底的颜色（透明底要连透明度一起填回，不能被盖成实色）
    filters = [f"fps={spec['fps']}:start_time=0:round=near", "format=rgba" if alpha else "format=rgb24"]
    extra = []
    if spec["chapters"]:
        # 短标签样式：底线和标签已画在图里；这里只加蓝色进度、段间小缝和红色发光光标
        u = spec["unit"]
        ty, th = rise(s, u, h)[0], max(2, round(u*s["track_height_ratio"]))
        margin, tw, gap, edges = bar(s, w, u, count, spec["chapters"])
        filters += ["sendcmd=f=progress.txt", f"drawbox@progress=x={margin}:y={ty}:w=1:h={th}:color={s['progress']}:t=fill"]
        filters += [f"drawbox=x={x-gap//2}:y={ty}:w={gap}:h={th}:color={hole}:t=fill" for x in edges[1:-1]]
        cw, ch = max(2, round(u*s["caret_width_ratio"])), round(u*s["caret_height_ratio"])
        g = min(round(cw*2.7), ty+th-ch)
        if g < 1:
            raise ValueError("光标放不下：bar_top_ratio 太小或 caret_height_ratio 太大")
        # 帧号用 round(t×帧率) 算，不用 overlay 的 n：ffmpeg 9 里 n 从 1 数起，光标会领先一帧；+0.5 让 overlay 取整变成四舍五入
        place = f"overlay=format=rgb:x='{margin-cw//2-g}+{tw}*round(t*{spec['fps']})/{count-1}+0.5':y={ty+th-ch-g}"
        blink = bool(s.get("caret_blink", False))      # 2026-11-14 用户定：默认常亮不闪
        glow(out / "caret.png", cw, ch, g, s["caret"], 1.0)
        extra = ["-loop", "1", "-framerate", spec["fps"], "-i", "caret.png"]
        graph = "[0:v]" + ",".join(filters) + "[base];"
        if blink:      # 闪烁＝亮暗交替：暗的一直在，亮的每 1.1 秒灭 0.3 秒
            glow(out / "caret-dim.png", cw, ch, g, s["caret"], 0.45)
            extra += ["-loop", "1", "-framerate", spec["fps"], "-i", "caret-dim.png"]
            graph += f"[base][2:v]{place}[dim];[dim][1:v]{place}:enable='lt(mod(t,1.1),0.8)',format={'yuva444p10le' if alpha else 'yuv420p'}[v]"
        else:
            graph += f"[base][1:v]{place},format={'yuva444p10le' if alpha else 'yuv420p'}[v]"
        (out / "filters.txt").write_text(graph, encoding="utf-8")
        graph_args = ["-filter_complex", graph, "-map", "[v]"]
    else:
        filters += [f"drawbox=x={margin}:y={ty}:w={tw}:h={th}:color={s['track']}:t=fill",
                    "sendcmd=f=progress.txt",
                    f"drawbox@progress=x={margin}:y={ty}:w=1:h={th}:color={s['progress']}:t=fill"]
        chapter = spec["rows"][0]["chapter"]
        for row in spec["rows"][1:]:
            if row["chapter"] != chapter:
                x = margin+round(tw*row["frame_start"]/count)-gap//2
                filters.append(f"drawbox=x={x}:y={ty}:w={gap}:h={th}:color={s['background']}:t=fill")
                chapter = row["chapter"]
        filters.append("format=yuv420p")
        (out / "filters.txt").write_text(",".join(filters), encoding="utf-8")
        graph_args = ["-vf", (out / "filters.txt").read_text(encoding="utf-8")]
    filename = final.name if final else ("navigation-full.mov" if alpha else "navigation-full.mp4")
    codec = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", "-vendor", "apl0"] if alpha else \
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    cmd = [ff, "-hide_banner", "-nostdin", "-n", "-f", "concat", "-safe", "0", "-i", "frames.txt", *extra,
           *graph_args, "-frames:v", str(count), "-fps_mode", "passthrough", "-an", *codec, filename]
    with (out / "encode.log").open("w") as log:
        subprocess.run(cmd, cwd=out, stdout=log, stderr=log, check=True)
    # Full decode, independent of encode success; count frames and inspect stream metadata.
    checked = subprocess.run([ff, "-hide_banner", "-v", "info", "-xerror", "-i", str(out / filename), "-map", "0:v:0", "-an", "-progress", "pipe:1", "-f", "null", "-"], capture_output=True, text=True, check=True)
    counts = re.findall(r"^frame=(\d+)", checked.stdout, re.M)
    if not counts or int(counts[-1]) != count:
        raise ValueError("完整解码帧数与计划不一致")
    write(out / "validation.json", {"decoded_frames": int(counts[-1]), "expected_frames": count,
          "fps": spec["fps"], "duration": count/fps, "dimensions": [w, h], "placement": spec["placement"],
          "end": spec["end"], "source_sha256": spec["source_sha256"], "warnings": spec["warnings"],
          "visual_review_required": True, "wording_confirmed": not note, "file": filename, "background_mode": s["background_mode"], "alpha": alpha})
    write(out / "timeline.json", spec["rows"])
    table = ["# 时间轴与字幕定位", "", "时间为原片绝对时间；结束时间不包含该帧。字幕按起始时间归属，保留原文。", "", "| 编号 | 标题／问题 | 开始 | 结束 | 第一条字幕 | 最后一条字幕 |", "|---|---|---|---|---|---|"]
    def cell(v):
        return str(v).replace("|", "\\|").replace("\n", "<br>")
    for r in spec["rows"]:
        table.append("| " + " | ".join(map(cell, [r["index"], r["title"]+"／"+r["question"], stamp(spec["placement"]+r["frame_start"]/fps), stamp(spec["placement"]+r["frame_end"]/fps), r["first_subtitle"]["text"] if r["first_subtitle"] else "无对白；见画面依据", r["last_subtitle"]["text"] if r["last_subtitle"] else "无对白；见画面依据"])) + " |")
    (out / "时间轴.md").write_text("\n".join(table), encoding="utf-8")
    (out / "使用说明.md").write_text(f"# 导航视频使用说明\n\n导入 {filename}{'（带透明通道，底是半透明／透明的）' if alpha else ''}，从原视频 {stamp(spec['placement'])} 放置，到 {stamp(spec['end'])} 结束。\n\n正常速度，无音轨，尺寸 {w} × {h}，帧率 {spec['fps']}，时长 {count/fps:.6f} 秒。\n\n目标画布 {project['target']['width']} × {project['target']['height']}；保持原尺寸，顶部水平居中，不要拉伸成全画幅。前后未覆盖区间无导航。\n\nproject.json 保存此次参数快照。修改原片剪辑或字幕后必须重新核对时间轴。图片是文字层，动态进度条位于导航视频中。\n", encoding="utf-8")
    if final:
        shutil.move(str(out / filename), str(final))
    print(json.dumps({"output": str(final or out), "frames": count, "duration": count/fps}, ensure_ascii=False))

def doctor():
    """Report capabilities without installing packages or reading credentials."""
    import importlib.util
    report = {"python": sys.version.split()[0], "platform": sys.platform,
              "swift": shutil.which("swift"), "pillow": importlib.util.find_spec("PIL") is not None}
    for name in ("ffmpeg", "ffprobe"):
        try:
            path = executable(name)
            result = subprocess.run([path, "-version"], capture_output=True, text=True, check=True)
            report[name] = {"path": path, "version": result.stdout.splitlines()[0]}
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            report[name] = {"error": str(exc)}
    if "error" not in report["ffmpeg"]:
        path = report["ffmpeg"]["path"]
        enc = subprocess.check_output([path, "-hide_banner", "-encoders"], text=True, stderr=subprocess.DEVNULL)
        filters = subprocess.check_output([path, "-hide_banner", "-filters"], text=True, stderr=subprocess.DEVNULL)
        report["libx264"] = "libx264" in enc
        report["required_filters"] = {name: name in filters for name in ("sendcmd", "drawbox", "aresample")}
    return report

def extract_audio(video, output, segment_seconds=900):
    """Produce PCM segments with sample-exact offsets on the source playback timeline."""
    if segment_seconds <= 0 or not math.isfinite(segment_seconds):
        raise ValueError("分段时长必须为正有限秒数")
    video, out = Path(video).resolve(), Path(output).resolve()
    metadata = probe(video)
    if not any(s.get("codec_type") == "audio" for s in metadata["streams"]):
        raise ValueError("视频没有音轨：需要画面分析或用户提供定位，不能虚构转写")
    out.mkdir(parents=True, exist_ok=False)
    wav = out / "timeline.wav"
    cmd = [executable("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-n",
           "-copyts", "-start_at_zero", "-i", str(video), "-map", "0:a:0",
           "-af", "aresample=16000:async=1:first_pts=0", "-ac", "1", "-c:a", "pcm_s16le", str(wav)]
    subprocess.run(cmd, check=True)
    segments = []
    with wave.open(str(wav), "rb") as src:
        rate = src.getframerate()
        chunk = max(1, round(segment_seconds * rate))
        offset = 0
        while offset < src.getnframes():
            count = min(chunk, src.getnframes() - offset)
            filename = f"segment-{len(segments)+1:03}.wav"
            with wave.open(str(out / filename), "wb") as target:
                target.setparams(src.getparams())
                target.writeframes(src.readframes(count))
            segments.append({"audio": filename, "subtitles": filename.replace(".wav", ".srt"),
                             "offset_samples": offset, "samples": count, "offset_seconds": offset / rate,
                             "duration_seconds": count / rate, "sha256": digest(out / filename)})
            offset += count
    manifest = {"source_video": str(video), "source_size": video.stat().st_size,
                "source_mtime_ns": video.stat().st_mtime_ns, "probe": metadata,
                "sample_rate": rate, "decoded_duration": offset / rate, "segments": segments,
                "timeline_basis": "source playback start=0; original timestamps, gaps and audio delay preserved as PCM"}
    write(out / "audio-manifest.json", manifest)
    return manifest

def merge_subtitles(manifest_path, output):
    manifest_path, output = Path(manifest_path).resolve(), Path(output)
    manifest = read(manifest_path)
    entries = []
    cursor = 0
    rate = manifest["sample_rate"]
    if type(rate) is not int or rate <= 0:
        raise ValueError("非法 sample_rate")
    for segment in manifest["segments"]:
        if segment["offset_samples"] != cursor or type(segment["samples"]) is not int or segment["samples"] <= 0:
            raise ValueError("音频分段必须连续且样本数为正整数")
        audio = manifest_path.parent / segment["audio"]
        if digest(audio) != segment["sha256"]:
            raise ValueError("音频分段已变化，需重新转写")
        srt = manifest_path.parent / segment["subtitles"]
        # An empty file explicitly records a segment with no recognized speech.
        rows, warnings = subtitles(srt) if srt.read_text(encoding="utf-8-sig").strip() else ([], [])
        if warnings:
            raise ValueError("分段字幕有顺序或重叠异常，请先核对：" + "; ".join(warnings))
        if rows and max(r["end"] for r in rows) > segment["samples"] / rate + 0.05:
            raise ValueError("分段字幕超出音频时长；核对转写引擎时间基准")
        for row in rows:
            entries.append({**row, "start": row["start"] + cursor / rate,
                            "end": row["end"] + cursor / rate})
        cursor += segment["samples"]
    if not entries:
        raise ValueError("没有识别到对白，使用实际画面证据分析，不生成虚假字幕")
    text = "\n\n".join(f"{i}\n{stamp(r['start']).replace('.', ',')} --> {stamp(r['end']).replace('.', ',')}\n{r['text']}" for i, r in enumerate(entries, 1)) + "\n"
    with output.open("x", encoding="utf-8") as f:
        f.write(text)
    return {"output": str(output), "entries": len(entries), "duration": cursor / rate}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    p = sub.add_parser("extract-audio"); p.add_argument("video"); p.add_argument("output"); p.add_argument("--segment-seconds", type=float, default=900)
    p = sub.add_parser("merge-subtitles"); p.add_argument("manifest"); p.add_argument("output")
    p = sub.add_parser("prepare"); p.add_argument("srt"); p.add_argument("output")
    p = sub.add_parser("probe"); p.add_argument("video"); p.add_argument("--ffprobe")
    p = sub.add_parser("validate"); p.add_argument("project")
    p = sub.add_parser("outline"); p.add_argument("project")
    p = sub.add_parser("confirm"); p.add_argument("project")
    p = sub.add_parser("render"); p.add_argument("project"); p.add_argument("output"); p.add_argument("--ffmpeg")
    p = sub.add_parser("preset-show"); p.add_argument("--name")
    p = sub.add_parser("preset-save"); p.add_argument("name"); p.add_argument("style"); p.add_argument("--default", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        print(json.dumps(doctor(), ensure_ascii=False, indent=2))
    elif args.command == "extract-audio":
        print(json.dumps(extract_audio(args.video, args.output, args.segment_seconds), ensure_ascii=False, indent=2))
    elif args.command == "merge-subtitles":
        print(json.dumps(merge_subtitles(args.manifest, args.output), ensure_ascii=False, indent=2))
    elif args.command == "prepare":
        rows, warnings = subtitles(args.srt)
        write(args.output, {"source": {"subtitles": str(Path(args.srt).resolve()), "sha256": digest(args.srt)}, "entries": rows, "warnings": warnings})
    elif args.command == "probe":
        print(json.dumps(probe(args.video, args.ffprobe), ensure_ascii=False, indent=2))
    elif args.command == "validate":
        print(json.dumps(validate(read(args.project), Path(args.project).resolve().parent), ensure_ascii=False, indent=2))
    elif args.command == "outline":
        print(json.dumps(outline(args.project), ensure_ascii=False, indent=2))
    elif args.command == "confirm":
        print(json.dumps(confirm(args.project), ensure_ascii=False, indent=2))
    elif args.command == "render":
        render(args.project, args.output, args.ffmpeg)
    elif args.command == "preset-show":
        print(json.dumps(preset(args.name), ensure_ascii=False, indent=2))
    else:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.name):
            raise ValueError("预设名无效")
        style = read(args.style)
        allowed = set(read(ROOT / "assets/blue.json")) | {"font_title_path", "font_question_path", "font_title_index", "font_question_index"}
        if not set(style) <= allowed:
            raise ValueError("样式预设不能包含画布、帧率、时间轴或其他项目字段")
        if args.default and (CONFIG / "config.json").exists():
            raise ValueError("默认配置已存在；请保留旧版本后显式更新，不覆盖")
        target = CONFIG / "presets"
        target.mkdir(parents=True, exist_ok=True)
        write(target / (args.name + ".json"), style)
        if args.default:
            config = CONFIG / "config.json"
            # Keep existing defaults; explicit new names never silently replace them.
            write(config, {"default_preset": args.name})

if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
