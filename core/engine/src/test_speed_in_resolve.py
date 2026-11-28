# -*- coding: utf-8 -*-
"""口播 1.2 倍速改在达芬奇里设（2026-11-27 用户：“不需要重新压一遍视频……直接可以用达芬奇里1.2倍速”）的离线回归。

剪口播的时间都是加速后的时间；放进达芬奇时原片按换算的帧放、再设 120%。换算规则来自达芬奇 21.1 实测（见 speed_map.py）。
"""
import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import import_mvp_delivery as importer
import source_guard
from speed_map import clip_range, speed_of
from test_delivery_flow import FlowSession
from test_safety_checks import SEGMENTS, SPEC, SOURCE, MEDIA


class ClipRangeTests(unittest.TestCase):
    def test_matches_what_resolve_21_did_in_the_trial(self):
        # 试验田实测：30 帧原片放 600 帧 → 时间线 599 帧；设 120% 后来源从第 300 帧起
        self.assertEqual(clip_range(250, 849, 1.2, 30), (300, 900))
        # 时间线长度 = floor(放的帧数 × 29.97/30) 正好等于要的长度（实测 1001 帧 → 1000、37 → 36、333 → 332）
        for length in (1, 2, 36, 332, 599, 1000, 9000):
            start, end = clip_range(1000, 1000 + length, 1.2, 30)
            self.assertEqual(int((end - start) * 30000 / 1001 / 30 + 1e-9) if length > 1 else 1, length)

    def test_29_97_original_without_speed_is_frame_for_frame(self):
        self.assertEqual(clip_range(10, 40, 1, 30000 / 1001), (10, 40))

    def test_60_fps_and_slow_originals(self):
        start, end = clip_range(0, 300, 1.2, 60)
        self.assertEqual((start, int((end - start) * (30000 / 1001) / 60)), (0, 300))
        with self.assertRaisesRegex(ValueError, '低于 29.97'):
            clip_range(0, 30, 1.2, 25)

    def test_old_projects_have_speed_one(self):
        self.assertEqual(speed_of({'source': 'x'}), 1.0)
        self.assertEqual(speed_of({'speed': 1.2}), 1.2)
        with self.assertRaises(ValueError):
            speed_of({'speed': True})


class SpeedSession(FlowSession):
    """假达芬奇：原片 30 帧；记下设速度的调用，读回按设过的速度回答。"""
    def __init__(self):
        super().__init__(); self.speeds = {}

    async def call_tool(self, tool, args):
        action, p = args['action'], args['params']
        if action == 'get_clip_property' and p['key'] == 'FPS':
            payload = {'properties': '30.0'}
        elif action == 'get_source_start_frame':
            payload = {'frame': clip_range(SEGMENTS[p['item_index']]['src_in'], SEGMENTS[p['item_index']]['src_out'], 1.2, 30)[0]}
        elif action == 'get_speed':
            options = self.speeds.get(p['item_index'], {})
            payload = {'speed': {'Percentage': options.get('Percentage', 100.0), 'PitchCorrection': options.get('PitchCorrection', False)}}
        elif action == 'get_left_offset':
            raise AssertionError('倍速交付包不该按左偏移核对来源')
        else:
            return await super().call_tool(tool, args)
        self.calls.append((tool, action, p))
        text = type('Text', (), {'type': 'text', 'text': json.dumps(payload)})
        return type('Result', (), {'isError': False, 'content': [text]})


