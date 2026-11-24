# -*- coding: utf-8 -*-
"""首页（2026-11-24）离线测试：不连达芬奇、不碰真片子（片子都建在临时文件夹）。run_tests.sh 会跑。
扫片子和阶段判断、按需起单片审片台并复用、新建片子、备料（拖进来／登记路径：同名不覆盖、大文件、半截不留）、等所有片子、命令行不能替用户点。"""
import contextlib, http.client, io, json, os, shutil, socket, sys, tempfile, threading, time, unittest, urllib.error, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinkcut import home as HM, workbench as W, board, writing, materials as M   # noqa: E402
from thinkcut.common import wj, rj                                                 # noqa: E402
os.environ['THINKCUT_NO_OPEN'] = '1'


def free_ports(n):
    """找一段连续的空端口给测试起的审片台用（不占用户的 8765–8784）。"""
    for a in range(18800, 19800, n):
        if all(W.port_free(p) for p in range(a, a + n)): return range(a, a + n)
    raise RuntimeError('找不到空端口')


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / '片子'; self.root.mkdir(); (self.root / 'finish').mkdir()
        self.ports = free_ports(6); self.jobs = []

    def tearDown(self):
        for j in self.jobs:
            with contextlib.redirect_stdout(io.StringIO()): W.stop(j)
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def film(self, title):
        fid = HM.new_film(self.root, title); return fid, W.open_film(self.root / fid)


class ScanTests(Base):
    def test_scan_and_stages(self):
        a, ja = self.film('还在写文案')
        b, jb = self.film('定稿了'); board.set_script(jb, '标题', '定稿了', '写稿'); board.set_script(jb, '口播正文', '一段。\n\n二段。', '写稿'); board.settle(jb, '写稿', True, 't')
        c, _ = self.film('在改初稿'); wj(self.root / c / '02_制作' / '自动剪辑' / 'job.json', {'fps': '30000/1001', 'format': '3:4', 'frames': 300, 'project': 'x', 'review_timeline': 'v1'})
        d, _ = self.film('做完了'); shutil.move(str(self.root / d), str(self.root / 'finish' / d))
        e, _ = self.film('存了草稿'); wj(self.root / e / '02_制作' / '自动剪辑' / 'job.json', {'fps': '30000/1001', 'format': '3:4', 'frames': 300, 'project': 'x', 'review_timeline': 'v1', 'export_ok': {'at': 'x'}})
        wj(self.root / e / '05_上传记录' / 'r1' / '回执.json', {'state': 'draft_saved'})
        (self.root / '不是片子').mkdir()
        L = HM.scan(self.root); ids = [x['id'] for x in L]
        self.assertEqual(len(L), 5, '不是项目的文件夹不算'); self.assertEqual(ids[-1], f'finish/{d}', '做完的排最后'); self.assertTrue(L[-1]['done'])
        self.assertTrue(all(L[i]['t'] >= L[i + 1]['t'] for i in range(3)), '正在做的按最后改动从新到旧')
        self.assertEqual(next(x for x in L if x['id'] == a)['title'], '还在写文案')
        st = lambda fid: [(s['name'], s['state'], s['tag']) for s in HM.status(self.root, fid)['stages']]
        self.assertEqual(st(a)[0], ('写文案', 'now', '')); self.assertEqual([s[1] for s in st(a)], ['now', 'todo', 'todo', 'todo'])
        self.assertEqual(st(b)[:2], [('写文案', 'done', ''), ('出初稿', 'now', 'AI 在改')], '定稿后 AI 在开项')
        board.edit(jb, lambda dd: dd['settled']['写稿'].update(handled={'at': 'x', 'note': '项目开好了'}))
        self.assertEqual(st(b)[1], ('出初稿', 'now', '待你看'), 'AI 回了开好项：轮到用户备料、点开工')
        self.assertEqual([s[1] for s in st(c)], ['done', 'done', 'now', 'todo'])
        self.assertEqual([s[1] for s in st(f'finish/{d}')], ['done'] * 4)
        self.assertEqual([s[1] for s in st(e)], ['done'] * 4, '存进蚁小二草稿算发布这步做完')
        s = HM.status(self.root, a); self.assertEqual((s['turn'], s['ai'], s['open']), (0, 0, False))
        writing.note_add(ja, {'text': '帮我先写一版'}); s = HM.status(self.root, a)
        self.assertEqual(s['turn'], 1, '轮到你 和审片台顶栏一样算'); self.assertEqual(s['stages'][0]['tag'], '待发送')
        with self.assertRaises(HM.Oops): HM.status(self.root, '../外面')
        with self.assertRaises(HM.Oops): HM.film_dir(self.root, '不是片子')

    def test_new_film(self):
        fid, job = self.film('卫生间 / 防水:到底刷多高')
        d = self.root / fid; meta = rj(d / '项目.json')
        self.assertRegex(fid, r'^\d{6} 卫生间 防水到底刷多高$', '编号＋空格＋题目，去掉路径符号')
        self.assertEqual(meta['name'], fid); self.assertEqual(meta['title'], '卫生间 防水到底刷多高')
        for sub in ('01_原始素材/A roll', '01_原始素材/B roll', '01_原始素材/素材清单.json', '02_制作', '03_成片', '05_上传记录'): self.assertTrue((d / sub).exists(), sub)
        self.assertFalse((d / '02_制作' / '达芬奇工程').exists(), '不登记达芬奇库')
        u, _ = self.film('')
        self.assertTrue(u.endswith(' 未命名')); self.assertNotEqual(u[:6], fid[:6], '编号撞了按规矩往后顺延'); self.assertEqual(rj(self.root / u / '项目.json')['title'], '')
        self.assertTrue(W.lite(job) and board.project_dir(job) == d.resolve(), '新片子开出来是只有文案栏的审片台')


