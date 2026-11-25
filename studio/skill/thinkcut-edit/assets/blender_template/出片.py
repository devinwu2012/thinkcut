#!/usr/bin/env python3
"""Blender 出片（Blender 动画模板，2026-11-12 水电反馈新3/4/6/8）。三种用法：
  python3 出片.py --冒烟                       只出 A 镜头第 1 帧、25% 分辨率，改完 场景.py 先跑这一帧查脚本能不能跑（几秒）
  python3 出片.py --样张 31.2,40.5 [--shot A10] 出这几个时刻的静帧（1080P，加上每个标签刚出现那一刻）→ 样张/，自动发到审片台“动画”栏（页面没开时拼成一张图在“预览”里弹出），并打印“请看哪里、确认什么”给用户
  python3 出片.py --shot A10                    整段渲染，默认 1080P 审片版（scale 50）。要先有样张确认（用户在审片台点“可以”，或 anim-confirm --kind 样张），没有就拦
  python3 出片.py --shot A10 --4k               只在用户看完 OK、导出前用：渲 4K 定稿（2026-11-21 Q159 复发：默认 1080P，不靠记得加参数）
渲染前硬拦：分镜规格.json 每个镜头要有口播原话要点（老工程读 镜头.json）；讲关系/对比/档位的要有草图确认；布局检查要过；整段渲染要有对得上当前代码的样张确认；标签位置不能拿不准。
默认吃满本机：1080P 及以下 8 个 Blender 进程、4K 4 个（M5 Max 实测，渲染测速.json），每个进程一份连续帧号。
出完：标签逐帧合成（白字不加底；没写 fixed 的标签字放在目标点正上方、引线一条竖线，2026-11-21 新14）并逐帧查字压模型/字压字，非 0 帧就不登记；
分镜规格.json 里给这个镜头登记了“盖实拍”的区间（新15/16），那几帧标签不画、不查（总装时会被实拍盖掉）；登记进 合成/动画清单.json 和 合成/渲染尺寸.json（预览尺寸导出前会被拦）。
新出的片先叫 出片/<镜头>.新出.mp4：自检 0 帧才换成正式的 <镜头>.mp4 并登记；有压字就不动原来的正式文件，新出的留成 <镜头>_待查.mp4。
只重渲部分镜头（--shot A10）时，事件.json 只替换这次渲的镜头的事件，别的镜头的留着。
逐帧图（Blender 渲出的原始帧、画好标签的帧）和 Blender 的运行环境临时目录放这台 Mac 的临时目录（2026-11-21 用户定，以前在片子项目的 缓存/ 里，NAS 上慢）：
没出成（出错、超时、标签压字）时原始帧留着，重跑接着渲、不重渲已有的帧；这个镜头整段出片成功并登记后删掉。日志、任务单、每帧记录照旧在 缓存/ 里。"""
import argparse, math, os, shutil, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent; FILM = HERE.parent
def find_skill(env=None, fixed=str(Path.home() / '.claude' / 'skills' / 'thinkcut-edit'), volumes='/Volumes'):
    """找剪辑能力（skill/thinkcut-edit）。这个文件会被复制到片子目录里用，不能按自己的位置推：
    先用环境变量 THINKCUT_SKILL，再用这台 Mac 的技能链接 ~/.claude/skills/thinkcut-edit（指着使用版，2026-11-24 起不写死盘符路径）；写死的不在（NAS 换了挂载名之类）就在 /Volumes 下的 work* 共享盘里找（程序文件夹叫什么都行，开发版排后面），都没有就停下说 NAS 没挂上。"""
    env = os.environ.get('THINKCUT_SKILL') if env is None else env
    if env: return Path(env)
    if Path(fixed).is_dir(): return Path(fixed)
    got = sorted((q for q in Path(volumes).glob('work*/*/studio/skill/thinkcut-edit') if q.is_dir()), key=lambda q: (q.parents[2].name.endswith('-开发'), str(q)))   # 使用版排在开发版前面
    if got: return got[0]
    sys.exit(f'停止：找不到剪辑能力（{fixed}，{volumes}/work* 下也没有）：NAS 没挂上？先在访达里连上 NAS 的 work 共享盘再跑；能力放在别处就设环境变量 THINKCUT_SKILL 指到 skill/thinkcut-edit')


