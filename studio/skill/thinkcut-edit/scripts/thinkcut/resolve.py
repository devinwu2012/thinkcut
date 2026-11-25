# 达芬奇（DaVinciResolveScript 直连）：设 4K 画幅、搭审核时间线、快照/比对、导出关键帧、改单条字幕、导出 4K 成片。
# 原则：不碰主线（只复制）；同名时间线已存在就停；每次写完都保存→关闭→重开→读回核对。
# 不用达芬奇 MCP 的时间线写操作（会自动复制 _archived 时间线）。
import json, os, sys, time
from pathlib import Path
from .common import Job, die, rj, wj, run, say, probe

API = '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting'
LIB = '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so'
NAMES = {'broll': 'B-roll 卡点动画', 'nav': '章节导航', 'flower': '花字', 'subs': '字幕（新样式）', 'ambience': 'B-roll 原声', 'sfx': '音效'}
KEEP_PROPS = ('Pan', 'Tilt', 'ZoomX', 'ZoomY', 'Opacity', 'CompositeMode', 'Scaling', 'CropLeft', 'CropRight', 'CropTop', 'CropBottom')


def connect():
    os.environ.setdefault('RESOLVE_SCRIPT_LIB', LIB)
    if API + '/Modules' not in sys.path: sys.path.insert(0, API + '/Modules')
    try:
        import DaVinciResolveScript as d
    except ImportError:
        die('找不到达芬奇脚本模块：需要安装 DaVinci Resolve（Studio 版）')
    r = d.scriptapp('Resolve')
    if r is None:
        die('连不上达芬奇：先打开达芬奇，并在 偏好设置 → 系统 → 常规 把“外部脚本使用”设为“本地”；刚打开时等它启动完再试')
    return r


# ---------- 达芬奇写入锁（2026-11-21 新17：两个会话同时重建审核线、渲小样，达芬奇卡死在“渲染中”，只能强制结束） ----------
# 本机固定一个文件，和 口播剪辑 的 B-roll/花字写入是同一把（engine/broll/live/workspace.py write_lock），跟片子无关。
# 用 fcntl.flock 加独占锁：进程退出（做完、崩溃、被杀）系统自动放锁，所以持锁进程不在了锁就自动失效，不用手删。
# 锁文件里写谁在用（JSON：片子目录、命令、进程号、开始时间；口播剪辑 只写进程号），只用来给第二个人看。锁文件不要删（删了会出现两把锁）。
LOCK_FILE = Path.home() / '.cache' / 'ai-talking-head-edit' / 'resolve-write.lock'
_held = None


def _alive(pid):
    try: os.kill(int(pid), 0); return True
    except PermissionError: return True
    except (ProcessLookupError, TypeError, ValueError, OverflowError): return False


def lock(job=None, what=None):
    """拿达芬奇写入锁，拿到就一直拿到本进程结束；拿不到（别的命令正在写达芬奇）直接停，说明是谁在用。同一进程重复拿不重复加。"""
    global _held
    if _held: return _held[1]
    import fcntl
    path = Path(os.environ.get('THINKCUT_RESOLVE_LOCK') or LOCK_FILE); path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, 'a+', encoding='utf-8')
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0); txt = f.read().strip(); f.close()
        try: who = json.loads(txt) if txt.startswith('{') else {'进程号': int(txt), '命令': '口播剪辑（B-roll/花字写入）'}
        except ValueError: who = {}
        pid = who.get('进程号', '?')
        hint = '' if pid == '?' or _alive(pid) else f'（记下的进程 {pid} 已经不在了，锁还被占着：多半是它启动的子进程还在跑）'
        die(f"达芬奇正被另一个命令占用，这次没动达芬奇（两个同时写会让达芬奇卡死在“渲染中”）：{who.get('命令', '不知道哪个命令')}，"
            f"项目 {who.get('片子目录', '不知道')}，进程 {pid}，{who.get('开始时间', '开始时间不知道')} 开始{hint}。\n"
            f"等它做完再跑；确认那个会话已经停了还被拦，就结束那个进程（kill {pid}）后重试。锁文件：{path}")
    info = {'片子目录': str(job.dir) if job is not None else '', '命令': what or ' '.join(sys.argv[1:3]) or 'thinkcut-edit',
            '进程号': os.getpid(), '开始时间': time.strftime('%F %T')}
    f.seek(0); f.truncate(); f.write(json.dumps(info, ensure_ascii=False)); f.flush()
    _held = (f, info); return info


def unlock():
    global _held
    if not _held: return
    import fcntl
    f = _held[0]; _held = None
    try: fcntl.flock(f, fcntl.LOCK_UN)
    finally: f.close()


SWITCH_LOG = ('02_制作', '达芬奇切换记录.json')      # 和 projects/bin/resolve_library.py 的 SWITCH_LOG 是同一个文件、同一种写法


def project_library(root):
    """这条片子的工程该在哪个项目库：默认库名＝项目文件夹名；项目.json 的 resolve.database.DbName 另指时用它
    （2026-11-23 初稿模式实测：达芬奇开着时建的新片子，工程借用当时开着的库，project.py resolve-open 记在这里）。"""
    if not root: return None
    try: named = json.loads((root / '项目.json').read_text(encoding='utf-8'))['resolve']['database']['DbName']
    except (OSError, ValueError, KeyError, TypeError): named = None
    return named if isinstance(named, str) and named else root.name


def remember_switch(root, pm, cur, to):
    """切走用户开着的工程之前记一笔（原来的库、工程、时间线），status 的【初稿五项】会提醒在汇报里告诉用户（2026-11-23 定：不问、保存、留记录）。
    记不成不拦（老片子没有项目文件夹、盘只读）。"""
    if not root or not cur or cur.GetName() == to: return
    try:
        f = root.joinpath(*SWITCH_LOG); tl = cur.GetCurrentTimeline()
        rows = json.loads(f.read_text(encoding='utf-8')) if f.exists() else []
        rows.append({'时间': time.strftime('%Y-%m-%d %H:%M:%S'), '原项目库': (pm.GetCurrentDatabase() or {}).get('DbName'), '原工程': cur.GetName(),
                     '原时间线': tl.GetName() if tl else None, '原因': f'剪辑：切到《{to}》'})
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except Exception: pass


def project(job: Job, write=True):
    """打开片子对应的达芬奇工程；当前开着别的工程时先保存它。write=True（默认）先拿达芬奇写入锁；只读查询（snap）传 False 不拿锁，
    达芬奇正在渲染时只要当前开着的就是这条片子的工程也不拦（要切工程才能读的会拦，见下）。
    2026-10-29：标准项目的工程在片子自己的项目库（库名=项目文件夹名，02_制作/达芬奇工程）。有这个库就只在里面找，
    不去本机库和别的库里找同名工程；老片子没有这个库时才挨个库找。"""
    if write: lock(job)
    r = connect(); pm = r.GetProjectManager(); name = job['project']
    root = next((q for q in job.dir.parents if (q / '项目.json').is_file()), None)
    lib = project_library(root)
    now = pm.GetCurrentDatabase(); all_dbs = pm.GetDatabaseList() or []
    mine = [d for d in all_dbs if d.get('DbType') == 'Disk' and d.get('DbName') == lib]
    cur = pm.GetCurrentProject()
    # 2026-11-21 体检：达芬奇正在渲染时不写它（上一条命令等超时退出、或用户自己在渲）。只读查询（write=False）不拦；先查再切工程
    if write and cur and cur.IsRenderingInProgress(): die('达芬奇正在渲染，这次没动它：等渲完，或去达芬奇里停掉渲染再试')
    if cur and cur.GetName() == name and (not mine or now == mine[0]): return r, pm, cur
    # 只读查询（snap）也一样：达芬奇正在渲染别的工程时不保存、不切工程（切走会打断那边的渲染），停下说明。当前开着的就是这条片子的工程时上一行已经返回，照常只读
    if not write and cur and cur.IsRenderingInProgress(): die(f'达芬奇正在渲染（当前开着的工程是「{cur.GetName()}」，不是这个项目的「{name}」），这次没保存、没切工程：等渲完再看')
    if cur and cur.GetName() != 'Untitled Project':
        pm.SaveProject(); remember_switch(root, pm, cur, name)
    dbs = mine or [now] + [d for d in all_dbs if d != now]
    want = job.get('resolve_db')
    if want: dbs.sort(key=lambda d: d.get('DbName') != want)
    for db in dbs:
        if db != pm.GetCurrentDatabase() and not pm.SetCurrentDatabase(db): continue
        pm.GotoRootFolder()
        if name in (pm.GetProjectListInCurrentFolder() or []):
            p = pm.LoadProject(name)
            if p:
                if job.get('resolve_db') != db.get('DbName'): job.cfg['resolve_db'] = db.get('DbName'); job.save()
                return r, pm, p
    die(f'在达芬奇的各个项目库里都没找到工程「{name}」')


def timelines(p):
    return {p.GetTimelineByIndex(i + 1).GetName(): p.GetTimelineByIndex(i + 1) for i in range(p.GetTimelineCount())}


def timeline(p, name):
    t = timelines(p).get(name)
    if not t: die(f'工程里没有时间线「{name}」')
    return t


def reopen(pm, p, name):
    assert pm.SaveProject(), '保存失败'
    pn = p.GetName(); pm.CloseProject(p); p = pm.LoadProject(pn)
    if not p: die('重开工程失败')
    t = timeline(p, name); p.SetCurrentTimeline(t); return p, t


def track_index(tl, kind, label):
    hits = [j + 1 for j in range(tl.GetTrackCount(kind)) if tl.GetTrackName(kind, j + 1) == label]
    return hits[0] if len(hits) == 1 else None


def tcode(f_abs, fps):
    n = round(fps); return f'{f_abs // (n * 3600):02d}:{f_abs // (n * 60) % 60:02d}:{f_abs // n % 60:02d}:{f_abs % n:02d}'


