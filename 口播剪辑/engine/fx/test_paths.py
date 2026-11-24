from pathlib import Path
import tempfile
import unittest
from engine.fx.paths import default_work_dir


class PathTests(unittest.TestCase):
    def thinkcut(self,root):
        (root/'01_原始素材').mkdir(parents=True);(root/'项目.json').write_text('{}');return root/'02_制作'/'花字音效'
    def test_thinkcut_project_and_nested_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();ai=self.thinkcut(root)
            self.assertEqual(default_work_dir(root/'notes'/'batch.json'),ai/'batch')
    def test_existing_work_dir_kept_even_when_ai_root_appears(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();self.thinkcut(root)
            old=root/'old';old.mkdir();(old/'操作记录.json').write_text('{}')
            self.assertEqual(default_work_dir(old/'plan.json'),old)
            self.assertEqual(default_work_dir(root/'花字音效/批次/plan.json'),root/'花字音效/批次')
    def test_legacy_fallback_and_explicit_project_root(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            self.assertEqual(default_work_dir(root/'plan.json'),root/(root.name+'_AI剪辑')/'花字音效')
            self.assertEqual(default_work_dir(root/'片名_AI剪辑/plan.json'),root/'片名_AI剪辑/花字音效')
            ai=self.thinkcut(root)
            self.assertEqual(default_work_dir('/tmp/plan.json',root,'本轮'),ai/'本轮')

if __name__=='__main__':unittest.main()
