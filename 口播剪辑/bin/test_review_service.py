import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
import edit_project


class ReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name)

    def write(self, **info):
        (self.run_dir / '审核网址.txt').write_text(json.dumps(info))

    def test_no_file_or_dead_process_means_not_running(self):
        self.assertIsNone(edit_project.service_info(self.run_dir))
        self.write(url='u', pid=999999, host=socket.gethostname())
        self.assertIsNone(edit_project.service_info(self.run_dir))

    def test_unrelated_live_process_is_not_mistaken_for_the_service(self):
        self.write(url='u', pid=os.getpid(), host=socket.gethostname())
        self.assertIsNone(edit_project.service_info(self.run_dir))

    def test_service_on_the_other_mac_is_reported(self):
        self.write(url='u', pid=1, host='另一台Mac')
        self.assertTrue(edit_project.service_info(self.run_dir)['other_host'])

    def test_stale_records_can_be_cleared(self):
        # 2026-11-13 审查：残留记录不再把项目锁死
        record = self.run_dir / '审核网址.txt'
        self.write(url='u', pid=999999, host=socket.gethostname())
        edit_project.stop(self.run_dir); self.assertFalse(record.exists())          # 本机进程已死：stop 清掉
        self.write(url='u', pid=1, host='另一台Mac')
        with self.assertRaises(SystemExit) as e: edit_project.stop(self.run_dir)
        self.assertIn('--force', str(e.exception)); self.assertTrue(record.exists())   # 另一台 Mac：默认不动，提示怎么办
        edit_project.stop(self.run_dir, force=True); self.assertFalse(record.exists())

    def test_finished_alignment_from_other_mac_is_not_running(self):
        (self.run_dir / '对齐进程.txt').write_text(json.dumps({'pid': 1, 'host': '另一台Mac'}))
        with self.assertRaises(SystemExit): edit_project.alignment_running(self.run_dir)
        (self.run_dir / 'character_alignment.json').write_text('{}')
        self.assertFalse(edit_project.alignment_running(self.run_dir))

    def test_free_port_skips_a_port_in_use(self):
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0)); busy.listen()
            port = busy.getsockname()[1]
            self.assertNotEqual(edit_project.free_port(port), port)


