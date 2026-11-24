# 备料（2026-11-24 用户定：“在文案确定之后……创建好这个文件夹，然后把B-roll素材发给他，让他自动整理到文件夹里”）。
# 文案定稿后，用户在审片台“文案”栏把口播录像、B-roll 直接拖进页面，或写这台 Mac 上的路径由程序复制；都放进项目 01_原始素材（保留原文件名，同名不覆盖，
# 改成“名字 (2).mp4”）。分类整理（A roll／B roll／音乐、登记素材清单）仍由 AI 在用户点“开工”以后按 thinkcut-project 的规矩做。
# 记录在 <片子目录>/审片工作台/备料.json：收到了哪些、各放在哪、多大、怎么来的。传到一半的写成 .名字.接收中，传完才改成真名字，断了就删掉。
import contextlib, os, shutil, threading, time
from pathlib import Path
from . import board
from .common import rj, wj

CHUNK = 4 << 20                  # 4MB 一块：大文件边收边写，不整个放进内存
PART = '.接收中'
_lock = threading.Lock()
_active = {}                     # 正在接收／复制的：编号 → {name, dest, size, done, how, src, film, error, at}
_seq = [0]


class Oops(Exception):
    pass


def now(): return time.strftime('%Y-%m-%d %H:%M:%S')
def folder(job):
    pd = board.project_dir(job); return pd / '01_原始素材' if pd else None
def record_file(job): return job.dir / '审片工作台' / '备料.json'


def size_text(n):
    return f'{n / 1024 ** 3:.2f} GB' if n >= 1024 ** 3 else f'{n / 1024 ** 2:.1f} MB' if n >= 1024 ** 2 else f'{max(n, 0) / 1024:.0f} KB'


def safe_name(name):
    """只要文件名本身（去掉路径），不收空名、点开头（隐藏文件）的。"""
    n = Path(str(name or '').replace('\\', '/')).name.strip()
    if not n or n in ('.', '..') or n.startswith('.'): raise Oops(f'文件名不对：“{name}”')
    return n


def _taken(p):
    return p.exists() or (p.parent / ('.' + p.name + PART)).exists() or any(a['dest'] == str(p) for a in _active.values())


def free_path(dirp, name):
    """同名不覆盖：已有（或正在传）同名的，改成“名字 (2).扩展名”“名字 (3)…”。调用方拿着 _lock。"""
    p = Path(dirp) / name
    if not _taken(p): return p
    stem, suf = (p.stem, p.suffix) if p.suffix and not p.is_dir() else (p.name, '')
    for k in range(2, 10000):
        q = p.with_name(f'{stem} ({k}){suf}')
        if not _taken(q): return q
    raise Oops('同名的太多了')


def _target(job):
    f = folder(job)
    if not f: raise Oops('这条片子还没有项目文件夹（项目.json），没地方放素材')
    f.mkdir(parents=True, exist_ok=True); return f


def _begin(job, name, how, size, src=''):
    with _lock:
        dest = free_path(_target(job), safe_name(name)); _seq[0] += 1; k = str(_seq[0])
        _active[k] = {'id': k, 'name': safe_name(name), 'dest': str(dest), 'size': size, 'done': 0, 'how': how, 'src': src, 'film': str(job.dir), 'error': '', 'at': now()}
    return k, dest


def _record(job, a, ok):
    """传完／复制完写进 备料.json；失败的只留在内存里给页面看一会儿。"""
    with _lock:
        a = _active.pop(a['id'], a)
        if not ok: a['failed_at'] = time.time(); _failed.append(a); del _failed[:-20]; return a
        f = record_file(job); f.parent.mkdir(parents=True, exist_ok=True)
        d = rj(f) if f.exists() else {'说明': '备料：用户在审片台“文案”栏拖进来、或写路径由程序复制进 01_原始素材 的素材（AI 开工后按 thinkcut-project 的规矩归类、登记素材清单）', 'files': []}
        pd = board.project_dir(job); dest = Path(a['dest'])
        rec = {'name': a['name'], 'path': str(dest.relative_to(pd)) if pd and pd in dest.parents else str(dest), 'size': a['size'], 'how': a['how'], 'at': now(), **({'src': a['src']} if a['src'] else {})}
        d['files'].append(rec); wj(f, d); return rec


_failed = []


def receive(job, name, stream, length):
    """页面拖进来的一个文件：按块从请求里读、写进 01_原始素材。length＝请求的 Content-Length（就是文件大小）。没收全（页面关了、断了）就删掉半截，报错。"""
    if length < 0: raise Oops('不知道文件多大（缺 Content-Length）')
    k, dest = _begin(job, name, '拖进来', length); a = _active[k]; part = dest.parent / ('.' + dest.name + PART)
    try:
        with open(part, 'wb') as out:
            left = length
            while left > 0:
                b = stream.read(min(CHUNK, left))
                if not b: raise Oops(f'没传完：收到 {size_text(length - left)}，应该 {size_text(length)}（页面关了或断了），已经删掉半截，重新拖一次')
                out.write(b); left -= len(b); a['done'] = length - left
        os.rename(part, dest)
    except BaseException as e:
        with contextlib.suppress(FileNotFoundError): part.unlink()
        a['error'] = str(e) if isinstance(e, Oops) else f'没存上：{e}'; _record(job, a, False)
        raise Oops(a['error']) from None
    return _record(job, a, True)


