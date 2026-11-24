"""Compare the user's script with the words actually kept; the agent decides corrections.

The spoken words stay the source of truth. The script only helps find homophone
ASR errors for subtitles and script sentences that were never spoken.
"""
import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from project_runtime import RUN
from auto_review import write
from review_build import active_map, proofread_problem
from prepare_mvp_delivery import exclusive_lock

SCRIPT = RUN/'文案.txt'
REPORT = RUN/'文案对照.json'
SHEET = RUN/'字幕校对.json'
GLOSSARY = Path(__file__).resolve().parents[2]/'词库.txt'      # 所有 IP 共用的专有名词


def ip_glossary():
    """这条片子所属 IP 自己的专有名词（IP 名、口头禅这类）：配置档/<IP>/词库.txt，没有就 None。"""
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[3]/'bin')); import cutdesk_config
    p = cutdesk_config.load(project=RUN).folder/'词库.txt'
    return p if p.is_file() else None
RULES = ['逐句通读保留下来的字幕，改正识别错字；字幕以现场实际说的话为准。',
         '有文案时：数字、型号、品牌、专有名词按文案写法（如“X五百”→“X500”、“长脚”→“长焦”）；现场换了说法的地方不套用文案。',
         '人名、品牌、型号等专有名词按 glossary 的写法；wrong 里的识别结果直接改成 right。',
         '没有文案时：结合上下文判断原意，改正同音字、错字和明显听错的词。',
         '某处识别得和实际说的完全不一样时，按说话人的原意改写这几个字的字幕。',
         '连续多字可写成 {"ids":["u3c1","u3c2"],"before":"Mac Studio","after":"Mac Studio","reason":"专有名词"}，ids 按原顺序、同句连续；范围整体替换，空格只写在 after 中。',
         '每处改动写成 {"id","before","after","reason"}；after 不能为空。全部看完后用 corrections --done 标记校对完成（没有要改的也要标记）。']
STATE = RUN/'review_v2_state.json'


def kept_tokens(manifest, state, aligned):
    retained = set(active_map(manifest['cards'], state, aligned)['retained_token_ids'])
    return [t for t in aligned['tokens'] if t['id'] in retained]


def compare(tokens, script):
    spoken, owner = [], []
    for t in tokens:
        for c in t['text']:
            if c.isalnum():
                spoken.append(c.lower()); owner.append(t)
    written = [(i, c.lower()) for i, c in enumerate(script) if c.isalnum()]
    matcher = SequenceMatcher(None, spoken, [c for _, c in written], autojunk=False)
    equal = [False] * len(written)
    candidates = []
    for op, a, b, c, d in matcher.get_opcodes():
        if op == 'equal':
            equal[c:d] = [True] * (d - c)
        elif op == 'replace' and b - a <= 8 and d - c <= 16:
            # Longer mismatches are different wording or unspoken script, not ASR typos.
            ids = list(dict.fromkeys(t['id'] for t in owner[a:b]))
            candidates.append({'token_ids': ids, 'spoken': [t['text'] for t in tokens if t['id'] in ids],
                               'script': script[written[c][0]:written[d-1][0]+1],
                               'context': ''.join(spoken[max(0, a-8):a]) + '【' + ''.join(spoken[a:b]) + '】' + ''.join(spoken[b:b+8])})
    # Script sentences mostly absent from the kept words: possibly never spoken.
    missing, start = [], 0
    for match in re.finditer(r'[^。！？!?；;\n]+', script):
        idx = [k for k, (i, _) in enumerate(written) if match.start() <= i < match.end()]
        if idx and sum(equal[k] for k in idx) / len(idx) < 0.5:
            missing.append(match.group().strip())
    return {'candidates': candidates, 'possibly_missing': missing}


