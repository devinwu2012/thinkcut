import unittest
from review_build import active_map
from tight_cut import selection_map


def token(ident, left, right):
    return {'id': ident, 'start_ms': left * 1001 / 30,
            'end_ms': right * 1001 / 30}


class NaturalPauseMapTests(unittest.TestCase):
    def setUp(self):
        self.aligned = {'tokens': [token('a', 10, 20), token('b', 25, 35),
                                   token('c', 45, 55)]}
        self.state = {'natural_pause_ranges': [{'source_frames': [0, 80]}]}

    def build(self, state=None, cards=None):
        return active_map(cards or [], self.state if state is None else state,
                          self.aligned, 80)

    def test_default_selection_does_not_change(self):
        self.assertEqual(self.build({}), selection_map(self.aligned['tokens'], [], 80))

    def test_sentence_stays_continuous_and_keeps_every_word(self):
        result = self.build()
        self.assertEqual(result['segments'], [{'src_in': 10, 'src_out': 55, 'tl_in': 0, 'tl_out': 45}])
        self.assertEqual(result['retained_token_ids'], ['a', 'b', 'c'])

    def test_explicit_deletion_is_not_bridged_or_padded(self):
        state = {**self.state, 'natural_pause_padding_frames': 3}
        result = self.build(state, [{'id': 'cut', 'default': 'apply', 'cut_frames': [37, 42]}])
        self.assertEqual([(s['src_in'], s['src_out']) for s in result['segments']], [(7, 35), (45, 58)])

    def test_pause_beside_deletion_is_opt_in_and_keeps_deletion_cut(self):
        cards = [{'id': 'cut', 'default': 'apply', 'cut_frames': [37, 42]}]
        result = self.build({**self.state, 'restore_pause_beside_deletions': True}, cards)
        self.assertEqual([(s['src_in'], s['src_out']) for s in result['segments']], [(10, 37), (42, 55)])
        self.assertEqual(result['retained_token_ids'], ['a', 'b', 'c'])
        with self.assertRaises(ValueError):
            self.build({**self.state, 'restore_pause_beside_deletions': 1}, cards)

    def test_padding_stays_inside_source_and_requested_range(self):
        result = self.build({**self.state, 'natural_pause_padding_frames': 3})
        self.assertEqual([(s['src_in'], s['src_out']) for s in result['segments']], [(7, 58)])

    def test_resolve_partial_word_trim_is_preserved(self):
        result = self.build({**self.state, 'resolve_frame_cuts': [{'source_frames': [30, 47]}]})
        self.assertEqual([(s['src_in'], s['src_out']) for s in result['segments']], [(10, 30), (47, 55)])
        self.assertEqual(result['retained_token_ids'], ['a', 'b', 'c'])

    def test_resolve_whole_word_removal_needs_explicit_text_selection(self):
        with self.assertRaisesRegex(ValueError, 'complete reviewed word'):
            self.build({**self.state, 'resolve_frame_cuts': [{'source_frames': [25, 35]}]})

    def test_resolve_pause_trim_preserves_exact_handles(self):
        result = self.build({**self.state, 'resolve_frame_cuts': [{'source_frames': [21, 23]}]})
        self.assertEqual([(s['src_in'], s['src_out']) for s in result['segments']], [(10, 21), (23, 55)])

    def test_resolve_edge_trim_does_not_discard_remaining_padding(self):
        result = self.build({**self.state, 'natural_pause_padding_frames': 3,
                             'resolve_frame_cuts': [{'source_frames': [7, 8]}, {'source_frames': [57, 58]}]})
        self.assertEqual([(s['src_in'], s['src_out']) for s in result['segments']], [(8, 57)])

    def test_sentence_mode_keeps_sentences_whole_and_cuts_between_them(self):
        aligned = {'tokens': [{**token('a', 10, 20), 'unit_id': 0}, {**token('b', 25, 35), 'unit_id': 0},
                              {**token('c', 45, 55), 'unit_id': 1}]}
        result = active_map([], {'natural_pause_mode': 'sentence'}, aligned, 80)
        self.assertEqual([(s['src_in'], s['src_out']) for s in result['segments']], [(10, 35), (45, 55)])
        self.assertEqual(result['retained_token_ids'], ['a', 'b', 'c'])

    def test_sentence_mode_never_bridges_a_deleted_retake(self):
        aligned = {'tokens': [{**token('a', 10, 20), 'unit_id': 0}, {**token('x', 22, 30), 'unit_id': 0},
                              {**token('b', 32, 40), 'unit_id': 0}]}
        result = active_map([{'id': 'cut', 'default': 'apply', 'cut_frames': [22, 30]}],
                            {'natural_pause_mode': 'sentence'}, aligned, 80)
        self.assertEqual([(s['src_in'], s['src_out']) for s in result['segments']], [(10, 20), (32, 40)])

    def test_sub_frame_word_is_not_mistaken_as_removed_by_resolve_trim(self):
        aligned = {'tokens': [{**token('a', 10, 12), 'unit_id': 0},
                              {'id': 'b', 'unit_id': 0, 'start_ms': 1010, 'end_ms': 1020},
                              {**token('c', 45, 51), 'unit_id': 0}]}
        result = active_map([], {'natural_pause_mode': 'sentence', 'resolve_frame_cuts': [{'source_frames': [60, 62]}]}, aligned, 80)
        self.assertEqual(result['retained_token_ids'], ['a', 'b', 'c'])

    def test_invalid_configuration_stops(self):
        for key, value in [('natural_pause_padding_frames', -1),
                           ('natural_pause_ranges', [{'source_frames': [40, 20]}]),
                           ('resolve_frame_cuts', [{'source_frames': [1, 81]}]),
                           ('natural_pause_mode', 'word')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.build({**self.state, key: value})


if __name__ == '__main__':
    unittest.main()
