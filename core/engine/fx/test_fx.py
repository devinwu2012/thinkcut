import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from engine.fx.check import check, text_items
from engine.fx.plan import validate
from engine.fx.snapshot import frame_timecode, still, read_timeline, parse_drt_subtitles
from engine.fx.connect import find_timeline

DATA=Path(__file__).parent/'testdata'
def fixture():return json.loads((DATA/'snapshot_excerpt.json').read_text())
def plan():return json.loads((DATA/'plan_example.json').read_text())


class CheckTests(unittest.TestCase):
    def test_real_excerpt_reports_empty_text_and_nested_spec(self):
        d=fixture();r=check(d,plan())
        self.assertTrue(any(x['kind']=='empty_text' for x in r['findings']))
        self.assertFalse(any(x['kind'].startswith('plan_') for x in r['findings']))
        title=next(o for o in text_items(d['timeline']) if o['nested_path'])
        self.assertEqual((title['start'],title['end'],title['track']['index']),(109410,109442,17))
        self.assertEqual(title['text']['inputs']['StyledText'],'24毫米主摄')

    def test_nested_trim_mapping_and_empty_text(self):
        d=fixture();item=d['timeline']['items'][-1];item['start']=200000;item['end']=200022;item['source_start']=1415
        found=list(text_items(d['timeline']))[-1]
        self.assertEqual((found['start'],found['end']),(200000,200022))
        item['nested']['items'][0]['texts'][0]['inputs']['StyledText']=' '
        self.assertTrue(any(x['kind']=='empty_text' and '花字2_1008' in x['message'] for x in check(d)['findings']))

    def test_range_and_long_audio_overlap_not_only_adjacent(self):
        d=fixture();row=d['timeline']['items'][0];row['texts'][0]['inputs'].update(GlobalIn=0,GlobalOut=0)
        row['texts'][0]['comp_range']={'COMPN_RenderStart':73}
        for n,(s,e) in enumerate([(0,100),(10,20),(30,40),(100,110)]):
            d['timeline']['items'].append({'id':str(n),'name':'sound','type':'audio','track':1,'start':s,'end':e})
        report=check(d)['findings']
        self.assertTrue(any(x['kind']=='short_text_range' for x in report))
        self.assertEqual(sum(x['kind']=='audio_overlap' for x in report),2)

    def test_wrong_track_and_same_text_different_timing_fail(self):
        p=plan();p['effects'][0]['track']=16
        self.assertTrue(any(x['kind']=='plan_effect_missing' for x in check(fixture(),p)['findings']))
        p=plan();p['effects'][0]['start']+=1
        self.assertTrue(any(x['kind']=='plan_effect_missing' for x in check(fixture(),p)['findings']))


class PlanTests(unittest.TestCase):
    def test_example_valid_and_filename_catalog(self):
        p=plan();self.assertEqual(validate(p),[])
        p['effects'][0]['sounds']=[{'filename':'相机快门2.mp3','start':109410,'gain_db':-3}]
        self.assertEqual(validate(p),[])
        p['effects'][0]['sounds'][0]['filename']='missing.mp3';self.assertTrue(validate(p))

    def test_bool_frames_blank_unknown_style_and_marker(self):
        for key,value in [('start',True),('end',109410),('text',' '),('layer',0),('source_marker',{'frame':2})]:
            p=plan();p['effects'][0][key]=value;self.assertTrue(validate(p),key)
        p=plan();p['effects'][0]['style']['profile']='other';self.assertTrue(validate(p))

    def test_overlap_same_layer_rejected_other_layer_allowed(self):
        p=plan();e=copy.deepcopy(p['effects'][0]);e['effect_id']='second';p['effects'].append(e)
        self.assertTrue(any('重叠' in e for e in validate(p)))
        e['layer']=3;self.assertEqual(validate(p),[])
        e['layer']=2;e['start']=e['end'];e['end']+=30;self.assertEqual(validate(p),[])

    def test_malformed_collections_and_ready_requirements(self):
        for value in (None, [], {'effects':[None]}, {'effects':{}}, {'effects':[{'style':[], 'sounds':[None]}]}):
            self.assertTrue(validate(value))
        p=plan();p['status']='ready';self.assertTrue(validate(p))


