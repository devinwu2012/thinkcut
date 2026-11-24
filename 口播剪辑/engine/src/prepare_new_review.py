"""Build a reversible review from explicit transcript selections; no AI calls."""
import hashlib
import json
from project_runtime import RUN, CONFIG, TOTAL
from auto_review import write
from tight_cut import word_frame_span, selection_map
from local_repetitions import refresh
from review_build import build_full
from source_guard import verify_source
import re

# 删重复字（口吃）选错位置（2026-11-22 261122 反馈单 新12）：AI 手写 review_decisions.json 删“我我们”的口吃，
# 写成 char_start 0（句首“我们从头跟到尾”的“我”），程序只核对“这几个字确实是‘我’”就放行了，删成“们从头跟到尾”。
# 可靠的检查：理由写的是口吃/重复这类、删的又只有 1–3 个字时，删掉的字紧挨着的前面或后面必须还有一模一样的一份（删的是重复的那一份）；
# 不是就停下，列出句子里真正连着重复的位置。不猜词典。另外每条短删改都打印删前删后的前后文，让 AI 逐条核对。
REPEAT_REASON = re.compile(r'口吃|结巴|重复|叠字|多说')
SHORT = 3


def _core(text): return ''.join(c for c in text if c.isalnum())


def repeat_problem(text, a, b, reason):
    """删重复字：删的那几个字旁边没有同样的一份时，返回中文问题说明；不是这类删改或没问题返回 None。"""
    core = _core(text[a:b])
    if not core or len(core) > SHORT or not REPEAT_REASON.search(reason or ''):
        return None
    if _core(text[b:]).startswith(core) or _core(text[:a]).endswith(core):
        return None
    hits = [i for i in range(len(text)) if text.startswith(core * 2, i)]
    where = '；句子里连着重复的位置：' + '、'.join(f'char_start {i}（…{text[max(0, i - 6):i + 2 * len(core) + 6]}…）' for i in hits) if hits else '；句子里找不到连着重复的「' + core * 2 + '」'
    return (f'理由写的是删重复字，可删掉的「{text[a:b]}」（char_start {a}）前后都没有同样的字，删完读作「{context_after(text, a, b)}」，'
            f'删的不是重复的那一份{where}。改好 review_decisions.json 再运行')


def context_after(text, a, b, n=8):
    return text[max(0, a - n):a] + text[b:b + n]


def context_line(index, unit_id, text, a, b):
    return f'删字核对 第 {index} 条（第 {unit_id} 句 {a}–{b}）：…{text[max(0, a - 8):a]}[{text[a:b]}]{text[b:b + 8]}…  删后读作：…{context_after(text, a, b)}…'


