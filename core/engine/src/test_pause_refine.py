import unittest
from pause_refine import refine_state, should_refine
from review_build import active_map


def token(ident, unit, left, right):
    return {'id': ident, 'text': ident, 'unit_id': unit, 'start_ms': left * 1001 / 30, 'end_ms': right * 1001 / 30}


class PauseRefineTests(unittest.TestCase):
    def setUp(self):
        self.total = 200
        self.aligned = {'tokens': [token('a', 0, 10, 20), token('b', 0, 22, 30), token('c', 1, 60, 70),
                                   token('d', 2, 73, 80), token('e', 3, 100, 110), token('f', 4, 140, 150)]}
        self.cards = [{'id': 'take', 'default': 'apply', 'cut_frames': [100, 110]}]
        self.levels = [-90.0] * self.total
        for a, b in ((10, 20), (22, 30), (60, 70), (73, 80), (100, 110), (140, 150)):
            for f in range(a, b): self.levels[f] = -30.0
        for f in (30, 31, 32): self.levels[f] = -50.0   # word tail after the aligned end
        self.state = {'natural_pause_mode': 'sentence'}

    def refine(self):
        return refine_state(self.cards, self.state, self.aligned, self.total, self.levels)

    def test_pauses_keep_chosen_rhythm_and_follow_audio(self):
        state, _ = self.refine()
        cuts = [row['source_frames'] for row in state['resolve_frame_cuts']]
        # Long pause between sentences keeps 4 frames after the measured tail (33), not the word time (30). (2026-11-01 收紧：原 6 帧)
        self.assertIn([35, 58], cuts)
        # Beside the deleted take both sides together keep 4 frames.
        self.assertIn([82, 100], cuts); self.assertIn([110, 138], cuts)
        # The 3-frame pause between c and d is left alone.
        self.assertFalse(any(a < 73 and b > 70 for a, b in cuts))

    def test_every_retained_word_survives(self):
        state, _ = self.refine()
        mapping = active_map(self.cards, state, self.aligned, self.total)
        self.assertEqual(mapping['retained_token_ids'], ['a', 'b', 'c', 'd', 'f'])
        kept = [(s['src_in'], s['src_out']) for s in mapping['segments']]
        for a, b in ((10, 20), (22, 33), (60, 70), (73, 80), (140, 150)):
            self.assertTrue(any(x <= a and b <= y for x, y in kept), (a, b, kept))
        self.assertFalse(any(x < 110 and y > 100 for x, y in kept))

    def test_refuses_other_trims_and_skips_when_done(self):
        with self.assertRaises(SystemExit):
            should_refine({'resolve_frame_cuts': [{'source_frames': [1, 2]}]})
        # 2026-11-13（Q060）：网页改过删句不再拒绝，而是只改受影响的接缝
        self.assertTrue(should_refine({'pause_refine': {'version': 'x'}, 'editor': {'operations': [{'action': 'cut', 'ranges': [[1, 2]]}]}}))
        self.assertFalse(should_refine({'pause_refine': {'version': 'x'}, 'resolve_frame_cuts': [{'source_frames': [1, 2]}]}))
        self.assertTrue(should_refine({'pause_refine': {'version': 'x'}}, force=True))
        self.assertTrue(should_refine({}))

