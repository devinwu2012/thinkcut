"""Offline safety regressions: ownership, manual edits, merge and legacy-record refusal."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from engine.fx import ledger
from engine.fx.executor import run, removal_plan, placement_preflight, validate_execution
from engine.fx.merge import structure, fingerprint
from engine.fx.resolve_io import ResolveIO

# 测试不碰真的达芬奇写入锁（~/.cache/ai-talking-head-edit/resolve-write.lock）：换成本进程自己的临时锁文件
_LOCK=tempfile.NamedTemporaryFile(prefix='fx-test-lock-');ledger.LOCK_PATH=_LOCK.name


def title(uid='text',start=110,end=134,text='拍照'):
    return {'id':uid,'name':'effect-1','type':'video','track':2,'start':start,'end':end,
            'props':{'Pan':0,'Tilt':0,'ZoomX':1,'ZoomY':1},
            'texts':[{'inputs':{'StyledText':text,'Font':'font','Style':'Black','Size':.08,
                               'Center':{'1':.5,'2':.8},'GlobalIn':73,'GlobalOut':96},
                      'comp_range':{'COMPN_RenderStart':73},
                      'size_keyframes':{'73':{'1':.08},'76':{'1':.1}}}]}


def timeline(items=None):
    return {'name':'main','id':'main','start':100,'end':300,'fps':29.97,'width':1920,'height':1080,
            'settings':{},'mark_in_out':{},'markers':{'12':{'note':'保留标记'}},
            'tracks':[{'type':'video','index':1,'name':'视频 1','enabled':True,'locked':False},
                      {'type':'video','index':2,'name':'花字','enabled':True,'locked':False}],
            'items':items if items is not None else [
                {'id':'source','name':'source','type':'video','track':1,'start':100,'end':200,
                 'source_start':50,'source_end':149,'media_path':'/source.mp4','media_type':'Video',
                 'media_fps':29.97,'enabled':True,'props':{},'texts':[]}]}


def snap(items=None):
    return {'timeline':timeline(items),'timelines':[{'id':'main','name':'main'}]}


def plan():
    p=json.loads((ROOT/'engine/fx/testdata/plan_example.json').read_text())
    e=p['effects'][0];e.update(effect_id='effect-1',start=110,end=134,layer=1,text='拍照')
    e['speech']={'mode':'visual'}
    p.update(status='ready',timeline={'name':'main','start':100,'end':300})
    e['anchor']['subject_bbox']=[.05,.3,.15,.9];e['layout_approved']=True
    e['precondition_snapshot_sha256']='0'*64
    return p


class ManualTests(unittest.TestCase):
    def test_size_uses_settled_key_not_first_frame(self):
        state=ledger.title_state(title());self.assertEqual(state['Size'],.1)
        self.assertEqual(state['SizeKeyframes'],{'0':{'1':.08},'3':{'1':.1}})

    def test_manual_text_position_timing_and_transform_win(self):
        p=plan();state=ledger.title_state(title());now=copy.deepcopy(state)
        now.update(StyledText='用户改字',Center={'1':.2,'2':.6},start=115,end=139)
        now['clip.Pan']=12
        p['effects'][0]['sounds']=[{'filename':'UI+弹出，弹出+6.mp3','start':110,'gain_db':-3}]
        result,report=ledger.preserve_manual(p,{'effects':[{'effect_id':'effect-1','written_state':state}]},{'effect-1':now})
        e=result['effects'][0]
        self.assertEqual((e['text'],e['anchor']['center'],e['sounds'][0]['start']),('用户改字',[.2,.6],115))
        self.assertEqual(e['overrides']['clip.Pan'],12);self.assertEqual(len(report),1)
        self.assertEqual(p['effects'][0]['text'],'拍照')

    def test_untouched_field_keeps_new_plan_value(self):
        p=plan();p['effects'][0]['text']='计划更新';s=ledger.title_state(title())
        result,report=ledger.preserve_manual(p,{'effects':[{'effect_id':'effect-1','written_state':s}]},{'effect-1':s})
        self.assertEqual(result['effects'][0]['text'],'计划更新');self.assertEqual(report,[])

    def test_manual_deletion_not_resurrected(self):
        with self.assertRaises(ValueError):ledger.preserve_manual(plan(),{'effects':[{'effect_id':'effect-1','written_state':{}}]}, {})

    def test_edited_or_deleted_audio_preserved(self):
        a={'id':'sound','start':110,'end':115,'media_path':'/s.mp3','props':{'AudioVolume':-3}}
        r={'sounds':[{'effect_id':'effect-1','item_id':'sound','written_state':ledger.sound_state(a)}]}
        self.assertEqual(removal_plan(snap([a]),r)[0],['sound'])
        a['props']['AudioVolume']=-6
        removed,preserved=removal_plan(snap([a]),r)
        self.assertFalse(removed);self.assertEqual(preserved[0]['manual_fields'],['AudioVolume'])
        self.assertEqual(removal_plan(snap([]),r)[1][0]['manual_fields'],['deleted'])


class SafetyTests(unittest.TestCase):
    def test_exact_delta_rejects_unplanned_clip_marker_track_timeline(self):
        b=snap();a=copy.deepcopy(b);a['timeline']['items'].append(title())
        self.assertEqual(ledger.verify_delta(b,a,added=['text']),[])
        for mutate in [lambda x:x['timeline']['markers'].clear(),
                       lambda x:x['timeline']['items'][0].update(end=201),
                       lambda x:x['timeline']['tracks'][0].update(enabled=False),
                       lambda x:x['timelines'].append({'id':'oops','name':'archive'})]:
            changed=copy.deepcopy(a);mutate(changed)
            self.assertTrue(ledger.verify_delta(b,changed,added=['text']))

    def test_only_unloaded_zero_controls_of_disabled_shading_are_equivalent(self):
        t=title();t['texts'][0]['inputs'].update(Enabled2=0,Red2=0)
        b=snap([t]);a=copy.deepcopy(b);a['timeline']['items'][0]['texts'][0]['inputs']['Red2']=None
        self.assertEqual(ledger.verify_delta(b,a),[])
        for value in (1,.4):
            b['timeline']['items'][0]['texts'][0]['inputs']['Red2']=value
            self.assertTrue(ledger.verify_delta(b,a))
        b['timeline']['items'][0]['texts'][0]['inputs'].update(Enabled2=1,Red2=0)
        a['timeline']['items'][0]['texts'][0]['inputs']['Enabled2']=1
        self.assertTrue(ledger.verify_delta(b,a))

    def test_tracks_skip_future_occupied_and_disabled(self):
        b=snap([title()]);effects=plan()['effects']
        assignments,created=placement_preflight(b,effects,[])
        self.assertEqual(assignments['effect-1'],3)
        self.assertEqual(placement_preflight(b,effects,['text']),(dict([('effect-1',1)]),[]))
        b['timeline']['tracks'][1]['enabled']=False
        effects[0]['track']=2
        with self.assertRaises(ValueError):placement_preflight(b,effects,['text'])

    def test_wrong_project_stops_before_any_write(self):
        r,p,t=Mock(),Mock(),Mock();p.GetName.return_value='wrong';p.GetUniqueId.return_value='p'
        r.GetProjectManager.return_value.GetCurrentProject.return_value=p
        with self.assertRaises(RuntimeError):ResolveIO(r,p,t,'expected')
        p.SetCurrentTimeline.assert_not_called()

    def test_seek_waits_for_stable_playhead_and_refuses_permanent_mismatch(self):
        io=ResolveIO.__new__(ResolveIO);io.write=Mock();io.guard=Mock()
        t=Mock();t.GetCurrentTimecode.side_effect=['old','target','old','target','target','target','target']
        with patch('engine.fx.resolve_io.time.sleep'):io.seek(t,'target')
        self.assertEqual(t.GetCurrentTimecode.call_count,7)
        t.GetCurrentTimecode.side_effect=None;t.GetCurrentTimecode.return_value='old'
        with patch('engine.fx.resolve_io.time.sleep'),self.assertRaises(RuntimeError):io.seek(t,'target')

    def reopen_io(self, loads, current=None):
        """保存重开用的假达芬奇：pm 是 Mock，LoadProject 按 loads 依次返回。"""
        io=ResolveIO.__new__(ResolveIO);io.write=lambda fn,*args:fn(*args);io.guard=Mock();io.activate=Mock()
        io.name='片子';io.db={'DbName':'片子库'};io.main_name='main';io.main_id='tl';io.p=Mock();io.main=Mock()
        io.pm=Mock();io.pm.SaveProject.return_value=True;io.pm.CloseProject.return_value=True
        io.pm.GetCurrentDatabase.return_value={'DbName':'片子库'};io.pm.GetCurrentProject.side_effect=current or (lambda:None)
        io.pm.LoadProject.side_effect=loads
        main=Mock();main.GetUniqueId.return_value='tl'
        find=patch('engine.fx.resolve_io.find_timeline',return_value=main);find.start();self.addCleanup(find.stop)
        return io

    def assertOnlyReopenedOwnProject(self, io):
        # 只允许：保存、关闭、读当前库/当前工程、按名字重开这一个工程；不切库、不开别的工程、不新建
        self.assertLessEqual({c[0] for c in io.pm.method_calls},{'SaveProject','CloseProject','GetCurrentDatabase','GetCurrentProject','LoadProject'})
        self.assertEqual({c.args for c in io.pm.LoadProject.call_args_list},{('片子',)})

    def test_save_reopen_first_try_is_unchanged(self):
        project=Mock();io=self.reopen_io([project])
        with patch('engine.fx.resolve_io.time.sleep') as sleep:io.save_reopen()
        self.assertIs(io.p,project);self.assertEqual(io.pm.LoadProject.call_count,1);sleep.assert_not_called()
        self.assertOnlyReopenedOwnProject(io)

    def test_save_reopen_retries_until_the_project_opens(self):
        project=Mock();io=self.reopen_io([None,None,project])
        with patch('engine.fx.resolve_io.time.sleep') as sleep:io.save_reopen()
        self.assertIs(io.p,project);self.assertEqual(io.pm.LoadProject.call_count,3);self.assertEqual(sleep.call_count,2)
        io.pm.SaveProject.assert_called_once();io.pm.CloseProject.assert_called_once()
        self.assertOnlyReopenedOwnProject(io)

    def test_save_reopen_that_never_opens_says_saved_and_open_by_hand(self):
        from engine.fx import resolve_io
        closed=Mock();io=self.reopen_io(lambda name:None);io.p=closed
        with patch('engine.fx.resolve_io.time.sleep'),self.assertRaisesRegex(RuntimeError,'工程「片子」已经保存成功.*重新打开失败.*手动打开工程「片子」后再继续'):
            io.save_reopen()
        self.assertEqual(io.pm.LoadProject.call_count,resolve_io.REOPEN_TRIES);self.assertGreaterEqual(resolve_io.REOPEN_TRIES,2)
        self.assertOnlyReopenedOwnProject(io);io.guard.assert_not_called()

    def test_save_reopen_stops_retrying_when_user_opened_another_project(self):
        other=Mock();other.GetName.return_value='别的片子'
        state=iter([None,other]);io=self.reopen_io(lambda name:None,current=lambda:next(state))
        with patch('engine.fx.resolve_io.time.sleep'),self.assertRaisesRegex(RuntimeError,'其他工程'):io.save_reopen()
        self.assertEqual(io.pm.LoadProject.call_count,1);self.assertOnlyReopenedOwnProject(io)

    def test_draft_and_center_animation_refused(self):
        t=Mock();t.GetName.return_value='main';t.GetStartFrame.return_value=100;t.GetSetting.return_value=29.97
        for invalid in (None,[],{'effects':[None]}):
            with self.assertRaises(ValueError):validate_execution(invalid,t)
        p=plan();p['status']='draft'
        with self.assertRaises(ValueError):validate_execution(p,t)
        p=plan();p['effects'][0]['animation']=[{'input':'Center','frame':110,'value':[.2,.3]}]
        with self.assertRaises(ValueError):validate_execution(p,t)

    def test_dry_run_never_calls_writing_io(self):
        p=plan();b=snap();p['effects'][0]['precondition_snapshot_sha256']=ledger.digest(b['timeline'])
        t=Mock();t.GetName.return_value='main';t.GetStartFrame.return_value=100;t.GetSetting.return_value=29.97
        io=Mock();io.db={}
        with tempfile.TemporaryDirectory() as d,patch('engine.fx.executor.ResolveIO',return_value=io),patch('engine.fx.executor.snapshot',return_value=b),patch('engine.fx.executor.check_layout',return_value={'valid':True}):
            f=Path(d)/'plan.json';f.write_text(json.dumps(p));r=run(Mock(),Mock(),t,'p',f,Path(d)/'work')
            self.assertFalse(r['applied']);io.write.assert_not_called();io.activate.assert_not_called();io.new_timeline.assert_not_called()
            self.assertFalse((Path(d)/'work').exists())

    def test_old_copy_based_record_is_refused(self):
        # 2026-11-13 用户定：只在主线上直接做，“先在副本上做、审完再重放”那条路去掉了；遇到旧记录要说清楚
        p=plan();t=Mock();t.GetName.return_value='main';t.GetStartFrame.return_value=100;t.GetSetting.return_value=29.97
        project=Mock();project.GetUniqueId.return_value='pid';io=Mock();io.db={}
        record={'project_id':'pid','database':{},'deferred':True}
        with tempfile.TemporaryDirectory() as d,patch('engine.fx.executor.ResolveIO',return_value=io),patch('engine.fx.executor.ledger.load',return_value=record):
            f=Path(d)/'plan.json';f.write_text(json.dumps(p))
            with self.assertRaisesRegex(ValueError,'新的工作目录'):run(Mock(),project,t,'p',f,d,True)
            io.snapshot.assert_not_called();io.write.assert_not_called()

    def test_legacy_nested_or_decomposed_record_is_refused_everywhere(self):
        # E1：旧版（嵌套时间线、用户分解后核对）的兼容代码已删；遇到旧记录 build、小修、recover、cleanup、review 都停下，不乱动
        from engine.fx.recover import recover
        from engine.fx.cleanup import cleanup
        from engine.fx.review import review
        p=plan();t=Mock();t.GetName.return_value='main';t.GetStartFrame.return_value=100;t.GetSetting.return_value=29.97;t.GetUniqueId.return_value='main'
        project=Mock();project.GetUniqueId.return_value='pid';io=Mock();io.db={}
        base={'project_id':'pid','database':{},'timeline_id':'main','timeline':'main','effects':[]}
        legacy=[{**base},                                              # 没有 placement_mode（旧版都没有）
                {**base,'placement_mode':'nested'},
                {**base,'placement_mode':'direct','nested':[{'item_id':'nest'}]},
                {**base,'placement_mode':'direct','decomposed':True},
                {**base,'placement_mode':'direct','decomposed':True,'pending':{'status':'failed'}}]
        for record in legacy:
            with tempfile.TemporaryDirectory() as d,patch('engine.fx.executor.ResolveIO',return_value=io),patch('engine.fx.review.ResolveIO',return_value=io), \
                    patch('engine.fx.cleanup.ResolveIO',return_value=io):
                f=Path(d)/'plan.json';f.write_text(json.dumps(p));ledger.save(d,record);saved=(Path(d)/'操作记录.json').read_text()
                for call in (lambda:run(Mock(),project,t,'p',f,d,True),lambda:run(Mock(),project,t,'p',f,d),
                             lambda:run(Mock(),project,t,'p',f,d,True,mode='small'),
                             lambda:recover(Mock(),project,t,'p',d,True),lambda:recover(Mock(),project,t,'p',d),
                             lambda:cleanup(Mock(),project,t,'p',d,True),lambda:cleanup(Mock(),project,t,'p',d),
                             lambda:review(Mock(),project,t,'p',d,apply=True),lambda:review(Mock(),project,t,'p',d)):
                    with self.assertRaisesRegex(ValueError,'旧版做法留下的操作记录，已不支持；请换新工作目录在主线上重新 build'):call()
                self.assertEqual((Path(d)/'操作记录.json').read_text(),saved)      # 记录原样不动
        io.snapshot.assert_not_called();io.write.assert_not_called();io.activate.assert_not_called()

    def test_decompose_check_command_is_gone(self):
        import importlib.util,io as textio
        from contextlib import redirect_stderr
        spec=importlib.util.spec_from_file_location('fx_cli_e1',ROOT/'bin/fx.py');cli=importlib.util.module_from_spec(spec)
        with patch('os.execvpe'):spec.loader.exec_module(cli)
        with patch.object(sys,'argv',['fx.py','decompose-check','--project','p','--timeline','t','--work-dir','w']),redirect_stderr(textio.StringIO()):
            with self.assertRaises(SystemExit):cli.main()
        import engine.fx.executor as ex
        for name in ('decompose_check','decomposition_report','nested_state'):self.assertFalse(hasattr(ex,name))

    def test_ledger_save_uses_a_unique_temp_file_each_time(self):
        # B3：以前固定用 操作记录.tmp，两个进程同时存会互相写坏
        seen=[]
        from engine.fx import snapshot as snap_mod
        real=snap_mod.write_json
        with tempfile.TemporaryDirectory() as d,patch('engine.fx.ledger.write_json',side_effect=lambda path,data:(seen.append(Path(path).name),real(path,data))):
            ledger.save(d,{'n':1});ledger.save(d,{'n':2})
            self.assertEqual(ledger.load(d),{'n':2})
            self.assertEqual(sorted(x.name for x in Path(d).iterdir()),['操作记录.json'])      # 没留下临时文件
            with patch('engine.fx.ledger.os.replace',side_effect=OSError('模拟：替换失败')):
                with self.assertRaises(OSError):ledger.save(d,{'n':3})
            self.assertEqual(ledger.load(d),{'n':2});self.assertEqual(len(list(Path(d).iterdir())),1)   # 失败不留半个文件，原记录还在
        self.assertEqual(len(set(seen)),3);self.assertNotIn('操作记录.tmp',seen)


class MergeTests(unittest.TestCase):
    def test_copy_ids_do_not_change_structure(self):
        a=timeline();b=copy.deepcopy(a);b['items'][0]['id']='new-id'
        self.assertEqual(fingerprint(a),fingerprint(b))


if __name__=='__main__':unittest.main()
