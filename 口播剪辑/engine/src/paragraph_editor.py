"""Character-timed operations. Existing sentence decisions remain the baseline."""
import copy
import hashlib
import json
from review_build import active_map,effective_cuts,word_order,moves,apply_move,TOTAL,FPS
from tight_cut import word_frame_span

from project_runtime import RUN, CONFIG, NAME


class StateConflict(ValueError):
    """The browser must reload before applying its edit to a different state."""


def state_revision(state):
    return hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def alignment():
    path=RUN/'character_alignment.json'
    return json.loads(path.read_text()) if path.exists() else None


def manual_changes(manifest,state,aligned,removed_ids,units=None):
    """Display who removed a word, without writing or changing the cut map."""
    if not aligned:return {'manual_cut_token_ids':[], 'manual_restored_token_ids':[], 'manual_changes':[]}
    tokens=aligned['tokens'];known={t['id'] for t in tokens}
    baseline={**state,'overrides':{},'text_edits':{},'editor':{}}
    if units is not None:
        from local_repetitions import detect
        baseline.pop('repeat_edits',None)
        baseline['repeat_edits']={'candidates':detect(tokens,units,effective_cuts(manifest['cards'],baseline),baseline)}
    spans={t['id']:word_frame_span(t['start_ms'],t['end_ms'],TOTAL)[:2]
           if t['end_ms']>t['start_ms'] else (t['start_ms']*FPS/1000,)*2 for t in tokens}
    def range_ids(ranges):
        return [ident for ident,(left,right) in spans.items() if any(
            a<=left<b if left==right else a<right and b>left for a,b in ranges)]
    # Final deletion IDs remain the authority. This presentation baseline may
    # include old cut boundaries that the user's restoration has repaired.
    initially_removed=set(range_ids(effective_cuts(manifest['cards'],baseline)))
    owners={ident:'ai' for ident in initially_removed}
    def apply(action,ids):
        for ident in ids:
            if ident not in known:continue
            if action=='cut' and owners.get(ident) not in ('ai','cut'):owners[ident]='cut'
            elif action=='restore' and owners.get(ident) in ('ai','cut'):
                owners[ident]='restore' if ident in initially_removed else None
    for card in manifest['cards']:
        selected=state.get('overrides',{}).get(card['id'])
        if selected and selected!=card['default'] and card.get('cut_frames'):
            apply('cut' if selected=='apply' else 'restore',range_ids([card['cut_frames']]))
    for edit in state.get('text_edits',{}).values():
        if edit['status']=='applied':apply(edit['action'],range_ids([edit['frames']]))
    editor=state.get('editor',{})
    for operation in editor.get('operations',[])[:editor.get('cursor',0)]:
        if operation['action']=='move':continue
        apply(operation['action'],operation['ids'] if 'ids' in operation else range_ids(operation['ranges']))
    removed=set(removed_ids);cuts=[];restored=[];groups=[]
    for index,token in enumerate(tokens):
        ident=token['id'];owner=owners.get(ident)
        if owner=='cut' and ident in removed:cuts.append(ident)
        elif owner=='restore' and ident not in removed:restored.append(ident)
        else:continue
        if groups and groups[-1]['last_index']==index-1 and groups[-1]['action']==owner and groups[-1]['unit_id']==token['unit_id']:
            group=groups[-1];group['ids'].append(ident);group['text']+=token['text'];group['last_index']=index
        else:groups.append({'action':owner,'ids':[ident],'text':token['text'],'unit_id':token['unit_id'],
                            'start_ms':token['start_ms'],'last_index':index})
    for group in groups:group.pop('last_index')
    if word_order(state,tokens)!=[t['id'] for t in tokens]:
        lookup={t['id']:t for t in tokens};seen={}
        for op in moves(state):
            ids=op['ids'] if 'ids' in op else [t['id'] for t in tokens if t['unit_id'] in op['units']]
            words=[lookup[i] for i in ids if i in lookup]
            if words:seen[tuple(ids)]={'action':'move','ids':ids,'text':''.join(t['text'] for t in words),
                                       'unit_id':words[0]['unit_id'],'start_ms':words[0]['start_ms'],'side':op.get('side','after')}
        groups.extend(seen.values())
    return {'manual_cut_token_ids':cuts,'manual_restored_token_ids':restored,'manual_changes':groups}


