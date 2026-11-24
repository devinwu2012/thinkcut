"""Build a viewing-only timeline from owned effects; never edit the source cut."""
from datetime import datetime
from pathlib import Path
import subprocess

from . import ledger, matte
from .audio import audio_preflight, is_sound, sound_track
from .resolve_io import ResolveIO, SETTINGS
from .snapshot import frame_timecode, still, write_json, read_timeline


def run_ffmpeg(args, output):
    result=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n',*args,str(output)],
                          capture_output=True,text=True,timeout=90)
    if result.returncode or not output.is_file() or not output.stat().st_size:
        raise RuntimeError('图片提取失败：'+result.stderr[-600:])


HINTS = {
    '重点大字': '看大小、位置是否挡脸/手机，停留够不够读完',
    '规格小字': '看两行是否对齐、是否挡手机',
    '对比标签': '看左右标签逐个弹出、每个起点一个音效、橙底白字是否清楚',
    '照片标签': '看与照片的间距、是否对称',
    '应用名': '看大小、是否压到手机画面',
    '音效': '听音量是否盖过人声、是否对准文字出现',
}


def category(effect):
    # Existing plans use photo_label for app names; retain their explicit identity.
    if effect.get('kind') == 'app_name' or str(effect.get('effect_id', '')).startswith('应用_'):
        return '应用名'
    if effect.get('kind') in ('spec', 'lens_label'):
        return '规格小字'
    if effect.get('kind') == 'compare_label':
        return '对比标签'
    if effect.get('kind') == 'photo_label':
        return '照片标签'
    return '重点大字'


def review_ranges(record, start, end, pad=30, merge_gap=30):
    """Absolute, end-exclusive source ranges. Group first, pad/clip, then merge."""
    if not record:
        raise ValueError('没有操作记录.json，无法确定本程序制作的花字和音效；请指定原 build 的 work-dir。')
    if any(type(v) is not int or v < 0 for v in (pad, merge_gap)):
        raise ValueError('pad 和 merge-gap 必须是非负整数帧')
    if start >= end:
        raise ValueError('原时间线范围为空')
    groups, effects = {}, {}

    def add(key, state, content, kind):
        a, b = state.get('start'), state.get('end')
        if (type(a) not in (int, float) or type(b) not in (int, float)
                or int(a) != a or int(b) != b or a >= b):
            raise ValueError('操作记录缺少有效起止：'+content)
        entry = {'start': int(a), 'end': int(b), 'content': content,
                 'category': kind, 'hint': HINTS[kind]}
        groups.setdefault(key, []).append(entry)

    for row in record.get('effects', []):
        effect = row['effect']
        key = str(effect.get('group_id') or row['effect_id'])
        effects[row['effect_id']] = key
        state = row.get('current_state') or row['written_state']
        add(key, state, state.get('StyledText', effect.get('text', row['effect_id'])), category(effect))
        if effect.get('kind') == 'compare_label':
            groups[key][-1]['hint'] += '；'+effect['timing_basis']+f'；出现帧 {state["start"]}'
    for index, row in enumerate(record.get('sounds', [])):
        if 'deleted' in row.get('manual_fields', []):
            continue
        state = row.get('current_state') or row['written_state']
        if state.get('enabled') is False:
            continue
        key = effects.get(row.get('effect_id'), 'sound_'+str(index))
        add(key, state, Path(state.get('media_path') or row.get('effect_id', '音效')).name, '音效')
    ranges = []
    for entries in groups.values():
        a = max(start, min(e['start'] for e in entries)-pad)
        b = min(end, max(e['end'] for e in entries)+pad)
        if a < b:
            ranges.append({'start': a, 'end': b, 'effects': entries})
    merged = []
    for row in sorted(ranges, key=lambda r: (r['start'], r['end'])):
        if merged and (row['start'] < merged[-1]['end']
                       or row['start']-merged[-1]['end'] < merge_gap):
            merged[-1]['end'] = max(merged[-1]['end'], row['end'])
            merged[-1]['effects'].extend(row['effects'])
        else:
            merged.append(row)
    if not merged:
        raise ValueError('操作记录中没有位于原时间线范围内的花字或音效')
    return merged