class DrtSubtitleTests(unittest.TestCase):
    def test_drt_subtitle_fragment_and_ambiguous_container(self):
        fragment='<SubtitleTrackVec><Element><Sm2TiTrack><Items><Element><Sm2TiGenerator><PrettyType>Subtitle</PrettyType><Name>文字&amp;内容</Name><Start>108004</Start><Duration>47</Duration></Sm2TiGenerator></Element></Items></Sm2TiTrack></Element></SubtitleTrackVec>'
        expected=[{'track':1,'start':108004,'end':108051}]
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'test.drt'
            with zipfile.ZipFile(path,'w') as z:z.writestr('SeqContainer/a.xml','<ListMgt::Invalid/>'+fragment)
            self.assertEqual(parse_drt_subtitles(path,expected)[0]['text'],'文字&内容')
            with zipfile.ZipFile(path,'a') as z:z.writestr('SeqContainer/b.xml',fragment)
            with self.assertRaises(ValueError):parse_drt_subtitles(path,expected)

    def test_case_library_learning_is_gone(self):
        """花字学习（定稿入库到花字案例库）2026-11-21 下线：模块、命令都没有了；还在用的两个函数搬到了现行模块。"""
        import importlib
        with self.assertRaises(ImportError):importlib.import_module('engine.fx.learn')
        self.assertFalse((Path(__file__).parent/'learn.py').exists())
        cli=(ROOT/'bin/fx.py').read_text(encoding='utf-8')
        for word in ('learn','case-root','案例库','入库'):self.assertNotIn(word,cli,word)
        from engine.fx import review, snapshot as snapshot_module
        from engine.broll.live import resolve_adapter
        self.assertIs(resolve_adapter.parse_drt_subtitles,snapshot_module.parse_drt_subtitles)   # 配 B-roll 读字幕原话仍然要用
        self.assertTrue(callable(review.run_ffmpeg))                                             # 审片出图仍然要用
        for module in (review,snapshot_module):self.assertNotIn('案例库',Path(module.__file__).read_text(encoding='utf-8'))


