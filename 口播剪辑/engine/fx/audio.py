"""Identify SFX from their source/ownership, never from an audio file extension."""
import os
from pathlib import Path
import re
from .plan import config, sfx_roots


def sound_track(name):
    return isinstance(name, str) and bool(re.fullmatch(r'音效(?:[2-9]|[1-9][0-9]+)?', name))


def is_sound(item):
    path = item.get('media_path')
    if not path:
        return False
    return any(Path(path).resolve().is_relative_to(root) for root in sfx_roots(config('sfx_favorites.json')))


def audio_preflight(timeline, record=None):
    owned = {s['item_id'] for s in (record or {}).get('sounds', [])}
    names = set()
    for track in timeline['tracks']:
        if track['type'] != 'audio' or not sound_track(track['name']):
            continue
        if track['name'] in names:
            raise ValueError('音效专用轨重名：'+track['name'])
        names.add(track['name'])
        rows = [i for i in timeline['items'] if i['type'] == 'audio' and i['track'] == track['index']]
        if any(not is_sound(i) for i in rows):
            raise ValueError('音效专用轨包含人声、B-roll 原声或未知片段，拒绝混写：'+track['name'])
    tracks = {t['index']: t['name'] for t in timeline['tracks'] if t['type'] == 'audio'}
    safe_indices = {n for n in tracks if n>=3 and all(is_sound(i) for i in timeline['items'] if i['type']=='audio' and i['track']==n)}
    for item in timeline['items']:
        if item['id'] in owned and (item['type'] != 'audio' or not (item['track'] in safe_indices or sound_track(tracks.get(item['track'])))):
            raise ValueError('本程序音效已被移入口播或原声轨，build前检查失败：'+item['id'])


