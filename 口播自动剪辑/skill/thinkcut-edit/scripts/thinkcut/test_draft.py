# -*- coding: utf-8 -*-
"""初稿模式（2026-11-22 用户定“先做出来再改”）的离线测试：不连达芬奇、不真生图、不调豆包。run_tests.sh 会跑。
① 以前等用户确认才能渲染的（草图、样张、标签位置）现在不拦，只列成“初稿·未确认”；硬问题照拦。
② 只留的最终闸：整片导出要用户在审片台页面点过审核线“这一栏定了”，之后内容一变就不算；命令行没有替用户点的入口；转述的原话不算。
③ 占位：机器配音（没有人脸：分镜不留露脸段、不放小圆圈、不用修音）；占位图（分镜写画面描述 → 生成 → 排进去 → 审片台标出来）。
④ 审片台卡片上的“初稿”“占位”“我是这么假设的”只是小记号，4 个状态不变。⑤ status 报五项初稿做到哪。"""
import contextlib, io, json, os, shutil, stat, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinkcut import anim as A, beats, board as B, draft, guard, overlay, resolve, status, workbench as W      # noqa: E402
from thinkcut.common import Job, rj, wj                                                              # noqa: E402
from thinkcut.test_board import img, project                                                         # noqa: E402
from thinkcut.test_workbench import FakeDV                                                           # noqa: E402
os.environ['THINKCUT_NO_OPEN'] = '1'

S = Path(__file__).resolve().parents[1] / 'thinkcut_edit.py'
PAGE = Path(__file__).resolve().parents[2] / 'assets' / '审片工作台' / 'index.html'


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as o: r = fn(*a, **k)
    return r, o.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.pd, self.job, self.hf = project(self.tmp); self.d = self.job.dir
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)


class RenderGateTests(Base):
    def test_confirmations_no_longer_block_rendering_but_are_listed(self):
        # S2 讲“关系”：以前没选草图连样张都不让出、没点样张“可以”不让整段出片
        self.assertTrue(any('草图' in x for x in A.gate(self.job, self.hf, ['S2'], False)))            # 检查本身没变：还是会列出来
        self.assertTrue(any('没有样张确认' in x for x in A.gate(self.job, self.hf, ['S1'], True)))
        _, out = quiet(A.require, self.job, self.hf, ['S1', 'S2'], True)                              # 现在不停
        self.assertIn('【初稿·未确认】', out); self.assertIn('S2 讲关系', out); self.assertIn('S1 没有样张确认', out)
        hard, soft = A.gate(self.job, self.hf, ['S1', 'S2'], True, split=True)
        self.assertEqual(hard, []); self.assertEqual(len(soft), 3)

    def test_hard_problems_still_block(self):
        spec = rj(self.d / '分镜规格.json'); spec['shots'][0].pop('口播要点'); wj(self.d / '分镜规格.json', spec)
        with self.assertRaisesRegex(SystemExit, '没写口播要点'): A.require(self.job, self.hf, ['S1'], False)
        hard, soft = A.gate(self.job, self.hf, ['S1'], False, split=True)
        self.assertEqual(len(hard), 1); self.assertEqual(soft, [])

    def test_unsure_label_position_is_soft(self):
        wj(self.hf / '标签定位.json', {'灯': {'unsure': True, 'why': '两处都能放'}})
        hard, soft = A.gate(self.job, self.hf, ['S1'], True, split=True)
        self.assertEqual(hard, []); self.assertTrue(any('拿不准' in x for x in soft))


