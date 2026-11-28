# -*- coding: utf-8 -*-
"""AI 引擎（2026-11-26）离线测试：审片台后台直接调本机 AI。不起真的 AI——用假的 claude／codex 命令行替身（同一个小脚本），
不碰钥匙串（THINKCUT_NO_KEYCHAIN）、不调付费接口（豆包识别换成假的）。run_tests.sh 会跑。
走通：提交 → 后台处理 → 结果回到栏里 → 用户采纳；接着上次的会话；换引擎新开会话；停止；AI 提问；出错说人话；
未装／未登录的提示和登录按钮；对话里挂着 --wait 时交给它（老办法）；权限参数收住；钥匙不回显、不进文件和 AI 的环境。"""
import json, os, shutil, stat, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['THINKCUT_NO_OPEN'] = '1'
os.environ.setdefault('THINKCUT_NO_KEYCHAIN', '1')
_TMP = Path(tempfile.mkdtemp(prefix='thinkcut-engine-')).resolve()
os.environ['THINKCUT_SECRETS_DIR'] = str(_TMP / 'secrets')          # 豆包 Key 在测试里落到这里的受限文件（doubao_credentials 的老规矩）
from thinkcut import workbench as W, board as B, writing as X, engine as E, settings as ST     # noqa: E402
from thinkcut.test_workbench import FakeDV                                                      # noqa: E402
from thinkcut.test_board import project                                                         # noqa: E402

FAKE = r'''#!/usr/bin/env python3
# 假的 AI 命令行：认得自己叫 claude 还是 codex，按 FAKE_AI_MODE 演一遍；FAKE_AI_DO 是要跑的 workbench 参数（JSON，每项一组）
import json, os, re, subprocess, sys, time
from pathlib import Path
name = os.path.basename(sys.argv[0]); a = sys.argv[1:]; mode = os.environ.get('FAKE_AI_MODE', 'reply')
log = os.environ.get('FAKE_AI_LOG')
if log:
    with open(log, 'a', encoding='utf-8') as f: f.write(json.dumps({'name': name, 'argv': a, 'cwd': os.getcwd(), 'env': {k: v for k, v in os.environ.items()}}, ensure_ascii=False) + '\n')
logged = os.environ.get('FAKE_LOGGED', '1') == '1'
if name == 'claude' and a[:2] == ['auth', 'status']: print(json.dumps({'loggedIn': logged})); sys.exit(0)
if name == 'codex' and a[:2] == ['login', 'status']:
    print('Logged in using ChatGPT' if logged else 'Not logged in'); sys.exit(0 if logged else 1)
def ev(x): print(json.dumps(x, ensure_ascii=False), flush=True)
prompt = a[a.index('-p') + 1] if '-p' in a else a[-1]; m = re.search(r'python3 "([^"]+)" workbench "([^"]+)"', prompt)
sid = (a[a.index('--session-id') + 1] if '--session-id' in a else a[a.index('--resume') + 1] if '--resume' in a else None)
if name == 'codex': ev({'type': 'thread.started', 'thread_id': a[2] if a[1:2] == ['resume'] else 'T-123'})
else: ev({'type': 'system', 'subtype': 'init', 'session_id': sid})
if mode == 'fail':
    if name == 'codex': ev({'type': 'turn.failed', 'error': {'message': "You've hit your usage limit."}})
    else: ev({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'result': 'usage limit reached', 'session_id': sid})
    sys.exit(1)
if mode == 'keyprobe':
    prog = Path(m[1]).resolve().parents[4]; sys.path.insert(0, str(prog / 'bin')); sys.path.insert(0, str(prog / 'core' / 'bin')); sys.path.insert(0, str(prog / 'projects' / 'bin'))
    res = {}
    try: import doubao_credentials; res['load_key'] = repr(doubao_credentials.load_key())
    except Exception as e: res['load_key'] = '拒绝：' + str(e)
    import keychain; res['keychain_enabled'] = keychain.enabled(); res['keychain_get'] = repr(keychain.get('cn.thinkcut.doubao', 'api-key'))
    try: import yxer_secure; res['yxer'] = repr(yxer_secure.credential())
    except Exception as e: res['yxer'] = '拒绝：' + str(e)
    sec = Path(os.environ.get('THINKCUT_SECRETS_DIR', '')); res['secrets_files'] = sorted(x.name for x in sec.iterdir()) if sec.is_dir() else []
    def run(*a): r = subprocess.run([sys.executable, *a], capture_output=True, text=True, timeout=60); return {'code': r.returncode, 'out': r.stdout, 'err': r.stderr}
    res['tts'] = run(str(prog / 'core' / 'bin' / 'doubao_tts.py'), '--project', os.getcwd(), '--out', os.path.join(os.getcwd(), '合成.txt'))
    res['tts_outside'] = run(str(prog / 'core' / 'bin' / 'doubao_tts.py'), '--project', os.getcwd(), '--out', '/tmp/../Users/逃出去.txt')
    res['yxer_safe'] = run(str(prog / 'projects' / 'bin' / 'yxer_secure.py'), 'draft', 'save', '--dry-run')
    Path(os.environ['FAKE_PROBE']).write_text(json.dumps(res, ensure_ascii=False), encoding='utf-8')
if mode == 'davinci':
    prog = Path(m[1]).resolve().parents[4]; ed = str(prog / 'studio/skill/thinkcut-edit/scripts/thinkcut_edit.py')
    def run(*a, cwd=None): r = subprocess.run([sys.executable, *a], capture_output=True, text=True, timeout=120, cwd=cwd); return {'code': r.returncode, 'out': r.stdout, 'err': r.stderr}
    res = {'review': run(ed, 'review', m[2]), 'cover': run(str(prog / 'cover/skill/thinkcut-cover/scripts/thinkcut_cover.py'), 'gen', os.getcwd(), 'portrait'),
           'list_local': run(ed, 'workbench', m[2], '--list'),
           'export': run(ed, 'export', m[2]), 'upload': run(str(prog / 'projects/bin/project.py'), 'upload', os.getcwd())}
    for k, f in (os.environ.get('FAKE_CHUPIAN') or '{}') and json.loads(os.environ.get('FAKE_CHUPIAN') or '{}').items():
        res[k] = run(f, '--help', cwd=str(Path(f).parent))
    Path(os.environ['FAKE_PROBE']).write_text(json.dumps(res, ensure_ascii=False), encoding='utf-8')
if mode == 'sleep':
    ev({'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'ls 02_制作'}} if name == 'codex' else
       {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/x/P1.md'}}]}})
    time.sleep(120); sys.exit(0)
for args in json.loads(os.environ.get('FAKE_AI_DO') or '[]'):
    cmd = [sys.executable, '-B', m[1], 'workbench', m[2], *args]
    if name == 'codex': ev({'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'python3 thinkcut_edit.py workbench P1 ' + ' '.join(args)}})
    else: ev({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'python3 thinkcut_edit.py workbench P1 ' + ' '.join(args)}}]}})
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: print(r.stdout + r.stderr, file=sys.stderr); sys.exit(3)
if name == 'codex': ev({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '改好了标题，请你看看。'}}); ev({'type': 'turn.completed'})
else:
    ev({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '改好了标题，请你看看。'}]}})
    ev({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '改好了标题，请你看看。', 'session_id': sid})
'''