# ---------- 画幅 ----------
def _fmt(tl, job):
    want = [('useCustomSettings', '1'), ('timelineResolutionWidth', str(job.W)), ('timelineResolutionHeight', str(job.H)),
            ('timelinePixelAspectRatio', 'square'), ('timelineInputResMismatchBehavior', 'scaleToCrop'), ('timelineOutputResMatchTimelineRes', '1')]
    for k, v in want:
        if str(tl.GetSetting(k)) != v: tl.SetSetting(k, v)
    back = {k: tl.GetSetting(k) for k, _ in want}
    return all(str(back[k]) == v for k, v in want), back


def set_format(job: Job, name=None):
    # 主线永远不动（2026-11-13 审查：以前不写 --name 就把主线改成 4K）；审核线的画幅 review 会自己设，这条命令只给别的时间线补设
    if not name: die('要写 --name <时间线>；主线不改画幅（审核线的画幅 review 会自己设）')
    if name == job['main_timeline']: die(f'「{name}」是主线，不改它的画幅；要 4K 画幅就 review 建审核线')
    r, pm, p = project(job); tl = timeline(p, name); p.SetCurrentTimeline(tl)
    ok, back = _fmt(tl, job)
    pm.SaveProject()
    say(f"时间线「{tl.GetName()}」画幅 {back['timelineResolutionWidth']}×{back['timelineResolutionHeight']}，素材缩放裁切填满：{'已设好' if ok else '没设上，看下面'}")
    if not ok: die(json.dumps(back, ensure_ascii=False))


# ---------- 审核时间线 ----------
MARK_PREFIX = ('改：', '新增：', '删：')


def load_changes(job: Job, path):
    """改动清单（2026-11-12《水电阶段》用户：“每次直接给我一个总的时间线……你把需要看的打好点，我上下切换点就能看到”）：
    [{"t": 秒 | "at": 卡点名 | "shot": 镜头id, "name": "改：…", "note": "改了什么"}]。返回 [(成片帧号, 名称, 备注)]。"""
    rows = rj(path); B = rj(job.p('分镜.json')) if job.p('分镜.json').exists() else {'anchors': {}, 'shots': []}
    if not isinstance(rows, list) or not rows: die(f'改动清单 {path} 要是非空列表')
    shots = {x['id']: x for x in B['shots']}; out = []; N = int(job['frames'])
    for x in rows:
        if not isinstance(x, dict) or not x.get('name') or not x.get('note'): die(f'改动清单每条都要有 name（改：…）和 note（改了什么）：{x}')
        if 't' in x: t = float(x['t'])
        elif x.get('at') in B['anchors']: t = B['anchors'][x['at']]
        elif x.get('shot') in shots: t = shots[x['shot']]['a']
        else: die(f'改动清单这条没写 t（秒），at/shot 也对不上分镜：{x}')
        f = job.f(t)
        if not 0 <= f < N: die(f'改动清单 {x["name"]} 在 {t} 秒，超出成片（{N} 帧）')
        name = x['name'] if x['name'].startswith(MARK_PREFIX) else '改：' + x['name']
        out.append((f, name, x['note']))
    # 达芬奇一帧只能打一个标记：落在同一帧的几条自动合成一个（名称、备注用“／”连起来），不再拒收（2026-11-21 新18）
    merged = {}
    for f, name, note in out:
        if f in merged: merged[f] = (merged[f][0] + '／' + name, merged[f][1] + '／' + note)
        else: merged[f] = (name, note)
    if len(merged) < len(out): say(f'【注意】改动清单里有 {len(out) - len(merged)} 条和别的落在同一帧，已合成一个标记')
    return sorted((f, n, t) for f, (n, t) in merged.items())


def flower_rows(job: Job):
    """花字表的整条记录（带 kind 等，人挡字有 遮挡、入场帧）：包装系统写的 合成/花字/花字表.json（{"items": [{file, start, end, …}]}，成片帧号；
    老片子是同样字段的纯列表）；更早的片子在 花字/花字表.json。check 也用这个，路径和花字程序输出一致（新16）。"""
    for f in (job.p('合成', '花字', '花字表.json'), job.dir / '花字' / '花字表.json'):
        if f.exists():
            d = rj(f); return d['items'] if isinstance(d, dict) else d
    return []


def flower_items(job: Job):
    """花字轨要放的片段 [(文件, 起帧, 帧数)]（人挡字也在里面：整幅不透明片段，同样放花字轨）。"""
    # 老花字表的 file 可能是相对路径（huazi 用相对的片子目录跑出来的）：按片子目录补成完整路径，不然导入达芬奇失败（2026-11-21 261118）
    return [(str(job.dir / x['file']) if not Path(x['file']).is_absolute() else x['file'], int(x['start']), int(x['end']) - int(x['start'])) for x in flower_rows(job)]


def check_flowers(job: Job, flowers):
    """建审核线前查花字：文件都在；不拖出片尾（2026-11-21 261118：koubo 从 97.83 秒起拖出片尾，审核线变长，到 voice-polish 才拦下）。"""
    for f, _, _ in flowers:
        if not Path(f).exists(): die(f'花字文件不存在：{f}')
    late = [(Path(f).name, a + n) for f, a, n in flowers if a + n > int(job['frames'])]
    if late: die(f"花字超出片尾（成片 {int(job['frames'])} 帧），审核线会被拖长：" + '；'.join(f'{x} 到第 {e} 帧' for x, e in late) + '。改 花字.json 往前挪或换短的组件，重出花字再建')


def extra_items(job: Job, path):
    """额外轨道清单：[{"kind": "video"|"audio", "track": "轨道名", "file": 路径, "t": 秒, "frames": 帧数, "src_start": 0, "gain_db": 0}]"""
    rows = rj(path); out = {}
    for x in rows:
        if x.get('kind') not in ('video', 'audio') or not x.get('track') or not x.get('file'): die(f'额外轨道清单这条要有 kind（video/audio）、track、file：{x}')
        if not Path(x['file']).exists(): die(f'额外轨道的文件不存在：{x["file"]}')
        out.setdefault((x['kind'], x['track']), []).append(x)
    return out


POLISH_TRACK = '口播（隔离修音）'


def polished_voice(job: Job):
    """口播降噪修音（voice-polish，2026-11-21 新13）出的整条口播：(文件, 轨道名)；没做过返回 None。
    文件没了、长度和成片对不上（剪口播改过）就停：重跑 voice-polish，或从 job.json 删掉 voice_polish 改回用原口播。"""
    v = job.get('voice_polish')
    if not v: return None
    f = Path(v['file'])
    if not f.exists(): die(f'job.json 记着口播修过音，但文件不在了：{f}。重跑 voice-polish，或从 job.json 删掉 voice_polish 改回用原口播')
    from .common import duration
    n = round(duration(f) * float(job.fps))
    if abs(n - int(job['frames'])) > 2: die(f'修音口播 {f.name} 是 {n} 帧，成片 {job["frames"]} 帧（剪口播改过？）：重跑 voice-polish，或从 job.json 删掉 voice_polish')
    return f, v.get('track') or POLISH_TRACK


def voice_track(job: Job, tl):
    """人声在哪条音频轨：做过修音且审核线上有那条轨，就是它；否则 A1。返回 (轨道号, 是不是修音轨)。"""
    v = job.get('voice_polish')
    j = track_index(tl, 'audio', (v.get('track') or POLISH_TRACK)) if v else None
    return (j, True) if j else (1, False)


def _uid(tl):
    try: return tl.GetUniqueId() or None
    except Exception: return None


def drop_timeline(pm, uid, back_to=None):
    """删掉唯一 id 是 uid 的那一条时间线（只删这一条，按 id 认、不按名字），删完保存。删掉了返回 True。back_to：删之前先切过去的时间线名（当前时间线删不了）。"""
    try:
        p = pm.GetCurrentProject()
        if not uid or not p: return False
        all_tl = [p.GetTimelineByIndex(i + 1) for i in range(p.GetTimelineCount())]
        hit = [t for t in all_tl if _uid(t) == uid]
        if len(hit) != 1: return False
        other = next((t for t in all_tl if t.GetName() == back_to and _uid(t) != uid), None)
        if other: p.SetCurrentTimeline(other)
        if not p.GetMediaPool().DeleteTimelines(hit): return False
        pm.SaveProject(); return True
    except Exception:
        return False


BUILDING = ('合成', '审核线建线中.json')       # 正在建的审核线（名字、唯一 id）：建好或删掉半成品后删；进程被硬杀（SIGKILL）时留下，下次 review 先删那条半成品
REFS = ('合成', '审核线引用.json')             # 当前审核线引用了片子里哪些文件（review 建好时记）：subs、pip、anim-assemble、nav 覆盖前查（2026-11-22 新3）


def _signals_to_exit():
    """建审核线期间收到 SIGTERM/SIGHUP（命令行工具超时、关掉终端）也按“失败”收尾、删半成品（2026-11-22 新2：Q106 复发——导入卡了 10 分钟被外面结束，
    Python 默认收到 SIGTERM 直接退出，不走清理，留下只有 V1/A1 的半成品）。返回恢复原处理的函数。"""
    import signal
    old = {}
    def stop(sig, frame): raise SystemExit(f'停止：建审核线时进程被结束（信号 {sig}，多半是命令超时或终端关了）')
    for s in (signal.SIGTERM, signal.SIGHUP):
        try: old[s] = signal.signal(s, stop)
        except (ValueError, OSError): pass          # 不在主线程（测试里）就不装
    def back():
        for s, h in old.items():
            try: signal.signal(s, h)
            except (ValueError, OSError): pass
    return back


def leftover_build(job: Job, pm):
    """上次 review 被硬杀（来不及清理）留下的半成品：建线中记录里那个唯一 id 的时间线还在就删掉（只删这一条，按 id 认）。返回给人看的一句话（没有就空串）。"""
    f = job.p(*BUILDING)
    if not f.exists(): return ''
    try: m = rj(f)
    except (ValueError, OSError): f.unlink(missing_ok=True); return ''
    p = pm.GetCurrentProject(); have = [p.GetTimelineByIndex(i + 1) for i in range(p.GetTimelineCount())] if p else []
    if not any(_uid(t) == m.get('uid') for t in have if m.get('uid')):
        f.unlink(missing_ok=True); return ''
    if not drop_timeline(pm, m['uid'], job['main_timeline']):
        return f"【注意】上次建审核线没做完，留下的半成品时间线「{m.get('名称')}」没能自动删掉：在达芬奇里手动删掉这一条（只删它）再重跑 review"
    f.unlink(missing_ok=True)
    return f"上次建审核线没做完（{m.get('开始', '')} 开始，进程被结束），留下的半成品时间线「{m.get('名称')}」已删掉（只删了这一条）"


