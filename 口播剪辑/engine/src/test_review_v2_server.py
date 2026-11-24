"""Isolated HTTP regression tests; never read or mutate the real review state."""
import concurrent.futures
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import paragraph_editor
import review_v2_server as server


class ReviewServerTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.run=Path(self.directory.name)
        self.initial={'overrides':{},'notes':{},'seen':[]}
        self.manifest={'cards':[{'id':'retake','default':'keep','cut_frames':[30,36]}],'pending':[]}
        aligned={'alignment_id':'fixture','tokens':[
            {'id':'a','unit_id':0,'char_start':0,'text':'甲','start_ms':1000,'end_ms':1200,'ready':True},
            {'id':'b','unit_id':0,'char_start':1,'text':'乙','start_ms':1250,'end_ms':1400,'ready':True},
            {'id':'c','unit_id':0,'char_start':2,'text':'丙','start_ms':1500,'end_ms':1700,'ready':True}]}
        for name,value in [('review_v2_state.json',self.initial),('review_v2.json',self.manifest),
                           ('character_alignment.json',aligned),('sentence_units.json',[{'id':0,'text':'甲乙丙','start_ms':1000}])]:
            (self.run/name).write_text(json.dumps(value,ensure_ascii=False))
        self.original=(self.run/'review_v2_state.json').read_bytes()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.port=self.http.server_address[1]
        for target,value in [('review_v2_server.RUN',self.run),('review_v2_server.STATE',self.run/'review_v2_state.json'),
                             ('review_v2_server.PORT',self.port),('review_build.RUN',self.run),('paragraph_editor.RUN',self.run)]:
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        p=patch('review_v2_server.refresh_repetitions',side_effect=lambda *_:None);p.start();self.addCleanup(p.stop)
        p=patch('review_v2_server.rebuild');p.start();self.addCleanup(p.stop)
        thread=threading.Thread(target=self.http.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)

    def request(self,path='/editor-action',body=None,origin=None,host=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        headers={'Host':host or f'127.0.0.1:{self.port}',
                 'Origin':origin or f'http://127.0.0.1:{self.port}','Content-Type':'application/json'}
        connection.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers=headers)
        response=connection.getresponse();raw=response.read();status=response.status;connection.close()
        return status,json.loads(raw) if response.getheader('Content-Type','').startswith('application/json') else raw

    def edit(self,**extra):
        return {'action':'cut','ids':['b'],'alignment_id':'fixture','revision':0,
                'state_revision':paragraph_editor.state_revision(self.initial),**extra}

    def assertUnchanged(self):
        self.assertEqual((self.run/'review_v2_state.json').read_bytes(),self.original)

    def test_non_object_and_invalid_id_return_json_error(self):
        for body in [[],42,'text']:
            code,result=self.request(body=body);self.assertEqual(code,400);self.assertIn('error',result)
        code,_=self.request('/subtitle-corrections',{'rows':'not a list'});self.assertEqual(code,400)
        self.assertUnchanged()

    def test_cross_origin_and_wrong_host_are_rejected(self):
        self.assertEqual(self.request(body=self.edit(),origin='https://example.org')[0],403)
        self.assertEqual(self.request(body=self.edit(),host='example.org')[0],403)
        self.assertUnchanged()

    def test_final_validation_failure_never_persists(self):
        def break_mapping(state,_manifest):state['pause_edits']={'accepted':[{'cut_frames':[31,32]}]}
        with patch('review_v2_server.refresh_repetitions',side_effect=break_mapping):
            code,result=self.request(body=self.edit())
        self.assertEqual(code,400);self.assertIn('partially overlaps',result['error']);self.assertUnchanged()
        self.assertFalse((self.run/'review_state_backups').exists())

    def test_old_page_address_says_merged_into_shenpiantai(self):
        # 2026-11-22 用户定：单独的审核网页并进审片台“口播剪辑”栏；直接打开后台地址只说一句并指到审片台
        code,page=self.request('/');page=page.decode()
        self.assertEqual(code,200);self.assertIn('已并入审片台',page);self.assertNotIn('<script',page);self.assertNotIn('saveResolve',page)
        (self.run/'审片台.json').write_text(json.dumps({'film_dir':'/x','url':'http://127.0.0.1:8765/'}))
        code,page=self.request('/');self.assertIn('href="http://127.0.0.1:8765/#%E6%96%87%E6%A1%88"',page.decode())
        (self.run/'审片台.json').write_text(json.dumps({'url':'javascript:alert(1)'}))
        self.assertNotIn('javascript:',self.request('/')[1].decode())
        for gone in ('/source.mp4','/review_editor.html'):self.assertEqual(self.request(gone)[0],404)

    def test_page_info_gives_preview_lengths_for_the_shenpiantai_page(self):
        self.assertNotIn('segment_seconds',self.request('/editor-page-info')[1])
        with patch('review_v2_server.CONFIG',{'source':'/x.mov'}),patch('review_v2_server.TOTAL',3974),patch('review_v2_server.verify_source'):
            info=self.request('/editor-page-info')[1]
        self.assertAlmostEqual(info['segment_seconds'],12.012);self.assertAlmostEqual(info['source_duration'],3974/(30000/1001))

    def test_removed_legacy_routes_are_refused(self):
        for path in ('/review','/text-review'):
            self.assertEqual(self.request(path,{'id':'retake','action':'apply'})[0],403)
        self.assertUnchanged()

    def test_edits_are_refused_while_a_delivery_runs(self):
        from prepare_mvp_delivery import exclusive_lock
        with exclusive_lock(self.run/'.delivering.lock'):
            self.assertEqual(self.request(body=self.edit())[0],409)
            self.assertEqual(self.request('/subtitle-corrections',{'rows':[{'id':'b','before':'乙','after':'已','reason':'测试'}]})[0],409)
        self.assertUnchanged()

    def test_success_backs_up_old_state_and_returns_exact_removed_ids(self):
        code,result=self.request(body=self.edit());self.assertEqual(code,200)
        self.assertEqual(result['removed_token_ids'],['b'])
        backups=list((self.run/'review_state_backups').glob('*.json'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),self.original)
        saved=json.loads((self.run/'review_v2_state.json').read_text())
        self.assertEqual(result['state_revision'],paragraph_editor.state_revision(saved))
        self.assertEqual(result['revision'],1)

    def test_concurrent_browser_edits_only_one_commits(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.request(body=self.edit()),range(2)))
        self.assertEqual(sorted(result[0] for result in results),[200,409])
        saved=json.loads((self.run/'review_v2_state.json').read_text())
        self.assertEqual(saved['editor']['cursor'],1);self.assertEqual(len(saved['editor']['operations']),1)

    def test_whole_state_revision_detects_a_subtitle_fix_made_meanwhile(self):
        code,_=self.request('/subtitle-corrections',{'rows':[{'id':'b','before':'乙','after':'已','reason':'测试'}]});self.assertEqual(code,200)
        code,result=self.request(body=self.edit());self.assertEqual(code,409);self.assertTrue(result['reload'])
        self.assertNotIn('editor',json.loads((self.run/'review_v2_state.json').read_text()))

    def test_replace_failure_preserves_original_and_cleans_only_own_tempfile(self):
        with patch('review_v2_server.os.replace',side_effect=OSError('disk failure')):
            code,result=self.request(body=self.edit())
        self.assertEqual(code,500);self.assertIn('error',result);self.assertUnchanged()
        self.assertEqual(list(self.run.glob('.review-state-*')),[])

    def test_backup_write_failure_can_be_retried(self):
        with patch('review_v2_server.os.fsync',side_effect=OSError('disk failure')):
            self.assertEqual(self.request(body=self.edit())[0],500)
        self.assertUnchanged();self.assertEqual(list((self.run/'review_state_backups').glob('*.json')),[])
        self.assertEqual(self.request(body=self.edit())[0],200)

    def test_corrupt_read_has_controlled_error(self):
        (self.run/'character_alignment.json').write_text('{')
        code,result=self.request('/editor-api');self.assertEqual(code,500);self.assertIn('error',result)

    def test_word_inside_range_correction_returns_chinese_hint_and_keeps_it(self):
        whole={'ids':['a','b'],'before':'甲乙','after':'甲 乙','reason':'整体'}
        self.assertEqual(self.request('/subtitle-corrections',{'rows':[whole]})[0],200)
        saved=(self.run/'review_v2_state.json').read_bytes()
        code,result=self.request('/subtitle-corrections',{'rows':[{'id':'b','before':'乙','after':'已','reason':'网页手动修改字幕'}]})
        self.assertEqual(code,400);self.assertIn('属于一段整体纠错「甲乙」',result['error']);self.assertIn('先撤销整段',result['error'])
        self.assertEqual((self.run/'review_v2_state.json').read_bytes(),saved)
        code,result=self.request('/subtitle-corrections',{'rows':[{'id':'a','remove':True}]})
        self.assertEqual(code,200);self.assertEqual(result['subtitle_token_corrections'],[])

    def test_state_missing_field_returns_json_error_instead_of_dropping_connection(self):
        broken={**self.initial,'editor':{'operations':[],'cursor':0}}
        (self.run/'review_v2_state.json').write_text(json.dumps(broken))
        code,result=self.request(body=self.edit(state_revision=paragraph_editor.state_revision(broken)))
        self.assertEqual(code,500);self.assertIn('审核状态文件缺少内容',result['error']);self.assertIn('alignment_id',result['error'])
        self.assertEqual(json.loads((self.run/'review_v2_state.json').read_text()),broken)

    def test_preview_conversion_timeout_returns_json_error(self):
        source=self.run/'原片.mov';source.write_bytes(b'x')
        timeout=server.subprocess.TimeoutExpired('ffmpeg',120)
        with patch('review_v2_server.CONFIG',{'source':str(source)}),patch('review_v2_server.TOTAL',360),patch('review_v2_server.verify_source'), \
                patch('review_v2_server.shutil.which',return_value='ffmpeg'),patch('review_v2_server.subprocess.run',side_effect=timeout):
            code,result=self.request('/source-part/0.mp4')
        self.assertEqual(code,504);self.assertIn('超过限定时间',result['error'])
        self.assertEqual(list((self.run/'preview_parts').glob('*')),[])

    def test_preview_without_ffmpeg_says_so_in_chinese(self):
        source=self.run/'原片.mov';source.write_bytes(b'x')
        with patch('review_v2_server.CONFIG',{'source':str(source)}),patch('review_v2_server.TOTAL',360),patch('review_v2_server.verify_source'), \
                patch('review_v2_server.shutil.which',return_value=None),patch('review_v2_server.subprocess.run') as process:
            code,result=self.request('/source-part/0.mp4')
        self.assertEqual(code,500);self.assertIn('找不到 ffmpeg',result['error']);self.assertIn('setup_mac.sh',result['error'])
        process.assert_not_called();self.assertEqual(list((self.run/'preview_parts').glob('*')),[])

    def test_replaced_source_blocks_preview_and_edit(self):
        from source_guard import SourceChanged
        with patch('review_v2_server.verify_source',side_effect=SourceChanged('原片已改变')):
            code,result=self.request('/source-part/0.mp4');self.assertEqual(code,409);self.assertEqual(result['error'],'原片已改变')
            code,result=self.request(body=self.edit());self.assertEqual(code,400);self.assertEqual(result['error'],'原片已改变')
        self.assertUnchanged()

    def test_persist_holds_delivery_lock_through_backup_and_replace(self):
        from prepare_mvp_delivery import delivering
        dump=server.json.dump;replace=server.os.replace
        def locked_dump(*args,**kwargs):
            self.assertTrue(delivering(self.run));self.assertTrue(list((self.run/'review_state_backups').glob('*.json')))
            return dump(*args,**kwargs)
        def locked_replace(*args,**kwargs):
            self.assertTrue(delivering(self.run));return replace(*args,**kwargs)
        with patch.object(server.json,'dump',side_effect=locked_dump),patch.object(server.os,'replace',side_effect=locked_replace):
            server.persist_state(self.initial,{**self.initial,'note':'saved'})
        self.assertFalse(delivering(self.run))

    def test_persist_retries_lock_and_rechecks_revision_after_acquiring(self):
        from prepare_mvp_delivery import exclusive_lock
        import time
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with exclusive_lock(self.run/'.delivering.lock'):
                future=pool.submit(server.persist_state,self.initial,{**self.initial,'note':'saved'})
                time.sleep(.15);self.assertFalse(future.done())
                (self.run/'review_v2_state.json').write_text(json.dumps({**self.initial,'changed':True}))
            with self.assertRaises(paragraph_editor.StateConflict):future.result(timeout=2)
        self.assertFalse((self.run/'review_state_backups').exists())

    def test_persist_busy_timeout_is_chinese(self):
        from prepare_mvp_delivery import exclusive_lock
        import time
        with exclusive_lock(self.run/'.delivering.lock'),patch.object(time,'monotonic',side_effect=[0,4]):
            with self.assertRaisesRegex(paragraph_editor.StateConflict,'正在保存到达芬奇或另一处正在写入，请稍后重试'):
                server.persist_state(self.initial,self.initial)
        self.assertUnchanged();self.assertFalse((self.run/'review_state_backups').exists())

    def test_persist_external_byte_change_before_replace_is_conflict(self):
        changed=json.dumps(self.initial,ensure_ascii=False,indent=4).encode();dump=server.json.dump
        def mutate(*args,**kwargs):
            dump(*args,**kwargs);(self.run/'review_v2_state.json').write_bytes(changed)
        with patch.object(server.json,'dump',side_effect=mutate),self.assertRaisesRegex(paragraph_editor.StateConflict,'写入期间'):
            server.persist_state(self.initial,{**self.initial,'note':'saved'})
        self.assertEqual((self.run/'review_v2_state.json').read_bytes(),changed)
        self.assertEqual(list(self.run.glob('.review-state-*')),[])
        self.assertEqual(next((self.run/'review_state_backups').glob('*.json')).read_bytes(),self.original)


if __name__=='__main__':unittest.main()