class ExportGateTests(Base):
    def setUp(self):
        super().setUp(); self.job.cfg.update(review_timeline='v1_卡点审核', main_timeline='v1'); self.job.save()
        self.srv = W.make_server(self.job, 0, FakeDV()); self.base = f'http://127.0.0.1:{self.srv.server_address[1]}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def tearDown(self): self.srv.shutdown(); self.srv.server_close(); super().tearDown()

    def req(self, p, body, page=True):
        h = {'Content-Type': 'application/json', **({'Origin': self.base, 'Sec-Fetch-Site': 'same-origin', 'X-Shenpian-Token': W.TOKEN} if page else {})}
        q = urllib.request.Request(self.base + p, json.dumps(body).encode(), h)
        try:
            with urllib.request.urlopen(q) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def test_export_needs_the_page_click_and_nothing_else_counts(self):
        with self.assertRaisesRegex(SystemExit, '(?s)还没在审片台点审核线“这一栏定了”.*AI 不能替用户点'): draft.export_gate(self.job)
        with self.assertRaisesRegex(SystemExit, '这一栏定了'): resolve.export(self.job, user_ok='用户说：可以了导出吧')        # 转述的原话不算
        code, _ = self.req('/api/settle', {'column': '审核线', 'on': True}, page=False)                                  # 不是页面发的（没有口令）：不收
        self.assertNotEqual(code, 200)
        with self.assertRaisesRegex(SystemExit, '这一栏定了'): draft.export_gate(self.job)
        code, r = self.req('/api/settle', {'column': '审核线', 'on': True}); self.assertEqual((code, r.get('ok')), (200, True))   # 用户在页面上点
        got = draft.export_gate(self.job)
        self.assertEqual(got['column'], '审核线'); self.assertIn('这一栏定了', got['via']); self.assertTrue(got['at'])

    def test_any_change_after_the_click_voids_it(self):
        self.req('/api/settle', {'column': '审核线', 'on': True}); draft.export_gate(self.job)
        with self.assertRaisesRegex(SystemExit, '不是审片台上用户定的那条审核线'): draft.export_gate(self.job, '别的线')
        self.job.cfg['review_timeline'] = 'v1_卡点审核_第2版'; self.job.save()                                            # AI 又建了新一版审核线
        with self.assertRaisesRegex(SystemExit, '之后审核线这一栏的内容变了'): draft.export_gate(Job(self.d))
        self.req('/api/settle', {'column': '审核线', 'on': True}); draft.export_gate(Job(self.d))
        self.req('/api/settle', {'column': '审核线', 'on': False})                                                       # 用户取消
        with self.assertRaisesRegex(SystemExit, '这一栏定了'): draft.export_gate(Job(self.d))

    def test_command_line_has_no_way_to_settle(self):
        text = S.read_text(encoding='utf-8')
        self.assertNotIn("'--settle'", text); self.assertNotIn('--settle', subprocess.run([sys.executable, '-B', str(S), 'workbench', '-h'], capture_output=True, text=True).stdout)
        r = subprocess.run([sys.executable, '-B', str(S), 'export', str(self.d), '--user-ok', '可以了'], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0); self.assertIn('这一栏定了', r.stderr + r.stdout)

    def test_trial_export_of_a_few_seconds_is_not_gated(self):
        self.addCleanup(setattr, resolve, 'project', resolve.project)
        class Reached(Exception): pass
        def stop(*a, **k): raise Reached()
        resolve.project = stop
        with self.assertRaises(Reached): quiet(resolve.export, self.job, seconds=5)

    def test_export_record_says_where_the_confirmation_came_from(self):
        self.req('/api/settle', {'column': '审核线', 'on': True})
        src = Path(resolve.__file__).read_text(encoding='utf-8')
        self.assertIn("'via': settled['via']", src); self.assertIn('export_gate(job, name)', src)


