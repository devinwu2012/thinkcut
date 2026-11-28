# -*- coding: utf-8 -*-
"""口播交付（保存到达芬奇）的离线回归：交付包身份、直连预检、整条导入流程、手工改过的主线不被顶替。

这些用例原来夹在旧版 B-roll 的测试文件里（test_broll_delivery / test_broll_v2_delivery / test_task11_delivery）。
旧版 B-roll（edit_project.py broll-index / broll-plan）2026-11-21 下线，和现行口播交付有关的用例搬到这里，
并补上“旧版 B-roll 已下线、碰到旧记录就停下”的用例。
"""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
import import_mvp_delivery as importer
import prepare_mvp_delivery as prepare
from test_safety_checks import probe, SEGMENTS, SPEC, MEDIA, SOURCE
from test_timeline_archive import FakeSession

ROOT = Path(__file__).resolve().parents[2]
MAIN = importer.NAME + '_主线_1007-1200_abcd'


class PackageIdentityTests(unittest.TestCase):
    def test_signature_and_inventory_match_the_original_algorithm(self):
        """没有旧 B-roll 的片子：交付包的身份算法、生成程序清单、成品清单和下线前一样。"""
        # 下线前“没配旧 B-roll 的片子”用的那份生成程序清单（当时叫 LEGACY_BUILD_INPUTS）
        self.assertEqual(prepare.BUILD_INPUTS, [
            'src/project_runtime.py', 'src/source_guard.py', 'src/timing.py', 'src/tight_cut.py', 'src/review_build.py',
            'src/prepare_mvp_delivery.py', 'src/import_mvp_delivery.py', 'src/run_mvp.py',
            'src/export_approved_captions.cjs', 'src/author_subtitle_drt.cjs',
            'caption_engine.js', 'caption_text.js', 'src/caption_ranges.js'])
        checks = {'review': 'a'}; builders = {n: 'b' for n in prepare.BUILD_INPUTS}; cuts = [[1, 2]]; source = {'path': 'x'}
        value = {'checksums': checks, 'builder': builders, 'cuts': cuts, 'source': source, 'project': prepare.PROJECT, 'version': 'mvp-v2'}
        original = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]
        self.assertEqual(prepare.delivery_signature(checks, builders, cuts, source), original)
        self.assertEqual(prepare.delivery_signature(checks, builders, cuts, source, None), original)
        self.assertNotEqual(prepare.delivery_signature(checks, builders, cuts, source, ['u1c0', 'u0c0']), original)
        self.assertEqual(prepare.artifact_names({'srt_name': 'a.srt'}),
                         ['trial_keep_segments.json', 'tight_manifest.json', 'a.srt', 'subtitle_cues.json', '字幕纠错记录.json'])

    def test_package_with_old_broll_is_refused_before_anything_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / 'Clip_0123456789ab'; package.mkdir()
            (package / 'delivery_manifest.json').write_text(json.dumps({'version': 'mvp-v3', 'no_resolve_render': True, 'broll_items': 1}))
            (package / 'broll_timeline.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, '旧版 B-roll.*已于 2026-11-21 下线.*没有动达芬奇') as caught:
                prepare.verify_package(package, require_current=False)
            self.assertIn('Clip_0123456789ab', str(caught.exception))
            self.assertEqual(sorted(p.name for p in package.iterdir()), ['broll_timeline.json', 'delivery_manifest.json'])


class OldBrollRemovedTests(unittest.TestCase):
    def test_old_chain_code_is_gone(self):
        for module, names in ((importer, ('append_broll', 'validate_broll_controls', 'validate_broll_offsets', 'broll_tracks', 'direct_write', 'direct_item')),
                              (prepare, ('read_broll', 'verify_broll', 'signature_broll', 'broll_clips', 'repair_package_clips', 'LEGACY_BUILD_INPUTS'))):
            for name in names:
                self.assertFalse(hasattr(module, name), name)
        self.assertFalse((ROOT / 'engine/broll/plan.py').exists())
        for path, words in (('engine/broll/media.py', ('def render_clip(', 'def scan(')), ('engine/broll/index.py', ('def build_index(',)),
                            ('bin/edit_project.py', ('broll-index', 'broll-plan', 'broll_timeline', 'B-roll片段')),
                            ('engine/src/web_delivery.py', ('broll',)), ('engine/src/paragraph_editor.py', ('broll',))):
            text = (ROOT / path).read_text(encoding='utf-8')
            for word in words:
                self.assertNotIn(word, text, path + ' ' + word)
        # 现行配 B-roll、素材库要用的还在
        media_text = (ROOT / 'engine/broll/media.py').read_text(encoding='utf-8')
        index_text = (ROOT / 'engine/broll/index.py').read_text(encoding='utf-8')
        for word in ('def render_live_clip(', 'def rehearsal_sheet(', 'def photo_source(', 'def extract_frames('):
            self.assertIn(word, media_text)
        for word in ('def build_index_files(', 'def merge_descriptions(', 'def supplement_descriptions(', 'def atomic_json(', 'def exclusive_lock('):
            self.assertIn(word, index_text)

    def test_rules_config_keeps_only_what_the_live_chain_reads(self):
        rules = json.loads((ROOT / 'engine/config/broll_rules.json').read_text(encoding='utf-8'))
        # 现行索引读 6 项切段规则，现行排布读覆盖上限；旧排布专用的 6 项（最短/最长秒数、片头片尾保护、间隔）随旧版下线
        self.assertEqual(rules, {'photo_max_seconds': 5.0, 'max_coverage': 1.0, 'window_min_seconds': 6.0, 'window_max_seconds': 12.0,
                                 'min_shot_seconds': 1.0, 'scene_threshold': 0.35, 'prompt_version': 1})

    def test_edit_project_no_longer_accepts_the_two_actions(self):
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        with tempfile.TemporaryDirectory() as tmp:
            for action in ('broll-index', 'broll-plan'):
                result = subprocess.run([sys.executable, str(ROOT / 'bin/edit_project.py'), action, '--project-dir', str(Path(tmp) / '项目')],
                                        capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 2, action); self.assertIn('invalid choice', result.stderr)
            help_text = subprocess.run([sys.executable, str(ROOT / 'bin/edit_project.py'), '--help'], capture_output=True, text=True, env=env).stdout
            for word in ('broll', '--matches', '--spot', '--face-on', '--descriptions'):
                self.assertNotIn(word, help_text, word)
            for word in ('deliver', 'replace-source', '--script', '--video'):
                self.assertIn(word, help_text, word)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class DirectScriptTests(unittest.TestCase):
    def test_config_paths_and_mac_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(importer.Path, 'home', return_value=Path(tmp)):
            defaults = importer.resolve_script_environment()
            self.assertTrue(defaults['PYTHONPATH'].endswith('/Scripting/Modules'))
            config = Path(tmp) / '.codex/config.toml'; config.parent.mkdir()
            config.write_text('[mcp_servers.davinci-resolve.env]\nPYTHONPATH="/modules:/other"\nRESOLVE_SCRIPT_API="/api"\nRESOLVE_SCRIPT_LIB="/lib.so"\n')
            self.assertEqual(importer.resolve_script_environment(), defaults)   # 2026-10-29：默认不读 Mac 上的 Codex 配置
            with patch.dict(os.environ, {'THINKCUT_MCP_CONFIG': str(config)}):
                self.assertEqual(importer.resolve_script_environment(), {'PYTHONPATH': '/modules:/other', 'RESOLVE_SCRIPT_API': '/api', 'RESOLVE_SCRIPT_LIB': '/lib.so'})
                config.write_text('[mcp_servers.davinci-resolve.env]\nRESOLVE_SCRIPT_API="/api"\n')
                self.assertEqual(importer.resolve_script_environment()['PYTHONPATH'], '/api/Modules')

    def test_connection_and_current_project_timeline_guards(self):
        timeline = types.SimpleNamespace(GetName=lambda: 'final')
        project = types.SimpleNamespace(GetName=lambda: 'project', GetCurrentTimeline=lambda: timeline)
        manager = types.SimpleNamespace(GetCurrentProject=lambda: project)
        resolve = types.SimpleNamespace(GetProjectManager=lambda: manager)
        module = types.SimpleNamespace(scriptapp=lambda name: resolve)
        env = {'PYTHONPATH': '/fake/modules', 'RESOLVE_SCRIPT_API': '/fake/api', 'RESOLVE_SCRIPT_LIB': '/fake/lib'}
        with patch.dict(sys.modules, {'DaVinciResolveScript': module}), patch.dict(os.environ), patch.object(sys, 'path', sys.path.copy()), \
             patch.object(importer, 'resolve_script_environment', return_value=env):
            self.assertIs(importer.direct_timeline('project', 'final'), timeline)
            self.assertEqual(os.environ['RESOLVE_SCRIPT_LIB'], '/fake/lib')
            self.assertIn('/fake/modules', sys.path)
            with self.assertRaisesRegex(ValueError, '当前工程不匹配'): importer.direct_timeline('wrong', 'final')
            with self.assertRaisesRegex(ValueError, '当前时间线不匹配'): importer.direct_timeline('project', 'wrong')
            module.scriptapp = lambda name: None
            with self.assertRaisesRegex(RuntimeError, '无法直接连接达芬奇脚本接口'): importer.direct_timeline('project', 'final')
            def unavailable(name): raise ImportError('unavailable')
            module.scriptapp = unavailable
            with self.assertRaisesRegex(RuntimeError, '无法直接连接达芬奇脚本接口'): importer.direct_timeline('project', 'final')


class ImportPreflightTests(unittest.TestCase):
    def test_import_preflight_stops_before_mcp_or_mutation(self):
        for error in (RuntimeError('无法直接连接达芬奇脚本接口'), ValueError('达芬奇当前工程不匹配')):
            with self.subTest(error=error), patch.object(importer, 'direct_timeline', side_effect=error) as direct, patch.object(importer, 'resolve_mcp_runtime') as runtime:
                with self.assertRaises(type(error)):
                    asyncio.run(importer.import_package(Path('/fake'), {'project': 'project'}, {}, 'attempt'))
                direct.assert_called_once_with('project', need_timeline=False); runtime.assert_not_called()

    def test_empty_project_without_timeline_passes_preflight_with_accurate_reason(self):
        class Reached(Exception): pass
        project = types.SimpleNamespace(GetName=lambda: 'project', GetCurrentTimeline=lambda: None)   # 新建的空工程：没有时间线
        manager = types.SimpleNamespace(GetCurrentProject=lambda: project)
        module = types.SimpleNamespace(scriptapp=lambda name: types.SimpleNamespace(GetProjectManager=lambda: manager))
        mcp = types.ModuleType('mcp'); mcp.ClientSession = mcp.StdioServerParameters = None
        client = types.ModuleType('mcp.client'); stdio = types.ModuleType('mcp.client.stdio'); stdio.stdio_client = None
        env = {'PYTHONPATH': '/fake/modules', 'RESOLVE_SCRIPT_API': '/fake/api', 'RESOLVE_SCRIPT_LIB': '/fake/lib'}
        with patch.dict(sys.modules, {'DaVinciResolveScript': module, 'mcp': mcp, 'mcp.client': client, 'mcp.client.stdio': stdio}), patch.dict(os.environ), \
             patch.object(sys, 'path', sys.path.copy()), patch.object(importer, 'resolve_script_environment', return_value=env):
            self.assertIsNone(importer.direct_timeline('project', need_timeline=False))
            # 需要时间线的地方：说准原因，不再说成“连不上达芬奇”
            with self.assertRaisesRegex(RuntimeError, '已连上.*没有当前时间线.*双击打开一条时间线') as caught: importer.direct_timeline('project', 'final')
            self.assertNotIn('无法直接连接', str(caught.exception))
            with self.assertRaisesRegex(ValueError, '当前工程不匹配'): importer.direct_timeline('other', need_timeline=False)
            manager.GetCurrentProject = lambda: None
            with self.assertRaisesRegex(RuntimeError, '没有打开任何工程'): importer.direct_timeline('project', need_timeline=False)
            manager.GetCurrentProject = lambda: project
            # 交付入口不替换 direct_timeline：空工程能走过预检、进到后面的建线流程
            with patch.object(importer, 'expected_subtitle_spec', return_value={}), \
                 patch.object(importer, 'resolve_mcp_runtime', side_effect=Reached), self.assertRaises(Reached):
                asyncio.run(importer.import_package(Path('/fake'), {'project': 'project', 'source': {'path': '/v.mov'}}, {'segments': []}, 'attempt'))


class MainLineTests(unittest.TestCase):
    """以前交付过的主线被手工改过，就不能悄悄用新主线顶替它。"""
    def edited(self, readback, manifest=None):
        session = FakeSession([MAIN], [{'name': MAIN, 'id': 't'}])
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp); (package / 'trial_keep_segments.json').write_text(json.dumps({'segments': SEGMENTS}))
            manifest = manifest or {'source': {'media_pool_item_id': MEDIA, 'path': SOURCE}}
            real = importer.call
            async def fake(session, tool, action, **params):
                if action == 'probe_timeline_structure': return readback
                return await real(session, tool, action, **params)
            with patch.object(importer, 'package_for', return_value=(package, manifest)), patch.object(importer, 'expected_subtitle_spec', return_value=SPEC), \
                 patch.object(importer, 'call', side_effect=fake):
                return asyncio.run(importer.edited_main_lines(session, None))

    def test_untouched_main_line_matches_its_frozen_package(self):
        self.assertEqual(self.edited(probe()), [])

    def test_anything_added_above_the_main_line_counts_as_hand_edited(self):
        for kind in ('video', 'audio'):
            readback = probe(); readback['tracks'][kind]['tracks'].append({'items': [{'start': 108010, 'end': 108030, 'duration': 20, 'file_path': '/clips/b.mov', 'media_status': 'Online'}]})
            with self.subTest(kind=kind):
                self.assertEqual(self.edited(readback), [MAIN])
                with self.assertRaisesRegex(ValueError, 'exactly one'): importer.validate_items(readback, SEGMENTS, SPEC, MEDIA, Path(SOURCE))
        readback = probe(); readback['tracks']['video']['tracks'].append({'items': []})        # 空轨不算改动
        self.assertEqual(self.edited(readback), [])

    def test_unreadable_frozen_main_line_is_treated_as_hand_edited(self):
        for location in ('manifest', 'mapping'):
            with tempfile.TemporaryDirectory() as tmp:
                session = FakeSession([MAIN], [{'name': MAIN, 'id': 't'}]); package = Path(tmp)
                context = patch.object(importer, 'package_for', side_effect=OSError('unreadable')) if location == 'manifest' else patch.object(importer, 'package_for', return_value=(package, {'source': {}}))
                with context: self.assertEqual(asyncio.run(importer.edited_main_lines(session, None)), [MAIN])

    def test_non_timeline_named_final_in_subfolder_is_not_moved(self):
        session = FakeSession([], [], ['历史版本'], [{'id': 'video', 'name': 'final'}])
        asyncio.run(importer.archive_old_timelines(session, 'final'))
        self.assertFalse(any(a == 'move_clips' for _, a, _ in session.calls))
        session = FakeSession(['final'], [], ['历史版本'], [{'id': 'timeline', 'name': 'final'}, {'id': 'other', 'name': 'other'}])
        asyncio.run(importer.archive_old_timelines(session, 'final'))
        moves = [p for _, a, p in session.calls if a == 'move_clips']
        self.assertEqual(moves, [{'clip_ids': ['timeline'], 'target_path': 'Master'}])