class StillTests(unittest.TestCase):
    def test_non_drop_and_drop_frame_timecode(self):
        self.assertEqual(frame_timecode(108100,29.97),'01:00:03:10')
        self.assertEqual(frame_timecode(1800,29.97,True),'00:01:00;02')
        self.assertEqual(frame_timecode(17982,29.97,True),'00:10:00;00')
        self.assertEqual(frame_timecode(107892,29.97,True),'01:00:00;00')

    def environment(self):
        t=Mock();t.GetStartFrame.return_value=108000;t.GetEndFrame.return_value=109000;t.GetSetting.return_value=29.97
        state=['01:00:00:00'];t.GetCurrentTimecode.side_effect=lambda:state[0]
        def seek(tc):state[0]=tc;return True
        t.SetCurrentTimecode.side_effect=seek
        return Mock(),Mock(),t,state

    def test_still_restores_on_export_failure(self):
        r,p,t,state=self.environment();p.ExportCurrentFrameAsStill.return_value=False
        with tempfile.TemporaryDirectory() as td,patch('engine.fx.snapshot.guard'),patch('engine.fx.snapshot.time.sleep'):
            with self.assertRaises(RuntimeError):still(r,p,t,[108100],td)
        self.assertEqual(state[0],'01:00:00:00')

    def test_still_transient_failure_retries_and_restores(self):
        r,p,t,state=self.environment();calls=[]
        def export(path):
            calls.append(path)
            if len(calls)==1:return False
            Path(path).write_bytes(b'nonempty');return True
        p.ExportCurrentFrameAsStill.side_effect=export
        with tempfile.TemporaryDirectory() as td,patch('engine.fx.snapshot.guard'),patch('engine.fx.snapshot.time.sleep'):
            result=still(r,p,t,[108100],td)
            self.assertEqual(len(result['stills'][0]['attempts']),2)
            self.assertEqual(state[0],'01:00:00:00')

    def test_still_file_readback_and_existing_refused(self):
        r,p,t,state=self.environment()
        def export(path):Path(path).write_bytes(b'nonempty');return True
        p.ExportCurrentFrameAsStill.side_effect=export
        with tempfile.TemporaryDirectory() as td,patch('engine.fx.snapshot.guard'),patch('engine.fx.snapshot.time.sleep'):
            result=still(r,p,t,[108100],td)
            self.assertEqual(result['stills'][0]['timecode'],'01:00:03:10')
            self.assertEqual(result['restored_timecode'],result['original_timecode'])
            with self.assertRaises(FileExistsError):still(r,p,t,[108100],td)

    def test_find_timeline_unique_without_switch(self):
        p=Mock();a=Mock();a.GetName.return_value='main';p.GetTimelineCount.return_value=1;p.GetTimelineByIndex.return_value=a
        self.assertIs(find_timeline(p,'main'),a)
        p.SetCurrentTimeline.assert_not_called()
        p.GetTimelineCount.return_value=2
        with self.assertRaises(ValueError):find_timeline(p,'main')


    def test_snapshot_recurses_both_timeline_type_names(self):
        for media_type in ('Timeline','时间线'):
            child=Mock();child.GetUniqueId.return_value='child';child.GetName.return_value='nested'
            child.GetSetting.return_value={'timelineFrameRate':29.97};child.GetStartFrame.return_value=100
            child.GetEndFrame.return_value=120;child.GetMarkers.return_value={};child.GetMarkInOut.return_value={}
            child.GetTrackCount.return_value=0
            item=Mock();item.GetUniqueId.return_value='item';item.GetName.return_value='nested'
            item.GetStart.return_value=100;item.GetEnd.return_value=120;item.GetDuration.return_value=20
            item.GetClipEnabled.return_value=True;item.GetSourceStartFrame.return_value=0;item.GetSourceEndFrame.return_value=19
            item.GetProperty.return_value={};item.GetFusionCompCount.return_value=0
            media=Mock();media.GetClipProperty.side_effect=lambda k:media_type if k=='Type' else None
            media.GetTimeline.return_value=child;item.GetMediaPoolItem.return_value=media
            main=Mock();main.GetUniqueId.return_value='main';main.GetName.return_value='main'
            main.GetSetting.return_value={'timelineFrameRate':29.97};main.GetStartFrame.return_value=100;main.GetEndFrame.return_value=120
            main.GetMarkers.return_value={};main.GetMarkInOut.return_value={}
            main.GetTrackCount.side_effect=lambda k:1 if k=='video' else 0
            main.GetTrackName.return_value='花字';main.GetIsTrackEnabled.return_value=True;main.GetIsTrackLocked.return_value=False
            main.GetItemListInTrack.return_value=[item]
            result=read_timeline(main)
            self.assertEqual(result['items'][0]['nested']['name'],'nested')
            child.GetUniqueId.return_value='main'
            with self.assertRaises(ValueError):read_timeline(main)

    def test_retimed_nested_mapping_refused(self):
        d=fixture();item=d['timeline']['items'][-1];item['source_end']=100
        with self.assertRaises(ValueError):list(text_items(d['timeline']))

    def test_no_project_mutating_sdk_calls(self):
        # Explicitly constrain the only non-Get SDK methods allowed in this phase.
        methods=set()
        for file in ('connect.py','snapshot.py'):
            tree=ast.parse((Path(__file__).parent/file).read_text())
            methods.update(n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr[0].isupper() and not (isinstance(n.func.value,ast.Name) and n.func.value.id in ('tempfile','zipfile')))
        self.assertEqual({m for m in methods if not m.startswith('Get')},{'SetCurrentTimecode','ExportCurrentFrameAsStill'})


if __name__=='__main__':unittest.main()