class ServerTests(Base):
    def setUp(self):
        super().setUp()
        self.srv = HM.make_server(self.root, 0, self.ports); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); super().tearDown()

    def req(self, path, body=None, page=True, origin=None):
        h = {'Content-Type': 'application/json'}
        if page: h.update({'Origin': origin or f'http://127.0.0.1:{self.port}', 'X-Shenpian-Token': HM.TOKEN})
        q = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', None if body is None else json.dumps(body).encode(), h)
        try:
            with urllib.request.urlopen(q, timeout=20) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def test_page_list_open_reuse(self):
        c, page = 200, urllib.request.urlopen(f'http://127.0.0.1:{self.port}/').read().decode()
        self.assertIn(f"const TOKEN='{HM.TOKEN}'", page); self.assertIn('新建片子', page)
        fid, job = self.film('打开试试'); self.jobs.append(job)
        c, r = self.req('/api/films'); self.assertEqual([x['id'] for x in r['films']], [fid])
        c, r = self.req('/api/open', {'id': fid}); self.assertTrue(r['ok'], r)
        port = int(r['url'].rsplit(':', 1)[1].strip('/')); self.assertIn(port, self.ports, '起在首页给的端口范围里')
        pg = W.ping(port); self.assertEqual(pg['dir'], str(job.dir)); self.assertTrue(pg['lite'])
        c, r2 = self.req('/api/open', {'id': fid}); self.assertEqual(r2['url'], r['url'], '再点同一条：复用，不起第二个')
        self.assertTrue(HM.status(self.root, fid)['open'])
        c, r = self.req('/api/open', {'id': '../../etc'}); self.assertFalse(r['ok'])
        c, r = self.req('/api/ai'); self.assertEqual((r['all'], r['films']), (False, []))
        urllib.request.urlopen(f'http://127.0.0.1:{port}/api/wait').read()       # 这条片子的 --wait 来问过
        c, r = self.req('/api/ai'); self.assertEqual(r['films'], [fid], '首页看得到 AI 在等哪条片子')
        urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/wait-all').read()
        c, r = self.req('/api/ai'); self.assertTrue(r['all'])

    def test_new_from_page(self):
        c, r = self.req('/api/new', {'title': '新的一条'}); self.assertTrue(r['ok'], r)
        self.jobs.append(W.open_film(self.root / r['id']))
        self.assertTrue((self.root / r['id'] / '项目.json').is_file()); self.assertTrue(r['url'].startswith('http://127.0.0.1:'))

    def test_cli_cannot_click(self):
        for path in ('/api/new', '/api/open'):
            c, r = self.req(path, {'title': 'x', 'id': 'x'}, page=False); self.assertEqual(c, 403, path)
            c, r = self.req(path, {'title': 'x', 'id': 'x'}, origin='http://evil.example'); self.assertEqual(c, 403, path)
        self.assertEqual(HM.scan(self.root), [], '没页面口令的请求什么都没建')
        q = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/films', headers={'Host': 'evil.example:80'})
        with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(q)


