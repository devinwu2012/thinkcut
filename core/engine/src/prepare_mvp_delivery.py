"""Freeze the current review into one reproducible delivery package (SRT + frame map + checksums)."""
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from review_build import active_map, effective_cuts, fragment_problem, proofread_problem

from project_runtime import ROOT, RUN, PROJECT, SOURCE_ID, NAME, CONFIG
from source_guard import verify_source
INPUT_NAMES = ['review_v2_state.json', 'review_v2.json', 'character_alignment.json', 'sentence_units.json', 'glossary.txt']
BUILD_INPUTS = ['src/project_runtime.py', 'src/source_guard.py', 'src/timing.py', 'src/tight_cut.py', 'src/review_build.py',
                'src/prepare_mvp_delivery.py', 'src/import_mvp_delivery.py', 'src/run_mvp.py',
                'src/export_approved_captions.cjs', 'src/author_subtitle_drt.cjs',
                'caption_engine.js', 'caption_text.js', 'src/caption_ranges.js']
# 旧版 B-roll（审核状态里的 broll 计划、交付包里的 broll_timeline.json）已于 2026-11-21 下线：碰到带这种记录的片子就停下，不悄悄丢掉。
OLD_BROLL = ('这条片子带着旧版 B-roll 的记录（{where}）。旧版 B-roll（edit_project.py broll-index / broll-plan）已于 2026-11-21 下线，'
             '这次没有生成交付包、没有动达芬奇。配 B-roll 现在用 thinkcut-edit「配 B-roll」（bin/broll.py）；'
             '这条片子要继续保存口播主线，请交“架构审查”处理这份旧记录')


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def lock_busy(error):
    """Locked by someone else. On the NAS a lock held from another Mac is reported as Permission denied."""
    return error.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES)


