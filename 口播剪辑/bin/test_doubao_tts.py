# -*- coding: utf-8 -*-
"""机器配音占位（doubao_tts.py）的离线测试：豆包接口全用假回应，不真调、不花钱。"""
import argparse
import base64
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import doubao_tts as tts
import transcribe_video

KEY = 'test-only-key-0123456789abcdef'
FFMPEG = shutil.which('ffmpeg')


def tone(seconds=1.0):
    """一小段真的 mp3（合成结果的替身）。"""
    out = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi', '-i', f'sine=frequency=300:duration={seconds}',
                          '-ar', '48000', '-ac', '1', '-f', 'mp3', '-'], capture_output=True, check=True)
    return out.stdout


class Reply:
    def __init__(self, rows, headers=None):
        self.rows = [(json.dumps(r, ensure_ascii=False) + '\n').encode('utf-8') if isinstance(r, dict) else r for r in rows]
        self.headers = headers or {'X-Tt-Logid': 'LOG1'}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self.rows)


def good(text, mp3, step=0.2):
    """照豆包真实回应的样子：几行音频、一行整句（words 为空）、一行带每个字时间的句子、最后一行 20000000。"""
    words = []; t = 0.0
    for piece in __import__('re').findall(r'[A-Za-z0-9]+[，。？！：；、]?|[^\s][，。？！：；、]?', text):
        words.append({'word': piece, 'startTime': t, 'endTime': t + step, 'confidence': 0.9}); t += step
    half = len(mp3) // 2
    return [{'code': 0, 'message': '', 'data': base64.b64encode(mp3[:half]).decode()},
            {'code': 0, 'message': '', 'data': base64.b64encode(mp3[half:]).decode()},
            {'code': 0, 'message': '', 'data': None, 'sentence': {'text': text, 'words': []}},
            {'code': 0, 'message': '', 'data': None, 'sentence': {'text': text, 'words': words}},
            {'code': 20000000, 'message': 'OK', 'data': None, 'usage': {'text_words': len(text)}}]


class SplitTest(unittest.TestCase):
    def test_headings_notes_and_rules_are_not_spoken(self):
        rows = tts.clean_script('### 开场\n先看效果。\n\n> 备注：这里放实拍\n---\n第二段。\n')
        self.assertEqual(rows, ['先看效果。', '第二段。'])

    def test_one_chunk_per_paragraph_and_long_ones_split_at_sentence_ends(self):
        long = '这是一句话。' * 80           # 480 字
        chunks = tts.split_chunks(['短的一段。', long], limit=300)
        self.assertEqual(chunks[0], '短的一段。')
        self.assertTrue(all(len(c) <= 300 for c in chunks))
        self.assertTrue(all(c.endswith('。') for c in chunks))
        self.assertEqual(''.join(chunks[1:]), long)

    def test_sentence_without_any_punctuation_is_still_cut_under_limit(self):
        chunks = tts.split_chunks(['字' * 700], limit=300)
        self.assertEqual([len(c) for c in chunks], [300, 300, 100])


