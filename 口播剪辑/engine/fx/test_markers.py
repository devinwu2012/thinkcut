import copy
import tempfile
import unittest
from pathlib import Path
from engine.fx.markers import reconcile,delivery
from engine.fx.test_executor import plan,timeline


class MarkerTests(unittest.TestCase):
    def test_every_round_reads_new_markers_and_full_name_note(self):
        p=plan();t=timeline();e=p['effects'][0]
        e['source_marker']={'frame':12,'name':'名称','note':'第一行\n第二行'}
        t['markers']={'12':{'name':'名称','note':'第一行\n第二行'}}
        self.assertEqual(reconcile(p,t)['stale_effect_ids'],[])
        t['markers']['20']={'name':'新增','note':'不能漏'}
        t['markers']['12']['name']='新名称'
        result=reconcile(p,t)
        self.assertEqual(result['uncovered_frames'],[20]);self.assertEqual(result['stale_effect_ids'],['effect-1'])
        del t['markers']['12']
        self.assertEqual(reconcile(p,t)['missing_marker_effect_ids'],['effect-1'])

    def test_delivery_uses_actual_landed_range_and_lists_unwritten(self):
        p=plan();e=p['effects'][0];e['source_marker']={'frame':12,'name':'','note':'保留标记'}
        record={'effects':[dict(effect_id='effect-1',track=3,written_state=dict(StyledText='实际字',start=113,end=140))],
                'sounds':[dict(effect_id='effect-1',item_id='sound',written_state=dict(start=112,end=125))]}
        t=timeline();t['markers']['30']={'name':'漏项','note':'长备注'}
        with tempfile.TemporaryDirectory() as td:
            path,report=delivery(td,p,t,record);text=Path(path).read_text()
            self.assertIn('实际字',text);self.assertIn('[113,140)',text);self.assertIn('长备注',text)
            self.assertEqual(report['markers'][0]['effects'][0]['sounds'][0]['state']['start'],112)
            self.assertEqual(reconcile(p,t,{'effects':[]})['markers'][0]['effects'][0]['status'],'未写入')

if __name__=='__main__':unittest.main()
