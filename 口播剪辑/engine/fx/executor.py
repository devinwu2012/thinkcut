"""Dry-run first build: native titles and sounds placed directly on the main timeline."""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
from . import ledger, matte, direct
from .timing import prepare_timing, continuous_states
from .layout import prepare_layout
from .connect import find_timeline
from .merge import structure, fingerprint
from .plan import validate, config, color_errors, sound_rules, sfx_roots
from .safety import check_layout, with_default_sizes
from .audio import audio_preflight, sound_placement
from .markers import reconcile, delivery, needs_refresh
from .repair import changes as small_changes, run_small
from .tracks import title_tracks, audio_track, create_tracks
from .resolve_io import ResolveIO, objects
from .snapshot import snapshot, write_json
from .snapshot import TEXT_KEYS, BACKGROUND_KEYS
from .check import text_items


def track_name(layer, prefix='花字'):
    return prefix+(str(layer) if layer > 1 else '')


OVERRIDE_KEYS = (set(TEXT_KEYS) - {'GlobalIn','GlobalOut','StyledText','Size','Center'}) | set(BACKGROUND_KEYS) | {
    'SizeKeyframes', 'clip.Pan','clip.Tilt','clip.ZoomX','clip.ZoomY'}


def validate_execution(plan, timeline):
    errors = validate(plan)
    if errors:
        raise ValueError('；'.join(errors))
    if plan.get('status') != 'ready':
        errors.append('写入计划 status 必须是 ready')
    if plan.get('timeline', {}).get('name') != timeline.GetName():
        errors.append('计划与指定主时间线名称不一致')
    if plan.get('timeline', {}).get('start') != timeline.GetStartFrame():
        errors.append('计划与主时间线起点不一致')
    if abs(float(timeline.GetSetting('timelineFrameRate'))-29.97) > .01:
        errors.append('当前执行器音效帧数配置仅支持 29.97 fps')
    allowed = OVERRIDE_KEYS
    for e in plan.get('effects', []):
        if e['style'].get('engine','Text+') != 'Text+':
            errors.append('安全外框支持普通 Text，但当前写入引擎仍为 Text+，不能用 Text 计划执行 build')
        if any(e['anchor'].get(k,'center') != 'center' for k in ('horizontal_align','vertical_align')):
            errors.append('当前写入引擎只制作居中对齐；非居中计划可做外框估算，不能静默按居中写入')
        if e.get('replace_item_ids'):
            errors.append('只能替换操作记录认领的片段；replace_item_ids 必须为空')
        if any(a.get('input') != 'Size' for a in e.get('animation', [])):
            errors.append('执行器暂不制作 Center 跟踪动画；请人工处理，不会静默忽略')
        if set(e.get('overrides', {}))-allowed:
            errors.append('手调覆盖包含未支持的字段')
        if e.get('source_marker') and '不用音效' in e['source_marker']['note'] and e.get('sounds'):
            errors.append('来源标记要求不用音效')
    names=[e.get('name',e['effect_id']) for e in plan.get('effects',[])]
    if len(names)!=len(set(names)):
        errors.append('效果片段名必须唯一，建议使用 effect_id')
    if errors:
        raise ValueError('；'.join(errors))


def sound_files(plan):
    cfg = config('sfx_favorites.json')
    root = sfx_roots(cfg)[0]
    catalog = {s['filename']:s for s in cfg['favorites']}
    result = {}
    for e in plan['effects']:
        for sound in e['sounds']:
            name = sound['filename']
            if name not in result:
                found = list(root.rglob(name))
                if len(found) != 1 or not found[0].is_file():
                    raise ValueError('音效文件须唯一且可读取：'+name)
                found[0].resolve().relative_to(root)
                result[name] = {'path':str(found[0].resolve()), **catalog[name]}
    return result


