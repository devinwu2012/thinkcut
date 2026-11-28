"""broll.py add-track（2026-11-28）：主线没有能放 B-roll 的画面轨时加一条空轨。替身达芬奇，不碰真的。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).absolute().parents[3]))
import json, io as sio, contextlib, unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from engine.broll.live._fake_resolve import Resolve
from engine.broll.live.resolve_adapter import Adapter
from engine.broll.live import service


class AddTrack(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        p = patch('engine.fx.resolve_io.time.sleep'); p.start(); self.addCleanup(p.stop)
        self.r = Resolve(); self.tl = self.r.project.current; self.lock = str(Path(self.tmp.name) / 'lock')

    def only_v1(self):
        self.tl.value['tracks'] = [t for t in self.tl.value['tracks'] if not (t['type'] == 'video' and t['index'] > 1)]
        self.tl.items = [i for i in self.tl.items if not (i.row['type'] == 'video' and i.row['track'] > 1)]

    def io(self): return Adapter(self.r, self.r.project, self.tl)

    def test_has_free_track_does_nothing(self):
        out = service.add_track(self.io(), apply=True, lock_path=self.lock)
        self.assertFalse(out['加轨']); self.assertIn('不用加', out['原因']); self.assertEqual(self.tl.GetTrackCount('video'), 4)

    def test_only_v1_dry_run_then_apply(self):
        self.only_v1(); saved = self.r.GetProjectManager().saved
        out = service.add_track(self.io(), lock_path=self.lock)                                       # 默认只演练
        self.assertTrue(out['演练']); self.assertEqual(out['加在'], 'V2'); self.assertEqual(self.tl.GetTrackCount('video'), 1)
        out = service.add_track(self.io(), apply=True, lock_path=self.lock)
        self.assertTrue(out['加轨']); self.assertEqual(self.tl.GetTrackCount('video'), 2); self.assertEqual(self.tl.GetTrackName('video', 2), 'B-roll')
        self.assertEqual(self.r.GetProjectManager().saved, saved + 1)
        self.assertEqual(len(self.tl.GetItemListInTrack('video', 2)), 0)                              # 只加空轨，不动片段
        self.assertFalse(service.add_track(self.io(), apply=True, lock_path=self.lock)['加轨'])       # 再跑：已有能放的，不重复加

    def test_overlay_above_refuses(self):
        self.only_v1(); self.tl.AddTrack('video'); self.tl.SetTrackName('video', 2, '花字')
        out = service.add_track(self.io(), apply=True, lock_path=self.lock)
        self.assertFalse(out['加轨']); self.assertIn('下面插一条空画面轨', out['原因']); self.assertEqual(self.tl.GetTrackCount('video'), 2)

    def test_cli(self):
        import importlib.util                                                           # 按路径载入 bin/broll.py（名字和 engine/broll 包撞了）
        spec = importlib.util.spec_from_file_location('broll_cli', Path(__file__).absolute().parents[3] / 'bin' / 'broll.py')
        broll = importlib.util.module_from_spec(spec); spec.loader.exec_module(broll)
        self.only_v1(); a = self.io()
        buf = sio.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf), patch.dict('os.environ', {'THINKCUT_RESOLVE_LOCK': self.lock}):
            code = broll.main(['add-track', '--config', str(Path(self.tmp.name) / 'none.json')], preflight=lambda cfg, tl: (a, {}))
        self.assertEqual(code, 0, buf.getvalue()); self.assertTrue(json.loads(buf.getvalue())['演练']); self.assertEqual(self.tl.GetTrackCount('video'), 1)


if __name__ == '__main__':
    unittest.main()
