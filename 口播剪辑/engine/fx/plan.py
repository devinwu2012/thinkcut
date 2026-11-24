"""Validate the stage-one plan using repository style / sound names, offline."""
import json
import math
import os
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1]/'config'


def program():
    return CONFIG.parent.parent.parent      # 程序目录：CONFIG＝本仓库/engine/config，往上三级


def legacy_styles():
    """老花字计划里的样式名 → 现在的名字：bin/旧名兼容.sh 的 THINKCUT_LEGACY_STYLES（从改名前升级来的安装才有；干净版是空表）。"""
    import sys; sys.path.append(str(program()/'bin')); import cutdesk_config  # noqa: F401  导入时把旧名兼容表读进环境变量
    return dict(x.split('=', 1) for x in os.environ.get('THINKCUT_LEGACY_STYLES', '').split() if '=' in x)


class Styles(dict):
    """fx_styles.json 的样式表：片子里已有计划、台账写的老样式名也查得到（就是新名字那一条）；列出来只有新名字。"""
    def __init__(self, data, aliases=None):
        super().__init__(data); self.aliases = {k: v for k, v in (aliases or {}).items() if v in data}
    def __missing__(self, key):
        if key in self.aliases: return self[self.aliases[key]]
        raise KeyError(key)
    def __contains__(self, key): return dict.__contains__(self, key) or key in self.aliases
    def get(self, key, default=None): return self[key] if key in self else default


def config(name):
    data = json.loads((CONFIG/name).read_text(encoding='utf-8'))
    if name == 'fx_styles.json': data['styles'] = Styles(data['styles'], legacy_styles())
    if name == 'sfx_favorites.json' and not Path(data['root']).is_absolute(): data['root'] = str(program()/data['root'])   # 2026-11-24 起写相对程序目录
    return data


def sfx_roots(cfg):
    """音效素材根目录，第一个是这台 Mac 上实际去找文件的。环境变量优先；否则用 sfx_favorites.json 里写的 root。
    写的那个不在（NAS 换了挂载名之类）、而按本仓库位置推出来的 <程序目录>/素材库/音效素材 在时，用推出来的；
    写的那个排第二，只用来认“这个片段是不是音效库里的”。"""
    if cfg['root_env'] in os.environ:
        return [Path(os.environ[cfg['root_env']]).resolve()]
    written = Path(cfg['root']); written = (written if written.is_absolute() else program()/written).resolve()   # 2026-11-24 起收藏表里写相对程序目录的 素材库/音效素材
    located = (program()/'素材库'/'音效素材').resolve()
    return [located, written] if located != written and located.is_dir() and not written.is_dir() else [written]


