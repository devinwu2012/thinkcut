"""speed_voice：固定增益＋限幅、分段响度报错、HDR 颜色曲线（2026-11-12《水电阶段》新11①②）。"""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import speed_voice as sv

FFMPEG = shutil.which('ffmpeg')


class WindowTests(unittest.TestCase):
    def points(self, levels):
        """每段 10 秒、每 0.1 秒一个瞬时响度。"""
        return [(round(k * 10 + i * 0.1 + 0.1, 1), level) for k, level in enumerate(levels) for i in range(100)]

    def test_quiet_opening_like_dynamic_loudnorm_is_reported(self):
        rows = sv.windows(self.points([-25, -21, -16, -13, -13, -12.5, -13, -12.8]))
        self.assertIn('0–10 秒', sv.spread_problem(rows))

    def test_normal_speech_variation_passes(self):
        rows = sv.windows(self.points([-12.7, -13.3, -14.0, -11.2, -12.8, -13.1, -12.4]))
        self.assertIsNone(sv.spread_problem(rows))

    def test_silent_and_partial_windows_are_ignored(self):
        points = self.points([-13, -13, -13, -13]) + [(40 + i * 0.1, -18.0) for i in range(1, 40)]   # 结尾只有 4 秒
        points[100:200] = [(t, -90.0) for t, _ in points[100:200]]                                  # 第二段全是静音
        rows = sv.windows(points)
        self.assertIsNone(rows[1][1]); self.assertIsNone(rows[-1][1]); self.assertIsNone(sv.spread_problem(rows))


class CurveTests(unittest.TestCase):
    def test_fit_recovers_a_known_display_curve(self):
        import numpy as np
        rng = np.random.default_rng(1)
        direct = rng.uniform(0.05, 0.95, (40000, 3))
        apple = np.clip(direct ** 1.3 * np.array([0.95, 0.9, 0.85]), 0, 1)
        curve, error = sv.fit_curve([(direct, apple)])
        grid = np.linspace(0, 1, 256)
        self.assertLess(error, 0.01)
        self.assertLess(np.abs(curve[30:240, 1] - grid[30:240] ** 1.3 * 0.9).max(), 0.01)
        self.assertTrue((np.diff(curve, axis=0) >= 0).all())

    def test_cube_file_format(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'a.cube'; sv.write_cube(path, np.tile(np.linspace(0, 1, 256)[:, None], 3), 't')
            lines = path.read_text().splitlines()
            self.assertEqual(lines[1], 'LUT_1D_SIZE 256'); self.assertEqual(len(lines), 258)


@unittest.skipUnless(FFMPEG, '需要 ffmpeg')
class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.folder = Path(self.temp.name)

    def make(self, name, *extra, audio="aevalsrc='0.02*sin(330*2*PI*t)*gt(sin(2*PI*t*1.7),-0.2)':s=48000:d=24"):
        path = self.folder / name
        subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=60:duration=24',
                        '-f', 'lavfi', '-i', audio, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', *extra, '-c:a', 'aac', '-shortest', str(path)], check=True)
        return path

    def test_quiet_source_gets_fixed_gain_without_slow_start(self):
        # 原片约 -36 LUFS（比《水电阶段》原片还小），要拉 20 dB 以上：固定增益后开头不能比后面小
        src = self.make('quiet.mov')
        wav = self.folder / 'v.wav'
        gain, measured = sv.voice_track(src, 'aresample=async=1:first_pts=0,atempo=1.2,' + sv.VOICE, -12.5, wav)
        self.assertGreater(gain, 15); self.assertLess(abs(measured + 12.5), 0.6)
        _, peak = sv.loudness(wav); self.assertLessEqual(peak, -1.2)
        self.assertIsNone(sv.spread_problem(sv.windows(sv.momentary(wav))))

    def test_hdr_detection(self):
        self.assertIsNone(sv.hdr_kind(self.make('sdr.mov')))
        hlg = self.make('hlg.mov', '-vf', 'setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc')
        self.assertEqual(sv.hdr_kind(hlg), 'HLG')

    def test_full_run_sdr(self):
        src = self.make('a.mov'); out = self.folder / 'out.mov'
        r = subprocess.run([sys.executable, str(Path(sv.__file__)), str(src), '--out', str(out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn('分段响度', r.stdout); self.assertNotIn('苹果', r.stdout)
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,r_frame_rate', '-of', 'json', str(out)]))
        self.assertEqual({s['codec_type']: s['r_frame_rate'] for s in probe['streams']}['video'], '30000/1001')
        self.assertFalse(list(self.folder.glob('.speed_voice_*')))   # 临时声音文件已清掉

    def test_interrupted_run_leaves_no_half_output_and_next_run_works(self):
        import contextlib, io
        from unittest.mock import patch
        src = self.make('a.mov'); out = self.folder / 'out.mov'; real = sv.ffmpeg

        def killed_while_encoding(*args):
            if str(args[-1]).endswith('out.mov'):          # 最后那步转码：写了半个文件就被打断
                Path(args[-1]).write_bytes(b'half'); raise KeyboardInterrupt
            return real(*args)
        with patch.object(sv, 'ffmpeg', killed_while_encoding), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            sv.main([str(src), '--out', str(out)])
        self.assertFalse(out.exists())                     # 原来：留下半个 out.mov，下次只说“已存在”
        self.assertFalse(list(self.folder.glob('.speed_voice_*')))
        dead = self.folder / '.speed_voice_out_上次强退'; dead.mkdir(); (dead / 'out.mov').write_bytes(b'half')
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            sv.main([str(src), '--out', str(out)])
        self.assertIn('上次转码中断留下的临时文件，已清掉', text.getvalue())
        self.assertFalse(dead.exists()); self.assertGreater(out.stat().st_size, 10000)
        self.assertFalse(list(self.folder.glob('.speed_voice_*')))

    def test_cube_path_with_filter_special_characters(self):
        import numpy as np
        folder = self.folder / "曲线,a:b'c[d];e"; folder.mkdir()
        cube = folder / 'x.cube'; sv.write_cube(cube, np.tile(np.linspace(0, 1, 16)[:, None], 3), 't')
        sv.ffmpeg('-f', 'lavfi', '-i', 'color=red:s=16x16:d=0.1', '-vf', f'format=rgb48le,lut1d={sv.filter_path(cube)},format=yuv420p', '-f', 'null', '-')
        r = subprocess.run([FFMPEG, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=16x16:d=0.1', '-vf', f'lut1d={cube}', '-f', 'null', '-'], capture_output=True)
        self.assertNotEqual(r.returncode, 0)               # 原来直接拼路径：这种文件夹名会让 ffmpeg 解析失败


if __name__ == '__main__':
    unittest.main()
