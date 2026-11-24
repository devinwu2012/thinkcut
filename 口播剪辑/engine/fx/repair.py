"""Small edits: one backup, selected controls/ranges, one position-only check."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from . import ledger
from .markers import reconcile, delivery, needs_refresh
from .resolve_io import objects
from .snapshot import safe, TEXT_KEYS, BACKGROUND_KEYS, WATCH_KEYS


def changes(plan, record):
    old={e['effect_id']:e for e in record.get('requested_plan',record.get('plan',{})).get('effects',[])}
    new={e['effect_id']:e for e in plan['effects']}
    if old.keys()!=new.keys():return None
    result=[]
    for uid,e in new.items():
        before=deepcopy(old[uid]);after=deepcopy(e)
        # These fields may change in a small repair, all other behavior stays identical.
        for row in (before,after):
            for key in ('text','start','end','layout_approved','precondition_snapshot_sha256','source_marker'):row.pop(key,None)
            row['anchor'].pop('center',None)
            row['style'].pop('size',None)
            for sound in row.get('sounds',[]):
                sound.pop('gain_db',None);sound.pop('start',None)
        if before!=after:return None
        fields=[]
        for key in ('text','start','end'):
            if e[key]!=old[uid][key]:fields.append(key)
        if e['anchor']['center']!=old[uid]['anchor']['center']:fields.append('center')
        if e['style']['size']!=old[uid]['style']['size']:fields.append('size')
        if [s.get('gain_db') for s in e['sounds']]!=[s.get('gain_db') for s in old[uid]['sounds']]:fields.append('gain_db')
        if fields:result.append((e,fields))
    return result if len(result)<=5 else None


def positions(timeline):
    return {i.GetUniqueId():(kind,n,i.GetStart(),i.GetEnd(),i.GetClipEnabled())
            for kind in ('video','audio','subtitle') for n in range(1,timeline.GetTrackCount(kind)+1)
            for i in timeline.GetItemListInTrack(kind,n) or []}


def read_selected(item):
    comp=item.GetFusionCompByIndex(1)
    tools=list((comp.GetToolList(False,'TextPlus') or {}).values()) if comp else []
    if len(tools)!=1:raise ValueError('小修对象没有唯一原生Text+')
    from .direct import assert_native
    assert_native(item)
    tool=tools[0];inputs=tool.GetInputList() or {};rs=comp.GetAttrs()['COMPN_RenderStart']
    size_input=next((v for v in inputs.values() if v.GetAttrs().get('INPS_ID')=='Size'),None)
    out=size_input.GetConnectedOutput() if size_input else None
    keys=out.GetTool().GetKeyFrames() if out else {}
    row=dict(name=item.GetName(),start=item.GetStart(),end=item.GetEnd(),props=item.GetProperty() or {},
             texts=[dict(inputs={k:tool.GetInput(k,rs) for k in TEXT_KEYS+WATCH_KEYS+BACKGROUND_KEYS},
                         comp_range={'COMPN_RenderStart':rs},size_keyframes=keys)])
    return ledger.title_state(safe(row)),tool,rs


def run_small(io, plan, record, work_dir, apply=False):
    changed=changes(plan,record)
    if changed is None:raise ValueError('小修只支持已有至多5处的文字、位置、时长、字号或音量；新做请用 --mode batch')
    t=io.main
    if record.get('deferred') or record.get('pending'):raise ValueError('未完成的批次（或以前在副本上做的旧记录）不能按小修处理')
    if apply:io.activate(t)
    elif io.p.GetCurrentTimeline().GetUniqueId()!=t.GetUniqueId():
        raise ValueError('小修演练前请打开目标主线')
    marker_timeline={'markers':safe(t.GetMarkers() or {})}
    marker_report=reconcile(plan,marker_timeline)
    result=dict(applied=False,mode='small',saved_closed_reopened=False,marker_report=marker_report,
                changed=[{'effect_id':e['effect_id'],'fields':fields,'start':e['start'],'end':e['end']} for e,fields in changed])
    if needs_refresh(marker_report):
        return {**result,'allowed':False,'message':'计划来源标记已变化或删除，先更新计划；未写入。'}
    owned={r['effect_id']:r for r in record['effects']}
    baseline=positions(t);live={i.GetUniqueId():i for i in objects(t)}
    for e,fields in changed:
        row=owned[e['effect_id']];position=baseline.get(row['item_id'])
        if not position or position[:2]!=('video',row['track']) or position[-1]!=row.get('enabled',True):
            raise ValueError('小修对象被移动、删除或启用状态改变，先确认：'+e['effect_id'])
        if t.GetIsTrackLocked('video',row['track']):raise ValueError('小修目标轨已锁定')
    # Resolve exposes no verified non-ripple trim setter here. Do not replace a batch to trim one title.
    ranges=[r for r in result['changed'] if set(r['fields']) & {'start','end'}]
    if ranges:
        return {**result,'needs_targeted_edit':ranges,
                'message':'起止小修需在剪辑页按清单修剪并读回；当前SDK路径没有已验证的非波纹修剪方法，未写入、未整批重建。'}
    if not apply or not changed:return result
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup=Path(work_dir)/'备份'/f'小修_{stamp}.drt';backup.parent.mkdir(parents=True,exist_ok=True)
    if not io.write(t.Export,str(backup),io.r.EXPORT_DRT) or not backup.is_file() or not backup.stat().st_size:
        raise RuntimeError('小修一次备份失败，未继续')
    updated=deepcopy(record)
    try:
        for e,fields in changed:
            row=next(r for r in updated['effects'] if r['effect_id']==e['effect_id'])
            item=live[row['item_id']];state,tool,rs=read_selected(item)
            if ledger.manual_fields({k:v for k,v in row['written_state'].items() if k in state},state):row['manual']=True
            mapping={'text':('StyledText',e['text']),'center':('Center',{1:e['anchor']['center'][0],2:e['anchor']['center'][1]}),
                     'size':('Size',e['style']['size'])}
            if 'size' in fields and state.get('SizeKeyframes'):
                raise ValueError('字号由既有关键帧驱动，请先明确如何处理动画；不清除用户关键帧')
            for field in fields:
                if field not in mapping:continue
                key,value=mapping[field]
                io.write(tool.SetInput,key,value)
                if not ledger.equal(safe(tool.GetInput(key,rs)),safe(value)):
                    raise RuntimeError('小修参数读回不符：'+e['effect_id']+'/'+key)
            if 'gain_db' in fields:
                sounds=[s for s in updated.get('sounds',[]) if s['effect_id']==e['effect_id']]
                if len(sounds)!=1 or sounds[0]['item_id'] not in live:
                    raise ValueError('小修音效对象不唯一或已删除：'+e['effect_id'])
                sound=sounds[0];audio=live[sound['item_id']]
                pos=baseline[sound['item_id']]
                if pos[0]!='audio' or pos[1]!=sound['written_state']['track'] or t.GetIsTrackLocked('audio',pos[1]):
                    raise ValueError('小修音效轨道变化或锁定')
                gain=e['sounds'][0].get('gain_db')
                if gain is None:
                    from .audio import analyze_sound, peak_gain
                    try:source=round(audio.GetLeftOffset(True))
                    except TypeError:source=round(audio.GetLeftOffset())
                    measured=analyze_sound(dict(path=audio.GetMediaPoolItem().GetClipProperty('File Path'),
                        source_start_frame=source,duration_frames=round(audio.GetDuration())),
                        Path(work_dir)/'音效起点缓存.json')
                    gain=peak_gain(measured['peak_dbfs'])
                if not io.write(audio.SetProperty,'AudioVolume',gain) or not ledger.equal(audio.GetProperty('AudioVolume'),gain):
                    raise RuntimeError('小修音量读回不符')
                sound['written_state']['AudioVolume']=gain
            row['written_state']=read_selected(item)[0];row['fingerprint']=ledger.digest(row['written_state']);row['effect']=deepcopy(e)
        if positions(t)!=baseline:raise RuntimeError('小修改变了其他片段位置或启用状态，停止并保留现场')
        if not ledger.equal(safe(t.GetMarkers() or {}),marker_timeline['markers']):raise RuntimeError('小修标记变化')
        if not io.write(io.pm.SaveProject):raise RuntimeError('小修保存失败')
        updated.update(plan=deepcopy(plan),requested_plan=deepcopy(plan),saved_closed_reopened=False,
                       backup=str(backup),updated_at=datetime.now().astimezone().isoformat())
        checklist,report=delivery(work_dir,plan,marker_timeline,updated)
        ledger.save(work_dir,updated)
        return {**result,'applied':True,'backup':str(backup),'checklist':checklist,'marker_report':report,
                'other_positions_unchanged':True,'saved':True}
    except BaseException as exc:
        from .snapshot import write_json
        write_json(Path(work_dir)/'小修失败.json',{'error':str(exc),'backup':str(backup),
                   'message':'保留现场，先定向排查，不重复写入。'})
        raise