def media_id(clip):
    try: return clip.GetMediaId()
    except Exception: return None


def used_media(p):
    """工程里所有时间线（视频、音频、字幕轨）用到的媒体池条目 id。读不出来（接口不支持、出错）返回 None：这时不清理任何条目。"""
    try:
        out = set()
        for i in range(p.GetTimelineCount()):
            tl = p.GetTimelineByIndex(i + 1)
            for k in ('video', 'audio', 'subtitle'):
                for j in range(tl.GetTrackCount(k)):
                    for it in tl.GetItemListInTrack(k, j + 1) or []:
                        c = it.GetMediaPoolItem(); mid = media_id(c) if c else None
                        if c and not mid: return None
                        if mid: out.add(mid)
        return out
    except Exception:
        return None


def pool_clips(folder):
    """媒体池这个文件夹及所有子文件夹里的片段。"""
    out = list(folder.GetClipList() or [])
    for f in folder.GetSubFolderList() or []: out += pool_clips(f)
    return out


def purge_unused(mp, root, path, used, keep=()):
    """媒体池里文件路径是 path、又没被任何时间线用到的旧条目删掉（只删媒体池条目，不删文件）。used=None（读不出引用）什么都不删。
    keep：这次 review 自己导入的条目 id，不删。返回删掉几个（2026-11-22 新2：“旧素材”文件夹里攒了 7 份同路径的 B肉_小圆圈.mp4，导入卡住、失败两次）。"""
    if used is None: return 0
    old = [c for c in pool_clips(root) if c.GetClipProperty('File Path') == str(path)
           and media_id(c) and media_id(c) not in used and media_id(c) not in keep]
    if not old: return 0
    try: ok = mp.DeleteClips(old)
    except Exception: ok = False
    if not ok: say(f'【注意】媒体池里 {len(old)} 个同路径、没被用到的旧条目（{Path(path).name}）没删掉，照样导入'); return 0
    return len(old)


def review(job: Job, name=None, broll=None, force_len=False, allow_gaps=False, changes=None, extra=None):
    """changes：改动清单 json，在整片审核线改动处打绿色标记（不再另建“待确认”线，2026-11-12）；extra：额外轨道清单 json。
    复制出新审核线之后任何一步失败，删掉这次新建的那一条（2026-11-13 C11：以前半成品留在工程里，重跑被“已有时间线”挡住），同一条命令可以直接重跑。
    2026-11-22 新2（Q106 复发）：被 SIGTERM/SIGHUP 结束也删；被硬杀来不及删的，下次 review 开头按 合成/审核线建线中.json 删掉那一条半成品。"""
    made = {}; back = _signals_to_exit()
    try:
        return _review(job, made, name, broll, force_len, allow_gaps, changes, extra)
    except BaseException as e:
        if not made: raise
        ok = drop_timeline(made['pm'], made['uid'], job['main_timeline'])
        if ok: job.p(*BUILDING).unlink(missing_ok=True)
        tail = (f"已删掉这次新建的半成品时间线「{made['name']}」（只删了这一条），这次审核线没建成；问题处理后同一条命令可以直接重跑" if ok else
                f"这次新建的半成品时间线「{made['name']}」没能自动删掉，这次审核线没建成：重跑前先在达芬奇里手动删掉这一条（只删它，主线和别的审核线别动）")
        if isinstance(e, SystemExit) and isinstance(e.code, str): raise SystemExit(f'{e.code}\n{tail}')
        say(tail); raise
    finally:
        back()