def removal_plan(before, record):
    items = {i['id']:i for i in before['timeline']['items']}
    removed, preserved = [], []
    for old in record.get('sounds', []):
        current = items.get(old['item_id'])
        fields = ledger.changed_fields(old['written_state'], ledger.sound_state(current)) if current else ['deleted']
        if fields:
            preserved.append({**old, 'manual_fields':fields, 'current_state':ledger.sound_state(current) if current else None})
        else:
            removed.append(old['item_id'])
    matte_removed, _ = matte.removal(record, items)
    removed += matte_removed
    return removed, preserved


def placement_preflight(before, effects, removed):
    return title_tracks(before['timeline'], effects, removed)


def audio_placement_preflight(before, plan, files, removed, preserved, cache_path=None):
    # Plan every sound before touching titles, including numeric/name overrides.
    virtual=deepcopy(before['timeline'])
    virtual['items']=[i for i in virtual['items'] if i['id'] not in removed]
    kept={s['effect_id'] for s in preserved};used=set();placements=[]
    for e in sorted(plan['effects'],key=lambda e:e['start']):
        if e['effect_id'] in kept:continue
        for spec in e['sounds']:
            info=files[spec['filename']]
            placement=sound_placement(spec,info,cache_path,virtual['start'])
            start=placement['start']; end=start+placement['length']
            track,created=audio_track(virtual,start,end,spec.get('track'),used)
            for index,name in created:
                virtual['tracks'].append(dict(type='audio',index=index,name=name,enabled=True,locked=False))
            virtual['items'].append(dict(id='planned:'+e['effect_id'],type='audio',track=track,
                                         start=start,end=end,media_path=info['path']))
            if 'track' not in spec:used.add(track)
            placements.append(dict(effect_id=e['effect_id'],filename=spec['filename'],track=track,**placement))
    return placements


def place_media(io, plan, record, files, before, matte_rows=(), work_dir=None):
    removed, preserved = removal_plan(before, record)
    new_tracks, added, sounds = [], [], []
    wanted = {i.GetUniqueId():i for i in objects(io.main)}
    for uid in removed:
        if not io.write(io.main.DeleteClips,[wanted[uid]],False):
            raise RuntimeError('非波纹删除失败：'+uid)
    used_tracks = set()
    preserved_ids = {s['effect_id'] for s in preserved}
    for effect in sorted(plan['effects'],key=lambda e:e['start']):
        if effect['effect_id'] in preserved_ids: continue
        for spec in effect['sounds']:
            info = files[spec['filename']]
            if work_dir is None:raise ValueError('音效检测缓存需要本片 work_dir')
            placement=sound_placement(spec,info,Path(work_dir)/'音效起点缓存.json',io.main.GetStartFrame())
            start,length=placement['start'],placement['length']
            track, creations = audio_track(io.snapshot()['timeline'],start,start+length,
                                           spec.get('track'),used_tracks)
            new_tracks += create_tracks(io,'audio',creations)
            if 'track' not in spec:used_tracks.add(track)
            item = io.append(io.media(info['path']),'audio',track,start,placement['source_start'],length)
            io.write(item.SetProperty,'AudioVolume',placement['gain_db'])
            if not ledger.equal(item.GetProperty('AudioVolume'),placement['gain_db']):
                raise RuntimeError('音效音量读回不符')
            try:source_read=round(item.GetLeftOffset(True))
            except TypeError:source_read=round(item.GetLeftOffset())
            sync_read=item.GetStart()+placement['onset']['sync_frame']-source_read
            if source_read!=placement['source_start'] or sync_read!=placement['sync_timeline_frame']:
                raise RuntimeError('音效源入点/瞬态落点读回不符')
            added.append(item.GetUniqueId())
            sounds.append({'effect_id':effect['effect_id'],'item_id':item.GetUniqueId(),
                           'onset':placement['onset'],'effective_anchor':spec['start'],
                           'placement':placement,'sync_readback':sync_read})
    mattes, matte_tracks = matte.place(io, matte_rows)
    new_tracks += matte_tracks
    added += [m['item_id'] for m in mattes]
    after = io.snapshot()
    rows = {i['id']:i for i in after['timeline']['items']}
    for s in sounds:s['written_state']=ledger.sound_state(rows[s['item_id']])
    for m in mattes:m['written_state']=matte.state(rows[m['item_id']])
    # Keep the original AI fingerprint for edited/deleted audio across every rebuild.
    sounds += preserved
    return after, removed, added, new_tracks, sounds, preserved, mattes