FAKE_TTS = '''import json, os, sys
sys.path.insert(0, %r); import doubao_credentials
a = sys.argv[1:]; out = a[a.index('--out') + 1]; k = doubao_credentials.load_key()
open(out, 'w', encoding='utf-8').write('合成好了')
print(json.dumps({'代办里拿到钥匙': bool(k), '后台标记': bool(os.environ.get('THINKCUT_AI_BACKGROUND')), '原文': k}, ensure_ascii=False))
'''
FAKE_YX = '''import sys
print('蚁小二替身：' + ' '.join(sys.argv[1:]))
'''


FAKE_DV = '''import os, sys
print('达芬奇替身：' + ' '.join(sys.argv[1:2]) + '｜后台标记：' + str(bool(os.environ.get('THINKCUT_AI_BACKGROUND'))))
'''
FAKE_GEN = '''import os, sys
from pathlib import Path
out = Path(sys.argv[2]) / '封面' / '生图.png'; out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b'PNG')
print('生图替身：' + str(out) + '｜后台标记：' + str(bool(os.environ.get('THINKCUT_AI_BACKGROUND'))))
'''


def fake_program(folder):
    (folder / 'core' / 'bin').mkdir(parents=True); (folder / 'projects' / 'bin').mkdir(parents=True)
    (folder / 'core' / 'bin' / 'doubao_tts.py').write_text(FAKE_TTS % str(E.PROGRAM / 'core' / 'bin'), encoding='utf-8')
    (folder / 'projects' / 'bin' / 'yxer_secure.py').write_text(FAKE_YX, encoding='utf-8')
    for rel, code in (('studio/skill/thinkcut-edit/scripts/thinkcut_edit.py', FAKE_DV), ('cover/skill/thinkcut-cover/scripts/thinkcut_cover.py', FAKE_GEN)):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True); (folder / rel).write_text(code, encoding='utf-8')
    return folder


def fake_bin(folder, names=('claude', 'codex')):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        f = folder / n; f.write_text(FAKE.replace('/usr/bin/env python3', sys.executable), encoding='utf-8'); f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return folder


class Base(unittest.TestCase):
    engines = ('claude', 'codex')

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve(); E._cache.clear()
        self.env0 = dict(os.environ)
        os.environ.update(THINKCUT_AI_PATH=str(fake_bin(self.tmp / 'bin', self.engines)), THINKCUT_AI_SETTINGS=str(self.tmp / 'AI引擎设置.json'),
                          FAKE_AI_LOG=str(self.tmp / 'fake.log'), FAKE_LOGGED='1', FAKE_AI_MODE='reply')
        os.environ.pop('FAKE_AI_DO', None)
        self.pd, self.job, _ = project(self.tmp); self.srv = W.make_server(self.job, 0, FakeDV())
        self.port = self.srv.server_address[1]; self.base = f'http://127.0.0.1:{self.port}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        with __import__('contextlib').suppress(Exception): self.srv.runner.stop()
        self.srv.shutdown(); self.srv.server_close(); os.environ.clear(); os.environ.update(self.env0); E._cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, p, body=None, page=True):
        h = {'Content-Type': 'application/json', **({'Origin': self.base, 'Sec-Fetch-Site': 'same-origin', 'Cookie': W.cookie_header(self.base)} if page else {})}
        q = urllib.request.Request(self.base + p, json.dumps(body or {}).encode() if body is not None else None, h)
        try:
            with urllib.request.urlopen(q, timeout=30) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def ok(self, p, body=None):
        code, out = self.req(p, body or {}); self.assertEqual(code, 200, out); self.assertTrue(out['ok'], out); return out

    def data(self): return self.req('/api/data', None, page=False)[1]

    def do(self, *cmds): os.environ['FAKE_AI_DO'] = json.dumps([list(c) for c in cmds], ensure_ascii=False)

    def rewrite_spec(self, title='新标题'):
        f = self.tmp / '改写.json'; f.write_text(json.dumps({'note': '我理解你要标题更抓人，所以改了标题。', 'changes': [{'field': '标题', 'new': title}]}, ensure_ascii=False), encoding='utf-8'); return str(f)

    def note(self, text='标题太平了'):
        nid = self.ok('/api/draft/note', {'text': text})['id']; return nid

    def submit(self, col='写稿', join=True):
        out = self.ok('/api/send', {'column': col})
        if join and self.srv.runner.thread: self.srv.runner.thread.join(60)
        return out

    def calls(self):
        f = self.tmp / 'fake.log'
        return [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()] if f.exists() else []

    def runs(self): return [c for c in self.calls() if c['argv'][:2] not in (['auth', 'status'], ['login', 'status'])]

    def run_rec(self): return E.load(self.job).get('run') or {}


