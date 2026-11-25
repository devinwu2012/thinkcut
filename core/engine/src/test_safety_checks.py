"""Direct tests for the checks that protect user work: readback validation, proofreading, leftovers, locks, names."""
import json
import locale
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import import_mvp_delivery as importer
import prepare_mvp_delivery as prepare
import script_check
from review_build import proofread_problem

SOURCE, MEDIA = '/videos/clip.MP4', 'media-1'
SEGMENTS = [{'src_in': 10, 'src_out': 40, 'tl_in': 0, 'tl_out': 30}, {'src_in': 60, 'src_out': 90, 'tl_in': 30, 'tl_out': 60}]
SPEC = {'origin': 108000, 'subtitles': [{'startFrame': 108000, 'durationFrames': 30, 'text': '第一句'},
                                        {'startFrame': 108030, 'durationFrames': 30, 'text': '第二句'}]}


class DirectTimelineLocaleTests(unittest.TestCase):
    def test_script_connection_restores_locale_on_success_and_failure(self):
        original_locale = locale.setlocale(locale.LC_ALL)
        timeline = Mock()
        timeline.GetName.return_value = '时间线'
        project = Mock()
        project.GetName.return_value = '工程'
        project.GetCurrentTimeline.return_value = timeline
        resolve = Mock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value = project

        for fails in (False, True):
            def scriptapp(name):
                self.assertEqual(name, 'Resolve')
                locale.setlocale(locale.LC_ALL, 'C')
                self.assertEqual(locale.setlocale(locale.LC_ALL), 'C')
                if fails:
                    raise RuntimeError('模拟连接失败')
                return resolve

            module = SimpleNamespace(scriptapp=scriptapp)
            with self.subTest(fails=fails), \
                    patch.dict(sys.modules, {'DaVinciResolveScript': module}), \
                    patch.dict(importer.os.environ), \
                    patch.object(importer, 'resolve_script_environment', return_value={'PYTHONPATH': ''}):
                try:
                    if fails:
                        with self.assertRaisesRegex(RuntimeError, '无法直接连接达芬奇脚本接口'):
                            importer.direct_timeline('工程', '时间线')
                    else:
                        self.assertIs(importer.direct_timeline('工程', '时间线'), timeline)
                    self.assertEqual(locale.setlocale(locale.LC_ALL), original_locale)
                finally:
                    locale.setlocale(locale.LC_ALL, original_locale)


def probe():
    """What a correct readback of the delivered main line looks like."""
    clips = [{'start': 108000 + s['tl_in'], 'end': 108000 + s['tl_out'], 'duration': s['src_out'] - s['src_in'],
              'media_status': 'Online', 'media_pool_item_id': MEDIA, 'file_path': SOURCE} for s in SEGMENTS]
    subs = [{'start': c['startFrame'], 'duration': c['durationFrames'], 'name': c['text']} for c in SPEC['subtitles']]
    return {'start_frame': 108000, 'end_frame': 108060, 'tracks': {
        'video': {'tracks': [{'items': [dict(c) for c in clips]}]},
        'audio': {'tracks': [{'items': [dict(c) for c in clips]}]},
        'subtitle': {'tracks': [{'items': subs}]}}}