def marker_text(index, segment):
    categories = list(dict.fromkeys(e['category'] for e in segment['effects']))
    name = f'第{index}处 '+ '、'.join(categories)
    notes = list(dict.fromkeys(f'{e["category"]}「{e["content"]}」：{e["hint"]}' for e in segment['effects']))
    return {'color': 'Cyan', 'name': name, 'note': '\n'.join(notes),
            'duration': 1, 'customData': f'fx_review_{index}'}


def read_segments(timeline, segments, source_media_id, audio_media_id=None, sounds=None, matte_layers=None):
    """Read actual tracks, including source offsets; returned append handles are insufficient."""
    result = []
    for kind in ('video', 'audio'):
        items = [i for track in (range(1, timeline.GetTrackCount(kind)+1) if audio_media_id is None else [1])
                 for i in timeline.GetItemListInTrack(kind, track) or []]
        items.sort(key=lambda i: i.GetStart())
        if len(items) != len(segments):
            raise RuntimeError(f'审片 {kind} 段数不符：{len(items)} / {len(segments)}')
        for index, (item, segment) in enumerate(zip(items, segments), 1):
            row = {'segment': index, 'type': kind, 'id': item.GetUniqueId(),
                   'start': item.GetStart(), 'end': item.GetEnd(), 'duration': item.GetDuration(),
                   'source_start': item.GetLeftOffset(),
                   'source_end': item.GetLeftOffset()+item.GetDuration(),
                   'api_source_start': item.GetSourceStartFrame(), 'api_source_end': item.GetSourceEndFrame(),
                   'media_id': item.GetMediaPoolItem().GetUniqueId()}
            # For these freshly inserted, unretimed, same-FPS timeline nests,
            # LeftOffset is exact. GetSourceStart/EndFrame can truncate by one
            # at 29.97 fps; retain those raw readings as diagnostic evidence.
            if (row['start'], row['end'], row['duration'], row['source_start'], row['source_end'], row['media_id']) != (
                    segment['review_start'], segment['review_end'], segment['end']-segment['start'],
                    segment['source_offset'], segment['source_offset']+segment['end']-segment['start'],
                    audio_media_id if kind == 'audio' and audio_media_id is not None else source_media_id):
                raise RuntimeError('审片片段起止或来源读回不符：'+str(row))
            result.append(row)
    if audio_media_id is not None:
        if (timeline.GetTrackCount('audio') != 2 or timeline.GetTrackName('audio', 1) != '人声与原声'
                or timeline.GetTrackName('audio', 2) != '音效' or timeline.GetTrackCount('video') != (2 if matte_layers else 1)):
            raise RuntimeError('审片须为V1底图、可选V2人物抠像、A1人声与原声、A2音效')
        items = sorted(timeline.GetItemListInTrack('audio', 2) or [], key=lambda i: i.GetStart())
        wanted = sorted(sounds or [], key=lambda s: s['review_start'])
        if len(items) != len(wanted):
            raise RuntimeError('审片A2音效数量不符')
        for item, sound in zip(items, wanted):
            row = {'type':'audio', 'track':2, 'id':item.GetUniqueId(),
                   'start':item.GetStart(), 'end':item.GetEnd(), 'source_start':item.GetLeftOffset(),
                   'media_id':item.GetMediaPoolItem().GetUniqueId(), 'gain_db':item.GetProperty('AudioVolume')}
            expected = {'start':sound['review_start'], 'end':sound['review_end'],
                        'source_start':sound['source_offset'], 'media_id':sound['media_id'], 'gain_db':sound['gain_db']}
            if not ledger.equal({k:row[k] for k in expected}, expected) or not item.GetClipEnabled():
                raise RuntimeError('审片A2音效来源、位置或音量不符：'+str(row))
            result.append(row)
    if matte_layers:
        if timeline.GetTrackName('video',2)!=matte.TRACK or not timeline.GetIsTrackEnabled('video',2):
            raise RuntimeError('审片人物轨名称或启用状态不符')
        live={i.GetUniqueId():i for i in timeline.GetItemListInTrack('video',2) or []}
        if set(live)!={row['review_item_id'] for row in matte_layers}:
            raise RuntimeError('审片人物片段数量或身份不符')
        for row in matte_layers:
            item=live[row['review_item_id']]
            if (item.GetStart(),item.GetEnd())!=(row['start'],row['end']) or not item.GetClipEnabled():
                raise RuntimeError('审片人物片段区间或启用状态不符')
    markers = timeline.GetMarkers() or {}
    expected = {s['review_start']-timeline.GetStartFrame(): marker_text(i, s)
                for i, s in enumerate(segments, 1)}
    if not ledger.equal(markers, expected):
        raise RuntimeError('审片标记数量或文字读回不符')
    return result