class MaterialTests(Base):
    def setUp(self):
        super().setUp(); self.fid, self.job = self.film('备料'); self.dst = self.root / self.fid / '01_原始素材'
        self.srv = W.make_server(self.job, 0); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); super().tearDown()

    def upload(self, name, data, page=True, length=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        h = {'Content-Length': str(len(data) if length is None else length)}
        if page: h.update({'Origin': f'http://127.0.0.1:{self.port}', 'X-Shenpian-Token': W.TOKEN})
        from urllib.parse import quote
        c.putrequest('POST', '/api/material/upload?name=' + quote(name))
        for k, v in h.items(): c.putheader(k, v)
        c.endheaders()
        try:
            for i in range(0, len(data), 1 << 20): c.send(data[i:i + (1 << 20)])
            if length is not None and length > len(data): c.sock.shutdown(socket.SHUT_WR)
            r = c.getresponse(); return r.status, json.loads(r.read())
        except (ConnectionError, http.client.HTTPException): return None, {}
        finally: c.close()

    def test_drag_in(self):
        c, r = self.upload('口播 第1条.mp4', b'a' * 1000); self.assertTrue(r['ok'], r)
        self.assertEqual((self.dst / '口播 第1条.mp4').read_bytes(), b'a' * 1000); self.assertEqual(r['file']['path'], '01_原始素材/口播 第1条.mp4')
        c, r = self.upload('口播 第1条.mp4', b'b' * 10)
        self.assertEqual(r['file']['path'], '01_原始素材/口播 第1条 (2).mp4', '同名不覆盖'); self.assertEqual((self.dst / '口播 第1条.mp4').read_bytes(), b'a' * 1000)
        big = os.urandom(1 << 20) * 40                                   # 40MB：分块收，内容一字不差
        c, r = self.upload('B roll/../大文件.mov', big); self.assertTrue(r['ok'], r)
        self.assertEqual(r['file']['name'], '大文件.mov', '只取文件名，不认路径'); self.assertEqual((self.dst / '大文件.mov').stat().st_size, len(big))
        self.assertEqual((self.dst / '大文件.mov').read_bytes()[-4096:], big[-4096:])
        info = M.info(self.job); self.assertEqual([x['name'] for x in info['files']], ['口播 第1条.mp4', '口播 第1条.mp4', '大文件.mov'])
        self.assertTrue(all(x['exists'] for x in info['files'])); self.assertEqual(info['active'], [])
        c, r = self.upload('半截.mp4', b'x' * 1000, length=5000)          # 传到一半断了
        self.assertFalse((self.dst / '半截.mp4').exists()); self.assertEqual([p.name for p in self.dst.iterdir() if '接收中' in p.name], [], '半截删掉')
        self.assertEqual(len(M.info(self.job)['failed']), 1)
        self.assertIn('大文件.mov', '\n'.join(M.summary(self.job)), '开工时 AI 读得到收到了哪些')

    def test_upload_only_from_page(self):
        c, r = self.upload('偷偷放.mp4', b'x' * 10, page=False); self.assertEqual(c, 403)
        self.assertFalse((self.dst / '偷偷放.mp4').exists())
        q = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/material/paths', json.dumps({'text': '/etc/hosts'}).encode(), {'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(q)
        self.assertEqual(e.exception.code, 403)

    def test_paths(self):
        src = self.root.parent / '外面'; (src / '一组B roll').mkdir(parents=True)
        (src / '一组B roll' / '1.mp4').write_bytes(b'1' * 3000); (src / '一组B roll' / '.DS_Store').write_bytes(b'x'); (src / '口播.mov').write_bytes(b'k' * 5000)
        (self.dst / '口播.mov').write_bytes(b'old')
        r = M.add_paths(self.job, f'{src / "口播.mov"}\n"{src / "一组B roll"}"\n相对路径.mp4\n/没有这个/文件.mp4\n{self.dst}', wait=True)
        self.assertEqual([x['name'] for x in r['started']], ['口播.mov', '一组B roll']); self.assertEqual(len(r['errors']), 3)
        self.assertEqual((self.dst / '口播.mov').read_bytes(), b'old', '同名不覆盖'); self.assertEqual((self.dst / '口播 (2).mov').read_bytes(), b'k' * 5000)
        self.assertEqual((self.dst / '一组B roll' / '1.mp4').read_bytes(), b'1' * 3000); self.assertFalse((self.dst / '一组B roll' / '.DS_Store').exists())
        self.assertTrue((src / '口播.mov').exists(), '原文件不动')
        files = M.info(self.job)['files']; self.assertEqual({x['how'] for x in files}, {'登记路径'}); self.assertEqual(files[1]['size'], 3000)
        with self.assertRaises(M.Oops): M.add_paths(self.job, '  ')


OLD = """
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
d, port = sys.argv[1], int(sys.argv[2])
class H(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path == '/api/ping':
            b = json.dumps({'app': '审片工作台', 'dir': d, 'pid': 0, 'lite': True}).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b); return
        self.close_connection = True          # 老服务的页面文件被删掉了：打开页面没有任何回应（2026-11-24 架构审查复现的空白）
HTTPServer(('127.0.0.1', port), H).serve_forever()
"""


class OldServerTests(Base):
    """首页复用审片台前先认版本（2026-11-24 架构审查验收：发版前的老服务、从删掉的 worktree 起的服务，内嵌进来一片空白）。"""
    def test_old_program_is_replaced(self):
        import subprocess
        fid, job = self.film('老服务'); self.jobs.append(job); old_port = self.ports[-1]
        W.edit(job, lambda d: None)
        p = subprocess.Popen([sys.executable, '-c', OLD, str(job.dir), str(old_port)])
        try:
            for _ in range(50):
                if W.ping(old_port, 0.2): break
                time.sleep(0.1)
            wj(W.home(job) / '服务.json', {'pid': p.pid, 'port': old_port, 'url': f'http://127.0.0.1:{old_port}/', 'dir': str(job.dir), 'started': 'x'})
            self.assertFalse(W.same_program(old_port), '老服务没有版本标记，算不同版')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(W.start(job, ports=self.ports)[0], f'http://127.0.0.1:{old_port}/', 'workbench 命令照旧复用，不替用户重起')
                url = HM.ensure(job, self.ports)
            port = int(url.rsplit(':', 1)[1].strip('/'))
            self.assertTrue(W.same_program(port), '首页换成了现在的程序起的审片台'); self.assertEqual(p.wait(5), -15, '老服务被停掉了')
            with urllib.request.urlopen(url, timeout=5) as r: self.assertIn('全部片子', r.read().decode(), '页面打得开，有“← 全部片子”')
            with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(HM.ensure(job, self.ports), url, '同一版就复用')
        finally:
            if p.poll() is None: p.kill()

    def test_lite_format_matches_init(self):
        fid, job = self.film('画幅'); self.assertEqual(job.get('format'), '3:4满屏')


class WaitAllTests(Base):
    def test_wait_all(self):
        srv = HM.make_server(self.root, 0, self.ports); hp = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            a, ja = self.film('第一条'); b, jb = self.film('第二条'); self.jobs += [ja, jb]
            for f in (a, b): HM.make_handler  # noqa
            urls = [json.loads(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{hp}/api/open', json.dumps({'id': f}).encode(),
                    {'Origin': f'http://127.0.0.1:{hp}', 'X-Shenpian-Token': HM.TOKEN}), timeout=20).read())['url'] for f in (a, b)]
            out = io.StringIO()
            with contextlib.redirect_stdout(out): self.assertIsNone(HM.wait_all(self.root, 1, hp), '没人点：到时间退出')
            self.assertIn('哪条片子都还没人点', out.getvalue())
            self.assertTrue(W.ping(int(urls[1].rsplit(':', 1)[1].strip('/')))['listening'], '等所有片子时，每条片子的审片台都显示“AI 在等”')
            writing.note_add(jb, {'text': '先写一版'}); board.send(jb, '写稿')
            out = io.StringIO()
            with contextlib.redirect_stdout(out): got = HM.wait_all(self.root, 10, hp)
            t = out.getvalue()
            self.assertEqual(got.dir, jb.dir); self.assertIn(f'【片子】{b}', t); self.assertIn('栏目：文案，1 件', t); self.assertIn('home --wait', t)
            self.assertEqual(board.pick(jb), [], '接过了，不会再打印一次')
            out = io.StringIO()
            with contextlib.redirect_stdout(out): self.assertIsNone(HM.wait_all(self.root, 1, hp))
            other = Path(tempfile.mkdtemp())
            with contextlib.redirect_stdout(io.StringIO()): self.assertIsNone(HM.wait_all(other, 1, hp), '只认放片子的文件夹里的片子')
            shutil.rmtree(other)
        finally: srv.shutdown(); srv.server_close()

    def test_single_wait_still_works(self):
        a, ja = self.film('单片'); self.jobs.append(ja)
        HM.ensure(ja, self.ports); writing.note_add(ja, {'text': '改改'}); board.send(ja, '写稿')
        out = io.StringIO()
        with contextlib.redirect_stdout(out): W.wait(ja, 5)
        self.assertIn('【用户点了“发给 AI”】栏目：文案', out.getvalue()); self.assertIn('再起一次 --wait', out.getvalue())