class FlowTests(Base):
    def test_submit_background_result_adopt(self):
        """文稿栏：提意见 → 提交 AI → 后台 AI（替身 Codex）交改写建议 → 栏里出现建议 → 用户采纳才写进文案文件。"""
        E.choose('codex'); nid = self.note(); self.do(['--rewrite', nid, '--json', self.rewrite_spec()])
        a = self.data()['ai']; self.assertEqual((a['engine'], a['state']), ('codex', '在线'))
        out = self.submit(); self.assertTrue(out['engine'] and out['listening'] and not out['waiter'], out)
        r = self.run_rec(); self.assertEqual(r['state'], '完成', r); self.assertEqual(r['summary'], '改好了标题，请你看看。')
        self.assertTrue(any('交了 稿1 的改写建议' in s['text'] for s in r['steps']), r['steps'])
        n = X.note(X.load(self.job), nid); self.assertEqual(n['status'], '改写'); self.assertEqual(n['proposal']['changes'][0]['new'], '新标题')
        self.assertIn('旧的标题', (self.pd / '03_成片' / 'P1.md').read_text(encoding='utf-8'))          # 还没采纳：文件没动
        self.ok('/api/draft/decide', {'id': nid, 'accept': True, 'which': 'all'})
        self.assertIn('## 标题\n\n新标题\n', (self.pd / '03_成片' / 'P1.md').read_text(encoding='utf-8'))
        c = self.runs()[0]; argv = c['argv']
        self.assertEqual(Path(c['cwd']).resolve(), self.pd.resolve())                                         # 工作目录＝项目文件夹
        self.assertIn('--sandbox', argv); self.assertEqual(argv[argv.index('--sandbox') + 1], 'workspace-write')
        self.assertIn('approval_policy="never"', argv); self.assertFalse(any('dangerously' in x or 'bypass' in x.lower() for x in argv))
        self.assertIn('thinkcut-edit/SKILL.md', argv[-1]); self.assertIn('不发布', argv[-1]); self.assertIn('稿1', argv[-1]); self.assertEqual(c['env']['THINKCUT_AI_BACKGROUND'], '1')
        self.assertEqual(E.load(self.job)['sessions']['codex']['id'], 'T-123')
        # 第二次提交接着用同一条会话
        nid2 = self.note('封面文字也改一下'); self.do(['--reply', nid2, '--note', '封面文字我觉得可以不改'])
        self.submit(); argv = self.runs()[-1]['argv']; self.assertEqual(argv[:3], ['exec', 'resume', 'T-123'])
        self.assertIn('sandbox_mode="workspace-write"', argv); self.assertEqual(self.run_rec()['state'], '完成')

    def test_switch_engine_new_session_and_claude_permissions(self):
        E.choose('codex'); nid = self.note(); self.do(['--reply', nid, '--note', '收到'])
        self.submit(); self.assertIn('codex', E.load(self.job)['sessions'])
        self.ok('/api/settings/engine', {'engine': 'claude'}); time.sleep(0.3)
        nid2 = self.note('再看看'); self.do(['--reply', nid2, '--note', '好'])
        self.submit(); c = self.runs()[-1]; argv = c['argv']; self.assertEqual(c['name'], 'claude')
        self.assertIn('--session-id', argv); self.assertNotIn('--resume', argv)                     # 换了引擎：新开会话
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'dontAsk'); self.assertNotIn('bypassPermissions', argv)
        self.assertFalse(any('dangerously' in x for x in argv))
        deny = argv[argv.index('--disallowedTools') + 1: argv.index('--session-id')]
        self.assertTrue(any('yxer publish' in x for x in deny) and any('security' in x for x in deny) and 'WebFetch' in deny, deny)
        allow = argv[argv.index('--allowedTools') + 1: argv.index('--disallowedTools')]
        self.assertIn('Edit(./**)', allow); self.assertNotIn('Bash', allow)                             # 不放开所有命令
        sb = json.loads(argv[argv.index('--settings') + 1])['sandbox']                                        # Claude 自带沙盒：开着、不许退回无沙盒、挡钥匙串、程序文件夹不能写
        self.assertTrue(sb['enabled'] and sb['failIfUnavailable']); self.assertFalse(sb['allowUnsandboxedCommands'])
        self.assertIn('~/Library/Keychains', sb['filesystem']['denyRead']); self.assertIn(str(E.PROGRAM), sb['filesystem']['denyWrite'])
        self.assertNotIn('network', sb); self.assertNotIn('--add-dir', argv)                                    # 不放行任何网络域名；程序文件夹不加进可写
        self.assertNotIn('Application Support', ' '.join(sb['filesystem']['allowWrite']))
        rec = E.load(self.job); self.assertEqual(rec['engine_last'], 'claude'); self.assertNotIn('codex', rec['sessions'])
        self.assertEqual(rec['sessions']['claude']['id'], argv[argv.index('--session-id') + 1]); self.assertEqual(self.run_rec()['state'], '完成')
        # 再提交：Claude 接着用它自己的会话
        nid3 = self.note('还有'); self.do(['--reply', nid3, '--note', '好']); self.submit()
        argv = self.runs()[-1]['argv']; self.assertEqual(argv[argv.index('--resume') + 1], rec['sessions']['claude']['id'])

    def test_stop(self):
        E.choose('codex'); self.note(); os.environ['FAKE_AI_MODE'] = 'sleep'
        self.submit(join=False)
        for _ in range(100):
            if any(s['text'] == '查看项目文件' for s in self.run_rec().get('steps') or []): break
            time.sleep(0.1)
        self.assertEqual(self.data()['ai']['state'], '处理中'); self.assertEqual(self.data()['board']['ucols']['写稿']['counts']['AI 在改'], 1)
        code, _ = self.req('/api/ai/stop', {}, page=False); self.assertEqual(code, 403)                     # 停止也只收页面的
        t0 = time.time(); self.ok('/api/ai/stop'); self.assertLess(time.time() - t0, 15)
        r = self.run_rec(); self.assertEqual(r['state'], '已停止'); self.assertIn('待提交', r['error'])
        d = self.data(); self.assertEqual(d['board']['ucols']['写稿']['counts']['待发送'], 1)               # 没做完的退回“待提交”
        self.assertNotEqual(d['ai']['state'], '处理中')

    def test_ai_asks_user(self):
        """AI 要问用户：用审片台已有的提问＋选项回写，不在后台卡住；页面上这一条变“待审阅”，带选项按钮。"""
        E.choose('claude'); nid = self.note('开头要不要改'); self.do(['--reply', nid, '--status', '问你', '--note', '开头改成问句还是陈述句？', '--options', 'A 问句|B 陈述句'])
        self.submit(); r = self.run_rec(); self.assertEqual(r['state'], '完成', r)
        self.assertTrue(any('向你提了个问题' in s['text'] for s in r['steps']), r['steps'])
        n = X.note(X.load(self.job), nid); self.assertEqual(n['status'], '问你'); self.assertEqual(n['thread'][-1]['options'], ['A 问句', 'B 陈述句'])
        d = self.data(); self.assertEqual(d['board']['ucols']['写稿']['counts']['AI 在改'], 0)
        self.assertEqual(next(x for x in d['board']['writing']['notes'] if str(x['id']) in (nid, nid[1:]))['ustate'], '待你看')

    def test_error_said_plainly(self):
        E.choose('codex'); self.note(); os.environ['FAKE_AI_MODE'] = 'fail'; self.submit()
        r = self.run_rec(); self.assertEqual(r['state'], '出错'); self.assertIn('额度', r['error']); self.assertNotIn('usage limit', r['error'])
        a = self.data()['ai']; self.assertEqual(a['state'], '出错'); self.assertIn('额度', a['why'])
        self.assertEqual(self.data()['board']['ucols']['写稿']['counts']['待发送'], 1)

    def test_waiter_gets_it_old_way(self):
        """对话里的 AI 挂着 --wait：提交交给它，不起后台 AI。"""
        E.choose('codex'); self.note(); urllib.request.urlopen(self.base + '/api/wait').read()
        out = self.submit(join=False); self.assertTrue(out['waiter'] and out['listening']); self.assertFalse(out['engine'])
        time.sleep(0.5); self.assertEqual(self.runs(), []); self.assertEqual(B.pick(self.job), [('写稿', '稿1')])
        self.assertEqual(self.data()['ai']['state'], '在线'); self.assertTrue(self.data()['ai']['waiter'])

    def test_offline_saved_then_auto_start(self):
        """AI 没登录：提交只存下（页面写“已存下，等 AI 上线”），登录好以后自动开始。"""
        E.choose('codex'); os.environ['FAKE_LOGGED'] = '0'; E._cache.clear(); nid = self.note(); self.do(['--reply', nid, '--note', '收到'])
        out = self.submit(join=False); self.assertFalse(out['listening'] or out['engine']); self.assertEqual(self.runs(), [])
        d = self.data(); self.assertFalse(d['board']['send']['listening']); self.assertEqual(d['ai']['state'], '未登录'); self.assertIn('登录', d['ai']['why'])
        self.assertEqual(d['board']['ucols']['写稿']['counts']['AI 在改'], 1)
        page = W.PAGE.read_text(encoding='utf-8'); self.assertIn('已存下，等 AI 上线', page)
        os.environ['FAKE_LOGGED'] = '1'; E._cache.clear()
        t = threading.Thread(target=W.watch_ai, args=(self.srv, 0.1), daemon=True); t.start()
        for _ in range(100):
            if self.run_rec().get('state') == '完成': break
            time.sleep(0.1)
        self.assertEqual(self.run_rec()['state'], '完成'); self.assertEqual(X.note(X.load(self.job), nid)['status'], '已回')


