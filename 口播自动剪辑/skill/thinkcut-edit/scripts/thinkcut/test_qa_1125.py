# -*- coding: utf-8 -*-
"""2026-11-25 发布前 QA（QA_发布前验收报告_2026-11-25.1.md）后台问题的回归测试。不连达芬奇、不碰真片子，片子都建在临时文件夹，端口用空的。run_tests.sh 会跑。
每个测试的名字前面是报告里的编号。"""
import contextlib, io, json, os, shutil, signal, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinkcut import home as HM, workbench as W, board as B, publish as P, draft   # noqa: E402
from thinkcut.common import Job, wj, rj                                             # noqa: E402
from thinkcut.test_workbench import FakeDV, film                                    # noqa: E402
from thinkcut.test_home import free_ports                                           # noqa: E402
os.environ['THINKCUT_NO_OPEN'] = '1'


def call(base, path, body=None, cookie=None, page=True, raw=None, headers=None):
    """像页面一样发请求（page=True 带来源、同源标记；cookie 给了就带）。返回 (状态码, 回话)。"""
    h = {'Content-Type': 'application/json', **({'Origin': base, 'Sec-Fetch-Site': 'same-origin'} if page else {}), **({'Cookie': cookie} if cookie else {}), **(headers or {})}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    q = urllib.request.Request(base + path, data, h)
    try:
        with urllib.request.urlopen(q, timeout=20) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        b = e.read()
        try: return e.code, json.loads(b)
        except ValueError: return e.code, b


class Served(unittest.TestCase):
    """一条完整的片子（有 job.json）＋在本进程里起的审片台。"""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.job = film(self.tmp); self.dv = FakeDV()
        self.srv = W.make_server(self.job, 0, self.dv); self.port = self.srv.server_address[1]; self.base = f'http://127.0.0.1:{self.port}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.ck = W.cookie_header(self.port)

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self, path, body=None): return call(self.base, path, body, self.ck)


class JobReload(Served):
    def test_1_4_second_review_timeline_settle_then_export(self):
        """审片台开着时 AI 建了第二轮审核线（job.json 换了 review_timeline）：页面拿到新名字；用户点“确定此栏”以后导出闸放行（以前对不上、导出被拦）。"""
        self.assertEqual(call(self.base, '/api/data')[1]['review_timeline'], 'v1_卡点审核')
        cfg = rj(self.tmp / 'job.json'); cfg['review_timeline'] = 'QA_第二轮'; wj(self.tmp / 'job.json', cfg)
        t = time.time() + 5; os.utime(self.tmp / 'job.json', (t, t))
        self.assertEqual(call(self.base, '/api/data')[1]['review_timeline'], 'QA_第二轮')
        c, r = self.page('/api/settle', {'column': '审核线'}); self.assertEqual(c, 200, r); self.assertTrue(r['ok'], r)
        with contextlib.redirect_stdout(io.StringIO()): got = draft.export_gate(Job(self.tmp), 'QA_第二轮')
        self.assertEqual(got['column'], '审核线')


class NoForgedConfirm(Served):
    def test_4_token_only_in_httponly_cookie(self):
        """AI 拿不到页面口令：页面里、/api/data 里都没有；拿页面里公开的编号、或没有 cookie，都点不了“确定此栏”“存到蚁小二草稿”。"""
        with urllib.request.urlopen(self.base + '/') as r: html = r.read().decode(); ck = r.headers['Set-Cookie']
        self.assertNotIn(W.TOKEN, html); self.assertIn('HttpOnly', ck)
        self.assertNotIn(W.TOKEN, json.dumps(call(self.base, '/api/data')[1], ensure_ascii=False))
        for path, body in (('/api/settle', {'column': '审核线'}), ('/api/publish/request', {'mode': 'draft'})):
            for cookie, extra in ((None, {'X-Shenpian-Token': W.SESSION}), (f'{W.COOKIE}_{self.port}={W.SESSION}', {}), (self.ck, {'Sec-Fetch-Site': 'cross-site'})):
                c, r = call(self.base, path, body, cookie, page=True, headers=extra); self.assertEqual(c, 403, (path, cookie, extra))
        self.assertNotEqual((B.settle_info(self.job).get('审核线') or {}).get('state'), 'ok')
        c, r = self.page('/api/settle', {'column': '审核线'}); self.assertTrue(r['ok'], r)                 # 页面自己（带 cookie）照常能点
        rec = B.load(self.job)['settled']['审核线']; self.assertEqual(rec['who'], B.WHO)

    def test_9_stale_page_after_restart(self):
        """服务重启过（cookie 是上一次的）：403 带 code=stale_page，页面据此刷新。"""
        c, r = call(self.base, '/api/settle', {'column': '审核线'}, f'{W.COOKIE}_{self.port}=old-token')
        self.assertEqual((c, r['code']), (403, 'stale_page'))


