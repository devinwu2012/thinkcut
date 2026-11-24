#!/usr/bin/env python3
"""只归档索引指定的最终时间线，在独立工程收集媒体并恢复检验。"""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
from pathlib import Path
import sys
import uuid
import shutil

from project import load, write_json, digest

API = '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting'


LOCK_FILE = Path.home() / '.cache/ai-talking-head-edit/resolve-write.lock'      # 和 口播自动剪辑、口播剪辑 同一把达芬奇写入锁（2026-11-21）
_held = None


def write_lock(d, what):
    """写达芬奇前拿锁，拿到就一直拿到本进程结束；别的命令正在写达芬奇就停下说明是谁。两个会话同时写会让达芬奇卡死在“渲染中”。"""
    global _held
    if _held: return
    import fcntl, time
    path = Path(os.environ.get('THINKCUT_RESOLVE_LOCK') or LOCK_FILE); path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, 'a+', encoding='utf-8')
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0); who = f.read().strip(); f.close()
        raise RuntimeError('达芬奇正被另一个命令占用，这次没动达芬奇：' + (who or '不知道是谁') + '。等它做完再跑。')
    f.seek(0); f.truncate()
    f.write(json.dumps({'片子目录': str(d), '命令': what, '进程号': os.getpid(), '开始时间': time.strftime('%F %T')}, ensure_ascii=False)); f.flush()
    _held = f


def connect():
    os.environ.setdefault('RESOLVE_SCRIPT_LIB', '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so')
    sys.path.insert(0, API + '/Modules')
    import DaVinciResolveScript
    r = DaVinciResolveScript.scriptapp('Resolve')
    if not r:
        raise RuntimeError('请先打开达芬奇；工具不会自动启动或切换项目库。')
    return r


def snapshot(tl):
    result = {'start': tl.GetStartFrame(), 'end': tl.GetEndFrame(), 'settings': tl.GetSetting(), 'tracks': {}}
    for kind in ('video', 'audio', 'subtitle'):
        result['tracks'][kind] = []
        for index in range(1, tl.GetTrackCount(kind) + 1):
            clips = []
            for c in tl.GetItemListInTrack(kind, index) or []:
                props = (c.GetProperty() or {}) if kind != 'subtitle' else {}
                keep = ('Pan', 'Tilt', 'ZoomX', 'ZoomY', 'Opacity', 'CompositeMode', 'Scaling', 'CropLeft', 'CropRight', 'CropTop', 'CropBottom', 'AudioVolume')
                clips.append({'name': c.GetName(), 'start': c.GetStart(), 'end': c.GetEnd(), 'source_in': c.GetLeftOffset(), 'properties': {k: props[k] for k in keep if k in props}})
            result['tracks'][kind].append({'name': tl.GetTrackName(kind, index), 'enabled': tl.GetIsTrackEnabled(kind, index), 'clips': clips})
    return result


def project_settings(project):
    # DRT 保存时间线覆盖设置；项目级色彩管理、帧率和采样率也必须保留。
    explicit = {'colorScienceMode', 'isAutoColorManage', 'separateColorSpaceAndGamma',
                'useColorSpaceAwareGradingTools', 'inputDRT', 'outputDRT', 'rcmPresetMode',
                'acesVersion', 'imageResizingGamma', 'videoDataLevels'}
    return {k: v for k, v in (project.GetSetting() or {}).items()
            if k.startswith(('timeline', 'colorSpace', 'colorAces')) or k in explicit}


def clear_copy(pool, folder):
    for child in folder.GetSubFolderList() or []:
        clear_copy(pool, child)
    clips = folder.GetClipList() or []
    if clips and not pool.DeleteClips(clips):
        raise RuntimeError('无法清空隔离工程的旧媒体索引。')


def media_files(folder):
    paths = set()
    for clip in folder.GetClipList() or []:
        raw = clip.GetClipProperty('File Path')
        if raw:
            path = Path(raw)
            if not path.is_file():
                raise RuntimeError('媒体文件不存在，或为需要专门处理的图片序列：' + raw)
            paths.add(path)
    for child in folder.GetSubFolderList() or []:
        paths.update(media_files(child))
    return paths