class MachineVoiceTests(Base):
    TOK = [{'c': c, 't0': round(i * 0.3, 3), 't1': round(i * 0.3 + 0.28, 3)} for i, c in enumerate('先看效果再说三种距离最后给结论好')]

    def spec(self, shots): return dict(anchors={}, shots=shots, sounds={}, gain={}, sfx=[], pip={'min_seconds': 1})

    def test_no_face_means_no_face_shots_and_no_pip(self):
        gap = self.spec([{'id': 'B01', 'a': 1.0, 'b': 3.0}])
        self.assertEqual([s['kind'] for s in beats.compile_spec(gap, self.TOK, 5.0)['shots']], ['F', 'B', 'F'])          # 真口播：空着的是露脸段
        with self.assertRaisesRegex(SystemExit, '(?s)机器配音占位.*0.00–1.00 秒.*3.00–5.00 秒.*占位图'): beats.compile_spec(gap, self.TOK, 5.0, no_face=True)
        full = self.spec([{'id': 'B01', 'a': 0.0, 'b': 3.0}, {'id': 'R02', 'a': 3.0, 'b': 5.0}])
        self.assertTrue(all(s.get('pip') for s in beats.compile_spec(full, self.TOK, 5.0, pip_min=1)['shots']))          # 真口播：连续画面放小圆圈
        out = beats.compile_spec(full, self.TOK, 5.0, pip_min=1, no_face=True)
        self.assertEqual([s['kind'] for s in out['shots']], ['B', 'B']); self.assertFalse(any(s.get('pip') for s in out['shots']))

    def test_job_carries_the_placeholder_and_checks_follow(self):
        self.assertIsNone(draft.machine_voice(self.job)); self.assertEqual(draft.placeholders(self.job), [])
        miss, _ = guard.polish_missing(self.job); self.assertIn('口播还没修音', miss)
        self.job.cfg['placeholder'] = {'kind': '机器配音', 'record': '/x.json'}
        self.assertTrue(draft.machine_voice(self.job))
        miss, note = guard.polish_missing(self.job); self.assertIsNone(miss); self.assertIn('机器配音占位，不用修音', note)
        ps = draft.placeholders(self.job); self.assertEqual([p['kind'] for p in ps], ['机器配音']); self.assertIn('你录了真口播', ps[0]['text'])

    def test_voice_loudness_check_does_not_block_a_machine_voice(self):
        (self.d / '合成').mkdir(exist_ok=True); (self.d / '合成' / 'A肉.mov').write_bytes(b'x'); wj(self.d / '合成' / '音量.json', {'voice_lufs': -17.6, 'voice_db': 4.8})
        bad, _ = guard.voice(self.job, measure_floor=lambda p: -49.0)
        self.assertEqual(len(bad), 2)                                                              # 真口播：响度偏、底噪高都拦
        self.job.cfg['placeholder'] = {'kind': '机器配音'}
        bad, note = guard.voice(self.job, measure_floor=lambda p: -49.0)
        self.assertEqual(bad, []); self.assertIn('机器配音占位，不按真口播的标准拦', note); self.assertIn('-17.6', note)

    def test_init_reads_the_placeholder_from_the_aroll_project(self):
        from thinkcut import aroll
        a = self.tmp / '机器配音占位_口播_AI剪辑'; (a / '审核').mkdir(parents=True); (a / '交付').mkdir(); (a / '交付' / 'v1.srt').write_text('1\n')
        wj(a / '审核' / 'latest_delivery.json', {'status': 'complete', 'project': 'P1', 'timeline': 'v1_主线', 'frames': 600, 'output': str(a / '交付')})
        rt = {'source': '/x.mov', 'resolution': [2160, 3840], 'fps': [30000, 1001]}
        wj(a / '审核' / 'project_runtime.json', rt); self.assertNotIn('placeholder', aroll.detect(a))
        wj(a / '审核' / 'project_runtime.json', dict(rt, placeholder={'kind': '机器配音', 'record': '/r.json'}))
        self.assertEqual(aroll.detect(a)['placeholder']['kind'], '机器配音')

    def test_pip_step_just_copies_when_there_is_no_face(self):
        self.job.cfg['placeholder'] = {'kind': '机器配音'}
        wj(self.d / '分镜.json', {'shots': [{'id': 'B01', 'kind': 'B', 'a': 0, 'b': 2}]})
        (self.d / '合成').mkdir(exist_ok=True); (self.d / '合成' / 'B肉.mp4').write_bytes(b'video-bytes')
        quiet(overlay.pip, self.job)                                                              # 没有 A肉 也行
        self.assertEqual((self.d / '合成' / 'B肉_小圆圈.mp4').read_bytes(), b'video-bytes')


