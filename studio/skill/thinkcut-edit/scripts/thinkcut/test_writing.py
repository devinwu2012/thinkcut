# -*- coding: utf-8 -*-
"""审片台第一栏“文案”（数据里叫 写稿，2026-11-24 用户定）离线测试：不连达芬奇，起真的本机服务走页面请求。run_tests.sh 会跑。
读写各节；从空白开始、贴一大段字；提意见 → 交给 AI → --wait 打印带上下文；AI 交改写建议 → 逐处采纳／不要，只有采纳才落盘；
命令行不能替用户采纳、定稿；版本和退回；定稿、改后失效、定稿进 --wait；只有项目文件夹时只有文案栏能用。
要用到视频项目管理的 script_doc：开发时用环境变量 THINKCUT_PM 指到它的 bin（合并后不用设）。"""
import json, os, shutil, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinkcut import workbench as W, board as B, writing as X    # noqa: E402
from thinkcut.common import wj                                    # noqa: E402
from thinkcut.test_workbench import FakeDV                        # noqa: E402
from thinkcut.test_board import project, DOC                      # noqa: E402
os.environ['THINKCUT_NO_OPEN'] = '1'

S = Path(__file__).resolve().parents[1] / 'thinkcut_edit.py'
BODY = '### 开头\n\n第一段，讲收口。\n\n> 这里停一下\n\n第二段，讲打胶。\n\n第三段，讲开关对齐。'


class Base(unittest.TestCase):
    early = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        if self.early:                      # 只有项目文件夹：项目.json、01_原始素材，别的什么都没有
            self.pd = self.tmp / '261124 新片'; (self.pd / '01_原始素材').mkdir(parents=True); wj(self.pd / '项目.json', {'name': self.pd.name, 'title': ''})
            self.job = W.open_film(self.pd); self.srv = W.make_server(self.job, 0)
        else:
            self.pd, self.job, _ = project(self.tmp); self.srv = W.make_server(self.job, 0, FakeDV())
        self.doc = self.pd / '03_成片' / f'{self.pd.name}.md'; self.port = self.srv.server_address[1]; self.base = f'http://127.0.0.1:{self.port}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, p, body=None, page=True):
        """page=True：像页面一样带来源和口令。返回 (状态码, 内容)。"""
        h = {'Content-Type': 'application/json', **({'Origin': self.base, 'Sec-Fetch-Site': 'same-origin', 'Cookie': W.cookie_header(self.base)} if page else {})}
        q = urllib.request.Request(self.base + p, json.dumps(body or {}).encode() if body is not None else None, h)
        try:
            with urllib.request.urlopen(q) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def ok(self, p, body):
        code, out = self.req(p, body); self.assertEqual(code, 200, out); self.assertTrue(out['ok'], out); return out

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.job.dir), *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def save(self, field, value): return self.req('/api/script', {'field': field, 'value': value, 'column': '写稿'})[1]
    def data(self): return self.req('/api/data', None, page=False)[1]['board']
    def text(self): return self.doc.read_text(encoding='utf-8')
    def body(self): return X.read(self.job)['fields']['口播正文']
    def listen(self): W.home(self.job).mkdir(parents=True, exist_ok=True); (W.home(self.job) / '服务.json').write_text(json.dumps({'pid': os.getpid(), 'port': self.port, 'url': self.base + '/', 'dir': str(self.job.dir)}))

    def rewrite(self, nid, spec, *more):
        f = self.tmp / '改写.json'; f.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8'); return self.cli('--rewrite', nid, '--json', str(f), *more)

    def changes(self, nid): return X.note(X.load(self.job), nid)['proposal']['changes']