def relink_missing(pool, folder, project_dir, mappings):
    for clip in folder.GetClipList() or []:
        raw = clip.GetClipProperty('File Path')
        if raw and not Path(raw).is_file():
            relative = mappings.get(raw)
            if not relative:
                raise RuntimeError('归档副本有未定位的离线素材：' + raw)
            new = (project_dir / relative).resolve()
            if not new.is_relative_to(project_dir) or not new.is_file() or new.name != Path(raw).name:
                raise RuntimeError('重连映射须为本项目内、同文件名的真实文件：' + raw)
            if not pool.RelinkClips([clip], str(new.parent)) or not Path(clip.GetClipProperty('File Path')).is_file():
                raise RuntimeError('归档副本重连失败：' + raw)
    for child in folder.GetSubFolderList() or []:
        relink_missing(pool, child, project_dir, mappings)


def collect(pool, folder, destination, sources):
    for clip in folder.GetClipList() or []:
        raw = clip.GetClipProperty('File Path')
        if raw:
            source = Path(raw)
            if raw not in sources:
                sha = digest(source)
                target = destination / '媒体' / sha[:12] / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if digest(target) != sha:
                    raise RuntimeError('收集素材摘要不一致：' + source.name)
                # 2026-11-13 审查：记相对存档目录的路径。以前记绝对路径，项目文件夹挪位置或 NAS 换了挂载名 后封存就报“媒体缺失、越界”
                sources[raw] = {'target': str(target.relative_to(destination)), 'sha256': sha}
            target = destination / sources[raw]['target']
            if not pool.RelinkClips([clip], str(target.parent)):
                raise RuntimeError('存档副本关联收集素材失败：' + source.name)
    for child in folder.GetSubFolderList() or []:
        collect(pool, child, destination, sources)


# 2026-10-29：标准项目的工程放在片子自己的项目库（项目文件夹 02_制作/达芬奇工程，库名=文件夹名，见 resolve_library）。
# 原来直接在达芬奇当前开着的库里做，停在别的库（比如局域网协作库）时会打不开本项目，抽帧验收还会把临时工程导进那个库。
# 现在先记下用户的库、文件夹、工程和时间线，切到片子的库做，做完全部切回。


def with_library(d, res):
    import resolve_library
    return dict(res, database=res.get('database') or resolve_library.db(d))      # 项目.json 的 resolve.database 可以另指


def target_db(res):
    return res['database']


def same_db(a, b):
    return bool(a) and bool(b) and a.get('DbType') == b.get('DbType') and a.get('DbName') == b.get('DbName')


def where(pm):
    """当前的库、文件夹链（从根开始）、工程、时间线；记完回到原文件夹。"""
    cur = pm.GetCurrentProject(); tl = cur.GetCurrentTimeline() if cur else None
    chain = []
    while True:
        chain.insert(0, pm.GetCurrentFolder())
        if not pm.GotoParentFolder(): break
    goto(pm, None, chain)
    return {'db': pm.GetCurrentDatabase(), 'folders': chain, 'project': cur.GetName() if cur else None, 'timeline': tl.GetName() if tl else None}


def goto(pm, db, folders):
    import time
    if db and not same_db(pm.GetCurrentDatabase(), db):
        if not any(same_db(x, db) for x in pm.GetDatabaseList() or []):
            raise RuntimeError(f"达芬奇里没有项目库「{db.get('DbName')}」。新片子还没建工程：运行 project.py resolve-open 项目目录（达芬奇开着也行，会自动借用当前开着的库）；"
                               "老片子的库不见了：先保存并退出达芬奇，运行 project.py resolve-library 项目目录 登记。")
        if not pm.SetCurrentDatabase(db) or not same_db(pm.GetCurrentDatabase(), db):
            raise RuntimeError(f"切不到达芬奇项目库「{db.get('DbName')}」。")
        time.sleep(2)
    pm.GotoRootFolder()
    for f in (folders or [])[1:]:
        if not pm.OpenFolder(f):
            raise RuntimeError(f'达芬奇项目库里找不到文件夹「{f}」。')


