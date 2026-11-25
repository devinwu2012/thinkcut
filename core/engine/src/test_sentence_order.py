# -*- coding: utf-8 -*-
"""网页里按小句挪位置（2026-11-13）：顺序、撤销、映射、字幕、交付身份。按字记顺序，网页一次拖一个小句。"""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import review_build as rb
from paragraph_editor import change, manual_changes

TOKENS = []
for u, base in enumerate([1000, 2000, 3000]):
    TOKENS += [{'id': f'u{u}c0', 'unit_id': u, 'char_start': 0, 'text': '甲乙丙'[u], 'start_ms': base, 'end_ms': base + 200, 'ready': True},
               {'id': f'u{u}c1', 'unit_id': u, 'char_start': 1, 'text': '子丑寅'[u], 'start_ms': base + 250, 'end_ms': base + 400, 'ready': True}]
ALIGNED = {'alignment_id': 'fixture', 'tokens': TOKENS}
UNITS = [{'id': u, 'text': '甲乙丙'[u] + '子丑寅'[u] + '。', 'start_ms': 1000 * (u + 1)} for u in range(3)]
MANIFEST = {'cards': []}
TOTAL = 150
PAUSES = {'natural_pause_ranges': [{'source_frames': [0, TOTAL]}]}


def unit(u):
    return [t['id'] for t in TOKENS if t['unit_id'] == u]


def mapping(state):
    return rb.active_map([], state, ALIGNED, TOTAL)


class SentenceOrderTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('paragraph_editor.alignment', return_value=ALIGNED); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch('paragraph_editor.TOTAL', TOTAL); patcher.start(); self.addCleanup(patcher.stop)

    def act(self, state, action, **extra):
        request = {'action': action, 'ids': [], 'alignment_id': 'fixture', 'revision': state.get('editor', {}).get('revision', 0), **extra}
        return change(state, request, MANIFEST)

    def test_move_undo_redo(self):
        s = self.act(copy.deepcopy(PAUSES), 'move', ids=unit(2), before=unit(0)[0])
        self.assertEqual(rb.word_order(s, TOKENS), unit(2) + unit(0) + unit(1))
        self.assertEqual(mapping(s)['retained_token_ids'], ['u2c0', 'u2c1', 'u0c0', 'u0c1', 'u1c0', 'u1c1'])
        undone = self.act(s, 'undo')
        self.assertEqual(mapping(undone), mapping(PAUSES))
        self.assertEqual(mapping(self.act(undone, 'redo')), mapping(s))

    def test_part_of_a_sentence_moves_on_its_own(self):
        s = self.act(copy.deepcopy(PAUSES), 'move', ids=['u0c1'], before='u2c0')
        m = mapping(s)
        self.assertEqual(m['retained_token_ids'], ['u0c0', 'u1c0', 'u1c1', 'u0c1', 'u2c0', 'u2c1'])
        self.assertEqual(m['frames'], mapping(PAUSES)['frames'])
        used = sorted((x['src_in'], x['src_out']) for x in m['segments'])
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(used, used[1:])))

    def test_first_version_sentence_moves_still_read(self):
        old = {**copy.deepcopy(PAUSES), 'editor': {'alignment_id': 'fixture', 'cursor': 1, 'revision': 1,
               'operations': [{'action': 'move', 'units': [2], 'before': 0, 'text': '丙寅'}]}}
        self.assertEqual(mapping(old)['retained_token_ids'], unit(2) + unit(0) + unit(1))

    def test_reordered_map_uses_every_kept_frame_once_and_splits_pauses_in_the_middle(self):
        before, after = mapping(PAUSES), mapping(self.act(copy.deepcopy(PAUSES), 'move', ids=unit(2), before=unit(0)[0]))
        self.assertEqual(before['segments'], [{'src_in': 30, 'src_out': 102, 'tl_in': 0, 'tl_out': 72}])
        self.assertEqual([(s['src_in'], s['src_out']) for s in after['segments']], [(81, 102), (30, 81)])
        self.assertEqual([(s['tl_in'], s['tl_out']) for s in after['segments']], [(0, 21), (21, 72)])
        self.assertEqual((after['cut_frames'], after['frames']), (before['cut_frames'], before['frames']))
        self.assertEqual(after['word_order'], unit(2) + unit(0) + unit(1))

    def test_moving_back_restores_the_unmoved_map_exactly(self):
        s = self.act(copy.deepcopy(PAUSES), 'move', ids=unit(0), before=None)
        s = self.act(s, 'move', ids=unit(0), before=unit(1)[0])
        self.assertEqual(mapping(s), mapping(PAUSES))
        self.assertNotIn('word_order', mapping(s))

    def test_deletions_still_apply_after_a_move(self):
        s = self.act({}, 'move', ids=unit(1), before=unit(0)[0])
        s = self.act(s, 'cut', ids=['u1c1'])
        m = mapping(s)
        self.assertEqual(m['retained_token_ids'], ['u1c0', 'u0c0', 'u0c1', 'u2c0', 'u2c1'])
        self.assertEqual(m['segments'][0]['src_in'], 60)

    def test_rejects_unchanged_position_and_unknown_units(self):
        with self.assertRaisesRegex(ValueError, '没有变化'):
            self.act({}, 'move', ids=unit(1), before=unit(2)[0])
        with self.assertRaises(ValueError):
            self.act({}, 'move', ids=['nope'], before=unit(0)[0])
        with self.assertRaisesRegex(ValueError, '连在一起'):
            self.act({}, 'move', ids=['u0c0', 'u1c0'], before=None)
        with self.assertRaises(ValueError):
            self.act({}, 'move', ids=unit(1), before=unit(1)[0])

    def test_repeat_detection_reads_a_history_with_moves(self):
        from local_repetitions import user_restored
        s = self.act({}, 'move', ids=unit(2), before=unit(0)[0])
        s = self.act(s, 'cut', ids=['u0c0'])
        s = self.act(s, 'restore', ids=['u0c0'])
        self.assertTrue(user_restored([TOKENS[0]], s))
        self.assertFalse(user_restored([TOKENS[4]], s))

    def test_default_paragraphs_follow_script_then_length(self):
        """2026-11-22 用户：“不能一整个自然段，应该……按照咱们写文案的时候是几个自然段”。先按定稿文案的自然段对，对不上、太长的按字数在句末拆；自动分一次存下来，之后不再自动分。"""
        import paragraph_editor
        texts = ['装修的时候你让施工队改个细节。', '他嘴上答应得好好的。', '全案设计里的灯光设计最怕这种情况。', '我们做了十几年设计。', '外地的朋友找我们单做灯光设计。', '我们都不接了。']
        units = [{'id': k, 'text': x} for k, x in enumerate(texts)]
        tokens = [{'id': f'u{k}c0', 'unit_id': k, 'char_start': 0, 'text': x[0]} for k, x in enumerate(texts)]
        script = ['装修的时候，你让施工队改个细节，他嘴上答应得好好的。', '全案设计里的灯光设计，最怕就是这种情况。我们做了十几年设计。', '这一段口播里没说。', '外地的朋友找我们单做灯光设计，我们都不接了。']
        self.assertEqual(paragraph_editor.default_breaks(units, tokens, script), ['u2c0', 'u4c0'])          # 文案第 2、4 段的开头对上了；第 3 段口播里没有，跳过
        self.assertEqual(paragraph_editor.default_breaks(units, tokens, script, [{'start': 2, 'title': '灯光'}]), ['u4c0'])   # 章节开头本来就另起一段，不重复记
        long = [{'id': k, 'text': '这是一句三十个字左右的口播用来凑够自动分段需要的字数啊。'} for k in range(20)]
        ltok = [{'id': f'u{k}c0', 'unit_id': k, 'char_start': 0, 'text': '这'} for k in range(20)]
        cuts = paragraph_editor.default_breaks(long, ltok)
        self.assertEqual(cuts, ['u6c0', 'u12c0', 'u18c0'])                                                 # 没有文案：攒够 150 字到句末就分
        with tempfile.TemporaryDirectory() as folder:
            with patch('paragraph_editor.RUN', Path(folder)), patch('paragraph_editor.script_paragraphs', return_value=script), patch('paragraph_editor.alignment', return_value={'tokens': tokens}):
                self.assertEqual(paragraph_editor.outline(units, tokens)['breaks'], ['u2c0', 'u4c0'])
                saved = json.loads((Path(folder) / '文稿分段.json').read_text(encoding='utf-8')); self.assertTrue(saved['auto'])
                paragraph_editor.set_break('u2c0', False); paragraph_editor.set_break('u4c0', False)       # 用户把段都合并了：不再自动分回去
                self.assertIsNone(paragraph_editor.outline(units, tokens))

    def test_layout_file_keeps_only_valid_entries_and_records_breaks(self):
        import paragraph_editor
        with tempfile.TemporaryDirectory() as folder:
            with patch('paragraph_editor.RUN', Path(folder)):
                self.assertIsNone(paragraph_editor.outline(UNITS, TOKENS))
                (Path(folder) / '文稿分段.json').write_text(json.dumps({'sections': [{'start': 1, 'title': ' 第一件 '}, {'start': 9, 'title': '没有这句'}],
                                                                       'breaks': ['u2c0', 'nope', 3]}), encoding='utf-8')
                self.assertEqual(paragraph_editor.outline(UNITS, TOKENS), {'sections': [{'start': 1, 'title': '第一件'}], 'breaks': ['u2c0']})
                paragraph_editor.set_break('u1c0', True); paragraph_editor.set_break('u2c0', False)
                self.assertEqual(paragraph_editor.outline(UNITS, TOKENS)['breaks'], ['u1c0'])
                self.assertEqual(paragraph_editor.outline(UNITS, TOKENS)['sections'][0]['title'], '第一件')
                with self.assertRaises(ValueError):
                    paragraph_editor.set_break('nope', True)

    def test_change_list_names_the_moved_sentence(self):
        s = self.act({}, 'move', ids=unit(2), before=unit(0)[0])
        groups = manual_changes(MANIFEST, s, ALIGNED, [])['manual_changes']
        self.assertEqual([(g['action'], g['text'], g['ids']) for g in groups], [('move', '丙寅', unit(2))])
        self.assertEqual(groups[0]['side'], 'after')
        s = self.act(s, 'move', ids=unit(1), before=unit(0)[0], side='before')
        self.assertEqual(manual_changes(MANIFEST, s, ALIGNED, [])['manual_changes'][-1]['side'], 'before')

    def test_delivery_identity_includes_order_only_when_moved(self):
        from prepare_mvp_delivery import delivery_signature
        plain = delivery_signature({}, {}, [[0, 30]], {'path': 'x'})
        self.assertEqual(delivery_signature({}, {}, [[0, 30]], {'path': 'x'}, None), plain)
        self.assertNotEqual(delivery_signature({}, {}, [[0, 30]], {'path': 'x'}, ['u2c0', 'u0c0']), plain)

    def test_exported_subtitles_follow_the_new_order(self):
        if not shutil.which('node'):
            self.skipTest('node missing')
        s = self.act(copy.deepcopy(PAUSES), 'move', ids=unit(2), before=unit(0)[0])
        m = mapping(s)
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            (d / 'character_alignment.json').write_text(json.dumps(ALIGNED))
            (d / 'sentence_units.json').write_text(json.dumps(UNITS))
            (d / 'review_v2_state.json').write_text(json.dumps(s))
            (d / 'trial_keep_segments.json').write_text(json.dumps(m))
            subprocess.run(['node', str(Path(__file__).with_name('export_approved_captions.cjs')), folder, 'out'], check=True, capture_output=True)
            cues = json.loads((d / 'subtitle_cues.json').read_text())['cues']
        self.assertEqual([c['ids'] for c in cues], [['u2c0', 'u2c1'], ['u0c0', 'u0c1'], ['u1c0', 'u1c1']])
        self.assertTrue(all(a['end_ms'] <= b['start_ms'] for a, b in zip(cues, cues[1:])))
        self.assertLess(cues[0]['start_ms'], 1000)   # 第 3 句挪到最前，字幕从成片开头附近开始


if __name__ == '__main__':
    unittest.main()