def validate(plan):
    errors=[]
    def need(ok, message):
        if not ok: errors.append(message)
    def string(v): return isinstance(v,str) and bool(v.strip())
    def number(v): return type(v) in (int,float) and math.isfinite(v)
    def interval(s,e): return type(s) is int and type(e) is int and s<e
    def vector(v,n): return isinstance(v,list) and len(v)==n and all(number(x) and 0<=x<=1 for x in v)
    if not isinstance(plan,dict): return ['计划必须是 JSON 对象']
    need(type(plan.get('schema_version')) is int and plan['schema_version']==1, 'schema_version 必须为整数 1')
    need(plan.get('status') in ('draft','ready'), 'status 必须为 draft 或 ready')
    tl=plan.get('timeline');tl=tl if isinstance(tl,dict) else {}
    need(string(tl.get('name')), '时间线 name 不能为空；按名称定位，不需要固定 ID')
    valid_tl=interval(tl.get('start'),tl.get('end'));need(valid_tl,'时间线 start/end 必须是整数帧有效区间（终点不包含）')
    effects=plan.get('effects')
    if not isinstance(effects,list) or not effects: return errors+['effects 必须是非空数组']
    styles_cfg=config('fx_styles.json');profiles=styles_cfg['styles'];colors=color_table(styles_cfg);sounds={s['filename'] for s in config('sfx_favorites.json')['favorites']}
    ids=set();layers={}
    for n,e in enumerate(effects):
        tag=f'效果 {n+1}：'
        if not isinstance(e,dict): need(False,tag+'必须是对象');continue
        uid=e.get('effect_id');need(string(uid) and uid not in ids,tag+'effect_id 为空或重复');ids.add(str(uid))
        need(string(e.get('group_id')),tag+'group_id 不能为空')
        need(e.get('kind') in ('keyword','lens_label','photo_label','compare_label','spec','chapter'),tag+'kind 无效')
        need(string(e.get('text')),tag+'文字不能为空')
        s,z=e.get('start'),e.get('end');ok=interval(s,z);need(ok,tag+'start/end 必须是整数帧有效区间')
        if ok and valid_tl: need(tl['start']<=s<z<=tl['end'],tag+'范围超出时间线')
        layer=e.get('layer');good_layer=type(layer) is int and layer>=1;need(good_layer,tag+'layer 必须是从 1 开始的整数')
        if good_layer and ok: layers.setdefault(layer,[]).append((s,z,uid))
        marker=e.get('source_marker')
        need('source_marker' in e and (marker is None or isinstance(marker,dict) and type(marker.get('frame')) is int and isinstance(marker.get('note'),str)), tag+'source_marker 需为 null 或 {frame: 标记相对帧, note: 备注原文}')
        if isinstance(marker,dict):need(isinstance(marker.get('name',''),str),tag+'source_marker.name须保留名称原文')
        style=e.get('style');style=style if isinstance(style,dict) else {}
        need(isinstance(style.get('profile'),str) and style['profile'] in profiles,tag+'样式必须来自 fx_styles.json')
        need('color' not in style or isinstance(style['color'],str) and style['color'] in colors,tag+'style.color 只能写 fx_styles.json 里登记的颜色名：'+'、'.join(colors))
        need(type(style.get('shadow_enabled',False)) is bool,tag+'shadow_enabled须为布尔值')
        need(type(style.get('static',False)) is bool,tag+'static须为布尔值（true＝用户明确要静态，不加淡入淡出）')
        motion=e.get('motion',{});need(isinstance(motion,dict) and set(motion)<={'in','out'} and all(type(v) is bool for v in motion.values()),tag+'motion 只能写 in/out 布尔值')
        engine=style.get('engine','Text+')
        need(engine in ('Text+','Text'),tag+'engine 必须是 Text+ 或 Text')
        need('size' not in style or number(style.get('size')) and style['size']>0 and (engine=='Text' or style['size']<1),tag+'Text+ 字号必须在 0 与 1 之间；Text 字号须为正数；省略时用样式默认字号')
        anchor=e.get('anchor');anchor=anchor if isinstance(anchor,dict) else {}
        need(string(anchor.get('subject')),tag+'缺少位置参照主体')
        need(anchor.get('coordinate_system')=='frame_normalized_bottom_left',tag+'坐标系必须为 frame_normalized_bottom_left')
        need(vector(anchor.get('center'),2),tag+'center 必须是两项 0–1 坐标')
        need(type(anchor.get('overlap_ok',False)) is bool,tag+'overlap_ok 必须是布尔值')
        need(anchor.get('subject_kind') in (None,'person','phone','photo','object'),tag+'subject_kind 无效')
        need(anchor.get('horizontal_align','center') in ('left','center','right'),tag+'horizontal_align 无效')
        need(anchor.get('vertical_align','center') in ('bottom','center','top','baseline'),tag+'vertical_align 无效')
        box=anchor.get('subject_bbox');valid_box=vector(box,4) and box[0]<box[2] and box[1]<box[3]
        need('subject_bbox' in anchor and (box is None or valid_box),tag+'subject_bbox 无效')
        obstacles=anchor.get('avoid_bboxes',[])
        need(isinstance(obstacles,list) and all(vector(b,4) and b[0]<b[2] and b[1]<b[3] for b in obstacles),tag+'avoid_bboxes 必须是真实手机/照片等避让外框数组')
        replacements=e.get('replace_item_ids');need(isinstance(replacements,list) and all(string(v) for v in replacements),tag+'replace_item_ids 必须为字符串数组')
        if plan.get('status')=='ready':
            need(valid_box and e.get('layout_approved') is True,tag+'ready 计划需要主体框和布局审核')
            digest=e.get('precondition_snapshot_sha256');need(isinstance(digest,str) and len(digest)==64 and all(c in '0123456789abcdef' for c in digest),tag+'ready 计划缺少快照 SHA-256')
        for field in ('animation','sounds'):
            values=e.get(field);need(isinstance(values,list),tag+field+' 必须是数组')
            if not isinstance(values,list):continue
            if field=='sounds':need(len(values)<=1,tag+'每个效果最多一个音效')
            for value in values:
                if not isinstance(value,dict):need(False,tag+field+' 条目必须是对象');continue
                if field=='animation':
                    need(value.get('input') in ('Size','Center'),tag+'动画只支持 Size / Center')
                    f=value.get('frame');need(type(f) is int and ok and s<=f<z,tag+'动画帧越界')
                    v=value.get('value');need(number(v) and v>0 if value.get('input')=='Size' else vector(v,2),tag+'动画值无效')
                else:
                    need(isinstance(value.get('filename'),str) and value['filename'] in sounds,tag+'音效 filename 必须来自 sfx_favorites.json')
                    f=value.get('start');need(type(f) is int and ok and f==s,tag+'音效须从花字起点开始')
                    if 'track' in value:need((type(value['track']) is int and value['track']>0) or string(value['track']),tag+'音效track须为正整数轨号或轨道名')
                    need(type(value.get('lead_frames',0)) is int and value.get('lead_frames',0)>=0,tag+'lead_frames须为非负整数')
                    if 'sync_frame' in value:need(type(value['sync_frame']) is int and value['sync_frame']>=0,tag+'sync_frame须为非负整数源帧')
                    g=value.get('gain_db',-5);need(number(g) and -60<=g<=6,tag+'音量必须在 -60 至 6 dB')
                    if 'event' in value:need(string(value['event']),tag+'音效 event 须为非空文字（这一声属于哪类事件）')
        if 'placement' in anchor:need(anchor['placement'] in ('beside_person','explicit','subtitle_replacement','top_center'),tag+'placement无效')
        if 'speech' in e:need(isinstance(e['speech'],dict) and e['speech'].get('mode','spoken') in ('spoken','visual'),tag+'speech无效')
        if 'track' in e:need((type(e['track']) is int and e['track']>0) or string(e['track']),tag+'track 必须是正整数轨号或轨道名')
    for layer,items in layers.items():
        ordered=sorted(items)
        for i,left in enumerate(ordered):
            for right in ordered[i+1:]:
                if right[0]>=left[1]:break
                need(False,f'layer {layer} 内效果 {left[2]} 与 {right[2]} 时间重叠')
    for n,left in enumerate(effects):
        for right in effects[n+1:]:
            if (isinstance(left,dict) and isinstance(right,dict) and 'track' in left
                    and left.get('track')==right.get('track')
                    and interval(left.get('start'),left.get('end')) and interval(right.get('start'),right.get('end'))
                    and left['start']<right['end'] and right['start']<left['end']):
                need(False,f"同轨标题不能重叠：{left.get('effect_id')} / {right.get('effect_id')}")
    from .compare import validate_comparisons
    errors.extend(validate_comparisons(effects))
    return errors