LAYOUT='文稿分段.json'


def outline(units,tokens=()):
    """文稿怎么排版（审核/文稿分段.json）：AI 按话题写的章节 sections [{start: 句编号, title}]，
    用户在网页里按回车分的自然段 breaks [段首字编号]。只管显示，不改剪辑，所以单独存、不进审核状态。
    没有或写错的条目就不用；没有章节时网页按“第一个/第二件…”自动分章。2026-11-13 用户：要像文章，一章一整段，自然段自己分。"""
    try:value=json.loads((RUN/LAYOUT).read_text(encoding='utf-8'))
    except (OSError,ValueError):value=None
    if value is not None and not isinstance(value,dict):return None
    value=auto_breaks(value,units,tokens)
    if value is None:return None
    known={u['id'] for u in units};ids={t['id'] for t in tokens}
    sections=[{'start':row['start'],'title':row['title'].strip()} for row in value.get('sections') or []
              if isinstance(row,dict) and row.get('start') in known and isinstance(row.get('title'),str) and row['title'].strip()]
    breaks=sorted({i for i in value.get('breaks') or [] if isinstance(i,str) and i in ids})
    return {'sections':sorted(sections,key=lambda row:row['start']),'breaks':breaks} if sections or breaks else None


PARA_TARGET=150     # 自动分段：一段攒够这么多字，到句末就另起一段
PARA_MAX=260        # 按文案对出来的段比这长，再按上面的字数拆


def script_paragraphs():
    """标准项目的定稿文案（03_成片/<项目名>.md，老项目在 02_制作/）里「口播正文」的自然段；不是标准项目、没有文案返回 []。"""
    from source_guard import standard_project_root
    root=standard_project_root(RUN)
    if not root:return []
    text=next((f.read_text(encoding='utf-8',errors='replace') for f in (root/'03_成片'/(root.name+'.md'),root/'02_制作'/(root.name+'.md')) if f.is_file()),'')
    lines=text.split('\n');start=next((i+1 for i,l in enumerate(lines) if l.strip().startswith('## 口播正文')),None)
    if start is None:return []
    end=next((i for i in range(start,len(lines)) if lines[i].startswith('## ')),len(lines))
    out,cur=[],[]
    for l in lines[start:end]+['']:
        l=l.strip()
        if not l or l.startswith(('#','>')):
            if cur:out.append(''.join(cur));cur=[]
        else:cur.append(l)
    return out


def _plain(text):return ''.join(c for c in text if c.isalnum())


def default_breaks(units,tokens,paragraphs=(),sections=()):
    """文稿默认怎么分自然段（2026-11-22 用户：“不能一整个自然段，应该……按照咱们写文案的时候是几个自然段，或者说分成3到4个自然段这样比较好改”）。
    先按定稿文案的自然段：每段开头几个字在口播里按顺序找对应的那一句；对不上的段跳过。再把太长的段（和没有文案时的整篇）按字数在句末拆开。
    返回段首字的编号（每句第一个字）；章节开头本来就另起一段，不重复记。只管显示，不改剪辑。"""
    import difflib
    first={}
    for t in tokens:first.setdefault(t['unit_id'],t['id'])
    us=[u for u in units if u['id'] in first];plain=[_plain(u['text']) for u in us]
    starts={row['start'] for row in sections};cut=set();j=1
    for para in list(paragraphs)[1:]:
        key=_plain(para)[:8]
        if len(key)<4:continue
        for k in range(j,min(len(us),j+60)):
            head=plain[k][:len(key)]
            if head[:4]==key[:4] or difflib.SequenceMatcher(None,head,key).ratio()>=0.75:cut.add(k);j=k+1;break
    size=0;limit=PARA_MAX if cut else PARA_TARGET   # 按文案对上了：只拆特别长的段；没对上：按字数分
    for k,u in enumerate(us):                       # 太长的再按字数拆（在句末）
        if k==0 or k in cut or u['id'] in starts:size=0
        elif size>=limit:cut.add(k);size=0
        size+=len(plain[k])
    return [first[us[k]['id']] for k in sorted(cut) if us[k]['id'] not in starts]