class BrokenFiles(Served):
    def test_8_broken_marks_readable_error_and_restore(self):
        """标记.json 坏了：/api/data 回一句中文和 code=broken_file（不再断开连接），页面点恢复：坏的留着、退回上一版。"""
        W.edit(self.job, lambda d: d['markers'].append({'id': 1, 'round': 1, 't': 1.0, 'text': '第一条', 'status': '待处理', 'frame': '', 'imgs': [], 'reply': '', 'of': None, 'at': 'x', 'timeline': '', 'quote': ''}))
        W.edit(self.job, lambda d: d['markers'][0].update(text='第一条改过'))
        W.data_file(self.job).write_text('{broken', encoding='utf-8')
        c, r = call(self.base, '/api/data')
        self.assertEqual((c, r['ok'], r['code'], r['file'], r['which'], r['can_restore']), (500, False, 'broken_file', '标记.json', '标记', True)); self.assertIn('已损坏', r['error'])
        self.assertEqual(call(self.base, '/api/ping')[0], 200)                         # 服务还活着
        c, r = call(self.base, '/api/repair', {'file': '标记'}); self.assertEqual(c, 403)   # 没有页面 cookie 不能动
        c, r = self.page('/api/repair', {'file': '标记'}); self.assertTrue(r['ok'] and r['restored'], r)
        self.assertTrue(Path(r['kept']).is_file()); self.assertEqual(Path(r['kept']).read_text(encoding='utf-8'), '{broken')
        self.assertEqual([m['text'] for m in call(self.base, '/api/data')[1]['markers']], ['第一条'])   # 退回的是上一版

    def test_8_unexpected_errors_answer_not_disconnect(self):
        """没料到的请求（数组、反着写的分段范围、备料空路径）：回 JSON 或 416，不断开（QA 2.5）。"""
        c, r = call(self.base, '/api/text', raw=b'[1,2]', cookie=self.ck); self.assertEqual(c, 400)
        c, r = self.page('/api/material/paths', {'text': ''}); self.assertEqual(c, 200); self.assertFalse(r['ok'])
        f = B.store(self.job) / 'a.mp4'; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'0' * 1000)
        q = urllib.request.Request(self.base + urllib.parse.quote('/审片台/a.mp4'), headers={'Range': 'bytes=500-100'})
        with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(q)
        self.assertEqual(e.exception.code, 416)
        with urllib.request.urlopen(self.base + urllib.parse.quote('/审片台/a.mp4')) as r: self.assertEqual(len(r.read()), 1000)    # 没 Range：整段分块写出
        with urllib.request.urlopen(urllib.request.Request(self.base + urllib.parse.quote('/审片台/a.mp4'), headers={'Range': 'bytes=10-19'})) as r: self.assertEqual((r.status, r.read()), (206, b'0' * 10))