def main_probe():
    result = probe()
    for i, item in enumerate(result['tracks']['audio']['tracks'][0]['items']): item['timeline_item_id'] = str(i)
    result['id'] = 'timeline'
    return result


class FlowSession(FakeSession):
    """整条导入流程用的假 MCP 会话：不启动任何进程。"""
    def __init__(self, existing=False, extra=False):
        super().__init__([], []); self.extra = extra
        if existing: self.final = True
    async def initialize(self): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def call_tool(self, tool, args):
        action, p = args['action'], args['params']
        created = hasattr(self, 'final')
        payload = {'success': True}
        if action == 'get_current': payload = {'name': 'project'}
        elif action == 'is_rendering': payload = {'rendering': False}
        elif action == 'get_clip_property': payload = {'properties': SOURCE}
        elif action == 'list': payload = {'timelines': ([{'name': 'base'}, {'name': 'final'}] + ([{'name': 'final_archived_v01'}] if self.extra else [])) if created else []}
        elif action == 'get_clips': payload = {'clips': [{'name': 'final', 'id': 'final'}] if created and p['path'] == 'Master' else []}
        elif action == 'get_subfolders': payload = {'subfolders': []}
        elif action == 'probe_timeline_structure': payload = main_probe()
        elif action == 'import_timeline_checked': self.final = True; payload = {'name': 'final', 'media': {'offline': 0}}
        elif action == 'get_left_offset': payload = {'offset': SEGMENTS[p['item_index']]['src_in']}
        elif action == 'get_linked_items': payload = {'items': [{'id': str(p['item_index'])}]}
        elif action == 'get_setting': payload = {'settings': {'timelineResolutionWidth': '3840', 'timelineResolutionHeight': '2160', 'timelineFrameRate': '29.97'}[p['name']]}
        elif action == 'get_track_enabled': payload = {'enabled': True}
        self.calls.append((tool, action, p))
        text = type('Text', (), {'type': 'text', 'text': json.dumps(payload)})
        return type('Result', (), {'isError': False, 'content': [text]})


