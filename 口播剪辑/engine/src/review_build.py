"""The single cut map (kept source frames) shared by preview, subtitles and delivery."""
import hashlib
import json
from bisect import bisect_right
from auto_review import RUN, write
from timing import kept_segments
from tight_cut import selection_map, word_frame_span
from project_runtime import TOTAL
FPS = 30000/1001


def effective_cuts(cards, state):
    cuts = [c['cut_frames'] for c in cards if c.get('cut_frames') and
            state.get('overrides',{}).get(c['id'],c['default'])=='apply']
    # Automatic local repeats are a baseline; explicit user restoration wins.
    cuts.extend([list(row['cut_frames']) for row in state.get('repeat_edits',{}).get('candidates',[]) if row['status']=='apply'])
    for edit in state.get('text_edits',{}).values():
        if edit['status'] != 'applied': continue
        a,b = edit['frames']
        if edit['action'] == 'cut': cuts.append([a,b])
        else:
            cuts = [part for x,y in cuts for part in
                    ([[x,y]] if y<=a or x>=b else
                     ([[x,a]] if x<a else []) + ([[b,y]] if y>b else []))]
    editor=state.get('editor',{})
    for operation in editor.get('operations',[])[:editor.get('cursor',0)]:
        if operation['action']=='move':continue   # 挪分句不改删了哪些帧，只改顺序（见 word_order）
        for a,b in operation['ranges']:
            if operation['action']=='cut':cuts.append([a,b])
            else:
                cuts=[part for x,y in cuts for part in
                      ([[x,y]] if y<=a or x>=b else
                       ([[x,a]] if x<a else [])+([[b,y]] if y>b else []))]
    # Pause compression is independent of word restoration.
    cuts.extend([list(row['cut_frames']) for row in state.get('pause_edits',{}).get('accepted',[])])
    merged=[]
    for a,b in sorted(cuts):
        if merged and a<=merged[-1][1]: merged[-1][1]=max(b,merged[-1][1])
        else: merged.append([a,b])
    return merged


def apply_move(order, ids, before):
    """把 ids（按当前顺序连在一起的一段字）挪到 before 这个字前面；before 为 None 挪到最后。"""
    moving = set(ids)
    rest = [i for i in order if i not in moving]
    at = rest.index(before) if before in rest else len(rest)
    return rest[:at] + [i for i in order if i in moving] + rest[at:]


def moves(state):
    """生效中的挪动操作（撤销掉的不算）。2026-11-13 第一版按整句记 units，之后按字记 ids。"""
    editor = state.get('editor', {})
    return [op for op in editor.get('operations', [])[:editor.get('cursor', 0)] if op['action'] == 'move']


def word_order(state, tokens):
    """成片里字的先后顺序（字编号列表）。网页里没挪过时就是原片顺序。网页按分句（逗号、句号）挪。"""
    order = [t['id'] for t in tokens]
    by_unit = {}
    for t in tokens: by_unit.setdefault(t['unit_id'], []).append(t['id'])
    for op in moves(state):
        ids = op['ids'] if 'ids' in op else [i for u in op['units'] for i in by_unit.get(u, [])]
        before = op['before']
        if 'ids' not in op and before is not None: before = (by_unit.get(before) or [None])[0]
        order = apply_move(order, ids, before)
    return order


