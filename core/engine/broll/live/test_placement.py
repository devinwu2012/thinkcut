from pathlib import Path
import tempfile
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from broll.live.placement import choose_placement, select_lut, clean_candidates, prepare_placement


def row(**kw):
    return dict({'kind':'video','fps':29.97,'in_media_pool':True,'vfr':False,'file_path':'/source/a.mov'},**kw)


class PlacementTests(unittest.TestCase):
    def test_direct_native_frame_conversion(self):
        result=choose_placement(row(),90,100)
        self.assertEqual(result['method'],'direct');self.assertEqual(result['source_start_frame'],90)
        result=choose_placement(row(fps=59.94),90,100,direct_fps=[29.97,59.94])
        self.assertEqual(result['method'],'direct');self.assertEqual(result['source_start_frame'],180)

    def test_render_reasons(self):
        for change in ({'vfr':True},{'hdr':True},{'fps':30},{'fps':25}):           # 59.94 2026-11-28 起直接放（真达芬奇实测）
            with self.subTest(change=change):self.assertEqual(choose_placement(row(**change),0,100)['method'],'render')

    def test_photos_and_pool_external_ordinary_sources_use_original(self):
        for change in ({'kind':'photo'}, {'in_media_pool':False}):
            self.assertEqual(choose_placement(row(**change),30,100)['method'],'direct')
        self.assertEqual(choose_placement(row(kind='photo'),30,100)['source_start_frame'],0)

    def test_probe_flags(self):
        self.assertEqual(choose_placement(row(probe={'hdr':True}),0,100)['method'],'render')
        for n in (-1,True):
            with self.assertRaises(ValueError):choose_placement(row(),n,100)

    def test_recursive_lut_filename(self):
        with tempfile.TemporaryDirectory() as name:
            base=Path(name);folder=base/'sony';folder.mkdir();lut=folder/'Sony.CUBE';lut.write_text('cube')
            result=select_lut(row(input_lut='sony/Sony'),lut_dirs=[base])
            self.assertEqual(result['status'],'ok');self.assertEqual(Path(result['lut_path']),lut.resolve())

    def test_log_missing_and_named_lut_not_found(self):
        for candidate in (row(is_log=True),row(input_lut='missing')):
            result=select_lut(candidate,lut_dirs=[]);self.assertEqual(result['status'],'skip');self.assertIn('跳过',result['reason'])
        self.assertEqual(select_lut(row(input_lut='None'),lut_dirs=[])['status'],'ok')

    def test_external_folder_rule_and_ambiguous(self):
        with tempfile.TemporaryDirectory() as name:
            base=Path(name);(base/'Sony.cube').write_text('cube');(base/'Other.cube').write_text('cube')
            r=row(in_media_pool=False,is_log=True,file_path='/source/a.mov')
            rules=[{'folder':'/','lut':'Other'},{'folder':'/source','lut':'Sony'}]
            self.assertEqual(Path(select_lut(r,folder_rules=rules,lut_dirs=[base])['lut_path']).name,'Sony.cube')
            sub=base/'nested';sub.mkdir();(sub/'Sony.cube').write_text('duplicate')
            self.assertEqual(select_lut(r,folder_rules=rules,lut_dirs=[base])['status'],'skip')

    def test_clean_reference_pairs_only_no_io(self):
        files=['/clips/a.mov','/clips/a.json','/clips/b.mov','/clips/b.json','/clips/noise.txt','/clips/待清理/c.mov']
        result=clean_candidates(files,{'/clips/a.mov'})
        self.assertEqual([r['path'] for r in result],['/clips/b.mov','/clips/b.json'])
        self.assertEqual(result[0]['destination'],'/clips/待清理/b.mov')

    def test_prepare_skips_log_keeps_direct_pool_lut(self):
        self.assertEqual(prepare_placement(row(is_log=True),0,100)['method'],'skip')
        self.assertEqual(prepare_placement(row(is_log=True,input_lut='Sony'),0,100,lut_dirs=[])['method'],'direct')
        self.assertEqual(prepare_placement(row(fps=30,input_lut='missing'),0,100,lut_dirs=[])['method'],'skip')

    def test_unknown_vfr_not_assumed_constant(self):
        c=row();del c['vfr']
        self.assertEqual(choose_placement(c,0,100)['method'],'render')