def auto_breaks(value,units,tokens):
    """分段文件里还没分过自然段（没有 breaks，也没记过 auto）时，自动分一次并存下来；之后用户按回车、删除键改的都照旧存在同一个文件里，不会再自动分。"""
    if not tokens or (value or {}).get('auto') or (value or {}).get('breaks'):return value
    value=dict(value or {})
    sections=[row for row in value.get('sections') or [] if isinstance(row,dict) and 'start' in row]
    try:value['breaks']=default_breaks(units,tokens,script_paragraphs(),sections)
    except Exception:return value or None
    value['auto']='自然段是程序按定稿文案（没有就按字数）自动分的；用户在网页里按回车、删除键可以改'
    path=RUN/LAYOUT;temporary=path.with_name('.'+path.name+'.partial')
    try:temporary.write_text(json.dumps(value,ensure_ascii=False,indent=1),encoding='utf-8');temporary.replace(path)
    except OSError:pass
    return value


def set_break(ident,on):
    """网页里按回车（on=True）从这个字起另起一段，按删除键（on=False）和上一段合并。"""
    aligned=alignment()
    if not aligned or not isinstance(ident,str) or ident not in {t['id'] for t in aligned['tokens']}:raise ValueError('Unknown character')
    if type(on) is not bool:raise ValueError('Invalid paragraph break')
    path=RUN/LAYOUT
    try:value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):value={}
    if not isinstance(value,dict):value={}
    breaks=[i for i in value.get('breaks') or [] if i!=ident]+([ident] if on else [])
    value['breaks']=breaks
    temporary=path.with_name('.'+path.name+'.partial')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=1),encoding='utf-8');temporary.replace(path)


def payload(manifest,state):
    aligned=alignment();semantic_cuts=effective_cuts(manifest['cards'],state)
    units=json.loads((RUN/'sentence_units.json').read_text())
    mapping=active_map(manifest['cards'],state,aligned)
    cuts=mapping['cut_frames'];segments=mapping['segments']
    editor=state.get('editor',{})
    return {'name':NAME,'sections':CONFIG.get('sections'),'alignment':aligned,'units':units,
            'cuts':cuts,'text_cuts':semantic_cuts,'segments':segments,'fps':FPS,'total_frames':TOTAL,
            'duration':sum(s['tl_out']-s['tl_in'] for s in segments)/FPS,
            'cursor':editor.get('cursor',0),'history_length':len(editor.get('operations',[])),
            'revision':editor.get('revision',0),'state_revision':state_revision(state),
            'removed_token_ids':mapping.get('removed_token_ids'),
            'word_order':(lambda order:order if order!=[t['id'] for t in aligned['tokens']] else None)(word_order(state,aligned['tokens'])) if aligned else None,
            **manual_changes(manifest,state,aligned,mapping.get('removed_token_ids',[]),units),
            'outline':outline(units,aligned['tokens'] if aligned else ()),'notes':state.get('notes',{}),
            'subtitle_token_corrections':state.get('subtitle_token_corrections',[]),
            'prior_choices':len(state.get('overrides',{})),
            'pause_count':len(state.get('pause_edits',{}).get('accepted',[])),
            'repeat_count':sum(x['status']=='apply' for x in state.get('repeat_edits',{}).get('candidates',[]))}