class ReadbackValidationTests(unittest.TestCase):
    def validate(self, readback):
        importer.validate_items(readback, SEGMENTS, SPEC, MEDIA, Path(SOURCE))

    def test_untouched_timeline_passes(self):
        self.validate(probe())

    def test_every_kind_of_hand_edit_is_detected(self):
        edits = {
            'deleted clip': lambda p: p['tracks']['video']['tracks'][0]['items'].pop(),
            'trimmed clip': lambda p: p['tracks']['audio']['tracks'][0]['items'][0].update(end=108025, duration=25),
            'changed subtitle text': lambda p: p['tracks']['subtitle']['tracks'][0]['items'][1].update(name='改过的字幕'),
            'other media': lambda p: p['tracks']['video']['tracks'][0]['items'][1].update(media_pool_item_id='other'),
            'extra track': lambda p: p['tracks']['audio']['tracks'].append({'items': [{'start': 108000}]}),
            'length changed': lambda p: p.update(end_frame=108070),
        }
        for name, edit in edits.items():
            readback = probe()
            edit(readback)
            with self.subTest(name), self.assertRaises(ValueError):
                self.validate(readback)

    def test_subtitle_spec_is_read_from_the_frozen_srt(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / 'a.srt').write_text('1\n00:00:00,000 --> 00:00:01,001\n第一句\n\n2\n00:00:01,001 --> 00:00:02,002\n第二句\n')
            manifest = {'fps': [30000, 1001], 'srt_name': 'a.srt', 'frames': 60, 'subtitle_cues': 2, 'timeline_name': 'T'}
            self.assertEqual(importer.expected_subtitle_spec(package, manifest)['subtitles'], SPEC['subtitles'])
            (package / 'a.srt').write_text('1\n00:00:00,000 --> 00:00:01,500\n第一句\n\n2\n00:00:01,001 --> 00:00:02,002\n第二句\n')
            with self.assertRaises(ValueError):  # overlapping cues
                importer.expected_subtitle_spec(package, manifest)


class LeftoverAndNameTests(unittest.TestCase):
    def test_a_package_that_ever_passed_is_never_treated_as_leftover(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / 'mcp_readback.json').write_text(json.dumps({'passed': False, 'status': 'failed'}))
            self.assertFalse(importer.ever_passed(package))
            (package / 'mcp_readback.previous-abc.json').write_text(json.dumps({'passed': True}))
            self.assertTrue(importer.ever_passed(package))  # a later blocked attempt must not erase the success

    def test_only_exact_program_names_match(self):
        n = importer.NAME
        self.assertTrue(importer.MAIN_TIMELINE.fullmatch(n + '_主线_1006-2200_aaaa'))
        for name in (n + '_主线_1006-2200_aaaa 改', '我的精修版', n + '_主线_1006-2200_aaaa_未完成_abcdef'):
            self.assertIsNone(importer.MAIN_TIMELINE.fullmatch(name), name)
        self.assertTrue(importer.AI_TIMELINE.fullmatch(n + '_主线_1006-2200_aaaa_未完成_abcdef'))
        self.assertIsNone(importer.AI_TIMELINE.fullmatch(n + '_主线_1006-2200_aaaa 改'))

    def test_resolution_must_match_except_in_a_bound_user_project(self):
        self.assertIsNone(importer.resolution_problem([1080, 1920], {'resolution': [1080, 1920]}))
        self.assertIn('不一致', importer.resolution_problem([3840, 2160], {'resolution': [1080, 1920]}))
        self.assertIsNone(importer.resolution_problem([3840, 2160], {'resolution': [1080, 1920], 'existing_project_only': True}))


class ProofreadTests(unittest.TestCase):
    tokens = [{'id': 'a', 'text': '甲'}, {'id': 'b', 'text': '乙'}, {'id': 'c', 'text': '丙'}]

    def test_old_projects_are_exempt(self):
        self.assertIsNone(proofread_problem({}, ['a', 'b'], self.tokens))

    def test_unproofread_project_is_refused(self):
        self.assertIn('还没校对', proofread_problem({'subtitle_proofread': None}, ['a'], self.tokens))

    def test_words_restored_after_proofreading_must_be_proofread(self):
        state = {'subtitle_proofread': {'token_ids': ['a', 'b']}}
        self.assertIsNone(proofread_problem(state, ['a', 'b'], self.tokens))
        self.assertIsNone(proofread_problem(state, ['a'], self.tokens))  # deleting more is fine
        self.assertIn('丙', proofread_problem(state, ['a', 'b', 'c'], self.tokens))


