# -*- coding: utf-8 -*-
"""261114 新流程口播反馈单（2026-11-27）离线回归：
新1 剪口播交付后一条命令修音（aroll-polish：主线副本上做，不要动画项目、主线不动）；
新2 修音后分段响度补齐、底噪提示；新3 审片台口播精剪栏跟着命令点名的剪口播工程。"""
import subprocess, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np                                                       # noqa: E402
from thinkcut import resolve, voice, workbench, koubo                    # noqa: E402
from thinkcut.common import Job, wj, rj                                  # noqa: E402
from thinkcut.test_tools_1121 import FakeResolve, FTL, quiet, speech, FPS   # noqa: E402


def uneven_speech(path, seconds=60, quiet_from=20, quiet_to=30, drop_db=6):
    """像口播的测试音，其中一段整体小 drop_db（原片收音时离麦远了，261114 的 48–60 秒那种）。"""
    sr = 48000; rng = np.random.default_rng(3); t = np.arange(int(seconds * sr)) / sr
    f0 = 160 + 30 * np.sin(2 * np.pi * 0.7 * t)
    v = sum(np.sin(2 * np.pi * np.cumsum(f0 * k) / sr) / k for k in range(1, 18))
    syl = ((t % 0.32) < 0.24) & ((t % 4.0) < 3.0)
    v = 0.08 * v * np.convolve(syl.astype(float), np.ones(480) / 480, 'same')
    v[(t >= quiet_from) & (t < quiet_to)] *= 10 ** (-drop_db / 20)
    v = v + rng.normal(0, 10 ** (-80 / 20), len(t))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(sr), '-ac', '2', '-i', '-', '-c:a', 'pcm_s24le', str(path)],
                   input=np.stack([v, v], 1).astype(np.float32).tobytes(), check=True)
    return path


class SegmentLoudnessTests(unittest.TestCase):
    def test_quiet_stretch_is_evened_out_on_the_polished_track(self):
        with tempfile.TemporaryDirectory() as t:
            src = uneven_speech(Path(t) / '隔离.wav')
            before = voice.uneven(voice.segments(voice.momentary(src)))
            self.assertEqual([(a, b) for a, b, _ in before], [(20.0, 30.0)])
            notes = []; _, lufs, pk, _ = voice.polish(src, Path(t) / '修音.wav', notes=notes)
            after = voice.uneven(voice.segments(voice.momentary(Path(t) / '修音.wav')))
            self.assertEqual(after, [])                                  # 补完和中位数差都 ≤ 1.5 LU
            self.assertTrue(any(n.startswith('分段补齐：20–30 秒') for n in notes), notes)
            self.assertLess(abs(lufs - voice.TARGET_LUFS), 1.0); self.assertLessEqual(pk, -1.0)
            self.assertFalse(list(Path(t).glob('*_补段.wav')) + list(Path(t).glob('*_未定响度.wav')))

    def test_even_speech_is_not_touched(self):
        rows = [(k * 10.0, k * 10.0 + 10, -20.0 + (0.5 if k % 2 else 0)) for k in range(6)] + [(60.0, 64.0, None)]
        self.assertEqual(voice.gain_curve(rows), [])

    def test_floor_hint_does_not_suggest_amount_when_gain_lifted_the_noise(self):
        self.assertNotIn('--amount', voice.floor_hint(16.9, 80))       # 261114：整体 +16.9 dB，70、80 都是 -54
        self.assertIn('--amount 80', voice.floor_hint(4.0, 70))


class NamedTL(FTL):
    def __init__(s, name, n, tracks): super().__init__(n, tracks); s.name = name
    def GetName(s): return s.name


class ArollResolve(FakeResolve):
    """假达芬奇：工程里只有剪口播交付的主线；能复制时间线。"""
    def __init__(s, n, d, main):
        super().__init__(n, d)
        s.lines = [NamedTL(main, n, s.tl.tracks['audio'])]; s.tl = s.lines[0]
    def GetTimelineCount(s): return len(s.lines)
    def GetTimelineByIndex(s, i): return s.lines[i - 1]


def delivered(tmp, main='v4_主线_1127-1211_e67a', frames=None):
    """剪口播工程（已交付到达芬奇）：项目/02_制作/<片名>_AI剪辑。"""
    frames = frames or round(40 * FPS)
    root = Path(tmp) / '项目'; (root / '01_原始素材').mkdir(parents=True); wj(root / '项目.json', {'name': '261114 测试片'})
    a = root / '02_制作' / '口播_AI剪辑'; out = a / '交付'; out.mkdir(parents=True); (out / f'{main}.srt').write_text('1\n')
    wj(a / '审核' / 'latest_delivery.json', {'status': 'complete', 'output': str(out), 'project': '261114 测试片', 'timeline': main, 'frames': frames})
    wj(a / '审核' / 'project_runtime.json', {'source': '/x/原片.mov', 'fps': [30000, 1001], 'resolution': [2160, 3840], 'speed': 1.2})
    for name in ('review_v2_state.json',): wj(a / '审核' / name, {})
    return root, a


