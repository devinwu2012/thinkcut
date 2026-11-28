#!/usr/bin/env python3
"""每个标准项目的片子自己的达芬奇项目库：放在项目文件夹的 02_制作/达芬奇工程，库名就是项目文件夹名。
2026-10-29 用户：这台 Mac 上什么都不存，一个视频一个项目文件夹，工程文件也放在里面；不用本机库，也不用别的库。"""
import os
from pathlib import Path
import subprocess

PREFS = Path(os.environ.get('THINKCUT_RESOLVE_PREFS', str(Path.home() / 'Library/Preferences/Blackmagic Design/DaVinci Resolve')))
SUB = '02_制作/达芬奇工程'
CACHE = {'perfCacheClipsLocation': '02_制作/达芬奇缓存', 'colorGalleryStillsLocation': '02_制作/达芬奇工程/静帧'}


# 达芬奇色彩管理（2026-11-28 用户定：“工程开色彩管理”）：DaVinci YRGB Color Managed、自动色彩管理、SDR（Rec.709）输出。
# 达芬奇自己认每段素材的色彩（iPhone 的 HDR/HLG 也认），预览和导出都转对；B-roll 的 HDR 素材因此不用再转码成 709，直接引用原片。
# 顺序有讲究：先换色彩科学，再开自动管理，再选 SDR（和 core/engine/fx/review.py 的 COLOR_SETTINGS 同一个顺序）。
def _placement():
    import sys
    core = Path(__file__).resolve().parents[2] / 'core'
    if str(core) not in sys.path: sys.path.insert(0, str(core))
    from engine.broll.live import placement
    return placement


COLOR_MANAGED = _placement().COLOR_MANAGED        # 具体设哪几项、为什么，见 core/engine/broll/live/placement.py（B-roll 用同一条规则判断）


def is_color_managed(p):
    """这个工程开着那一套色彩管理没有（达芬奇自己认 HDR 素材，B-roll 直接用原片）。"""
    return _placement().color_managed_ok(p.GetSetting)


def color_manage(p):
    """给当前工程开色彩管理并读回核对；设不上报 RuntimeError。返回改了哪些（已经开着返回空列表）。"""
    if is_color_managed(p): return []
    changed = []
    for k, v in COLOR_MANAGED:
        if str(p.GetSetting(k) or '') == v: continue
        if not p.SetSetting(k, v): raise RuntimeError(f'达芬奇工程设置 {k}={v} 没设上')
        changed.append(f'{k}={v}')
    if not is_color_managed(p):
        raise RuntimeError('达芬奇工程的色彩管理没设上（读回：' + '、'.join(f'{k}={p.GetSetting(k)!r}' for k in _placement().COLOR_CHECK) + '）')
    return changed


def enable_color_management(d, r=None):
    """project.py resolve-color：给达芬奇里正开着的这条片子的工程开色彩管理、保存。只认当前开着的就是这条片子的工程（不替你切工程）。"""
    d = Path(d).resolve(); r = r or connect(launch=False)
    pm = r.GetProjectManager(); p = pm.GetCurrentProject()
    if not p or p.GetName() != d.name:
        raise RuntimeError(f'达芬奇里现在开着的不是《{d.name}》（是《{p.GetName() if p else "没有工程"}》）：先在达芬奇打开这条片子的工程再运行。')
    if p.IsRenderingInProgress(): raise RuntimeError('达芬奇正在渲染，这次没动：等渲完再跑。')
    before = {k: p.GetSetting(k) for k, _ in COLOR_MANAGED}
    changed = color_manage(p)
    if changed and not pm.SaveProject(): raise RuntimeError('工程保存失败。')
    return {'工程': d.name, '原来': before, '改了': changed or '本来就开着', '说明': '达芬奇自己认素材的色彩（含 iPhone HDR），预览和导出都转成 Rec.709；B-roll 的 HDR 素材以后直接用原片'}


def db(d):
    return {'DbType': 'Disk', 'DbName': Path(d).name}


def folder(d):
    return Path(d) / SUB


def resolve_running():
    return subprocess.run(['pgrep', '-f', 'DaVinci Resolve.app/Contents/MacOS/Resolve'], capture_output=True).returncode == 0