SKILL = find_skill()
sys.path.insert(0, str(SKILL / 'scripts')); sys.path.insert(0, str(SKILL / 'assets' / '包装系统'))
from thinkcut.common import Job, rj, wj, die, say, local_path, local_tmp, local_rm   # noqa: E402
from thinkcut import anim                            # noqa: E402
import biaoqian                                  # noqa: E402

# 等 Blender 的时限（秒，2026-11-21 体检：以前 Blender 卡住就一直等）：固定时间＋最大那一份的帧数×每帧时间。
# 依据 渲染测速.json（M5 Max 实测）：最慢的配置是 4K 开 6 个进程共 21 帧/分，合每个进程 17 秒一帧；1080P 8 个进程合 6 秒一帧。每帧给 120 秒是它的 7～20 倍，正常碰不到。
# 例：20 秒的镜头 600 帧，4K 4 个进程每份 150 帧，正常约半小时，时限 600+150×120 秒＝5 小时多。机器慢、采样数调高时用环境变量 THINKCUT_TIMEOUT_X=2 整体放大。
TIMEOUT_X = float(os.environ.get('THINKCUT_TIMEOUT_X') or 1)
BLENDER_BASE = 600         # 启动 Blender、搭场景留的固定时间
BLENDER_PER_FRAME = 120    # 每帧
ENCODE_BASE = 300          # ffmpeg 把逐帧图合成视频：固定时间
ENCODE_PER_FRAME = 5       # 再加每帧这么多秒（正常 4K 每帧零点几秒，这是几十倍）
RAW_BYTES, LABELED_BYTES = 1.7, 2.3    # 估本机要写多少：每个像素原始帧约 1.7 字节、带标签帧约 2.3 字节（实测 1080×1440 一帧 1.7 MB＋2.3 MB，合每像素 1.1＋1.5 字节，按 1.5 倍留余量）
BLENDER = next((b for b in (os.environ.get('BLENDER'), '/Applications/Blender.app/Contents/MacOS/Blender', '/opt/homebrew/bin/blender') if b and Path(b).exists()), 'blender')


def args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--shot', default='all', help='镜头 id，逗号分隔；默认 分镜规格.json 里 "工程" 写本工程的全部镜头（老工程：镜头.json 里全部）')
    p.add_argument('--4k', dest='full4k', action='store_true', help='只在导出前用：渲 4K 定稿（不加就是 1080P 审片版）')
    p.add_argument('--scale', type=int, default=50, help='分辨率百分比；默认 50＝1080P 审片版（只降 Blender 分辨率，总装缩放进 4K）；100 和 --4k 一样')
    p.add_argument('--jobs', type=int, help='Blender 进程数；不写按 渲染测速.json（1080P 8 个、4K 4 个）')
    p.add_argument('--样张', dest='still', help='只出这些时刻（成片秒，逗号分隔）的静帧')
    p.add_argument('--冒烟', dest='smoke', action='store_true', help='只出第一个镜头第 1 帧、25%% 分辨率，查脚本能跑')
    p.add_argument('--samples', type=int, default=32, help='EEVEE 采样数')
    return p.parse_args()


def default_jobs(scale):
    t = rj(HERE / '渲染测速.json') if (HERE / '渲染测速.json').exists() else {}
    return t.get('1080P', 8) if scale < 100 else t.get('4K', 4)


def split(idx, n):
    """连续帧号分份：每个进程一段连续的帧（不交错），份数不超过帧数。"""
    n = max(1, min(n, len(idx))); k = math.ceil(len(idx) / n); return [idx[i:i + k] for i in range(0, len(idx), k)]