COLOR_TOLERANCE = 0.002     # 小于 1/255 的一半：只容浮点误差，差一级色阶就不算同一个颜色
SOUND_WINDOW_FRAMES = 60    # 2 秒（29.97 fps，执行器只支持这个帧率）


DEFAULT_COLORS = {'white': [1, 1, 1], 'accent': [100/255, 169/255, 232/255]}   # 白、#64A9E8；配置里没登记时也只认这两个


def color_table(cfg=None):
    return {k: v for k, v in (cfg or config('fx_styles.json')).get('colors', {}).items() if isinstance(v, list)} or DEFAULT_COLORS


def color_errors(plan, skip=()):
    """计划要写进去的文字颜色只能是白或 #64A9E8（fx_styles.json 的 colors）。

    只查计划指定的颜色：style.color、样式自带色、overrides 里的 Red1/Green1/Blue1（文字是着色元素 1）。
    skip 是用户在达芬奇里手改过、整条留在原位的花字，它们的颜色不是计划写的，不查、不拦。"""
    cfg = config('fx_styles.json'); allowed = color_table(cfg); errors = []
    for e in plan['effects']:
        if e['effect_id'] in skip:
            continue
        style = e['style']; base = allowed.get(style.get('color')) or cfg['styles'][style['profile']].get('rgb', [1, 1, 1])
        overrides = e.get('overrides') or {}
        rgb = [overrides.get(k, b) for k, b in zip(('Red1', 'Green1', 'Blue1'), base)]
        ok = all(type(v) in (int, float) for v in rgb) and any(
            all(abs(v-w) <= COLOR_TOLERANCE for v, w in zip(rgb, c)) for c in allowed.values())
        if not ok:
            shown = '#'+''.join('%02X' % max(0, min(255, round(v*255))) for v in rgb) if all(type(v) in (int, float) for v in rgb) else str(rgb)
            errors.append(f'效果 {e["effect_id"]}：文字颜色 {shown} 不在允许范围，花字颜色只用白和 #64A9E8。'
                          '下一步：强调色写 style.color="accent"，去掉 overrides 里的 Red1/Green1/Blue1；'
                          '如果这是用户在达芬奇里手改的颜色，不用改计划，plan --validate 加 --work-dir 指到本片花字工作目录即可（手改的花字整条保留，不查它的颜色）')
    return errors


