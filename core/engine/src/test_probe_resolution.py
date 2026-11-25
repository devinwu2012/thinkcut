import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from probe_media import probe
from setup_new_resolve import timeline_resolution

FFMPEG = shutil.which('ffmpeg')


def clip(folder, name, size, rotation=None):
    path = Path(folder) / name
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size={size}:rate=30000/1001',
                    '-t', '0.2', '-c:v', 'libx264', str(path)], check=True)
    if rotation:
        rotated = path.with_name('rot_' + name)
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-display_rotation', str(rotation), '-i', str(path),
                        '-c', 'copy', str(rotated)], check=True)
        path = rotated
    return path


@unittest.skipUnless(FFMPEG, 'ffmpeg required')
class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def size(self, *args):
        return next(t for t in probe(clip(self.tmp.name, *args))['tracks'] if t['handler'] == 'vide')['display_size']

    def test_landscape_portrait_and_rotated_phone_video(self):
        self.assertEqual(self.size('land.mp4', '1920x1080'), [1920, 1080])
        self.assertEqual(self.size('port.mp4', '1080x1920'), [1080, 1920])
        self.assertEqual(self.size('phone.mp4', '1920x1080', 90), [1080, 1920])

    def test_timeline_uses_recorded_size_or_probes_older_projects(self):
        self.assertEqual(timeline_resolution({'resolution': [1080, 1920]}), [1080, 1920])
        source = clip(self.tmp.name, 'old.mp4', '1280x720')
        self.assertEqual(timeline_resolution({'source': str(source)}), [1280, 720])
        with self.assertRaises(ValueError):
            timeline_resolution({'resolution': [0, 1920]})


if __name__ == '__main__':
    unittest.main()
