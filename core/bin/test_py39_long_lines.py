"""Mac 自带 Python 3.9 的坑（2026-11-12《水电阶段》新11⑤）：一行超过约 1000 字节、里面有中文、文件头又没写编码声明时，
3.9 会误报 “Non-UTF-8 code starting with …” 而拒绝运行（换个字的位置又好了，很难查）。
文件第一或第二行写 `# -*- coding: utf-8 -*-` 就不会出错。这里查本仓库所有 .py 都满足这一条。"""
import re, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECLARATION = re.compile(rb'^[ \t\f]*#.*?coding[:=]')


def risky(path, limit=1000):
    lines = path.read_bytes().split(b'\n')
    if any(DECLARATION.match(line) for line in lines[:2]): return []
    return [number for number, line in enumerate(lines, 1) if len(line) >= limit and any(b > 127 for b in line)]


class LongLineTests(unittest.TestCase):
    def test_no_long_non_ascii_line_without_coding_declaration(self):
        found = {str(p.relative_to(ROOT)): risky(p) for p in ROOT.rglob('*.py') if '__pycache__' not in p.parts}
        self.assertEqual({k: v for k, v in found.items() if v}, {}, '这些行在 Python 3.9 上可能误报 Non-UTF-8：拆短，或在文件头加 # -*- coding: utf-8 -*-')

    def test_check_catches_the_real_failure(self):
        import subprocess, sys, tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'x.py'
            path.write_text('x = "' + '中' * 400 + '"\n', encoding='utf-8')
            self.assertEqual(risky(path), [1])
            path.write_text('# -*- coding: utf-8 -*-\nx = "' + '中' * 400 + '"\n', encoding='utf-8')
            self.assertEqual(risky(path), [])
            self.assertEqual(subprocess.run([sys.executable, str(path)]).returncode, 0)


if __name__ == '__main__':
    unittest.main()
