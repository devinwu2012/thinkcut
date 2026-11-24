"""Detect exact local restarts in original tokens and current retained speech.

No ASR smoothing or product-name substitutions. User restores take precedence.
"""
import hashlib
import json
import math
import re
from pathlib import Path
from timing import ms_to_frame
from tight_cut import word_frame_span
from review_build import FPS,effective_cuts

ROOT=Path(__file__).resolve().parents[1]
from project_runtime import RUN, CONFIG, TOTAL
RULES=json.loads((ROOT/'config/repetition_rules.json').read_text())
if CONFIG: RULES={**RULES,'cross_sentence_prefixes':['或者','以及']}
PUNCT=re.compile(r'[，。！？；、,:;!?]')


def removed(token,cuts):
    f=(token['start_ms']+token['end_ms'])*FPS/2000
    return any(a<=f<b for a,b in cuts)


def edited_gap_ms(start,end,cuts):
    return max(0,end-start-sum(max(0,min(end,b/FPS*1000)-max(start,a/FPS*1000)) for a,b in cuts))


def user_restored(tokens,state):
    operations=state.get('editor',{}).get('operations',[])[:state.get('editor',{}).get('cursor',0)]
    for t in tokens:
        frame=ms_to_frame((t['start_ms']+t['end_ms'])/2)
        for op in reversed(operations):
            if any(a<=frame<b for a,b in op.get('ranges',[])):   # 挪句子（move）没有 ranges
                if op['action']=='restore':return True
                break
    return False


def span_frames(token):
    """Frame span of a word; a zero-length word (alignment unsure) becomes an empty span at its time."""
    if token['end_ms']<=token['start_ms']:
        f=min(max(math.floor(token['start_ms']*FPS/1000),0),TOTAL-1)
        return f,f
    return word_frame_span(token['start_ms'],token['end_ms'],TOTAL)[:2]


def detect(tokens,units,cuts,state,rules=RULES):
    units={u['id']:u for u in units};result=[];seen=set()
    # Original stream accounts for already handled examples; retained stream
    # also detects restarts exposed by an earlier sentence deletion.
    for stream in ([t for t in tokens if not removed(t,cuts)],tokens):
        i=0
        while i<len(stream):
            found=False
            for n in range(min(rules['max_tokens'],(len(stream)-i)//2),rules['min_tokens']-1,-1):
                first,second=stream[i:i+n],stream[i+n:i+2*n]
                if [t['text'].casefold() for t in first]!=[t['text'].casefold() for t in second]:continue
                if len({t['unit_id'] for t in first})!=1 or len({t['unit_id'] for t in second})!=1:continue
                phrase=''.join(t['text'] for t in first)
                u,v=units[first[0]['unit_id']],units[second[0]['unit_id']]
                atext=u['text'][first[0]['char_start']:first[-1]['char_start']+len(first[-1]['text'])]
                if PUNCT.search(atext):continue
                gap=edited_gap_ms(first[-1]['end_ms'],second[0]['start_ms'],cuts)
                if gap>rules['max_edited_gap_ms'] or first[-1]['end_ms']-first[0]['start_ms']>rules['max_phrase_ms']:continue
                key=tuple(t['id'] for t in first)
                if key in seen:continue
                seen.add(key)
                status='apply';reason='连续相同短语，后一次继续承接正文，删除前一次和重起间隙'
                tail=v['text'][second[-1]['char_start']+len(second[-1]['text']):]
                before=u['text'][:first[0]['char_start']]
                middle=u['text'][first[-1]['char_start']+len(first[-1]['text']):second[0]['char_start']] if u['id']==v['id'] else u['text'][first[-1]['char_start']+len(first[-1]['text']):]
                compact_repeat = (2 <= len(phrase) <= 4 and u['id'] == v['id'] and not middle.strip())
                if compact_repeat and gap <= 180:
                    status='keep';reason='紧邻的短词叠用，默认保留有意强调或动作叠词'
                elif compact_repeat and gap < 450:
                    status='review';reason='短词重复的重起依据不明确，保留并交 AI 语义复核'
                elif phrase in rules['emphasis_phrases'] or any(x in (before[-20:]+tail[:20]) for x in rules['emphasis_context']):
                    status='keep';reason='正常强调或语用表达，禁止机械去重'
                elif not re.search(r'[\w\u4e00-\u9fff]',tail):
                    status='review';reason='后一次没有继续展开，可能是强调，需语义判断'
                elif PUNCT.search(middle) and not ((n>=rules['min_cross_clause_tokens'] and gap<=rules['max_cross_clause_gap_ms']) or (u['id']!=v['id'] and phrase.startswith(tuple(rules['cross_sentence_prefixes'])))):
                    status='review';reason='跨标点重复但重起依据不足，默认保留'
                if not all(t['ready'] for t in (first[0],first[-1],second[0])):
                    status='review';reason='切点时间边界待核实'
                if user_restored(first,state):status='user_keep';reason='用户明确恢复过，自动规则不覆盖'
                elif all(removed(t,cuts) for t in first):status='already_removed';reason='当前剪法已删除，记录命中但不重复叠加'
                elif any(removed(t,cuts) for t in second):status='review';reason='后一次已被部分删除，不能假定仍可完整替代'
                # Same frame spans as the cut map, so sub-frame words never straddle a cut edge.
                # 对齐时长为 0 的字（边界待核实）没有可靠帧范围：只记录候选、交人工判断，不自动补剪
                flat=any(t['end_ms']<=t['start_ms'] for t in (first[0],first[-1]))
                if flat and status=='apply':status='review';reason='切点时间边界待核实'
                start=span_frames(first[0])[0]
                end=max(span_frames(first[-1])[1],math.floor((second[0]['start_ms']-rules['keep_before_replacement_ms'])*FPS/1000))
                if status=='already_removed' and not flat and not any(removed(t,cuts) for t in second):
                    remaining=end-start-sum(max(0,min(end,b)-max(start,a)) for a,b in cuts)
                    if remaining>=3 and not user_restored(first,state):
                        status='apply';reason='前一次重复词已删，补齐残留的重起气口'
                row={'id':'local_'+hashlib.sha256('|'.join(key).encode()).hexdigest()[:12],
                     'kind':'within_sentence_restart' if u['id']==v['id'] else 'cross_sentence_restart',
                     'phrase':phrase,'source_ids':list(key),'replacement_ids':[t['id'] for t in second],
                     'cut_frames':[start,end],'edited_gap_ms':round(gap,2),'status':status,'reason':reason,
                     'before_text':u['text'] if u['id']==v['id'] else u['text']+' '+v['text']}
                result.append(row);i+=n;found=True;break
            if not found:i+=1
    return result


def refresh(state,manifest,aligned=None):
    """Recompute from other decisions, never from our own previous output.

    aligned: 初剪时传还没写盘的修复后对齐（2026-11-13 审查：以前初剪读盘上修复前的，网页一编辑按修复后的重算，会多删内容）。"""
    r=RUN
    if aligned is None:aligned=json.loads((r/'character_alignment.json').read_text())
    baseline={k:v for k,v in state.items() if k!='repeat_edits'}
    rows=detect(aligned['tokens'],json.loads((r/'sentence_units.json').read_text()),
                effective_cuts(manifest['cards'],baseline),baseline)
    state['repeat_edits']={'version':'local-repeats-v2','alignment_id':aligned['alignment_id'],
                           'rules':RULES,'candidates':rows}
    return state