def _review(job: Job, made, name=None, broll=None, force_len=False, allow_gaps=False, changes=None, extra=None):
    from .check import gate, rendang_problems
    from .anim import label_overlap
    gate(job, allow_gaps)
    rp = rendang_problems(job)                  # 人挡字（2026-11-13）：遮挡不通过、不在露脸段、口播画面重出过的都不放
    if rp: die('人挡字没通过：' + '；'.join(rp) + '。改 花字.json 或换风格后重跑 huazi.py，再建审核线')                       # 气口没精修完不建（2026-11-01），先查，免得白开达芬奇
    lab, hint = label_overlap(job)              # 代码动画标签逐帧自检有字压物体的帧不建（2026-11-13 C7；Blender 的 出片.py 自己拦）
    if lab: die('代码动画标签自检没过（动画/自查/标签重叠.json）：' + '；'.join(lab) + '。先挪标签位置、重跑 anim-labelcheck 到 0 帧（再重出、总装），再建审核线')
    if hint: say('【提醒】' + hint)
    # 音效：0 个不建（2026-11-12《水电阶段》：加镜头后重跑 beats 清空了音效，建出 0 音效审核线）；音效表比分镜旧也不建
    sfxf0 = job.p('合成', '音效表.json')
    if not sfxf0.exists() or not rj(sfxf0): die('合成/音效表.json 没有或是 0 个音效：先 beats（音效写在分镜规格的 sfx）→ sfx，再建审核线')
    if job.p('分镜.json').exists() and job.p('分镜.json').stat().st_mtime > sfxf0.stat().st_mtime + 1:
        die('分镜.json 比 音效表.json 新：重跑 beats 之后要再跑 sfx（不然音效还是旧位置）')
    marks = load_changes(job, changes) if changes else []
    extras = extra_items(job, extra) if extra else {}
    sub_tab, sub_trk = job.p('合成', '字幕', '字幕表.json'), job.p('合成', '字幕', '字幕整轨.mov')
    # 2026-11-21 体检：字幕表是空的（0 条）以前在下面 rows[-1] 直接崩。空表拼不出字幕整轨（subs 也按至少一条算），不是正常状态：开达芬奇之前就停
    if sub_tab.exists() and not rj(sub_tab): die('合成/字幕/字幕表.json 是空的（0 条字幕），没建审核线：先重跑 subs 出字幕（SRT 是不是空的、是不是每一句都被 subtitle_hide 藏掉了），再建审核线')
    if sub_tab.exists() and (not sub_trk.exists() or sub_tab.stat().st_mtime > sub_trk.stat().st_mtime + 1):
        from . import subs                      # 改过字幕（subs/subfix）没重拼整轨：自动重拼，审核线才是新字（2026-11-21 新18）
        from .common import overwrite_ok      # 这就是在建新一版审核线：覆盖旧审核线引用的整轨不拦（新3）
        say('字幕表比字幕整轨新（改过字幕）：先自动重拼字幕整轨…')
        with overwrite_ok(): subs.track(job)
    polished = polished_voice(job)             # 口播降噪修音（voice-polish）做过：放修音口播轨、关掉 A1（2026-11-21 新13）
    flowers = flower_items(job)
    check_flowers(job, flowers)
    amb = Path(job.get('ambience') or job.p('合成', '实拍原声.wav'))
    r, pm, p = project(job)
    left = leftover_build(job, pm)              # 上次被硬杀留下的半成品先删（2026-11-22 新2）
    if left: say(left)
    mp = p.GetMediaPool(); root = mp.GetRootFolder(); T = timelines(p)
    main = T.get(job['main_timeline']) or die(f"没有主线「{job['main_timeline']}」")
    name = name or job.get('review_timeline') or f"{job['main_timeline']}_卡点审核"
    if name in T: die(f'已有时间线「{name}」，换个名字（--name）或先在达芬奇里改名旧的')
    N = int(job['frames']); fps = float(job.fps)
    mlen = main.GetEndFrame() - main.GetStartFrame()
    if mlen != N and not force_len:
        die(f'主线现在 {mlen} 帧，项目记录的是 {N} 帧：主线在 a-roll 之后被改过。先用 snap/diff 弄清改了哪里，重新 words/subs，或加 --force-len')
    B = rj(job.p('分镜.json')); shots = [s for s in B['shots'] if s['kind'] == 'B']
    broll = Path(broll) if broll else next((x for x in (job.p('合成', 'B肉_小圆圈.mp4'), job.p('合成', 'B肉.mp4')) if x.exists()), None)
    subt, subr = job.p('合成', '字幕', '字幕整轨.mov'), job.p('合成', '字幕', '字幕表.json')
    navf, sfxf = job.p('合成', '导航', '导航整帧.mov'), job.p('合成', '音效表.json')
    for f in (broll, subt, subr):
        if not f or not Path(f).exists(): die(f'缺 {f or "合成/B肉.mp4"}')
    rows = rj(subr); sfx = sorted(rj(sfxf), key=lambda e: e['record']) if sfxf.exists() else []

    def folder(n):
        return next((f for f in root.GetSubFolderList() if f.GetName() == n), None) or mp.AddSubFolder(root, n)

    used = used_media(p); mine_ids = set(); imported = []      # 复制主线之前：各时间线用到的媒体条目（清理同路径旧条目时不碰这些）

    def imp(path, fname, fresh=False):
        f = folder(fname); mp.SetCurrentFolder(f)
        have = {c.GetClipProperty('File Path'): c for c in (f.GetClipList() or [])}
        if fresh and str(path) in have and media_id(have[str(path)]) not in mine_ids:
            # 重新生成过的同名文件：媒体池里的旧片段帧数、内容都是旧的，会被复用。旧审核线还在用的挪进“旧素材_<审核线名>”留给旧审核线，再重新导入（2026-11-01《别墅地下室防水》）；
            # 没有任何时间线用到的直接删掉条目（下面 purge_unused，2026-11-22 新2）
            if used is None or media_id(have[str(path)]) in used:
                if not mp.MoveClips([have.pop(str(path))], folder('旧素材_' + name)): die(f'挪旧素材失败：{path}')
            else: have.pop(str(path))
            mp.SetCurrentFolder(f)
        if str(path) not in have:
            n = purge_unused(mp, root, path, used, mine_ids)          # 媒体池里同路径、没被任何时间线用到的旧条目先清掉（新2）
            if n: say(f'媒体池里 {n} 个同路径、没被任何时间线用到的旧条目已清掉：{Path(path).name}')
            mp.SetCurrentFolder(f)
            got = mp.ImportMedia([str(path)]) or []
            if len(got) != 1: die(f'导入失败：{path}（这一轨没放上，审核线没建成）')
            have[str(path)] = got[0]
        if media_id(have[str(path)]): mine_ids.add(media_id(have[str(path)]))   # 不叫 mine：下面放音效时有个同名的列表，撞名后这里就崩（2026-11-22 第一次连真达芬奇试出来的）
        imported.append(str(path))
        mp.SetCurrentFolder(root); return have[str(path)]

    def add(kind, label, sub=None):
        assert (tl.AddTrack(kind, sub) if sub else tl.AddTrack(kind)), f'加{kind}轨失败'
        j = tl.GetTrackCount(kind); tl.SetTrackName(kind, j, label); return j

    def put(items, what):
        if items and not mp.AppendToTimeline(items): die(f'放{what}失败')

    p.SetCurrentTimeline(main); tl = main.DuplicateTimeline(name)
    if not tl: die('复制主线失败')
    made.update(pm=pm, name=name, uid=_uid(tl))          # 从这里起失败就删掉这一条（review 里按唯一 id 删）
    if made['uid']: wj(job.p(*BUILDING), {'说明': 'review 正在建这条审核线；建好或删掉半成品后自动删。进程被硬杀时留下，下次 review 先删这条半成品', '名称': name, 'uid': made['uid'], '开始': time.strftime('%F %T')})
    p.SetCurrentTimeline(tl); st = tl.GetStartFrame()
    ok, back = _fmt(tl, job)                    # 4K 只设在审核时间线上，主线不动
    if not ok: die('审核时间线设不成 4K 画幅：' + json.dumps(back, ensure_ascii=False))
    say(f'已复制主线 → 「{name}」（{job.W}×{job.H}），开始放轨道…')
    swapped = swap_source(tl, job['source'], imp, put)
    dy = int(job.get('人像下移') or 0)      # 2026-11-21 新10：口播画面整体下移（4K 像素），和人挡字底片 A肉_人像下移<值>.mov 同取景；以前靠用户在审核线上手调 Tilt，重建就丢
    if dy:
        for it in tl.GetItemListInTrack('video', 1) or []:
            if not it.SetProperty('Tilt', float(-dy)): die('给口播画面设 Tilt（job.json 的 人像下移）失败')
        say(f'口播画面（V1）Tilt = {-dy}（job.json 的 人像下移）')
    # 动画镜头
    clip = imp(broll, 'B-roll 动画', fresh=True); cn = int(clip.GetClipProperty('Frames'))
    vb = add('video', NAMES['broll'])
    want_b = [(job.f(s['a']), min(job.f(s['b']), cn)) for s in shots]
    put([{'mediaPoolItem': clip, 'startFrame': a, 'endFrame': b, 'trackIndex': vb, 'recordFrame': st + a, 'mediaType': 1} for a, b in want_b if b > a], '动画镜头')
    # 花字（包装系统出的整幅片，放上就对位）：在导航、字幕下面（2026-11-13 起：人挡字是不透明整幅片段，导航条和字幕要压在它上面；
    # 普通花字在安全区 y≥420、导航条 0–340，换层不影响画面）
    vh = None
    if flowers:
        vh = add('video', NAMES['flower'])
        put([{'mediaPoolItem': imp(f, '花字', fresh=True), 'startFrame': 0, 'endFrame': n, 'trackIndex': vh, 'recordFrame': st + a, 'mediaType': 1}
             for f, a, n in flowers], '花字')
    # 章节导航（整帧透明，放上就对位）
    vn = None
    if navf.exists():
        nc = imp(navf, '章节导航', fresh=True); at = job.f(job.get('nav_at', 0)); n = min(int(nc.GetClipProperty('Frames')), N - at)
        vn = add('video', NAMES['nav'])
        put([{'mediaPoolItem': nc, 'startFrame': 0, 'endFrame': n, 'trackIndex': vn, 'recordFrame': st + at, 'mediaType': 1}], '导航条')
    # 字幕：整轨一次导入，按每条起止切开放（批量导入几十个 4K 透明小片会让达芬奇闪退）
    sc = imp(subt, '字幕（新样式）', fresh=True)
    if int(sc.GetClipProperty('Frames')) < rows[-1]['end']: die('字幕整轨比字幕表短，重跑 subs')
    vs = add('video', NAMES['subs'])
    put([{'mediaPoolItem': sc, 'startFrame': x['start'], 'endFrame': x['end'], 'trackIndex': vs, 'recordFrame': st + x['start'], 'mediaType': 1} for x in rows], '字幕')
    for j in range(tl.GetTrackCount('subtitle')): tl.SetTrackEnable('subtitle', j + 1, False)   # 原字幕轨关掉，留着对照
    # 音频：A2 空给 B-roll 原声；音效从下一条起，重叠的放下一条；人声和音效绝不同轨
    aa = add('audio', NAMES['ambience'], 'stereo')
    if amb.exists():       # 自家实拍原声当环境音（2026-11-12 用户：B-roll 原声放出来、比口播小，当环境音），整条从 0 帧放
        put([{'mediaPoolItem': imp(amb, 'B-roll 原声', fresh=True), 'startFrame': 0, 'endFrame': N, 'trackIndex': aa, 'recordFrame': st, 'mediaType': 2}], 'B-roll 原声')
    lanes = []
    for e in sfx:
        k = next((i for i, end in enumerate(lanes) if end <= e['record']), None)
        if k is None: lanes.append(0); k = len(lanes) - 1
        lanes[k] = e['record'] + int(e['frames']); e['lane'] = k
    for k in range(len(lanes)):
        ai = add('audio', NAMES['sfx'] if k == 0 else f"{NAMES['sfx']}{k + 1}", 'stereo')
        mine = [e for e in sfx if e['lane'] == k]
        cl = {e['file']: imp(e['file'], '音效（卡点）') for e in mine}
        put([{'mediaPoolItem': cl[e['file']], 'startFrame': int(e['src_start']), 'endFrame': int(e['src_start']) + int(e['frames']), 'trackIndex': ai,
              'recordFrame': st + int(e['record']), 'mediaType': 2} for e in mine], '音效')
        for it, e in zip(sorted(tl.GetItemListInTrack('audio', ai) or [], key=lambda i: i.GetStart()), mine): it.SetProperty('AudioVolume', float(e['gain_db']))
    want_x = {}
    for (kind, label), xs in extras.items():
        j = add(kind, label, 'stereo' if kind == 'audio' else None); mt = 1 if kind == 'video' else 2
        put([{'mediaPoolItem': imp(x['file'], label), 'startFrame': int(x.get('src_start', 0)), 'endFrame': int(x.get('src_start', 0)) + int(x['frames']),
              'trackIndex': j, 'recordFrame': st + job.f(float(x.get('t', 0))), 'mediaType': mt} for x in xs], label)
        if kind == 'audio':
            for it, x in zip(sorted(tl.GetItemListInTrack('audio', j) or [], key=lambda i: i.GetStart()), sorted(xs, key=lambda x: float(x.get('t', 0)))):
                if x.get('gain_db') is not None: it.SetProperty('AudioVolume', float(x['gain_db']))
        want_x[(kind, j, label)] = len(xs)
    pv = None
    if polished:
        pf, plabel = polished
        pv = next((j for (k, j, lb) in want_x if k == 'audio' and lb == plabel), None)    # 老写法：额外轨道清单里自己放了这条
        if pv is None:
            pv = add('audio', plabel, 'stereo')
            put([{'mediaPoolItem': imp(pf, plabel, fresh=True), 'startFrame': 0, 'endFrame': N, 'trackIndex': pv, 'recordFrame': st, 'mediaType': 2}], '修音口播')
        if not tl.SetTrackEnable('audio', 1, False): die('关掉“音频 1”（原口播）失败')
    # 改动处打绿色标记：名称“改：…”，备注写改了什么
    for f, mname, note in marks:
        if not tl.AddMarker(f, 'Green', mname, note, 1): die(f'打标记失败：{mname}（{f / fps:.2f} 秒）')
    vol = job.p('合成', '音量.json'); vdb = rj(vol)['voice_db'] if vol.exists() else job.get('voice_db', 0)
    if isinstance(vdb, (int, float)) and vdb:
        for it in tl.GetItemListInTrack('audio', 1) or []: it.SetProperty('AudioVolume', float(vdb))
    # 保存 → 关闭 → 重开 → 读回
    p, tl = reopen(pm, p, name); st = tl.GetStartFrame()
    got_b = [(i.GetStart() - st, i.GetEnd() - st) for i in sorted(tl.GetItemListInTrack('video', vb) or [], key=lambda i: i.GetStart())]
    got_s = [(i.GetStart() - st, i.GetEnd() - st, i.GetLeftOffset()) for i in sorted(tl.GetItemListInTrack('video', vs) or [], key=lambda i: i.GetStart())]
    sfx_items = [i for a in range(1, tl.GetTrackCount('audio') + 1) if tl.GetTrackName('audio', a).startswith(NAMES['sfx']) for i in tl.GetItemListInTrack('audio', a) or []]
    a_src = {i.GetMediaPoolItem().GetClipProperty('File Path') for i in tl.GetItemListInTrack('audio', 1) or [] if i.GetMediaPoolItem()}   # 人声以 A1 为准（新文件只有声音时 V1 留原片）
    v_src = a_src | {i.GetMediaPoolItem().GetClipProperty('File Path') for i in tl.GetItemListInTrack('video', 1) or [] if i.GetMediaPoolItem()}
    rep = {'时间线': name, '口播源': sorted(v_src) + (['（从主线换成 job.json 的 source，' + swapped + '）'] if swapped else []), '画幅': f"{tl.GetSetting('timelineResolutionWidth')}×{tl.GetSetting('timelineResolutionHeight')}",
           '动画镜头': f'{len(got_b)}/{len(want_b)}', '镜头位置不符': [g for g, w in zip(got_b, want_b) if g != w][:3],
           '字幕': f'{len(got_s)}/{len(rows)}', '字幕位置不符': [g for g, x in zip(got_s, rows) if g != (x['start'], x['end'], x['start'])][:3],
           '导航条': len(tl.GetItemListInTrack('video', vn) or []) if vn else '没有（合成/导航/导航整帧.mov 不存在）',
           '音效': f'{len(sfx_items)}/{len(sfx)}',
           '花字': f"{len(tl.GetItemListInTrack('video', vh) or [])}/{len(flowers)}" if vh else '没有（合成/花字/花字表.json 不存在）',
           'B-roll 原声': len(tl.GetItemListInTrack('audio', aa) or []) if amb.exists() else '没有（合成/实拍原声.wav 不存在）',
           '额外轨道': {label: f"{len(tl.GetItemListInTrack(kind, j) or [])}/{n}" for (kind, j, label), n in want_x.items()},
           '改动标记': [f"{round(f / fps, 2)} 秒 {m.get('name')}" for f, m in sorted((tl.GetMarkers() or {}).items()) if m.get('color') == 'Green'],
           '原字幕轨关闭': not any(tl.GetIsTrackEnabled('subtitle', j + 1) for j in range(tl.GetTrackCount('subtitle'))),
           '口播修音轨': (f"{len(tl.GetItemListInTrack('audio', pv) or [])} 段，音频 1 {'已关' if not tl.GetIsTrackEnabled('audio', 1) else '没关上'}" if pv else '没有（没做 voice-polish）'),
           '轨道': {k: [tl.GetTrackName(k, j + 1) for j in range(tl.GetTrackCount(k))] for k in ('video', 'audio')}}
    say(json.dumps(rep, ensure_ascii=False, indent=1))
    got_marks = {f: m for f, m in (tl.GetMarkers() or {}).items() if m.get('color') == 'Green'}
    bad = a_src != {str(job['source'])} or rep['镜头位置不符'] or rep['字幕位置不符'] or len(got_b) != len(want_b) or len(got_s) != len(rows) or len(sfx_items) != len(sfx) \
        or (vh and len(tl.GetItemListInTrack('video', vh) or []) != len(flowers)) or (amb.exists() and len(tl.GetItemListInTrack('audio', aa) or []) != 1) \
        or any(len(tl.GetItemListInTrack(kind, j) or []) != n for (kind, j, _), n in want_x.items()) \
        or any(got_marks.get(f, {}).get('name') != mname for f, mname, _ in marks) \
        or (pv and (len(tl.GetItemListInTrack('audio', pv) or []) != 1 or tl.GetIsTrackEnabled('audio', 1)))
    if bad: die('读回和应放的不一致，见上面')
    tl.SetCurrentTimecode(tcode(st, fps)); r.OpenPage('edit'); pm.SaveProject()
    made.clear(); job.p(*BUILDING).unlink(missing_ok=True)   # 建好了：之后不再删
    job.cfg['review_timeline'] = name; job.save()
    # 这条审核线引用了片子里哪些文件（新3）：之后 subs、pip、anim-assemble、nav 要覆盖其中的文件就停下（覆盖了用户在看的审核线会离线、内容变）
    wj(job.p(*REFS), {'说明': 'review 建好时记：当前审核线引用的文件。覆盖这些文件的命令会停下（先建新一版审核线，或加 --force）', '时间线': name,
                      '建于': time.strftime('%F %T'), '文件': sorted(set(imported))})
    wj(job.p('快照', f'{name}_建好时.json'), snapshot(tl))   # 用户手改之后拿它 diff，看改了哪里
    say('审核时间线已建好并重开核对通过，达芬奇停在开头' + (f'；{len(marks)} 处改动打了绿色标记，用户在时间线上切换标记就能逐个看' if marks else ''))
    try:                                                     # 审核线建好就起审片台并打开到“审核线”栏（用户 2026-11-21：“审片工作台打开……每次都自动打开”；2026-11-22 扩成审片台）
        from . import workbench; workbench.launch(job, column='审核线')      # 审片台打开到“审核线”栏（2026-11-22）
        say('换了新一轮审核线的话，用 workbench --round N 把审片台的审核线换到这一轮')
    except (SystemExit, Exception) as e:
        say(f'审片台没起来（审核线不受影响）：{e}；手动跑 workbench {job.dir}')


