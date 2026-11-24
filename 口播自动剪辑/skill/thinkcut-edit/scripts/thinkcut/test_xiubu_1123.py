# -*- coding: utf-8 -*-
"""2026-11-23 初稿模式实测后的修补（离线：不连达芬奇、不生图、不调豆包）。run_tests.sh 会跑。
① 该做动画的镜头写了占位图要提醒，全片没有动画要提醒；② 全片假设显示在审片台审核线栏头下；③ 达芬奇被切走、借用库：status 提醒；
④ 审核线连达芬奇认 项目.json 另指的库、切走前留记录；⑤ init 默认片名不再是“自动剪辑”；note 用电脑的钟记反馈；
⑥ 试听直接收带音轨的视频；⑦ check：花字间隔、有花字的句子字幕标蓝、白字压浅色；subs 遇到花字不标蓝；⑧ nav-draft；⑨ 占位图总览。"""
import contextlib, io, json, os, re, shutil, subprocess, sys, tempfile, time, types, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['PYTHONUTF8'] = '1'            # thinkcut_edit.py 一导入就按这个决定要不要重启自己：测试里不让它重启
import thinkcut_edit                                                                              # noqa: E402
from thinkcut import beats, board as B, check as C, draft, resolve, status, subs, workbench as W      # noqa: E402
from thinkcut.common import Job, rj, wj                                                              # noqa: E402
from thinkcut.test_board import img, project                                                         # noqa: E402
os.environ['THINKCUT_NO_OPEN'] = '1'

PAGE = Path(__file__).resolve().parents[2] / 'assets' / '审片工作台' / 'index.html'
SAMPLE = Path(__file__).resolve().parents[4] / 'samples' / '烟道反味'
FF = shutil.which('ffmpeg')


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as o: r = fn(*a, **k)
    return r, o.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.pd, self.job, self.hf = project(self.tmp); self.d = self.job.dir
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)


