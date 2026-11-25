# -*- coding: utf-8 -*-
"""审片工作台（2026-11-21 别墅装灯反馈单 新9）离线测试：不连达芬奇，达芬奇用替身。run_tests.sh 会跑。
走一遍接口：打标记（截帧、正在说的那句）、写字、跳转、换轮次、旧轮次只读、这条还不行、删、AI 回写、导航条文字、导航底图；
达芬奇那层（工程不对、交付页自动切回剪辑页、别的页面、渲染中、达芬奇锁接入点）；起服务、复用、端口被占、停服务。"""
import fcntl, json, os, shutil, socket, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinkcut import workbench as W, resolve          # noqa: E402
from thinkcut.common import Job, wj, rj               # noqa: E402
from PIL import Image                             # noqa: E402
os.environ['THINKCUT_NO_OPEN'] = '1'                  # 测试不打开浏览器

S = Path(__file__).resolve().parents[1] / 'thinkcut_edit.py'
TEXT = ['第一句话说灯', '第二句话说顶', '第三句话收尾']


def film(d):
    wj(d / 'job.json', {'fps': '30000/1001', 'format': '3:4满屏', 'nav_h': 224, 'frames': 900, 'project': '测试工程', 'title': '测试片', 'review_timeline': 'v1_卡点审核'})
    tok, t = [], 0.0
    for u, s in enumerate(TEXT):
        for c in s: tok.append({'u': u, 'c': c, 't0': round(t, 2), 't1': round(t + 0.4, 2)}); t += 0.5
    wj(d / '口播字时间.json', {'note': '', 'fps': '30000/1001', 'tokens': tok})
    wj(d / '导航' / 'project.json', {'segments': [{'chapter': 'q', 'title': '问题：长标题', 'label': '问题', 'start': 0, 'end': 10},
                                                 {'chapter': 'q', 'title': '问题二', 'start': 10, 'end': 15},
                                                 {'chapter': 'why', 'title': '为什么', 'label': '为什么', 'start': 15, 'end': 30.03}]})
    return Job(d)


