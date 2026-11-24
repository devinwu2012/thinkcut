"""Offline interval, readback and source-write boundary regressions."""
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
from engine.fx.review import review_ranges, marker_text, review, read_segments
import engine.fx.test_executor  # noqa: F401  换成临时写入锁，测试不碰真的达芬奇写入锁


def effect(uid, start, end, group=None, kind='keyword', text=None):
    return {'effect_id': uid, 'effect': {'effect_id': uid, 'kind': kind, 'group_id': group,
                                       'text': text or uid},
            'written_state': {'start': start, 'end': end, 'StyledText': text or uid}}


class RangeTests(unittest.TestCase):
    def test_padding_and_clipping_both_ends(self):
        rows = review_ranges({'effects': [effect('a', 102, 120), effect('b', 280, 299)]}, 100, 300)
        self.assertEqual([(r['start'], r['end']) for r in rows], [(100, 150), (250, 300)])

    def test_group_staggered_titles_before_padding(self):
        rows = review_ranges({'effects': [effect('a', 110, 150, 'g'), effect('b', 135, 165, 'g')]},
                             100, 300, 5, 0)
        self.assertEqual((rows[0]['start'], rows[0]['end'], len(rows)), (105, 170, 1))
        self.assertEqual(len(rows[0]['effects']), 2)

    def test_merge_is_transitive_and_strictly_less_than_gap(self):
        record = {'effects': [effect('a', 100, 110), effect('b', 119, 129), effect('c', 138, 148)]}
        self.assertEqual(len(review_ranges(record, 100, 300, 0, 10)), 1)
        record['effects'][1]['written_state']['start'] = 120
        rows = review_ranges(record, 100, 300, 0, 10)
        self.assertEqual([(r['start'], r['end']) for r in rows], [(100, 110), (120, 148)])

    def test_overlap_merges_with_zero_gap_and_containment_keeps_end(self):
        record = {'effects': [effect('a', 100, 200), effect('b', 110, 120), effect('c', 199, 220)]}
        self.assertEqual([(r['start'], r['end']) for r in review_ranges(record, 100, 300, 0, 0)], [(100, 220)])

    def test_unowned_plan_effects_never_included(self):
        record = {'effects': [effect('owned', 110, 120)], 'plan': {'effects': [effect('not-built', 240, 250)]}}
        self.assertEqual(len(review_ranges(record, 100, 300)), 1)

    def test_sounds_extend_group_and_current_state_wins(self):
        record = {'effects': [effect('a', 110, 120, 'g')], 'sounds': [
            {'effect_id': 'a', 'written_state': {'start': 110, 'end': 120},
             'current_state': {'start': 105, 'end': 180, 'media_path': '/fx/pop.mp3'}}]}
        row = review_ranges(record, 100, 300, 0, 0)[0]
        self.assertEqual((row['start'], row['end']), (105, 180))
        self.assertEqual(row['effects'][1]['content'], 'pop.mp3')

    def test_deleted_and_disabled_audio_not_replayed(self):
        record = {'effects': [effect('a', 110, 120)], 'sounds': [
            {'effect_id': 'a', 'manual_fields': ['deleted']},
            {'effect_id': 'a', 'written_state': {'start': 210, 'end': 250, 'enabled': False}}]}
        self.assertEqual(len(review_ranges(record, 100, 300, 0, 0)[0]['effects']), 1)

    def test_invalid_empty_and_missing_records_fail(self):
        for record in (None, {}, {'effects': []}, {'effects': [effect('a', 400, 450)]},
                       {'effects': [effect('a', 130, 120)]}):
            with self.assertRaises(ValueError): review_ranges(record, 100, 300)
        for pad, gap in ((-1, 0), (0, -1), (1.5, 0)):
            with self.assertRaises(ValueError): review_ranges({'effects': [effect('a', 110, 120)]}, 100, 300, pad, gap)

    def test_all_marker_categories_and_hints(self):
        rows = [effect('大字', 110, 130, 'g'), effect('规格', 110, 130, 'g', 'spec'),
                effect('照片', 110, 130, 'g', 'photo_label'), effect('应用_相机', 110, 130, 'g', 'photo_label')]
        record = {'effects': rows, 'sounds': [{'effect_id': '大字', 'written_state':
                  {'start': 110, 'end': 120, 'media_path': '/a/pop.mp3'}}]}
        mark = marker_text(2, review_ranges(record, 100, 300)[0])
        self.assertEqual(mark['customData'], 'fx_review_2')
        self.assertEqual(mark['color'], 'Cyan')
        for word in ('重点大字', '规格小字', '照片标签', '应用名', '音效'):
            self.assertIn(word, mark['name'])
        for word in ('挡脸/手机', '两行是否对齐', '是否对称', '压到手机', '盖过人声', 'pop.mp3'):
            self.assertIn(word, mark['note'])