def reorder(mapping, order, aligned, total_frames):
    """按字的新顺序重排成片（2026-11-13 网页挪分句）。删了哪些帧不变，只改先后。
    相邻两字之间的停顿从中间分开：前一半跟前一个字走，后一半跟后一个字走，挪动处的停顿和原来一样长。
    同一帧里分不开的字算一组，必须一起挪。"""
    retained = set(mapping['retained_token_ids'])
    words = sorted((word_frame_span(t['start_ms'], t['end_ms'], total_frames)[:2], t['id'])
                   for t in aligned['tokens'] if t['id'] in retained)
    group, bounds, groups = {}, [], []
    for k, ((a, b), ident) in enumerate(words):
        if k and a < words[k-1][0][1]:       # 和前一个字在时间上重叠：分不开，并成一组
            groups[-1][1] = max(groups[-1][1], b)
        else:
            if k: bounds.append((groups[-1][1] + a) // 2)
            groups.append([a, b])
        group[ident] = len(groups) - 1
    pieces = {}
    for s in mapping['segments']:
        a = s['src_in']
        for cut in [x for x in bounds if s['src_in'] < x < s['src_out']] + [s['src_out']]:
            pieces.setdefault(bisect_right(bounds, a), []).append([a, cut]); a = cut
    sequence = []
    for ident in order:
        if ident in group and (not sequence or sequence[-1] != group[ident]): sequence.append(group[ident])
    if len(sequence) != len(set(sequence)):
        raise ValueError('同一帧里说的几个字被分到两处了，分不开；请把这几个字连在一起挪')
    merged = []
    for g in sequence:
        for a, b in pieces.get(g, []):
            if merged and merged[-1][1] == a: merged[-1][1] = b
            else: merged.append([a, b])
    segments, position = [], 0
    for a, b in merged:
        segments.append({'src_in': a, 'src_out': b, 'tl_in': position, 'tl_out': position + b - a}); position += b - a
    rank = {ident: i for i, ident in enumerate(order)}
    ids = sorted(mapping['retained_token_ids'], key=rank.__getitem__)
    return {**mapping, 'segments': segments, 'retained_token_ids': ids, 'word_order': order}


def active_map(cards, state, aligned=None, total_frames=TOTAL):
    """The reviewed word frames used by preview, captions and delivery, in the reviewed word order."""
    if aligned is None:
        path = RUN/'character_alignment.json'
        aligned = json.loads(path.read_text()) if path.exists() else None
    mapping = source_order_map(cards, state, aligned, total_frames)
    if aligned and moves(state) and mapping['segments']:
        order = word_order(state, aligned['tokens'])
        if order != [t['id'] for t in aligned['tokens']]:
            mapping = reorder(mapping, order, aligned, total_frames)
    return mapping


def source_order_map(cards, state, aligned=None, total_frames=TOTAL):
    """保留的帧，按原片顺序排。"""
    semantic_cuts = effective_cuts(cards, state)
    if aligned is None:
        path = RUN/'character_alignment.json'
        aligned = json.loads(path.read_text()) if path.exists() else None
    if not aligned:
        segments = kept_segments(total_frames, semantic_cuts)
        return {'segments': segments, 'cut_frames': semantic_cuts,
                'frames': sum(s['src_out']-s['src_in'] for s in segments)}
    mapping = selection_map(aligned['tokens'], semantic_cuts, total_frames)
    # Explicit user requests may retain natural pauses between kept words.
    # A gap containing any semantic/text deletion remains cut in full.
    ranges = [row['source_frames'] for row in state.get('natural_pause_ranges', [])]
    # New projects keep every ASR sentence continuous; only gaps between
    # sentences stay tightly cut. Older states without the mode are unchanged.
    mode = state.get('natural_pause_mode')
    if mode not in (None, 'sentence'):
        raise ValueError('Invalid natural-pause mode')
    if mode == 'sentence':
        retained = set(mapping['retained_token_ids'])
        spans = {}
        for token in aligned['tokens']:
            if token['id'] in retained:
                a, b, _ = word_frame_span(token['start_ms'], token['end_ms'], total_frames)
                left, right = spans.get(token['unit_id'], (a, b))
                spans[token['unit_id']] = (min(left, a), max(right, b))
        ranges += [list(span) for span in spans.values()]
    for bounds in ranges:
        if (len(bounds) != 2 or any(type(x) is not int for x in bounds)
                or not 0 <= bounds[0] < bounds[1] <= total_frames):
            raise ValueError('Invalid natural-pause source range')
    padding = state.get('natural_pause_padding_frames', 0)
    if type(padding) is not int or not 0 <= padding <= 15:
        raise ValueError('Invalid natural-pause padding')
    manual_cuts = [row['source_frames'] for row in state.get('resolve_frame_cuts', [])]
    for bounds in manual_cuts:
        if (len(bounds) != 2 or any(type(x) is not int for x in bounds)
                or not 0 <= bounds[0] < bounds[1] <= total_frames):
            raise ValueError('Invalid Resolve source cut')
    if (not ranges and not manual_cuts) or not mapping['segments']:
        return mapping
    beside = state.get('restore_pause_beside_deletions', False)
    if type(beside) is not bool:
        raise ValueError('Invalid restore_pause_beside_deletions')
    merged = []
    restored = []
    for segment in mapping['segments']:
        a, b = segment['src_in'], segment['src_out']
        if merged:
            gap_start = merged[-1][1]
            inside = [[left, right] for left, right in semantic_cuts if left < a and right > gap_start]
            covered = any(left <= gap_start < a <= right for left, right in ranges)
            if covered and not inside:
                restored.append([gap_start, a])
                merged[-1][1] = b
                continue
            # Opt-in: keep the original pause on both sides of a deleted take;
            # the deletion itself stays cut and resolve_frame_cuts trims the rest.
            if beside and covered:
                merged[-1][1] = max(gap_start, min(x for x, _ in inside))
                a = min(a, max(y for _, y in inside))
        merged.append([a, b])
    # Preserve a little original audio around sentence edges. Never extend into
    # a deleted take, or shorten a gap that contains an intentional deletion.
    # Manual trims are subtracted exactly after padding. Letting them suppress
    # edge padding here would silently enlarge a user's frame trim.
    protected = semantic_cuts
    padded = []
    for i, (a, b) in enumerate(merged):
        left_gap = [merged[i-1][1] if i else 0, a]
        right_gap = [b, merged[i+1][0] if i+1 < len(merged) else total_frames]
        left = max(left_gap[0], a-padding)
        right = min(right_gap[1], b+padding)
        if (not any(x < left_gap[1] and y > left_gap[0] for x, y in protected)
                and any(x <= left <= a <= y for x, y in ranges)):
            a = left
        if (not any(x < right_gap[1] and y > right_gap[0] for x, y in protected)
                and any(x <= b <= right <= y for x, y in ranges)):
            b = right
        if padded and a <= padded[-1][1]:
            padded[-1][1] = max(padded[-1][1], b)
        else:
            padded.append([a, b])
    merged = []
    for a, b in padded:
        parts = [[a, b]]
        for left, right in manual_cuts:
            parts = [part for x, y in parts for part in
                     ([[x, y]] if y <= left or x >= right else
                      ([[x, left]] if x < left else []) + ([[right, y]] if y > right else []))]
        merged.extend(parts)
    # Frame trims made in Resolve may shorten a word; they must not silently
    # remove an entire reviewed word while leaving its subtitle behind.
    if manual_cuts:
        retained = set(mapping['retained_token_ids'])
        for token in aligned['tokens']:
            if token['id'] not in retained:
                continue
            # Same frame span as selection_map, including words shorter than one frame.
            left, right, _ = word_frame_span(token['start_ms'], token['end_ms'], total_frames)
            if not any(a < right and b > left for a, b in merged):
                raise ValueError('Resolve cut removes a complete reviewed word: ' + token['id'])
    segments, cuts, position, source_position = [], [], 0, 0
    for a, b in merged:
        if source_position < a:
            cuts.append([source_position, a])
        segments.append({'src_in': a, 'src_out': b, 'tl_in': position, 'tl_out': position + b - a})
        position += b - a
        source_position = b
    if source_position < total_frames:
        cuts.append([source_position, total_frames])
    return {**mapping, 'segments': segments, 'cut_frames': cuts, 'frames': position,
            'restored_pause_frames': restored}


def fragment_problem(mapping, aligned, total_frames=TOTAL):
    """成片里不含任何保留字的小段（两刀之间的孤立碎片），或 None。
    2026-11-13（台账 Q060）：气口精修后在网页删句，被删句子的字头字尾会留成这种碎片，交付前拦下。"""
    if not aligned or not mapping.get('retained_token_ids'): return None
    retained = set(mapping['retained_token_ids'])
    spans = [word_frame_span(t['start_ms'], t['end_ms'], total_frames)[:2] for t in aligned['tokens'] if t['id'] in retained]
    # 1–2 帧的不算：那是停顿里特意留的静音帧被旁边的人工裁剪隔开了（水电真数据），不是被删句子的声音（字头字尾保护至少再多几帧）
    stray = [s for s in mapping['segments'] if s['src_out'] - s['src_in'] > 2 and not any(a < s['src_out'] and b > s['src_in'] for a, b in spans)]
    if not stray: return None
    where = '、'.join(f"成片 {s['tl_in'] / FPS:.2f} 秒（{s['src_out'] - s['src_in']} 帧）" for s in stray[:8])
    return (f'成片里有 {len(stray)} 段不含任何字的碎片（多半是删句后留下的字头字尾）：{where}。'
            '先运行 edit_project.py refine 按当前删句重算接缝，再保存到达芬奇')


def proofread_problem(state, retained_ids, tokens):
    """Why these subtitles may not go to Resolve yet, or None. Projects created before proofreading existed are exempt."""
    if 'subtitle_proofread' not in state:
        return None
    retained = set(retained_ids)
    for row in state.get('subtitle_token_corrections', []):
        ids = set(row.get('ids', [row['id']]))
        if ids & retained and not ids <= retained:
            return '范围纠错只剩部分字，请撤销该范围纠错并重新校对后再保存到达芬奇'
    proofread = state['subtitle_proofread']
    if not proofread:
        return '字幕还没校对，不能导入达芬奇：请让 AI 按 审核/字幕校对.json 校对字幕并用 corrections --done 标记完成后再保存'
    missing = set(retained_ids) - set(proofread.get('token_ids', retained_ids))
    if missing:
        words = ''.join(t['text'] for t in tokens if t['id'] in missing)
        return ('校对之后又加回了没校对过的字幕（' + words[:20] + ('…' if len(words) > 20 else '') + '），不能导入达芬奇：'
                '请让 AI 重新生成 审核/字幕校对.json，校对新加的部分并用 corrections --done 标记')
    return None


def build_full(manifest,state):
    mapping=active_map(manifest['cards'],state)
    cuts=mapping['cut_frames']
    seconds=mapping['frames']/FPS
    write(RUN/'trial_keep_segments.json',{'cut_frames':cuts,'segments':mapping['segments'],'source_fps':[30000,1001],
          'retained_token_ids':mapping.get('retained_token_ids'),
          'removed_token_ids':mapping.get('removed_token_ids'),
          **({'word_order':mapping['word_order']} if mapping.get('word_order') else {})})
    identity=[cuts,mapping['word_order']] if mapping.get('word_order') else cuts
    full={'url':None,'seconds':seconds,'removed_seconds':TOTAL/FPS-seconds,
          'signature':hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:12],'cut_frames':cuts,'preview_available':False}
    write(RUN/'review_v2_full.json',full)
    print(f'full frame map {full["seconds"]:.2f}s, removed {full["removed_seconds"]:.2f}s',flush=True)
    return full
