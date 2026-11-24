"""Isolated synchronization tests: no Resolve calls and no real selections."""
import json
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
import test_review_v2_server as fixtures
from web_delivery import DeliveryJob, sha
import review_v2_server as server

class ResolveSyncTests(unittest.TestCase):
    request=fixtures.ReviewServerTests.request
    edit=fixtures.ReviewServerTests.edit
    assertUnchanged=fixtures.ReviewServerTests.assertUnchanged
    def setUp(self):
        fixtures.ReviewServerTests.setUp(self)
        command=self.run/'fake_entry.py';command.touch()
        self.job=DeliveryJob(self.run,command,server.LOCK)
        p=patch('review_v2_server.DELIVERY',self.job);p.start();self.addCleanup(p.stop)

    def test_stale_browser_cannot_start_sync(self):
        code,result=self.request('/save-to-resolve',{'state_revision':'old'})
        self.assertEqual(code,409);self.assertTrue(result['reload']);self.assertFalse(self.job.info['running'])

    def test_cross_origin_cannot_start_sync(self):
        code,_=self.request('/save-to-resolve',{'state_revision':'old'},origin='https://example.org')
        self.assertEqual(code,403);self.assertFalse(self.job.info['running'])

    def test_duplicate_click_and_edits_during_job(self):
        started=threading.Event();finish=threading.Event()
        def worker():
            started.set();finish.wait(4)
            with server.LOCK:self.job.info={'status':'failed','running':False,'error':'fixture'}
        self.addCleanup(finish.set)
        with patch.object(self.job,'_work',side_effect=worker) as work:
            revision=self.edit()['state_revision']
            self.assertEqual(self.request('/save-to-resolve',{'state_revision':revision})[0],200)
            self.assertTrue(started.wait(1))
            code,result=self.request('/save-to-resolve',{'state_revision':revision})
            self.assertEqual(code,200);self.assertTrue(result['running']);self.assertEqual(work.call_count,1)
            code,_=self.request(body=self.edit());self.assertEqual(code,409);self.assertUnchanged()
            self.assertTrue(self.request('/resolve-status')[1]['running']);finish.set()

    def test_reload_reads_verified_result_and_detects_new_edits(self):
        latest={'running':False,'status':'complete','timeline':'verified','state_sha256':sha(server.STATE)}
        (self.run/'latest_delivery.json').write_text(json.dumps(latest))
        self.assertTrue(self.request('/resolve-status')[1]['current'])
        self.assertEqual(self.request(body=self.edit())[0],200)
        self.assertFalse(self.request('/resolve-status')[1]['current'])

    def test_child_failure_never_reports_success(self):
        def fail(_command,_timeout,**kwargs):
            kwargs['stdout'].write('RuntimeError: Resolve unavailable\n')
            return type('Exit',(),{'returncode':1})()
        with patch('web_delivery.run_group',side_effect=fail):self.job._work()
        result=self.job.status();self.assertEqual(result['status'],'failed');self.assertFalse(result['running'])
        self.assertIn('Resolve unavailable',result['error']);self.assertTrue(Path(result['log']).is_file())

    def test_write_lock_busy_is_explained_to_the_page(self):
        def busy(_command,_timeout,**kwargs):
            kwargs['stdout'].write('达芬奇正被另一个命令占用，这次没有动达芬奇：thinkcut-edit build（片子 /x，进程 1，开始时间不知道 开始）。等它做完再保存一次。锁文件：/tmp/x\n')
            return type('Exit',(),{'returncode':1})()
        with patch('web_delivery.run_group',side_effect=busy):self.job._work()
        result=self.job.status();self.assertEqual(result['status'],'failed')
        self.assertIn('正在写达芬奇',result['error']);self.assertIn('等它做完',result['error'])

    def test_stuck_delivery_times_out_and_ends_the_whole_process_group(self):
        import os,subprocess,sys,time,web_delivery
        marker=self.run/'grandchild.pid'
        # 交付命令自己再起一个子进程（真正写达芬奇的那层），两个都一直不退出
        self.job.command.write_text('import subprocess,sys,time\n'
            'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n'
            f'open({str(marker)!r},"w").write(str(p.pid))\ntime.sleep(60)\n')
        with patch.object(web_delivery,'DELIVER_TIMEOUT_SECONDS',3):self.job._work()
        result=self.job.status();self.assertEqual(result['status'],'failed');self.assertFalse(result['running'])
        self.assertIn('还没完成',result['error']);self.assertIn('未标记保存成功',result['error'])
        grandchild=int(marker.read_text())
        for _ in range(50):
            alive=subprocess.run(['ps','-p',str(grandchild),'-o','stat='],capture_output=True,text=True).stdout.strip()
            if not alive or alive.startswith('Z'):break
            time.sleep(0.1)
        self.assertTrue(not alive or alive.startswith('Z'),'超时后交付命令起的子进程还活着')


class PublicationTests(unittest.TestCase):
    def test_versioned_files_published_only_after_verified_readback(self):
        import tempfile
        from web_delivery import publish,sha
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder)/'审核';package=run/'mvp_deliveries'/'fixture';package.mkdir(parents=True)
            manifest={'timeline_name':'Clip_主线_1006-2300_bbbb','project':'project','signature':'abc','srt_name':'Clip_主线_1006-2300_bbbb.srt','frames':30,'fps':[30,1],'segments':2,'subtitle_cues':1,'input_sha256':{'review_v2_state.json':'state'}}
            (package/'delivery_manifest.json').write_text(json.dumps(manifest))
            names=['Clip_主线_1006-2300_bbbb.srt','Clip_主线_1006-2300_bbbb_Resolve回存.drt','project_abc.drp']
            for name in names:(package/name).write_text('verified')
            rb={'passed':True,'resolve_render_started':False,'timeline':'Clip_主线_1006-2300_bbbb','delivery_manifest_sha256':sha(package/'delivery_manifest.json'),'artifact_sha256':{name:sha(package/name) for name in names}}
            (package/'mcp_readback.json').write_text(json.dumps(rb))
            output=Path(folder)/'交付';(output/'历史').mkdir(parents=True);(output/'Clip_主线_1006-2200_aaaa.srt').write_text('old')
            (output/'历史'/'Clip_主线_1006-2200_aaaa.srt').write_text('older');(output/'我的成片.mp4').write_text('render')
            with patch('prepare_mvp_delivery.verify_package',return_value=(manifest,{})):
                result=publish(package)
                self.assertEqual(result['timeline'],'Clip_主线_1006-2300_bbbb')
                moved=sorted(p.read_text() for p in (output/'历史').glob('Clip_主线_1006-2200_aaaa*.srt'))
                self.assertEqual(moved,['old','older'])  # moved, and the same-named older copy was not overwritten
                self.assertEqual((output/'我的成片.mp4').read_text(),'render')  # the user's own file stays
                self.assertFalse((output/'Clip_主线_1006-2200_aaaa.srt').exists())
                self.assertTrue((run/'latest_delivery.json').exists())
                (package/'Clip_主线_1006-2300_bbbb.srt').write_text('corrupted')
                with self.assertRaises(ValueError):publish(package)
                self.assertEqual((output/'Clip_主线_1006-2300_bbbb.srt').read_text(),'verified')
                rb['passed']=False;(package/'mcp_readback.json').write_text(json.dumps(rb))
                with self.assertRaises(ValueError):publish(package)

if __name__=='__main__':unittest.main()