class WritingTests(Base):
    """片子已经在做了（有 job.json、有项目文案）。"""

    def test_read_and_write_each_section(self):
        w = self.data()['writing']; self.assertTrue(w['ok'] and w['exists']); self.assertEqual(w['rel'], '03_成片/P1.md')
        self.assertEqual((w['fields']['标题'], w['fields']['封面文字']), ('旧的标题', '为什么外地\n灯光设计'))
        self.assertIn('### 开头', w['fields']['口播正文']); self.assertIn('> 这里停一下', w['fields']['口播正文'])        # 正文原样：带小标题和备注
        self.assertEqual(w['count'], len('我们做过两个外地的灯光设计，一个武汉的，一个广东的。每个项目去了五次左右现场。'))    # 小标题、备注不算口播
        self.assertEqual(w['seconds'], round(w['count'] * 60 / X.RATE))
        self.assertTrue(self.save('标题', '新的  标题\n')['ok']); t = self.text()
        self.assertIn('## 标题\n\n新的 标题\n', t); self.assertEqual(t.replace('新的 标题', '旧的标题'), DOC)              # 只动了标题这一节
        self.assertTrue(self.save('封面文字', '一行\n\n两行\n')['ok']); self.assertIn('## 封面文字\n\n一行\n两行\n', self.text())
        self.assertTrue(self.save('口播正文', BODY)['ok']); self.assertTrue(self.text().endswith('## 口播正文\n\n' + BODY + '\n')); self.assertIn('## 标题\n\n新的 标题\n', self.text())
        self.assertEqual([(b['k'], b['kind']) for b in X.blocks(self.body())], [(1, '小标题'), (2, '段'), (3, '备注'), (4, '段'), (5, '段')])
        bad = self.save('口播正文', '第一段\n\n## 又一节\n\n第二段'); self.assertFalse(bad['ok']); self.assertIn('## 开头', bad['error']); self.assertEqual(self.body(), BODY)
        self.assertFalse(self.save('简介', 'x')['ok'])
        code, out = self.req('/api/script', {'field': '标题', 'value': '冒充', 'column': '写稿'}, page=False); self.assertEqual(code, 403); self.assertNotIn('冒充', self.text())
        # 片子已经在做了：改的字记一条，交给 AI 跟着改用到的地方（栏目是 写稿，不再记到 口播剪辑 栏）
        d = self.data(); self.assertEqual({e['column'] for e in d['edits']}, {'写稿'}); self.assertEqual(d['send']['cols']['写稿']['unsent'], 3)
        self.assertEqual(d['ucols']['写稿']['counts']['待发送'], 3); self.assertNotIn('文案', d['send']['cols'])
        r = self.cli('--list'); self.assertIn('[改1] 写稿·标题', r.stdout); self.assertIn('【旧→新】的标题', r.stdout)

    def test_old_style_script_is_read_only_with_reason(self):
        self.doc.write_text('整份没有小标题的老文案。\n第二段。\n', encoding='utf-8')
        w = self.data()['writing']; self.assertIn('老写法', w['why']['口播正文']); out = self.save('口播正文', '改'); self.assertFalse(out['ok']); self.assertIn('老写法', out['error'])
        self.assertEqual(self.text(), '整份没有小标题的老文案。\n第二段。\n')

    def test_note_send_and_wait_prints_context(self):
        self.save('口播正文', BODY); B.edit(self.job, lambda d: d['edits'].clear())
        code, out = self.req('/api/draft/note', {'quote': '讲打胶', 'para': 4, 'field': '口播正文'}, page=False); self.assertEqual(code, 403)       # 提意见也只收页面的
        nid = self.ok('/api/draft/note', {'quote': '讲打胶', 'para': 4, 'field': '口播正文'})['id']; self.assertEqual(nid, '稿1')
        self.assertEqual(self.data()['send']['pending'], 0)                                                   # 还没写字：不算一件事
        for t in ('太书面', '太书面了，口语一点'): self.ok('/api/draft/text', {'id': nid, 'text': t})                # 边写边存：同一条
        all_ = self.ok('/api/draft/note', {'text': '整体再短一点'})['id']; self.assertEqual(all_, '稿2')
        d = self.data(); self.assertEqual(d['send']['ids'], {'稿1': 'unsent', '稿2': 'unsent'}); self.assertEqual(d['ucols']['写稿']['state'], '待发送')
        self.assertEqual([n['ustate'] for n in d['writing']['notes']], ['待发送', '待发送']); self.assertEqual(d['settle']['写稿']['state'], 'no'); self.assertIn('提交 AI', d['settle']['写稿']['why'])
        self.assertEqual(self.req('/api/send', {'column': '写稿'}, page=False)[0], 403)
        self.listen(); self.assertTrue(self.ok('/api/send', {'column': '写稿'})['n'] == 2)
        r = self.cli('--wait', '--wait-timeout', '20'); o = r.stdout; self.assertEqual(r.returncode, 0, r.stderr)
        for need in ('栏目：文案，2 件', str(self.doc), '标题：旧的标题', '口播正文 26 字', '5 段', '【文案栏的规矩】', '先用 Read 把文案全文读一遍', '不许直接改文案文件', '不许编数字和案例',
                     '[稿1] 选中了第 4 段里的字“讲打胶”｜待处理｜', '太书面了，口语一点', '前一段（第 3 段）：> 这里停一下', '所在段（第 4 段）：第二段，讲打胶。', '后一段（第 5 段）：第三段，讲开关对齐。',
                     '[稿2] 针对全文｜待处理｜', '整体再短一点', '【选中的字只是定位，不是修改范围】', '别处同类的地方', '调整段落顺序', '  第 1 段（小标题）：### 开头', '  第 3 段（备注）：> 这里停一下', '  第 5 段：第三段，讲开关对齐。', '--rewrite 稿N --json 改写.json', '--status 问你'):
            self.assertIn(need, o)
        self.assertNotIn('审片台其余栏目', o)                                                                   # 只打印这一栏的
        d = self.data(); self.assertEqual(d['send']['ids'], {'稿1': 'doing', '稿2': 'doing'}); self.assertEqual([n['ustate'] for n in d['writing']['notes']], ['AI 在改', 'AI 在改'])
        self.ok('/api/draft/delete', {'id': '稿2'}); self.assertEqual(list(self.data()['send']['ids']), ['稿1'])

    def test_rewrite_only_lands_when_user_accepts(self):
        self.save('口播正文', BODY); B.edit(self.job, lambda d: d['edits'].clear()); nid = self.ok('/api/draft/note', {'quote': '讲打胶', 'para': 4, 'text': '口语一点'})['id']
        self.ok('/api/send', {'column': '写稿'}); before = self.text()
        r = self.rewrite(nid, {'note': '我理解你要口语一点，改了两处', 'changes': [{'para': 4, 'new': '第二个，说说打胶。'}, {'para': 5, 'new': '第三个，开关齐不齐。'}, {'field': '标题', 'new': '新标题'}]})
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('3 处', r.stdout); self.assertIn('采纳的才写进文案文件', r.stdout)
        self.assertEqual(self.text(), before)                                                                  # AI 交了建议：文案文件一字没动
        cs = self.changes(nid); self.assertEqual([(c['i'], c['field'], c['op'], c['k'], c['state']) for c in cs], [(0, '标题', '改', 0, '待定'), (1, '口播正文', '改', 4, '待定'), (2, '口播正文', '改', 5, '待定')])
        self.assertEqual((cs[1]['old'], cs[1]['new']), ('第二段，讲打胶。', '第二个，说说打胶。'))
        d = self.data(); self.assertEqual(d['writing']['notes'][0]['ustate'], '待你看'); self.assertEqual(d['ucols']['写稿']['counts']['待你看'], 1); self.assertNotIn('稿1', d['send']['ids'])
        self.assertIn('3 处未处理', d['settle']['写稿']['why'])
        # 命令行、curl 不能替用户采纳
        self.assertEqual(self.req('/api/draft/decide', {'id': nid, 'which': 'all', 'accept': True}, page=False)[0], 403); self.assertEqual(self.text(), before)
        for args in (['--accept', nid], ['--reply', nid, '--status', '已改', '--note', '用户说采纳']): self.assertNotEqual(self.cli(*args).returncode, 0)
        self.assertEqual(self.text(), before); self.assertTrue(all(c['state'] == '待定' for c in self.changes(nid)))
        # 用户逐处点：采纳一处、不要一处
        out = self.ok('/api/draft/decide', {'id': nid, 'which': [1], 'accept': True}); self.assertEqual(out['done'], [1])
        self.assertEqual(self.body(), BODY.replace('第二段，讲打胶。', '第二个，说说打胶。')); self.assertIn('旧的标题', self.text())
        self.ok('/api/draft/decide', {'id': nid, 'which': [2], 'accept': False}); self.assertIn('第三段，讲开关对齐。', self.body())
        self.assertEqual([c['state'] for c in self.changes(nid)], ['待定', '采纳', '不要']); self.assertFalse(self.req('/api/draft/decide', {'id': nid, 'which': [1], 'accept': True})[1]['ok'])
        # 采纳后再手改；AI 看到的原文对不上了的那处：不硬改，说清楚
        self.save('口播正文', self.body().replace('第二个，说说打胶。', '第二个，说说打胶，这个最常见。')); self.assertIn('这个最常见', self.body())
        self.ok('/api/draft/answer', {'id': nid, 'text': '第三段还是改一下，短一点'}); self.assertEqual(self.data()['send']['ids'][nid], 'unsent')
        self.ok('/api/send', {'column': '写稿'}); self.assertEqual(self.rewrite(nid, {'note': '第三段改短了', 'changes': [{'para': 5, 'new': '第三，开关。'}]}).returncode, 0)
        self.assertEqual(len(self.changes(nid)), 1)                                                            # 新建议顶掉旧的（没点的那处标题也不留）
        self.save('口播正文', self.body().replace('第三段，讲开关对齐。', '第三段，讲开关对齐，我先自己改了。'))
        code, out = self.req('/api/draft/decide', {'id': nid, 'which': 'all', 'accept': True}); self.assertFalse(out['ok']); self.assertIn('对不上', out['error'])
        self.assertIn('我先自己改了', self.body()); self.assertEqual(self.changes(nid)[0]['state'], '待定')

    def test_rewrite_add_delete_and_bad_input(self):
        self.save('口播正文', BODY); nid = self.ok('/api/draft/note', {'text': '调一下结构'})['id']
        for spec, word in (({'changes': [{'para': 4, 'new': 'x'}]}, '要带说明'), ({'note': 'n', 'changes': [{'para': 9, 'new': 'x'}]}, '现在有 5 段'), ({'note': 'n', 'changes': [{'after': 6, 'new': 'x'}]}, '现在有 5 段'),
                           ({'note': 'n', 'changes': [{'para': 4, 'new': '第二段，讲打胶。'}]}, '没有要改的地方'), ({'note': 'n', 'changes': [{'para': 4, 'new': 'a'}, {'para': 4, 'new': 'b'}]}, '写了两次'),
                           ({'note': 'n', 'changes': [{'para': 4, 'new': '## 标题'}]}, '## 开头'), ({'note': 'n', '全文': '整篇'}, '“全文”要写成')):
            r = self.rewrite(nid, spec); self.assertNotEqual(r.returncode, 0, spec); self.assertIn(word, r.stderr + r.stdout)
        self.assertNotEqual(self.rewrite('稿9', {'note': 'n', 'changes': [{'para': 4, 'new': 'x'}]}).returncode, 0); self.assertIsNone(X.note(X.load(self.job), nid).get('proposal'))
        self.assertEqual(self.rewrite(nid, {'changes': [{'after': 0, 'new': '最前面加的。'}, {'para': 3, 'delete': True}, {'after': 5, 'new': '结尾加一段。\n结尾再加一段。'}]}, '--note', '挪了一下').returncode, 0)
        self.assertEqual([(c['op'], c['k']) for c in self.changes(nid)], [('加', 0), ('删', 3), ('加', 5)])
        self.ok('/api/draft/decide', {'id': nid, 'which': 'all', 'accept': True})
        self.assertEqual(self.body(), '最前面加的。\n\n### 开头\n\n第一段，讲收口。\n\n第二段，讲打胶。\n\n第三段，讲开关对齐。\n\n结尾加一段。\n\n结尾再加一段。')
        self.assertNotEqual(self.cli('--publish', '写稿', '--kind', '改字', '--title', 'x', '--look', 'y').returncode, 0)        # 这一栏不发布项

    def test_ask_back_and_answer(self):
        nid = self.ok('/api/draft/note', {'text': '开头换个说法'})['id']; self.ok('/api/send', {'column': '写稿'})
        r = self.cli('--reply', nid, '--status', '问你', '--note', '开头想更狠一点还是更家常一点？', '--options', 'A 更狠|B 更家常'); self.assertEqual(r.returncode, 0, r.stderr)
        d = self.data(); n = d['writing']['notes'][0]; self.assertEqual((n['status'], n['ustate'], n['opts']), ('问你', '待你看', ['A 更狠', 'B 更家常'])); self.assertIn('提出了 1 个问题', d['settle']['写稿']['why'])
        self.assertFalse(self.req('/api/draft/answer', {'id': nid, 'picked': 'C 都不要'})[1]['ok']); self.assertFalse(self.req('/api/draft/text', {'id': nid, 'text': '改口'})[1]['ok'])
        self.ok('/api/draft/answer', {'id': nid, 'picked': 'B 更家常'}); self.assertEqual(self.data()['send']['ids'], {nid: 'unsent'})
        self.ok('/api/send', {'column': '写稿'}); o = self.cli('--list').stdout
        for need in ('[稿1] 针对全文｜待处理', 'AI：开头想更狠一点还是更家常一点？｜给的选项：A 更狠 | B 更家常', '用户：选了：B 更家常｜选了：B 更家常'): self.assertIn(need, o)
        self.assertEqual(self.cli('--reply', nid, '--note', '这段我看不用改，原来的就很口语').returncode, 0)
        d = self.data(); self.assertEqual(d['writing']['notes'][0]['ustate'], ''); self.assertEqual(d['settle']['写稿']['state'], 'can')        # AI 只说明没改：不挡定稿

    def test_versions_diff_and_restore(self):
        self.save('标题', '第一次改'); self.save('标题', '接着改'); vs = self.data()['writing']['versions']
        self.assertEqual([(v['v'], v['kind']) for v in vs], [(2, '手改'), (1, '原来')])                         # 改之前的样子先留一版；10 分钟内接着改算同一版
        self.assertEqual(X._vtext(self.job, 1), DOC); self.assertIn('接着改', X._vtext(self.job, 2))
        nid = self.ok('/api/draft/note', {'text': '改'})['id']; self.rewrite(nid, {'note': 'n', 'changes': [{'para': 2, 'new': '我们做过两个外地项目。'}]})
        self.assertEqual(len(X.load(self.job)['versions']), 2); self.ok('/api/draft/decide', {'id': nid, 'which': 'all', 'accept': True})
        vs = self.data()['writing']['versions']; self.assertEqual((vs[0]['v'], vs[0]['kind']), (3, '采纳')); self.assertIn('稿1', vs[0]['why'])
        d = self.req('/api/draft/diff?v=3', None, page=False)[1]; self.assertEqual((d['v'], d['prev'], d['fields']), (3, 2, []))
        self.assertEqual([(c['op'], c['k'], c['new']) for c in d['body']], [('改', 2, '我们做过两个外地项目。')])
        d = self.req('/api/draft/diff?v=2', None, page=False)[1]; self.assertEqual(d['fields'], [{'field': '标题', 'old': '旧的标题', 'new': '接着改'}]); self.assertEqual(d['body'], [])
        self.doc.write_text(self.text().replace('接着改', '在别处改的'), encoding='utf-8')                         # 在页面以外改过：下次动之前先留一版
        self.assertEqual(self.req('/api/draft/restore', {'v': 1}, page=False)[0], 403)
        out = self.ok('/api/draft/restore', {'v': 1}); self.assertEqual(self.text(), DOC)
        vs = X.load(self.job)['versions']; self.assertEqual([(v['v'], v['kind']) for v in vs[-2:]], [(4, '原来'), (5, '退回')]); self.assertEqual(out['v'], 5); self.assertIn('第 1 版', vs[-1]['why'])
        self.assertIn('在别处改的', X._vtext(self.job, 4)); self.assertFalse(self.req('/api/draft/restore', {'v': 1})[1]['ok'])                 # 已经是这一版
        self.ok('/api/draft/restore', {'v': 3}); self.assertIn('我们做过两个外地项目。', self.body()); self.assertEqual(len(X.load(self.job)['versions']), 6)   # 退回不丢东西：还能退回去

    def test_settle_later_tells_ai_to_follow_not_redo(self):
        self.listen(); self.assertEqual(self.data()['settle']['写稿']['state'], 'can'); self.ok('/api/settle', {'column': '写稿', 'on': True})
        o = self.cli('--wait', '--wait-timeout', '20').stdout; self.assertIn('【文案定稿了】', o); self.assertIn('这个项目已经在做了', o); self.assertNotIn('先开项', o)
        self.assertIsNone(self.data()['writing']['kick']); self.assertFalse(self.req('/api/draft/kickoff', {})[1]['ok'])                     # 片子已经在做了：没有“开工”这一步

    def test_page_has_writing_column(self):
        page = W.PAGE.read_text(encoding='utf-8')
        for need in ('<div class="pane" data-col="写稿"><div class="colhead"><div class="ch-name"><h2>文稿</h2>', 'data-main="写稿"', "const LABEL={'写稿':'文稿','文案':'口播精剪'", 'id="wbody" contenteditable="plaintext-only"',
                     '对全文提意见', '全部采纳', '✓ 已解决', 'class="wrow"', 'function wLay()', 'id="wkick"', "'/api/draft/kickoff'", '>开始制作</button>', 'class="btn go wok">采纳', 'class="btn wno">不要', '看改了哪里', '退回到这一版', "column:'写稿'", "'/api/draft/decide'", '分成标题和正文',
                     "setCol('写稿')", '只读：请在文稿栏修改', '原稿（录之前的定稿）', "return D.koubo?.ready?'文案':'写稿'"):
            self.assertIn(need, page)
        self.assertLess(page.index('data-col="写稿"'), page.index('data-col="文案"'))                              # 排在最前面
        for gone in ('data-sf', "'/api/script',{field:k,value:t.value}"): self.assertNotIn(gone, page)             # 口播剪辑栏里不再有第二处改文案的地方
        self.assertEqual(B.COLUMNS[0], '写稿'); self.assertEqual((B.LABEL['写稿'], B.LABEL['文案']), ('文案', '口播剪辑'))
        d = self.data(); self.assertEqual(d['columns'][0], '写稿'); self.assertFalse(d['ucols']['写稿']['off'])
        self.assertTrue(d['ucols']['文案']['off'])                                                                 # 没有剪口播工程：口播剪辑栏还没到（字在文案栏改）


