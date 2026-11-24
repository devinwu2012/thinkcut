"""Offline checks only: never connect to Resolve."""
import asyncio
import json
import tempfile
import sys
import types
from contextlib import asynccontextmanager
import unittest
from pathlib import Path
from unittest.mock import patch
import import_mvp_delivery as delivery


class DeliveryGuardTests(unittest.TestCase):
    def test_frame_rate_mismatch_stops_without_creating_or_changing_settings(self):
        for existing in (None, 'base'):
            with self.subTest(existing=existing):
                async def fake(*args, **kw):
                    return {'settings': '24'}
                with patch.object(delivery, 'call', side_effect=fake) as call:
                    with self.assertRaisesRegex(ValueError, '已在建线/追加片段前停止'):
                        asyncio.run(delivery.frame_rate_preflight(None, {'fps': [30000,1001]}, existing))
                    self.assertTrue(all(c.args[2] in ('get_setting','set_current') for c in call.call_args_list))

    def test_existing_empty_project_does_not_change_project_settings(self):
        import setup_new_resolve as setup
        class Session:
            async def initialize(self):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stdio(_):yield None,None
        mcp=types.ModuleType('mcp');mcp.ClientSession=lambda *a,**kw:Session();mcp.StdioServerParameters=lambda **kw:kw
        client=types.ModuleType('mcp.client');stdio_module=types.ModuleType('mcp.client.stdio');stdio_module.stdio_client=stdio
        async def fake(session,tool,action,**params):
            if action=='get_current':return {'name':'bound'}
            if action=='list':return {'timelines':[]}
            if action=='get_clip_property':return {'properties':'/fake.mp4'}
            if action=='save':return {'success':True}
            raise AssertionError('Unexpected mutation: '+action)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules,{'mcp':mcp,'mcp.client':client,'mcp.client.stdio':stdio_module}), \
             patch.object(setup,'CONFIG',{'existing_project_only':True,'media_pool_item_id':'m','source':'/fake.mp4'}), \
             patch.object(setup,'RUN',Path(tmp)),patch.object(setup,'PROJECT','bound'), \
             patch.object(setup,'verify_source',return_value={}),patch.object(setup,'call',side_effect=fake), \
             patch.object(setup,'resolve_mcp_runtime',return_value=(Path(tmp),Path('python'),Path('server'))):
            asyncio.run(setup.main())

    def test_standard_project_stops_when_resolve_is_in_another_library(self):
        import setup_new_resolve as setup
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'261113 测试片';run=root/'02_制作'/'口播_AI剪辑'/'审核';run.mkdir(parents=True);(root/'01_原始素材').mkdir()
            self.assertIsNone(setup.expected_library({'source':'/别处/口播.mov'},run))      # 不是标准项目：不检查
            (root/'项目.json').write_text('{"name":"x"}')
            self.assertEqual(setup.expected_library({},run),'261113 测试片')
            self.assertEqual(setup.expected_library({'resolve_library':'协作库'},run),'协作库')
            calls=[]
            def session_in(name):
                async def fake(session,tool,action,**params):
                    calls.append(action)
                    if action=='database_capabilities':return {'current':{'DbType':'Disk','DbName':name}}
                    raise AssertionError('切库检查没过就不该再动达芬奇：'+action)
                return fake
            with patch.object(setup,'call',side_effect=session_in('Local Database')):
                with self.assertRaisesRegex(ValueError,'现在开着的项目库是「Local Database」.*应是「261113 测试片」.*没有改动达芬奇.*resolve-library'):
                    asyncio.run(setup.check_library(None,{},run))
            with patch.object(setup,'call',side_effect=session_in('261113 测试片')):
                asyncio.run(setup.check_library(None,{},run))
            self.assertEqual(calls,['database_capabilities']*2)
            (root/'项目.json').write_text(json.dumps({'resolve':{'database':{'DbType':'Disk','DbName':'另指的库'}}},ensure_ascii=False))
            self.assertEqual(setup.expected_library({},run),'另指的库')

    def test_deliver_checks_library_before_touching_the_project(self):
        import setup_new_resolve as setup
        class Session:
            async def initialize(self):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stdio(_):yield None,None
        mcp=types.ModuleType('mcp');mcp.ClientSession=lambda *a,**kw:Session();mcp.StdioServerParameters=lambda **kw:kw
        client=types.ModuleType('mcp.client');stdio_module=types.ModuleType('mcp.client.stdio');stdio_module.stdio_client=stdio
        actions=[]
        async def fake(session,tool,action,**params):
            actions.append(action)
            if action=='database_capabilities':return {'current':{'DbName':'Local Database'}}
            return {'name':'别人的工程','success':True,'projects':[],'timelines':[]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'261113 测试片';run=root/'02_制作'/'口播_AI剪辑'/'审核';run.mkdir(parents=True);(root/'01_原始素材').mkdir();(root/'项目.json').write_text('{}')
            with patch.dict(sys.modules,{'mcp':mcp,'mcp.client':client,'mcp.client.stdio':stdio_module}), \
                 patch.object(setup,'CONFIG',{'existing_project_only':True,'source':'/fake.mp4'}),patch.object(setup,'RUN',run),patch.object(setup,'PROJECT','261113 测试片'), \
                 patch.object(setup,'verify_source',return_value={}),patch.object(setup,'call',side_effect=fake), \
                 patch.object(setup,'resolve_mcp_runtime',return_value=(Path(tmp),Path('python'),Path('server'))):
                with self.assertRaisesRegex(ValueError,'项目库应是「261113 测试片」'):asyncio.run(setup.main())
                self.assertEqual(actions,['database_capabilities'])     # 没保存、没切工程、没新建
                actions.clear()
                async def right_library(session,tool,action,**params):
                    actions.append(action)
                    if action=='database_capabilities':return {'current':{'DbName':'261113 测试片'}}
                    if action=='get_current':return {'name':'别的工程'}
                    if action=='list':return {'projects':['别的工程']}
                    if action=='save':return {'success':True}
                    raise AssertionError('不该新建或改设置：'+action)
                with patch.object(setup,'call',side_effect=right_library),self.assertRaisesRegex(ValueError,'没有工程「261113 测试片」，程序不会替你新建'):
                    asyncio.run(setup.main())

    def test_rounded_source_rate_passes(self):
        async def fake(*args, **kw):return {'settings':'29.97'}
        with patch.object(delivery, 'call', side_effect=fake):
            asyncio.run(delivery.frame_rate_preflight(None, {'fps':[30000,1001]}))

    def run_import(self, *, changed=True, broken=False, failed_import=False):
        before=dict(zip(delivery.PROJECT_FORMAT_KEYS, ['3840','2160','29.97']))
        settings=dict(before)
        async def fake(session, tool, action, **kw):
            if action=='get_setting':return {'settings':settings[kw['name']]}
            if action=='set_setting':
                if broken:raise RuntimeError('fake refusal')
                settings[kw['name']]=kw['value'];return {'success':True}
            if action=='import_timeline_checked':
                if changed:settings.update(dict(zip(delivery.PROJECT_FORMAT_KEYS,['2160','2880','24'])))
                if failed_import:raise RuntimeError('fake import failure')
                return {'name':'final'}
            raise AssertionError(action)
        with tempfile.TemporaryDirectory() as tmp, patch.object(delivery, 'call', side_effect=fake):
            report=Path(tmp)/'report.json'
            if broken or failed_import:
                with self.assertRaisesRegex((ValueError,RuntimeError), '恢复或核验失败' if broken else 'fake import failure'):
                    asyncio.run(delivery.import_drt_preserving_project(None,Path('fake.drt'),report))
            else:
                result,_=asyncio.run(delivery.import_drt_preserving_project(None,Path('fake.drt'),report))
                self.assertEqual(result,{'name':'final'})
            record=json.loads(report.read_text())
            self.assertEqual(record['restored'],not broken)
            if not broken:self.assertEqual(settings,before)
            return record

    def test_drt_changes_are_restored_and_reported(self):
        self.assertEqual(len(self.run_import()['changed']),3)

    def test_unchanged_settings_need_no_restore(self):
        self.assertEqual(self.run_import(changed=False)['changed'],{})

    def test_restore_failure_is_chinese_error_and_saved(self):
        self.assertIn('error',self.run_import(broken=True))

    def test_even_failed_import_restores_project(self):
        self.assertTrue(self.run_import(failed_import=True)['restored'])


if __name__=='__main__':unittest.main()
