"""Behavioral fake Resolve: external archives, UID ownership, changes and audio nests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from engine.fx.cleanup import cleanup, register
from engine.fx.test_direct import Timeline, Project, IO, Item


class Media:
    def __init__(self,t):self.t=t
    def GetClipProperty(self,key):return 'Timeline' if key=='Type' else ''
    def GetTimeline(self):return self.t


class Nest(Item):
    def __init__(self,target):self.target=target
    def GetMediaPoolItem(self):return Media(self.target)


class Line(Timeline):
    def __init__(self,p,name):
        super().__init__(p,name);self.refs=[];self.hidden='grade0';self.fail_export=False
        self.data['tracks'].append(dict(type='audio',index=1,name='人声',enabled=True,locked=False))
    def Export(self,path,kind):
        if self.fail_export:return False
        Path(path).write_text(json.dumps([self.data,self.hidden],sort_keys=True));return True
    def GetItemListInTrack(self,kind,n):
        # A1 nest tests that disabled audio dependencies are protected too.
        if kind=='audio' and n==1:return [Nest(t) for t in self.refs]
        return super().GetItemListInTrack(kind,n)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.work=Path(self.tmp.name)
        self.p=Project();self.main=Line(self.p,'main');self.other=Line(self.p,'备份_用户原有')
        self.p.timelines={'main':self.main,self.other.name:self.other};self.p.active=self.main
        self.r=Mock();self.r.EXPORT_DRT='DRT';self.r.GetProjectManager.return_value.SaveProject.return_value=True
        self.io=IO(self.r,self.p,self.main,'p');self.io.pm=self.r.GetProjectManager()
        self.p.GetMediaPool=Mock();self.p.GetMediaPool.return_value.DeleteTimelines.side_effect=self.delete
        def snap(t):
            d=deepcopy(t.data);d.update(id=t.name,name=t.name);return d
        for context in (patch('engine.fx.cleanup.ResolveIO',return_value=self.io),
                        patch('engine.fx.cleanup.read_timeline',side_effect=snap)):
            context.start();self.addCleanup(context.stop)
    def delete(self,lines):
        for line in lines:
            archives=list(self.work.glob('时间线清理/*/*.drt'))
            if not archives:raise AssertionError('deleted without export')
            del self.p.timelines[line.name]
        return True
    def own(self,name='AI过程'):
        line=Line(self.p,name);self.p.timelines[name]=line
        register(self.io,self.work,line,'测试过程',seal=True)
        return line
    def run_cleanup(self,apply=False):return cleanup(self.r,self.p,self.main,'p',self.work,apply)

    def test_dry_run_exports_but_does_not_delete_user_or_process(self):
        line=self.own();r=self.run_cleanup()
        self.assertTrue(r['candidates'][0]['eligible']);self.assertFalse(r['applied'])
        self.assertTrue(Path(r['candidates'][0]['backup']).is_file())
        self.assertIn(line.name,self.p.timelines);self.p.GetMediaPool.return_value.DeleteTimelines.assert_not_called()
        self.assertEqual(self.p.active,self.main)

    def test_apply_only_recorded_uid_and_preserve_user_with_backup_name(self):
        line=self.own('普通名字');r=self.run_cleanup(True)
        self.assertEqual(r['deleted'],[line.name]);self.assertTrue(r['saved'])
        self.assertEqual(set(self.p.timelines),{'main',self.other.name})

    def test_manual_visible_and_hidden_drt_changes_are_protected(self):
        line=self.own();line.data['markers']['99']={'note':'独有'}
        self.assertFalse(self.run_cleanup(True)['candidates'][0]['eligible'])
        # Reset SDK-readable state; an unexposed grading change must still block.
        line.data['markers'].pop('99');line.hidden='manual grade'
        self.assertFalse(self.run_cleanup(True)['candidates'][0]['eligible'])
        self.p.GetMediaPool.return_value.DeleteTimelines.assert_not_called()

    def test_audio_reference_from_user_protects_nested_dependency_chain(self):
        parent=self.own('parent');child=self.own('child');parent.refs=[child];self.other.refs=[parent]
        r=self.run_cleanup(True)
        self.assertFalse(any(row['eligible'] for row in r['candidates']))
        self.p.GetMediaPool.return_value.DeleteTimelines.assert_not_called()

    def test_process_parent_deleted_before_unreferenced_child(self):
        parent=self.own('parent');child=self.own('child');parent.refs=[child]
        r=self.run_cleanup(True)
        self.assertEqual(r['deleted'],['parent','child'])

    def test_export_failure_means_no_deletion(self):
        line=self.own();line.fail_export=True
        with self.assertRaisesRegex(RuntimeError,'导出失败'):self.run_cleanup(True)
        self.p.GetMediaPool.return_value.DeleteTimelines.assert_not_called()
        self.assertTrue(list(self.work.glob('时间线清理/*/失败.json')))

    def test_no_registry_does_not_guess_from_name_and_scope_mismatch_refuses(self):
        self.assertEqual(self.run_cleanup(True)['deleted'],[])
        self.own();path=self.work/'AI时间线.json';data=json.loads(path.read_text());data['project_id']='other'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'不匹配'):self.run_cleanup(True)

    def test_failed_creation_archive_still_leaves_uid_claim_and_unfinished_protection(self):
        line=Line(self.p,'failed');self.p.timelines[line.name]=line;line.fail_export=True
        with self.assertRaisesRegex(RuntimeError,'导出失败'):register(self.io,self.work,line,'测试过程')
        data=json.loads((self.work/'AI时间线.json').read_text())
        self.assertEqual(data['timelines'][0]['id'],'failed')
        self.assertFalse(data['timelines'][0]['sealed'])
        line.fail_export=False
        r=self.run_cleanup(True)
        self.assertFalse(r['candidates'][0]['eligible'])
        self.p.GetMediaPool.return_value.DeleteTimelines.assert_not_called()

    def test_unfinished_and_cycle_are_not_deleted(self):
        line=self.own();path=self.work/'AI时间线.json';data=json.loads(path.read_text());data['timelines'][0]['sealed']=False
        path.write_text(json.dumps(data));self.assertFalse(self.run_cleanup(True)['candidates'][0]['eligible'])
        data['timelines'][0]['sealed']=True;path.write_text(json.dumps(data))
        child=self.own('child');line.refs=[child];child.refs=[line]
        with self.assertRaisesRegex(ValueError,'循环'):self.run_cleanup(True)
        self.p.GetMediaPool.return_value.DeleteTimelines.assert_not_called()