class BrokerTests(Base):
    engines = ('codex',)
    SECRET = 'sk-BROKER-TEST-KEY-0987654321abcdefXYZ'

    def test_ai_cannot_read_keys_but_broker_runs_for_it(self):
        sys.path.insert(0, str(E.PROGRAM / 'core' / 'bin')); import doubao_credentials as dc
        self.assertEqual(dc.save_key(self.SECRET), '文件')                 # 服务这边有钥匙（测试里是受限文件）
        os.environ.update(FAKE_AI_MODE='keyprobe', FAKE_PROBE=str(self.tmp / 'probe.json'), THINKCUT_BROKER_PROGRAM=str(fake_program(self.tmp / '替身程序')))
        E.choose('codex'); self.note(); self.submit()
        r = json.loads((self.tmp / 'probe.json').read_text(encoding='utf-8'))
        self.assertTrue(r['load_key'].startswith('拒绝：后台 AI 不直接读豆包钥匙'), r['load_key'])
        self.assertFalse(r['keychain_enabled']); self.assertEqual(r['keychain_get'], 'None')
        self.assertTrue(r['yxer'].startswith('拒绝：'), r['yxer']); self.assertEqual(r['secrets_files'], [])
        t = r['tts']; self.assertEqual(t['code'], 0, t); self.assertIn('交给审片台代办', t['err'])
        got = json.loads(t['out'].strip().splitlines()[-1]); self.assertTrue(got['代办里拿到钥匙']); self.assertFalse(got['后台标记'])
        self.assertEqual(got['原文'], '[已隐藏]')                                                                # 万一程序打印了钥匙，代办也抹掉
        self.assertEqual((self.pd / '合成.txt').read_text(encoding='utf-8'), '合成好了')
        self.assertEqual(r['tts_outside']['code'], 2); self.assertIn('不在项目文件夹里', r['tts_outside']['err'])
        self.assertEqual(r['yxer_safe']['code'], 0); self.assertIn('蚁小二替身：draft save --dry-run', r['yxer_safe']['out'])
        blob = json.dumps(r, ensure_ascii=False) + (self.tmp / 'fake.log').read_text(encoding='utf-8')
        self.assertNotIn(self.SECRET, blob)                                                                     # AI 那边任何地方都没出现过钥匙原文
        c = self.runs()[-1]; self.assertEqual(c['env']['THINKCUT_NO_KEYCHAIN'], '1'); self.assertTrue(c['env']['THINKCUT_BROKER_DIR'].endswith('代办'))
        self.assertIn('sandbox_workspace_write.network_access=false', c['argv'])
        self.assertEqual(list(Path(c['env']['THINKCUT_BROKER_DIR']).glob('*.json')), [])                      # 单子都处理完、收干净了

    def test_davinci_and_image_gen_go_through_broker(self):
        """替身达芬奇、替身生图：后台 AI 照常跑我们的程序，自动交给审片台代办；workbench 回写留在 AI 那边跑。"""
        os.environ.update(FAKE_AI_MODE='davinci', FAKE_PROBE=str(self.tmp / 'probe.json'), THINKCUT_BROKER_PROGRAM=str(fake_program(self.tmp / '替身程序')))
        E.choose('codex'); self.note(); self.submit()
        r = json.loads((self.tmp / 'probe.json').read_text(encoding='utf-8'))
        self.assertEqual(r['review']['code'], 0, r['review']); self.assertIn('达芬奇替身：review｜后台标记：False', r['review']['out'])
        self.assertIn('交给审片台代办', r['review']['err'])
        self.assertEqual(r['cover']['code'], 0, r['cover']); self.assertIn('生图替身', r['cover']['out']); self.assertTrue((self.pd / '封面' / '生图.png').is_file())
        self.assertEqual(r['list_local']['code'], 0, r['list_local']); self.assertNotIn('代办', r['list_local']['err'])     # 回写、读清单不走代办
        self.assertNotIn('达芬奇替身', r['list_local']['out'])

    def test_broker_keeps_export_and_upload_gates(self):
        """真程序走代办：整片导出、上传照样只认用户在审片台点的定稿（代办不绕过 export_gate／final_gate）；项目里改过的 出片.py 不代跑。"""
        tpl = E.SKILL / 'assets' / 'hyperframes_template'; good = self.pd / '动画' / '出片.py'; bad = self.pd / '动画改' / '出片.py'
        good.parent.mkdir(parents=True); bad.parent.mkdir(parents=True)
        shutil.copy(tpl / '出片.py', good); bad.write_text((tpl / '出片.py').read_text(encoding='utf-8') + '\nimport os  # AI 改过\n', encoding='utf-8')
        os.environ.update(FAKE_AI_MODE='davinci', FAKE_PROBE=str(self.tmp / 'probe.json'), FAKE_CHUPIAN=json.dumps({'chupian_ok': str(good), 'chupian_bad': str(bad)}))
        os.environ.pop('THINKCUT_BROKER_PROGRAM', None)
        E.choose('codex'); self.note(); self.submit()
        r = json.loads((self.tmp / 'probe.json').read_text(encoding='utf-8'))
        self.assertNotEqual(r['export']['code'], 0); self.assertIn('交给审片台代办', r['export']['err'])
        self.assertIn('还没在审片台点审片栏“定稿”', r['export']['err'] + r['export']['out'])
        self.assertNotEqual(r['upload']['code'], 0); self.assertIn('没有用户确认过的导出记录', r['upload']['err'] + r['upload']['out'])
        self.assertEqual(r['chupian_ok']['code'], 0, r['chupian_ok']); self.assertIn('HyperFrames', r['chupian_ok']['out'] + r['chupian_ok']['err'])
        self.assertEqual(r['chupian_bad']['code'], 2); self.assertIn('和程序里的模板不一样', r['chupian_bad']['err'])

    def test_broker_checks(self):
        sys.path.insert(0, str(E.PROGRAM / 'bin')); import thinkcut_broker as TB
        roots = [self.pd]
        with self.assertRaises(ValueError): TB.check({'script': 'rm.py', 'cwd': str(self.pd), 'argv': []}, roots)
        with self.assertRaises(ValueError): TB.check({'script': 'doubao_tts.py', 'cwd': '/', 'argv': []}, roots)
        with self.assertRaises(ValueError): TB.check({'script': 'doubao_tts.py', 'cwd': str(self.pd), 'argv': ['--out=../../x']}, roots)
        prog, cwd = TB.check({'script': 'transcribe_video.py', 'cwd': str(self.pd), 'argv': ['--video', '01_原始素材/a.mov', '--work-dir', str(self.pd / '转写')]}, roots)
        self.assertEqual(prog.name, 'transcribe_video.py'); self.assertEqual(cwd, self.pd.resolve())
        os.environ.update(THINKCUT_AI_BACKGROUND='1', THINKCUT_BROKER_DIR=str(self.tmp / 'q'), THINKCUT_BROKER_TAKE='0.5')
        import io
        err = io.StringIO(); self.assertEqual(TB.forward('doubao_tts.py', ['x'], io.StringIO(), err), 75); self.assertIn('审片台没有接', err.getvalue())   # 没人接单：说清楚，不挂死