def swap_source(tl, src, imp, put):
    """审核线 V1/A1 是从主线复制来的，指着交付时的口播文件。job.json 的 source 换过（比如人声重新处理过）时，只在这条审核线上
    按原来的入点、长度换成新文件，画面的缩放、位置照搬；不用 ReplaceClip（它会让所有旧审核线一起变，2026-11-01《别墅地下室防水》）。"""
    st = tl.GetStartFrame()
    its = {k: sorted(tl.GetItemListInTrack(k, 1) or [], key=lambda i: i.GetStart()) for k in ('video', 'audio')}
    old = {i.GetMediaPoolItem().GetClipProperty('File Path') for k in its for i in its[k] if i.GetMediaPoolItem()}
    if not old or old == {str(src)}: return ''
    if not Path(src).exists(): die(f'job.json 的 source 不存在：{src}')
    kinds = ('video', 'audio') if probe(src).get('width') else ('audio',)   # 新文件只有声音就只换 A1
    keep = {k: [(i.GetStart() - st, i.GetEnd() - st, i.GetLeftOffset(), i.GetProperty() or {}) for i in its[k]] for k in kinds}
    new = imp(src, '口播（换过的源）')
    if not tl.DeleteClips([i for k in kinds for i in its[k]]): die('删掉审核线上的旧口播片段失败')
    for k, mt in (('video', 1), ('audio', 2)):
        if k in kinds:
            put([{'mediaPoolItem': new, 'startFrame': lo, 'endFrame': lo + e - s, 'trackIndex': 1, 'recordFrame': st + s, 'mediaType': mt} for s, e, lo, _ in keep[k]], '新口播')
    if 'video' in kinds:
        for it, (*_, pr) in zip(sorted(tl.GetItemListInTrack('video', 1) or [], key=lambda i: i.GetStart()), keep['video']):
            for q in KEEP_PROPS:
                if q in pr: it.SetProperty(q, pr[q])
    got = {k: [(i.GetStart() - st, i.GetEnd() - st, i.GetLeftOffset()) for i in sorted(tl.GetItemListInTrack(k, 1) or [], key=lambda i: i.GetStart())] for k in kinds}
    if any(got[k] != [x[:3] for x in keep[k]] for k in kinds): die('换口播源后片段位置和主线对不上')
    # 分开放的画面和声音不链接，用户在审核线上剪会音画错开（2026-11-01 实测）：同位置的 V1/A1 重新链上
    if len(kinds) == 2:
        A = {(i.GetStart(), i.GetEnd()): i for i in tl.GetItemListInTrack('audio', 1) or []}
        for v in tl.GetItemListInTrack('video', 1) or []:
            x = A.get((v.GetStart(), v.GetEnd()))
            if x and not tl.SetClipsLinked([v, x], True): die('换口播源后音画重新链接失败')
    else:
        say('【注意】新口播文件只有声音，只换了 A1，画面和声音没链接：剪的时候两条一起选')
    return '+'.join({'video': 'V1', 'audio': 'A1'}[k] for k in kinds) + f' 共 {sum(len(keep[k]) for k in kinds)} 段'


# ---------- 快照与比对（用户手改之后先比对再动手） ----------
def snapshot(tl):
    st = tl.GetStartFrame()
    s = {'name': tl.GetName(), 'frames': tl.GetEndFrame() - st, 'size': [tl.GetSetting('timelineResolutionWidth'), tl.GetSetting('timelineResolutionHeight')]}
    for k in ('video', 'audio', 'subtitle'):
        s[k] = []
        for j in range(tl.GetTrackCount(k)):
            its = sorted(tl.GetItemListInTrack(k, j + 1) or [], key=lambda i: i.GetStart())
            row = {'name': tl.GetTrackName(k, j + 1), 'enabled': tl.GetIsTrackEnabled(k, j + 1), 'items': []}
            for i in its:
                x = {'clip': i.GetName(), 'start': i.GetStart() - st, 'end': i.GetEnd() - st, 'offset': i.GetLeftOffset()}
                if k == 'audio': x['db'] = round(float(i.GetProperty('AudioVolume') or 0), 2)
                if k == 'video':
                    pr = i.GetProperty() or {}; x['props'] = {q: pr.get(q) for q in KEEP_PROPS if q in pr}
                row['items'].append(x)
            s[k].append(row)
    return s


def snap(job: Job, name, out=None):
    r, pm, p = project(job, write=False); s = snapshot(timeline(p, name))     # 只读：不拿写入锁
    out = Path(out) if out else job.p('快照', f"{name}_{time.strftime('%m%d-%H%M%S')}.json")
    wj(out, s); say(f"{name}：{s['frames']} 帧，" + '，'.join(f"{k} {[len(t['items']) for t in s[k]]}" for k in ('video', 'audio', 'subtitle')) + f' → {out}')
    return out