class ImportFlowTests(unittest.TestCase):
    main_format = None

    def test_new_main_line_is_built_imported_reopened_and_read_back(self):
        self.run_flow()

    def test_new_main_line_reads_back_the_previous_main_line_format(self):
        # 2026-11-27 261114 新4：沿用上一版主线的画幅，导入后的主线读回核对（假达芬奇读回 3840×2160）
        self.main_format = {'from': 'v1_主线_1110-0900_aaaa', 'width': 3840, 'height': 2160, 'scaling': 'scaleToCrop'}
        self.run_flow()
        self.main_format = {'from': 'v1_主线_1110-0900_aaaa', 'width': 2160, 'height': 2880, 'scaling': 'scaleToCrop'}
        with self.assertRaisesRegex(ValueError, '和上一版主线.*2160×2880 不一样'):
            self.run_flow()

    def test_reuse_branch_only_reopens_and_reads_back(self):
        self.run_flow(existing=True)

    def test_unexpected_new_archive_timeline_fails_delivery(self):
        with self.assertRaisesRegex(ValueError, '达芬奇里多出了时间线：final_archived_v01'):
            self.run_flow(extra=True)

    def run_flow(self, existing=False, extra=False):
        session = FlowSession(existing, extra)
        @asynccontextmanager
        async def stdio(_): yield None, None
        mcp = types.ModuleType('mcp'); mcp.ClientSession = lambda *a, **kw: session; mcp.StdioServerParameters = lambda **kw: kw
        client = types.ModuleType('mcp.client'); stdio_module = types.ModuleType('mcp.client.stdio'); stdio_module.stdio_client = stdio
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp); (package / 'delivery_manifest.json').write_text('{}')
            manifest = {'source': {'path': SOURCE, 'media_pool_item_id': MEDIA}, 'project': 'project', 'timeline_name': 'final', 'base_timeline_name': 'base',
                        'srt_name': 'final.srt', 'fps': [30000, 1001], 'frames': 60, 'signature': 'abc'}
            (package / 'final.srt').write_text('srt'); (package / 'subtitle_cues.json').write_text('{}')
            async def export(session, path, *args, **kw): path.write_text('export')
            def author(args, **kw):
                (package / 'subtitle_drt_spec.json').write_text(json.dumps(SPEC))
                (package / args[4]).write_text('drt')
            with patch.dict(sys.modules, {'mcp': mcp, 'mcp.client': client, 'mcp.client.stdio': stdio_module}), \
                 patch.object(importer, 'resolve_mcp_runtime', return_value=(package, package / 'python', package / 'server')), \
                 patch.object(importer, 'node_runtime', return_value=('node', {})), \
                 patch.object(importer, 'expected_subtitle_spec', return_value=SPEC), \
                 patch.object(importer, 'export_atomically', side_effect=export), \
                 patch.object(importer.subprocess, 'run', side_effect=author), \
                 patch.object(importer, 'archive_old_timelines', return_value=0), \
                 patch.object(importer, 'direct_timeline', return_value=None) as direct, \
                 patch.object(importer, 'verify_package'), patch.object(importer, 'CONFIG', {}), \
                 patch.object(importer, 'match_previous_format', return_value=self.main_format), \
                 patch.object(importer, 'ever_passed', return_value=existing):
                asyncio.run(importer.import_package(package, manifest, {'segments': SEGMENTS}, 'attempt'))
            direct.assert_called_once_with('project', need_timeline=False)
            actions = [a for _, a, _ in session.calls]
            master = next(i for i, (_, a, p) in enumerate(session.calls) if a == 'set_current_folder' and p['path'] == 'Master')
            if existing:
                for action in ('create_timeline_from_clips', 'import_timeline_checked'): self.assertNotIn(action, actions)
            else:
                self.assertLess(master, actions.index('create_timeline_from_clips'))
                self.assertLess(actions.index('create_timeline_from_clips'), actions.index('import_timeline_checked'))
                self.assertLess(actions.index('import_timeline_checked'), actions.index('close'))
            self.assertLess(actions.index('close'), actions.index('load'))
            # 口播交付只碰第 1 轨：不加轨、不往时间线上追加别的片段、不建 B-roll 素材夹
            for action in ('append_to_timeline', 'safe_import_media', 'add_subfolder', 'add_track'): self.assertNotIn(action, actions)
            self.assertEqual([(a, p) for _, a, p in session.calls if p.get('track_index', 1) != 1 or p.get('index', 1) != 1], [])
            readback = json.loads((package / 'mcp_readback.json').read_text())
            self.assertTrue(readback['passed']); self.assertEqual((readback['video_clips'], readback['audio_clips'], readback['subtitle_cues']), (2, 2, 2))
            self.assertFalse([k for k in readback if 'broll' in k])


if __name__ == '__main__':
    unittest.main()