def seconds(v, anchors):
    return float(v) if isinstance(v, (int, float)) else float(anchors[v])


def span_times(l, k=8):
    """标签出现期间取几个时刻（刚出现、中间、快消失），选位和样张都看这几帧——镜头在动时字整段都不能压东西。"""
    a, b = l['_a'] + 0.45, max(l['_a'] + 0.45, l['_b'] - 0.35); return [a + (b - a) * j / (k - 1) for j in range(k)]


def blender_limit(tasks):
    """这一批 Blender 进程最多等多久：固定时间＋帧数最多那一份的帧数×每帧时间。"""
    return (BLENDER_BASE + BLENDER_PER_FRAME * max([len(t.get('indices') or [0]) for t in tasks] or [1])) * TIMEOUT_X


def encode_limit(n):
    """ffmpeg 把 n 帧逐帧图合成视频最多等多久。"""
    return (ENCODE_BASE + ENCODE_PER_FRAME * n) * TIMEOUT_X


def encode(cmd, n, what, kept=None):
    """跑 ffmpeg 把逐帧图合成视频并限时（2026-11-21：以前卡住就一直等）；超时先结束它（subprocess.run 超时会结束子进程）再停下说原因。"""
    limit = encode_limit(n)
    try: subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL, timeout=limit)
    except subprocess.TimeoutExpired:
        die(f'{what}：ffmpeg 把逐帧图合成视频超过 {limit:.0f} 秒还没结束（正常远用不了这么久），已结束它，这个镜头没出成。'
            + (f'逐帧图还留在本机 {kept}，重跑不用重渲 Blender。' if kept else '') + '确实是机器慢，命令前加 THINKCUT_TIMEOUT_X=2 把时限放大一倍再跑')


def frame_home(scale, fp, sid):
    """这个镜头的逐帧图放本机哪里：<本机临时目录>/ThinkCut临时/<片子>/<工程>/<分辨率>_<代码指纹>/<镜头>/（里面 raw＝原始帧，labeled＝带标签帧，运行环境＝Blender 的临时目录）。"""
    return local_path(FILM, HERE.name, f'{scale}_{fp}', sid)


def stale_frames(fp, sid):
    """这个镜头留在本机的、代码指纹已经对不上的逐帧图（改过场景/标签/布局，续不了了）：开始渲之前清掉。"""
    return [d for d in local_path(FILM, HERE.name).glob(f'*_*/{sid}') if d.is_dir() and d.parent.name.split('_', 1)[1] != fp]