def run(resolve, project, timeline, project_name, plan_path, work_dir, apply=False,
        allow_unsafe=False, resume=False, mode='auto', allow_sounds=False):
    if not apply:
        return _run(resolve, project, timeline, project_name, plan_path, work_dir, False, allow_unsafe, resume, mode, allow_sounds)
    with ledger.write_lock():      # 真写（整批、小修、续做）拿和 B-roll 同一把写入锁；演练不拿
        return _run(resolve, project, timeline, project_name, plan_path, work_dir, True, allow_unsafe, resume, mode, allow_sounds)


def _run(resolve, project, timeline, project_name, plan_path, work_dir, apply, allow_unsafe, resume, mode, allow_sounds=False):
    io = ResolveIO(resolve, project, timeline, project_name)
    plan_path, work_dir = Path(plan_path), Path(work_dir)
    plan = with_default_sizes(json.loads(plan_path.read_text(encoding='utf-8')))
    validate_execution(continuous_states(plan), timeline)
    record = ledger.load(work_dir)
    if record and (record['project_id'] != project.GetUniqueId() or record['database'] != io.db):
        raise ValueError('操作记录不属于当前工程和数据库')
    if record and (record.get('deferred') or (record.get('pending') or {}).get('defer_place')):
        # 2026-11-13 用户定：只在主线上直接做，“先在副本上做、审完再重放到主线”那条路去掉了
        raise ValueError('这是以前在主线副本上做花字留下的操作记录，这种做法已经去掉；请换一个新的工作目录，在主线上重新 build')
    ledger.refuse_legacy(record)    # 旧版嵌套/分解的记录：整批和小修都不接手
    if mode not in ('auto','small','batch'):raise ValueError('mode 必须是 auto/small/batch')
    if mode=='small' and resume:
        raise ValueError('small 仅用于当前主线build小修，不用于续做')
    if mode=='small' or (mode=='auto' and not resume and record and not record.get('pending')
                        and small_changes(plan,record) is not None):
        if not record or record.get('timeline_id')!=timeline.GetUniqueId():
            raise ValueError('小修需要当前主线的原生标题操作记录')
        return run_small(io,plan,record,work_dir,apply)
    before = io.snapshot() if apply else snapshot(resolve, project, timeline)
    marker_report = reconcile(plan,before['timeline'])
    if needs_refresh(marker_report):
        return {'applied':False,'allowed':False,'marker_report':marker_report,
                'message':'计划来源标记已变化或删除，请按当前名称和备注更新计划，未写入。'}
    if record and record['timeline_id'] != timeline.GetUniqueId():
        raise ValueError('目标时间线身份不同，请使用该主线自己的工作目录和计划')
    pending = (record or {}).get('pending')
    if bool(pending) != bool(resume):
        raise ValueError(('上次写入失败后还没恢复：先 fx.py recover 演练看现场，再 --apply，然后照常 build' if pending.get('status') != 'needs_target' else '存在未完成操作，请检查现场后用 --resume 续做') if pending else '没有可续做的断点')
    if pending:
        if (pending['input_digest'] != ledger.digest(plan) or pending['allow_unsafe'] != allow_unsafe
                or pending.get('allow_sounds', False) != allow_sounds):
            raise ValueError('续做的计划或安全选项变化，不能重复放置')
        if pending.get('status') != 'needs_target':
            raise ValueError('上次失败不是目标轨断点；保留现场，先检查执行证据，核对后用 fx.py recover 恢复（先演练，再 --apply）')
        target = find_timeline(project, pending['target_name'])
        io = ResolveIO(resolve, project, target, project_name)
        current = io.snapshot() if apply else snapshot(resolve, project, target)
        if ledger.verify_delta(pending['checkpoint'], current):
            raise ValueError('断点后时间线被改动，保留现场，不重复插入')
        if not apply:
            return {'applied':False, 'resume':True, 'completed':len(record['effects']),
                    'remaining':len(pending['plan']['effects'])-len(record['effects'])}
        return execute_direct(io, timeline, record, plan_path, work_dir)
    original_plan_digest = ledger.digest(plan)
    input_plan=deepcopy(plan)
    plan=continuous_states(prepare_timing(plan,before['timeline']))
    validate_execution(plan,timeline)
    preserved_titles = []; kept = {}
    if record:
        source = find_timeline(project, record['built_timeline'])
        if apply:
            source_io = ResolveIO(resolve, project, source, project_name)
            observed = direct.collect(source_io, record['effects'])
            io.activate(timeline)
        else:
            if project.GetCurrentTimeline().GetUniqueId() != source.GetUniqueId():
                raise ValueError('重建演练前请打开花字所在时间线，以便读取手调属性')
            # 演练和真写做同一套检查（被删、移轨、启用状态变了、不是原生 Text+、空字都在演练里报），只是不切换时间线
            observed = direct.collect(ResolveIO(resolve, project, source, project_name), record['effects'], activate=False)
        plan, preserved_titles = ledger.preserve_manual(plan, record,
            {e['effect_id']:e['written_state'] for e in observed})
        planned_ids = {e['effect_id'] for e in plan['effects']}
        old_by_id = {e['effect_id']:e for e in record['effects']}
        touched = lambda e: bool(old_by_id[e['effect_id']].get('manual') or ledger.manual_fields(
            old_by_id[e['effect_id']]['written_state'], e['written_state']))
        if any(e['effect_id'] not in planned_ids and touched(e) for e in observed):
            raise ValueError('计划移除了用户手调过的花字，请先确认，不删除')
        # 2026-11-13 台账 Q059（用户 2026-10-17：“只要是我改过的素材都不要再改了”）：手改过的花字整条留在原位，
        # 不删、不重放、不按计划改它；以前是全部删掉再按“计划＋抄回来的几个字段”重放，没抄到的手改就丢了。
        planned = {e['effect_id']:e for e in plan['effects']}
        for e in observed:
            if e['effect_id'] in planned and touched(e):
                p = planned[e['effect_id']]      # 这条不会被写，计划里只留程序认识的手调字段，免得校验报“未支持的字段”
                if p.get('overrides'): p['overrides'] = {k:v for k,v in p['overrides'].items() if k in OVERRIDE_KEYS}
                kept[e['effect_id']] = {**deepcopy(old_by_id[e['effect_id']]), 'written_state':deepcopy(e['written_state']),
                                        'fingerprint':ledger.digest(e['written_state']), 'manual':True,
                                        'effect':deepcopy(planned[e['effect_id']])}
        for r in preserved_titles:
            if r['effect_id'] in kept: r['message'] = '你手改过，整条留在原位没动：'+ '、'.join(r['fields'])
        preserved_titles += [{'effect_id':k, 'fields':[], 'message':'之前手改过，整条留在原位没动'}
                             for k in sorted(kept) if k not in {r['effect_id'] for r in preserved_titles}]
        validate_execution(plan, timeline)
    # 颜色只查计划要写进去的：手改后整条留在原位的花字（kept）不查、不拦
    bad_colors = color_errors(plan, kept)
    if bad_colors:
        raise ValueError('；'.join(bad_colors))
    plan=prepare_layout(plan,before['timeline'],sorted(set(kept) | {r['effect_id'] for r in preserved_titles
        if set(r['fields']) & {'Center','clip.Pan','clip.Tilt','clip.ZoomX','clip.ZoomY'}}))
    matting_enabled = config('fx_styles.json').get('matting', {}).get('enabled', True)
    safety = check_layout(plan, before['timeline'], allow_unsafe=allow_unsafe, check_person_bbox=not matting_enabled)
    requested_plan = deepcopy(plan)
    blocked = set(safety.get('blocked_effect_ids', [])) - set(kept)     # 留在原位的不因安全检查被拿出计划
    plan['effects'] = [e for e in plan['effects'] if e['effect_id'] not in blocked]
    if not plan['effects']:
        return {'applied':False, 'effects':0, 'safety':safety, 'marker_report':marker_report, 'needs_user_decision':safety.get('needs_user_decision', []), 'message':'所有效果需要用户决定，未写入。'}
    if not record or fingerprint(before['timeline']) != record['structure_fingerprint']:
        if any(e['precondition_snapshot_sha256'] != ledger.digest(before['timeline']) for e in plan['effects']):
            raise ValueError('计划前置快照已过期；请复核布局和计划')
    files = sound_files(plan)
    removal_record = record or {}
    placement_before = before
    audio_preflight(placement_before['timeline'], removal_record)
    title_ids = [e['item_id'] for e in removal_record.get('effects', []) if e['effect_id'] not in kept]
    media_removed, preserved_sounds = removal_plan(placement_before, removal_record)
    # 音效规矩（2 秒内不超过两声、同类事件同一声音）：用户动过、不会重放的音效不算；违规就停下等用户决定
    sound_report = sound_rules(plan, {s['effect_id'] for s in preserved_sounds})
    if not sound_report['valid']:
        if not allow_sounds:       # 音效放行单独一个开关（2026-11-13 用户定），--allow-unsafe 只管版面
            return {'applied':False, 'allowed':False, 'effects':len(plan['effects']), 'sound_rules':sound_report, 'safety':safety,
                    'marker_report':marker_report, 'needs_user_decision':safety.get('needs_user_decision', []),
                    'message':'音效需要用户决定，未写入：'+'；'.join(v['message'] for v in sound_report['violations'])+'。用户听过认可后才用 --allow-sounds 照做'}
        sound_report['forced'] = True
    audio_preview=audio_placement_preflight(placement_before,plan,files,media_removed,preserved_sounds,
                              work_dir/'音效起点缓存.json' if apply else None)
    try:
        placement_preflight(placement_before, [e for e in plan['effects'] if e['effect_id'] not in kept], title_ids+media_removed)
    except ValueError as exc:
        if kept: raise ValueError(f'{exc}。有 {len(kept)} 条手改过的花字留在原位（{"、".join(sorted(kept))}），其余的排不进去：改动少就用小修（--mode small），或给新花字写明 track；不会为了排版去动手改过的花字') from exc
        raise
    matte.preflight(placement_before, media_removed)
    _, manual_mattes = matte.removal(removal_record, {i['id']:i for i in placement_before['timeline']['items']})
    if manual_mattes:
        raise ValueError('人物层被手调或删除，请先确认，保留现场')
    version = (record or {}).get('version', 0)+1
    preview = {'command':'build', 'applied':False, 'project':project_name, 'timeline':timeline.GetName(),
               'version':version, 'placement_mode':'direct', 'effects':len(plan['effects']),
               'replace_owned_items':title_ids+media_removed, 'preserved_manual_titles':preserved_titles,
               'kept_manual_titles':sorted(kept),
               'preserved_manual_sounds':preserved_sounds,
               'safety':safety, 'skipped_effect_ids':sorted(blocked), 'needs_user_decision':safety.get('needs_user_decision', []),
               'matting_pending':matting_enabled and not apply, 'marker_report':marker_report,
               'sound_placements':audio_preview, 'sound_rules':sound_report, 'prepared_plan':requested_plan}
    if not apply:
        return preview
    work_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    evidence = work_dir/'执行证据'/stamp; evidence.mkdir(parents=True)
    write_json(evidence/'before.json', before); write_json(evidence/'intent.json', preview)
    backup = work_dir/'备份'/f'{stamp}.drt'; backup.parent.mkdir(exist_ok=True)
    if not io.write(timeline.Export, str(backup), resolve.EXPORT_DRT) or not backup.is_file() or not backup.stat().st_size:
        raise RuntimeError('主时间线 DRT 备份失败，未继续写入')
    if record:
        write_json(evidence/'previous_ledger.json', record)
    target = timeline
    target_before = io.snapshot()
    title_ids = [e['item_id'] for e in removal_record.get('effects', []) if e['effect_id'] not in kept]
    placement_preflight(target_before, [e for e in plan['effects'] if e['effect_id'] not in kept], title_ids+removal_plan(target_before, removal_record)[0])
    state = direct.capture(target)
    plan, decisions, matte_rows = matte.prepare(io, plan, target_before, work_dir,
        [r['effect_id'] for r in preserved_titles if set(r['fields']) & {'Center','clip.Pan','clip.Tilt','clip.ZoomX','clip.ZoomY'}]) if matting_enabled else (plan, [], [])
    check_layout(plan, target_before['timeline'], allow_unsafe=allow_unsafe, check_person_bbox=False)
    if preserved_titles:
        write_json(plan_path, requested_plan)
        original_plan_digest = ledger.digest(with_default_sizes(requested_plan))
        input_plan=deepcopy(requested_plan)
    progress = {'schema_version':3, 'placement_mode':'direct', 'project':project_name, 'project_id':io.pid,
                'database':io.db, 'timeline':timeline.GetName(), 'timeline_id':timeline.GetUniqueId(),
                'built_timeline':target.GetName(), 'version':version,
                'effects':[deepcopy(kept[k]) for k in sorted(kept)], 'sounds':[], 'mattes':[], 'plan':plan, 'requested_plan':requested_plan,'input_plan':input_plan,
                'structure':structure(before['timeline']), 'structure_fingerprint':fingerprint(before['timeline']),
                'baseline_title_ids':(record or {}).get('baseline_title_ids',[o['item']['id'] for o in text_items(before['timeline'])]),
                'baseline_sound_ids':(record or {}).get('baseline_sound_ids',[i['id'] for i in before['timeline']['items'] if i['type']=='audio']),
                'pending':{'command':'build', 'allow_unsafe':allow_unsafe, 'allow_sounds':allow_sounds,
                           'input_digest':original_plan_digest, 'target_name':target.GetName(),
                           'before':target_before,
                           'state':state, 'evidence':str(evidence), 'backup':str(backup), 'preview':preview,
                           'plan':plan, 'matte_rows':matte_rows, 'matte_decisions':decisions,
                           'removal_record':removal_record, 'removed_titles':title_ids, 'removed':[],
                           'tracks':[], 'track_map':{}, 'status':'inserting', 'deleted':False, 'kept':sorted(kept),
                           'prior_effects':(record or {}).get('effects', [])}}
    ledger.save(work_dir, progress)
    return execute_direct(io, timeline, progress, plan_path, work_dir)