def register(d, running=None):
    """建库文件夹，并登记进达芬奇的库列表 dblist.conf（达芬奇脚本接口不能新加库）。
    达芬奇开着时改了会在它退出时被覆盖，所以要先退出。返回 已登记 / 新登记 / 路径已更新。"""
    d = Path(d); path = folder(d)
    if ':' in d.name or ':' in str(path):
        raise RuntimeError('项目文件夹路径里不能有英文冒号，达芬奇的库列表用冒号分隔。')
    path.mkdir(parents=True, exist_ok=True)
    f = PREFS / 'dblist.conf'
    lines = f.read_text(encoding='utf-8').splitlines() if f.exists() else []
    rows = [i for i, l in enumerate(lines) if ':' in l and l.split(':')[0] == d.name]      # 不带冒号的行原样保留、不参与比较
    if rows and all(lines[i].split(':')[1] == str(path) for i in rows):
        return '已登记'
    if rows and any(Path(lines[i].split(':')[1]).exists() for i in rows):
        raise RuntimeError(f'达芬奇里已有同名项目库「{d.name}」指向另一个还在的文件夹；两个片子不能同名。')
    if running is None:
        running = resolve_running()
    if running:
        raise RuntimeError('达芬奇开着：它退出时会覆盖库列表。先保存并退出达芬奇，再运行 project.py resolve-library 项目目录。')
    line = f'{d.name}:{path}::::DISK'
    if rows:      # 同名库原来的文件夹已经不在了：片子挪了位置，改成新路径
        lines[rows[0]] = line; lines = [l for i, l in enumerate(lines) if i not in rows[1:]]; state = '路径已更新'
    else:
        lines.append(line); state = '新登记'
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + '.thinkcut'); tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8'); tmp.replace(f)
    return state


def unregister(name, path):
    """项目改名后，把旧名字那行从库列表里去掉。只删“库名是旧名，且路径正好是旧文件夹”的行；返回删了几行。
    2026-11-20 审查：不再删“路径不存在”的同名行——另一块 NAS 没挂载时会误删别的同名项目。"""
    f = PREFS / 'dblist.conf'
    if not f.exists(): return 0
    lines = f.read_text(encoding='utf-8').splitlines()
    def gone(l):
        part = l.split(':')
        return len(part) > 1 and part[0] == name and part[1] == str(path)
    keep = [l for l in lines if not gone(l)]
    if len(keep) != len(lines):
        tmp = f.with_name(f.name + '.thinkcut'); tmp.write_text('\n'.join(keep) + '\n', encoding='utf-8'); tmp.replace(f)
    return len(lines) - len(keep)


def use_project_folder(p, d):
    """新建或导入的工程：缓存和调色静帧也放进项目文件夹（达芬奇默认放 Mac 的 ~/Movies）。"""
    for k, sub in CACHE.items():
        (Path(d) / sub).mkdir(parents=True, exist_ok=True)
        if not p.SetSetting(k, str(Path(d) / sub)):
            raise RuntimeError(f'设不了工程的 {k}。')


# ---- 2026-11-23 初稿模式实测：达芬奇开着也能给新片子备好工程，不让用户退出达芬奇、不问 ----
# 官方脚本文档（Developer/Scripting/README.txt，2025-10-07 版）里项目库只有 GetDatabaseList / GetCurrentDatabase /
# SetCurrentDatabase 三个接口，没有“新建库、往列表里加库”的；库列表文件 dblist.conf 达芬奇只在启动时读、退出时整个写回。
# 所以达芬奇开着、列表里又没有这条片子的库时，工程先建在现在开着的库里（“借用”），记在 项目.json，存档后用 migrate 迁回。
SWITCH_LOG = '02_制作/达芬奇切换记录.json'
APP = '/Applications/DaVinci Resolve/DaVinci Resolve.app'
API = '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting'


def _json(path, default=None):
    import json
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError): return default


def _write(path, data):
    import json
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.写入中'); tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'); tmp.replace(path)


def same(a, b):
    return bool(a) and bool(b) and a.get('DbType') == b.get('DbType') and a.get('DbName') == b.get('DbName')


def scriptapp():
    import sys
    os.environ.setdefault('RESOLVE_SCRIPT_LIB', APP + '/Contents/Libraries/Fusion/fusionscript.so')
    if API + '/Modules' not in sys.path: sys.path.insert(0, API + '/Modules')
    try:
        import DaVinciResolveScript
        return DaVinciResolveScript.scriptapp('Resolve')
    except Exception:
        return None


def connect(wait=180, launch=True):
    """连达芬奇；没开就自己打开（THINKCUT_NO_OPEN=1 时不开），等到脚本接口连得上。"""
    import time
    r = scriptapp()
    if r: return r
    if not resolve_running():
        if not launch or os.environ.get('THINKCUT_NO_OPEN'):
            raise RuntimeError('达芬奇没开，这次也没让程序去开它。')
        subprocess.run(['open', '-a', APP], check=False)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(3); r = scriptapp()
        if r and r.GetProjectManager(): return r
    raise RuntimeError(f'达芬奇 {wait} 秒内没连上：看它是不是停在启动弹窗上，或“偏好设置 → 系统 → 常规 → 外部脚本使用”是不是“本地”。')


