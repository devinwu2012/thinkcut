# -*- coding: utf-8 -*-
"""2026-11-23 初稿模式实测：deliver 动达芬奇之前自动调 视频项目管理 的 project.py resolve-open，把达芬奇备到“开着这条片子的工程”，不问用户。
这里只用临时目录和一个假的 project.py（经环境变量 THINKCUT_PM），不连达芬奇。"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import edit_project


class PrepareResolveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root = base / '261123 测试片'; (self.root / '01_原始素材').mkdir(parents=True)
        (self.root / '项目.json').write_text(json.dumps({'name': self.root.name}, ensure_ascii=False), encoding='utf-8')
        self.run = self.root / '02_制作' / '口播_AI剪辑' / '审核'; self.run.mkdir(parents=True)
        self.pm = base / 'pm'; self.pm.mkdir(); self.log = base / 'called.json'
        p = patch.dict(os.environ, {'THINKCUT_PM': str(self.pm)}); p.start(); self.addCleanup(p.stop)

    def config(self, **extra):
        value = {'project': self.root.name, 'existing_project_only': True, 'source': str(self.root / '01_原始素材' / 'a.mov'), **extra}
        (self.run / 'project_runtime.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def tool(self, body):
        (self.pm / 'project.py').write_text('# -*- coding: utf-8 -*-\nimport json, sys\n'
                                            f'open({str(self.log)!r}, "w", encoding="utf-8").write(json.dumps(sys.argv[1:], ensure_ascii=False))\n' + body, encoding='utf-8')

    def test_standard_project_calls_resolve_open_and_reports_switch(self):
        self.config()
        self.tool('print(json.dumps({"工程": "x", "借用": "工程暂存在「旧库」库", "切走了": "达芬奇原来开着《别的片子》，已保存；要回去说一声", "结果": "新建了工程"}, ensure_ascii=False))\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = edit_project.prepare_resolve(self.run)
        self.assertEqual(json.loads(self.log.read_text(encoding='utf-8')), ['resolve-open', str(self.root)])
        self.assertEqual(result['结果'], '新建了工程')
        self.assertIn('切走了：达芬奇原来开着《别的片子》', out.getvalue()); self.assertIn('借用：', out.getvalue())

    def test_failure_stops_delivery_with_the_tools_own_reason(self):
        self.config()
        self.tool('print("停止：保存达芬奇当前工程《别的片子》失败，没有切换", file=sys.stderr); sys.exit(1)\n')
        with self.assertRaisesRegex(SystemExit, '没能自动备好达芬奇工程.*保存达芬奇当前工程《别的片子》失败'):
            edit_project.prepare_resolve(self.run)

    def test_skipped_when_user_pointed_elsewhere_or_not_a_standard_project(self):
        self.tool('print("{}")\n')
        for extra in ({'resolve_library': '用户指的库'}, {'project': '改过名的工程'}, {'existing_project_only': False}):
            with self.subTest(extra=extra):
                self.config(**extra)
                self.assertIsNone(edit_project.prepare_resolve(self.run)); self.assertFalse(self.log.exists())
        self.config(); (self.root / '项目.json').unlink()          # 不是标准项目
        self.assertIsNone(edit_project.prepare_resolve(self.run)); self.assertFalse(self.log.exists())

    def test_missing_tool_only_warns(self):
        self.config(); err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(edit_project.prepare_resolve(self.run))
        self.assertIn('THINKCUT_PM', err.getvalue())


if __name__ == '__main__':
    unittest.main()