class StatusTests(Base):
    engines = ('codex',)

    def test_not_installed_and_not_logged_in(self):
        st = {x['id']: x for x in E.all_status(True)}; self.assertEqual(st['claude']['state'], '未装'); self.assertIn('没找到', st['claude']['why'])
        self.assertEqual(st['codex']['state'], '在线'); self.assertEqual(E.chosen(), 'codex')                 # 没选过：用第一个已登录的
        os.environ['FAKE_LOGGED'] = '0'; st = E.engine_status('codex', fresh=True); self.assertEqual(st['state'], '未登录'); self.assertIn('登录', st['why'])
        s = self.req('/api/settings', None, page=False)[1]; self.assertEqual({e['id']: e['state'] for e in s['engines']}, {'claude': '未装', 'codex': '未登录'})
        code, _ = self.req('/api/settings/login', {'engine': 'codex'}, page=False); self.assertEqual(code, 403)   # 登录按钮也只收页面的
        out = self.ok('/api/settings/login', {'engine': 'codex'}); self.assertTrue(out['cmd'].endswith(' login')); self.assertFalse(out['opened'])   # 测试不弹终端
        self.assertFalse(self.req('/api/settings/login', {'engine': 'claude'})[1]['ok'])                      # 没装的不能登录
        os.environ['THINKCUT_AI_PATH'] = str(self.tmp / 'empty'); E._cache.clear()
        a = self.data()['ai']; self.assertEqual(a['state'], '未装'); self.assertFalse(a['online'])
        page = (W.ASSETS / '设置' / 'index.html').read_text(encoding='utf-8'); self.assertIn('data-login', page)
        code, html = 0, urllib.request.urlopen(self.base + '/settings').read().decode(); self.assertIn('豆包语音', html); self.assertIn('蚁小二', html)

    def test_claude_login_command(self):
        fake_bin(self.tmp / 'bin', ('claude',)); E._cache.clear(); os.environ['FAKE_LOGGED'] = '0'
        st = E.engine_status('claude', True); self.assertEqual(st['state'], '未登录'); self.assertIn('桌面端', st['why'])
        self.assertTrue(E.login('claude')['cmd'].endswith(' auth login'))

    def test_steps_in_plain_words(self):
        self.assertEqual(E.cmd_step('python3 "/a/thinkcut_edit.py" workbench "/p" --reply 稿2 --status 问你 --note x'), '向你提了个问题（稿2），等你在审片台回答')
        self.assertEqual(E.cmd_step('python3 x/thinkcut_edit.py workbench /p --publish 封面 --kind 多选一'), '发布到“封面”栏')
        self.assertEqual(E.tool_step('Edit', {'file_path': '/p/02_制作/分镜.json'}), '改了 分镜.json')
        self.assertEqual(E.cmd_step("/bin/zsh -lc \"sed -n '1,220p' '/x/thinkcut-edit/SKILL.md'\""), '读了 SKILL.md')
        self.assertEqual(E.cmd_step("/bin/zsh -lc \"python3 - <<'PY'\nimport json\nPY\""), '运行一小段脚本核对结果')
        self.assertEqual(E.cmd_step("/bin/zsh -lc 'python3 \"/a/thinkcut_edit.py\" workbench \"/p\" --rewrite 稿1 --json /t/x.json'"), '交了 稿1 的改写建议，等你逐处采纳')
        self.assertIn('登录', E.humane('claude', 'Invalid API key · Please run /login')); self.assertIn('网络', E.humane('codex', 'stream error: network down'))
        raw = json.dumps({'type': 'error', 'status': 400, 'error': {'type': 'invalid_request_error', 'message': "The 'gpt-x' model is not supported when using Codex with a ChatGPT account.", 'code': 'model_not_found'}})
        w = E.humane('codex', raw); self.assertIn('（gpt-x）你的账号用不了', w); self.assertIn('“模型”', w); self.assertNotIn('{', w)

    def test_model_setting(self):
        self.assertFalse(self.req('/api/settings/model', {'engine': 'codex', 'model': 'a b;rm'})[1]['ok'])
        self.ok('/api/settings/model', {'engine': 'codex', 'model': 'gpt-6.1-sol'}); self.assertEqual(E.model_of('codex'), 'gpt-6.1-sol')
        s = self.req('/api/settings', None, page=False)[1]; self.assertEqual(next(e for e in s['engines'] if e['id'] == 'codex')['model'], 'gpt-6.1-sol')
        E.choose('codex'); nid = self.note(); self.do(['--reply', nid, '--note', '收到']); self.submit()
        argv = self.runs()[-1]['argv']; self.assertEqual(argv[argv.index('-m') + 1], 'gpt-6.1-sol')
        self.ok('/api/settings/model', {'engine': 'codex', 'model': ''}); self.assertEqual(E.model_of('codex'), '')


