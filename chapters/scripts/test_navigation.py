#!/usr/bin/env python3
"""Run deterministic regression checks; optional real render via --render DIR."""
import copy
import math
import os
import shutil
import wave
import subprocess
import json
from pathlib import Path
import sys
import tempfile
import unittest
import navigation as nav

SRT = """1
00:00:00,000 --> 00:00:00,900
这是开场。

2
00:00:01,000 --> 00:00:02,000
先明确你希望服务谁。

3
00:00:02,001 --> 00:00:03,500
再看看这些人在哪里。

4
00:00:03,501 --> 00:00:04,500
接下来确定内容方向。

5
00:00:04,501 --> 00:00:06,000
选择你能够持续回答的问题。
"""

def fixture(root):
    srt = root / "input.srt"
    srt.write_text(SRT, encoding="utf-8")
    return {"schema_version": 1, "source": {"subtitles": "input.srt", "sha256": nav.digest(srt)},
            "target": {"width": 1920, "height": 2560, "nav_height": 280, "fps": "30000/1001"},
            "duration": 6, "coverage": [1, 6], "segments": [
                {"chapter": "platform", "title": "第一部分：选择平台", "question": "如何找到你的目标观众？", "start": 1, "end": 3.501},
                {"chapter": "direction", "title": "第二部分：确定方向", "question": "哪些问题值得持续回答？", "start": 3.501, "end": 6}]}

LABEL_SAMPLES = ["通风排味", "选平台", "地漏", "Wi-Fi 路由", "Low-E 玻璃", "HDMI 接口", "WWW", "MWMW", "WOMBAT", "QUICK FOX",
                 "2026", "4K 120Hz", "PM2.5", "3.14%", "A B C", "mmmm", "防水 10mm", "@&%#", "日本語", "café", "地漏👍"]

def label_floor(unit):
    """短标签样式在这个短边下的最小导航高度（偶数）。"""
    s = nav.read(nav.ROOT / "assets/blue.json")
    need = round(unit*s["label_top_ratio"]) + math.ceil(round(unit*s["label_ratio"])*nav.LABEL_LINE)
    return need + need % 2

def swift_widths(texts, size):
    """用和 text.swift 一样的 AppKit 字体量真实宽度，返回 [(粗体宽, 中等宽)]。"""
    style = nav.read(nav.ROOT / "assets/blue.json")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "measure.swift"
        script.write_text("import AppKit\nlet a = CommandLine.arguments\nfor t in a[3...] { print([a[1], a[2]].map { NSAttributedString(string: t, attributes: [.font: NSFont(name: $0, size: %d)!]).size().width }.map { String(Double($0)) }.joined(separator: \" \")) }\n" % size, encoding="utf-8")
        out = subprocess.run(["swift", str(script), style["font_title"], style["font_question"], *texts], capture_output=True, text=True, check=True).stdout
    return [tuple(map(float, line.split())) for line in out.splitlines()]