def diff(a, b):
    """两个快照逐轨比对：哪些片段没变、整体平移了几帧、被剪短/新增/删除"""
    A, Bs = rj(a), rj(b)
    say(f"总长 {A['frames']} → {Bs['frames']} 帧（{Bs['frames'] - A['frames']:+d}）")
    for k in ('video', 'audio', 'subtitle'):
        for j in range(max(len(A[k]), len(Bs[k]))):
            ta = A[k][j] if j < len(A[k]) else {'name': '（无）', 'items': []}
            tb = Bs[k][j] if j < len(Bs[k]) else {'name': '（无）', 'items': []}
            key = lambda x: (x['clip'], x['offset'])
            ia = {key(x): x for x in ta['items']}; ib = {key(x): x for x in tb['items']}
            same = shift = 0; shifts = {}; changed = []
            for kk, x in ia.items():
                y = ib.get(kk)
                if not y: continue
                if (x['start'], x['end']) == (y['start'], y['end']): same += 1
                elif x['end'] - x['start'] == y['end'] - y['start']: d = y['start'] - x['start']; shifts[d] = shifts.get(d, 0) + 1
                else: changed.append((x['clip'], (x['start'], x['end']), (y['start'], y['end'])))
                if k == 'video' and x.get('props') != y.get('props'): changed.append((x['clip'], '检查器设置变了', x.get('props'), y.get('props')))
            gone = [x for kk, x in ia.items() if kk not in ib]; new = [y for kk, y in ib.items() if kk not in ia]
            if same == len(ia) == len(ib) and not changed: continue
            say(f"{k} {j + 1}「{tb['name']}」：不变 {same}，平移 {dict(sorted(shifts.items()))}，改长短 {len(changed)}，删 {len(gone)}，新增 {len(new)}")
            for c in (changed + [('删', g['clip'], g['start'], g['end']) for g in gone] + [('增', n['clip'], n['start'], n['end'], n['offset']) for n in new])[:8]:
                say('   ', c)


# ---------- 关键帧 ----------
def stills(job: Job, name, frames, out_dir=None):
    r, pm, p = project(job); tl = timeline(p, name); p.SetCurrentTimeline(tl); r.OpenPage('edit'); st = tl.GetStartFrame(); fps = float(job.fps)
    out_dir = Path(out_dir) if out_dir else job.p('合成', '关键帧', 'x').parent; out = []
    for f in frames:
        t = tcode(st + f, fps)
        if not tl.SetCurrentTimecode(t): die(f'跳不到 {t}')
        path = out_dir / f"{name}_{t.replace(':', '')}.png"
        if not p.ExportCurrentFrameAsStill(str(path)): die(f'导出 {t} 失败')
        out.append(path)
    tl.SetCurrentTimecode(tcode(st, fps)); pm.SaveProject()
    say('→ ' + '、'.join(x.name for x in out)); return out


# ---------- 改一条字幕 ----------
def subfix(job: Job, n, old, new, name=None, write_srt=False):
    from . import subs
    row, clip = subs.refix(job, n, old, new, write_srt)
    r, pm, p = project(job); name = name or job.get('review_timeline') or die('不知道改哪条时间线，加 --name')
    tl = timeline(p, name); p.SetCurrentTimeline(tl); st = tl.GetStartFrame(); mp = p.GetMediaPool(); root = mp.GetRootFolder()
    vs = track_index(tl, 'video', NAMES['subs']) or die(f"时间线里没有唯一一条「{NAMES['subs']}」轨")
    L = row['end'] - row['start']; tag = Path(row['file']).stem
    cand = [i for i in tl.GetItemListInTrack('video', vs) or [] if i.GetEnd() - i.GetStart() == L and
            ((i.GetName() == '字幕整轨.mov' and i.GetLeftOffset() == row['start']) or i.GetName().startswith(tag + '_'))]
    if len(cand) != 1: die(f'在「{NAMES["subs"]}」轨上找到 {len(cand)} 个第 {n} 条字幕的片段，应为 1 个')
    it = cand[0]; rec = it.GetStart(); props = it.GetProperty() or {}
    f = next((x for x in root.GetSubFolderList() if x.GetName() == '字幕（新样式）'), None) or mp.AddSubFolder(root, '字幕（新样式）')
    mp.SetCurrentFolder(f); c = (mp.ImportMedia([str(clip)]) or [None])[0]; mp.SetCurrentFolder(root)
    if not c or int(c.GetClipProperty('Frames')) != L: die('导入新字幕片段失败')
    if not tl.DeleteClips([it], False): die('删旧字幕片段失败')
    if not mp.AppendToTimeline([{'mediaPoolItem': c, 'startFrame': 0, 'endFrame': L, 'trackIndex': vs, 'recordFrame': rec, 'mediaType': 1}]): die('放新字幕片段失败')
    nw = next((i for i in tl.GetItemListInTrack('video', vs) or [] if i.GetStart() == rec), None)
    if nw is None: die(f'新字幕片段放上去之后在「{NAMES["subs"]}」轨上读不到（达芬奇没返回这条轨的片段）：去达芬奇看一下第 {n} 条字幕那一段')
    for k in KEEP_PROPS:                       # 重放会丢检查器设置：按原片段补回
        if k in props and nw.GetProperty(k) != props[k]: nw.SetProperty(k, props[k])
    p, tl = reopen(pm, p, name); st = tl.GetStartFrame()
    nw = [i for i in tl.GetItemListInTrack('video', vs) or [] if i.GetStart() == rec]
    ok = len(nw) == 1 and nw[0].GetName() == clip.name and nw[0].GetEnd() - rec == L and len(tl.GetItemListInTrack('video', vs) or []) == len(rj(job.p('合成', '字幕', '字幕表.json')))
    tl.SetCurrentTimecode(tcode(rec + L // 2, float(job.fps))); r.OpenPage('edit')
    shot = job.p('合成', '关键帧', f'改字_第{n}条.png'); p.ExportCurrentFrameAsStill(str(shot))
    tl.SetCurrentTimecode(tcode(st, float(job.fps))); pm.SaveProject()
    if not ok: die('重开读回不对，去达芬奇看一下')
    say(f'达芬奇「{name}」第 {n} 条字幕已换成新片段（{tcode(rec, float(job.fps))}，{L} 帧），重开核对通过；截图 {shot}')


# ---------- 导出 ----------
def hand_tweaked(job, tl, voice, sfx, tol=0.05, polished=False):
    """平衡前找用户手调过的音量：审核线上的音量和 音效表.json / 音量.json 的记录对不上就算手调（2026-10-24 用户：音量以达芬奇平衡为准，但手调的不覆盖）。
    人声按“是不是记录过的某个音量”比，用户剪短或挪动片段不算手调；音效按位置和文件名比，挪过位置的按文件名和音量找。
    polished：人声是修音口播轨（review 放时 0 dB，平衡后记在 音量.json 的 polish_levels）。"""
    st = tl.GetStartFrame(); out = []
    tp = job.p('合成', '音效表.json'); rows = rj(tp) if tp.exists() else []
    by_key = {(int(e['record']), os.path.basename(e['file'])): float(e.get('gain_db') or 0) for e in rows}
    for x in sfx:
        k, v = (x.GetStart() - st, x.GetName()), float(x.GetProperty('AudioVolume') or 0)
        if k in by_key: bad = abs(v - by_key[k]) > tol
        else: bad = not any(n == k[1] and abs(v - g) <= tol for (_, n), g in by_key.items())
        if bad: out.append(f"音效 {x.GetName()}（{tcode(x.GetStart(), float(job.fps))}，现 {v:+.1f} dB" + (f"，记录 {by_key[k]:+.1f} dB）" if k in by_key else '，表里没有）'))
    vp = job.p('合成', '音量.json'); vol = rj(vp) if vp.exists() else {}
    vdb = vol.get('voice_db', job.get('voice_db', 0)); vdb = vdb if isinstance(vdb, (int, float)) else 0
    known = [float(x) for x in (vol.get('polish_levels', [0.0]) if polished else vol.get('voice_levels', [vdb]))]
    for x in voice:
        v = float(x.GetProperty('AudioVolume') or 0)
        if not any(abs(v - g) <= tol for g in known): out.append(f"人声 {tcode(x.GetStart(), float(job.fps))}（现 {v:+.1f} dB，记录 {'、'.join(f'{g:+.1f}' for g in known)} dB）")
    return out


def balance_audio(job, r, pm, tl, force=False):
    """达芬奇 21.1 原生标准化（2026-10-24《精装房》用户要求“用达芬奇平衡音效和人声”）：
    人声轨所有片段一起按真峰值 -1 dBFS（相对模式，段间大小关系不变）；“音效”开头的轨每个片段各自 -26 LKFS。
    人声轨：做过口播降噪修音就是“口播（隔离修音）”轨（A1 已关，2026-11-21 新13），否则 A1。"""
    if not hasattr(tl, 'NormalizeAudioLevel'): die('这个达芬奇版本没有 NormalizeAudioLevel（要 21.1 以上）：升级达芬奇，或加 --no-balance 不平衡直接导')
    vi, polished = voice_track(job, tl)
    if polished and tl.GetIsTrackEnabled('audio', 1): die('审核线上有修音口播轨，但“音频 1”（原口播）还开着，两条人声会叠在一起：在达芬奇里关掉音频 1（或重建审核线）再导')
    voice = tl.GetItemListInTrack('audio', vi) or []
    sfx = [x for i in range(1, tl.GetTrackCount('audio') + 1) if tl.GetTrackName('audio', i).startswith(NAMES['sfx']) for x in tl.GetItemListInTrack('audio', i) or []]
    if not force:
        tw = hand_tweaked(job, tl, voice, sfx, polished=polished)
        if tw: die(f'审核线上有 {len(tw)} 处音量和记录对不上，像是手调过，没有平衡、没有导出：\n  ' + '\n  '.join(tw[:8]) + ('\n  …' if len(tw) > 8 else '') +
                   '\n先问用户：保留手调就加 --no-balance 直接导；确认要全部按达芬奇平衡（会覆盖这些手调）就加 --balance')
    rel, ind = getattr(r, 'NORMALIZE_AUDIO_SET_LEVEL_RELATIVE', 0), getattr(r, 'NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT', 1)
    ok1 = bool(voice) and tl.NormalizeAudioLevel(voice, {'normalizationMode': 'True Peak', 'targetLevel': -1.0, 'setLevelMode': rel})
    ok2 = not sfx or tl.NormalizeAudioLevel(sfx, {'normalizationMode': 'ITU-R BS.1770-4', 'targetLoudness': -26.0, 'setLevelMode': ind})
    if not (ok1 and ok2): die(f'达芬奇标准化失败（人声 {ok1}，音效 {ok2}）')
    pm.SaveProject()
    # 平衡后以达芬奇里的音量为准，写回音效表和音量表：以后返修判断“用户手调过没有”要和这份比（审计 2026-10-24）
    st = tl.GetStartFrame(); got = {(x.GetStart() - st, x.GetName()): float(x.GetProperty('AudioVolume')) for x in sfx}
    tp = job.p('合成', '音效表.json')
    if tp.exists():
        rows = rj(tp)
        for e in rows:
            k = (int(e['record']), os.path.basename(e['file']))
            if k in got: e['gain_db'] = round(got[k], 2); e['balanced'] = True
        wj(tp, rows)
    vp = job.p('合成', '音量.json'); v = rj(vp) if vp.exists() else {}
    lv = sorted({round(float(x.GetProperty('AudioVolume')), 2) for x in voice})
    if polished: v.update(polish_db=round(float(voice[0].GetProperty('AudioVolume')), 2), polish_levels=lv, balanced='达芬奇原生标准化：人声（修音口播轨）True Peak -1，音效各 -26 LKFS')
    elif voice: v.update(voice_db=round(float(voice[0].GetProperty('AudioVolume')), 2), balanced='达芬奇原生标准化：人声 True Peak -1，音效各 -26 LKFS', voice_levels=lv)
    wj(vp, v)
    say(f"已平衡：人声{'（修音口播轨）' if polished else ''} {len(voice)} 段 → 真峰值 -1 dBFS（现 {float(voice[0].GetProperty('AudioVolume')):+.1f} dB）；音效 {len(sfx)} 个 → 各自 -26 LKFS")


def export_target(job: Job, out=None, seconds=None, name='审核线', now=None):
    """导出到哪：返回 (文件夹, 文件名不带后缀, 后缀 mp4/mov)（2026-11-12《水电阶段》新15：--out 写成 .mp4 被当成文件夹，成片要手动挪、改名）。
    --out 以 .mp4/.mov 结尾 = 就写成这个文件；--out 是别的 = 文件夹（文件名按审核线）；
    不给 --out 的整片导出 = 项目 03_成片/YYMMDD_<项目.json 的 name>.mp4（UTC+8 导出日期），register-final 直接用；试导几秒仍放片子目录 成片/。"""
    import datetime
    test = f'{name}_{job.W}x{job.H}' + (f'_前{seconds}秒测试' if seconds else '')
    if out:
        o = Path(out).expanduser()
        if o.suffix.lower() in ('.mp4', '.mov'): return o.parent.resolve(), o.stem, o.suffix.lower()[1:]
        return o.resolve(), test, 'mp4'
    root = next((q for q in job.dir.parents if (q / '项目.json').is_file()), None)
    if seconds or not root: return job.p('成片', 'x').parent, test, 'mp4'
    title = rj(root / '项目.json').get('name') or root.name
    day = (now or datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))).strftime('%y%m%d')
    return root / '03_成片', f'{day}_{title}', 'mp4'


