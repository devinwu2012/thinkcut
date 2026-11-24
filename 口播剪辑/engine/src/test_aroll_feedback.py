"""A-roll directory, summary and documentation regression; no project or Resolve access."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
with patch.object(sys,'path',[str(ROOT/'bin'),*sys.path]):
    spec=importlib.util.spec_from_file_location('aroll_edit_project',ROOT/'bin/edit_project.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)


class FeedbackTests(unittest.TestCase):
    def test_project_directory_choices_and_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();raw=root/'01_原始素材/A roll';raw.mkdir(parents=True);(root/'项目.json').write_text('{}')
            video=raw/'片子.mp4'
            self.assertEqual(cli.default_project_dir(video),root/'02_制作/片子_AI剪辑')
            legacy=raw/'片子_AI剪辑';legacy.mkdir()
            self.assertEqual(cli.default_project_dir(video),legacy)
        with tempfile.TemporaryDirectory() as tmp:
            video=Path(tmp).resolve()/'片子.mp4'
            self.assertEqual(cli.default_project_dir(video),video.parent/'片子_AI剪辑')

    def test_explicit_project_dir_wins_and_other_actions_require_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp).resolve()/'manual';args=['edit_project','prepare','--video',str(Path(tmp)/'video.mp4'),'--project-dir',str(folder)]
            with patch.object(sys,'argv',args),patch.object(cli,'prepare') as prepare:
                cli.main();self.assertEqual(prepare.call_args.args[0],folder)
            with patch.object(sys,'argv',['edit_project','status']),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):cli.main()

    def test_prepare_can_choose_new_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            video=Path(tmp).resolve()/'video.mp4'
            with patch.object(sys,'argv',['edit_project','prepare','--video',str(video)]),patch.object(cli,'prepare') as prepare:
                cli.main();self.assertEqual(prepare.call_args.args[0],video.parent/'video_AI剪辑')

    def test_uncertain_repeat_is_in_finish_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);run=folder/'审核';run.mkdir()
            (run/'review_decisions.json').write_text('{}')
            (run/'review_v2_state.json').write_text(json.dumps({'repeat_edits':{'candidates':[{'status':'review','before_text':'户外户外拍摄','reason':'待语义复核'}]}}))
            (run/'review_v2_full.json').write_text('{"seconds":10}')
            (run/'文案.txt').write_text('户外拍摄',encoding='utf-8')   # 2026-11-20 Q131：没有文案时 finish 会停下，这里只测摘要
            args=types.SimpleNamespace(script=None,no_serve=True,align_python=None)
            out=io.StringIO()
            with patch.object(cli,'wait_for_alignment'),patch.object(cli,'run_self'),contextlib.redirect_stdout(out):
                cli.finish(folder,run,args)
            self.assertIn('重复词已保留，需 AI 语义复核：户外户外拍摄',out.getvalue())

    def test_skill_reminders(self):
        skill=(ROOT/'skills/a-roll/SKILL.md').read_text()
        workflow=(ROOT/'skills/a-roll/references/editing-workflow.md').read_text()
        self.assertIn('"ids": ["u3c1", "u3c2"]',skill)
        for phrase in ('不套 LUT','01_原始素材','先向用户确认录制顺序，再逐条转写','已有识别结果校验后复用'):
            self.assertIn(phrase,workflow)
        self.assertIn('02_制作/<片名>_AI剪辑/',skill)
        self.assertNotIn('3.7AI素材',skill)


if __name__=='__main__':unittest.main()