class PlaceholderImageTests(Base):
    def setUp(self):
        super().setUp()
        spec = rj(self.d / '分镜规格.json')
        spec['shots'] += [{'id': 'R03', 'a': 6, 'b': 8, '占位图': '筒灯离墙 30 公分，光刚好洗到墙面', '口播要点': ['离墙三十公分最舒服']},
                          {'id': 'R04', 'a': 8, 'b': 11, '占位图': ['离墙 15 公分：尖亮的扇贝光斑', '离墙 60 公分：墙是暗的']}]
        wj(self.d / '分镜规格.json', spec)
        self.fake = self.tmp / 'fake_gen.py'       # 生图替身：照任务单里的 Save to 路径各写一张小图；FAIL_ON 里的镜头不出图
        self.fake.write_text('# -*- coding: utf-8 -*-\nimport os, re, sys\nfrom PIL import Image\ntask = open(sys.argv[2], encoding="utf-8").read()\n'
                             'if os.environ.get("FAIL_ON") and os.environ["FAIL_ON"] in sys.argv[2]: sys.exit(3)\n'
                             'for p in re.findall(r"Save to: (.+)", task): Image.new("RGB", (64, 96), (9, 9, 9)).save(p.strip())\n', encoding='utf-8')
        os.environ['THINKCUT_IMAGE_GEN'] = f'{sys.executable} -B {self.fake}'; self.addCleanup(os.environ.pop, 'THINKCUT_IMAGE_GEN', None)

    def test_spec_descriptions_become_picture_shots(self):
        out = beats.live_fields({'id': 'R04', '占位图': ['甲', '乙']}, {'a': 0, 'b': 3}, lambda x: x)
        self.assertEqual(out['图片'], {'文件': ['实拍/占位图/R04_1.png', '实拍/占位图/R04_2.png'], '铺法': '裁满'}); self.assertEqual(out['占位图'], ['甲', '乙'])
        self.assertEqual(beats.live_fields({'id': 'R03', '占位图': '甲'}, {'a': 0, 'b': 3}, lambda x: x)['图片']['文件'], ['实拍/占位图/R03.png'])
        with self.assertRaisesRegex(SystemExit, '有了真素材就把 占位图 删掉'): beats.live_fields({'id': 'R03', '占位图': '甲', '实拍': 'a.mp4'}, {'a': 0, 'b': 3}, lambda x: x)
        with self.assertRaisesRegex(SystemExit, '要写画面描述'): beats.live_fields({'id': 'R03', '占位图': ['']}, {'a': 0, 'b': 3}, lambda x: x)

    def test_generate_then_skip_then_force(self):
        made, out = quiet(draft.gen_images, self.job)
        self.assertEqual(sorted(made), ['R03', 'R04']); self.assertIn('2 个镜头、3 张', out)
        files = sorted(p.name for p in (self.d / '实拍' / '占位图').glob('*.png')); self.assertEqual(files, ['R03.png', 'R04_1.png', 'R04_2.png'])
        task = (self.d / '实拍' / '占位图' / '_生成记录' / '任务_R04.md').read_text(encoding='utf-8')
        self.assertIn('Never ask a question', task)                                                              # 描述含糊也不许反问（真生图试出来的：只写“15 公分”它会反问、不出图）
        self.assertIn('NO "AI generated" badge', task); self.assertIn('SAME camera position', task)              # 不加角标；一组对比同机位、一次出齐
        self.assertIn('Image 2: 离墙 60 公分：墙是暗的', task); self.assertIn('Portrait orientation', task)
        t3 = (self.d / '实拍' / '占位图' / '_生成记录' / '任务_R03.md').read_text(encoding='utf-8')
        self.assertNotIn('SAME camera position', t3); self.assertIn('- 离墙三十公分最舒服', t3)                      # 口播要点带进任务单当上下文
        made, out = quiet(draft.gen_images, self.job); self.assertEqual(made, []); self.assertIn('不用生成', out)        # 已有的不重出（不重复用额度）
        made, _ = quiet(draft.gen_images, self.job, ['R03'], True); self.assertEqual(made, ['R03'])
        self.assertEqual(len(list((self.d / '实拍' / '占位图').glob('R03_旧*.png'))), 1)                                  # --force：旧图改名留着

    def test_failure_names_the_shot_and_keeps_what_worked(self):
        os.environ['FAIL_ON'] = '任务_R04'; self.addCleanup(os.environ.pop, 'FAIL_ON', None)
        with self.assertRaisesRegex(SystemExit, '(?s)没生成出来：R04 缺 R04_1.png、R04_2.png.*生图失败也算一次额度'): quiet(draft.gen_images, self.job)
        self.assertTrue((self.d / '实拍' / '占位图' / 'R03.png').is_file())
        os.environ.pop('FAIL_ON'); made, _ = quiet(draft.gen_images, self.job); self.assertEqual(made, ['R04'])           # 重跑只补缺的

    def test_placeholders_are_listed_until_replaced_with_real_material(self):
        ps = draft.placeholders(self.job); self.assertEqual([p['kind'] for p in ps], ['占位图']); self.assertEqual(ps[0]['shots'], ['R03', 'R04'])
        self.assertIn('2 个镜头的画面是 AI 生成的占位图', ps[0]['text']); self.assertIn('你有真素材了跟 AI 说一声就换', ps[0]['text'])
        spec = rj(self.d / '分镜规格.json'); r3 = next(s for s in spec['shots'] if s['id'] == 'R03'); r3.pop('占位图'); r3['实拍'] = '实拍/真的.mp4'
        wj(self.d / '分镜规格.json', spec)
        self.assertEqual(draft.placeholders(self.job)[0]['shots'], ['R04'])                                             # 换成真素材：不再算占位

    def test_timeline_strip_and_page_mark_placeholder_shots(self):
        wj(self.d / '分镜.json', {'shots': [{'id': 'R03', 'kind': 'B', 'a': 6, 'b': 8, '图片': {'文件': ['实拍/占位图/R03.png']}, '占位图': ['甲']},
                                          {'id': 'R05', 'kind': 'B', 'a': 8, 'b': 9, '图片': {'文件': ['x.png']}}]})
        self.assertEqual([x['kind'] for x in W.shot_spans(self.job)], ['占位图', '图片'])
        self.assertEqual([p['kind'] for p in W.placeholders_of(self.job)], ['占位图'])
        page = PAGE.read_text(encoding='utf-8')
        for need in ("'占位图':'ph'", '.sh-ph{', 'id="phline"', 'function phLine()', 'D.placeholders'): self.assertIn(need, page)