class KeyTests(Base):
    engines = ('codex',)
    SECRET = 'sk-THIS-IS-A-TEST-KEY-1234567890abcdef'

    def test_keys_never_echoed_or_given_to_ai(self):
        K = self.SECRET
        code, _ = self.req('/api/settings/doubao', {'key': K}, page=False); self.assertEqual(code, 403)
        out = self.ok('/api/settings/doubao', {'key': K}); self.assertEqual(out['where'], '文件'); self.assertNotIn(K, json.dumps(out))
        self.assertFalse(self.req('/api/settings/doubao', {'key': 'bad key'})[1]['ok'])
        s = self.req('/api/settings', None, page=False)[1]; self.assertTrue(s['doubao']['configured']); self.assertNotIn(K, json.dumps(s, ensure_ascii=False))
        import doubao_direct_asr as A
        seen = {}
        def fake_rec(wav, key, timeout=0, **kw): seen['wav'] = Path(wav).suffix; seen['key'] = key == K; return {'result': {'text': '你好，这是思刻的语音识别测试。'}}, A.RESOURCES[0]
        old = (A.recognize_any, ST.sample_audio); A.recognize_any = fake_rec; ST.sample_audio = lambda d: Path(d, 'x.wav')
        try: out = self.ok('/api/settings/doubao/test')
        finally: A.recognize_any, ST.sample_audio = old
        self.assertEqual(out['text'], '你好，这是思刻的语音识别测试。'); self.assertTrue(seen['key']); self.assertNotIn(K, json.dumps(out, ensure_ascii=False))
        self.assertTrue(self.req('/api/settings', None, page=False)[1]['doubao']['verified'])
        # 蚁小二：存进钥匙串那一条（测试里换成假钥匙串）
        box = {}
        class KC:
            enabled = staticmethod(lambda: True)
            get = staticmethod(lambda s, a: box.get((s, a)))
            put = staticmethod(lambda s, a, v: box.__setitem__((s, a), v))
        old = ST._keychain; ST._keychain = lambda: KC
        try:
            self.ok('/api/settings/yxer', {'key': K}); self.assertEqual(box[(ST.YX_SERVICE, ST.YX_ACCOUNT)], K)
            s = self.req('/api/settings', None, page=False)[1]; self.assertTrue(s['yxer']['configured']); self.assertNotIn(K, json.dumps(s, ensure_ascii=False))
        finally: ST._keychain = old
        # 后台 AI 跑一次：钥匙不在提示、参数、环境、记录文件里
        E.choose('codex'); nid = self.note(); self.do(['--reply', nid, '--note', '收到']); self.submit()
        self.assertEqual(self.run_rec()['state'], '完成')
        blob = (self.tmp / 'fake.log').read_text(encoding='utf-8'); self.assertNotIn(K, blob)
        for f in [*W.home(self.job).rglob('*'), Path(os.environ['THINKCUT_AI_SETTINGS'])]:
            if f.is_file(): self.assertNotIn(K, f.read_text(encoding='utf-8', errors='replace'), f)
        self.assertNotIn(K, json.dumps(self.data(), ensure_ascii=False))


