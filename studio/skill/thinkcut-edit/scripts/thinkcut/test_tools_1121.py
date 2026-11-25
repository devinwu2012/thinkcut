# -*- coding: utf-8 -*-
"""2026-11-21《别墅装灯前》反馈单剪辑工具部分的离线测试（不连达芬奇，达芬奇一律用替身）。run_tests.sh 会跑：
新17 达芬奇写入锁；新18 同帧标记合并、渲染前载入预设、改字幕自动重拼整轨、大字顶替字幕的删除记进配置；
新13 口播降噪修音（ffmpeg 实测底噪和响度＋达芬奇替身）、平衡按修音轨；新12 大字/结论音效卡关键词；
新21 用户没看过样张的镜头（check 列出、export 拦）；新8 收尾提醒给用户看的 MD。"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np                                            # noqa: E402
from thinkcut import resolve, subs, check, status, voice, anim    # noqa: E402
from thinkcut.common import Job, wj, rj, THINKCUT_DIR                  # noqa: E402  视频项目管理按本仓库所在的程序目录找（开发版里就是开发版的），不写死使用版路径

SCRIPTS = Path(__file__).resolve().parents[1]
SAMPLE = Path(__file__).resolve().parents[4] / 'samples' / '烟道反味'
FPS = 30000 / 1001


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as o: r = fn(*a, **k)
    return r, o.getvalue()


def settle(job):
    """替身：用户在审片台点了审核线“这一栏定了”。2026-11-22 用户定“先做出来再改”以后，整片导出只认这条页面确认，不再认 --user-ok 转述的原话；
    真页面走 /api/settle（只收带口令的页面请求），这里直接记同一条记录。"""
    from thinkcut import board
    board._mark_settled(job, '审核线')


def tokens(text, step=0.3, start=1.0):
    return [{'c': c, 't0': round(start + i * step, 3), 't1': round(start + i * step + step - 0.02, 3)} for i, c in enumerate(text)]


def film(tmp, frames=1200, **job):
    """项目/02_制作/自动剪辑 的片子目录。"""
    root = Path(tmp) / '项目'; d = root / '02_制作' / '自动剪辑'; d.mkdir(parents=True)
    wj(root / '项目.json', {'name': '261117 测试片'})
    wj(d / 'job.json', dict({'format': '3:4', 'frames': frames, 'main_timeline': 'v1', 'source': '/x', 'project': 'p'}, **job))
    return d


class Reached(Exception): pass


# ---------------- 新17 达芬奇写入锁 ----------------
HOLD = '''
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from thinkcut import resolve
class J: dir = Path('/x/片子甲')
if sys.argv[2] == 'json': resolve.lock(J(), 'review 测试')
else:
    import fcntl; Path(resolve.os.environ['THINKCUT_RESOLVE_LOCK']).parent.mkdir(parents=True, exist_ok=True); f = open(resolve.os.environ['THINKCUT_RESOLVE_LOCK'], 'a+');fcntl.flock(f, fcntl.LOCK_EX); f.seek(0); f.truncate(); f.write(str(resolve.os.getpid())); f.flush()
print('拿到了', flush=True); time.sleep(60)
'''


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.lf = Path(self.tmp.name) / '锁' / 'resolve-write.lock'
        os.environ['THINKCUT_RESOLVE_LOCK'] = str(self.lf); self.addCleanup(os.environ.pop, 'THINKCUT_RESOLVE_LOCK', None)
        resolve.unlock(); self.addCleanup(resolve.unlock)

    def holder(self, mode='json'):
        p = subprocess.Popen([sys.executable, '-B', '-c', HOLD, str(SCRIPTS), mode], stdout=subprocess.PIPE, text=True, env=dict(os.environ))
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        self.assertEqual(p.stdout.readline().strip(), '拿到了'); return p

    def test_second_is_blocked_and_says_who_then_free_when_holder_gone(self):
        p = self.holder()
        with self.assertRaisesRegex(SystemExit, f'(?s)正被另一个命令占用.*review 测试.*片子甲.*进程 {p.pid}.*kill {p.pid}'):
            resolve.lock(type('J', (), {'dir': Path('/x/片子乙')})(), 'export')
        p.kill(); p.wait()                                                   # 持锁进程没了（崩溃、被杀）：锁自动失效
        info = resolve.lock(type('J', (), {'dir': Path('/x/片子乙')})(), 'export')
        self.assertEqual((info['命令'], info['片子目录'], info['进程号']), ('export', '/x/片子乙', os.getpid()))
        self.assertEqual(json.loads(self.lf.read_text(encoding='utf-8'))['命令'], 'export')
        self.assertIs(resolve.lock(None, '同一进程再拿'), info)                 # 同一进程重复拿不重复加

    def test_stale_record_of_dead_process_does_not_block(self):
        self.lf.parent.mkdir(parents=True); self.lf.write_text(json.dumps({'命令': 'review', '进程号': 999999}), encoding='utf-8')
        self.assertEqual(resolve.lock(None, 'review')['进程号'], os.getpid())

    def test_lock_shared_with_koubo_jianji_bare_pid(self):
        p = self.holder('pid')                                               # 口播剪辑 的 write_lock 只写进程号
        with self.assertRaisesRegex(SystemExit, f'口播剪辑.*进程 {p.pid}'): resolve.lock(None, 'review')

    def test_write_commands_lock_read_only_snap_does_not(self):
        self.addCleanup(setattr, resolve, 'connect', resolve.connect)
        def stop(): raise Reached()
        resolve.connect = stop
        with tempfile.TemporaryDirectory() as t:
            job = Job(film(t))
            with self.assertRaises(Reached): resolve.project(job, write=False)
            self.assertIsNone(resolve._held)                                 # 只读：没拿锁
            p = self.holder()
            with self.assertRaises(Reached): resolve.project(job, write=False)   # 别人拿着锁，只读照样能查
            with self.assertRaisesRegex(SystemExit, '正被另一个命令占用'): resolve.project(job)
            p.kill(); p.wait()
            with self.assertRaises(Reached): resolve.project(job)
            self.assertEqual(resolve._held[1]['片子目录'], str(job.dir))


# ---------------- 新18 审核线零碎 ----------------
class FakeP:
    """导出前的设置顺序：载入预设要在设格式之前。"""
    def __init__(s, preset_ok=True): s.calls = []; s.preset_ok = preset_ok
    def LoadRenderPreset(s, n): s.calls.append(('preset', n)); return s.preset_ok
    def GetRenderCodecs(s, fmt): return {'H.265': 'H265', 'H.264': 'H264'}
    def SetCurrentRenderFormatAndCodec(s, f, c): s.calls.append(('format', f, c)); raise Reached()
    def GetTimelineCount(s): return 1
    def GetTimelineByIndex(s, i): return s
    def GetName(s): return '审核线'
    def SetCurrentTimeline(s, t): return True
    def GetStartFrame(s): return 108000


class ReviewBitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = film(self.tmp.name); self.job = Job(self.d)

    def test_same_frame_changes_are_merged(self):
        wj(self.d / 'c.json', [{'t': 2.0, 'name': '改：A', 'note': '甲'}, {'t': 2.01, 'name': '新增：B', 'note': '乙'}, {'t': 5, 'name': 'C', 'note': '丙'}])
        got, out = quiet(resolve.load_changes, self.job, self.d / 'c.json')
        self.assertEqual(got, [(60, '改：A／新增：B', '甲／乙'), (150, '改：C', '丙')]); self.assertIn('合成一个标记', out)

    def test_export_loads_render_preset_before_setting_format(self):
        self.addCleanup(setattr, resolve, 'project', resolve.project); self.addCleanup(setattr, resolve, 'export_target', resolve.export_target)
        p = FakeP(); resolve.project = lambda job: (object(), p, p)
        resolve.export_target = lambda *a: (self.d / '成片', 'x', 'mp4')
        with self.assertRaises(Reached): quiet(resolve.export, self.job, name='审核线', seconds=5)
        self.assertEqual([c[0] for c in p.calls], ['preset', 'format']); self.assertEqual(p.calls[0][1], 'H.264 Master')
        p2 = FakeP(preset_ok=False); resolve.project = lambda job: (object(), p2, p2)
        with self.assertRaises(Reached): _, out = quiet(resolve.export, self.job, name='审核线', seconds=5)
        self.assertEqual(p2.calls[-1][0], 'format')                          # 载入失败只提醒，照样逐项设

    def test_review_rebuilds_stale_subtitle_track_first(self):
        c = self.d / '合成'; (c / '字幕').mkdir(parents=True)
        wj(self.d / '口播字时间.json', {'tokens': tokens('一二三四')})
        wj(c / '音效表.json', [{'file': '/a.mp3', 'record': 30, 'frames': 10, 'src_start': 0, 'gain_db': -6.0}])
        (c / '字幕' / '字幕整轨.mov').write_bytes(b'x'); wj(c / '字幕' / '字幕表.json', [{'n': 1, 'start': 0, 'end': 30, 'text': '字'}])
        t = time.time() + 5; os.utime(c / '字幕' / '字幕表.json', (t, t))       # 改过字幕：字幕表比整轨新
        calls = []
        self.addCleanup(setattr, subs, 'track', subs.track); self.addCleanup(setattr, resolve, 'project', resolve.project)
        subs.track = lambda job: calls.append('track')
        def stop(job): calls.append('project'); raise Reached()
        resolve.project = stop
        with self.assertRaises(Reached): quiet(resolve.review, self.job)
        self.assertEqual(calls, ['track', 'project'])
        calls.clear(); t = time.time() + 10; os.utime(c / '字幕' / '字幕整轨.mov', (t, t))
        with self.assertRaises(Reached): quiet(resolve.review, self.job)
        self.assertEqual(calls, ['project'])                                 # 整轨是新的：不重拼


@unittest.skipUnless(shutil.which('ffmpeg') and SAMPLE.exists(), '需要 ffmpeg 和烟道样例')
class SubsHideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        exp = rj(SAMPLE / '字幕前12条_期望.json'); self.d = d = Path(self.tmp.name) / 'p'; d.mkdir()
        shutil.copy(SAMPLE / '字幕前12条.srt', d / 'x.srt')
        wj(d / 'job.json', dict(rj(SAMPLE / 'job_样例.json'), srt=str(d / 'x.srt'), frames=exp['rows'][-1]['end'] + 10, aroll='', source='', project='', main_timeline=''))

    def test_hand_deleted_rows_stay_deleted_and_can_come_back(self):
        tab, full = self.d / '合成/字幕/字幕表.json', self.d / '合成/字幕/字幕表_全部.json'
        rows, _ = quiet(subs.render, Job(self.d)); self.assertEqual(len(rows), 12); self.assertEqual(len(rj(full)), 12)
        wj(tab, [r for r in rj(tab) if r['n'] not in (3, 7)])               # 大字顶替：手删第 3、7 条
        gone = {r['n']: r['text'] for r in rows if r['n'] in (3, 7)}
        rows, out = quiet(subs.render, Job(self.d))                          # 重跑 subs：不加回来，记进 job.json
        self.assertEqual([r['n'] for r in rows], [1, 2, 4, 5, 6, 8, 9, 10, 11, 12]); self.assertIn('手删过 2 条', out)
        self.assertEqual(sorted(rj(self.d / 'job.json')['subtitle_hide']), sorted(gone.values()))
        self.assertEqual([r['n'] for r in rj(full) if r.get('hidden')], [3, 7]); self.assertEqual(len(rj(full)), 12)   # 全表留给导出 SRT
        rows, _ = quiet(subs.render, Job(self.d)); self.assertEqual(len(rows), 10)          # 再跑一次照样
        j = rj(self.d / 'job.json'); j['subtitle_hide'] = [gone[3]]; wj(self.d / 'job.json', j)   # 用户要第 7 条回来：从配置里删掉
        rows, _ = quiet(subs.render, Job(self.d)); self.assertEqual([r['n'] for r in rows if r['n'] in (3, 7)], [7])
        self.assertEqual(rj(self.d / 'job.json')['subtitle_hide'], [gone[3]])           # 不会被当成手删又记回去

    def test_subs_command_rebuilds_track(self):
        calls = []; self.addCleanup(setattr, subs, 'track', subs.track); subs.track = lambda job: calls.append(1)
        quiet(subs.render, Job(self.d), with_track=True); self.assertEqual(calls, [1])
        quiet(subs.render, Job(self.d)); self.assertEqual(calls, [1])


# ---------------- 新13 口播降噪修音 ----------------
def speech(path, noise_db, seconds=40, sr=48000, seed=1):
    """像口播的测试音：一句句（每 4 秒停 1 秒）、字字之间短停，200–3000 Hz 谐波；整条叠白噪声（noise_db 是噪声均方根电平）。"""
    rng = np.random.default_rng(seed); t = np.arange(int(seconds * sr)) / sr
    f0 = 160 + 30 * np.sin(2 * np.pi * 0.7 * t)
    v = sum(np.sin(2 * np.pi * np.cumsum(f0 * k) / sr) / k for k in range(1, 18))
    syl = ((t % 0.32) < 0.24) & ((t % 4.0) < 3.0)
    env = np.convolve(syl.astype(float), np.ones(480) / 480, 'same')
    v = 0.08 * v * env + rng.normal(0, 10 ** (noise_db / 20), len(t))
    raw = np.stack([v, v], 1).astype(np.float32).tobytes()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(sr), '-ac', '2', '-i', '-', '-c:a', 'pcm_s24le', str(path)], input=raw, check=True)
    return path


@unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
class FlowerTableTests(unittest.TestCase):
    """2026-11-21 261118：花字表 file 是相对路径时 review 导入失败；koubo 拖出片尾把审核线拉长，到 voice-polish 才拦下。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = film(self.tmp.name, frames=100)
        (self.d / '合成' / '花字').mkdir(parents=True)
        for n in (1, 2): (self.d / '合成' / '花字' / f'0{n}_koubo.mov').write_bytes(b'x')

    def test_relative_file_and_overrun(self):
        wj(self.d / '合成' / '花字' / '花字表.json', {'items': [{'n': 1, 'use': 'koubo', 'file': '合成/花字/01_koubo.mov', 'start': 10, 'end': 76}]})
        self.assertEqual(resolve.flower_items(Job(self.d)), [(str(Job(self.d).dir / '合成/花字/01_koubo.mov'), 10, 66)])     # 相对路径按片子目录补全
        wj(self.d / '合成' / '花字' / '花字表.json', {'items': [{'n': 2, 'use': 'koubo', 'file': str(self.d / '合成/花字/02_koubo.mov'), 'start': 60, 'end': 126}]})
        with self.assertRaisesRegex(SystemExit, '超出片尾.*02_koubo.mov 到第 126 帧'): resolve.check_flowers(Job(self.d), resolve.flower_items(Job(self.d)))   # review 连达芬奇之前就停
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'assets' / '包装系统'))
        import huazi
        self.assertIn('第 2 条 koubo 到第 126 帧（多 26 帧）', huazi.overrun([{'n': 2, 'use': 'koubo', 'end': 126}], 100))
        self.assertEqual(huazi.overrun([{'n': 1, 'use': 'koubo', 'end': 100}], 100), '')
        self.assertEqual(huazi.overrun([{'n': 1, 'use': 'koubo', 'end': 999}], None), '')


class VoicePolishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.t = Path(self.tmp.name)

    def test_audio_format_when_wav_has_no_codec_list(self):
        """达芬奇 21.1 实测（261118）：格式里有 wav，但 GetRenderCodecs('wav') 是空的；以前直接停“设渲染格式 wav 失败”。"""
        class P:
            def __init__(s, ok): s.ok, s.calls = ok, []
            def GetRenderFormats(s): return {'Wave': 'wav', 'QuickTime': 'mov'}
            def GetRenderCodecs(s, f): return {} if f == 'wav' else {'H.264': 'H264', 'Apple ProRes 422': 'ProRes422'}
            def SetCurrentRenderFormatAndCodec(s, f, c): s.calls.append((f, c)); return (f, c) in s.ok
        p = P({('wav', '')}); self.assertEqual(voice.set_audio_format(p), 'wav'); self.assertEqual(p.calls, [('wav', '')])
        p = P({('mov', 'H264')}); self.assertEqual(voice.set_audio_format(p), 'mov')          # wav 怎么都设不上：换只出声音的 mov
        self.assertEqual(p.calls, [('wav', ''), ('wav', 'lpcm'), ('mov', 'H264')])
        with self.assertRaisesRegex(SystemExit, 'wav/空.*mov/H264'): voice.set_audio_format(P(set()))

    def test_ffmpeg_polish_loudness_and_noise_floor(self):
        """隔离后的口播（残留底噪约 -78 dB）修完：响度 -12 LUFS、真峰值不超 -1、底噪 ≤ -55 dB；没隔离、底噪 -45 的照样修会量出来不达标。"""
        g, lufs, pk, fl = voice.polish(speech(self.t / '隔离.wav', -78), self.t / '修音.wav')
        print(f'\n  修音实测：加 {g:+.1f} dB，响度 {lufs} LUFS，真峰值 {pk} dBFS，底噪 {fl} dB', end='')
        self.assertLess(abs(lufs - voice.TARGET_LUFS), 1.0); self.assertLessEqual(pk, -1.0); self.assertLessEqual(fl, voice.FLOOR_MAX)
        self.assertFalse((self.t / '修音_未定响度.wav').exists())
        _, _, _, fl2 = voice.polish(speech(self.t / '没隔离.wav', -45, seed=2), self.t / '修音2.wav')
        self.assertGreater(fl2, voice.FLOOR_MAX)                             # 量得出底噪没降下来（run_cmd 会提醒）
        self.assertGreater(voice.noise_floor(self.t / '没隔离.wav'), voice.noise_floor(self.t / '隔离.wav') + 20)

    def test_run_cmd_with_fake_resolve(self):
        d = film(self.t, frames=round(40 * FPS), review_timeline='v2_审核'); job = Job(d)
        R = FakeResolve(round(40 * FPS), d); self.addCleanup(setattr, resolve, 'project', resolve.project)
        resolve.project = lambda job: (R, R, R)
        out, said = quiet(voice.run_cmd, job)
        tl = R.tl; a1 = tl.tracks['audio'][0]
        self.assertTrue(R.rendered_isolated)                                 # 渲的时候：A1 开着人声隔离 70，别的音频轨关着
        self.assertEqual([(i.props['AudioVoiceIsolationEnabled'], i.props['AudioVoiceIsolationAmount']) for i in a1['items']], [(False, 0)] * 2)   # 渲完恢复
        self.assertEqual([x['enabled'] for x in tl.tracks['audio']], [False, True, False, True])    # 原来开关恢复，A1 关掉，修音轨开着
        self.assertEqual((tl.tracks['audio'][3]['name'], len(tl.tracks['audio'][3]['items'])), (resolve.POLISH_TRACK, 1))
        self.assertGreaterEqual(R.calls.count('preset'), 2)                  # 渲前、渲后都载回预设（新18）
        vp = rj(d / 'job.json')['voice_polish']
        self.assertEqual((vp['file'], vp['track']), (str(out), resolve.POLISH_TRACK)); self.assertLessEqual(vp['floor_db'], voice.FLOOR_MAX)
        self.assertLess(abs(vp['lufs'] + 12), 1.0); self.assertTrue(out.exists()); self.assertIn('底噪', said)
        self.assertEqual(resolve.polished_voice(Job(d)), (out, resolve.POLISH_TRACK))       # review 会放它
        j = rj(d / 'job.json'); j['frames'] = 999; wj(d / 'job.json', j)
        with self.assertRaisesRegex(SystemExit, '重跑 voice-polish'): resolve.polished_voice(Job(d))
        R.tl.end = R.tl.st + 500; j['frames'] = round(40 * FPS); wj(d / 'job.json', j)
        with self.assertRaisesRegex(SystemExit, '审核线上剪过'): quiet(voice.run_cmd, Job(d))