def audio_signature(row):
    return {k:row.get(k) for k in ('track','start','end','source_start','source_end','media_path','enabled','props')}


def clean_audio_copy(io, record, before, name, evidence):
    """Mute only SFX in a NEW duplicate, including legacy SFX on mixed tracks."""
    audio_preflight(before['timeline'], record)
    source_rows = [i for i in before['timeline']['items'] if i['type'] == 'audio']
    if any(i.get('media_type') in ('Timeline', '时间线') for i in source_rows):
        raise ValueError('原线含嵌套音频，无法确认内部是否混入音效；请先明确声音来源，不生成混音A1')
    if any(io.p.GetTimelineByIndex(i).GetName() == name for i in range(1,io.p.GetTimelineCount()+1)):
        raise ValueError('无音效副本已存在，不覆盖：'+name)
    target = io.write(io.main.DuplicateTimeline, name)
    if not target:
        raise RuntimeError('复制无音效时间线失败')
    write_json(evidence/'audio_source_created.json', {'name':name,'id':target.GetUniqueId()})
    io.activate(target)
    copied = read_timeline(target)
    from .cleanup import register
    register(io,evidence.parents[1],target,'审片无音效源',copied)
    rows = [i for i in copied['items'] if i['type'] == 'audio']
    if not ledger.equal([audio_signature(i) for i in source_rows], [audio_signature(i) for i in rows]):
        raise RuntimeError('无音效副本的原始音频与主线不一致')
    sound_ids = {new['id'] for old,new in zip(source_rows,rows) if is_sound(old)}
    for track in range(1,target.GetTrackCount('audio')+1):
        if sound_track(target.GetTrackName('audio',track)):
            if not io.write(target.SetTrackEnable,'audio',track,False):
                raise RuntimeError('无法在副本关闭音效轨')
        for item in target.GetItemListInTrack('audio',track) or []:
            if item.GetUniqueId() in sound_ids:
                if not io.write(item.SetClipEnabled,False):
                    raise RuntimeError('无法在副本停用混合轨上的旧音效')
    # The viewing copy supplies the base picture; foregrounds are placed directly above it.
    if record.get('mattes'):
        for track in range(1,target.GetTrackCount('video')+1):
            if target.GetTrackName('video',track)==matte.TRACK:
                if not io.write(target.SetTrackEnable,'video',track,False):
                    raise RuntimeError('无法在审片底图副本停用人物抠像轨')
    after = read_timeline(target)
    current = [i for i in after['items'] if i['type'] == 'audio']
    for old,new in zip(rows,current):
        expected = audio_signature(old)
        if old['id'] in sound_ids:
            expected['enabled'] = False
        if not ledger.equal(expected,audio_signature(new)):
            raise RuntimeError('无音效副本的音频参数变化超出停用音效范围')
    if any(i['enabled'] and i['id'] in sound_ids for i in current):
        raise RuntimeError('无音效副本仍有启用的音效')
    folder = io.folder('审片',io.folder('花字音效'))
    if not io.write(io.p.GetMediaPool().MoveClips,[target.GetMediaPoolItem()],folder):
        raise RuntimeError('无音效副本归档失败')
    result = {'name':name,'id':target.GetUniqueId(),'media_id':target.GetMediaPoolItem().GetUniqueId(),
              'muted_sound_ids':sorted(sound_ids),'all_sounds_disabled':True,
              'non_sound_audio_unchanged':True,'before':copied,'after':after}
    write_json(evidence/'audio_source.json',result)
    register(io,evidence.parents[1],target,'审片无音效源',after,seal=True)
    return target,result


