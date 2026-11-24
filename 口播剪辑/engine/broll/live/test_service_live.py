import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).absolute().parents[3]))
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from copy import deepcopy
import json
from engine.broll.live._fake_resolve import Resolve, Media
from engine.broll.live.resolve_adapter import Adapter
from engine.broll.live.service import Session, check_adjust_scope, preflight, main_for_review
from engine.broll.live.writer import snapshot_hash
from engine.broll.live.ledger import exact_state
from engine.broll.live.arrange import default_profile

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.r=Resolve();self.io=Adapter(self.r,self.r.project,self.r.project.current)
        self.sleep=patch('engine.fx.resolve_io.time.sleep');self.sleep.start();self.addCleanup(self.sleep.stop)
        self.config={'creator':'示例IP','ledger_index':str(self.root/'registry.json'),'learning_root':str(self.root/'learning')}
        self.s=Session(self.io,self.config,self.root/'film')
        self.source=self.root/'product.mov';self.source.write_bytes(b'fake test media')
        self.io.p.pool.root.clips.append(Media(str(self.source)))
        self.catalog={name:dict(segment=name,parent_segment=name,file=str(self.source),file_path=str(self.source),kind='video',in_media_pool=True,fps='29.97',vfr=False,duration=40,usable_start=start,usable_end=start+600,focus='sharp',subject='centered',people='none',use='operation',sample_of=None,probe={'has_audio':True},size=self.source.stat().st_size,mtime_ns=self.source.stat().st_mtime_ns) for name,start in [('one',0),('two',600),('three',1200)]}
        self.s.catalog=self.catalog
        self.matches={'version':3,'segments':[{'spot':'S01','lines':[2,2],'shots':[{'end':200,'candidates':[{'segment':'one','reason':'产品画面对上口播'},{'segment':'two','reason':'产品操作近景'}]}]}],'face':[]}
    def test_auto_adopt_then_noop_reconcile_only_touches_the_ledger(self):
        # 学偏好已下线：配置里还留着 learning_root 也不读不写；对账只动台账和台账索引
        self.assertTrue(self.s.path.exists());self.assertFalse((self.root/'learning').exists())
        book=json.loads(self.s.path.read_text());self.s.reconcile();after=json.loads(self.s.path.read_text())
        self.assertEqual({k:v for k,v in after.items() if k!='last_learned'},{k:v for k,v in book.items() if k!='last_learned'})
        self.assertFalse((self.root/'learning').exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['film','product.mov','registry.json','registry.lock'])
        self.assertFalse(hasattr(self.s,'events'));self.assertFalse(hasattr(self.s,'learning_command'))
    def test_session_and_preflight_no_longer_need_a_learning_root(self):
        from types import SimpleNamespace
        cfg={'creator':'示例IP','ledger_index':str(self.root/'registry.json')}
        s=Session(self.io,cfg,self.root/'film');self.assertEqual(s.profile,default_profile('示例IP'))
        def run(args,**kw):return SimpleNamespace(returncode=0,stdout='Mach-O arm64 x86_64 zscale',stderr='')
        with patch('engine.broll.live.service.shutil.which',return_value='/fake/ffmpeg'),patch('engine.broll.live.service.sys.flags',SimpleNamespace(utf8_mode=1)):
            _,env=preflight({**cfg,'nas_mount':str(self.root)},connector=lambda _: (self.r,self.io.p,self.io.main),runner=run)
            self.assertEqual(env['数据库'],'Disk')
            # 操作台账要用的台账索引仍然必须配置、且在 NAS 内
            for bad in ({'creator':'示例IP'},{**cfg,'ledger_index':'/别处/台账索引.json'}):
                with self.assertRaisesRegex(ValueError,'ledger_index'):preflight({**bad,'nas_mount':str(self.root)},connector=lambda _:None,runner=run)
    def test_unknown_creator_asks_once(self):
        cfg=deepcopy(self.config);cfg.pop('creator')
        with self.assertRaisesRegex(ValueError,'创作者'):Session(self.io,cfg,self.root/'other')
    def test_prepare_direct_preserves_candidates_null_quality_and_no_write(self):
        p=self.s.prepare(self.matches);self.assertEqual(len(p['operations']),1);op=p['operations'][0]
        self.assertFalse(op['new']['generated']);self.assertEqual(op['new']['offset'],0);self.assertEqual(len(op['candidates']),2)
        self.assertEqual(self.io.p.pool.append_count,0)

    def test_dry_apply_outputs_sheet_without_resolve_writes(self):
        path=str(self.s.work/'演练核图/test.jpg')
        with patch('engine.broll.live.service.media.rehearsal_sheet',return_value=path) as sheet:
            out=self.s.apply(self.matches)
        self.assertEqual(out['演练核图'],path)
        shot=sheet.call_args[0][0][0]
        self.assertEqual((shot['file'],shot['source_start'],shot['end']-shot['start']),(str(self.source),0,100))
        self.assertEqual(self.io.p.pool.append_count,0)

    def test_written_apply_and_dry_adjust_keep_full_report(self):
        out=self.s.apply(self.matches,True)
        self.assertEqual(out['placements'][0]['file'],self.source.name)
        self.assertEqual(out['placements'][0]['source_start'],0)
        self.assertGreater(out['coverage'],out['previous_coverage'])
        self.s.snapshot=self.io.snapshot()
        dry=self.s.adjust(['S01'],remove=True)
        self.assertEqual(dry['coverage'],0)
        self.assertGreater(dry['previous_coverage'],0)
        self.assertIn('product.mov',dry['report']);self.assertIn('匹配遵守率：100.0%',dry['report'])
        wet=self.s.adjust(['S01'],remove=True,apply=True)
        self.assertEqual((wet['coverage'],wet['previous_coverage']),(dry['coverage'],dry['previous_coverage']))

    def test_noop_apply_still_reports_skips_filters_and_coverage(self):
        self.catalog['one']['people']='host';self.catalog['two']['people']='host'
        for apply in (False,True):
            out=self.s.apply(self.matches,apply)
            self.assertFalse(out['applied']);self.assertEqual(len(out['skipped']),1)
            self.assertEqual(out['compliance']['considered'],2)
            self.assertEqual(out['compliance']['filtered_candidates'],2)
            self.assertIn('people=host',out['report'])

    def test_lut_filter_and_quality_filter_retain_raw_candidate_count(self):
        self.catalog['one'].update(is_log=True,input_lut=None)
        self.catalog['two']['people']='host'
        out=self.s.apply(self.matches)
        self.assertEqual(out['compliance']['considered'],2)
        self.assertEqual(out['compliance']['filtered_candidates'],2)
        self.assertEqual(out['compliance']['rate'],0)
        self.assertIn('Log',out['report']);self.assertIn('people=host',out['report'])
    def test_t4_legacy_target_render_uses_safe_filename(self):
        import re
        self.s.apply(self.matches,True);self.s.snapshot=self.io.snapshot()
        legacy='[D01] 主选:video:2:1';self.s.ledger['spots'][legacy]=self.s.ledger['spots'].pop('S01')
        v=deepcopy(self.matches);v['segments'][0]['spot']=legacy;self.catalog['two']['fps']=30
        output=self.root/'generated.mov';output.write_bytes(b'fixture')
        with patch('engine.broll.live.service.media.render_live_clip',return_value={'path':str(output),'startFrame':0}) as render:
            plan=self.s.prepare(v,[legacy])
        self.assertEqual(plan['operations'][0]['spot'],legacy)
        self.assertTrue(re.fullmatch(r'[\w-]{1,64}',render.call_args[0][2]))
    def test_apply_then_adjust_only_named_one(self):
        out=self.s.apply(self.matches,True);self.assertTrue(out['applied'])
        self.s.snapshot=self.io.snapshot()
        out=self.s.adjust(['S01'],use=2,apply=True);self.assertTrue(out['applied']);self.assertEqual(self.s.ledger['spots']['S01']['segment'],'two')
    def test_n20_adjustment_surviving_next_reconcile_is_approved_in_ledger(self):
        self.s.apply(self.matches,True);self.s.snapshot=self.io.snapshot()
        self.s.adjust(['S01'],use=2,apply=True)
        self.assertTrue(self.s.ledger['spots']['S01']['pending_replacement'])
        self.s.reconcile()
        self.assertTrue(self.s.ledger['spots']['S01']['approved'])
        self.assertEqual(self.s.profile,default_profile('示例IP'))
        self.assertNotIn('pending_replacement',self.s.ledger['spots']['S01'])
    def test_n18_unrelated_existing_flash_does_not_block_writer_readback(self):
        from engine.broll.live._fake_resolve import Item
        from engine.broll.live._fixtures import item
        for name,a,b in [('left',0,98),('right',100,200)]:
            path=str(self.source);self.io.main.items.append(Item(item(name,a,b,path=path),self.io.p.pool.find(path)))
        self.s.reconcile();v=deepcopy(self.matches);v['segments'][0].update(lines=[4,4],shots=[{'end':400,'candidates':[{'segment':'three','reason':'其他句产品画面'}]}])
        self.assertTrue(self.s.apply(v,True)['applied'])
    def test_reconcile_does_not_reauthorize_manual_clip_overwrite(self):
        self.s.apply(self.matches,True)
        old=self.s.ledger['spots']['S01']['placement'];handle=next(i for i in self.io.main.items if i.GetUniqueId()==old['id']);handle.SetProperty('Pan',5)
        self.s.reconcile();self.assertEqual(self.s.ledger['spots']['S01']['placement']['props']['Pan'],5)
        out=self.s.adjust(['S01'],use=2,apply=True)
        self.assertFalse(out['applied']);self.assertEqual(handle.GetProperty('Pan'),5)
    def test_remove_reserved_then_next_plan_and_explicit_face_off(self):
        self.s.apply(self.matches,True);self.s.snapshot=self.io.snapshot()
        self.s.adjust(['S01'],remove=True,apply=True)
        self.s.snapshot=self.io.snapshot();fresh=deepcopy(self.matches);fresh['segments'][0]['spot']='new_S01'
        self.assertEqual(self.s.prepare(fresh)['operations'],[])
        self.assertTrue(self.s.adjust(['S01'],face='off',apply=True)['applied'])
    def test_audio_on_and_off_only_named(self):
        self.s.apply(self.matches,True);self.s.snapshot=self.io.snapshot()
        self.s.adjust(['S01'],audio='on',apply=True);self.assertEqual(self.s.ledger['spots']['S01']['audio']['props']['AudioVolume'],-5)
        self.s.snapshot=self.io.snapshot();self.s.adjust(['S01'],audio='off',apply=True);self.assertNotIn('audio',self.s.ledger['spots']['S01'])
    def test_segmented_spot_stores_two_shots_without_overwrite(self):
        self.matches['segments'][0].update(lines=[2,3],shots=[{'end':200,'candidates':[{'segment':'one','reason':'第一句产品操作'}]},{'end':300,'candidates':[{'segment':'two','reason':'第二句产品操作'}]}])
        self.s.apply(self.matches,True)
        self.assertIn('S01',self.s.ledger['spots']);self.assertIn('S01-shot2',self.s.ledger['spots'])
        self.s.snapshot=self.io.snapshot();self.s.adjust(['S01'],remove=True,apply=True)
        self.assertTrue(all(r['status']=='deleted' for r in self.s.ledger['spots'].values()))
    def test_lut_skip_tries_secondary_and_retains_original_candidates(self):
        self.catalog['one'].update(is_log=True,input_lut=None)
        p=self.s.prepare(self.matches)
        self.assertEqual(p['operations'][0]['new']['segment'],'two');self.assertEqual(len(p['operations'][0]['candidates']),2)
        self.assertIn('Log',p['report'])
    def test_adjust_scope_checks_set_not_only_count(self):
        with self.assertRaisesRegex(ValueError,'范围'):check_adjust_scope([{'spot':'S02'}],['S01'])
        with self.assertRaises(ValueError):check_adjust_scope([{'spot':'S01'},{'spot':'S02'}],['S01'])
        check_adjust_scope([{'spot':'S01'},{'spot':'S01'}],['S01'])
    def test_review_playhead_resolves_main_via_registry(self):
        self.s.apply(self.matches,True);self.s.snapshot=self.io.snapshot();self.s.adjust(['S01'],use=2,apply=True)
        name=next(iter(self.s.ledger['reviews']));t=next(t for t in self.io.p.timelines if t.name==name);self.io.p.current=t
        io,project_dir=main_for_review(Adapter(self.r,self.io.p,t),self.config)
        self.assertEqual(io.main_name,'main');self.assertEqual(Path(project_dir),self.root/'film')
    def test_clean_keeps_media_pool_references_and_moves_unreferenced(self):
        folder=self.s.work/'B-roll片段';folder.mkdir(parents=True)
        used=folder/'used.mov';used.write_bytes(b'a');free=folder/'free.mov';free.write_bytes(b'b');free.with_suffix('.json').write_text('{}')
        self.io.p.pool.root.clips.append(Media(str(used)))
        rows=self.s.clean(apply=True)['清单'];self.assertEqual(len(rows),2);self.assertTrue(used.exists());self.assertTrue((folder/'待清理/free.mov').exists())
    def test_clean_without_confirmation_only_lists_and_moves_nothing(self):
        # B7：以前不带 --yes 就把文件移进“待清理”；现在不带确认参数只列清单
        folder=self.s.work/'B-roll片段';folder.mkdir(parents=True)
        free=folder/'free.mov';free.write_bytes(b'b');free.with_suffix('.json').write_text('{}');(folder/'voice.wav').write_bytes(b'w')
        before=sorted(str(p.relative_to(folder)) for p in folder.rglob('*'))
        out=self.s.clean()
        self.assertEqual(sorted(Path(r['path']).name for r in out['清单']),['free.json','free.mov','voice.wav'])
        self.assertEqual(out['已删除'],[]);self.assertIn('没有移动',out['message'])
        self.assertEqual(sorted(str(p.relative_to(folder)) for p in folder.rglob('*')),before)   # 一个文件都没动，也没建“待清理”
        self.assertFalse((folder/'待清理').exists())
        self.s.clean(apply=True);self.assertTrue((folder/'待清理/free.mov').exists());self.assertFalse(free.exists())
        self.assertEqual(self.s.clean()['待清理里已有'],sorted(str(folder/'待清理'/n) for n in ('free.json','free.mov','voice.wav')))
        self.assertTrue((folder/'待清理/free.mov').exists())                                      # 再演练：待清理里的也不删
    def test_clean_cli_forwards_confirmation_flags(self):
        import importlib.util
        from unittest.mock import Mock
        from contextlib import redirect_stdout
        from io import StringIO
        spec=importlib.util.spec_from_file_location('broll_cli_b7',Path(__file__).absolute().parents[3]/'bin/broll.py');cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        for argv,expected in ((['clean'],(False,False)),(['clean','--apply'],(False,True)),(['clean','--yes'],(True,False))):
            session=Mock();session.clean.return_value={}
            io=Mock()
            with redirect_stdout(StringIO()):self.assertEqual(cli.main(argv,preflight=lambda cfg,tl:(io,{'数据库':'Disk'}),session_type=lambda *a,**k:session),0)
            session.clean.assert_called_once_with(*expected)
    def test_preflight_all_checks_and_failure_paths(self):
        from types import SimpleNamespace
        cfg={**self.config,'nas_mount':str(self.root)}
        def run(args,**kw):return SimpleNamespace(returncode=0,stdout='Mach-O arm64 x86_64 zscale',stderr='')
        with patch('engine.broll.live.service.shutil.which',return_value='/fake/ffmpeg'),patch('engine.broll.live.service.sys.flags',SimpleNamespace(utf8_mode=1)):
            io,env=preflight(cfg,connector=lambda _: (self.r,self.io.p,self.io.main),runner=run);self.assertEqual(env['数据库'],'Disk')
            def broken(args,**kw):return SimpleNamespace(returncode=0,stdout='arm64 x86_64',stderr='')
            with self.assertRaisesRegex(ValueError,'zscale'):preflight(cfg,connector=lambda _:None,runner=broken)
    def test_preflight_without_nas_mount_uses_the_share_the_repository_sits_on(self):
        from types import SimpleNamespace
        from engine.broll.live import service
        def run(args,**kw):return SimpleNamespace(returncode=0,stdout='Mach-O arm64 x86_64 zscale',stderr='')
        # 假装本仓库在 <临时目录>/ThinkCut/口播剪辑：NAS 共享根就是 <临时目录>（挂成 /Volumes/work-1 时同理）
        root=self.root.resolve();fake=root/'ThinkCut'/'口播剪辑'/'engine'/'broll'/'live'/'service.py'
        cfg={'creator':'示例IP','ledger_index':str(root/'ThinkCut'/'B-roll学习库'/'台账索引.json'),'learning_root':str(root/'ThinkCut'/'B-roll学习库')}
        with patch('engine.broll.live.service.shutil.which',return_value='/fake/ffmpeg'),patch('engine.broll.live.service.sys.flags',SimpleNamespace(utf8_mode=1)), \
             patch.object(service,'__file__',str(fake)):
            _,env=preflight(cfg,connector=lambda _: (self.r,self.io.p,self.io.main),runner=run)
            self.assertEqual(env['NAS'],str(root))
            # 配置里写了 nas_mount 的照旧以配置为准
            with self.assertRaisesRegex(ValueError,'NAS未挂载：/Volumes/没有这个盘'):
                preflight({**cfg,'nas_mount':'/Volumes/没有这个盘'},connector=lambda _:None,runner=run)
    def test_stale_draft_basis_refuses_before_placement(self):
        (self.s.work/'匹配依据.json').write_text(json.dumps({'basis_hash':'old'}))
        with self.assertRaisesRegex(ValueError,'依据已变化'):self.s.prepare(self.matches)
        self.assertEqual(self.io.p.pool.append_count,0)
    def test_full_sentence_face_reservation(self):
        self.s.apply(self.matches,True);self.s.snapshot=self.io.snapshot();self.s.adjust(['S01'],face='on',apply=True)
        self.assertEqual(self.s.ledger['reserved'][0]['source_ranges'][0]['start'],1100)
        self.assertEqual(self.s.ledger['reserved'][0]['source_ranges'][0]['end'],1200)
    def test_clean_yes_deletes_previously_staged_only_after_ref_check(self):
        folder=self.s.work/'B-roll片段';folder.mkdir(parents=True);(folder/'free.mov').write_bytes(b'b')
        self.s.clean(apply=True);self.assertTrue((folder/'待清理/free.mov').exists())
        self.s.clean(True);self.assertFalse((folder/'待清理/free.mov').exists())
    def test_two_modified_copies_require_explicit_selection(self):
        self.s.apply(self.matches,True);state=self.s.ledger['spots']['S01']['placement']
        original=next(i for i in self.io.main.items if i.GetUniqueId()==state['id']);original.SetProperty('Pan',5)
        copy=self.io.main.DuplicateTimeline('copy');next(i for i in copy.items if i.GetName()==state['name']).SetProperty('Pan',6)
        with self.assertRaisesRegex(ValueError,'哪条'):Session(self.io,self.config,self.root/'film')
    def test_generated_audio_only_on_request(self):
        self.s.apply(self.matches,True);row=self.s.ledger['spots']['S01'];row['placement']['media_path']=str(self.root/'generated.mov');row['source_start']=30
        h=next(i for i in self.io.main.items if i.GetUniqueId()==row['placement']['id']);h.media=Media(row['placement']['media_path'])
        self.s.snapshot=self.io.snapshot();row['placement']=next(r for r in self.s.snapshot['timeline']['items'] if r['id']==h.GetUniqueId());row['guard_state']=deepcopy(row['placement'])
        output=self.root/'audio.wav';output.write_bytes(b'audio test')
        with patch('engine.broll.live.audio_clip.render_audio',return_value={'path':str(output),'startFrame':0,'frames':100}) as render:
            out=self.s.adjust(['S01'],audio='on',apply=True)
        self.assertTrue(out['applied']);render.assert_called_once();self.assertEqual(self.s.ledger['spots']['S01']['audio']['media_path'],str(output))
    def test_feedback19_original_names_and_items_survive_apply_and_adjust(self):
        media=next(m for m in self.io.p.pool.root.clips if m.GetClipProperty('File Path')==str(self.source))
        media.props['Clip Name']='用户媒体池原名称'
        with patch('engine.broll.live.service.media.render_live_clip',side_effect=AssertionError('普通素材不可渲染')):
            self.s.apply(self.matches,True)
            state=self.s.ledger['spots']['S01']['placement']
            self.assertEqual(state['name'],'用户媒体池原名称')
            self.assertEqual(state['broll_extra']['media_id'],media.GetUniqueId())
            self.assertEqual(state['media_path'],str(self.source))
            self.s.snapshot=self.io.snapshot()
            self.s.adjust(['S01'],use=2,apply=True)
            self.assertEqual(self.s.ledger['spots']['S01']['placement']['name'],'用户媒体池原名称')
        self.assertEqual(media.GetClipProperty('Clip Name'),'用户媒体池原名称')
        self.assertFalse((self.s.work/'B-roll片段').exists())

    def test_feedback19_photo_imports_original_without_render_or_rename(self):
        photo=self.root/'原截图.png';photo.write_bytes(b'fake image')
        for row in self.catalog.values():
            row.update(kind='photo',file=str(photo),file_path=str(photo),in_media_pool=False,
                       size=photo.stat().st_size,mtime_ns=photo.stat().st_mtime_ns)
        with patch('engine.broll.live.service.media.render_live_clip',side_effect=AssertionError('照片不可渲染')):
            out=self.s.apply(self.matches,True)
        self.assertTrue(out['applied'])
        state=self.s.ledger['spots']['S01']['placement']
        self.assertEqual((state['name'],state['media_path']),(photo.name,str(photo)))
        self.assertEqual(sum(m.GetClipProperty('File Path')==str(photo) for m in self.io.p.pool.root.clips),1)

    def test_feedback19_derivative_reason_and_original_source_are_in_ledger(self):
        for row in self.catalog.values():row['hdr']=True
        output=self.root/(self.source.name+'__converted.mov');output.write_bytes(b'fixture')
        with patch('engine.broll.live.service.media.render_live_clip',return_value={'path':str(output),'startFrame':0}):
            self.s.apply(self.matches,True)
        state=self.s.ledger['spots']['S01']
        self.assertEqual(state['source_file'],str(self.source))
        self.assertIn('HDR',state['derivative_reason'])

    def test_feedback19_compound_unresolved_skips_only_it_and_records_other_edit(self):
        from engine.broll.live._fake_resolve import Item
        from engine.broll.live._fixtures import item
        self.s.apply(self.matches,True)
        state=self.s.ledger['spots']['S01']['placement']
        next(i for i in self.io.main.items if i.GetUniqueId()==state['id']).SetProperty('Pan',5)
        clip=Item(item('Compound Clip 2',300,400,path=None),Media('',kind='Compound'))
        self.io.main.items.append(clip)
        self.s.reconcile()
        row=self.s.ledger['spots']['S01'];self.assertEqual((row['status'],row['placement']['props']['Pan']),('modified',5))
        self.assertEqual(self.s.untraced[0]['name'],'Compound Clip 2')
        self.assertTrue((self.s.work/'学习跳过.json').exists())
        self.assertIn(clip,self.io.main.items)
        self.assertEqual(clip.GetName(),'Compound Clip 2')
        self.io.main.items.remove(clip)
        self.s.reconcile()
        self.assertEqual(json.loads((self.s.work/'学习跳过.json').read_text())['items'],[])

    def test_feedback19_compound_and_fusion_trace_nested_original(self):
        from engine.broll.live._fake_resolve import Item, Timeline
        from engine.broll.live._fixtures import item, snapshot
        from engine.broll.live.timeline import build_model
        for typ in ('Compound','Fusion Clip'):
            nested_value=snapshot('inside')
            nested_value['items']=[item('inside',0,500,path=str(self.source),track=1,source_start=50)]
            nested=Timeline(self.io.p,'inside',nested_value)
            media=Media('',kind=typ);media.timeline=nested
            row=item(typ+' 2',300,400,path=None,source_start=10)
            clip=Item(row,media)
            self.io.main.items.append(clip)
            model=build_model(self.io.snapshot())
            traced=next(i for i in model['items'] if i['id']==clip.GetUniqueId())
            self.assertEqual(traced['owner'],'user')
            self.assertEqual(traced['media_path'],str(self.source))
            self.assertEqual(traced['source_start'],60)
            self.assertNotIn('learning_skip_reason',traced)
            self.io.main.items.remove(clip)

    def test_feedback19_duplicate_pool_path_uses_catalog_media_id(self):
        chosen=Media(str(self.source),**{'Clip Name':'另一文件夹的原名'})
        self.io.p.pool.root.clips.append(chosen)
        for row in self.catalog.values():row['media_id']=chosen.GetUniqueId()
        self.s.apply(self.matches,True)
        state=self.s.ledger['spots']['S01']['placement']
        self.assertEqual((state['broll_extra']['media_id'],state['name']),
                         (chosen.GetUniqueId(),'另一文件夹的原名'))

    def test_feedback19_new_ai_directory_review_finds_same_project(self):
        root=self.root/'new-film';(root/'01_原始素材').mkdir(parents=True);(root/'项目.json').write_text('{}')
        self.s=Session(self.io,self.config,root)
        self.s.catalog=self.catalog
        self.assertEqual(self.s.work,root/'02_制作/B-roll_AI')
        self.s.apply(self.matches,True);self.s.snapshot=self.io.snapshot()
        self.s.adjust(['S01'],use=2,apply=True)
        name=next(iter(self.s.ledger['reviews']))
        t=next(t for t in self.io.p.timelines if t.name==name);self.io.p.current=t
        io,project_dir=main_for_review(Adapter(self.r,self.io.p,t),self.config)
        self.assertEqual(Path(project_dir),root)
        self.assertEqual(Session(io,self.config,project_dir,readonly=True).work,self.s.work)
