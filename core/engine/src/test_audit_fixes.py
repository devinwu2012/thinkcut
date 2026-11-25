import copy
import io
from contextlib import redirect_stdout
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from source_guard import fingerprint, verify_source, single_audio
from prepare_mvp_delivery import exclusive_lock
from paragraph_editor import manual_changes
from local_repetitions import detect
from review_build import active_map, effective_cuts
import prepare_new_review as review


class SourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name);self.run=self.project/'审核';self.run.mkdir()
        self.source=(self.project/'source.mp4').resolve();self.source.write_bytes(b'original')
        s=self.source.stat();self.record={'path':str(self.source),'size':s.st_size,
            'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns,'fingerprint':fingerprint(self.source)}
        (self.project/'转写').mkdir();self.record_path=self.project/'转写/source_manifest.json'
        self.record_path.write_text(json.dumps(self.record));self.config={'source':str(self.source)}

    def test_original_source_passes_and_creates_no_baseline(self):
        self.assertEqual(verify_source(self.config,self.run,sampled=True)['source_size'],8)
        self.assertFalse((self.run/'delivery_approved_v1').exists())

    def test_replaced_source_is_rejected_before_setup_connects(self):
        import setup_new_resolve as setup
        self.source.write_bytes(b'replaced longer source')
        with patch.object(setup,'CONFIG',{**self.config,'project':'_mcp_Test'}),patch.object(setup,'PROJECT','_mcp_Test'),patch.object(setup,'RUN',self.run):
            import asyncio
            with self.assertRaisesRegex(ValueError,'原片已改变'):asyncio.run(setup.main())

    def test_changed_fingerprint_is_rejected(self):
        self.record['fingerprint']['digest']='wrong';self.record_path.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError,'抽样指纹'):verify_source(self.config,self.run,sampled=True)

    def test_existing_baseline_is_not_rebound(self):
        folder=self.run/'delivery_approved_v1';folder.mkdir();p=folder/'approval_manifest.json'
        p.write_text(json.dumps({'source':str(self.source),'source_size':999,'source_mtime_ns':self.record['mtime_ns']}));before=p.read_bytes()
        with self.assertRaisesRegex(ValueError,'不会重写'):verify_source(self.config,self.run)
        self.assertEqual(p.read_bytes(),before)

    def test_single_audio_rejects_multiple_or_unmapped_streams(self):
        single_audio({'tracks':[{'handler':'vide'},{'handler':'soun'}]})
        for info,stream in [({'tracks':[{'handler':'soun'}]},1),({'tracks':[{'handler':'soun'}]*2},0)]:
            with self.assertRaisesRegex(ValueError,'单音轨'):single_audio(info,stream)

    def test_project_lock_excludes_a_second_process_and_releases(self):
        lock=self.run/'.review-server.lock'
        code='from pathlib import Path; from prepare_mvp_delivery import exclusive_lock; import sys\nwith exclusive_lock(Path(sys.argv[1])): print("locked")'
        args=[sys.executable,'-c',code,str(lock)]
        with exclusive_lock(lock):
            blocked=subprocess.run(args,cwd=Path(__file__).parent,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(blocked.returncode,0);self.assertIn('Another delivery',blocked.stderr)
        self.assertEqual(subprocess.run(args,cwd=Path(__file__).parent,capture_output=True,text=True,timeout=5).returncode,0)

    def test_second_server_cannot_bind_another_port_for_same_project(self):
        import review_v2_server as server
        with exclusive_lock(self.run/'.review-server.lock'),patch.object(server,'RUN',self.run),patch.object(server,'ThreadingHTTPServer') as listener:
            with self.assertRaisesRegex(RuntimeError,'已有审核服务'):server.main()
            listener.assert_not_called()


class RestartOnUpdateTests(unittest.TestCase):
    def test_version_file_is_the_repo_root_one_and_a_vanished_file_counts_as_updated(self):
        # 2026-11-24 仓库合并后后台要手动重启：老进程盯各块自己的 VERSION，被删后一直读到 None，从不重启
        import review_v2_server as server
        self.assertEqual(server.VERSION_FILE,Path(__file__).resolve().parents[3]/'VERSION')      # 全家一份，在仓库根目录
        self.assertFalse(server.version_changed('2026-11-24.8','2026-11-24.8'))
        self.assertTrue(server.version_changed('2026-11-24.8','2026-11-24.9'))
        self.assertTrue(server.version_changed('2026-11-24.8',None))             # 文件被挪走：也重启
        self.assertFalse(server.version_changed(None,None));self.assertFalse(server.version_changed(None,'2026-11-24.9'))   # 启动时就没有：没东西可比，不乱重启


class InitialSelectionTests(unittest.TestCase):
    def run_review(self,text,tokens,start,end,reason='fixture'):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'审核';run.mkdir();(root/'转写').mkdir()
            units=[{'id':0,'text':text}]
            for name,value in [('sentence_units.json',units),('character_alignment.json',{'tokens':tokens}),
                ('review_decisions.json',{'reviewer':'fixture','limitations':[], 'cuts':[{'unit_id':0,'char_start':start,'char_end':end,'text':text[start:end],'reason':reason}]})]:
                (run/name).write_text(json.dumps(value))
            (root/'转写/doubao_turbo.json').write_text(json.dumps({'result':{'utterances':[{'words':[{'text':t['text'],'start_time':t['start_ms'],'end_time':t['end_ms']} for t in tokens]}]}}))
            def no_repeats(state,*_):state['repeat_edits']={'candidates':[]}
            with patch.object(review,'RUN',run),patch.object(review,'CONFIG',{'name':'fixture'}),patch.object(review,'TOTAL',300),patch.object(review,'verify_source'),patch.object(review,'refresh',side_effect=no_repeats),patch.object(review,'build_full'):
                try:review.main()
                except ValueError:
                    self.assertFalse((run/'review_v2_state.json').exists());self.assertFalse((run/'review_v2.json').exists());raise
                return json.loads((run/'review_v2.json').read_text())

    def test_same_frame_neighbor_is_rejected_before_state_saved(self):
        tokens=[{'id':str(i),'unit_id':0,'char_start':i,'char_end':i+1,'text':c,'start_ms':a,'end_ms':b,'ready':True}
                for i,(c,a,b) in enumerate([('甲',0,10),('乙',10,20),('丙',100,200)])]
        with self.assertRaisesRegex(ValueError,'同一帧'):self.run_review('甲乙丙',tokens,0,1)

    def english_tokens(self):
        parts=[('API',0,3),('测',3,4),('试',4,5),('结',5,6),('束',6,7)]
        return [dict(id=str(i),unit_id=0,text=t,char_start=a,char_end=b,start_ms=100+i*200,end_ms=200+i*200,ready=True) for i,(t,a,b) in enumerate(parts)]

    def test_partial_english_word_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'完整发音词'):self.run_review('API测试结束',self.english_tokens(),1,5)

    def stutter_tokens(self,text):
        return [dict(id=str(i),unit_id=0,text=c,char_start=i,char_end=i+1,start_ms=100+i*200,end_ms=250+i*200,ready=True) for i,c in enumerate(text)]

    def test_stutter_cut_must_remove_the_repeated_copy(self):
        # 2026-11-22 261122 反馈单 新12：删口吃“我我们”时 AI 写成了句首的“我”（char_start 0），删成“们从头跟到尾”；现在停下并指出真正重复的位置
        text='我们从头跟到尾，还研发了我我们自己的系统'
        tokens=self.stutter_tokens(text)
        with self.assertRaisesRegex(ValueError,r'删完读作「们从头跟到尾，还」.*char_start 12（…尾，还研发了我我们自己的系统…）'):
            self.run_review(text,tokens,0,1,'口吃重复“我我们”，删一个')
        out=io.StringIO()
        with redirect_stdout(out):result=self.run_review(text,tokens,12,13,'口吃重复“我我们”，删前一个')
        self.assertEqual(result['cards'][0]['removed_text'],['我'])
        self.assertIn('删字核对 第 1 条（第 0 句 12–13）：…跟到尾，还研发了[我]我们自己的系统…  删后读作：…跟到尾，还研发了我们自己的系统…',out.getvalue())
        # 隔着标点的口吃（“我，我们”）也认；不是“重复”类的删改（如删语气词“呃”）不查，但照样打印前后文给 AI 核对
        self.run_review('我，我们走',self.stutter_tokens('我，我们走'),0,1,'口吃')
        out=io.StringIO()
        with redirect_stdout(out):self.run_review('那呃我们走',self.stutter_tokens('那呃我们走'),1,2,'语气词')
        self.assertIn('删后读作：…那我们走…',out.getvalue())

    def test_whole_english_word_remains_valid(self):
        result=self.run_review('API测试结束',self.english_tokens(),0,3)
        self.assertEqual(result['cards'][0]['removed_text'],['API'])