@contextmanager
def exclusive_lock(path, message='Another delivery operation is running; retry when it finishes', wait=0):
    """Use an OS lock: process death releases it, unlike a stale lock-directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as lock:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if not lock_busy(error):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(message) from None
                time.sleep(min(0.2, remaining))
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def delivering(run):
    """True while a delivery to Resolve holds this project's delivery lock (from the page or the command line)."""
    with (run / '.delivering.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if not lock_busy(error):
                raise
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False


def artifact_names(manifest):
    return ['trial_keep_segments.json', 'tight_manifest.json', manifest['srt_name'],
            'subtitle_cues.json', '字幕纠错记录.json']


def timeline_names(signature, meta=None):
    """(final, base, srt). mvp-v3: one readable main line per save; older packages keep their names."""
    if meta is None or meta.get('version') == 'mvp-v3':
        label = (meta or {}).get('timeline_label', '')
        if meta is not None and not re.fullmatch(r'\d{4}-\d{4}_' + signature[:4], label):
            raise ValueError('Invalid timeline label')
        final = NAME + '_主线_' + label
        return final, NAME + '_无字幕_' + signature, final + '.srt'
    base = NAME + '_紧剪_' + signature
    return base + '_含字幕', base + '_无字幕', base + '.srt'


def delivery_signature(checksums, builders, cuts, source, order=None):
    value = {'checksums': checksums, 'builder': builders, 'cuts': cuts,
             'source': source, 'project': PROJECT, 'version': 'mvp-v2'}
    # 只挪了分句、没删字时，剪掉的帧一样；顺序也要算进身份，不然会沿用旧时间线（没挪过的项目不加，旧交付包名不变）
    if order:value['word_order'] = order
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


def verify_package(package, require_current=True):
    """Validate the complete frozen contract before any Resolve mutation."""
    package = Path(package)
    if require_current:verify_source(CONFIG,RUN)
    meta = json.loads((package / 'delivery_manifest.json').read_text())
    if meta.get('broll_items'):
        raise ValueError(OLD_BROLL.format(where='交付包 ' + package.name + ' 里有 broll_timeline.json'))
    if meta.get('version') not in ('mvp-v2', 'mvp-v3') or meta.get('no_resolve_render') is not True:
        raise ValueError('Package predates the current delivery contract; prepare again')
    if set(meta.get('input_sha256', {})) != set(INPUT_NAMES):
        raise ValueError('Package input checksum inventory is incomplete')
    builders=set(meta.get('builder_sha256', {}))
    allowed = [set(BUILD_INPUTS)]
    if not require_current:
        allowed += [x-{'src/caption_ranges.js'} for x in allowed]
        allowed += [x-{'src/source_guard.py'} for x in allowed]
    if builders not in allowed:
        raise ValueError('Package builder checksum inventory is incomplete')
    if set(meta.get('artifact_sha256', {})) != set(artifact_names(meta)):
        raise ValueError('Package artifact checksum inventory is incomplete')
    for name, expected in {**meta['input_sha256'], **meta['artifact_sha256']}.items():
        if Path(name).name != name or digest(package / name) != expected:
            raise ValueError('Frozen delivery file changed: ' + name)
    if require_current:
        for directory, checksums in ((RUN, meta['input_sha256']), (ROOT, meta['builder_sha256'])):
            for name, expected in checksums.items():
                if digest(directory / name) != expected:
                    raise ValueError(name + ' changed after preparation; prepare again')
    source = meta['source']
    stat = Path(source['path']).stat()
    if (stat.st_size, stat.st_mtime_ns) != (source['size'], source['mtime_ns']):
        raise ValueError('Source media changed after package preparation')
    mapping = json.loads((package / 'trial_keep_segments.json').read_text())
    signature = delivery_signature(meta['input_sha256'], meta['builder_sha256'], mapping['cut_frames'], source, mapping.get('word_order'))
    if (meta['signature'], meta['project'], meta['timeline_name'], meta['base_timeline_name'], meta['srt_name'], meta['fps']) != (
            signature, PROJECT, *timeline_names(signature, meta), [30000, 1001]):
        raise ValueError('Package identity does not match its frozen inputs')
    segments = mapping['segments']
    cursor = 0
    for seg in segments:
        if any(type(seg.get(key)) is not int for key in ('src_in', 'src_out', 'tl_in', 'tl_out')):
            raise ValueError('Frame map must contain integer frame boundaries')
        if (seg['src_out'] <= seg['src_in'] or
                seg['tl_in'] != cursor or seg['tl_out'] - cursor != seg['src_out'] - seg['src_in']):
            raise ValueError('Package frame map is inconsistent')
        cursor = seg['tl_out']
    # 挪过句子时片段不按原片先后排，但原片的每一帧最多用一次；没挪过的项目仍须按原片先后排
    by_source = sorted(segments, key=lambda s: s['src_in'])
    if (any(a['src_out'] > b['src_in'] for a, b in zip(by_source, by_source[1:]))
            or (not mapping.get('word_order') and by_source != segments)):
        raise ValueError('Package frame map is inconsistent')
    if not segments or len(segments) != meta['segments'] or cursor != meta['frames'] or cursor != mapping['frames']:
        raise ValueError('Package frame map totals are inconsistent')
    captions = json.loads((package / 'subtitle_cues.json').read_text())
    if (len(captions['cues']) != meta['subtitle_cues'] or
            captions['coverage']['caption_tokens'] != meta['retained_tokens'] or
            captions['coverage']['retained_tokens'] != meta['retained_tokens']):
        raise ValueError('Package subtitle counts are inconsistent')
    caption_ids = [token_id for cue in captions['cues'] for token_id in cue['ids']]
    if caption_ids != mapping['retained_token_ids'] or len(set(caption_ids)) != len(caption_ids):
        raise ValueError('Package retained token IDs differ from subtitles')
    return meta, mapping


def prepare():
    with exclusive_lock(RUN / 'mvp_deliveries' / '.prepare.lock'):
        return _prepare_locked()


def _prepare_locked():
    verify_source(CONFIG,RUN)
    # Parse and publish exactly these bytes. Never hash one revision and copy another.
    snapshot = {name: (RUN / name).read_bytes() for name in INPUT_NAMES}
    checksums = {name: hashlib.sha256(data).hexdigest() for name, data in snapshot.items()}
    baseline = json.loads((RUN / 'delivery_approved_v1' / 'approval_manifest.json').read_text())
    source = Path(baseline['source'])
    stat = source.stat()
    if (stat.st_size, stat.st_mtime_ns) != (baseline['source_size'], baseline['source_mtime_ns']):
        raise ValueError('Original source has changed; recheck media before delivery')
    source_info = {'path': str(source), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'media_pool_item_id': SOURCE_ID}
    state, review, aligned = (json.loads(snapshot[name]) for name in INPUT_NAMES[:3])
    semantic = effective_cuts(review['cards'], state)
    missing = [t['id'] for t in aligned['tokens'] if not t['ready'] and
               not any(a <= (t['start_ms']+t['end_ms'])*(30000/1001)/2000 < b for a,b in semantic)]
    if missing:
        raise ValueError('Retained words lack reliable alignment: ' + ', '.join(missing))
    mapping = active_map(review['cards'], state, aligned)
    problem = proofread_problem(state, mapping['retained_token_ids'], aligned['tokens'])
    if problem:
        raise ValueError(problem)
    if not mapping['segments']:
        raise ValueError('No reviewed speech remains')
    problem = fragment_problem(mapping, aligned)
    if problem:
        raise ValueError(problem)
    if mapping['frames'] != mapping['segments'][-1]['tl_out']:
        raise ValueError('Cut map has inconsistent frame totals')
    if state.get('broll'):
        raise ValueError(OLD_BROLL.format(where='审核/review_v2_state.json 里有 broll 计划'))
    builder_checksums = {name:digest(ROOT/name) for name in BUILD_INPUTS}
    signature = delivery_signature(checksums, builder_checksums, mapping['cut_frames'], source_info, mapping.get('word_order'))
    package = RUN / 'mvp_deliveries' / (NAME+'_' + signature)
    if package.exists():
        verify_package(package)
        print(json.dumps({'package': str(package), 'reused': True, 'frames': mapping['frames']}, ensure_ascii=False))
        return package
    staging = Path(tempfile.mkdtemp(prefix='.building-', dir=package.parent))
    try:
        for name, data in snapshot.items():
            (staging / name).write_bytes(data)
        (staging / 'trial_keep_segments.json').write_text(json.dumps({**mapping, 'source_fps': [30000, 1001]}, ensure_ascii=False, indent=2), encoding='utf-8')
        label = datetime.now().strftime('%m%d-%H%M') + '_' + signature[:4]
        timeline_name, base_timeline, srt_name = timeline_names(signature, {'version': 'mvp-v3', 'timeline_label': label})
        (staging / 'tight_manifest.json').write_text(json.dumps({'timeline_name': base_timeline}, ensure_ascii=False), encoding='utf-8')
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node.js is required to build the SRT')
        subprocess.run([node, str(ROOT/'src'/'export_approved_captions.cjs'), str(staging), srt_name[:-4]], check=True, capture_output=True, text=True)
        captions = json.loads((staging / 'subtitle_cues.json').read_text())
        if captions['coverage']['caption_tokens'] != captions['coverage']['retained_tokens']:
            raise ValueError('Subtitle coverage mismatch')
        expected_ids = mapping['retained_token_ids']
        caption_ids = [token_id for cue in captions['cues'] for token_id in cue['ids']]
        if caption_ids != expected_ids or len(expected_ids) != captions['coverage']['retained_tokens']:
            raise ValueError('Retained word IDs do not match SRT')
        meta = {'version': 'mvp-v3', 'timeline_label': label, 'signature': signature, 'project': PROJECT, 'timeline_name': timeline_name,
                'base_timeline_name': base_timeline, 'srt_name': srt_name,
                'source': source_info, 'fps': [30000, 1001], 'frames': mapping['frames'], 'segments': len(mapping['segments']),
                'subframe_words': len(mapping['precision_warnings']),
                'subtitle_cues': len(captions['cues']), 'retained_tokens': captions['coverage']['retained_tokens'],
                'input_sha256': checksums, 'builder_sha256': builder_checksums, 'no_resolve_render': True}
        meta['artifact_sha256'] = {name: digest(staging / name) for name in artifact_names(meta)}
        (staging / 'delivery_manifest.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        verify_package(staging)
        os.replace(staging, package)
    except BaseException:
        shutil.rmtree(staging)
        raise
    print(json.dumps({'package': str(package), 'reused': False, 'frames': mapping['frames'], 'segments': len(mapping['segments']), 'cues': len(captions['cues'])}, ensure_ascii=False))
    return package


if __name__ == '__main__':
    prepare()
