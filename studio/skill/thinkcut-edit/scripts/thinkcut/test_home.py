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
        if page: h.update({'Origin': origin or f'http://127.0.0.1:{self.port}', 'Cookie': HM.cookie_header(self.port)})
        q = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', None if body is None else json.dumps(body).encode(), h)
        try:
            with urllib.request.urlopen(q, timeout=20) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def test_page_list_open_reuse(self):
        c, page = 200, urllib.request.urlopen(f'http://127.0.0.1:{self.port}/').read().decode()
        self.assertIn(f"const TOKEN='{HM.SESSION}'", page); self.assertNotIn(HM.TOKEN, page); self.assertIn('新建项目', page)   # 页面里只有公开的编号，口令在 HttpOnly cookie
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
        if page: h.update({'Origin': f'http://127.0.0.1:{self.port}', 'Cookie': W.cookie_header(self.port)})
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
            with urllib.request.urlopen(url, timeout=5) as r: self.assertIn('全部项目', r.read().decode(), '页面打得开，有“全部项目”')
            with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(HM.ensure(job, self.ports), url, '同一版就复用')
        finally:
            if p.poll() is None: p.kill()

    def test_lite_format_matches_init(self):
        fid, job = self.film('画幅'); self.assertEqual(job.get('format'), '3:4满屏')


