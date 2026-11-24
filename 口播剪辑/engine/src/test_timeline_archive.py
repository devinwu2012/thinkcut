import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
import import_mvp_delivery as importer
import prepare_mvp_delivery as prepare


class TimelineNamingTests(unittest.TestCase):
    def test_new_package_has_one_readable_main_line(self):
        meta = {'version': 'mvp-v3', 'timeline_label': '1006-2251_abcd'}
        final, base, srt = prepare.timeline_names('abcdef123456', meta)
        self.assertEqual((final, srt), (prepare.NAME + '_主线_1006-2251_abcd', final + '.srt'))
        self.assertEqual(base, prepare.NAME + '_无字幕_abcdef123456')

    def test_older_packages_keep_their_names(self):
        final, base, _ = prepare.timeline_names('abcdef123456', {'version': 'mvp-v2'})
        self.assertEqual((final, base), (prepare.NAME + '_紧剪_abcdef123456_含字幕', prepare.NAME + '_紧剪_abcdef123456_无字幕'))

    def test_label_must_belong_to_the_signature(self):
        with self.assertRaises(ValueError):
            prepare.timeline_names('abcdef123456', {'version': 'mvp-v3', 'timeline_label': '1006-2251_ffff'})


class FakeSession:
    def __init__(self, timelines, clips, subfolders=(), history=()):
        self.timelines, self.clips, self.subfolders, self.history, self.calls = timelines, clips, list(subfolders), list(history), []

    async def call_tool(self, tool, args):
        action, params = args['action'], args['params']
        self.calls.append((tool, action, params))
        payload = {('timeline', 'list'): {'timelines': [{'name': n} for n in self.timelines]},
                   ('folder', 'get_clips'): {'clips': self.history if params.get('path') == 'Master/历史版本' else self.clips},
                   ('folder', 'get_subfolders'): {'subfolders': [{'name': n} for n in self.subfolders] if params.get('path') == 'Master' else []}}.get((tool, action), {'success': True})
        text = type('Text', (), {'type': 'text', 'text': json.dumps(payload)})
        return type('Result', (), {'isError': False, 'content': [text]})


class ArchiveTests(unittest.TestCase):
    def test_main_line_in_nested_archive_moves_back(self):
        session = FakeSession(['final'], [])
        async def fake(session, tool, action, **params):
            path = params.get('path')
            if action == 'list':return {'timelines':[{'name':'final'}]}
            if action == 'get_subfolders':
                return {'subfolders':[{'name':n} for n in {'Master':['Archive'], 'Master/Archive':['nested']}.get(path,[])]}
            if action == 'get_clips':
                return {'clips':[{'name':'final','id':'f'}] if path == 'Master/Archive/nested' else []}
            session.calls.append((tool,action,params));return {'success':True}
        with patch.object(importer,'call',side_effect=fake):
            self.assertEqual(asyncio.run(importer.archive_old_timelines(session,'final')),0)
        self.assertIn(('media_pool','move_clips',{'clip_ids':['f'],'target_path':'Master'}),session.calls)

    def test_inventory_requires_master_and_only_allowed_new_names(self):
        session = FakeSession(['old','base','final','final_未完成_abcdef'],[{'name':'final'}])
        allowed = {'base','final','final_未完成_abcdef'}
        asyncio.run(importer.validate_timeline_inventory(session,{'old'},allowed,'final'))
        session.timelines.append('final_archived_v01')
        with self.assertRaisesRegex(ValueError,'达芬奇里多出了时间线：final_archived_v01'):
            asyncio.run(importer.validate_timeline_inventory(session,{'old'},allowed,'final'))
        session.timelines.pop();session.clips=[]
        with self.assertRaisesRegex(ValueError,'不在 Master'):
            asyncio.run(importer.validate_timeline_inventory(session,{'old'},allowed,'final'))

    def test_only_earlier_ai_timelines_are_moved_and_nothing_is_deleted(self):
        n = importer.NAME
        ai = [n + '_紧剪_0123456789ab_含字幕', n + '_紧剪_0123456789ab_无字幕', n + '_无字幕_aaaaaaaaaaaa', n + '_主线_1006-2200_aaaa']
        keep = n + '_主线_1006-2251_bbbb'
        user = ['我的精剪版', n + '_主线_1006-2200_aaaa 改', n + '.MP4']
        clips = [{'name': name, 'id': str(i)} for i, name in enumerate(ai + [keep] + user)]
        session = FakeSession(ai + [keep] + user[:2], clips)
        moved = asyncio.run(importer.archive_old_timelines(session, keep))
        self.assertEqual(moved, 4)
        self.assertIn(('media_pool', 'add_subfolder', {'parent_path': 'Master', 'name': '历史版本'}), session.calls)
        self.assertIn(('media_pool', 'move_clips', {'clip_ids': ['0', '1', '2', '3'], 'target_path': 'Master/历史版本'}), session.calls)
        self.assertFalse(any('delete' in action for _, action, _ in session.calls))

    def test_existing_history_folder_is_reused_and_nothing_to_move_is_quiet(self):
        n = importer.NAME
        session = FakeSession([n + '_主线_1006-2251_bbbb'], [{'name': n + '_主线_1006-2251_bbbb', 'id': '9'}], ['历史版本'])
        self.assertEqual(asyncio.run(importer.archive_old_timelines(session, n + '_主线_1006-2251_bbbb')), 0)
        self.assertEqual([a for _, a, _ in session.calls], ['list', 'get_clips', 'get_subfolders', 'get_clips', 'get_subfolders', 'set_current_folder'])

    def test_main_line_created_inside_history_is_moved_back_to_master(self):
        n = importer.NAME
        keep, old = n + '_主线_1006-2259_cccc', n + '_主线_1006-2258_aaaa'
        session = FakeSession([keep, old], [{'name': old, 'id': '1'}], ['历史版本'], [{'name': keep, 'id': '2'}])
        self.assertEqual(asyncio.run(importer.archive_old_timelines(session, keep)), 1)
        moves = [p for _, a, p in session.calls if a == 'move_clips']
        self.assertEqual(moves, [{'clip_ids': ['1'], 'target_path': 'Master/历史版本'}, {'clip_ids': ['2'], 'target_path': 'Master'}])
        self.assertEqual(session.calls[-1][1:], ('set_current_folder', {'path': 'Master'}))


