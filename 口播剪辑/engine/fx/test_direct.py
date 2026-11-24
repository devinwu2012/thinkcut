"""Behavioral SDK simulation: tracks, marker ripple, ownership and persisted resume."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine.fx import direct, ledger
from engine.fx.executor import run
from engine.fx.test_executor import title, plan, timeline


class Item:
    def __init__(self, row): self.row = row
    def GetUniqueId(self): return self.row['id']
    def GetStart(self): return self.row['start']
    def GetEnd(self): return self.row['end']
    def GetDuration(self): return self.GetEnd()-self.GetStart()
    def GetMediaPoolItem(self): return None
    def GetFusionCompCount(self): return 1
    def GetClipEnabled(self): return self.row.get('enabled', True)
    def SetName(self, name): self.row['name'] = name; return True
    def GetName(self): return self.row['name']
    def SetClipColor(self, color): self.row['color'] = color; return True
    def GetClipColor(self): return self.row.get('color')


class Timeline:
    def __init__(self, project, name='main'):
        self.p = project; self.name = name; self.data = timeline()
        self.data['tracks'].append({'type':'video','index':3,'name':'花字2','locked':False,'enabled':True})
        self.marks = {'video':{}, 'audio':{}}
        self.data['mark_in_out'] = self.marks
        self.data['markers'] = {'12':{'color':'Blue','name':'原标记','note':'原注释','duration':2,'customData':'私有字段'}}
        self.tc = '01:00:00:00'; self.source_target = 2; self.seq = 0
        self.calls = []; self.deletes = []
    def GetName(self): return self.name
    def GetUniqueId(self): return self.name
    def GetStartFrame(self): return 100
    def GetStartTimecode(self): return '00:00:03:10'
    def GetSetting(self, key): return 29.97
    def GetTrackCount(self, kind): return sum(t['type']==kind for t in self.data['tracks'])
    def tr(self, kind, n): return next(t for t in self.data['tracks'] if t['type']==kind and t['index']==n)
    def GetTrackName(self, kind, n): return self.tr(kind,n)['name']
    def GetIsTrackLocked(self, kind, n): return self.tr(kind,n)['locked']
    def GetIsTrackEnabled(self, kind, n): return self.tr(kind,n)['enabled']
    def SetTrackLock(self, kind, n, value): self.tr(kind,n)['locked']=value; return True
    def GetItemListInTrack(self, kind, n): return [Item(r) for r in self.data['items'] if r['type']==kind and r['track']==n]
    def GetMarkInOut(self): return copy.deepcopy(self.marks)
    def SetMarkInOut(self, a, b, kind):
        for k in self.marks if kind=='all' else [kind]: self.marks[k]={'in':a,'out':b}
        return True
    def ClearMarkInOut(self, kind):
        for k in self.marks if kind=='all' else [kind]: self.marks[k]={}
        return True
    def GetCurrentTimecode(self): return self.tc
    def SetCurrentTimecode(self, tc): self.tc=tc; return True
    def GetMarkers(self): return copy.deepcopy(self.data['markers'])
    def GetMarkerCustomData(self, f): return self.data['markers'][str(int(f))]['customData']
    def DeleteMarkerAtFrame(self, f): del self.data['markers'][str(int(f))]; return True
    def AddMarker(self, f, color, name, note, duration, custom):
        self.data['markers'][str(int(f))]={'color':color,'name':name,'note':note,'duration':duration,'customData':custom}; return True
    def InsertFusionTitleIntoTimeline(self, kind):
        self.calls.append((self.source_target, copy.deepcopy(self.marks)))
        if self.GetIsTrackLocked('video', self.source_target): return None
        self.seq += 1; a = 100+self.marks['video']['in']; b = 101+self.marks['video']['out']
        row=title(f'{self.name}-{self.seq}',a,b); row.update(track=self.source_target,enabled=True)
        row.setdefault('props',{}).setdefault('RotationAngle',0.0)   # 真达芬奇的 GetProperty 总是返回全部属性
        self.data['items'].append(row)
        self.data['markers']={str(int(k)+b-a):v for k,v in self.data['markers'].items()}
        return Item(row)
    def DeleteClips(self, items, ripple):
        assert ripple is False
        ids={i.GetUniqueId() for i in items}; self.deletes.extend(ids)
        self.data['items']=[r for r in self.data['items'] if r['id'] not in ids]; return True
    def Export(self, path, kind): Path(path).write_bytes(b'DRT'); return True
    def DuplicateTimeline(self, name):
        t=Timeline(self.p,name); t.data=copy.deepcopy(self.data)
        for r in t.data['items']: r['id']=name+'-'+r['id']
        self.p.timelines[name]=t; return t


class Project:
    def __init__(self): self.timelines={}; self.active=None
    def GetUniqueId(self): return 'pid'
    def GetCurrentTimeline(self): return self.active
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self,n): return list(self.timelines.values())[n-1]


class IO:
    def __init__(self,r,p,t,name):
        self.r=r;self.p=p;self.main=t;self.db={};self.pid='pid';self.main_name=t.name;self.main_id=t.name;self.main.GetUniqueId()
    def write(self,fn,*args): return fn(*args)
    def activate(self,t): self.p.active=t
    def seek(self,t,tc): t.tc=tc
    def guard(self): pass
    def snapshot(self):
        self.activate(self.main); data=copy.deepcopy(self.main.data)
        data.update(name=self.main.name,id=self.main.name,mark_in_out=self.main.GetMarkInOut())
        return {'timeline':data,'timelines':[{'id':t.name,'name':t.name} for t in self.p.timelines.values()]}
    def ensure_track(self,kind,name):
        matches=[t for t in self.main.data['tracks'] if t['type']==kind and t['name']==name]
        if matches:return matches[0]['index'],False
        n=self.main.GetTrackCount(kind)+1
        self.main.data['tracks'].append(dict(type=kind,index=n,name=name,locked=False,enabled=True))
        return n,True
    def style(self,item,e):
        i=item.row; i['texts'][0]['inputs'].update(StyledText=e['text'],Size=e['style']['size'])
        for k,v in e.get('overrides',{}).items():
            if k.startswith('clip.'): i['props'][k[5:]]=v
    def save_reopen(self): pass


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir=Path(self.tmp.name);self.path=self.dir/'plan.json';self.work=self.dir/'work'
        self.p=Project();self.t=Timeline(self.p);self.p.timelines['main']=self.t;self.p.active=self.t
        self.r=Mock();self.r.EXPORT_DRT='DRT'
        pp=plan(); e=pp['effects'][0];e['sounds']=[]
        e2=copy.deepcopy(e);e2.update(effect_id='second',name='second',start=140,end=160,text='第二处')
        e3=copy.deepcopy(e);e3.update(effect_id='overlap',name='overlap',layer=2,start=110,end=134,text='第二层')
        pp['effects']=[e3,e2,e]; self.pp=pp
        patches=[patch('engine.fx.executor.ResolveIO',IO),patch('engine.fx.executor.snapshot',side_effect=lambda r,p,t:IO(r,p,t,'p').snapshot()),
                 patch('engine.fx.direct.read_timeline',side_effect=lambda t:IO(None,t.p,t,'p').snapshot()['timeline']),
                 patch('engine.fx.executor.config',return_value={'matting':{'enabled':False}}),
                 patch('engine.fx.executor.check_layout',return_value={'valid':True}),
                 patch('engine.fx.executor.sound_files',return_value={})]
        for p in patches: p.start();self.addCleanup(p.stop)
        self.refresh()
    def refresh(self):
        digest=ledger.digest(IO(self.r,self.p,self.t,'p').snapshot()['timeline'])
        for e in self.pp['effects']: e['precondition_snapshot_sha256']=digest
        self.path.write_text(json.dumps(self.pp))
    def run_build(self,**kw):
        kw.setdefault('mode','batch')
        return run(self.r,self.p,self.t,'p',self.path,self.work,True,**kw)
    def finish(self,**kw):
        while True:
            try:return self.run_build(**kw)
            except direct.NeedsTarget as exc:
                t=self.p.timelines[ledger.load(self.work)['built_timeline']];t.source_target=exc.track
                kw['resume']=True
    def test_order_resume_markers_and_user_locks_restored(self):
        self.t.SetTrackLock('video',1,True);self.t.SetMarkInOut(1,20,'video');self.refresh()
        before=direct.capture(self.t)
        with self.assertRaises(direct.NeedsTarget):self.run_build()
        rec=ledger.load(self.work);self.assertEqual(len(rec['effects']),1)
        first_ids=[r['item_id'] for r in rec['effects']]
        self.assertEqual(direct.capture(self.t),before)
        self.t.source_target=3; result=self.finish(resume=True)
        self.assertTrue(result['applied']);rec=ledger.load(self.work)
        self.assertEqual([r['item_id'] for r in rec['effects'][:1]],first_ids)
        self.assertEqual([r['effect_id'] for r in rec['effects']],['effect-1','overlap','second'])
        self.assertEqual(direct.capture(self.t),before)
        self.assertNotIn('nested',rec);self.assertNotIn('sources',rec)      # 旧版嵌套做法的字段不再写进操作记录
    def test_resume_rejects_changed_plan_and_foreign_partial_clip(self):
        with self.assertRaises(direct.NeedsTarget):self.run_build()
        self.pp['effects'][0]['text']='变了';self.path.write_text(json.dumps(self.pp))
        with self.assertRaisesRegex(ValueError,'计划'):self.run_build(resume=True)
        self.pp['effects'][0]['text']='第二层';self.path.write_text(json.dumps(self.pp))
        self.t.data['items'].append(title('foreign',250,260))
        with self.assertRaisesRegex(ValueError,'断点后'):self.run_build(resume=True)
    def test_current_marker_changed_or_deleted_stops_before_insert(self):
        self.pp['effects'][0]['source_marker']=dict(frame=12,name='旧名称',note='旧备注')
        self.refresh()
        result=self.run_build()
        self.assertFalse(result['allowed']);self.assertFalse(result['applied'])
        self.assertEqual(result['marker_report']['stale_effect_ids'],['overlap'])
        self.assertEqual(self.t.calls,[])

    def test_future_clip_skipped_before_insert_and_not_moved(self):
        foreign=title('foreign',290,299);self.t.data['items'].append(foreign);self.refresh()
        self.finish()
        self.assertEqual(foreign['start'],290)
        self.assertTrue(all(row['track']!=2 for row in ledger.load(self.work)['effects']))
        self.assertNotIn('foreign',self.t.deletes)

    def test_future_clip_blocks_explicit_track_without_any_insert(self):
        self.t.data['items'].append(title('foreign',290,299))
        # One non-overlapping planned title isolates the existing future-clip guard.
        self.pp['effects']=[self.pp['effects'][1]]
        self.pp['effects'][0]['track']=2
        self.refresh()
        with self.assertRaisesRegex(ValueError,'后续片段'):self.run_build()
        self.assertEqual(self.t.calls,[])

    def test_rebuild_keeps_hand_edited_title_whole_and_only_deletes_untouched(self):
        # 2026-11-13 台账 Q059（用户 10-17：“只要是我改过的素材都不要再改了”）：手改过的花字整条留在原位，
        # 不删、不重放；以前是全部删掉再按“计划＋抄回来的几个字段”重放，没抄到的字段（旋转、字距…）就丢了。
        self.finish();rec=ledger.load(self.work);owned=[r['item_id'] for r in rec['effects']]
        row=next(r for r in self.t.data['items'] if r['id']==owned[0])
        row['texts'][0]['inputs']['StyledText']='用户手调';row['props']['Pan']=18
        row['props']['RotationAngle']=30;row['props']['Opacity']=60      # 程序不记的字段（旋转、不透明度）也不能丢
        frozen=json.loads(json.dumps(row));self.t.deletes.clear()
        result=self.finish()
        self.assertEqual(result['kept_manual_titles'],['effect-1'])
        self.assertEqual(set(self.t.deletes),set(owned[1:]))                                    # 只删没动过的
        self.assertEqual(json.loads(json.dumps(next(r for r in self.t.data['items'] if r['id']==owned[0]))),frozen)   # 手改的那条逐字段没变
        final=ledger.load(self.work);kept=next(r for r in final['effects'] if r['effect_id']=='effect-1')
        self.assertEqual((kept['item_id'],kept['manual']),(owned[0],True))
        self.assertEqual((kept['written_state']['StyledText'],kept['written_state']['clip.Pan']),('用户手调',18))
        self.assertEqual(len(final['effects']),len(owned))
        # 只改了程序以前不记的属性（旋转）：现在也认得出是手改
        other=next(r for r in self.t.data['items'] if r['id']==next(x['item_id'] for x in final['effects'] if x['effect_id']!='effect-1'))
        other['props']['RotationAngle']=12;oid=other['id'];self.t.deletes.clear();result=self.finish()
        self.assertEqual(len(result['kept_manual_titles']),2);self.assertNotIn(oid,self.t.deletes)
        self.assertEqual(next(r for r in self.t.data['items'] if r['id']==oid)['props']['RotationAngle'],12)
        saved=json.loads(self.path.read_text());e=next(e for e in saved['effects'] if e['effect_id']=='effect-1')
        self.assertEqual(e['text'],'用户手调')
        # 再重建一次（这次没再动它）：还是不删
        self.t.deletes.clear();self.finish()
        self.assertNotIn(owned[0],self.t.deletes)
        self.assertEqual(json.loads(json.dumps(next(r for r in self.t.data['items'] if r['id']==owned[0]))),frozen)
        # 计划里把它拿掉：停下，不删
        self.pp['effects']=[e for e in json.loads(self.path.read_text())['effects'] if e['effect_id']!='effect-1'];self.refresh()
        with self.assertRaisesRegex(ValueError,'手调'):self.run_build()
        self.assertNotIn(owned[0],self.t.deletes)
    def test_recover_after_failed_batch_then_build_completes(self):
        # 2026-11-13 台账 Q061：整批写入中途失败后，以前所有命令都拒绝、没有出路。
        from engine.fx.recover import recover
        snap=patch('engine.fx.recover.snapshot',side_effect=lambda r,p,t:IO(r,p,t,'p').snapshot());snap.start();self.addCleanup(snap.stop)
        self.assertFalse(recover(self.r,self.p,self.t,'p',self.work)['needed'])                 # 没有未完成的写入：不需要恢复
        self.finish();first=ledger.load(self.work);ids={r['effect_id']:r['item_id'] for r in first['effects']}
        real=direct.insert;calls=[]
        def flaky(io,effect,track,claim):
            calls.append(effect['effect_id'])
            if len(calls)==2:raise RuntimeError('模拟：插到第二条时达芬奇出错')
            return real(io,effect,track,claim)
        e4=copy.deepcopy(self.pp['effects'][1]);e4.update(effect_id='fourth',name='fourth',start=170,end=190,text='第四处')
        self.pp['effects'].append(e4);self.refresh()
        with patch('engine.fx.direct.insert',side_effect=flaky):
            with self.assertRaisesRegex(RuntimeError,'模拟'):self.finish()
        self.assertEqual(ledger.load(self.work)['pending']['status'],'failed')
        with self.assertRaisesRegex(ValueError,'recover'):self.run_build()                      # 失败后 build 拒绝，并说明怎么恢复
        before=copy.deepcopy(self.t.data)
        dry=recover(self.r,self.p,self.t,'p',self.work)
        self.assertTrue(dry['needed']);self.assertFalse(dry['applied']);self.assertIn('模拟',dry['failed_with'])
        self.assertEqual(len(dry['titles_on_timeline']),1);self.assertEqual(len(dry['titles_missing']),3)
        self.assertIn('pending',ledger.load(self.work));self.assertEqual(self.t.data,before)    # 演练不改记录、不动时间线
        done=recover(self.r,self.p,self.t,'p',self.work,True)
        self.assertTrue(done['applied']);self.assertEqual(self.t.data,before)                    # 恢复只改记录
        rec=ledger.load(self.work);self.assertNotIn('pending',rec);self.assertEqual(len(rec['effects']),1)
        self.assertTrue((Path(done['recover_evidence'])/'previous_ledger.json').is_file())
        self.refresh();self.finish();final=ledger.load(self.work)                                 # 恢复后照常 build：补齐
        self.assertEqual(sorted(r['effect_id'] for r in final['effects']),['effect-1','fourth','overlap','second'])
        names=[i['name'] for i in self.t.data['items'] if i.get('texts')]
        self.assertEqual(sorted(names),sorted(set(names)));self.assertEqual(len(names),4)        # 时间线上每条花字正好一条，没有重复
    def test_recover_leaves_waiting_for_target_to_resume(self):
        from engine.fx.recover import recover
        with self.assertRaises(direct.NeedsTarget):self.run_build()
        with self.assertRaisesRegex(ValueError,'--resume'):recover(self.r,self.p,self.t,'p',self.work,True)
    def test_volatile_cache_setting_is_not_a_timeline_change(self):
        snapA=IO(self.r,self.p,self.t,'p').snapshot();snapA['timeline']['settings']={'perfCacheClipsLocation':'','timelineFrameRate':'29.97'}
        snapB=copy.deepcopy(snapA);snapB['timeline']['settings']['perfCacheClipsLocation']='/Users/x/Movies/CacheClip'
        self.assertEqual(ledger.verify_delta(snapA,snapB),[])                                    # 达芬奇自己改的缓存位置不算
        snapB['timeline']['settings']['timelineFrameRate']='25'
        self.assertEqual(ledger.verify_delta(snapA,snapB),['时间线设置变化：settings'])           # 别的设置变了照样拦
    def test_removing_a_manual_title_from_plan_requires_decision(self):
        self.finish();rec=ledger.load(self.work);uid=rec['effects'][0]['item_id']
        next(r for r in self.t.data['items'] if r['id']==uid)['props']['Pan']=18
        self.pp['effects']=[e for e in self.pp['effects'] if e['effect_id']!='effect-1']
        self.path.write_text(json.dumps(self.pp))
        with self.assertRaisesRegex(ValueError,'计划移除'):self.run_build()
        self.assertEqual(self.t.deletes,[])
    def test_null_insert_is_single_attempt_and_wrong_track_is_cleaned_without_retry(self):
        io=IO(self.r,self.p,self.t,'p');e=self.pp['effects'][0]
        with self.assertRaises(direct.NeedsTarget):direct.insert(io,e,3,Mock())
        self.assertEqual(len(self.t.calls),1)
        # Simulate Resolve ignoring the temporary lock and returning a title on V2.
        old=self.t.InsertFusionTitleIntoTimeline
        def wrong(k): self.t.tr('video',2)['locked']=False; return old(k)
        self.t.tr('video',3)['locked']=False
        self.t.InsertFusionTitleIntoTimeline=wrong;before=list(self.t.data['items']);claim=Mock()
        with self.assertRaises(direct.NeedsTarget):direct.insert(io,e,3,claim)
        self.assertEqual(self.t.data['items'],before);self.assertEqual(len(self.t.calls),2);claim.assert_not_called()
    def test_unexpected_insert_ripple_is_caught_before_style(self):
        io=IO(self.r,self.p,self.t,'p');effect=self.pp['effects'][-1]
        original=self.t.InsertFusionTitleIntoTimeline
        def ripple(kind):
            item=original(kind);self.t.data['items'][0]['start']+=5;return item
        self.t.InsertFusionTitleIntoTimeline=ripple;claim=Mock()
        with self.assertRaisesRegex(RuntimeError,'推动'):direct.insert(io,effect,2,claim)
        claim.assert_not_called()

    def test_compound_title_rejected(self):
        item=Mock();item.GetMediaPoolItem.return_value.GetClipProperty.return_value='Compound'
        with self.assertRaisesRegex(RuntimeError,'嵌套'):direct.assert_native(item)
    def test_rebuild_dry_run_reports_moved_or_disabled_title_like_apply(self):
        # B2：以前演练自己拼现状，不查移轨/停用，演练通过、真写才报错
        self.finish();rec=ledger.load(self.work);uid=rec['effects'][0]['item_id'];eid=rec['effects'][0]['effect_id']
        dry=lambda:run(self.r,self.p,self.t,'p',self.path,self.work,False,mode='batch')
        self.refresh();self.assertFalse(dry()['applied'])                                        # 没动过：演练照常通过
        row=next(r for r in self.t.data['items'] if r['id']==uid);calls=len(self.t.calls)
        for change,undo,message in ((lambda:row.update(track=1),lambda:row.update(track=rec['effects'][0]['track']),'删除或移轨'),
                                    (lambda:row.update(enabled=False),lambda:row.update(enabled=True),'启用状态')):
            change();errors=[]
            for call in (dry,self.run_build):                                                    # 演练和真写报同一个错
                with self.assertRaisesRegex(ValueError,message) as caught:call()
                errors.append(str(caught.exception))
            self.assertEqual(errors[0],errors[1]);self.assertIn(eid,errors[0]);undo()
        self.assertEqual(self.t.deletes,[]);self.assertEqual(len(self.t.calls),calls)            # 都没动时间线
        self.assertIsNot(self.p.active,None)

    def test_apply_takes_the_shared_write_lock_and_dry_run_does_not(self):
        # B3：花字真写以前不拿锁，可能和 B-roll 或另一个花字进程同时写达芬奇
        from engine.broll.live.workspace import write_lock
        from engine.fx.recover import recover
        from engine.fx.cleanup import cleanup
        from engine.fx.review import review
        self.finish();self.refresh();before=copy.deepcopy(self.t.data);saved=(self.work/'操作记录.json').read_text()
        rec=ledger.load(self.work);failed={**rec,'pending':{'status':'failed','plan':rec['plan'],'checkpoint':None}}
        snap=patch('engine.fx.recover.snapshot',side_effect=lambda r,p,t:IO(r,p,t,'p').snapshot());snap.start();self.addCleanup(snap.stop)
        with write_lock(ledger.LOCK_PATH):                                                       # 另一处正在写达芬奇
            with self.assertRaisesRegex(RuntimeError,'另一处正在写达芬奇'):self.run_build()
            with self.assertRaisesRegex(RuntimeError,'另一处正在写达芬奇'):self.run_build(mode='small')
            with patch('engine.fx.cleanup.ResolveIO',IO),self.assertRaisesRegex(RuntimeError,'另一处正在写达芬奇'):cleanup(self.r,self.p,self.t,'p',self.work,True)
            self.t.GetEndFrame=lambda:300
            with patch('engine.fx.review.ResolveIO',IO),self.assertRaisesRegex(RuntimeError,'另一处正在写达芬奇'):review(self.r,self.p,self.t,'p',self.work,apply=True)
            self.assertEqual(self.t.data,before);self.assertEqual((self.work/'操作记录.json').read_text(),saved)
            self.assertFalse(run(self.r,self.p,self.t,'p',self.path,self.work,False,mode='batch')['applied'])   # 演练不拿锁，照常能跑
            ledger.save(self.work,failed)
            self.assertTrue(recover(self.r,self.p,self.t,'p',self.work)['needed'])               # 恢复的演练也不拿锁
            with self.assertRaisesRegex(RuntimeError,'另一处正在写达芬奇'):recover(self.r,self.p,self.t,'p',self.work,True)
            self.assertIn('pending',ledger.load(self.work))
        self.assertTrue(recover(self.r,self.p,self.t,'p',self.work,True)['applied'])             # 锁放开后照常
        self.refresh();self.assertTrue(self.finish()['applied'])

    def test_hand_changed_color_on_kept_title_is_not_blocked_but_plan_colors_are(self):
        # B8：计划要写进去的文字颜色只能是白或 #64A9E8；用户在达芬奇里手改成别的颜色、整条保留的花字不查不拦
        self.finish();rec=ledger.load(self.work);uid=rec['effects'][0]['item_id']
        row=next(r for r in self.t.data['items'] if r['id']==uid)
        row['texts'][0]['inputs'].update(Red1=1,Green1=.2,Blue1=.2);frozen=json.loads(json.dumps(row))    # 用户手改成红色
        rec['effects'][0]['written_state'].update(Red1=1,Green1=1,Blue1=1);ledger.save(self.work,rec)    # 上次写的是白
        result=self.finish()
        self.assertEqual(result['kept_manual_titles'],[rec['effects'][0]['effect_id']])
        self.assertEqual(json.loads(json.dumps(next(r for r in self.t.data['items'] if r['id']==uid))),frozen)
        self.finish()                                                                             # 抄回计划的红色下次也不拦
        self.assertEqual(json.loads(json.dumps(next(r for r in self.t.data['items'] if r['id']==uid))),frozen)
        other=next(e for e in self.pp['effects'] if e['effect_id']!=rec['effects'][0]['effect_id'])
        saved=json.loads(self.path.read_text());target=next(e for e in saved['effects'] if e['effect_id']==other['effect_id'])
        target['overrides']={'Red1':1,'Green1':.8,'Blue1':0};self.pp=saved;self.refresh();self.t.deletes.clear();calls=len(self.t.calls)
        for apply in (False,True):
            with self.assertRaisesRegex(ValueError,'文字颜色 #FFCC00 不在允许范围'):run(self.r,self.p,self.t,'p',self.path,self.work,apply,mode='batch')
        self.assertEqual(self.t.deletes,[]);self.assertEqual(len(self.t.calls),calls)

    def test_build_stops_on_sound_rule_violation_until_user_decides(self):
        # B9：一个镜头 2 秒内不超过两声、同类事件全片同一声音；以前只限“每条花字最多一个音效”
        names=['咻-起身01+可爱.mp3','UI+弹出，弹出+6.mp3','02_清脆鼠标点击.mp3']
        for e,name,group in zip(self.pp['effects'],names,'abc'):
            e['group_id']=group;e['sounds']=[{'filename':name,'start':e['start']}]
        self.refresh()
        for apply in (False,True):
            result=run(self.r,self.p,self.t,'p',self.path,self.work,apply,mode='batch')
            self.assertFalse(result['applied']);self.assertFalse(result['allowed']);self.assertIn('需要用户决定',result['message'])
            rules={v['rule'] for v in result['sound_rules']['violations']};self.assertEqual(rules,{'window','same_event'})
            window=next(v for v in result['sound_rules']['violations'] if v['rule']=='window')
            self.assertEqual(sorted(window['effect_ids']),['effect-1','overlap','second'])
        self.assertEqual(self.t.calls,[]);self.assertFalse((self.work/'操作记录.json').exists())
        # 2026-11-13 用户定：音效放行和版面放行分开——--allow-unsafe 不放行音效，--allow-sounds 才放行
        result=run(self.r,self.p,self.t,'p',self.path,self.work,False,allow_unsafe=True,mode='batch')
        self.assertFalse(result['allowed']);self.assertIn('--allow-sounds',result['message'])
        with patch('engine.fx.executor.audio_placement_preflight',return_value=[]):      # 假达芬奇没有真音效文件，放行之后的音效排位不在这条测试范围
            result=run(self.r,self.p,self.t,'p',self.path,self.work,False,mode='batch',allow_sounds=True)
        self.assertNotEqual(result.get('allowed'),False);self.assertTrue(result['sound_rules']['forced'])

    def test_cli_missing_resolve_object_gives_chinese_stop_not_traceback(self):
        # B1：达芬奇接口返回空时的 AttributeError / IndexError 以前直接甩英文堆栈
        import io as textio
        from contextlib import redirect_stderr
        spec=importlib.util.spec_from_file_location('direct_cli_b1',ROOT/'bin/fx.py');cli=importlib.util.module_from_spec(spec)
        with patch('os.execvpe'):spec.loader.exec_module(cli)
        argv=['fx.py','build','--project','p','--timeline','main','--work-dir','w','--plan','p']
        for exc in (AttributeError("'NoneType' object has no attribute 'GetName'"),IndexError('list index out of range')):
            err=textio.StringIO()
            with patch.object(sys,'argv',argv),patch.object(cli,'connect',return_value=(self.r,self.p,self.t)),patch.object(cli,'run',side_effect=exc),patch('builtins.print') as out:
                self.assertEqual(cli.main(),2)
            message=json.loads(out.call_args.args[0])['error']
            for word in ('操作停止','build','达芬奇接口没有返回对象','下一步',type(exc).__name__):self.assertIn(word,message)

    def test_cli_target_exit_code_and_resume_forwarding(self):
        spec=importlib.util.spec_from_file_location('direct_cli',ROOT/'bin/fx.py');cli=importlib.util.module_from_spec(spec)
        with patch('os.execvpe'):spec.loader.exec_module(cli)
        argv=['fx.py','build','--project','p','--timeline','main','--work-dir','w','--plan','p','--apply','--resume']
        with patch.object(sys,'argv',argv),patch.object(cli,'connect',return_value=(self.r,self.p,self.t)),patch.object(cli,'run',side_effect=direct.NeedsTarget(3,'花字2')) as fn,patch('builtins.print'):
            self.assertEqual(cli.main(),3);self.assertTrue(fn.call_args.args[-1])


if __name__=='__main__':unittest.main()