class IPTests(Base):
    """切 IP（2026-11-24 用户定）：左上角列出所有 IP、点一个看它的片子；新建 IP 只填名字；新片子归当前 IP；记住上次选的（本机.json）；--root 钉死不能切；home --wait 看所有 IP。
    用临时配置档（甲是默认 IP、乙），片子文件夹都在临时目录，不碰真配置档和真片子。"""
    def setUp(self):
        super().setUp()
        cc = HM.cfg(); self.prof = self.root.parent / 'profiles'; self.prof.mkdir()
        pub = cc._read(Path(cc.PROGRAM) / 'profiles' / '公共.json'); pub.update({'默认IP': '甲', '片子文件夹': str(self.root.parent / '{IP}视频'), '回收站': str(self.root.parent / '回收站')}); (self.root.parent / '回收站').mkdir()
        (self.prof / '公共.json').write_text(json.dumps(pub, ensure_ascii=False), encoding='utf-8')
        for n in ('甲', '乙'): (self.prof / n).mkdir(); (self.prof / n / 'ip.json').write_text(json.dumps({'名字': n}, ensure_ascii=False), encoding='utf-8')
        (self.prof / '本机.json').write_text(json.dumps({'_说明': '这台机器的'}, ensure_ascii=False), encoding='utf-8')
        self.old = {k: os.environ.get(k) for k in ('THINKCUT_PROFILES', 'THINKCUT_IP', 'THINKCUT_VIDEO_ROOT', 'THINKCUT_RECYCLE_ROOT', 'THINKCUT_APP_FLAVOR')}
        os.environ['THINKCUT_APP_FLAVOR'] = 'release'      # 按正式版读配置：开发版直接跑这个测试时，不会把片子文件夹换成真的试验项目、往里建片子（2026-11-26 界面精修查出）
        os.environ['THINKCUT_PROFILES'] = str(self.prof); os.environ['THINKCUT_RECYCLE_ROOT'] = str(self.root.parent / '回收站'); os.environ.pop('THINKCUT_IP', None); os.environ.pop('THINKCUT_VIDEO_ROOT', None)
        self.srvs = []

    def tearDown(self):
        for s in self.srvs: s.shutdown(); s.server_close()
        for k, v in self.old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        super().tearDown()

    def serve(self, root=None):
        s = HM.make_server(root, 0, self.ports); self.srvs.append(s); threading.Thread(target=s.serve_forever, daemon=True).start(); return s.server_address[1]

    def req(self, port, path, body=None, page=True):
        h = {'Content-Type': 'application/json'}
        if page: h.update({'Origin': f'http://127.0.0.1:{port}', 'Cookie': HM.cookie_header(port)})
        q = urllib.request.Request(f'http://127.0.0.1:{port}{path}', None if body is None else json.dumps(body).encode(), h)
        try:
            with urllib.request.urlopen(q, timeout=20) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def local(self): return json.loads((self.prof / '本机.json').read_text(encoding='utf-8'))

    def new(self, port, title, ip):
        c, r = self.req(port, '/api/new', {'title': title, 'ip': ip}); self.assertTrue(r['ok'], r)
        self.jobs.append(W.open_film(self.root.parent / f'{ip}视频' / r['id'])); return r['id']

    def test_switch_new_film_and_remember(self):
        jia, yi = (self.root.parent / '甲视频').resolve(), (self.root.parent / '乙视频').resolve()
        p = self.serve()
        c, r = self.req(p, '/api/ips'); self.assertEqual((r['current'], r['ips'], r['pinned']), ('甲', ['乙', '甲'], False), '没选过：默认 IP；列出所有 IP')
        c, r = self.req(p, '/api/films'); self.assertEqual((r['ip'], r['root'], r['exists'], r['films']), ('甲', str(jia), False, []), '片子文件夹还没有：空列表，不报错')
        a = self.new(p, '甲的片子', '甲')
        self.assertEqual(rj(jia / a / '项目.json')['ip'], '甲', '新片子归当前 IP')
        c, r = self.req(p, '/api/ip', {'name': '乙'}); self.assertTrue(r['ok'], r); self.assertEqual((r['ip'], r['root']), ('乙', str(yi)))
        self.assertEqual(self.local(), {'_说明': '这台机器的', '首页IP': '乙'}, '记在 本机.json，别的键留着')
        c, r = self.req(p, '/api/films'); self.assertEqual((r['ip'], r['films']), ('乙', []), '切过去只看乙的片子')
        b = self.new(p, '乙的片子', '乙')
        self.assertEqual(rj(yi / b / '项目.json')['ip'], '乙'); self.assertFalse((jia / b).exists(), '不串到甲的文件夹')
        c, r = self.req(p, '/api/films'); self.assertEqual([x['id'] for x in r['films']], [b])
        c, r = self.req(p, '/api/rename', {'id': b, 'title': '改个名', 'ip': '甲'}); self.assertFalse(r['ok'], '页面还停在甲、首页已经切到乙：不动'); self.assertIn('乙', r['error'])
        c, r = self.req(p, '/api/ip', {'name': '没有这个'}); self.assertFalse(r['ok'])
        c, r = self.req(p, '/api/ip', {'name': '甲'}, page=False); self.assertEqual(c, 403, '命令行不能替用户切')
        c, r = self.req(p, '/api/ips'); self.assertEqual(r['current'], '乙')
        p2 = self.serve(); c, r = self.req(p2, '/api/ips'); self.assertEqual(r['current'], '乙', '下次打开还是上次选的')
        c, r = self.req(p2, '/api/ping'); self.assertEqual((r['ip'], r['pin']), ('乙', ''))
        shutil.rmtree(self.prof / '乙'); p3 = self.serve(); c, r = self.req(p3, '/api/ips'); self.assertEqual(r['current'], '甲', '记的 IP 没了：回默认')

    def test_new_ip(self):
        p = self.serve()
        c, r = self.req(p, '/api/ip-new', {'name': '丙'}); self.assertTrue(r['ok'], r); self.assertEqual(r['ip'], '丙', '建好自动切过去')
        raw = json.loads((self.prof / '丙' / 'ip.json').read_text(encoding='utf-8'))
        self.assertEqual({k: v for k, v in raw.items() if not k.startswith('_')}, {'名字': '丙'}, '只写名字，其余跟公共层')
        self.assertEqual(self.local()['首页IP'], '丙')
        c, r = self.req(p, '/api/ips'); self.assertEqual((r['current'], r['ips']), ('丙', ['丙', '乙', '甲']))
        f = self.new(p, '丙的第一条', '丙'); self.assertEqual(rj(self.root.parent / '丙视频' / f / '项目.json')['ip'], '丙')
        for bad in ('丙', '甲', '', 'a/b', '.x'):
            c, r = self.req(p, '/api/ip-new', {'name': bad}); self.assertFalse(r['ok'], bad)
        c, r = self.req(p, '/api/ip-new', {'name': '丁'}, page=False); self.assertEqual(c, 403); self.assertFalse((self.prof / '丁').exists())

    def test_started_server_follows_ip(self):
        """home 命令起的后台首页（不带 --root）：跟着 IP 走，不是钉死在当前文件夹。"""
        port = next(p for p in range(19900, 20000) if W.port_free(p))
        with contextlib.redirect_stdout(io.StringIO()): HM.start(None, port)
        try:
            pg = HM.ping(port); self.assertEqual((pg['pin'], pg['ip'], pg['root']), ('', '甲', str((self.root.parent / '甲视频').resolve())))
            with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(HM.start(None, port)[1], True, '再起：复用')
            with self.assertRaises(SystemExit): HM.start(self.root, port)
        finally:
            with contextlib.redirect_stdout(io.StringIO()): HM.stop(port)

    def test_delete_ip(self):
        """删除 IP…：配置挪回收站、片子不动、能撤销；默认 IP 不能删；删的是正在看的就回默认 IP；命令行不能替用户删。"""
        p = self.serve(); self.req(p, '/api/ip', {'name': '乙'}); b = self.new(p, '乙的片子', '乙')
        c, r = self.req(p, '/api/ips'); self.assertEqual(r['default'], '甲')
        c, r = self.req(p, '/api/ip-info?name=' + urllib.parse.quote('乙')); self.assertEqual((r['films'], r['root']), (1, str((self.root.parent / '乙视频').resolve())))
        c, r = self.req(p, '/api/ip-del', {'name': '乙'}, page=False); self.assertEqual(c, 403); self.assertTrue((self.prof / '乙').is_dir())
        c, r = self.req(p, '/api/ip-del', {'name': '甲'}); self.assertFalse(r['ok'], '默认 IP 不能删'); self.assertIn('默认', r['error'])
        c, r = self.req(p, '/api/ip-del', {'name': '乙'}); self.assertTrue(r['ok'], r)
        self.assertEqual(r['ip'], '甲', '删的是正在看的：回默认 IP'); self.assertFalse((self.prof / '乙').exists())
        self.assertTrue(Path(r['to']).is_relative_to(self.root.parent / '回收站')); self.assertTrue((self.root.parent / '乙视频' / b / '项目.json').is_file(), '片子不动')
        self.assertNotIn('首页IP', self.local())
        c, r2 = self.req(p, '/api/ips'); self.assertEqual(r2['ips'], ['甲'])
        c, u = self.req(p, '/api/ip-undel', {'to': r['to']}); self.assertTrue(u['ok'], u); self.assertTrue((self.prof / '乙' / 'ip.json').is_file(), '撤销：还原')
        self.req(p, '/api/ip', {'name': '乙'}); c, f = self.req(p, '/api/films'); self.assertEqual([x['id'] for x in f['films']], [b], '还原以后片子照旧看得到')
        p2 = self.serve(self.root); c, r = self.req(p2, '/api/ip-del', {'name': '乙'}); self.assertFalse(r['ok'], '--root 钉死时不能删')

    def test_recycle_bin(self):
        """回收站…：列出删掉的片子（带它属于哪个 IP）和 IP；还原放回原处；片子的 IP 也删了就先要还原 IP；命令行不能替用户拿。"""
        p = self.serve(); c, r = self.req(p, '/api/bin'); self.assertEqual(r['items'], [], '空的')
        a = self.new(p, '甲的片子', '甲'); self.req(p, '/api/ip', {'name': '乙'}); b = self.new(p, '乙的片子', '乙')
        c, t = self.req(p, '/api/trash', {'id': b, 'ip': '乙'}); self.assertTrue(t['ok'], t)
        self.req(p, '/api/ip', {'name': '甲'}); c, t2 = self.req(p, '/api/trash', {'id': a, 'ip': '甲'}); self.assertTrue(t2['ok'], t2)
        c, d = self.req(p, '/api/ip-del', {'name': '乙'}); self.assertTrue(d['ok'], d)
        c, r = self.req(p, '/api/bin'); its = r['items']
        self.assertEqual(sorted((x['kind'], x['name'], x['ip']) for x in its), sorted([('film', a, '甲'), ('film', b, '乙'), ('ip', '乙', '乙')]))
        self.assertTrue(all(its[i]['at'] >= its[i + 1]['at'] for i in range(len(its) - 1)), '新删的在前')
        fb = next(x for x in its if x['name'] == b); fi = next(x for x in its if x['kind'] == 'ip')
        c, r = self.req(p, '/api/bin-restore', {'to': fb['to'], 'kind': 'film'}); self.assertFalse(r['ok'], '片子的 IP 也删了'); self.assertIn('先把这个 IP 还原', r['error'])
        c, r = self.req(p, '/api/bin-restore', {'to': fi['to'], 'kind': 'ip'}, page=False); self.assertEqual(c, 403)
        c, r = self.req(p, '/api/bin-restore', {'to': fi['to'], 'kind': 'ip'}); self.assertTrue(r['ok'], r); self.assertIn('乙', HM.cfg().ips())
        c, r = self.req(p, '/api/bin-restore', {'to': fb['to'], 'kind': 'film'}); self.assertTrue(r['ok'], r)
        self.assertEqual(r['ip'], '乙'); self.assertTrue((self.root.parent / '乙视频' / b / '项目.json').is_file(), '回到它那个 IP 的文件夹，不是当前看的甲')
        c, r = self.req(p, '/api/films'); self.assertEqual(r['films'], [], '首页还在看甲')
        fa = next(x for x in its if x['name'] == a); c, r = self.req(p, '/api/bin-restore', {'to': fa['to'], 'kind': 'film'}); self.assertTrue(r['ok'], r)
        c, r = self.req(p, '/api/films'); self.assertEqual([x['id'] for x in r['films']], [a])
        c, r = self.req(p, '/api/bin'); self.assertEqual(r['items'], [], '都已还原')
        c, r = self.req(p, '/api/bin-restore', {'to': '/etc/passwd_20260101-000000', 'kind': 'film'}); self.assertFalse(r['ok'])

    def test_pinned_root_unchanged(self):
        """给了 --root：只看这一个文件夹、不能切；新片子照旧归默认 IP（和以前一样）。"""
        p = self.serve(self.root)
        c, r = self.req(p, '/api/ips'); self.assertEqual((r['pinned'], r['current'], r['ips']), (True, '甲', ['甲']))
        c, r = self.req(p, '/api/ip', {'name': '乙'}); self.assertFalse(r['ok']); self.assertIn('--root', r['error'])
        c, r = self.req(p, '/api/ip-new', {'name': '丙'}); self.assertFalse(r['ok']); self.assertFalse((self.prof / '丙').exists())
        c, r = self.req(p, '/api/new', {'title': '钉死的'}); self.assertTrue(r['ok'], r); self.jobs.append(W.open_film(self.root / r['id']))
        self.assertEqual(rj(self.root / r['id'] / '项目.json')['ip'], '甲'); self.assertNotIn('首页IP', self.local())
        c, r = self.req(p, '/api/ping'); self.assertEqual(r['pin'], str(self.root.resolve()))

    def test_wait_all_sees_every_ip(self):
        """AI 的 home --wait（不带 --root）看所有 IP：首页切在甲，乙的片子点了“交给 AI”也接得到。"""
        p = self.serve(); self.req(p, '/api/ip', {'name': '乙'}); b = self.new(p, '乙要AI', '乙'); jb = self.jobs[-1]
        c, r = self.req(p, '/api/open', {'id': b}); self.assertTrue(r['ok'], r)
        self.req(p, '/api/ip', {'name': '甲'})
        self.assertEqual(HM.all_roots(), [(self.root.parent / n).resolve() for n in ('乙视频', '甲视频')])
        HM.ensure(jb, self.ports); writing.note_add(jb, {'text': '先写一版'}); board.send(jb, '写稿')
        out = io.StringIO()
        with contextlib.redirect_stdout(out): got = HM.wait_all([self.root.parent / '甲视频'], 1, p)
        self.assertIsNone(got, '只给甲的文件夹：看不到乙的')
        with contextlib.redirect_stdout(out): got = HM.wait_all(HM.all_roots(), 10, p)
        self.assertEqual(got.dir, jb.dir); self.assertIn('乙要AI', out.getvalue())