class EditedMainLineTests(unittest.TestCase):
    def run_check(self, unchanged):
        n = importer.NAME
        old, legacy, keep = n + '_主线_1006-2200_aaaa', n + '_紧剪_0123456789ab_含字幕', n + '_主线_1006-2251_bbbb'
        names = [old, legacy, keep, n + '_无字幕_aaaaaaaaaaaa', '我的精剪版', old + ' 改']  # a user-renamed copy must be ignored
        session = FakeSession(names, [{'name': x, 'id': str(i)} for i, x in enumerate(names)])
        def validate(*_):
            if session.calls[-2][2]['name'] not in unchanged:
                raise ValueError('edited')
        with tempfile.TemporaryDirectory() as tmp:
            package = importer.Path(tmp)
            (package / 'trial_keep_segments.json').write_text('{"segments": []}')
            with patch.object(importer, 'package_for', return_value=(package, {'source': {'media_pool_item_id': 'm', 'path': '/v'}})), \
                 patch.object(importer, 'expected_subtitle_spec', return_value={}), \
                 patch.object(importer, 'validate_items', side_effect=validate):
                return asyncio.run(importer.edited_main_lines(session, keep)), session

    def test_untouched_main_lines_pass(self):
        n = importer.NAME
        edited, session = self.run_check({n + '_主线_1006-2200_aaaa', n + '_紧剪_0123456789ab_含字幕'})
        self.assertEqual(edited, [])
        checked = [p['name'] for _, a, p in session.calls if a == 'set_current']
        self.assertEqual(checked, [n + '_主线_1006-2200_aaaa', n + '_紧剪_0123456789ab_含字幕'])

    def test_hand_edited_main_line_is_reported(self):
        n = importer.NAME
        edited, _ = self.run_check({n + '_紧剪_0123456789ab_含字幕'})
        self.assertEqual(edited, [n + '_主线_1006-2200_aaaa'])

    def test_interface_error_is_not_reported_as_hand_edit(self):
        n = importer.NAME
        old = n + '_主线_1006-2200_aaaa'
        class Flaky(FakeSession):
            async def call_tool(self, tool, args):
                if args['action'] == 'probe_timeline_structure':
                    text = type('Text', (), {'type': 'text', 'text': json.dumps({'error': 'Resolve busy'})})
                    return type('Result', (), {'isError': False, 'content': [text]})
                return await super().call_tool(tool, args)
        session = Flaky([old], [{'name': old, 'id': '1'}])
        with tempfile.TemporaryDirectory() as tmp:
            package = importer.Path(tmp)
            (package / 'trial_keep_segments.json').write_text('{"segments": []}')
            with patch.object(importer, 'package_for', return_value=(package, {'source': {'media_pool_item_id': 'm', 'path': '/v'}})):
                with self.assertRaisesRegex(RuntimeError, '达芬奇接口出错，没有查完.*再保存一次') as caught:
                    asyncio.run(importer.edited_main_lines(session, 'other'))
        self.assertNotIsInstance(caught.exception, importer.ResolveCallError)
        self.assertIn('Resolve busy', str(caught.exception))

    def test_main_line_without_package_counts_as_edited(self):
        n = importer.NAME
        session = FakeSession([n + '_主线_1006-2200_aaaa'], [{'name': n + '_主线_1006-2200_aaaa', 'id': '1'}])
        with patch.object(importer, 'package_for', return_value=(None, None)):
            self.assertEqual(asyncio.run(importer.edited_main_lines(session, 'other')), [n + '_主线_1006-2200_aaaa'])


class DeliveringLockTests(unittest.TestCase):
    def test_page_sees_a_delivery_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = importer.Path(tmp)
            self.assertFalse(prepare.delivering(run))
            with prepare.exclusive_lock(run / '.delivering.lock'):
                self.assertTrue(prepare.delivering(run))
            self.assertFalse(prepare.delivering(run))


if __name__ == '__main__':
    unittest.main()