class DeliveryLockTests(unittest.TestCase):
    def test_command_line_corrections_wait_for_a_running_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'review_v2_state.json').write_text('{}')
            (run / 'rows.json').write_text(json.dumps([{'id': 'a', 'before': '甲', 'after': '假', 'reason': '测试'}]))
            from unittest.mock import patch
            with patch.object(script_check, 'RUN', run), patch.object(script_check, 'STATE', run / 'review_v2_state.json'), \
                    prepare.exclusive_lock(run / '.delivering.lock'), \
                    patch.object(prepare.time, 'monotonic', side_effect=[0, 100]):
                with self.assertRaisesRegex(ValueError, '正在保存到达芬奇'):
                    script_check.apply(run / 'rows.json', {'tokens': [{'id': 'a', 'text': '甲'}]})
            self.assertEqual((run / 'review_v2_state.json').read_text(), '{}')

    def test_a_lock_held_from_another_mac_counts_as_busy(self):
        import errno
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'review_v2_state.json').write_text('{}')
            (run / 'rows.json').write_text(json.dumps([{'id': 'a', 'before': '甲', 'after': '假', 'reason': '测试'}]))
            denied = PermissionError(errno.EACCES, 'Permission denied')  # what the NAS reports
            with patch.object(prepare.fcntl, 'flock', side_effect=denied):
                self.assertTrue(prepare.delivering(run))
                with self.assertRaisesRegex(RuntimeError, '占用'):
                    with prepare.exclusive_lock(run / '.x.lock', '占用'):
                        pass
                with patch.object(script_check, 'RUN', run), patch.object(script_check, 'STATE', run / 'review_v2_state.json'), \
                        patch.object(prepare.time, 'monotonic', side_effect=[0, 100]), \
                        self.assertRaisesRegex(ValueError, '正在保存到达芬奇'):
                    script_check.apply(run / 'rows.json', {'tokens': [{'id': 'a', 'text': '甲'}]})
            self.assertEqual((run / 'review_v2_state.json').read_text(), '{}')


    def test_review_server_lock_does_not_block_corrections(self):
        self.check_correction_lock('.review-server.lock', None)

    def test_busy_delivery_lock_preserves_state(self):
        self.check_correction_lock('.delivering.lock', '正在保存到达芬奇')

    def check_correction_lock(self, name, error):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            state = run/'review_v2_state.json'
            state.write_text('{}')
            rows = run/'rows.json'
            rows.write_text(json.dumps([{'id': 'a', 'before': '甲', 'after': '假', 'reason': '测试'}]))
            with patch.object(script_check, 'RUN', run), patch.object(script_check, 'STATE', state), \
                    prepare.exclusive_lock(run/name), patch.object(prepare.time, 'monotonic', side_effect=[0, 0, 100]):
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        script_check.apply(rows, {'tokens': [{'id': 'a', 'text': '甲'}]})
                else:
                    self.assertEqual(script_check.apply(rows, {'tokens': [{'id': 'a', 'text': '甲'}]}), 1)
            if error:
                self.assertEqual(state.read_text(), '{}')
                self.assertFalse((run/'review_state_backups').exists())
            else:
                self.assertEqual(json.loads(state.read_text())['subtitle_token_corrections'][0]['after'], '假')
                self.assertEqual(next((run/'review_state_backups').glob('*.json')).read_text(), '{}')

    def test_exclusive_lock_retries_busy_lock_until_available(self):
        import errno
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(prepare.fcntl, 'flock', side_effect=[BlockingIOError(errno.EAGAIN, 'busy'), None, None]) as flock, \
                    patch.object(prepare.time, 'monotonic', return_value=0), patch.object(prepare.time, 'sleep') as sleep:
                with prepare.exclusive_lock(Path(tmp)/'.x.lock', wait=1):
                    self.assertEqual(flock.call_count, 2)
                sleep.assert_called_once_with(0.2)
                self.assertEqual(flock.call_count, 3)


if __name__ == '__main__':
    unittest.main()