class WaitAllTests(Base):
    def test_wait_all(self):
        srv = HM.make_server(self.root, 0, self.ports); hp = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            a, ja = self.film('第一条'); b, jb = self.film('第二条'); self.jobs += [ja, jb]
            for f in (a, b): HM.make_handler  # noqa
            urls = [json.loads(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{hp}/api/open', json.dumps({'id': f}).encode(),
                    {'Origin': f'http://127.0.0.1:{hp}', 'Cookie': HM.cookie_header(hp)}), timeout=20).read())['url'] for f in (a, b)]
            out = io.StringIO()
            with contextlib.redirect_stdout(out): self.assertIsNone(HM.wait_all(self.root, 1, hp), '没人点：到时间退出')
            self.assertIn('哪个项目都还没人点', out.getvalue())
            self.assertTrue(W.ping(int(urls[1].rsplit(':', 1)[1].strip('/')))['listening'], '等所有片子时，每条片子的审片台都显示“AI 在等”')
            writing.note_add(jb, {'text': '先写一版'}); board.send(jb, '写稿')
            out = io.StringIO()
            with contextlib.redirect_stdout(out): got = HM.wait_all(self.root, 10, hp)
            t = out.getvalue()
            self.assertEqual(got.dir, jb.dir); self.assertIn(f'【项目】{b}', t); self.assertIn('栏目：文案，1 件', t); self.assertIn('home --wait', t)
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
        self.assertIn('【用户点了“提交 AI”】栏目：文案', out.getvalue()); self.assertIn('再起一次 --wait', out.getvalue())