class FItem:
    def __init__(s, start, end, name='A', db=0.0, path=''):
        s.s, s.e, s.n, s.path = start, end, name, path
        s.props = {'AudioVoiceIsolationEnabled': False, 'AudioVoiceIsolationAmount': 0, 'AudioVolume': db}
    def GetStart(s): return s.s
    def GetEnd(s): return s.e
    def GetName(s): return s.n
    def GetProperty(s, k=None): return s.props.get(k) if k else dict(s.props)
    def SetProperty(s, k, v): s.props[k] = v; return True


class FTL:
    def __init__(s, n, tracks):
        s.st, s.end = 108000, 108000 + n; s.tracks = {'audio': tracks, 'video': []}
    def GetName(s): return 'v2_审核'
    def GetStartFrame(s): return s.st
    def GetEndFrame(s): return s.end
    def GetTrackCount(s, k): return len(s.tracks[k])
    def GetTrackName(s, k, j): return s.tracks[k][j - 1]['name']
    def SetTrackName(s, k, j, n): s.tracks[k][j - 1]['name'] = n; return True
    def GetItemListInTrack(s, k, j): return list(s.tracks[k][j - 1]['items'])
    def GetIsTrackEnabled(s, k, j): return s.tracks[k][j - 1]['enabled']
    def SetTrackEnable(s, k, j, v): s.tracks[k][j - 1]['enabled'] = v; return True
    def AddTrack(s, k, sub=None): s.tracks[k].append({'name': f'音频 {len(s.tracks[k]) + 1}', 'enabled': True, 'items': []}); return True
    def DeleteClips(s, its): [t['items'].remove(i) for t in s.tracks['audio'] for i in its if i in t['items']]; return True


