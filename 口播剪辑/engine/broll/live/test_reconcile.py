# -*- coding: utf-8 -*-
"""对账（辨认用户手改、推进台账）的回归；这些用例原在 test_learn.py，学偏好的部分 2026-11-21 随功能下线。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from broll.live import reconcile
from broll.live.arrange import default_profile, DEFAULTS
from broll.live._fixtures import with_ai, item
from broll.live._util import digest


def ripple(snap, start, end):
    """Delete one complete host sentence and ripple every downstream item."""
    result=deepcopy(snap);rows=[]
    for i in result['items']:
        if i['start']>=start and i['end']<=end:
            continue
        if i['start']>=end:
            i['start']-=end-start;i['end']-=end-start
        rows.append(i)
    result['items']=rows;result['end']-=end-start
    return result


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.s,self.l=with_ai(5,length=800)
        self.cat={f's{n}':{'segment':f's{n}','description':f'画面{n}','aspect':'2.39:1'} for n in range(5)}

    def diff(self,after,**kwargs):
        return reconcile.diff_snapshots(self.s,after,self.l,self.cat,**kwargs)

    def test_untouched_timeline_has_no_actions_and_keeps_every_spot(self):
        d=self.diff(self.s);self.assertEqual(d['actions'],[]);self.assertEqual(d['raw_changes'],[])
        self.assertEqual([r['status'] for r in d['results']],['kept']*5)
        self.assertFalse(any(r['approved'] for r in d['results']));self.assertEqual(d['ledger']['reserved'],[])

    def test_fx_build_marks_untouched_spots_approved_in_the_ledger(self):
        d=self.diff(self.s,fx_built=True);self.assertEqual(d['actions'],[])
        self.assertTrue(all(r['approved'] and r['weight']==0.3 for r in d['results']))
        self.assertTrue(all(row['approved'] for row in d['ledger']['spots'].values()))

    def test_deleted_exposed_and_reserve_not_reason_inference(self):
        a=deepcopy(self.s);a['items'].pop();d=self.diff(a)
        self.assertEqual([x['kind'] for x in d['actions']],['deleted'])
        self.assertEqual(d['actions'][0]['exposed'][0]['label'],'aroll')
        self.assertNotIn('reason_class',d['actions'][0]);self.assertEqual(len(d['ledger']['reserved']),1)

    def test_replaced_own_uses_union_not_double_count(self):
        a=deepcopy(self.s);a['items'].pop()
        a['items'] += [item('她的画面1',400,425,3,source_start=500),item('她的画面2',425,450,4,source_start=700)]
        d=self.diff(a);self.assertEqual(d['actions'][0]['kind'],'replaced_own')
        self.assertEqual(len(d['actions'][0]['materials']),2);self.assertEqual(d['new_folders'],['/media'])
        a['items'][-1]['start']=400;a['items'][-1]['end']=425
        self.assertEqual(self.diff(a)['actions'][0]['kind'],'deleted')

    def test_disabled_trim_extended_slip_reframe_full_actions(self):
        a=deepcopy(self.s);ai=a['items'][-5:]
        ai[0]['enabled']=False
        ai[1]['end']-=20
        ai[2]['end']+=20
        ai[3]['source_start']+=10;ai[3]['source_end']+=10
        ai[4]['props']={'ZoomX':1.2,'ZoomY':1.2,'Pan':5,'Tilt':6,'RotationAngle':2}
        d=self.diff(a)
        self.assertEqual(set(x['kind'] for x in d['actions']),{'disabled','trimmed','extended','slipped','reframed'})
        trimmed=next(x for x in d['actions'] if x['kind']=='trimmed');self.assertEqual(trimmed['delta_frames'],-20)
        self.assertEqual(next(x for x in d['actions'] if x['kind']=='reframed')['zoom'],1.2)

    def test_new_reserved_and_marker_only_visual_feedback(self):
        a=deepcopy(self.s);a['items'][0]['enabled']=False
        a['markers']={'150':{'name':'画面换成产品近景','duration':20},'250':{'name':'这里加花字','duration':20}}
        d=self.diff(a)
        self.assertEqual(set(x['kind'] for x in d['actions']),{'new_reserved','marker_note'})
        self.assertEqual(next(x for x in d['actions'] if x['kind']=='marker_note')['source'],'user_quote')

    def test_user_added_and_fx_all_excluded(self):
        a=deepcopy(self.s);title=item('Text+',600,700,4);title['media_path']=None
        fx=item('fx视频',500,600,4,source_start=700)
        a['items'] += [title,fx]
        self.assertEqual(self.diff(a,fx_record={'item_ids':[fx['id']]})['actions'],[])

    def test_ripple_realigns_no_preference_actions(self):
        a=ripple(self.s,500,600);d=self.diff(a)
        self.assertEqual(d['actions'],[]);self.assertEqual(len(d['aroll_reedit']),1)
        a=ripple(self.s,0,100);d=self.diff(a)
        self.assertEqual(d['actions'],[]);self.assertEqual(len(d['aroll_reedit']),1)

    def test_ripple_inside_or_at_front_of_multi_sentence_ai_is_not_trim_or_slip(self):
        s,l=with_ai(1,length=400)
        clip=s['items'][-1];clip.update(end=200,duration=200,source_end=250)
        l['spots']['0']['S0']=deepcopy(clip);l['spots']['0']['placement']=deepcopy(clip)
        for cut_start in (0,100):
            a=ripple(s,cut_start,cut_start+100)
            if cut_start==0:
                # ripple() retained a crossing item; model actual ripple trim.
                current=a['items'][-1];current.update(start=0,end=100,source_start=150,source_end=250)
            else:
                current=a['items'][-1];current.update(end=100,source_end=150)
            d=reconcile.diff_snapshots(s,a,l)
            self.assertEqual(d['actions'],[]);self.assertEqual(len(d['aroll_reedit']),1)

    def test_unsynced_subtitles_fail_before_events(self):
        a=ripple(self.s,0,100)
        for i in a['items']:
            if i['type']=='subtitle':i['start']+=100;i['end']+=100
        with self.assertRaisesRegex(ValueError,'字幕'):self.diff(a)

    def test_drp_copy_relink_are_zero_preference_events(self):
        a=deepcopy(self.s);a['name']='copy'
        for i in a['items']:
            i['id']='new'+i['id']
            if i['media_path']:i['media_path']='/new/'+Path(i['media_path']).name
        d=self.diff(a);self.assertEqual(d['actions'],[]);self.assertEqual(d['aroll_reedit'],[])

    def test_exact_twenty_percent_can_learn_known_actions(self):
        a=deepcopy(self.s);a['items'].append(deepcopy(a['items'][-1]));a['items'][-1]['id']='dup'
        a['items'][-3]['props']['ZoomX']=1.2
        d=self.diff(a);self.assertEqual(d['uncertain_rate'],0.2);self.assertTrue(d['update_allowed'])

    def test_repeated_same_baseline_no_new_actions(self):
        a=deepcopy(self.s);a['items'].pop();first=self.diff(a)
        second=reconcile.diff_snapshots(a,a,first['ledger'],self.cat)
        self.assertEqual(second['actions'],[]);self.assertEqual(second['results'][-1]['status'],'deleted')
        a['items'][-1]['enabled']=False
        first=reconcile.diff_snapshots(first['ledger']['S0'],a,first['ledger'],self.cat)
        second=reconcile.diff_snapshots(a,a,first['ledger'],self.cat)
        self.assertEqual(second['actions'],[]);self.assertEqual(second['results'][-2]['status'],'disabled')

    def test_rejected_sentence_version_not_learning(self):
        a=deepcopy(self.s);a['name']='01肖May时间线_逐句校对_20261011';a['items'].pop()
        d=self.diff(a);self.assertFalse(d['update_allowed']);self.assertEqual(d['actions'],[])

    def test_uncertainty_over_twenty_percent_leaves_the_ledger_alone(self):
        a=deepcopy(self.s)
        a['items'] += [deepcopy(a['items'][-1]),deepcopy(a['items'][-2])]
        a['items'][-1]['id']='dup1';a['items'][-2]['id']='dup2'
        d=self.diff(a);self.assertEqual(d['uncertain_rate'],0.4);self.assertFalse(d['update_allowed'])
        self.assertIn('不更新台账',d['reason']);self.assertEqual(d['ledger'],self.l)

    def test_aroll_fx_copy_drp_are_not_hand_edits(self):
        """口播波纹重剪、加花字、复制时间线、DRP 改 ID 和路径：都不算用户手改 B-roll（原学习验收③）。"""
        s,l=with_ai(3)
        controls=[ripple(s,0,100),deepcopy(s),deepcopy(s),deepcopy(s)]
        title=item('Text+',300,400,4);title['media_path']=None;controls[1]['items'].append(title)
        controls[2]['name']='复制的主时间线'
        for i in controls[3]['items']:
            i['id']='drp-'+i['id']
            if i['media_path']:i['media_path']='/relink/'+Path(i['media_path']).name
        for a in controls:
            with self.subTest(name=a['name'],items=len(a['items'])):
                self.assertEqual(reconcile.diff_snapshots(s,a,l)['actions'],[])


class DefaultProfileTests(unittest.TestCase):
    """学偏好下线后排布用固定偏好：必须和下线前“一条学习记录都没有”的档案逐项相同，排布结果才不变。"""
    # 下线当天 B-roll学习库/<IP>/档案.json 的原文（学习库里一条事件都没有），creator 换成 示例IP（inputs_hash 跟着 creator 变）
    BEFORE={"algo_version":"broll-offline-1","blacklist":[],"creator":"示例IP","evidence":{},
            "hard_filters":{"focus":["missed"],"people":["person","host"],"subject":["cut_off"]},
            "inputs_hash":"d3c9b2c851f81039eb8704076fa60469cdd3b005c9bb113a693a8ebf24a55e24","material_priority":[],
            "negative":[],"new_folders":[],"parameters":{"max_frames":150,"min_frames":75,"snap_tolerance":5},"positive":[],
            "quality_deletions":{},"quality_unavailable":[],"reserved":[],"rules":{},"semantic_guidance":[],"trend":[],
            "version_verdict":[],"zoom":{}}

    def test_same_as_the_empty_learned_profile(self):
        self.assertEqual(default_profile('示例IP'),self.BEFORE)
        self.assertEqual(default_profile('Codex')['inputs_hash'],'0c782a5c77aaa49f1e4e1ff1bc60fa877cb1e1646075b95bb57f587bd142dbc4')
        self.assertEqual(digest(default_profile('示例IP')),digest(self.BEFORE))

    def test_each_call_is_a_fresh_copy(self):
        a=default_profile('示例IP');a['parameters']['min_frames']=1;a['reserved'].append('x')
        self.assertEqual(default_profile('示例IP'),self.BEFORE);self.assertEqual(DEFAULTS['min_frames'],75)


class LearningRemovedTests(unittest.TestCase):
    def test_learning_modules_are_gone(self):
        import importlib
        for name in ('broll.live.learn','broll.live.seeds'):
            with self.subTest(name=name),self.assertRaises(ImportError):importlib.import_module(name)
        live=Path(__file__).resolve().parent
        for name in ('learn.py','seeds.py','T3接口.md','test_learn.py','test_seeds.py','test_acceptance.py'):
            self.assertFalse((live/name).exists(),name)
        for name in ('make_event','write_event','rebuild_profile','save_profile','load_profile','ignore_learning','learning_summary'):
            self.assertFalse(hasattr(reconcile,name),name)


if __name__=='__main__':unittest.main()
