# -*- coding: utf-8 -*-
"""Q131（2026-11-20）：finish/script 没传 --script、也还没有 审核/文案.txt 时，自动取项目文案里的“口播正文”对稿；
取不到就停下说清楚。只用临时目录里的假项目，不识别、不连达芬奇。"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import edit_project

# 结构照 261117 的真实文案（标题与封面 + 口播正文 + ### 小标题），内容是编的
DOC = ('# 261117 某某｜口播文案\n\n## 标题与封面\n\n**标题**：装灯前先到现场试一遍\n\n**封面文字**：\n\n7米挑空\n\n灯装哪？\n\n'
       '## 口播正文\n\n　　做灯光，我们从来不出模拟图。\n\n### 结尾\n\n　　我是设计师小王，我们下期见。\n')


class DefaultScriptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / '261117 某某'
        (self.root / '01_原始素材/A roll').mkdir(parents=True)
        (self.root / '项目.json').write_text(json.dumps({'name': self.root.name}, ensure_ascii=False))
        self.doc = self.root / '02_制作' / (self.root.name + '.md')
        self.folder = self.root / '02_制作' / '口播_AI剪辑'; self.run = self.folder / '审核'; self.run.mkdir(parents=True)
        for name, value in (('review_decisions.json', {'cuts': []}), ('review_v2_state.json', {}), ('review_v2_full.json', {'seconds': 3.0})):
            (self.run / name).write_text(json.dumps(value))
        self.args = SimpleNamespace(script=None, no_serve=True, no_open=True, align_python=None)

    def finish(self):
        calls = []
        with patch.object(edit_project, 'wait_for_alignment'), patch.object(edit_project, 'run_self', side_effect=calls.append), \
                contextlib.redirect_stdout(io.StringIO()):
            edit_project.finish(self.folder, self.run, self.args)
        return [c[2] for c in calls]          # 每条子命令的动作名

    def test_finish_without_script_uses_spoken_part_of_project_doc(self):
        self.doc.write_text(DOC, encoding='utf-8')
        actions = self.finish()
        self.assertIn('script', actions)
        text = (self.run / '文案.txt').read_text(encoding='utf-8')
        self.assertEqual(text, '做灯光，我们从来不出模拟图。\n我是设计师小王，我们下期见。\n')
        for word in ('标题', '封面', '7米挑空', '结尾', '#', '　'): self.assertNotIn(word, text)

    def test_doc_in_finished_folder_is_used_first(self):
        # 2026-11-22 用户定：定稿文案放 03_成片/<项目名>.md；老项目还在 02_制作 的照读，两处都有以 03_成片 为准
        new = self.root / '03_成片' / (self.root.name + '.md'); new.parent.mkdir(parents=True)
        new.write_text(DOC, encoding='utf-8'); self.finish()
        self.assertEqual((self.run / '文案.txt').read_text(encoding='utf-8'), '做灯光，我们从来不出模拟图。\n我是设计师小王，我们下期见。\n')
        (self.run / '文案.txt').unlink(); self.doc.write_text('# 某某\n\n## 口播正文\n\n这是 02_制作 里的旧稿。\n', encoding='utf-8')
        self.finish(); self.assertNotIn('旧稿', (self.run / '文案.txt').read_text(encoding='utf-8'))
        self.assertEqual(edit_project.project_script_doc(self.folder, self.run), new)
        new.unlink(); self.assertEqual(edit_project.project_script_doc(self.folder, self.run), self.doc)

    def test_finish_stops_when_doc_has_no_spoken_part(self):
        self.doc.write_text('# 某某\n\n## 标题与封面\n\n**标题**：只有标题\n\n## 拍摄提纲\n\n先拍厨房。\n', encoding='utf-8')
        with self.assertRaises(SystemExit) as e: self.finish()
        self.assertIn('## 口播正文', str(e.exception)); self.assertIn('--script', str(e.exception))
        self.assertFalse((self.run / '文案.txt').exists())

    def test_finish_stops_when_doc_missing_or_not_a_thinkcut_project(self):
        with self.assertRaisesRegex(SystemExit, '缺项目文案'): self.finish()
        (self.root / '项目.json').unlink()
        with self.assertRaisesRegex(SystemExit, '不在标准项目里'): self.finish()

    def test_script_action_alone_also_stops_without_spoken_part(self):
        import os, subprocess, sys
        self.doc.write_text('# 某某\n\n## 标题\n\n只有标题\n', encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k != 'AI_EDIT_RUN_DIR'}
        out = subprocess.run([sys.executable, '-B', edit_project.__file__, 'script', '--project-dir', str(self.folder)],
                             capture_output=True, text=True, env={**env, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(out.returncode, 1); self.assertIn('## 口播正文', out.stderr)
        self.assertFalse((self.run / '文案.txt').exists())

    def test_existing_script_file_is_kept(self):
        self.doc.write_text(DOC, encoding='utf-8')
        (self.run / '文案.txt').write_text('早先确认的文案', encoding='utf-8')
        self.assertIn('script', self.finish())
        self.assertEqual((self.run / '文案.txt').read_text(encoding='utf-8'), '早先确认的文案')

    def test_explicit_script_is_passed_through(self):
        own = self.folder / '我的文案.txt'; own.write_text('自己给的', encoding='utf-8')
        self.args.script = own
        calls = []
        with patch.object(edit_project, 'wait_for_alignment'), patch.object(edit_project, 'run_self', side_effect=calls.append), \
                contextlib.redirect_stdout(io.StringIO()):
            edit_project.finish(self.folder, self.run, self.args)
        script = next(c for c in calls if c[2] == 'script')
        self.assertEqual(script[-2:], ['--script', str(own)])
        self.assertFalse((self.run / '文案.txt').exists())       # 文案.txt 由 script_check 写，原有“内容不同就拦”的保护不变


if __name__ == '__main__':
    unittest.main()