def sound_ranges(record, timeline, segments):
    """Use live owned clips, not stale ledger gain/timing. Never replay deleted clips."""
    by_id = {i.GetUniqueId():i for tr in range(1,timeline.GetTrackCount('audio')+1)
             for i in timeline.GetItemListInTrack('audio',tr) or []
             if timeline.GetIsTrackEnabled('audio',tr)}
    result,media = [],{}
    for sound in record.get('sounds',[]):
        item = by_id.get(sound['item_id'])
        if item is None or not item.GetClipEnabled():
            continue
        source = item.GetMediaPoolItem();mid = source.GetUniqueId();media[mid] = source
        for n,segment in enumerate(segments,1):
            a,b = max(item.GetStart(),segment['start']),min(item.GetEnd(),segment['end'])
            if a >= b:
                continue
            start = segment['review_start']+a-segment['start']
            result.append({'effect_id':sound['effect_id'],'segment':n,'original_start':a,'original_end':b,
                           'review_start':start,'review_end':start+b-a,
                           'source_offset':item.GetLeftOffset()+a-item.GetStart(),
                           'media_id':mid,'media_path':source.GetClipProperty('File Path'),
                           'gain_db':item.GetProperty('AudioVolume')})
    result.sort(key=lambda s:s['review_start'])
    if any(a['review_end'] > b['review_start'] for a,b in zip(result,result[1:])):
        raise ValueError('本程序音效互相重叠，单条审片A2放不下；需要用户决定分轨，未覆盖声音')
    return result,media


# Apply color-management settings in dependency order to the NEW timeline only.
COLOR_SETTINGS = ('colorScienceMode', 'isAutoColorManage', 'rcmPresetMode',
                  'separateColorSpaceAndGamma', 'colorSpaceTimeline', 'colorSpaceTimelineGamma',
                  'colorSpaceOutput', 'colorSpaceOutputGamma', 'inputDRT', 'outputDRT',
                  'timelineWorkingLuminanceMode', 'timelineWorkingLuminance',
                  'graphicsWhiteLevel', 'disableFusionToneMapping', 'imageResizingGamma')
IMAGE_SETTINGS = ('timelinePixelAspectRatio', 'timelineInputResMismatchBehavior',
                  'timelineOutputResMatchTimelineRes', 'timelineOutputResolutionWidth',
                  'timelineOutputResolutionHeight', 'timelineOutputPixelAspectRatio',
                  'timelineOutputResMismatchBehavior')