class BoardDraftTests(Base):
    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(S), 'workbench', str(self.d), *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def test_publish_carries_draft_placeholder_and_assumption(self):
        a = img(self.tmp / '样张.png')
        r = self.cli('--publish', '动画', '--kind', '可以', '--title', 'S2 初稿', '--look', '看远程和实际的关系', '--files', str(a), '--draft', '--placeholder',
                     '--assume', '你没说用哪种画法，我用了左右对比\\n灯带位置按常见做法画')
        self.assertEqual(r.returncode, 0, r.stderr)
        it = B.item(B.load(self.job), '动1'); v = it['versions'][0]
        self.assertEqual((v.get('draft'), v.get('placeholder'), v.get('assume')), (True, True, '你没说用哪种画法，我用了左右对比\n灯带位置按常见做法画'))
        self.assertEqual(B.state(it), ('等你看', False))                                    # 状态照旧：初稿、占位不是状态
        b = img(self.tmp / '样张2.png', (1, 2, 3)); self.cli('--reply', '动1', '--files', str(b), '--note', '换了实拍')
        v2 = B.item(B.load(self.job), '动1')['versions'][-1]
        self.assertFalse(v2.get('draft') or v2.get('placeholder') or v2.get('assume'))      # 标记只跟着那一版：换了新版就没有了

    def test_page_shows_chips_and_assumption_without_a_fifth_state(self):
        page = PAGE.read_text(encoding='utf-8')
        for need in ('const chips=v=>', '>占位</span>', '>初稿</span>', '我是这么假设的：', '${chips(ver)}', '${assumeHtml(ver)}', '.chip{'): self.assertIn(need, page)
        self.assertEqual(page.count('${chips(ver)}'), 2)                                    # 卡片、全部摆开两处都有
        ust = page.split('const UST=')[1].split('}')[0]
        self.assertEqual(ust.count(':'), 4)                                                 # 给用户看的状态还是 4 个


class StatusTests(Base):
    def test_five_items_and_final_gate_are_reported(self):
        self.job.cfg.pop('review_timeline', None)
        rows = {n: ok for n, ok, _ in draft.five(self.job)}
        self.assertEqual(list(rows), ['口播剪辑', '动画和画面', '审核线', '封面', '上传文案'])
        self.assertFalse(rows['审核线'] or rows['封面'] or rows['上传文案'])
        self.job.cfg.update(review_timeline='v1_卡点审核'); B.publish(self.job, '封面', '封面', '看主角', '可以', [img(self.tmp / 'c.png')])
        meta = rj(self.pd / '项目.json'); meta.update(description='简介', tags=['a']); wj(self.pd / '项目.json', meta)
        rows = {n: ok for n, ok, _ in draft.five(self.job)}
        self.assertTrue(rows['审核线'] and rows['封面'] and rows['上传文案'])
        _, out = quiet(draft.report, self.job)
        self.assertIn('【初稿五项】', out); self.assertIn('初稿还差：', out); self.assertIn('最终闸：用户还没在审片台点审核线“这一栏定了”', out)
        B._mark_settled(self.job, '审核线'); _, out = quiet(draft.report, self.job)
        self.assertIn('最终闸：用户已在审片台点了审核线“这一栏定了”，可以整片导出', out)


if __name__ == '__main__':
    unittest.main()