class EarlyTests(Base):
    """片子还很早：只有项目文件夹（项目.json、01_原始素材），没有文案、没有口播、没有 job.json。"""
    early = True

    def test_only_writing_column_and_start_from_blank(self):
        self.assertTrue(W.lite(self.job)); self.assertEqual(self.job.dir, self.pd / '02_制作' / '自动剪辑'); self.assertFalse(self.doc.exists())
        full = self.req('/api/data', None, page=False)[1]; d = full['board']; w = d['writing']
        self.assertTrue(full['lite']); self.assertEqual({c for c, u in d['ucols'].items() if not u['off']}, {'写稿'})          # 只有文案栏能用，其余“还没到”
        self.assertTrue(w['ok'] and w['early']); self.assertFalse(w['exists']); self.assertEqual(w['fields'], {'标题': '', '封面文字': '', '口播正文': ''})
        self.assertIn('标题和口播正文完成后方可定稿', d['settle']['写稿']['why'])
        self.assertTrue(self.save('标题', '只有一个题目')['ok'])                                                    # 从空白开始：一动笔就建文件（标准写法）
        self.assertEqual(self.text(), f'# {self.pd.name}\n\n## 标题\n\n只有一个题目\n\n## 封面文字\n\n## 口播正文\n')
        self.assertTrue(self.save('口播正文', '第一段。\n\n第二段。')['ok']); self.assertTrue(self.save('封面文字', '')['ok'])          # 封面文字可以先空着
        d = self.data(); self.assertEqual(d['edits'], []); self.assertEqual(d['send']['pending'], 0)                  # 还在写稿：手改不算要交给 AI 的事
        self.assertEqual((d['writing']['count'], d['settle']['写稿']['state']), (8, 'can'))

    def test_paste_splits_title_and_body(self):
        out = self.ok('/api/draft/import', {'text': '装修最后那点细节\n\n第一段，讲收口。\n第二段，讲打胶。\n'}); self.assertEqual((out['title'], out['paras']), ('装修最后那点细节', 2))
        f = X.read(self.job)['fields']; self.assertEqual((f['标题'], f['口播正文']), ('装修最后那点细节', '第一段，讲收口。\n\n第二段，讲打胶。'))
        again = self.req('/api/draft/import', {'text': '又贴一遍\n\n正文'})[1]; self.assertFalse(again['ok']); self.assertIn('已经有正文了', again['error'])
        self.assertEqual(X.split_pasted('第一句就很长很长，是一句完整的话。\n第二句。'), ('', [], '第一句就很长很长，是一句完整的话。\n\n第二句。'))   # 第一行像一句话：不当标题
        self.assertEqual(X.split_pasted('## 标题\n\n题目\n\n## 封面文字\n\n甲\n乙\n\n## 口播正文\n\n### 开头\n正文。'), ('题目', ['甲', '乙'], '### 开头\n正文。'))
        self.assertEqual(self.req('/api/draft/import', {'text': 'x\n\ny'}, page=False)[0], 403)

    def test_whole_draft_pick_in_any_order(self):
        self.save('标题', '题目'); nid = self.ok('/api/draft/note', {'text': '按题目先写一版'})['id']; self.ok('/api/send', {'column': '写稿'})
        r = self.rewrite(nid, {'note': '先写了一版', '全文': {'标题': '题目', '封面文字': ['甲', '乙'], '口播正文': '第一段。\n\n### 中间\n\n第二段。\n\n第三段。'}})
        self.assertEqual(r.returncode, 0, r.stderr); cs = self.changes(nid)
        self.assertEqual([(c['field'], c['op']) for c in cs], [('封面文字', '改')] + [('口播正文', '加')] * 4)             # 标题没变：不列
        self.assertEqual(self.body(), ''); self.ok('/api/draft/decide', {'id': nid, 'which': [3], 'accept': True}); self.assertEqual(self.body(), '第二段。')   # 逐段挑，先挑后面的
        self.ok('/api/draft/decide', {'id': nid, 'which': [1], 'accept': True}); self.assertEqual(self.body(), '第一段。\n\n第二段。')
        self.ok('/api/draft/decide', {'id': nid, 'which': 'all', 'accept': True}); self.assertEqual(self.body(), '第一段。\n\n### 中间\n\n第二段。\n\n第三段。')    # 剩下的整篇采纳：顺序是对的
        self.assertEqual(X.read(self.job)['fields']['封面文字'], '甲\n乙'); self.assertEqual(self.data()['writing']['notes'][0]['ustate'], '已定')
        # 已经有几段了再整篇重写：逐段比出 改／加／删
        self.ok('/api/draft/answer', {'id': nid, 'text': '中间那段删了，结尾加一句'}); self.rewrite(nid, {'note': '照改', '全文': {'口播正文': '第一段。\n\n第二段，改了。\n\n第三段。\n\n结尾。'}})
        self.assertEqual([(c['op'], c['k']) for c in self.changes(nid)], [('删', 2), ('改', 3), ('加', 4)])
        self.ok('/api/draft/decide', {'id': nid, 'which': 'all', 'accept': True}); self.assertEqual(self.body(), '第一段。\n\n第二段，改了。\n\n第三段。\n\n结尾。')

    def test_settle_goes_to_wait_and_expires_when_text_changes(self):
        self.listen(); self.save('标题', '题目'); self.assertFalse(self.req('/api/settle', {'column': '写稿', 'on': True})[1]['ok'])       # 还没正文：不能定稿
        self.save('口播正文', '第一段。\n\n第二段。')
        self.assertEqual(self.req('/api/settle', {'column': '写稿', 'on': True}, page=False)[0], 403); self.assertNotIn('写稿', B.load(self.job).get('settled') or {})   # 命令行不能替用户定稿
        self.assertEqual(self.cli('--reply', '定稿', '--note', '收到').returncode, 1)
        self.ok('/api/settle', {'column': '写稿', 'on': True}); d = self.data()
        self.assertEqual((d['settle']['写稿']['state'], d['ucols']['写稿']['state'], d['send']['ids']), ('ok', '已定', {'定稿': 'sent'}))       # 4 个状态不新增：栏目是“已定”，这件事算已交给 AI
        r = self.cli('--wait', '--wait-timeout', '20'); o = r.stdout; self.assertEqual(r.returncode, 0, r.stderr)
        for need in ('栏目：文案，1 件', '【文案定稿了】', str(self.doc), '标题：题目', '【先开项，不要开始制作】', 'project.py rename', '登记进 项目.json', '用户点“开始制作”之前不要剪、不要初剪', '--reply 定稿'): self.assertIn(need, o)
        self.assertNotIn('按初稿模式初剪', o)                                                               # 2026-11-24 用户定：定稿后先开项、存资料，点了“开工”才开工
        self.assertEqual(self.data()['send']['ids'], {'定稿': 'doing'})
        self.assertEqual(self.cli('--reply', '定稿', '--note', '项目开好了，素材放进 01_原始素材').returncode, 0); d = self.data()
        self.assertEqual((d['send']['pending'], d['settle']['写稿']['state'], d['settle']['写稿']['note']), (0, 'ok', '项目开好了，素材放进 01_原始素材'))
        k = d['writing']['kick']; self.assertEqual((k['at'], k['videos'], k['opened']), (None, 0, '项目开好了，素材放进 01_原始素材'))
        # 开工：只有用户在页面上点；带上他写的素材路径；--wait 打印后 AI 才按初稿模式做
        self.assertEqual(self.req('/api/draft/kickoff', {'materials': 'x'}, page=False)[0], 403); self.assertNotEqual(self.cli('--reply', '开工', '--note', '开始').returncode, 0)
        self.ok('/api/draft/kickoff', {'materials': '/Volumes/work/素材/工地实拍\n收口那段用上次的特写'}); d = self.data()
        self.assertEqual(d['send']['ids'], {'开工': 'sent'}); self.assertTrue(d['writing']['kick']['at']); self.assertEqual(d['ucols']['写稿']['state'], '已定')
        o = self.cli('--wait', '--wait-timeout', '20').stdout
        for need in ('【用户点了“开始制作”】', '/Volumes/work/素材/工地实拍⏎收口那段用上次的特写', '01_原始素材 里现在没有视频文件', '机器配音占位', '整理进 01_原始素材', '按初稿模式初剪', 'references/初稿模式.md', '--reply 开工'): self.assertIn(need, o)
        self.assertEqual(self.cli('--reply', '开工', '--note', '收到，开始整理素材、出初稿').returncode, 0); d = self.data()
        self.assertEqual((d['send']['pending'], d['writing']['kick']['note']), (0, '收到，开始整理素材、出初稿'))
        self.save('口播正文', '第一段，改了。\n\n第二段。'); d = self.data()                                             # 定稿后文案再改：定稿自动失效
        self.assertEqual((d['settle']['写稿']['state'], d['ucols']['写稿']['state'], d['send']['pending']), ('can', '', 0)); self.assertIsNone(d['writing']['kick'])   # 开工那一条也跟着收起
        self.assertFalse(self.req('/api/draft/kickoff', {})[1]['ok'])                                          # 没定稿不能开工
        (self.pd / '01_原始素材' / '口播').mkdir(); (self.pd / '01_原始素材' / '口播' / 'A001.mp4').write_bytes(b'x')
        self.ok('/api/settle', {'column': '写稿', 'on': True}); o = self.cli('--wait', '--wait-timeout', '20').stdout          # 重新点：又是一件新的事
        self.assertIn('【文案定稿了】', o); self.assertIsNone(self.data()['writing']['kick']['at'])              # 重新定稿：要重新点开工
        self.ok('/api/draft/kickoff', {}); o = self.cli('--wait', '--wait-timeout', '20').stdout
        self.assertIn('有 1 个视频文件（01_原始素材/口播/A001.mp4）', o); self.assertIn('（没写）', o); self.assertNotIn('机器配音占位', o)
        self.ok('/api/settle', {'column': '写稿', 'on': False}); self.assertEqual(self.data()['send']['pending'], 0)          # 取消：这件事也撤了

    def test_command_line_opens_project_folder(self):
        env = {**os.environ, 'THINKCUT_NO_OPEN': '1'}; film = self.pd / '02_制作' / '自动剪辑'
        r = subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.pd), '--column', '写稿'], capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
        try:
            self.assertEqual(r.returncode, 0, r.stderr)
            for need in ('只有文稿栏能用', '#%E5%86%99%E7%A8%BF', '--rewrite 稿N --json', f'workbench {film} --wait'): self.assertIn(need, r.stdout)
            self.assertNotIn('口播剪辑', r.stdout)
        finally:
            subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.pd), '--stop'], capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)


if __name__ == '__main__':
    unittest.main()
