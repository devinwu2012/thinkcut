# -*- coding: utf-8 -*-
"""审片台（2026-11-22）离线测试：不连达芬奇（替身），起真的本机服务走页面请求。run_tests.sh 会跑。
发布／读结果／回复命令；页面点“可以”“选这个”“写要求”“改字”后的数据文件；动画确认 anim.gate 认得出；命令行不能伪造用户确认；
文案改字只改对应小节；上传文案走视频项目管理的 copy-apply；导航文字栏同旧；确认、改字只收本页面带口令的请求；样张自动发布；音频分段读。
要用到视频项目管理的 script_doc.set_section：开发时用环境变量 THINKCUT_PM 指到它的 bin（合并后不用设）。"""
import urllib.parse, json, os, shutil, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinkcut import workbench as W, board as B, anim as A      # noqa: E402
from thinkcut.common import Job, wj, rj                         # noqa: E402
from thinkcut.test_workbench import FakeDV, film                # noqa: E402
from PIL import Image                                       # noqa: E402
os.environ['THINKCUT_NO_OPEN'] = '1'

S = Path(__file__).resolve().parents[1] / 'thinkcut_edit.py'
DOC = ('# P1\n\n> 定稿文案\n\n## 标题\n\n旧的标题\n\n## 封面文字\n\n为什么外地\n灯光设计\n\n## 口播正文\n\n### 开头\n我们做过两个外地的灯光设计，'
       '一个武汉的，一个广东的。\n> 这里停一下\n每个项目去了五次左右现场。\n')
TAGS = ['灯光设计', '全案设计', '室内设计', '装修细节', '别墅设计', '大平层', '照明设计', '装修避坑']


def img(p, color=(200, 30, 30)):
    p.parent.mkdir(parents=True, exist_ok=True); Image.new('RGB', (40, 30), color).save(p); return p


