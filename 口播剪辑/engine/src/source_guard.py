"""Bind editing to the original transcription source, without rereading a whole movie."""
import hashlib
import json
from pathlib import Path


def standard_project_root(*paths):
    """标准项目的项目根（同时有 项目.json 和 01_原始素材 的那一层）；不是标准项目返回 None。"""
    for path in paths:
        if not path:
            continue
        path = Path(path)
        for parent in (path, *path.parents):
            if (parent/'项目.json').is_file() and (parent/'01_原始素材').is_dir():
                return parent
    return None


class SourceChanged(ValueError):
    """The saved transcript must not be used with this media."""


def fingerprint(path):
    size = path.stat().st_size
    block = 1024 * 1024
    offsets = sorted({round(max(0, size - block) * i / 15) for i in range(16)})
    checksum = hashlib.sha256(str(size).encode())
    with path.open('rb') as stream:
        for offset in offsets:
            stream.seek(offset)
            checksum.update(str(offset).encode() + b':' + stream.read(block))
    return {'algorithm': 'sha256-sampled-16x1MiB-v1', 'size': size, 'digest': checksum.hexdigest()}


def single_audio(info, audio_stream=0):
    if len([t for t in info['tracks'] if t['handler'] == 'soun']) != 1 or audio_stream != 0:
        raise ValueError('完整剪辑当前只支持单音轨视频；多音轨请先准备选定音轨的素材。独立转写仍可选轨。')


def verify_source(config, run, *, sampled=False, audio=False):
    if not config:  # unit tests without a project; real projects always have a config
        return None
    run = Path(run)
    record = json.loads((run.parent / '转写/source_manifest.json').read_text())
    source = Path(config['source']).resolve(strict=True)
    before = source.stat()
    current = {'path': str(source), 'size': before.st_size,
               'mtime_ns': before.st_mtime_ns, 'ctime_ns': before.st_ctime_ns}
    if any(record.get(key) != value for key, value in current.items()):
        raise SourceChanged('原片已改变或不匹配原始转写；已停止本次操作，请使用独立新项目。')
    if sampled and (not record.get('fingerprint') or fingerprint(source) != record['fingerprint']):
        raise SourceChanged('原片抽样指纹与转写不匹配；已停止本次操作。')
    if audio:
        from probe_media import probe
        single_audio(probe(source), config.get('audio_stream', 0))
    after = source.stat()
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (before.st_size, before.st_mtime_ns, before.st_ctime_ns):
        raise SourceChanged('原片在检查时发生变化；已停止本次操作。')
    approval = run / 'delivery_approved_v1/approval_manifest.json'
    if approval.exists():
        old = json.loads(approval.read_text())
        if (old['source'], old['source_size'], old['source_mtime_ns']) != (
                str(source), record['size'], record['mtime_ns']):
            raise SourceChanged('原片与已冻结来源基线不一致；不会重写旧基线。')
    return {'source': str(source), 'source_size': record['size'], 'source_mtime_ns': record['mtime_ns']}


# ---- 同帧数换源（2026-11-12《水电阶段》：口播换了新颜色/新声音版、帧数不变，返修删句要能继续走剪口播工具） ----
FPS = 30000 / 1001


def _identity(path):
    stat = Path(path).stat()
    return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns}


def _pcm(path, start, seconds, rate=16000, stream=0):
    import shutil, subprocess
    r = subprocess.run([shutil.which('ffmpeg') or 'ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{start:.3f}', '-t', f'{seconds:.3f}',
                        '-i', str(path), '-map', f'0:a:{stream}', '-ac', '1', '-ar', str(rate), '-f', 's16le', '-'], capture_output=True)
    if r.returncode: raise SourceChanged('读不出声音：' + r.stderr.decode(errors='replace')[-200:])
    import numpy as np
    return np.frombuffer(r.stdout, dtype='<i2').astype(float)


def _envelope(samples, step=160):
    """每 10 毫秒一个响度（dB），去掉均值后归一，换了音量和人声处理也能比时间。"""
    import numpy as np
    n = len(samples) // step
    if n < 50: return None
    blocks = samples[:n * step].reshape(n, step)
    level = 10 * np.log10((blocks ** 2).mean(axis=1) + 1.0)
    level = level - level.mean()
    norm = np.sqrt((level ** 2).sum())
    return level / norm if norm else None


