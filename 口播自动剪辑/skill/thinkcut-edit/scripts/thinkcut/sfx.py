# 音效表：按花字音效收藏的选段（源起点＋长度）取音效，找选段里最强的一下起音，让它正好落在卡点那一帧。
# 音量按规范自动定（2026-10-24 用户：音效比人声还大，要按音效规范来，人声也按规范）：
#   人声：口播峰值调到 voice_peak_db（默认 -1 dBFS）；job.json 的 voice_db 写数字时以数字为准。
#   音效：选段实测峰值调到 sfx_peak_db（默认 -5 dBFS，音效规范）；长于 1 秒的持续声（冲水、气声）
#        另外压到最响处比人声平均响度低 sfx_under_voice_lu（默认 6 LU），免得盖过说话。分镜规格里写明的 gain 优先。
import math, os, re, subprocess, time
from pathlib import Path
import numpy as np
from .common import Job, ASSETS, die, rj, wj, say, duration

_cache = {}
_lv = {}


def level(path, ss=0.0, t=None):
    """返回 (逐声道最大采样峰值 dBFS, 最响 400ms 响度 LUFS, 整体响度 LUFS)"""
    key = (str(path), ss, t)
    if key not in _lv:
        cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-ss', f'{ss:.4f}'] + (['-t', f'{t:.4f}'] if t else []) + ['-i', str(path), '-vn',
               '-af', 'astats=measure_perchannel=Peak_level:measure_overall=none,ebur128', '-f', 'null', '-']
        o = subprocess.run(cmd, capture_output=True, text=True).stderr
        pk = [float(x) for x in re.findall(r'Peak level dB: (-?[\d.]+)', o)]
        M = [float(x) for x in re.findall(r' M:\s*(-?[\d.]+)', o)]
        I = re.findall(r'I:\s+(-?[\d.]+) LUFS', o)
        if not pk: die(f'量不出音量：{path}')
        _lv[key] = (max(pk), max(M) if M else -70.0, float(I[-1]) if I else -70.0)
    return _lv[key]


def down(x):            # 向下保留 0.1 dB，保证不超过目标
    return math.floor(x * 10) / 10


def voice_gain(job: Job):
    """人声增益：job 写数字就用数字；否则按 合成/A肉.mov 的口播峰值调到 voice_peak_db。返回 (增益, 调后整体响度)"""
    a = job.p('合成', 'A肉.mov')
    if not a.exists(): die('没有 合成/A肉.mov，先跑 aroll-render')
    pk, _, I = level(a)
    v = job.get('voice_db', 'auto')
    g = float(v) if isinstance(v, (int, float)) else down(float(job.get('voice_peak_db', -1.0)) - pk)
    return g, I + g


def onset(path, s0, n, fps):
    key = (path, s0, n)
    if key not in _cache:
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', f'{s0 / fps:.4f}', '-t', f'{n / fps:.4f}', '-i', path, '-f', 'f32le', '-ac', '2', '-ar', '48000', '-'])
        x = np.abs(np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)).max(1)      # 逐声道取峰，不合并抵消
        hop = 240
        env = np.array([x[i:i + hop].max() for i in range(0, len(x) - hop, hop)]) if len(x) > hop else np.array([x.max()])
        k = int(np.argmax(np.diff(np.concatenate([[0], env]))))
        _cache[key] = round(k * hop / 48000 * fps)
    return _cache[key]


def local_sound(job: Job, name):
    """项目里自己下载的音效（2026-11-12《水电阶段》：只认收藏表，新下的免费音效用不上）：写绝对路径，或相对片子目录/02_制作 的路径。"""
    p = Path(name).expanduser()
    if p.is_absolute(): return p if p.is_file() else None
    for base in [job.dir] + [q / '02_制作' for q in job.dir.parents if (q / '项目.json').is_file()]:
        if (base / p).is_file(): return (base / p).resolve()
    return None


def place(cue, k):
    """音效放到时间线第几帧：让选段里第 k 帧（起音）落在卡点帧 cue 上。片头放不下（要从 0 帧之前放）时从 0 帧放、
    去掉选段开头放不下的那几帧，起音仍对准。返回 (时间线帧, 去掉开头几帧)。"""
    rec = cue - k
    return (rec, 0) if rec >= 0 else (0, -rec)