def project(root):
    """<root>/P1/项目.json、03_成片/P1.md、02_制作/自动剪辑（片子目录）、HF_测 动画工程（S1 场景、S2 关系类）。"""
    pd = root / 'P1'; film_dir = pd / '02_制作' / '自动剪辑'; film_dir.mkdir(parents=True)
    wj(pd / '项目.json', {'schema_version': 1, 'name': 'P1', 'title': '旧的标题', 'description': '', 'tags': [], 'assets': {}, 'targets': []})
    (pd / '03_成片').mkdir(); (pd / '03_成片' / 'P1.md').write_text(DOC, encoding='utf-8')
    job = film(film_dir)
    hf = film_dir / 'HF_测'; (hf / '镜头').mkdir(parents=True)
    for s in ('S1', 'S2'): (hf / '镜头' / f'{s}.html').write_text(f'<div>{s}</div>', encoding='utf-8')
    wj(film_dir / '分镜规格.json', {'anchors': {}, 'shots': [
        {'id': 'S1', 'a': 1, 'b': 3, '工程': 'HF_测', '讲什么': '场景', '口播要点': ['两个外地的灯光设计']},
        {'id': 'S2', 'a': 4, 'b': 6, '工程': 'HF_测', '讲什么': '关系', '口播要点': ['远程改', '实际没改']}]})
    return pd, job, hf


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.pd, self.job, self.hf = project(self.tmp); self.dv = FakeDV()
        self.srv = W.make_server(self.job, 0, self.dv); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.port}'

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); shutil.rmtree(self.tmp, ignore_errors=True)

    # —— 请求 ——
    def req(self, p, body=None, page=True, **headers):
        """page=True：像页面一样带来源和口令（浏览器 fetch 会带 Origin、Sec-Fetch-Site）。返回 (状态码, 内容)。"""
        h = {'Content-Type': 'application/json', **({'Origin': self.base, 'Sec-Fetch-Site': 'same-origin', 'X-Shenpian-Token': W.TOKEN} if page else {}), **headers}
        q = urllib.request.Request(self.base + p, json.dumps(body or {}).encode() if body is not None else None, h)
        try:
            with urllib.request.urlopen(q) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def act(self, iid, action, **kw):
        v = B.latest(B.item(B.load(self.job), iid))['v']
        return self.req('/api/item/act', {'id': iid, 'v': v, 'action': action, **kw})

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.job.dir), *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    # —— 发布、读结果、回复（命令行）——
    def test_publish_list_reply_cli(self):
        a, b, c = (img(self.tmp / f'封面{k}.jpg', col) for k, col in (('A', (1, 2, 3)), ('B', (4, 5, 6)), ('C', (7, 8, 9))))
        r = self.cli('--publish', '封面', '--kind', '多选一', '--title', '封面三个方向', '--look', '看主角清不清楚\\n选一个', '--files', f'{a},{b},{c}', '--labels', 'A 射灯,B 梯子,C 挑空')
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('封1', r.stdout); self.assertIn('“选这个”就是确认', r.stdout)
        it = B.item(B.load(self.job), '封1'); v = it['versions'][0]
        self.assertEqual((it['column'], v['kind'], [f['label'] for f in v['files']], v['look']), ('封面', '多选一', ['A 射灯', 'B 梯子', 'C 挑空'], '看主角清不清楚\n选一个'))
        self.assertTrue(all((W.home(self.job) / f['path']).is_file() and len(f['sha256']) == 64 for f in v['files']))      # 拷了一份、记了哈希
        self.assertEqual(B.state(it), ('等你看', False))
        self.assertIn('0 项', self.cli('--list').stdout)                                                                 # 用户还没点：读结果里没有
        img(a, (9, 9, 9)); r = self.cli('--publish', '封面', '--id', '封1', '--kind', '多选一', '--title', '封面三个方向', '--look', '再看', '--files', f'{a},{b}', '--note', 'A 换了颜色')
        self.assertEqual(r.returncode, 0, r.stderr); it = B.item(B.load(self.job), '封1'); self.assertEqual(len(it['versions']), 2)   # 同一项再发布＝新版，旧版留着
        self.assertNotEqual(it['versions'][0]['files'][0]['sha256'], it['versions'][1]['files'][0]['sha256'])
        self.assertTrue((W.home(self.job) / it['versions'][0]['files'][0]['path']).is_file())
        code, out = self.req('/api/item/act', {'id': '封1', 'v': 1, 'action': '选这个', 'pick': 'A'})
        self.assertFalse(out['ok']); self.assertIn('第 2 版', out['error'])                                             # 旧版不能点
        code, out = self.act('封1', '选这个', pick='B'); self.assertTrue(out['ok'], out)
        r = self.cli('--list'); self.assertIn('[封1] 封面｜封面三个方向｜第 2 版｜你已定｜用户', r.stdout); self.assertIn('选 B', r.stdout); self.assertIn(str(b), r.stdout)
        r = self.cli('--reply', '封1', '--note', '收到，按 B 出横版'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(B.state(B.item(B.load(self.job), '封1')), ('你已定', False)); self.assertIn('0 项', self.cli('--list').stdout)
        self.assertIn('[封1]', self.cli('--list', '--all').stdout)
        for bad in (['--publish', '审核线', '--title', 'x', '--look', 'y'], ['--publish', '封面', '--kind', '多选一', '--title', 'x', '--look', 'y', '--files', str(a)],
                    ['--publish', '封面', '--title', 'x', '--look', 'y', '--files', str(self.tmp / '没有.jpg')]):
            r = self.cli(*bad); self.assertNotEqual(r.returncode, 0); self.assertIn('停止', r.stderr)

    # —— 2026-11-22 用户试用：“口播试听……这干嘛的……不要也行吧” ——
    def test_publish_videos_side_by_side(self):
        """2026-11-22 反馈单（261122）：动画直接发视频（mp4/mov/webm），不用转动图；页面拿到的是 video，能分段读（拖进度条），点“选这个”照常记确认。"""
        a = self.tmp / 'Hyper.mp4'; a.write_bytes(b'0123456789' * 100); b = self.tmp / 'Motion.mov'; b.write_bytes(b'abcdefghij' * 100)
        r = self.cli('--publish', '动画', '--kind', '多选一', '--title', '两版动画', '--look', '看哪版更清楚', '--files', f'{a},{b}', '--labels', 'Hyper,Motion')
        self.assertEqual(r.returncode, 0, r.stderr)
        it = next(x for x in self.req('/api/data', page=False)[1]['board']['items'] if x['title'] == '两版动画'); fs = it['versions'][-1]['files']
        self.assertEqual([f['type'] for f in fs], ['video', 'video'])
        url = self.base + '/' + urllib.parse.quote('审片台/' + fs[0]['path'].split('/', 1)[1])
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=10-19'})) as x:
            self.assertEqual((x.status, x.headers['Content-Type'], x.headers['Content-Range'], x.read()), (206, 'video/mp4', 'bytes 10-19/1000', b'0123456789'))
        with urllib.request.urlopen(self.base + '/' + urllib.parse.quote('审片台/' + fs[1]['path'].split('/', 1)[1])) as x: self.assertEqual(x.headers['Content-Type'], 'video/quicktime')
        self.assertEqual(self.act(it['id'], '选这个', pick='Motion')[0], 200)
        self.assertIn('Motion', self.cli('--list').stdout)
        bad = self.tmp / '片.avi'; bad.write_bytes(b'x')
        r = self.cli('--publish', '动画', '--kind', '可以', '--title', 'x', '--look', 'y', '--files', str(bad)); self.assertNotEqual(r.returncode, 0); self.assertIn('视频（.mp4/.mov/.webm/.m4v）', r.stdout + r.stderr)
        page = W.PAGE.read_text(encoding='utf-8')
        for need in ("f.type==='video'", '<video controls loop', '一起播放', '下一帧', '原尺寸'): self.assertIn(need, page)

    def test_send_to_ai(self):
        """2026-11-22 用户：“我在导航文字里觉得……能改好了，直接点‘发给AI’，它就直接发给AI了，然后直接把导航文字这部分改完；封面这部分改完，我点‘发给AI’，它就直接把封面部分改了……总是要给我一个状态吧。”
        在哪一栏点就只发那一栏；每件事的状态：还没发给 AI（unsent）→ 已发给 AI（sent）→ AI 在处理（doing）；AI 挂着 --wait 的只拿到发来的那一栏。"""
        send = lambda: self.req('/api/data', None, page=False)[1]['board']['send']
        badge = lambda c: self.req('/api/data', None, page=False)[1]['board']['badges'][c]['text']
        a = img(self.tmp / '封A.jpg'); b = img(self.tmp / '封B.jpg', (1, 2, 3))
        self.assertEqual(self.cli('--publish', '封面', '--kind', '多选一', '--title', '封面两个方向', '--look', '选一个', '--files', f'{a},{b}').returncode, 0)
        self.assertEqual((send()['pending'], send()['unsent']), (0, 0))
        self.assertFalse(self.req('/api/send', {'column': '封面'})[1]['ok'])                              # 这一栏没有要处理的：不让空发
        self.assertTrue(self.act('封1', '写要求', text='选 B，圈再大一点')[1]['ok'])
        self.req('/api/mark', {}, page=True); mid = W.load(self.job)['markers'][-1]['id']; self.req('/api/text', {'id': mid, 'text': '角标去掉'})
        s = send(); self.assertEqual((s['pending'], s['unsent'], s['ids'], s['listening']), (2, 2, {'封1': 'unsent', f'#{mid}': 'unsent'}, False))
        self.assertEqual(s['cols'], {'封面': {'unsent': 1, 'sent': 0, 'doing': 0}, '审核线': {'unsent': 1, 'sent': 0, 'doing': 0}})
        self.assertEqual(badge('封面'), '1 条没发给 AI'); self.assertIn('1 条没发给 AI', badge('审核线'))
        r = self.req('/api/send', {'column': '封面'}, page=False); self.assertEqual((r[0], r[1]['ok']), (403, False)); self.assertEqual(send()['unsent'], 2)   # 命令行、curl 不能替用户点“发给 AI”
        (W.home(self.job) / '服务.json').write_text(json.dumps({'pid': os.getpid(), 'port': self.port, 'url': self.base + '/', 'dir': str(self.job.dir)}))
        r = self.cli('--wait', '--wait-timeout', '1'); self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('用户还没点“发给 AI”', r.stdout)   # 没点：到时间退出
        self.assertTrue(send()['listening'])                                                             # AI 刚来问过：页面写“AI 在等你”
        r = self.req('/api/send', {'column': '封面'})[1]; self.assertEqual((r['ok'], r['n'], r['column'], r['listening']), (True, 1, '封面', True))   # 只发封面这一栏
        s = send(); self.assertEqual((s['ids'], s['unsent']), ({'封1': 'sent', f'#{mid}': 'unsent'}, 1)); self.assertEqual(badge('封面'), '已发给 AI')
        r = self.cli('--wait', '--wait-timeout', '20'); self.assertEqual(r.returncode, 0, r.stderr)
        for need in ('【用户点了“发给 AI”】栏目：封面，1 件', '[封1] 封面｜封面两个方向', '选 B，圈再大一点'): self.assertIn(need, r.stdout)
        self.assertNotIn('角标去掉', r.stdout)                                                           # 审核线那条用户还没发：不给 AI
        s = send(); self.assertEqual(s['ids'], {'封1': 'doing', f'#{mid}': 'unsent'}); self.assertEqual(badge('封面'), 'AI 在处理 1 条')
        self.assertFalse(self.req('/api/send', {'column': '封面'})[1]['ok'])                              # AI 已经接了：不用再发
        r = self.req('/api/send', {'column': '审核线'})[1]; self.assertEqual((r['ok'], r['n']), (True, 1))
        r = self.cli('--wait', '--wait-timeout', '20'); self.assertIn('栏目：审核线，1 件', r.stdout); self.assertIn('角标去掉', r.stdout); self.assertNotIn('[封1]', r.stdout)
        self.assertTrue(self.act('封1', '写要求', text='选 B，圈再大一点，字也加粗')[1]['ok'])           # 发过以后又改了要求：又算没发
        self.assertEqual(send()['ids']['封1'], 'unsent'); self.assertEqual(badge('封面'), '1 条没发给 AI')
        self.req('/api/send', {'column': '封面'}); self.assertEqual(send()['ids']['封1'], 'sent')
        self.assertIn('封1', self.cli('--list').stdout); self.assertEqual(send()['ids']['封1'], 'doing')  # AI 手动 --list 也算接了
        B.reply(self.job, '封1', '圈放大了、字加粗了', [b, a]); s = send(); self.assertNotIn('封1', s['ids'])   # AI 回了新版：这件事了结，轮到用户看
        self.assertEqual(badge('封面'), '等你看 1 项')
        old = {'at': '2026-11-22 19:52:33', 'keys': ['x@1'], 'picked': None}                             # 当天早些时候的老格式照样读得了
        B.edit(self.job, lambda d: (d.pop('sent', None), d.update(send=old))); self.assertEqual(send()['unsent'], 1)
        page = W.PAGE.read_text(encoding='utf-8')
        for need in ('data-main="封面"', '交给 AI（${n.unsent}）', 'AI 没在等', "'待发送'", "'AI 在改'", "'待你看'"): self.assertIn(need, page)   # 审片台 v2：栏头主按钮“交给 AI（N）”，4 个状态

    def test_column_settled_only_by_user(self):
        """2026-11-22 用户：“‘已定’是怎么确定的？这件事已定了，是不是应该有个标签或者有方法能让我确定啊？”
        栏目的“你已定／你定了”只来自用户：每一项都点过，或点了“这一栏定了”；之后内容一变就不算；命令行不能替用户点。"""
        meta = lambda: self.req('/api/data', None, page=False)[1]['board']
        a = img(self.tmp / '封A.jpg'); b = img(self.tmp / '封B.jpg', (1, 2, 3))
        self.cli('--publish', '封面', '--kind', '多选一', '--title', '封面两个方向', '--look', '选一个', '--files', f'{a},{b}')
        m = meta(); self.assertEqual(m['settle']['封面']['state'], 'no'); self.assertIn('还有 1 项等你看', m['settle']['封面']['why'])
        self.assertFalse(self.req('/api/settle', {'column': '封面'})[1]['ok'])                           # 还有等你看的：不能定
        self.act('封1', '选这个', pick='B'); self.assertEqual(meta()['settle']['封面']['state'], 'can')    # 2026-11-24 起：还没交给 AI 的不挡“这一栏定了”
        self.req('/api/send', {'column': '封面'}); self.assertIn('AI 还在处理', meta()['settle']['封面']['why'])
        B.reply(self.job, '封1', '收到，按 B 往下做'); m = meta()
        self.assertEqual((m['badges']['封面']['text'], m['settle']['封面']['state']), ('你已定', 'can'))   # 每一项都点过：栏目写“你已定”
        self.assertEqual(m['badges']['上传文案']['text'], '没有待办'); self.assertEqual(m['settle']['上传文案']['state'], 'can')   # 没人确认过：不写“已定”
        self.assertEqual(self.req('/api/settle', {'column': '上传文案'}, page=False)[0], 403)            # 命令行不能替用户点
        self.assertTrue(self.req('/api/settle', {'column': '上传文案'})[1]['ok']); m = meta()
        self.assertEqual((m['badges']['上传文案']['text'], m['settle']['上传文案']['state']), ('你定了 ✓', 'ok'))
        self.assertIn('用户点过“这一栏定了”的栏目：上传文案', self.cli('--list').stdout)
        self.assertTrue(self.req('/api/upload-copy', {'description': '改了一版简介，说清楚为什么要到现场调灯。', 'tags': ['别墅装修', '灯光设计', '大平层', '射灯', '装修避坑', '全屋灯光', '设计师', '现场调试']})[1]['ok'])
        m = meta(); self.assertNotEqual(m['settle']['上传文案']['state'], 'ok')                           # 定了以后内容变了：不算，要重新点
        self.assertFalse(self.req('/api/settle', {'column': '文案'})[1]['ok'])                           # 口播剪辑栏不用点（它的确认是“保存到达芬奇”）
        self.req('/api/settle', {'column': '封面'}); self.assertEqual(meta()['settle']['封面']['state'], 'ok')
        self.req('/api/settle', {'column': '封面', 'on': False}); self.assertEqual(meta()['settle']['封面']['state'], 'can')   # 能取消
        page = W.PAGE.read_text(encoding='utf-8')
        for need in ('class="btn mainbtn', '这一栏定了', '✓ 已定 · ', '取消'): self.assertIn(need, page)   # 审片台 v2：主按钮“这一栏定了”→“✓ 已定 · 时间”，旁边“取消”

    def test_own_text_columns_settle_directly(self):
        """2026-11-22 用户：“导航文字这里，有的是我写的。我写完之后，没法点确定，也没法发给AI……要么直接点确定，要么发给AI。我发给AI基本上也就定了，因为内容都是我写完了的。”"""
        meta = lambda: self.req('/api/data', None, page=False)[1]['board']
        self.assertEqual(self.cli('--publish', '导航文字', '--kind', '改字', '--title', '请确认导航短标签', '--look', '不对直接改').returncode, 0)
        self.req('/api/nav', {'labels': {'q': '挑空怎么照亮'}}, page=False)
        m = meta(); self.assertEqual(m['settle']['导航文字']['state'], 'can')                              # 有等你看的卡片也能直接定：字是用户自己写的
        r = self.req('/api/settle', {'column': '导航文字'})[1]; self.assertEqual((r['ok'], r['sent']), (True, 2))   # 卡片替用户点成“可以”，连同改的字一起发给 AI
        self.assertEqual(B.state(B.item(B.load(self.job), '导1')), ('你已定', True))
        m = meta(); self.assertEqual((m['settle']['导航文字']['state'], m['send']['ids']), ('ok', {'导1': 'sent', '导航条': 'sent'}))
        self.assertEqual(m['badges']['导航文字']['text'], '已发给 AI')                                    # AI 还没接：先写发送状态
        B.reply(self.job, '导1', '按你改的字出好了'); self.cli('--reply', '导航条', '--note', '已做进审核线')
        m = meta(); self.assertEqual((m['badges']['导航文字']['text'], m['settle']['导航文字']['state']), ('你定了 ✓', 'ok'))   # AI 照做完：还是你定了
        self.req('/api/nav', {'labels': {'q': '挑空到底怎么照亮'}}, page=False); self.assertNotEqual(meta()['settle']['导航文字']['state'], 'ok')   # 又改了字：要重新定
        r = self.req('/api/send', {'column': '导航文字'})[1]; self.assertEqual((r['ok'], r.get('settled')), (True, True))    # 只发自己写的字：发了也就定了
        self.assertEqual(meta()['settle']['导航文字']['state'], 'ok')
        self.cli('--publish', '导航文字', '--id', '导1', '--kind', '改字', '--title', '请确认导航短标签', '--look', '再看', '--note', '换了分段')
        self.act('导1', '写要求', text='分成 4 段'); m = meta()
        self.assertEqual(m['settle']['导航文字']['state'], 'no'); self.assertIn('让 AI 改', m['settle']['导航文字']['why'])   # 写了要求是让 AI 改：不算定
        r = self.req('/api/send', {'column': '导航文字'})[1]; self.assertTrue(r['ok']); self.assertFalse(r.get('settled'))
        page = W.PAGE.read_text(encoding='utf-8'); self.assertIn("own?'定稿'", page); self.assertNotIn('就用这些字', page)   # 审片台 v2：只留栏头一个“定稿”

    def test_upload_copy_column_takes_no_screenshot(self):
        """2026-11-22 用户：“你不用非得给我一张截图，你就直接写在里边，有问题我直接在里边改了，或者我直接给你写要求让你改就完了。”"""
        a = img(self.tmp / '上传文案初稿.png')
        r = self.cli('--publish', '上传文案', '--kind', '可以', '--title', '简介、标签初稿', '--look', '看一眼', '--files', str(a))
        self.assertNotEqual(r.returncode, 0); self.assertIn('上传文案栏不发截图', r.stdout + r.stderr)
        r = self.cli('--publish', '上传文案', '--kind', '改字', '--title', '简介、标签初稿', '--look', '看一眼上面的简介和标签'); self.assertEqual(r.returncode, 0, r.stderr)
        page = W.PAGE.read_text(encoding='utf-8'); self.assertLess(page.index('id="uploaded"'), page.index('data-items="上传文案"'))    # 文本框在卡片上面

    def test_listen_column_only_when_something_to_hear_and_withdraw(self):
        cols = lambda: self.req('/api/data', page=False)[1]['board']['columns']
        self.assertNotIn('口播试听', cols())                                   # 没东西要听：这一栏不显示
        a = self.tmp / '旧.m4a'; a.write_bytes(b'x'); b = self.tmp / '新.m4a'; b.write_bytes(b'y')
        r = self.cli('--publish', '口播试听', '--kind', '试听', '--title', '气口新旧对比', '--look', '听字尾', '--files', f'{a},{b}', '--labels', '现在的,新做法')
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('口播试听', cols())   # 有要听的才出现
        r = self.cli('--withdraw', '听1', '--note', '用户已在对话里定了'); self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('已撤回 听1', r.stdout)
        self.assertNotIn('口播试听', cols()); self.assertNotIn('[听1]', self.cli('--list', '--all').stdout)
        self.assertTrue(B.item(B.load(self.job), '听1').get('撤回'))            # 不删，留着记录
        self.assertEqual(self.cli('--withdraw', '没有这项').returncode != 0, True)

    # —— 页面上点：可以、选这个、写要求（数据文件）——
    def test_page_actions_data(self):
        p1, p2 = img(self.tmp / 'x1.png'), img(self.tmp / 'x2.png')
        B.publish(self.job, '口播试听', '气口 01', '听“子”字尾音有没有被切短', '试听', [self._audio('旧'), self._audio('新')], ['现在的', '新做法'])
        code, out = self.act('听1', '可以'); self.assertEqual(code, 200); self.assertTrue(out['ok'])
        e = B.item(B.load(self.job), '听1')['events'][-1]
        self.assertEqual((e['type'], e['action'], e['v'], e['who']), ('user', '可以', 1, B.WHO)); self.assertTrue(e['at'] and len(e['files']) == 2 and all(len(f['sha256']) == 64 for f in e['files']))
        B.publish(self.job, '动画', '草图', '选一种画法', '多选一', [p1, p2])
        self.assertFalse(self.act('动1', '可以')[1]['ok'])                                                              # 多选一只能“选这个”
        self.assertFalse(self.act('动1', '选这个', pick='Z')[1]['ok'])
        for t in ('远程那段', '远程那段太快了'): self.assertTrue(self.act('动1', '写要求', text=t)[1]['ok'])               # 自动保存：改的是同一条要求
        it = B.item(B.load(self.job), '动1'); asks = [x for x in it['events'] if x['type'] == 'user']
        self.assertEqual([x['text'] for x in asks], ['远程那段太快了']); self.assertEqual(B.state(it), ('AI 改中', True))
        r = self.cli('--list'); self.assertIn('[动1] 动画｜草图｜第 1 版｜AI 改中｜用户写的要求（第 1 版）：远程那段太快了', r.stdout)
        B.reply(self.job, '动1', '放慢到 2 秒'); self.assertEqual(B.state(B.item(B.load(self.job), '动1')), ('AI 已回复', False))
        B.reply(self.job, '动1', '换了新画法', [p2, p1]); it = B.item(B.load(self.job), '动1')
        self.assertEqual((len(it['versions']), B.state(it)[0]), (2, '等你看'))
        self.assertTrue(self.act('动1', '选这个', pick='B')[1]['ok']); self.assertEqual(B.state(B.item(B.load(self.job), '动1')), ('你已定', True))
        d = self.req('/api/data', None, page=False)[1]['board']                                                       # 给页面的数据：栏目状态
        self.assertEqual(d['columns'], list(B.COLUMNS)); self.assertEqual(d['badges']['动画']['text'], '1 条没发给 AI'); self.assertEqual(d['badges']['封面']['text'], '还没到')
        self.assertEqual(d['badges']['文案']['text'], '没有待办'); self.assertEqual(d['badges']['口播试听']['text'], '1 条没发给 AI')   # 点了“可以”还没发给 AI（2026-11-22 起要点“发给 AI”）
        B.publish(self.job, '封面', '封面', '选一个', '多选一', [p1, p2]); d = self.req('/api/data', None, page=False)[1]['board']
        self.assertEqual((d['badges']['封面']['text'], d['waiting']), ('等你看 1 项', 1))

    def _audio(self, name):
        f = self.tmp / f'{name}.m4a'; f.write_bytes(b'\x00\x00\x00\x18ftypM4A ' + os.urandom(3000)); return f

    def test_media_and_range(self):
        a = self._audio('a'); B.publish(self.job, '口播试听', '试听', '听', '试听', [a]); f = B.latest(B.item(B.load(self.job), '听1'))['files'][0]
        url = self.base + urllib.request.quote('/审片台/' + f['path'].split('/', 1)[1])
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=10-19'})) as r:
            self.assertEqual((r.status, r.headers['Content-Range'], r.read()), (206, f'bytes 10-19/{a.stat().st_size}', a.read_bytes()[10:20]))
        with urllib.request.urlopen(url) as r: self.assertEqual((r.headers['Content-Type'], len(r.read())), ('audio/mp4', a.stat().st_size))
        for bad in ('/审片台/../标记.json', '/审片台/听1/v1/没有.m4a'):
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(self.base + urllib.request.quote(bad))
            self.assertEqual(e.exception.code, 404)

    # —— 动画：用户点了，anim.gate 认 ——
    def test_anim_confirm_recognized_by_gate(self):
        shots = img(self.tmp / 's1a.png'), img(self.tmp / 's1b.png')
        self.assertTrue(any('S1 没有样张确认' in x for x in A.gate(self.job, self.hf, ['S1'], True)))
        it, live = B.publish_stills(self.job, [('S1', 1.5, shots[0]), ('S1', 2.5, shots[1])], self.hf, '请看 S1'); self.assertFalse(live)
        self.assertEqual((it['id'], it['column'], it['versions'][0]['anim']['shots']), ('动1', '动画', ['S1']))
        self.assertTrue(any('S1 没有样张确认' in x for x in A.gate(self.job, self.hf, ['S1'], True)))                   # 发布了还不算，要用户点
        code, out = self.act('动1', '可以'); self.assertTrue(out['ok']); self.assertTrue(out['event']['anim']['ok'], out)
        self.assertEqual(A.gate(self.job, self.hf, ['S1'], True), [])                                                 # 用户点了“可以”＝样张确认
        rec = A._records(self.job)['items'][-1]
        self.assertEqual((rec['kind'], rec['project'], rec['shots']), ('样张', 'HF_测', ['S1'])); self.assertRegex(rec['words'], r'^审片台：用户点了可以（\d{4}-\d\d-\d\d \d\d:\d\d:\d\d）$')
        self.assertTrue(all(Path(f).is_file() and str(W.home(self.job)) in f for f in rec['files']))
        it2, _ = B.publish_stills(self.job, [('S1', 1.5, shots[0])], self.hf, '再看'); self.assertEqual((it2['id'], len(it2['versions'])), ('动1', 2))   # 再出样张＝新版
        (self.hf / '镜头' / 'S1.html').write_text('<div>改过</div>', encoding='utf-8')                                 # 发布之后代码又改了：用户点了也不记
        code, out = self.act('动1', '可以'); self.assertTrue(out['ok']); self.assertFalse(out['event']['anim']['ok']); self.assertIn('改过', out['event']['anim']['msg'])
        self.assertTrue(any('S1 没有样张确认' in x for x in A.gate(self.job, self.hf, ['S1'], True)))
        self.assertIn('【注意】', self.cli('--list').stdout)
        # 草图三选一：关系类镜头选定才能出样张
        a, b, c = (img(self.tmp / f'草图{k}.png') for k in 'ABC')
        self.assertTrue(any('S2 讲关系' in x for x in A.gate(self.job, self.hf, ['S2'], False)))
        r = self.cli('--publish', '动画', '--kind', '多选一', '--title', 'S2 草图', '--look', '选一种画法', '--files', f'{a},{b},{c}', '--anim', '草图', '--project', str(self.hf), '--shots', 'S2', '--check', '远程改=手机聊天')
        self.assertNotEqual(r.returncode, 0); self.assertIn('实际没改', r.stderr)                                       # 每个选项都要逐条对照口播要点
        r = self.cli('--publish', '动画', '--kind', '多选一', '--title', 'S2 草图', '--look', '选一种画法', '--files', f'{a},{b},{c}', '--anim', '草图', '--project', str(self.hf), '--shots', 'S2',
                     '--check', '远程改=手机聊天', '--check', 'A:实际没改=红色没改章', '--check', 'B:实际没改=灯带位置不变', '--check', 'C:实际没改=前后对比')
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('动2', r.stdout)
        code, out = self.act('动2', '选这个', pick='B'); self.assertTrue(out['event']['anim']['ok'], out)
        self.assertEqual(A.gate(self.job, self.hf, ['S2'], False), [])
        rec = A._records(self.job)['items'][-1]
        self.assertEqual((rec['kind'], rec['words'][:10], [x['画面'] for x in rec['对照']]), ('草图', '审片台：用户选了 B'[:10], ['手机聊天', '灯带位置不变']))
        self.assertTrue(rec['words'].startswith('审片台：用户选了 B（')); self.assertTrue(rec['files'][0].endswith('草图B.png'))

    # —— 命令行、别的程序不能替用户点 ——
    def test_cannot_forge_user_confirm(self):
        B.publish(self.job, '封面', '封面', '选', '多选一', [img(self.tmp / 'a.png'), img(self.tmp / 'b.png')])
        body = {'id': '封1', 'v': 1, 'action': '选这个', 'pick': 'A'}
        for headers in ({}, {'Origin': self.base}, {'Origin': self.base, 'X-Shenpian-Token': 'wrong'},
                        {'Origin': self.base, 'X-Shenpian-Token': W.TOKEN, 'Sec-Fetch-Site': 'cross-site'},
                        {'Origin': 'https://evil.example', 'X-Shenpian-Token': W.TOKEN}, {'X-Shenpian-Token': W.TOKEN}):
            for path, b in (('/api/item/act', body), ('/api/script', {'field': '标题', 'value': '冒充'}), ('/api/upload-copy', {'description': 'x', 'tags': TAGS})):
                code, out = self.req(path, b, page=False, **headers); self.assertEqual(code, 403, (path, headers)); self.assertFalse(out['ok'])
        self.assertEqual(B.state(B.item(B.load(self.job), '封1'))[0], '等你看'); self.assertIn('旧的标题', (self.pd / '03_成片' / 'P1.md').read_text(encoding='utf-8'))
        r = self.cli('--pick', 'A'); self.assertEqual(r.returncode, 2)                                               # 命令行没有“替用户点”的参数
        for args in (['--reply', '封1', '--status', '已改', '--note', '用户说可以'], ['--reply', '封1', '--note', '可以']):
            self.assertEqual(self.cli(*args).returncode, 0)
        self.assertEqual(B.state(B.item(B.load(self.job), '封1'))[0], '等你看')                                       # 回复不会变成“你已定”
        self.assertFalse([x for x in B.item(B.load(self.job), '封1')['events'] if x['type'] == 'user'])
        html = urllib.request.urlopen(self.base + '/').read().decode(); self.assertIn(W.TOKEN, html); self.assertNotIn('__SHENPIAN_TOKEN__', html)

    # —— 文案：只改对应小节 ——
    def test_script_edit_only_that_section(self):
        md = self.pd / '03_成片' / 'P1.md'
        d = self.req('/api/data', None, page=False)[1]['board']['script']
        self.assertEqual((d['rel'], d['fields']['标题']['value'], d['fields']['封面文字']['value']), ('03_成片/P1.md', '旧的标题', '为什么外地\n灯光设计'))
        self.assertIn('### 开头', d['fields']['口播正文']['value'])
        self.assertTrue(self.req('/api/script', {'field': '标题', 'value': '新的标题'})[1]['ok'])
        self.assertEqual(md.read_text(encoding='utf-8'), DOC.replace('旧的标题', '新的标题'))                              # 其余一字不动
        self.assertTrue(self.req('/api/script', {'field': '口播正文', 'value': d['fields']['口播正文']['value'].replace('五次', '三次')})[1]['ok'])
        self.assertEqual(md.read_text(encoding='utf-8'), DOC.replace('旧的标题', '新的标题').replace('五次', '三次'))
        self.assertTrue(self.req('/api/script', {'field': '封面文字', 'value': '为什么外地\n\n我不接了\n'})[1]['ok'])
        t = md.read_text(encoding='utf-8'); self.assertIn('## 封面文字\n\n为什么外地\n我不接了\n\n## 口播正文', t)
        self.assertEqual(t.split('## 口播正文')[1], DOC.split('## 口播正文')[1].replace('五次', '三次'))
        self.assertFalse(self.req('/api/script', {'field': '标题', 'value': '  '})[1]['ok'])
        eds = B.load(self.job)['edits']; self.assertEqual([(e['field'], e['old'][:4], e['ok']) for e in eds], [('标题', '旧的标题', True), ('口播正文', '### ', True), ('封面文字', '为什么外', True)])
        r = self.cli('--list'); self.assertIn('[改1] 文案·标题｜', r.stdout); self.assertIn('改了 1 处：…【旧→新】的标题…', r.stdout)
        self.assertTrue(self.req('/api/script', {'field': '标题', 'value': '新的标题二'})[1]['ok'])                      # 10 分钟内接着改：同一条，原来的字留最早的
        e = B.load(self.job)['edits'][0]; self.assertEqual((e['id'], e['old'], e['new']), ('改1', '旧的标题', '新的标题二'))
        self.assertEqual(self.cli('--reply', '改1', '--note', '标题已同步').returncode, 0); self.assertNotIn('[改1]', self.cli('--list').stdout)
        md.write_text('只有口播。\n', encoding='utf-8')                                                                # 老写法：不改，说原因
        out = self.req('/api/script', {'field': '标题', 'value': 'x'})[1]; self.assertFalse(out['ok']); self.assertIn('老写法', out['error'])
        self.assertEqual(md.read_text(encoding='utf-8'), '只有口播。\n')

    # —— 上传文案：走视频项目管理的 copy-apply ——
    def test_upload_copy(self):
        pj = self.pd / '项目.json'; before = pj.read_text(encoding='utf-8'); md = (self.pd / '03_成片' / 'P1.md').read_text(encoding='utf-8')
        out = self.req('/api/upload-copy', {'description': '外地灯光设计讲解', 'tags': '灯光设计、筒灯'})[1]
        self.assertFalse(out['ok']); self.assertIn('标签 2 个', out['error'])                                           # copy-apply 的检查照样管
        self.assertEqual(pj.read_text(encoding='utf-8'), before); self.assertEqual((self.pd / '03_成片' / 'P1.md').read_text(encoding='utf-8'), md)
        dr = B.load(self.job)['drafts']['上传文案']; self.assertEqual((dr['description'], dr['tags']), ('外地灯光设计讲解', ['灯光设计', '筒灯']))
        out = self.req('/api/upload-copy', {'description': '我们做过两个外地的灯光设计，每个项目去五次左右现场', 'tags': '、'.join(TAGS)})[1]
        self.assertTrue(out['ok'], out); m = rj(pj); self.assertEqual((m['tags'], m['title']), (TAGS, '旧的标题'))
        self.assertIn('每个项目去五次', m['description']); self.assertNotIn('上传文案', B.load(self.job)['drafts'])
        out = self.req('/api/upload-copy', {'description': '我们做过 9 个外地的灯光设计', 'tags': '、'.join(TAGS)})[1]
        self.assertFalse(out['ok']); self.assertIn('9', out['error']); self.assertIn('每个项目去五次', rj(pj)['description'])      # 数字只用口播原话
        t = (self.pd / '03_成片' / 'P1.md').read_text(encoding='utf-8'); self.assertIn('## 简介\n\n我们做过两个外地的灯光设计，每个项目去五次左右现场', t)   # 文案里的「## 简介」没被错的这次改掉
        self.assertTrue(self.req('/api/upload-copy', {'description': '我们做过两个外地的灯光设计', 'tags': '、'.join(TAGS)})[1]['ok'])   # 文案里已有「## 简介」：先改文案那一节再写回
        self.assertEqual(rj(pj)['description'], '我们做过两个外地的灯光设计'); self.assertIn('## 简介\n\n我们做过两个外地的灯光设计\n', (self.pd / '03_成片' / 'P1.md').read_text(encoding='utf-8'))
        self.assertTrue(any(e['column'] == '上传文案' and e['field'] == 'description' for e in B.load(self.job)['edits']))

    # —— 导航文字栏：和原来一样 ——
    def test_nav_same_as_before(self):
        r = self.req('/api/nav', {'labels': {'q': ' 挑空难照亮 ', 'zz': 'x'}}, page=False)[1]; self.assertEqual(r['nav_labels'], {'q': '挑空难照亮'})
        d = self.req('/api/data', None, page=False)[1]; self.assertEqual(d['nav_labels'], {'q': '挑空难照亮'}); self.assertEqual(d['board']['badges']['导航文字']['text'], '1 条没发给 AI')     # 2026-11-22：改了没发，不再写“已定”
        with urllib.request.urlopen(urllib.request.Request(self.base + '/api/navshot', b'{}', {'Content-Type': 'application/json'})) as x: self.assertEqual(x.headers['Content-Type'], 'image/jpeg')
        self.assertIn('用户改的导航条文字：{"q": "挑空难照亮"}', self.cli('--list').stdout)
        # 261122：AI 照改完没法标成已处理，页面一直挂着“1 条没发给 AI”。--reply 导航条 了结；用户再改又是新的一件；导航条已经是这些字也算了结
        badge = lambda: self.req('/api/data', None, page=False)[1]['board']['badges']['导航文字']['text']
        self.assertNotEqual(self.cli('--reply', '导航条').returncode, 0)                                   # 要带说明
        r = self.cli('--reply', '导航条', '--note', '4 段导航条已做进审核线第 7 版'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(badge(), '没有待办'); self.assertIn('AI：4 段导航条已做进审核线第 7 版', self.cli('--list').stdout)
        self.req('/api/nav', {'labels': {'q': '挑空怎么照亮'}}, page=False); self.assertEqual(badge(), '1 条没发给 AI')
        same = {c['id']: c['label'] for c in W.chapters(self.job)}
        W.edit(self.job, lambda d: d.update(nav_labels={'q': same['q']})); self.assertEqual(badge(), '没有待办')     # “已定”只认用户的确认，没人确认过写“没有待办”

    def test_page_has_columns(self):
        html = urllib.request.urlopen(self.base + '/').read().decode()
        for c in B.COLUMNS:
            if c != '导航文字': self.assertIn(f'data-col="{c}"', html)
        # 导航文字并进审核线（2026-11-22 用户定：“合并吧”）：没有这一栏、章节标题能改、栏头有“导航条预览”、#导航文字 落到审核线
        self.assertNotIn('data-col="导航文字"', html); self.assertIn("shownCols=()=>BD.columns.filter(c=>!MERGE[c])", html)
        self.assertIn('<input class="chin', html); self.assertIn('id="navbtn"', html); self.assertIn('导航条预览', html)
        self.assertIn("const MERGE={'导航文字':'审核线'}", html); self.assertIn('function setCol(c,quiet){c=MERGE[c]||c;', html)
        self.assertIn("h=MERGE[h]||h", html)
        for w in ('选这个', '可以', '提意见', '只看这一轮', '这一帧有问题', '<title>审片台</title>'): self.assertIn(w, html)
        self.assertNotRegex(html, r'(src|href)="https?://')
        js = self.tmp / 'p.js'; js.write_text(html.split('<script>')[1].split('</script>')[0], encoding='utf-8')
        if shutil.which('node'):
            r = subprocess.run(['node', '--check', str(js)], capture_output=True, text=True); self.assertEqual(r.returncode, 0, r.stderr)


    # —— 审片台 v2（2026-11-22 用户定）：页面只用 4 个状态，服务端算好 ——
    def test_unified_state(self):
        """待你看／待发送／AI 在改／已定／无：每一项、每条标记、每一栏都算对；老字段（state、badges）照旧。"""
        meta = lambda: self.req('/api/data', None, page=False)[1]
        ust = lambda iid: next(x for x in meta()['board']['items'] if x['id'] == iid)['ustate']
        col = lambda c: meta()['board']['ucols'][c]
        a = img(self.tmp / '封A.jpg'); b = img(self.tmp / '封B.jpg', (1, 2, 3))
        self.assertEqual((col('封面')['state'], col('封面')['off']), ('', True))                         # 还没到：整栏灰
        self.assertEqual(col('上传文案')['state'], '')                                                      # 没事做：不挂标签
        B.publish(self.job, '封面', '封面', '选一个', '多选一', [a, b])
        self.assertEqual((ust('封1'), col('封面')['state'], col('封面')['off']), ('待你看', '待你看', False))
        self.act('封1', '选这个', pick='B'); self.assertEqual((ust('封1'), col('封面')['state']), ('待发送', '待发送'))
        self.assertEqual(meta()['board']['totals'], {'轮到你': 1, 'AI 在改': 0})
        self.req('/api/send', {'column': '封面'}); self.assertEqual((ust('封1'), col('封面')['state']), ('AI 在改', 'AI 在改'))
        self.assertEqual(meta()['board']['totals'], {'轮到你': 0, 'AI 在改': 1})
        B.reply(self.job, '封1', '收到'); self.assertEqual((ust('封1'), col('封面')['state']), ('已定', '已定'))   # 用户点过、AI 回了：已定
        self.act('封1', '写要求', text='圈再大一点'); self.assertEqual(ust('封1'), '待发送')
        self.req('/api/send', {'column': '封面'}); self.assertEqual(ust('封1'), 'AI 在改')
        B.reply(self.job, '封1', '圈放大了', [b, a]); self.assertEqual((ust('封1'), col('封面')['state']), ('待你看', '待你看'))   # 换了新版：待你看
        x = next(x for x in meta()['board']['items'] if x['id'] == '封1'); self.assertEqual((x['state'], x['decided']), ('AI 已回复', False))   # 老字段照旧
        # 审核线：没写字的不挂；写了待发送；交了 AI 在改；AI 回了待你看；这一栏定了以后已定；以前轮次的不挂
        m = self.req('/api/markrange', {'span': [0, 2], 't': 0.0, 't_end': 0.5, 'quote': '第一'})[1]; mid = m['id']      # 选了字还没写、没截图
        mk = lambda: next(x for x in meta()['markers'] if x['id'] == mid)['ustate']
        self.assertEqual(mk(), '')
        self.req('/api/text', {'id': mid, 'text': '角标去掉'}); self.assertEqual((mk(), col('审核线')['state']), ('待发送', '待发送'))
        self.req('/api/send', {'column': '审核线'}); self.assertEqual((mk(), col('审核线')['state']), ('AI 在改', 'AI 在改'))
        W.reply(self.job, mid, '已改', '去掉了'); self.assertEqual((mk(), col('审核线')['state'], col('审核线')['counts']['待你看']), ('待你看', '待你看', 1))
        self.assertTrue(self.req('/api/settle', {'column': '审核线'})[1]['ok']); self.assertEqual((mk(), col('审核线')['state']), ('已定', '已定'))
        W.set_round(self.job, 2); self.assertEqual(mk(), '')
        # 用户自己写的字（导航文字）：定稿以后栏目已定，那条卡片 AI 在改
        self.req('/api/nav', {'labels': {'q': '挑空怎么照亮'}}, page=False); self.assertEqual(col('导航文字')['state'], '待发送')
        self.req('/api/settle', {'column': '导航文字'}); self.assertEqual((col('导航文字')['state'], col('导航文字')['counts']['AI 在改']), ('已定', 1))
        for s in ('待你看', '待发送', 'AI 在改', '已定'): self.assertIn(s, B.USTATES)

    def test_marker_kind(self):
        """提意见时点的类型存进标记的 kind，--list 打印出来（让 AI 读到有结构的意见）；可以不选、能清掉；旧轮次不能改。"""
        m = self.req('/api/markrange', {'span': [0, 4], 't': 0.0, 't_end': 1.5, 'quote': '第一句话', 'text': '字幕错字', 'kind': '字幕'})[1]
        self.assertEqual(W.load(self.job)['markers'][0]['kind'], '字幕')
        r = self.req('/api/markkind', {'id': m['id'], 'kind': '动画'})[1]; self.assertTrue(r['ok']); self.assertEqual(W.load(self.job)['markers'][0]['kind'], '动画')
        self.assertFalse(self.req('/api/markkind', {'id': m['id'], 'kind': '乱写'})[1]['ok']); self.assertEqual(W.load(self.job)['markers'][0]['kind'], '动画')
        self.assertIn('[待处理] 字幕错字｜类型：动画｜', self.cli('--list').stdout)
        self.req('/api/send', {'column': '审核线'}); self.req('/api/markkind', {'id': m['id'], 'kind': '花字'})
        self.assertEqual(self.req('/api/data', None, page=False)[1]['board']['send']['ids'][f"#{m['id']}"], 'sent')   # 只改类型不算重新写了意见
        self.assertTrue(self.req('/api/markkind', {'id': m['id'], 'kind': ''})[1]['ok']); self.assertNotIn('kind', W.load(self.job)['markers'][0])
        self.assertNotIn('类型：', self.cli('--list').stdout)
        self.req('/api/markkind', {'id': m['id'], 'kind': 'B-roll'}); W.set_round(self.job, 2)
        self.assertFalse(self.req('/api/markkind', {'id': m['id'], 'kind': '声音'})[1]['ok'])
        a = self.req('/api/again', {'id': m['id']})[1]; self.assertEqual(a['kind'], 'B-roll')                  # “这条还不行”接着用原来的类型
        self.assertEqual(B.MARK_KINDS, ('字幕', '花字', '动画', 'B-roll', '节奏', '声音', '其它'))

    def test_shot_spans_in_page_data(self):
        """全片时间条：页面数据里有动画、实拍、图片镜头的区间（片子目录 分镜.json 的 shots；露脸镜头不画；没有就是空的）。"""
        self.assertEqual(self.req('/api/data', None, page=False)[1]['shots'], [])
        wj(self.job.dir / '分镜.json', {'shots': [{'id': 'F_0', 'kind': 'F', 'a': 0, 'b': 2}, {'id': 'B1', 'kind': 'B', 'a': 2, 'b': 5, '盖实拍': [{'a': 3, 'b': 4, '文件': 'x.mov'}]},
                                                  {'id': 'B2', 'kind': 'B', 'a': 5, 'b': 7, '实拍': {'文件': 'y.mov'}}, {'id': 'B3', 'kind': 'B', 'a': 7, 'b': 8, '图片': {'文件': ['z.jpg']}}, {'kind': 'B'}]})
        self.assertEqual(self.req('/api/data', None, page=False)[1]['shots'], [{'id': 'B1', 'kind': '动画', 'a': 2.0, 'b': 5.0}, {'id': 'B1', 'kind': '实拍', 'a': 3.0, 'b': 4.0},
                                                                              {'id': 'B2', 'kind': '实拍', 'a': 5.0, 'b': 7.0}, {'id': 'B3', 'kind': '图片', 'a': 7.0, 'b': 8.0}])
        (self.job.dir / '分镜.json').write_text('坏了', encoding='utf-8'); self.assertEqual(self.req('/api/data', None, page=False)[1]['shots'], [])

    def test_settle_without_send_first(self):
        """2026-11-24 用户：“交给AI和确定、不确定不应该是绑定的……没有交给AI的情况下，我也可以点确定，把这个方向都确定”：
        选了方向直接点“这一栏定了”就能定，选的一起交给 AI（AI 才知道选了哪个）；能取消。"""
        meta = lambda: self.req('/api/data', None, page=False)[1]['board']
        a = img(self.tmp / '封A.jpg'); b = img(self.tmp / '封B.jpg', (1, 2, 3))
        self.cli('--publish', '封面', '--kind', '多选一', '--title', '封面两个方向', '--look', '选一个', '--files', f'{a},{b}')
        self.act('封1', '选这个', pick='B')
        r = self.req('/api/settle', {'column': '封面'})[1]; self.assertTrue(r['ok'], r); self.assertEqual(r['sent'], 1)
        self.assertEqual(meta()['settle']['封面']['state'], 'ok')
        self.assertIn('封1', self.cli('--list').stdout)                                                # AI 拿到“选了 B”
        self.req('/api/settle', {'column': '封面', 'on': False}); self.assertNotEqual(meta()['settle']['封面']['state'], 'ok')
        page = W.PAGE.read_text(encoding='utf-8'); self.assertIn('alt:send', page)                     # 能定时“交给 AI”和“这一栏定了”两个按钮都给

    def test_publish_to_yixiaoer_draft(self):
        """2026-11-24 用户：“我想加一个发布功能，就上传到蚁小二的草稿……定时发送可以在这里写”；“什么时候发做按钮，其他要求给发布 AI 一句话”。
        设置只收页面请求；审核线没点“确定此栏”不能存；定时要填将来的时间；点了＝这一栏定稿＋一件交给 AI 的事；AI 回话后显示。"""
        from thinkcut import publish as P
        meta = lambda: self.req('/api/data', None, page=False)[1]['board']
        self.assertEqual(self.req('/api/publish/set', {'mode': 'draft'}, page=False)[0], 403)            # 命令行不能替用户填
        self.assertTrue(self.req('/api/publish/set', {'mode': 'schedule', 'time': '2000-01-01T20:00', 'note': '只发抖音和小红书'})[1]['ok'])
        m = meta()['publish']; self.assertEqual((m['settings']['mode'], m['settings']['time'], m['gate']['ok']), ('schedule', '2000-01-01 20:00', False))
        r = self.req('/api/publish/request', {})[1]; self.assertFalse(r['ok']); self.assertIn('10 分钟以后', r['error'])   # 时间过了
        r = self.req('/api/publish/request', {'mode': 'draft'})[1]; self.assertFalse(r['ok']); self.assertIn('确定此栏', r['error'])   # 审核线没定
        self.assertEqual(self.req('/api/publish/request', {'mode': 'draft'}, page=False)[0], 403)
        self.assertTrue(self.req('/api/settle', {'column': '审核线'})[1]['ok']); self.assertTrue(meta()['publish']['gate']['ok'])
        import datetime; later = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime('%Y-%m-%dT20:00')
        self.assertTrue(self.req('/api/publish/request', {'mode': 'schedule', 'time': later})[1]['ok'])
        m = meta(); self.assertEqual(m['settle']['上传文案']['state'], 'ok'); self.assertFalse(m['publish']['req']['handled'])   # 点了＝这一栏定稿
        out = self.cli('--list').stdout
        for need in ('【用户要存到蚁小二草稿】', '只发抖音和小红书', '--schedule', '绝不调用发布'): self.assertIn(need, out)
        self.assertIsNotNone(P.key(self.job))
        r = self.cli('--reply', '发布', '--note', '存好了：抖音、小红书；定时明晚 8 点'); self.assertEqual(r.returncode, 0, r.stderr)
        m = meta()['publish']['req']; self.assertIn('抖音、小红书', m['handled']['note']); self.assertIsNone(P.key(self.job))   # 回过话：不再等 AI
        self.assertNotIn('用户要存到蚁小二草稿', self.cli('--list').stdout)
        page = W.PAGE.read_text(encoding='utf-8')
        for need in ("'上传文案':'发布'", '存到蚁小二草稿', '给发布 AI 的话', 'datetime-local', '/api/publish/request'): self.assertIn(need, page)

    def test_page_words(self):
        """页面上 4 个状态词齐；老状态词不再出现在给用户看的文字里（只在把服务端老说法换成新说法的那一行 FIX 里）。"""
        page = W.PAGE.read_text(encoding='utf-8')
        for w in ('待你看', '待发送', 'AI 在改', '已定', '还没到', '提意见', '交给 AI', '确定此栏', '定稿', '全部摆开', '这条还不行'): self.assertIn(w, page)
        # 2026-11-24 用户定“第一次看就懂”：页面上显示 等你看／还没交给 AI／AI 修改中／已定、等你处理、AI 已连／AI 未连、达芬奇已断开（数据里的名字不变）
        for w in ("'待你看':'等你看'", "'待发送':'还没交给 AI'", "'AI 在改':'AI 修改中'", '等你处理', "'AI 已连':'AI 未连'", '达芬奇已断开'): self.assertIn(w, page)
        rest = '\n'.join(l for l in page.split('\n') if not l.startswith('const FIX='))
        for old in ('AI 改中', '还没发给 AI', '已发给 AI', 'AI 已回复', '没有待办', '你定了', '你已定', '发给 AI', 'AI 改完了', '已发', '写要求', 'AI 没在等', '达芬奇连不上'):
            hits = [l.strip()[:80] for l in rest.split('\n') if old in l and "action==='写要求'" not in l and "action:'写要求'" not in l and "action!=='写要求'" not in l and 'action 仍是 写要求' not in l]
            self.assertFalse(hits, (old, hits))


    # —— 2026-11-22 用户定：选中的字只是定位，AI 按意思判断范围；拿不准回问（问你），用户在卡片上回答；改了哪几处用 --where 标 ——
    def test_semantic_markers_ask_answer_where(self):
        meta = lambda: self.req('/api/data', None, page=False)[1]
        ust = lambda mid: next(x for x in meta()['markers'] if x['id'] == mid)['ustate']
        old = self.req('/api/markrange', {'span': [0, 2], 't': 0.0, 't_end': 0.4, 'quote': '第一', 'text': '口误'})[1]['id']      # 老样子的标记：没有新字段
        mid = self.req('/api/markrange', {'span': [8, 10], 't': 3.2, 't_end': 3.6, 'quote': '话说', 'text': '这句太啰嗦'})[1]['id']
        out = self.cli('--list').stdout
        self.assertIn('【选中的字只是定位】', out); self.assertIn('--status 问你', out)
        self.assertIn('   上下文：章节：问题｜整句：第一句话说灯。｜后一句：第二句话说顶。', out)                                  # 第一句：没有前一句
        self.assertIn('   上下文：章节：问题｜前一句：第一句话说灯。｜整句：第二句话说顶。｜后一句：第三句话收尾。', out)
        self.assertNotIn('问答', out); self.assertEqual(ust(old), '待发送')                                                     # 老标记照常
        self.req('/api/send', {'column': '审核线'}); self.cli('--list')                                                       # AI 接了
        self.assertEqual(ust(mid), 'AI 在改')
        for bad in (['--status', '问你', '--where', '1-2'], ['--status', '已改', '--where', '1:75-2'], ['--status', '已改', '--where', '5-3'], ['--status', '已改', '--where', 'abc']):
            r = self.cli('--reply', str(mid), '--note', 'x', *bad); self.assertNotEqual(r.returncode, 0, bad); self.assertIn('--where', r.stderr)
        r = self.cli('--reply', '封1', '--note', 'x', '--where', '1-2'); self.assertNotEqual(r.returncode, 0); self.assertIn('只给审核线', r.stderr)
        self.assertEqual(W.load(self.job)['markers'][1]['status'], '待处理')                                                   # 写错的都没存
        r = self.cli('--reply', str(mid), '--status', '问你', '--note', '是只删“话说”两个字，还是整句重说？'); self.assertEqual(r.returncode, 0, r.stderr)
        m = W.load(self.job)['markers'][1]; self.assertEqual((m['status'], m['reply'], [x['who'] for x in m['thread']]), ('问你', '是只删“话说”两个字，还是整句重说？', ['AI']))
        self.assertEqual(ust(mid), '待你看'); self.assertEqual(meta()['board']['ucols']['审核线']['state'], '待你看')
        self.assertIn('问答', self.cli('--list', '--all').stdout)
        self.assertFalse(self.req('/api/answer', {'id': mid, 'text': '  '})[1]['ok'])
        self.assertTrue(self.req('/api/answer', {'id': mid, 'text': '整句重说，和开头那句呼应'})[1]['ok'])
        m = W.load(self.job)['markers'][1]; self.assertEqual((m['status'], m['text'], [x['who'] for x in m['thread']]), ('待处理', '这句太啰嗦', ['AI', '用户']))   # 原来的话不动
        self.assertEqual(ust(mid), '待发送')                                                                                  # 回答了：回到待发送（虽然以前发过）
        (W.home(self.job) / '服务.json').write_text(json.dumps({'pid': os.getpid(), 'port': self.port, 'url': self.base + '/', 'dir': str(self.job.dir)}))
        self.assertTrue(self.req('/api/send', {'column': '审核线'})[1]['ok'])
        r = self.cli('--wait', '--wait-timeout', '20'); self.assertIn(f'#{mid} ', r.stdout); self.assertIn('用户：整句重说，和开头那句呼应', r.stdout); self.assertIn('AI 问：是只删', r.stdout)
        r = self.cli('--reply', str(mid), '--status', '已改', '--note', '我理解你的意思是整句重说并和开头呼应，所以改了这两处', '--where', '0:00.5-0:01.9, 3-4.5')
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('改了 2 处', r.stdout)
        m = W.load(self.job)['markers'][1]; self.assertEqual(m['touched'], [{'a': 0.5, 'b': 1.9}, {'a': 3.0, 'b': 4.5}]); self.assertEqual([x['who'] for x in m['thread']], ['AI', '用户', 'AI'])
        self.assertIn('AI 改了这几处：0:00.50–0:01.90、0:03.00–0:04.50', self.cli('--list', '--all').stdout)
        self.assertEqual(ust(mid), '待你看')
        self.cli('--reply', str(mid), '--status', '问你', '--note', '还要不要再短一点？')                                       # 又问：用户直接改自己原来那句话也算回答
        self.req('/api/text', {'id': mid, 'text': '这句太啰嗦，再短一点'}); self.assertEqual(ust(mid), '待发送')
        page = W.PAGE.read_text(encoding='utf-8')
        for need in ('选中的字只是告诉 AI 大概在哪', 'AI 问你', "post('/api/answer'", 'AI 改了这几处', "CSS.highlights.set('touched'", 'tchb'): self.assertIn(need, page)

    # —— 2026-11-22 夜用户定：每条意见都是一段对话；AI 给的选项能直接点；回复能带图 ——
    def test_conversation_options_and_images(self):
        meta = lambda: self.req('/api/data', None, page=False)[1]
        mk = lambda mid: next(x for x in meta()['markers'] if x['id'] == mid)
        png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
        self.assertEqual(B.parse_options('A 片尾多留 1 秒｜B 不加时长、提前出| C 只在最后闪一下 '), ['A 片尾多留 1 秒', 'B 不加时长、提前出', 'C 只在最后闪一下'])
        for bad in ('只有一个', 'A x|A x', ''): self.assertRaises(B.Oops, B.parse_options, bad)
        self.assertEqual(B.auto_options('想了三种。A：片尾多留 1 秒；B：不加时长、提前出；C：只在最后闪一下。你选哪个？'), ['A：片尾多留 1 秒', 'B：不加时长、提前出', 'C：只在最后闪一下'])
        self.assertEqual(B.auto_options('有两种：1. 字幕放大 2. 换颜色'), ['1. 字幕放大', '2. 换颜色'])
        self.assertEqual(len(B.auto_options('超大的动画来不及看。你要哪种？A：片尾多留约 1 秒（片子变长 1 秒）；B：不加时长；C：只在最后闪一下。')), 3)     # 2026-11-23 真用时拆不出：A 紧跟在问号后面
        for no in ('这次只改了字幕颜色，B-roll 没动。', 'A 是对的，B 不行', '选 A 还是 B？'): self.assertEqual(B.auto_options(no), [], no)
        old = self.req('/api/markrange', {'span': [0, 2], 't': 0.0, 't_end': 0.4, 'quote': '第一', 'text': '口误'})[1]['id']
        self.assertFalse(self.req('/api/answer', {'id': old, 'text': '还没回就回复'})[1]['ok'])                                   # AI 还没回：直接改自己的话
        W.edit(self.job, lambda d: W.marker(d, old).update(status='没改', reply='两种做法。A：重录这句；B：只改字幕。'))         # 老数据（只有 reply）：从话里拆按钮
        self.assertEqual(mk(old)['opts'], ['A：重录这句', 'B：只改字幕']); self.assertNotIn('thread', W.marker(W.load(self.job), old))
        mid = self.req('/api/markrange', {'span': [6, 9], 't': 3.0, 't_end': 3.4, 'quote': '第二句', 'text': '结尾太快'})[1]['id']
        self.req('/api/send', {'column': '审核线'}); self.cli('--list')
        r = self.cli('--reply', str(mid), '--status', '问你', '--note', '片尾怎么处理？', '--options', '只有一个'); self.assertNotEqual(r.returncode, 0); self.assertIn('至少要 2 个', r.stderr)
        r = self.cli('--reply', str(mid), '--status', '问你', '--note', '片尾怎么处理？', '--options', 'A 片尾多留 1 秒|B 不加时长、提前出|C 只在最后闪一下'); self.assertEqual(r.returncode, 0, r.stderr)
        m = mk(mid); self.assertEqual((m['ustate'], m['opts']), ('待你看', ['A 片尾多留 1 秒', 'B 不加时长、提前出', 'C 只在最后闪一下']))
        self.assertFalse(self.req('/api/answer', {'id': mid, 'picked': 'D 乱写'})[1]['ok'])
        self.assertTrue(self.req('/api/answer', {'id': mid, 'picked': 'B 不加时长、提前出'})[1]['ok'])
        m = mk(mid); self.assertEqual((m['ustate'], m['thread'][-1]['picked'], m['thread'][-1]['text'], m['opts']), ('待发送', 'B 不加时长、提前出', '选了：B 不加时长、提前出', []))
        self.assertFalse(self.req('/api/answer', {'id': mid, 'imgs': ['图/没有.png']})[1]['ok'])
        n1 = self.req('/api/replyimg', {'id': mid, 'data': png})[1]['name']; self.assertTrue((W.home(self.job) / n1).is_file())
        self.assertTrue(self.req('/api/answer', {'id': mid, 'text': '再补一句：字也小一点', 'imgs': [n1]})[1]['ok'])
        self.assertEqual(W.marker(W.load(self.job), mid).get('frame'), '')                                                       # 回复带的图不挂到意见自己的图上
        (W.home(self.job) / '服务.json').write_text(json.dumps({'pid': os.getpid(), 'port': self.port, 'url': self.base + '/', 'dir': str(self.job.dir)}))
        self.assertTrue(self.req('/api/send', {'column': '审核线'})[1]['ok'])
        r = self.cli('--wait', '--wait-timeout', '20')
        for need in ('给的选项：A 片尾多留 1 秒 | B 不加时长、提前出 | C 只在最后闪一下', '用户：选了：B 不加时长、提前出｜选了：B 不加时长、提前出', '用户：再补一句：字也小一点｜图 ' + str(W.home(self.job) / n1)):
            self.assertIn(need, r.stdout)
        self.cli('--reply', str(mid), '--status', '已改', '--note', '按 B 改了，字也小了'); self.assertEqual(mk(mid)['ustate'], '待你看')
        n2 = self.req('/api/replyimg', {'id': mid, 'shot': True})[1]['name']; self.assertIn('回复_第', n2)                       # 截达芬奇当前画面（替身）
        self.assertTrue(self.req('/api/answer', {'id': mid, 'text': '还是不对', 'imgs': [n2]})[1]['ok']); self.assertEqual(mk(mid)['ustate'], '待发送')   # 已改以后又回：接着改
        self.assertEqual([x['who'] for x in W.marker(W.load(self.job), mid)['thread']], ['AI', '用户', '用户', 'AI', '用户'])
        # 其余栏卡片：多次回复、带图、点选项
        a, b = img(self.tmp / 'cA.jpg'), img(self.tmp / 'cB.jpg', (1, 2, 3)); B.publish(self.job, '封面', '封面', '选一个', '多选一', [a, b])
        self.act('封1', '写要求', text='字太小')
        self.req('/api/send', {'column': '封面'}); self.cli('--list')
        B.reply(self.job, '封1', '放大到多少？', options='A 放大一倍|B 放大一半')
        it = lambda: next(x for x in meta()['board']['items'] if x['id'] == '封1')
        self.assertEqual((it()['ustate'], it()['opts']), ('待你看', ['A 放大一倍', 'B 放大一半']))
        self.assertEqual(self.req('/api/item/replyimg', {'id': '封1', 'data': png}, page=False)[0], 403)                          # 只收页面的
        self.assertFalse(self.act('封1', '写要求', append=True, picked='C 乱写')[1]['ok'])
        self.assertTrue(self.act('封1', '写要求', append=True, picked='B 放大一半')[1]['ok']); self.assertEqual(it()['ustate'], '待发送')
        n3 = self.req('/api/item/replyimg', {'id': '封1', 'data': png})[1]['name']; self.assertTrue(n3.startswith('审片台/封1/回复/'))
        self.assertFalse(self.act('封1', '写要求', append=True, imgs=['../标记.json'])[1]['ok'])
        self.assertTrue(self.act('封1', '写要求', append=True, text='再加粗', imgs=[n3])[1]['ok'])
        us = [e for e in B.item(B.load(self.job), '封1')['events'] if e['type'] == 'user']
        self.assertEqual([(e['text'], e.get('picked'), e.get('imgs')) for e in us], [('字太小', None, None), ('选了：B 放大一半', 'B 放大一半', None), ('再加粗', None, [n3])])   # 每次回复都是新的一条
        out = self.cli('--list').stdout
        for need in ('对话 ', 'AI：放大到多少？｜给的选项：A 放大一倍 | B 放大一半', '用户：选了：B 放大一半｜选了：B 放大一半', f'用户：再加粗｜图 {W.home(self.job)}/{n3}'): self.assertIn(need, out)
        with urllib.request.urlopen(self.base + '/' + urllib.parse.quote(n3)) as x: self.assertEqual(x.headers['Content-Type'], 'image/png')
        B.reply(self.job, '封1', '换了新版', [b, a]); self.assertFalse(self.act('封1', '写要求', append=True, text='x', v=1)[1]['ok'])   # 不是最新版不收
        page = W.PAGE.read_text(encoding='utf-8')
        for need in ('function replyBox(', 'class="btn ropt"', 'rshot', "post('/api/item/replyimg'", 'AI 回了话，就在卡片最下面直接回它'): self.assertIn(need, page)

    def test_every_overlay_closes_the_same_way(self):
        """规矩 8（2026-11-22 用户定：“我点击空白的位置，它应该自动退出……整个审片台里都是这样的”）：盖在页面上的东西都带 class="ovl"，
        由同一个 closeOvl 管（点外面、Esc）；以后新加的弹出框（details.pop、fixed 浮层）漏了 ovl 这条测试就会挂。"""
        import re
        page = W.PAGE.read_text(encoding='utf-8'); ed = W.EDITOR.read_text(encoding='utf-8')
        for need in ('function closeOvl(el)', "document.querySelectorAll('.ovl')", "e.key!=='Escape'", 'function flushSaves()', 'window.closeOvlAll'): self.assertIn(need, page)
        for oid in ('zoom', 'navpop', 'dvpop', 'help'): self.assertRegex(page, rf'id="{oid}" class="[^"]*\bovl\b')
        self.assertNotIn('<details class="tc', page); self.assertNotIn('class="tcbtn tcgo"', page)             # 2026-11-24 第二轮：“标题 · 封面文字 · 原稿”那一行整个去掉（用户：独立占一行很浪费），顶上点“文案”就是
        self.assertRegex(page, r'id="wver" class="[^"]*\bovl\b')                                              # 文案栏的“版本”是新加的浮层，同样归 closeOvl 管
        self.assertFalse([m for m in re.findall(r'<details[^>]*class="[^"]*\bpop\b[^"]*"', page) if 'ovl' not in m])   # 页面里的弹出框都归 closeOvl 管
        self.assertNotRegex(page, r"\$\('zoom'\)\.className='(on|)'")                                       # 大图开关不会把 ovl 弄丢
        for need in ('function closePops(', "details.pop[open]", 'closeOvlAll'): self.assertIn(need, ed)        # 编辑器的“我的改动”“帮助”也点外面收起


class HookTests(unittest.TestCase):
    """出样张自动发布、review 打开到审核线栏（不起真服务）。"""
    def setUp(self): self.tmp = Path(tempfile.mkdtemp()); self.pd, self.job, self.hf = project(self.tmp)
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)

    def test_still_sheet_publishes_and_preview_only_when_page_closed(self):
        f1, f2 = img(self.tmp / 'S1_1.50秒.jpg'), img(self.tmp / 'S2_4.50秒.jpg')
        opened = []; keep = (A.open_file, B.page_open); self.addCleanup(lambda: (setattr(A, 'open_file', keep[0]), setattr(B, 'page_open', keep[1])))
        A.open_file = lambda p: opened.append(p) or True
        B.page_open = lambda job: False
        A.still_sheet(self.job, [('S1', 1.5, f1), ('S2', 4.5, f2)], self.tmp / '拼图.jpg', self.hf)
        it = B.item(B.load(self.job), '动1'); v = it['versions'][0]
        self.assertEqual((it['title'], v['kind'], v['anim']['kind'], v['anim']['shots'], [x['label'] for x in v['files']]), ('S1、S2 样张（HF_测）', '可以', '样张', ['S1', 'S2'], ['S1 第 1.5 秒', 'S2 第 4.5 秒']))
        self.assertIn('请看 S1', v['look']); self.assertEqual(len(opened), 1)                                                # 页面没开：照旧弹“预览”
        B.page_open = lambda job: True
        A.still_sheet(self.job, [('S1', 1.5, f1), ('S2', 4.5, f2)], self.tmp / '拼图.jpg', self.hf)
        self.assertEqual(len(opened), 1); self.assertEqual(len(B.item(B.load(self.job), '动1')['versions']), 2)       # 页面开着：不弹，同一项换新版
        os.environ['THINKCUT_SELF_CHECK'] = '1'; self.addCleanup(os.environ.pop, 'THINKCUT_SELF_CHECK', None)                 # 子助手自查：不发布、不弹
        B.page_open = lambda job: False; A.still_sheet(self.job, [('S1', 1.5, f1)], self.tmp / '拼图.jpg', self.hf)
        self.assertEqual((len(opened), len(B.item(B.load(self.job), '动1')['versions'])), (1, 2))

    def test_launch_opens_column(self):
        got = []; keep = (W.start, W.open_page); self.addCleanup(lambda: (setattr(W, 'start', keep[0]), setattr(W, 'open_page', keep[1])))
        W.start = lambda job, port=None: ('http://127.0.0.1:8765/', True); W.open_page = lambda u: got.append(u) or True
        self.assertEqual(W.launch(self.job, column='审核线'), 'http://127.0.0.1:8765/#%E5%AE%A1%E6%A0%B8%E7%BA%BF')
        self.assertEqual(got[-1], 'http://127.0.0.1:8765/#%E5%AE%A1%E6%A0%B8%E7%BA%BF'); self.assertEqual(W.launch(self.job), 'http://127.0.0.1:8765/')
        with self.assertRaises(SystemExit): W.launch(self.job, column='别的')
        self.assertIn("workbench.launch(job, column='审核线')", (Path(W.__file__).parent / 'resolve.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
