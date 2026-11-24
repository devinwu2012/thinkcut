"""Deterministic safety tests use a temporary font, never user-installed fonts/Resolve."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine.fx.plan import config, validation_report
from engine.fx.safety import check_layout, estimate, subtitle_zone
from engine.fx.executor import run
from engine.fx.ledger import digest


class SafetyGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        cls.temp = tempfile.TemporaryDirectory()
        cls.fonts = Path(cls.temp.name)
        fb = FontBuilder(1000, isTTF=True)
        fb.setupGlyphOrder(['.notdef', 'A', 'B', 'space'])
        fb.setupCharacterMap({65: 'A', 66: 'B', 32: 'space'})
        glyphs = {}
        for name in ['.notdef', 'A', 'B', 'space']:
            pen = TTGlyphPen(None)
            if name != 'space':
                pen.moveTo((50, -100)); pen.lineTo((550, -100)); pen.lineTo((550, 800))
                pen.lineTo((50, 800)); pen.closePath()
            glyphs[name] = pen.glyph()
        fb.setupGlyf(glyphs)
        fb.setupHorizontalMetrics({name: (600, 50) for name in glyphs})
        fb.setupHorizontalHeader(ascent=900, descent=-100)
        fb.setupNameTable({'familyName':'SafetyTest','styleName':'Regular'})
        fb.setupOS2(sTypoAscender=900, sTypoDescender=-100, usWinAscent=900, usWinDescent=100)
        fb.setupPost(); fb.setupMaxp(); fb.save(cls.fonts/'test.ttf')

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.cfg = config('fx_styles.json')
        # 测试自带一个无底框的样本样式，不依赖正式样式配置
        self.cfg['styles']['plain'] = dict(font='SafetyTest', style='Regular', rgb=[1,1,1], font_file='test.ttf', default_size=.05, shadow_enabled=False)
        self.cfg['safety']['font_calibration']['test.ttf'] = {'Text+':{'baseline_units':400},'Text':{'baseline_units':400}}
        self.plan = {'schema_version':1, 'status':'ready',
                     'timeline':{'name':'main','start':100,'end':300,'width':1920,'height':1080},
                     'effects':[{'effect_id':'sample','group_id':'one','kind':'photo_label','text':'AB',
                                 'start':110,'end':200,'layer':1,'source_marker':None,
                                 'style':{'profile':'plain','size':.05,'static':True},
                                 'anchor':{'subject':'photo','coordinate_system':'frame_normalized_bottom_left',
                                           'center':[.5,.65],'subject_bbox':[.3,.25,.7,.45]},
                                 'animation':[],'sounds':[],'replace_item_ids':[],
                                 'layout_approved':True,'precondition_snapshot_sha256':'0'*64,
                                 'overrides':{'Enabled3':0}}]}
        self.e = self.plan['effects'][0]

    def check(self, **kw):
        return check_layout(self.plan, cfg=self.cfg, fonts_dir=self.fonts, **kw)

    def bounds(self, w=1920, h=1080):
        return estimate(self.e, w, h, self.cfg, self.fonts)

    def test_font_ink_width_ascent_descent_and_center(self):
        b=self.bounds()['bbox_px']
        # Scale is .05*1920*.8/1000=.0768; ink spans 1100x900 units.
        self.assertAlmostEqual(b[2]-b[0],1100*.0768+4)
        self.assertAlmostEqual(b[3]-b[1],900*.0768+4)
        self.assertTrue(self.check()['valid'])

    def test_brand_labels_use_pingfang_and_cover_measured_boxes(self):
        # 2026-11-12 统一到包装系统：苹方 SC 中粗（系统字体资源里的 PingFang.ttc 第 11 个）。
        # 达芬奇 21.1.1 测试工程实测（左下原点像素，2160×2880，居中）：字墨框、深色底胶囊框都必须被估算外框盖住
        from engine.fx.safety import font_path
        cfg = config('fx_styles.json'); self.assertEqual(list(cfg['styles']), ['brand_label', 'brand_label_dark'])
        for name, index in (('brand_label', 11), ('brand_label_dark', 7)):   # 中粗 / 中等
            path = font_path(cfg['styles'][name], {}, cfg, None)
            self.assertTrue(str(path).endswith(f'/PingFang.ttc#{index}'), path)
        ink = {('brand_label', '通气阀防臭', .06): [899, 1408, 1262, 1477], ('brand_label', '通气阀防臭', .0843): [825, 1394, 1336, 1492],
               ('brand_label', 'Oatey', .0843): [938, 1385, 1225, 1481], ('brand_label', '通气阀 Oatey 防臭', .0843): [645, 1385, 1516, 1492],
               ('brand_label', '通气阀防臭', .0616): [894, 1407, 1267, 1478],
               ('brand_label_dark', '通气阀防臭', .0519): [923, 1412, 1237, 1472], ('brand_label_dark', 'Oatey', .0519): [993, 1406, 1169, 1465]}
        for (profile, text, size), real in ink.items():
            self.e.update(text=text, style={'profile': profile, 'size': size, 'static': True}, overrides={'Enabled3': 0})
            cfg = config('fx_styles.json'); cfg['styles'][profile].pop('background', None)   # 只量字墨
            self.e['anchor']['center'] = [.5, .5]
            b = estimate(self.e, 2160, 2880, cfg)['bbox_px']
            self.assertTrue(b[0] <= real[0] and b[1] <= real[1] and b[2] >= real[2] and b[3] >= real[3], (text, b))
            self.assertLess(max(real[0]-b[0], real[1]-b[1], b[2]-real[2], b[3]-real[3]), 5)
        # 投影开着（默认）外框要更大，但不离谱
        cfg = config('fx_styles.json')
        self.e.update(text='通气阀防臭', style={'profile': 'brand_label', 'size': .0616, 'static': True}, overrides={})
        shadow = estimate(self.e, 2160, 2880, cfg)['bbox_px']
        self.assertTrue(shadow[0] < 894 and shadow[1] < 1407 and shadow[3] > 1478 and shadow[2]-shadow[0] < 373+120, shadow)
        # 深色底胶囊实测（苹方中等 .0519）：延伸 0/0 和样式默认延伸 .183/.108
        for text, extend, real in (('通气阀防臭', (0, 0), [898, 1397, 1262, 1487]),
                                   ('通气阀防臭', (.183, .108), [878, 1385, 1282, 1499]), ('Oatey', (.183, .108), [949, 1385, 1211, 1499])):
            self.e.update(text=text, style={'profile': 'brand_label_dark', 'size': .0519, 'static': True},
                          overrides={'ExtendHorizontal2': extend[0], 'ExtendVertical2': extend[1]})
            b = estimate(self.e, 2160, 2880, cfg)['bbox_px']
            self.assertTrue(b[0] <= real[0] and b[1] <= real[1] and b[2] >= real[2] and b[3] >= real[3], (text, b))
            self.assertLess(max(real[0]-b[0], real[1]-b[1], b[2]-real[2], b[3]-real[3]), 12)

    def test_collection_index_is_checked_by_font_name(self):
        # macOS 升级后字体合集里的顺序会变：配置的序号对不上名字时按字体名找；量字用找到的那个，路径（校准表的键）不变
        from fontTools.ttLib import TTFont, TTCollection
        from engine.fx import safety
        for cache in (safety.MEMBER.clear, safety.font_metrics.cache_clear, safety.collection_member.cache_clear):
            cache();self.addCleanup(cache)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);regular=TTFont(self.fonts/'test.ttf');bold=TTFont(self.fonts/'test.ttf')
            for platform in ((3,1,0x409),(1,0,0)):bold['name'].setName('Bold',2,*platform)
            bold['hmtx']['A']=(700,50)
            collection=TTCollection();collection.fonts=[regular,bold];collection.save(root/'test.ttc')
            style=lambda weight,file:{'font':'SafetyTest','style':weight,'font_file':file}
            def cfg(**styles):return {**self.cfg,'styles':styles}
            # 序号对：和以前一样，不记换号
            right=safety.font_path(style('Bold','test.ttc#1'),{},cfg(a=style('Bold','test.ttc#1')),root)
            self.assertEqual(right,root/'test.ttc#1');self.assertEqual(safety.MEMBER,{})
            self.assertEqual(safety.font_metrics(str(right),'A')['lines'][0][0],700)
            # 序号错（#1 现在是 Bold，要的是 Regular）、序号超出个数：都按名字找到 #0
            for file in ('test.ttc#1','test.ttc#9'):
                safety.MEMBER.clear();safety.font_metrics.cache_clear()
                moved=safety.font_path(style('Regular',file),{},cfg(a=style('Regular',file)),root)
                self.assertEqual(moved,root/file);self.assertEqual(safety.MEMBER,{str(root/file):0})
                self.assertEqual(safety.font_metrics(str(moved),'A')['lines'][0][0],600)
            # 合集里根本没有这个字体：中文提示，不拿别的字体顶替
            with self.assertRaisesRegex(ValueError,'找不到「SafetyTest Heavy」.*字体册'):
                safety.font_path(style('Heavy','test.ttc#0'),{},cfg(a=style('Heavy','test.ttc#0')),root)

    def test_default_fade_in_rise_is_inside_bounds(self):
        # 2026-11-12 默认淡入从下方 28px（4K 竖版）升上来：外框向下多 28px；static=true 或 motion.in=false 不加
        static=self.bounds(2160,2880)['bbox_px']
        self.e['style'].pop('static')
        moving=self.bounds(2160,2880)['bbox_px']
        self.assertAlmostEqual(static[1]-moving[1],.01296*2160,places=3);self.assertEqual(static[3],moving[3])
        self.e['motion']={'in':False,'out':True}
        self.assertEqual(self.bounds(2160,2880)['bbox_px'],static)

    def test_recursive_fonts_by_filename_and_family_with_cached_metadata(self):
        from engine.fx.safety import font_path, font_names, sizes
        import shutil
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); nested=root/'5.0无版权字体'; nested.mkdir()
            target=nested/'test.ttf';shutil.copy(self.fonts/'test.ttf',target)
            style=self.cfg['styles']['plain']
            self.assertEqual(font_path(style,{},self.cfg,root),target)
            self.cfg['styles']['plain'].pop('font_file')
            font_names.cache_clear()
            for _ in range(2):self.assertEqual(font_path(style,{},self.cfg,root),target)
            self.assertEqual(font_names.cache_info().hits,1)
            # A newly installed nested font is visible without restarting Python.
            target.rename(nested/'new.ttf')
            self.assertEqual(font_path(style,{},self.cfg,root),nested/'new.ttf')
        self.assertEqual(sizes(self.e,self.cfg),(.05,.05))

    def test_text_point_size_and_textplus_both_supported(self):
        self.e['style'].update(engine='Text',size=76.8)
        self.assertAlmostEqual(self.bounds()['bbox_px'][2]-self.bounds()['bbox_px'][0],88.48)
        self.assertTrue(self.check()['valid'])

    def test_alignment_and_clip_pan_tilt_zoom(self):
        self.e['animation']=[{'input':'Size','frame':110,'value':.05}]
        center=self.bounds()['bbox_px']
        self.e['anchor']['horizontal_align']='left'
        left=self.bounds()['bbox_px']
        self.assertAlmostEqual(left[0]-center[0],600*.0768)
        self.e['anchor']['vertical_align']='top'
        self.assertAlmostEqual(self.bounds()['bbox_px'][3],.65*1080+2)
        self.e['anchor'].update(horizontal_align='center',vertical_align='center')
        self.e['overrides'].update({'clip.Pan':120,'clip.Tilt':-50,'clip.ZoomX':2,'clip.ZoomY':1.5})
        moved=self.bounds()['bbox_px']
        self.assertAlmostEqual(moved[0],960+(center[0]+2-960)*2+120-2)
        self.assertAlmostEqual(moved[1],540+(center[1]+2-540)*1.5-50-2)

    def test_multiline_and_animation_peak_and_bezier_hull(self):
        normal=self.bounds()['bbox_px'];self.e['text']='A\nB'
        self.assertGreater(self.bounds()['bbox_px'][3]-self.bounds()['bbox_px'][1],normal[3]-normal[1])
        self.e['text']='AB';self.e['animation']=[{'input':'Size','frame':112,'value':.2}]
        self.assertEqual(self.bounds()['sizes'][1],.2)
        self.e['overrides']['SizeKeyframes']={'0':{'1':.05,'RH':{'1':1,'2':.25}},'10':{'1':.1}}
        self.assertAlmostEqual(self.bounds()['sizes'][1],.3)

    def test_compare_background_expands_bounds_and_keeps_photo_rules(self):
        self.e['kind']='compare_label'
        self.cfg['styles']['plain']['shadow_enabled']=False
        normal=self.bounds()['bbox_px']
        self.cfg['styles']['plain']['background']={'extend_horizontal':.1,'extend_vertical':.04}
        self.cfg['safety']['font_calibration']['test.ttf']['Text+']['border_fill']={'height_ratio':.78,'horizontal_padding_ratio':.1}
        larger=self.bounds()['bbox_px']
        self.assertLess(larger[0],normal[0]);self.assertLess(larger[1],normal[1])
        self.assertGreater(larger[2],normal[2]);self.assertGreater(larger[3],normal[3])
        self.e['anchor']['subject_kind']='photo'
        self.assertEqual(self.check()['bounds'][0]['edge_ratio'],.03)

    def test_shadow_and_outline_expand_bounds(self):
        normal=self.bounds()['bbox_px'];self.e['overrides'].update(Enabled3=1,Offset3={'1':.01,'2':-.01},SoftnessX3=0,SoftnessY3=0)
        shadow=self.bounds()['bbox_px']
        self.assertGreater(shadow[2],normal[2]);self.assertLess(shadow[1],normal[1])
        self.e['overrides']['Thickness3']=.1
        self.assertGreater(self.bounds()['bbox_px'][2],shadow[2])

    def test_landscape_and_portrait_margin_use_short_side(self):
        for w,h in [(1920,1080),(1080,1920)]:
            self.plan['timeline'].update(width=w,height=h)
            self.e['anchor']['center']=[0,.7]
            result=self.check();v=next(v for v in result['violations'] if v['rule']=='edge')
            self.assertAlmostEqual(v['excess_px'],min(w,h)*.03-result['bounds'][0]['bbox_px'][0],places=3)
            self.assertIn('右移',v['message']);self.assertGreater(v['suggested_delta'][0],0)

    def test_subtitle_default_and_off_time_disabled_cues(self):
        self.e['anchor']['center']=[.5,.19]
        self.e['anchor']['overlap_ok']=True
        r=self.check();v=next(v for v in r['violations'] if v['rule']=='subtitle')
        self.assertIn('上移',v['message']);self.assertGreater(v['excess_px'],0)
        timeline={**self.plan['timeline'],'items':[{'type':'subtitle','enabled':False,'start':250,'end':290,
                                                  'props':{'SubtitleBoundingBox':[.2,.35,.8,.5]}}]}
        self.e['anchor']['center']=[.5,.45]
        r=self.check(timeline=timeline)
        self.assertFalse(r['subtitle_zone']['fallback_used'])
        self.assertTrue(any(v['rule']=='subtitle' for v in r['violations']))
        timeline['items'].append({'type':'subtitle','props':{}})
        self.assertTrue(self.check(timeline=timeline)['subtitle_zone']['fallback_used'])

    def test_subject_overlap_gap_and_explicit_overlap_ok(self):
        self.e['anchor']['center']=[.5,.47]
        v=next(v for v in self.check()['violations'] if v['rule']=='subject')
        self.assertIn('半个字高',v['message']);self.assertIn('上移',v['message'])
        self.e['anchor']['overlap_ok']=True
        self.assertTrue(self.check()['valid'])
        self.e['anchor']['center']=[0,.1]
        self.assertEqual({v['rule'] for v in self.check()['violations']},{'edge','subtitle'})

    def test_photo_label_default_allows_only_slight_top_overlap(self):
        self.e['anchor']['subject_kind']='photo'
        # Move so 30% of its outer height enters the photo's top edge.
        b=self.bounds()['bbox'];h=b[3]-b[1]
        self.e['anchor']['center'][1]+=.45-.30*h-b[1]
        self.assertTrue(self.check()['valid'])
        self.e['anchor']['overlap_ok']=False
        self.assertTrue(any(v['rule']=='subject' for v in self.check()['violations']))
        self.e['anchor']['overlap_ok']=True
        self.e['anchor']['center'][1]-=.2
        self.assertTrue(any(v['rule']=='subject' for v in self.check()['violations']))

    def test_too_large_reports_cannot_only_move(self):
        self.e['style']['size']=.95
        self.assertTrue(any('无法仅平移' in v['message'] for v in self.check()['violations']))

    def test_allow_unsafe_keeps_violations_and_warning(self):
        self.e['anchor']['center']=[0,.1]
        self.assertFalse(self.check()['valid'])
        r=self.check(allow_unsafe=True)
        self.assertTrue(r['valid']);self.assertTrue(r['unsafe_skipped'])
        self.assertIn('--allow-unsafe',r['warnings'][0]);self.assertTrue(r['violations'])

    def test_unknown_font_missing_glyph_dimensions_and_false_overlap_flag(self):
        self.e['overrides']['Font']='missing'
        with self.assertRaisesRegex(ValueError,'找不到字体'):self.check(allow_unsafe=True)
        del self.e['overrides']['Font'];self.e['text']='C'
        with self.assertRaisesRegex(ValueError,'缺少字形'):self.check()
        self.e['text']='AB';del self.plan['timeline']['width']
        with self.assertRaisesRegex(ValueError,'分辨率'):self.check()
        self.e['anchor']['overlap_ok']='true'
        self.assertTrue(validation_report(self.plan,allow_unsafe=True)['errors'])

    def test_plan_gate_and_override(self):
        self.e['anchor']['center']=[0,.1]
        with patch('engine.fx.plan.config',side_effect=lambda name:self.cfg if name=='fx_styles.json' else config(name)),patch('engine.fx.safety.font_path',return_value=self.fonts/'test.ttf'):
            self.assertFalse(validation_report(self.plan)['valid'])
            self.assertTrue(validation_report(self.plan,allow_unsafe=True)['valid'])
            self.plan['effects'][0]['text']=''
            self.assertFalse(validation_report(self.plan,allow_unsafe=True)['valid'])

    def test_build_dry_and_apply_gate_before_writes_and_explicit_bypass(self):
        self.e['speech']={'mode':'visual'}
        self.e['anchor']['center']=[0,.1]
        tl={**self.plan['timeline'],'id':'main','items':[],'tracks':[],'markers':{}}
        before={'timeline':tl,'timelines':[{'name':'main','id':'main'}]}
        self.e['precondition_snapshot_sha256']=digest(tl)
        t=Mock();t.GetName.return_value='main';t.GetStartFrame.return_value=100;t.GetSetting.return_value=29.97
        io=Mock();io.snapshot.return_value=before;io.db={}
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'plan.json';path.write_text(json.dumps(self.plan))
            with patch('engine.fx.executor.ResolveIO',return_value=io),patch('engine.fx.executor.snapshot',return_value=before), \
                 patch('engine.fx.plan.config',side_effect=lambda name:self.cfg if name=='fx_styles.json' else config(name)),patch('engine.fx.safety.font_path',return_value=self.fonts/'test.ttf'):
                for apply in (False,True):
                    result=run(Mock(),Mock(),t,'p',path,td,apply)
                    self.assertFalse(result['applied']);self.assertEqual(result['effects'],0)
                    self.assertEqual(result['needs_user_decision'][0]['effect_id'],'sample')
                    io.write.assert_not_called();io.new_timeline.assert_not_called()
                r=run(Mock(),Mock(),t,'p',path,td,allow_unsafe=True)
                self.assertTrue(r['safety']['unsafe_skipped'])
                # Apply clears the safety gate but deliberately fails at the first backup write.
                io.write.return_value=False
                with self.assertRaisesRegex(RuntimeError,'备份失败'):run(Mock(),Mock(),t,'p',path,td,True,allow_unsafe=True)
                self.assertEqual(io.write.call_count,1)

    def test_default_and_floor_work_for_both_engines_and_zoom(self):
        from engine.fx.safety import default_size,with_default_sizes
        for engine in ('Text+','Text'):
            self.e['style']['engine']=engine
            base=default_size(self.e,self.cfg,self.fonts)
            self.e['style']['size']=base*.75
            self.e['animation']=[{'input':'Size','frame':110,'value':base*.75}]
            self.assertTrue(self.check()['valid'])
            self.e['style']['size']=base*.749
            self.assertIn('minimum_size',{v['rule'] for v in self.check()['violations']})
            del self.e['style']['size']
            self.assertAlmostEqual(with_default_sizes(self.plan,self.cfg,self.fonts)['effects'][0]['style']['size'],base)
            self.e['style']['size']=base;self.e['animation']=[]
        self.e['style'].update(engine='Text+',size=.05)
        self.e['overrides']['clip.ZoomX']=.5
        self.assertIn('minimum_size',{v['rule'] for v in self.check()['violations']})

    def test_person_15_percent_boundary_not_applied_to_phone_or_photo(self):
        self.e['anchor']['center']=[.5,.65]
        b=self.bounds()['bbox'];width=b[2]-b[0]
        for ratio,valid in ((.15,True),(.151,False)):
            self.e['anchor'].update(subject_kind='person',subject_bbox=[b[2]-width*ratio,b[1],.9,b[3]])
            self.assertEqual(self.check()['valid'],valid)
        for kind in ('phone','photo'):
            self.e['anchor']['subject_kind']=kind
            self.assertFalse(self.check()['valid'])

    def test_only_photo_and_keywords_get_three_percent(self):
        for kind,ratio in [('photo_label',.03),('keyword',.03),('spec',.05),('lens_label',.05)]:
            self.e['kind']=kind
            self.assertEqual(self.check()['bounds'][0]['edge_ratio'],ratio)

    def test_partial_build_skips_one_effect_but_prepares_the_other(self):
        self.e['speech']={'mode':'visual'}
        self.e['anchor']['center']=[0,.1]
        good=copy.deepcopy(self.e);good.update(effect_id='good',start=210,end=240)
        good['anchor']['center']=[.5,.65];self.plan['effects'].append(good)
        tl={**self.plan['timeline'],'id':'main','items':[],'tracks':[],'markers':{}}
        before={'timeline':tl,'timelines':[{'name':'main','id':'main'}]}
        for e in self.plan['effects']:e['precondition_snapshot_sha256']=digest(tl)
        t=Mock();t.GetName.return_value='main';t.GetStartFrame.return_value=100;t.GetSetting.return_value=29.97
        io=Mock();io.db={};io.snapshot.return_value=before
        with tempfile.TemporaryDirectory() as td,patch('engine.fx.executor.ResolveIO',return_value=io), \
             patch('engine.fx.executor.snapshot',return_value=before), \
             patch('engine.fx.plan.config',side_effect=lambda name:self.cfg if name=='fx_styles.json' else config(name)), \
             patch('engine.fx.safety.font_path',return_value=self.fonts/'test.ttf'):
            path=Path(td)/'plan.json';path.write_text(json.dumps(self.plan))
            result=run(Mock(),Mock(),t,'p',path,td)
            self.assertEqual(result['effects'],1);self.assertEqual(result['skipped_effect_ids'],['sample'])
            io.new_timeline.assert_not_called()

if __name__=='__main__':unittest.main()
