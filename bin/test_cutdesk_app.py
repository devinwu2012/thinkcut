#!/usr/bin/env python3
"""Mac 程序外壳的离线测试（2026-11-24）：启动逻辑在“首页已在 / 不在 / 起不来”三种情况下的判断、退出只停自己起的，
以及外壳能构建出来（Mac 上有 swiftc 才测）。不联网、不碰用户的片子；用的端口都在 8760–8800 以外，不碰在跑的首页和审片台。
  python3 bin/test_cutdesk_app.py"""
import json
import os
import plistlib
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import cutdesk_app as A  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


def fake_server(answer):
    """起一个假服务：/api/ping 回 answer（None 就回 404，当成“别的程序”）。"""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            body = json.dumps(answer).encode() if answer is not None else b'nope'
            self.send_response(200 if answer is not None else 404); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    srv = ThreadingHTTPServer(('127.0.0.1', free_port()), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Launch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.t = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_已在跑就直接用_退出不停它(self):
        srv = fake_server({'app': '首页', 'root': str(self.t), 'pid': 999999})
        try:
            port = srv.server_address[1]
            r = A.start(port, root=str(self.t))
            self.assertTrue(r['ok']); self.assertFalse(r['started']); self.assertEqual(r['pid'], 999999)
            self.assertEqual(r['url'], f'http://127.0.0.1:{port}/')
            self.assertTrue(A.start(port)['ok'])                                  # 不指定放片子的文件夹也直接用
            self.assertIn('不是这个程序起的', A.stop(port, 123))                    # pid 对不上：不停
        finally:
            srv.shutdown(); srv.server_close()

    def test_已在跑但看的是别的文件夹(self):
        srv = fake_server({'app': '首页', 'root': '/别处', 'pid': 1})
        try:
            r = A.start(srv.server_address[1], root=str(self.t))
            self.assertFalse(r['ok']); self.assertIn('/别处', r['error'])
        finally:
            srv.shutdown(); srv.server_close()

    def test_端口被别的程序占(self):
        for answer in (None, {'app': '审片工作台'}):
            srv = fake_server(answer)
            try:
                r = A.start(srv.server_address[1], root=str(self.t))
                self.assertFalse(r['ok']); self.assertIn('被别的程序占', r['error'])
            finally:
                srv.shutdown(); srv.server_close()

    def test_不在就起_退出只停自己起的(self):
        port = free_port(); (self.t / '片子').mkdir()
        r = A.start(port, root=str(self.t / '片子'))
        try:
            self.assertTrue(r['ok'], r); self.assertTrue(r['started']); self.assertIsInstance(r['pid'], int)
            pg = A.ping(port); self.assertEqual(pg['app'], '首页'); self.assertEqual(pg['root'], str(self.t / '片子'))
            again = A.start(port, root=str(self.t / '片子'))                      # 第二个窗口：复用，不算自己起的
            self.assertTrue(again['ok']); self.assertFalse(again['started'])
            self.assertIn('不是这个程序起的', A.stop(port, r['pid'] + 1)); self.assertIsNotNone(A.ping(port))
            self.assertEqual(A.stop(port, r['pid']), '已停'); self.assertIsNone(A.ping(port))
        finally:
            if A.ping(port):
                os.kill(A.ping(port)['pid'], 15)
        self.assertEqual(A.stop(port, r['pid']), '首页已经不在了')

    def test_起不来_给人话和程序说的(self):
        port = free_port()
        r = A.start(port, root=str(self.t / '没有这个文件夹'), wait=3)
        self.assertFalse(r['ok']); self.assertFalse(r['started'])
        self.assertIn('首页没起来', r['error']); self.assertIn('放片子的文件夹不存在', r['error'])
        self.assertTrue(r['log']); self.assertIsNone(A.ping(port))

    def test_起不来_程序文件找不到(self):
        r = A.start(free_port(), root=str(self.t), script=self.t / 'thinkcut_edit.py')
        self.assertFalse(r['ok']); self.assertIn('找不到程序文件', r['error'])

    def test_命令行只打印一行JSON(self):
        srv = fake_server({'app': '首页', 'root': str(self.t), 'pid': 7})
        try:
            out = subprocess.run([sys.executable, '-B', str(ROOT / 'bin' / 'cutdesk_app.py'), 'start', '--port', str(srv.server_address[1])],
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip().splitlines()
            self.assertEqual(len(out), 1); self.assertEqual(json.loads(out[0])['pid'], 7)
        finally:
            srv.shutdown(); srv.server_close()

    def test_程序坞打开时补上Homebrew的路径(self):
        env = A.child_env()
        self.assertIn('/opt/homebrew/bin', env['PATH'].split(':')); self.assertEqual(env['THINKCUT_NO_OPEN'], '1')


@unittest.skipUnless(sys.platform == 'darwin', '只在 Mac 上构建')
class Build(unittest.TestCase):
    def test_构建出Cutdesk_app(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(['bash', str(ROOT / 'bin' / 'build_app.sh'), '--out', d], capture_output=True, text=True, errors='replace', stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            app = Path(d) / 'ThinkCut.app'
            info = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
            self.assertEqual(info['CutdeskProgram'], str(ROOT)); self.assertEqual(info['CFBundleExecutable'], 'Cutdesk'); self.assertEqual((info['CFBundleName'], info['CFBundleDisplayName']), ('ThinkCut', 'ThinkCut'))
            self.assertTrue((app / 'Contents' / 'Resources' / 'Cutdesk.icns').stat().st_size > 1000)
            env = {k: v for k, v in os.environ.items() if k != 'CUTDESK_PROGRAM'}
            s = subprocess.run([str(app / 'Contents' / 'MacOS' / 'Cutdesk'), '--selftest'], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=30)
            self.assertEqual(s.returncode, 0, s.stdout + s.stderr); self.assertIn('链接判断、首页判断 ok', s.stdout); self.assertIn(str(ROOT), s.stdout)


if __name__ == '__main__':
    unittest.main()