class ServeOpensShenpiantaiTests(unittest.TestCase):
    """2026-11-22 用户定：剪口播的审核网页并进审片台“口播剪辑”栏。serve（和 finish 末尾自动起的那次）打开的是审片台，停在“口播剪辑”栏。"""
    def setUp(self):
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); root = Path(self.tmp.name)
        self.folder = root / '片子_AI剪辑'; self.run_dir = self.folder / '审核'; self.run_dir.mkdir(parents=True)
        self.skill = root / 'thinkcut-edit'; (self.skill / 'scripts').mkdir(parents=True); self.log = root / '审片台被叫.json'
        (self.skill / 'scripts' / 'thinkcut_edit.py').write_text(
            'import json, os, sys\n'
            f'open({str(self.log)!r}, "w").write(json.dumps({{"argv": sys.argv[1:], "no_open": os.environ.get("THINKCUT_NO_OPEN"), "run_dir": os.environ.get("AI_EDIT_RUN_DIR")}}))\n'
            'print("审片台已启动：http://127.0.0.1:8765/#文案")\n')
        for target, value in (('edit_project.serve_backend', lambda run, env, args: {'url': 'http://127.0.0.1:18443/', 'version': 'v'}),):
            p = patch(target, side_effect=value); p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'THINKCUT_SKILL': str(self.skill)}); p.start(); self.addCleanup(p.stop)

    def args(self, **kw):
        import argparse
        return argparse.Namespace(**{'backend': False, 'no_open': False, 'port': None, **kw})

    def test_serve_opens_the_shenpiantai_at_the_wenan_column(self):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            edit_project.serve(self.run_dir, {'AI_EDIT_RUN_DIR': str(self.run_dir)}, self.args())
        got = json.loads(self.log.read_text())
        self.assertEqual(got['argv'], ['workbench', str(self.folder), '--column', '文案']); self.assertIsNone(got['no_open']); self.assertIsNone(got['run_dir'])
        with contextlib.redirect_stdout(io.StringIO()):
            edit_project.serve(self.run_dir, {}, self.args(no_open=True))
        self.assertEqual(json.loads(self.log.read_text())['no_open'], '1')

    def test_backend_only_does_not_open_anything(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out): edit_project.serve(self.run_dir, {}, self.args(backend=True))
        self.assertFalse(self.log.exists()); self.assertIn('剪口播后台：http://127.0.0.1:18443/', out.getvalue())

    def test_missing_shenpiantai_is_explained(self):
        os.environ['THINKCUT_SKILL'] = str(self.skill / '没有')
        with self.assertRaisesRegex(SystemExit, 'THINKCUT_SKILL'): edit_project.serve(self.run_dir, {}, self.args())

    def test_skill_is_found_next_to_this_repo_without_env(self):
        os.environ.pop('THINKCUT_SKILL')
        self.assertEqual(edit_project.thinkcut_skill(), edit_project.ROOT.parent / '口播自动剪辑' / 'skill' / 'thinkcut-edit')

    def test_finish_ends_by_serving_the_shenpiantai(self):
        import argparse, contextlib, io
        from unittest.mock import patch
        for name, value in (('review_decisions.json', {}), ('review_v2_state.json', {}), ('review_v2_full.json', {'seconds': 12.0})):
            (self.run_dir / name).write_text(json.dumps(value))
        calls = []
        args = argparse.Namespace(script=None, no_serve=False, no_open=True, align_python=None)
        with patch.object(edit_project, 'wait_for_alignment'), patch.object(edit_project, 'default_script', return_value=None), \
             patch.object(edit_project, 'run_self', side_effect=calls.append), contextlib.redirect_stdout(io.StringIO()):
            edit_project.finish(self.folder, self.run_dir, args)
        serve = [c for c in calls if c[2] == 'serve']
        self.assertEqual(len(serve), 1); self.assertIn('--no-open', serve[0])
        # finish 起的 serve 走的就是上面那个 serve：打开审片台“口播剪辑”栏
        with contextlib.redirect_stdout(io.StringIO()):
            edit_project.serve(self.run_dir, {}, self.args(no_open=True))
        self.assertEqual(json.loads(self.log.read_text())['argv'][-2:], ['--column', '文案'])


class CleanTests(unittest.TestCase):
    def test_only_regenerable_files_are_listed_and_delivery_packages_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / '审核'
            (run / 'mvp_deliveries' / 'Clip_old').mkdir(parents=True)
            (run / 'mvp_deliveries' / 'Clip_new').mkdir()
            (run / 'latest_delivery.json').write_text(json.dumps({'package': str(run / 'mvp_deliveries' / 'Clip_new')}))
            for name in ('audio_asr.wav', 'character_alignment.json', 'review_v2_state.json'):
                (run / name).write_text('x')
            (run / 'review_state_backups').mkdir()
            for i in range(22):
                (run / 'review_state_backups' / f'{i:02d}.json').write_text('x'); os.utime(run / 'review_state_backups' / f'{i:02d}.json', (i, i))
            # 剪辑目录旁的 B-roll片段/ 是旧版 B-roll（2026-11-21 下线）留下的：clean 不再认它，一个文件都不列、不删
            clips = run.parent/'B-roll片段'; clips.mkdir()
            for name in ('keep.mov', 'old.mov', '.clip-one.partial'): (clips/name).write_bytes(b'clip')
            (run/'mvp_deliveries/Clip_old/broll_timeline.json').write_text('{')
            listed = {p.relative_to(run).as_posix() for p, _, _ in edit_project.cleanable(run)}
            self.assertEqual({p.name for p in clips.iterdir()}, {'keep.mov', 'old.mov', '.clip-one.partial'})
            # delivery packages are never listed: they hold the pre-import backup and the hand-edit check data
            self.assertEqual(listed, {'audio_asr.wav', 'review_state_backups/00.json', 'review_state_backups/01.json'})

    def test_clean_with_yes_never_deletes_anything_outside_the_review_folder(self):
        from unittest.mock import patch
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)/'审核';run.mkdir();clips=run.parent/'B-roll片段';clips.mkdir()
            for name in ('keep.mov','old.mov','.clip-one.partial'): (clips/name).write_bytes(b'clip')
            (run/'audio_asr.wav').write_text('x');(run/'character_alignment.json').write_text('x')
            with patch.object(edit_project,'service_info',return_value=None),contextlib.redirect_stdout(io.StringIO()):
                edit_project.clean(run,False);self.assertTrue((run/'audio_asr.wav').exists())
                edit_project.clean(run,True)
            self.assertFalse((run/'audio_asr.wav').exists())
            self.assertEqual({p.name for p in clips.iterdir()},{'keep.mov','old.mov','.clip-one.partial'})


