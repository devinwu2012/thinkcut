"""同帧数换源（2026-11-12《水电阶段》新9）：新颜色/新声音、帧数不变的口播能接着用原审核状态；错位、帧数不同的拒绝。"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from source_guard import fingerprint, verify_source, replace_source, SourceChanged

FFMPEG = shutil.which('ffmpeg')
# 有起伏的“说话”声：时有时无的蜂鸣，换了音量和处理也能比出时间
VOICE = "aevalsrc='0.5*sin(440*2*PI*t)*gt(sin(2*PI*t*1.3)+sin(2*PI*t*0.7+1),0.4)':s=48000:d=12"


def make(path, *, video_filter='null', trim_frames=0, audio_filter='anull', frames=300):
    subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30000/1001:duration=12',
                    '-f', 'lavfi', '-i', VOICE, '-vf', f'trim=start_frame={trim_frames},setpts=PTS-STARTPTS,{video_filter}',
                    '-af', audio_filter, '-frames:v', str(frames), '-c:v', 'libx264', '-g', '30', '-pix_fmt', 'yuv420p', '-c:a', 'pcm_s16le',
                    '-t', f'{frames * 1001 / 30000:.4f}', str(path)], check=True)


@unittest.skipUnless(FFMPEG, '需要 ffmpeg')
class ReplaceSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        folder = Path(cls.media.name)
        cls.original = folder / 'old.mov'; make(cls.original)
        cls.recolored = folder / 'new.mov'; make(cls.recolored, video_filter='eq=brightness=0.08:saturation=1.4', audio_filter='volume=6dB,acompressor')
        cls.shifted = folder / 'shift.mov'; make(cls.shifted, trim_frames=2)
        cls.late_audio = folder / 'late.mov'; make(cls.late_audio, audio_filter='adelay=200:all=1')
        cls.shorter = folder / 'short.mov'; make(cls.shorter, frames=290)
        cls.audio = folder / 'source_audio.mp3'
        subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-i', str(cls.original), '-vn', '-ac', '1', '-c:a', 'libmp3lame', str(cls.audio)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        project = Path(self.temp.name); self.run = project / '审核'; self.run.mkdir(); (project / '转写').mkdir()
        self.source = project / '口播.mov'; shutil.copy(self.original, self.source); self.source = self.source.resolve()
        shutil.copy(self.audio, project / '转写/source_audio.mp3')
        s = self.source.stat()
        (project / '转写/source_manifest.json').write_text(json.dumps({'path': str(self.source), 'size': s.st_size, 'mtime_ns': s.st_mtime_ns,
                                                                         'ctime_ns': s.st_ctime_ns, 'audio_stream': 0, 'fingerprint': fingerprint(self.source)}))
        self.config = {'name': 'v1', 'source': str(self.source), 'source_frames': 300, 'fps': [30000, 1001], 'resolution': [320, 240],
                       'audio_stream': 0, 'media_pool_item_id': 'clip-1', 'project': '_mcp_test'}
        (self.run / 'project_runtime.json').write_text(json.dumps(self.config))
        (self.run / 'delivery_approved_v1').mkdir()
        (self.run / 'delivery_approved_v1/approval_manifest.json').write_text(json.dumps(
            {'source': str(self.source), 'source_size': s.st_size, 'source_mtime_ns': s.st_mtime_ns}))
        self.old_copy = project / '口播_旧.mov'; shutil.copy(self.original, self.old_copy)

    def swap_in(self, media):
        """像《水电阶段》那样：旧文件留作副本，新文件换进原路径。"""
        shutil.copy(media, self.source)

    def test_same_frames_new_colour_and_voice_keeps_review_state(self):
        self.swap_in(self.recolored)
        with self.assertRaisesRegex(ValueError, '原片已改变'):verify_source(self.config, self.run)
        updated, entry = replace_source(self.config, self.run, self.source, old=self.old_copy)
        self.assertEqual(verify_source(updated, self.run, sampled=True, audio=True)['source_size'], self.source.stat().st_size)
        self.assertEqual(updated['media_pool_item_id'], 'clip-1')  # 同路径：达芬奇里的素材照用
        self.assertTrue(all(c['offset'] == 0 for c in entry['checks']['frame_checks']))
        self.assertTrue(all(abs(c['offset_ms']) <= 40 for c in entry['checks']['audio_checks']))
        approval = json.loads((self.run / 'delivery_approved_v1/approval_manifest.json').read_text())
        self.assertEqual(approval['source_size'], self.source.stat().st_size)
        history = json.loads((self.run.parent / '转写/换源记录.json').read_text())
        self.assertEqual(len(history), 1); self.assertEqual(history[0]['old_media_pool_item_id'], 'clip-1')

    def test_new_path_drops_resolve_binding(self):
        new = self.run.parent / '口播_新.mov'; shutil.copy(self.recolored, new)
        updated, _ = replace_source(self.config, self.run, new)
        self.assertEqual(updated['source'], str(new.resolve())); self.assertIsNone(updated['media_pool_item_id'])
        self.assertEqual(json.loads((self.run / 'project_runtime.json').read_text())['source'], str(new.resolve()))

    def refused(self, media, message, *, old=True):
        before = {p.name: p.read_bytes() for p in (self.run / 'project_runtime.json', self.run.parent / '转写/source_manifest.json')}
        self.swap_in(media)
        with self.assertRaisesRegex(SourceChanged, message):
            replace_source(self.config, self.run, self.source, old=self.old_copy if old else None)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.run / 'project_runtime.json', self.run.parent / '转写/source_manifest.json')})
        self.assertFalse((self.run.parent / '转写/换源记录.json').exists())

    def test_shifted_picture_is_refused(self):
        self.refused(self.shifted, '对不上')

    def test_late_voice_is_refused_even_without_old_file(self):
        self.refused(self.late_audio, '毫秒', old=False)

    def test_different_frame_count_is_refused(self):
        self.refused(self.shorter, '帧数不同')


if __name__ == '__main__':
    unittest.main()