def keep_project_drp(d, project_name, seed):
    """存档成功后，把整个工程（全部时间线）的 .drp 放进 02_制作/达芬奇工程/<工程名>.drp，覆盖上一份。
    2026-10-29 用户：项目库里的工程是文件夹，要在这里看得见一个工程文件。这是存档那一刻的快照，之后在达芬奇里改了不会跟着变。"""
    import resolve_library
    folder = resolve_library.folder(d); folder.mkdir(parents=True, exist_ok=True)
    target = folder / (project_name + '.drp'); tmp = folder / ('.' + project_name + '.drp.写入中')
    shutil.copy2(seed, tmp)
    if digest(tmp) != digest(seed):
        tmp.unlink(missing_ok=True); raise RuntimeError('工程 .drp 复制到 达芬奇工程 时内容不一致。')
    tmp.replace(target)
    return target


def remember_user_project(pm, res, switch):
    """达芬奇开着的不是本项目（或不在本项目的库）时：没加 --switch 就停（先问用户）；加了就保存用户工程，
    返回它所在的库、文件夹、工程和时间线供最后切回。开着的就是本项目时返回 None。"""
    project_name = res.get('project'); cur = pm.GetCurrentProject()
    if cur and cur.GetName() == project_name and same_db(pm.GetCurrentDatabase(), target_db(res)):
        return None
    name = cur.GetName() if cur else None; db = (pm.GetCurrentDatabase() or {}).get('DbName')
    if not switch:
        raise RuntimeError(f'达芬奇当前开的是「{db}」库里的《{name}》，不是片子项目库里的本项目《{project_name}》。先问用户；同意后加 --switch，会先保存《{name}》，做完切回。')
    if cur and not pm.SaveProject():
        raise RuntimeError(f'保存《{name}》失败，没有切换。')
    return where(pm)


def open_project(pm, res, name=None):
    goto(pm, target_db(res), res.get('folders'))
    return pm.LoadProject(name or res.get('project'))


def restore_user_project(pm, back):
    if not back:
        return
    goto(pm, back['db'], back['folders'])
    if not back['project']:
        return
    q = pm.LoadProject(back['project'])
    if not q:
        raise RuntimeError(f"没能切回「{back['db'].get('DbName')}」库里的《{back['project']}》，请在达芬奇手动打开它。")
    if back['timeline']:
        for i in range(1, q.GetTimelineCount() + 1):
            if q.GetTimelineByIndex(i).GetName() == back['timeline']:
                q.SetCurrentTimeline(q.GetTimelineByIndex(i)); break


def restore_quietly(pm, back):
    try:
        restore_user_project(pm, back)
    except Exception as exc:
        print('注意：' + str(exc), file=sys.stderr)


