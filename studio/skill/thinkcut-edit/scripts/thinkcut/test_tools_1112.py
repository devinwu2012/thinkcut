# -*- coding: utf-8 -*-
"""2026-11-12《水电阶段没做好这4样》反馈单剪辑工具部分的离线测试（不连达芬奇）。run_tests.sh 会跑：
新1/⑬ 音效只放重点、画面事件只认当前动画、每分钟上限；新2 改动标记；新10 重跑 beats 不清音效、review 0 音效硬拦；
新11③ 小圆圈任意位置/分段；④ 卡点按上一个卡点之后找＋缩短匹配；⑨ 花字轨/额外轨清单；⑩ 项目内音效。"""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinkcut import beats, overlay, resolve, sfx, check          # noqa: E402
from thinkcut.common import Job, wj, rj                          # noqa: E402


def tokens(text, step=0.3, start=1.0, gaps=()):
    """每个字 0.3 秒；gaps 里的字前面多停 1 秒。"""
    out, t = [], start
    for i, c in enumerate(text):
        if i in gaps: t += 1.0
        out.append({'c': c, 't0': round(t, 3), 't1': round(t + step - 0.02, 3)}); t += step
    return out


def settle(job):
    """替身：用户在审片台点了审核线“这一栏定了”。2026-11-22 用户定“先做出来再改”以后，整片导出只认这条页面确认，不再认 --user-ok 转述的原话；
    真页面走 /api/settle（只收带口令的页面请求），这里直接记同一条记录。"""
    from thinkcut import board
    board._mark_settled(job, '审核线')


TEXT = '开头说一句钢丝刷很重要然后再说一遍钢丝刷最后说光纤设备和网线设备结束'
TOK = tokens(TEXT)
T = {c: i for i, c in enumerate(TEXT)}
at = lambda k: round(1.0 + k * 0.3, 3)          # 第 k 个字的起点


class AnchorTests(unittest.TestCase):
    def spec(self, anchors, **kw):
        return dict(anchors=anchors, shots=[], sounds={}, gain={}, sfx=[], **kw)

    def test_sequential_uses_previous_order_not_seconds(self):
        # 两次“钢丝刷”：第二个卡点在第一个之后找；写错的秒数（换口播后）不再起作用
        prev = {'a': 0.0, 'b': 3.0, 'c': 6.0}
        sp = self.spec({'a': ['开头', 0], 'b': ['钢丝刷', 99], 'c': ['钢丝刷', 99]})
        out = beats.compile_spec(sp, TOK, 30, previous=prev)
        self.assertEqual(out['anchors']['b'], at(TEXT.index('钢丝刷')))
        self.assertEqual(out['anchors']['c'], at(TEXT.index('钢丝刷', TEXT.index('钢丝刷') + 1)))

    def test_seconds_mode_kept_for_old_specs(self):
        sp = self.spec({'b': ['钢丝刷', 4.0]})               # 没有上次的分镜：照老办法按秒数
        self.assertEqual(beats.compile_spec(sp, TOK, 30)['anchors']['b'], at(TEXT.index('钢丝刷', 10)))

    def test_after_named_anchor_and_null(self):
        sp = self.spec({'x': ['再说', None], 'y': ['钢丝刷', 'x'], 'z': ['设备', None, 0, True]})
        out = beats.compile_spec(sp, TOK, 30)
        self.assertEqual(out['anchors']['y'], at(TEXT.index('钢丝刷', TEXT.index('再说'))))
        k = TEXT.index('设备'); self.assertAlmostEqual(out['anchors']['z'], round(at(k + 1) + 0.28, 3), places=3)

    def test_changed_wording_is_shortened_and_reported(self):
        notes = []
        out = beats.compile_spec(self.spec({'f': ['接光纤设备', None]}), TOK, 30, notes=notes)
        self.assertEqual(out['anchors']['f'], at(TEXT.index('光纤设备')))
        self.assertTrue(notes and '缩短成「光纤设备」' in notes[0])

    def test_not_found_and_cycle(self):
        with self.assertRaisesRegex(SystemExit, '找不到'):
            beats.compile_spec(self.spec({'q': ['完全没说过的话呀', None]}), TOK, 30)
        with self.assertRaisesRegex(SystemExit, '绕成了圈'):
            beats.compile_spec(self.spec({'a': ['开头', 'b'], 'b': ['钢丝刷', 'a']}), TOK, 30)

    # —— 2026-11-22 261122 反馈单 新6 ——
    def test_new_anchor_without_after_never_cycles(self):
        # y 没写“从哪之后找”、书写顺序的前一个 x 正写着“在 y 之后找”：以前 y 跟着 x 找 → 绕成了圈；现在跳过 x，从更前面的卡点找
        out = beats.compile_spec(self.spec({'a': ['开头', None], 'x': ['钢丝刷', 'y'], 'y': ['再说', None]}), TOK, 30)
        self.assertEqual(out['anchors']['y'], at(TEXT.index('再说'))); self.assertEqual(out['anchors']['x'], at(TEXT.index('钢丝刷', TEXT.index('再说'))))

    def test_new_anchor_searches_after_previous_shot(self):
        # 新卡点 s2 写在最前面（书写顺序排第一）：以前从开头找，对到第一次“钢丝刷”；现在从上一个镜头的卡点 e1（“再说”）往后找
        prev = {'s1': at(0), 'e1': at(TEXT.index('再说')), 'e2': at(TEXT.index('最后'))}
        sp = self.spec({'s2': ['钢丝刷', None], 's1': ['开头', None], 'e1': ['再说', None], 'e2': ['最后', None]})
        sp['shots'] = [{'id': 'B1', 'a': 's1', 'b': 'e1'}, {'id': 'B2', 'a': 's2', 'b': 'e2'}]
        out = beats.compile_spec(sp, TOK, 30, previous=prev)
        self.assertEqual(out['anchors']['s2'], at(TEXT.index('钢丝刷', TEXT.index('再说'))))

    def test_errors_give_copyable_fix_and_nearby_words(self):
        with self.assertRaises(SystemExit) as e:
            beats.compile_spec(self.spec({'a': ['最后', None], 'b': ['钢丝刷', 'a']}), TOK, 30)
        m = str(e.exception)
        self.assertIn('7.00 秒附近的口播原话：要然后再说一遍钢丝刷｜最后说光纤设备和网线', m)          # 起找点前后各十来个字
        self.assertIn(f'「钢丝刷」在 {at(TEXT.index("钢丝刷")):.2f} 秒说过', m); self.assertIn('"b": ["钢丝刷", "开头"]', m)   # 能照抄的写法
        out = beats.compile_spec(self.spec({'a': ['最后', None], 'b': ['钢丝刷', '开头']}), TOK, 30)                 # 照抄那一行就过
        self.assertEqual(out['anchors']['b'], at(TEXT.index('钢丝刷')))
        with self.assertRaises(SystemExit) as e:
            beats.compile_spec(self.spec({'a': ['开头', 'b'], 'b': ['钢丝刷', 'a']}), TOK, 30)
        m = str(e.exception); self.assertIn('绕成了圈：a → b → a', m); self.assertIn('"a": ["开头", null]', m); self.assertIn('"a": ["开头", "开头"]   （口播 1.00 秒：｜开头说一句钢丝刷很重）', m)
        with self.assertRaisesRegex(SystemExit, '没有这个卡点。改法（照抄）："q": \\["开头", null\\]'):
            beats.compile_spec(self.spec({'q': ['开头', '没有的']}), TOK, 30)