class CleanLockTests(unittest.TestCase):
    def test_clean_refuses_while_a_delivery_runs(self):
        import sys
        sys.path.insert(0, str(Path(edit_project.__file__).resolve().parents[1] / 'engine' / 'src'))
        from prepare_mvp_delivery import exclusive_lock
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'audio_asr.wav').write_text('x'); (run / 'character_alignment.json').write_text('{}')
            with exclusive_lock(run / '.delivering.lock'), self.assertRaises(SystemExit):
                edit_project.clean(run, True)
            self.assertTrue((run / 'audio_asr.wav').exists())


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name)

    def test_finish_needs_selections_first(self):
        with self.assertRaisesRegex(SystemExit, 'review_decisions'):
            edit_project.finish(self.run_dir.parent, self.run_dir, None)

    def test_failed_background_alignment_is_reported_in_chinese(self):
        (self.run_dir / 'align.log').write_text('Loading model\nRuntimeError: out of memory\n')
        with self.assertRaisesRegex(SystemExit, '声学对齐没有完成：RuntimeError: out of memory'):
            edit_project.wait_for_alignment(self.run_dir)

    def test_stuck_background_alignment_stops_waiting_with_next_step(self):
        from unittest.mock import patch
        (self.run_dir / '对齐进程.txt').write_text('{}')
        with patch.object(edit_project, 'alignment_running', return_value=True), patch.object(edit_project, 'ALIGN_WAIT_SECONDS', 0), \
             patch.object(edit_project.time, 'sleep') as sleep, self.assertRaises(SystemExit) as stopped:
            edit_project.wait_for_alignment(self.run_dir)
        message = str(stopped.exception)
        self.assertIn('还没完成', message); self.assertIn(str(self.run_dir / 'align.log'), message); self.assertIn('重新运行 align', message)
        sleep.assert_not_called()
        # 没有结束后台进程，也不清掉记录：再运行 finish 可以接着等
        self.assertTrue((self.run_dir / '对齐进程.txt').exists())

    def test_alignment_within_the_limit_still_waits_as_before(self):
        from unittest.mock import patch
        (self.run_dir / 'character_alignment.json').write_text('{}'); (self.run_dir / '对齐进程.txt').write_text('{}')
        with patch.object(edit_project, 'alignment_running', side_effect=[True, True, False]), patch.object(edit_project.time, 'sleep') as sleep:
            edit_project.wait_for_alignment(self.run_dir)
        self.assertEqual(sleep.call_count, 2); self.assertFalse((self.run_dir / '对齐进程.txt').exists())

    def test_align_without_ffmpeg_says_so_in_chinese(self):
        import sys
        from unittest.mock import patch
        project = self.run_dir / '片子_AI剪辑'; run = project / '审核'; run.mkdir(parents=True); (run / 'project_runtime.json').write_text('{}')
        with patch.object(sys, 'argv', ['edit_project', 'align', '--project-dir', str(project)]), patch.object(edit_project, 'verify_source'), \
             patch.object(edit_project, 'alignment_runtime', return_value={'alignment_dependencies_ready': True, 'alignment_python': sys.executable}), \
             patch.object(edit_project.shutil, 'which', return_value=None), patch.object(edit_project.subprocess, 'run') as run_process, \
             patch.object(edit_project, 'execute') as execute, self.assertRaisesRegex(SystemExit, '找不到 ffmpeg'):
            edit_project.main()
        run_process.assert_not_called(); execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