def archive(directory, switch=False):
    d, meta = load(directory)
    project_name, timeline_name = meta['resolve'].get('project'), meta['resolve'].get('timeline')
    if not project_name or not timeline_name:
        raise RuntimeError('先在项目索引中指定达芬奇工程及最终时间线。')
    write_lock(d, 'project.py archive'); r = connect(); pm = r.GetProjectManager()
    res = with_library(d, meta['resolve'])
    user_back = remember_user_project(pm, res, switch)
    try:      # 正式开始前失败也要切回用户原来的库和工程（2026-10-29 审查）
        original = pm.GetCurrentProject() if not user_back else open_project(pm, res)
        if not original or original.GetName() != project_name:
            raise RuntimeError(f"项目库「{target_db(res)['DbName']}」里打不开本项目工程《{project_name}》（工程在别的库或文件夹时，在 项目.json 的 resolve 里写 database / folders）。")
        original_timeline = original.GetCurrentTimeline()
        original_timeline_name = original_timeline.GetName() if original_timeline else None
        target = next((original.GetTimelineByIndex(i) for i in range(1, original.GetTimelineCount() + 1) if original.GetTimelineByIndex(i).GetName() == timeline_name), None)
        if target is None:
            raise RuntimeError('工程中没有索引指定的最终时间线。')
        if not pm.SaveProject():
            raise RuntimeError('原工程保存失败。')
        wanted = snapshot(target)
        wanted_project_settings = project_settings(original)
        out = d / '04_达芬奇归档' / datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d_%H%M%S')
        out.mkdir()
        timeline_file = out / '最终时间线.drt'
        if not target.Export(str(timeline_file), r.EXPORT_DRT):
            raise RuntimeError('最终时间线导出失败。')
    except Exception:
        restore_quietly(pm, user_back); raise
    unique = uuid.uuid4().hex[:10]
    temporary_name, verify_name = '__归档_' + unique, '__恢复核验_' + unique
    created = []
    report = {'project': project_name, 'timeline': timeline_name, 'format': 'drp+collected_media', 'state': 'in_progress', 'structural_verification': False, 'visual_review_required': True}
    archive_path = out / (meta['name'] + '.drp')
    seed = out / '__配置临时.drp'
    try:
        # 部分播放帧率/色彩参数不能通过 SetSetting 完整重建，先克隆原工程配置。
        # 删除时间线与媒体索引只发生在本次唯一命名的隔离副本；源工程不修改。
        if not pm.ExportProject(project_name, str(seed), True) or not pm.ImportProject(str(seed), temporary_name):
            raise RuntimeError('隔离工程配置克隆失败。')
        created.append(temporary_name)
        p = pm.LoadProject(temporary_name)
        if not p or p.GetName() != temporary_name:
            raise RuntimeError('未进入隔离副本，停止后续操作。')
        pool = p.GetMediaPool()
        timelines = [p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount() + 1)]
        if timelines and not pool.DeleteTimelines(timelines):
            raise RuntimeError('无法重置隔离副本的时间线。')
        clear_copy(pool, pool.GetRootFolder())
        if project_settings(p) != wanted_project_settings:
            raise RuntimeError('隔离副本未保留原工程设置。')
        tl = pool.ImportTimelineFromFile(str(timeline_file))
        if tl and tl.GetName() != timeline_name:
            if not tl.SetName(timeline_name):
                raise RuntimeError('存档副本的时间线名称无法保留。')
        if not tl or snapshot(tl) != wanted:
            raise RuntimeError('最终时间线导入副本后结构不一致。')
        relink_missing(p.GetMediaPool(), p.GetMediaPool().GetRootFolder(), d, meta['resolve'].get('relinks', {}))
        dependencies = media_files(p.GetMediaPool().GetRootFolder())
        print(f'收集 {len(dependencies)} 个最终时间线素材……', flush=True)
        sources = {}
        collect(p.GetMediaPool(), p.GetMediaPool().GetRootFolder(), out, sources)
        hashes = {v['sha256'] for v in sources.values()}
        write_json(out / '媒体清单.json', sources)
        if not pm.SaveProject():
            raise RuntimeError('归档副本保存失败。')
        if not pm.ExportProject(temporary_name, str(archive_path), True):
            raise RuntimeError('独立工程 DRP 导出失败。')
        pm.CloseProject(p)
        print('导入存档工程并重开验证……', flush=True)
        if not pm.ImportProject(str(archive_path), verify_name):
            raise RuntimeError('存档工程导入失败；保留所有源文件。')
        created.append(verify_name)
        p = pm.LoadProject(verify_name)
        if not p or p.GetTimelineCount() != 1:
            raise RuntimeError('恢复后未得到唯一的最终时间线。')
        if project_settings(p) != wanted_project_settings:
            # 2026-10-24 实测：工程里只有一条自定义 4K 竖版时间线时，导入 DRP 后达芬奇会把工程默认分辨率/播放帧率改成那条时间线的值。
            # 时间线本身不变；把被改的工程默认值设回原值再核对，设不回去才停止。
            for k, v in wanted_project_settings.items():
                if (p.GetSetting(k) or '') != v:
                    p.SetSetting(k, v)
            pm.SaveProject()
        got_settings = project_settings(p)
        bad = sorted(k for k in set(wanted_project_settings) | set(got_settings) if got_settings.get(k) != wanted_project_settings.get(k))
        tl_rate = str(p.GetTimelineByIndex(1).GetSetting('timelineFrameRate') or '')
        # 播放帧率设不回去时（达芬奇把它锁成唯一时间线的帧率），只要等于最终时间线自己的帧率就接受，并记进报告
        if bad == ['timelinePlaybackFrameRate'] and got_settings.get('timelinePlaybackFrameRate') == tl_rate:
            report['playback_rate_note'] = f"工程默认播放帧率 {wanted_project_settings.get('timelinePlaybackFrameRate')} → {tl_rate}（等于最终时间线帧率，达芬奇导入后锁定）"
        elif bad:
            raise RuntimeError('恢复后项目色彩、帧率或采样设置改变：' + '、'.join(bad))
        tl = p.GetTimelineByIndex(1)
        if snapshot(tl) != wanted:
            raise RuntimeError('恢复后轨道、片段或字幕位置不一致。')
        restored = media_files(p.GetMediaPool().GetRootFolder())
        if not restored or any(not path.resolve().is_relative_to(out.resolve()) for path in restored):
            raise RuntimeError('恢复工程仍引用归档之外的媒体。')
        if {digest(path) for path in restored} != hashes:
            raise RuntimeError('恢复媒体与原媒体摘要不一致。')
        pm.SaveProject(); pm.CloseProject(p); p = pm.LoadProject(verify_name)
        if not p or snapshot(p.GetTimelineByIndex(1)) != wanted:
            raise RuntimeError('保存重开后的时间线结构不一致。')
        report['project_drp'] = str(keep_project_drp(d, project_name, seed).relative_to(d))
        report.update(state='structure_and_media_verified', structural_verification=True, media_count=len(restored), archive=str(archive_path), note='DRP 与媒体目录应一起保留，不能只移动 DRP。当前项目路径下已验证；搬到另一位置后须关联媒体目录。轨道、片段及媒体摘要已核对；仍需抽看关键画面，特别是 Fusion 外链、字体和插件。')
        from project import asset_path
        if meta['assets'].get('video'):
            report['delivery_sha256'] = digest(asset_path(d, meta['assets']['video']))
        _, latest = load(d)
        latest['resolve'].update(archive=str(archive_path.relative_to(d)), report=str((out / '归档验收.json').relative_to(d)))
        write_json(d / '项目.json', latest)
        write_json(out / '时间线快照.json', wanted)
        write_json(out / '工程设置.json', wanted_project_settings)
        if latest['resolve'].get('borrowed'):      # 2026-11-23 初稿模式实测：达芬奇开着时建的新片子，工程借用了别的库
            print(f"提醒：这条片子的工程暂存在「{latest['resolve']['borrowed'].get('库')}」库（建工程时达芬奇开着）。存档已做完；"
                  f'迁回片子自己的库：python3 {Path(__file__).resolve().parent}/project.py resolve-library "{d}" --migrate（达芬奇关着时跑一次登记库，下次开着时再跑一次迁过去；不迁也不影响用）。', flush=True)
    except Exception as exc:
        report.update(state='failed', error=str(exc))
        raise
    finally:
        write_json(out / '归档验收.json', report)
        seed.unlink(missing_ok=True)
        current = pm.GetCurrentProject()
        if current and current.GetName() in created:
            pm.CloseProject(current)
        back = pm.LoadProject(project_name)
        if back and original_timeline_name:
            for i in range(1, back.GetTimelineCount() + 1):
                candidate = back.GetTimelineByIndex(i)
                if candidate.GetName() == original_timeline_name:
                    back.SetCurrentTimeline(candidate); break
        # 只清理本次自己创建的临时工程；归档与用户原工程不删。
        if back:
            for name in created:
                pm.DeleteProject(name)
        else:
            report.update(original_project_restored=False)
            write_json(out / '归档验收.json', report)
            raise RuntimeError('归档结束但未能回到原工程，请在达芬奇手动打开原工程。')
        restore_user_project(pm, user_back)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def archive_stills(directory, seconds=(3, 0.5, 0.78, -3), switch=False):
    """画面验收用（2026-10-24 补）：把最近一次存档 DRP 导入临时工程，按秒抽帧，和 03_成片 同一帧并排拼图。
    seconds 里 0–1 之间的数按片长比例算，负数从片尾倒数。只生成对比图，是否通过由人看图判断后写进 归档验收.json。"""
    import subprocess, time
    d, meta = load(directory)
    archive = d / meta['resolve'].get('archive', '')
    video = d / meta['assets'].get('video', '')
    if not archive.is_file() or not video.is_file():
        raise RuntimeError('先完成 archive，且 项目.json 里要有成片路径。')
    out = archive.parent / '画面验收'; out.mkdir(exist_ok=True)
    write_lock(d, 'project.py archive-stills'); r = connect(); pm = r.GetProjectManager()
    res = with_library(d, meta['resolve'])
    back = remember_user_project(pm, res, switch)
    if not back:      # 开着的就是本项目：也要先存、最后切回它
        if not pm.SaveProject(): raise RuntimeError('本项目工程保存失败，没有继续。')
        back = where(pm)
    tmp = '_验收抽帧_' + uuid.uuid4().hex[:6]
    try:      # 临时工程只导进片子自己的项目库，不进用户当时开着的库
        goto(pm, target_db(res), res.get('folders'))
        if not pm.ImportProject(str(archive), tmp):
            raise RuntimeError('存档 DRP 导入失败。')
    except Exception:
        restore_quietly(pm, back); raise
    pairs = []
    try:
        p = pm.LoadProject(tmp)
        if not p:
            raise RuntimeError('存档 DRP 导入后打不开临时工程。到达芬奇里关掉弹窗后重跑；还不行就重新 archive。')
        tl = p.GetTimelineByIndex(1)
        if not tl:
            raise RuntimeError('存档 DRP 里没有时间线，抽不了帧。重新 archive 后再验收。')
        p.SetCurrentTimeline(tl); r.OpenPage('edit')
        fps = float(tl.GetSetting('timelineFrameRate') or 29.97); nominal = round(fps); st = tl.GetStartFrame()
        length = (tl.GetEndFrame() - st) / fps
        for sec in seconds:
            t = sec * length if 0 < sec < 1 else (length + sec if sec < 0 else sec)
            f = round(t * fps); tc = st + f
            code = f"{tc // nominal // 3600:02}:{tc // nominal // 60 % 60:02}:{tc // nominal % 60:02}:{tc % nominal:02}"
            if str(tl.GetSetting('timelineDropFrameTimecode') or '0') not in ('0', 'False', 'false'):
                raise RuntimeError('时间线是丢帧时间码，抽帧换算不支持；请手动抽帧验收。')
            if not tl.SetCurrentTimecode(code) or tl.GetCurrentTimecode() != code:
                raise RuntimeError(f'跳到 {code} 失败（当前 {tl.GetCurrentTimecode()}），没有导出这一帧。')
            time.sleep(1.5)
            a = out / f'存档_{t:.1f}s.png'; b = out / f'成片_{t:.1f}s.png'
            if not p.ExportCurrentFrameAsStill(str(a)):
                raise RuntimeError(f'第 {t:.1f} 秒抽帧失败。')
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{(f + 0.5) / fps:.4f}', '-i', str(video), '-frames:v', '1', str(b)], check=True)
            pairs += [a, b]
        pm.CloseProject(p)
    finally:
        cur = pm.GetCurrentProject()
        if cur and cur.GetName() == tmp:
            pm.CloseProject(cur)
        if not pm.DeleteProject(tmp):      # 还在片子的项目库里删，再切回用户的库
            print(f'注意：临时工程 {tmp} 没删掉，可在达芬奇「{target_db(res)["DbName"]}」项目库里手动删除。', file=sys.stderr)
        restore_user_project(pm, back)
    sheet = out / '恢复工程vs成片.jpg'
    ins = sum((['-i', str(x)] for x in pairs), [])
    n = len(pairs)
    fc = ''.join(f'[{i}]scale=270:-1[v{i}];' for i in range(n)) + ''.join(f'[v{i}]' for i in range(n)) + f'xstack=inputs={n}:grid={n}x1'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', *ins, '-filter_complex', fc, str(sheet)], check=True)
    print(json.dumps({'对比图': str(sheet), '说明': '每组左边是存档恢复的工程，右边是成片；看图确认内容一致后，把 归档验收.json 的 visual_review_required 改为 false 并写 visual_review'}, ensure_ascii=False, indent=2))
    return sheet


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument('directory'); a = ap.parse_args()
    try:
        archive(a.directory)
    except Exception as exc:
        print('停止：' + str(exc), file=sys.stderr); sys.exit(1)
