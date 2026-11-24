import contextlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import editing_preflight
import edit_project


class PreflightTests(unittest.TestCase):
    def test_missing_alignment_python_fails_without_running(self):
        with patch('editing_preflight.subprocess.run') as process:
            result=editing_preflight.alignment_runtime('/missing/python')
        self.assertFalse(result['alignment_dependencies_ready']);process.assert_not_called()

    def test_alignment_checks_imports_and_reports_versions(self):
        with patch('editing_preflight.subprocess.run',return_value=subprocess.CompletedProcess([],0,'{"qwen-asr":"test"}\n','')):
            result=editing_preflight.alignment_runtime(sys.executable)
        self.assertTrue(result['alignment_dependencies_ready']);self.assertEqual(result['alignment_versions']['qwen-asr'],'test')

    def test_alignment_import_failure_is_controlled(self):
        with patch('editing_preflight.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','missing dependency')):
            self.assertFalse(editing_preflight.alignment_runtime(sys.executable)['alignment_dependencies_ready'])

    def test_delivery_dependency_failure_precedes_setup(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(sys,'argv',['edit_project','deliver','--project-dir',tmp]), \
             patch.dict(os.environ,{'THINKCUT_RESOLVE_LOCK':str(Path(tmp)/'resolve-write.lock')}), \
             patch('prepare_mvp_delivery.exclusive_lock',return_value=contextlib.nullcontext()), \
             patch('run_mvp.resolve_mcp_runtime',return_value=(Path(tmp),Path(sys.executable),Path(tmp)/'server')), \
             patch('run_mvp.node_runtime',side_effect=RuntimeError('missing subtitles')),patch.object(edit_project,'execute') as execute:
            with self.assertRaisesRegex(RuntimeError,'missing subtitles'):edit_project.main()
            # Only the offline proofread check ran; nothing touched Resolve.
            self.assertEqual([c.args[0] for c in execute.call_args_list],['script_check.py'])

    def test_init_missing_alignment_stops_before_paid_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            video=Path(tmp)/'video.mp4';video.write_bytes(b'fixture');project=Path(tmp)/'new'
            info={'tracks':[{'handler':'vide','fps':{'num':30000,'den':1001},'constant_presentation_duration':True},{'handler':'soun'}]}
            with patch.object(sys,'argv',['edit_project','init','--project-dir',str(project),'--video',str(video)]),patch.object(edit_project,'probe',return_value=info), \
                 patch.object(edit_project,'alignment_runtime',return_value={'alignment_dependencies_ready':False,'alignment_error':'missing align'}),patch.object(edit_project.subprocess,'run') as paid:
                with self.assertRaises(SystemExit):edit_project.main()
                paid.assert_not_called();self.assertFalse(project.exists())

    def test_init_multi_audio_stops_before_paid_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            video=Path(tmp)/'video.mp4';video.write_bytes(b'fixture')
            info={'tracks':[{'handler':'vide','fps':{'num':30000,'den':1001},'constant_presentation_duration':True}]+[{'handler':'soun'}]*2}
            with patch.object(sys,'argv',['edit_project','init','--project-dir',str(Path(tmp)/'new'),'--video',str(video)]),patch.object(edit_project,'probe',return_value=info),patch.object(edit_project.subprocess,'run') as paid:
                with self.assertRaisesRegex(ValueError,'单音轨'):edit_project.main()
                paid.assert_not_called()

    def test_init_delayed_audio_stops_before_paid_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            video=Path(tmp)/'video.mov';video.write_bytes(b'fixture');project=Path(tmp)/'new'
            edits=[{'movie_duration':162240,'media_time':-1},{'movie_duration':69876380,'media_time':0}]
            info={'movie_timescale':240000,'tracks':[{'handler':'vide','fps':{'num':30000,'den':1001},'constant_presentation_duration':True},{'handler':'soun','timescale':48000,'edits':edits}]}
            with patch.object(sys,'argv',['edit_project','init','--project-dir',str(project),'--video',str(video)]),patch.object(edit_project,'probe',return_value=info),patch.object(edit_project.subprocess,'run') as paid:
                with self.assertRaises(SystemExit):edit_project.main()
                paid.assert_not_called();self.assertFalse(project.exists())

    def fake_init(self,tmp,standard):
        """离线跑一次 init：识别那一步换成假的（不调付费接口），识别结果预先放好。"""
        root=Path(tmp)/'261113 测试片';raw=root/'01_原始素材'/'A roll';raw.mkdir(parents=True)
        if standard:(root/'项目.json').write_text('{}')
        video=raw/'口播.mov';video.write_bytes(b'fixture');project=root/'02_制作'/'口播_AI剪辑'
        (project/'转写').mkdir(parents=True)
        (project/'转写/transcription_manifest.json').write_text(json.dumps({'audio_stream':0}))
        (project/'转写/doubao_turbo.json').write_text(json.dumps({'result':{'utterances':[{'text':'你好','start_time':0,'end_time':500}]}},ensure_ascii=False))
        info={'tracks':[{'handler':'vide','fps':{'num':30000,'den':1001},'constant_presentation_duration':True,'sample_count':300,'display_size':[1920,1080]},{'handler':'soun'}]}
        def run():
            with patch.object(sys,'argv',['edit_project','init','--project-dir',str(project),'--video',str(video)]),patch.object(edit_project,'probe',return_value=info), \
                 patch.object(edit_project,'alignment_runtime',return_value={'alignment_dependencies_ready':True}),patch.object(edit_project,'run_self') as paid:
                edit_project.main()
            return paid
        return root,project/'审核',run

    def test_interrupted_init_is_not_stuck_half_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,run,init=self.fake_init(tmp,standard=False)
            replace=edit_project.os.replace;written=[]
            def cut_off(source,target):
                if len(written)==2:raise OSError('模拟写到一半断开')
                written.append(Path(target).name);return replace(source,target)
            with patch.object(edit_project.os,'replace',side_effect=cut_off),self.assertRaises(OSError):init()
            self.assertFalse((run/'project_runtime.json').exists())     # 标志文件最后才写：中断后不会被当成“已初始化”
            self.assertFalse(edit_project.initialized(run));self.assertEqual(list(run.glob('.*.partial')),[])
            init()                                                      # 原来：先写了 project_runtime.json，再跑只会报“项目已初始化”
            self.assertTrue(edit_project.initialized(run))
            self.assertEqual(json.loads((run/'sentence_units.json').read_text())[0]['text'],'你好')
            self.assertTrue(json.loads((run/'project_runtime.json').read_text())['project'].startswith('_mcp_AI口播_v1_'))
            with self.assertRaises(SystemExit),contextlib.redirect_stderr(__import__('io').StringIO()):init()   # 初始化完整后仍不覆盖

    def test_half_initialized_project_from_older_version_is_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,run,init=self.fake_init(tmp,standard=False)
            run.mkdir();(run/'project_runtime.json').write_text('{"name":"v1"}')   # 旧版先写这个文件，写完就断了
            init();self.assertTrue(edit_project.initialized(run))
            (run/'sentence_units.json').unlink();(run/'review_v2_state.json').write_text('{}')
            with self.assertRaises(SystemExit),contextlib.redirect_stderr(__import__('io').StringIO()):init()   # 已有审核状态：绝不重写

    def test_standard_project_binds_resolve_project_named_after_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,run,init=self.fake_init(tmp,standard=True);init()
            config=json.loads((run/'project_runtime.json').read_text())
            self.assertEqual((config['project'],config['existing_project_only']),(root.name,True))

    def test_retry_uncertain_reaches_paid_transcription_only_when_asked(self):
        import argparse
        for flag in (False,True):
            args=argparse.Namespace(audio_stream=None,align_python=None,retry_uncertain=flag)
            self.assertEqual('--retry-uncertain' in edit_project.transcribe_command('v.mov',Path('/p'),args),flag)
            self.assertEqual('--retry-uncertain' in edit_project.self_command('init',Path('/p'),args,'--video','v.mov'),flag)
            self.assertNotIn('--retry-uncertain',edit_project.self_command('align',Path('/p'),args))
        with tempfile.TemporaryDirectory() as tmp:      # 原来：prepare 不认识这个参数，完整剪辑流程没法确认重试
            video=Path(tmp)/'v.mov';video.write_bytes(b'x');commands=[]
            def capture(command):commands.append(command);raise SystemExit(0)
            with patch.object(sys,'argv',['edit_project','prepare','--project-dir',str(Path(tmp)/'new'),'--video',str(video),'--retry-uncertain']), \
                 patch.object(edit_project,'run_self',side_effect=capture),self.assertRaises(SystemExit):edit_project.main()
            self.assertEqual(commands[0][2],'init');self.assertIn('--retry-uncertain',commands[0])

    def test_main_line_name_is_short_version_counting_earlier_cuts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'02_制作'; first=base/'精装房_AI剪辑'; first.mkdir(parents=True)
            self.assertEqual(edit_project.main_line_name(first),'v1')
            (first/'审核').mkdir(); (first/'审核/project_runtime.json').write_text('{}')
            (base/'临时').mkdir()
            self.assertEqual(edit_project.main_line_name(base/'精装房_AI剪辑_v2'),'v2')
            self.assertEqual(edit_project.main_line_name(first),'v1')


class ResolveWriteLockTests(unittest.TestCase):
    """剪口播交付和 B-roll／花字／thinkcut-edit 拿同一把达芬奇写入锁；锁换成临时文件，不碰真实的 ~/.cache 锁。"""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name)/'片子_AI剪辑';self.project.mkdir()
        self.lock=Path(self.tmp.name)/'cache'/'resolve-write.lock'
        for p in (patch.dict(os.environ,{'THINKCUT_RESOLVE_LOCK':str(self.lock)}),patch.object(sys,'argv',['edit_project','deliver','--project-dir',str(self.project)]),
                  patch('prepare_mvp_delivery.exclusive_lock',return_value=contextlib.nullcontext())):
            p.start();self.addCleanup(p.stop)

    def held(self):
        """另开一个句柄试着拿锁：拿不到＝正被占着。"""
        with self.lock.open('a+') as probe:
            try:fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return True
            fcntl.flock(probe,fcntl.LOCK_UN);return False

    def test_deliver_holds_the_shared_write_lock_while_touching_resolve(self):
        seen=[]
        def step(script,*_):
            if script!='script_check.py':seen.append((script,self.held(),json.loads(self.lock.read_text(encoding='utf-8'))))
        with patch.object(edit_project,'deliver_preflight',return_value=sys.executable),patch.object(edit_project,'execute',side_effect=step):
            edit_project.main()
        self.assertEqual([s[0] for s in seen],['setup_new_resolve.py','run_mvp.py'])
        self.assertTrue(all(held for _,held,_ in seen))
        # 锁文件内容：隔壁 thinkcut-edit 认的 JSON 四个键
        self.assertEqual(seen[0][2],{'片子目录':str(self.project.resolve()),'命令':'口播剪辑 deliver','进程号':os.getpid(),'开始时间':seen[0][2]['开始时间']})
        # 做完放锁，锁文件留着不删（删了会出现两把锁）
        self.assertFalse(self.held());self.assertTrue(self.lock.is_file())

    def test_busy_write_lock_stops_before_anything_touches_resolve(self):
        for content,expected in (('4242','口播剪辑（B-roll/花字写入）'),                                   # B-roll／花字只写进程号
                                 (json.dumps({'片子目录':'/片子','命令':'thinkcut-edit build','进程号':4242,'开始时间':'2026-11-21 10:00:00'},ensure_ascii=False),'thinkcut-edit build'),
                                 ('',' 不知道哪个命令')):
            self.lock.parent.mkdir(exist_ok=True)
            with self.lock.open('a+',encoding='utf-8') as other:
                fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB);other.truncate(0);other.write(content);other.flush()
                with patch.object(edit_project,'deliver_preflight') as preflight,patch.object(edit_project,'execute') as execute, \
                     self.assertRaises(SystemExit) as stopped:
                    edit_project.main()
                message=str(stopped.exception)
                self.assertIn('达芬奇正被另一个命令占用',message);self.assertIn('等它做完',message);self.assertIn(expected.strip(),message)
                # 只跑了离线的字幕校对检查；没做交付前检查，没碰达芬奇，也没占这个项目自己的交付锁
                self.assertEqual([c.args[0] for c in execute.call_args_list],['script_check.py']);preflight.assert_not_called()
                self.assertFalse((self.project/'审核'/'.delivering.lock').exists())
                # 对方的锁文件内容原样留着
                other.seek(0);self.assertEqual(other.read(),content)

    def test_failed_delivery_releases_the_write_lock(self):
        with patch.object(edit_project,'deliver_preflight',side_effect=RuntimeError('missing subtitles')),patch.object(edit_project,'execute'), \
             self.assertRaisesRegex(RuntimeError,'missing subtitles'):
            edit_project.main()
        self.assertFalse(self.held())

    def test_shared_lock_file_is_the_one_broll_and_fx_use(self):
        import inspect
        sys.path.insert(0,str(Path(edit_project.__file__).resolve().parents[1]))
        from engine.broll.live import workspace
        self.assertEqual(edit_project.RESOLVE_WRITE_LOCK,Path.home()/'.cache/ai-talking-head-edit/resolve-write.lock')
        self.assertIn(".cache/ai-talking-head-edit/resolve-write.lock",inspect.getsource(workspace.write_lock))


if __name__=='__main__':unittest.main()
