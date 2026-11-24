# -*- coding: utf-8 -*-
"""审片台“口播剪辑”栏（2026-11-22 用户定：剪口播的审核网页并进来）离线测试：不连达芬奇，剪口播后台用替身（和真的一样查 Host／Origin、回 409、分段读）。
走一遍：每个剪辑器接口原样转发（含 409 和 reload、保存进度、预览片段的 Range、报错文字）；没带口令／来源不对的写请求被拒且没转过去；
后台没在跑时按需起（edit_project.py serve --backend）；口播阶段没有 job.json 也能开、只有口播剪辑栏；init 之后数据接着用；
不是标准项目时数据在剪口播工程里、init 到别处第一次开就搬过去；有／没有剪口播工程时口播剪辑栏下半给什么；页面口令和预览长度填进剪辑器页面。"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinkcut import workbench as W, koubo                 # noqa: E402
from thinkcut.common import Job, wj, rj                    # noqa: E402
os.environ['THINKCUT_NO_OPEN'] = '1'
S = Path(__file__).resolve().parents[1] / 'thinkcut_edit.py'
INDEX = W.PAGE.read_text(encoding='utf-8'); EDITOR = W.EDITOR.read_text(encoding='utf-8')


class Backend:
    """剪口播后台的替身：和 review_v2_server 一样只认 Host=127.0.0.1:端口、POST 还要 Origin；记下收到的请求。"""
    def __init__(self):
        self.seen, self.part = [], bytes(range(256)) * 40
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_): pass

            def reply(self, obj, code=200, kind='application/json'):
                body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                me.seen.append(('GET', self.path, dict(self.headers)))
                if self.headers.get('Host') != f'127.0.0.1:{me.port}': return self.reply({'error': 'host'}, 403)
                if self.path == '/editor-api': return self.reply({'revision': 3, 'state_revision': 'r3', 'alignment': {'alignment_id': 'a'}})
                if self.path == '/resolve-status': return self.reply({'status': 'running', 'running': True, 'message': '正在生成字幕、导入达芬奇并读回核对…', 'started_at': '2026-11-22T00:00:00+00:00'})
                if self.path == '/editor-page-info': return self.reply({'name': 'v1', 'segment_seconds': 12.012, 'source_duration': 132.5})
                if self.path == '/caption-engine.js': return self.reply(b'/* engine */', kind='application/javascript')
                m = re.fullmatch(r'/source-part/(\d+)\.mp4', self.path)
                if m:
                    r = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range') or '')
                    if not r: return self.reply(me.part, kind='video/mp4')
                    a = int(r[1]); b = int(r[2]) if r[2] else len(me.part) - 1; body = me.part[a:b + 1]
                    self.send_response(206); self.send_header('Content-Type', 'video/mp4'); self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Content-Range', f'bytes {a}-{b}/{len(me.part)}'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
                self.reply({'error': 'no'}, 404)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length') or 0)); me.seen.append(('POST', self.path, dict(self.headers), raw))
                if self.headers.get('Host') != f'127.0.0.1:{me.port}' or self.headers.get('Origin') != f'http://127.0.0.1:{me.port}': return self.reply({'error': 'origin'}, 403)
                body = json.loads(raw)
                if body.get('state_revision') == 'old': return self.reply({'error': '审核状态已更新，请核对最新文字后重试。', 'reload': True}, 409)
                if self.path == '/save-to-resolve': return self.reply({'status': 'running', 'running': True, 'message': '正在生成字幕、导入达芬奇并读回核对…'})
                if self.path == '/editor-action' and body.get('action') == 'boom': return self.reply({'error': '不能删除全部口播文字；此次未修改。'}, 400)
                self.reply({'revision': 4, 'state_revision': 'r4', 'echo': body})

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self): self.srv.shutdown(); self.srv.server_close()


def aroll_project(root, name='片子_AI剪辑'):
    """一个剪完的剪口播工程（审核状态、工程设置都在）。"""
    a = root / name; (a / '审核').mkdir(parents=True)
    wj(a / '审核' / 'review_v2_state.json', {}); wj(a / '审核' / 'project_runtime.json', {'name': 'v1', 'source': '/x.mov', 'source_frames': 300, 'fps': [30000, 1001]})
    return a


def standard(root):
    """标准项目：项目.json、01_原始素材、02_制作/<剪口播工程>、03_成片/<项目名>.md。"""
    (root / '01_原始素材').mkdir(parents=True); (root / '03_成片').mkdir()
    wj(root / '项目.json', {'title': '测试项目标题', 'name': root.name})
    (root / '03_成片' / f'{root.name}.md').write_text('# 测试\n\n## 标题\n\n原标题\n\n## 封面文字\n\n第一行\n第二行\n\n## 口播正文\n\n录之前的定稿。\n', encoding='utf-8')
    return aroll_project(root / '02_制作')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve(); self.be = Backend(); self.servers = []
        self.bin = self.tmp / 'bin'; self.bin.mkdir(); os.environ['THINKCUT_AROLL_BIN'] = str(self.bin)
        # edit_project.py 的替身：serve --backend 时把替身后台的地址写进 审核/审核网址.txt（真的那个会起 review_v2_server）
        (self.bin / 'edit_project.py').write_text(
            'import json, socket, sys\nfrom pathlib import Path\na = sys.argv[1:]; d = Path(a[a.index("--project-dir") + 1])\n'
            'open(d / "起后台.log", "a").write(" ".join(a) + "\\n")\n'
            f'(d / "审核" / "审核网址.txt").write_text(json.dumps({{"url": "http://127.0.0.1:{self.be.port}/", "pid": 1, "host": socket.gethostname()}}))\n',
            encoding='utf-8')

    def tearDown(self):
        for s in self.servers: s.shutdown(); s.server_close()
        self.be.close(); os.environ.pop('THINKCUT_AROLL_BIN', None); shutil.rmtree(self.tmp, ignore_errors=True)

    def serve(self, job):
        srv = W.make_server(job, 0); self.servers.append(srv); threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.port = srv.server_address[1]; return self.port

    def req(self, path, body=None, headers=None, method=None):
        h = {'Content-Type': 'application/json', **(headers or {})}
        q = urllib.request.Request(f'http://127.0.0.1:{self.port}{urllib.parse.quote(path, safe="/?=&")}', None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode()), h, method=method)
        try:
            with urllib.request.urlopen(q, timeout=20) as r: return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()

    def page(self, path, body):
        """像审片台页面那样发：本服务的来源、页面口令、浏览器标的同源。"""
        return self.req(path, body, {'Origin': f'http://127.0.0.1:{self.port}', 'X-Shenpian-Token': W.TOKEN, 'Sec-Fetch-Site': 'same-origin'})


import urllib.parse  # noqa: E402


class ForwardTests(Base):
    def setUp(self):
        super().setUp(); self.aroll = standard(self.tmp / '项目')
        self.job = W.open_film(self.aroll); self.serve(self.job)

    def test_every_editor_request_is_forwarded_as_is(self):
        code, h, b = self.req('/editor-api'); self.assertEqual((code, json.loads(b)['revision']), (200, 3))
        code, h, b = self.req('/resolve-status'); self.assertEqual(json.loads(b)['message'], '正在生成字幕、导入达芬奇并读回核对…')   # 保存进度原样
        code, h, b = self.req('/caption-engine.js'); self.assertEqual((code, b, h['Content-Type']), (200, b'/* engine */', 'application/javascript'))
        code, h, b = self.req('/source-part/0.mp4', headers={'Range': 'bytes=100-199'})                       # 预览片段：分段读
        self.assertEqual((code, h['Content-Range'], h['Accept-Ranges'], b), (206, f'bytes 100-199/{len(self.be.part)}', 'bytes', self.be.part[100:200]))
        code, h, b = self.req('/source-part/0.mp4'); self.assertEqual((code, b), (200, self.be.part))
        for path in koubo.POST:
            code, h, b = self.page(path, {'state_revision': 'r3', 'action': 'cut', 'ids': ['a']})
            self.assertEqual(code, 200, path)
            sent = [x for x in self.be.seen if x[0] == 'POST' and x[1] == path][-1]
            self.assertEqual(json.loads(sent[3])['ids'], ['a'])                                                # 内容原样
            self.assertEqual(sent[2]['Origin'], f'http://127.0.0.1:{self.be.port}'); self.assertEqual(sent[2]['Host'], f'127.0.0.1:{self.be.port}')
        self.assertTrue(all(x[2]['Host'] == f'127.0.0.1:{self.be.port}' for x in self.be.seen))

    def test_conflict_and_errors_come_back_unchanged(self):
        code, h, b = self.page('/editor-action', {'state_revision': 'old', 'action': 'cut', 'ids': ['a']})
        self.assertEqual(code, 409); self.assertEqual(json.loads(b), {'error': '审核状态已更新，请核对最新文字后重试。', 'reload': True})
        code, h, b = self.page('/editor-action', {'state_revision': 'r3', 'action': 'boom'})
        self.assertEqual((code, json.loads(b)['error']), (400, '不能删除全部口播文字；此次未修改。'))
        code, h, b = self.page('/save-to-resolve', {'state_revision': 'r3'}); self.assertEqual((code, json.loads(b)['running']), (200, True))

    def test_writes_without_token_or_from_elsewhere_are_refused_and_never_forwarded(self):
        n = len(self.be.seen); o = f'http://127.0.0.1:{self.port}'
        for headers in ({}, {'Origin': o}, {'Origin': o, 'X-Shenpian-Token': 'guess'}, {'X-Shenpian-Token': W.TOKEN},
                        {'Origin': o, 'X-Shenpian-Token': W.TOKEN, 'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'https://evil.example', 'X-Shenpian-Token': W.TOKEN}):
            for path in koubo.POST:
                code, h, b = self.req(path, {'state_revision': 'r3', 'action': 'cut', 'ids': ['a']}, headers)
                self.assertEqual(code, 403, (path, headers)); self.assertFalse(json.loads(b)['ok'])
        code, *_ = self.req('/editor-api', headers={'Host': f'evil.example:{self.port}'}); self.assertEqual(code, 403)   # GET 也查 Host
        self.assertEqual(len(self.be.seen), n)                                                                  # 一个都没转过去

    def test_backend_started_on_demand_and_restarted_when_gone(self):
        log = self.aroll / '起后台.log'
        self.req('/editor-api'); self.assertIn('serve --backend --no-open --project-dir ' + str(self.aroll), log.read_text())
        with socket.socket() as s: s.bind(('127.0.0.1', 0)); dead = s.getsockname()[1]
        wj(self.aroll / '审核' / '审核网址.txt', {'url': f'http://127.0.0.1:{dead}/', 'pid': 1, 'host': socket.gethostname()})   # 后台被关了：记录还在，端口没人听
        code, h, b = self.req('/editor-api'); self.assertEqual(code, 200); self.assertEqual(log.read_text().count('serve --backend'), 2)
        code, h, b = self.page('/editor-action', {'state_revision': 'r3', 'action': 'cut', 'ids': ['a']}); self.assertEqual(code, 200)
        self.assertEqual(len([x for x in self.be.seen if x[0] == 'POST']), 1)                                  # 写入只发了一次

    def test_backend_that_cannot_start_says_why(self):
        (self.bin / 'edit_project.py').write_text('import sys\nprint("剪口播后台没有启动成功：原片已改变", file=sys.stderr); sys.exit(1)\n', encoding='utf-8')
        code, h, b = self.req('/editor-api'); self.assertEqual(code, 503); self.assertIn('原片已改变', json.loads(b)['error'])

    def test_editor_page_has_token_and_preview_length(self):
        code, h, b = self.req('/文案剪辑.html'); page = b.decode()
        self.assertEqual(code, 200); self.assertIn(f"const SHENPIAN_TOKEN='{W.TOKEN}'", page); self.assertNotIn('__SHENPIAN_TOKEN__', page)
        self.assertIn('data-segment-seconds="12.012" data-source-duration="132.5"', page); self.assertNotIn('src="/source.mp4"', page)
        for want in ('id="saveResolve" class="btn primary"', 'location.replace', "'X-Shenpian-Token'", 'wenanShown'):
            self.assertIn(want, page)
        self.assertEqual(page.count('class="btn primary"'), 1)                                               # 这一栏唯一的主按钮：保存到达芬奇

    def test_old_ui_is_gone_and_page_is_in_index(self):
        self.assertIn("f.src='/文案剪辑.html'", INDEX); self.assertIn('原稿（录之前的定稿）', INDEX)
        self.assertNotIn('review_editor', INDEX + EDITOR)


class LiteTests(Base):
    """口播阶段：剪完口播、还没 init（没有 job.json）。"""
    def test_standard_project_opens_at_the_future_film_dir_with_only_wenan(self):
        a = standard(self.tmp / '项目'); job = W.open_film(a)
        self.assertTrue(W.lite(job)); self.assertEqual(job.dir, self.tmp / '项目' / '02_制作' / '自动剪辑'); self.assertEqual(W.aroll_of(job), a)
        self.assertEqual(job['title'], '测试项目标题')
        self.serve(job); d = json.loads(self.req('/api/data')[2])
        self.assertTrue(d['lite']); self.assertTrue(d['koubo']['ready'])
        b = d['board']['badges']; self.assertEqual(b['文案']['kind'], 'idle')     # 没人确认过：写“没有待办”，不写“已定”
        self.assertTrue(all(v['text'] == '还没到' for c, v in b.items() if c not in ('文案', '写稿')))          # 2026-11-24：第一栏“文案”（数据里叫 写稿）这时也能用
        self.assertNotEqual(b['写稿']['text'], '还没到'); self.assertFalse(d['board']['ucols']['写稿']['off'])
        self.assertEqual(d['board']['script']['fields']['标题']['value'], '原标题')                          # 上半：标题、封面文字照旧
        dv = json.loads(self.req('/api/dv')[2]); self.assertFalse(dv['ok']); self.assertTrue(dv['lite'])     # 不连达芬奇
        code, h, b2 = self.req('/api/goto', {'t': 1}); self.assertIn('口播阶段', json.loads(b2)['error'])
        self.assertFalse((job.dir / '合成').exists())                                                          # 不建 合成/ 这些目录
        self.assertEqual(json.loads(self.req('/api/ping')[2])['lite'], True)
        # 片子目录、项目根都能直接写；记住的剪口播工程下次不用再给
        self.assertEqual(W.open_film(self.tmp / '项目').dir, job.dir); self.assertEqual(W.aroll_of(W.open_film(job.dir)), a)

    def test_without_aroll_wenan_keeps_the_script_editor(self):
        (self.tmp / '项目' / '01_原始素材').mkdir(parents=True); wj(self.tmp / '项目' / '项目.json', {'title': 't'})
        job = W.open_film(self.tmp / '项目'); self.assertTrue(W.lite(job)); self.assertIsNone(W.aroll_of(job))
        self.serve(job); d = json.loads(self.req('/api/data')[2])
        self.assertFalse(d['koubo']['ready']); self.assertIn('还没有剪口播工程', d['koubo']['why'])
        code, h, b = self.req('/文案剪辑.html'); self.assertEqual(code, 404)
        code, h, b = self.req('/editor-api'); self.assertEqual(code, 404)

    def test_unfinished_aroll_is_not_ready(self):
        a = standard(self.tmp / '项目'); (a / '审核' / 'review_v2_state.json').unlink()
        self.assertFalse(koubo.info(a)['ready']); self.assertIn('还没剪完', koubo.info(a)['why'])

    def test_not_a_film_dir_is_refused(self):
        with self.assertRaisesRegex(SystemExit, '不是片子目录'): W.open_film(self.tmp)

    def test_after_init_the_same_data_carries_on(self):
        a = standard(self.tmp / '项目'); job = W.open_film(a)
        W.edit(job, lambda d: d['markers'].append({'id': 1, 'round': 1, 't': 1.0, 'text': '口播阶段记的', 'status': '待处理'}))
        W.board.edit(job, lambda d: d['edits'].append({'id': '改1', 'column': '文案', 'field': '标题', 'old': 'a', 'new': 'b', 'at': 'x', 'ts': 0, 'ok': True, 'handled': None}))
        wj(job.dir / 'job.json', {'fps': '30000/1001', 'format': '3:4', 'aroll': str(a), 'project': 'p', 'frames': 300})   # init 写的就是这个目录
        full = W.open_film(a)
        self.assertFalse(W.lite(full)); self.assertEqual(full.dir, job.dir)
        self.assertEqual(W.load(full)['markers'][0]['text'], '口播阶段记的'); self.assertEqual(W.board.load(full)['edits'][0]['id'], '改1')
        self.assertEqual(W.aroll_of(full), a)

    def test_job_aroll_wins_over_the_command(self):
        a = standard(self.tmp / '项目'); b = aroll_project(self.tmp / '项目' / '02_制作', '另一条_AI剪辑')
        d = self.tmp / '项目' / '02_制作' / '自动剪辑'; d.mkdir(); wj(d / 'job.json', {'fps': '30000/1001', 'format': '3:4', 'aroll': str(a)})
        self.assertEqual(W.aroll_of(W.open_film(d, b)), a)

    def test_non_standard_aroll_keeps_data_inside_and_moves_it_after_init_elsewhere(self):
        a = aroll_project(self.tmp / '随便放'); job = W.open_film(a)
        self.assertTrue(W.lite(job)); self.assertEqual(job.dir, a)                                           # 不是标准项目：数据在剪口播工程里
        self.serve(job); d = json.loads(self.req('/api/data')[2]); self.assertTrue(d['koubo']['ready']); self.assertFalse(d['board']['script']['exists'])
        W.edit(job, lambda x: x['markers'].append({'id': 1, 'round': 1, 't': 0, 'text': '记的', 'status': '待处理'}))
        film = self.tmp / '片子'; film.mkdir(); wj(film / 'job.json', {'fps': '30000/1001', 'format': '3:4', 'aroll': str(a)})
        full = W.open_film(film); W.adopt(full)
        self.assertEqual(W.load(full)['markers'][0]['text'], '记的'); self.assertFalse((a / '审片工作台').exists())   # 搬过去了，只有一份
        koubo.write_pointer(a, film, 'http://127.0.0.1:1/')
        self.assertEqual(W.open_film(a).dir, film)                                                            # 以后从剪口播工程开，找到的是片子目录

    def test_command_line_opens_lite_and_switches_to_full_after_init(self):
        a = standard(self.tmp / '项目'); env = {**os.environ, 'THINKCUT_NO_OPEN': '1'}
        r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(a), '--column', '文案'], capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
        film = self.tmp / '项目' / '02_制作' / '自动剪辑'
        try:
            self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('还没建片子目录，只有“文案”栏和“口播剪辑”栏能用', r.stdout); self.assertIn('#%E6%96%87%E6%A1%88', r.stdout)   # 2026-11-24 起多了第一栏“文案”
            info = rj(film / '审片工作台' / '服务.json'); self.assertTrue(W.ping(info['port'])['lite'])
            self.assertEqual(koubo.pointer(a)['url'], info['url'])                                         # 剪口播后台直接打开时据此指到审片台
            wj(film / 'job.json', {'fps': '30000/1001', 'format': '3:4', 'aroll': str(a), 'project': 'p', 'frames': 300})
            r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(a)], capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('换成完整的', r.stdout)
            info = rj(film / '审片工作台' / '服务.json'); self.assertFalse(W.ping(info['port'])['lite'])
        finally:
            subprocess.run([sys.executable, '-B', str(S), 'workbench', str(film), '--stop'], capture_output=True, timeout=30)


if __name__ == '__main__':
    unittest.main()