def make_room(folder: Path, stem, ext):
    """目标位置已有同名成片/字幕：挪进 历史/（加时间），不覆盖。"""
    old = [folder / f'{stem}.{x}' for x in (ext, 'srt') if (folder / f'{stem}.{x}').exists()]
    if not old: return []
    hist = folder / '历史'; hist.mkdir(parents=True, exist_ok=True); tag = time.strftime('%H%M%S'); moved = []
    for f in old:
        dst = hist / f.name
        if dst.exists(): dst = hist / f'{f.stem}_{tag}{f.suffix}'
        os.replace(f, dst); moved.append(dst)
    return moved


def fresh_render(p, preset='H.264 Master'):
    """加渲染任务前先载入标准预设，清掉上一次脚本渲染留在工程里的设置（2026-11-21 新18：渲过“只导音频”后，下一次小样只出声音）。
    之后各项参数照样逐项设。载入失败只提醒（ExportVideo/ExportAudio 会明确设）。所有脚本渲染（导出、小样、修音渲纯口播）都先调它。"""
    ok = bool(getattr(p, 'LoadRenderPreset', None) and p.LoadRenderPreset(preset))
    if not ok: say(f'【注意】载入渲染预设「{preset}」失败，下面逐项设渲染参数')
    return ok


def stop_render(p, tries=10, gap=0.5):
    """达芬奇还在渲就叫停。返回一句给人看的话：不在渲返回空串；叫停了、没叫停各说清楚。"""
    try:
        if not p.IsRenderingInProgress(): return ''
        p.StopRendering()
        for _ in range(tries):
            if not p.IsRenderingInProgress(): return '已叫停达芬奇渲染'
            time.sleep(gap)
    except Exception:
        pass
    return '没能叫停达芬奇渲染，请去达芬奇里手动停止渲染'


class _RenderTimeout(Exception): pass


def wait_render(p, jid, limit, what, poll=3):
    """开始渲染并等它渲完（导出、渲纯口播共用）。等超过 limit 秒、被 Ctrl-C 打断、轮询中出任何错，都先叫停达芬奇还在跑的渲染再退出/抛出
    （2026-11-21 体检：以前只退出，进程一退达芬奇写入锁就放开，而达芬奇还在渲，下一条命令会写进正在渲染的工程）。正常渲完不叫停。
    等超时并叫停之后，把这次加进渲染队列的任务（jid）也删掉；被打断、出错时不删（和以前一样）。"""
    t0 = time.time()
    try:
        p.StartRendering([jid], False)
        while p.IsRenderingInProgress():
            time.sleep(poll)
            if time.time() - t0 > limit: raise _RenderTimeout
    except BaseException as e:
        tail = stop_render(p)
        if isinstance(e, _RenderTimeout):
            long = f'{limit / 3600:g} 小时' if limit >= 3600 else f'{limit:g} 秒'
            # 叫停之后把这次加进队列的任务删掉（2026-11-21）：留在队列里，用户下次在达芬奇里点“渲染所有”会把它再渲一遍。没叫停成的不删（还在渲）；删不掉只说明，不盖住超时这个原因
            gone = False
            if not tail.startswith('没能'):
                try: gone = bool(p.DeleteRenderJob(jid))
                except Exception: gone = False
            die(f"{what}超过 {long}，{tail or '达芬奇这时已经不在渲染'}；"
                + ('这次加进渲染队列的任务已经删掉' if gone else '这次加的任务还留在达芬奇交付页的渲染队列里，请去达芬奇手动删掉'))
        if tail: say(f'【注意】{what}等渲染时中断（{type(e).__name__}），{tail}')
        raise


def unseen_gate(job: Job, unseen_ok=None):
    """导出前：有用户没看过样张的动画镜头（授权免看放行、或确认原话里写着用户没看）就拦，除非带用户同意导出的原话（2026-11-21 新21）。
    返回要记进导出记录的 {原话, 镜头}，没有就返回 None。"""
    from .check import unseen_samples
    un = unseen_samples(job)
    if not un: return None
    lst = '\n- '.join(f"{p} {s}：{why}（样张 {'、'.join(Path(f).name for f in files) or '没有'}）" for p, s, files, why in un)
    if not (unseen_ok or '').strip():
        die(f'这些动画镜头的样张用户还没看过，先别导出：\n- {lst}\n把样张发到审片台“动画”栏给用户看，用户点了“可以”就算看过（对话里说的不算，2026-11-22 新1）；'
            '用户明确说不看了照样导，就把用户这句原话写进 --未看样张用户同意 "…"（会记进导出记录）')
    say(f'【注意】{len(un)} 个镜头用户没看过样张，按用户同意的原话照导：{unseen_ok.strip()}')
    return {'原话': unseen_ok.strip(), '镜头': [f'{p} {s}' for p, s, _, _ in un]}