class FakeDV:
    """替身：和 workbench.DV 一样的三个方法。"""
    def __init__(s): s.t, s.err, s.goto_calls = 3.2, None, []
    def status(s):
        if s.err: raise W.Oops(s.err)
        return {'timeline': 'v1_卡点审核', 'page': 'edit', 'rendering': False, 't': s.t, 'dur': 30.03}
    def goto(s, t): s.goto_calls.append(t); s.t = t; return {'t': t}
    def still(s, out):
        if s.err: raise W.Oops(s.err)
        out.parent.mkdir(parents=True, exist_ok=True); Image.new('RGB', (30, 40), (200, 10, 10)).save(out); return out


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.job = film(self.tmp); self.dv = FakeDV()
        self.srv = W.make_server(self.job, 0, self.dv); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, p, raw=False):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}{p}') as r: b = r.read(); return (b, r.headers['Content-Type']) if raw else json.loads(b)

    def post(self, p, body=None, raw=False):
        q = urllib.request.Request(f'http://127.0.0.1:{self.port}{p}', json.dumps(body or {}).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(q) as r: b = r.read(); return (b, r.headers['Content-Type']) if raw else json.loads(b)

    def post_from(self, p, body=None, **headers):
        """带指定请求头发 POST，返回 (状态码, 回的内容)。"""
        q = urllib.request.Request(f'http://127.0.0.1:{self.port}{p}', json.dumps(body or {}).encode(), {'Content-Type': 'application/json', **headers})
        try:
            with urllib.request.urlopen(q) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def test_post_only_from_own_page(self):
        """2026-11-21 体检：POST 只接受本页面的来源；别的网站 403 且什么都没做；没有 Origin 的（curl、程序）照旧放行。"""
        for origin in (f'http://127.0.0.1:{self.port}', f'http://localhost:{self.port}'):        # 本页面来源放行
            code, out = self.post_from('/api/goto', {'t': 5.0}, Origin=origin); self.assertEqual((code, out['ok']), (200, True))
        self.assertEqual(self.dv.goto_calls, [5.0, 5.0])
        m = self.post('/api/mark'); self.assertTrue(m['ok'])                                      # 没有 Origin 放行（现有调用方）
        for origin in ('https://evil.example', 'http://127.0.0.1:1', f'http://evil.example:{self.port}', f'https://127.0.0.1:{self.port}', 'null'):   # 别的网站来源 403
            for path, body in (('/api/goto', {'t': 9.0}), ('/api/delete', {'id': m['id']})):
                code, out = self.post_from(path, body, Origin=origin); self.assertEqual((code, out['ok']), (403, False), origin); self.assertIn('来源', out['error'])
        self.assertEqual(self.dv.goto_calls, [5.0, 5.0]); self.assertEqual(len(W.load(self.job)['markers']), 1)   # 没跳转、标记没被删
        code, out = self.post_from('/api/goto', {'t': 9.0}, Host=f'evil.example:{self.port}')     # Host 不是本机（DNS 重绑定）403
        self.assertEqual(code, 403); self.assertEqual(self.dv.goto_calls, [5.0, 5.0])
        code, out = self.post_from('/api/goto', {'t': 7.0}, Host=f'localhost:{self.port}'); self.assertEqual(code, 200)
        self.assertEqual(self.get('/api/ping')['dir'], str(self.job.dir))                          # 只读的 GET 不受影响

    def test_get_checks_host(self):
        """2026-11-21 体检第二批：GET 也查 Host（防 DNS 重绑定：别的网站把自己的域名指到 127.0.0.1 来读标记、截图）。
        本机地址加本端口放行（浏览器打开页面、页面拿数据和图、程序自己 ping 都是这样），没有 Host 头的放行，别的 403。"""
        m = self.post('/api/mark'); self.assertTrue(m['ok']); img = '/' + urllib.request.quote(m['frame'])
        def get_from(path, host):
            q = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', headers={'Host': host})
            try:
                with urllib.request.urlopen(q) as r: return r.status, r.read()
            except urllib.error.HTTPError as e: return e.code, e.read()
        for path in ('/', '/index.html', '/api/ping', '/api/data', '/api/dv', img):
            for host in (f'127.0.0.1:{self.port}', f'localhost:{self.port}', f'LOCALHOST:{self.port}'):
                self.assertEqual(get_from(path, host)[0], 200, (path, host))
            for host in (f'evil.example:{self.port}', 'evil.example', f'127.0.0.1:{self.port + 1}', '127.0.0.1', f'127.0.0.1.evil.example:{self.port}'):
                code, body = get_from(path, host); self.assertEqual(code, 403, (path, host)); self.assertIn('本机', json.loads(body)['error'])
        self.assertEqual(self.get('/api/ping')['dir'], str(self.job.dir)); self.assertEqual(W.ping(self.port)['app'], '审片工作台')   # 程序自己的 ping（workbench.ping）照常
        b, kind = self.get(img, raw=True); self.assertTrue(kind.startswith('image/') and len(b) > 100)                       # 页面拿图照常
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as c:                                              # 没有 Host 头的请求（HTTP/1.0）照旧放行
            c.sendall(b'GET /api/ping HTTP/1.0\r\n\r\n'); data = b''
            while True:
                x = c.recv(4096)
                if not x: break
                data += x
        self.assertTrue(data.startswith(b'HTTP/1.0 200') or data.startswith(b'HTTP/1.1 200'), data[:40]); self.assertIn('审片工作台'.encode(), data)

    def test_page_and_js(self):
        html, kind = self.get('/', raw=True); html = html.decode()
        self.assertIn('<title>审片台</title>', html); self.assertIn('只看这一轮', html); self.assertIn('text/html', kind)
        self.assertNotRegex(html, r'(src|href)="https?://')                     # 不引外部 CDN
        js = self.tmp / 'page.js'; js.write_text(html.split('<script>')[1].split('</script>')[0], encoding='utf-8')
        if shutil.which('node'):
            r = subprocess.run(['node', '--check', str(js)], capture_output=True, text=True); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.get('/api/ping')['dir'], str(self.job.dir))

    def test_mark_flow_rounds_and_reply(self):
        s = self.get('/api/dv'); self.assertTrue(s['ok']); self.assertEqual(s['t'], 3.2)
        m = self.post('/api/mark')
        self.assertTrue(m['ok']); self.assertEqual((m['id'], m['round'], m['status'], m['t']), (1, 1, '待处理', 3.2))
        self.assertEqual(m['quote'], '第二句话说顶')                               # 3.2 秒正在说第二句
        self.assertTrue((W.home(self.job) / m['frame']).is_file())
        img, kind = self.get('/' + urllib.request.quote(m['frame']), raw=True); self.assertEqual(kind, 'image/jpeg'); self.assertGreater(len(img), 100)
        self.assertTrue(self.post('/api/text', {'id': 1, 'text': '灯画成壁灯了'})['ok'])
        self.assertTrue(self.post('/api/goto', {'t': 12.5})['ok']); self.assertEqual(self.dv.goto_calls, [12.5])
        W.reply(self.job, 1, '已改', '换成吊顶射灯')
        d = self.get('/api/data'); m1 = d['markers'][0]
        self.assertEqual((m1['text'], m1['status'], m1['reply']), ('灯画成壁灯了', '已改', '换成吊顶射灯'))
        self.assertEqual(d['nav'], [0, 0, 2160, 224]); self.assertEqual([c['id'] for c in d['chapters']], ['q', 'why']); self.assertEqual(d['chapters'][0]['b'], 15)
        self.post('/api/text', {'id': 1, 'text': '灯画成壁灯了，再亮一点'})
        self.assertEqual(rj(W.data_file(self.job))['markers'][0]['status'], '待处理')   # AI 回过以后用户又改字：重新等 AI
        W.set_round(self.job, 2); self.dv.t = 0.4
        m2 = self.post('/api/mark'); self.assertEqual((m2['id'], m2['round'], m2['quote']), (2, 2, '第一句话说灯'))
        e = self.post('/api/text', {'id': 1, 'text': 'x'}); self.assertFalse(e['ok']); self.assertIn('旧轮次', e['error'])
        self.assertFalse(self.post('/api/delete', {'id': 1})['ok'])
        a = self.post('/api/again', {'id': 1})
        self.assertEqual((a['id'], a['round'], a['of'], a['frame'], a['t'], a['status']), (3, 2, 1, m['frame'], 3.2, '待处理'))
        self.assertTrue(self.post('/api/delete', {'id': 3})['ok'])
        d = rj(W.data_file(self.job)); self.assertEqual([(x['id'], x['round']) for x in d['markers']], [(1, 1), (2, 2)]); self.assertEqual(d['round'], 2)
        with self.assertRaises(SystemExit): W.reply(self.job, 9, '已改', 'x')
        with self.assertRaises(SystemExit): W.reply(self.job, 2, '已改', '')

    def test_script_and_range_marks(self):
        """口播全文排成文章（照 261117 的审片台）：每字一个时间，按句补句号、按字幕换条补逗号；在全文上选一段字写要求。"""
        wj(self.job.p('合成', '字幕', '字幕表.json'), [{'n': 1, 'start': 0, 'end': 45, 'text': '第一句话'}, {'n': 2, 'start': 60, 'end': 90, 'text': '说灯'}])
        d = self.get('/api/data'); self.assertEqual(d['script_src'], '口播字时间')
        self.assertEqual(''.join(l['text'] for l in d['script']), '第一句话，说灯。第二句话说顶。第三句话收尾。')
        self.assertTrue(all(len(l['text']) == len(l['ct']) for l in d['script'])); self.assertEqual(d['script'][1]['ct'][0], 3.0)
        os.remove(self.job.dir / '口播字时间.json')                                                # 没有字时间：用审核线字幕，字时间在条里均分
        d = self.get('/api/data'); self.assertEqual(d['script_src'], '审核线字幕'); self.assertEqual([l['text'] for l in d['script']], ['第一句话。', '说灯。'])
        m = self.post('/api/markrange', {'span': [2, 4], 't': 0.75, 't_end': 1.12, 'quote': '句话', 'text': '这里口误'})
        self.assertTrue(m['ok']); self.assertEqual((m['id'], m['t'], m['t_end'], m['span'], m['quote'], m['frame'], m['text']), (1, 0.75, 1.12, [2, 4], '句话', '', '这里口误'))
        self.assertEqual(self.dv.goto_calls, [])                                                    # 选字写要求不动达芬奇
        self.assertTrue(self.post('/api/range', {'id': 1, 'span': [0, 4], 't': 0.0, 't_end': 1.12, 'quote': '第一句话'})['ok'])
        m1 = W.load(self.job)['markers'][0]; self.assertEqual((m1['span'], m1['t'], m1['quote']), ([0, 4], 0.0, '第一句话'))
        r = self.post('/api/shot', {'id': 1}); self.assertTrue(r['ok']); self.post('/api/shot', {'id': 1})
        m1 = W.load(self.job)['markers'][0]; self.assertTrue(m1['frame']); self.assertEqual(len(m1['imgs']), 1)
        self.assertTrue((W.home(self.job) / m1['imgs'][0]).is_file())
        png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
        u = self.post('/api/upload', {'id': 1, 'data': png}); self.assertTrue(u['ok']); self.assertTrue((W.home(self.job) / u['name']).is_file())
        self.assertFalse(self.post('/api/upload', {'id': 1, 'data': 'data:text/plain;base64,aGk='})['ok'])
        self.assertTrue(self.post('/api/delimg', {'id': 1, 'name': m1['frame']})['ok'])
        m1 = W.load(self.job)['markers'][0]; self.assertEqual((m1['frame'], len(m1['imgs'])), (m1['frame'], 1)); self.assertNotIn('你贴的', m1['frame'])
        self.post('/api/mark'); m2 = self.post('/api/range', {'id': 2, 'span': [5, 7], 't': 9.9, 't_end': 2.4, 'quote': '说灯'})
        self.assertEqual(W.load(self.job)['markers'][1]['t'], 3.2)                                  # 截过图的标记：t 还是截图那一刻
        r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.tmp), '--list'], capture_output=True, text=True)
        self.assertIn('#1 第1轮 0:00.00–0:01.12 [待处理] 这里口误｜选的字：第一句话｜图 ', r.stdout)
        W.set_round(self.job, 2); self.assertFalse(self.post('/api/range', {'id': 1, 'span': [0, 2], 't_end': 1, 'quote': '第一'})['ok'])
        self.assertFalse(self.post('/api/shot', {'id': 1})['ok'])
        a = self.post('/api/again', {'id': 1}); self.assertEqual((a['span'], a['t_end'], a['round']), ([0, 4], 1.12, 2))

    def test_dv_errors_do_not_add_marker(self):
        self.dv.err = '请把达芬奇切到“剪辑”页再截图（现在在别的页面）'
        r = self.post('/api/mark'); self.assertFalse(r['ok']); self.assertIn('剪辑', r['error'])
        self.assertFalse(self.get('/api/dv')['ok'])
        self.assertEqual(W.load(self.job)['markers'], [])

    def test_nav_labels_and_shot(self):
        r = self.post('/api/nav', {'labels': {'q': ' 挑空难照亮 ', 'why': '为什么', 'zz': '没有这一章'}})
        self.assertEqual(r['nav_labels'], {'q': '挑空难照亮'})                    # 和原标签一样的不记，不认识的章不记
        self.post('/api/nav', {'labels': {'q': '问题'}}); self.assertEqual(W.load(self.job)['nav_labels'], {})
        img, kind = self.post('/api/navshot', raw=True); self.assertEqual(kind, 'image/jpeg')
        self.assertFalse(any((W.home(self.job) / '图').glob('*')) if (W.home(self.job) / '图').exists() else False)   # 导航底图不算标记

    def test_listing(self):
        self.post('/api/mark'); self.post('/api/text', {'id': 1, 'text': '这里太暗'}); self.post('/api/nav', {'labels': {'q': '新字'}})
        r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.tmp), '--list'], capture_output=True, text=True)
        self.assertIn('#1 第1轮', r.stdout); self.assertIn('这里太暗', r.stdout); self.assertIn('正在说：第二句话说顶', r.stdout); self.assertIn('新字', r.stdout)
        r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.tmp), '--reply', '1', '--status', '没改', '--note', '要等模型'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertEqual(W.load(self.job)['markers'][0]['status'], '没改')


