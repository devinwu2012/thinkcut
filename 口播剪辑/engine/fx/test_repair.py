import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from engine.fx import repair,ledger
from engine.fx.executor import run
from engine.fx.test_executor import plan,title
from engine.fx.test_direct import IO,Timeline,Project


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.work=Path(self.tmp.name)
        self.p=Project();self.t=Timeline(self.p);self.p.timelines['main']=self.t;self.p.active=self.t
        self.io=IO(Mock(),self.p,self.t,'p');self.io.pm=Mock();self.io.r.EXPORT_DRT='DRT'
        self.plan=plan();e=self.plan['effects'][0];e['sounds']=[]
        row=title('owned');row['enabled']=True;row['texts'][0]['size_keyframes']={}
        self.t.data['items'].append(row);self.row=row
        self.record=dict(project_id='pid',timeline_id='main',database={},placement_mode='direct',
                         effects=[dict(effect_id='effect-1',item_id='owned',track=2,enabled=True,
                                       written_state=ledger.title_state(row))],sounds=[],
                         requested_plan=copy.deepcopy(self.plan),plan=copy.deepcopy(self.plan))
        self.values={}
        self.tool=Mock();self.tool.SetInput.side_effect=lambda k,v:self.values.update({k:copy.deepcopy(v)}) or True
        self.tool.GetInput.side_effect=lambda k,f:self.values[k]
        def read(item):
            return ({**ledger.title_state(self.row),**self.values},self.tool,73)
        self.patch=patch('engine.fx.repair.read_selected',side_effect=read);self.patch.start();self.addCleanup(self.patch.stop)
    def test_default_existing_small_edit_no_full_snapshot_reopen_or_review(self):
        self.plan['effects'][0]['text']='改字';path=self.work/'plan.json';path.write_text(json.dumps(self.plan))
        ledger.save(self.work,self.record)
        self.io.save_reopen=Mock(side_effect=AssertionError('cannot reopen'))
        self.io.snapshot=Mock(side_effect=AssertionError('cannot full snapshot'))
        with patch('engine.fx.executor.ResolveIO',return_value=self.io):
            result=run(self.io.r,self.p,self.t,'p',path,self.work,True)
        self.assertTrue(result['applied']);self.assertEqual(result['mode'],'small')
        self.assertFalse(result['saved_closed_reopened']);self.io.snapshot.assert_not_called()
        self.io.save_reopen.assert_not_called();self.io.pm.SaveProject.assert_called_once()
        self.assertEqual(len(list((self.work/'备份').glob('*.drt'))),1)
        self.assertEqual(self.t.calls,[]);self.assertEqual(self.t.deletes,[])
    def test_current_marker_change_stops_old_plan_before_backup(self):
        for key in ('requested_plan','plan'):
            self.record[key]['effects'][0]['source_marker']=dict(frame=12,name='旧名称',note='旧备注')
        self.plan['effects'][0]['source_marker']=dict(frame=12,name='旧名称',note='旧备注')
        self.plan['effects'][0]['text']='改字'
        result=repair.run_small(self.io,self.plan,self.record,self.work,True)
        self.assertFalse(result['allowed']);self.assertFalse(result['applied'])
        self.assertFalse((self.work/'备份').exists());self.tool.SetInput.assert_not_called()

    def test_changed_range_routes_to_targeted_trim_without_rebuilding(self):
        self.plan['effects'][0]['end']=140
        result=repair.run_small(self.io,self.plan,self.record,self.work,True)
        self.assertFalse(result['applied']);self.assertEqual(result['needs_targeted_edit'][0]['end'],140)
        self.assertEqual(self.t.calls,[]);self.tool.SetInput.assert_not_called()
    def test_other_position_changes_are_caught_without_full_engineering_compare(self):
        self.plan['effects'][0]['text']='改字'
        def moved(k,v):
            self.values[k]=v;self.t.data['items'][0]['start']+=1;return True
        self.tool.SetInput.side_effect=moved
        with self.assertRaisesRegex(RuntimeError,'其他片段位置'):repair.run_small(self.io,self.plan,self.record,self.work,True)
        self.io.pm.SaveProject.assert_not_called();self.assertTrue((self.work/'小修失败.json').exists())
    def test_new_effect_or_style_change_is_batch_and_manual_animation_protected(self):
        self.plan['effects'][0]['style']['profile']='another_style'
        self.assertIsNone(repair.changes(self.plan,self.record))
        self.plan=copy.deepcopy(self.record['requested_plan']);self.plan['effects'][0]['style']['size']=.09
        self.row['texts'][0]['size_keyframes']={'73':{'1':.08}}
        with self.assertRaisesRegex(ValueError,'关键帧'):repair.run_small(self.io,self.plan,self.record,self.work,True)
        self.tool.SetInput.assert_not_called()
    def test_position_and_volume_change_without_resetting_other_controls(self):
        e=self.plan['effects'][0]
        old_sound=dict(filename='咻-起身01+可爱.mp3',start=110,gain_db=-5)
        for key in ('requested_plan','plan'):self.record[key]['effects'][0]['sounds']=[copy.deepcopy(old_sound)]
        e['sounds']=[{**old_sound,'gain_db':-8}];e['anchor']['center']=[.3,.6]
        audio_row=dict(id='sfx',type='audio',track=3,start=109,end=116,enabled=True)
        self.t.data['tracks'].append(dict(type='audio',index=1,name='原声',locked=False,enabled=True))
        self.t.data['tracks'] += [dict(type='audio',index=n,name=f'音轨{n}',locked=False,enabled=True) for n in (2,3)]
        self.t.data['items'].append(audio_row)
        audio=Mock();audio.GetUniqueId.return_value='sfx';volume=[-5]
        audio.SetProperty.side_effect=lambda k,v:volume.__setitem__(0,v) or True
        audio.GetProperty.side_effect=lambda k:volume[0]
        self.record['sounds']=[dict(effect_id='effect-1',item_id='sfx',written_state=dict(track=3,AudioVolume=-5))]
        from engine.fx.test_direct import Item
        with patch('engine.fx.repair.objects',return_value=[Item(self.row),audio]):
            result=repair.run_small(self.io,self.plan,self.record,self.work,True)
        self.assertTrue(result['other_positions_unchanged']);self.assertEqual(volume[0],-8)
        self.tool.SetInput.assert_called_once_with('Center',{1:.3,2:.6})
        self.assertEqual(ledger.load(self.work)['sounds'][0]['written_state']['AudioVolume'],-8)
        # Removing a prior explicit gain requests the peak-based default, not -5 gain.
        self.record=ledger.load(self.work);e['sounds'][0].pop('gain_db')
        audio.GetLeftOffset.return_value=4;audio.GetDuration.return_value=7
        with patch('engine.fx.repair.objects',return_value=[Item(self.row),audio]),patch('engine.fx.audio.analyze_sound',return_value={'peak_dbfs':-20}) as measure:
            repair.run_small(self.io,self.plan,self.record,self.work,True)
        self.assertEqual(volume[0],15)
        self.assertEqual(measure.call_args.args[0]['source_start_frame'],4)

    def test_small_edit_on_hand_edited_title_marks_it_manual(self):
        # 2026-11-13 台账 Q059：用户先手动挪了位置，AI 再小修它的字。以前小修把手改吸收进基准，下次重建按 AI 旧位置重做；
        # 现在记成“手改过”，以后整批重建整条不动。
        self.row['props']['Pan']=40
        self.plan['effects'][0]['text']='小修改字'
        with patch('engine.fx.repair.delivery',return_value=('清单',{})):
            repair.run_small(self.io,self.plan,self.record,self.work,True)
        self.assertTrue(ledger.load(self.work)['effects'][0].get('manual'))

    def test_manual_fields_only_compares_fields_both_sides_have(self):
        self.assertEqual(ledger.manual_fields({'a':1,'Level2':0.0,'b':2},{'a':1,'b':3,'新字段':9}),['b'])

    def test_small_dry_run_no_backup_no_writes_and_markers_fresh(self):
        self.plan['effects'][0]['text']='改字'
        self.t.data['markers']['33']=dict(name='新标记',note='新要求')
        self.io.activate=Mock(side_effect=AssertionError('write during dry run'))
        result=repair.run_small(self.io,self.plan,self.record,self.work)
        self.assertIn(33,result['marker_report']['uncovered_frames'])
        self.assertFalse((self.work/'备份').exists());self.tool.SetInput.assert_not_called()

if __name__=='__main__':unittest.main()
