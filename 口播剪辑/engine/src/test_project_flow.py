"""End-to-end offline run of the real project path: review -> script check -> correction -> delivery package (SRT).

Uses a tiny fake project with AI_EDIT_RUN_DIR set, exactly like bin/edit_project.py; never calls ASR or Resolve.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
FPS = 30000 / 1001


def word(text, start, end):
    return {'text': text, 'start_time': start, 'end_time': end}


@unittest.skipUnless(shutil.which('node'), 'node required')
class ProjectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name) / '片子_AI剪辑'
        self.run_dir = folder / '审核'
        self.run_dir.mkdir(parents=True)
        (folder / '转写').mkdir()
        source = Path(self.tmp.name).resolve() / '片子.MP4'
        source.write_bytes(b'not a real video')
        stat = source.stat()
        (folder / '转写/source_manifest.json').write_text(json.dumps(
            {'path': str(source), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns}))
        # Sentence 0 is a retake of sentence 1; sentence 1 holds an ASR homophone (支出 -> 直出).
        utterances = [
            {'text': '我们今天', 'start_time': 0, 'end_time': 900,
             'words': [word('我', 0, 200), word('们', 200, 400), word('今', 500, 700), word('天', 700, 900)]},
            {'text': '我们今天聊支出', 'start_time': 2000, 'end_time': 4000,
             'words': [word('我', 2000, 2200), word('们', 2200, 2400), word('今', 2600, 2800), word('天', 2800, 3000),
                       word('聊', 3100, 3300), word('支', 3500, 3700), word('出', 3700, 3900)]},
        ]
        (folder / '转写/doubao_turbo.json').write_text(json.dumps({'result': {'utterances': utterances}}, ensure_ascii=False))
        units, tokens = [], []
        for i, u in enumerate(utterances):
            units.append({'id': i, 'text': u['text'], 'context_text': u['text'], 'start_ms': u['start_time'],
                          'end_ms': u['end_time'], 'project_generic': True})
            for j, w in enumerate(u['words']):
                tokens.append({'id': f'u{i}c{j}', 'unit_id': i, 'char_start': j, 'char_end': j + 1, 'text': w['text'],
                               'start_ms': w['start_time'], 'end_ms': w['end_time'], 'ready': True})
        frames = round(5 * FPS)
        files = {
            'project_runtime.json': {'name': 'Clip', 'source': str(source), 'source_frames': frames, 'fps': [30000, 1001],
                                     'resolution': [1920, 1080], 'audio_stream': 0, 'media_pool_item_id': 'media-1',
                                     'project': '_mcp_Clip', 'sections': [{'start': 0, 'title': '口播全文'}]},
            'sentence_units.json': units,
            'character_alignment.json': {'alignment_id': 'fixture', 'tokens': tokens, 'issues': []},
            'review_decisions.json': {'reviewer': 'test', 'limitations': [], 'cuts': [
                {'unit_id': 0, 'char_start': 0, 'char_end': 4, 'text': '我们今天', 'reason': '重录', 'replacement_ids': [1]}]},
            'delivery_approved_v1/approval_manifest.json': {'source': str(source), 'source_size': stat.st_size,
                                                            'source_mtime_ns': stat.st_mtime_ns},
        }
        for name, value in files.items():
            (self.run_dir / name).parent.mkdir(exist_ok=True)
            (self.run_dir / name).write_text(json.dumps(value, ensure_ascii=False))
        (self.run_dir / 'glossary.txt').write_text('')
        self.env = {**os.environ, 'AI_EDIT_RUN_DIR': str(self.run_dir), 'PYTHONDONTWRITEBYTECODE': '1'}

    def step(self, script, *args):
        result = subprocess.run([sys.executable, str(HERE / script), *map(str, args)], env=self.env,
                                cwd=HERE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        return result.stdout

    def read(self, name):
        return json.loads((self.run_dir / name).read_text())

    def test_review_script_correction_and_delivery_package(self):
        self.step('prepare_new_review.py')
        state = self.read('review_v2_state.json')
        self.assertEqual(state['natural_pause_mode'], 'sentence')
        kept = self.read('trial_keep_segments.json')
        self.assertEqual(kept['retained_token_ids'], [f'u1c{j}' for j in range(7)])
        self.assertEqual(len(kept['segments']), 1)  # the kept sentence stays one continuous piece

        script = Path(self.tmp.name) / '文案.txt'
        script.write_text('座位离舞台特别远。我们今天聊直出。', encoding='utf-8')
        self.step('script_check.py', '--script', script)
        report = self.read('文案对照.json')
        self.assertEqual([(c['token_ids'], c['script']) for c in report['candidates']], [(['u1c5'], '直')])
        self.assertEqual(report['possibly_missing'], ['座位离舞台特别远'])

        fix = Path(self.tmp.name) / 'fix.json'
        fix.write_text(json.dumps([{'id': 'u1c5', 'before': '支', 'after': '直', 'reason': '同音'}], ensure_ascii=False))
        self.step('script_check.py', '--corrections', fix)
        self.assertEqual(self.read('review_v2_state.json')['subtitle_token_corrections'][0]['after'], '直')

        self.step('script_check.py', '--sheet')
        sheet = self.read('字幕校对.json')
        self.assertTrue(sheet['script_used'])
        self.assertEqual(sheet['sentences'][0]['subtitle'], '我们今天聊直出')
        blocked = subprocess.run([sys.executable, str(HERE / 'prepare_mvp_delivery.py')], env=self.env, cwd=HERE,
                                 capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn('字幕还没校对', blocked.stderr)
        self.step('script_check.py', '--done')
        self.assertEqual(self.read('review_v2_state.json')['subtitle_proofread']['corrections'], 1)

        out = self.step('prepare_mvp_delivery.py')
        package = Path(json.loads(out.strip().splitlines()[-1])['package'])
        manifest = json.loads((package / 'delivery_manifest.json').read_text())
        self.assertEqual(manifest['version'], 'mvp-v3')
        self.assertRegex(manifest['timeline_name'], r'^Clip_主线_\d{4}-\d{4}_[0-9a-f]{4}$')
        srt = (package / manifest['srt_name']).read_text()
        self.assertIn('我们今天聊直出', srt)
        self.assertNotIn('支', srt)

        # A frozen package that was changed afterwards must never be reused.
        (package / manifest['srt_name']).write_text(srt.replace('直出', '支出'))
        tampered = subprocess.run([sys.executable, str(HERE / 'prepare_mvp_delivery.py')], env=self.env, cwd=HERE,
                                  capture_output=True, text=True)
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn('Frozen delivery file changed', tampered.stderr)

    def test_old_broll_plan_in_review_state_stops_delivery_with_reason(self):
        """旧版 B-roll 2026-11-21 下线：审核状态里还带着旧计划的片子，停下说清楚，不悄悄出一条没有 B-roll 的主线。"""
        self.step('prepare_new_review.py'); self.step('script_check.py', '--done')
        plain = Path(json.loads(self.step('prepare_mvp_delivery.py').strip().splitlines()[-1])['package'])
        self.assertFalse((plain / 'broll_timeline.json').exists())
        self.assertNotIn('broll_items', json.loads((plain / 'delivery_manifest.json').read_text()))
        self.assertTrue(json.loads(self.step('prepare_mvp_delivery.py').strip().splitlines()[-1])['reused'])   # 没有旧记录：照常复用
        state = self.read('review_v2_state.json')
        state['broll'] = {'version': 1, 'dir': str(Path(self.tmp.name) / 'broll'), 'spots': [{'id': 'spot', 'status': 'confirmed'}]}
        (self.run_dir / 'review_v2_state.json').write_text(json.dumps(state))
        packages = sorted(p.name for p in (self.run_dir / 'mvp_deliveries').iterdir())
        result = subprocess.run([sys.executable, str(HERE / 'prepare_mvp_delivery.py')], env=self.env, cwd=HERE, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('旧版 B-roll', result.stderr); self.assertIn('已于 2026-11-21 下线', result.stderr); self.assertIn('review_v2_state.json', result.stderr)
        self.assertEqual(sorted(p.name for p in (self.run_dir / 'mvp_deliveries').iterdir()), packages)   # 没有多出交付包
        self.assertFalse((self.run_dir.parent / 'B-roll片段').exists())

    def reopen(self):
        return subprocess.run([sys.executable, str(HERE.parents[1] / 'bin' / 'edit_project.py'), 'reopen', '--project-dir', str(self.run_dir.parent)],
                              env=self.env, cwd=HERE, capture_output=True, text=True)

    def test_reopen_regenerates_review_from_fixed_decisions(self):
        """2026-11-22 261122 反馈单 新12：审核生成后发现删错了字，以前只能另开 _第2次 目录；现在 reopen 回退（不删，挪进备份）再按改好的选择重新生成。"""
        self.step('prepare_new_review.py'); self.step('script_check.py', '--done')
        out = self.step('prepare_mvp_delivery.py')                           # 已经存过一版主线
        package = Path(json.loads(out.strip().splitlines()[-1])['package'])
        first_alignment = (self.run_dir / 'character_alignment.json').read_bytes()
        decisions = self.read('review_decisions.json')
        decisions['cuts'][0].update(char_start=2, text='今天', reason='说错重说')
        (self.run_dir / 'review_decisions.json').write_text(json.dumps(decisions, ensure_ascii=False))
        again = subprocess.run([sys.executable, str(HERE / 'prepare_new_review.py')], env=self.env, cwd=HERE, capture_output=True, text=True)
        self.assertIn('Review already exists', again.stderr); self.assertIn('edit_project.py reopen', again.stderr)   # 报错里指路
        # 用户在页面上动过：不回退，指路到审片台口播剪辑栏
        state = self.read('review_v2_state.json')
        (self.run_dir / 'review_v2_state.json').write_text(json.dumps({**state, 'overrides': {'semantic_0': 'skip'}}))
        result = self.reopen()
        self.assertNotEqual(result.returncode, 0); self.assertIn('改过删改卡片的取舍', result.stderr); self.assertIn('审片台口播剪辑栏', result.stderr)
        self.assertTrue((self.run_dir / 'review_v2.json').exists())
        (self.run_dir / 'review_v2_state.json').write_text(json.dumps(state))
        result = self.reopen()
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn('已回退', result.stdout); self.assertIn('主线不动', result.stdout)
        kept = [p for p in (self.run_dir / 'review_state_backups').iterdir() if p.name.startswith('回退前_')]
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / 'review_v2_state.json').exists() and (kept[0] / 'review_v2.json').exists())
        self.assertEqual((kept[0] / 'character_alignment.json').read_bytes(), first_alignment)
        self.assertFalse((self.run_dir / 'review_v2_state.json').exists())
        self.assertEqual((self.run_dir / 'character_alignment.json').read_bytes(), (self.run_dir / 'alignment_qwen_full.json').read_bytes())   # 字时间换回对齐原样
        self.assertTrue(package.is_dir())                                    # 存过的交付包（旧主线）不动
        self.step('prepare_new_review.py')                                   # 按改好的选择重新生成
        self.assertEqual(self.read('review_v2.json')['cards'][0]['removed_text'], ['今天'])
        self.assertEqual(self.read('character_alignment.json')['alignment_id'], json.loads(first_alignment)['alignment_id'])   # 字编号和以前一样


if __name__ == '__main__':
    unittest.main()