def sound_rules(plan, skip=(), blacklist=None):
    """音效两条规矩（SKILL 必守规则 5）：任意 2 秒内不超过两声；同类事件全片同一个声音文件。
    另加一条（2026-11-22）：用户否掉过的音效（sfx_favorites.json 的 blacklist）不能用；blacklist 不给就读收藏表。

    同类＝音效自己写的 event；没写就按“花字 kind＋它是本组（list_group，没有就 group_id）里第几个带音效的”。
    skip 是用户动过、不会重放的音效所在的效果。只列问题、标需要用户决定，不自动删改音效。"""
    rows = []; order = {}
    for e in sorted(plan['effects'], key=lambda e: (e['start'], e['effect_id'])):
        if e['effect_id'] in skip or not e['sounds']:
            continue
        key = (e['kind'], str(e.get('list_group') or e.get('group_id')))
        order[key] = order.get(key, 0)+1
        for s in e['sounds']:
            rows.append({'effect_id': e['effect_id'], 'start': s['start'], 'filename': s['filename'],
                         'event': s.get('event') or f'{e["kind"]} 组内第{order[key]}个'})
    rows.sort(key=lambda r: (r['start'], r['effect_id']))
    violations = []
    if blacklist is None: blacklist = {b['filename']: b.get('why', '') for b in config('sfx_favorites.json').get('blacklist', [])}
    black = [r for r in rows if r['filename'] in blacklist]
    if black:
        violations.append({'rule': 'blacklist', 'effect_ids': [r['effect_id'] for r in black],
                           'message': '用了用户否掉过的音效：' + '、'.join(f'{r["effect_id"]}（第 {r["start"]} 帧 {r["filename"]}：{blacklist[r["filename"]]}）' for r in black),
                           'options': ['换成别的音效（叮、点击、错误音这类）', '用户听过、这次确实要用：用 --allow-sounds 照做']})
    clusters = []
    for i in range(len(rows)-2):
        if rows[i+2]['start']-rows[i]['start'] < SOUND_WINDOW_FRAMES:
            if clusters and i < clusters[-1][1]: clusters[-1][1] = i+2
            else: clusters.append([i, i+2])
    for a, b in clusters:
        group = rows[a:b+1]
        violations.append({'rule': 'window', 'effect_ids': [r['effect_id'] for r in group],
                           'message': '2 秒内超过两声：' + '、'.join(f'{r["effect_id"]}（第 {r["start"]} 帧 {r["filename"]}）' for r in group),
                           'options': ['去掉其中几声，只留最要紧的两声', '把其中几声挪开到 2 秒以外', '用户听过认可后用 --allow-sounds 照做']})
    events = {}
    for r in rows:
        events.setdefault(r['event'], {}).setdefault(r['filename'], []).append(r['effect_id'])
    for event, files in events.items():
        if len(files) > 1:
            violations.append({'rule': 'same_event', 'event': event, 'effect_ids': [i for ids in files.values() for i in ids],
                               'message': f'同类事件「{event}」用了 {len(files)} 种声音：' + '；'.join(f'{name} ← {"、".join(ids)}' for name, ids in files.items()),
                               'options': ['统一成同一个声音文件', '确实不是同一类事件：给这些音效分别写 event 说明各是什么事件', '用户听过认可后用 --allow-sounds 照做']})
    return {'valid': not violations, 'violations': violations,
            'needs_user_decision': [{'effect_ids': v['effect_ids'], 'reasons': [v['message']], 'options': v['options']} for v in violations],
            'scope': '只按帧数和文件名查；听感是否合适仍要用户试听。'}


def validation_report(plan, timeline=None, allow_unsafe=False, record=None, allow_sounds=False):
    """CLI plan validation includes geometry; structural errors are never bypassed."""
    from .safety import check_layout
    errors = validate(plan)
    result = {'valid': False, 'errors': errors, 'execution_performed': False}
    if errors:
        return result
    # 颜色不能用 --allow-unsafe 放行；有操作记录时，用户手改后整条保留的花字不查
    errors.extend(color_errors(plan, {r['effect_id'] for r in (record or {}).get('effects', []) if r.get('manual')}))
    result['sound_rules'] = sounds = sound_rules(plan)
    if not sounds['valid']:
        result['needs_user_decision'] = sounds['needs_user_decision']
        if allow_sounds: sounds['forced'] = True      # 2026-11-13 用户定：音效放行单独一个开关，不跟版面的 --allow-unsafe 绑在一起
        else: errors.extend('音效需要用户决定：'+v['message'] for v in sounds['violations'])
    try:
        if timeline is not None:
            from .markers import reconcile
            result['marker_report']=reconcile(plan,timeline)
        safety = check_layout(plan, timeline, allow_unsafe=allow_unsafe)
        result['safety'] = safety
        if not safety['valid']:
            errors.extend(v['message'] for v in safety['violations'])
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    result['valid'] = not errors
    return result
