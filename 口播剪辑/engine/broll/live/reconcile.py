# -*- coding: utf-8 -*-
"""对账：对照 B-roll 台账（AI 写过什么）和当前时间线，辨认用户手改，把台账基线推进到现状，并记下删除/替换/停用/裁短留下的保留位。

这是“用户手改过的不再改”要靠的操作台账的一部分，只动台账、不学偏好（学偏好的功能 2026-11-21 下线，见 docs/历史做法.md）。
diff_snapshots 原样来自以前的 learn.py：返回值里的 actions 仍用来判断“这条时间线有没有被手改过”。
"""
from copy import deepcopy
from pathlib import PurePath
import re
from ._util import digest, union, intersects, subtract
from .timeline import build_model, record_to_source, source_to_record
from .ledger import match_identity, learning_allowed, main_picture, _identity
from .arrange import visible_frames

_TRANSFORMS = ('ZoomX','ZoomY','Pan','Tilt','RotationAngle','Rotation','Rotate')


def _meta(catalog, spot):
    key = spot.get('segment')
    rows = spot.get('candidates',[])
    if not key and rows:
        key = rows[0].get('segment') if isinstance(rows[0],dict) else rows[0]
    parent=spot.get('parent_segment') or re.sub(r':\d+$','',key or '')
    meta = deepcopy(catalog.get(key) or next((v for k,v in sorted(catalog.items()) if v.get('parent_segment',k)==parent),{}))
    meta['segment'] = meta.get('parent_segment') or spot.get('parent_segment') or re.sub(r':\d+$','',key or '') or None
    return meta


def _edge(model, frame):
    kinds = []
    if frame in model['cuts']:
        kinds.append('cut')
    if any(frame in (l['start'],l['end']) for l in model['lines']):
        kinds.append('subtitle')
    if any(frame in r for r in model['picture_occupied']):
        kinds.append('user_picture')
    if any(frame in r for r in model['reserved']):
        kinds.append('reserved')
    anchors = sorted(set(model['cuts']) | {l[k] for l in model['lines'] for k in ('start','end')})
    nearest = min(anchors,key=lambda p:(abs(p-frame),p))
    return {'frame':frame,'kinds':kinds,'offset':frame-nearest}


def _relocate(item, before, after):
    sources = record_to_source(before,item['start'],item['end'])
    ranges = source_to_record(after,sources)
    if not ranges:
        return None
    if len(ranges) != 1:
        raise ValueError('AI位置映射成不连续区间，需要先确认口播重剪')
    expected = deepcopy(item)
    expected.update(start=ranges[0][0],end=ranges[0][1],duration=ranges[0][1]-ranges[0][0])
    # Leading host content removed by ripple also removes the corresponding
    # front of the B-roll source. Internal removals have no preference signal.
    current_sources = record_to_source(after,*ranges[0])
    front = current_sources[0]
    lost = 0
    for old in sources:
        if old['source']==front['source'] and old['start']<=front['start']<old['end']:
            lost += front['start']-old['start'];break
        lost += old['end']-old['start']
    if expected.get('source_start') is not None:
        expected['source_start'] += lost
    return expected


def _check_subtitles(before,after):
    if after['snapshot'].get('broll_extra',{}).get('subtitles_synced') is False:
        raise ValueError('字幕与V1不同步，停止核对手改（没有改台账，也没有写入）')
    if before['structure_fingerprint']==after['structure_fingerprint']:
        return
    for line in before['lines']:
        expected = source_to_record(after,record_to_source(before,line['start'],line['end']))
        if not expected:
            continue
        matching = [l for l in after['lines'] if l['text']==line['text'] and l['track']==line['track']]
        if not any([l['start'],l['end']] in expected for l in matching):
            raise ValueError('字幕与V1源帧不同步，停止核对手改（没有改台账，也没有写入）：'+line['text'])


