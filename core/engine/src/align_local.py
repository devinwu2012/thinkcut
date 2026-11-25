"""Local character alignment of the existing Doubao transcript. No media upload."""
import hashlib
import json
import os
import re
import time
from pathlib import Path

os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
os.environ.setdefault('HF_HUB_DISABLE_XET','1')
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK','1')
os.environ.setdefault('HF_HOME',str(Path(__file__).resolve().parents[3]/'环境/huggingface'))   # 对齐模型放 NAS（2026-10-29）
from project_runtime import RUN
MODEL='Qwen/Qwen3-ForcedAligner-0.6B'


def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)


def main():
    import torch
    import soundfile as sf
    from qwen_asr import Qwen3ForcedAligner
    audio,rate=sf.read(str(RUN/'audio_asr.wav'),dtype='float32')
    units=json.loads((RUN/'sentence_units.json').read_text())
    fingerprint=hashlib.sha256((RUN/'sentence_units.json').read_bytes()+(RUN/'audio_asr.wav').read_bytes()).hexdigest()
    cache=RUN/'alignment_raw.json'
    old=json.loads(cache.read_text()) if cache.exists() else {}
    rows=old.get('rows',{}) if old.get('fingerprint')==fingerprint else {}
    device='mps' if torch.backends.mps.is_available() else 'cpu'
    print('Loading local aligner on '+device,flush=True)
    model=Qwen3ForcedAligner.from_pretrained(MODEL,dtype=torch.float32,device_map=device,attn_implementation='eager')
    started=time.time()
    batch=max(1,int(os.environ.get('AI_EDIT_ALIGN_BATCH','8')))
    end=len(audio)/rate
    def window(i,before,after):
        left=max(0,units[i]['start_ms']/1000-before)
        right=min(end,(units[i+1]['start_ms']/1000 if i+1<len(units) else end)+after)
        return left,right
    def run(indices,before,after):
        """Align several sentences in one model call; returns {index: (items, window_ms)}."""
        spans=[window(i,before,after) for i in indices]
        results=model.align(audio=[(audio[int(l*rate):int(r*rate)],rate) for l,r in spans],
                            text=[units[i]['text'] for i in indices],language='Chinese')
        return {i:([{'text':x.text,'start_ms':round((l+x.start_time)*1000),'end_ms':round((l+x.end_time)*1000)} for x in result],
                   [round(l*1000),round(r*1000)]) for i,(l,r),result in zip(indices,spans,results)}
    def save():write(cache,{'model':MODEL,'fingerprint':fingerprint,'device':device,'rows':rows})
    # Batch sentences of similar length: padding a short clip to a long one slightly shifts its timestamps.
    def by_length(indices,before,after):return sorted(indices,key=lambda i:window(i,before,after)[1]-window(i,before,after)[0])
    todo=by_length([i for i,u in enumerate(units) if str(u['id']) not in rows],.35,.12)
    for k in range(0,len(todo),batch):
        for i,(items,span) in run(todo[k:k+batch],.35,.12).items():
            rows[str(units[i]['id'])]={'unit_id':units[i]['id'],'source_window_ms':span,'items':items}
        save()
        print(f"Aligned {min(k+batch,len(todo))}/{len(todo)}, elapsed {time.time()-started:.1f}s",flush=True)
    # Different acoustic windows can resolve tokens quantized to a zero-length span.
    # Re-run the model; do not distribute sentence time uniformly over characters.
    def defects(items):return sum(x['end_ms']<=x['start_ms'] or x['end_ms']-x['start_ms']>=3000 for x in items)
    retry=[i for i,u in enumerate(units) if not rows[str(u['id'])].get('retried') and defects(rows[str(u['id'])]['items'])]
    for padding in (.43,.59):
        pending=by_length([i for i in retry if defects(rows[str(units[i]['id'])]['items'])],padding,.2)
        for k in range(0,len(pending),batch):
            for i,(items,span) in run(pending[k:k+batch],padding,.2).items():
                entry=rows[str(units[i]['id'])]
                if defects(items)<defects(entry['items']):entry.update(items=items,source_window_ms=span)
    for i in retry:rows[str(units[i]['id'])]['retried']=True
    if retry:
        save()
        left=sum(1 for i in retry if defects(rows[str(units[i]['id'])]['items']))
        print(f"Rechecked {len(retry)} sentences; {left} still uncertain, elapsed {time.time()-started:.1f}s",flush=True)
    tokens=[];issues=[]
    for u in units:
        cursor=0;raw=u['text'];items=rows[str(u['id'])]['items']
        source_indices=[i for i,c in enumerate(raw) if c.isalnum()]
        normalized=''.join(raw[i].lower() for i in source_indices)
        for item in items:
            text=''.join(c.lower() for c in item['text'] if c.isalnum())
            match=re.search(re.escape(text),normalized[cursor:]) if text else None
            if not match:
                raise ValueError(f"Alignment token cannot map to source: {u['id']} {text!r}")
            na=cursor+match.start();nb=cursor+match.end();cursor=nb
            a=source_indices[na];b=source_indices[nb-1]+1
            token={'id':f"u{u['id']}c{a}",'unit_id':u['id'],'char_start':a,'char_end':b,'text':raw[a:b],
                   'start_ms':item['start_ms'],'end_ms':item['end_ms']}
            token['ready']=token['end_ms']>token['start_ms'] and token['end_ms']-token['start_ms']<3000
            if not token['ready']:issues.append(token['id'])
            tokens.append(token)
        covered=set(j for t in tokens if t['unit_id']==u['id'] for j in range(t['char_start'],t['char_end']))
        missing=[j for j,c in enumerate(raw) if c.isalnum() and j not in covered]
        if missing:raise ValueError(f"Unmapped spoken characters {u['id']}: {missing}")
    # Reject overlapping speech labels instead of silently claiming both are exact.
    for previous,current in zip(tokens,tokens[1:]):
        if current['start_ms']<previous['end_ms']:
            previous['ready']=False;current['ready']=False
            issues.extend([previous['id'],current['id']])
    payload={'version':'local-align-v1','model':MODEL,'fingerprint':fingerprint,'source':'Doubao console raw transcript + local acoustic forced alignment','time_resolution_ms':80,'tokens':tokens,'issues':sorted(set(issues))}
    payload['alignment_id']=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    write(RUN/'character_alignment.json',payload)
    (RUN/'alignment_qwen_full.json').unlink(missing_ok=True)  # a fresh alignment gets a fresh backup at review
    print(f"Complete: {len(tokens)} aligned speech units; {len(set(issues))} need boundary review",flush=True)


if __name__=='__main__':main()
