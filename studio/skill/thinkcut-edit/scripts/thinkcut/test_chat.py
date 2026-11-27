# -*- coding: utf-8 -*-
"""AI 对话侧栏（2026-11-27）离线测试：不起真的 AI（假的 claude／codex 替身），不碰钥匙串、达芬奇库列表和真片子。run_tests.sh 会跑。
走通：首页开项 → 对话接到新项目（接着做完一次）；项目里拖入素材登记（同名不覆盖、只收页面的）；说了路径 AI 用 --add-material 放进来；
存档走代办（首页代办只跑 project.py）；停止；提问选项；要求导出／发布／定稿被程序拦下、AI 自己去跑导出也被对话代办拒绝；
和“提交 AI”共用一条会话、不同时跑；未登录说人话；记录重开还在、能清空；流式回复（Claude 的一小段一小段）。"""
import contextlib, json, os, shutil, stat, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['THINKCUT_NO_OPEN'] = '1'
os.environ.setdefault('THINKCUT_NO_KEYCHAIN', '1')
from thinkcut import workbench as W, home as HM, engine as E, chat as C, board as B, materials as M     # noqa: E402
from thinkcut.test_workbench import FakeDV                                                              # noqa: E402
from thinkcut.test_board import project                                                                 # noqa: E402

FAKE = r'''#!/usr/bin/env python3
# 假的 AI：认得自己叫 claude 还是 codex；FAKE_CHAT 决定这一回做什么（按收到的话里的字认）
import json, os, re, subprocess, sys, time
from pathlib import Path
name = os.path.basename(sys.argv[0]); a = sys.argv[1:]
log = os.environ.get('FAKE_AI_LOG')
if log:
    with open(log, 'a', encoding='utf-8') as f: f.write(json.dumps({'name': name, 'argv': a, 'cwd': os.getcwd(), 'env': {k: v for k, v in os.environ.items() if k.startswith('THINKCUT')}}, ensure_ascii=False) + '\n')
logged = os.environ.get('FAKE_LOGGED', '1') == '1'
if name == 'claude' and a[:2] == ['auth', 'status']: print(json.dumps({'loggedIn': logged})); sys.exit(0)
if name == 'codex' and a[:2] == ['login', 'status']: print('Logged in using ChatGPT' if logged else 'Not logged in'); sys.exit(0 if logged else 1)
def ev(x): print(json.dumps(x, ensure_ascii=False), flush=True)
prompt = a[a.index('-p') + 1] if '-p' in a else a[-1]
sid = (a[a.index('--session-id') + 1] if '--session-id' in a else a[a.index('--resume') + 1] if '--resume' in a else None)
if name == 'codex': ev({'type': 'thread.started', 'thread_id': a[2] if a[1:2] == ['resume'] else 'T-chat'})
else: ev({'type': 'system', 'subtype': 'init', 'session_id': sid})
def step(cmd, desc=''):
    if name == 'codex': ev({'type': 'item.started', 'item': {'type': 'command_execution', 'command': cmd}})
    else: ev({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': cmd, 'description': desc}}]}})
def run(*x):
    r = subprocess.run([sys.executable, *x], capture_output=True, text=True, timeout=120); return {'code': r.returncode, 'out': r.stdout, 'err': r.stderr}
def finish(text):
    if name == 'claude':
        for i in range(0, len(text), 4): ev({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': text[i:i + 4]}}})
        ev({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}})
        ev({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text, 'session_id': sid})
    else:
        ev({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}}); ev({'type': 'turn.completed'})
    sys.exit(0)
said = prompt.split('用户说：', 1)[-1]
probe = os.environ.get('FAKE_PROBE')
if '对话已经接到项目' in prompt:
    m = re.search(r'接到项目「[^」]+」（([^）]+)）', prompt); pd = Path(m[1])
    (pd / '02_制作' / '对话写的.txt').write_text('接着做完了', encoding='utf-8')
    finish('接着把素材放好了。')
if '开项' in said:
    proj = re.search(r'python3 "([^"]+project\.py)" init', prompt)[1]
    step(f'python3 project.py init "卫生间防水刷多高"', '建项目')
    r = run(proj, 'init', '261127 卫生间防水刷多高', '--root', os.environ['FAKE_ROOT'])
    if probe: Path(probe).write_text(json.dumps(r, ensure_ascii=False), encoding='utf-8')
    path = json.loads(r['out'][r['out'].index('{'):])['project'] if r['code'] == 0 else ''
    finish(f'项目建好了：{Path(path).name}。\n接到项目：{path}' if path else '建项目没成：' + r['err'])
if '存档' in said:
    proj = re.search(r'python3 "([^"]+project\.py)" init', prompt)[1]; pd = re.search(r'存档 (\S+)', said)[1]
    step(f'python3 project.py archive "{pd}"', '存档')
    r = run(proj, 'archive', pd); r2 = run(re.search(r'python3 "([^"]+thinkcut_edit\.py)"', prompt)[1], 'export', pd)
    if probe: Path(probe).write_text(json.dumps({'archive': r, 'cover': r2}, ensure_ascii=False), encoding='utf-8')
    finish('存档交给代办了。')
if '导出试试' in said:
    m = re.search(r'python3 "([^"]+thinkcut_edit\.py)" workbench "([^"]+)"', prompt)
    step('python3 thinkcut_edit.py export', '试着导出')
    r = run(m[1], 'export', m[2]); r2 = run(str(Path(m[1]).resolve().parents[4] / 'projects/bin/project.py'), 'upload', os.getcwd())
    if probe: Path(probe).write_text(json.dumps({'export': r, 'upload': r2}, ensure_ascii=False), encoding='utf-8')
    finish('导出没让我跑：' + r['err'][-200:])
if '放进来' in said:
    m = re.search(r'python3 "([^"]+thinkcut_edit\.py)" workbench "<项目文件夹>" --add-material', prompt); src = re.search(r'在 (\S+) ', said)[1]
    pd = os.getcwd(); step(f'python3 thinkcut_edit.py workbench . --add-material {src}', '放素材')
    r = run(m[1], 'workbench', pd, '--add-material', src)
    if probe: Path(probe).write_text(json.dumps(r, ensure_ascii=False), encoding='utf-8')
    finish(r['out'] or r['err'])
if '慢慢' in said:
    step('ls 01_原始素材', '看看素材'); time.sleep(120); sys.exit(0)
if '选' in said:
    finish('C0033 拿不准算哪类。\n选项：A 算 B-roll｜B 算口播补录')
if '文件' in said:
    files = re.findall(r'^  (/.+?)（', prompt, re.M)
    finish('收到：' + '、'.join(Path(x).name for x in files))
if 'fail' in said:
    if name == 'codex': ev({'type': 'turn.failed', 'error': {'message': 'stream error: network connection lost'}})
    else: ev({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'result': 'Connection error.', 'session_id': sid})
    sys.exit(1)
step('cat 项目.json', '看了项目.json')
finish('这个项目还在撰稿，**文稿**还没定稿。')
'''