def run_blender(tasks, cache, rt=None):
    """rt＝Blender 的运行环境临时目录（出片时给本机的位置；不给就在 cache 里，和以前一样）。每次开始先清空（上次崩溃的残留），正常渲完删掉；出错、超时留着给人看。"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1'); rt = Path(rt) if rt else cache / '运行环境'; shutil.rmtree(rt, ignore_errors=True)
    for k in ('TMPDIR', 'BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_EXTENSIONS', 'XDG_CACHE_HOME'):
        env[k] = str(rt / k); Path(env[k]).mkdir(parents=True, exist_ok=True)
    ps = []
    for k, t in enumerate(tasks):
        f = cache / f"任务_{t['shot']}_{k}.json"; wj(f, t)
        ps.append(subprocess.Popen([BLENDER, '--background', '--factory-startup', '--python-exit-code', '1', '--python', str(HERE / 'worker.py'), '--', str(f)],
                                   stdout=open(cache / f"blender_{t['shot']}_{k}.log", 'w'), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, cwd=HERE))
    limit = blender_limit(tasks); deadline = time.time() + limit; bad = []
    try:
        for k, p in enumerate(ps):
            if p.wait(timeout=max(0, deadline - time.time())): bad.append(k)
    except subprocess.TimeoutExpired:
        late = [k for k, p in enumerate(ps) if p.poll() is None]
        for p in ps:            # Blender 是几个进程一起跑：还没结束的全部结束
            if p.poll() is None: p.kill()
        for p in ps: p.wait()
        die(f'Blender 渲了 {limit:.0f} 秒还没结束（正常远用不了这么久），已把还在跑的 {len(late)} 个 Blender 进程全部结束，这个镜头没出成。看日志卡在哪：'
            + '、'.join(str(cache / f"blender_{tasks[k]['shot']}_{k}.log") for k in late)
            + '。确实是镜头太重、机器慢，命令前加 THINKCUT_TIMEOUT_X=2 把时限放大一倍再跑（已渲出的帧留在'
            + (f"本机 {tasks[0]['raw']}，重跑接着渲）" if tasks and tasks[0].get('raw') else '缓存里）'))
    if bad: die('Blender 进程出错：' + '、'.join(str(cache / f"blender_{tasks[k]['shot']}_{k}.log") for k in bad))
    shutil.rmtree(rt, ignore_errors=True)


def choose_above(frames, l, pt, size, taken, protected):
    """标签选位（2026-11-21 新14，用户：“为什么不直接就一个引线上去，还得拐个弯？”）：没写 fixed 的标签只在目标点正上方找位置——
    字的左右范围盖住目标点（目标点落在字中间四成里），引线就是一条竖线；正上方放不下，再到整个画面找，并标成拿不准、出位置样张问用户。"""
    if l.get('fixed'): return biaoqian.choose(frames, l, pt, size, taken, protected)
    s = biaoqian.scale_of(size); bw = biaoqian.text_box(l, size)[0] / s; x, y = pt[0] / s, pt[1] / s; sx0, sy0, sx1, sy1 = biaoqian.SAFE_4K
    col = (max(sx0, x - bw * 0.7), sy0, min(sx1, x + bw * 0.7), min(sy1, y - 120))
    if col[2] - col[0] >= bw and col[3] - col[1] >= biaoqian.text_box(l, size)[1] / s:
        p = biaoqian.choose(frames, l, pt, size, taken, protected, safe=col)
        if p.get('rect') and p['rect'][0] <= pt[0] <= p['rect'][2]: return p
    p = biaoqian.choose(frames, l, pt, size, taken, protected)
    p.update(unsure=True, why='目标点正上方放不下字' + ('；' + p['why'] if p.get('why') else '')); return p


def _composite(a):
    """一帧：读原始渲染 → 画标签 → 存；返回本帧字的方框和自检问题（并行进程里跑）。"""
    from PIL import Image
    raw, out, labs, rec, places, t, need = a
    im = Image.open(raw); lab, boxes = biaoqian.draw(im, labs, rec, places, t)
    lab.convert('RGB').save(out, compress_level=1)
    return rec['i'], boxes, (biaoqian.check_frame(im, boxes, labs) if need and boxes else [])


def settle(pending, ok):
    """pending = {镜头: (新出的临时文件, 正式文件)}。标签自检通过（ok）才把新出的换成正式文件；
    没过：正式文件不动（登记表指着它，下次总装还是用通过的那一版），新出的改名成 <正式名>_待查.mp4 留着看。返回 {镜头: 现在这份新出的在哪}。"""
    out = {}
    for sid, (new, final) in pending.items():
        dst = final if ok else final.with_name(final.stem + '_待查.mp4')
        os.replace(new, dst); out[sid] = dst
    return out


def main():
    a = args(); job = Job(FILM); fps = float(job.fps); B = rj(FILM / '分镜.json'); anchors = B.get('anchors', {})
    shot_ids = anim.project_shots(HERE, FILM) if a.shot == 'all' else a.shot.split(',')
    if not shot_ids: die(f'不知道这个工程出哪些镜头：在 分镜规格.json 的镜头里写 "工程": "{HERE.name}"，或用 --shot 指定')
    beats = {s['id']: s for s in B['shots']}
    for s in shot_ids:
        if s not in beats: die(f'分镜.json 里没有镜头 {s}')
    mode = 'smoke' if a.smoke else 'still' if a.still else 'full'
    scale = 25 if a.smoke else 100 if a.full4k else a.scale
    if mode == 'smoke': shot_ids = shot_ids[:1]
    if mode != 'smoke': anim.require(job, HERE, shot_ids, full=(mode == 'full'))
    W, H = job.W * scale // 100 // 2 * 2, job.H * scale // 100 // 2 * 2; s4 = W / job.W
    jobs = a.jobs or (1 if mode == 'smoke' else default_jobs(scale))
    labels = [l for l in rj(HERE / '标签.json').get('labels', [])] if (HERE / '标签.json').exists() else []
    for l in labels: l['_a'], l['_b'] = seconds(l['start'], anchors), seconds(l['end'], anchors)
    lp = HERE / '标签定位.json'; places = rj(lp) if lp.exists() else {}
    pending, events, report, timing, made, homes = {}, [], {}, {}, [], {}
    for sid in shot_ids:
        sh = beats[sid]; f0 = round(sh['a'] * fps); n = round(sh['b'] * fps) - f0; labs = [l for l in labels if l['shot'] == sid]
        if mode == 'full': idx = list(range(n))
        elif mode == 'smoke': idx = [0]
        else:
            show = [float(x) for x in a.still.split(',') if sh['a'] <= float(x) < sh['b']] + [l['_a'] + 0.45 for l in labs]   # 给用户看的
            fi = lambda t: min(n - 1, max(0, round(t * fps) - f0)); show = {fi(t) for t in show}
            idx = sorted(show | {fi(u) for l in labs for u in span_times(l)})                                              # 加上选位要看的帧
        if not idx: continue
        fp = anim.fingerprint(job, HERE, [sid])
        cache = HERE / '缓存' / f"{scale}_{fp}" / sid; cache.mkdir(parents=True, exist_ok=True)   # 按代码指纹分目录：改了场景/标签/布局就不会续用旧帧。这里只放日志、任务单、每帧记录
        for d in stale_frames(fp, sid): local_rm(d)                                              # 指纹对不上的旧帧续不了：清掉
        home = frame_home(scale, fp, sid); raw = home / 'raw'; lab_dir = home / 'labeled'; homes[sid] = fp
        todo = sum(1 for i in idx if not (raw / f'{i:06d}.png').exists())                        # 已有的原始帧接着用（中断后续渲）
        local_tmp(FILM, HERE.name, f'{scale}_{fp}', sid, clean=False, what=f'{sid} 的逐帧图（{len(idx)} 帧 {W}×{H}）',
                  need=int(W * H * (todo * RAW_BYTES + (0 if mode == 'smoke' else len(idx)) * LABELED_BYTES)))
        raw.mkdir(parents=True, exist_ok=True); shutil.rmtree(lab_dir, ignore_errors=True)       # 带标签帧每次都重画：先清掉上次的
        base = dict(shot=sid, frame0=f0, fps_num=job.fps.numerator, fps_den=job.fps.denominator, resolution=[W, H], samples=a.samples, raw=str(raw),
                    shots={k: dict(a=beats[k]['a'], b=beats[k]['b']) for k in shot_ids})
        tasks = [dict(base, indices=part, records=str(cache / f'记录_{k}.json')) for k, part in enumerate(split(idx, jobs))]
        say(f'{sid}：{len(idx)} 帧，{W}×{H}，{len(tasks)} 个 Blender 进程（每个一段连续帧）…'); t0 = time.time()
        for f in cache.glob('记录_*.json'): f.unlink()
        run_blender(tasks, cache, home / '运行环境'); dt = time.time() - t0
        timing[sid] = dict(frames=len(idx), seconds=round(dt, 1), per_min=round(len(idx) / dt * 60, 1), jobs=len(tasks), size=[W, H])
        recs = {}; ev = []
        for k in range(len(tasks)):
            d = rj(cache / f'记录_{k}.json'); ev = d['events']
            for r in d['frames']: recs[r['i']] = r
        events += [e for e in ev if e['shot'] == sid]
        if mode == 'smoke': say(f'冒烟通过：{raw / "000000.png"}（{dt:.0f} 秒）'); continue
        # 标签选位（4K 坐标存，按当前分辨率缩放）：已定的沿用；新的在“刚出现那一刻”的画面上选，拿不准出位置样张
        from PIL import Image
        cur = {}
        for l in labs:
            sig = [l['text'], l.get('point'), l.get('fixed')]; old = places.get(l['id'])
            if old and old.get('sig') == sig:
                p = dict(old)
            else:
                ii = [min(n - 1, max(0, round(u * fps) - f0)) for u in span_times(l)]; ii = [i for i in ii if (raw / f'{i:06d}.png').exists()]
                i = ii[0]; im = [Image.open(raw / f'{k:06d}.png') for k in ii]; rec = recs[i]
                pt = rec['points'].get(l.get('point'), {}); taken = [cur[k]['rect'] for k in cur if cur[k].get('rect')]
                p = choose_above(im, l, (pt.get('x', W / 2), pt.get('y', H / 2)), (W, H), taken, rec['protected'])
                for key in ('rect',):
                    if p.get(key): p[key] = [v / s4 for v in p[key]]
                for o in p['options']: o['rect'] = [v / s4 for v in o['rect']]
                p['sig'] = sig
                if p['unsure']:
                    sheet = biaoqian.position_sheet(im[0], l, pt, dict(p, options=[dict(o, rect=[v * s4 for v in o['rect']]) for o in p['options']]), HERE / '样张' / f"标签位置_{l['id']}.jpg")
                    say(f"  标签“{l['id']}”位置拿不准（{p['why']}）→ 位置样张 {sheet}，给用户选 A/B/C")
            places[l['id']] = p; cur[l['id']] = dict(p, rect=[v * s4 for v in p['rect']] if p.get('rect') else None)
        wj(lp, places)
        lab_dir.mkdir(exist_ok=True)
        cov = anim.cover_list(job)      # 盖实拍的帧：标签不画、不查
        work = [(raw / f'{i:06d}.png', lab_dir / f'{i:06d}.png', [] if anim.covered(cov, sid, i) else labs, recs[i], cur, recs[i]['t'], mode == 'full') for i in idx]
        bad = {}
        with ProcessPoolExecutor(max_workers=8) as ex:
            for i, boxes, probs in ex.map(_composite, work, chunksize=4):
                for q in probs: bad.setdefault(q.split(' ')[0], []).append(i)
        if mode == 'still':
            out = HERE / '样张'; out.mkdir(exist_ok=True)
            for i in sorted(show):
                t = (f0 + i) / fps; Image.open(lab_dir / f'{i:06d}.png').convert('RGB').save(out / f'{sid}_{t:.2f}秒.jpg', quality=92); made.append((sid, round(t, 2), out / f'{sid}_{t:.2f}秒.jpg'))
            say(f'{sid} 样张 → {out}（{len(show)} 张）'); continue
        report[sid] = dict(重叠帧数=len({i for v in bad.values() for i in v}), 标签={k: len(v) for k, v in bad.items()})
        for k in bad:                                   # 整段渲完才发现压字：用这个标签出现期间的全部帧（隔一帧取一帧）重新选位，出位置样张问用户
            l = next((x for x in labs if x['id'] == k), None)
            if not l or places[k].get('mode') == '世界坐标': continue
            ii = [i for i in range(n) if l['_a'] <= recs[i]['t'] < l['_b']][::2]; frs = [Image.open(raw / f'{i:06d}.png') for i in ii]
            pt = recs[ii[0]]['points'].get(l.get('point'), {}); taken = [cur[j]['rect'] for j in cur if j != k and cur[j].get('rect')]
            p = choose_above(frs, l, (pt.get('x', W / 2), pt.get('y', H / 2)), (W, H), taken, recs[ii[0]]['protected'])
            for o in p['options']: o['rect'] = [v / s4 for v in o['rect']]
            if p.get('rect'): p['rect'] = [v / s4 for v in p['rect']]
            p.update(sig=places[k]['sig'], unsure=True, why=f"整段渲完发现 {len(bad[k])} 帧压字，已按整段重新找了位置")
            places[k] = p; wj(lp, places)
            sheet = biaoqian.position_sheet(frs[len(frs) // 2], l, pt, dict(p, options=[dict(o, rect=[v * s4 for v in o['rect']]) for o in p['options']]), HERE / '样张' / f'标签位置_{k}.jpg')
            say(f'  “{k}”压字 {len(bad[k])} 帧 → 新的位置样张 {sheet}；用户选定后重跑只重新合成标签，不重渲 Blender')
        final = HERE / '出片' / (f'{sid}.mp4' if scale == 100 else f'{sid}_预览{scale}.mp4'); final.parent.mkdir(exist_ok=True)
        dst = final.with_name(final.stem + '.新出.mp4')      # 先出到临时名：自检过了才换成正式文件（登记表指着正式文件，不能先被压字版盖掉）
        encode(['ffmpeg', '-v', 'error', '-y', '-framerate', job['fps'], '-i', str(lab_dir / '%06d.png'), '-frames:v', str(n), '-c:v', 'libx264', '-crf', '16',
                '-pix_fmt', 'yuv420p', '-r', job['fps'], str(dst)], n, sid, home)
        if anim._ffprobe_frames(dst) != n: die(f'{dst.name} 帧数不对，应为 {n}')
        pending[sid] = (dst, final); say(f'{sid} → {dst.name}（{n} 帧，{timing[sid]["per_min"]} 帧/分；标签自检过了才换成 {final.name}）')
    (HERE / '自查').mkdir(exist_ok=True); wj(HERE / '自查' / '渲染计时.json', timing)
    if made: anim.still_sheet(job, made, HERE / '样张' / f"样张拼图_{'_'.join(dict.fromkeys(s for s, _, _ in made))}.jpg", HERE)   # 2026-11-21 新4：弹出拼图，说清看哪里、确认什么
    if mode != 'full': return
    wj(HERE / '自查' / '标签重叠.json', report); total = sum(r['重叠帧数'] for r in report.values())
    say(f'标签自检：字压模型/字压字共 {total} 帧' + ''.join(f'\n  {k}：{v["标签"]}' for k, v in report.items() if v['重叠帧数']))
    files = settle(pending, total == 0)
    if total: die('有重叠帧，不登记、不交审：先挪标签位置（标签.json 写 fixed，或拿不准出位置样张问用户）再渲。\n原来的正式出片没动（登记表、总装还是用它）；这次新出的留作待查：'
                  + '、'.join(str(f) for f in files.values()) + '（看完可以删）。\n原始帧留在本机 ' + '、'.join(str(frame_home(scale, homes[k], k)) for k in files)
                  + '，挪好标签重跑只重新合成标签、不重渲 Blender')
    anim.register(job, HERE, 'blender', list(files), scale, files, events, [W, H])
    for sid in files:                    # 出片成功并登记了：这个镜头留在本机的逐帧图用完了，删掉（冒烟、样张留下的更小尺寸的也一起删）
        for d in local_path(FILM, HERE.name).glob(f'*_{homes[sid]}/{sid}'):
            if d.parent.name.split('_', 1)[0].isdigit() and int(d.parent.name.split('_', 1)[0]) <= scale: local_rm(d)
    say('已登记进 合成/动画清单.json、合成/渲染尺寸.json' + ('' if scale == 100 else f'（{scale}% 预览＝1080P 审片版；用户看完 OK、导出前加 --4k 重渲）'))


if __name__ == '__main__':
    main()