class RequestTest(unittest.TestCase):
    def test_audio_words_and_usage_are_collected(self):
        mp3 = b'ID3fake-audio-bytes'
        seen = {}
        def opener(request, timeout=0):
            seen['headers'] = dict(request.header_items()); seen['body'] = json.loads(request.data); return Reply(good('先看效果，好。', mp3))
        got = tts.request_once('先看效果，好。', KEY, tts.SPEAKER, opener=opener)
        self.assertEqual(got['audio'], mp3)
        self.assertEqual([w['word'] for w in got['words']], ['先', '看', '效', '果，', '好。'])
        self.assertEqual(got['usage'], 7)
        self.assertEqual(seen['headers']['X-api-resource-id'], 'seed-tts-2.0')
        self.assertEqual(seen['headers']['X-api-key'], KEY)
        self.assertTrue(seen['body']['req_params']['audio_params']['enable_subtitle'])
        self.assertEqual(seen['body']['req_params']['speaker'], 'zh_female_xiaohe_uranus_bigtts')

    def test_transient_failures_are_retried_then_succeed(self):
        calls = []; naps = []; lines = []
        def opener(request, timeout=0):
            calls.append(1)
            if len(calls) == 1: raise URLError('connection reset')
            if len(calls) == 2: return Reply(good('好。', b'xx')[:2])      # 流中途断了：没有结束标记
            return Reply(good('好。', b'xx'))
        got = tts.synthesize('好。', KEY, tts.SPEAKER, sleep=naps.append, opener=opener, log=lines.append)
        self.assertEqual(len(calls), 3); self.assertEqual(len(naps), 2); self.assertEqual(got['audio'], b'xx')

    def test_gives_up_after_three_tries_and_says_it_may_have_been_billed(self):
        calls = []
        def opener(request, timeout=0): calls.append(1); raise HTTPError(tts.API, 503, 'busy', {}, io.BytesIO(b'busy'))
        with self.assertRaises(tts.Transient) as ctx:
            tts.synthesize('好。', KEY, tts.SPEAKER, sleep=lambda s: None, opener=opener, log=lambda s: None)
        self.assertEqual(len(calls), 3); self.assertIn('可能已计费', str(ctx.exception))

    def test_refusal_is_not_retried_and_never_shows_the_key(self):
        calls = []
        def opener(request, timeout=0):
            calls.append(1); raise HTTPError(tts.API, 403, 'no', {}, io.BytesIO(('requested resource not granted for ' + KEY).encode()))
        with self.assertRaises(tts.Refused) as ctx:
            tts.synthesize('好。', KEY, tts.SPEAKER, sleep=lambda s: None, opener=opener, log=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(KEY, str(ctx.exception)); self.assertIn('开通', str(ctx.exception))

    def test_error_code_in_stream_is_refused_without_key(self):
        def opener(request, timeout=0): return Reply([{'code': 40402003, 'message': 'text too long ' + KEY}])
        with self.assertRaises(tts.Refused) as ctx:
            tts.request_once('好。', KEY, tts.SPEAKER, opener=opener)
        self.assertIn('40402003', str(ctx.exception)); self.assertNotIn(KEY, str(ctx.exception))


class JoinTest(unittest.TestCase):
    def test_second_chunk_times_are_shifted_by_first_chunk_and_gap(self):
        a = b'\x00\x00' * tts.RATE            # 1 秒
        b = b'\x00\x00' * (tts.RATE // 2)     # 0.5 秒
        pcm, words, seconds = tts.join([(a, [{'word': '一。', 'start': 0.1, 'end': 0.4}]), (b, [{'word': '二。', 'start': 0.0, 'end': 0.3}])], gap=0.25)
        self.assertAlmostEqual(seconds, 1.75, places=3)
        self.assertAlmostEqual(words[1]['start'], 1.25, places=3); self.assertAlmostEqual(words[1]['end'], 1.55, places=3)
        self.assertEqual(len(pcm), (tts.RATE + tts.RATE // 4 + tts.RATE // 2) * 2)

    def test_asr_shape_is_accepted_by_the_transcription_checker(self):
        words = [{'word': '先', 'start': 0.0, 'end': 0.16, 'chunk': 0}, {'word': '看，', 'start': 0.16, 'end': 0.5, 'chunk': 0},
                 {'word': 'X500', 'start': 0.9, 'end': 0.93, 'chunk': 0}, {'word': '好？', 'start': 0.92, 'end': 1.2, 'chunk': 0},     # 和上一个重叠：要排开
                 {'word': '第', 'start': 1.6, 'end': 1.7, 'chunk': 1}, {'word': '二', 'start': 1.7, 'end': 1.7, 'chunk': 1}, {'word': '段', 'start': 1.71, 'end': 9.0, 'chunk': 1}]
        asr = tts.to_asr(words, 2.0)
        self.assertEqual(transcribe_video.count_timed_tokens(asr), 7)
        texts = [u['text'] for u in asr['result']['utterances']]
        self.assertEqual(texts, ['先看，X500好？', '第二段'])            # 句末标点、换段都断句；标点留在句子里
        self.assertEqual([w['text'] for w in asr['result']['utterances'][0]['words']], ['先', '看', 'X500', '好'])
        self.assertLessEqual(asr['result']['utterances'][-1]['words'][-1]['end_time'], 2000)


@unittest.skipUnless(FFMPEG, '要 ffmpeg')
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.root = self.tmp / '991231 试验'
        (self.root / '01_原始素材').mkdir(parents=True); (self.root / '02_制作').mkdir(); (self.root / '03_成片').mkdir()
        (self.root / '项目.json').write_text('{"name": "991231 试验"}')
        self.script = self.tmp / '文案.txt'; self.script.write_text('### 开场\n先看效果，好。\n\n> 备注\n第二段说完了。\n')
        self.mp3 = tone(0.8); self.calls = []; self.lines = []
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)

    def opener(self, request, timeout=0):
        text = json.loads(request.data)['req_params']['text']; self.calls.append(text); return Reply(good(text, self.mp3, step=0.1))

    def args(self, **kw):
        base = dict(speaker=tts.SPEAKER, rate=0, format='3:4', dry_run=False, force=False)
        base.update(kw); return argparse.Namespace(**base)

    def build(self, **kw):
        return tts.build(self.root, self.script.read_text(), '文案 文案.txt', self.args(**kw), key_loader=lambda: KEY, opener=self.opener, log=self.lines.append)

    def test_dry_run_reports_characters_and_never_loads_the_key(self):
        def boom(): raise AssertionError('dry-run 不该取钥匙')
        out = tts.build(self.root, self.script.read_text(), '文案', self.args(dry_run=True), key_loader=boom, opener=self.opener, log=self.lines.append)
        self.assertIsNone(out); self.assertEqual(self.calls, [])
        self.assertIn('共 14 字', self.lines[0]); self.assertIn('分 2 段', self.lines[0])

    def test_real_run_prints_characters_and_segments_before_synthesis(self):
        # 2026-11-23 初稿模式实测：不用先跑 --dry-run，正式合成也先把总字数和每段字数打印出来
        tts.build(self.root, self.script.read_text(), '文案', self.args(), key_loader=lambda: 'k' * 36, opener=self.opener, log=self.lines.append)
        first_done = next(i for i, l in enumerate(self.lines) if '已合成' in l or '用上次合成好的' in l)
        self.assertIn('共 14 字', self.lines[0]); self.assertTrue(all('预计第' in l for l in self.lines[1:3])); self.assertGreater(first_done, 2)
        self.assertTrue(any('--kind 试听' in l and '不用自己转 mp3' in l for l in self.lines))

    def test_build_makes_source_movie_and_placeholder_record(self):
        mov = self.build()
        self.assertEqual(self.calls, ['先看效果，好。', '第二段说完了。'])
        self.assertTrue(self.lines[0].startswith('机器配音：') and '14 字' in self.lines[0])      # 合成前先报字数
        info = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,r_frame_rate,width,height,start_time', '-of', 'json', str(mov)],
                              capture_output=True, text=True, check=True)
        streams = json.loads(info.stdout)['streams']
        video = next(s for s in streams if s['codec_type'] == 'video'); audio = [s for s in streams if s['codec_type'] == 'audio']
        self.assertEqual((video['r_frame_rate'], video['width'], video['height']), ('30000/1001', 2160, 3840))
        self.assertEqual(len(audio), 1); self.assertEqual(float(audio[0]['start_time']), 0.0); self.assertEqual(float(video['start_time']), 0.0)
        record = json.loads(tts.sidecar_of(mov).read_text())
        self.assertTrue(record['占位']); self.assertEqual(record['类型'], '机器配音'); self.assertEqual(record['音色名'], '小何')
        self.assertEqual(record['字数'], 14); self.assertEqual(record['这次新合成的计费字数'], 14)
        self.assertEqual(transcribe_video.count_timed_tokens(record['asr']), 11)
        second = record['asr']['result']['utterances'][1]
        self.assertGreater(second['start_time'], 800)                       # 第二段的字时间排在第一段和停顿之后
        for path in self.root.rglob('*'):                                   # 钥匙不落进任何产物
            if path.is_file(): self.assertNotIn(KEY.encode(), path.read_bytes(), path)
        self.assertFalse(any(KEY in line for line in self.lines))

    def test_rerun_reuses_everything_and_failed_chunk_only_redoes_the_rest(self):
        fail = {'on': True}
        def flaky(request, timeout=0):
            text = json.loads(request.data)['req_params']['text']
            if text.startswith('第二段') and fail['on']: raise HTTPError(tts.API, 401, 'no', {}, io.BytesIO(b'invalid key'))
            return self.opener(request)
        with self.assertRaises(SystemExit) as ctx:
            tts.build(self.root, self.script.read_text(), '文案', self.args(), key_loader=lambda: KEY, opener=flaky, log=self.lines.append)
        self.assertIn('第 2/2 段', str(ctx.exception)); self.assertNotIn(KEY, str(ctx.exception))
        self.assertFalse((self.root / '02_制作/口播转码/机器配音占位_口播.mov').exists())     # 没合成完不出半截口播源
        fail['on'] = False; self.calls.clear()
        mov = tts.build(self.root, self.script.read_text(), '文案', self.args(), key_loader=lambda: KEY, opener=flaky, log=self.lines.append)
        self.assertEqual(self.calls, ['第二段说完了。'])                       # 第一段用上次合成好的，不重复花钱
        self.assertEqual(json.loads(tts.sidecar_of(mov).read_text())['这次新合成的计费字数'], 7)
        self.calls.clear(); self.build()
        self.assertEqual(self.calls, [])                                    # 文案没变：整条直接用

    def test_transcription_uses_the_record_instead_of_calling_recognition(self):
        mov = self.build()
        parser = argparse.ArgumentParser()
        record = json.loads(tts.sidecar_of(mov).read_text())
        work = self.root / '02_制作' / '机器配音占位_口播_AI剪辑' / '转写'
        work.parent.mkdir(parents=True); work.mkdir(mode=0o700)
        preset = {'resource_id': 'tts.' + record['资源'], 'result': record['asr']}
        with patch.object(transcribe_video, 'load_key', side_effect=AssertionError('机器配音不该取识别的钥匙')), \
             patch.object(transcribe_video, 'recognize_any', side_effect=AssertionError('机器配音不该调识别')):
            transcribe_video.run(mov, work, parser, preset=preset)
            transcribe_video.run(mov, work, parser, preset=preset)          # 再跑一次：校验后复用
        manifest = json.loads((work / 'transcription_manifest.json').read_text())
        self.assertEqual(manifest['resource_id'], 'tts.seed-tts-2.0'); self.assertEqual(manifest['timed_tokens'], 11)
        self.assertFalse((work / 'asr_request.json').exists())
        self.assertFalse(json.loads((work / 'timings.json').read_text())['api_called'])


if __name__ == '__main__':
    unittest.main()