class ColorManagedTests(unittest.TestCase):
    """2026-11-28 用户定“工程开色彩管理”：开着时 HDR 素材交给达芬奇，直接引用原片；没开照旧转码。"""
    def test_hdr_direct_when_color_managed(self):
        from broll.live.placement import choose_placement, prepare_placement
        c = {'segment': 's', 'kind': 'video', 'fps': [30000, 1001], 'vfr': False, 'hdr': True, 'in_media_pool': True}
        self.assertEqual(choose_placement(c, 0, 30)['method'], 'render')
        got = choose_placement(c, 0, 30, color_managed=True)
        self.assertEqual(got['method'], 'direct'); self.assertIn('色彩管理', got['reason'])
        self.assertEqual(prepare_placement(c, 0, 30, color_managed=True)['method'], 'direct')
        vfr = dict(c, vfr=True, fps=[25, 1])                                    # 真可变帧率、不支持的帧率照旧转码
        self.assertEqual(choose_placement(vfr, 0, 30, color_managed=True)['method'], 'render')

    def test_iphone_5994_direct(self):
        # 2026-11-28 真达芬奇实测：iPhone 59.94（ffprobe 报可变帧率，平均 59.898）直接放帧数、入点都准
        from broll.live.placement import choose_placement
        c = {'segment': 's', 'kind': 'video', 'fps': [602400, 10057], 'vfr': True, 'hdr': True, 'nominal_fps': [60000, 1001]}
        got = choose_placement(c, 75, 90, color_managed=True)
        self.assertEqual(got['method'], 'direct'); self.assertEqual(got['source_start_frame'], 150); self.assertEqual(got['source_frames'], 180)
        self.assertEqual(choose_placement(dict(c, fps=[50, 1], vfr=False), 0, 30, color_managed=True)['method'], 'render')
        self.assertEqual(choose_placement(dict(c, nominal_fps=None), 0, 30, color_managed=True)['method'], 'render')    # 老索引没记标称帧率：照旧转码

    def test_color_managed_rule(self):
        from broll.live.placement import color_managed_ok, COLOR_MANAGED
        want = dict(COLOR_MANAGED)
        self.assertTrue(color_managed_ok(want.get))
        self.assertFalse(color_managed_ok(dict(want, timelineWorkingLuminanceMode='HDR 1000').get))     # 只开自动管理、工作亮度 HDR 1000：HDR 过曝，不算
        self.assertFalse(color_managed_ok(dict(want, colorSpaceOutput='Rec.709 Gamma 2.4').get))         # 输出标准是 Rec.709-A
        self.assertFalse(color_managed_ok({}.get))


class RateRatioTests(unittest.TestCase):
    def test_rate_ratio(self):
        # 2026-11-28 真达芬奇：iPhone 59.94 读成 60，放进 29.97 时结束帧按 60/29.97 往上取整，90 帧要给 181 个源帧
        import math
        from types import SimpleNamespace
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from engine.broll.live.resolve_adapter import Adapter
        me = SimpleNamespace(main=SimpleNamespace(GetSetting=lambda k: '29.97'))
        clip = lambda fps: SimpleNamespace(GetClipProperty=lambda k: fps)
        r = Adapter.rate_ratio(me, clip(60.0)); self.assertEqual(math.ceil(90 * r - 1e-9), 181); self.assertEqual(math.ceil(151 * r - 1e-9), 303)
        self.assertIsNone(Adapter.rate_ratio(me, clip(29.97))); self.assertIsNone(Adapter.rate_ratio(me, clip(None))); self.assertIsNone(Adapter.rate_ratio(me, clip(24.0 * 0)))