def execute_direct(io, main, record, plan_path, work_dir):
    pending = record['pending']; plan = pending['plan']; evidence = Path(pending['evidence'])
    rows = record['effects']; target = io.main
    try:
        if not pending['deleted']:
            live = {i.GetUniqueId():i for i in objects(target)}
            for uid in pending['removed_titles']:
                if uid not in live:
                    raise ValueError('上一版花字已删除或移轨，停止：'+uid)
            if pending['removed_titles'] and not io.write(target.DeleteClips, [live[u] for u in pending['removed_titles']], False):
                raise RuntimeError('非波纹删除自有花字失败')
            pending['removed'] = list(pending['removed_titles']); pending['deleted'] = True
            track_map, creations = placement_preflight(io.snapshot(),[e for e in plan['effects'] if e['effect_id'] not in pending.get('kept',[])],[])
            pending['track_map'] = track_map
            pending['tracks'] += create_tracks(io,'video',creations)
            ledger.save(work_dir, record)
        done = {e['effect_id'] for e in rows}
        for effect in direct.ordered(plan['effects']):
            if effect['effect_id'] in done: continue
            track = pending['track_map'][effect['effect_id']]
            def claim(item):
                rows.append({'effect_id':effect['effect_id'], 'item_id':item.GetUniqueId(),
                             'track':track, 'enabled':item.GetClipEnabled(), 'source_timeline_id':target.GetUniqueId(), 'effect':deepcopy(effect)})
                ledger.save(work_dir, record)
            direct.restore(io, pending['state'])
            item = direct.insert(io, effect, track, claim)
            observed = direct.collect(io, [rows[-1]])[0]
            rows[-1].update(observed)
            # Every earlier title must retain all observed fields, not only its range.
            current = direct.collect(io, rows)
            if not ledger.equal([r['written_state'] for r in rows], [r['written_state'] for r in current]):
                raise RuntimeError('本次插入改变了此前花字，停止')
            ledger.save(work_dir, record)
        direct.restore(io, pending['state'])
        # The existing audio/matting writer remains direct, with no timeline sources passed in.
        audio_record = {k:pending['removal_record'].get(k, []) for k in ('sounds','mattes')}
        after, removed, added, tracks, sounds, preserved, mattes = place_media(
            io, plan, audio_record, sound_files(plan), io.snapshot(), pending['matte_rows'],work_dir)
        record['sounds'] = sounds; record['mattes'] = mattes
        pending['removed'] += removed; pending['tracks'] += tracks
        direct.restore(io, pending['state'])
        after = io.snapshot()
        all_added = [r['item_id'] for r in rows if r['effect_id'] not in pending.get('kept',[])]+added   # 留在原位的本来就在，不算新增
        errors = ledger.verify_delta(pending['before'], after, pending['removed'], all_added,
                                     new_tracks=pending['tracks'])
        write_json(evidence/'after.json', after); write_json(evidence/'delta.json', {'errors':errors})
        if errors: raise RuntimeError('直接放主线快照不符：'+'；'.join(errors))
        before_effects = direct.collect(io, rows)
        io.save_reopen()
        reopened_effects = direct.collect(io, rows)
        observed = lambda rr:[{k:v for k,v in row.items() if k != 'fingerprint'} for row in rr]
        if not ledger.equal(observed(before_effects), observed(reopened_effects)):
            raise RuntimeError('保存重开后花字文字、样式或位置变化')
        reopened = io.snapshot()
        if ledger.verify_delta(after, reopened): raise RuntimeError('保存重开后主线变化')
        write_json(evidence/'reopened.json', reopened); write_json(evidence/'reopened_effects.json', reopened_effects)
        record['effects'] = reopened_effects
        for row in record['sounds']:
            prior = next((e for e in pending['removal_record'].get('sounds', []) if e['effect_id']==row['effect_id']), {})
            row['ai_state'] = deepcopy(prior.get('ai_state', prior.get('written_state', row['written_state'])))
        for row in record['effects']:
            prior = next((e for e in pending['prior_effects'] if e['effect_id']==row['effect_id']), {})
            row['ai_state'] = deepcopy(prior.get('ai_state', prior.get('written_state', row['written_state'])))
        record.update(matte_decisions=pending['matte_decisions'], saved_closed_reopened=True,
                      needs_user_decision=pending['preview']['needs_user_decision'],
                      evidence=str(evidence), backup=pending['backup'],
                      updated_at=datetime.now().astimezone().isoformat())
        record['structure'] = structure(reopened['timeline'])
        record['structure_fingerprint'] = fingerprint(reopened['timeline'])
        checklist, marker_report = delivery(work_dir,record['requested_plan'],reopened['timeline'],record)
        record['marker_report']=marker_report
        del record['pending']; ledger.save(work_dir, record)
        return {**pending['preview'], 'applied':True, 'built_timeline':record['built_timeline'],
                'evidence':str(evidence), 'backup':pending['backup'], 'saved_closed_reopened':True,
                'all_text_nonempty':True,
                'checklist':checklist,'marker_report':marker_report}
    except BaseException as exc:
        pending['status'] = 'needs_target' if isinstance(exc, direct.NeedsTarget) else 'failed'
        pending['error'] = str(exc)
        write_json(evidence/'failure.json', {'error':str(exc), 'status':pending['status']})
        raise
    finally:
        if 'pending' in record:
            try:
                direct.restore(io, pending['state'])
                pending['checkpoint'] = io.snapshot()
            finally:
                ledger.save(work_dir, record)