def repair_alignment(aligned, response, units):
    """Use genuine ASR boundaries for defective aligner spans, never interpolate."""
    reference = {}
    words_by_unit = {}
    for unit, row in zip(units, response['result']['utterances']):
        cursor = 0
        for word in row['words']:
            if not any(c.isalnum() for c in word['text']): continue
            start = unit['text'].find(word['text'], cursor)
            if start < 0: raise ValueError('ASR token cannot map to raw transcript')
            cursor = start + len(word['text'])
            reference[(unit['id'], start, cursor)] = word
            words_by_unit.setdefault(unit['id'], []).append((start, cursor, word))
    tokens = aligned['tokens']
    changed = set()
    for _ in range(len(tokens)+1):
        bad = {i for i,t in enumerate(tokens) if not t['ready'] or t['end_ms'] <= t['start_ms']}
        for i in range(1,len(tokens)):
            if tokens[i]['start_ms'] < tokens[i-1]['end_ms']: bad.update((i-1,i))
        if not bad: break
        progress = False
        merges = {}
        for i in sorted(bad):
            t = tokens[i]; word = reference.get((t['unit_id'],t['char_start'],t['char_end']))
            if word and (t['start_ms'],t['end_ms'],t['ready']) != (word['start_time'],word['end_time'],True):
                t.update(start_ms=word['start_time'],end_ms=word['end_time'],ready=True,
                         timing_source='doubao-raw-word-boundary')
                changed.add(t['id']);progress=True
            elif not word:
                # The aligner split one ASR word (e.g. "五百") into characters it cannot
                # time. Keep the word whole with its genuine ASR time; never interpolate.
                for start, end, whole in words_by_unit.get(t['unit_id'], []):
                    if start <= t['char_start'] and t['char_end'] <= end:
                        merges[(t['unit_id'], start, end)] = whole
        if merges:
            merged = []
            for t in tokens:
                key = next((k for k in merges if k[0] == t['unit_id'] and k[1] <= t['char_start'] and t['char_end'] <= k[2]), None)
                if key is None: merged.append(t); continue
                if merged and merged[-1].get('_merge') == key: continue
                unit_id, start, end = key; word = merges[key]
                merged.append({'id': f'u{unit_id}c{start}', 'unit_id': unit_id, 'char_start': start, 'char_end': end,
                               'text': word['text'], 'start_ms': word['start_time'], 'end_ms': word['end_time'],
                               'ready': True, 'timing_source': 'doubao-raw-atomic-word-boundary', '_merge': key})
                changed.add(merged[-1]['id']); progress = True
            for t in merged: t.pop('_merge', None)
            tokens[:] = merged
        if not progress: raise ValueError('Cannot repair alignment from genuine ASR word boundaries')
    else: raise ValueError('Alignment repair did not converge')
    aligned.update(issues=[],boundary_fallback_ids=sorted(changed),
                   source='Local acoustic forced alignment; defective spans fall back to original Doubao word boundaries')
    aligned['alignment_id']=hashlib.sha256(json.dumps(aligned,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return aligned


def main():
    if not CONFIG: raise ValueError('An independent project_runtime.json is required')
    verify_source(CONFIG,RUN)
    state_path=RUN/'review_v2_state.json'
    if state_path.exists(): raise ValueError('Review already exists; never overwrite user choices（要按改好的 review_decisions.json 重新生成：edit_project.py reopen）')
    units=json.loads((RUN/'sentence_units.json').read_text())
    aligned=json.loads((RUN/'character_alignment.json').read_text())
    # Keep the aligner's own output once; a re-run after fixing selections must not replace it with repaired data.
    if not (RUN/'alignment_qwen_full.json').exists():write(RUN/'alignment_qwen_full.json',aligned)
    response=json.loads((RUN.parent/'转写'/'doubao_turbo.json').read_text())
    aligned=repair_alignment(aligned,response,units)
    decisions=json.loads((RUN/'review_decisions.json').read_text())
    cards=[];selected_ids=set()
    for index,row in enumerate(decisions['cuts']):
        unit=units[row['unit_id']];a,b=row['char_start'],row['char_end']
        if not 0<=a<b<=len(unit['text']) or unit['text'][a:b]!=row['text']:
            raise ValueError(f'review_decisions.json 第 {index+1} 条选区与原文对不上：请核对 unit_id、char_start、char_end（从 0 开始、不含结尾）和 text 是否与 sentence_units.json 一致')
        problem=repeat_problem(unit['text'],a,b,row.get('reason',''))
        if problem:raise ValueError(f'review_decisions.json 第 {index+1} 条（第 {unit["id"]} 句）：'+problem)
        if len(_core(row['text']))<=SHORT or REPEAT_REASON.search(row.get('reason') or ''):
            print(context_line(index+1,unit['id'],unit['text'],a,b))
        ts=[t for t in aligned['tokens'] if t['unit_id']==unit['id'] and a<=t['char_start'] and t['char_end']<=b]
        if any(t['unit_id']==unit['id'] and t['char_start']<b and t['char_end']>a
               and not a<=t['char_start']<t['char_end']<=b for t in aligned['tokens']):
            raise ValueError('选区穿过一个完整发音词，请选中完整词；此次未生成审核。')
        if not ts or not ts[0]['ready'] or not ts[-1]['ready']:
            raise ValueError(f'review_decisions.json 第 {index+1} 条选区没有覆盖到可用的字（或首尾字时间不可靠）：请调整选区范围')
        if ''.join(c for c in row['text'] if c.isalnum()).casefold()!= ''.join(c for t in ts for c in t['text'] if c.isalnum()).casefold():
            raise ValueError('选区原文与时间词覆盖不一致；此次未生成审核。')
        selected_ids.update(t['id'] for t in ts)
        left,right,_=word_frame_span(ts[0]['start_ms'],ts[-1]['end_ms'],TOTAL)
        cards.append({'id':f'semantic_{index}','kind':'automatic','default':'apply','cut_frames':[left,right],
                      'source_frames':[left,right],'source_ids':[unit['id']],
                      'title':row['reason'],'removed_text':[row['text']],
                      'retained_text':[units[i]['text'] for i in row.get('replacement_ids',[])],
                      'reason':row['reason'],'before_url':'/source.mp4','after_url':'/source.mp4'})
    review={'version':'project-review-v1','model':decisions['reviewer'],'cards':cards,'pending':[],
            'source_duration':TOTAL*1001/30000,'limitations':decisions['limitations']}
    state={'overrides':{},'seen':[],'notes':{},'confirmed_at':None,'natural_pause_mode':'sentence','subtitle_proofread':None}
    explicit=selection_map(aligned['tokens'],[c['cut_frames'] for c in cards],TOTAL)
    if set(explicit['removed_token_ids'])!=selected_ids:
        raise ValueError('初剪选区与实际删字不一致，可能有同一帧中的相邻字；请调整选区。此次未生成审核。')
    refresh(state,review,aligned)
    allowed=selected_ids | {ident for candidate in state['repeat_edits']['candidates']
                            if candidate['status']=='apply' for ident in candidate['source_ids']}
    from review_build import active_map
    if set(active_map(cards,state,aligned)['removed_token_ids'])!=allowed:
        raise ValueError('自动重复剪切连带删除未选中的字；此次未生成审核。')
    write(RUN/'character_alignment.json',aligned)
    write(RUN/'review_v2.json',review);write(state_path,state)
    build_full(review,state)
    print(json.dumps({'semantic_selections':len(cards),'aligned_tokens':len(aligned['tokens']),
                      'asr_boundary_fallbacks':len(aligned['boundary_fallback_ids'])}))

if __name__=='__main__': main()