class SfxSpecTests(unittest.TestCase):
    def setUp(self):
        k1, k2 = TEXT.index('光纤'), TEXT.index('结束')
        self.spec = dict(anchors={'s': ['光纤', None], 'e': ['结束', None]}, shots=[{'id': 'A21', 'a': 's', 'b': 'e'}],
                         sounds={'CLICK': '02_清脆鼠标点击.mp3'}, gain={}, sfx=[])
        self.inside = at(k1) + 0.5; self.outside = at(k2) + 3

    def compile(self, sfx_rows, events):
        sp = dict(self.spec, sfx=sfx_rows); return beats.compile_spec(sp, TOK, 40, events=events)

    def test_event_sound_uses_current_event_time(self):
        ev = [{'shot': 'A21', 't': self.inside, 'name': '光纤设备出现', 'project': 'Blender_网络'}]
        out = self.compile([{'event': '光纤设备', 'shot': 'A21', 'sound': 'CLICK', 'beat': '画面重点', 'why': '设备出现'}], ev)
        e = out['sfx'][0]
        self.assertEqual((e['t'], e['event'], e['beat'], e['project']), (round(self.inside, 3), '光纤设备出现', '画面重点', 'Blender_网络'))

    def test_stale_event_outside_shot_is_refused(self):
        ev = [{'shot': 'A21', 't': self.outside, 'name': '光纤设备出现', 'project': '动画_网络'}]
        with self.assertRaisesRegex(SystemExit, '不在镜头 A21 里'):
            self.compile([{'event': '光纤设备', 'shot': 'A21', 'sound': 'CLICK', 'beat': '画面重点'}], ev)
        with self.assertRaisesRegex(SystemExit, '不在当前动画的事件表里'):
            self.compile([{'event': '网线', 'sound': 'CLICK', 'beat': '画面重点'}], ev)

    def test_beat_category_required(self):
        with self.assertRaisesRegex(SystemExit, '没写 beat'):
            self.compile([{'at': 's', 'sound': 'CLICK'}], [])
        with self.assertRaisesRegex(SystemExit, '不是重点类'):
            self.compile([{'at': 's', 'sound': 'CLICK', 'beat': '每句话'}], [])
        out = self.compile([{'at': 's', 'sound': 'CLICK', 'beat': '关键数字', 'd': 0.1}], [])
        self.assertEqual(out['sfx'][0]['t'], round(at(TEXT.index('光纤')) + 0.1, 3))

    def test_push_is_silent_by_default(self):
        sp = dict(self.spec, sounds={'ZOOM': 'z.mp3'})
        out = beats.compile_spec(sp, TOK, 40, pushes=[{'shot': 'A21', 't0': self.inside, 't1': self.inside + 0.7, 'kind': 'in'}])
        self.assertEqual(out['sfx'], [])

    def test_peak_minute_only_warns(self):
        self.assertEqual(beats.PEAK_MINUTE, 12)

    def test_per_minute(self):
        self.assertEqual(beats.per_minute([1, 2, 3, 70, 71])[0], 3)
        self.assertEqual(beats.per_minute([])[0], 0)


class FlickerTests(unittest.TestCase):
    def test_single_frame_flash_and_black_frame_are_found_motion_is_not(self):
        import numpy as np
        rng = np.random.default_rng(0)
        move = np.stack([np.roll(rng.uniform(0, 255, (72, 54)), k * 3, axis=1) for k in range(30)])   # 运镜：整幅在动、亮度不跳
        self.assertEqual(check.flicker(move), [])
        flash = move.copy(); flash[10] = np.clip(flash[10] + 40, 0, 255)                              # 单帧整幅变亮
        self.assertEqual(check.flicker(flash), [10])
        black = move.copy(); black[0] = 0; black[20] = 3                                               # 首帧黑、中间一帧黑
        self.assertEqual(check.flicker(black), [0, 20])
        fade = np.stack([np.full((72, 54), 10.0 + k * 6) for k in range(30)])                          # 淡入：连续变亮不算
        self.assertEqual(check.flicker(fade), [])


class PipTests(unittest.TestCase):
    def test_positions_and_segments(self):
        sp = dict(anchors={'s': ['钢丝刷', None], 'm': ['然后', None], 'e': ['光纤', None]}, shots=[{'id': 'B1', 'a': 's', 'b': 'e'}],
                  pip={'min_seconds': 1, 'pos': {'B1': [['m', 'BL'], [1, [1500, 900]]]}}, sounds={}, gain={}, sfx=[])
        out = beats.compile_spec(sp, TOK, 40)
        b1 = next(s for s in out['shots'] if s['id'] == 'B1')
        self.assertEqual([x[2] for x in b1['pip_at']], ['BL', [1500, 900]])
        self.assertEqual(b1['pip_at'][0][1], at(TEXT.index('然后')))
        sp['pip']['pos'] = {'B1': 'none'}
        self.assertNotIn('pip', next(s for s in beats.compile_spec(sp, TOK, 40)['shots'] if s['id'] == 'B1'))
        sp['pip']['pos'] = {'B1': 'XX'}
        with self.assertRaisesRegex(SystemExit, '位置写错.*ML/MC/MR'):          # C13④：报错里列全 9 个位置（以前漏了 MC）
            beats.compile_spec(sp, TOK, 40)
        sp['pip']['pos'] = {'B1': 'MC'}
        self.assertEqual(next(s for s in beats.compile_spec(sp, TOK, 40)['shots'] if s['id'] == 'B1')['pip_at'][0][2], 'MC')

    def test_named_bottom_corners_match_old_left_right(self):
        with tempfile.TemporaryDirectory() as d:
            for fmt in ('3:4', '3:4满屏', '9:16', '16:9'):
                Path(d, 'job.json').write_text(json.dumps({'format': fmt, 'frames': 100}))
                job = Job(d); P = job.fmt['pip']; a = overlay.anchors(job)
                self.assertEqual(a['BL'][:2], (P['L'][0] - P['ring'], P['L'][1] + P['d'] + P['ring']))
                self.assertEqual(a['BR'][:2], (P['R'][0] + P['d'] + P['ring'], P['R'][1] + P['d'] + P['ring']))
                self.assertLess(a['TL'][1], a['BL'][1])
        self.assertEqual(overlay.segments(None, [{'a': 1, 'b': 2, 'pip': 'R'}, {'a': 2, 'b': 3, 'pip': True},
                                                 {'a': 3, 'b': 4, 'pip': 'L', 'pip_at': [[3, 3.5, 'TL'], [3.5, 4, [10, 20]]]}]),
                         [(1, 2, 'BR'), (2, 3, 'BL'), (3, 3.5, 'TL'), (3.5, 4, (10, 20))])


class JobBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / '项目'; (root / '02_制作' / '素材_音效').mkdir(parents=True); (root / '项目.json').write_text('{}')
        self.d = root / '02_制作' / '自动剪辑'; self.d.mkdir()
        wj(self.d / 'job.json', {'format': '3:4', 'frames': 1200, 'main_timeline': 'v1', 'source': '/x', 'project': 'p'})
        wj(self.d / '口播字时间.json', {'tokens': TOK})
        wj(self.d / '分镜规格.json', {'anchors': {'s': ['钢丝刷', None], 'e': ['光纤', None]}, 'shots': [{'id': 'B1', 'a': 's', 'b': 'e'}],
                                     'sounds': {}, 'gain': {}, 'sfx': []})
        self.job = Job(self.d)


class JobTests(JobBase):
    def test_rerun_beats_keeps_sfx(self):
        wj(self.d / '分镜.json', {'anchors': {}, 'shots': [], 'sfx': [{'t': 5.0, 'file': 'a.mp3', 'gain_db': None, 'role': ''}]})
        beats.run(self.job)
        self.assertEqual(len(rj(self.d / '分镜.json')['sfx']), 1)

    def test_picture_notes_badge_and_one_image_per_scene(self):
        """2026-11-22 反馈单：AI 图不加角标；口播列举几个场景时一个场景一张图。编分镜时提醒，不拦。"""
        tok = tokens('有人看投影有人看电视有的家里有宝宝那我们做私宅')
        fl = {'items': [{'use': 'keywords', 'at': '看投影', 'until': '那我们做私宅', 'items': [{'t': '看投影', 'at': '看投影'}, {'t': '看电视', 'at': '看电视'}, {'t': '有宝宝', 'at': '有宝宝'}]}]}
        wj(self.d / '花字.json', fl)
        one = [{'id': 'R14', 'kind': 'B', 'a': 1.0, 'b': 9.0, '图片': {'文件': ['实拍/AI图/home_标注.png'], '铺法': '完整'}}]
        notes = beats.picture_notes(self.job, one, tok)
        self.assertEqual(len(notes), 2)
        self.assertIn('home_标注.png', notes[0]); self.assertIn('不加“示意图／AI 生成”角标', notes[0])
        self.assertIn('R14 用 1 张图配了口播列举的 3 个场景（看投影、看电视、有宝宝）', notes[1])
        at = lambda w: 1.0 + 0.3 * '有人看投影有人看电视有的家里有宝宝那我们做私宅'.index(w)
        each = [{'id': f'R14{k}', 'kind': 'B', 'a': at(a), 'b': at(b), '图片': {'文件': [f'实拍/AI图/{k}_画面.png'], '铺法': '完整'}}
                for k, a, b in (('a', '看投影', '看电视'), ('b', '看电视', '有宝宝'), ('c', '有宝宝', '那我们'))]
        self.assertEqual(beats.picture_notes(self.job, each, tok), [])                 # 一个场景一张、文件名不带角标：不提醒
        three = [{'id': 'R14', 'kind': 'B', 'a': 1.0, 'b': 9.0, '图片': {'文件': ['a.png', 'b.png', 'c.png'], '铺法': '完整'}}]
        self.assertEqual(beats.picture_notes(self.job, three, tok), [])                # 一个镜头里放了 3 张（平分时长）也算每个场景有图
        clean = [{'id': 'R02', 'kind': 'B', 'a': 20.0, 'b': 22.0, '图片': {'文件': ['实拍/R02a_模拟图_无角标.png'], '铺法': '完整'}}]
        self.assertEqual(beats.picture_notes(self.job, clean, tok), [])                # 文件名写着“无角标”的不报（261122 真片子上试出来的误报）
        (self.d / '花字.json').unlink(); self.assertEqual(beats.picture_notes(self.job, each, tok), [])   # 没有花字表不出错

    def test_sfx_script_rerun_after_beats(self):
        (self.d / '合成').mkdir()
        (self.d / '合成' / '音效节拍表.py').write_text('# -*- coding: utf-8 -*-\nimport json\nb=json.load(open("分镜.json"))\n'
                                                      'b["sfx"]=[{"t":1.0,"file":"a.mp3","gain_db":None,"role":""}]*2\njson.dump(b,open("分镜.json","w"))\n')
        sp = rj(self.d / '分镜规格.json'); sp['音效脚本'] = '合成/音效节拍表.py'; wj(self.d / '分镜规格.json', sp)
        beats.run(self.job)
        self.assertEqual(len(rj(self.d / '分镜.json')['sfx']), 2)

    def test_sfx_zero_and_too_many(self):
        wj(self.d / '分镜.json', {'shots': [], 'sfx': []})
        with self.assertRaisesRegex(SystemExit, '0 个音效'): sfx.run(self.job)
        wj(self.d / '分镜.json', {'shots': [], 'sfx': [{'t': i * 0.5, 'file': 'a.mp3'} for i in range(60)]})   # 40 秒片子 60 个
        with self.assertRaisesRegex(SystemExit, '音效太多'): sfx.run(self.job)

    def test_project_sound_paths(self):
        f = self.d.parent / '素材_音效' / 'x.wav'; f.write_bytes(b'0')
        self.assertEqual(sfx.local_sound(self.job, '素材_音效/x.wav'), f.resolve())
        self.assertEqual(sfx.local_sound(self.job, str(f)), f)
        self.assertIsNone(sfx.local_sound(self.job, '素材_音效/没有.wav'))

    def test_review_refuses_zero_sfx_before_resolve(self):
        with self.assertRaisesRegex(SystemExit, '0 个音效'): resolve.review(self.job)
        wj(self.d / '合成' / '音效表.json', [{'file': 'a', 'record': 1}])
        import os, time; wj(self.d / '分镜.json', {'shots': []}); t = time.time() + 5; os.utime(self.d / '分镜.json', (t, t))
        with self.assertRaisesRegex(SystemExit, '再跑 sfx'): resolve.review(self.job)

    def test_changes_markers(self):
        wj(self.d / '分镜.json', {'anchors': {'s': 10.0}, 'shots': [{'id': 'B1', 'a': 12.0, 'b': 20.0}]})
        wj(self.d / 'c.json', [{'t': 1.5, 'name': '地漏芯（R36）', 'note': '新增实拍'}, {'at': 's', 'name': '改：A21 音效', 'note': '卡到光纤设备'},
                               {'shot': 'B1', 'name': '新增：B1', 'note': 'x'}])
        got = resolve.load_changes(self.job, self.d / 'c.json')
        self.assertEqual([(f, n) for f, n, _ in got], [(45, '改：地漏芯（R36）'), (300, '改：A21 音效'), (360, '新增：B1')])
        wj(self.d / 'c.json', [{'t': 999, 'name': '改：x', 'note': 'y'}])
        with self.assertRaisesRegex(SystemExit, '超出成片'): resolve.load_changes(self.job, self.d / 'c.json')
        wj(self.d / 'c.json', [{'t': 1, 'name': '改：x'}])
        with self.assertRaisesRegex(SystemExit, 'note'): resolve.load_changes(self.job, self.d / 'c.json')

    def test_flower_and_extra_lists(self):
        self.assertEqual(resolve.flower_items(self.job), [])
        wj(self.d / '花字' / '花字表.json', [{'file': '/old.mov', 'start': 3, 'end': 9}])                 # 更早的位置
        self.assertEqual(resolve.flower_items(self.job), [('/old.mov', 3, 6)])
        wj(self.d / '合成' / '花字' / '花字表.json', [{'file': '/a.mov', 'start': 30, 'end': 90}])        # 水电那份：纯列表
        self.assertEqual(resolve.flower_items(self.job), [('/a.mov', 30, 60)])
        wj(self.d / '合成' / '花字' / '花字表.json', {'items': [{'file': '/b.mov', 'start': 60, 'end': 100}], 'warnings': []})   # 包装系统
        self.assertEqual(resolve.flower_items(self.job), [('/b.mov', 60, 40)])
        f = self.d / 'amb.wav'; f.write_bytes(b'0')
        wj(self.d / 'x.json', [{'kind': 'audio', 'track': '环境', 'file': str(f), 't': 1, 'frames': 30}])
        self.assertEqual(list(resolve.extra_items(self.job, self.d / 'x.json')), [('audio', '环境')])
        wj(self.d / 'x.json', [{'kind': 'audio', 'track': '环境', 'file': '/没有.wav'}])
        with self.assertRaisesRegex(SystemExit, '不存在'): resolve.extra_items(self.job, self.d / 'x.json')

    def test_export_target_and_history(self):
        import datetime
        now = datetime.datetime(2026, 11, 12, 23, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        root = self.job.dir.parents[1]; wj(root / '项目.json', {'name': '261110 水电阶段'})
        self.assertEqual(resolve.export_target(self.job, None, None, '审核线', now), (root / '03_成片', '261112_261110 水电阶段', 'mp4'))
        self.assertEqual(resolve.export_target(self.job, str(root / '03_成片' / 'x.mov'), None, '审核线')[1:], ('x', 'mov'))
        d, stem, ext = resolve.export_target(self.job, str(root / '别处'), None, '审核线')
        self.assertEqual((d, ext), ((root / '别处').resolve(), 'mp4')); self.assertTrue(stem.startswith('审核线_'))
        d, stem, _ = resolve.export_target(self.job, None, 5, '审核线'); self.assertTrue(d == self.job.dir / '成片' and '前5秒测试' in stem)   # 试导不进 03_成片
        out = root / '03_成片'; out.mkdir(); (out / 'a.mp4').write_text('1'); (out / 'a.srt').write_text('2')
        moved = resolve.make_room(out, 'a', 'mp4')
        self.assertEqual(sorted(m.name for m in moved), ['a.mp4', 'a.srt']); self.assertFalse((out / 'a.mp4').exists())
        (out / 'a.mp4').write_text('3'); self.assertEqual(len(list((out / '历史').iterdir())), 2); resolve.make_room(out, 'a', 'mp4')
        self.assertEqual(len(list((out / '历史').iterdir())), 3)                                       # 历史里已有同名就加时间，不覆盖

    def test_export_refuses_preview_size(self):
        wj(self.d / '分镜.json', {'shots': [{'id': 'A10', 'kind': 'B', 'a': 1, 'b': 2}]})
        wj(self.d / '合成' / '渲染尺寸.json', {'items': [{'name': 'Blender_通风', 'shot': 'A10', 'file': '/x.mov', 'scale': 50}]})
        with self.assertRaisesRegex(SystemExit, '“定稿”'): resolve.export(self.job, user_ok='用户说 OK')      # 2026-11-22 规矩变了：转述的原话不算，先被最终闸拦
        settle(self.job)                                                                                     # 用户在审片台定了：预览分辨率照旧拦
        with self.assertRaisesRegex(SystemExit, '预览分辨率'): resolve.export(self.job, user_ok='用户说 OK')

    @unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
    def test_check_reports_events_outside_shot_and_density(self):
        c = self.d / '合成'; c.mkdir(exist_ok=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=216x288:rate=30000/1001:duration=40',
                        '-pix_fmt', 'yuv420p', str(c / 'B肉.mp4')], check=True)
        wj(self.d / '分镜.json', {'shots': [{'id': 'F_0', 'kind': 'F', 'a': 0, 'b': 10}, {'id': 'A21', 'kind': 'B', 'a': 10, 'b': 20},
                                           {'id': 'F_2', 'kind': 'F', 'a': 20, 'b': 40.04}], 'sfx': []})
        wj(c / '音效表.json', [{'file': '/a.mp3', 'record': 450, 'at': 15.0, 'event': '光纤设备', 'shot': 'A21'}])
        ev = self.d / 'Blender_网络' / '事件.json'
        wj(ev, [{'shot': 'A21', 't': 15.5, 'name': '光纤设备'}, {'shot': 'A21', 't': 25.0, 'name': '网线设备'}])
        wj(c / '动画清单.json', {'projects': [{'name': 'Blender_网络', 'events': str(ev)}], 'shots': {'A21': {'project': 'Blender_网络', 'file': 'x'}}})
        check.run(self.job); r = rj(c / '自检结果.json')
        self.assertEqual([x[1:3] for x in r['画面事件不在对应镜头里']], [['A21', '网线设备']])
        self.assertEqual(len(r['音效卡的画面事件已过期']), 1)          # 音效在 15.0，当前事件在 15.5
        self.assertEqual(r['每分钟音效'], [])
        self.assertIn('平均每分钟 1.5 个', r['音效密度'])
        self.assertTrue(r['总装逐镜头比对'])                              # 有登记就要有 anim-assemble 的比对结果
        # C7：代码动画标签自检（动画/自查/标签重叠.json）是 check 的一项，有重叠帧就不过；没有这个文件不算不过
        self.assertEqual(r['代码动画标签压物体'], [])
        wj(self.d / '动画' / '自查' / '标签重叠.json', {'查了帧数': 300, '重叠帧数': 2, '镜头': {'A21': [15.0, 15.03]}})
        self.assertFalse(check.run(self.job))
        self.assertEqual(rj(c / '自检结果.json')['代码动画标签压物体'], ['A21 字压物体 2 帧'])