def switch_log(d):
    return _json(Path(d) / SWITCH_LOG, []) or []


def remember(d, pm, cur, why):
    """切走用户开着的工程之前：记下原来的库、工程、时间线（项目里留一笔，做完在汇报里告诉用户）。"""
    import time
    tl = cur.GetCurrentTimeline() if cur else None
    row = {'时间': time.strftime('%Y-%m-%d %H:%M:%S'), '原项目库': (pm.GetCurrentDatabase() or {}).get('DbName'), '原工程': cur.GetName() if cur else None,
           '原时间线': tl.GetName() if tl else None, '原因': why}
    _write(Path(d) / SWITCH_LOG, switch_log(d) + [row])
    return row


def save_current(pm, cur):
    """切走之前先保存用户正开着的工程；刚启动的空白 Untitled Project 存不了也不用存。保存失败就不切。"""
    if not cur: return
    if cur.GetName() == 'Untitled Project' and not cur.GetTimelineCount(): return
    if not pm.SaveProject():
        raise RuntimeError(f'保存达芬奇当前工程《{cur.GetName()}》失败，没有切换（怕丢你正在改的东西）。到达芬奇里看一眼它是不是在弹窗。')


def pick_borrow(pm, own_name):
    """借哪个库：现在开着的磁盘库优先；开着的是网络协作库就不往里放，改用最近登记的磁盘库。"""
    now = pm.GetCurrentDatabase() or {}
    if now.get('DbType') == 'Disk' and now.get('DbName') != own_name: return dict(now)
    disks = [x for x in pm.GetDatabaseList() or [] if x.get('DbType') == 'Disk' and x.get('DbName') != own_name]
    if not disks: raise RuntimeError('达芬奇里没有能用的磁盘项目库。')
    return dict(([x for x in disks if x.get('DbName') != 'Local Database'] or disks)[-1])


def open_project(d, r=None, fps='29.97'):
    """把达芬奇备到“开着这条片子的工程”：没开就登记库并打开；开着就切库（或借用当前库）、没有工程就新建同名工程。全程不问。
    返回一个说明用的 dict。出处：2026-11-23 初稿模式实测（达芬奇开着时新片子登记不了库，还要 AI 自己写脚本建工程）。"""
    import time
    d = Path(d).resolve(); meta = _json(d / '项目.json')
    if not meta or meta.get('name') != d.name:
        raise RuntimeError('这不是标准项目（没有 项目.json，或文件夹名和项目名不一致）。')
    name = d.name; own = db(d); out = {'工程': name}
    if r is None:
        if not resolve_running():
            out['库列表'] = register(d, running=False)
        r = connect()
    pm = r.GetProjectManager(); dbs = pm.GetDatabaseList() or []
    res = meta.setdefault('resolve', {}); target = res.get('database')
    if res.get('borrowed') and any(same(x, own) for x in dbs) and not target_has(pm, target, name):
        target = None      # 借用登记了但工程其实还没建，现在自己的库已经能用：直接用自己的库
        res.pop('database', None); res.pop('borrowed', None); _write(d / '项目.json', meta)
    if not target:
        if any(same(x, own) for x in dbs): target = own
        else:
            target = pick_borrow(pm, name)
            res['database'] = target
            res['borrowed'] = {'库': target['DbName'], '时间': time.strftime('%Y-%m-%d %H:%M:%S'),
                               '说明': f'建工程时达芬奇开着、库列表里还没有这条片子自己的库，工程暂存在「{target["DbName"]}」库；存档后运行 project.py resolve-library <项目> --migrate 迁回自己的库'}
            _write(d / '项目.json', meta)
            out['借用'] = res['borrowed']['说明']
    out['项目库'] = target['DbName']
    cur = pm.GetCurrentProject()
    if cur and cur.GetName() == name and same(pm.GetCurrentDatabase(), target):
        out['结果'] = '已经开着'; return out
    if cur and cur.IsRenderingInProgress():
        raise RuntimeError(f'达芬奇正在渲染《{cur.GetName()}》，这次没动它：等渲完再跑。')
    save_current(pm, cur)
    if cur and not (cur.GetName() == 'Untitled Project' and not cur.GetTimelineCount()):
        row = remember(d, pm, cur, '初稿模式：切到这条片子的工程')
        out['切走了'] = f"达芬奇原来开着「{row['原项目库']}」库里的《{row['原工程']}》" + (f"（时间线「{row['原时间线']}」）" if row['原时间线'] else '') + '，已保存；要回去说一声'
    if not same(pm.GetCurrentDatabase(), target):
        if not pm.SetCurrentDatabase(target) or not same(pm.GetCurrentDatabase(), target):
            raise RuntimeError(f"切不到达芬奇项目库「{target['DbName']}」。")
        time.sleep(2)
    pm.GotoRootFolder()
    if name in (pm.GetProjectListInCurrentFolder() or []):
        p = pm.LoadProject(name)
        if not p: raise RuntimeError(f'项目库「{target["DbName"]}」里的工程《{name}》打不开。')
        out['结果'] = '打开了已有工程'; return out
    p = pm.CreateProject(name)
    if not p: raise RuntimeError(f'在项目库「{target["DbName"]}」里新建工程《{name}》失败（文件夹里可能已有同名工程）。')
    if not p.SetSetting('timelineFrameRate', fps): raise RuntimeError(f'新工程的帧率设不成 {fps}。')
    color_manage(p)                    # 2026-11-28 用户定：新工程开色彩管理
    use_project_folder(p, d)
    if not pm.SaveProject(): raise RuntimeError('新工程保存失败。')
    out['结果'] = f'新建了工程（帧率 {fps}，开了色彩管理，缓存和调色静帧在项目文件夹）'
    return out


