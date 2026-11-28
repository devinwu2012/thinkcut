"""Read timeline state, recursive Text+, markers and stills without project edits."""
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from .connect import guard

TEXT_KEYS = ('StyledText Font Style Size Center GlobalIn GlobalOut Enabled1 Enabled2 Enabled3 '
             'Alpha1 Red1 Green1 Blue1 Opacity3 SoftnessX3 SoftnessY3 Offset3 '
             'Red2 Green2 Blue2 Thickness2 Red3 Green3 Blue3 Opacity1 Alpha2 Alpha3 '
             'Opacity2 SoftnessGlow3 ElementShape1 ElementShape2 ElementShape3 Thickness3 '
             'HorizontalJustificationNew VerticalJustificationNew').split()

# 只用来认“用户动过没有”的文字参数（字距、行距、旋转等，名字 2026-11-13 在达芬奇 21.1 里核对过）：程序不写它们，也不能当手调覆盖写回。
WATCH_KEYS = ('CharacterSpacing LineSpacing WordSpacing AngleX AngleY AngleZ LayoutRotation '
              'CharacterSizeX CharacterSizeY CharacterAngleZ Underline Strikeout').split()

BACKGROUND_KEYS = ('Level2 ExtendHorizontal2 ExtendVertical2 Round2 SoftnessX2 SoftnessY2 Offset2').split()


def safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [safe(v) for v in value]
    return {'unreadable_type': type(value).__name__}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe(data), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def read_timeline(timeline, ancestors=()):
    uid = timeline.GetUniqueId()
    if uid in ancestors:
        raise ValueError('嵌套时间线循环引用，无法完整读取。')
    settings = timeline.GetSetting() or {}
    result = {'name': timeline.GetName(), 'id': uid, 'start': timeline.GetStartFrame(),
              'end': timeline.GetEndFrame(), 'fps': settings.get('timelineFrameRate'),
              'width': settings.get('timelineResolutionWidth'), 'height': settings.get('timelineResolutionHeight'),
              'settings': settings, 'markers': timeline.GetMarkers() or {},
              'mark_in_out': timeline.GetMarkInOut(), 'tracks': [], 'items': []}
    for kind in ('video', 'audio', 'subtitle'):
        for track in range(1, timeline.GetTrackCount(kind)+1):
            track_name = timeline.GetTrackName(kind, track)
            result['tracks'].append({'type': kind, 'index': track, 'name': track_name,
                                     'enabled': timeline.GetIsTrackEnabled(kind, track),
                                     'locked': timeline.GetIsTrackLocked(kind, track)})
            for item in timeline.GetItemListInTrack(kind, track) or []:
                media = item.GetMediaPoolItem()
                row = {'id': item.GetUniqueId(), 'name': item.GetName(), 'type': kind,
                       'track': track, 'track_name': track_name, 'start': item.GetStart(),
                       'end': item.GetEnd(), 'duration': item.GetDuration(), 'enabled': item.GetClipEnabled(),
                       'source_start': item.GetSourceStartFrame(), 'source_end': item.GetSourceEndFrame(),
                       'media_path': media.GetClipProperty('File Path') if media else None,
                       'media_type': media.GetClipProperty('Type') if media else None,
                       'media_fps': media.GetClipProperty('FPS') if media else None,
                       'speed': clip_speed(item),
                       'props': item.GetProperty() or {}, 'texts': []}
                if kind=='audio' and track==1:
                    try:offset=item.GetLeftOffset(True)
                    except TypeError:offset=item.GetLeftOffset()  # Older bridge/fakes expose no argument.
                    row['source_offset']=round(offset)
                if kind == 'subtitle':
                    row['subtitle_text'] = item.GetName()
                if kind == 'video':
                    for ci in range(1, item.GetFusionCompCount()+1):
                        comp = item.GetFusionCompByIndex(ci)
                        if not comp:
                            raise RuntimeError(f'无法读取合成：{row["name"]} / {ci}')
                        attrs = comp.GetAttrs()
                        rs = attrs.get('COMPN_RenderStart', 0)
                        for tool in (comp.GetToolList(False, 'TextPlus') or {}).values():
                            inputs = tool.GetInputList() or {}
                            size_input = next((v for v in inputs.values() if v.GetAttrs().get('INPS_ID') == 'Size'), None)
                            output = size_input.GetConnectedOutput() if size_input else None
                            modifier = output.GetTool() if output else None
                            keyframes = modifier.GetKeyFrames() if modifier else None
                            row['texts'].append({'comp': ci, 'tool': tool.Name,
                                'comp_range': {k: attrs.get(k) for k in ('COMPN_RenderStart','COMPN_RenderEnd','COMPN_GlobalStart','COMPN_GlobalEnd')},
                                'inputs': {k: tool.GetInput(k, rs) for k in TEXT_KEYS+WATCH_KEYS+(BACKGROUND_KEYS if tool.GetInput('Enabled2',rs) and tool.GetInput('ElementShape2',rs)==2 else [])},
                                'size_has_keyframes': bool(keyframes), 'size_keyframes': keyframes,
                                'size_modifier': modifier.GetAttrs().get('TOOLS_RegID') if modifier else None,
                                'samples': [{'frame': f, 'text': tool.GetInput('StyledText', f),
                                             'size': tool.GetInput('Size', f), 'center': tool.GetInput('Center', f)}
                                            for f in (rs, rs + row['duration']//2, rs + row['duration']-1)]})
                    if row['media_type'] in ('Timeline', '时间线'):
                        nested = media.GetTimeline()
                        if not nested:
                            raise RuntimeError(f'无法读取嵌套时间线：{row["name"]}')
                        row['nested'] = read_timeline(nested, ancestors+(uid,))
                result['items'].append(row)
    return safe(result)


def clip_speed(item):
    """片段速度（1.2＝120%；2026-11-27 起口播原片在达芬奇里设 120%）。没设过、读不出（21.1 以前、假达芬奇）是 None。"""
    try:value=(item.GetSpeed() or {}).get('Percentage')
    except Exception:return None
    try:return None if value in (None, 100, 100.0) else float(value)/100
    except (TypeError,ValueError):return None


def snapshot(resolve, project, timeline):
    guard(resolve, project)
    data = {'schema_version': 1, 'project': project.GetName(), 'project_id': project.GetUniqueId(),
            'database': resolve.GetProjectManager().GetCurrentDatabase(),
            'resolve_version': resolve.GetVersionString(),
            'timelines': [{'name': t.GetName(), 'id': t.GetUniqueId()} for t in
                          (project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount()+1))],
            'timeline': read_timeline(timeline), 'timecode': timeline.GetCurrentTimecode()}
    guard(resolve, project)
    return safe(data)


def parse_drt_subtitles(path, expected):
    candidates=[]
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith('SeqContainer/') or not name.endswith('.xml'): continue
            text=archive.read(name).decode('utf-8')
            # Resolve's outer XML has non-XML ListMgt:: tags; only parse the valid subtitle subtree.
            for fragment in re.findall(r'<SubtitleTrackVec>.*?</SubtitleTrackVec>',text,re.S):
                tree=ET.fromstring(fragment);cues=[]
                for track,element in enumerate(tree.findall('./Element'),1):
                    for item in element.findall('.//Sm2TiGenerator'):
                        value=item.findtext('Name')
                        if item.findtext('PrettyType')!='Subtitle' or value is None: continue
                        start=int(item.findtext('Start'));duration=int(item.findtext('Duration'))
                        cues.append({'track':track,'start':start,'end':start+duration,'text':value})
                if cues: candidates.append(cues)
    wanted=sorted((x['track'],x['start'],x['end']) for x in expected)
    matches=[c for c in candidates if sorted((x['track'],x['start'],x['end']) for x in c)==wanted]
    if len(matches)!=1: raise ValueError('DRT 字幕轨不能按轨道和范围唯一对应，停止以免配错原话。')
    return matches[0]


def frame_timecode(frame, fps, drop=False):
    nominal = round(float(fps))
    frame = int(frame)
    if frame < 0 or nominal <= 0:
        raise ValueError('帧数和帧率无效')
    if drop:
        if nominal not in (30, 60):
            raise ValueError('仅支持 29.97/59.94 丢帧时间码')
        dropped = 2 if nominal == 30 else 4
        per10 = nominal*600 - dropped*9
        blocks, remainder = divmod(frame, per10)
        frame += dropped*9*blocks + dropped*max(0, (remainder-dropped)//(nominal*60-dropped))
    return f'{frame//(nominal*3600):02}:{frame//(nominal*60)%60:02}:{frame//nominal%60:02}{";" if drop else ":"}{frame%nominal:02}'


def markers(data):
    timeline = data['timeline']
    drop = ';' in (data.get('timecode') or '')
    return [{'frame': int(float(k)), 'absolute_frame': timeline['start']+int(float(k)),
             'timecode': frame_timecode(timeline['start']+int(float(k)), timeline['fps'], drop), **v}
            for k, v in sorted(timeline['markers'].items(), key=lambda pair: float(pair[0]))]


def still(resolve, project, timeline, frames, out_dir):
    guard(resolve, project, timeline)
    frames = list(dict.fromkeys(frames))
    if any(type(f) is not int or not timeline.GetStartFrame() <= f < timeline.GetEndFrame() for f in frames):
        raise ValueError('静帧必须是时间线范围内的整数绝对帧，终点不包含。')
    folder = Path(out_dir); folder.mkdir(parents=True, exist_ok=True)
    if any((folder/name).exists() for f in frames for name in
           (f'{f}.png', f'{f}_retry2.png', f'{f}_retry3.png')):
        raise FileExistsError('静帧目标已存在，停止以免覆盖。')
    original = timeline.GetCurrentTimecode()
    rows = []
    def seek(tc):
        guard(resolve, project, timeline)
        if not timeline.SetCurrentTimecode(tc):
            raise RuntimeError(f'播放头定位失败：{tc}')
        for _ in range(20):
            time.sleep(0.1)
            if timeline.GetCurrentTimecode() == tc:
                time.sleep(0.15)
                if timeline.GetCurrentTimecode() == tc:
                    return
        raise RuntimeError(f'播放头读回不匹配：{tc}')
    try:
        for frame in frames:
            tc = frame_timecode(frame, timeline.GetSetting('timelineFrameRate'), ';' in original)
            seek(tc)
            attempts=[]
            for attempt, delay in enumerate((1,3,5),1):
                if timeline.GetCurrentTimecode()!=tc:
                    seek(tc)
                time.sleep(delay)
                guard(resolve, project, timeline)
                path = folder/(f'{frame}.png' if attempt==1 else f'{frame}_retry{attempt}.png')
                exported = project.ExportCurrentFrameAsStill(str(path))
                readback = timeline.GetCurrentTimecode()
                size = path.stat().st_size if path.is_file() else 0
                attempts.append({'attempt':attempt,'exported':exported,'timecode':readback,'bytes':size})
                if readback==tc and exported and size:
                    break
            else:
                raise RuntimeError(f'静帧导出或时间码核对失败：{frame}；{attempts}')
            rows.append({'frame': frame, 'timecode': readback, 'path': str(path),
                         'attempts':attempts, 'bytes': path.stat().st_size,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    finally:
        seek(original)
    return {'stills': rows, 'original_timecode': original, 'restored_timecode': timeline.GetCurrentTimecode()}
