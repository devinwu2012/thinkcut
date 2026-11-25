"""动画栏“带口播播放”（animclip）：镜头起止、这一段的字、卡点、从成片切小视频（2026-11-24 用户定：画面用成片里那一段）。"""
import json, shutil, subprocess, tempfile, unittest
from pathlib import Path
from . import animclip, board
from .common import Job


def _job(tmp):
    d = Path(tmp) / '02_制作' / '自动剪辑'; (d / '审片工作台').mkdir(parents=True)
    (d / 'job.json').write_text(json.dumps({'project': '测试片'}), encoding='utf-8')
    (d / '分镜.json').write_text(json.dumps({'shots': [{'id': 'B01', 'kind': 'B', 'a': 0.0, 'b': 1.0}, {'id': 'B02', 'kind': 'B', 'a': 1.0, 'b': 2.0}]}), encoding='utf-8')
    toks = [{'u': 0, 'c': c, 't0': i * 0.25, 't1': i * 0.25 + 0.25} for i, c in enumerate('台面多高灶台低十')]
    (d / '口播字时间.json').write_text(json.dumps({'tokens': toks}), encoding='utf-8')
    (d / '审片工作台' / '审片台.json').write_text(json.dumps({'items': [
        {'id': '动1', 'column': '动画', 'versions': [{'v': 1, 'at': '2000-01-01 00:00:00', 'anim': {'kind': '样张', 'shots': ['B02']},
                                                    'files': [{'label': 'B02 第 1.5 秒'}, {'label': 'B02 第 9 秒'}]}]},
        {'id': '封1', 'column': '封面', 'versions': [{'v': 1, 'at': '2000-01-01 00:00:00', 'files': []}]}]}), encoding='utf-8')
    return Job(str(d))


class AnimClipTests(unittest.TestCase):
    def test_span_chars_beats_without_render(self):
        with tempfile.TemporaryDirectory() as t:
            r = animclip.clip(_job(t), '动1')
            self.assertEqual((r['a'], r['b'], r['shots']), (1.0, 2.0, ['B02']))
            self.assertEqual(''.join(c[0] for c in r['chars']), '灶台低十')        # 只要这个镜头那一段的字
            self.assertEqual(r['beats'], [1.5])                                    # 图上的“第 N 秒”，不在这一段的不要
            self.assertIsNone(r['video']); self.assertIn('还没出成片', r['why'])

    def test_only_animation_items(self):
        with tempfile.TemporaryDirectory() as t:
            job = _job(t)
            with self.assertRaises(animclip.Oops): animclip.clip(job, '封1')
            with self.assertRaises(animclip.Oops): animclip.clip(job, '没有这个')

    @unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
    def test_cuts_picture_and_voice_from_the_render(self):
        with tempfile.TemporaryDirectory() as t:
            job = _job(t); comp = job.dir / '合成'; comp.mkdir()
            ff = shutil.which('ffmpeg')
            subprocess.run([ff, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=120x160:d=3', '-pix_fmt', 'yuv420p', str(comp / 'B肉.mp4')], check=True)
            subprocess.run([ff, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=120x160:d=3', '-f', 'lavfi', '-i', 'sine=d=3', '-shortest', '-pix_fmt', 'yuv420p', str(comp / 'A肉.mov')], check=True)
            r = animclip.clip(job, '动1')
            f = board.store(job) / r['video']; self.assertTrue(f.exists())
            out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type:format=duration', '-of', 'json', str(f)], capture_output=True, text=True).stdout
            info = json.loads(out); kinds = sorted(s['codec_type'] for s in info['streams'])
            self.assertEqual(kinds, ['audio', 'video'])                           # 画面来自 B肉，声音来自 A肉
            self.assertAlmostEqual(float(info['format']['duration']), 1.0, delta=0.15)
            self.assertFalse(r['older'])                                           # 成片比这一版新
            self.assertEqual(animclip.clip(job, '动1')['video'], r['video'])         # 再要一次不重切


if __name__ == '__main__': unittest.main()
