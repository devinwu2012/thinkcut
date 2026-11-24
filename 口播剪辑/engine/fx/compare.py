"""Timeline word evidence and staggered labels for a fixed comparison interval."""
from copy import deepcopy
import re

from .plan import legacy_styles

# 对比标签默认白字＋投影；画面太花才用深色底（2026-11-12 包装系统）
LABEL_PROFILES = ('brand_label', 'brand_label_dark')
LABEL_PROFILES += tuple(k for k, v in legacy_styles().items() if v in LABEL_PROFILES)   # 片子里老计划写的旧样式名（bin/旧名兼容.sh）
SOUNDS = ('咻-起身01+可爱.mp3', 'UI+弹出，弹出+6.mp3', '02_清脆鼠标点击.mp3')


def clean(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text)


def map_a1_words(timeline, transcripts, start=None, end=None):
    """transcripts maps source file paths to existing Doubao JSON; no transcription."""
    result = []; found_audio = False
    fps = float(timeline['fps'])
    for clip in timeline['items']:
        if clip['type'] != 'audio' or clip['track'] != 1 or not clip['enabled']:
            continue
        if start is not None and clip['end'] <= start or end is not None and clip['start'] >= end:
            continue
        found_audio = True
        data = transcripts.get(clip.get('media_path'))
        if data is None:
            return None  # Missing timings must use subtitles, never assert that the word was absent.
        from .merge import linear
        if not linear(clip):
            raise ValueError('变速A1不能可靠映射逐字时间')
        if not any(u.get('words') for u in data['result']['utterances']):
            return None
        for utterance in data['result']['utterances']:
            for word in utterance.get('words', []):
                source = round(word['start_time']*fps/1000)
                frame = int(clip['start']+source-clip.get('source_offset',clip['source_start']))
                if clip['start'] <= frame < clip['end']:
                    result.append({'text':word['text'], 'frame':frame, 'a1_item_id':clip['id'],
                                   'source_frame':source, 'source_ms':word['start_time']})
    return sorted(result, key=lambda w:w['frame']) if found_audio else None


def first_mention(word, start, end, words=None, subtitles=()):
    if words is not None:
        chars = [(c,row['frame']) for row in words if start <= row['frame'] < end
                 for c in clean(row['text'])]
        text = ''.join(c for c,f in chars)
        index = text.find(clean(word))
        return (chars[index][1], 'word') if index >= 0 else (None, 'rhythm')
    for cue in sorted(subtitles, key=lambda c:c['start']):
        text = clean(cue.get('subtitle_text', cue.get('text','')))
        index = text.find(clean(word))
        if index < 0 or not text:
            continue
        frame = cue['start']+round(index*(cue['end']-cue['start'])/len(text))
        if start <= frame < end:
            return int(frame), 'subtitle_estimate'
    return None, 'rhythm'


def schedule(effects, start, end, words=None, subtitles=()):
    """Keep photos fixed. Word timing wins; absence uses the authorized visual rhythm."""
    if type(start) is not int or type(end) is not int or end-start < 19:
        raise ValueError('对比区间不足以容纳至少15帧错峰')
    if len(effects) != 2:
        raise ValueError('每组对比标签必须恰为左右两个')
    result = deepcopy(effects)
    evidence = {'words':words, 'subtitles':list(subtitles) if words is None else []}
    for index, effect in enumerate(sorted(result,key=lambda e:e['anchor']['center'][0])):
        frame, method = first_mention(effect['text'], start, end, **evidence)
        if frame is None:
            frame = start+(3 if index == 0 else max(18,round((end-start)*.4)))
        effect.update(kind='compare_label',start=frame,end=end,
                      comparison={'start':start,'end':end,'side':'left' if index==0 else 'right',
                                  'word':effect['text'],'method':method, **evidence},
                      timing_basis={'word':'A1源帧映射逐字时间',
                                    'subtitle_estimate':'字幕按字位置比例估算（估算）',
                                    'rhythm':'按画面节奏，不是口播对齐'}[method])
        size=effect['style']['size']
        if effect['style'].get('profile') not in LABEL_PROFILES:
            effect['style']['profile']='brand_label'
        # Preserve only an explicitly requested animation, remapped to the scheduled start.
        old_start = next(e['start'] for e in effects if e['effect_id']==effect['effect_id'])
        effect['animation'] = [{**a, 'frame':frame+a['frame']-old_start} for a in effect.get('animation', [])]
        effect['sounds']=[{'filename':SOUNDS[index%3],'start':frame}]
    return result


def validate_comparisons(effects):
    errors=[];groups={}
    for effect in effects:
        if isinstance(effect,dict) and effect.get('kind')=='compare_label':
            groups.setdefault(str(effect.get('group_id')),[]).append(effect)
    for group, rows in groups.items():
        try:
            if len(rows)!=2:raise ValueError('必须恰有左右两个标签')
            rows=sorted(rows,key=lambda e:e['anchor']['center'][0])
            for index,e in enumerate(rows):
                c=e['comparison'];a,b=c['start'],c['end']
                if type(a) is not int or type(b) is not int or b-a<19:raise ValueError('对比区间无效')
                if c['word']!=e['text'] or c['side']!=('left' if index==0 else 'right'):raise ValueError('词或左右位置不匹配')
                if e['style']['profile'] not in LABEL_PROFILES:raise ValueError('须使用brand_label或brand_label_dark样式')
                frame,method=first_mention(e['text'],a,b,c['words'],c['subtitles'])
                if frame is None:frame=a+(3 if index==0 else max(18,round((b-a)*.4)))
                if e['start']!=frame or e['end']!=b or c['method']!=method:raise ValueError('起点须对应各自的词；没说到才按画面节奏')
                if len(e['sounds'])!=1 or e['sounds'][0]['filename']!=SOUNDS[index]:raise ValueError('每个标签须按连续顺序配一个音效')
                animation=e['animation']
                if animation and (len(animation)!=2 or animation[0]!=dict(input='Size',frame=frame,value=e['style']['size']*.8) or animation[1]!=dict(input='Size',frame=min(frame+3,b-1),value=e['style']['size'])):raise ValueError('要求弹出时须使用Size弹出动画')
            left,right=(e['comparison'] for e in rows)
            if any(left[k]!=right[k] for k in ('start','end','words','subtitles')):raise ValueError('同组必须使用相同对比区间与找词证据')
            if all(e['comparison']['method']=='rhythm' for e in rows) and rows[1]['start']-rows[0]['start']<15:raise ValueError('画面节奏须至少间隔15帧')
        except (KeyError,TypeError,ValueError,IndexError,AttributeError) as exc:
            errors.append(f'对比组 {group}：{exc}')
    return errors