def audio_offset(reference, candidate, starts, *, seconds=30.0, max_lag=0.5, stream=0):
    """在几个位置比较两段声音的起伏，返回 [(位置秒, 偏移毫秒, 相似度)]；偏移为正表示新声音晚。"""
    import numpy as np
    rows = []
    lag_steps = int(max_lag * 100)
    for start in starts:
        a = _envelope(_pcm(reference, start, seconds))
        b = _envelope(_pcm(candidate, start, seconds, stream=stream))
        if a is None or b is None: continue
        n = min(len(a), len(b)); a, b = a[:n], b[:n]
        best = max(range(-lag_steps, lag_steps + 1),
                   key=lambda k: float(np.dot(a[max(0, -k):n - max(0, k)], b[max(0, k):n - max(0, -k)])))
        score = float(np.dot(a[max(0, -best):n - max(0, best)], b[max(0, best):n - max(0, -best)]))
        rows.append((start, best * 10, score))
    return rows


def _gray(path, frame, count, width=72, height=128):
    import shutil, subprocess
    import numpy as np
    r = subprocess.run([shutil.which('ffmpeg') or 'ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{(frame - 0.5) / FPS:.4f}', '-i', str(path),
                        '-frames:v', str(count), '-fps_mode', 'passthrough', '-vf', f'scale={width}:{height}:flags=area,format=gray',
                        '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], capture_output=True)
    size = width * height
    if r.returncode or len(r.stdout) < size * count: raise SourceChanged(f'第 {frame} 帧读不出来：' + r.stderr.decode(errors='replace')[-200:])
    frames = np.frombuffer(r.stdout[:size * count], dtype=np.uint8).astype(float).reshape(count, height, width)
    # 用横竖方向的亮度变化（边缘）比，换了调色也不影响
    edges = np.abs(np.diff(frames, axis=1))[:, :, :-1] + np.abs(np.diff(frames, axis=2))[:, :-1, :]
    edges = edges.reshape(count, -1); edges -= edges.mean(axis=1, keepdims=True)
    return edges / (np.linalg.norm(edges, axis=1, keepdims=True) + 1e-9)


def frame_offsets(old, new, positions, *, reach=2):
    """在 positions 各帧上，把新片这一帧和旧片前后 reach 帧比，返回 [(帧号, 最像的偏移, 相似度)]。"""
    rows = []
    for frame in positions:
        start = max(0, frame - reach)
        candidates = _gray(old, start, frame + reach - start + 1)
        target = _gray(new, frame, 1)[0]
        scores = candidates @ target
        best = int(scores.argmax()); here = frame - start
        # 不动的镜头前后几帧几乎一样：偏 0 帧和最像的差不到 0.01 就算同一帧
        if scores[here] >= scores[best] - 0.01: best = here
        rows.append((frame, start + best - frame, float(scores[best])))
    return rows


def check_replacement(config, run, new, *, old=None):
    """新口播源能否接着用原审核状态：画面帧数/帧率/尺寸/单音轨一致，声音时间和转写时一致，给了旧片还逐帧抽查画面。
    通过返回检查记录；不通过抛 SourceChanged（中文原因）。"""
    from probe_media import probe
    new = Path(new).resolve(strict=True); run = Path(run)
    info = probe(new)
    video = next((t for t in info['tracks'] if t['handler'] == 'vide'), None)
    if not video: raise SourceChanged('新文件里没有画面')
    if video.get('fps') != {'num': 30000, 'den': 1001} or not video.get('constant_presentation_duration'):
        raise SourceChanged('新文件不是恒定 29.97 fps，不能接着用原来的剪辑')
    if video['sample_count'] != config['source_frames']:
        raise SourceChanged(f"新文件 {video['sample_count']} 帧，原来 {config['source_frames']} 帧；帧数不同不能换源（请用新项目）")
    if config.get('resolution') and video['display_size'] != config['resolution']:
        raise SourceChanged('新文件画面尺寸 %s 和原来 %s 不同' % ('×'.join(map(str, video['display_size'])), '×'.join(map(str, config['resolution']))))
    single_audio(info, config.get('audio_stream', 0))
    audio = next(t for t in info['tracks'] if t['handler'] == 'soun')
    duration = config['source_frames'] / FPS
    if abs(audio['duration_seconds'] - duration) > 0.2:
        raise SourceChanged(f"新文件声音 {audio['duration_seconds']:.2f} 秒，画面 {duration:.2f} 秒，差太多")
    record = {'frames': video['sample_count'], 'resolution': video['display_size'], 'audio_seconds': round(audio['duration_seconds'], 3)}
    # 声音：和转写用的那份声音比起伏，字时间都靠它
    reference = run.parent / '转写/source_audio.mp3'
    if reference.is_file():
        span = max(1.0, duration - 31)
        rows = audio_offset(reference, new, [round(span * k / 4, 1) for k in range(5)], stream=0)
        record['audio_checks'] = [{'at_seconds': s, 'offset_ms': o, 'similarity': round(c, 3)} for s, o, c in rows]
        if len(rows) < 3: raise SourceChanged('声音比对位置不够（新声音大段静音？），不能确认字时间还对得上')
        bad = [r for r in rows if abs(r[1]) > 40 or r[2] < 0.5]
        if bad:
            s, o, c = bad[0]
            raise SourceChanged(f'新声音在 {s:.0f} 秒处和转写时差 {o} 毫秒（相似度 {c:.2f}）；字时间会错，不能换源')
    else:
        raise SourceChanged('找不到转写用的声音 转写/source_audio.mp3，不能确认新声音时间一致')
    if old:
        old = Path(old).resolve(strict=True)
        total = config['source_frames']
        positions = [min(total - 3, max(2, round(total * k / 9))) for k in range(1, 9)]
        rows = frame_offsets(old, new, positions)
        record['old_compared'] = str(old)
        record['frame_checks'] = [{'frame': f, 'offset': o, 'similarity': round(c, 3)} for f, o, c in rows]
        bad = [r for r in rows if r[1] != 0 or r[2] < 0.6]
        if bad:
            f, o, c = bad[0]
            raise SourceChanged(f'第 {f} 帧和旧片对不上（最像的是旧片偏 {o} 帧，相似度 {c:.2f}）；不能换源')
    return record


def replace_source(config, run, new, *, old=None, now=None):
    """检查通过后，把审核、交付的来源基线换成新文件；旧基线写进 转写/换源记录.json。返回新的 config。"""
    import os
    from datetime import datetime
    run = Path(run); new = Path(new).resolve(strict=True)
    manifest_path = run.parent / '转写/source_manifest.json'
    previous = json.loads(manifest_path.read_text())
    checks = check_replacement(config, run, new, old=old)
    before = _identity(new)
    fresh = {**previous, **{k: before[k] for k in ('path', 'size', 'mtime_ns', 'ctime_ns')},
             'fingerprint': fingerprint(new), 'sha256': None, 'audio_sha256': None, 'full_sha256': None, 'replaced_source': True}
    if _identity(new) != before: raise SourceChanged('新文件在检查时发生变化；没有换源')
    history_path = run.parent / '转写/换源记录.json'
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    entry = {'time': (now or datetime.now()).isoformat(timespec='seconds'), 'from': previous, 'to': {k: fresh[k] for k in ('path', 'size', 'mtime_ns', 'ctime_ns')},
             'old_source_config': config.get('source'), 'old_media_pool_item_id': config.get('media_pool_item_id'), 'checks': checks}
    updated = dict(config)
    if str(new) != str(Path(config['source']).resolve()):
        # 换了路径：达芬奇里原来那个素材指向旧文件，下次交付时导入新文件
        updated['source'] = str(new); updated['media_pool_item_id'] = None
    else:
        updated['source'] = str(new)

    def write(path, value):
        temporary = path.with_name('.' + path.name + '.partial')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)); os.replace(temporary, path)
    write(history_path, history + [entry])
    write(manifest_path, fresh)
    write(run / 'project_runtime.json', updated)
    approval = run / 'delivery_approved_v1/approval_manifest.json'
    if approval.exists():
        write(approval, {'source': str(new), 'source_size': fresh['size'], 'source_mtime_ns': fresh['mtime_ns']})
    verify_source(updated, run, sampled=True, audio=True)
    return updated, entry