def export(job: Job, name=None, out_dir=None, codec='H265', kbps=60000, seconds=None, balance=None, user_ok=None, unseen_ok=None):
    """balance：None（默认）= 先按达芬奇平衡，发现手调就停下问；True = 强制平衡（覆盖手调）；False = 不平衡。2026-10-24 用户定：音量以达芬奇平衡为准。
    整片导出的闸（2026-11-22 用户定“先做出来再改”，取代 2026-11-01 的“带用户说 OK 的原话”）：用户在审片台点过审核线“这一栏定了”、之后内容没变才导
    （draft.export_gate；AI 不能替用户点）。user_ok：用户说过的原话，只当备注记进导出记录，不算确认。
    unseen_ok：有用户没单独看过样张的镜头时的备注原话；用户点了“这一栏定了”就不再因为这个拦。"""
    if seconds and seconds > 30: die('--seconds 只用来试导，最多 30 秒；整片导出不写 --seconds，要带 --user-ok "用户原话"')   # 2026-11-13 审查：--seconds 写成接近全片能绕过下面两道拦
    if (name or job.get('review_timeline')) == job.get('main_timeline') and job.get('main_timeline'):      # 主线永远不动：先于别的检查说清楚
        die(f"「{job.get('main_timeline')}」是主线，不从主线导出（主线永远不动，导出前的音量平衡会改它）：要导就导审核线（review 建的那条）")
    settled = None
    if not seconds:      # 最终闸（2026-11-22 用户定“先做出来再改”）：前面都不问，整片导出只认用户在审片台点的审核线“这一栏定了”；--user-ok 的原话只当备注，不算确认
        from .draft import export_gate
        settled = export_gate(job, name)
    unseen = None
    if not seconds:     # 预览分辨率渲的动画镜头不能进成片（2026-11-12《水电阶段》新8，动画与包装系统会话提供检查）
        from .anim import render_size_problems
        small = render_size_problems(job)
        if small: die('这些动画镜头还是预览分辨率，先按 4K 重渲再导出：\n- ' + '\n- '.join(small))
        unseen = unseen_gate(job, unseen_ok or settled['via'])      # 用户点了“这一栏定了”＝整片看过：没单独看过样张的镜头不再拦，照旧记进导出记录
    name = name or job.get('review_timeline') or die('加 --name 指定要导出的时间线')
    # 主线永远不动（2026-11-13 C10：以前 --name 指到主线会在主线上做音量平衡）
    if name == job.get('main_timeline'): die(f'「{name}」是主线，不从主线导出（主线永远不动，导出前的音量平衡会改它）：要导就导审核线（review 建的那条）')
    if not seconds:
        from .guard import polish_missing, live_resolution
        miss, note = polish_missing(job)        # 每条片子都修音（2026-11-22 新4）：没修音不导整片
        if miss: die(miss)
        if note: say('【注意】' + note)
        for x in live_resolution(job): say('【提醒】' + x)      # 实拍镜头源文件清晰度不够只提醒（新10）
    r, pm, p = project(job)
    tl = timeline(p, name); p.SetCurrentTimeline(tl); st = tl.GetStartFrame()
    # 试导几秒不做平衡、不改 音效表.json/音量.json（2026-11-13 C9：以前试导也平衡整条审核线，之后 status 报“素材比审核线新”），按审核线上现在的音量导
    if seconds: say('试导：不做音量平衡，按审核线上现在的音量导（整片导出时才平衡）')
    elif balance is not False: balance_audio(job, r, pm, tl, force=balance is True)
    out_dir, nm, fmt = export_target(job, out_dir, seconds, name)
    out_dir.mkdir(parents=True, exist_ok=True)
    for m in make_room(out_dir, nm, fmt): say(f'同名旧成片挪进历史：{m}')
    codecs = set((p.GetRenderCodecs(fmt) or {}).values())
    if codec not in codecs: codec = 'H264' if 'H264' in codecs else die(f'{fmt} 可用编码只有 {sorted(codecs)}')
    fresh_render(p)
    if not p.SetCurrentRenderFormatAndCodec(fmt, codec): die('设导出格式失败')
    p.SetCurrentRenderMode(1)
    # 实测（Resolve 21，Apple 硬件 H.265）：VideoQuality 只认数字码率（kb/s），不认 "Best"；SelectAllFrames=False 会被拒，只设入出点即可
    s = {'TargetDir': str(out_dir), 'CustomName': nm, 'FormatWidth': job.W, 'FormatHeight': job.H, 'ExportVideo': True, 'ExportAudio': True, 'VideoQuality': int(kbps)}
    s.update({'MarkIn': st, 'MarkOut': st + job.f(seconds) - 1} if seconds else {'SelectAllFrames': True})
    for k, v in s.items():
        if not p.SetRenderSettings({k: v}): die(f'设导出参数 {k}={v} 失败')
    jid = p.AddRenderJob()
    if not jid: die('加入渲染队列失败')
    say(f'导出 {job.W}×{job.H} {codec} → {out_dir}/{nm}.{fmt} …')
    t0 = time.time(); wait_render(p, jid, 7200, '导出', 3)
    stt = p.GetRenderJobStatus(jid); p.DeleteRenderJob(jid); r.OpenPage('edit')
    f = out_dir / f'{nm}.{fmt}'
    if stt.get('JobStatus') != 'Complete' or not f.exists(): die(f'导出没完成：{stt}')
    if not seconds:
        job.cfg['export_ok'] = {'timeline': name, 'user_said': (user_ok or '').strip() or settled['via'], 'via': settled['via'], 'settled_at': settled.get('at'),
                                'at': time.strftime('%F %T'), **({'未看样张用户同意': unseen} if unseen else {})}; job.save()
    v = probe(f)
    if (int(v['width']), int(v['height'])) != (job.W, job.H): die(f"导出的尺寸是 {v['width']}×{v['height']}")
    say(f"导出完成：{f}（{v['width']}×{v['height']}，{v['codec_name']}，用时 {time.time() - t0:.0f} 秒）")
    # 导完测响度和峰值：2026-10-24《精装房》第一次导出有一个音效爆到 +11.7 dBFS，参数核验没发现。
    # 量不出来要停（2026-11-13 C6：以前静默跳过限幅）；先把同名 SRT 写完再停，免得成片在、字幕没有
    peak_err = None
    try: limit_peak(f)
    except SystemExit as e: peak_err = e
    # 同名 SRT，交付 03_成片/字幕 用。时间按审核线上“字幕（新样式）”轨的实际片段（用户剪过停顿也对得上），
    # 文字按片段的源入点对回字幕表（2026-10-24 审计：只按字幕表写，用户剪过之后会错位）
    rows_p = job.p('合成', '字幕', '字幕表.json'); vs = track_index(tl, 'video', NAMES['subs'])
    if rows_p.exists() and not seconds:
        rows = rj(rows_p)
        items = sorted(tl.GetItemListInTrack('video', vs) or [], key=lambda i: i.GetStart()) if vs else []
        spans = [(i.GetStart() - st, i.GetEnd() - st, i.GetLeftOffset(), i.GetName()) for i in items]
        cues, miss = srt_cues(rows, spans)
        if not cues or miss:
            say(f'【注意】审核线上字幕片段和字幕表对不上（{len(miss)} 段找不到对应字幕），没写 SRT；先核对审核线字幕轨')
        else:
            # 平台字幕要完整口播：被口播大字（花字）替代、从字幕轨拿掉的句子也要写（2026-11-01《别墅地下室防水》219/259 条）
            full = next((rj(x) for x in (job.p('合成', '字幕', '字幕表_全部.json'), rows_p) if x.exists()), rows)
            extra = srt_fill(cues, full)
            if extra and tl.GetEndFrame() - st != int(job['frames']):
                say(f'【注意】字幕轨外还有 {len(extra)} 句口播（被花字替代的），但审核线长度和记录的不一样（剪过），放不准时间，SRT 只写了字幕轨上的；去达芬奇核对后手补')
                extra = []
            cues = sorted(cues + extra)
            srt = f.with_suffix('.srt'); srt.write_text(srt_text(cues, float(job.fps)), encoding='utf-8')
            say(f'字幕 → {srt}（{len(cues)} 条，时间按审核线' + (f'；其中 {len(extra)} 句是花字替代、按字幕表时间补上的' if extra else '') + '）')
    if peak_err: raise peak_err
    return f


def parse_loudness(text):
    """ffmpeg ebur128=peak=true 的输出 → (整体响度 LUFS, 真峰值 dBFS)；读不出来返回 None。"""
    import re
    I = re.findall(r'I:\s+(-?[\d.]+) LUFS', text or ''); pk = re.findall(r'Peak:\s+(-?[\d.]+) dBFS', text or '')
    try: return (float(I[-1]), float(pk[-1])) if I and pk else None
    except ValueError: return None


def loudness(f):
    import subprocess
    return parse_loudness(subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(f), '-vn', '-af', 'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True).stderr)


def limit_peak(f, measure=loudness, ceiling=-1.0):
    """导出后的最后一步：量成片响度和真峰值，真峰值超过 -1 dBFS 就给总输出限幅到 -2 dBFS（画面不重编）。返回 (响度, 真峰值, 限幅了没有)。
    量不出来（ffmpeg 没输出、读不出数）就停下说清楚：成片在哪、峰值没验证、怎么只重跑这一步。export 和 export-peak 命令都用。"""
    f = Path(f)
    if not f.exists(): die(f'没有这个成片文件：{f}')
    again = f'修好后只重跑这一步：python3 thinkcut_edit.py export-peak <项目文件夹> --file "{f}"'
    m = measure(f)
    if not m: die(f'成片已导出，但量不出响度和真峰值（ffmpeg 没给出读数），峰值没验证、也没限幅：{f}\n这个文件先别交付。看 ffmpeg 能不能用、文件有没有声音；{again}')
    say(f'成片响度 {m[0]} LUFS，真峰值 {m[1]} dBFS')
    if m[1] <= ceiling: return m[0], m[1], False
    # 平衡只管每个音效各 -26 LKFS，不管叠在人声上的总峰值（2026-11-01《别墅地下室防水》快门、点击叠人声到 +1.2）：总输出限幅到 -2 dBFS，画面不重编
    say(f'真峰值超过 {ceiling:g} dBFS，给总输出加限幅（-2 dBFS）…')
    tmp = f.with_name(f.stem + '_限幅中' + f.suffix)
    run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(f), '-map', '0:v', '-map', '0:a', '-c:v', 'copy',
         '-af', 'alimiter=limit=0.794:attack=1:release=50:level=disabled', '-c:a', 'aac', '-b:a', '320k', '-movflags', '+faststart', str(tmp)])
    os.replace(tmp, f)
    m = measure(f)
    if not m: die(f'限幅做完了，但限幅后量不出响度和真峰值，没法确认压下来没有：{f}\n这个文件先别交付；{again}')
    say(f'限幅后：响度 {m[0]} LUFS，真峰值 {m[1]} dBFS' + ('' if m[1] <= ceiling else '【还是超 -1：查音效表里增益很大的那几个】'))
    return m[0], m[1], True


def srt_cues(rows, spans):
    """spans = [(起帧, 止帧, 源入点[, 片段名])]；字幕整轨的源入点就是那条的起帧，用它找回文字。
    subfix 换进去的「字幕_aab_a.mov」源入点从 0 起，按文件名对回那一条（2026-10-29 审查：按入点会写成第 1 条的字）。
    返回 (字幕列表, 对不上的片段)。"""
    by = {int(x['start']): x for x in rows}; cues, miss = [], []
    for a, b, src, *nm in spans:
        nm = nm[0] if nm else '字幕整轨.mov'
        if nm != '字幕整轨.mov':
            x = next((r for r in rows if nm.startswith(Path(r.get('file', '')).stem + '_')), None) if nm.startswith('字幕_') else None
        else:
            x = by.get(int(round(src))) or next((r for r in rows if r['start'] <= src < r['end']), None)
        (cues.append((a, b, x['text'])) if x else miss.append((a, b, src)))
    return cues, miss


def srt_fill(cues, full):
    """字幕轨上没有、时间也不和已有字幕重叠的整句（被花字替代的）→ 按字幕表原时间补成字幕。"""
    return [(int(x['start']), int(x['end']), x['text']) for x in full
            if not any(a < x['end'] and x['start'] < b for a, b, _ in cues)]


def srt_text(cues, fps):
    def tc(fr):
        ms = round(fr / fps * 1000); return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'
    return '\n'.join(f"{i}\n{tc(a)} --> {tc(b)}\n{t.replace(' / ', chr(10))}\n" for i, (a, b, t) in enumerate(cues, 1))
