"""Ordinary spoken emphasis goes beside a measured person, without shrinking."""
from copy import deepcopy
import unicodedata
from .plan import config
from .safety import estimate, check_layout


def ordinary(effect):
    return (effect['kind'] in ('keyword','spec','lens_label')
            and effect['anchor'].get('subject_kind')=='person'
            and effect['anchor'].get('placement','beside_person')=='beside_person')


def prepare_layout(plan, timeline, preserve_ids=(), cfg=None, fonts_dir=None):
    result=deepcopy(plan);cfg=cfg or config('fx_styles.json')
    width,height=map(float,(timeline['width'],timeline['height']))
    groups={}
    for e in result['effects']:
        if e.get('list_group'):groups.setdefault(e['list_group'],[]).append(e)
    handled=set()
    for rows in groups.values():
        if any(e['effect_id'] in preserve_ids for e in rows):continue
        final=max(rows,key=lambda e:e['start'])
        single=deepcopy(final);single.pop('list_group',None)
        placed=prepare_layout({'timeline':result['timeline'],'effects':[single]},timeline,(),cfg,fonts_dir)['effects'][0]
        for e in rows:
            e['anchor']['center']=deepcopy(placed['anchor']['center'])
            if 'layout_basis' in placed:e['layout_basis']=placed['layout_basis']
            handled.add(e['effect_id'])
    for e in result['effects']:
        if e['kind']=='chapter':
            e['text']=''.join(' ' if unicodedata.category(c).startswith('P') else c for c in e['text'])
            e['text']='\n'.join(' '.join(line.split()) for line in e['text'].split('\n'))
        if not ordinary(e) or e['effect_id'] in preserve_ids or e['effect_id'] in handled:continue
        person=e['anchor'].get('subject_bbox')
        if not person:raise ValueError('人物旁布局需要真实主体框：'+e['effect_id'])
        measured=estimate(e,width,height,cfg,fonts_dir)['bbox']
        if measured[2]<=person[0] or measured[0]>=person[2]:continue
        # Move horizontally beyond the person, maintaining the current baseline.
        gap=cfg['safety']['subject_gap_text_height_ratio']*(measured[3]-measured[1])
        moves=[person[0]-gap-measured[2],person[2]+gap-measured[0]]
        chosen=None
        for dx in sorted(moves,key=abs):
            candidate=deepcopy(e);candidate['anchor']['center'][0]+=dx
            if not 0<=candidate['anchor']['center'][0]<=1:continue
            safety=check_layout({'timeline':result['timeline'],'effects':[candidate]},timeline,
                                cfg,fonts_dir,check_person_bbox=True)
            if not safety['violations']:
                chosen=candidate;break
        if chosen is None:
            raise ValueError('人物两侧没有足够安全空处，不自动居中盖脸或缩字：'+e['effect_id'])
        e.update(chosen);e['layout_basis']='真实人物框旁留白'
    return result
