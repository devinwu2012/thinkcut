# -*- coding: utf-8 -*-
"""Audio-checked cut points after the conservative first cut (2026-10-23).

Word times from alignment run tight, so cutting exactly on them clips word
onsets/tails. Keep the whole source continuous, then remove only the quiet
middle of each pause: pauses between sentences and beside a deleted take keep
at most PAUSE_KEEP frames (the user's chosen v4 rhythm), long silences inside
a sentence keep INTRA_KEEP frames. Word edges follow the measured audio level.
2026-11-22（用户试听 5 处新旧对比后定，方法 C）：一帧算“有声”要音量过门限，而且 Silero 语音检测也认为是人声
（人声概率 >= VAD_MIN）；底噪略过门限的停顿就能收掉。人声概率由对齐环境的 Python 算（vad_frames.py），
算不出来（没装 silero-vad、子进程失败或超时）就照旧只看音量，并打印一行提醒。
"""
import argparse
import array
import copy
import json
import math
import shutil
import subprocess
import sys

FPS = 30000 / 1001
# 2026-11-01《别墅地下室防水》用户嫌“气口不干净”，收紧后通过：字边 4、句间 4、句内 >4 帧收到 3（原 8/6/18/9，8 帧字边会把换气声当字尾留下）
EDGE_CAP = 4        # frames a word edge may extend into the pause
PAUSE_KEEP = 4      # between sentences / beside a deletion
INTRA_MAX = 4       # silence inside a sentence longer than this is shortened
INTRA_KEEP = 3
# 2026-11-27 261114 反馈单 新5：两个保留的字之间最后留下的帧（字尾保护＋停顿＋字头保护）最多 7 帧（0.23 秒）；
# 以前字尾、字头各能保护 4 帧再加 4 帧停顿，最多 12 帧，成片上 ≥0.27 秒的停顿 check 不过、要人工补剪。超了先保字尾 3、字头 2，再给停顿
MAX_KEPT = 7
TAIL_FIRST, HEAD_FIRST = 3, 2
VERSION = 'pause-refine-v2'
# 方法 C 的人声概率阈值（试验田《气口对比》2026-11-22：261118 上和只看音量一帧不差，《261110 水电》残留 >=0.27 秒停顿 11→1）
VAD_MIN = 0.5
VAD_TIMEOUT = 300   # 秒；实测 7 分半口播约 4 秒，给得很宽，卡住才会超