def analyze_sound(info, cache_path=None, fps=30000/1001):
    """Measure the selected segment, preserving channels and leading silence.

    Positive changes of 5ms peak envelopes locate attacks. Attacks within 40ms
    belong to one impact; the largest rise wins, not the first audible sample.
    This is an automatic suggestion, not a listening/visual acceptance.
    """
    import array
    import hashlib
    import json
    import math
    import subprocess
    import sys
    from .snapshot import write_json
    path = Path(info['path']); source = info.get('source_start_frame', 0)
    length = info['duration_frames']
    if type(source) is not int or source < 0 or type(length) is not int or length <= 0:
        raise ValueError('音效源选段必须为非负入点和正整数长度')
    stat = path.stat()
    key = hashlib.sha256(json.dumps([str(path.resolve()), stat.st_mtime_ns,
        stat.st_size, source, length, fps, 'attack-5ms-cluster40ms-v1']).encode()).hexdigest()
    cache_path = Path(cache_path) if cache_path else None
    cache = json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else {}
    if key in cache:
        return cache[key]
    try:
        probe = subprocess.run(['ffprobe','-v','error','-select_streams','a:0',
            '-show_entries','stream=sample_rate,channels','-of','json',str(path)],
            capture_output=True, timeout=60, check=True)
        stream = json.loads(probe.stdout)['streams'][0]
        rate, channels = int(stream['sample_rate']), int(stream['channels'])
        decoded = subprocess.run(['ffmpeg','-v','error','-nostdin','-threads','1',
            '-i',str(path),'-ss',str(source/fps),'-t',str(length/fps),
            '-map','0:a:0','-vn','-c:a','pcm_f32le','-f','f32le','-'],
            capture_output=True, timeout=60, check=True)
        samples = array.array('f'); samples.frombytes(decoded.stdout)
        if sys.byteorder != 'little': samples.byteswap()
    except (subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        raise ValueError('音效瞬态/峰值检测失败：'+path.name) from exc
    if not samples or not all(math.isfinite(v) for v in samples):
        raise ValueError('音效波形为空或无效：'+path.name)
    peak = max(abs(v) for v in samples)
    if peak == 0:
        raise ValueError('音效选段全为静音：'+path.name)
    block = max(1, round(rate*.005))*channels
    envelope = [max(abs(v) for v in samples[n:n+block]) for n in range(0,len(samples),block)]
    rises = [max(0, v-(envelope[n-1] if n else 0)) for n,v in enumerate(envelope)]
    threshold = max(rises)*.1
    candidates = [n for n,v in enumerate(rises) if v > 0 and v >= threshold
                  and (n==0 or v>=rises[n-1]) and (n==len(rises)-1 or v>rises[n+1])]
    attacks = []
    for n in candidates:
        if attacks and (n-attacks[-1][-1])*block/channels/rate <= .04:
            attacks[-1].append(n)
        else:
            attacks.append([n])
    peaks = [max(group,key=lambda n:rises[n]) for group in attacks]
    chosen = max(peaks,key=lambda n:rises[n])
    # Locate the maximum within that rising window, not the window's left edge.
    offset = chosen*block
    local = samples[offset:offset+block]
    sample = (offset+max(range(len(local)),key=lambda n:abs(local[n])))//channels
    value = {'sync_frame':source+round(sample/rate*fps),
        'sync_seconds':source/fps+sample/rate, 'ordinal':peaks.index(chosen)+1,
        'transient_count':len(peaks), 'peak_dbfs':20*math.log10(peak),
        'method':'自动检测，第%d下（共%d下，最强起音瞬态）' % (peaks.index(chosen)+1,len(peaks))}
    # Rounding a last-sample transient must not put it outside the selected frames.
    value['sync_frame'] = min(source+length-1,value['sync_frame'])
    cache[key] = value
    if cache_path: write_json(cache_path,cache)
    return value


def effective_onset(info, cache_path=None, fps=30000/1001):
    """Compatibility name: returns the target transient, never a silence threshold."""
    result = dict(analyze_sound(info,cache_path,fps))
    if 'sync_frame' in info:
        frame = info['sync_frame']; source = info.get('source_start_frame',0)
        if type(frame) is not int or not source <= frame < source+info['duration_frames']:
            raise ValueError('音效 sync_frame 必须是选段内的绝对源帧')
        result.update(sync_frame=frame, sync_seconds=frame/fps, ordinal=None, method='常用音效 sync_frame 指定')
    return result


def peak_gain(peak_dbfs):
    import math
    return math.floor((-5-peak_dbfs)*1000)/1000


def sound_placement(spec, info, cache_path=None, timeline_start=0):
    selected = dict(info)
    if 'sync_frame' in spec: selected['sync_frame'] = spec['sync_frame']
    onset = effective_onset(selected,cache_path)
    source = info.get('source_start_frame',0); length = info['duration_frames']
    lead = spec.get('lead_frames',0)
    if type(lead) is not int or lead < 0: raise ValueError('音效 lead_frames 必须是非负整数')
    anchor = spec['start']-lead
    if anchor < timeline_start: raise ValueError('音效目标瞬态早于时间线起点')
    start = anchor-(onset['sync_frame']-source)
    if start < timeline_start:
        trim = timeline_start-start
        source += trim; length -= trim; start = timeline_start
        selected.update(source_start_frame=source,duration_frames=length)
        # Normalization must use the segment actually placed after boundary trim.
        onset['peak_dbfs'] = analyze_sound(selected,cache_path)['peak_dbfs']
    gain = spec.get('gain_db')
    explicit = gain is not None
    if not explicit:
        gain = peak_gain(onset['peak_dbfs'])
    return {'start':start,'source_start':source,'length':length,'gain_db':gain,
        'onset':onset, 'sync_timeline_frame':start+onset['sync_frame']-source,
        'output_peak_dbfs':onset['peak_dbfs']+gain,
        'volume_basis':'用户指定增益' if explicit else '选段实测峰值归一到不超过 -5 dBFS'}