class RestoredRepeatOriginTests(unittest.TestCase):
    def test_first_cut_refresh_uses_in_memory_alignment(self):
        # 2026-11-13 审查：初剪的重复检测要用修复后的对齐（内存里那份），和之后网页重算读到的一致
        import local_repetitions as lr
        with tempfile.TemporaryDirectory() as t:
            run=Path(t);(run/'sentence_units.json').write_text('[]');(run/'character_alignment.json').write_text(json.dumps({'alignment_id':'磁盘旧版','tokens':[]}))
            with patch.object(lr,'RUN',run):
                first=lr.refresh({},{'cards':[]},{'alignment_id':'修复后','tokens':[]})
                self.assertEqual(first['repeat_edits']['alignment_id'],'修复后')
                self.assertEqual(lr.refresh({},{'cards':[]})['repeat_edits']['alignment_id'],'磁盘旧版')

    def test_restoration_refresh_undo_redo_preserve_origin_without_mutation(self):
        chars='户外户外拍摄';starts=[100,200,1000,1100,1200,1300]
        tokens=[dict(id=f'u0c{i}',unit_id=0,char_start=i,char_end=i+1,text=c,start_ms=starts[i],end_ms=starts[i]+100,ready=True) for i,c in enumerate(chars)]
        units=[{'id':0,'text':chars}];aligned={'tokens':tokens};manifest={'cards':[]}
        state={'editor':{'cursor':1,'operations':[{'action':'restore','ids':['u0c0','u0c1'],'ranges':[[3,9]],'text':'户外'}]}}
        for cursor in [1,0,1]:
            state['editor']['cursor']=cursor
            state['repeat_edits']={'candidates':detect(tokens,units,effective_cuts([], {k:v for k,v in state.items() if k!='repeat_edits'}),state)}
            before=copy.deepcopy(state);mapping=active_map([],state,aligned)
            shown=manual_changes(manifest,state,aligned,mapping['removed_token_ids'],units)
            self.assertEqual(shown['manual_restored_token_ids'],['u0c0','u0c1'] if cursor else [])
            self.assertEqual(state,before)


