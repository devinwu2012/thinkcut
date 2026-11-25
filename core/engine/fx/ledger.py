"""Operation ownership, stable fingerprints and field-level manual preservation."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid
from .snapshot import safe, write_json

TRANSFORM = ('Pan', 'Tilt', 'ZoomX', 'ZoomY')
VOLATILE_SETTINGS = ('perfCacheClipsLocation',)


def digest(value):
    return hashlib.sha256(json.dumps(safe(value), sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def equal(a, b):
    if type(a) in (int, float) and type(b) in (int, float):
        return abs(a-b) < 1e-5
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


def title_state(item, text=None):
    if text is None and len(item.get('texts', [])) != 1:
        raise ValueError('所属花字必须恰有一个 Text+：'+item['name'])
    text = text if text is not None else item['texts'][0]
    inputs = deepcopy(text['inputs'])
    rs = text.get('comp_range', {}).get('COMPN_RenderStart', 0)
    # Animated first-frame Size is not the settled font size.
    keys = text.get('size_keyframes') or {}
    relative = {str(int(float(k)-rs)): v for k, v in keys.items()}
    if keys:
        last = max(keys, key=float)
        inputs['Size'] = keys[last].get('1', keys[last].get(1))
    inputs.pop('GlobalIn', None)
    inputs.pop('GlobalOut', None)
    inputs['SizeKeyframes'] = relative
    # 片段检查器里的全部属性都记（2026-11-13 台账 Q059）：以前只记平移和缩放，用户只改旋转、裁切、不透明度会被当成没动过。
    inputs.update({'clip.'+k: v for k, v in (item.get('props') or {}).items()})
    inputs.update({'clip.'+k: item.get('props', {}).get(k, 1 if k.startswith('Zoom') else 0)
                   for k in TRANSFORM})
    inputs.update(start=int(item['start']), end=int(item['end']))
    return safe(inputs)


def sound_state(item):
    return {k: item.get(k) for k in ('start', 'end', 'media_path', 'source_start', 'source_end',
                                    'track', 'enabled')} | {'AudioVolume': item.get('props', {}).get('AudioVolume')}


def changed_fields(old, current):
    return [k for k in sorted(set(old) | set(current)) if not equal(old.get(k), current.get(k))]


def manual_fields(written, current):
    """用户动过哪些字段：只比上次写入时记下的那些字段（以后多记字段，老记录不会被整批误判成手改）。"""
    # 两边都有的字段才比：小修和整批读回的字段集合不一样（底框那几项），缺的不算用户改的
    return [k for k in sorted(written) if k in current and not equal(written[k], current[k])]


def preserve_manual(plan, ledger, current):
    """current maps effect_id to observed state or None; never silently resurrect deletions."""
    result = deepcopy(plan)
    reports = []
    previous = {e['effect_id']: e for e in ledger.get('effects', [])}
    for effect in result['effects']:
        old = previous.get(effect['effect_id'])
        if not old:
            continue
        state = current.get(effect['effect_id'])
        if state is None:
            raise ValueError('上一版花字被删除或无法唯一识别，请先确认计划：'+effect['effect_id'])
        fields = manual_fields(old['written_state'], state)     # 只比上次记下的字段，老记录不会因为现在多记了字段被误判
        for field in fields:
            value = deepcopy(state[field])
            if field in ('start', 'end'):
                effect[field] = value
            elif field == 'StyledText':
                effect['text'] = value
            elif field == 'Size':
                effect['style']['size'] = value
            elif field == 'Center':
                effect['anchor']['center'] = [value['1'], value['2']]
            else:
                effect.setdefault('overrides', {})[field] = value
        if fields:
            for sound in effect['sounds']:
                sound['start'] = effect['start']
            reports.append({'effect_id': effect['effect_id'], 'fields': fields,
                            'message': '保留了你的手调：'+ '、'.join(fields)})
    return result, reports


def load(work_dir):
    path = Path(work_dir)/'操作记录.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def save(work_dir, record):
    path = Path(work_dir)/'操作记录.json'
    # 每次一个不重名的临时文件再原子替换：两个进程同时存，不会互相写坏对方的半个文件
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        write_json(tmp, record)
        os.replace(tmp, path)
    finally:
        if tmp.exists(): tmp.unlink()


LOCK_PATH = None    # 默认和 B-roll 同一把：环境变量 THINKCUT_RESOLVE_LOCK，没设就是 ~/.cache/ai-talking-head-edit/resolve-write.lock（测试里换成临时文件）


def write_lock():
    """花字真写达芬奇时拿的写入锁，和 B-roll 是同一把；拿不到就停（另一处正在写达芬奇）。"""
    from engine.broll.live.workspace import write_lock as lock
    return lock(LOCK_PATH)


LEGACY = '这是旧版做法留下的操作记录，已不支持；请换新工作目录在主线上重新 build'


def refuse_legacy(record):
    """旧版（花字装在嵌套时间线里、用户分解后核对）的记录一律不接手：build、小修、recover、cleanup、review 都停下。"""
    if record and (record.get('placement_mode') != 'direct' or record.get('nested') or record.get('decomposed')):
        raise ValueError(LEGACY)


def comparable_item(item):
    """Resolve unloads zero-valued controls of disabled shading elements on reopen.

    Keep raw snapshots. Only normalize None/zero for those inactive controls;
    enabled controls and any nonzero dormant value still compare strictly.
    """
    result=deepcopy(item)
    for text in result.get('texts',[]):
        inputs=text['inputs']
        for key,value in list(inputs.items()):
            if (key[-1:] in ('1','2','3') and key[:-1] in
                ('Red','Green','Blue','SoftnessX','SoftnessY','SoftnessGlow')
                and inputs.get('Enabled'+key[-1])==0 and value in (None,0)):
                inputs[key]=None
    if result.get('nested'):
        result['nested']['items']=[comparable_item(i) for i in result['nested']['items']]
    return result


def verify_delta(before, after, removed=(), added=(), new_timelines=(), new_tracks=()):
    """Check every existing clip/marker/track, excluding only explicit owned replacements."""
    b, a = before['timeline'], after['timeline']
    bi = {i['id']: i for i in b['items']}
    ai = {i['id']: i for i in a['items']}
    removed, added = set(removed), set(added)
    errors = []
    if set(bi)-set(ai) != removed:
        errors.append('删除片段与计划不符')
    if set(ai)-set(bi) != added:
        errors.append('新增片段与计划不符')
    for uid in set(bi) & set(ai):
        if not equal(comparable_item(bi[uid]), comparable_item(ai[uid])):
            errors.append('既有片段变化：'+uid)
    if not equal(b['markers'], a['markers']):
        errors.append('主时间线标记变化')
    bt = {(t['type'], t['index']): t for t in b['tracks']}
    at = {(t['type'], t['index']): t for t in a['tracks']}
    if set(at)-set(bt) != set(map(tuple, new_tracks)) or set(bt)-set(at):
        errors.append('新增/删除轨道与计划不符')
    if any(not equal(t, at.get(k)) for k, t in bt.items()):
        errors.append('既有轨道变化')
    for k in ('start', 'fps', 'width', 'height', 'settings', 'mark_in_out'):
        x, y = b.get(k), a.get(k)
        if k == 'settings':     # 达芬奇自己会改的设置不算（2026-11-13 实测：新工程第一次保存重开，缓存位置被改成默认值，整批写入被误判失败）
            x, y = ({n: v for n, v in (s or {}).items() if n not in VOLATILE_SETTINGS} for s in (x, y))
        if not equal(x, y):
            errors.append('时间线设置变化：'+k)
    old = {t['id']: t for t in before['timelines']}
    new = {t['id']: t for t in after['timelines']}
    if set(new)-set(old) != set(new_timelines) or any(new.get(k) != v for k, v in old.items()):
        errors.append('时间线数量或身份变化与计划不符')
    return errors