class ReviewExecutionTests(unittest.TestCase):
    def setup_fake(self):
        # A source object with NO edit interfaces: any accidental edit raises immediately.
        source = Mock(spec=['GetStartFrame', 'GetEndFrame', 'GetSetting', 'GetName', 'GetUniqueId',
                            'GetStartTimecode', 'GetMediaPoolItem', 'GetTrackCount', 'GetItemListInTrack', 'GetIsTrackEnabled'])
        source.GetTrackCount.return_value=0
        source.GetStartFrame.return_value = 100
        source.GetEndFrame.return_value = 300
        source.GetName.return_value = 'main'
        source.GetUniqueId.return_value = 'main-id'
        source.GetStartTimecode.return_value = '00:00:03:10'
        settings = {'timelineFrameRate': 30, 'timelineResolutionWidth': '1920', 'timelineResolutionHeight': '1080',
                    'timelineDropFrameTimecode': '0'}
        source.GetSetting.side_effect = lambda k: settings.get(k)
        source.GetMediaPoolItem.return_value.GetUniqueId.return_value = 'source-media'
        target = Mock()
        names={1:'Audio 1'}
        target.SetTrackName.side_effect=lambda kind,n,name:names.update({n:name}) or True
        target.GetTrackName.side_effect=lambda kind,n:names[n]
        target.AddTrack.side_effect=lambda *args:names.update({2:'Audio 2'}) or True
        target.GetUniqueId.return_value = 'new-id'
        target.GetStartTimecode.return_value = source.GetStartTimecode()
        target.GetStartFrame.return_value = 100
        target.GetSetting.side_effect = lambda k: settings.get(k)
        markers, clips = {}, {'video': [], 'audio': []}
        target.GetMarkers.side_effect = lambda: copy.deepcopy(markers)
        def marker(frame, color, name, note, duration, data):
            markers[frame] = dict(color=color, name=name, note=note, duration=duration, customData=data)
            return True
        target.AddMarker.side_effect = marker
        target.GetTrackCount.side_effect = lambda kind:len(names) if kind=='audio' else 1
        target.GetItemListInTrack.side_effect = lambda kind, index: clips[kind] if index==1 else []
        target.GetEndFrame.side_effect = lambda: max(i.GetEnd() for i in clips['video'])
        target.GetMediaPoolItem.return_value.GetUniqueId.return_value = 'new-media'
        project = Mock()
        project.GetUniqueId.return_value = 'pid'
        project.GetTimelineCount.side_effect = lambda: 1 + int(bool(clips['video']))
        project.GetTimelineByIndex.side_effect = lambda i: source if i == 1 else target
        project.GetCurrentTimeline.return_value = source
        mp = project.GetMediaPool.return_value
        mp.CreateEmptyTimeline.return_value = target
        def append(rows):
            self.assertIs(project.GetCurrentTimeline(), target)
            row = rows[0]
            if row['mediaType']==1:self.assertEqual(row['mediaPoolItem'], source.GetMediaPoolItem())
            else:self.assertEqual(row['mediaPoolItem'].GetUniqueId(),'clean-media')
            item = Mock()
            item.GetUniqueId.return_value = str(len(clips['video']))+'-'+str(row['mediaType'])
            item.GetStart.return_value = row['recordFrame']
            item.GetEnd.return_value = row['recordFrame']+row['endFrame']-row['startFrame']
            item.GetDuration.return_value = row['endFrame']-row['startFrame']
            item.GetSourceStartFrame.return_value = row['startFrame']
            item.GetSourceEndFrame.return_value = row['endFrame']
            item.GetLeftOffset.return_value = row['startFrame']
            item.GetMediaPoolItem.return_value = row['mediaPoolItem']
            clips['video' if row['mediaType'] == 1 else 'audio'].append(item)
            return [item]
        mp.AppendToTimeline.side_effect = append
        io = Mock()
        io.p, io.main, io.db = project, source, {'DbType': 'Disk', 'DbName': 'Local Database'}
        io.write.side_effect = lambda fn, *args: fn(*args)
        io.activate.side_effect = lambda t: setattr(project.GetCurrentTimeline, 'return_value', t)
        io.folder.return_value.GetClipList.return_value = [target.GetMediaPoolItem()]
        baseline = {'timeline': {'name': 'main', 'id': 'main-id', 'start': 100, 'end': 300,
                               'items': [], 'tracks': [], 'markers': {}},
                    'timelines': [{'name': 'main', 'id': 'main-id'}]}
        def snapshot():
            result = copy.deepcopy(baseline)
            if clips['video']: result['timelines'].append({'name': 'new', 'id': 'new-id'})
            if clips['video']: result['timelines'].append({'name': 'clean', 'id': 'clean-id'})
            return result
        io.snapshot.side_effect = snapshot
        record = {'project_id': 'pid', 'database': io.db, 'timeline': 'main', 'timeline_id': 'main-id',
                  'placement_mode': 'direct', 'effects': [effect('first', 110, 130), effect('last', 260, 270)]}
        return source, project, target, mp, io, record

    def test_missing_ledger_stops_before_resolve(self):
        with tempfile.TemporaryDirectory() as d, patch('engine.fx.review.ResolveIO') as io:
            with self.assertRaisesRegex(ValueError, '没有操作记录'):
                review(None, None, None, 'p', d)
            io.assert_not_called()

    def test_creation_separate_video_audio_offsets_gaps_markers_and_no_source_edits(self):
        source, project, target, mp, io, record = self.setup_fake()
        def capture(r, p, t, frames, directory):
            self.assertIs(t, source)
            return {'stills': [{'path': '/fake.png', 'frame': f} for f in frames]}
        clean=Mock();clean.GetMediaPoolItem.return_value.GetUniqueId.return_value='clean-media'
        proof={'id':'clean-id','media_id':'clean-media','name':'clean'}
        with tempfile.TemporaryDirectory() as d, patch('engine.fx.review.ledger.load', return_value=record), \
                patch('engine.fx.review.ResolveIO', return_value=io), \
                patch('engine.fx.review.clean_audio_copy',return_value=(clean,proof)), \
                patch('engine.fx.cleanup.register'), \
                patch('engine.fx.review.still', side_effect=capture), patch('engine.fx.review.run_ffmpeg'):
            result = review(Mock(), project, source, 'project', d, pad=10, apply=True)
            self.assertTrue(result['source_unchanged'])
            self.assertAlmostEqual(result['duration_seconds'], 90/30)
            self.assertEqual([(s['review_start'], s['review_end']) for s in result['segments']], [(100, 140), (160, 190)])
            calls = [c.args[0][0] for c in mp.AppendToTimeline.call_args_list]
            self.assertEqual([c['mediaType'] for c in calls], [1, 2, 1, 2])
            self.assertEqual([c['startFrame'] for c in calls], [0, 0, 150, 150])
            self.assertEqual([c['endFrame'] for c in calls], [40, 40, 180, 180])
            self.assertEqual(len(result['readback']), 4)
            self.assertIn('颜色以原时间线为准', Path(result['checklist']).read_text())
            target.AddMarker.assert_called()
            io.pm.SaveProject.assert_called_once()
            self.assertIs(project.GetCurrentTimeline(), source)

    def test_default_is_dry_run_and_creates_nothing(self):
        # B4：以前 review 一运行就建时间线、建媒体池文件夹、保存工程，没有演练
        source, project, target, mp, io, record = self.setup_fake()
        with tempfile.TemporaryDirectory() as d, patch('engine.fx.review.ledger.load', return_value=record), \
                patch('engine.fx.review.ResolveIO', return_value=io), patch('engine.fx.review.clean_audio_copy') as copy_audio, \
                patch('engine.fx.cleanup.register') as register, patch('engine.fx.review.still') as capture:
            result = review(Mock(), project, source, 'project', d, pad=10)
            self.assertFalse(result['applied'])
            name = result['would_create_timelines'][0]
            self.assertTrue(name.startswith('审片_main_'));self.assertEqual(result['would_create_timelines'][1], name+'_无音效源')
            self.assertEqual([(s['start'], s['end'], s['review_start'], s['review_end']) for s in result['segments']],
                             [(100, 140, 100, 140), (250, 280, 160, 190)])
            self.assertEqual(result['segments'][0]['content'], ['first'])
            self.assertIn('--apply', result['message'])
            self.assertEqual(list(Path(d).iterdir()), [])                       # 工作目录里什么都没写
            for fn in (mp.CreateEmptyTimeline, mp.AppendToTimeline, mp.SetCurrentFolder, io.write, io.snapshot, io.activate, io.folder,
                       io.pm.SaveProject, copy_audio, register, capture, target.AddMarker):
                fn.assert_not_called()

    def test_unfinished_batch_is_refused(self):
        source, project, target, mp, io, record = self.setup_fake(); record['pending'] = {'status': 'failed'}
        with patch('engine.fx.review.ledger.load', return_value=record), patch('engine.fx.review.ResolveIO', return_value=io):
            for apply in (False, True):
                with self.assertRaisesRegex(ValueError, '还没完成'): review(Mock(), project, source, 'p', '/unused', apply=apply)
        mp.CreateEmptyTimeline.assert_not_called()

    def test_wrong_ledger_identity_stops_before_snapshot_or_creation(self):
        source, project, target, mp, io, record = self.setup_fake()
        record['timeline_id'] = 'other'
        with patch('engine.fx.review.ledger.load', return_value=record), patch('engine.fx.review.ResolveIO', return_value=io):
            with self.assertRaises(ValueError): review(Mock(), project, source, 'p', '/unused')
        io.snapshot.assert_not_called()
        mp.CreateEmptyTimeline.assert_not_called()

    def test_readback_rejects_audio_missing_and_wrong_markers(self):
        t = Mock(); t.GetTrackCount.return_value = 1
        t.GetItemListInTrack.return_value = []
        with self.assertRaisesRegex(RuntimeError, '段数不符'):
            read_segments(t, [{'start': 100, 'end': 120}], 'm')
        t.GetMarkers.return_value = {0: {'note': 'wrong'}}
        with self.assertRaisesRegex(RuntimeError, '标记'):
            read_segments(t, [], 'm')

    def test_nested_source_frame_rounding_uses_exact_offset(self):
        segment = {'start': 875, 'end': 959, 'source_offset': 775, 'review_start': 100, 'review_end': 184,
                   'effects': [{'content': '字', 'category': '重点大字', 'hint': '看大小'}]}
        item = Mock(); item.GetStart.return_value = 100; item.GetEnd.return_value = 184
        item.GetUniqueId.return_value = 'clip'; item.GetDuration.return_value = 84
        item.GetLeftOffset.return_value = 775; item.GetSourceStartFrame.return_value = 774
        item.GetSourceEndFrame.return_value = 858; item.GetMediaPoolItem.return_value.GetUniqueId.return_value = 'media'
        t = Mock(); t.GetTrackCount.return_value = 1; t.GetItemListInTrack.return_value = [item]
        t.GetStartFrame.return_value = 100; t.GetMarkers.return_value = {0: marker_text(1, segment)}
        rows = read_segments(t, [segment], 'media')
        self.assertEqual((rows[0]['source_start'], rows[0]['source_end']), (775, 859))
        self.assertEqual(rows[0]['api_source_start'], 774)
        item.GetLeftOffset.return_value = 774
        with self.assertRaisesRegex(RuntimeError, '来源读回不符'):
            read_segments(t, [segment], 'media')


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Prevent the CLI's UTF-8 re-exec when imported by offline unittest.
        spec = importlib.util.spec_from_file_location('fx_cli_review_test', ROOT/'bin/fx.py')
        cls.cli = importlib.util.module_from_spec(spec)
        with patch('os.execvpe'): spec.loader.exec_module(cls.cli)

    def test_review_is_opt_in_and_never_automatic_for_small_repairs(self):
        argv = ['fx.py', 'build', '--project', 'p', '--timeline', 'main', '--work-dir', '/tmp/w', '--plan', '/tmp/plan', '--apply']
        for extra, applied, mode, count in (([], True, 'batch', 0), (['--no-review'], True, 'batch', 0), (['--review'], True, 'batch', 1), (['--review'], False, 'batch', 0), (['--review'], True, 'small', 0)):
            with patch.object(sys, 'argv', argv+extra), patch.object(self.cli, 'connect', return_value=(Mock(), Mock(), Mock())), \
                    patch.object(self.cli, 'run', return_value={'applied': applied,'mode':mode}), \
                    patch.object(self.cli, 'review', return_value={'timeline': '审片', 'checklist': '/tmp/list.md'}) as fn, patch('builtins.print'):
                self.assertEqual(self.cli.main(), 0)
                self.assertEqual(fn.call_count, count)
                if count: self.assertIs(fn.call_args.kwargs['apply'], True)     # build --review 是用户已明确要求审片：直接真建

    def test_review_cli_forwards_frame_options(self):
        argv = ['fx.py', 'review', '--project', 'p', '--timeline', 'main', '--work-dir', '/tmp/w', '--pad', '8', '--merge-gap', '12']
        r, p, t = Mock(), Mock(), Mock()
        for extra, apply in (([], False), (['--apply'], True)):                  # 默认只演练，--apply 才真建
            with patch.object(sys, 'argv', argv+extra), patch.object(self.cli, 'connect', return_value=(r, p, t)), \
                    patch.object(self.cli, 'review', return_value={}) as fn, patch('builtins.print'):
                self.assertEqual(self.cli.main(), 0)
                fn.assert_called_once_with(r, p, t, 'p', '/tmp/w', 8, 12, apply)


if __name__ == '__main__': unittest.main()