class IntentionalRepeatTests(unittest.TestCase):
    def check_repeat(self, text, phrase, gap, expected):
        offset=text.index(phrase)+len(phrase)
        ts=[dict(id=f'u0c{i}',unit_id=0,char_start=i,char_end=i+1,text=c,
                 start_ms=100+i*100+(gap if i>=offset else 0),
                 end_ms=200+i*100+(gap if i>=offset else 0),ready=True) for i,c in enumerate(text)]
        rows=detect(ts,[{'id':0,'text':text}],[],{})
        self.assertEqual(rows[0]['phrase'],phrase)
        self.assertEqual(rows[0]['status'],expected)

    def test_emphatic_and_action_repeats_stay(self):
        self.check_repeat('一直卡一直卡然后就重启','一直卡',0,'keep')
        self.check_repeat('我自己也是经常忙着忙着就忽略了','忙着',80,'keep')

    def test_clear_restart_after_pause_still_cuts(self):
        self.check_repeat('户外户外拍摄','户外',700,'apply')

    def test_uncertain_short_repeat_is_reviewed(self):
        self.check_repeat('户外户外拍摄','户外',300,'review')


class ZeroDurationRepeatTests(unittest.TestCase):
    def test_zero_length_word_in_repeat_is_reviewed_not_crashing(self):
        # 2026-10-16 真片“所以所以”：对齐把第一个“以”压成 0 时长，检测不能报错，也不能自动补剪
        chars='所以所以基本';starts=[100,200,400,500,600,700];ends=[200,200,500,600,700,800]
        tokens=[dict(id=f'u0c{i}',unit_id=0,char_start=i,char_end=i+1,text=c,start_ms=starts[i],end_ms=ends[i],ready=i!=1) for i,c in enumerate(chars)]
        units=[{'id':0,'text':chars}]
        rows=detect(tokens,units,[],{})
        self.assertEqual([r['phrase'] for r in rows],['所以'])
        self.assertEqual(rows[0]['status'],'review')
        cut=[[3,6]]   # 已删掉前一次“所以”：只记录，不因残留间隙再补剪
        rows=detect(tokens,units,cut,{})
        self.assertTrue(rows and all(r['status'] in ('already_removed','review') for r in rows))


if __name__=='__main__':unittest.main()