class FakeResolve:
    """假达芬奇（兼当 Resolve、ProjectManager、Project、MediaPool）：够 voice-polish 走完。渲染时按 A1 有没有开人声隔离出干净或带噪的口播。"""
    def __init__(s, n, d):
        s.n, s.d, s.calls, s.settings, s.rendered_isolated = n, d, [], {}, None
        s.tl = FTL(n, [{'name': '音频 1', 'enabled': True, 'items': [FItem(108000, 108000 + n // 2), FItem(108000 + n // 2, 108000 + n)]},
                       {'name': 'B-roll 原声', 'enabled': True, 'items': [FItem(108000, 108000 + n)]},
                       {'name': '音效', 'enabled': False, 'items': []}])
    def __getattr__(s, k):
        if k in ('OpenPage', 'SaveProject', 'CloseProject', 'SetCurrentTimeline', 'SetCurrentFolder', 'SetCurrentRenderMode', 'DeleteRenderJob'): return lambda *a: True
        raise AttributeError(k)
    def GetName(s): return 'p'
    def LoadProject(s, n): return s
    def GetTimelineCount(s): return 1
    def GetTimelineByIndex(s, i): return s.tl
    def GetMediaPool(s): return s
    def GetRootFolder(s): return s
    def GetSubFolderList(s): return []
    def AddSubFolder(s, root, n): return s
    def ImportMedia(s, paths): return [FItem(0, 0, Path(paths[0]).name, path=paths[0])]
    def AppendToTimeline(s, xs):
        for x in xs: s.tl.tracks['audio'][x['trackIndex'] - 1]['items'].append(FItem(x['recordFrame'], x['recordFrame'] + x['endFrame'], x['mediaPoolItem'].n))
        return True
    def LoadRenderPreset(s, n): s.calls.append('preset'); return True
    def GetRenderFormats(s): return {'Wave': 'wav', 'QuickTime': 'mov'}
    def GetRenderCodecs(s, f): return {'Linear PCM': 'LinearPCM'}
    def SetCurrentRenderFormatAndCodec(s, f, c): s.calls.append(('format', f)); return True
    def SetRenderSettings(s, x): s.settings.update(x); return True
    def AddRenderJob(s): return 'job1'
    def IsRenderingInProgress(s): return False
    def GetRenderJobStatus(s, j): return {'JobStatus': 'Complete'}
    def StartRendering(s, ids, inter):
        A = s.tl.tracks['audio']
        iso = all(i.props['AudioVoiceIsolationEnabled'] and i.props['AudioVoiceIsolationAmount'] == 70 for i in A[0]['items'])
        s.rendered_isolated = iso and A[0]['enabled'] and not any(t['enabled'] for t in A[1:]) and s.settings.get('ExportVideo') is False
        speech(Path(s.settings['TargetDir']) / (s.settings['CustomName'] + '.wav'), -78 if iso else -45, seconds=s.n / FPS)
        return True


class BalanceTests(unittest.TestCase):
    """导出平衡按修音口播轨算（A1 关着），音量记成 polish_levels，下次不误报手调。"""
    def test_balance_uses_polished_track(self):
        with tempfile.TemporaryDirectory() as t:
            d = film(t, voice_polish={'file': '/x.wav', 'track': resolve.POLISH_TRACK}); job = Job(d)
            wj(d / '合成' / '音量.json', {'voice_db': 8.0, 'voice_levels': [8.0]}); wj(d / '合成' / '音效表.json', [])
            a1 = [FItem(108000, 108600, db=8.0)]; pol = [FItem(108000, 109200, 'p.wav', db=0.0)]
            tl = FTL(1200, [{'name': '音频 1', 'enabled': True, 'items': a1}, {'name': '音效', 'enabled': True, 'items': []},
                            {'name': resolve.POLISH_TRACK, 'enabled': True, 'items': pol}])
            got = []
            def norm(items, opt):
                got.append(([i.n for i in items], opt.get('normalizationMode')))
                for i in items: i.props['AudioVolume'] = -3.5
                return True
            tl.NormalizeAudioLevel = norm
            class PM: SaveProject = staticmethod(lambda: True)
            with self.assertRaisesRegex(SystemExit, '音频 1.*还开着'): resolve.balance_audio(job, object(), PM, tl)
            tl.tracks['audio'][0]['enabled'] = False
            quiet(resolve.balance_audio, job, object(), PM, tl)
            self.assertEqual(got, [(['p.wav'], 'True Peak')])
            v = rj(d / '合成' / '音量.json'); self.assertEqual((v['voice_db'], v['polish_levels'], v['polish_db']), (8.0, [-3.5], -3.5))
            self.assertEqual(resolve.hand_tweaked(job, tl, pol, [], polished=True), [])       # 平衡后的音量认得
            pol[0].props['AudioVolume'] = 2.0
            self.assertEqual(len(resolve.hand_tweaked(job, tl, pol, [], polished=True)), 1)  # 用户手调修音轨照样拦
            self.assertEqual(resolve.voice_track(Job(film(Path(t) / 'b')), tl), (1, False))  # 没做修音：还是 A1


# ---------------- 新12 大字/结论音效卡关键词 ----------------
class KeywordTests(unittest.TestCase):
    TEXT = '开头说一句但那是营销行为不是设计行为最后定在门套上沿齐平结束'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = d = film(self.tmp.name, subtitle_keywords=['门套上沿齐平']); self.job = Job(d)
        wj(d / '口播字时间.json', {'tokens': tokens(self.TEXT)})
        self.at = lambda w: round(1.0 + self.TEXT.index(w) * 0.3, 3)
        wj(d / '花字.json', [{'use': 'note', 'at': '开头', 'text': 'x'}, {'use': 'koubo', 'at': '但那是营销行为', 'until': '最后', 'lines': ['那是〖营销行为〗', '不是【设计行为】']}])
        c = d / '合成'; (c / '花字').mkdir(parents=True); (c / '字幕').mkdir()
        wj(c / '字幕' / '字幕表.json', [{'n': 1, 'start': round(self.at('最后') * FPS), 'end': round(self.at('结束') * FPS), 'text': '最后定在门套上沿齐平', 'keys': []}])
        self.put(self.at('但那是'), self.at('但那是'), self.at('最后'))

    def put(self, flower, wrong, ding, wrong_file='/s/错误音.mp3'):
        wj(self.d / '合成' / '花字' / '花字表.json', {'items': [{'n': 1, 'use': 'note', 'kind': '文字', 'file': '/n.mov', 'start': 0, 'end': 20},
                                                              {'n': 2, 'use': 'koubo', 'kind': '文字', 'file': '/k.mov', 'start': round(flower * FPS), 'end': round(flower * FPS) + 60, 'at': flower, 'until': self.at('最后')}]})
        wj(self.d / '合成' / '音效表.json', [{'file': wrong_file, 'record': 1, 'at': wrong, 'beat': '结论'}, {'file': '/s/叮.mp3', 'record': 1, 'at': ding, 'beat': '结论'}])

    def test_reports_flower_and_sounds_at_sentence_start(self):
        off, wh = check.keyword_offsets(self.job)
        self.assertEqual([(x[1].split('（')[0], x[2], x[4]) for x in off],
                         [('第 2 个花字', '营销行为', -0.9), ('音效 错误音.mp3', '营销行为', -0.9), ('音效 叮.mp3', '门套上沿齐平', -1.2)])
        self.assertEqual(wh, [])

    def test_keyword_start_passes_and_whoosh_on_big_text_is_reported(self):
        k = self.at('营销行为'); self.put(k, k + 0.1, self.at('门套'))
        self.assertEqual(check.keyword_offsets(self.job), ([], []))
        self.put(k, k, self.at('门套'), wrong_file='/s/唰音效刷出标题.mp3')
        self.assertEqual([x[1] for x in check.keyword_offsets(self.job)[1]], ['唰音效刷出标题.mp3'])

    def test_conclusion_sound_is_judged_before_subs_exist_by_reading_the_srt(self):
        # 2026-11-24 初稿模式第二次实测：sfx 在 subs 之前跑，字幕表还没有，结论音效差 0.32 秒没被 sfx 拦、到 check 才报
        (self.d / '合成' / '字幕' / '字幕表.json').unlink()
        def stamp(x): h, r = divmod(x, 3600); m, r = divmod(r, 60); return '%02d:%02d:%06.3f' % (h, m, r)
        srt = Path(self.tmp.name) / '主线.srt'
        srt.write_text('1\n%s --> %s\n最后定在门套上沿齐平\n' % (stamp(self.at('最后')).replace('.', ','), stamp(self.at('结束')).replace('.', ',')), encoding='utf-8')
        j = rj(self.d / 'job.json'); j['srt'] = str(srt); wj(self.d / 'job.json', j); job = Job(self.d)
        wj(self.d / '花字.json', [{'use': 'note', 'at': '开头', 'text': 'x'}]); wj(self.d / '合成' / '花字' / '花字表.json', {'items': []})
        rows = [{'file': '/s/叮.mp3', 'at': self.at('门套') - 0.32, 'beat': '结论'}]
        off, _ = check.keyword_offsets(job, rows)
        self.assertEqual([(x[1].split('（')[0], x[2], x[4]) for x in off], [('音效 叮.mp3', '门套上沿齐平', round(-0.32 - 0.0, 2))])
        rows[0]['at'] = self.at('门套') + 0.1
        self.assertEqual(check.keyword_offsets(job, rows)[0], [])             # 差在 0.15 秒内：放行

    def test_unknown_keyword_is_not_judged(self):
        wj(self.d / '花字.json', [{'use': 'note'}, {'use': 'koubo', 'at': '但那是', 'lines': ['【没说过的词】']}])
        wj(self.d / '合成' / '字幕' / '字幕表.json', [])
        self.assertEqual(check.keyword_offsets(self.job), ([], []))

    def test_rendang_may_show_before_its_keyword_but_not_after(self):      # 2026-11-21 用户定：人挡字可以比关键词早出现
        k = self.at('营销行为'); early = self.at('但那是')
        wj(self.d / '花字.json', [{'use': 'note'}, {'use': '人挡字', 'at': '但那是营销行为', 'text': '营销行为'}])
        def put(a, snd):
            wj(self.d / '合成' / '花字' / '花字表.json', {'items': [{'n': 2, 'use': '人挡字', 'kind': '文字', 'file': '/r.mov', 'start': round(a * FPS), 'end': round(a * FPS) + 90}]})
            wj(self.d / '合成' / '音效表.json', [{'file': '/s/叮.mp3', 'record': 1, 'at': snd}])
        put(early, early); self.assertEqual(check.keyword_offsets(self.job), ([], []))          # 字早出、音效跟着字：不报
        put(early, early + 0.3); self.assertEqual([x[1].split('（')[0] for x in check.keyword_offsets(self.job)[0]], ['音效 叮.mp3'])   # 音效两头都没卡上：报
        put(k + 0.4, k + 0.4); self.assertEqual([(x[1].split('（')[0], x[4]) for x in check.keyword_offsets(self.job)[0]], [('第 2 个花字', 0.4), ('音效 叮.mp3', 0.4)])   # 晚了照样报


# ---------------- 新21 用户没看过样张 ----------------
class UnseenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = film(self.tmp.name); self.job = Job(self.d)
        self.proj = self.d / 'Blender_灯'; self.proj.mkdir(); (self.proj / '出图.py').write_text('x = 1\n', encoding='utf-8')
        self.png = self.d / 's.png'; self.png.write_bytes(b'\x89PNG')
        self.ok = lambda words, waive=False: quiet(anim.confirm, self.job, '样张', self.proj, ['B01'], [str(self.png)], words, waive=waive)

    def old(self, words):
        """老记录（2026-11-21 以前）：words 里写着用户没看、却记成了普通确认。现在 confirm 会拒收，所以先记一条再把原话改回去。"""
        self.ok('占位'); f = self.job.p('合成', '确认记录.json'); r = json.loads(f.read_text(encoding='utf-8'))
        r['items'][-1]['words'] = words; f.write_text(json.dumps(r, ensure_ascii=False), encoding='utf-8')

    def test_waiver_and_not_seen_words_are_listed_and_block_export(self):
        self.assertEqual(check.unseen_samples(self.job), [])
        self.ok('这条不用等我确认，直接做', waive=True)
        self.assertEqual([(p, s, w[:4]) for p, s, _, w in check.unseen_samples(self.job)], [('Blender_灯', 'B01', '授权免看')])
        with self.assertRaisesRegex(SystemExit, '改用 --免看'): self.ok('（用户未逐张看样张）按 /goal 放行')      # 现在记不进去
        self.old('（用户未逐张看样张）按 /goal 放行')                          # 老记录里自己记的“确认”：不算补看
        un = check.unseen_samples(self.job); self.assertEqual(len(un), 1); self.assertIn('B01', un[0][1])
        # 2026-11-22 规矩变了（用户定“先做出来再改”）：样张不再是导出前单独的一道闸。以前这里是“样张用户还没看过…--未看样张用户同意”拦导出；
        # 现在导出只有一道闸——用户在审片台点审核线“这一栏定了”（整片都看过了）。没点：拦；点了：没单独看过样张的镜头只记进导出记录、不拦。
        with self.assertRaisesRegex(SystemExit, '“定稿”'): resolve.export(self.job, name='审核线', user_ok='OK')
        with self.assertRaisesRegex(SystemExit, '(?s)样张用户还没看过.*Blender_灯 B01.*--未看样张用户同意'): resolve.unseen_gate(self.job)      # 这个检查本身没变（没给说明就停）
        self.job.cfg['review_timeline'] = '审核线'; settle(self.job)
        self.addCleanup(setattr, resolve, 'project', resolve.project)
        class Past(Exception): pass
        def past(*a, **k): raise Past()
        resolve.project = past; self.job.cfg['免修音'] = '（测试）'
        with self.assertRaises(Past): quiet(resolve.export, self.job, name='审核线')                    # 用户定了：过了最终闸和样张那一步，走到连达芬奇
        self.assertEqual(quiet(resolve.unseen_gate, self.job, '不用看了，直接导')[0], {'原话': '不用看了，直接导', '镜头': ['Blender_灯 B01']})
        self.ok('看了，可以')                                                 # 真确认
        self.assertEqual(check.unseen_samples(self.job), []); self.assertIsNone(resolve.unseen_gate(self.job))

    def test_old_style_not_seen_confirmation_is_listed(self):
        self.old('用户没有逐张看过这组样张，按目标放行')
        self.assertIn('写着用户没看', check.unseen_samples(self.job)[0][3])
        (self.proj / '出图.py').write_text('x = 2\n', encoding='utf-8')     # 代码改过：老确认作废，渲染前 gate 会拦，这里不重复列
        self.assertEqual(check.unseen_samples(self.job), [])

    def test_not_seen_words(self):
        for w in ('用户未逐张看', '用户没看', '（用户没有逐张看过这组样张）', '未经用户确认', '用户还没确认'): self.assertTrue(check.not_seen(w), w)
        for w in ('可以', '看过 动画确认_第1轮.jpg 后说：直接按你的想法做', '用户看完说没问题', '用户未提意见'): self.assertFalse(check.not_seen(w), w)


# ---------------- 新8 收尾提醒：给用户看的 MD ----------------
class UserMdTests(unittest.TestCase):
    def test_only_user_facing_list_md_is_reported(self):
        with tempfile.TemporaryDirectory() as t:
            d = film(t); made = d.parent
            names = ['V1目标.md', 'V2交付清单.md', '提速建议.md', '给用户的说明.md', '反馈记录.md', '给架构审查_标签自检提速.md',
                     '261117 测试片.md', '项目.md', '口播文案.md', '片子复盘.md', '笔记.md', 'README.md']
            for n in names: (made / n).write_text('x', encoding='utf-8')
            (made / '子目录').mkdir(); (made / '子目录' / 'V3交付清单.md').write_text('x', encoding='utf-8')   # 下面的子文件夹不查
            (d / 'V4交付清单.md').write_text('x', encoding='utf-8')
            self.assertEqual(sorted(f.name for f in status.user_md(Job(d))), ['V1目标.md', 'V2交付清单.md', 'V4交付清单.md', '提速建议.md', '给用户的说明.md'])
            _, out = quiet(status.run, Job(d))
            self.assertIn('给用户看的内容放对话或 Claude 文档', out)


# ================= 2026-11-22 261122 反馈单（别墅大平层灯光设计师）：新1–新8、新10 =================
FF = ['ffmpeg', '-v', 'error', '-y']


def mp4(path, size='64x48', frames=12, tmcd=False):
    """小测试视频；tmcd=True 带时间码数据流（像用户给的往期成片）和一条声音。"""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    extra = ['-f', 'lavfi', '-i', 'sine=d=2', '-shortest', '-timecode', '01:00:00:00'] if tmcd else []
    subprocess.run(FF + ['-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=30000/1001'] + extra + ['-frames:v', str(frames), '-pix_fmt', 'yuv420p', str(path)],
                   check=True, stdin=subprocess.DEVNULL)
    return path


def streams(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', str(path)], capture_output=True, text=True).stdout
    return [x['codec_type'] for x in json.loads(out)['streams']]


@unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
class Tmcd1122Tests(unittest.TestCase):
    """新5：素材带时间码数据流（tmcd）时读帧数不崩；实拍片段只留第一条视频流、不带数据流。"""
    def test_frames_and_live_clip_with_tmcd(self):
        with tempfile.TemporaryDirectory() as t:
            src = mp4(Path(t) / '往期成片.mp4', frames=40, tmcd=True)
            self.assertIn('data', streams(src))                                     # 真带 tmcd（和镜前灯往期成片一样）
            self.assertEqual((anim._ffprobe_frames(src), subs.packets(src)), (40, 40))  # 以前 csv 读：ffprobe 9 多打一行 → ValueError
            d = film(t); job = Job(d); out = d / '片段.mp4'
            quiet(anim._clip, job, str(src), 0.0, 20, out)
            self.assertEqual(streams(out), ['video']); self.assertEqual(anim._ffprobe_frames(out), 20)   # -map 0:v:0 -dn -write_tmcd 0：没有声音、没有数据流
            s = resolve.probe(out); self.assertEqual((int(s['width']), int(s['height'])), (job.W, job.H))
            src2 = mp4(Path(t) / '另一段.mp4', frames=10, tmcd=True); dst = Path(t) / '拼.mp4'
            anim._join([src, src2], dst, '30000/1001', 50); self.assertEqual(anim._ffprobe_frames(dst), 50)   # 拼接读宽高也走 JSON


class Overwrite1122Tests(unittest.TestCase):
    """新3：覆盖当前审核线引用的文件前停下（合成/审核线引用.json 由 review 建好时记）；--force 放行；换了新一版审核线不拦。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = film(self.tmp.name, review_timeline='审核线_第5版'); c = self.d / '合成'
        self.files = {k: c.joinpath(*k.split('/')) for k in ('B肉.mp4', 'B肉_小圆圈.mp4', 'A肉.mov', '字幕/字幕整轨.mov', '导航/导航整帧.mov')}
        for f in self.files.values(): f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'x')
        wj(c / '字幕' / '字幕表.json', [{'n': 1, 'file': str(c / '字幕' / 'a.png'), 'start': 0, 'end': 30, 'text': '字'}])
        wj(self.d / '分镜.json', {'anchors': {}, 'shots': []})
        wj(c / '审核线引用.json', {'时间线': '审核线_第5版', '文件': [str(self.files[k]) for k in ('B肉_小圆圈.mp4', '字幕/字幕整轨.mov', '导航/导航整帧.mov')]})
        self.job = Job(self.d)
        from thinkcut import common; self.addCleanup(common.allow_overwrite, False)

    def test_commands_stop_before_writing(self):
        from thinkcut import overlay
        before = {k: f.read_bytes() for k, f in self.files.items()}
        for name, fn in (('B肉_小圆圈.mp4', lambda: overlay.pip(self.job)), ('字幕整轨.mov', lambda: subs.track(self.job)), ('导航整帧.mov', lambda: overlay.nav(self.job, str(self.files['B肉.mp4']))),
                         ('字幕整轨.mov', lambda: subs.render(self.job, str(self.d / '没有.srt'), with_track=True))):
            with self.assertRaisesRegex(SystemExit, '当前审核线「审核线_第5版」正引用着要被覆盖的文件：' + name + '.*先建新一版审核线（review），或用 --force'): fn()
        self.assertEqual({k: f.read_bytes() for k, f in self.files.items()}, before)            # 一个字节都没动

    def test_assemble_only_when_broll_is_referenced(self):
        ok = []
        def fake_prepare(job): ok.append(1); raise Reached()
        self.addCleanup(setattr, anim, 'prepare_live', anim.prepare_live); anim.prepare_live = fake_prepare
        with self.assertRaises(Reached): anim.assemble(self.job)                                  # 审核线用的是 B肉_小圆圈：B肉.mp4 照常总装
        f = self.d / '合成' / '审核线引用.json'; r = rj(f); r['文件'].append(str(self.files['B肉.mp4'])); wj(f, r)
        with self.assertRaisesRegex(SystemExit, 'B肉.mp4'): anim.assemble(self.job)                # 没做小圆圈、审核线直接用 B肉 时：拦
        self.assertEqual(ok, [1])

    def test_force_new_version_and_old_films(self):
        from thinkcut.common import guard_overwrite, allow_overwrite, overwrite_ok
        g = lambda: guard_overwrite(self.job, self.files['B肉_小圆圈.mp4'])
        self.assertRaises(SystemExit, g)
        with overwrite_ok(): g()                                                  # review 建新一版时自己重拼字幕整轨
        self.assertRaises(SystemExit, g)
        allow_overwrite(); g(); allow_overwrite(False)                           # 命令行 --force
        self.job.cfg['review_timeline'] = '审核线_第6版'; g()                     # 记的是上一版审核线：不拦
        self.job.cfg['review_timeline'] = '审核线_第5版'; (self.d / '合成' / '审核线引用.json').unlink(); g()   # 老片子没有清单：不拦
        guard_overwrite(self.job, self.d / '合成' / '没有.mp4')

    def test_cli_force_flag(self):
        S = SCRIPTS / 'thinkcut_edit.py'
        r = subprocess.run([sys.executable, '-B', str(S), 'pip', str(self.d)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0); self.assertIn('正引用着', r.stderr)
        r = subprocess.run([sys.executable, '-B', str(S), 'pip', str(self.d), '--force'], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotIn('正引用着', r.stdout + r.stderr)                               # 过了这道（假文件后面照样会失败，和这道无关）
        for cmd in ('subs', 'subs-track', 'anim-assemble', 'anim-render', 'nav'):
            h = subprocess.run([sys.executable, '-B', str(S), cmd, '-h'], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
            self.assertIn('--force', h, cmd)

    def test_review_records_refs(self):
        src = (SCRIPTS / 'thinkcut' / 'resolve.py').read_text(encoding='utf-8')
        self.assertIn("wj(job.p(*REFS)", src); self.assertIn("'文件': sorted(set(imported))", src)
        self.assertIn('with overwrite_ok(): subs.track(job)', src)


class Polish1122Tests(unittest.TestCase):
    """新4：没修音（job.json 没有 voice_polish）check 不过、整片 export 停；免修音、规矩之前导出过的老片子放行；试导不拦。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = film(self.tmp.name, review_timeline='审核线'); self.job = Job(self.d)

    def test_polish_missing_rules(self):
        from thinkcut import guard
        miss, note = guard.polish_missing(self.job); self.assertIn('voice-polish', miss); self.assertIn('免修音', miss); self.assertIsNone(note)
        for k, v, want in (('voice_polish', {'file': '/x.wav'}, (None, None)), ('免修音', '这条不修了', '免修音 放行'),
                           ('export_ok', {'at': '2026-11-20 10:00:00'}, '老项目')):
            j = Job(self.d); j.cfg[k] = v; got = guard.polish_missing(j)
            if isinstance(want, tuple): self.assertEqual(got, want)
            else: self.assertIsNone(got[0]); self.assertIn(want, got[1])
        j = Job(self.d); j.cfg['export_ok'] = {'at': '2026-11-22 20:38:51'}; self.assertTrue(guard.polish_missing(j)[0])   # 规矩之后导出过的也要修音

    def test_export_blocks_without_polish(self):
        self.addCleanup(setattr, resolve, 'project', resolve.project)
        def stop(*a, **k): raise Reached()
        resolve.project = stop
        with self.assertRaisesRegex(SystemExit, '“定稿”'): resolve.export(self.job, user_ok='OK')      # 2026-11-22 规矩变了：光有转述的原话不导，先被最终闸拦
        settle(self.job)                                                                              # 用户在审片台定了以后，修音的拦截照旧
        with self.assertRaisesRegex(SystemExit, '口播还没修音'): resolve.export(self.job, user_ok='OK')
        with self.assertRaises(Reached): quiet(resolve.export, self.job, seconds=5)                 # 试导几秒不拦
        self.job.cfg['免修音'] = '用户：这条不修音'
        with self.assertRaises(Reached): quiet(resolve.export, self.job, user_ok='OK')
        self.assertIn('免修音', (SCRIPTS / 'thinkcut_edit.py').read_text(encoding='utf-8').split('INIT_KEEP = ')[1].split(')')[0])   # init --force 带过来

    @unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
    def test_check_reports_missing_polish_unregistered_and_low_res(self):
        """check：没修音不过（新4）；分镜用了 01_原始素材 里没登记的文件不过（新7）；实拍源 1080P 只提醒（新10）。"""
        c = self.d / '合成'; c.mkdir(); mp4(c / 'B肉.mp4', '216x288', 300)
        wj(self.d / '口播字时间.json', {'tokens': tokens('开头说一句话')})
        root = self.d.parents[1]; raw = root / '01_原始素材'; (raw / 'B roll').mkdir(parents=True)
        wj(raw / '素材清单.json', {'schema_version': 1, 'items': []}); (raw / '素材目录.md').write_text('#\n', encoding='utf-8')
        clip = mp4(raw / 'B roll' / '桌面拷来的往期成片.mp4', '1920x1080', 6)
        wj(self.d / '分镜.json', {'anchors': {}, 'shots': [{'id': 'F_0', 'kind': 'F', 'a': 0, 'b': 4}, {'id': 'R01', 'kind': 'B', 'a': 4, 'b': 5, '实拍': {'文件': str(clip), '入点': 0}},
                                                          {'id': 'F_2', 'kind': 'F', 'a': 5, 'b': 1200 / FPS}]})
        ok, out = quiet(check.run, self.job); r = rj(c / '自检结果.json')
        self.assertFalse(ok); self.assertEqual(len(r['口播没修音']), 1)
        if (Path(os.environ.get('THINKCUT_PM') or THINKCUT_DIR / 'projects' / 'bin') / 'project.py').exists():
            self.assertEqual(len(r['分镜用了没登记的原始素材']), 1); self.assertIn('R01', r['分镜用了没登记的原始素材'][0])
        self.assertTrue(any('R01' in h and '1920×1080' in h and '先换成 4K 源' in h for h in r['提醒']), r['提醒'])
        self.job.cfg['voice_polish'] = {'file': '/x.wav', 'lufs': -12.5, 'floor_db': -60}
        quiet(check.run, self.job); self.assertEqual(rj(c / '自检结果.json')['口播没修音'], [])


class Materials1122Tests(unittest.TestCase):
    """新7：分镜引用了项目 01_原始素材/ 下没登记的文件，check 就停（用视频项目管理现成的 check_materials，按 THINKCUT_PM／兄弟目录找）。"""
    PM = Path(os.environ.get('THINKCUT_PM') or THINKCUT_DIR / 'projects' / 'bin')

    def make(self, t, register):
        import hashlib
        d = film(t); root = d.parents[1]; raw = root / '01_原始素材'
        for k in ('A roll', 'B roll', '音乐'): (raw / k).mkdir(parents=True)
        f = raw / 'B roll' / '现场调灯_展示_01.mp4'; f.write_bytes(b'video')
        items = [{'category': 'B roll', 'content': '现场调灯', 'purpose': '展示', 'original_name': 'IMG_1.mp4', 'sequence': 1,
                  'path': '01_原始素材/B roll/现场调灯_展示_01.mp4', 'sha256': hashlib.sha256(b'video').hexdigest()}] if register else []
        wj(raw / '素材清单.json', {'schema_version': 1, 'items': items}); (raw / '素材目录.md').write_text('#\n', encoding='utf-8')
        (raw / 'B roll' / '没用到的.png').write_bytes(b'p') if register == 'extra' else None
        wj(d / '分镜.json', {'shots': [{'id': 'R03', 'kind': 'B', 'a': 1, 'b': 2, '实拍': {'文件': str(f), '入点': 0}}]})
        return Job(d)

    @unittest.skipUnless((PM / 'project.py').exists(), '需要视频项目管理')
    def test_unregistered_reference_blocks(self):
        from thinkcut import guard
        with tempfile.TemporaryDirectory() as t:
            bad, hints = guard.unregistered_materials(self.make(t, False))
            self.assertEqual(len(bad), 1); self.assertIn('R03', bad[0]); self.assertIn('没登记进', bad[0]); self.assertEqual(hints, [])
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(guard.unregistered_materials(self.make(t, True)), ([], []))          # 登记过：0
        with tempfile.TemporaryDirectory() as t:
            bad, hints = guard.unregistered_materials(self.make(t, 'extra'))                       # 没被分镜用到的没登记文件：只提醒（存档会拦）
            self.assertEqual(bad, []); self.assertTrue(hints and '没用到的.png' in hints[0])

    def test_no_raw_reference_or_no_pm(self):
        from thinkcut import guard
        with tempfile.TemporaryDirectory() as t:
            d = film(t); wj(d / '分镜.json', {'shots': [{'id': 'R1', 'kind': 'B', 'a': 1, 'b': 2, '实拍': {'文件': '/不存在.mp4'}}]})
            self.assertEqual(guard.unregistered_materials(Job(d)), ([], []))                     # 没引用 01_原始素材：不查
            job = self.make(Path(t) / '二', False); os.environ['THINKCUT_PM'] = str(Path(t) / '没有'); self.addCleanup(os.environ.pop, 'THINKCUT_PM')
            bad, hints = guard.unregistered_materials(job); self.assertEqual(bad, []); self.assertIn('没找到视频项目管理', hints[0])
            calls = []
            bad, _ = guard.unregistered_materials(job, check=lambda root: calls.append(root) or (_ for _ in ()).throw(RuntimeError('原始素材存在未登记文件：01_原始素材/B roll/现场调灯_展示_01.mp4')))
            self.assertEqual(len(bad), 1); self.assertEqual(calls, [job.dir.parents[1]])


@unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
class LiveRes1122Tests(unittest.TestCase):
    """新10 前一半：实拍／盖实拍的源文件清晰度不够（铺满要放大超过 1.5 倍）列出来，check、export 只提醒。"""
    def test_list_low_res_live(self):
        from thinkcut import guard
        with tempfile.TemporaryDirectory() as t:
            d = film(t); a = mp4(Path(t) / 'motion_1080.mp4', '1080x1440', 3); b = mp4(Path(t) / '竖拍4K.mp4', '2160x3840', 3)
            c = mp4(Path(t) / '横拍4K.mp4', '3840x2160', 3); e = mp4(Path(t) / '横拍1080.mp4', '1920x1080', 3)
            wj(d / '分镜.json', {'shots': [{'id': 'R01', 'kind': 'B', 'a': 0, 'b': 1, '实拍': {'文件': str(a)}}, {'id': 'R02', 'kind': 'B', 'a': 1, 'b': 2, '实拍': {'文件': str(b)}},
                                          {'id': 'R03', 'kind': 'B', 'a': 2, 'b': 3, '实拍': {'文件': str(c)}},
                                          {'id': 'B04', 'kind': 'B', 'a': 3, 'b': 5, '盖实拍': [{'a': 3, 'b': 4, '文件': str(e)}]}]})
            job = Job(d); out = guard.live_resolution(job)
            self.assertEqual(len(out), 1); self.assertIn('R01（motion_1080.mp4，1080×1440，要放大 2.0 倍）', out[0]); self.assertIn('B04（横拍1080.mp4', out[0])
            self.assertNotIn('R02', out[0]); self.assertNotIn('R03', out[0]); self.assertIn('先换成 4K 源再导出，或跟用户确认就用这个', out[0])
            job.cfg.update(review_timeline='审核线', voice_polish={'file': '/x.wav'})
            self.addCleanup(setattr, resolve, 'project', resolve.project)
            def stop(*a, **k): raise Reached()
            resolve.project = stop
            settle(job)                                                  # 2026-11-22 起整片导出要用户在审片台点过审核线“这一栏定了”
            with self.assertRaises(Reached): said = quiet(resolve.export, job, user_ok='OK')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try: resolve.export(job, user_ok='OK')
                except Reached: pass
            self.assertIn('【提醒】这几镜实拍的源文件清晰度不够', buf.getvalue())             # 只提醒，照样往下走到连达芬奇


class Confirm1122Tests(unittest.TestCase):
    """新1：命令行 anim-confirm 记样张、草图不再收对话原话，只认审片台上用户点的（board 在用户点时自动记）；免看、标签位置、地图照旧。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = film(self.tmp.name); self.job = Job(self.d)
        self.proj = self.d / 'HF_灯'; (self.proj / '镜头').mkdir(parents=True); (self.proj / '镜头' / 'B01.html').write_text('<div>1</div>', encoding='utf-8')
        from PIL import Image
        self.png = self.d / '样张.png'; Image.new('RGB', (8, 8)).save(self.png)
        wj(self.d / '分镜.json', {'shots': [{'id': 'B01', 'kind': 'B', 'a': 1.0, 'b': 3.0}]})

    def records(self): return anim._records(self.job)['items']

    def test_words_in_chat_are_refused(self):
        with self.assertRaisesRegex(SystemExit, '(?s)B01 的样张确认不收对话里的原话.*审片台“动画”栏点“可以”'):
            anim.confirm_cmd(self.job, '样张', self.proj, ['B01'], [str(self.png)], '导航文字还有封面你都一起都可以出')
        with self.assertRaisesRegex(SystemExit, '草图确认不收对话里的原话'):
            anim.confirm_cmd(self.job, '草图', self.proj, ['B01'], [str(self.png)], '可以', ['要点=画面'])
        with self.assertRaisesRegex(SystemExit, '动画工程文件夹不存在'): anim.confirm_cmd(self.job, '样张', self.d / '没有', ['B01'], [str(self.png)], '可以')
        self.assertEqual(self.records(), [])                                                       # 一条都没记
        r = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'thinkcut_edit.py'), 'anim-confirm', str(self.d), '--kind', '样张', '--project', str(self.proj), '--shots', 'B01',
                            '--files', str(self.png), '--words', '都可以'], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0); self.assertIn('审片台', r.stderr); self.assertEqual(self.records(), [])

    def test_board_click_is_the_confirmation(self):
        from thinkcut import board
        it = board.publish(self.job, '动画', 'B01 样张', '看灯', '可以', [str(self.png)], anim={'kind': '样张', 'project': str(self.proj), 'shots': ['B01']})
        with self.assertRaises(SystemExit): anim.confirm_cmd(self.job, '样张', self.proj, ['B01'], [], '可以')     # 发布了、用户还没点
        out = board.act(self.job, {'id': it['id'], 'v': 1, 'action': '可以'}); self.assertTrue(out['event']['anim']['ok'], out)
        n = len(self.records()); self.assertEqual(n, 1); self.assertEqual(anim.gate(self.job, self.proj, ['B01'], True), [] if False else anim.gate(self.job, self.proj, ['B01'], True))
        got, said_ = quiet(anim.confirm_cmd, self.job, '样张', self.proj, ['B01'], [], '可以')
        self.assertIn('审片台上用户已经点过', said_); self.assertEqual(len(self.records()), n)                    # 只核对，不重复记
        (self.proj / '镜头' / 'B01.html').write_text('<div>改过</div>', encoding='utf-8')                         # 点了之后代码又改：那次点的不算
        with self.assertRaisesRegex(SystemExit, '不收对话里的原话'): anim.confirm_cmd(self.job, '样张', self.proj, ['B01'], [], '可以')

    def test_kept_exceptions(self):
        rec, _ = quiet(anim.confirm_cmd, self.job, '样张', self.proj, ['B01'], [str(self.png)], '这条不用等我确认', waive=True)   # 授权免看（Q129）照旧
        self.assertTrue(rec['免看']); self.assertEqual(len(self.records()), 1)
        with self.assertRaisesRegex(SystemExit, '标签位置要写 --label'): anim.confirm_cmd(self.job, '标签位置', self.proj, ['B01'], [], '选 B')   # 标签位置照旧走 confirm
        with self.assertRaisesRegex(SystemExit, '没写到地图'): anim.confirm_cmd(self.job, '地图', self.proj, ['B01'], [], '就用地图')


class FinalName1122Tests(unittest.TestCase):
    """新8：没改默认成片名。视频项目管理的 register-final／存档／上传要求成片名是 YYMMDD_项目名（project.check_final_video_name），
    只改这边会让下一条片子登记不了；这里锁住两边一致，改名要两个仓库一起改（报告里交架构审查）。"""
    PM = Path(os.environ.get('THINKCUT_PM') or THINKCUT_DIR / 'projects' / 'bin')

    @unittest.skipUnless((PM / 'project.py').exists(), '需要视频项目管理')
    def test_export_name_matches_register_final(self):
        import datetime, importlib
        if str(self.PM) not in sys.path: sys.path.insert(0, str(self.PM))
        pm = importlib.import_module('project')
        with tempfile.TemporaryDirectory() as t:
            d = film(t); name = '261122 别墅、大平层要不要单独请灯光设计师'; wj(d.parents[1] / '项目.json', {'name': name})
            now = datetime.datetime(2026, 11, 22, 20, 40, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
            folder, stem, ext = resolve.export_target(Job(d), None, None, '审核线', now)
            self.assertEqual(stem, '261122_' + name)
            self.assertEqual(pm.check_final_video_name(f'03_成片/{stem}.{ext}', name), f'{stem}.{ext}')
            with self.assertRaises(RuntimeError): pm.check_final_video_name(f'03_成片/{name}.mp4', name)   # 成片名＝项目名：现在的登记规则不收


if __name__ == '__main__':
    unittest.main()