def diff_snapshots(before_snapshot, after_snapshot, ledger, catalog=None, approve=False, fx_built=False, fx_record=None):
    """Return hand-edit actions, raw/A-roll changes, latest spot results and ledger.

    Never writes. `ledger` returned with the new baseline prevents replaying the
    same manual actions. Uncertainty >20% leaves the ledger where it was.
    """
    catalog = catalog or {}
    before = build_model(before_snapshot,ledger,fx_record=fx_record)
    after = build_model(after_snapshot,ledger,fx_record=fx_record)
    result = {'actions':[],'results':[],'aroll_reedit':[],'raw_changes':[],
              'uncertain':[],'update_allowed':True,'new_folders':[], 'ledger':deepcopy(ledger),
              'skipped':after['learning_skipped']}
    if not learning_allowed(after['snapshot']['name']):
        result.update(update_allowed=False,reason='审核/历史/逐句否决版默认不核对、不写入');return result
    if before['v1_source_fingerprint'] != after['v1_source_fingerprint']:
        raise ValueError('V1口播源身份改变，不是同谱系；请确认新项目')
    _check_subtitles(before,after)
    if before['structure_fingerprint'] != after['structure_fingerprint']:
        result['aroll_reedit'].append({'before':before['structure'],'after':after['structure']})
    own_before = [i for i in before['items'] if i['owner']=='user' and i['role']=='picture' and not i.get('learning_skip_reason')]
    added, uncertain_own, own_used = [], [], set()
    for i in sorted(after['items'], key=lambda i: (i['start'],i['end'])):
        if i['owner'] != 'user' or i['role'] != 'picture' or not i['enabled'] or i.get('learning_skip_reason'):
            continue
        cur = _identity(i)
        candidates = [n for n,old in enumerate(own_before) if n not in own_used
                      and _identity(old)['file_name']==cur['file_name']
                      and (_identity(old)['size'] is None or cur['size'] is None or _identity(old)['size']==cur['size'])
                      and (_identity(old)['source_start']==cur['source_start']
                           or (old['start'],old['end'],old['track'])==(i['start'],i['end'],i['track']))]
        exact = [n for n in candidates if (own_before[n]['start'],own_before[n]['end'],own_before[n]['track'])==(i['start'],i['end'],i['track'])]
        winners = exact or candidates
        if not winners:
            added.append(i)
        elif len(winners)==1:
            own_used.add(winners[0])
        else:
            uncertain_own.append(i)
    eligible = 0
    replacement_ids = set()
    for spot,entry in ledger['spots'].items():
        old = entry['S0']
        if not main_picture(entry) or entry.get('status')=='aroll_removed':
            continue
        eligible += 1
        expected = _relocate(old,before,after)
        if expected is None:
            result['ledger']['spots'][spot]['status']='aroll_removed'
            result['raw_changes'].append({'spot':spot,'reason':'所在口播源区间已删除，排除偏好'});continue
        if any(i.get('learning_skip_reason') and i['enabled'] and intersects((i['start'],i['end']),(expected['start'],expected['end'])) for i in after['items']):
            eligible-=1
            result['raw_changes'].append({'spot':spot,'reason':'与未能追溯的复合/Fusion片段重叠，本处不推导删换偏好'})
            continue
        # Every surviving baseline follows the same coordinate system, including
        # missing/uncertain/fx-owned spots when the timeline baseline advances.
        result['ledger']['spots'][spot]['S0']=deepcopy(expected)
        result['ledger']['spots'][spot]['placement']=deepcopy(expected)
        match = match_identity(old,after['items'])
        state = {'lineage':ledger['lineage'],'spot':spot,'text':entry['semantic']['text'],
                 'segment':entry.get('segment'),'status':'unreviewed','approved':False,'weight':0,
                 'creator':ledger['creator']}
        meta = _meta(catalog,entry)
        payload = {'spot':spot, 'text':entry['semantic']['text'],'segment':meta.get('segment'),
                   'description':meta.get('description',''), 'quality':deepcopy(meta.get('quality',{})),
                   'aspect':meta.get('aspect','unknown'), 'source':'user_edit',
                   'source_ranges':record_to_source(after,expected['start'],expected['end']),
                   'start':expected['start'],'end':expected['end']}
        payload['quality'].update({k:meta[k] for k in ('people','focus','subject') if k in meta})
        if match['status']=='uncertain':
            result['uncertain'].append(spot);continue
        local_actions = []
        cur = after['items'][match['index']] if match['status']=='matched' else None
        covering = [i for i in added if cur is None or i['track']>cur['track']]
        owned_ranges = union((max(i['start'],expected['start']),min(i['end'],expected['end'])) for i in covering
                             if intersects((i['start'],i['end']),(expected['start'],expected['end'])))
        replaced = sum(e-s for s,e in owned_ranges)/(expected['end']-expected['start'])>=0.5
        if cur is None or replaced:
            kind = 'replaced_own' if replaced else 'deleted'
            action = dict(payload,kind=kind)
            action['exposed'] = [r for r in visible_frames(after) if intersects((r['start'],r['end']),(expected['start'],expected['end']))]
            if kind=='replaced_own':
                replacement_ids.update(i['id'] for i in covering if intersects((i['start'],i['end']),(expected['start'],expected['end'])))
                action['materials']=[{'path':i['media_path'],'folder':str(PurePath(i['media_path']).parent),
                                      'media_type':i.get('media_type')} for i in added
                                     if intersects((i['start'],i['end']),(expected['start'],expected['end']))]
            if match_identity(old,before['items'])['status']!='missing' and entry.get('status')!=kind:
                if entry.get('origin')=='ai_legacy' and not meta.get('segment'):
                    action['quality_unavailable']=True
                local_actions.append(action)
            if cur:
                result['ledger']['spots'][spot]['S0']=deepcopy(cur)
                result['ledger']['spots'][spot]['placement']=deepcopy(cur)
            state['status']=kind
            reserve = {'lineage':ledger['lineage'],'source_ranges':payload['source_ranges'],
                       'reason':'她删除/替换过的AI位置','spot':spot}
            if reserve not in result['ledger']['reserved']:
                result['ledger']['reserved'].append(reserve)
        else:
            cur = after['items'][match['index']]
            if cur['owner']=='fx' or cur['role']=='overlay':
                result['ledger']['spots'][spot]['S0']=deepcopy(cur)
                result['ledger']['spots'][spot]['placement']=deepcopy(cur)
                result['ledger']['spots'][spot]['status']='fx_excluded'
                result['raw_changes'].append({'spot':spot,'reason':'fx认领，排除'});continue
            result['ledger']['spots'][spot]['S0']=deepcopy(cur)
            result['ledger']['spots'][spot]['placement']=deepcopy(cur)
            if old['enabled'] and not cur['enabled']:
                local_actions.append(dict(payload,kind='disabled'))
                reserve={'lineage':ledger['lineage'],'source_ranges':payload['source_ranges'],'reason':'disabled','spot':spot}
                if reserve not in result['ledger']['reserved']:result['ledger']['reserved'].append(reserve)
            if cur['duration'] != expected['duration']:
                kind='trimmed' if cur['duration']<expected['duration'] else 'extended'
                local_actions.append(dict(payload,kind=kind,delta_frames=cur['duration']-expected['duration'],
                                          duration=cur['duration'], edges=[_edge(after,cur['start']),_edge(after,cur['end'])]))
                if kind=='trimmed':
                    removed=subtract((expected['start'],expected['end']),[(cur['start'],cur['end'])])
                    ranges=[r for a,b in removed for r in record_to_source(after,a,b)]
                    if ranges:
                        local_actions[-1]['reserved_source_ranges']=ranges
                        reserve={'lineage':ledger['lineage'],'source_ranges':ranges,'reason':'trimmed','spot':spot}
                        if reserve not in result['ledger']['reserved']:result['ledger']['reserved'].append(reserve)
            elif (cur['start'],cur['end']) != (expected['start'],expected['end']):
                result['raw_changes'].append({'spot':spot,'before':expected,'after':cur,'reason':'未分类位置差异'})
            # The slip baseline is the source offset expected after ripple.
            old_offset=expected.get('broll_extra',{}).get('left_offset')
            if old_offset is not None:
                old_offset += expected.get('source_start',0)-old.get('source_start',0)
            else:
                old_offset=expected.get('source_start')
            new_offset=cur.get('broll_extra',{}).get('left_offset')
            if new_offset is None:
                new_offset=cur.get('source_start')
            if (cur['start'],cur['end'])==(expected['start'],expected['end']) and old_offset!=new_offset:
                local_actions.append(dict(payload,kind='slipped',before=old_offset,after=new_offset))
            changed = {k:{'before':old.get('props',{}).get(k),'after':cur.get('props',{}).get(k)}
                       for k in _TRANSFORMS if old.get('props',{}).get(k)!=cur.get('props',{}).get(k)}
            if changed:
                action=dict(payload,kind='reframed',changes=changed)
                if cur.get('props',{}).get('ZoomX') is not None and cur['props'].get('ZoomX')==cur['props'].get('ZoomY'):
                    action['zoom']=cur['props']['ZoomX']
                local_actions.append(action)
            raw_fields={k:{'before':old.get(k),'after':cur.get(k)} for k in ('name','track','enabled','props','broll_extra')
                        if old.get(k)!=cur.get(k)}
            if raw_fields:
                result['raw_changes'].append({'spot':spot,'fields':raw_fields})
            state['status']=('disabled' if not cur['enabled'] else 'modified' if local_actions or raw_fields or (cur['start'],cur['end'])!=(expected['start'],expected['end'])
                             else entry.get('status') if entry.get('status') in ('modified','replaced_own') else 'kept')
            if not local_actions and (approve or fx_built):
                if state['status']=='kept':state.update(approved=True,weight=0.3)
            elif not local_actions and entry.get('approved') and state['status']=='kept':
                state.update(approved=True,weight=0.3)
            if entry.get('pending_replacement') and state['status']=='kept':
                state.update(approved=True,weight=0.3)
        result['actions'].extend(local_actions)
        state['actions']=[a['kind'] for a in local_actions]
        state.update(description=payload['description'],quality=payload['quality'])
        result['results'].append(state)
        result['ledger']['spots'][spot].update(status=state['status'],approved=state['approved'])
        result['ledger']['spots'][spot].pop('pending_replacement',None)
    result['uncertain_rate']=len(result['uncertain'])/eligible if eligible else 0
    result['update_allowed']=result['uncertain_rate']<=0.2 and not uncertain_own
    if not result['update_allowed']:
        result['reason']='身份不确定超过20%或新增用户画面身份不确定，本次不更新台账，也不写入'
    for i in added:
        if i['id'] in replacement_ids:
            if not any(m.get('file',m.get('media_path'))==i['media_path'] for m in catalog.values()):result['new_folders'].append(str(PurePath(i['media_path']).parent))
            continue
        result['actions'].append({'kind':'user_added','source':'user_edit','spot':'own:'+digest({
            'file':PurePath(i['media_path']).name,'source_start':i['source_start'],'start':i['start']})[:16],
            'start':i['start'],'end':i['end'],'duration':i['duration'],
            'path':i['media_path'],'folder':str(PurePath(i['media_path']).parent),
            'media_type':i.get('media_type'),'text':''.join(l['text'] for l in after['lines']
                    if intersects((l['start'],l['end']),(i['start'],i['end']))),
            'edges':[_edge(after,i['start']),_edge(after,i['end'])],
            'aspect':i['broll_extra'].get('aspect','unknown'),
            'zoom':i.get('props',{}).get('ZoomX') if i.get('props',{}).get('ZoomX')==i.get('props',{}).get('ZoomY') else None})
        if not any(m.get('file',m.get('media_path'))==i['media_path'] for m in catalog.values()):
            result['new_folders'].append(str(PurePath(i['media_path']).parent))
    for i in after['items']:
        if i['type']!='video' or i['track']!=1 or i['enabled']:
            continue
        sources=record_to_source(after,i['start'],i['end'])
        old_ranges=source_to_record(before,sources)
        if any(v['type']=='video' and v['track']==1 and v['enabled'] and
               any(intersects((v['start'],v['end']),r) for r in old_ranges) for v in before['items']):
            result['actions'].append({'kind':'new_reserved','source':'user_edit','spot':'v1:'+digest(sources)[:16],
                                      'source_ranges':sources,'start':i['start'],'end':i['end'],'reason':'新停用V1'})
    old_markers=before['snapshot']['markers']
    for frame,marker in after['snapshot']['markers'].items():
        text=marker.get('note','')+' '+marker.get('name','')
        start=after['start']+int(frame);end=start+max(1,int(marker.get('duration',1)))
        if old_markers.get(frame)==marker or marker.get('customData','').startswith(('fx','broll')):
            continue
        if not re.search('画面|素材|镜头|B.?roll|留空|不铺|露脸',text,re.I) or ('花字' in text and not re.search('画面|素材|镜头|B.?roll',text,re.I)):
            continue
        overlapping=[r for r in result['results'] if any(intersects((start,end),v) for v in source_to_record(after,
                         ledger['spots'][r['spot']]['semantic']['source_ranges']))]
        for r in overlapping:
            result['actions'].append({'kind':'marker_note','source':'user_quote','spot':r['spot'],
                                      'text':text.strip(),'provenance':{'timeline':after['snapshot']['name'],'marker_frame':frame}})
    result['new_folders']=sorted(set(result['new_folders']))
    # Do not advance ambiguous baselines; caller may persist raw evidence only.
    if result['update_allowed']:
        result['ledger']['S0']=deepcopy(after['snapshot'])
    else:
        result['ledger']=deepcopy(ledger)
    return result