def create_timeline(io, name):
    if any(io.p.GetTimelineByIndex(i).GetName() == name for i in range(1, io.p.GetTimelineCount()+1)):
        raise ValueError('同分钟审片时间线已存在，不覆盖或重复创建：'+name)
    mp = io.p.GetMediaPool()
    folder = io.folder('审片', io.folder('花字音效'))
    if not io.write(mp.SetCurrentFolder, folder):
        raise RuntimeError('无法选择审片文件夹')
    review = io.write(mp.CreateEmptyTimeline, name)
    if not review:
        raise RuntimeError('新建审片时间线失败')
    io.activate(review)
    if not io.write(review.SetSetting, 'useCustomSettings', '1'):
        raise RuntimeError('审片自定义设置失败')
    setting_results = []
    for key in (*SETTINGS, *IMAGE_SETTINGS, *COLOR_SETTINGS):
        wanted = io.main.GetSetting(key)
        if wanted is None:
            continue
        if str(review.GetSetting(key)) != str(wanted):
            io.write(review.SetSetting, key, str(wanted))
        actual = review.GetSetting(key)
        setting_results.append({'key': key, 'source': wanted, 'review': actual})
        if key in (*SETTINGS, *IMAGE_SETTINGS) and str(actual) != str(wanted):
            raise RuntimeError('审片设置读回不符：'+key)
    if not io.write(review.SetStartTimecode, io.main.GetStartTimecode()):
        raise RuntimeError('审片起始时间码设置失败')
    if (review.GetStartTimecode() != io.main.GetStartTimecode()
            or review.GetStartFrame() != io.main.GetStartFrame()):
        raise RuntimeError('审片起点读回不符')
    if review.GetMediaPoolItem().GetUniqueId() not in {c.GetUniqueId() for c in folder.GetClipList() or []}:
        raise RuntimeError('新时间线不在花字音效/审片文件夹')
    return review, setting_results


def checklist(path, name, duration, segments, fps, drop, color_note):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', '<br>')
    lines = [f'# {name}（总时长 {duration:.2f} 秒）', '', color_note, '',
             '这条时间线只用于审核花字和音效；修改请回原时间线。静帧均来自原时间线各段中间，时间码区间终点不含。', '',
             '| 第几处 | 审片时间线时间码 | 原时间线时间码 | 内容（文字/音效名） | 请看什么 | 静帧 |',
             '| --- | --- | --- | --- | --- | --- |']
    for index, s in enumerate(segments, 1):
        tc = lambda f: frame_timecode(f, fps, drop)
        content = '；'.join(dict.fromkeys(e['content'] for e in s['effects']))
        hints = '；'.join(dict.fromkeys(e['category']+'：'+e['hint'] for e in s['effects']))
        link = Path(s['still']).relative_to(path.parent).as_posix()
        values = [index, tc(s['review_start'])+'–'+tc(s['review_end']),
                  tc(s['start'])+'–'+tc(s['end']), content, hints,
                  f'[原线静帧](<{link}>)']
        lines.append('| '+' | '.join(cell(v) for v in values)+' |')
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')


def review(resolve, project, timeline, project_name, work_dir, pad=30, merge_gap=30, apply=False):
    """默认只演练：报出会建哪两条时间线、覆盖哪些段，不碰工程；apply=True 才真建并保存。"""
    work_dir = Path(work_dir)
    record = ledger.load(work_dir)
    # Missing ledger must fail even before any Resolve interface is used.
    if not record:
        raise ValueError('没有操作记录.json，无法确定审核点；请指定原 build 的 work-dir。')
    ledger.refuse_legacy(record)
    io = ResolveIO(resolve, project, timeline, project_name)
    if any(record.get(k) != value for k, value in (
            ('project_id', project.GetUniqueId()), ('database', io.db),
            ('timeline_id', timeline.GetUniqueId()), ('timeline', timeline.GetName()))):
        raise ValueError('操作记录不属于指定工程、数据库或原时间线')
    if record.get('pending'):
        raise ValueError('花字整批写入还没完成，不能建审片线；下一步：等设视频源目标的用 build --resume 续做，失败的先 recover，再 review')
    segments = review_ranges(record, int(timeline.GetStartFrame()), int(timeline.GetEndFrame()), pad, merge_gap)
    stamp = datetime.now().strftime('%m%d-%H%M')
    name = f'审片_{timeline.GetName()}_{stamp}'
    output = work_dir/f'审片清单_{stamp}.md'
    evidence = work_dir/'审片证据'/stamp
    if not apply:
        # 演练：只算不写。不建时间线、不建媒体池文件夹、不保存工程、不写证据文件
        fps = float(timeline.GetSetting('timelineFrameRate')); drop = ';' in timeline.GetStartTimecode()
        cursor = int(timeline.GetStartFrame()); rows = []
        for index, s in enumerate(segments, 1):
            length = s['end']-s['start']
            rows.append({'index': index, 'start': s['start'], 'end': s['end'],
                         'timecode': frame_timecode(s['start'], fps, drop)+'–'+frame_timecode(s['end'], fps, drop),
                         'review_start': cursor, 'review_end': cursor+length,
                         'content': list(dict.fromkeys(e['content'] for e in s['effects']))})
            cursor += length+20
        return {'applied': False, 'would_create_timelines': [name, name+'_无音效源'], 'media_pool_folder': '花字音效/审片',
                'segments': rows, 'would_write': [str(output), str(evidence)],
                'message': '演练完成，没有改工程。加 --apply 才会新建上面两条时间线（放媒体池“花字音效/审片”）并保存工程；'
                           '时间线名里的时间以真建那一刻为准。音轨来源等检查在真建时做。'}
    if evidence.exists() or output.exists():
        raise FileExistsError('同分钟审片输出已存在，请检查已有结果后再运行')
    with ledger.write_lock():       # 真建时间线：和 B-roll、花字写入同一把锁
        return _create_review(io, project, timeline, record, work_dir, segments, name, output, evidence)