def target_has(pm, target, name):
    """借用的库里是不是真有这条片子的工程（只在当前就开着那个库时查，不为了查去切库）。"""
    if not target or not same(pm.GetCurrentDatabase(), target): return True
    pm.GotoRootFolder(); return name in (pm.GetProjectListInCurrentFolder() or [])


def migrate(d, r=None):
    """把借用别的库建的工程迁回片子自己的库。达芬奇关着：只登记库（下次开着时再跑一次完成迁移）；
    开着且列表里已有自己的库：导出工程 → 切到自己的库导入 → 改 项目.json。借用库里那份不删（留给用户自己决定）。"""
    import time
    d = Path(d).resolve(); meta = _json(d / '项目.json') or {}; res = meta.get('resolve') or {}
    if not res.get('borrowed'): return {'结果': '不用迁：工程没有借用别的库'}
    name = d.name; own = db(d); src = res.get('database')
    if r is None:
        if not resolve_running():
            return {'结果': '达芬奇关着：' + register(d, running=False) + '了片子自己的库；下次达芬奇开着时再跑一次本命令，把工程迁过去'}
        r = connect(launch=False)
    pm = r.GetProjectManager()
    if not any(same(x, own) for x in pm.GetDatabaseList() or []):
        return {'结果': f'这次迁不了：达芬奇开着，它的库列表里还没有「{name}」（库只能在达芬奇关着时登记）。不耽误用：工程继续留在「{src["DbName"]}」库；等达芬奇哪次退出后再跑本命令'}
    cur = pm.GetCurrentProject()
    if cur and cur.IsRenderingInProgress(): raise RuntimeError('达芬奇正在渲染，这次没动它。')
    save_current(pm, cur)
    if cur and cur.GetName() not in (name, 'Untitled Project'): remember(d, pm, cur, '迁库：把这条片子的工程迁回自己的库')
    if not same(pm.GetCurrentDatabase(), src):
        if not pm.SetCurrentDatabase(src): raise RuntimeError(f"切不到借用的库「{src['DbName']}」。")
        time.sleep(2)
    pm.GotoRootFolder()
    if name not in (pm.GetProjectListInCurrentFolder() or []): raise RuntimeError(f"借用的库「{src['DbName']}」里没找到工程《{name}》，没有改动。")
    drp = folder(d) / ('.' + name + '.迁库.drp'); drp.parent.mkdir(parents=True, exist_ok=True)
    if not pm.ExportProject(name, str(drp), True) or not drp.is_file(): raise RuntimeError('导出工程失败，没有改动。')
    if not pm.SetCurrentDatabase(own) or not same(pm.GetCurrentDatabase(), own): raise RuntimeError(f'切不到片子自己的库「{name}」，工程还在借用的库里，没有改动。')
    time.sleep(2); pm.GotoRootFolder()
    if name not in (pm.GetProjectListInCurrentFolder() or []) and not pm.ImportProject(str(drp), name):
        raise RuntimeError('工程导进自己的库失败；借用库里那份没动。')
    p = pm.LoadProject(name)
    if not p: raise RuntimeError('迁过去的工程打不开；借用库里那份没动，项目.json 没改。')
    use_project_folder(p, d); pm.SaveProject(); drp.unlink()
    res.pop('database', None); res['migrated'] = dict(res.pop('borrowed'), 迁回时间=time.strftime('%Y-%m-%d %H:%M:%S'))
    _write(d / '项目.json', meta)
    return {'结果': f"工程《{name}》已迁回片子自己的库，达芬奇现在开着它", '提醒': f"借用库「{src['DbName']}」里还留着一份同名工程（没删）；确认新库里的没问题后，可以让用户在达芬奇项目管理器里删掉那份"}