# ---------- 真的 DV 类，接假的达芬奇对象 ----------
class FTL:
    def __init__(s): s.tc = '01:00:10:15'
    def GetName(s): return 'v1_卡点审核'
    def GetSetting(s, k): return '29.97'
    def GetStartTimecode(s): return '01:00:00:00'
    def GetCurrentTimecode(s): return s.tc
    def SetCurrentTimecode(s, tc): s.tc = tc; return True
    def GetStartFrame(s): return 108000
    def GetEndFrame(s): return 108900


class FP:
    def __init__(s, name='测试工程'): s.name, s.tl, s.rendering, s.stills = name, FTL(), False, []
    def GetName(s): return s.name
    def GetCurrentTimeline(s): return s.tl
    def IsRenderingInProgress(s): return s.rendering
    def ExportCurrentFrameAsStill(s, p): s.stills.append(p); Image.new('RGB', (2160, 2880)).save(p, 'JPEG'); return True


class FR:
    def __init__(s, p, page='edit'): s.p, s.page, s.opened = p, page, []
    def GetProjectManager(s): return s
    def GetCurrentProject(s): return s.p
    def GetCurrentPage(s): return s.page
    def OpenPage(s, pg): s.opened.append(pg); s.page = pg; return True


class DVTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.job = film(self.tmp); self.p = FP(); self.r = FR(self.p)
        self.conn = resolve.connect; resolve.connect = lambda: self.r; self.dv = W.DV(self.job)
        self.lockf = self.tmp / 'resolve-write.lock'; self.env = os.environ.get('THINKCUT_RESOLVE_LOCK'); os.environ['THINKCUT_RESOLVE_LOCK'] = str(self.lockf)   # 不碰本机真的锁文件

    def tearDown(self):
        resolve.connect = self.conn; shutil.rmtree(self.tmp, ignore_errors=True)
        if self.env is None: os.environ.pop('THINKCUT_RESOLVE_LOCK', None)
        else: os.environ['THINKCUT_RESOLVE_LOCK'] = self.env

    def test_status_goto(self):
        s = self.dv.status(); self.assertEqual((s['timeline'], s['page']), ('v1_卡点审核', 'edit')); self.assertAlmostEqual(s['t'], 315 / 29.97, 3)
        self.assertAlmostEqual(self.dv.goto(20)['t'], 599 / 29.97, 3); self.assertEqual(self.p.tl.tc, '01:00:19:29')
        self.dv.goto(999); self.assertEqual(self.p.tl.tc, '01:00:29:29')                    # 跳到结尾以后：停在最后一帧
        self.p.rendering = True
        with self.assertRaises(W.Oops): self.dv.goto(1)

    def test_still_pages(self):
        out = self.tmp / '图' / 'a.jpg'; self.dv.still(out)
        with Image.open(out) as im: self.assertEqual(max(im.size), 1440)
        self.assertFalse((self.tmp / '图' / '.截图中.jpg').exists())
        self.r.page = 'deliver'; self.dv.still(out); self.assertEqual(self.r.opened, ['edit'])     # 渲完小样停在交付页：自动切回剪辑页
        self.r.page = 'fusion'
        with self.assertRaisesRegex(W.Oops, '剪辑'): self.dv.still(out)
        self.r.page = 'edit'; self.p.rendering = True
        with self.assertRaisesRegex(W.Oops, '渲染'): self.dv.still(out)

    def test_play_space(self):
        """网页按空格 → 给达芬奇发空格（2026-11-21 用户要的）：没辅助功能权限停下说怎么给；渲染、不在剪辑页、工程不对、写入锁被占都不发。"""
        class K:
            ok, sent = True, 0
            def space(s):
                if not s.ok: raise W.Oops(W.NEED_AX)                                  # 按键小服务回 noax
                s.sent += 1
        k, keep = K(), W.KEYS; W.KEYS = k; self.addCleanup(setattr, W, 'KEYS', keep)
        self.assertEqual(self.dv.toggle_play(), {'ok': True}); self.dv.toggle_play(); self.assertEqual(k.sent, 2)     # 再按一次＝达芬奇自己的暂停
        self.assertIsNone(resolve._held)                                                                             # 发完就放锁
        k.ok = False
        with self.assertRaisesRegex(W.Oops, '辅助功能'): self.dv.toggle_play()
        k.ok = True; self.r.page = 'deliver'
        with self.assertRaisesRegex(W.Oops, '剪辑'): self.dv.toggle_play()
        self.r.page = 'edit'; self.p.rendering = True
        with self.assertRaisesRegex(W.Oops, '渲染'): self.dv.toggle_play()
        self.p.rendering = False; self.p.name = '别的工程'
        with self.assertRaisesRegex(W.Oops, '别的工程'): self.dv.toggle_play()
        self.p.name = '测试工程'
        with open(self.lockf, 'a+') as f:
            fcntl.flock(f, fcntl.LOCK_EX); f.seek(0); f.truncate(); f.write(json.dumps({'命令': 'review 重建审核线', '进程号': os.getpid()}, ensure_ascii=False)); f.flush()
            with self.assertRaisesRegex(SystemExit, 'review 重建审核线'): self.dv.toggle_play()
        self.assertEqual(k.sent, 2)

    def test_proxy_when_resolve_playing(self):
        """达芬奇播放时脚本接口不回话（261118 实测）：子进程卡住就报“正在播放”，服务不卡；这时按空格不问达芬奇、直接发；达芬奇回话后恢复。"""
        class Conn:
            def __init__(s): s.sent, s.reply = [], []
            def send(s, x): s.sent.append(x)
            def poll(s, t=0): return bool(s.reply)
            def recv(s): return s.reply.pop(0)
        class Alive:
            def is_alive(s): return True
        class K:
            sent = 0
            def space(s): s.sent += 1
        k, keep = K(), W.KEYS; W.KEYS = k; self.addCleanup(setattr, W, 'KEYS', keep)
        px = W.DVProxy(self.job); px.p, px.conn = Alive(), Conn(); px.LIMIT = dict.fromkeys(px.LIMIT, 0)
        with self.assertRaisesRegex(W.Oops, '正在播放'): px.status()                 # 达芬奇不回话
        self.assertTrue(px.busy)
        with self.assertRaisesRegex(W.Oops, '正在播放'): px.goto(3)                   # 还卡着：不再往子进程塞
        self.assertEqual(len(px.conn.sent), 1)
        self.assertEqual(px.toggle_play(), {'ok': True}); self.assertEqual((k.sent, len(px.conn.sent)), (1, 1))   # 暂停：直接发空格
        self.assertIsNone(resolve._held)
        px.conn.reply = [('ok', {'t': 9.0}), ('ok', {'t': 9.0, 'timeline': 'v'})]   # 达芬奇停了：卡着的那次回来，再问就正常
        self.assertEqual(px.status(), {'t': 9.0, 'timeline': 'v'}); self.assertFalse(px.busy)
        px.conn.reply = [('err', '达芬奇里没有打开工程')]
        with self.assertRaisesRegex(W.Oops, '没有打开工程'): px.toggle_play()           # 不卡时照常走子进程里的检查

    def test_wrong_project_and_lock(self):
        self.p.name = '别的工程'
        with self.assertRaisesRegex(W.Oops, '别的工程'): self.dv.status()
        self.p.name = '测试工程'
        self.dv.goto(1); self.dv.still(self.tmp / 'x.jpg'); self.assertIsNone(resolve._held)          # 跳转、截图用完就放锁（服务一直开着）
        with open(self.lockf, 'a+') as f:                                                            # 别的命令（重建审核线）拿着达芬奇写入锁
            fcntl.flock(f, fcntl.LOCK_EX); f.seek(0); f.truncate(); f.write(json.dumps({'命令': 'review 重建审核线', '进程号': os.getpid()}, ensure_ascii=False)); f.flush()
            n = len(self.p.stills); tc = self.p.tl.tc
            with self.assertRaisesRegex(SystemExit, 'review 重建审核线'): self.dv.still(self.tmp / 'y.jpg')
            with self.assertRaisesRegex(SystemExit, 'review 重建审核线'): self.dv.goto(5)
            self.assertEqual((len(self.p.stills), self.p.tl.tc), (n, tc))                            # 拿不到锁就不截、不跳
            self.assertEqual(self.dv.status()['timeline'], 'v1_卡点审核')                            # 读播放头不加锁，照常
        self.dv.goto(5); self.assertIsNone(resolve._held)


