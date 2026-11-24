"""Clean only recorded AI-created timelines, with external DRT and dependency proof."""
from copy import deepcopy
from datetime import datetime
import hashlib
import io as binary_io
import json
from pathlib import Path
import zipfile
from . import ledger
from .resolve_io import ResolveIO, objects
from .snapshot import read_timeline, write_json

REGISTRY='AI时间线.json'


def drt_digest(path):
    """Ignore ZIP container timestamps, but no serialized content (including grades)."""
    data=Path(path).read_bytes()
    if zipfile.is_zipfile(binary_io.BytesIO(data)):
        with zipfile.ZipFile(binary_io.BytesIO(data)) as z:
            return ledger.digest([(i.filename,hashlib.sha256(z.read(i)).hexdigest())
                                  for i in sorted(z.infolist(),key=lambda i:i.filename)])
    return hashlib.sha256(data).hexdigest()


def export(io,timeline,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():raise FileExistsError('DRT目标已存在，不覆盖：'+str(path))
    if not io.write(timeline.Export,str(path),io.r.EXPORT_DRT) or not path.is_file() or not path.stat().st_size:
        raise RuntimeError('过程时间线DRT导出失败：'+timeline.GetName())
    return drt_digest(path)


def register(io,work_dir,timeline,purpose,baseline=None,seal=False,main_id=None):
    """Called only at actual creation; never infer ownership from timeline names."""
    work_dir=Path(work_dir);path=work_dir/REGISTRY
    registry=json.loads(path.read_text()) if path.exists() else dict(
        project_id=io.pid,database=io.db,main_id=main_id or io.main_id,timelines=[])
    if registry['project_id']!=io.pid or registry['database']!=io.db:
        raise ValueError('AI时间线记录不属于当前工程/数据库')
    uid=timeline.GetUniqueId()
    row=next((r for r in registry['timelines'] if r['id']==uid),None)
    if row and not seal:return
    if uid==registry['main_id']:raise ValueError('不能认领主时间线为过程线')
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    baseline_file=work_dir/'时间线外部备份'/f'{stamp}_{len(registry["timelines"])}.drt'
    # Claim the UID before export: a failed archive must not hide a newly created line.
    if row is None:
        row=dict(id=uid,name=timeline.GetName(),purpose=purpose,baseline=None,
                 drt=str(baseline_file),drt_digest='',sealed=False)
        registry['timelines'].append(row)
        write_json(path,registry)
    original=io.p.GetCurrentTimeline()
    try:
        io.activate(timeline)
        digest=export(io,timeline,baseline_file)
        state=deepcopy(baseline if baseline is not None else read_timeline(timeline))
        value=dict(id=uid,name=timeline.GetName(),purpose=purpose,baseline=state,
                   drt=str(baseline_file),drt_digest=digest,sealed=seal)
        row.update(value)
        write_json(path,registry)
    finally:
        if original:io.activate(original)


def references(timeline):
    """Check both audio and video nests, including disabled tracks/clips."""
    found=set()
    for item in objects(timeline):
        media=item.GetMediaPoolItem()
        if not media:continue
        kind=media.GetClipProperty('Type') or ''
        if 'compound' in kind.lower() or '复合' in kind or 'multicam' in kind.lower() or '多机位' in kind:
            raise ValueError('复合/多机位内部依赖无法可靠枚举，保留过程线：'+timeline.GetName())
        if kind in ('Timeline','时间线'):
            child=media.GetTimeline()
            if not child:raise ValueError('嵌套依赖无法读取：'+timeline.GetName())
            found.add(child.GetUniqueId())
    return found


def cleanup(resolve,project,main,project_name,work_dir,apply=False):
    ledger.refuse_legacy(ledger.load(work_dir))
    if not apply:return _cleanup(resolve,project,main,project_name,work_dir,False)
    with ledger.write_lock():return _cleanup(resolve,project,main,project_name,work_dir,True)   # 真删时间线：拿写入锁


def _cleanup(resolve,project,main,project_name,work_dir,apply):
    io=ResolveIO(resolve,project,main,project_name);work_dir=Path(work_dir)
    path=work_dir/REGISTRY
    if not path.exists():
        return dict(applied=False,candidates=[],deleted=[],message='没有本程序创建记录；不按名称猜测或删除任何时间线')
    registry=json.loads(path.read_text())
    if (registry['project_id']!=io.pid or registry['database']!=io.db or registry['main_id']!=io.main_id):
        raise ValueError('AI时间线记录与工程、数据库或主线不匹配')
    if (ledger.load(work_dir) or {}).get('pending'):raise ValueError('批次尚未完成，不能清理过程线')
    all_lines={t.GetUniqueId():t for t in (project.GetTimelineByIndex(n)
               for n in range(1,project.GetTimelineCount()+1))}
    original=project.GetCurrentTimeline();stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    evidence=work_dir/'时间线清理'/stamp;rows=[];states={};graph={};deleted=[]
    try:
        for uid,t in all_lines.items():
            io.activate(t);states[uid]=read_timeline(t);graph[uid]=references(t)
        for n,owned in enumerate(registry['timelines']):
            uid=owned['id']
            if uid not in all_lines:continue
            t=all_lines[uid];io.activate(t)
            backup=evidence/f'{n}.drt';digest=export(io,t,backup)
            reasons=[]
            if uid==io.main_id:reasons.append('主线绝不删除')
            original_backup=Path(owned.get('drt',''))
            if not original_backup.is_file() or drt_digest(original_backup)!=owned['drt_digest']:
                reasons.append('创建时DRT备份缺失或已变化')
            if not owned.get('sealed'):reasons.append('过程尚未完成，不能确认独有修改')
            if not ledger.equal(states[uid],owned['baseline']) or digest!=owned['drt_digest']:
                reasons.append('存在修改或DRT内容变化，无法确认没有独有修改')
            rows.append(dict(id=uid,name=t.GetName(),purpose=owned['purpose'],backup=str(backup),
                             reasons=reasons,drt_digest=digest))
        eligible={r['id'] for r in rows if not r['reasons']}
        # Any surviving user's line, changed process, or main protects all its dependencies.
        while True:
            referenced={child for uid,children in graph.items() if uid not in eligible for child in children}
            blocked=eligible & referenced
            if not blocked:break
            eligible-=blocked
            for row in rows:
                if row['id'] in blocked:row['reasons'].append('仍被保留时间线嵌套引用')
        for row in rows:row['eligible']=row['id'] in eligible
        result=dict(applied=False,candidates=rows,deleted=[],evidence=str(evidence),
                    message='演练已导出DRT；仅 --apply 删除已认领、无独有修改、无有效依赖的过程线')
        write_json(evidence/'演练.json',result)
        if not apply or not eligible:return result
        # Compare again after all exports, before the first deletion.
        for uid in all_lines:
            io.activate(all_lines[uid])
            if not ledger.equal(read_timeline(all_lines[uid]),states[uid]) or references(all_lines[uid])!=graph[uid]:
                raise RuntimeError('检查期间时间线发生变化，未开始删除')
        # Delete parents first; refuse cycles rather than guessing deletion semantics.
        remaining=set(eligible);order=[]
        while remaining:
            roots=sorted(uid for uid in remaining if not any(uid in graph[parent] for parent in remaining))
            if not roots:raise ValueError('候选过程线存在循环依赖，保留现场')
            order+=roots;remaining-=set(roots)
        io.activate(main)
        for uid in order:
            # Fresh reference scan catches any surviving dependency before each deletion.
            current={t.GetUniqueId():t for t in (project.GetTimelineByIndex(n)
                     for n in range(1,project.GetTimelineCount()+1))}
            if any(uid in references(t) for tid,t in current.items() if tid!=uid):
                raise RuntimeError('删除前发现新增嵌套引用，停止：'+uid)
            io.activate(current[uid])
            recheck=export(io,current[uid],evidence/f'删除前_{order.index(uid)}.drt')
            if recheck!=next(row['drt_digest'] for row in rows if row['id']==uid):
                raise RuntimeError('删除前DRT内容变化，停止：'+uid)
            io.activate(main)
            if not io.write(project.GetMediaPool().DeleteTimelines,[current[uid]]):
                raise RuntimeError('删除过程时间线失败，已导出DRT：'+uid)
            if any(project.GetTimelineByIndex(n).GetUniqueId()==uid for n in range(1,project.GetTimelineCount()+1)):
                raise RuntimeError('删除读回仍存在：'+uid)
            result['deleted'].append(uid);deleted.append(uid)
            write_json(evidence/'结果.json',result)
        for uid,t in all_lines.items():
            if uid in eligible:continue
            io.activate(t)
            if not ledger.equal(read_timeline(t),states[uid]):
                raise RuntimeError('保留时间线发生变化，停止并保留DRT')
        if not io.write(io.pm.SaveProject):raise RuntimeError('清理后保存失败，已导出DRT')
        result.update(applied=True,saved=True,surviving_timelines_unchanged=True)
        write_json(evidence/'结果.json',result)
        return result
    except BaseException as exc:
        write_json(evidence/'失败.json',dict(error=str(exc),candidates=rows))
        raise
    finally:
        if original and original.GetUniqueId() not in deleted:
            io.activate(original)
        else:io.activate(main)