def frame_levels(source, total, audio_stream=0):
    """dBFS per video frame, read on the same clock Resolve plays (audio must start at 0)."""
    # Full band at 48 kHz: fricatives and breaths live above 8 kHz and read about 3 dB low at 16 kHz.
    rate = 48000
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('这台 Mac 找不到 ffmpeg（没装，或不在 PATH 里）：运行 bash bin/setup_mac.sh 装好后再试')
    raw = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(source),
                          '-map', f'0:a:{audio_stream}', '-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'],
                         capture_output=True, check=True).stdout
    samples = array.array('f'); samples.frombytes(raw[:len(raw) // 4 * 4])
    if sys.byteorder == 'big': samples.byteswap()
    per = rate / FPS; levels = []
    for i in range(total):
        part = samples[int(i * per):int((i + 1) * per)]
        power = sum(x * x for x in part) / len(part) if part else 0
        levels.append(10 * math.log10(power) if power else -120.0)
    return levels


def threshold(levels, spans):
    """Sound/silence level: just above the noise floor, well below speech."""
    floor = sorted(levels)[len(levels) // 10]
    speech = sorted(levels[f] for a, b in spans for f in range(a, b))
    speech = speech[len(speech) // 2] if speech else -30.0
    return min(max(-66.0, floor + 15), speech - 15)


def refine_state(cards, state, aligned, total, levels, th=None, vad=None):
    """Return (new_state, rows). Removes only quiet frames; never a retained word. th: reuse a recorded threshold.
    vad: 每帧人声概率；给了就要音量过门限、且人声概率 >= VAD_MIN 才算有声（门限仍只按音量算）；None 时和以前一样只看音量。"""
    from review_build import active_map, effective_cuts
    from tight_cut import word_frame_span
    base = copy.deepcopy(state)
    base.pop('resolve_frame_cuts', None)
    base['restore_pause_beside_deletions'] = True
    base['natural_pause_ranges'] = [{'source_frames': [0, total], 'reason': '整片保留原声，按实际音量只收短停顿'}]
    mapping = active_map(cards, base, aligned, total)
    deletions = effective_cuts(cards, base)
    keep = set(mapping['retained_token_ids'])
    words = sorted(((word_frame_span(t['start_ms'], t['end_ms'], total)[:2], t)
                    for t in aligned['tokens'] if t['id'] in keep), key=lambda w: w[0][0])
    if th is None: th = threshold(levels, [span for span, _ in words])
    loud = lambda f: 0 <= f < total and levels[f] > th and (vad is None or vad[f] >= VAD_MIN)

    def tail(b):
        k = 0
        while k < EDGE_CAP and loud(b + k): k += 1
        return b + k

    def head(a):
        k = 0
        while k < EDGE_CAP and loud(a - k - 1): k += 1
        return a - k

    def fit(b1, a2, tail_n, head_n, keep_n):
        """两字之间留 left（前字之后）、right（后字之前）帧，合起来不超过 MAX_KEPT。返回 (left, right)。"""
        t, h = min(tail_n, TAIL_FIRST), min(head_n, HEAD_FIRST)
        k = min(keep_n, max(0, MAX_KEPT - t - h))
        more = MAX_KEPT - t - h - k
        t2 = min(tail_n - t, more); t += t2; h += min(head_n - h, more - t2)
        return t + k // 2, h + (k - k // 2)

    cuts, rows = [], []
    for ((_, b1), t1), ((a2, _), t2) in zip(words, words[1:]):
        if a2 <= b1: continue
        x, y = tail(b1), head(a2)
        if y <= x: continue
        inside = [c for c in deletions if c[0] < a2 and c[1] > b1]
        if inside:
            c0, c1 = min(c[0] for c in inside), max(c[1] for c in inside)
            p = x
            while p < c0 and not loud(p): p += 1        # deleted take may start before its word time
            q = y
            while q > c1 and not loud(q - 1): q -= 1
            before, after = max(0, p - x), max(0, y - q)
            total_pause = before + after; keep_n = min(total_pause, PAUSE_KEEP)
            k2 = min(after, keep_n - (keep_n + 1) // 2); k1 = min(before, keep_n - k2); k2 = min(after, keep_n - k1)
            left, right = (x - b1) + k1, (a2 - y) + k2
            if left + right > MAX_KEPT: left, right = fit(b1, a2, x - b1, a2 - y, k1 + k2)
            cuts += [c for c in ([b1 + left, max(c0, b1 + left)], [min(c1, a2 - right), a2 - right]) if c[1] > c[0]]
            rows.append({'接口': '删除重录两侧', '前后原话': t1['text'] + '｜' + t2['text'], '原停顿帧': total_pause, '保留帧': left + right})
            continue
        pause = y - x; between = t1['unit_id'] != t2['unit_id']
        limit, keep_n = (PAUSE_KEEP, PAUSE_KEEP) if between else (INTRA_MAX, INTRA_KEEP)
        cut = [x + keep_n // 2, y - (keep_n - keep_n // 2)] if pause > limit else None
        kept = (a2 - b1) - (cut[1] - cut[0] if cut else 0)
        if kept > MAX_KEPT:
            left, right = fit(b1, a2, x - b1, a2 - y, min(keep_n, pause))
            cut = [b1 + left, a2 - right]
        if cut and cut[1] > cut[0]:
            cuts.append(cut)
            rows.append({'接口': '句间' if between else '句内', '前后原话': t1['text'] + '｜' + t2['text'],
                         '原停顿帧': pause, '保留帧': (a2 - b1) - (cut[1] - cut[0]), '删除源帧': cut})
    base['resolve_frame_cuts'] = [{'source_frames': c, 'reason': AUTO_REASON} for c in sorted(cuts)]
    base['pause_refine'] = {'version': VERSION, 'threshold_db': round(th, 1), 'cuts': len(cuts)}
    if vad is not None: base['pause_refine']['vad_min'] = VAD_MIN   # 记下用了语音检测：之后重算接缝也得用同一份人声概率
    return base, rows


AUTO_REASON = '按实际音量收短停顿'
PHRASE_END = set('，。？！、；：,.?!;:…—”’）) ')


def snap_quiet_words(cards, state, aligned, total, levels, vad=None, texts=None):
    """轻声字尾（“拆了”的“了”）对齐时间整段落在静音里：贴到前一个字的尾音上（2026-11-27 261114 反馈单 新5）。
    条件：是一个短句的最后一个字（后面是标点或句尾；texts＝{句编号: 原文}，没给就不限）；同一句里紧挨着前一个保留的字；这个字的帧一帧有声的都没有，和前一个字之间也全是静音（真念出来的“了”其实压在前一个字的尾音里）。
    贴法：从前一个字的结尾到它的有声尾巴（最多 EDGE_CAP 帧，至少 1 帧）。不贴的话这段静音里夹着一个“字”，收停顿收不掉，
    人工补剪又会被“删掉整个字”拦下，成片留下 ≥0.27 秒停顿。返回 (新的对齐结果, 贴了哪些字)；不改原对象。"""
    from review_build import active_map
    from tight_cut import word_frame_span
    base = copy.deepcopy(state); base.pop('resolve_frame_cuts', None)
    keep = set(active_map(cards, base, aligned, total)['retained_token_ids'])
    words = sorted(((word_frame_span(t['start_ms'], t['end_ms'], total)[:2], t) for t in aligned['tokens'] if t['id'] in keep),
                   key=lambda w: w[0][0])
    th = threshold(levels, [span for span, _ in words])
    loud = lambda f: 0 <= f < total and levels[f] > th and (vad is None or vad[f] >= VAD_MIN)
    moved, out = [], copy.deepcopy(aligned)
    index = {t['id']: t for t in out['tokens']}
    for ((a0, b0), t0), ((a1, b1), t1) in zip(words, words[1:]):
        if t0['unit_id'] != t1['unit_id'] or a1 < b0:
            continue
        end = b0
        while end - b0 < EDGE_CAP and loud(end): end += 1      # 前一个字的有声尾巴
        if a1 < end or any(loud(f) for f in range(end, b1)):
            continue      # 字本来就贴在尾音里，或尾巴之后到这个字结束有声音（真念在那儿）：不动
        text = (texts or {}).get(t1['unit_id'])
        if text is not None and t1.get('char_end') is not None and t1['char_end'] < len(text) and text[t1['char_end']] not in PHRASE_END:
            continue      # 句中的字（“一个”的“一”）对齐偏了不贴：真声音可能就在后面，贴过去会把它当停顿剪掉
        end = max(end, b0 + 1)
        row = index[t1['id']]
        before = [row['start_ms'], row['end_ms']]
        row['start_ms'], row['end_ms'] = round(b0 * 1001 / 30, 3), round(end * 1001 / 30, 3)
        moved.append({'字': t1['text'], '编号': t1['id'], '前一个字': t0['text'], '原时间毫秒': before, '贴到毫秒': [row['start_ms'], row['end_ms']]})
    return out, moved


LEVELS_NAME = '气口音量.json'


def manual_cuts(state):
    """人工气口精修加的裁剪（不是本程序按音量算出来的）。重算时原样保留。"""
    return [c for c in state.get('resolve_frame_cuts') or [] if c.get('reason') != AUTO_REASON]


def recompute(cards, before, after, aligned, total, levels, vad=None):
    """删句变了以后（before → after），只改受影响的接缝，别处一帧不动（2026-11-13，台账 Q060）。
    以前精修只算一次：之后在网页删句，被删句子的字头字尾保护帧还留在成片里，接缝有半口气。
    做法：同一份音量、同一个门限，分别按改前、改后的删句各算一遍自动裁剪，只把两遍的差别套到现有裁剪上。
    人工加的裁剪、人工改过或拿掉的自动裁剪都不在差别里，所以原样保留（水电那条人工调过 8 处自动裁剪）。"""
    th = (after.get('pause_refine') or {}).get('threshold_db')
    fresh = lambda st: {tuple(c['source_frames']) for c in refine_state(cards, st, aligned, total, levels, th, vad)[0]['resolve_frame_cuts']}
    old, new = fresh(before), fresh(after)
    drop, add = old - new, new - old
    now = copy.deepcopy(after.get('resolve_frame_cuts') or [])
    present = {tuple(c['source_frames']) for c in now if c.get('reason') == AUTO_REASON}
    # 本该在、但人工精修时已经拿掉或改过的自动裁剪：记下来，之后恢复这句时不把它加回来（删句再恢复，人工调整不丢）
    skipped = {tuple(c) for c in (after.get('pause_refine') or {}).get('人工动过的自动裁剪', [])} | (drop - present)
    cuts = [c for c in now if not (c.get('reason') == AUTO_REASON and tuple(c['source_frames']) in drop)]
    have = {tuple(c['source_frames']) for c in cuts}
    cuts += [{'source_frames': list(c), 'reason': AUTO_REASON} for c in sorted(add) if c not in have and c not in skipped]
    out = copy.deepcopy(after); out['resolve_frame_cuts'] = sorted(cuts, key=lambda c: c['source_frames'])
    if skipped: out['pause_refine'] = {**out['pause_refine'], '人工动过的自动裁剪': sorted(map(list, skipped))}
    return out, {'去掉': sorted(map(list, drop)), '新加': sorted(map(list, add))}


def without_web_edits(state):
    """精修那一刻的删句：去掉之后在网页里做的删句和恢复（精修总在网页编辑之前做）。"""
    base = copy.deepcopy(state); base.pop('editor', None); base.pop('text_edits', None)
    return base


def save_levels(run, levels, source, name=LEVELS_NAME, key='levels'):
    """每帧音量存下来，网页删句后重算接缝不用再读一遍整片音频。"""
    from pathlib import Path
    st = Path(source).stat()
    (Path(run) / name).write_text(json.dumps({'source': str(source), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns, key: levels}))


def load_levels(run, source, total, name=LEVELS_NAME, key='levels'):
    """存下来的每帧音量；没有、帧数不对或口播源换过（replace-source）就返回 None。"""
    from pathlib import Path
    try:
        d = json.loads((Path(run) / name).read_text()); st = Path(source).stat()
    except (OSError, ValueError): return None
    ok = d.get('source') == str(source) and d.get('size') == st.st_size and d.get('mtime_ns') == st.st_mtime_ns and len(d.get(key, [])) == total
    return d[key] if ok else None


VAD_NAME = '气口人声概率.json'


def load_vad(run, source, total):
    """存下来的每帧人声概率（指纹同每帧音量：口播源路径、大小、修改时间）。"""
    return load_levels(run, source, total, VAD_NAME, 'vad')


def align_python():
    """对齐环境的 Python，和 align 一步同一个找法（bin/editing_preflight.alignment_python）。"""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('editing_preflight', Path(__file__).resolve().parents[2] / 'bin' / 'editing_preflight.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.alignment_python()


def vad_probs(run, source, total, audio_stream=0):
    """每帧人声概率：先用缓存，没有就用对齐环境的 Python 起 vad_frames.py 算一遍并存缓存。
    返回 (概率列表, None)；算不出来返回 (None, 中文原因)，不抛错。"""
    from pathlib import Path
    cached = load_vad(run, source, total)
    if cached is not None: return cached, None
    python = align_python()
    if not python.is_file(): return None, f'找不到对齐环境的 Python：{python}'
    try:
        process = subprocess.run([str(python), str(Path(__file__).with_name('vad_frames.py')), str(source), str(total), str(audio_stream)],
                                 capture_output=True, text=True, timeout=VAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f'语音检测超过 {VAD_TIMEOUT} 秒没算完'
    except OSError as error:
        return None, f'对齐环境的 Python 起不来：{error}'
    if process.returncode == 3: return None, '对齐环境里没有装 silero-vad'
    if process.returncode:
        tail = (process.stderr or '').strip().splitlines()
        return None, f'语音检测出错（退出码 {process.returncode}）' + (f'：{tail[-1][:200]}' if tail else '')
    try:
        probs = json.loads(process.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, '语音检测的输出读不懂'
    if not isinstance(probs, list) or len(probs) != total:
        return None, f'语音检测算出的帧数不对（{len(probs) if isinstance(probs, list) else "?"}，应为 {total}）'
    save_levels(run, probs, source, VAD_NAME, 'vad')
    return probs, None


def vad_or_warn(run, source, total, audio_stream=0):
    """人声概率；算不出来就打印一行提醒，返回 None（照旧只看音量）。"""
    probs, why = vad_probs(run, source, total, audio_stream)
    if probs is None: print(f'提醒：这次没用上语音检测（{why}），只按音量判断有声，和以前的做法一样。')
    return probs


def after_edit(run, cards, before, after, aligned, total, source):
    """审核网页每次删句、恢复之后调用：精修过的片子只改这次动到的接缝。没精修过、或没有存下来的音量，原样返回（交付前的碎片检查会拦）。"""
    if not source or not after.get('pause_refine'): return after
    levels = load_levels(run, source, total)
    if levels is None: return after
    vad = None
    if after['pause_refine'].get('vad_min') is not None:     # 精修时用了语音检测：只用存下来的人声概率，没有就不动
        vad = load_vad(run, source, total)
        if vad is None: return after
    return recompute(cards, before, after, aligned, total, levels, vad)[0]


def should_refine(state, force=False):
    """False when already refined and nothing changed since. Manual trims are always kept (see recompute)."""
    if state.get('resolve_frame_cuts') and not state.get('pause_refine'):
        raise SystemExit('审核状态里已有别的精确裁剪（可能是手改记录），不自动覆盖；请先核对。')
    if force and state.get('pause_refine') and (manual_cuts(state) or state.get('editor', {}).get('operations') or state.get('text_edits')):
        # 整片从头重算会冲掉人工气口精修（人工加的裁剪、人工改过的自动裁剪）和网页改动后的接缝
        raise SystemExit('这条已经做过人工气口精修或网页改动，--force 整片重算会把它们冲掉，没有执行。网页删句后的接缝：直接运行 refine（不加 --force），只改受影响的接缝。')
    edited = bool(state.get('editor', {}).get('operations') or state.get('text_edits'))
    return force or edited or not state.get('pause_refine')   # 网页改过删句：只改受影响的接缝（2026-11-13，Q060）


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--force', action='store_true'); args = parser.parse_args()
    from project_runtime import RUN, TOTAL, CONFIG, VOICE
    import review_build, review_v2_server
    previous = json.loads((RUN / 'review_v2_state.json').read_text())
    if not should_refine(previous, args.force):
        print('停顿已按实际音量精修过，跳过（要重算加 --force）。'); return
    manifest = json.loads((RUN / 'review_v2.json').read_text())
    aligned = json.loads((RUN / 'character_alignment.json').read_text())
    levels = load_levels(RUN, VOICE, TOTAL)
    if levels is None:
        levels = [round(x, 1) for x in frame_levels(VOICE, TOTAL, CONFIG.get('audio_stream', 0))]
        save_levels(RUN, levels, VOICE)
    stream = CONFIG.get('audio_stream', 0)
    if previous.get('pause_refine') and not args.force:      # 精修过、之后网页改过删句：只改受影响的接缝
        vad = None
        if previous['pause_refine'].get('vad_min') is not None:   # 精修时用了语音检测，重算接缝也得用；只用音量会和原来的裁剪对不上
            vad, why = vad_probs(RUN, VOICE, TOTAL, stream)
            if vad is None:
                print(f'提醒：这次没用上语音检测（{why}）。这条精修时用了语音检测，只按音量重算会和原来的裁剪对不上，接缝没改；修好对齐环境后再运行 refine。'); return
        current, delta = recompute(manifest['cards'], without_web_edits(previous), previous, aligned, TOTAL, levels, vad)
        review_v2_server.persist_state(previous, current); review_build.build_full(manifest, current)
        print(f'按网页里现在的删句改了接缝：去掉 {len(delta["去掉"])} 处旧裁剪、新加 {len(delta["新加"])} 处；其余裁剪（含 {len(manual_cuts(current))} 处人工裁剪）没动。'); return
    vad = vad_or_warn(RUN, VOICE, TOTAL, stream)
    texts = {u['id']: u['text'] for u in json.loads((RUN / 'sentence_units.json').read_text())}
    aligned, moved = snap_quiet_words(manifest['cards'], previous, aligned, TOTAL, levels, vad, texts)
    if moved:      # 字时间改在 character_alignment.json（对齐原样在 alignment_qwen_full.json，reopen 会换回）；字幕跟着贴过去
        path = RUN / 'character_alignment.json'; tmp = path.with_name('.' + path.name + '.partial')
        tmp.write_text(json.dumps(aligned, ensure_ascii=False, indent=1)); tmp.replace(path)
    current, rows = refine_state(manifest['cards'], previous, aligned, TOTAL, levels, vad=vad)
    rows = [{'接口': '轻声字贴回前字尾音', **m} for m in moved] + rows
    review_v2_server.persist_state(previous, current)
    review_build.build_full(manifest, current)
    (RUN / '气口精修.json').write_text(json.dumps({
        '说明': f'{VERSION}：整片保留原声，按实际音量（高于 {current["pause_refine"]["threshold_db"]} dBFS'
              + (f'、且语音检测人声概率 >= {VAD_MIN}' if vad is not None else '（这次没用上语音检测）') +
              f' 视为有声，字边最多补 {EDGE_CAP} 帧）只删停顿中间的静音；'
              f'句间与删除重录接口最多留 {PAUSE_KEEP} 帧，句内静音超过 {INTRA_MAX} 帧收到 {INTRA_KEEP} 帧。',
        '听审': '待听', 'rows': rows}, ensure_ascii=False, indent=1))
    kept = len(manual_cuts(current))
    print(f'停顿精修：收短 {current["pause_refine"]["cuts"]} 处（按实际音量保护字头字尾）' + (f'；另有 {kept} 处人工裁剪原样保留。' if kept else '。')
          + (f'对齐时间整段落在静音里的轻声字 {len(moved)} 个（' + '、'.join(m['前一个字'] + m['字'] for m in moved[:8]) + '）贴回了前一个字的尾音。' if moved else ''))


if __name__ == '__main__':
    main()
