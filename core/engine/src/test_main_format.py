# -*- coding: utf-8 -*-
"""新主线画幅沿用上一版主线（2026-11-27 261114 反馈单 新4：已有工程里重剪，新主线按工程默认建成 1920×1080、缩放成“放进去”）。"""
import unittest
from unittest.mock import patch
import import_mvp_delivery as importer


class Line:
    def __init__(self, name, w='1920', h='1080', fit='scaleToFit'):
        self.name = name; self.s = {'timelineResolutionWidth': w, 'timelineResolutionHeight': h,
                                    'timelineInputResMismatchBehavior': fit, 'useCustomSettings': '0'}
    def GetName(self): return self.name
    def GetSetting(self, k): return self.s[k]
    def SetSetting(self, k, v): self.s[k] = v; return True


class Project:
    def __init__(self, lines): self.lines = lines
    def GetTimelineCount(self): return len(self.lines)
    def GetTimelineByIndex(self, i): return self.lines[i - 1]


class MainFormatTests(unittest.TestCase):
    def run_match(self, lines, base):
        with patch.object(importer, 'direct_timeline', return_value=(Project(lines), base)):
            return importer.match_previous_format('p', base.name, 'v4_主线_1127-1211_e67a')

    def test_new_main_line_copies_the_latest_previous_main_line(self):
        base = Line('v4_无字幕_e67a8f7f0896')
        lines = [Line('v2_主线_1114-2133_6a39', '2160', '2880', 'scaleToCrop'), Line('审核线_v2_占位口播', '1080', '1920'),
                 Line('v1_主线_1110-0900_aaaa', '3840', '2160'), Line('v2_主线_1114-2100_bbbb_未完成_abcdef', '720', '1280'), base]
        got = self.run_match(lines, base)
        self.assertEqual(got, {'from': 'v2_主线_1114-2133_6a39', 'width': 2160, 'height': 2880, 'scaling': 'scaleToCrop'})
        self.assertEqual((base.s['useCustomSettings'], base.s['timelineResolutionWidth'], base.s['timelineResolutionHeight'],
                          base.s['timelineInputResMismatchBehavior']), ('1', '2160', '2880', 'scaleToCrop'))

    def test_first_main_line_keeps_old_behaviour(self):
        base = Line('v1_无字幕_0123456789ab')
        self.assertIsNone(self.run_match([Line('审核线_v1', '2160', '2880'), base], base))
        self.assertEqual(base.s['timelineResolutionWidth'], '1920')

    def test_refused_setting_stops_delivery(self):
        class Stubborn(Line):
            def SetSetting(self, k, v): return False
        base = Stubborn('v4_无字幕_e67a8f7f0896')
        with self.assertRaisesRegex(ValueError, '设不成上一版主线'):
            self.run_match([Line('v2_主线_1114-2133_6a39', '2160', '2880'), base], base)


if __name__ == '__main__':
    unittest.main()
