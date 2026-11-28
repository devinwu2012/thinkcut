"""Offline matting decisions, caching, source visibility and ownership regressions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from engine.fx import matte
from engine.fx.review import matte_review_ranges, place_review_mattes
from engine.fx.executor import removal_plan


class MattingTests(unittest.TestCase):
    def test_alpha_coverage_bottom_left_and_three_decisions(self):
        mask=np.zeros((100,100),dtype='uint8');mask[80:100,0:20]=255
        self.assertEqual(matte.coverage(mask,[0,0,.2,.2]),1)
        self.assertEqual(matte.coverage(mask,[0,.8,.2,1]),0)
        self.assertAlmostEqual(matte.coverage(mask,[0,0,1,1]),.04)
        self.assertEqual([matte.decision(x) for x in (0,.01,.2,.20001,1)],['none','matte','matte','move','move'])
        self.assertEqual(matte.decision(.21,.3),'matte')

    def test_cache_hit_and_source_interval_resolution_invalidation(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'source.mp4';src.write_bytes(b'source')
            key=matte.cache_key(src,1,10,1920,1080);base=Path(d)/key
            base.with_suffix('.mov').write_bytes(b'movie');base.with_suffix('.npz').write_bytes(b'alpha')
            base.with_suffix('.json').write_text(json.dumps({'cache_key':key,'bytes':5,'mask_bytes':5}))
            self.assertTrue(matte.cached(d,key)['cache_hit'])
            self.assertNotEqual(key,matte.cache_key(src,2,10,1920,1080))
            self.assertNotEqual(key,matte.cache_key(src,1,10,2880,2160))
            with patch.object(matte,'MODEL_VERSION','new'):self.assertNotEqual(key,matte.cache_key(src,1,10,1920,1080))
            src.write_bytes(b'changed');self.assertNotEqual(key,matte.cache_key(src,1,10,1920,1080))
            base.with_suffix('.mov').write_bytes(b'broken');self.assertIsNone(matte.cached(d,key))

    def test_stuck_worker_is_stopped_with_chinese_reason_and_log(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'source.mp4';src.write_bytes(b'source');key=matte.cache_key(src,0,90,1920,1080)
            stuck=subprocess.TimeoutExpired('worker',1800,output=b'20/90\n',stderr=None)
            with patch.object(matte,'environment_check',return_value=(True,None)),patch.object(matte.subprocess,'run',side_effect=stuck) as run, \
                 patch.object(matte,'frames_root',return_value=Path(d)/'本机临时'), \
                 self.assertRaisesRegex(RuntimeError,'自动抠像超过 30 分钟还没完成（90 帧），已停止'):
                matte.generate(src,0,90,1920,1080,d)
            # 短片段给保底 30 分钟；进度留在日志里
            self.assertEqual(run.call_args.kwargs['timeout'],30*60)
            self.assertIn('20/90',(Path(d)/'抠像'/(key+'.log')).read_text())

    def test_timeout_message_points_at_local_frames_and_clears_them(self):
        """超时提示说的是本机临时目录（不再是片子目录），并顺手清掉被结束进程留下的原始帧。"""
        import subprocess
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as local:
            src=Path(d)/'source.mp4';src.write_bytes(b'source')
            left=Path(local)/(matte.FRAMES_PREFIX+'killed');left.mkdir();(left/'.lock').write_text('');(left/'rgb.raw').write_bytes(b'x'*10)
            stuck=subprocess.TimeoutExpired('worker',1800,output=b'',stderr=None)
            with patch.object(matte,'environment_check',return_value=(True,None)),patch.object(matte.subprocess,'run',side_effect=stuck), \
                 patch.object(matte,'frames_root',return_value=Path(local)),self.assertRaises(RuntimeError) as caught:
                matte.generate(src,0,90,1920,1080,d)
            message=str(caught.exception)
            self.assertIn(str(Path(local)),message);self.assertIn(matte.FRAMES_PREFIX,message);self.assertIn('不用手动删',message)
            self.assertNotIn('rvm-frames- 开头的临时文件夹是没做完留下的',message)
            self.assertFalse(left.exists())
            self.assertEqual([p.name for p in (Path(d)/'抠像').iterdir()],[matte.cache_key(src,0,90,1920,1080)+'.log'])   # 片子目录里只有日志

    def test_raw_frames_live_in_a_local_prefixed_folder_and_are_removed(self):
        """原始帧文件夹建在本机临时目录、带固定前缀，用完即删；片子的 抠像/ 目录里不出现它。"""
        import inspect
        with tempfile.TemporaryDirectory() as local:
            with matte.frame_directory(local) as frames:
                self.assertEqual(frames.parent,Path(local));self.assertTrue(frames.name.startswith(matte.FRAMES_PREFIX))
                (frames/'rgb.raw').write_bytes(b'raw');self.assertTrue((frames/'.lock').is_file())
            self.assertEqual(list(Path(local).iterdir()),[])
            with self.assertRaises(RuntimeError):                       # 出错也删
                with matte.frame_directory(local) as frames:
                    (frames/'rgba.raw').write_bytes(b'raw');raise RuntimeError('boom')
            self.assertEqual(list(Path(local).iterdir()),[])
        self.assertEqual(matte.frames_root(),Path(tempfile.gettempdir()))
        source=inspect.getsource(matte.worker)
        self.assertIn('with frame_directory() as temporary',source)
        self.assertNotIn("dir=request['directory']",source)
        # 成品、遮罩、日志照旧按 request['directory']（片子的 抠像/）写
        for suffix in ("'.partial.mov'","'.decode.log'","'.encode.log'","'.npz'","'.mov'","'.json'"):self.assertIn('base.with_suffix('+suffix+')',source)
        self.assertIn("base = Path(request['directory'])/request['cache_key']",source)

    def test_stale_frames_are_cleared_but_a_running_matte_is_left_alone(self):
        """开始抠像时清残留：崩溃留下的（没人拿锁）清掉；别的抠像正在用的（拿着锁）、别的名字的文件夹都不动。"""
        import fcntl, os, time
        with tempfile.TemporaryDirectory() as local:
            root=Path(local)
            crashed=root/(matte.FRAMES_PREFIX+'crashed');crashed.mkdir();(crashed/'.lock').write_text('');(crashed/'rgb.raw').write_bytes(b'x')
            running=root/(matte.FRAMES_PREFIX+'running');running.mkdir();(running/'rgb.raw').write_bytes(b'y')
            fresh=root/(matte.FRAMES_PREFIX+'fresh');fresh.mkdir()                      # 刚建好还没上锁：先不动
            old=root/(matte.FRAMES_PREFIX+'old-unlocked');old.mkdir();ago=time.time()-3600;os.utime(old,(ago,ago))
            other=root/'别的程序的文件夹';other.mkdir();(other/'keep').write_text('1')
            note=root/(matte.FRAMES_PREFIX+'is-a-file');note.write_text('1')
            with (running/'.lock').open('w') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with matte.frame_directory(root) as mine:
                    self.assertTrue(mine.is_dir())
                    self.assertFalse(crashed.exists());self.assertFalse(old.exists())
                    self.assertTrue((running/'rgb.raw').exists());self.assertTrue(fresh.exists())
                    self.assertTrue((other/'keep').exists());self.assertTrue(note.exists())
                    self.assertEqual(matte.clear_stale_frames(root),[])                 # 正在用的自己这个也不会被清
                    self.assertTrue(mine.is_dir())
            self.assertEqual(matte.clear_stale_frames(root),[str(running)])             # 那个抠像结束（放锁）后才算残留

    def test_worker_limit_grows_with_frames_and_normal_run_is_unchanged(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'source.mp4';src.write_bytes(b'source');key=matte.cache_key(src,0,9000,1920,1080)
            def worker(*args,**kwargs):
                base=Path(d)/'抠像'/key;base.with_suffix('.mov').write_bytes(b'movie');base.with_suffix('.npz').write_bytes(b'alpha')
                base.with_suffix('.json').write_text(json.dumps({'cache_key':key,'bytes':5,'mask_bytes':5}))
                return subprocess.CompletedProcess(args,0,'done\n','')
            with patch.object(matte,'environment_check',return_value=(True,None)),patch.object(matte.subprocess,'run',side_effect=worker) as run:
                result=matte.generate(src,0,9000,1920,1080,d)
            self.assertFalse(result['cache_hit']);self.assertEqual((Path(d)/'抠像'/(key+'.log')).read_text(),'done\n')
            # 约 30 秒处理 1 秒画面＝每帧 1 秒；9000 帧正常 2.5 小时，给 15 小时
            self.assertEqual(run.call_args.kwargs['timeout'],9000*matte.MATTE_SECONDS_PER_FRAME);self.assertGreaterEqual(matte.MATTE_SECONDS_PER_FRAME,5)

    def test_replace_only_untouched_owned_foregrounds(self):
        item={'id':'m','start':10,'end':20,'track':3,'enabled':True,'media_path':'a.mov','props':{'Pan':0}}
        record={'mattes':[{'item_id':'m','written_state':matte.state(item)}]}
        self.assertEqual(removal_plan({'timeline':{'items':[item]}},record)[0],['m'])
        item['props']['Pan']=5
        removed,preserved=matte.removal(record,{'m':item});self.assertEqual(removed,[]);self.assertTrue(preserved)
        self.assertEqual(matte.removal(record,{})[1][0]['manual_fields'],['deleted'])

    def test_import_failure_reports_which_file_and_next_step(self):
        # B11：导入失败（接口返回空）以前直接对空值取下标，甩 TypeError
        io=Mock();io.write.side_effect=lambda fn,*args:fn(*args);io.ensure_track.return_value=(3,False)
        io.main.GetTrackCount.return_value=3
        source=Mock();source.GetUniqueId.return_value='v1';io.main.GetItemListInTrack.return_value=[source]
        mp=io.p.GetMediaPool.return_value;mp.GetRootFolder.return_value.GetClipList.return_value=[]
        mp.GetRootFolder.return_value.GetSubFolderList.return_value=[]
        row={'source_id':'v1','path':'/本片/抠像/abc.mov','start':100,'end':150,'transform':{}}
        for empty in (None,[]):
            mp.ImportMedia.return_value=empty
            with self.assertRaisesRegex(RuntimeError,'人物抠像文件导入达芬奇失败：/本片/抠像/abc.mov；下一步'):matte.place(io,[row])
        io.append.assert_not_called();mp.SetCurrentFolder.assert_called()       # 没放任何片段，媒体池当前文件夹已还原

    def test_shared_foreground_cannot_cover_fallback_title(self):
        rows=[{'start':100,'end':150,'effect_ids':['eligible'],'path':'alpha.mov'}]
        plan={'effects':[{'effect_id':'eligible','start':100,'end':150},{'effect_id':'fallback','start':110,'end':130}]}
        decisions=[{'effect_id':'eligible','action':'matte'},{'effect_id':'fallback','action':'fallback'}]
        result=matte.exclude_fallback_intervals(rows,plan,decisions)
        self.assertEqual([(r['start'],r['end'],r['source_offset']) for r in result],[(100,110,0),(130,150,30)])
        self.assertIn('共享人物层',decisions[0]['reason'])

    def test_review_contains_matte_above_titles_with_trimmed_offsets(self):
        record={'mattes':[{'item_id':'m','written_state':{'start':100,'end':150}}]}
        segments=[{'start':110,'end':130,'review_start':500}]
        rows=matte_review_ranges(record,segments)
        self.assertEqual(rows,[{'item_id':'m','start':500,'end':520,'source_offset':10,'original_start':110,'original_end':130}])

    def test_review_copies_full_transform_from_active_source(self):
        source,target,io,item,fg=(Mock() for _ in range(5))
        active=[None];io.activate.side_effect=lambda t:active.__setitem__(0,t)
        io.write.side_effect=lambda fn,*args:fn(*args)
        source.GetTrackCount.return_value=1;source.GetItemListInTrack.return_value=[item]
        item.GetUniqueId.return_value='m';item.GetClipEnabled.return_value=True;item.GetLeftOffset.return_value=0
        wanted={'ZoomGang':False,'ZoomX':4/3,'ZoomY':1,'Pan':45}
        item.GetProperty.side_effect=lambda:copy.deepcopy(wanted) if active[0] is source else {'Pan':45}
        props={};fg.SetProperty.side_effect=lambda k,v:props.update({k:v}) or True
        fg.GetProperty.side_effect=lambda:props.copy();fg.GetStart.return_value=500;fg.GetEnd.return_value=520
        fg.GetUniqueId.return_value='review-m';target.GetItemListInTrack.return_value=[fg]
        io.p.GetMediaPool.return_value.AppendToTimeline.return_value=[fg]
        record={'mattes':[{'item_id':'m','written_state':{'start':100,'end':150}}]}
        rows=place_review_mattes(io,target,source,record,[{'start':110,'end':130,'review_start':500}])
        self.assertEqual(props,wanted);self.assertEqual(rows[0]['track'],2);self.assertTrue(rows[0]['above_titles'])
        item.CopyGrades.assert_called_once_with([fg])

    def test_projection_compensates_anisotropic_output(self):
        source=Mock();media=source.GetMediaPoolItem.return_value;media.GetClipProperty.return_value='3840x2160'
        source.GetProperty.return_value={'Pan':45,'Tilt':0,'ZoomX':1,'ZoomY':1,'Scaling':0}
        timeline=Mock();timeline.GetSetting.side_effect=lambda k:{'timelineResolutionWidth':2880,'timelineResolutionHeight':2160,'timelineInputResMismatchBehavior':'scaleToCrop'}[k]
        props=matte.geometry(source,timeline);self.assertAlmostEqual(props['ZoomX'],4/3);self.assertEqual(props['ZoomY'],1)
        self.assertEqual(props['Pan'],60);self.assertEqual(props['Tilt'],0)
        mask=np.zeros((54,72),dtype='uint8');mask[:,36:]=255
        projected=matte.projected(mask,props,2880,2160)
        self.assertTrue(np.all(projected[:,38:60]>250));self.assertTrue(np.all(projected[:,5:35]==0))

    def test_v1_original_at_120_percent_is_matted_from_original_time(self):
        """2026-11-27 起口播原片（30 帧）在达芬奇里设 120%：按原片时间取帧、加速成 29.97 帧再抠，不再当成“变速不能抠”跳过。"""
        src=Mock();src.GetStart.return_value=100;src.GetEnd.return_value=700;src.GetClipEnabled.return_value=True
        src.GetDuration.return_value=600;src.GetSourceStartFrame.return_value=300;src.GetSourceEndFrame.return_value=1020
        src.GetSpeed.return_value={'Percentage':120.0,'PitchCorrection':True}
        src.GetMediaPoolItem.return_value.GetClipProperty.side_effect=lambda k:{'FPS':'30.0','File Path':'原片.mov'}[k]
        timeline=Mock();timeline.GetTrackCount.return_value=1;timeline.GetItemListInTrack.side_effect=lambda kind,n:[src]
        rows=matte.visible_segments(timeline,200,260)
        self.assertEqual(len(rows),1)
        self.assertAlmostEqual(rows[0]['retime']['seconds'],10+100*1.2*1001/30000,places=5);self.assertEqual(rows[0]['retime']['speed'],1.2)

    def test_upper_picture_splits_and_suppresses_v1(self):
        src=Mock();src.GetStart.return_value=100;src.GetEnd.return_value=150;src.GetClipEnabled.return_value=True
        src.GetDuration.return_value=50;src.GetSourceStartFrame.return_value=20;src.GetSourceEndFrame.return_value=70;src.GetLeftOffset.return_value=20
        src.GetMediaPoolItem.return_value.GetClipProperty.side_effect=lambda k:{'FPS':29.97,'File Path':'a.mp4'}[k]
        upper=Mock();upper.GetStart.return_value=110;upper.GetEnd.return_value=130;upper.GetClipEnabled.return_value=True
        timeline=Mock();timeline.GetTrackCount.return_value=2;timeline.GetIsTrackEnabled.return_value=True;timeline.GetTrackName.return_value='B-roll'
        timeline.GetItemListInTrack.side_effect=lambda kind,n:[src] if n==1 else [upper]
        rows=matte.visible_segments(timeline,100,150)
        self.assertEqual([(x['start'],x['end'],x['source_start']) for x in rows],[(100,110,20),(130,150,50)])
        timeline.GetTrackName.return_value='花字2'
        rows=matte.visible_segments(timeline,100,150)
        self.assertEqual([(x['start'],x['end']) for x in rows],[(100,150)])
        timeline.GetIsTrackEnabled.return_value=False
        self.assertEqual(matte.visible_segments(timeline,100,150),[])


if __name__=='__main__':unittest.main()

class AutomaticLayoutTests(unittest.TestCase):
    def test_retimed_source_is_documented_fallback_without_generation(self):
        effect={'effect_id':'title','anchor':{'center':[.5,.7]},'start':10,'end':12}
        plan={'timeline':{'width':100,'height':100},'effects':[effect]}
        with patch('engine.fx.matte.visible_segments',side_effect=ValueError('V1变速片段不能可靠抠像')),patch('engine.fx.matte.generate') as generate:
            kept,decisions,rows=matte.prepare(Mock(),plan,{'timeline':{'width':100,'height':100}},'.')
        self.assertEqual(kept,plan);self.assertFalse(rows);generate.assert_not_called()
        self.assertIn('变速',decisions[0]['reason']);self.assertEqual(decisions[0]['action'],'none')

    def test_move_then_matte_and_unmovable_fallback(self):
        from engine.fx.matte import prepare
        effect={'effect_id':'title','kind':'keyword','text':'字','anchor':{'subject_kind':'person','placement':'explicit','center':[.5,.7]},'start':10,'end':12}
        plan={'timeline':{'width':100,'height':100},'effects':[effect]}
        source=Mock();source.GetUniqueId.return_value='source';source.GetMediaPoolItem.return_value.GetClipProperty.return_value='source.mp4'
        io=Mock();part={'start':10,'end':12,'source_start':0,'source':source}
        with tempfile.TemporaryDirectory() as d:
            masks=Path(d)/'a.npz';np.savez_compressed(masks,alpha=np.ones((2,100,100),dtype='uint8')*255)
            asset={'path':'a.mov','masks':str(masks),'cache_hit':True}
            with patch('engine.fx.matte.visible_segments',return_value=[part]),patch('engine.fx.matte.geometry',return_value={'ZoomX':1,'ZoomY':1}),patch('engine.fx.matte.generate',return_value=asset),patch('engine.fx.safety.estimate',return_value={'bbox':[.1,.1,.2,.2]}),patch('engine.fx.safety.check_layout',return_value={'violations':[]}),patch('engine.fx.matte.coverage',side_effect=lambda m,b: .5 if io.move_count==0 else .1):
                io.move_count=0
                # Supply coverage based on call number, simulating moving away from the person.
                with patch('engine.fx.matte.coverage',side_effect=[.5,.1]):
                    moved,decisions,rows=prepare(io,plan,{'timeline':{'width':100,'height':100}},d)
                self.assertTrue(decisions[0]['moved']);self.assertEqual(decisions[0]['action'],'matte');self.assertEqual(len(rows),1)
                with patch('engine.fx.matte.coverage',return_value=.5),patch('engine.fx.safety.check_layout',return_value={'violations':['edge']}):
                    kept,decisions,rows=prepare(io,plan,{'timeline':{'width':100,'height':100}},d)
                self.assertEqual(decisions[0]['action'],'fallback');self.assertFalse(rows);self.assertEqual(kept,plan)
                with patch('engine.fx.matte.coverage',return_value=0):
                    _,decisions,rows=prepare(io,plan,{'timeline':{'width':100,'height':100}},d)
                self.assertEqual(decisions[0]['action'],'none');self.assertFalse(rows)
                # Ordinary emphasis must find zero-mask space, unlike explicit placements.
                effect['anchor'].pop('placement')
                with patch('engine.fx.matte.coverage',side_effect=[.5,0]):
                    _,decisions,rows=prepare(io,plan,{'timeline':{'width':100,'height':100}},d)
                self.assertTrue(decisions[0]['moved']);self.assertFalse(rows)
                with patch('engine.fx.matte.coverage',return_value=.5),patch('engine.fx.safety.check_layout',return_value={'violations':['edge']}):
                    with self.assertRaisesRegex(ValueError,'安全留白'):prepare(io,plan,{'timeline':{'width':100,'height':100}},d)