class AiTools(Base):
    """后台 AI 带的技能、MCP、依赖（2026-11-28 用户：“对话栏没有装 mcp 和各种必须的依赖和应用，包括必须的 skill，所以他干活总是错的……claude 和 codex 都装上，也默认都装”）。
    假的 HOME：Codex 配了达芬奇、chrome-devtools、操控电脑三个 MCP，装了一堆不相干的技能；旧名 old-edit 和 thinkcut-edit 指同一份。"""

    def setUp(self):
        super().setUp()
        from thinkcut import aitools as A
        self.A = A; h = self.home = self.tmp / 'home'; os.environ['HOME'] = str(h)
        cx = h / '.codex'; (cx / 'skills').mkdir(parents=True)
        (cx / 'config.toml').write_text('model = "x"\n[mcp_servers.davinci-resolve]\ncommand = "python"\n[mcp_servers.davinci-resolve.env]\nA = "1"\n'
                                        '[mcp_servers.chrome-devtools]\ncommand = "npx"\n[mcp_servers.computer-use]\ncommand = "x"\nenabled = false\n', encoding='utf-8')
        for root, n in ((cx / 'skills', 'hyperframes'), (cx / 'skills', 'media-use'), (cx / 'skills' / '.system', 'imagegen'), (h / '.agents' / 'skills', 'lark-doc'),
                        (cx / 'skills' / '@org', 'yixiaoer-cli')):
            (root / n).mkdir(parents=True); (root / n / 'SKILL.md').write_text(f'---\nname: {n}\n---\n', encoding='utf-8')
        edit = E.PROGRAM / 'studio' / 'skill' / 'thinkcut-edit'
        (cx / 'skills' / 'thinkcut-edit').symlink_to(edit); (cx / 'skills' / 'old-edit').symlink_to(edit)
        (h / '.claude.json').write_text(json.dumps({'mcpServers': {'chrome-devtools': {'command': 'npx'}}}), encoding='utf-8')

    def submit_as(self, eid):
        E.choose(eid); nid = self.note(); self.do(['--reply', nid, '--note', '收到']); self.submit(); self.assertEqual(self.run_rec()['state'], '完成')
        return self.runs()[-1]

    def test_claude_submit_loads_skills_and_only_whitelisted_mcp(self):
        argv = self.submit_as('claude')['argv']
        self.assertEqual(argv[argv.index('--setting-sources') + 1], 'project')          # 不为技能放开用户的全部设置
        plug = Path(argv[argv.index('--plugin-dir') + 1]) / 'skills'                      # “提交 AI”以前一个 ThinkCut 技能都没有
        for n in ('thinkcut-edit', 'thinkcut-project', 'thinkcut-cover', 'thinkcut-upload', 'thinkcut-feedback', 'hyperframes'):
            self.assertTrue((plug / n / 'SKILL.md').is_file(), n)
        self.assertFalse((plug / 'media-use').exists())
        self.assertIn('--strict-mcp-config', argv)                                          # claude.ai 连接器、你自己配的 MCP 都不进来
        servers = json.loads(argv[argv.index('--mcp-config') + 1])['mcpServers']                   # 白名单只有达芬奇（2026-11-28 用户定开），经护栏起
        self.assertEqual(list(servers), ['davinci-resolve']); self.assertIn('davinci_mcp_guard.py', servers['davinci-resolve']['args'][0])
        self.assertNotIn('env', servers['davinci-resolve'])                                  # 用户配置里的环境变量不放进命令行，护栏运行时自己读
        allow = argv[argv.index('--allowedTools') + 1: argv.index('--disallowedTools')]; deny = argv[argv.index('--disallowedTools') + 1: argv.index('--session-id')]
        self.assertIn('mcp__davinci-resolve', allow)
        for t in ('render', 'render_presets', 'project_manager', 'project_manager_database', 'resolve_control', 'timeline_versioning', 'script_plugin'):
            self.assertIn(f'mcp__davinci-resolve__{t}', deny)
        self.assertIn('hyperframes', argv[argv.index('-p') + 1])                             # 提示里说清辅助技能只当参考

    def test_codex_only_whitelisted_skills_and_mcp(self):
        for n in range(2):                                                                     # 新开会话、接着上次的会话都一样
            argv = self.submit_as('codex')['argv']
            if n: self.assertEqual(argv[:2], ['exec', 'resume'])
            cs = [argv[i + 1] for i, x in enumerate(argv) if x == '-c']
            for f in ('plugins', 'apps', 'browser_use', 'computer_use', 'memories', 'image_generation'): self.assertIn(f'features.{f}=false', cs)
            for m in ('chrome-devtools', 'computer-use'): self.assertIn(f'mcp_servers.{m}.enabled=false', cs)
            self.assertIn('mcp_servers.davinci-resolve.enabled=true', cs); self.assertNotIn('mcp_servers.davinci-resolve.enabled=false', cs)
            self.assertIn('mcp_servers.davinci-resolve.default_tools_approval_mode="approve"', cs)
            self.assertTrue(any(c.startswith('mcp_servers.davinci-resolve.args=') and 'davinci_mcp_guard.py' in c for c in cs))
            off = json.loads(next(c for c in cs if c.startswith('mcp_servers.davinci-resolve.disabled_tools='))[len('mcp_servers.davinci-resolve.disabled_tools='):])
            self.assertTrue({'render', 'project_manager', 'resolve_control', 'timeline_versioning'} <= set(off), off)
            self.assertFalse(any('.env.enabled' in c or 'mcp_servers.davinci-resolve.env' in c for c in cs))     # 子表不当成一个 MCP
            sk = next(c for c in cs if c.startswith('skills.config='))
            off = [x for x in json.loads('[' + sk[len('skills.config=['):-1].replace('{path=', '{"path":').replace(',enabled=false}', ',"enabled":false}') + ']')]
            paths = [x['path'] for x in off]
            for n2 in ('media-use', 'imagegen', 'lark-doc', 'yixiaoer-cli'): self.assertTrue(any(f'/{n2}/SKILL.md' in p for p in paths), n2)
            for n2 in ('thinkcut-edit', 'old-edit', 'hyperframes'): self.assertFalse(any(f'/{n2}/SKILL.md' in p for p in paths), n2)   # 旧名和新名指同一份，不能连带关掉

    def test_env_finds_tools_like_user_shell(self):
        os.environ['PATH'] = '/usr/bin:/bin'                                                 # 从程序坞打开时 PATH 很短
        env = self.submit_as('codex')['env']; path = env['PATH'].split(os.pathsep)
        for d in ('/opt/homebrew/bin', str(self.home / '.local/bin'), str(self.home / '.local/node-system/bin')): self.assertIn(d, path)
        self.assertEqual(env['PYTHONUTF8'], '1'); self.assertEqual(env['HYPERFRAMES_NO_TELEMETRY'], '1'); self.assertEqual(env['THINKCUT_NO_KEYCHAIN'], '1')

    def test_whitelist_is_one_list_for_both(self):
        A = self.A
        for n in ('computer-use', 'chrome-devtools', 'node_repl', 'cua_repl', 'codex_apps'): self.assertNotIn(n, A.allowed_mcp())   # 危险的默认不给
        self.assertIn('davinci-resolve', A.allowed_mcp())                                   # 2026-11-28 用户定开达芬奇 MCP
        self.assertTrue(all(n in A.MCP_KNOWN for n in ('computer-use', 'chrome-devtools', 'davinci-resolve')))                              # 不给的都写了理由
        demo = {'does': '演示', 'command': '/bin/echo', 'args': ['hi'], 'env': {'K': 'v'}}
        old = A.MCP_ALLOW
        try:
            A.MCP_ALLOW = {'chrome-devtools': demo}                                            # 假设白名单里加一个：两边都带上
            cl = json.loads(A.claude_args()[2])['mcpServers']; self.assertEqual(cl['chrome-devtools']['command'], '/bin/echo')
            cs = A.codex_args(); self.assertIn('mcp_servers.chrome-devtools.enabled=true', cs); self.assertNotIn('mcp_servers.chrome-devtools.enabled=false', cs)
            self.assertIn('mcp_servers.chrome-devtools.command="/bin/echo"', cs); self.assertIn('mcp_servers.davinci-resolve.enabled=true', cs)
        finally: A.MCP_ALLOW = old

    def test_settings_tools_report_and_fix(self):
        t = self.req('/api/settings/tools', None, page=False)[1]; self.assertTrue(t['ok'])
        sk = {x['name']: x for x in t['skills']}
        self.assertTrue(sk['thinkcut-edit']['claude'] and sk['thinkcut-edit']['codex']); self.assertFalse(sk['thinkcut-project']['codex'])
        self.assertIn('hyperframes', t['missing_links'])                                       # Codex 那边有、Claude 那边没链
        mc = {m['name']: m for m in t['mcp']}; self.assertTrue(set(mc) >= {'davinci-resolve', 'chrome-devtools'})
        self.assertEqual([n for n, m in mc.items() if m['backend']], ['davinci-resolve'])          # 只有达芬奇开着
        self.assertTrue(all(m['why'] for n, m in mc.items() if n != 'davinci-resolve'))
        dv = mc['davinci-resolve']; self.assertTrue(any(x['name'] == 'render' for x in dv['tools_off'])); self.assertTrue(dv['actions_off'] and dv['notes'])
        self.assertTrue(any(d['name'].startswith('ffmpeg') for d in t['deps']))
        self.assertEqual(self.req('/api/settings/tools/fix', {}, page=False)[0], 403)          # 一键补只收设置页的
        (self.home / '.claude' / 'skills' / 'thinkcut-cover').mkdir(parents=True)              # 已有同名文件夹：不动
        out = self.ok('/api/settings/tools/fix', {})
        self.assertTrue((self.home / '.claude' / 'skills' / 'hyperframes' / 'SKILL.md').is_file(), out)
        self.assertTrue(any('thinkcut-cover' in x for x in out['skipped']), out); self.assertFalse((self.home / '.claude' / 'skills' / 'thinkcut-cover').is_symlink())


