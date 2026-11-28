# -*- coding: utf-8 -*-
"""代办跑 AI 改过的 Blender 工程（2026-11-28）：程序自己的文件一字不差就代跑，整个套专用沙盒（不联网、读不到钥匙、只写项目和临时目录）。
用替身程序目录，不起真的 Blender。"""
import json, os, shutil, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import thinkcut_broker as B      # noqa: E402

FAKE_OUT = r'''
import json, os, socket, sys
from pathlib import Path
r = {}
try: socket.create_connection(("1.1.1.1", 80), timeout=3); r["net"] = "通"
except Exception as e: r["net"] = "挡"
try: open(os.path.expanduser("~/Library/Keychains/login.keychain-db"), "rb").read(4); r["keychain"] = "读到"
except Exception as e: r["keychain"] = "挡"
try:
    p = Path(os.path.expanduser("~/Desktop/_thinkcut_sandbox_probe.txt")); p.write_text("x"); p.unlink(); r["desktop"] = "写了"
except Exception as e: r["desktop"] = "挡"
try: Path("项目里.txt").write_text("ok"); r["project"] = "写了"
except Exception as e: r["project"] = "挡"
print(json.dumps(r, ensure_ascii=False))
'''


class BlenderSandbox(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp()).resolve(); self.addCleanup(shutil.rmtree, self.t, True)
        tpl = self.t / '程序' / B.BLENDER; tpl.mkdir(parents=True)
        (tpl / '出片.py').write_text(FAKE_OUT, encoding='utf-8'); (tpl / 'worker.py').write_text('# w\n'); (tpl / '布局检查.py').write_text('# c\n'); (tpl / '场景.py').write_text('# 模板\n')
        self.proj = self.t / '项目'; self.bl = self.proj / 'Blender_x'; shutil.copytree(tpl, self.bl)
        os.environ['THINKCUT_BROKER_PROGRAM'] = str(self.t / '程序'); self.addCleanup(os.environ.pop, 'THINKCUT_BROKER_PROGRAM', None)

    def req(self): return {'script': '出片.py', 'path': str(self.bl / '出片.py'), 'cwd': str(self.bl), 'argv': ['--冒烟']}

    def test_ai_edited_scene_runs_in_sandbox(self):
        (self.bl / '场景.py').write_text('# AI 改过\n')
        info = {}; B.check(self.req(), [self.proj], info=info); self.assertTrue(info.get('sandbox'))
        q = self.proj / '代办'; q.mkdir(); (q / 'req-a.json').write_text(json.dumps({'id': 'a', **self.req()}, ensure_ascii=False))
        B.serve_once(q, [self.proj], wait=True)
        res = json.loads((q / 'res-a.json').read_text()); self.assertEqual(res['code'], 0, res)
        got = json.loads(res['stdout'].strip().splitlines()[-1])
        self.assertEqual(got, {'net': '挡', 'keychain': '挡', 'desktop': '挡', 'project': '写了'})

    def test_program_files_must_match(self):
        (self.bl / 'worker.py').write_text('# AI 改了程序自己的文件\n')
        with self.assertRaisesRegex(ValueError, '不代跑'): B.check(self.req(), [self.proj])

    def test_profile_has_the_guards(self):
        p = B.seatbelt([self.proj], home='/Users/u')
        for k in ('(deny network*)', '/Users/u/Library/Keychains', 'com.apple.SecurityServer', '/Users/u/.codex/auth.json', '(deny file-write*)', str(self.proj)):
            self.assertIn(k, p)


if __name__ == '__main__':
    unittest.main()