def said(fn, *a, **k):
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): r = fn(*a, **k)
    return r, buf.getvalue()


@unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
class SfxPlaceTests(JobBase):
    """2026-11-13 C1/C2：最密一分钟的个数、片头音效不放到 0 帧之前。"""
    def setUp(self):
        super().setUp()
        lib = self.d.parent / '库'; lib.mkdir(); wj(lib / '收藏.json', {'favorites': []})
        cfg = rj(self.d / 'job.json'); cfg.update(sfx_library=str(lib), sfx_favorites=str(lib / '收藏.json')); wj(self.d / 'job.json', cfg); self.job = Job(self.d)
        ff = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi']
        # 音效：先静 334 毫秒（10 帧）再响半秒，起音在选段第 10 帧左右；全长约 25 帧
        subprocess.run(ff + ['-i', 'sine=frequency=880:duration=0.5', '-af', 'adelay=334:all=1', str(self.d.parent / '素材_音效' / 'x.wav')], check=True)
        (self.d / '合成').mkdir(exist_ok=True)
        subprocess.run(ff + ['-i', 'sine=frequency=220:duration=3', '-c:a', 'pcm_s16le', str(self.d / '合成' / 'A肉.mov')], check=True)

    def test_peak_minute_is_a_count_and_hook_sound_not_negative(self):
        wj(self.d / '分镜.json', {'shots': [], 'sfx': [{'t': t, 'file': '素材_音效/x.wav'} for t in (0.0, 10.0, 20.0)]})
        _, out = said(sfx.run, self.job); rows = rj(self.d / '合成' / '音效表.json'); fps = float(self.job.fps)
        # C1：以前这里报的是最后一个音效的帧数（25 左右），还跟着报“太密”
        self.assertIn('最密的一分钟（0 秒起）3 个', out); self.assertNotIn('太密', out)
        # C2：0 秒的开场音效，起音在选段第 k 帧：以前 record = -k
        k = rows[1]['sync_frame_in_sel']; self.assertGreater(k, 5)
        self.assertEqual(rows[1]['record'], round(10.0 * fps) - k); self.assertNotIn('head_cut', rows[1])
        self.assertTrue(all(r['record'] >= 0 for r in rows))
        self.assertEqual((rows[0]['record'], rows[0]['src_start'], rows[0]['sync_frame_in_sel'], rows[0]['head_cut']), (0, k, 0, k))   # 从 0 帧放、去掉开头 k 帧，起音仍在卡点帧
        self.assertEqual(rows[0]['frames'], rows[1]['frames'] - k)
        self.assertIn('去掉开头', out)

    def test_place(self):
        self.assertEqual(sfx.place(300, 9), (291, 0))
        self.assertEqual(sfx.place(5, 9), (0, 4))
        self.assertEqual(sfx.place(0, 0), (0, 0))


