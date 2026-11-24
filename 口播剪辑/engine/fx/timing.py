"""Map existing word evidence onto the current A1; markers only bound the search."""
from copy import deepcopy
import json
from pathlib import Path
from .compare import map_a1_words, first_mention


def prepare_timing(plan, timeline):
    result=deepcopy(plan)
    transcripts={path:(json.loads(Path(data).read_text()) if isinstance(data,str) else data)
                 for path,data in plan.get('transcripts',{}).items()}
    for e in result['effects']:
        speech=e.get('speech',{})
        if speech.get('mode')=='visual' or e['kind']=='compare_label':
            continue  # Explicit visual timing / existing comparison evidence is authoritative.
        # Search only nearby speech: repeated words in another paragraph cannot win.
        a=speech.get('search_start',max(timeline['start'],e['start']-90))
        b=speech.get('search_end',min(timeline['end'],e['end']+90))
        if type(a) is not int or type(b) is not int or not timeline['start']<=a<b<=timeline['end']:
            raise ValueError('口播找词区间无效：'+e['effect_id'])
        words=map_a1_words(timeline,transcripts,a,b)
        text=speech.get('text',e.get('list_line',e['text']))
        cues=[i for i in timeline['items'] if i['type']=='subtitle' and i['start']<b and a<i['end']]
        frame,method=first_mention(text,a,b,words,cues)
        if frame is None:
            raise ValueError('未找到对应口播首字：'+e['effect_id']+'；请提供现有逐字结果或对应字幕；画面补充须明确 speech.mode=visual')
        old=e['start'];delta=frame-old
        if frame>=e['end']:
            raise ValueError('口播首字晚于标题结束，请复核范围：'+e['effect_id'])
        e['start']=frame
        for sound in e['sounds']:sound['start']=frame
        for key in e['animation']:key['frame']+=delta
        e['timing_basis']='A1源区间映射逐字首字' if method=='word' else '估算：字幕字位置/时间'
        e['timing_evidence']={'text':text,'start':frame,'search_start':a,'search_end':b,
                              'method':method,'words':words,'subtitles':cues if words is None else []}
    return result


def continuous_states(plan):
    """One sequential title per cumulative-list state; no track per row."""
    result=deepcopy(plan);groups={}
    for e in result['effects']:
        if e.get('list_group'):
            groups.setdefault(e['list_group'],[]).append(e)
    for group,rows in groups.items():
        rows.sort(key=lambda e:e['start'])
        final_end=max(e['end'] for e in rows)
        base=rows[0];texts=[]
        for n,e in enumerate(rows):
            if n and e['start']<=rows[n-1]['start']:raise ValueError('清单逐行起点须递增：'+group)
            if e['style']!=base['style'] or e['anchor']!=base['anchor'] or e.get('track')!=base.get('track'):
                raise ValueError('同一清单须同样式、同位置、同轨：'+group)
            line=e.get('list_line',e['text']);texts.append(line)
            e.update(text='\n'.join(texts)+'\n'*(len(rows)-n-1),list_line=line,layer=base['layer'],
                     end=rows[n+1]['start'] if n+1<len(rows) else final_end)
            # 连续状态首尾相接：只在第一状态淡入、最后状态淡出，中间切换不闪
            e['motion']={'in':n==0,'out':n==len(rows)-1}
            # Static cumulative states retain all prior rows; animation could move them.
            if e['animation']:raise ValueError('连续状态清单须静态；逐字动画需单独指定制作')
    return result