class SpeedDeliveryTests(unittest.TestCase):
    main_format = None

    def timeline(self, session, works=True):
        """设速度直接走达芬奇脚本接口（不经 MCP）：假时间线上的片段把设的速度记到 session.speeds。"""
        class Item:
            def __init__(self, i): self.i = i
            def GetStart(self): return SEGMENTS[self.i]['tl_in']
            def SetSpeed(self, options):
                if works: session.speeds[self.i] = options
                return works
        timeline = types.SimpleNamespace(GetItemListInTrack=lambda kind, n: [Item(i) for i in range(len(SEGMENTS))])
        return timeline

    def deliver(self, session, works=True):
        @asynccontextmanager
        async def stdio(_): yield None, None
        mcp = types.ModuleType('mcp'); mcp.ClientSession = lambda *a, **kw: session; mcp.StdioServerParameters = lambda **kw: kw
        client = types.ModuleType('mcp.client'); stdio_module = types.ModuleType('mcp.client.stdio'); stdio_module.stdio_client = stdio
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp); (package / 'delivery_manifest.json').write_text('{}')
            manifest = {'source': {'path': SOURCE, 'media_pool_item_id': MEDIA, 'speed': 1.2}, 'project': 'project', 'timeline_name': 'final',
                        'base_timeline_name': 'base', 'srt_name': 'final.srt', 'fps': [30000, 1001], 'frames': 60, 'signature': 'abc'}
            (package / 'final.srt').write_text('srt'); (package / 'subtitle_cues.json').write_text('{}')
            async def export(session, path, *args, **kw): path.write_text('export')
            def author(args, **kw):
                (package / 'subtitle_drt_spec.json').write_text(json.dumps(SPEC)); (package / args[4]).write_text('drt')
            with patch.dict(sys.modules, {'mcp': mcp, 'mcp.client': client, 'mcp.client.stdio': stdio_module}), \
                 patch.object(importer, 'resolve_mcp_runtime', return_value=(package, package / 'python', package / 'server')), \
                 patch.object(importer, 'node_runtime', return_value=('node', {})), \
                 patch.object(importer, 'expected_subtitle_spec', return_value=SPEC), \
                 patch.object(importer, 'export_atomically', side_effect=export), \
                 patch.object(importer.subprocess, 'run', side_effect=author), \
                 patch.object(importer, 'archive_old_timelines', return_value=0), \
                 patch.object(importer, 'direct_timeline', side_effect=lambda name, final=None, need_timeline=True: self.timeline(session, works) if final else None), \
                 patch.object(importer, 'verify_package'), patch.object(importer, 'CONFIG', {}), \
                 patch.object(importer, 'match_previous_format', return_value=self.main_format), \
                 patch.object(importer, 'ever_passed', return_value=False):
                asyncio.run(importer.import_package(package, manifest, {'segments': SEGMENTS}, 'attempt'))
            return json.loads((package / 'mcp_readback.json').read_text())

    def test_original_is_placed_by_converted_frames_then_set_to_120_percent(self):
        session = SpeedSession()
        readback = self.deliver(session)
        created = next(p for _, a, p in session.calls if a == 'create_timeline_from_clips')
        first = SEGMENTS[0]
        self.assertEqual(created['clip_infos'][0]['start_frame'], clip_range(first['src_in'], first['src_out'], 1.2, 30)[0])
        self.assertEqual(created['clip_infos'][0]['record_frame'], first['tl_in'])
        self.assertEqual(session.speeds[0], {'Percentage': 120.0, 'PitchCorrection': True, 'RippleTimeline': False})
        self.assertEqual(readback['speed_percent'], 120.0)
        self.assertTrue(readback['passed'])
        # 设速度不经 MCP：MCP 每次改动前会把整条时间线另存一份 _archived_v01，交付会多出时间线
        self.assertNotIn('set_speed', [a for _, a, _ in session.calls])

    def test_speed_that_did_not_stick_stops_delivery(self):
        with self.assertRaisesRegex(ValueError, '设 120% 速度失败'):
            self.deliver(SpeedSession(), works=False)


class SourceGuardTests(unittest.TestCase):
    def test_voice_is_bound_to_the_transcription_and_original_to_its_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); run = folder / '审核'; run.mkdir(); (folder / '转写').mkdir()
            video = folder / '原片.mov'; video.write_bytes(b'video'); voice = folder / '口播声音_1.2倍速.wav'; voice.write_bytes(b'voice')
            st = voice.stat()
            (folder / '转写/source_manifest.json').write_text(json.dumps(
                {'path': str(voice.resolve()), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns}))
            config = {'source': str(video), 'voice': str(voice), 'speed': 1.2, 'video_identity': source_guard.video_identity(video)}
            baseline = source_guard.verify_source(config, run)
            self.assertEqual(baseline['source'], str(video.resolve()))      # 交付基线认的是放进达芬奇的原片
            self.assertEqual(baseline['source_size'], video.stat().st_size)
            os.utime(video, ns=(1, 1))
            with self.assertRaisesRegex(source_guard.SourceChanged, '原片 原片.mov 在建项之后改过'):
                source_guard.verify_source(config, run)
            with self.assertRaisesRegex(source_guard.SourceChanged, '还不支持换源'):
                source_guard.replace_source(config, run, video)


class PreviewTests(unittest.TestCase):
    def test_preview_part_takes_picture_from_original_and_sound_from_voice(self):
        import review_v2_server as server
        config = {'source': '/x/原片.mov', 'voice': '/x/口播声音_1.2倍速.wav', 'speed': 1.2, 'audio_stream': 0}
        with patch.object(server, 'CONFIG', config), patch.object(server, 'SPEED', 1.2), patch.object(server, 'TOTAL', 3600):
            args = server.preview_inputs(2, Path(config['source']))
        start = 2 * server.PART_SECONDS
        self.assertEqual(args[:2], ['-ss', f'{start * 1.2:.6f}']); self.assertEqual(args[4:6], ['-ss', f'{start:.6f}'])
        self.assertIn('setpts=(PTS-STARTPTS)/1.2,fps=30000/1001', args[args.index('-vf') + 1])
        self.assertEqual(args[args.index('-map') + 1:args.index('-map') + 4], ['0:v:0', '-map', '1:a:0'])


if __name__ == '__main__':
    unittest.main()
