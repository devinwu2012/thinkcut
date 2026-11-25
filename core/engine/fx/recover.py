"""花字整批写入中途失败后的恢复（2026-11-13，台账 Q061）。

以前失败后操作记录停在“未完成”，build、小修、清理都拒绝，又不许手改记录，没有出路。
这里不动达芬奇里的任何东西，只把操作记录改成和时间线上的实际情况一致：还在的认领下来，没了的从记录里去掉，
去掉“未完成”。之后照常 build，缺的花字和音效按计划补上。默认只演练，--apply 才改记录。
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from . import ledger
from .merge import structure, fingerprint
from .snapshot import snapshot, write_json


def recover(resolve, project, timeline, project_name, work_dir, apply=False):
    work_dir = Path(work_dir); record = ledger.load(work_dir)
    ledger.refuse_legacy(record)
    if not record or not record.get('pending'):
        return {'applied': False, 'needed': False, 'message': '操作记录里没有未完成的写入，不需要恢复。'}
    pending = record['pending']
    if pending.get('status') == 'needs_target':
        raise ValueError('这不是失败，是在等设视频源目标：在剪辑页设好后用 build --resume 续做，不用恢复')
    if pending.get('defer_place') or record.get('built_timeline') != record.get('timeline'):
        raise ValueError('这是以前在主线副本上做花字留下的记录，这种做法已经去掉，恢复命令不管；请保留现场，先看执行证据')
    if record.get('timeline_id') != timeline.GetUniqueId():
        raise ValueError('目标时间线和操作记录不是同一条，请用这条主线自己的工作目录')
    current = project.GetCurrentTimeline()
    if not current or current.GetUniqueId() != timeline.GetUniqueId():
        raise ValueError('恢复前请先在达芬奇里打开这条时间线')
    snap = snapshot(resolve, project, timeline); items = {i['id']: i for i in snap['timeline']['items']}

    effects, lost = [], []
    for row in record.get('effects', []):
        item = items.get(row['item_id'])
        if item is None or item['track'] != row['track'] or len(item.get('texts', [])) != 1:
            lost.append(row['effect_id']); continue
        state = ledger.title_state(item)
        effects.append({**deepcopy(row), 'enabled': item['enabled'], 'written_state': state, 'fingerprint': ledger.digest(state)})
    owned = {e['effect_id'] for e in effects}
    plan_ids = [e['effect_id'] for e in pending.get('plan', {}).get('effects', [])]

    def alive(group):
        rows = [deepcopy(r) for r in record.get(group, []) if r['item_id'] in items]
        seen = {r['item_id'] for r in rows}
        # 失败发生在重放音效/人物层之前时，旧的还在时间线上：接着认领，下次 build 按老规矩处理
        rows += [deepcopy(r) for r in pending.get('removal_record', {}).get(group, []) if r['item_id'] in items and r['item_id'] not in seen]
        return rows
    sounds, mattes = alive('sounds'), alive('mattes')
    changed = ledger.verify_delta(pending['checkpoint'], snap) if pending.get('checkpoint') else ['没有失败时的现场快照，无法比对']
    report = {
        'applied': False, 'needed': True, 'failed_with': pending.get('error') or '（没记下原因，可能是被强行中断）',
        'status': pending.get('status'), 'backup': pending.get('backup'), 'evidence': pending.get('evidence'),
        'titles_on_timeline': sorted(owned),
        'titles_missing': [k for k in plan_ids if k not in owned],        # 计划里有、时间线上现在没有的：下次 build 会补
        'titles_lost_from_record': lost,                                    # 记录里有、时间线上找不到的
        'sounds_on_timeline': len(sounds), 'mattes_on_timeline': len(mattes),
        'changed_since_failure': changed,                                   # 失败之后时间线又被改过的地方（只报告，不拦）
        'next': '核对无误后加 --apply 把操作记录改成和时间线一致；再照常 build，缺的花字和音效按计划补上。时间线本身不会被这条命令改动；要回到写入前，用上面的 backup 时间线备份。',
    }
    if not apply:
        return report
    with ledger.write_lock():       # 恢复不动达芬奇，但要改操作记录：和正在写达芬奇的进程错开
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f'); evidence = work_dir/'执行证据'/(stamp+'_恢复'); evidence.mkdir(parents=True)
        write_json(evidence/'previous_ledger.json', record); write_json(evidence/'timeline.json', snap); write_json(evidence/'report.json', report)
        fixed = {k: deepcopy(v) for k, v in record.items() if k != 'pending'}
        fixed.update(effects=effects, sounds=sounds, mattes=mattes, plan=deepcopy(pending.get('plan', record.get('plan'))),
                     structure=structure(snap['timeline']), structure_fingerprint=fingerprint(snap['timeline']),
                     saved_closed_reopened=False,
                     recovered={'at': datetime.now().astimezone().isoformat(), 'failed_with': report['failed_with'], 'evidence': str(evidence),
                                'titles_missing': report['titles_missing'], 'titles_lost_from_record': lost},
                     updated_at=datetime.now().astimezone().isoformat())
        ledger.save(work_dir, fixed)
    return {**report, 'applied': True, 'recover_evidence': str(evidence),
            'next': '操作记录已和时间线一致。现在照常 build（先演练），缺的花字和音效按计划补上。'}