def change(state,request,manifest):
    aligned=alignment()
    if not aligned:raise ValueError('Character alignment is not available')
    if request.get('alignment_id')!=aligned['alignment_id']:raise StateConflict('逐字时间戳已更新，请刷新后重试。')
    if 'state_revision' in request and request['state_revision']!=state_revision(state):
        raise StateConflict('其他页面已保存修改，已停止本次操作；请核对最新文字后重试。')
    s=copy.deepcopy(state)
    editor=s.setdefault('editor',{'alignment_id':aligned['alignment_id'],'operations':[],'cursor':0,'revision':0})
    if editor['alignment_id']!=aligned['alignment_id']:raise ValueError('Saved edits use a different alignment')
    if type(request.get('revision')) is not int or request['revision']!=editor.get('revision',0):
        raise StateConflict('其他页面已保存修改，已停止本次操作；请核对最新文字后重试。')
    operations=editor['operations'];cursor=editor['cursor'];action=request.get('action')
    if action=='undo':
        if cursor<=0:raise ValueError('Nothing to undo')
        editor['cursor']-=1
    elif action=='redo':
        if cursor>=len(operations):raise ValueError('Nothing to redo')
        editor['cursor']+=1
    elif action=='move':
        # 挪一段（网页里是一个分句：按逗号、句号分开）：ids 是按当前顺序连在一起的字，挪到 before 这个字前面（None＝挪到最后）
        known={t['id'] for t in aligned['tokens']}
        ids,before=request.get('ids'),request.get('before')
        if (not isinstance(ids,list) or not ids or len(set(ids))!=len(ids)
                or any(not isinstance(i,str) or i not in known for i in ids)):raise ValueError('Invalid selection')
        if before is not None and (not isinstance(before,str) or before not in known or before in ids):
            raise ValueError('Invalid move target')
        order=word_order(s,aligned['tokens']);where={i:k for k,i in enumerate(order)}
        positions=sorted(where[i] for i in ids)
        if positions!=list(range(positions[0],positions[0]+len(ids))):raise ValueError('一次只能挪连在一起的一段；此次未修改。')
        ids=[order[k] for k in positions]
        if apply_move(order,ids,before)==order:raise ValueError('位置没有变化；此次未修改。')
        lookup={t['id']:t['text'] for t in aligned['tokens']}
        # side：用户松手在目标小句的前半截（before）还是后半截（after）；只用来决定网页里显示在哪一章，不影响剪辑
        side=request.get('side') if request.get('side') in ('before','after') else 'after'
        editor['operations']=operations[:cursor]+[{'action':'move','ids':ids,'before':before,'side':side,'text':''.join(lookup[i] for i in ids)}]
        editor['cursor']=cursor+1
    elif action in ('cut','restore'):
        previous_mapping=active_map(manifest['cards'],state,aligned)
        ids=request.get('ids')
        if not isinstance(ids,list) or not ids or len(ids)>len(aligned['tokens']):raise ValueError('Invalid selection')
        lookup={t['id']:t for t in aligned['tokens']}
        if any(not isinstance(i,str) or i not in lookup for i in ids):raise ValueError('Unknown character')
        selected=sorted({i:lookup[i] for i in ids}.values(),key=lambda t:(t['unit_id'],t['char_start']))
        # Keep distinct selections distinct. Adjacent selected tokens include their intervening pause.
        indices={t['id']:i for i,t in enumerate(aligned['tokens'])};groups=[]
        for t in selected:
            if groups and indices[t['id']]==groups[-1]['last']+1:
                groups[-1]['tokens'].append(t);groups[-1]['last']=indices[t['id']]
            else:groups.append({'tokens':[t],'last':indices[t['id']]})
        ranges=[]
        for group in groups:
            first,last=group['tokens'][0],group['tokens'][-1]
            if not first['ready'] or not last['ready']:
                raise ValueError('选区起止字的边界尚待核实，请把相邻的字一并选入；此次未修改。')
            a,b,_=word_frame_span(first['start_ms'],last['end_ms'],TOTAL)
            ranges.append([a,b])
        operation={'action':action,'ranges':ranges,
                   'ids':[t['id'] for t in selected],'text':''.join(t['text'] for t in selected)}
        editor['operations']=operations[:cursor]+[operation];editor['cursor']=cursor+1
        next_mapping=active_map(manifest['cards'],s,aligned)
        changed_ids=set(previous_mapping['removed_token_ids'])^set(next_mapping['removed_token_ids'])
        if changed_ids-set(ids):
            raise ValueError('Cannot separate two spoken words within one video frame; 请把同一帧中的相邻字一并选中，此次未修改。')
    else:raise ValueError('Unknown editor action')
    if not active_map(manifest['cards'],s,aligned)['segments']:raise ValueError('Cannot delete every spoken word')
    editor['revision']=editor.get('revision',0)+1;s['confirmed_at']=None
    return s