FAKE_PROJECT = '''import json, sys
from pathlib import Path
a = sys.argv[1:]
if a[0] == 'init':
    root = Path(a[a.index('--root') + 1]); d = root / a[1]; d.mkdir(parents=True)
    for x in ('01_原始素材', '02_制作', '03_成片'): (d / x).mkdir()
    (d / '项目.json').write_text(json.dumps({'name': a[1]}, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'project': str(d), 'name': a[1]}, ensure_ascii=False))
else:
    print('project.py 替身：' + ' '.join(a))
'''


def fake_bin(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for n in ('claude', 'codex'):
        f = folder / n; f.write_text(FAKE.replace('/usr/bin/env python3', sys.executable), encoding='utf-8'); f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return folder


def page(base): return {'Origin': base, 'Sec-Fetch-Site': 'same-origin'}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve(); E._cache.clear(); self.env0 = dict(os.environ)
        prog = self.tmp / '程序'; (prog / 'projects' / 'bin').mkdir(parents=True); (prog / 'projects/bin/project.py').write_text(FAKE_PROJECT, encoding='utf-8')
        os.environ.update(THINKCUT_AI_PATH=str(fake_bin(self.tmp / 'bin')), THINKCUT_AI_SETTINGS=str(self.tmp / 'AI引擎设置.json'),
                          FAKE_AI_LOG=str(self.tmp / 'fake.log'), FAKE_LOGGED='1', FAKE_PROBE=str(self.tmp / 'probe.json'),
                          THINKCUT_HOME_CHAT=str(self.tmp / '首页对话'), THINKCUT_BROKER_PROGRAM=str(prog), THINKCUT_BROKER_TAKE='10')

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.env0); E._cache.clear(); shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, p, body=None, page_=True, raw=None, cookie=None):
        h = {'Content-Type': 'application/json', **(page(self.base) if page_ else {}), **({'Cookie': cookie or self.cookie} if page_ else {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        q = urllib.request.Request(self.base + p, data, h, method='POST' if data is not None else 'GET')
        try:
            with urllib.request.urlopen(q, timeout=30) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def ok(self, p, body=None):
        code, out = self.req(p, body if body is not None else {}); self.assertEqual(code, 200, out); self.assertTrue(out['ok'], out); return out

    def state(self): return self.req('/api/chat', page_=False)[1]

    def wait_done(self, n=60):
        t = time.time()
        while time.time() - t < n:
            s = self.state()
            if not s['busy']: return s
            time.sleep(0.2)
        self.fail('对话一直没回完')

    def say(self, text, files=None):
        out = self.ok('/api/chat/send', {'text': text, **({'files': files} if files else {})}); return out, self.wait_done()

    def calls(self):
        f = self.tmp / 'fake.log'
        return [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()] if f.exists() else []

    def runs(self): return [c for c in self.calls() if c['argv'][:2] not in (['auth', 'status'], ['login', 'status'])]

    def last_ai(self, s=None): return [m for m in (s or self.state())['messages'] if m['role'] == 'ai'][-1]

    def probe(self): return json.loads((self.tmp / 'probe.json').read_text(encoding='utf-8'))


class ProjectChat(Base):
    def setUp(self):
        super().setUp()
        self.pd, self.job, _ = project(self.tmp); self.srv = W.make_server(self.job, 0, FakeDV())
        self.port = self.srv.server_address[1]; self.base = f'http://127.0.0.1:{self.port}'; self.cookie = W.cookie_header(self.base)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        with contextlib.suppress(Exception): self.srv.chat.stop()
        with contextlib.suppress(Exception): self.srv.runner.stop()
        self.srv.shutdown(); self.srv.server_close(); super().tearDown()

    def test_page_has_sidebar_and_button(self):
        with urllib.request.urlopen(self.base + '/') as r: html = r.read().decode()
        self.assertIn('id="tcChatBtn"', html); self.assertIn('id="tcChat"', html); self.assertNotIn('<!--THINKCUT_CHAT-->', html)
        self.assertIn('window.tcChat', html)

    def test_reply_streams_shares_session_with_submit(self):
        """项目对话：Claude 一小段一小段回；会话号记进 AI引擎.json（和提交 AI 同一条），下一句 --resume 同一条。"""
        E.choose('claude'); _, s = self.say('这个项目到哪一步了')
        m = self.last_ai(s); self.assertEqual(m['state'], '完成'); self.assertIn('还在撰稿', m['text']); self.assertEqual(m['steps'][0]['text'], '看了项目.json')
        argv = self.runs()[-1]['argv']; self.assertIn('--include-partial-messages', argv); sid = argv[argv.index('--session-id') + 1]
        self.assertEqual(E.load(self.job)['sessions']['claude']['id'], sid, '会话号和“提交 AI”记在同一处')
        self.assertIn('同一条会话', argv[argv.index('-p') + 1])
        self.say('再说一句'); argv = self.runs()[-1]['argv']; self.assertEqual(argv[argv.index('--resume') + 1], sid, '下一句接着同一条会话')
        env = self.runs()[-1]['env']; self.assertTrue(env['THINKCUT_BROKER_DIR'].endswith('代办-对话'), '对话用自己的代办文件夹')
        self.assertEqual(env['THINKCUT_NO_KEYCHAIN'], '1')

    def test_codex_session_and_permissions(self):
        E.choose('codex'); self.say('这个项目到哪一步了'); self.assertEqual(E.load(self.job)['sessions']['codex']['id'], 'T-chat')
        self.say('还有呢'); argv = self.runs()[-1]['argv']; self.assertEqual(argv[:3], ['exec', 'resume', 'T-chat'])
        self.assertIn('sandbox_workspace_write.network_access=false', argv)
        roots = json.loads(next(x for x in argv if x.startswith('sandbox_workspace_write.writable_roots=')).split('=', 1)[1])
        self.assertIn(str(self.pd), roots); self.assertFalse(any('Application Support' in r for r in roots))

    def test_drag_in_files_same_name_and_page_only(self):
        """拖进来：放进 01_原始素材、记进 备料.json；同名不覆盖；不是页面发的不收；发给 AI 时带着放在哪。"""
        E.choose('codex')
        code, out = self.req('/api/chat/upload?name=C0031.MP4', raw=b'x' * 1000); self.assertEqual(code, 200, out); self.assertEqual(out['file']['path'], '01_原始素材/C0031.MP4')
        code, out2 = self.req('/api/chat/upload?name=C0031.MP4', raw=b'y' * 10); self.assertEqual(out2['file']['path'], '01_原始素材/C0031 (2).MP4', '同名不覆盖')
        self.assertEqual((self.pd / '01_原始素材' / 'C0031.MP4').read_bytes(), b'x' * 1000)
        code, _ = self.req('/api/chat/upload?name=evil.mp4', raw=b'z', page_=False); self.assertEqual(code, 403, '命令行不能替用户放文件')
        self.assertEqual(len(M.info(self.job)['files']), 2, '和文稿栏“素材”记在同一份 备料.json')
        files = [out['file'] | {'name': 'C0031.MP4'}, {'name': 'x', 'path': '../项目.json', 'size': 1}]
        _, s = self.say('这两个文件帮我整理', files)
        me = [m for m in s['messages'] if m['role'] == 'me'][-1]; self.assertEqual([f['path'] for f in me['files']], ['01_原始素材/C0031.MP4'], '只认 01_原始素材 里真有的')
        self.assertIn('C0031.MP4', self.last_ai(s)['text'])

    def test_add_material_by_path(self):
        """用户说了文件在哪：AI 用 workbench --add-material（在 AI 那边跑，不走代办），同名不覆盖。"""
        src = self.tmp / '桌面' / 'B1.mov'; src.parent.mkdir(); src.write_bytes(b'v' * 2048); (self.pd / '01_原始素材').mkdir(exist_ok=True); (self.pd / '01_原始素材' / 'B1.mov').write_bytes(b'old')
        E.choose('codex'); _, s = self.say(f'把素材放进来，在 {src} 里')
        r = self.probe(); self.assertEqual(r['code'], 0, r); self.assertIn('B1 (2).mov', r['out'])
        self.assertEqual((self.pd / '01_原始素材' / 'B1 (2).mov').read_bytes(), b'v' * 2048); self.assertEqual((self.pd / '01_原始素材' / 'B1.mov').read_bytes(), b'old')
        self.assertTrue(src.exists(), '原文件不动')

    def test_gates_export_publish_settle(self):
        """对话里说“导出吧”“发布吧”“定稿”：程序直接回一句、给去那一栏的按钮，不交给 AI；问问题照常交给 AI。"""
        E.choose('codex'); n0 = len(self.runs())
        for text, col in (('导出吧', '审核线'), ('帮我发布到各平台', '上传文案'), ('定稿', '')):
            out = self.ok('/api/chat/send', {'text': text}); self.assertIn('gate', out)
            note = self.state()['messages'][-1]; self.assertEqual(note['role'], 'note'); self.assertIn('页面上亲手点', note['text'])
            self.assertEqual([a.get('col') for a in note['actions'] if a.get('col')], [col] if col else [])
        self.ok('/api/chat/send', {'text': '导出吧，然后发布到各平台'}); note = self.state()['messages'][-1]
        self.assertEqual([x['col'] for x in note['actions']], ['审核线', '上传文案'], '两件都说到：两条都回、两个按钮')
        self.assertEqual(len(self.runs()), n0, '没交给 AI')
        self.say('现在能导出了吗？'); self.assertEqual(len(self.runs()), n0 + 1, '问问题照常交给 AI')

    def test_ai_running_export_refused_by_chat_broker(self):
        """AI 自己去跑导出、上传：对话的代办不跑（只认页面上点的）。"""
        E.choose('codex'); _, s = self.say('导出试试看能不能')
        r = self.probe(); self.assertNotEqual(r['export']['code'], 0); self.assertIn('只认你在页面上亲手点', r['export']['err'])
        self.assertNotEqual(r['upload']['code'], 0); self.assertIn('只认你在页面上亲手点', r['upload']['err'])

    def test_stop(self):
        E.choose('codex'); self.ok('/api/chat/send', {'text': '慢慢看'})
        t = time.time()
        while time.time() - t < 20 and not (self.state()['busy'] and self.last_ai()['steps']): time.sleep(0.2)
        s = self.state(); self.assertTrue(s['busy']); self.assertEqual(s['engine']['state'], '处理中')
        code, _ = self.req('/api/chat/stop', {}, page_=False); self.assertEqual(code, 403, '停止也只收页面的')
        self.ok('/api/chat/stop'); s = self.wait_done(15); self.assertEqual(self.last_ai(s)['state'], '已停止')

    def test_busy_chat_holds_submit(self):
        """对话正在用这条会话时点“提交 AI”：等对话说完再接着做，不同时起两个 AI。"""
        E.choose('codex'); self.ok('/api/chat/send', {'text': '慢慢看'})
        while not self.state()['busy']: time.sleep(0.1)
        nid = self.ok('/api/draft/note', {'text': '标题太平了'})['id']; self.assertTrue(nid)
        self.ok('/api/send', {'column': '写稿'}); time.sleep(1.0)
        self.assertFalse(self.srv.runner.busy(), '对话在跑时“提交 AI”先等着'); self.assertEqual(sum(1 for c in self.runs()), 1)
        self.ok('/api/chat/stop'); t = time.time()
        while time.time() - t < 20 and len(self.runs()) < 2: time.sleep(0.2)
        self.assertEqual(len(self.runs()), 2, '对话停了以后“提交 AI”接着做')
        if self.srv.runner.thread: self.srv.runner.thread.join(30)
        code, out = self.req('/api/chat/send', {'text': 'x'})
        self.assertTrue(out['ok'] or '提交的意见' in out.get('error', ''))
        self.wait_done()

    def test_options(self):
        E.choose('claude'); _, s = self.say('帮我选一下')
        m = self.last_ai(s); self.assertEqual(m['options'], ['A 算 B-roll', 'B 算口播补录']); self.assertNotIn('选项', m['text'])

    def test_offline_and_errors(self):
        os.environ['FAKE_LOGGED'] = '0'; E._cache.clear(); E.choose('codex')
        out = self.ok('/api/chat/send', {'text': '在吗'}); self.assertTrue(out['offline'])
        e = self.state()['messages'][-1]; self.assertEqual(e['role'], 'err'); self.assertIn('登录', e['text']); self.assertEqual(e['actions'][0]['go'], 'settings')
        os.environ['FAKE_LOGGED'] = '1'; E._cache.clear(); E.engine_status('codex', fresh=True)
        _, s = self.say('fail 一下'); m = self.last_ai(s); self.assertEqual(m['state'], '出错'); self.assertIn('网络', m['error'])

    def test_history_persists_and_clear(self):
        E.choose('codex'); self.say('这个项目到哪一步了')
        again = C.for_project(self.job); self.assertEqual(len(again.state()['messages']), 2, '重开还在（记在项目里）')
        self.assertTrue((self.job.dir / '审片工作台' / '对话.json').is_file())
        code, _ = self.req('/api/chat/clear', {}, page_=False); self.assertEqual(code, 403)
        self.ok('/api/chat/clear'); self.assertEqual(self.state()['messages'], [])
        self.assertIn('codex', E.load(self.job)['sessions'], '项目的会话和“提交 AI”共用，清空记录不清会话')


class HomeChat(Base):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / '片子'; self.root.mkdir(); os.environ['FAKE_ROOT'] = str(self.root)
        from thinkcut.test_home import free_ports
        self.srv = HM.make_server(self.root, 0, free_ports(4)); self.port = self.srv.server_address[1]; self.base = f'http://127.0.0.1:{self.port}'
        self.cookie = HM.cookie_header(self.port); threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        with contextlib.suppress(Exception): self.srv.chat.stop()
        self.srv.shutdown(); self.srv.server_close(); super().tearDown()

    def test_page_has_sidebar(self):
        with urllib.request.urlopen(self.base + '/') as r: html = r.read().decode()
        self.assertIn('id="tcChatBtn"', html); self.assertIn('id="tcChat"', html)

    def test_open_project_then_hand_over(self):
        """首页开项：AI 交代办跑 project.py init → 说“转到项目：…” → 这句话带进新项目自己的对话（首页对话不写项目、不在首页接着干项目的活）。"""
        E.choose('claude'); s0 = self.state(); self.assertEqual(s0['kind'], 'home'); self.assertIsNone(s0['focus'])
        _, s = self.say('开项：卫生间防水刷多高，素材整理好')
        r = self.probe(); self.assertEqual(r['code'], 0, r); self.assertIn('后台 AI 不直接跑 project.py', r['err'], '开项走代办')
        pd = self.root / '261127 卫生间防水刷多高'; self.assertTrue((pd / '项目.json').is_file())
        self.assertEqual(s['focus']['path'], str(pd))
        note = [m for m in s['messages'] if m['role'] == 'note'][-1]; self.assertIn('转到它自己的对话', note['text']); self.assertEqual(note['actions'][0]['open'], str(pd))
        ais = [m for m in s['messages'] if m['role'] == 'ai']; self.assertEqual(len(ais), 1, '首页不接着干项目的活'); self.assertNotIn('接到项目：', ais[0]['text'])
        self.assertFalse((pd / '02_制作' / '对话写的.txt').exists())
        st = json.loads(self.runs()[-1]['argv'][self.runs()[-1]['argv'].index('--settings') + 1])['sandbox']['filesystem']['allowWrite']
        self.assertNotIn(str(pd), st); self.assertNotIn(str(self.root), st, '首页对话不能写项目文件夹')
        job = W.open_film(pd); pc = C.for_project(job).state()['messages']
        self.assertEqual(len(pc), 1); self.assertIn('从首页转过来', pc[0]['text']); self.assertEqual(pc[0]['actions'][0]['send'], '开项：卫生间防水刷多高，素材整理好')
        self.assertEqual(self.req('/api/chat/upload?name=a.mov', raw=b'1')[1]['ok'], False, '首页对话不收文件')
        out = self.ok('/api/chat/open', {'path': str(pd)}); self.assertTrue(out['url'].startswith('http://127.0.0.1:'))
        W.stop(job)
        self.assertFalse(self.req('/api/chat/open', {'path': str(self.tmp)})[1]['ok'], '只认放项目的文件夹里的真项目')
        self.assertIsInstance(self.srv.chat.set_focus(str(self.tmp)), str, '不是项目的不转')

    def test_archive_goes_through_home_broker_only_project_py(self):
        """首页对话存档：project.py 交首页代办跑；别的程序（达芬奇、导出）首页代办不跑。"""
        pd = self.root / '261101 老项目'; pd.mkdir(); (pd / '项目.json').write_text('{}', encoding='utf-8')
        E.choose('codex'); self.say(f'帮我存档 {pd} 吧')
        r = self.probe(); self.assertEqual(r['archive']['code'], 0, r); self.assertIn('project.py 替身：archive', r['archive']['out'])
        self.assertNotEqual(r['cover']['code'], 0); self.assertIn('首页对话只代办 project.py', r['cover']['err'])

    def test_home_gate_and_clear_resets_focus(self):
        E.choose('codex'); out = self.ok('/api/chat/send', {'text': '把 261124 那条发布了吧'}); self.assertEqual(out['gate'], 'publish')
        pd = self.root / '261101 老项目'; pd.mkdir(); (pd / '项目.json').write_text('{}', encoding='utf-8'); self.srv.chat.set_focus(str(pd))
        self.say('这个项目到哪一步了'); self.assertIsNotNone(self.state()['focus'])
        self.ok('/api/chat/clear'); s = self.state(); self.assertEqual(s['messages'], []); self.assertIsNone(s['focus'], '首页清空连接到的项目一起清')
        self.assertTrue((self.tmp / '首页对话' / '对话.json').is_file(), '首页的对话存在程序数据文件夹')


class Units(unittest.TestCase):
    def test_gate_words(self):
        for t in ('导出吧', '导出整片', '出成片', '发布吧', '上传到蚁小二', '存草稿', '定稿', '帮我确认一下'): self.assertTrue(C.gate(t), t)
        for t in ('能导出了吗', '导出的成片在哪', '素材上传到项目里', '怎么发布', '这个项目到哪一步了', '把 B-roll 放进来'): self.assertFalse(C.gate(t), t)

    def test_parse_tail(self):
        body, opts, focus = C.parse_tail('好了。\n选项：A 一｜B 二|C 三\n接到项目：/Volumes/x/261127 a')
        self.assertEqual((body, opts, focus), ('好了。', ['A 一', 'B 二', 'C 三'], '/Volumes/x/261127 a'))

    def test_trash_moves_into_recycle(self):
        tmp = Path(tempfile.mkdtemp()); rb = tmp / '#recycle'; rb.mkdir(); f = tmp / 'x.txt'; f.write_text('1')
        sys.path.insert(0, str(E.PROGRAM / 'bin')); import thinkcut_trash as T
        os.environ['THINKCUT_RECYCLE_ROOT'] = str(rb)
        try: out = T.trash(f)
        finally: os.environ.pop('THINKCUT_RECYCLE_ROOT')
        self.assertFalse(f.exists()); self.assertTrue(Path(out['回收站里']).is_file()); self.assertIn('对话挪走', out['回收站里']); shutil.rmtree(tmp)


if __name__ == '__main__':
    unittest.main()