class ArollPolishTests(unittest.TestCase):
    def test_one_command_polishes_a_copy_of_the_main_line(self):
        with tempfile.TemporaryDirectory() as t:
            root, a = delivered(t)
            R = ArollResolve(round(40 * FPS), a, 'v4_主线_1127-1211_e67a')
            def duplicate(name):
                copy = NamedTL(name, R.n, [dict(x, items=list(x['items'])) for x in R.lines[0].tracks['audio']]); R.lines.append(copy); R.tl = copy; return copy
            R.lines[0].DuplicateTimeline = duplicate
            old = resolve.project; resolve.project = lambda job: (R, R, R); self.addCleanup(setattr, resolve, 'project', old)
            out, said = quiet(voice.aroll_cmd, a)
            self.assertEqual([x.name for x in R.lines], ['v4_主线_1127-1211_e67a', 'v4_主线_1127-1211_e67a_修音'])
            main, copy = R.lines
            self.assertTrue(main.tracks['audio'][0]['enabled']); self.assertEqual(len(main.tracks['audio']), 3)     # 主线不动
            self.assertFalse(copy.tracks['audio'][0]['enabled']); self.assertEqual(copy.tracks['audio'][-1]['name'], resolve.POLISH_TRACK)
            j = rj(a / '修音' / 'job.json')
            self.assertEqual((j['main_timeline'], j['review_timeline'], j['aroll']), ('v4_主线_1127-1211_e67a', 'v4_主线_1127-1211_e67a_修音', str(a.resolve())))
            self.assertEqual(j['voice_polish']['file'], str(out)); self.assertIn('gain_db', j['voice_polish'])
            self.assertFalse((root / '02_制作' / '自动剪辑').exists())     # 不要动画项目
            quiet(voice.aroll_cmd, a)                                       # 再跑：用已有的副本，不再复制
            self.assertEqual(len(R.lines), 2)


class ChosenArollTests(unittest.TestCase):
    def test_serve_on_a_new_cut_shows_it_and_leaves_job_json_alone(self):
        with tempfile.TemporaryDirectory() as t:
            root, new = delivered(t)
            old = root / '02_制作' / '占位口播_AI剪辑'; (old / '审核').mkdir(parents=True)
            for f in ('review_v2_state.json', 'project_runtime.json'): wj(old / '审核' / f, {})
            film = root / '02_制作' / '自动剪辑'; film.mkdir()
            wj(film / 'job.json', {'format': '3:4', 'frames': 100, 'main_timeline': 'v2', 'source': '/x', 'project': 'p', 'aroll': str(old)})
            job, said = quiet(workbench.open_film, new)                     # edit_project.py serve --project-dir <新剪口播工程>
            self.assertEqual(workbench.aroll_of(job), new.resolve())
            self.assertEqual(rj(film / 'job.json')['aroll'], str(old))       # 动画项目不动
            self.assertIn('init', said)
            self.assertEqual(workbench.aroll_of(quiet(workbench.open_film, film)[0]), new.resolve())   # 之后直接开项目也还是新剪的
            quiet(workbench.open_film, old)                                  # 点名旧的就换回旧的
            self.assertEqual(workbench.aroll_of(Job(film)), old.resolve())