def _du(p):
    if p.is_file(): return p.stat().st_size
    return sum(q.stat().st_size for q in p.rglob('*') if q.is_file() and not q.is_symlink() and not q.name.startswith('.'))     # 和复制时一样跳过隐藏文件


def _copy(job, k, src, dest):
    a = _active[k]; part = dest.parent / ('.' + dest.name + PART)
    try:
        if src.is_dir():
            part.mkdir()
            for q in sorted(src.rglob('*')):
                if q.is_symlink() or q.name.startswith('.'): continue
                t = part / q.relative_to(src)
                if q.is_dir(): t.mkdir(parents=True, exist_ok=True); continue
                t.parent.mkdir(parents=True, exist_ok=True)
                with open(q, 'rb') as i, open(t, 'wb') as o:
                    while True:
                        b = i.read(CHUNK)
                        if not b: break
                        o.write(b); a['done'] += len(b)
                shutil.copystat(q, t)
        else:
            with open(src, 'rb') as i, open(part, 'wb') as o:
                while True:
                    b = i.read(CHUNK)
                    if not b: break
                    o.write(b); a['done'] += len(b)
            shutil.copystat(src, part)
        os.rename(part, dest); _record(job, a, True)
    except Exception as e:
        shutil.rmtree(part, ignore_errors=True) if part.is_dir() else part.unlink() if part.exists() else None
        a['error'] = f'复制 {src} 没成：{e}'; _record(job, a, False)


def add_paths(job, text, wait=False):
    """登记这台 Mac 上的路径（一行一个，文件或文件夹）：程序在后台复制进 01_原始素材（原文件不动）。大文件优先用这个，不经浏览器。
    返回 {started: [{id, name, size}], errors: [...]}。wait=True 时复制完才返回（测试用）。"""
    tgt = _target(job); started, errors, threads = [], [], []
    for line in [x.strip().strip('"\'') for x in str(text or '').splitlines() if x.strip()]:
        p = Path(os.path.expanduser(line))
        if not p.is_absolute(): errors.append(f'“{line}”：要写完整路径（从 / 开头；在访达里选中文件按 ⌥⌘C 拷贝路径）'); continue
        if not p.exists(): errors.append(f'“{line}”：找不到这个文件或文件夹'); continue
        p = p.resolve()
        if p == tgt or tgt in p.parents or p in tgt.parents: errors.append(f'“{line}”：已经在这条片子的 01_原始素材 里了（或包含它），不用再放'); continue
        try: size = _du(p)
        except OSError as e: errors.append(f'“{line}”：读不了（{e}）'); continue
        try: k, dest = _begin(job, p.name, '登记路径', size, str(p))
        except Oops as e: errors.append(f'“{line}”：{e}'); continue
        t = threading.Thread(target=_copy, args=(job, k, p, dest), daemon=True); t.start(); threads.append(t)
        started.append({'id': k, 'name': p.name, 'size': size})
    if not started and not errors: raise Oops('没写路径：一行写一个文件或文件夹的完整路径')
    if wait:
        for t in threads: t.join()
    return {'ok': bool(started), 'started': started, 'errors': errors, **({'error': '；'.join(errors)} if errors and not started else {})}


def info(job):
    """给页面：放在哪、已经收到的（还在不在）、正在传的进度、最近没成的。"""
    f = folder(job); pd = board.project_dir(job); rec = record_file(job)
    files = (rj(rec).get('files') or []) if rec.exists() else []
    for x in files: x['exists'] = bool(pd) and (pd / x['path']).exists()
    with _lock:
        act = [dict(a) for a in _active.values() if a['film'] == str(job.dir)]
        bad = [dict(a) for a in _failed if a['film'] == str(job.dir) and time.time() - a['failed_at'] < 600]
    return {'folder': str(f) if f else '', 'files': files, 'active': act, 'failed': bad}


def summary(job):
    """给 AI（--wait 打印“开工”时）：用户在页面上放进来的素材，一行一个。"""
    rec = record_file(job)
    files = (rj(rec).get('files') or []) if rec.exists() else []
    if not files: return []
    return ['用户在页面上放进 01_原始素材 的素材（还没归类：按 thinkcut-project 的规矩归到 A roll／B roll／音乐、改名、登记素材清单）：'] + \
        [f"   {x['path']}（{size_text(x['size'])}，{x['how']}{'，原文件 ' + x['src'] if x.get('src') else ''}）" for x in files]