def _create_review(io, project, timeline, record, work_dir, segments, name, output, evidence):
    original = project.GetCurrentTimeline()
    original_folder = project.GetMediaPool().GetCurrentFolder()
    before = io.snapshot()
    evidence.mkdir(parents=True)
    write_json(evidence/'before.json', before)
    target = None
    try:
        audio_preflight(before['timeline'], record)
        cursor = int(timeline.GetStartFrame())
        for segment in segments:
            length = segment['end']-segment['start']
            segment.update(review_start=cursor,review_end=cursor+length,
                           source_offset=segment['start']-timeline.GetStartFrame())
            cursor += length+20
        sounds,sound_media = sound_ranges(record,timeline,segments)
        write_json(evidence/'planned_sounds.json', sounds)
        audio_source,audio_proof = clean_audio_copy(io,record,before,name+'_无音效源',evidence)
        target, settings = create_timeline(io, name)
        write_json(evidence/'created.json', {'name': name, 'id': target.GetUniqueId(), 'settings': settings})
        from .cleanup import register
        register(io,work_dir,target,'审片过程')
        if target.GetTrackCount('audio') != 1:
            raise RuntimeError('新审片线默认音轨数量异常')
        if not io.write(target.SetTrackName,'audio',1,'人声与原声') or not io.write(target.AddTrack,'audio','stereo'):
            raise RuntimeError('审片分轨创建失败')
        if not io.write(target.SetTrackName,'audio',2,'音效'):
            raise RuntimeError('审片音效轨命名失败')
        media = audio_source.GetMediaPoolItem() if record.get('mattes') else timeline.GetMediaPoolItem()
        cursor = int(target.GetStartFrame())
        for index, segment in enumerate(segments, 1):
            length = segment['end']-segment['start']
            segment.update(review_start=cursor, review_end=cursor+length,
                           source_offset=segment['start']-timeline.GetStartFrame())
            for media_type in (1, 2):
                io.activate(target)
                clips = io.write(project.GetMediaPool().AppendToTimeline, [{
                    'mediaPoolItem': media if media_type == 1 else audio_source.GetMediaPoolItem(), 'startFrame': segment['source_offset'],
                    'endFrame': segment['source_offset']+length, 'recordFrame': cursor,
                    'trackIndex': 1, 'mediaType': media_type}])
                if not clips or len(clips) != 1:
                    raise RuntimeError(f'第{index}段放置失败（mediaType={media_type}）')
            mark = marker_text(index, segment)
            if not io.write(target.AddMarker, cursor-target.GetStartFrame(), mark['color'],
                            mark['name'], mark['note'], mark['duration'], mark['customData']):
                raise RuntimeError('审片标记写入失败')
            cursor += length+20
        for sound in sounds:
            io.activate(target)
            clips = io.write(project.GetMediaPool().AppendToTimeline,[{
                'mediaPoolItem':sound_media[sound['media_id']], 'startFrame':sound['source_offset'],
                'endFrame':sound['source_offset']+sound['review_end']-sound['review_start'],
                'recordFrame':sound['review_start'], 'trackIndex':2, 'mediaType':2}])
            if not clips or len(clips) != 1 or not io.write(clips[0].SetProperty,'AudioVolume',sound['gain_db']):
                raise RuntimeError('审片音效放置或音量设置失败')
        matte_layers=place_review_mattes(io,target,timeline,record,segments)
        write_json(evidence/'matte_layers.json',matte_layers)
        write_json(evidence/'planned_segments.json', segments)
        return finish_review(io, timeline, target, segments, before, evidence, output, name, audio_proof, sounds,
                             record.get('needs_user_decision', []),matte_layers,media.GetUniqueId(),record.get('matte_decisions',[]))
    except BaseException as exc:
        write_json(evidence/'failure.json', {'error': str(exc), 'timeline': name,
                                           'message': '保留现场，不删除时间线、不自动重试。'})
        raise
    finally:
        io.activate(original)
        io.write(project.GetMediaPool().SetCurrentFolder, original_folder)

