"""A single frame map for transcript preview, captions and Resolve delivery."""
import math
from bisect import bisect_right
from timing import kept_segments, ms_to_frame


def validate_word_time(start_ms, end_ms, total_frames):
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (start_ms, end_ms)):
        raise ValueError('Word timestamps must be finite numbers')
    if not 0 <= start_ms <= end_ms <= total_frames * 1000 * 1001 / 30000 + 1e-6:
        raise ValueError('Word timestamp outside source or reversed')


def word_frame_span(start_ms, end_ms, total_frames):
    if type(total_frames) is not int or total_frames <= 0:
        raise ValueError('Source has no valid integer video frame count')
    validate_word_time(start_ms, end_ms, total_frames)
    if end_ms == start_ms:
        raise ValueError('Spoken word has no duration')
    a, b = ms_to_frame(start_ms), ms_to_frame(end_ms)
    if b > a:
        return a, b, False
    midpoint = (start_ms + end_ms) * (30000 / 1001) / 2000
    a = min(max(math.floor(midpoint), 0), total_frames - 1)
    return a, a + 1, True


def selection_map(tokens, semantic_cuts, total_frames):
    """Keep only the frame intervals of words not removed by the review state."""
    if type(total_frames) is not int or total_frames <= 0:
        raise ValueError('Source has no video frames')
    approved = kept_segments(total_frames, semantic_cuts)
    approved_ends = [s['src_out'] for s in approved]
    intervals = []
    precision_warnings = []
    retained_ids, removed_ids = [], []
    token_ids = [t['id'] for t in tokens if 'id' in t]
    if len(set(token_ids)) != len(token_ids):
        raise ValueError('Duplicate word IDs in alignment')
    for token in tokens:
        start_ms, end_ms = token['start_ms'], token['end_ms']
        validate_word_time(start_ms, end_ms, total_frames)
        if end_ms == start_ms:
            point = start_ms * (30000 / 1001) / 1000
            if any(left <= point < right for left, right in semantic_cuts):
                if 'id' in token: removed_ids.append(token['id'])
                continue
            raise ValueError('Retained word has no duration: ' + str(token.get('id', token.get('text', ''))))
        a, b, subframe = word_frame_span(start_ms, end_ms, total_frames)
        index = bisect_right(approved_ends, a)
        span = approved[index] if index < len(approved) else None
        is_removed = span is None or span['src_in'] >= b
        if not is_removed and not (span['src_in'] <= a and b <= span['src_out']):
            raise ValueError('Cut partially overlaps a spoken word: ' + str(token.get('id', token.get('text', ''))))
        if is_removed:
            # A frame cannot distinguish words that occupy it together. The
            # editor compares these IDs with the explicit selection before save.
            if 'id' in token: removed_ids.append(token['id'])
            continue
        if subframe:
            precision_warnings.append({'token_id': token.get('id'), 'source_ms': [start_ms, end_ms],
                                       'source_frame': a, 'reason': 'word shorter than one video frame'})
        intervals.append((a, b))
        if 'id' in token: retained_ids.append(token['id'])
    intervals.sort()
    merged = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    segments = []
    position = 0
    for a, b in merged:
        segments.append({'src_in': a, 'src_out': b, 'tl_in': position, 'tl_out': position + b - a})
        position += b - a
    cuts = []
    position = 0
    for span in segments:
        if position < span['src_in']:
            cuts.append([position, span['src_in']])
        position = span['src_out']
    if position < total_frames:
        cuts.append([position, total_frames])
    return {'segments': segments, 'cut_frames': cuts,
            'frames': sum(s['src_out']-s['src_in'] for s in segments),
            'retained_token_ids': retained_ids, 'removed_token_ids': removed_ids,
            'precision_warnings': precision_warnings}
