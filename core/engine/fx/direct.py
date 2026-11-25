"""Native titles on dedicated main tracks; checkpoints never retry an insertion."""
from copy import deepcopy
from . import ledger
from .resolve_io import objects
from .snapshot import safe, frame_timecode, read_timeline


class NeedsTarget(RuntimeError):
    def __init__(self, track, name):
        self.track = track
        super().__init__(f'需要在剪辑页把视频源目标设到 V{track}（{name}），设好后运行 `build … --resume`；执行 AI 应用电脑操作菜单完成，不让用户手动做。')


def ordered(effects):
    return sorted(effects, key=lambda e: (e['start'], e['layer'], e['effect_id']))


def capture(t):
    return {'locks': [[k, n, bool(t.GetIsTrackLocked(k, n))]
                      for k in ('video', 'audio', 'subtitle')
                      for n in range(1, t.GetTrackCount(k)+1)],
            'marks': safe(t.GetMarkInOut()), 'playhead': t.GetCurrentTimecode(),
            'markers': safe(t.GetMarkers() or {})}


def restore_markers(io, state):
    t = io.main
    baseline = state['markers']; now = safe(t.GetMarkers() or {})
    for frame, value in now.items():
        if not ledger.equal(baseline.get(frame), value):
            if not io.write(t.DeleteMarkerAtFrame, float(frame)):
                raise RuntimeError('删除被推动标记失败：'+frame)
    for frame, value in baseline.items():
        if not ledger.equal(now.get(frame), value):
            if not io.write(t.AddMarker, float(frame), value['color'], value['name'],
                            value['note'], value['duration'], value.get('customData', '')):
                raise RuntimeError('还原标记失败：'+frame)
    if not ledger.equal(safe(t.GetMarkers() or {}), baseline):
        raise RuntimeError('标记原字段读回不符')
    for frame, value in baseline.items():
        if t.GetMarkerCustomData(float(frame)) != value.get('customData', ''):
            raise RuntimeError('标记 customData 读回不符：'+frame)


def restore(io, state):
    t = io.main
    restore_markers(io, state)
    original = {(k, n): v for k, n, v in state['locks']}
    for k in ('video', 'audio', 'subtitle'):
        for n in range(1, t.GetTrackCount(k)+1):
            value = original.get((k, n), False)
            if bool(t.GetIsTrackLocked(k, n)) != value:
                if not io.write(t.SetTrackLock, k, n, value):
                    raise RuntimeError('恢复锁状态失败')
            if bool(t.GetIsTrackLocked(k, n)) != value:
                raise RuntimeError('锁状态读回不符')
    if not io.write(t.ClearMarkInOut, 'all'):
        raise RuntimeError('清除临时入出点失败')
    for kind, marks in state['marks'].items():
        if marks and not io.write(t.SetMarkInOut, marks['in'], marks['out'], kind):
            raise RuntimeError('恢复入出点失败')
    if not ledger.equal(safe(t.GetMarkInOut()), state['marks']):
        raise RuntimeError('入出点恢复读回不符')
    io.seek(t, state['playhead'])


def lock_target(io, track):
    t = io.main
    for kind in ('video', 'audio', 'subtitle'):
        for n in range(1, t.GetTrackCount(kind)+1):
            value = not (kind == 'video' and n == track)
            if bool(t.GetIsTrackLocked(kind, n)) != value:
                if not io.write(t.SetTrackLock, kind, n, value):
                    raise RuntimeError('设置临时轨道锁失败')
            if bool(t.GetIsTrackLocked(kind, n)) != value:
                raise RuntimeError('临时轨道锁读回不符')