class RecomputeAfterWebEdit(unittest.TestCase):
    """2026-11-13 台账 Q060：气口精修后在网页删句，接缝不能留被删句子的字头字尾。"""
    def setUp(self):
        self.total = 200
        self.aligned = {'tokens': [token('a', 0, 10, 20), token('b', 1, 60, 70), token('c', 2, 110, 120)]}
        self.levels = [-90.0] * self.total
        for a, b in ((10, 20), (60, 70), (110, 120)):
            for f in range(a - 3, b + 3): self.levels[f] = -30.0      # 每个字前后各有 3 帧字头字尾（对齐时间之外的声音）
        self.state, _ = refine_state([], {'natural_pause_mode': 'sentence'}, self.aligned, self.total, self.levels)

    def kept(self, state):
        return [(s['src_in'], s['src_out']) for s in active_map([], state, self.aligned, self.total)['segments']]

    def test_deleting_a_sentence_leaves_fragments_until_recomputed(self):
        import pause_refine as pr
        from review_build import fragment_problem
        self.assertIsNone(fragment_problem(active_map([], self.state, self.aligned, self.total), self.aligned, self.total))
        edited = dict(self.state, editor={'operations': [{'action': 'cut', 'ranges': [[60, 70]]}], 'cursor': 1})
        stale = active_map([], edited, self.aligned, self.total)
        self.assertTrue(any(55 <= a and b <= 75 for a, b in [(s['src_in'], s['src_out']) for s in stale['segments']]))   # b 的字头字尾还在
        self.assertIn('碎片', fragment_problem(stale, self.aligned, self.total))                                       # 交付前拦
        fixed, _ = pr.recompute([], self.state, edited, self.aligned, self.total, self.levels)
        kept = self.kept(fixed)
        self.assertFalse(any(a < 75 and b > 55 for a, b in kept), kept)                                                # b 的声音一帧不剩
        self.assertIsNone(fragment_problem(active_map([], fixed, self.aligned, self.total), self.aligned, self.total))
        gap = [b for a, b in kept if b <= 55][-1], [a for a, b in kept if a >= 75][0]
        self.assertLessEqual((gap[0] - 23) + (107 - gap[1]), pr.PAUSE_KEEP)                                            # a 尾到 c 头之间总共最多留 4 帧

    def test_manual_cuts_survive_recompute_and_force(self):
        import pause_refine as pr
        manual = {'source_frames': [24, 26], 'reason': '人工：句尾气口'}
        state = dict(self.state, resolve_frame_cuts=self.state['resolve_frame_cuts'] + [manual])
        with self.assertRaises(SystemExit): pr.should_refine(state, force=True)                    # 整片重算会冲掉人工精修：不做
        state['resolve_frame_cuts'] = [c for c in state['resolve_frame_cuts'] if c['source_frames'] != [75, 105]]   # 人工还拿掉了一处自动裁剪（b、c 之间想留停顿）
        edited = dict(state, editor={'operations': [{'action': 'cut', 'ranges': [[10, 20]]}], 'cursor': 1})          # 之后网页删掉第一句 a
        again, delta = pr.recompute([], pr.without_web_edits(edited), edited, self.aligned, self.total, self.levels)
        self.assertIn(manual, again['resolve_frame_cuts'])                                         # 人工加的还在
        self.assertFalse(any(c['source_frames'] == [75, 105] for c in again['resolve_frame_cuts']))  # 人工拿掉的没被加回来
        self.assertTrue(all(c[1] <= 60 for c in delta['去掉'] + delta['新加']), delta)               # 只动了 a 周围的接缝

    def test_levels_cache_and_after_edit(self):
        import pause_refine as pr, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            src = Path(t) / 'a.mov'; src.write_bytes(b'x')
            edited = dict(self.state, editor={'operations': [{'action': 'cut', 'ranges': [[60, 70]]}], 'cursor': 1})
            self.assertIs(pr.after_edit(t, [], self.state, edited, self.aligned, self.total, src), edited)          # 没有存下来的音量：原样返回
            pr.save_levels(t, self.levels, src)
            self.assertEqual(pr.load_levels(t, src, self.total), self.levels)
            fixed = pr.after_edit(t, [], self.state, edited, self.aligned, self.total, src)
            self.assertFalse(any(a < 75 and b > 55 for a, b in self.kept(fixed)))
            src.write_bytes(b'xy'); self.assertIsNone(pr.load_levels(t, src, self.total))               # 口播源换过：旧音量不用

    def test_missing_ffmpeg_is_reported_in_chinese(self):
        import pause_refine as pr
        from unittest.mock import patch
        with patch.object(pr.shutil, 'which', return_value=None), patch.object(pr.subprocess, 'run') as process, \
             self.assertRaisesRegex(RuntimeError, '找不到 ffmpeg.*setup_mac.sh'):
            pr.frame_levels('原片.mov', 10)
        process.assert_not_called()


