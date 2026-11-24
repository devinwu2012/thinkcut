"""Offline track allocation. Native Insert is allowed only on an empty tail."""
from .audio import is_sound


def requested_track(value, tracks, kind):
    if type(value) is int:
        index = value
    elif isinstance(value, str):
        matches = [t['index'] for t in tracks if t['type']==kind and t['name']==value]
        if len(matches)!=1:
            raise ValueError('指定轨道名称不存在或不唯一：'+value)
        index = matches[0]
    else:
        raise ValueError('track 必须是正整数轨号或已有轨道名')
    if not any(t['type']==kind and t['index']==index for t in tracks):
        raise ValueError('指定轨道不存在：'+str(index))
    return index


def title_tracks(timeline, effects, removed=()):
    tracks = [dict(t) for t in timeline['tracks'] if t['type']=='video']
    items = [i for i in timeline['items'] if i['type']=='video' and i['id'] not in removed]
    foreground={t['index'] for t in tracks if t['name']=='人物抠像'}
    # Empty high tracks do not raise the picture floor. Existing titles remain protected.
    assignments, created, automatic = {}, [], set()
    occupied = [(i['track'],i['start'],i['end']) for i in items]
    for e in sorted(effects,key=lambda e:(e['start'],e['layer'],e['effect_id'])):
        floor=max((i['track'] for i in items if i['start']<e['end'] and e['start']<i['end']
                   and i['track'] not in foreground),default=0)
        explicit = 'track' in e
        candidates = [requested_track(e['track'],tracks,'video')] if explicit else [
            t['index'] for t in sorted(tracks,key=lambda t:t['index']) if t['index']>floor and t['index'] not in foreground]
        def usable(n):
            t=next(t for t in tracks if t['index']==n)
            return (t['enabled'] and not t['locked'] and n>floor and n not in foreground
                    and not any(k==n and b>e['start'] for k,a,b in occupied))
        chosen = next((n for n in candidates if usable(n) and
                       (explicit or n in automatic or len(automatic)<2)),None)
        if chosen is None:
            if explicit:
                raise ValueError('指定花字轨锁定、停用、位于画面下方或有后续片段，不能安全插入：'+str(e['track']))
            if len(automatic)>=2:
                raise ValueError('最低两条可用花字轨仍无法安全放置；请调整计划或明确指定 track')
            if foreground:
                raise ValueError('人物抠像轨下没有安全可用的花字轨，停止，请协调轨道')
            chosen=max((t['index'] for t in tracks),default=0)+1
            names={t['name'] for t in tracks};number=1
            while ('花字'+(str(number) if number>1 else '')) in names:number+=1
            name='花字'+(str(number) if number>1 else '')
            tracks.append(dict(type='video',index=chosen,name=name,locked=False,enabled=True))
            created.append((chosen,name))
        if not explicit:automatic.add(chosen)
        assignments[e['effect_id']]=chosen
        occupied.append((chosen,e['start'],e['end']))
    return assignments, created


def audio_track(timeline, start, end, override=None, used=()):
    tracks = [t for t in timeline['tracks'] if t['type']=='audio']
    def usable(n):
        t=next(t for t in tracks if t['index']==n)
        rows=[i for i in timeline['items'] if i['type']=='audio' and i['track']==n]
        return (t['enabled'] and not t['locked'] and all(is_sound(i) for i in rows)
                and not any(i['start']<end and start<i['end'] for i in rows))
    if override is not None:
        n=requested_track(override,tracks,'audio')
        if not usable(n):raise ValueError('指定音效轨锁定、占用或混有人声/原声：'+str(override))
        return n, []
    for t in sorted(tracks,key=lambda t:t['index']):
        n=t['index']
        if n>=3 and usable(n) and (n in used or len(set(used))<2):return n, []
    if len(set(used))>=2:raise ValueError('两条音效轨仍有重叠，停止，不继续堆轨')
    count=max((t['index'] for t in tracks),default=0)
    # A1/A2 are left for voice/B-roll even on an empty timeline.
    names={t['name'] for t in tracks};created=[]
    for n in range(count+1,max(3,count+1)+1):
        if n<3:name='人声' if n==1 else 'B-roll 原声'     # 和配 B-roll、审核线用同一个名字（2026-11-13 审查：以前少一个空格，开 B-roll 原声时对不上、另建一条轨）
        else:
            number=1
            while ('音效'+(str(number) if number>1 else '')) in names:number+=1
            name='音效'+(str(number) if number>1 else '')
        created.append((n,name));names.add(name)
    return created[-1][0],created


def create_tracks(io, kind, requested):
    created=[]
    for expected,name in requested:
        actual,was_created=io.ensure_track(kind,name)
        if actual!=expected or not was_created:
            raise RuntimeError('轨道状态已改变，停止：'+name)
        created.append((kind,actual))
    return created
