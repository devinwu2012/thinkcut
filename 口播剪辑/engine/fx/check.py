"""Offline checks; text_items also reads titles inside nested timelines (generic snapshot reading)."""
from collections import defaultdict


def text_items(timeline, offset=0, bounds=None, path=(), root_track=None, parents=()):
    for item in timeline['items']:
        if item['type'] != 'video':
            continue
        start, end = item['start']+offset, item['end']+offset
        if bounds:
            start, end = max(start, bounds[0]), min(end, bounds[1])
        if start >= end:
            continue
        track = root_track or {'index': item['track'], 'name': item.get('track_name', '')}
        for text in item.get('texts', []):
            yield {'item': item, 'text': text, 'start': start, 'end': end,
                   'track': track, 'nested_path': list(path), 'parents': list(parents),
                   'source_clip_offset': start-offset-item['start']}
        nested = item.get('nested')
        if nested:
            source = item.get('source_start')
            if source is None:
                raise ValueError('嵌套源帧不可读，不能猜测花字位置。')
            if nested.get('fps') and timeline.get('fps') and abs(float(nested['fps'])-float(timeline['fps']))>.01:
                raise ValueError('嵌套与父时间线帧率不同，无法可靠换算花字位置。')
            if item.get('source_end') is not None and abs((item['source_end']-source+1)-(item['end']-item['start']))>1.1:
                raise ValueError('嵌套存在变速或不明确的源范围，无法可靠换算花字位置。')
            # Source frames of a nested clip are relative to the nested timeline start.
            nested_offset = offset+item['start']-nested['start']-source
            yield from text_items(nested, nested_offset, (start,end), path+(nested['name'],),
                                  track, parents+(item,))


def check(data, plan=None):
    findings = []
    def add(kind, message, **details):
        findings.append({'kind': kind, 'message': message, **details})
    def visit(timeline, path=''):
        audio = defaultdict(list)
        for item in timeline['items']:
            label = f'{path}{item["type"]}{item["track"]}「{item["name"]}」'
            if item['start'] >= item['end']:
                add('invalid_range', label+'片段范围无效', id=item['id'])
            for text in item.get('texts', []):
                value = text['inputs'].get('StyledText')
                if not isinstance(value,str) or not value.strip():
                    add('empty_text', label+'文字为空', id=item['id'], tool=text['tool'])
                inputs = text['inputs']
                rs = text.get('comp_range', item.get('comp_range',{})).get('COMPN_RenderStart')
                gi, go = inputs.get('GlobalIn'), inputs.get('GlobalOut')
                if all(type(v) in (int,float) for v in (rs,gi,go)):
                    last = rs+item['end']-item['start']-1
                    if gi>rs or go<last:
                        add('short_text_range', label+'文字显示范围短于片段（可能有意分段显示，需人工核对）',
                            id=item['id'], clip_range=[rs,last], text_range=[gi,go])
            if item['type'] == 'audio':
                audio[item['track']].append(item)
            if item.get('nested'):
                visit(item['nested'], path+item['nested']['name']+'/')
        for track, items in audio.items():
            ordered = sorted(items, key=lambda v:v['start'])
            for i, left in enumerate(ordered):
                for right in ordered[i+1:]:
                    if right['start'] >= left['end']:
                        break
                    add('audio_overlap', f'{path}A{track} 音频片段重叠', ids=[left['id'],right['id']])
    visit(data['timeline'])
    if plan is not None:
        from .plan import validate
        errors = validate(plan)
        for error in errors:
            add('invalid_plan', error)
        if not errors:
            if plan['timeline']['name'] != data['timeline']['name']:
                add('timeline_mismatch', '计划时间线名称与快照不一致')
            observed = list(text_items(data['timeline']))
            for effect in plan['effects']:
                layer_name = '花字' + (str(effect['layer']) if effect['layer']>1 else '')
                wanted_track = effect.get('track', layer_name)
                matches = [o for o in observed if o['start']==effect['start'] and o['end']==effect['end']
                           and o['text']['inputs'].get('StyledText')==effect['text']
                           and (o['track']['name']==wanted_track or o['track']['index']==wanted_track)]
                if len(matches) != 1:
                    add('plan_effect_missing' if not matches else 'plan_effect_ambiguous',
                        f'计划效果 {effect["effect_id"]} 按轨道、起止、文字匹配到 {len(matches)} 项', effect_id=effect['effect_id'])
    return {'count':len(findings), 'findings':findings,
            'scope':'只检查参数；不代表画面无遮挡、动画播放或音效听感通过。'}