class InitForceTests(unittest.TestCase):
    """2026-11-13 C5：init --force 先备份，再把手填的设置带过来。"""
    def test_force_backs_up_and_keeps_user_fields(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t); ar = t / '口播'; out = t / '交付'; (ar / '审核').mkdir(parents=True); out.mkdir(); (out / 'a.srt').write_text('x')
            wj(ar / '审核' / 'latest_delivery.json', {'status': 'complete', 'output': str(out), 'project': '工程新', 'timeline': '主线新', 'frames': 999})
            wj(ar / '审核' / 'project_runtime.json', {'source': '/新源.mov', 'fps': [30000, 1001]})
            d = t / '片'; d.mkdir()
            old = {'title': '老标题', 'format': '3:4', 'project': '工程旧', 'main_timeline': '主线旧', 'frames': 100, 'source': '/旧.mov', 'srt': '/旧.srt', 'aroll': '/旧', 'fps': '30000/1001',
                   'subtitle_keywords': ['防水', '闭水试验'], 'subtitle_replace': {'刷子': '钢丝刷'}, 'pip_body': [100, 200, 1800], 'voice_db': 6.5, 'max_gap': 0.4,
                   'export_ok': {'user_said': 'OK'}, 'review_timeline': '主线旧_卡点审核'}
            wj(d / 'job.json', old)
            S = Path(__file__).resolve().parents[1] / 'thinkcut_edit.py'
            r = subprocess.run([sys.executable, '-B', str(S), 'init', str(d), '--aroll', str(ar)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertNotEqual(r.returncode, 0); self.assertEqual(rj(d / 'job.json'), old)                       # 不加 --force：不动
            r = subprocess.run([sys.executable, '-B', str(S), 'init', str(d), '--aroll', str(ar), '--force'], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            baks = list(d.glob('job.备份_*.json')); self.assertEqual(len(baks), 1); self.assertEqual(rj(baks[0]), old)
            new = rj(d / 'job.json')
            for k in ('subtitle_keywords', 'subtitle_replace', 'pip_body', 'voice_db', 'max_gap', 'title'): self.assertEqual(new[k], old[k], k)
            self.assertEqual((new['project'], new['main_timeline'], new['frames'], new['source']), ('工程新', '主线新', 999, '/新源.mov'))
            self.assertNotIn('export_ok', new); self.assertNotIn('review_timeline', new)                          # 重新 init 后要重新确认、重新建审核线
            self.assertIn(str(baks[0]), r.stdout); self.assertIn('subtitle_keywords', r.stdout); self.assertIn('pip_body', r.stdout)
            self.assertRegex(r.stdout, '没带过来.*export_ok')


class PeakTests(unittest.TestCase):
    """2026-11-13 C6：导出后量不出响度/真峰值要停，不能静默跳过限幅。"""
    def test_parse(self):
        text = '[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -14.3 LUFS\n    Threshold: -24.5 LUFS\n\n  True peak:\n    Peak:       -0.4 dBFS\n'
        self.assertEqual(resolve.parse_loudness(text), (-14.3, -0.4))
        for bad in ('', None, 'I:  -14.3 LUFS', 'Peak:  -0.4 dBFS', 'Conversion failed!'): self.assertIsNone(resolve.parse_loudness(bad))

    def test_unmeasurable_stops_and_says_how_to_rerun(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / '成片.mp4'; f.write_bytes(b'x')
            with self.assertRaisesRegex(SystemExit, '(?s)成片已导出.*峰值没验证.*成片.mp4.*export-peak') : resolve.limit_peak(f, measure=lambda p: None)
            self.assertEqual(f.read_bytes(), b'x')
            _, out = said(resolve.limit_peak, f, measure=lambda p: (-16.0, -3.2)); self.assertIn('真峰值 -3.2', out); self.assertEqual(f.read_bytes(), b'x')   # 没超：不动文件

    @unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
    def test_real_files(self):
        with tempfile.TemporaryDirectory() as t:
            mk = lambda name, af: subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=30:duration=2', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                                                  '-af', af, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(Path(t) / name)], check=True) and None or Path(t) / name
            quiet = mk('小声.mp4', 'volume=0dB'); b0 = quiet.read_bytes()
            (I, pk, did), _ = said(resolve.limit_peak, quiet); self.assertFalse(did); self.assertLess(pk, -1.0); self.assertEqual(quiet.read_bytes(), b0)
            loud = mk('爆音.mp4', 'volume=17.5dB'); before = resolve.loudness(loud)[1]; self.assertGreater(before, -1.0)
            (I, pk, did), out = said(resolve.limit_peak, loud); self.assertTrue(did); self.assertLess(pk, before); self.assertIn('限幅后', out)
            self.assertEqual(resolve.probe(loud).get('codec_name'), 'h264')                                       # 画面还在、没重编
            novoice = Path(t) / '没声音.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=30:duration=1', '-pix_fmt', 'yuv420p', str(novoice)], check=True)
            with self.assertRaisesRegex(SystemExit, '量不出'): resolve.limit_peak(novoice)                          # 真的量不出来（没有声音）：停


class FakeTL:
    def __init__(s, proj, name, uid): s.proj, s.name, s.uid, s.set = proj, name, uid, {}
    def GetName(s): return s.name
    def GetUniqueId(s): return s.uid
    def GetStartFrame(s): return 108000
    def GetEndFrame(s): return 108000 + 1200
    def GetSetting(s, k): return s.set.get(k, '')
    def SetSetting(s, k, v):
        if s.proj.can_4k: s.set[k] = v
        return s.proj.can_4k
    def GetItemListInTrack(s, kind, i): return []
    def DuplicateTimeline(s, name):
        s.proj.n += 1; t = FakeTL(s.proj, name, f'uid-{s.proj.n}'); s.proj.tls.append(t); return t


class FakeFolder:
    def __init__(s, name): s.name = name
    def GetName(s): return s.name
    def GetSubFolderList(s): return []
    def GetClipList(s): return []


class FakeProject:
    """假达芬奇工程：只够 review 走到“设 4K / 导入素材”这两步。"""
    def __init__(s, names, can_4k=True, can_delete=True):
        s.n = 0; s.can_4k, s.can_delete = can_4k, can_delete; s.tls = []; s.saves = 0; s.cur = None
        for x in names: s.n += 1; s.tls.append(FakeTL(s, x, f'uid-{s.n}'))
    def GetName(s): return 'p'
    def GetTimelineCount(s): return len(s.tls)
    def GetTimelineByIndex(s, i): return s.tls[i - 1]
    def SetCurrentTimeline(s, t): s.cur = t; return True
    def GetMediaPool(s): return s
    def GetCurrentProject(s): return s          # 兼当 ProjectManager
    def SaveProject(s): s.saves += 1; return True
    def GetRootFolder(s): return FakeFolder('Master')
    def AddSubFolder(s, root, n): return FakeFolder(n)
    def SetCurrentFolder(s, f): return True
    def ImportMedia(s, paths): return []        # 导入失败
    def DeleteTimelines(s, tls):
        if not s.can_delete: return False
        s.tls = [t for t in s.tls if t not in tls]; return True



class PoolClip:
    def __init__(s, path, mid): s.path, s.mid = path, mid
    def GetClipProperty(s, k): return s.path if k == 'File Path' else '0'
    def GetMediaId(s): return s.mid


class PoolFolder(FakeFolder):
    def __init__(s, name, clips=(), subs=()): super().__init__(name); s.clips, s.subs = list(clips), list(subs)
    def GetSubFolderList(s): return s.subs
    def GetClipList(s): return s.clips


class PoolItem:
    def __init__(s, c): s.c = c
    def GetMediaPoolItem(s): return s.c


class PoolTL(FakeTL):
    def __init__(s, proj, name, uid, items=()): super().__init__(proj, name, uid); s.items = list(items)
    def GetTrackCount(s, kind): return 1 if kind == 'video' else 0
    def GetItemListInTrack(s, kind, i): return s.items if kind == 'video' else []
    def DuplicateTimeline(s, name):
        s.proj.n += 1; t = PoolTL(s.proj, name, f'uid-{s.proj.n}'); s.proj.tls.append(t); return t


class PoolProject(FakeProject):
    """媒体池里有 4 份同路径的 B肉：当前文件夹 1 份、旧素材文件夹 2 份（其中 old2 旧审核线在用）、另一层子文件夹 1 份。"""
    def __init__(s, names, path):
        super().__init__([]); s.deleted, s.imports = [], []
        used = PoolClip(path, 'old2')
        for x in names: s.n += 1; s.tls.append(PoolTL(s, x, f'uid-{s.n}', [PoolItem(used)] if x == '旧审核线' else []))
        s.root = PoolFolder('Master', subs=[PoolFolder('B-roll 动画', [PoolClip(path, 'cur')]),
                                            PoolFolder('旧素材_v1_卡点审核_第4版', [PoolClip(path, 'old1'), used], [PoolFolder('更旧', [PoolClip(path, 'old3'), PoolClip('/别的.mp4', 'x')])])])
    def GetRootFolder(s): return s.root
    def AddSubFolder(s, root, n): f = PoolFolder(n); root.subs.append(f); return f
    def ImportMedia(s, paths): s.imports.append(paths); return []
    def DeleteClips(s, clips):
        def walk(f):
            for c in list(f.clips):
                if c in clips: f.clips.remove(c); s.deleted.append(c.mid)
            for g in f.subs: walk(g)
        walk(s.root); return True
    def left(s):
        out = []
        def walk(f): out.extend(c.mid for c in f.clips if c.mid != 'x'); [walk(g) for g in f.subs]
        walk(s.root); return out

class ResolveGuardTests(JobBase):
    """2026-11-13 C9/C10/C11：用假达芬奇对象，不连达芬奇。"""
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, resolve, 'project', resolve.project)
        c = self.d / '合成'; (c / '字幕').mkdir(parents=True)
        wj(self.d / '分镜.json', {'anchors': {}, 'shots': [{'id': 'B1', 'kind': 'B', 'a': 1.0, 'b': 5.0}], 'sfx': []})
        for f in (c / 'B肉.mp4', c / '字幕' / '字幕整轨.mov'): f.write_bytes(b'x')
        wj(c / '字幕' / '字幕表.json', [{'n': 1, 'start': 0, 'end': 30, 'text': '字'}])
        wj(c / '音效表.json', [{'file': '/a.mp3', 'record': 30, 'frames': 10, 'src_start': 0, 'gain_db': -6.0}])

    def fake(self, **kw):
        p = FakeProject(['v1', '旧审核线'], **kw); resolve.project = lambda job: (object(), p, p); return p

    def test_review_failure_removes_only_the_new_timeline(self):
        p = self.fake(can_4k=False)                                               # 复制出来之后第一步（设 4K）就失败
        with self.assertRaisesRegex(SystemExit, '(?s)设不成 4K.*已删掉这次新建的半成品时间线「v1_卡点审核」'): resolve.review(self.job)
        self.assertEqual([t.name for t in p.tls], ['v1', '旧审核线']); self.assertEqual(p.cur.name, 'v1'); self.assertGreaterEqual(p.saves, 1)
        self.assertNotIn('review_timeline', rj(self.d / 'job.json'))               # 没建成：不记成审核线
        p = self.fake()                                                           # 走到导入动画素材才失败（更靠后的一步）
        for _ in range(2):                                                        # 同一条命令直接重跑：不会被“已有时间线”挡住
            with self.assertRaisesRegex(SystemExit, '(?s)导入失败.*已删掉') as e: resolve.review(self.job)
            self.assertNotIn('已有时间线', str(e.exception)); self.assertEqual([t.name for t in p.tls], ['v1', '旧审核线'])
        self.assertEqual(p.n, 4)                                                  # 两次各新建过一条（uid-3、uid-4），都只删了自己那条

    def test_review_same_name_other_timeline_is_never_deleted(self):
        p = self.fake(can_delete=False)
        with self.assertRaisesRegex(SystemExit, '(?s)导入失败.*没能自动删掉.*手动删掉这一条'): resolve.review(self.job)
        self.assertEqual([t.name for t in p.tls], ['v1', '旧审核线', 'v1_卡点审核'])
        with self.assertRaisesRegex(SystemExit, '已有时间线') as e: resolve.review(self.job)      # 复制之前就停的：什么都不删
        self.assertNotIn('删掉', str(e.exception)); self.assertEqual(len(p.tls), 3)
        self.assertFalse(resolve.drop_timeline(p, None)); self.assertFalse(resolve.drop_timeline(p, 'uid-不存在')); self.assertEqual(len(p.tls), 3)

    # —— 2026-11-22 261122 反馈单 新2（Q106 复发） ——
    def test_review_sigterm_still_removes_half_made_timeline(self):
        import os, signal
        p = self.fake()
        def killed(paths): os.kill(os.getpid(), signal.SIGTERM); return []                         # 导入卡住时被外面结束（命令超时）
        p.ImportMedia = killed; before = signal.getsignal(signal.SIGTERM)
        with self.assertRaisesRegex(SystemExit, '(?s)进程被结束.*已删掉这次新建的半成品时间线「v1_卡点审核」.*没建成'): resolve.review(self.job)
        self.assertEqual([t.name for t in p.tls], ['v1', '旧审核线']); self.assertFalse((self.d / '合成' / '审核线建线中.json').exists())
        self.assertIs(signal.getsignal(signal.SIGTERM), before)                                     # 恢复原来的处理

    def test_review_removes_leftover_of_hard_killed_run(self):
        p = FakeProject(['v1', '旧审核线', 'v1_卡点审核']); resolve.project = lambda job: (object(), p, p)    # uid-3 是上次被硬杀留下的半成品
        wj(self.d / '合成' / '审核线建线中.json', {'名称': 'v1_卡点审核', 'uid': 'uid-3', '开始': '2026-11-22 18:00:00'})
        with self.assertRaisesRegex(SystemExit, '(?s)导入失败.*已删掉') as e: said(resolve.review, self.job)
        self.assertNotIn('已有时间线', str(e.exception)); self.assertEqual([t.name for t in p.tls], ['v1', '旧审核线'])
        self.assertFalse((self.d / '合成' / '审核线建线中.json').exists())
        from thinkcut import status
        wj(self.d / '合成' / '审核线建线中.json', {'名称': 'v1_卡点审核_第5版', 'uid': 'uid-9'})
        self.assertIn('上次建审核线「v1_卡点审核_第5版」没做完', said(status.run, self.job)[1])

    def test_review_purges_unused_same_path_clips_before_import(self):
        broll = str(self.job.p('合成', 'B肉.mp4')); p = PoolProject(['v1', '旧审核线'], broll)
        resolve.project = lambda job: (object(), p, p)
        with self.assertRaisesRegex(SystemExit, '(?s)导入失败.*已删掉'): said(resolve.review, self.job)
        self.assertEqual(sorted(p.deleted), ['cur', 'old1', 'old3'])                                # 没被任何时间线用到的同路径旧条目删掉
        self.assertEqual(p.imports, [[broll]]); self.assertEqual(p.left(), ['old2'])                # 旧审核线还在用的那份留着
        self.assertEqual(resolve.purge_unused(p, p.root, broll, None), 0)                          # 读不出引用：什么都不删


    def test_review_import_helper_does_not_share_a_name_with_the_sfx_list(self):
        """2026-11-22 第一次连真达芬奇建线就崩：导入素材的内层函数用的“这次导入的条目”集合叫 mine，下面放音效时又有个叫 mine 的列表，
        放到音效那一步 mine.add 报 'list' object has no attribute 'add'（替身测试在导入失败处就停了，走不到音效）。"""
        imp = next(c for c in resolve._review.__code__.co_consts if hasattr(c, 'co_name') and c.co_name == 'imp')
        self.assertNotIn('mine', imp.co_freevars); self.assertIn('mine_ids', imp.co_freevars)

    def test_export_refuses_main_timeline(self):
        self.fake()
        for kw in ({'seconds': 5}, {'user_ok': '可以了'}, {'user_ok': '可以了', 'balance': False}):
            with self.assertRaisesRegex(SystemExit, '是主线'): resolve.export(self.job, name='v1', **kw)

    def test_trial_export_does_not_balance(self):
        self.fake(); calls = []
        class Reached(Exception): pass
        def stop(*a, **k): raise Reached()
        for k, v in (('balance_audio', lambda job, r, pm, tl, force=False: calls.append(force)), ('export_target', stop)):
            self.addCleanup(setattr, resolve, k, getattr(resolve, k)); setattr(resolve, k, v)
        before = {f: (self.d / '合成' / f).read_bytes() for f in ('音效表.json',)}
        for kw in ({'seconds': 5}, {'seconds': 5, 'balance': True}):
            with self.assertRaises(Reached): said(resolve.export, self.job, name='旧审核线', **kw)
        self.assertEqual(calls, [])                                                # 试导：不平衡（以前会平衡整条审核线并重写音效表/音量表）
        self.assertEqual({f: (self.d / '合成' / f).read_bytes() for f in before}, before); self.assertFalse((self.d / '合成' / '音量.json').exists())
        self.job.cfg['免修音'] = '（测试：这里只验整片导出会平衡；没修音会被拦见 test_tools_1121 PolishGateTests）'      # 2026-11-22 新4 起整片导出要先修音
        settle(self.job)                                                           # 2026-11-22 起整片导出要用户在审片台点过审核线“这一栏定了”
        with self.assertRaises(Reached): resolve.export(self.job, name='旧审核线', user_ok='可以了')
        self.assertEqual(calls, [False])                                           # 整片导出照旧先平衡


@unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
class SubsPipTests(unittest.TestCase):
    """2026-11-13 C14：subs-check 判“压到小圆圈”用和 pip 同一套位置（pip.pos、分段、推近缩小）。"""
    B = {'shots': [{'id': 'F_0', 'kind': 'F', 'a': 0.0, 'b': 1.0},
                   {'id': 'B1', 'kind': 'B', 'a': 1.0, 'b': 9.0, 'pip': 'L', 'pip_at': [[1.0, 5.0, 'TR'], [5.0, 8.0, [1080, 2500]], [8.0, 9.0, 'none']]},
                   {'id': 'B2', 'kind': 'B', 'a': 9.0, 'b': 10.0, 'pip': 'R'}],
         'pip_shrink': {'windows': [{'a': 2.0, 'b': 4.0}], 'to': 0.64, 'ramp': 0.7}}

    def test_pip_box_follows_pos_segments_and_shrink(self):
        with tempfile.TemporaryDirectory() as d:
            wj(Path(d) / 'job.json', {'format': '3:4', 'frames': 300}); job = Job(d); P = job.fmt['pip']; R = P['d'] + 2 * P['ring']
            x1, y0 = overlay.anchors(job)['TR'][:2]
            self.assertIsNone(overlay.pip_box(job, self.B, 0.5))                                            # 露脸段
            self.assertEqual(overlay.pip_box(job, self.B, 1.5), (x1 - R, y0, x1, y0 + R))                   # 右上，原大
            k = int(R * 0.64 / 2) * 2; self.assertEqual(overlay.pip_box(job, self.B, 3.0), (x1 - k, y0, x1, y0 + k))   # 推近时缩小，右上角不动
            self.assertEqual(overlay.pip_box(job, self.B, 6.0), (1080 - R / 2, 2500 - R / 2, 1080 + R / 2, 2500 + R / 2))   # [x, y] 是圆心
            self.assertIsNone(overlay.pip_box(job, self.B, 8.5))                                            # none：这段不放
            rx, ry = P['R']; self.assertEqual(overlay.pip_box(job, self.B, 9.5), (rx - P['ring'], ry - P['ring'], rx + P['d'] + P['ring'], ry + P['d'] + P['ring']))   # 老写法 pip: R = 右下
            self.assertAlmostEqual(overlay.shrink_factor(self.B, 2.35), 1 - 0.36 * 0.5, places=6)           # 过渡到一半

    def test_sheet_uses_real_pip_position(self):
        from PIL import Image, ImageDraw
        from thinkcut import subs
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); wj(d / 'job.json', {'format': '3:4满屏', 'frames': 300}); wj(d / '分镜.json', self.B); job = Job(d); c = d / '合成'; (c / '字幕').mkdir(parents=True)
            for f in ('A肉.mov', 'B肉_小圆圈.mp4'):
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=108x144:rate=30000/1001', '-frames:v', '300', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(c / f)], check=True)
            def sub(n, mid_s, box):
                im = Image.new('RGBA', (2160, 2880), (0, 0, 0, 0)); ImageDraw.Draw(im).rectangle(box, fill=(0, 0, 0, 150)); f = c / '字幕' / f'字幕_{n}.png'; im.save(f)
                m = round(mid_s * 30000 / 1001); return {'n': n, 'file': str(f), 'start': m - 5, 'end': m + 5, 'text': 'x', 'keys': []}
            wj(c / '字幕' / '字幕表.json', [
                sub(1, 1.5, (200, 1600, 800, 1700)),      # 在左下老位置，但这段圆圈挪到了右上：不算压（以前按左下判，误报）
                sub(2, 3.0, (1250, 1000, 1450, 1100)),    # 在右上圆圈原大的范围里，但这时推近、圆圈缩小了：不算压
                sub(3, 4.9, (1250, 1000, 1450, 1100)),    # 同一位置，圆圈回到原大：压到
                sub(4, 6.0, (800, 2400, 1400, 2600)),     # 圆圈挪到 [1080, 2500]：压到（以前按左下判，漏报）
                sub(5, 8.5, (200, 1600, 800, 1700))])     # 这段写了 none，没有圆圈：不算压
            bad, _ = said(subs.sheet, job)
            self.assertEqual([(x['n'], x['问题']) for x in bad], [(3, ['压到小圆圈']), (4, ['压到小圆圈'])])
            res = {x['n']: x for x in rj(c / '字幕' / '检查' / '检查表.json')}
            self.assertNotIn('小圆圈', res[5]); self.assertEqual(len(res[2]['小圆圈']), 4)


    def test_sheet_overlays_flowers_on_the_frame(self):
        # 2026-11-24 初稿模式第二次实测：总览不叠花字，花字压人脸看不出来
        from PIL import Image, ImageDraw
        from thinkcut import subs
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); wj(d / 'job.json', {'format': '3:4满屏', 'frames': 300}); wj(d / '分镜.json', {'shots': [{'id': 'F_0', 'kind': 'F', 'a': 0.0, 'b': 10.0}]}); job = Job(d)
            c = d / '合成'; (c / '字幕').mkdir(parents=True); (c / '花字').mkdir()
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=black:size=108x144:rate=30000/1001', '-frames:v', '300', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(c / 'A肉.mov')], check=True)
            red = c / '花字' / '红块.mov'          # 透明底、中间一块纯红的花字片段（60 帧）
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red@1.0:size=1080x1440:rate=30000/1001,format=rgba,drawbox=x=0:y=0:w=1080:h=720:color=black@0.0:t=fill',
                            '-frames:v', '60', '-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuva444p10le', str(red)], check=True)
            wj(c / '花字' / '花字表.json', {'items': [{'n': 1, 'use': 'note', 'kind': '文字', 'file': str(red), 'start': 100, 'end': 160}]})
            sub = Image.new('RGBA', (2160, 2880), (0, 0, 0, 0)); ImageDraw.Draw(sub).rectangle((10, 2700, 60, 2750), fill=(255, 255, 255, 255)); f = c / '字幕' / '字幕_a.png'; sub.save(f)
            wj(c / '字幕' / '字幕表.json', [{'n': 1, 'file': str(f), 'start': 120, 'end': 140, 'text': 'x', 'keys': []}, {'n': 2, 'file': str(f), 'start': 200, 'end': 220, 'text': 'x', 'keys': []}])
            _, out = said(subs.sheet, job)
            self.assertIn('1 条画面上有花字', out)        # 第 1 条在花字 100–160 里，第 2 条不在
            sheet = Image.open(c / '字幕' / '检查' / '总览_01.jpg').convert('RGB'); tw = sheet.width // 4
            tile1 = sheet.crop((0, 40, tw, sheet.height // 3)); tile2 = sheet.crop((tw, 40, 2 * tw, sheet.height // 3))
            reds = lambda im: sum(1 for px in im.getdata() if px[0] > 150 and px[1] < 80)
            self.assertGreater(reds(tile1), 500); self.assertEqual(reds(tile2), 0)


class Py39Tests(unittest.TestCase):
    def test_long_chinese_lines_have_coding_declaration(self):
        """Mac 自带 Python 3.9：超过约 1000 字节的中文长行、文件头又没写编码声明时误报 Non-UTF-8 code（新11⑤）。"""
        import re
        root = Path(__file__).resolve().parents[4]; bad = {}
        for f in root.rglob('*.py'):
            if '__pycache__' in f.parts or 'node_modules' in f.parts: continue
            lines = f.read_bytes().split(b'\n')
            if any(re.match(rb'^[ \t\f]*#.*?coding[:=]', x) for x in lines[:2]): continue
            long = [i + 1 for i, x in enumerate(lines) if len(x) >= 1000 and any(b > 127 for b in x)]
            if long: bad[str(f.relative_to(root))] = long
        self.assertEqual(bad, {}, '这些行在 Python 3.9 上可能误报：拆短，或文件头加 # -*- coding: utf-8 -*-')


if __name__ == '__main__':
    unittest.main()
