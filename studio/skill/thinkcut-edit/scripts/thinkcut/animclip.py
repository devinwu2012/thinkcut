"""动画栏“带口播播放”（2026-11-24 用户：“动画……应该对应某一句话吧？……边放动画的时候，那句话也在播放……就知道动效、卡点对不对”；画面用“成片里那一段”）。

一项动画（审片台.json 里 anim.shots 记着它是哪几个镜头）→ 在 分镜.json 里查这几个镜头在成片上的起止秒 →
从 合成/B肉.mp4（动画铺进时间线的画面）切出这一段，配上 合成/A肉.mov（剪好的口播）同一段的声音，存成一个小视频；
再从 口播字时间.json 取这一段里每个字的起止秒，页面按声音一个字一个字亮；卡点＝这一项发来的图上写的“第 N 秒”。

小视频放在这条片子自己的 审片工作台/审片台/<编号>/带口播/ 里（片子的东西放片子文件夹），按 镜头起止＋两个成片文件的修改时间 起名，
成片换了就重新切；只读成片，不改任何东西。
"""
import json, re, shutil, subprocess
from pathlib import Path
from . import board


class Oops(Exception):
    pass


def _shots(job):
    f = job.dir / '分镜.json'
    if not f.exists(): raise Oops('片子目录里还没有 分镜.json（动画还没排进时间线），没法对上口播')
    d = json.loads(f.read_text(encoding='utf-8'))
    return {s['id']: s for s in (d.get('shots') if isinstance(d, dict) else d) if isinstance(s, dict) and 'id' in s}


def span(job, it):
    """这一项动画在成片上的起止秒（几个镜头就取最早的开头、最晚的结尾）。"""
    an = (board.latest(it).get('anim') or {})
    ids = an.get('shots') or []
    if not ids: raise Oops('这一项没记是哪个镜头（不是用出片发来的样张），没法对上口播')
    sh = _shots(job); got = [sh[i] for i in ids if i in sh]
    if not got: raise Oops(f'分镜.json 里找不到镜头 {"、".join(ids)}')
    return float(min(s['a'] for s in got)), float(max(s['b'] for s in got)), ids


def chars(job, a, b):
    """这一段里的字：[[字, 起, 止], …]（秒，成片时间）。"""
    f = job.dir / '口播字时间.json'
    if not f.exists(): return []
    toks = json.loads(f.read_text(encoding='utf-8')).get('tokens') or []
    return [[t['c'], round(float(t['t0']), 3), round(float(t['t1']), 3)] for t in toks if float(t['t1']) > a + 0.01 and float(t['t0']) < b - 0.01]


def beats(it):
    """卡点：这一项最新版的图上写的“第 N 秒”（成片时间）。"""
    out = []
    for f in board.latest(it).get('files') or []:
        m = re.search(r'第\s*([0-9]+(?:\.[0-9]+)?)\s*秒', str(f.get('label') or ''))
        if m: out.append(float(m.group(1)))
    return sorted(set(out))


def clip(job, iid, make=True):
    """给页面的：{ok, a, b, shots, video（审片台下的相对路径）, chars, beats, older}。older＝成片比这一版动画旧（AI 还没把新版出进成片）。"""
    d = board.load(job)
    it = next((x for x in d.get('items', []) if x.get('id') == iid), None)
    if not it: raise Oops(f'没有这一项：{iid}')
    if it.get('column') != '动画': raise Oops('带口播播放只在动画栏')
    a, b, ids = span(job, it)
    comp = job.dir / '合成'; vb, va = comp / 'B肉.mp4', comp / 'A肉.mov'
    out = {'ok': True, 'a': a, 'b': b, 'shots': ids, 'chars': chars(job, a, b), 'beats': [x for x in beats(it) if a - 0.5 <= x <= b + 0.5]}
    if not vb.exists() or not va.exists():
        out.update(video=None, why='还没出成片（合成/B肉.mp4、A肉.mov），AI 出片以后这里就能带口播看')
        return out
    import time
    ver_at = board.latest(it).get('at') or ''
    try: out['older'] = bool(ver_at) and time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(vb.stat().st_mtime)) < ver_at
    except OSError: out['older'] = False
    key = f'{a:.3f}-{b:.3f}-{int(vb.stat().st_mtime)}-{int(va.stat().st_mtime)}'
    dest = board.store(job) / iid / '带口播' / f'{key}.mp4'
    out['video'] = str(dest.relative_to(board.store(job)))
    if dest.exists() or not make: return out
    ff = shutil.which('ffmpeg')
    if not ff: raise Oops('这台 Mac 上没找到 ffmpeg，切不出带口播的小样')
    dest.parent.mkdir(parents=True, exist_ok=True)
    for old in dest.parent.glob('*.mp4'): old.unlink()           # 只留现在这一份（成片换了，旧的就没用了）
    tmp = dest.with_suffix('.tmp.mp4')
    cmd = [ff, '-v', 'error', '-y', '-ss', f'{a:.3f}', '-to', f'{b:.3f}', '-i', str(vb), '-ss', f'{a:.3f}', '-to', f'{b:.3f}', '-i', str(va),
           '-map', '0:v:0', '-map', '1:a:0?', '-vf', 'scale=-2:960', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode or not tmp.exists():
        tmp.unlink(missing_ok=True); raise Oops('切带口播的小样没成功：' + (r.stderr.strip().splitlines() or ['ffmpeg 出错'])[-1])
    tmp.rename(dest)
    return out