def run(job: Job, allow_sounds=False):
    """allow_sounds：用户听过认可后放行音效规矩违规（用户否掉过的黑名单音效、音效和关键词差超过 0.15 秒），
    和花字音效的 fx.py --allow-sounds 同一个意思；放行的那几条在 音效表.json 里记 放行（check 只提醒不拦）。"""
    from .beats import per_minute
    from . import guard
    B = rj(job.p('分镜.json')); fps = float(job.fps)
    if not B.get('sfx'): die('分镜.json 里 0 个音效：分镜规格.json 的 sfx 没写，或重跑 beats 时被清空了（写进分镜规格的 sfx 或“音效脚本”后再 beats）')
    peak, at = per_minute([e['t'] for e in B['sfx']]); limit = int(job.get('sfx_per_minute', 8))
    minutes = job['frames'] / fps / 60; avg = len(B['sfx']) / minutes
    if avg > limit:
        die(f'音效太多：全片 {len(B["sfx"])} 个，平均每分钟 {avg:.1f} 个，上限 {limit} 个（2026-11-12 用户：“每句话都有就等于每句话没有”）。'
            '只留开场钩子、主题、章节转场、关键数字/价格、每章结论、画面重点；用户点名要更密时在 job.json 写 sfx_per_minute')
    # 2026-11-22：用户否掉过的音效（音效收藏表的 blacklist）选到就停，免得同一个音效被否第三次
    black = dict(guard.black_hits(job, [e['file'] for e in B['sfx']]))
    if black and not allow_sounds:
        die('这些音效用户否掉过（音效收藏表 sfx_favorites.json 的 blacklist）：' + '；'.join(f'{n}（{w}）' for n, w in black.items())
            + '。换成别的（叮、点击、错误音这类）；用户听过、这次确实要用，才加 --allow-sounds 照做')
    lib = Path(job['sfx_library'])
    favp = Path(job['sfx_favorites'])
    if not favp.exists(): die(f'找不到音效收藏表 {favp}（NAS 没挂载？）')
    fav = {f['filename']: f for f in rj(favp)['favorites']}
    vg, vI = voice_gain(job)
    peak_t, under = float(job.get('sfx_peak_db', -5.0)), float(job.get('sfx_under_voice_lu', 6.0))
    rows, miss, cut = [], [], []
    for e in B['sfx']:
        name = e['file']
        if name.startswith('synth:'):
            path = ASSETS / 'sfx' / f'synth_{name[6:]}.wav'; s0 = 0
            if not path.exists(): miss.append(name); continue
            n = int(duration(path) * fps)
        elif name in fav and (lib / name).exists():
            path = lib / name
            f = fav[name]; s0 = f.get('source_start_frame', 0); n = f['duration_frames']
            if name.startswith('电子触摸'): n = 30      # 收藏里的 14 帧选段几乎全是静音，实际声音从第 14 帧开始
        elif ('/' in name or name.startswith('~')) and local_sound(job, name):
            path = local_sound(job, name); s0 = int(e.get('src_start', 0)); n = int(e.get('frames') or max(1, int(duration(path) * fps)))
        else:
            miss.append(name); continue
        k = 0 if name.startswith('synth:') or e.get('role') == 'lead' else onset(str(path), s0, n, fps)
        g = e.get('gain_db')
        if g is None:
            pad = 2                                               # 前后多量 2 帧：mp3 在达芬奇里会差约 1 帧，选段外紧挨着的响声不能被放大
            pk, M, _ = level(path, max(0, s0 - pad) / fps, (n + 2 * pad) / fps)
            g = down(peak_t - pk)
            if n / fps > 1.05: g = min(g, down(vI - under - M))      # 持续声（明显长于 1 秒）不盖过人声
            g = min(g, 12.0)                                          # 增益封顶 +12 dB：选段很小时别把旁边的响声放大到爆音（2026-10-24 审计）
        rec, head = place(round(e['t'] * fps), k); head = min(head, n - 1)
        if head: cut.append(f"{os.path.basename(str(path))}（{e['t']:.2f} 秒）去掉开头 {head} 帧")
        rows.append({'file': str(path), 'src_start': s0 + head, 'frames': n - head, 'record': rec, 'sync_frame_in_sel': max(0, k - head), 'gain_db': g, 'at': e['t'],
                     **({'head_cut': head} if head else {}), **{x: e[x] for x in ('beat', 'why', 'event', 'shot', 'project') if e.get(x)}})
    if miss:
        die('这些音效找不到（不在收藏表，也不是项目里的文件）：' + '、'.join(sorted(set(miss))) + '；项目里下的音效写路径（如 素材_音效/xxx.wav，相对 02_制作）')
    # 大字、结论音效要卡在关键词（高亮的词）起点，不卡句首（2026-11-21 新12 报警；2026-11-22 音效这半升成硬拦，台账 Q168）：写音效表之前先查
    from .check import keyword_offsets, KEY_TOL
    off, whoosh = keyword_offsets(job, rows)
    late = [x for x in off if x[1].startswith('音效 ')]
    if late and not allow_sounds:
        die(f'这些音效和关键词起点差超过 {KEY_TOL} 秒（音效表没写）：' + '；'.join(f'{t} 秒 {what} 比「{k}」（{h} 秒）{"早" if dt < 0 else "晚"} {abs(dt)} 秒' for t, what, k, h, dt in late)
            + '。分镜规格里把卡点改写成关键词本身再 beats → sfx；用户听过认可这样卡，才加 --allow-sounds 照做')
    if allow_sounds:
        tag = f"用户听过认可（sfx --allow-sounds，{time.strftime('%F %T')}）"
        for r in rows:
            why = ([f'黑名单：{black[Path(r["file"]).name]}'] if Path(r['file']).name in black else []) + \
                  [f'比关键词「{k}」{"早" if dt < 0 else "晚"} {abs(dt)} 秒' for t, what, k, h, dt in late if t == round(r['at'], 2) and what.startswith('音效 ' + os.path.basename(r['file']))]
            if why: r['放行'] = tag + '：' + '；'.join(why)
        if any(r.get('放行') for r in rows): say('【放行】' + '；'.join(f"{r['at']:.2f} 秒 {os.path.basename(r['file'])}：{r['放行']}" for r in rows if r.get('放行')))
    wj(job.p('合成', '音效表.json'), rows)
    wj(job.p('合成', '音量.json'), {'voice_db': vg, 'voice_lufs': round(vI, 1), 'sfx_peak_db': peak_t, 'sfx_under_voice_lu': under})
    say(f'人声增益 {vg:+.1f} dB（调后整体响度 {vI:.1f} LUFS）→ 合成/音量.json')
    from .beats import PEAK_MINUTE
    say(f'音效平均每分钟 {avg:.1f} 个（上限 {limit}），最密的一分钟（{at:.0f} 秒起）{peak} 个'
        + (f'【注意：任意一分钟超过 {PEAK_MINUTE} 个，听一下 {at:.0f}–{at + 60:.0f} 秒是不是太密；不拦】' if peak > PEAK_MINUTE else ''))
    if cut: say('【注意】这些音效卡在片头，起音前那一小段放不下（时间线不能从 0 帧之前放）：从 0 帧放起、去掉选段开头，起音仍对准卡点：' + '；'.join(cut))
    say(f'{len(rows)} 个音效 → 合成/音效表.json；起音偏移', sorted({(os.path.basename(r["file"]), r['sync_frame_in_sel']) for r in rows}))
    for t, what, k, h, dt in off:
        if what.startswith('音效 '): continue          # 音效这半上面已经停下或放行
        say(f'【注意】{t} 秒 {what} 比关键词「{k}」（{h} 秒）{"早" if dt < 0 else "晚"} {abs(dt)} 秒（超过 {KEY_TOL} 秒）：卡点改写成关键词本身')
    for t, name, why in whoosh: say(f'【注意】{t} 秒 {name}：{why}')