class LifecycleTests(unittest.TestCase):
    """起服务（真的后台进程，测试里不访问 /api/dv，所以不会去连达芬奇）、复用、端口被占、停。"""
    def setUp(self): self.tmp = Path(tempfile.mkdtemp()); self.job = film(self.tmp)
    def tearDown(self): W.stop(self.job); shutil.rmtree(self.tmp, ignore_errors=True)

    def test_start_reuse_stop(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); busy = s.getsockname()[1]; s.listen()
            with self.assertRaisesRegex(SystemExit, '被占了'): W.pick_port(self.job, busy)
        r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.tmp), '--round', '3'], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('已启动：http://127.0.0.1:', r.stdout); self.assertIn('第 3 轮', r.stdout)
        info = rj(W.home(self.job) / '服务.json'); self.assertEqual(W.ping(info['port'])['dir'], str(self.job.dir))
        r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.tmp)], capture_output=True, text=True, timeout=30)
        self.assertIn('已经在跑（复用）：' + info['url'], r.stdout)
        with self.assertRaisesRegex(SystemExit, '另一个项目的审片工作台'):
            other = film(Path(tempfile.mkdtemp())); W.pick_port(other, info['port'])
        shutil.rmtree(other.dir, ignore_errors=True)
        r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.tmp), '--stop'], capture_output=True, text=True, timeout=30)
        self.assertIn('已停', r.stdout); self.assertIsNone(W.ping(info['port'])); self.assertFalse((W.home(self.job) / '服务.json').exists())
        wj(W.home(self.job) / '服务.json', {'pid': 999999, 'port': info['port'], 'url': 'x', 'dir': str(self.job.dir)})   # 服务被强制结束留下的旧记录
        self.assertIsNone(W.running(self.job)); self.assertFalse((W.home(self.job) / '服务.json').exists())


if __name__ == '__main__':
    unittest.main()