def finish_review(io, timeline, target, segments, before, evidence, output, name, audio_proof=None, sounds=None, decisions=None, matte_layers=None, video_media_id=None, matte_decisions=None):
    """Verify the created timeline and produce evidence without inserting more clips."""
    resolve, project = io.r, io.p
    io.activate(target)
    readback = read_segments(target, segments, video_media_id or timeline.GetMediaPoolItem().GetUniqueId(),
                             audio_proof['media_id'] if audio_proof else None, sounds, matte_layers)
    write_json(evidence/'segments.json', {'segments': segments, 'readback': readback, 'markers': target.GetMarkers()})
    io.activate(timeline)
    frames = [(s['start']+s['end'])//2 for s in segments]
    captures = still(resolve, project, timeline, frames, evidence/'原线静帧')
    write_json(evidence/'stills.json', captures)
    for index, (segment, capture) in enumerate(zip(segments, captures['stills']), 1):
        image = evidence/f'第{index}处.png'
        run_ffmpeg(['-threads','4','-filter_threads','4','-i', capture['path'], '-vf', 'scale=960:-2', '-frames:v', '1','-threads','4'], image)
        segment['still'] = str(image)
    after = io.snapshot()
    new_ids = [target.GetUniqueId()]+([audio_proof['id']] if audio_proof else [])
    errors = ledger.verify_delta(before, after, new_timelines=new_ids)
    if not ledger.equal(before['timeline'], after['timeline']):
        errors.append('原时间线完整快照发生变化')
    write_json(evidence/'after.json', after)
    write_json(evidence/'delta.json', {'errors': errors, 'source_unchanged': not errors})
    if errors:
        raise RuntimeError('审片前后原线快照不一致：'+'；'.join(errors))
    duration = (segments[-1]['review_end']-target.GetStartFrame())/float(timeline.GetSetting('timelineFrameRate'))
    if target.GetEndFrame() != segments[-1]['review_end']:
        raise RuntimeError('审片总长读回不符')
    color_note = '颜色以原时间线为准（审片使用嵌套时间线，未逐帧验证色彩一致性）。'
    checklist(output, name, duration, segments, timeline.GetSetting('timelineFrameRate'),
              ';' in timeline.GetStartTimecode(), color_note)
    with output.open('a',encoding='utf-8') as f:
        if audio_proof:
            f.write('\n音频：A1「人声与原声」来自无音效副本；A2「音效」为本程序音效素材，按原位置和音量放置。\n')
        if matte_decisions:
            f.write('\n## 自动人物遮挡\n\n')
            for row in matte_decisions:
                ratio=row.get('coverage')
                f.write('- '+row['effect_id']+'：'+row['action']+'；覆盖 '+(f'{ratio:.2%}' if ratio is not None else '不适用')+'；'+row.get('reason','')+'\n')
        if decisions:
            f.write('\n## 需要用户决定（未写入）\n\n')
            for decision in decisions:
                f.write('- '+decision['effect_id']+'：'+'；'.join(decision['reasons'])+'\n')
    if not io.write(io.pm.SaveProject):
        raise RuntimeError('审片工程保存失败')
    result = {'timeline': name, 'timeline_id': target.GetUniqueId(), 'duration_seconds': duration,
              'segments': segments, 'readback': readback, 'checklist': str(output),
              'evidence': str(evidence), 'source_unchanged': True, 'saved': True, 'color_note': color_note}
    if matte_layers:
        result['matte_layers']=matte_layers
    if audio_proof:
        result['audio_split'] = {k:v for k,v in audio_proof.items() if k not in ('before','after')}
        result['audio_split'].update(sound_placements=sounds,a1_without_sfx=True,a2_only_owned_sfx=True)
    from .cleanup import register
    register(io,evidence.parents[1],target,'审片过程',seal=True)
    write_json(evidence/'result.json', result)
    return result


def matte_review_ranges(record, segments):
    result=[]
    for row in record.get('mattes',[]):
        state=row['written_state']
        for segment in segments:
            a,b=max(state['start'],segment['start']),min(state['end'],segment['end'])
            if a<b:
                start=segment['review_start']+a-segment['start']
                result.append({'item_id':row['item_id'],'start':start,'end':start+b-a,
                               'source_offset':int(a-state['start']), 'original_start':a,'original_end':b})
    return result


def place_review_mattes(io,target,source,record,segments):
    rows=matte_review_ranges(record,segments)
    if not rows:return []
    io.activate(source)
    items={i.GetUniqueId():i for track in range(1,source.GetTrackCount('video')+1)
           for i in source.GetItemListInTrack('video',track) or []}
    properties={row['item_id']:items[row['item_id']].GetProperty() for row in rows}
    io.activate(target)
    if not io.write(target.AddTrack,'video') or not io.write(target.SetTrackName,'video',2,matte.TRACK):
        raise RuntimeError('审片人物抠像轨创建失败')
    result=[]
    for row in rows:
        item=items[row['item_id']]
        if not item.GetClipEnabled():raise RuntimeError('审片人物抠像已被停用')
        media=item.GetMediaPoolItem()
        placed=io.write(io.p.GetMediaPool().AppendToTimeline,[{'mediaPoolItem':media,
                       'startFrame':item.GetLeftOffset()+row['source_offset'],
                       'endFrame':item.GetLeftOffset()+row['source_offset']+row['end']-row['start'],
                       'recordFrame':row['start'],'trackIndex':2,'mediaType':1}])
        if not placed or len(placed)!=1:raise RuntimeError('审片人物抠像放置失败')
        fg=placed[0]
        props=properties[row['item_id']]
        io.write(fg.SetProperty,'ZoomGang',False)
        for key,value in props.items():io.write(fg.SetProperty,key,value)
        if not item.CopyGrades([fg]):raise RuntimeError('审片人物抠像CopyGrades失败')
        if (fg.GetStart(),fg.GetEnd())!=(row['start'],row['end']) or not ledger.equal(fg.GetProperty(),props):
            raise RuntimeError('审片人物抠像参数读回不符')
        result.append({**row,'review_item_id':fg.GetUniqueId(),'track':2,'above_titles':True})
    live={i.GetUniqueId():i for i in target.GetItemListInTrack('video',2) or []}
    if set(live)!={r['review_item_id'] for r in result}:raise RuntimeError('审片人物抠像实际轨道不符')
    return result