class BackendHelp(Base):
    """2026-11-28 架构审查：后台 AI 一律中文；261114 真记录里卡住的三处（pgrep、带三引号的 heredoc、没空画面轨）给现成办法。"""

    def submit_as(self, eid):
        E.choose(eid); nid = self.note(); self.do(['--reply', nid, '--note', '收到']); self.submit(); self.assertEqual(self.run_rec()['state'], '完成')
        return self.runs()[-1]['argv']

    def check_tools(self, text):
        for k in ('简体中文', 'resolve-status', 'add-track', 'python3 - <<EOF', 'Write'): self.assertIn(k, text, k)

    def test_claude_submit_chinese_in_system_prompt_and_tool_hints(self):
        argv = self.submit_as('claude')
        self.assertIn('简体中文', argv[argv.index('--append-system-prompt') + 1])               # 系统提示里也有：管住过程中说的话
        self.assertEqual(json.loads(argv[argv.index('--settings') + 1])['language'], '简体中文')      # Claude Code 自己的回复语言设置：接着英文旧会话也回中文（实测）
        self.check_tools(argv[argv.index('-p') + 1])

    def test_env_drops_parent_agent_session_vars(self):
        os.environ.update(CLAUDECODE='1', CLAUDE_CODE_SESSION_ID='别人的会话', CLAUDE_CODE_MESSAGING_TOKEN='口令', CLAUDE_CODE_ENTRYPOINT='claude-desktop',
                          CODEX_THREAD_ID='t', CODEX_HOME=str(self.tmp / 'cx'))                     # 审片台是在另一个 AI 会话里起的
        E.choose('claude'); nid = self.note(); self.do(['--reply', nid, '--note', '收到']); self.submit(); env = self.runs()[-1]['env']
        self.assertFalse([k for k in env if k.startswith('CLAUDE') or k == 'CODEX_THREAD_ID'], [k for k in env if k.startswith(('CLAUDE', 'CODEX'))])
        self.assertEqual(env['CODEX_HOME'], str(self.tmp / 'cx'))

    def test_codex_submit_has_same_rules(self):
        self.check_tools(self.submit_as('codex')[-1])

    def test_chat_prompt_has_same_rules(self):
        from thinkcut import chat
        c = chat.for_project(self.job)
        for eid in ('claude', 'codex'):
            system, _ = c.prompt(eid, '你好', []); self.check_tools(system)
            self.assertNotIn(tempfile.gettempdir() + '。', system)                           # 临时文件放项目里（Claude 的 Write 只放行工作目录）
        self.assertTrue((self.job.dir / '审片工作台' / 'tmp').is_dir())

    def test_resolve_status_read_only(self):
        from thinkcut import resolve as R
        class TL:
            def GetName(s): return 'v4_主线'
            def GetTrackCount(s, k): return {'video': 1, 'audio': 2}[k]
            def GetTrackName(s, k, i): return '视频 1'
            def GetIsTrackEnabled(s, k, i): return True
            def GetIsTrackLocked(s, k, i): return False
            def GetItemListInTrack(s, k, i): return [1, 2, 3]
            def __getattr__(s, n): raise AssertionError('不该调用写操作：' + n)
        class P:
            def GetName(s): return '261114 示例'
            def GetCurrentTimeline(s): return TL()
        class PM:
            def GetCurrentDatabase(s): return {'DbName': '库'}
            def GetCurrentProject(s): return P()
        class RV:
            def GetProjectManager(s): return PM()
        out = R.live_status(RV(), is_running=lambda: True)
        self.assertEqual(out['工程'], '261114 示例'); self.assertEqual(out['当前时间线'], 'v4_主线'); self.assertEqual(out['音频轨数'], 2)
        self.assertEqual(out['画面轨'], [{'第几条': 1, '名字': '视频 1', '开着': True, '锁着': False, '片段数': 3}])
        self.assertIn('没开', R.live_status(is_running=lambda: False)['说明'])
        sys.path.insert(0, str(E.PROGRAM / 'bin')); import thinkcut_broker as BK                  # 走代办（AI 沙盒里连不上达芬奇、看不到进程）
        self.assertFalse(BK.local_ok('thinkcut_edit.py', ['resolve-status'])); self.assertIn('broll.py', BK.SCRIPTS)


class Capabilities(unittest.TestCase):
    """本地对话能做 vs 后台能做（2026-11-28）：表里说“代办”的程序都真在代办名单里，要代办的子命令真会被交出去；差别只剩“安全规矩”。"""
    def test_table_matches_broker(self):
        from thinkcut import aitools as A
        sys.path.insert(0, str(E.PROGRAM / 'bin')); import thinkcut_broker as BK
        rows = A.capabilities(); self.assertTrue(len(rows) >= 25)
        self.assertEqual({r['kind'] for r in rows} - {'一样', '代办', '护栏', '安全规矩'}, set())
        import re as _re
        for r in rows:
            if r['kind'] != '代办': continue
            for name in set(_re.findall(r'[A-Za-z0-9_]+\.py|出片\.py', r['local'] + ' ' + r['backend'])):          # 场景.py 是 AI 改的工程文件，不是命令
                self.assertTrue(name in BK.SCRIPTS or name in BK.TEMPLATES, f"{r['what']}：{name} 不在代办名单里")
        for name, argv, local in (('library.py', ['find', 'x'], True), ('library.py', ['--index', '/p/i', 'add-project', '/p'], False), ('library.py', ['notes', '/p'], False),
                                  ('navigation.py', ['validate', 'a'], True), ('navigation.py', ['render', 'a'], False), ('navigation.py', ['preset-save'], False),
                                  ('thinkcut_config.py', ['rules'], True), ('thinkcut_config.py', ['new', 'x'], False), ('thinkcut_feedback.py', ['submit', 'f'], False),
                                  ('thinkcut_edit.py', ['workbench', 'd', '--list'], True), ('thinkcut_edit.py', ['resolve-status'], False)):
            self.assertEqual(BK.local_ok(name, argv), local, (name, argv))
        t = A.report(); self.assertEqual(len(t['capabilities']), len(rows))

    def test_feedback_submit(self):
        sys.path.insert(0, str(E.PROGRAM / 'bin')); import thinkcut_feedback as F
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); prog = d / '程序'; (prog / 'internal').mkdir(parents=True)
            src = d / '2026-11-28_示例_反馈单_小王.md'; src.write_text('# 反馈单：示例（2026-11-28）\n', encoding='utf-8')
            a = F.submit(src, prog); b = F.submit(src, prog)
            self.assertEqual(a.parent, prog / 'internal' / '经验' / '待审'); self.assertNotEqual(a, b)          # 不覆盖同名的
            bad = d / '随便.md'; bad.write_text('# 反馈单：x\n', encoding='utf-8')
            with self.assertRaises(SystemExit): F.submit(bad, prog)
            shutil.rmtree(prog / 'internal'); self.assertIsNone(F.submit(src, prog))                        # 公开版：留在项目里


class AppLaunched(unittest.TestCase):
    """2026-11-26 用户在 Mac 程序的设置页看到 Claude Code、Codex 都“未装”：程序起的服务带着 THINKCUT_NO_OPEN，以前被当成测试、一个 AI 都不找。"""
    def test_no_open_is_not_a_test_marker(self):
        old = os.environ.pop('THINKCUT_AI_PATH', None)
        try:
            os.environ['THINKCUT_NO_OPEN'] = '1'
            dirs = [str(p) for p in E.search_dirs()]
            self.assertIn(str(Path.home() / '.local/node-system/bin'), dirs, '程序起的服务也要到常见安装位置找 AI 命令行')
        finally:
            if old is not None: os.environ['THINKCUT_AI_PATH'] = old


if __name__ == '__main__':
    unittest.main()