class ManageTests(Base):
    """2026-11-24 方向 C：右键改名、移到“做完的”／移回来、移到 NAS 回收站（能还原）、封面小图、点开先到 /open 再跳。达芬奇库列表指到临时文件夹，不碰真的。"""
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
        to = Path(r['to']); self.assertEqual(to.parent, self.bin / 'ThinkCut'); self.assertRegex(to.name, '^' + fid + r'_\d{8}-\d{6}$')
        self.assertFalse((self.root / fid).exists()); self.assertEqual((to / '01_原始素材' / 'a.txt').read_text(encoding='utf-8'), '留着', '整个文件夹原样挪过去')
        self.assertIsNone(W.ping(port, 0.3), '先停掉了在跑的审片台'); self.assertEqual(HM.scan(self.root), [])
        c, b = self.req('/api/restore', {'to': '/etc', 'from': fid}); self.assertFalse(b['ok'], '只认回收站 ThinkCut/ 里的')
        c, b = self.req('/api/restore', {'to': r['to'], 'from': '别的名字'}); self.assertFalse(b['ok'], '名字对不上原位置不动')
        c, b = self.req('/api/restore', {'to': r['to'], 'from': r['from']}); self.assertTrue(b['ok'], b)
        self.assertEqual((self.root / fid / '01_原始素材' / 'a.txt').read_text(encoding='utf-8'), '留着'); self.assertFalse(to.exists())
        old = self.bin / '口播台'; old.mkdir(exist_ok=True)                 # 改名前删的片子在 #recycle/口播台/ 里：照样列得出、还原得回来
        c, r = self.req('/api/trash', {'id': fid}); os.rename(r['to'], old / Path(r['to']).name); src = old / Path(r['to']).name
        self.assertEqual([x['to'] for x in HM.recycled([self.root])], [str(src)])
        c, b = self.req('/api/restore', {'to': str(src), 'from': fid}); self.assertTrue(b['ok'], b); self.assertTrue((self.root / fid).is_dir()); self.assertTrue(old.is_dir(), '旧文件夹空了也留着')

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
        time.sleep(0.05); b = d / '03_成片' / 'cover' / '封面_3x4_1.jpg'; b.parent.mkdir(parents=True, exist_ok=True); b.write_bytes(b'B')
        self.assertEqual(HM.cover_of(d), b, '最新的那张 3:4'); self.assertGreater(HM.status(self.root, fid)['cover'], 0)
        self.assertEqual(urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/cover?id=' + urllib.parse.quote(fid)).read(), b'B')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a): return None
        op = urllib.request.build_opener(NoRedirect)
        q = f'http://127.0.0.1:{self.port}/open?id=' + urllib.parse.quote(fid)
        with self.assertRaises(urllib.error.HTTPError) as e: op.open(q + '&t=' + HM.SESSION)
        self.assertEqual((e.exception.code, e.exception.headers['Location']), (302, '/'), '没有页面的 cookie（首页重启过、命令行）不开：回首页重新打开（QA 2.6）')
        ck = {'Cookie': HM.cookie_header(self.port)}
        with self.assertRaises(urllib.error.HTTPError) as e: op.open(urllib.request.Request(q + '&t=' + HM.SESSION + '&col=' + urllib.parse.quote('写稿'), headers=ck))
        loc = e.exception.headers['Location']; self.assertEqual(e.exception.code, 302)
        self.assertRegex(loc, r'^http://127\.0\.0\.1:\d+/\?home=http%3A%2F%2F127\.0\.0\.1%3A\d+#%E5%86%99%E7%A8%BF$')
        self.assertEqual(HM.status(self.root, fid)['url'], loc.split('?')[0], '审片台起好了，下次页面直接开它')
        with self.assertRaises(urllib.error.HTTPError) as e: op.open(urllib.request.Request(f'http://127.0.0.1:{self.port}/open?new=&t={HM.SESSION}', headers=ck))
        new = [x for x in HM.scan(self.root) if x['id'].endswith(' 未命名')]; self.assertEqual(len(new), 1, '/open?new= 建一条新片子'); self.jobs.append(W.open_film(self.root / new[0]['id']))

    def test_cli_cannot_manage(self):
        fid, _ = self.film('别动它')
        for path in ('/api/rename', '/api/finish', '/api/trash', '/api/restore', '/api/reveal'):
            c, r = self.req(path, {'id': fid, 'title': 'x'}, page=False); self.assertEqual(c, 403, path)
        self.assertEqual([x['id'] for x in HM.scan(self.root)], [fid]); self.assertEqual(list(self.bin.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