def parse_glossary(path):
    """One preferred spelling per line, optionally with ASR variants on the left."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        wrong, separator, right = line.partition('=>')
        if not separator:
            right, wrong = line, ''
        right = right.strip()
        if right:
            entries.append({'right': right, 'wrong': [word.strip() for word in wrong.split('|') if word.strip()]})
    return entries


def proofread_sheet(manifest, state, aligned):
    """Kept subtitle text per sentence, with word IDs, for the agent to proofread before any delivery."""
    fixes = {}
    for r in state.get('subtitle_token_corrections', []):
        for i, ident in enumerate(r.get('ids', [r['id']])):
            fixes[ident] = r['after'] if i == 0 else ''
    sentences = {}
    for t in kept_tokens(manifest, state, aligned):
        sentences.setdefault(t['unit_id'], []).append([t['id'], t['text'], fixes.get(t['id'])])
    rows = [{'unit_id': u, 'subtitle': ''.join(text if fix is None else fix for _, text, fix in words),
             'words': [[i, text] + ([fix] if fix is not None else []) for i, text, fix in words]} for u, words in sentences.items()]
    own = ip_glossary()
    sheet = {'rules': RULES, 'glossary': (parse_glossary(own) if own else []) + parse_glossary(GLOSSARY), 'script_used': SCRIPT.exists(), 'sentences': rows}
    if SCRIPT.exists() and REPORT.exists():
        sheet['script_candidates'] = json.loads(REPORT.read_text())['candidates']
    return sheet


def validate_rows(rows, aligned, allow_empty=False):
    """Rows bound to the exact original word; {"id", "remove": true} drops an earlier correction."""
    tokens = {t['id']: t for t in aligned['tokens']}
    if not isinstance(rows, list) or (not rows and not allow_empty):
        raise ValueError('字幕纠错须为非空列表')
    clean = []
    positions = {t['id']: i for i, t in enumerate(aligned['tokens'])}
    used = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('字幕纠错每条须为对象')
        ids = row.get('ids', [row.get('id')])
        if not isinstance(ids, list) or not ids or any(not isinstance(i,str) or i not in tokens for i in ids):
            raise ValueError('纠错对应的字不存在：' + str(ids))
        ts = [tokens[i] for i in ids]
        if (len(set(ids)) != len(ids) or any(positions[v] != positions[ids[0]]+i for i,v in enumerate(ids))
                or (len(ids)>1 and any(t.get('unit_id') is None or t['unit_id'] != ts[0].get('unit_id') for t in ts)) or used.intersection(ids)):
            raise ValueError('范围纠错须为同一句按顺序连续且不重叠的字编号')
        used.update(ids)
        ident = ids[0]
        identity = {'id': ident, **({'ids': ids} if len(ids)>1 else {})}
        if row.get('remove') is True:
            clean.append({**identity, 'remove': True}); continue
        before = row.get('before')
        original = ''.join(t['text'] for t in ts)
        if not isinstance(before,str) or (original != before if len(ids)==1 else re.sub(r'\s','',original) != re.sub(r'\s','',before)):
            raise ValueError('纠错对应的原字已不一致：' + ident)
        if not isinstance(row.get('after'), str) or not row['after'].strip() or not str(row.get('reason', '')).strip():
            raise ValueError('纠错须写明非空的 after 和 reason：' + ident)
        clean.append({**identity, 'before': before, 'after': row['after'].strip(), 'reason': str(row['reason']).strip()})
    return clean


def merge(state, rows, done=False, kept_ids=None):
    merged = list(state.get('subtitle_token_corrections', []))
    for row in rows:
        ids = set(row.get('ids', [row['id']]))
        for r in merged if not row.get('remove') else []:
            old = r.get('ids', [r['id']])
            if len(old) > 1 and ids.intersection(old) and not ids.issuperset(old):
                # 只改整体纠错里的一部分，会让整段纠错作废、其余字退回识别原文：不悄悄丢，停下让人决定
                raise ValueError(f'「{row.get("before", "")}」属于一段整体纠错「{r.get("before", "")}」→「{r.get("after", "")}」（{len(old)} 个字编号：{"、".join(old)}），'
                                 '不能只改其中一部分，这次没有改动。要改这段的写法：整段重写（ids 写全这几个编号）；'
                                 '要单独改这个字：先撤销整段纠错（网页里选这段的第一个字点“改字幕”、改回原字；或写 {"ids": [这几个编号], "remove": true}），再改')
        merged = [r for r in merged if not ids.intersection(r.get('ids', [r['id']]))]
        if not row.get('remove'):
            merged.append(row)
    result = {**state, 'subtitle_token_corrections': merged}
    if done:
        from datetime import datetime
        # Remember exactly which words were proofread; words restored later must be proofread again.
        result['subtitle_proofread'] = {'at': datetime.now().isoformat(timespec='seconds'), 'script_used': SCRIPT.exists(),
                                        'corrections': len(result['subtitle_token_corrections']),
                                        'token_ids': sorted(kept_ids or [])}
    return result


def apply(path, aligned, done=False):
    rows = validate_rows(json.loads(Path(path).read_text()) if path else [], aligned, allow_empty=done)
    try:
        # Every writer of the review state (page, delivery) holds this lock briefly or for the delivery.
        with exclusive_lock(RUN/'.delivering.lock', '这个项目正在保存到达芬奇或正在写入审核状态，请稍后再写入字幕纠错', wait=3):
            return write_directly(rows, aligned, done)
    except RuntimeError as error:
        raise ValueError(str(error)) from None


def write_directly(rows, aligned, done):
    """Under the delivery lock: back up the exact old state, then replace it."""
    import hashlib
    original = STATE.read_bytes()
    backups = RUN/'review_state_backups'
    backups.mkdir(exist_ok=True)
    backup = backups/(hashlib.sha256(original).hexdigest()+'.json')
    if not backup.exists():
        backup.write_bytes(original)
    state = json.loads(original)
    kept = [t['id'] for t in kept_tokens(json.loads((RUN/'review_v2.json').read_text()), state, aligned)] if done else None
    write(STATE, merge(state, rows, done, kept))
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--script', type=Path)
    parser.add_argument('--corrections', type=Path)
    parser.add_argument('--done', action='store_true', help='标记字幕校对完成')
    parser.add_argument('--sheet', action='store_true', help='生成 审核/字幕校对.json')
    parser.add_argument('--check', action='store_true', help='检查字幕是否已校对（交付前）')
    args = parser.parse_args()
    aligned = json.loads((RUN/'character_alignment.json').read_text())
    if args.corrections or args.done:
        print(json.dumps({'applied_corrections': apply(args.corrections, aligned, args.done),
                          'proofread_done': args.done}, ensure_ascii=False))
        return
    if args.check:
        state = json.loads(STATE.read_text())
        kept = [t['id'] for t in kept_tokens(json.loads((RUN/'review_v2.json').read_text()), state, aligned)]
        problem = proofread_problem(state, kept, aligned['tokens'])
        if problem:
            raise ValueError(problem)
        return
    if args.sheet:
        sheet = proofread_sheet(json.loads((RUN/'review_v2.json').read_text()), json.loads(STATE.read_text()), aligned)
        write(SHEET, sheet)
        print(json.dumps({'sentences': len(sheet['sentences']), 'sheet': str(SHEET)}, ensure_ascii=False))
        return
    if args.script:
        text = args.script.read_text(encoding='utf-8')
        if SCRIPT.exists() and SCRIPT.read_text(encoding='utf-8') != text:
            raise ValueError('项目已有不同的文案；如需更换，先人工确认并移走旧的 审核/文案.txt')
        SCRIPT.write_text(text, encoding='utf-8')
    if not SCRIPT.exists():
        raise ValueError('项目还没有文案；用 --script 提供纯文本文案')
    manifest = json.loads((RUN/'review_v2.json').read_text())
    state = json.loads(STATE.read_text())
    result = compare(kept_tokens(manifest, state, aligned), SCRIPT.read_text(encoding='utf-8'))
    write(REPORT, result)
    print(json.dumps({'candidates': len(result['candidates']), 'possibly_missing': len(result['possibly_missing']),
                      'report': str(REPORT)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
