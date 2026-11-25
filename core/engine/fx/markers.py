"""Reconcile every current main-timeline marker, including name and full note."""
from pathlib import Path
from .snapshot import safe, write_json


def reconcile(plan, timeline, record=None):
    effects=plan.get('effects',[])
    live={str(int(float(k))):safe(v) for k,v in timeline.get('markers',{}).items()}
    written={r['effect_id']:r for r in (record or {}).get('effects',[])}
    rows=[]
    for key,marker in sorted(live.items(),key=lambda kv:int(kv[0])):
        linked=[e for e in effects if e.get('source_marker') and str(e['source_marker']['frame'])==key]
        stale=[e['effect_id'] for e in linked if e['source_marker'].get('name','')!=marker.get('name','')
               or e['source_marker']['note']!=marker.get('note','')]
        landed=[]
        for e in linked:
            actual=written.get(e['effect_id']);state=(actual or {}).get('written_state',{})
            sounds=[{'filename':s['filename'],'start':s['start'],'gain_db':s.get('gain_db','按选段峰值计算'),
                     'status':'计划'} for s in e.get('sounds',[])]
            if record is not None:
                sounds=[{'item_id':s['item_id'],'state':s.get('current_state',s.get('written_state')),
                         'placement':s.get('placement'),
                         'status':'保留手调/删除' if s.get('manual_fields') else '已写入'}
                        for s in record.get('sounds',[]) if s['effect_id']==e['effect_id']]
            landed.append({'effect_id':e['effect_id'],'text':state.get('StyledText',e['text']),
                           'start':state.get('start',e['start']),'end':state.get('end',e['end']),
                           'track':(actual or {}).get('track',e.get('track')),'sounds':sounds,
                           'status':'已写入' if actual else '未写入' if record is not None else '计划'})
        rows.append({'frame':int(key),'name':marker.get('name',''),'note':marker.get('note',''),
                     'status':'原文变化，需重新对照' if stale else '已对应计划' if linked else '未覆盖',
                     'stale_effect_ids':stale,'effects':landed})
    orphan=[e['effect_id'] for e in effects if e.get('source_marker') and str(e['source_marker']['frame']) not in live]
    return {'markers':rows,'uncovered_frames':[r['frame'] for r in rows if not r['effects']],
            'stale_effect_ids':[e for r in rows for e in r['stale_effect_ids']],
            'missing_marker_effect_ids':orphan}


def needs_refresh(report):
    return bool(report['stale_effect_ids'] or report['missing_marker_effect_ids'])


def delivery(work_dir, plan, timeline, record):
    report=reconcile(plan,timeline,record)
    write_json(Path(work_dir)/'标记对照.json',report)
    lines=['# 花字音效交付清单','', '范围均为绝对帧 [start,end)；未覆盖标记需另行处理。','']
    for row in report['markers']:
        lines += [f"- 标记 {row['frame']}：{row['name']} / {row['note']} → {row['status']}"]
        for e in row['effects']:
            lines += [f"  - {e['effect_id']}「{e['text']}」→ {e['status']}，V{e['track']} [{e['start']},{e['end']})；音效：{e['sounds']}"]
    for effect in plan.get('effects',[]):
        lines.append(f"- 花字 {effect['effect_id']}：{effect.get('timing_basis','按计划/用户指定')}；[{effect['start']},{effect['end']})")
    for sound in record.get('sounds',[]):
        if sound.get('placement'):
            p=sound['placement'];o=p['onset']
            lines.append(f"- 音效 {sound['effect_id']}：{o['method']}，源同步帧{o['sync_frame']} → 时间线{p['sync_timeline_frame']}；{p['volume_basis']}，增益{p['gain_db']} dB，选段输出峰值{p['output_peak_dbfs']:.3f} dBFS")
    if report['missing_marker_effect_ids']:lines += ['来源标记已不存在：'+ '、'.join(report['missing_marker_effect_ids'])]
    path=Path(work_dir)/'交付清单.md';path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return str(path),report