class SendAndPublish(Served):
    def test_6_publish_stuck_can_be_cancelled_or_called_again(self):
        """“存到蚁小二草稿”AI 接了没回话：过了 STALE 页面看到 state=stale，能再叫一次（重新交给 AI）、也能取消；取消后 AI 再回话被拒，用户能重新点。"""
        B.edit(self.job, lambda d: d.setdefault('settled', {}).update({'审核线': {'at': 'x', 'sig': B.col_sig(self.job, '审核线'), 'who': B.WHO}}))
        c, r = self.page('/api/publish/request', {'mode': 'draft'}); self.assertTrue(r['ok'], r)
        self.assertEqual(P.info(self.job)['req']['state'], 'waiting')
        self.assertEqual(B.pick(self.job), [('上传文案', '发布')]); self.assertEqual(P.info(self.job)['req']['state'], 'doing')
        c, r = self.page('/api/send', {'column': '上传文案'}); self.assertFalse(r['ok'])        # 刚接：不能再叫
        with mock.patch.object(B, 'STALE', 0):
            self.assertEqual(P.info(self.job)['req']['state'], 'stale'); self.assertIn('发布', B.send_info(self.job)['stale'])
            c, r = self.page('/api/send', {'column': '上传文案'}); self.assertTrue(r['ok'], r)  # 很久没动静：再叫一次
        self.assertEqual(B.send_info(self.job)['ids']['发布'], 'sent')
        c, r = self.page('/api/publish/cancel', {}); self.assertTrue(r['ok'], r)
        self.assertEqual(P.info(self.job)['req']['state'], 'cancelled'); self.assertNotIn('发布', B.send_info(self.job)['ids'])
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()): P.reply(self.job, '存好了')
        c, r = self.page('/api/publish/request', {'mode': 'draft'}); self.assertTrue(r['ok'], r)   # 能重新点
        self.assertEqual(P.info(self.job)['req']['state'], 'waiting')

    def test_6_unsend_any_column(self):
        """其他栏的“AI 在处理”也能撤回：回到待发送。"""
        W.edit(self.job, lambda d: d['markers'].append({'id': 1, 'round': 1, 't': 1.0, 'text': '改这里', 'status': '待处理', 'frame': '', 'imgs': [], 'reply': '', 'of': None, 'at': 'x', 'timeline': '', 'quote': ''}))
        self.assertTrue(self.page('/api/send', {'column': '审核线'})[1]['ok']); B.pick(self.job)
        self.assertEqual(B.send_info(self.job)['ids']['#1'], 'doing')
        c, r = self.page('/api/unsend', {'column': '审核线'}); self.assertTrue(r['ok'], r); self.assertEqual(r['n'], 1)
        self.assertEqual(B.send_info(self.job)['ids']['#1'], 'unsent')
        self.assertEqual(call(self.base, '/api/unsend', {'column': '审核线'})[0], 403)
        self.assertTrue(self.page('/api/send', {'column': '审核线'})[1]['ok']); c, r = self.page('/api/send/cancel', {'column': '审核线'}); self.assertTrue(r['ok'], r)   # 页面用的别名