class AnimateOrPlaceholderTests(Base):
    def test_shots_about_size_position_steps_should_be_animation(self):
        yes = [{'id': 'R1', '占位图': '玄关', '口播要点': ['进门的开关离地一米三左右']}, {'id': 'R2', '占位图': '厨房', '口播要点': ['插座离台面三十公分']},
               {'id': 'R3', '占位图': 'x', '画面': '第一步先刷界面剂'}, {'id': 'R4', '占位图': 'x', '讲什么': '对比', '口播要点': ['两种做法']},
               {'id': 'R5', '占位图': 'x', '口播要点': ['正确的做法是把线管走顶']}]
        no = [{'id': 'R6', '占位图': '刚装修好的客厅一角', '口播要点': ['住进去才发现哪哪都别扭']}, {'id': 'R7', '实拍': 'a.mp4', '口播要点': ['离地一米三']},
              {'id': 'S1', '工程': 'HF_测', '讲什么': '关系', '口播要点': ['离地一米三']}]
        for s in yes: self.assertTrue(draft.should_animate(s), s)
        for s in no: self.assertFalse(draft.should_animate(s), s)
        notes = draft.animation_notes({'shots': yes[:2] + no[:1]})
        self.assertEqual(len(notes), 3); self.assertIn('R1', notes[0]); self.assertIn('要做动画', notes[0]); self.assertIn('没有一个动画镜头', notes[-1])
        self.assertFalse(any('没有一个动画镜头' in x for x in draft.animation_notes({'shots': yes[:1] + no[2:]})))      # 有动画镜头就不报“全片没有”
        self.assertEqual(draft.animation_notes({'shots': no[1:]}), [])

    def test_status_report_warns_and_lists_film_assumptions(self):
        wj(self.d / '分镜规格.json', {'anchors': {}, 'shots': [{'id': 'R1', 'a': 0, 'b': 3, '占位图': '玄关的开关', '口播要点': ['开关离地一米三']}]})
        _, out = quiet(draft.report, Job(self.d))
        self.assertIn('全片 1 个镜头没有一个动画镜头', out); self.assertIn('镜头 R1 讲到“离地”', out); self.assertIn('没有真听', out)
        quiet(draft.assume_cmd, Job(self.d), '画幅你没说，按 3:4 做的'); quiet(draft.assume_cmd, Job(self.d), '画幅你没说，按 3:4 做的')
        quiet(draft.assume_cmd, Job(self.d), '没做动画，因为全片都是真实场景')
        job = Job(self.d); self.assertEqual(draft.assumptions(job), ['画幅你没说，按 3:4 做的', '没做动画，因为全片都是真实场景'])
        self.assertIn('全片假设：画幅你没说', quiet(draft.report, job)[1])
        rows = W.placeholders_of(job)
        self.assertEqual([r['text'] for r in rows if r['kind'] == '假设'], draft.assumptions(job))
        self.assertIn("p.kind==='假设'?'假设':'占位'", PAGE.read_text(encoding='utf-8'))          # 页面上小记号写“假设”
        quiet(draft.assume_cmd, Job(self.d), None, True); self.assertEqual(draft.assumptions(Job(self.d)), [])
        r = subprocess.run([sys.executable, '-B', str(Path(thinkcut_edit.__file__)), 'assume', str(self.d), '画幅按 3:4'], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertEqual(rj(self.d / 'job.json')['assumptions'], ['画幅按 3:4'])


class ResolveSwitchTests(Base):
    def fake(self, now, cur_name, dbs, tree):
        class TL:
            def GetName(s): return '别人的时间线'
        class Proj:
            def __init__(s, n): s.n = n
            def GetName(s): return s.n
            def GetCurrentTimeline(s): return TL()
            def IsRenderingInProgress(s): return False
        class PM:
            def __init__(s): s.db = dict(now); s.cur = Proj(cur_name); s.saved = []
            def GetCurrentDatabase(s): return dict(s.db)
            def GetDatabaseList(s): return [dict(x) for x in dbs]
            def GetCurrentProject(s): return s.cur
            def SaveProject(s): s.saved.append(s.cur.GetName()); return True
            def SetCurrentDatabase(s, db): s.db = dict(db); s.cur = None; return True
            def GotoRootFolder(s): return True
            def GetProjectListInCurrentFolder(s): return tree.get(s.db['DbName'], [])
            def LoadProject(s, n):
                if n in tree.get(s.db['DbName'], []): s.cur = Proj(n); return s.cur
        pm = PM(); return pm, types.SimpleNamespace(GetProjectManager=lambda: pm)

    def test_review_side_honours_borrowed_library_and_records_the_switch(self):
        self.assertEqual(resolve.project_library(self.pd), 'P1')
        meta = rj(self.pd / '项目.json'); meta['resolve'] = {'database': {'DbType': 'Disk', 'DbName': '261118 试验田'}, 'borrowed': {'库': '261118 试验田'}}; wj(self.pd / '项目.json', meta)
        self.assertEqual(resolve.project_library(self.pd), '261118 试验田')
        OWN, OLD = {'DbType': 'Disk', 'DbName': 'P1'}, {'DbType': 'Disk', 'DbName': '261118 试验田'}
        pm, r = self.fake(OLD, '试验田工程', [OWN, OLD], {'P1': [], '261118 试验田': ['测试工程', '试验田工程']})
        keep = resolve.connect, resolve.lock
        resolve.connect = lambda: r; resolve.lock = lambda *a, **k: None
        try: _, _, p = resolve.project(self.job)
        finally: resolve.connect, resolve.lock = keep
        self.assertEqual((pm.db['DbName'], p.GetName(), pm.saved), ('261118 试验田', '测试工程', ['试验田工程']))      # 没去空的自己的库里找，切走前保存了
        log = rj(self.pd / '02_制作' / '达芬奇切换记录.json')
        self.assertEqual((log[0]['原项目库'], log[0]['原工程'], log[0]['原时间线']), ('261118 试验田', '试验田工程', '别人的时间线'))
        notes = draft.resolve_notes(self.job)
        self.assertTrue(any('《试验田工程》' in x and '要回去说一声' in x for x in notes)); self.assertTrue(any('暂存在「261118 试验田」库' in x for x in notes))
        self.assertIn('达芬奇：', quiet(draft.report, self.job)[1])


class TitleAndNoteTests(Base):
    def test_init_default_title_is_the_film_title_not_the_folder_name(self):
        self.assertEqual(thinkcut_edit.default_title(self.d), '旧的标题')                           # 项目.json 的 title
        meta = rj(self.pd / '项目.json'); meta['title'] = ''; wj(self.pd / '项目.json', meta)
        self.assertEqual(thinkcut_edit.default_title(self.d), 'P1')
        named = self.tmp / '261123 开关插座装多高才顺手' / '02_制作' / '自动剪辑'; named.mkdir(parents=True)
        wj(named.parents[1] / '项目.json', {'name': named.parents[1].name, 'title': ''})
        self.assertEqual(thinkcut_edit.default_title(named), '开关插座装多高才顺手')                     # 没有 title：项目文件夹名去掉编号
        lone = self.tmp / '散片'; lone.mkdir(); self.assertEqual(thinkcut_edit.default_title(lone), '散片')

    def test_note_appends_with_the_real_clock(self):
        a = types.SimpleNamespace(dir=str(self.d), step='口播', who='工具拦下', what='deliver 报库不对\n第二行 | 竖线', how='自动借用当前库')
        before = time.strftime('%Y-%m-%d %H:%M'); quiet(thinkcut_edit.cmd_note, a); after = time.strftime('%Y-%m-%d %H:%M')
        line = (self.pd / '02_制作' / '反馈记录.md').read_text(encoding='utf-8').splitlines()[-1]
        self.assertIn(line.split(' | ')[0], (before, after)); self.assertEqual(len(line.split(' | ')), 5); self.assertIn('第二行 ｜ 竖线', line)
        a.who = 'AI'
        with self.assertRaises(SystemExit): quiet(thinkcut_edit.cmd_note, a)


@unittest.skipUnless(FF, '需要 ffmpeg')
class ListenTests(Base):
    def test_listen_card_accepts_a_video_and_pulls_out_its_audio(self):
        mov = self.tmp / '配音.mov'; mute = self.tmp / '没声音.mov'
        subprocess.run([FF, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black:s=64x64:r=10', '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '1', '-c:v', 'mpeg4', '-c:a', 'aac', str(mov)], check=True, stdin=subprocess.DEVNULL)
        subprocess.run([FF, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black:s=64x64:r=10', '-t', '1', '-c:v', 'mpeg4', str(mute)], check=True, stdin=subprocess.DEVNULL)
        it = B.publish(self.job, '口播试听', '机器配音占位', '听听语速', '试听', [mov], draft=True, placeholder=True)
        f = B.latest(it)['files'][0]
        self.assertEqual(f['type'], 'audio'); self.assertTrue(f['name'].endswith('.mp3')); self.assertGreater((B.home(self.job) / f['path']).stat().st_size, 500)
        with self.assertRaisesRegex(B.Oops, '抽不出声音'): B.publish(self.job, '口播试听', 'x', 'y', '试听', [mute])
        with self.assertRaisesRegex(B.Oops, '试听要带音频'): B.publish(self.job, '口播试听', 'x', 'y', '试听', [img(self.tmp / 'a.png')])


class FlowerCheckTests(Base):
    def test_few_flowers_in_an_all_animation_film_is_hinted(self):
        # 2026-11-24 初稿模式第二次实测：38 秒全动画的片子只有 1 个花字；偏少只提醒不拦
        fps = 30000 / 1001
        one = [{'n': 1, 'kind': '文字', 'start': 100, 'end': 160}]
        msg = C.flower_few(one, 1157, fps)
        self.assertIn('至少 2 个', msg); self.assertIn('现在 1 个', msg)
        self.assertIsNone(C.flower_few(one + [{'n': 2, 'kind': '文字', 'start': 700, 'end': 760}], 1157, fps))      # 2 个够了
        self.assertIsNone(C.flower_few([], 600, fps))                                                              # 不到 30 秒的短片不提醒

    def test_spacing_and_blue_subtitles(self):
        fps = 30.0
        rows = [{'n': 1, 'kind': '文字', 'start': 0, 'end': 60}, {'n': 2, 'kind': '文字', 'start': 90, 'end': 150}, {'n': 3, 'kind': '文字', 'start': 140, 'end': 200},
                {'n': 4, 'kind': '卡片', 'bg': True, 'start': 205, 'end': 260}, {'n': 5, 'kind': '人挡字', 'start': 262, 'end': 300}, {'n': 6, 'kind': '文字', 'start': 400, 'end': 460}]
        got = C.flower_spacing(rows, fps)
        self.assertEqual(len(got), 2); self.assertIn('只隔 1.0 秒', got[0][1]); self.assertIn('同时出现', got[1][1])      # 整屏卡片、人挡字不参加；隔够 3 秒的不报
        sub = [{'n': 1, 'start': 10, 'end': 50, 'text': '离地一米三', 'keys': ['一米三']}, {'n': 2, 'start': 61, 'end': 85, 'text': '抬手就能够到', 'keys': ['抬手']},
               {'n': 3, 'start': 100, 'end': 130, 'text': '没有重点词', 'keys': []}]
        blue = C.flower_blue(rows, sub)
        self.assertEqual([x[0] for x in blue], [1]); self.assertIn('重跑 subs', blue[0][1])

    @unittest.skipUnless(FF, '需要 ffmpeg')
    def test_white_text_on_light_picture_is_reported(self):
        def clip(path, bg, alpha=False):
            vf = 'drawbox=x=60:y=100:w=96:h=40:color=white@1.0:t=fill:replace=1'      # replace=1：连透明度一起写成不透明白块（花字的白字）
            src = f'color={bg}:s=216x288:r=30' + (',format=rgba' if alpha else '')
            cmd = [FF, '-v', 'error', '-y', '-f', 'lavfi', '-i', src, '-t', '2', '-vf', vf] if alpha else [FF, '-v', 'error', '-y', '-f', 'lavfi', '-i', src, '-t', '2']
            subprocess.run(cmd + (['-c:v', 'qtrle', '-pix_fmt', 'argb'] if alpha else ['-c:v', 'mpeg4', '-q:v', '2']) + [str(path)], check=True, stdin=subprocess.DEVNULL)
            return path
        flower = clip(self.tmp / '花.mov', 'black@0.0', alpha=True)
        light, dark = clip(self.tmp / '浅.mp4', '0xE8E4DC'), clip(self.tmp / '深.mp4', '0x303030')
        rows = [{'n': 1, 'use': 'number', 'kind': '文字', '类型': '数字强调', 'file': str(flower), 'start': 10, 'end': 50}]
        shots = [{'id': 'R1', 'kind': 'B', 'a': 0, 'b': 2}]
        got = C.flower_contrast(rows, shots, light, 30.0)
        self.assertEqual(len(got), 1); self.assertIn('白字压在浅色画面上', got[0][1]); self.assertIn('anchor', got[0][1])
        self.assertEqual(C.flower_contrast(rows, shots, dark, 30.0), [])
        self.assertEqual(C.flower_contrast(rows, [{'id': 'F_0', 'kind': 'F', 'a': 0, 'b': 2}], light, 30.0), [])       # 露脸段不查
        self.assertEqual(C.flower_contrast([dict(rows[0], kind='卡片')], shots, light, 30.0), [])                      # 带底的卡片不查


@unittest.skipUnless(FF and SAMPLE.exists(), '需要 ffmpeg 和烟道样例')
class SubsBlueTests(unittest.TestCase):
    def test_subtitles_under_a_flower_are_not_coloured(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        exp = rj(SAMPLE / '字幕前12条_期望.json'); d = Path(tmp.name) / 'p'; d.mkdir(); shutil.copy(SAMPLE / '字幕前12条.srt', d / 'x.srt')
        wj(d / 'job.json', dict(rj(SAMPLE / 'job_样例.json'), srt=str(d / 'x.srt'), frames=exp['rows'][-1]['end'] + 10, aroll='', source='', project='', main_timeline=''))
        rows, _ = quiet(subs.render, Job(d)); blue = [r for r in rows if r['keys']]
        self.assertTrue(blue, '样例里要有带蓝色重点词的字幕')
        hit = blue[0]
        wj(d / '合成' / '花字' / '花字表.json', {'items': [{'n': 1, 'use': 'number', 'kind': '文字', 'file': 'x.mov', 'start': hit['start'] + 1, 'end': hit['end'] + 30}]})
        rows2, out = quiet(subs.render, Job(d)); again = {r['n']: r for r in rows2}
        self.assertEqual(again[hit['n']]['keys'], []); self.assertIn('同时有花字，没标蓝', out)
        self.assertEqual(C.flower_blue(rj(d / '合成' / '花字' / '花字表.json')['items'], rows2), [])
        self.assertTrue(C.flower_blue(rj(d / '合成' / '花字' / '花字表.json')['items'], rows))               # 先出字幕后出花字：check 报出来


class TextCardTests(Base):
    def test_text_card_shot_becomes_a_neutral_picture(self):
        # 机器配音的片子里“我是设计师小王”“关注我”这类本该露脸的句子：分镜写 "文字卡"，beats 自动画一张深底白字的卡当画面
        self.job.cfg['placeholder'] = {'kind': '机器配音'}; self.job.save()
        wj(self.d / '分镜规格.json', {'anchors': {}, 'shots': [{'id': 'R1', 'a': '开头', 'b': 20, '占位图': '客厅一角'},
                                                             {'id': 'R9', 'a': 20, 'b': '片尾', '文字卡': ['我是设计师小王', '关注我，装修少踩坑']}]})
        _, out = quiet(beats.run, Job(self.d))
        sh = {s['id']: s for s in rj(self.d / '分镜.json')['shots']}
        self.assertEqual((sh['R1']['a'], sh['R9']['b']), (0.0, round(900 / (30000 / 1001), 3)))          # "开头"、"片尾" 不用抄秒数
        self.assertEqual(sh['R9']['图片'], {'文件': ['实拍/文字卡/R9.png'], '铺法': '裁满'}); self.assertEqual(sh['R9']['文字卡'], ['我是设计师小王', '关注我，装修少踩坑'])
        card = self.d / '实拍' / '文字卡' / 'R9.png'
        from PIL import Image
        im = Image.open(card); self.assertEqual(im.size, tuple(Job(self.d).fmt['card'][2:]))
        px = im.convert('RGB'); self.assertEqual(px.getpixel((5, 5)), draft.CARD_BG); self.assertGreater(len(set(px.getdata())), 2)      # 深底，上面有字
        self.assertIn('文字卡 1 张', out); self.assertNotIn('R9', ' '.join(x['id'] for x in draft.placeholder_shots(Job(self.d))))
        t0 = card.stat().st_mtime_ns; _, out2 = quiet(beats.run, Job(self.d)); self.assertEqual(card.stat().st_mtime_ns, t0); self.assertNotIn('文字卡 1 张', out2)   # 字没变不重画
        spec = rj(self.d / '分镜规格.json'); spec['shots'][1]['图片'] = 'a.png'; wj(self.d / '分镜规格.json', spec)
        with self.assertRaisesRegex(SystemExit, '写了 文字卡 就不要再写'): quiet(beats.run, Job(self.d))


class DefaultFormatTests(unittest.TestCase):
    def test_init_defaults_to_full_screen_3x4(self):
        # 2026-11-24 用户定：没说画幅就用 3:4满屏（真片子 261114 起都是；机器配音没人脸也一样）
        # 抽配置后默认画幅不写在程序里，从默认 IP 的配置读（配置档/<IP>/ip.json）
        from thinkcut.common import ip_config
        self.assertEqual(ip_config(Path(tempfile.mkdtemp())).get('剪辑.画幅'), '3:4满屏')


class NavDraftTests(Base):
    def test_one_command_writes_a_valid_navigation_project(self):
        srt = self.d / 'x.srt'; srt.write_text('1\n00:00:00,000 --> 00:00:02,000\n你好\n\n2\n00:00:04,000 --> 00:00:06,000\n说正题\n\n3\n00:00:21,000 --> 00:00:23,000\n再见\n', encoding='utf-8')
        self.job.cfg['srt'] = str(srt); self.job.save(); job = Job(self.d)
        text = ''.join(t['c'] for t in rj(self.d / '口播字时间.json')['tokens']); phrase = text[6:9]
        (self.d / '导航' / 'project.json').unlink()                                    # 夹具里带了一份别的片子的导航：这里从没有开始
        with self.assertRaisesRegex(SystemExit, '短标签@从哪开始'): quiet(status.nav_draft, job, None)
        out, said = quiet(status.nav_draft, job, ['开场@1.5', f'正题@{phrase}', '结尾@20'])
        p = rj(out); segs = p['segments']; dur = round(900 / (30000 / 1001), 6)
        self.assertEqual([s['label'] for s in segs], ['开场', '正题', '结尾']); self.assertEqual(segs[0]['start'], 0); self.assertIn('已改成从 0 开始', said)
        self.assertTrue(all(a['end'] == b['start'] for a, b in zip(segs, segs[1:]))); self.assertEqual(segs[-1]['end'], dur)
        self.assertEqual((p['target'], p['coverage'], p['duration']), ({'width': job.W, 'height': job.H, 'nav_height': job.fmt['nav'][3], 'fps': '30000/1001'}, [0, dur], dur))
        import hashlib
        self.assertEqual(p['source'], {'subtitles': str(srt), 'sha256': hashlib.sha256(srt.read_bytes()).hexdigest()})
        self.assertIn('validate', said); self.assertIn('render', said)
        # 2026-11-24 初稿模式第二次实测：开发版 worktree 里打印的是使用版的路径。打印的程序路径要是“它自己所在仓库”里的那一份（按自身位置找，不写死）
        here = Path(status.__file__).resolve().parents[5]
        self.assertEqual(status.THINKCUT_DIR, here); self.assertIn(str(here / '章节导航' / 'scripts' / 'navigation.py'), said)
        with self.assertRaisesRegex(SystemExit, '已经有了'): quiet(status.nav_draft, job, ['开场@0'])
        quiet(status.nav_draft, job, ['开场@0', '结尾@20'], True); self.assertEqual(len(rj(out)['segments']), 2)
        self.assertEqual(len(list(out.parent.glob('project.旧_*.json'))), 1)
        with self.assertRaisesRegex(SystemExit, '找不到'): quiet(status.nav_draft, job, ['开场@0', '乱写@口播里没有的话'], True)
        with self.assertRaisesRegex(SystemExit, '按先后顺序'): quiet(status.nav_draft, job, ['开场@0', '二@20', '三@10'], True)
        nav = Path(os.environ.get('THINKCUT_NAV') or here / '章节导航' / 'scripts' / 'navigation.py')
        if nav.is_file():      # 章节导航程序在就真校验一遍（它只读这个文件）
            r = subprocess.run([sys.executable, '-B', str(nav), 'validate', str(out)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


@unittest.skipUnless(FF, '需要 ffmpeg')
class OverviewTests(Base):
    def test_placeholder_images_are_tiled_into_one_overview(self):
        wj(self.d / '分镜规格.json', {'anchors': {}, 'shots': [{'id': 'R1', 'a': 0, 'b': 3, '占位图': '客厅'}, {'id': 'R2', 'a': 3, 'b': 6, '占位图': ['白墙 A', '白墙 B']}]})
        self.assertIsNone(draft.overview(self.job))                                            # 还没有图
        for x in draft.placeholder_shots(self.job):
            for p in x['文件']: img(p)
        out = draft.overview(self.job)
        self.assertTrue(out and out.is_file() and out.name == '总览.jpg')
        order = out.with_name('总览_顺序.txt').read_text(encoding='utf-8')
        self.assertIn('1. R1', order); self.assertIn('3. R2', order)
        self.assertEqual([x['id'] for x in draft.placeholder_shots(self.job)], ['R1', 'R2'])       # 总览不算占位图


if __name__ == '__main__':
    unittest.main()