class ManageTests(Base):
    """2026-11-24 方向 C：右键改名、移到“做完的”／移回来、移到 NAS 回收站（能拿回来）、封面小图、点开先到 /open 再跳。达芬奇库列表指到临时文件夹，不碰真的。"""
    def setUp(self):
        super().setUp()
        self.rl = board._pm('resolve_library'); self.old_prefs = self.rl.PREFS
        self.rl.PREFS = self.root.parent / '达芬奇设置'; self.rl.PREFS.mkdir()
        self.bin = self.root.parent / '#recycle'; self.bin.mkdir(); os.environ['THINKCUT_RECYCLE_ROOT'] = str(self.bin)
        self.srv = HM.make_server(self.root, 0, self.ports); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); os.environ.pop('THINKCUT_RECYCLE_ROOT', None); self.rl.PREFS = self.old_prefs; super().tearDown()

    req = ServerTests.req

    def test_rename(self):
        fid, job = self.film('旧题目'); self.jobs.append(job); HM.ensure(job, self.ports); old_port = HM.server_of(job)['port']
        (self.root / fid / '03_成片' / f'{fid}.md').write_text('# x', encoding='utf-8')
        c, r = self.req('/api/rename', {'id': fid, 'title': '新题目: 好/的'}); self.assertTrue(r['ok'], r)
        self.assertEqual(r['id'], fid[:6] + ' 新题目 好的', '6 位编号＋空格＋标题，去掉路径符号')
        d = self.root / r['id']; self.assertTrue(d.is_dir()); self.assertFalse((self.root / fid).exists())
        self.assertEqual(rj(d / '项目.json')['name'], r['id']); self.assertTrue((d / '03_成片' / f"{r['id']}.md").is_file(), '文案文件名跟着改')
        self.assertIsNone(W.ping(old_port, 0.3), '先停掉了在跑的审片台')
        self.assertFalse((self.rl.PREFS / 'dblist.conf').exists(), '没登记过达芬奇库的不碰库列表')
        self.assertEqual(next(x for x in HM.scan(self.root) if x['id'] == r['id'])['title'], '新题目 好的', '片名跟文件夹名走')
        for bad in ('', '新题目 好的'):
            c, r2 = self.req('/api/rename', {'id': r['id'], 'title': bad}); self.assertFalse(r2['ok'])

    def test_rename_needs_ok(self):
        fid, job = self.film('有工程')
        wj(self.root / fid / '02_制作' / '自动剪辑' / 'job.json', {'fps': '30000/1001', 'format': '3:4', 'frames': 300, 'project': 'x'})
        c, r = self.req('/api/rename', {'id': fid, 'title': '改一下'})
        self.assertFalse(r['ok']); self.assertTrue(r.get('need_ok')); self.assertIn('剪辑工程', r['error']); self.assertTrue((self.root / fid).is_dir(), '没点头不改')
        c, r = self.req('/api/rename', {'id': fid, 'title': '改一下', 'user_ok': True}); self.assertTrue(r['ok'], r)

    def test_finish_and_back(self):
        fid, job = self.film('做完它')
        c, r = self.req('/api/finish', {'id': fid}); self.assertTrue(r['ok'], r); self.assertEqual(r['id'], f'finish/{fid}')
        self.assertTrue(next(x for x in HM.scan(self.root) if x['id'] == r['id'])['done'])
        self.assertFalse((self.rl.PREFS / 'dblist.conf').exists(), '没登记过达芬奇库的不碰库列表')
        c, r2 = self.req('/api/finish', {'id': r['id']}); self.assertFalse(r2['ok'], '已经在做完的里')
        c, r3 = self.req('/api/finish', {'id': r['id'], 'back': True}); self.assertTrue(r3['ok'], r3); self.assertEqual(r3['id'], fid)

    def test_trash_and_restore(self):
        fid, job = self.film('删掉它'); self.jobs.append(job); HM.ensure(job, self.ports); port = HM.server_of(job)['port']
        (self.root / fid / '01_原始素材' / 'a.txt').write_text('留着', encoding='utf-8')
        c, r = self.req('/api/trash', {'id': fid}); self.assertTrue(r['ok'], r)
        to = Path(r['to']); self.assertEqual(to.parent, self.bin / '口播台'); self.assertRegex(to.name, '^' + fid + r'_\d{8}-\d{6}$')
        self.assertFalse((self.root / fid).exists()); self.assertEqual((to / '01_原始素材' / 'a.txt').read_text(encoding='utf-8'), '留着', '整个文件夹原样挪过去')
        self.assertIsNone(W.ping(port, 0.3), '先停掉了在跑的审片台'); self.assertEqual(HM.scan(self.root), [])
        c, b = self.req('/api/restore', {'to': '/etc', 'from': fid}); self.assertFalse(b['ok'], '只认回收站 口播台/ 里的')
        c, b = self.req('/api/restore', {'to': r['to'], 'from': '别的名字'}); self.assertFalse(b['ok'], '名字对不上原位置不动')
        c, b = self.req('/api/restore', {'to': r['to'], 'from': r['from']}); self.assertTrue(b['ok'], b)
        self.assertEqual((self.root / fid / '01_原始素材' / 'a.txt').read_text(encoding='utf-8'), '留着'); self.assertFalse(to.exists())

    def test_trash_needs_recycle(self):
        fid, _ = self.film('回收站不在'); shutil.rmtree(self.bin)
        c, r = self.req('/api/trash', {'id': fid}); self.assertFalse(r['ok']); self.assertIn('回收站', r['error']); self.assertTrue((self.root / fid).is_dir(), '回收站不在就不删')
        os.environ.pop('THINKCUT_RECYCLE_ROOT'); self.assertIsNone(HM.recycle_root(Path('/tmp/x/y')), '不在 /Volumes 下没有 NAS 回收站')
        self.assertEqual(HM.recycle_root(Path('/Volumes/work/某IP视频/261124 x')), Path('/Volumes/work/#recycle'))

    def test_registered_library_follows(self):
        fid, _ = self.film('登记过'); d = (self.root / fid).resolve()
        lst = self.rl.PREFS / 'dblist.conf'; lst.write_text(f'别的:/x::::DISK\n{fid}:{self.rl.folder(d)}::::DISK\n', encoding='utf-8')
        self.assertTrue(HM.in_library(d))
        running = self.rl.resolve_running; self.rl.resolve_running = lambda: False
        try:
            c, r = self.req('/api/trash', {'id': fid}); self.assertTrue(r['ok'], r)
            self.assertNotIn(fid, lst.read_text(encoding='utf-8'), '删了的片子从达芬奇库列表里去掉'); self.assertIn('别的:/x', lst.read_text(encoding='utf-8'))
            self.rl.resolve_running = lambda: True
            g, _ = self.film('达芬奇开着'); lst.write_text(f'{g}:{self.rl.folder((self.root / g).resolve())}::::DISK\n', encoding='utf-8')
            c, r = self.req('/api/trash', {'id': g}); self.assertFalse(r['ok']); self.assertIn('达芬奇', r['error']); self.assertTrue((self.root / g).is_dir())
        finally: self.rl.resolve_running = running

    def test_cover_and_open_redirect(self):
        fid, job = self.film('有封面'); self.jobs.append(job); d = self.root / fid
        self.assertEqual(HM.status(self.root, fid)['cover'], 0)
        c, _ = self.req('/api/cover?id=' + urllib.parse.quote(fid)); self.assertEqual(c, 404)
        a = d / '02_制作' / '封面制作' / '底图方向' / 'A_x' / '封面_x_3x4.jpg'; a.parent.mkdir(parents=True); a.write_bytes(b'A')
        (d / '02_制作' / '封面制作' / '底图方向' / 'A_x' / '封面_x_16x9.jpg').write_bytes(b'W')
        time.sleep(0.05); b = d / '03_成片' / '封面' / '封面_3x4_1.jpg'; b.parent.mkdir(parents=True, exist_ok=True); b.write_bytes(b'B')
        self.assertEqual(HM.cover_of(d), b, '最新的那张 3:4'); self.assertGreater(HM.status(self.root, fid)['cover'], 0)
        self.assertEqual(urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/cover?id=' + urllib.parse.quote(fid)).read(), b'B')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a): return None
        op = urllib.request.build_opener(NoRedirect)
        q = f'http://127.0.0.1:{self.port}/open?id=' + urllib.parse.quote(fid)
        with self.assertRaises(urllib.error.HTTPError) as e: op.open(q)
        self.assertEqual(e.exception.code, 403, '没有页面口令不开')
        with self.assertRaises(urllib.error.HTTPError) as e: op.open(q + '&t=' + HM.TOKEN + '&col=' + urllib.parse.quote('写稿'))
        loc = e.exception.headers['Location']; self.assertEqual(e.exception.code, 302)
        self.assertRegex(loc, r'^http://127\.0\.0\.1:\d+/\?home=http%3A%2F%2F127\.0\.0\.1%3A\d+#%E5%86%99%E7%A8%BF$')
        self.assertEqual(HM.status(self.root, fid)['url'], loc.split('?')[0], '审片台起好了，下次页面直接开它')
        with self.assertRaises(urllib.error.HTTPError) as e: op.open(f'http://127.0.0.1:{self.port}/open?new=&t={HM.TOKEN}')
        new = [x for x in HM.scan(self.root) if x['id'].endswith(' 未命名')]; self.assertEqual(len(new), 1, '/open?new= 建一条新片子'); self.jobs.append(W.open_film(self.root / new[0]['id']))

    def test_cli_cannot_manage(self):
        fid, _ = self.film('别动它')
        for path in ('/api/rename', '/api/finish', '/api/trash', '/api/restore', '/api/reveal'):
            c, r = self.req(path, {'id': fid, 'title': 'x'}, page=False); self.assertEqual(c, 403, path)
        self.assertEqual([x['id'] for x in HM.scan(self.root)], [fid]); self.assertEqual(list(self.bin.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