class Processes(unittest.TestCase):
    """真的起审片台进程（和首页起的一样），测闲置退出、端口满了停最久没用的、不停别的程序起的。"""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / '片子'; self.root.mkdir(); self.jobs = []

    def tearDown(self):
        for j in self.jobs:
            with contextlib.redirect_stdout(io.StringIO()): W.stop(j)
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def new(self, title):
        fid = HM.new_film(self.root, title); j = W.open_film(self.root / fid); self.jobs.append(j); return fid, j

    def test_2_idle_exit(self):
        _, j = self.new('闲着')
        with mock.patch.dict(os.environ, {'THINKCUT_WB_IDLE': '2'}), contextlib.redirect_stdout(io.StringIO()):
            url, _ = W.start(j, None, free_ports(1))
        port = int(url.rsplit(':', 1)[1].strip('/'))
        self.assertIsNotNone(W.ping(port)); self.assertIn('idle', W.ping(port))
        for _ in range(80):
            if not W.ping(port, 0.2): break
            time.sleep(0.1)
        self.assertIsNone(W.ping(port, 0.2), '闲了 2 秒应该自己退出'); self.assertFalse((W.home(j) / '服务.json').exists())

    def test_2_ports_full_stops_longest_idle(self):
        ports = free_ports(1); _, a = self.new('先开的')
        with contextlib.redirect_stdout(io.StringIO()): W.start(a, None, ports)
        _, b = self.new('后开的')
        with mock.patch.object(W, 'RECLAIM_MIN', 0), contextlib.redirect_stdout(io.StringIO()): url, _ = W.start(b, None, ports)
        self.assertEqual(W.ping(ports[0])['dir'], str(b.dir)); self.assertIsNone(W.running(a))

    def test_2_home_new_film_open_failed_is_structured(self):
        """端口都被别的程序占着、停不了：片子建好了但审片台没开——回 code=open_failed 和片子 id（页面写“建好了，稍后再开”），不是 Python 原始报错。"""
        ports = free_ports(1); s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', ports[0])); s.listen()
        try:
            srv = HM.make_server(self.root, 0, ports); port = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                c, r = call(f'http://127.0.0.1:{port}', '/api/new', {'title': '开不了'}, HM.cookie_header(port))
                self.assertEqual((r['ok'], r['code']), (False, 'open_failed'), r); self.assertTrue((self.root / r['id'] / '项目.json').is_file())
            finally: srv.shutdown(); srv.server_close()
        finally: s.close()

    def test_1_other_program_not_stopped(self):
        """这条片子正被另一个程序文件夹（正式版／开发版）的审片台开着：首页不去停它，说清楚（以前两边来回互相停）。"""
        _, j = self.new('两边都开')
        other = self.root.parent / '另一份程序'; other.mkdir()
        class H(__import__('http.server').server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                b = json.dumps({'app': '审片工作台', 'dir': str(j.dir), 'pid': 1, 'lite': True, 'prog': {'dir': str(other), 'sig': 'x'}}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        fake = __import__('http.server').server.ThreadingHTTPServer(('127.0.0.1', 0), H); threading.Thread(target=fake.serve_forever, daemon=True).start()
        try:
            W.home(j).mkdir(parents=True, exist_ok=True); wj(W.home(j) / '服务.json', {'pid': 1, 'port': fake.server_address[1], 'url': f'http://127.0.0.1:{fake.server_address[1]}/', 'dir': str(j.dir)})
            with self.assertRaises(SystemExit) as e, contextlib.redirect_stdout(io.StringIO()): W.start(j, None, free_ports(2), fresh=True)
            self.assertIn(str(other), str(e.exception)); self.assertIsNotNone(W.ping(fake.server_address[1]))
        finally:
            fake.shutdown(); fake.server_close(); (W.home(j) / '服务.json').unlink(missing_ok=True)


class Home(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / '片子'; self.root.mkdir(); (self.root / 'finish').mkdir()

    def tearDown(self):
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def test_7_concurrent_and_repeated_new(self):
        """同一个题目同时来 3 个、又连着来一次：只建一条，都拿到它；不同题目照常建。"""
        out, errs = [], []
        def go():
            try: out.append(HM.new_film(self.root, '并发测试'))
            except Exception as e: errs.append(e)
        ts = [threading.Thread(target=go) for _ in range(3)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, []); self.assertEqual(len(set(out)), 1); self.assertEqual(HM.new_film(self.root, '并发测试'), out[0])
        self.assertEqual(len([d for d in self.root.iterdir() if '并发测试' in d.name]), 1)
        self.assertNotEqual(HM.new_film(self.root, '别的题目'), out[0])
        self.assertEqual([p.name for p in self.root.rglob('*.tmp')], [])

    def test_7_same_title_at_once_through_home(self):
        """通过首页同时来 3 个同题目的“新建”：都成功、拿到同一条、只起一个审片台（2026-11-25 在 NAS 上实测过两个请求同时写 标记.json 报“已存在”）。"""
        srv = HM.make_server(self.root, 0, free_ports(3)); port = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out = []
            ts = [threading.Thread(target=lambda: out.append(call(f'http://127.0.0.1:{port}', '/api/new', {'title': '同时'}, HM.cookie_header(port))[1])) for _ in range(3)]
            [t.start() for t in ts]; [t.join() for t in ts]
            self.assertTrue(all(r['ok'] for r in out), out); self.assertEqual(len({r['id'] for r in out}), 1); self.assertEqual(len({r['url'] for r in out}), 1)
            self.assertEqual(sorted(r['existed'] for r in out), [False, True, True])
        finally:
            srv.shutdown(); srv.server_close()
            with contextlib.redirect_stdout(io.StringIO()):
                for d in self.root.iterdir():
                    if (d / '项目.json').is_file(): W.stop(W.open_film(d))

    def test_7_home_errors_are_json_not_disconnect(self):
        srv = HM.make_server(self.root, 0); port = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f'http://127.0.0.1:{port}'
            self.assertEqual(call(base, '/api/new', raw=b'[1,2]', cookie=HM.cookie_header(port))[0], 400)
            c, r = call(base, '/api/new', {'title': 'x'}, f'{HM.COOKIE}_{port}=old-token'); self.assertEqual((c, r['code']), (403, 'stale_page'))
            class No(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a): return None
            op = urllib.request.build_opener(No)
            for path in ('/open?id=x&t=old', '/reopen?port=1'):          # 首页重启过：回首页，不是 403
                with self.assertRaises(urllib.error.HTTPError) as e: op.open(base + path)
                self.assertEqual((e.exception.code, e.exception.headers['Location']), (302, '/'), path)
        finally: srv.shutdown(); srv.server_close()

    def test_10_long_title_can_be_deleted(self):
        bin_ = self.root.parent / '回收站'; bin_.mkdir()
        with mock.patch.dict(os.environ, {'THINKCUT_RECYCLE_ROOT': str(bin_)}):
            fid = HM.new_film(self.root, '😀' * 60)
            self.assertLessEqual(len(fid.encode()), 7 + HM.MAX_TITLE)
            r = HM.trash_film(self.root, fid); self.assertTrue(Path(r['to']).is_dir())
            self.assertEqual(HM.restore_film(self.root, r['to'], r['from'])['id'], fid)
            long = self.root / ('261101 ' + '长' * 82); shutil.copytree(self.root / fid, long)        # 以前建出来的超长名字（246 字节）也删得掉、还原得回来
            r = HM.trash_film(self.root, long.name); self.assertLessEqual(len(Path(r['to']).name.encode()), 255)
            rj_ = json.loads((Path(r['to']) / '项目.json').read_text(encoding='utf-8')); rj_['name'] = long.name; (Path(r['to']) / '项目.json').write_text(json.dumps(rj_, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(HM.orig_name(r['to']), long.name); self.assertEqual(HM.restore_film(self.root, r['to'], long.name)['id'], long.name)

    def test_10_deleted_number_not_reused(self):
        bin_ = self.root.parent / '回收站'; bin_.mkdir()
        with mock.patch.dict(os.environ, {'THINKCUT_RECYCLE_ROOT': str(bin_)}):
            a = HM.new_film(self.root, 'a3'); HM.trash_film(self.root, a)
            b = HM.new_film(self.root, 'b4'); self.assertNotEqual(a[:6], b[:6])

    def test_10_case_only_rename(self):
        fid = HM.new_film(self.root, 'a1')
        if not (self.root / fid.upper()).exists(): self.skipTest('这块盘分大小写')
        with contextlib.redirect_stdout(io.StringIO()): new = HM.rename_film(self.root, fid, 'A1')
        self.assertEqual(new, fid[:7] + 'A1'); self.assertIn(new, [d.name for d in self.root.iterdir()])


class RestoreToOwnIP(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp()); prof = self.t / '配置档'; prof.mkdir(); (self.t / '回收站').mkdir()
        cc = HM.cfg(); pub = cc._read(Path(cc.PROGRAM) / '配置档' / '公共.json')
        pub.update({'默认IP': '甲', '片子文件夹': str(self.t / '{IP}视频'), '回收站': str(self.t / '回收站')}); (prof / '公共.json').write_text(json.dumps(pub, ensure_ascii=False), encoding='utf-8')
        for n in ('甲', '乙'): (prof / n).mkdir(); (prof / n / 'ip.json').write_text(json.dumps({'名字': n}, ensure_ascii=False), encoding='utf-8')
        self.env = mock.patch.dict(os.environ, {'THINKCUT_PROFILES': str(prof), 'THINKCUT_RECYCLE_ROOT': str(self.t / '回收站'), 'THINKCUT_APP_FLAVOR': 'release'}); self.env.start()
        for k in ('THINKCUT_IP', 'THINKCUT_VIDEO_ROOT', 'THINKCUT_USER_PROFILES'): os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop(); shutil.rmtree(self.t, ignore_errors=True)

    def test_10_undo_after_switching_ip(self):
        """在甲里删了一条片子，切到乙，再点“撤销”：放回甲的片子文件夹（以前放进了乙的）。"""
        srv = HM.make_server(None, 0); port = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f'http://127.0.0.1:{port}'; ck = HM.cookie_header(port)
            c, r = call(base, '/api/new', {'title': 'a3'}, ck); fid = r['id']
            with contextlib.redirect_stdout(io.StringIO()): W.stop(W.open_film(self.t / '甲视频' / fid))
            c, tr = call(base, '/api/trash', {'id': fid, 'ip': '甲'}, ck); self.assertTrue(tr['ok'], tr); self.assertEqual(tr['ip'], '甲')
            self.assertTrue(call(base, '/api/ip', {'name': '乙'}, ck)[1]['ok'])
            c, r = call(base, '/api/restore', {'to': tr['to'], 'from': tr['from'], 'ip': '乙'}, ck); self.assertTrue(r['ok'], r)
            self.assertEqual(r['ip'], '甲'); self.assertTrue((self.t / '甲视频' / fid / '项目.json').is_file()); self.assertFalse((self.t / '乙视频' / fid).exists())
        finally: srv.shutdown(); srv.server_close()


if __name__ == '__main__':
    unittest.main()