def insert(io, effect, track, claim):
    t = io.main; io.activate(t)
    if t.GetIsTrackLocked('video', track) or not t.GetIsTrackEnabled('video', track):
        raise ValueError('目标花字轨已锁定或停用')
    if any(i.GetEnd()>effect['start'] for i in t.GetItemListInTrack('video',track) or []):
        raise ValueError('目标轨插入点之后有片段，禁止会推移的原生插入；请换安全空轨')
    lock_target(io, track)
    previous = {i.GetUniqueId(): (i.GetStart(), i.GetEnd()) for i in objects(t)}
    start, end = effect['start'], effect['end']; base = int(t.GetStartFrame())
    marks = {'in': start-base, 'out': end-base-1}
    if not io.write(t.SetMarkInOut, marks['in'], marks['out'], 'all'):
        raise RuntimeError('设置花字入出点失败')
    if not all(v == marks for v in t.GetMarkInOut().values()):
        raise RuntimeError('花字入出点读回不符')
    io.seek(t, frame_timecode(start, t.GetSetting('timelineFrameRate'), ';' in t.GetStartTimecode()))
    item = io.write(t.InsertFusionTitleIntoTimeline, 'Text+')
    now = {i.GetUniqueId(): (i.GetStart(), i.GetEnd()) for i in objects(t)}
    if any(now.get(k) != v for k, v in previous.items()):
        raise RuntimeError('插入推动了既有片段，立即停止并保留现场')
    new = set(now)-set(previous)
    if not item:
        if new:
            raise RuntimeError('插入返回空但出现新片段，保留现场，不重试')
        raise NeedsTarget(track, t.GetTrackName('video', track))
    if new != {item.GetUniqueId()}:
        raise RuntimeError('插入新增片段数不符，保留现场')
    if item.GetUniqueId() not in {i.GetUniqueId() for i in t.GetItemListInTrack('video', track) or []}:
        # Only the just-returned native title is ours; never retry in this invocation.
        if not io.write(t.DeleteClips, [item], False):
            raise RuntimeError('新花字落错轨且清除失败，保留现场')
        if {i.GetUniqueId(): (i.GetStart(), i.GetEnd()) for i in objects(t)} != previous:
            raise RuntimeError('清除落错轨花字后现场不一致')
        raise NeedsTarget(track, t.GetTrackName('video', track))
    claim(item)  # Persist ownership before styling, including failures during style writes.
    if (item.GetStart(), item.GetEnd()) != (start, end):
        raise RuntimeError('花字起止读回不符，保留现场')
    assert_native(item)
    name = effect.get('name', effect['effect_id'])
    io.write(item.SetName, name); io.write(item.SetClipColor, 'Pink')
    if item.GetName() != name or item.GetClipColor() != 'Pink':
        raise RuntimeError('花字命名或颜色读回不符')
    io.style(item, effect)
    return item


def assert_native(item):
    media = item.GetMediaPoolItem()
    kind = str(media.GetClipProperty('Type') if media else '').lower()
    if any(s in kind for s in ('timeline', 'compound', '时间线', '复合')) or item.GetFusionCompCount() != 1:
        raise RuntimeError('本程序花字不是原生 Text+，禁止嵌套/复合片段')


def collect(io, rows, activate=True):
    """读回本程序花字的现状并做归属检查；activate=False 给演练用（不切时间线，调用方先确认它就是当前时间线）。"""
    if activate: io.activate(io.main)
    items = {i['id']: i for i in read_timeline(io.main)['items']}
    live = {i.GetUniqueId(): i for i in objects(io.main)}
    result = []
    for old in rows:
        item = items.get(old['item_id'])
        if item is None or item['track'] != old['track']:
            raise ValueError('本程序花字被删除或移轨：'+old['effect_id'])
        if item['enabled'] != old.get('enabled', True):
            raise ValueError('花字启用状态被手调，请先确认：'+old['effect_id'])
        assert_native(live[old['item_id']])
        state = ledger.title_state(item)
        if not str(state.get('StyledText') or '').strip():
            raise RuntimeError('本程序花字为空：'+old['effect_id'])
        result.append({**deepcopy(old), 'written_state': state, 'fingerprint': ledger.digest(state)})
    return result