class SoftTailAndCapTests(unittest.TestCase):
    """2026-11-27 261114 反馈单 新5：轻声字尾对齐时间整段落在静音里，贴回前一个字的尾音；两字之间最后留下的最多 7 帧。"""
    def test_silent_phrase_final_word_snaps_to_previous_tail_and_pause_is_trimmed(self):
        from pause_refine import snap_quiet_words, MAX_KEPT
        total = 200; levels = [-90.0] * total
        for f in range(10, 22): levels[f] = -30.0           # “拆”：10–20 帧，尾音到 22
        for f in range(60, 70): levels[f] = -30.0           # 下一句
        aligned = {'tokens': [token('拆', 0, 10, 20), token('了', 0, 36, 40), token('那', 1, 60, 70)]}
        aligned['tokens'][1].update(char_start=1, char_end=2)
        texts = {0: '拆了。', 1: '那'}
        state = {'natural_pause_mode': 'sentence'}
        new, moved = snap_quiet_words([], state, aligned, total, levels, texts=texts)
        self.assertEqual([m['字'] for m in moved], ['了'])
        self.assertEqual(round(new['tokens'][1]['start_ms'] * 30 / 1001), 20)        # 从“拆”的结尾起，贴着尾音
        self.assertEqual(round(new['tokens'][1]['end_ms'] * 30 / 1001), 22)
        self.assertEqual(aligned['tokens'][1]['start_ms'], 36 * 1001 / 30)          # 原对象不动
        cut_state, _ = refine_state([], state, new, total, levels)
        mapping = active_map([], cut_state, new, total)                              # 不再被“删掉整个字”拦下
        kept = 60 - 22 - sum(min(b, 60) - max(a, 22) for a, b in (c['source_frames'] for c in cut_state['resolve_frame_cuts']) if a < 60 and b > 22)
        self.assertLessEqual(kept, MAX_KEPT); self.assertTrue(mapping['segments'])
        # 没贴的话：静音里夹着“了”，两段停顿各自收，中间留下的远超 7 帧
        old_state, _ = refine_state([], state, aligned, total, levels)
        self.assertGreater(sum(1 for c in old_state['resolve_frame_cuts']), 0)

    def test_word_in_the_middle_of_a_phrase_is_not_moved(self):
        from pause_refine import snap_quiet_words
        total = 200; levels = [-90.0] * total
        for f in range(10, 20): levels[f] = -30.0
        for f in range(40, 50): levels[f] = -30.0
        aligned = {'tokens': [token('计', 0, 10, 20), token('一', 0, 30, 33), token('个', 0, 40, 50)]}
        aligned['tokens'][1].update(char_start=3, char_end=4)
        _, moved = snap_quiet_words([], {'natural_pause_mode': 'sentence'}, aligned, total, levels, texts={0: '设计，一个'})
        self.assertEqual(moved, [])

    def test_long_protected_tails_are_capped_at_seven_frames(self):
        from pause_refine import MAX_KEPT
        total = 200; levels = [-90.0] * total
        for f in range(10, 24): levels[f] = -30.0           # 字 10–20，尾音（换气）一直响到 24
        for f in range(56, 70): levels[f] = -30.0           # 下一句字头前 4 帧有声
        aligned = {'tokens': [token('多', 0, 10, 20), token('外', 1, 60, 70)]}
        state, _ = refine_state([], {'natural_pause_mode': 'sentence'}, aligned, total, levels)
        cut = state['resolve_frame_cuts'][0]['source_frames']
        self.assertEqual((cut[0] - 20) + (60 - cut[1]), MAX_KEPT)                   # 以前 4＋4＋4＝12 帧（0.4 秒）
        self.assertGreaterEqual(cut[0] - 20, 3)                                       # 字尾先保 3 帧