class PlaceholderToRealTests(unittest.TestCase):
    """261114（2026-11-27 用户）：项目先用占位口播做了动画，后来剪好了真口播，审片台口播精剪栏还是占位的灰人形。"""
    def project(self, t, src='/y/A-roll占位_TTS.mov', deliver=True):
        root, real = delivered(t)
        if not deliver: (real / '审核' / 'latest_delivery.json').unlink()
        ph = root / '02_制作' / '占位口播_AI剪辑'
        wj(ph / '审核' / 'project_runtime.json', {'source': src, 'fps': [30000, 1001]}); wj(ph / '审核' / 'review_v2_state.json', {})
        film = root / '02_制作' / '自动剪辑'; film.mkdir()
        wj(film / 'job.json', {'format': '3:4满屏', 'fps': '30000/1001', 'frames': 100, 'main_timeline': 'v2_主线', 'source': src, 'project': 'p',
                               'aroll': str(ph), 'review_timeline': '审核线_v2_占位口播', 'subtitle_keywords': ['泡沫砖']})
        # 真口播的字：“真的”在成片 2 秒起（原片 5 秒处，前面剪掉 3 秒）；占位口播的旧字时间说在 0 秒
        wj(real / '审核' / 'trial_keep_segments.json', {'segments': [{'src_in': 150, 'src_out': 300, 'tl_in': 60}], 'retained_token_ids': [1, 2]})
        wj(real / '审核' / 'character_alignment.json', {'tokens': [{'id': 1, 'unit_id': 0, 'text': '真', 'start_ms': 5005, 'end_ms': 5200},
                                                                   {'id': 2, 'unit_id': 0, 'text': '的', 'start_ms': 5200, 'end_ms': 5400}]})
        wj(film / '口播字时间.json', {'fps': '30000/1001', 'tokens': [{'u': 0, 'c': '真', 't0': 0.0, 't1': 0.2}, {'u': 0, 'c': '的', 't0': 0.2, 't1': 0.4}]})
        return film, ph, real.resolve()

    def test_switching_aroll_recomputes_word_times(self):
        with tempfile.TemporaryDirectory() as t:
            film, ph, real = self.project(t)
            quiet(workbench.follow_real, Job(film))
            tok = rj(film / '口播字时间.json')['tokens']
            self.assertEqual([x['c'] for x in tok], ['真', '的']); self.assertAlmostEqual(tok[0]['t0'], 2.0, places=1)
            self.assertEqual(rj(next(film.glob('口播字时间.备份_*.json')))['tokens'][0]['t0'], 0.0)    # 旧的留备份

    def test_stale_subtitle_table_does_not_add_commas(self):
        with tempfile.TemporaryDirectory() as t:
            film, ph, real = self.project(t)
            subs = film / '合成' / '字幕' / '字幕表.json'; subs.parent.mkdir(parents=True)
            wj(subs, [{'start': 0, 'end': 3, 'text': '真'}, {'start': 6, 'end': 12, 'text': '的'}])      # 旧口播的换条处：“真，的”
            import os, time as _t
            os.utime(subs, (_t.time() - 60, _t.time() - 60))
            self.assertEqual(workbench.script(Job(film))[1][0]['text'], '真的。')
            os.utime(subs, None)                                             # 字幕表比字时间新：照旧补逗号
            self.assertEqual(workbench.script(Job(film))[1][0]['text'], '真，的。')

    def test_placeholder_is_recognised(self):
        with tempfile.TemporaryDirectory() as t:
            _, ph, real = self.project(t)
            self.assertTrue(workbench.placeholder_aroll(ph)); self.assertFalse(workbench.placeholder_aroll(real))
            wj(ph / '审核' / 'project_runtime.json', {'source': '/y/口播.mov', 'placeholder': {'kind': '机器配音'}})
            self.assertTrue(workbench.placeholder_aroll(ph))

    def test_desk_shows_the_real_cut_before_it_is_delivered(self):
        with tempfile.TemporaryDirectory() as t:
            film, ph, real = self.project(t, deliver=False)
            job = Job(film)
            self.assertEqual(workbench.aroll_of(job), real)
            self.assertFalse(quiet(workbench.follow_real, job)[0])          # 没交付：init 读不到交付记录，动画项目先不换
            self.assertEqual(rj(film / 'job.json')['aroll'], str(ph))

    def test_delivered_real_cut_takes_over_the_project(self):
        with tempfile.TemporaryDirectory() as t:
            film, ph, real = self.project(t)
            wj(film / '审片工作台' / '口播工程.json', {'aroll': str(ph)})
            job = Job(film)
            changed, said = quiet(workbench.follow_real, job)
            self.assertTrue(changed); self.assertIn('换成了', said)
            j = rj(film / 'job.json')
            self.assertEqual((j['aroll'], j['main_timeline']), (str(real), 'v4_主线_1127-1211_e67a'))
            self.assertEqual(j['subtitle_keywords'], ['泡沫砖']); self.assertNotIn('review_timeline', j)       # 手填的带过来；占位口播的审核线不带
            self.assertEqual(len(list(film.glob('job.备份_*.json'))), 1)
            self.assertEqual(job['aroll'], str(real)); self.assertEqual(workbench.aroll_of(job), real)
            self.assertEqual(rj(film / '审片工作台' / '口播工程.json')['aroll'], str(real))
            self.assertFalse(quiet(workbench.follow_real, job)[0])          # 再调不再 init
            self.assertEqual(len(list(film.glob('job.备份_*.json'))), 1)

    def test_other_process_switched_first_only_reloads(self):
        with tempfile.TemporaryDirectory() as t:
            film, ph, real = self.project(t)
            stale = Job(film); quiet(workbench.follow_real, Job(film))      # 首页那边先换了
            self.assertTrue(quiet(workbench.follow_real, stale)[0])         # 在跑的服务：只重读
            self.assertEqual(stale['aroll'], str(real)); self.assertEqual(len(list(film.glob('job.备份_*.json'))), 1)

    def test_real_project_is_left_alone(self):
        with tempfile.TemporaryDirectory() as t:
            film, ph, real = self.project(t, src='/y/口播_第一次.mov')       # 当前用的不是占位：两条都是真口播，不替用户挑
            self.assertFalse(quiet(workbench.follow_real, Job(film))[0])
            self.assertEqual(rj(film / 'job.json')['aroll'], str(ph)); self.assertEqual(workbench.aroll_of(Job(film)), ph)


if __name__ == '__main__':
    unittest.main()
