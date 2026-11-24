#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试（2026-11-22 试验田：在 261118 的副本上从头跑一遍查出来的问题）。不连达芬奇，用替身模拟达芬奇的行为。
1 修音（voice-polish）渲完纯口播后，审核线的音轨开关要恢复原样、修音轨要开着（以前渲完停在“交付”页，在那一页恢复开关不生效，
  所有音轨都关着，导出没有声音）。"""
import contextlib, io, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'
sys.path.insert(0, str(SK / 'scripts'))
from thinkcut import resolve, voice      # noqa: E402
fails = []


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


class R:          # 达芬奇：记着现在在哪一页
    def __init__(self): self.page = 'edit'
    def OpenPage(self, name): self.page = name; return True


class Item:
    def __init__(self): self.props = {'AudioVoiceIsolationEnabled': False, 'AudioVoiceIsolationAmount': 0}
    def GetProperty(self, k): return self.props.get(k)
    def SetProperty(self, k, v): self.props[k] = v; return True


class TL:         # 时间线：像真达芬奇一样，不在剪辑页时改轨道开关不生效（返回 True 但没改）
    def __init__(self, r, on): self.r, self.on, self.items = r, dict(on), [Item(), Item()]
    def GetItemListInTrack(self, kind, j): return self.items if j == 1 else []
    def GetTrackCount(self, kind): return len(self.on)
    def GetIsTrackEnabled(self, kind, j): return self.on[j]
    def SetTrackEnable(self, kind, j, en):
        if self.r.page == 'edit': self.on[j] = en
        return True
    def GetTrackName(self, kind, j): return {1: '音频 1', 2: 'B-roll 原声', 3: '音效', 4: '口播（隔离修音）'}[j]


class P:
    def __init__(self, r, tl): self.r, self.tl = r, tl
    def SetCurrentTimeline(self, tl): return True
    def SetCurrentRenderMode(self, m): return True
    def SetRenderSettings(self, s): return True
    def AddRenderJob(self): return 'job1'


class PM:
    def SaveProject(self): return True


def fake_wait(p, jid, limit=3600):      # 渲染：达芬奇切到“交付”页，渲完停在那里；渲出一个文件
    p.r.page = 'deliver'; p.r.rendered = True
    Path(OUT, NAME[0] + '.wav').write_bytes(b'x')


with tempfile.TemporaryDirectory() as OUT:
    NAME = []
    orig = (voice._wait, voice.set_audio_format, resolve.fresh_render, voice.time.strftime)
    voice._wait = fake_wait; voice.set_audio_format = lambda p: 'wav'; resolve.fresh_render = lambda p, preset='H.264 Master': True
    voice.time.strftime = lambda fmt, *a: (NAME.append('人声隔离_渲染_000000') or '000000') if fmt == '%H%M%S' else orig[3](fmt, *a)
    try:
        r = R(); before = {1: False, 2: True, 3: True, 4: True}; tl = TL(r, before); p = P(r, tl)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): got = voice.isolate(r, PM(), p, tl, OUT)
        check(tl.on == before and got.name.startswith('人声隔离_渲染_'),
              f'修音渲完纯口播（达芬奇停在交付页）：审核线音轨开关恢复成原样 {tl.on}，不再全关（以前全关，导出没有声音）')
        check(all(not it.props['AudioVoiceIsolationEnabled'] for it in tl.items), '口播片段的人声隔离设置也恢复了')
        # 达芬奇不听话（剪辑页也改不动）时：停下说清楚哪几条轨，不悄悄往下走
        class Stuck(TL):          # 渲染前能改（单开原口播），渲完之后怎么都改不动
            def SetTrackEnable(self, kind, j, en):
                if not getattr(self.r, 'rendered', False): self.on[j] = en
                return True
        r2 = R(); tl2 = Stuck(r2, before); NAME.clear()
        try:
            with contextlib.redirect_stdout(io.StringIO()): voice.isolate(r2, PM(), P(r2, tl2), tl2, OUT); msg = None
        except SystemExit as e: msg = str(e)
        check(bool(msg) and '没恢复成原样' in msg and '「B-roll 原声」应开' in msg and '少声音' in msg,
              '轨道开关恢复不了：停下说清楚是哪几条、应开还是关，不悄悄往下走' + ('' if msg else '：没停'))
    finally:
        voice._wait, voice.set_audio_format, resolve.fresh_render, voice.time.strftime = orig

src = (SK / 'scripts/thinkcut/voice.py').read_text(encoding='utf-8')
check("if not tl.GetIsTrackEnabled('audio', j): die(" in src and '导出会没有人声' in src, '放好修音轨、重开读回时也核对修音轨本身是开的')

print('试验田测试全部通过' if not fails else f'试验田测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