class VadGate(unittest.TestCase):
    """2026-11-22 方法 C：音量过门限、而且语音检测也认为是人声，才算有声；算不出人声概率就照旧只看音量。"""
    def setUp(self):
        base = PauseRefineTests(); base.setUp()
        self.cards, self.aligned, self.total, self.state = base.cards, base.aligned, base.total, base.state
        self.clean = base.levels
        self.noisy = list(base.levels)
        for f in range(33, 60): self.noisy[f] = -60.0        # b、c 之间的停顿里底噪略过门限（门限 -66 dB）
        self.vad = [0.9 if v > -60.0 else 0.1 for v in self.noisy]   # 语音检测：字和字尾是人声，底噪不是

    def cuts(self, levels, vad=None):
        state, _ = refine_state(self.cards, self.state, self.aligned, self.total, levels, vad=vad)
        return state, [c['source_frames'] for c in state['resolve_frame_cuts']]

    def test_low_noise_pause_counts_as_silence_with_vad(self):
        _, plain = self.cuts(self.noisy)
        self.assertIn([34, 57], plain)                       # 只看音量：底噪被当成字尾字头；2026-11-27 起两字之间最多留 7 帧（以前 [36, 54] 留 12 帧）
        state, gated = self.cuts(self.noisy, self.vad)
        self.assertIn([35, 58], gated)                       # 加语音检测：和没有底噪时一样
        self.assertEqual(gated, self.cuts(self.clean)[1])
        self.assertEqual(state['pause_refine']['vad_min'], 0.5)
        self.assertEqual(state['pause_refine']['threshold_db'], self.cuts(self.noisy)[0]['pause_refine']['threshold_db'])   # 门限仍只按音量算

    def test_without_vad_result_is_unchanged(self):
        old = refine_state(self.cards, self.state, self.aligned, self.total, self.noisy)
        self.assertEqual(refine_state(self.cards, self.state, self.aligned, self.total, self.noisy, vad=None), old)
        self.assertNotIn('vad_min', old[0]['pause_refine'])
        self.assertEqual(self.cuts(self.clean)[1], [[35, 58], [82, 100], [110, 138]])
        # 语音检测处处说是人声时，和只看音量逐帧一样（它只会把底噪去掉，不会多认出声音）
        self.assertEqual(self.cuts(self.noisy, [1.0] * self.total)[1], self.cuts(self.noisy)[1])

    def fake_python(self, folder, body):
        import os, stat
        from pathlib import Path
        script = Path(folder) / 'python'
        script.write_text('#!/bin/bash\n' + body + '\n'); script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return {'AI_EDIT_ALIGN_PYTHON': str(script)}

    def test_vad_cache_hit_does_not_rerun(self):
        import pause_refine as pr, tempfile, os
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            src = Path(t) / 'a.mov'; src.write_bytes(b'x'); count = Path(t) / 'count'
            env = self.fake_python(t, f'echo x >> "{count}"; python3 -c "print([0.25] * {self.total})"')
            with patch.dict(os.environ, env):
                first = pr.vad_probs(t, src, self.total)
                second = pr.vad_probs(t, src, self.total)
            self.assertEqual(first, ([0.25] * self.total, None)); self.assertEqual(second, first)
            self.assertEqual(len(count.read_text().split()), 1)                          # 第二次用缓存，没再起子进程
            self.assertTrue((Path(t) / pr.VAD_NAME).exists())
            src.write_bytes(b'xy'); self.assertIsNone(pr.load_vad(t, src, self.total))   # 口播源变了：缓存不用

    def test_failure_falls_back_to_levels_with_notice(self):
        import pause_refine as pr, tempfile, os, io
        from pathlib import Path
        from unittest.mock import patch
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as t:
            src = Path(t) / 'a.mov'; src.write_bytes(b'x')
            for body, reason in (('echo 坏了 >&2; exit 1', '退出码 1'), ('exit 3', '没有装 silero-vad'), ('sleep 5', '超过')):
                out = io.StringIO()
                with patch.dict(os.environ, self.fake_python(t, body)), patch.object(pr, 'VAD_TIMEOUT', 1), redirect_stdout(out):
                    self.assertIsNone(pr.vad_or_warn(t, src, self.total))
                self.assertIn('这次没用上语音检测', out.getvalue()); self.assertIn(reason, out.getvalue())
            with patch.dict(os.environ, {'AI_EDIT_ALIGN_PYTHON': str(Path(t) / '没有这个')}):
                self.assertIn('找不到对齐环境', pr.vad_probs(t, src, self.total)[1])
            self.assertFalse((Path(t) / pr.VAD_NAME).exists())
            # 降级后的精修和以前只看音量的结果一样
            self.assertEqual(self.cuts(self.noisy, None)[1], [c['source_frames'] for c in refine_state(self.cards, self.state, self.aligned, self.total, self.noisy)[0]['resolve_frame_cuts']])

    def test_after_edit_uses_same_vad(self):
        import pause_refine as pr, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            src = Path(t) / 'a.mov'; src.write_bytes(b'x')
            refined, _ = refine_state([], self.state, self.aligned, self.total, self.noisy, vad=self.vad)
            edited = dict(refined, editor={'operations': [{'action': 'cut', 'ranges': [[60, 70]]}], 'cursor': 1})
            pr.save_levels(t, self.noisy, src)
            self.assertIs(pr.after_edit(t, [], refined, edited, self.aligned, self.total, src), edited)   # 精修用了语音检测、缓存没了：不动
            pr.save_levels(t, self.vad, src, pr.VAD_NAME, 'vad')
            fixed = pr.after_edit(t, [], refined, edited, self.aligned, self.total, src)
            want, _ = pr.recompute([], refined, edited, self.aligned, self.total, self.noisy, self.vad)
            self.assertEqual(fixed, want)
            self.assertNotIn('人工动过的自动裁剪', fixed['pause_refine'])                                  # 原来的自动裁剪都认得出来


if __name__ == '__main__':
    unittest.main()