class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.p = fixture(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_to_file_path(self):
        # 2026-11-21 新9：第二个参数写成 .mov/.mp4 时出的就是这个文件，不建同名文件夹
        path = self.root / "project.json"
        segs = [{**x, "label": f"标签{i}"} for i, x in enumerate(self.p["segments"])]
        nav.write(path, {**self.p, "segments": segs, "style": {"background_mode": "dim"}})
        nav.confirm(path)
        with self.assertRaisesRegex(ValueError, "文件名请改成 .mov"):
            nav.render(path, self.root / "nav.mp4")
        self.assertFalse((self.root / "nav.mp4").exists())
        try:
            nav.executable("ffmpeg")
        except Exception:
            self.skipTest("没有 ffmpeg")
        nav.render(path, self.root / "nav.mov")
        self.assertTrue((self.root / "nav.mov").is_file())
        self.assertTrue((self.root / "nav_导航文件" / "validation.json").is_file())

    def test_absolute_frames_and_subtitle_anchors(self):
        s = nav.validate(self.p, self.root)
        self.assertEqual(s["frames"], 150)
        self.assertEqual(s["rows"][1]["frame_start"], 75)
        self.assertEqual(s["rows"][0]["last_subtitle"]["text"], "再看看这些人在哪里。")
        self.assertAlmostEqual(s["placement"], 1.001)

    def test_wording_must_be_confirmed_before_render(self):
        path = self.root / "project.json"
        nav.write(path, self.p)
        # 2026-11-22 规矩变了（用户定“先做出来再改”）：以前这里是 render 抛“还没让用户确认”、什么都不出；
        # 现在没确认只提醒、照出（标成初稿），确认记录的判断（confirmed）本身没变。下面用“走到了找 ffmpeg 那一步”证明没被拦。
        self.assertIn("初稿·未确认", nav.draft_note(self.p, self.root))
        class Past(Exception): pass
        def past(*a, **k): raise Past()
        old = nav.executable; nav.executable = past; self.addCleanup(setattr, nav, "executable", old)
        with self.assertRaises(Past):
            nav.render(path, self.root / "out")
        nav.executable = old
        info = nav.outline(path)
        text = (self.root / nav.OUTLINE).read_text(encoding="utf-8")
        self.assertEqual((info["chapters"], info["segments"], info["confirmed"]), (2, 2, False))
        self.assertIn("## 1. 第一部分：选择平台（00:01–00:03，1 个小问题）", text)
        self.assertIn("| 1/1 | 如何找到你的目标观众？ | 00:01–00:03 | 先明确你希望服务谁。 |", text)
        self.assertIn("待确认", text)
        nav.confirm(path)
        self.assertTrue(nav.confirmed(self.p, self.root))
        self.assertIn("已确认", (self.root / nav.OUTLINE).read_text(encoding="utf-8"))
        self.assertIsNone(nav.draft_note(self.p, self.root))                       # 确认过：不再提醒
        moved = copy.deepcopy(self.p); moved["segments"][0]["end"] = moved["segments"][1]["start"] = 3.6
        self.assertTrue(nav.confirmed(moved, self.root))
        reworded = copy.deepcopy(self.p); reworded["segments"][1]["question"] = "换了一个说法"
        self.assertFalse(nav.confirmed(reworded, self.root))

    def _board_case(self, labels=("选平台", "定方向")):
        """标准位置：<片子>/02_制作/自动剪辑/导航/project.json，审片台在 自动剪辑/审片工作台/。"""
        nav_dir = self.root / "片子" / "02_制作" / "自动剪辑" / "导航"
        nav_dir.mkdir(parents=True)
        p = fixture(nav_dir)
        p["segments"][0]["label"], p["segments"][1]["label"] = labels
        nav.write(nav_dir / "project.json", p)
        return nav_dir / "project.json", nav_dir.parent / nav.BOARD

    @staticmethod
    def _card(look, v=1, click=None, at="2026-11-22 19:00:00"):
        ev = [{"type": "publish", "v": n, "at": "2026-11-22 13:0%d:00" % n, "note": ""} for n in range(1, v + 1)]
        if click:
            ev.append({"type": "user", "v": click[1], "action": click[0], "at": at, "who": "用户（审片台页面）", "text": click[2] if len(click) > 2 else ""})
        return {"id": "导1", "column": "导航文字", "versions": [{"v": n, "title": "请确认导航短标签", "look": look if n == v else "旧的", "kind": "改字", "note": "", "files": []} for n in range(1, v + 1)], "events": ev}

    def test_confirm_only_from_board(self):
        # 2026-11-22 261122 反馈单 新1（Q177 第二次复发）：标准项目的片子 confirm 只认审片台上用户点的，不收对话里的原话
        path, board = self._board_case()
        with self.assertRaisesRegex(ValueError, "没找到审片台数据.*--publish 导航文字"):
            nav.confirm(path)                                                           # 标准项目的片子没起审片台：不能按老办法确认
        board.mkdir()
        with self.assertRaisesRegex(ValueError, "还没有发过要用户确认的卡片"):
            nav.confirm(path)
        self.assertFalse((path.parent / nav.CONFIRMED).exists())
        def put(items, labels=None):
            (board / "审片台.json").write_text(json.dumps({"items": items, "edits": []}, ensure_ascii=False), encoding="utf-8")
            (board / "标记.json").write_text(json.dumps({"nav_labels": labels or {}}, ensure_ascii=False), encoding="utf-8")
        put([self._card("短标签：选平台／定方向")])                                        # 发了卡片、用户没点
        with self.assertRaisesRegex(ValueError, "第 1 版用户还没点“可以”"):
            nav.confirm(path)
        put([self._card("短标签：选平台／定方向", click=("写要求", 1, "分太多了，4 段就行"))])  # 用户写了要求（261122 就是这样）
        with self.assertRaisesRegex(ValueError, "最后是写要求：分太多了"):
            nav.confirm(path)
        put([self._card("短标签：选平台／定方向", v=2, click=("可以", 1))])                # 点的是旧版：新版没看过
        with self.assertRaisesRegex(ValueError, "第 2 版用户还没点"):
            nav.confirm(path)
        put([self._card("短标签：平台／方向", click=("可以", 1))])                         # 点了可以，但卡片上不是现在这些字
        with self.assertRaisesRegex(ValueError, "没有这些标签：选平台／定方向"):
            nav.confirm(path)
        put([self._card("短标签：选平台／定方向", click=("可以", 1))], labels={"platform": "挑平台"})   # 用户在页面上改了字，还没照改
        with self.assertRaisesRegex(ValueError, "第 1 段改成了「挑平台」，project.json 里还是「选平台」"):
            nav.confirm(path)
        put([self._card("短标签：选平台／定方向", click=("可以", 1))])
        rec = nav.confirm(path)
        self.assertIn("用户对导航文字栏 导1 第 1 版点了可以", rec["basis"])
        self.assertEqual(rec["labels"], ["选平台", "定方向"])
        self.assertTrue(nav.confirmed(nav.read(path), path.parent))
        # 用户自己改的字算看过：卡片上没写也行
        p = nav.read(path); p["segments"][0]["label"] = "挑平台"; path.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        put([self._card("短标签：定方向", click=("可以", 1))], labels={"platform": "挑平台"})
        self.assertIn("点了可以", nav.confirm(path)["basis"])

    def test_confirm_by_settled_column(self):
        # 新1：用户点了导航文字栏“这一栏定了”也算，但点了之后这一栏变了（标签改了、卡片换版）就不算
        path, board = self._board_case()
        board.mkdir()
        items = [self._card("请确认", click=("可以", 1))]
        sig = lambda labels, its=items: nav._h([[it["id"], it["versions"][-1]["v"], it["events"][-1].get("action"), None, it["events"][-1].get("text")] for it in its] + [[]] + [labels])
        good = {"导航文字": {"at": "2026-11-22 20:20:00", "sig": sig([["platform", "选平台"], ["direction", "定方向"]]), "who": "用户（审片台页面）"}}
        (board / "标记.json").write_text("{}", encoding="utf-8")
        (board / "审片台.json").write_text(json.dumps({"items": items, "edits": [], "settled": good}, ensure_ascii=False), encoding="utf-8")
        self.assertIn("这一栏定了", nav.confirm(path)["basis"])
        stale = {"导航文字": {**good["导航文字"], "sig": sig([["platform", "选平台"], ["direction", "别的字"]])}}
        (board / "审片台.json").write_text(json.dumps({"items": [], "edits": [], "settled": stale}, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "之后这一栏变了"):
            nav.confirm(path)

    def test_settled_sig_same_as_board(self):
        # 新1：“这一栏定了”是否还有效，和审片台 board.col_sig 用同一个算法；找得到口播自动剪辑就拿真代码对一遍，防两边各改各的
        for home in (nav.ROOT.parent / "studio",):
            scripts = home / "skill/thinkcut-edit/scripts"
            if (scripts / "thinkcut/board.py").is_file():
                break
        else:
            self.skipTest("没有 studio/（口播自动剪辑）")
        sys.path.insert(0, str(scripts))
        try:
            from thinkcut import board as B
        finally:
            sys.path.remove(str(scripts))
        from types import SimpleNamespace
        path, board = self._board_case()
        board.mkdir()
        items = [self._card("短标签：选平台／定方向", v=2, click=("写要求", 2, "再短点")), {**self._card("别的"), "id": "导2"}]
        (board / "审片台.json").write_text(json.dumps({"items": items, "edits": [{"id": "x", "column": "导航文字", "new": "y"}]}, ensure_ascii=False), encoding="utf-8")
        (board / "标记.json").write_text(json.dumps({"nav_labels": {"direction": "定方向"}}, ensure_ascii=False), encoding="utf-8")
        real = B.col_sig(SimpleNamespace(dir=board.parent), "导航文字")
        d = nav.read(board / "审片台.json"); d["settled"] = {"导航文字": {"at": "t", "sig": real}}
        (board / "审片台.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        basis, why = nav.board_evidence(nav.read(path), board)
        self.assertTrue(basis and "这一栏定了" in basis, why)

    def test_short_labels_layout(self):
        # 2026-11-14 定稿样式：进度条按大章节分段，每段下面写短标签
        self.assertIsNone(nav.validate(self.p, self.root)["chapters"])
        self.p["segments"][0]["label"], self.p["segments"][1]["label"] = "选平台", "定方向"
        chapters = nav.validate(self.p, self.root)["chapters"]
        self.assertEqual([c["label"] for c in chapters], ["选平台", "定方向"])
        self.assertLess(chapters[0]["x"] + chapters[0]["w"], chapters[1]["x"])
        self.assertGreaterEqual(chapters[0]["max_chars"], 3)
        path = self.root / "project.json"
        nav.write(path, self.p)
        nav.confirm(path)
        self.assertIn("| 1 | 选平台 | %d | 00:01–00:03 |" % chapters[0]["max_chars"], (self.root / nav.OUTLINE).read_text(encoding="utf-8"))
        changed = copy.deepcopy(self.p); changed["segments"][1]["label"] = "方向"
        self.assertFalse(nav.confirmed(changed, self.root))
        long = copy.deepcopy(self.p); long["segments"][0]["label"] = "字" * 40
        with self.assertRaisesRegex(ValueError, "放不下：这一段最多 %d 个汉字" % chapters[0]["max_chars"]):
            nav.validate(long, self.root)
        missing = copy.deepcopy(self.p); del missing["segments"][1]["label"]
        with self.assertRaisesRegex(ValueError, "第 2 段缺短标签"):
            nav.validate(missing, self.root)
        wide = copy.deepcopy(self.p); wide["target"].update(width=3840, height=2160, nav_height=224)
        self.assertEqual(nav.validate(wide, self.root)["unit"], 2160)      # 横屏按短边算字号，224 高放得下
        low = copy.deepcopy(self.p); low["target"]["nav_height"] = 100
        with self.assertRaisesRegex(ValueError, "导航高度不够"):
            nav.validate(low, self.root)

    def test_label_height_floor_matches_drawing(self):
        # 2026-11-20 审查：下限按 1.3 倍字号算，比 PingFang 真实行高 1.4 倍小，卡在下限时到 render 才失败
        self.p["segments"][0]["label"], self.p["segments"][1]["label"] = "选平台", "定方向"
        self.p["target"].update(width=2160, height=2880, nav_height=224)
        nav.validate(self.p, self.root)
        floor = label_floor(2160)
        self.assertGreaterEqual(floor - round(2160*nav.read(nav.ROOT / "assets/blue.json")["label_top_ratio"]), 60*1.41)
        self.p["target"]["nav_height"] = floor
        nav.validate(self.p, self.root)
        self.p["target"]["nav_height"] = floor - 2
        with self.assertRaisesRegex(ValueError, "至少要 %d" % floor):
            nav.validate(self.p, self.root)

    def test_label_width_never_underestimates(self):
        # 2026-11-20 审查：原来英文一律按 0.6 个字号估，“W”这类宽字母会漏到 render 才报超宽
        self.p["segments"][1]["label"] = "定方向"
        chapters = nav.validate(dict(self.p, segments=[dict(s, label="") for s in self.p["segments"]]), self.root)["chapters"]
        font = round(1920*nav.read(nav.ROOT / "assets/blue.json")["label_ratio"])
        n = int(chapters[0]["w"] // (font*0.6))      # 旧估算刚好放得下的 W 个数
        self.p["segments"][0]["label"] = "W" * n
        with self.assertRaisesRegex(ValueError, "放不下"):
            nav.validate(self.p, self.root)
        if sys.platform != "darwin" or not shutil.which("swift"):
            self.skipTest("实测宽度需要 macOS Swift/AppKit")
        real = swift_widths(LABEL_SAMPLES, 60)
        for text, (bold, medium) in zip(LABEL_SAMPLES, real):
            self.assertLessEqual(max(bold, medium), nav.label_width(text, 60), text)

    def test_empty_label_on_tiny_segment(self):
        # 2026-11-20 审查：段宽不足一个字（甚至比段间小缝还窄）时，空标签也报“放不下”
        self.p["target"]["fps"] = "240"
        self.p["segments"][0]["end"] = 3.501
        self.p["segments"][1:1] = [dict(chapter="tiny", title="一闪而过", question="", label="", start=3.501, end=3.506)]
        self.p["segments"][2]["start"] = 3.506
        self.p["segments"][0]["label"], self.p["segments"][2]["label"] = "选平台", "定方向"
        chapters = nav.validate(self.p, self.root)["chapters"]
        self.assertEqual((chapters[1]["w"], chapters[1]["max_chars"]), (0, 0))
        path = self.root / "project.json"
        nav.write(path, self.p)
        nav.outline(path)
        self.assertIn("| 2 | （空着） | 0 |", (self.root / nav.OUTLINE).read_text(encoding="utf-8"))
        self.p["segments"][1]["label"] = "字"
        with self.assertRaisesRegex(ValueError, "第 2 段短标签「字」放不下"):
            nav.validate(self.p, self.root)

    def test_label_squeeze_before_error(self):
        # 2026-11-21 新19：字多放不下先横向压窄（有下限），压到下限还放不下才报错
        self.p["segments"][0]["label"], self.p["segments"][1]["label"] = "选平台", "定方向"
        base = nav.validate(self.p, self.root)["chapters"]
        self.assertEqual([c["squeeze"] for c in base], [1.0, 1.0])      # 放得下的不压，老结果不变
        font, smin = round(1920*nav.read(nav.ROOT / "assets/blue.json")["label_ratio"]), nav.read(nav.ROOT / "assets/blue.json")["label_squeeze_min"]
        w = base[0]["w"]
        n = int(w // font) + 1      # 比不压窄多一个字
        self.p["segments"][0]["label"] = "字" * n
        chapters = nav.validate(self.p, self.root)["chapters"]
        self.assertTrue(smin <= chapters[0]["squeeze"] < 1)
        self.assertEqual(chapters[0]["max_chars"], int(w // font))      # 确认单上的“最多几个字”仍是不压窄的数
        path = self.root / "project.json"
        nav.write(path, self.p); nav.outline(path)
        self.assertRegex((self.root / nav.OUTLINE).read_text(encoding="utf-8"), r"字+（会压窄到 \d+%）")
        self.p["segments"][0]["label"] = "字" * (int((w - nav.squeeze_pad(1920)) / (font*smin)) + 1)      # 压到下限也多一个字
        with self.assertRaisesRegex(ValueError, "压到最窄 75%% 也只能放 %d 个汉字" % (int((w - nav.squeeze_pad(1920)) / (font*smin)))):
            nav.validate(self.p, self.root)
        self.p["segments"][0]["label"] = "字" * n
        self.p["style"] = {"label_squeeze_min": 1}      # 压窄关掉＝老规则：放不下就报错
        with self.assertRaisesRegex(ValueError, "放不下：这一段最多 %d 个汉字，请换短" % int(w // font)):
            nav.validate(self.p, self.root)
        self.p["style"] = {"label_squeeze_min": 0.1}
        with self.assertRaisesRegex(ValueError, "label_squeeze_min"):
            nav.validate(self.p, self.root)

    def test_background_modes(self):
        # 2026-11-21 新19：导航条底三种做法，老项目（不写 background_mode）一个参数都不变
        self.p["segments"][0]["label"], self.p["segments"][1]["label"] = "选平台", "定方向"
        blue = nav.read(nav.ROOT / "assets/blue.json")
        self.assertEqual((blue["background_mode"], blue["background_alpha"], blue["shadow_alpha"]), ("opaque", 1.0, 0.0))
        self.assertEqual(nav.validate(self.p, self.root)["style"], blue)
        for mode, alpha in (("dim", 0.45), ("clear", 0.0)):
            self.p["style"] = {"background_mode": mode}
            style = nav.validate(self.p, self.root)["style"]
            self.assertEqual((style["background_alpha"], style["background"]), (alpha, "#000000"))
            self.assertGreater(style["shadow_alpha"], 0)
        self.p["style"] = {"background_mode": "半透明"}
        with self.assertRaisesRegex(ValueError, "background_mode 只能是"):
            nav.validate(self.p, self.root)
        self.p["style"] = {"background_mode": "dim"}
        for segment in self.p["segments"]:
            del segment["label"]
        with self.assertRaisesRegex(ValueError, "只支持短标签样式"):
            nav.validate(self.p, self.root)

    def test_changed_source_rejected(self):
        (self.root / "input.srt").write_text(SRT + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "字幕已变化"):
            nav.validate(self.p, self.root)

    def test_missing_and_overlapping_timeline(self):
        for offset in (-0.1, 0.1):
            p = copy.deepcopy(self.p)
            p["segments"][1]["start"] += offset
            with self.assertRaisesRegex(ValueError, "无缺口"):
                nav.validate(p, self.root)

    def test_time_ambiguity(self):
        with self.assertRaises(ValueError):
            nav.number("20:20")
        self.assertEqual(nav.number("00:00:20.020"), 20.02)

    def test_invalid_dimensions(self):
        self.p["target"]["width"] = 1919
        with self.assertRaises(ValueError):
            nav.validate(self.p, self.root)

    def test_subframe_segment(self):
        self.p["segments"][0]["end"] = 1.001
        self.p["segments"][1]["start"] = 1.001
        with self.assertRaisesRegex(ValueError, "不足一帧"):
            nav.validate(self.p, self.root)

    def test_sorted_and_overlap_review(self):
        (self.root / "input.srt").write_text("1\n00:00:02,000 --> 00:00:04,000\n后文\n\n2\n00:00:01,000 --> 00:00:03,000\n前文\n", encoding="utf-8")
        entries, warnings = nav.subtitles(self.root / "input.srt")
        self.assertEqual(entries[0]["text"], "前文")
        self.assertEqual(len(warnings), 2)

    def test_preset_cannot_change_canvas(self):
        old = nav.CONFIG
        try:
            nav.CONFIG = self.root / "config"
            self.assertNotIn("fps", nav.preset())
            self.assertNotIn("width", nav.preset())
        finally:
            nav.CONFIG = old

    def test_single_line_and_visual_only_segment(self):
        self.p["segments"][0]["question"] = ""
        self.assertEqual(nav.validate(self.p, self.root)["rows"][0]["question"], "")
        self.p["source"] = {}
        with self.assertRaisesRegex(ValueError, "visual_evidence"):
            nav.validate(self.p, self.root)
        for segment in self.p["segments"]:
            segment["visual_evidence"] = [{"time": segment["start"], "note": "界面切换到对应步骤"}]
        rows = nav.validate(self.p, self.root)["rows"]
        self.assertIsNone(rows[0]["first_subtitle"])
        self.p["segments"][0]["visual_evidence"][0]["time"] = 99
        with self.assertRaises(ValueError):
            nav.validate(self.p, self.root)

    def test_video_version_change(self):
        video = self.root / "input.mp4"
        video.write_bytes(b"sample")
        self.p["source"].update(video=str(video), video_size=6)
        nav.validate(self.p, self.root)
        video.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "原视频版本"):
            nav.validate(self.p, self.root)

    def test_merge_uses_sample_offsets_not_service_duration(self):
        segments = []
        for i in range(2):
            path = self.root / f"segment-{i}.wav"
            with wave.open(str(path), "wb") as f:
                f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                f.writeframes(b"\0\0" * 16000)
            (self.root / f"segment-{i}.srt").write_text("1\n00:00:00,100 --> 00:00:00,900\n原文 unchanged\n")
            segments.append(dict(audio=path.name, subtitles=f"segment-{i}.srt", offset_samples=i*16000,
                                 samples=16000, duration_seconds=1.144, sha256=nav.digest(path)))
        manifest = self.root / "audio-manifest.json"
        nav.write(manifest, dict(sample_rate=16000, segments=segments))
        out = self.root / "merged.srt"
        nav.merge_subtitles(manifest, out)
        rows, warnings = nav.subtitles(out)
        self.assertEqual(rows[1]["start"], 1.1)
        self.assertEqual(rows[1]["text"], "原文 unchanged")
        self.assertFalse(warnings)
        with self.assertRaises(FileExistsError):
            nav.merge_subtitles(manifest, out)
        (self.root / segments[0]["audio"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "音频分段已变化"):
            nav.merge_subtitles(manifest, self.root / "new.srt")

    def test_subtitle_beyond_duration_and_repeated_chapter(self):
        self.p["duration"] = 5
        self.p["coverage"] = [1, 5]
        with self.assertRaisesRegex(ValueError, "字幕超出"):
            nav.validate(self.p, self.root)
        self.p["duration"] = 6
        self.p["coverage"] = [1, 6]
        self.p["segments"].append(dict(chapter="platform", title="回顾", question="", start=4.501, end=6))
        self.p["segments"][1]["end"] = 4.501
        with self.assertRaisesRegex(ValueError, "大章节必须连续"):
            nav.validate(self.p, self.root)

    def test_fractional_fps_and_offsets(self):
        for fps in ("24", "25", "30", "60", "30000/1001", "24000/1001"):
            self.p["target"]["fps"] = fps
            s = nav.validate(self.p, self.root)
            self.assertLessEqual(abs(s["placement"]-1), 0.5 / float(nav.Fraction(fps)))
            self.assertEqual(s["rows"][-1]["frame_end"], s["frames"])

    def test_bad_fps_text_gives_chinese_reason(self):
        # 2026-11-13 审查：帧率写成 "30/0" 会漏出英文的除零报错
        for fps in ("30/0", "abc", ""):
            self.p["target"]["fps"] = fps
            with self.assertRaisesRegex(ValueError, "目标帧率写法不对"):
                nav.validate(self.p, self.root)

    def test_invalid_secondary_line(self):
        self.p["segments"][0]["question"] = "line one\nline two"
        with self.assertRaisesRegex(ValueError, "第二行"):
            nav.validate(self.p, self.root)

def caret_check(case):
    """2026-11-20 审查：量第 0 帧、中间帧、最后一帧的红色光标中心和蓝色进度右沿，都要在 margin + tw×帧号/(帧数-1) 的 1 像素内。
    位置都按像素边界坐标算：光标中心＝实心竖线左边界和右边界的中点，进度右沿＝最右一个蓝色像素的右边界。"""
    out, ff = case / "output", nav.executable("ffmpeg")
    spec = nav.validate(nav.read(case / "project.json"), case)
    s, w, h, count, u = spec["style"], spec["width"], spec["height"], spec["frames"], spec["unit"]
    margin, tw = nav.bar(s, w, u, count, spec["chapters"])[:2]
    ty, th, ch = nav.rise(s, u, h)[0], max(2, round(u*s["track_height_ratio"])), round(u*s["caret_height_ratio"])
    def rows(args):      # 每帧只取一行像素，长片也不占内存
        raw = subprocess.run([ff, "-v", "error", *args, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             cwd=out, capture_output=True, check=True).stdout
        return [raw[i:i+w*3] for i in range(0, len(raw), w*3)]
    caret = rows(["-i", "navigation-full.mp4", "-vf", f"format=rgb24,crop={w}:1:0:{ty+th-ch+(ch-th)//2}"])      # 光标露在底线上方的那一截
    graph = (out / "filters.txt").read_text(encoding="utf-8").split("[base];")[0]      # 不叠光标的那半张滤镜图：蓝色进度本身
    blue = rows(["-f", "concat", "-safe", "0", "-i", "frames.txt", "-filter_complex", f"{graph}[base];[base]crop={w}:1:0:{ty+th//2}[row]",
                 "-map", "[row]", "-frames:v", str(count)])
    assert len(caret) == count and len(blue) == count, (len(caret), len(blue), count)
    found = []
    longest = max(spec["chapters"], key=lambda c: c["w"])      # 中间帧取最长一段的正中，避开段间小缝把蓝色盖住
    for f in (0, (longest["frame_start"] + longest["frame_end"])//2, count-1):
        red = [x for x in range(w) if caret[f][3*x] > 200 and caret[f][3*x+1] < 150]
        lit = [x for x in range(w) if blue[f][3*x+2] > 180 and blue[f][3*x] < 150]
        want, center, edge = margin + tw*f/(count-1), (red[0] + red[-1] + 1)/2, lit[-1] + 1
        assert abs(center-want) <= 1 and abs(edge-want) <= 1 and abs(edge-center) <= 1, (case.name, f, want, center, edge)
        found.append((f, round(want, 2), center, edge))
    return found

def integration(root):
    root.mkdir(parents=True, exist_ok=False)
    for name, width, height, nav_height, fps, single in [
        ("portrait", 1920, 2560, 280, "30000/1001", False),
        ("landscape", 1280, 720, 188, "25", False),
        ("single", 1080, 1920, 160, "60", True),
        ("visual", 1280, 720, 188, "24", False),
        ("multilingual", 1280, 720, 188, "30", False),
        ("labels", 2160, 2880, 224, "30", False),
        ("labels-wide", 3840, 2160, 224, "30", False),
        ("labels-ntsc", 2160, 2880, 224, "30000/1001", False),
        ("labels-floor", 2160, 2880, label_floor(2160), "30", False),      # 导航高度卡在下限也要能出片
        ("labels-empty", 2160, 2880, 224, "240", False),      # 比段间小缝还窄的段写空标签
        ("labels-dim", 2160, 2880, 224, "30", False),      # 2026-11-21 新19：半透明深色底（ProRes 4444 带透明）
        ("labels-clear", 2160, 2880, 224, "30", False),      # 完全透明底
        ("labels-squeeze", 2160, 2880, 224, "30", False),      # 字多压窄（不透明底，出 mp4）
    ]:
        case = root / name
        case.mkdir()
        project = fixture(case)
        project["target"] = dict(width=width, height=height, nav_height=nav_height, fps=fps)
        if name == "multilingual":
            for segment in project["segments"]:
                segment["title"] = "第一章 / Chapter 1"
                segment["question"] = "手順と確認 / 단계 확인"
        if name.startswith("labels"):
            project["segments"][0]["label"], project["segments"][1]["label"] = "选平台", "定方向"
        if name in ("labels-dim", "labels-clear"):
            project["style"] = dict(background_mode=name.split("-")[1])
        if name == "labels-squeeze":
            project["segments"][0]["label"], project["segments"][1]["label"] = "选平台选平台选平台", "定方向"
        if name == "labels-empty":
            project["segments"][1]["start"] = 3.506
            project["segments"].insert(1, dict(chapter="tiny", title="一闪而过", question="", label="", start=3.501, end=3.506))
        if single:
            for segment in project["segments"]:
                segment["question"] = ""
        if name == "visual":
            project["source"] = {}
            for segment in project["segments"]:
                segment["visual_evidence"] = [{"time": segment["start"], "note": "合成画面定位"}]
        if os.environ.get("NAV_TEST_FONT_REGULAR"):
            project["style"] = dict(font_title_path=os.environ["NAV_TEST_FONT_BOLD"],
                                    font_question_path=os.environ["NAV_TEST_FONT_REGULAR"])
        nav.write(case / "project.json", project)
        nav.confirm(case / "project.json")
        nav.render(case / "project.json", case / "output")
        translucent = name in ("labels-dim", "labels-clear")
        info = nav.probe(case / ("output/navigation-full.mov" if translucent else "output/navigation-full.mp4"))
        assert len(info["streams"]) == 1 and info["streams"][0]["width"] == width
        if translucent:
            assert info["streams"][0]["pix_fmt"].startswith("yuva") and info["streams"][0]["codec_name"] == "prores", info
            raw = subprocess.run([nav.executable("ffmpeg"), "-v", "error", "-i", str(case / "output/navigation-full.mov"), "-frames:v", "1",
                                  "-vf", "crop=1:1:5:5,format=rgba", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
            want = 115 if name == "labels-dim" else 0      # 背景角落的透明度：45% 黑 ≈ 115/255，透明＝0
            assert abs(raw[3] - want) <= 2, (name, list(raw))
            print(name, "背景角落 RGBA:", list(raw))
            continue
        if name.startswith("labels"):
            print(name, "光标/进度（帧号, 应在, 光标中心, 进度右沿）:", caret_check(case))
    case = root / "overflow"
    case.mkdir()
    project = fixture(case)
    project["segments"][0]["title"] = "超长标题" * 80
    if os.environ.get("NAV_TEST_FONT_REGULAR"):
        project["style"] = dict(font_title_path=os.environ["NAV_TEST_FONT_BOLD"], font_question_path=os.environ["NAV_TEST_FONT_REGULAR"])
    nav.write(case / "project.json", project)
    nav.confirm(case / "project.json")
    try:
        nav.render(case / "project.json", case / "output")
        raise AssertionError("overflowing title was accepted")
    except (ValueError, subprocess.CalledProcessError):
        pass
    # Exercise a late audio start, sample-exact split and a silent source.
    source = root / "delayed.mp4"
    subprocess.run([nav.executable("ffmpeg"), "-v", "error", "-n", "-f", "lavfi", "-i", "color=size=320x240:rate=25:duration=3",
                    "-itsoffset", "0.5", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2",
                    "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    manifest = nav.extract_audio(source, root / "audio", 1)
    assert manifest["segments"][1]["offset_samples"] == 16000
    with wave.open(str(root / "audio/timeline.wav"), "rb") as f:
        import array
        samples = array.array("h", f.readframes(f.getnframes()))
    assert max(abs(x) for x in samples[:4800]) < 20
    assert max(abs(x) for x in samples[11200:16000]) > 100
    try:
        nav.extract_audio(source, root / "audio", 1)
        raise AssertionError("existing output overwritten")
    except FileExistsError:
        pass
    silent = root / "silent.mp4"
    subprocess.run([nav.executable("ffmpeg"), "-v", "error", "-n", "-f", "lavfi", "-i", "color=size=320x240:duration=1", "-c:v", "libx264", str(silent)], check=True)
    try:
        nav.extract_audio(silent, root / "silent-audio")
        raise AssertionError("missing audio not rejected")
    except ValueError as exc:
        assert "没有音轨" in str(exc)
    print("Integration passed: 13 renders (含半透明/透明底、字压窄), caret/progress position, text-overflow rejection, delayed audio, PCM offsets, no-audio rejection")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--render":
        integration(Path(sys.argv[2]).resolve())
    else:
        unittest.main()
