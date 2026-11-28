# -*- coding: utf-8 -*-
# 口播降噪修音（2026-11-21 新13，照《别墅装灯》02_制作/自动剪辑/人声/polish.sh；用户：“降一下噪……只保留人声……把它修得更好听”）：
# ① 达芬奇人声隔离：审核线“音频 1”的口播片段开 AudioVoiceIsolationEnabled、Amount=70，别的音频轨先关，只渲声音 → 合成/人声/人声隔离.wav
#    渲完把片段设置、轨道开关、渲染预设都恢复。
# ② ffmpeg：去低频、均衡、去齿音、轻压缩，响度定到 -12 LUFS、限幅 -1.5 dB → 合成/人声/口播_隔离修音_<时间>.wav；量响度和底噪（要 ≤ -55 dB）。
# ③ 审核线上加“口播（隔离修音）”音频轨放它，关掉“音频 1”（SetTrackEnable 对音频轨有效）；job.json 记 voice_polish：
#    以后 review 重建审核线自动放这条、关 A1，export 的音量平衡和响度都按这条算（不用再 --no-balance）。
import subprocess, time
from pathlib import Path
import numpy as np
from .common import Job, die, run, say

POLISH = ('highpass=f=80,equalizer=f=280:t=q:w=1.2:g=-2,equalizer=f=3600:t=q:w=1.4:g=2,highshelf=f=9000:g=1.5,'
          'deesser=i=0.35:m=0.5:f=0.5,acompressor=threshold=-14dB:ratio=1.6:attack=12:release=140:makeup=1')
TARGET_LUFS = -12.0       # 和本片定稿一致
FLOOR_MAX = -55.0         # 底噪验收线（反馈单新13）
AMOUNT = 70               # 达芬奇人声隔离强度（本片用的）
SEG = 10.0                # 分段响度：每 10 秒一段（2026-11-27 261114 反馈单 新2）
SEG_TOL = 1.5             # 一段和全片中位数差超过 1.5 LU 就在修音轨上补
SEG_MAX = 6.0             # 一段最多补 ±6 dB（再大多半是没说话、或是故意的大小声，交给人听）
RAMP = 0.5                # 补的增益在段边上用 0.5 秒渐变过去，听不出台阶
LOUD_GAIN = 10.0          # 修音整体加了这么多 dB 以上，底噪多半是原片收音被一起抬上来的


def noise_floor(f, win=0.05, seg=30.0):
    """底噪 dBFS：每 50 毫秒算一次均方根电平，每 30 秒取最低 5%（说话停顿处），报最差的那段（本片“从某部分开始有底噪”）。"""
    raw = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(f), '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'], capture_output=True).stdout
    y = np.frombuffer(raw, np.float32).astype(float); n = int(48000 * win)
    if len(y) < n * 20: die(f'量不了底噪（声音太短或读不出来）：{f}')
    db = 20 * np.log10(np.sqrt((y[:len(y) // n * n].reshape(-1, n) ** 2).mean(1)) + 1e-9)
    k = int(seg / win); parts = [db[i:i + k] for i in range(0, len(db), k)]
    parts = [x for x in parts if len(x) >= k // 3] or [db]             # 结尾不到 10 秒的零头不单算
    return round(max(float(np.percentile(x, 5)) for x in parts), 1)


def momentary(f):
    """每 0.1 秒一个瞬时响度（400 毫秒窗）：[(秒, LUFS)]。"""
    import re
    o = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(f), '-vn', '-af', 'ebur128=framelog=info', '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    return [(float(t), float(m)) for t, m in re.findall(r't:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+)', o)]


def _gated(values):
    """按 BS.1770 门限（-70 绝对、-10 相对）把一串 400 毫秒块合成一个响度；没有有效块返回 None。"""
    import math
    blocks = [v for v in values if v > -70]
    if not blocks: return None
    power = lambda xs: 10 * math.log10(sum(10 ** (x / 10) for x in xs) / len(xs))
    kept = [v for v in blocks if v > power(blocks) - 10]
    return power(kept) if kept else None


def segments(points, size=SEG, min_speech=0.3):
    """每 size 秒一段的响度 [(起, 止, LUFS 或 None)]：说话太少（有声块不到 30%）或不满一段的结尾记 None，不参与比较、不补。"""
    if not points: return []
    end = points[-1][0]; rows = []
    for k in range(int(end // size) + 1):
        a, b = k * size, min((k + 1) * size, end)
        vals = [m for t, m in points if a < t <= b]
        full = b - a >= size - 0.2
        ok = full and vals and sum(1 for m in vals if m > -50) >= min_speech * len(vals)
        rows.append((a, b, _gated(vals) if ok else None))
    return rows


def uneven(rows, tol=SEG_TOL):
    """和全片中位数差超过 tol 的段：[(起, 止, 差多少)]（正＝比中位数大）。"""
    vals = sorted(v for *_, v in rows if v is not None)
    if len(vals) < 3: return []
    mid = vals[len(vals) // 2]
    return [(a, b, round(v - mid, 1)) for a, b, v in rows if v is not None and abs(v - mid) > tol]


def gain_curve(rows, tol=SEG_TOL, cap=SEG_MAX, ramp=RAMP):
    """偏大偏小的段补回中位数：每段一个增益（dB），段内平的、段和段之间 2×ramp 秒线性过渡。返回 [(秒, dB)] 拐点；不用补返回 []。"""
    fix = {a: max(-cap, min(cap, -d)) for a, b, d in uneven(rows, tol)}
    if not fix: return []
    pts = []
    for a, b, _ in rows:
        g = fix.get(a, 0.0)
        pts += [(a + ramp if a else 0.0, g), (b - ramp, g)]
    return pts


def curve_filter(pts):
    """[(秒, dB)] 拐点 → ffmpeg volume 滤镜（逐段线性插值；拐点外按两头的值）。"""
    terms = []
    for (t0, g0), (t1, g1) in zip(pts, pts[1:]):
        if t1 <= t0 or (g0 == 0 and g1 == 0): continue
        terms.append(f'between(t,{t0:.3f},{t1:.3f})*({g0:.2f}+({g1 - g0:.2f})*(t-{t0:.3f})/{t1 - t0:.3f})')
    if pts[-1][1]: terms.append(f'gt(t,{pts[-1][0]:.3f})*{pts[-1][1]:.2f}')
    return "volume='pow(10,(" + ('+'.join(terms) or '0') + ")/20)':eval=frame"


def polish(src, dst, measure=None, notes=None):
    """ffmpeg 修音（均衡、去齿音、轻压缩）→ 分段响度补齐（2026-11-27 新2：10 秒段和中位数差 >1.5 LU 的补回去）→
    响度定到 -12 LUFS、限幅 -1.5 dB。返回 (加了多少 dB, 响度, 真峰值, 底噪)；notes（列表）里追加补了哪些段、补完还差的段。"""
    from .resolve import loudness
    measure = measure or loudness; dst = Path(dst); tmp = dst.with_name(dst.stem + '_未定响度.wav')
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(src), '-vn', '-af', POLISH, '-c:a', 'pcm_s24le', str(tmp)])
    rows = segments(momentary(tmp)); pts = gain_curve(rows)
    if pts:
        even = dst.with_name(dst.stem + '_补段.wav')
        run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(tmp), '-af', curve_filter(pts), '-c:a', 'pcm_s24le', str(even)])
        tmp.unlink(); even.rename(tmp)
        if notes is not None: notes.append('分段补齐：' + '，'.join(f'{a:.0f}–{b:.0f} 秒 {-d:+.1f} dB' for a, b, d in uneven(rows) if abs(d) <= SEG_MAX * 2))
    m = measure(tmp) or die(f'修音后量不出响度：{tmp}')
    g = round(TARGET_LUFS - m[0], 2)
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(tmp), '-af', f'volume={g}dB,alimiter=limit=-1.5dB:level=false:attack=3:release=60',
         '-ar', '48000', '-c:a', 'pcm_s24le', str(dst)])
    tmp.unlink()
    m = measure(dst) or die(f'修音后量不出响度：{dst}')
    if notes is not None:
        left = uneven(segments(momentary(dst)))
        if left: notes.append('补完还和中位数差 >1.5 LU 的段（请听）：' + '，'.join(f'{a:.0f}–{b:.0f} 秒 {d:+.1f}' for a, b, d in left))
    return g, m[0], m[1], noise_floor(dst)


def floor_hint(gain, amount):
    """底噪没过时给的下一步：修音整体加了很多增益（原片收音小、底噪跟着抬上来）时，调大人声隔离没用（2026-11-27 261114：70、80 都是 -54 dB 上下）。"""
    if gain is not None and gain >= LOUD_GAIN:
        return (f'底噪是修音整体加了 {gain:+.1f} dB 把原片收音底噪一起抬上来的，调大人声隔离强度基本没用：'
                '请用户在达芬奇里听停顿处的底噪，能接受就照常往下做（导出前检查会再提醒）；不能接受，下次录音离麦近一点、录音电平高一点')
    return f'人声隔离调大重跑（--amount {max(80, (amount or AMOUNT) + 10)}），或请用户听一下'


def _wait(p, jid, limit=3600):
    from .resolve import wait_render
    wait_render(p, jid, limit, '渲纯口播', 2)       # 等超时或出错会先叫停达芬奇渲染（2026-11-21 体检）
    stt = p.GetRenderJobStatus(jid); p.DeleteRenderJob(jid)
    if stt.get('JobStatus') != 'Complete': die(f'渲纯口播没完成：{stt}')


def set_audio_format(p):
    """渲纯口播用的格式：先 wav，不行用 mov（只出声音）。返回用上的格式。
    达芬奇 21.1：GetRenderFormats 里有 Wave/wav，但 GetRenderCodecs('wav') 是空的（音频格式没有编码器列表），
    以前拿空列表直接停“设渲染格式 wav 失败”（2026-11-21 261118 实测）。现在 wav 依次试 列表里的编码器、空编码器、lpcm，都不行再换 mov。"""
    fmts = set((p.GetRenderFormats() or {}).values())
    tries = []
    if 'wav' in fmts: tries += [('wav', c) for c in list((p.GetRenderCodecs('wav') or {}).values()) + ['', 'lpcm']]
    if 'mov' in fmts:
        mc = list((p.GetRenderCodecs('mov') or {}).values())
        tries += [('mov', c) for c in (['H264'] if 'H264' in mc else []) + mc[:1]]
    for f, c in tries:
        if p.SetCurrentRenderFormatAndCodec(f, c): return f
    die('设渲染格式失败：试过 ' + '、'.join(f"{f}/{c or '空'}" for f, c in tries) + '，都设不上')


def isolate(r, pm, p, tl, out_dir, amount=AMOUNT):
    """只渲“音频 1”开了人声隔离的口播。渲完片段的隔离设置、各音频轨开关、渲染预设都恢复。返回渲出的文件。"""
    from .resolve import fresh_render
    items = tl.GetItemListInTrack('audio', 1) or die('审核线的“音频 1”上没有口播片段')
    keep = [(it, it.GetProperty('AudioVoiceIsolationEnabled'), it.GetProperty('AudioVoiceIsolationAmount')) for it in items]
    tracks = {j: tl.GetIsTrackEnabled('audio', j) for j in range(1, tl.GetTrackCount('audio') + 1)}
    name = f"人声隔离_渲染_{time.strftime('%H%M%S')}"
    try:
        for it in items:
            if not (it.SetProperty('AudioVoiceIsolationEnabled', True) and it.SetProperty('AudioVoiceIsolationAmount', amount)):
                die('给口播片段开人声隔离失败（这个达芬奇版本没有“人声隔离”？要 Studio 版 18.5 以上）')
        for j in tracks: tl.SetTrackEnable('audio', j, j == 1)
        fresh_render(p); fmt = set_audio_format(p)
        p.SetCurrentRenderMode(1)
        sets = {'TargetDir': str(out_dir), 'CustomName': name, 'ExportVideo': False, 'ExportAudio': True, 'SelectAllFrames': True}
        if fmt == 'mov': sets['AudioCodec'] = 'lpcm'                                   # 只出声音的 mov：无损 PCM（设不上就用预设的音频编码，后面 ffmpeg 照样读）
        for k, v in sets.items():
            if not p.SetRenderSettings({k: v}) and k != 'AudioCodec': die(f'设渲染参数 {k}={v} 失败')
        jid = p.AddRenderJob() or die('加入渲染队列失败')
        say('达芬奇人声隔离渲纯口播…'); _wait(p, jid)
    finally:          # 不管成没成，审核线恢复原样（片段设置、轨道开关），渲染设置载回预设（新18：不然下一次渲染只出声音）
        # 2026-11-22 试验田查出：渲完达芬奇停在“交付”页，在那一页恢复轨道开关不生效，修音做完审核线上所有音轨都是关的、导出没有声音。
        # 先回剪辑页、切回这条时间线再恢复，恢复完读回核对，对不上就停下说清楚（finally 里不 die，记下来出了 finally 再停）。
        r.OpenPage('edit'); p.SetCurrentTimeline(tl)
        for it, en, am in keep:
            if en is not None: it.SetProperty('AudioVoiceIsolationEnabled', en)
            if am is not None: it.SetProperty('AudioVoiceIsolationAmount', am)
        for j, en in tracks.items(): tl.SetTrackEnable('audio', j, en)
        wrong = [j for j, en in tracks.items() if bool(tl.GetIsTrackEnabled('audio', j)) != bool(en)]
        fresh_render(p); pm.SaveProject(); r.OpenPage('edit')
    if wrong:
        die('渲完纯口播后，审核线的音轨开关没恢复成原样（' + '、'.join(f'「{tl.GetTrackName("audio", j)}」应{"开" if tracks[j] else "关"}' for j in wrong)
            + '）：去达芬奇剪辑页把这几条音轨的开关点回去再导出，不然成片会少声音')
    got = [f for f in Path(out_dir).glob(name + '.*')]
    if len(got) != 1: die(f'渲完找不到纯口播文件（{out_dir}/{name}.*）')
    return got[0]


def put_track(job: Job, pm, p, tl, name, f, label):
    """审核线上放修音口播轨（已有同名轨就清空重放），关掉“音频 1”；保存→关闭→重开→读回核对。"""
    from . import resolve as R
    mp = p.GetMediaPool(); root = mp.GetRootFolder()
    fo = next((x for x in root.GetSubFolderList() if x.GetName() == label), None) or mp.AddSubFolder(root, label)
    mp.SetCurrentFolder(fo); got = mp.ImportMedia([str(f)]) or []; mp.SetCurrentFolder(root)
    if len(got) != 1: die(f'导入修音口播失败：{f}')
    j = R.track_index(tl, 'audio', label)
    if j:
        old = tl.GetItemListInTrack('audio', j) or []
        if old and not tl.DeleteClips(old): die(f'清空旧的「{label}」轨失败')
    else:
        if not tl.AddTrack('audio', 'stereo'): die('加音频轨失败')
        j = tl.GetTrackCount('audio'); tl.SetTrackName('audio', j, label)
    st = tl.GetStartFrame()
    if not mp.AppendToTimeline([{'mediaPoolItem': got[0], 'startFrame': 0, 'endFrame': int(job['frames']), 'trackIndex': j, 'recordFrame': st, 'mediaType': 2}]):
        die('放修音口播失败')
    if not tl.SetTrackEnable('audio', 1, False): die('关掉“音频 1”（原口播）失败')
    if not tl.GetIsTrackEnabled('audio', j) and not tl.SetTrackEnable('audio', j, True): die(f'打开「{label}」轨失败')
    p, tl = R.reopen(pm, p, name)
    n = len(tl.GetItemListInTrack('audio', j) or [])
    if n != 1 or tl.GetIsTrackEnabled('audio', 1): die(f'重开读回不对：「{label}」轨 {n} 段（应 1 段），音频 1 {"还开着" if tl.GetIsTrackEnabled("audio", 1) else "已关"}')
    # 2026-11-22：修音轨本身必须是开的（以前只查了音频 1 关没关；修音轨关着导出就没有人声）
    if not tl.GetIsTrackEnabled('audio', j): die(f'重开读回不对：「{label}」轨是关的，导出会没有人声：去达芬奇剪辑页打开这条轨')
    return j


def run_cmd(job: Job, name=None, amount=AMOUNT):
    """voice-polish 命令：在审核线上做口播降噪修音（步骤见文件头）。"""
    from . import resolve as R
    name = name or job.get('review_timeline') or die('还没有审核线：先 review 建审核线，再做口播降噪修音（或 --name 指定时间线）')
    if name == job.get('main_timeline'): die(f'「{name}」是主线，主线不动：在审核线上做')
    d = job.p('合成', '人声', 'x').parent; N = int(job['frames'])
    r, pm, p = R.project(job); tl = R.timeline(p, name); p.SetCurrentTimeline(tl)
    if tl.GetEndFrame() - tl.GetStartFrame() != N:
        die(f'审核线「{name}」现在 {tl.GetEndFrame() - tl.GetStartFrame()} 帧，项目记录 {N} 帧（审核线上剪过）：修音口播要和成片等长，先弄清长度再做')
    raw = isolate(r, pm, p, tl, d, amount)
    iso = d / '人声隔离.wav'
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(raw), '-vn', '-ar', '48000', '-c:a', 'pcm_s24le', str(iso)]); raw.unlink()
    before = noise_floor(iso)
    out = d / f"口播_隔离修音_{time.strftime('%m%d-%H%M%S')}.wav"      # 每次新文件名：达芬奇媒体池不会拿旧内容顶替
    notes = []; g, lufs, pk, fl = polish(iso, out, notes=notes)
    put_track(job, pm, p, tl, name, out, R.POLISH_TRACK)
    job.cfg['voice_polish'] = {'file': str(out), 'track': R.POLISH_TRACK, 'amount': amount, 'lufs': lufs, 'peak': pk, 'floor_db': fl, 'gain_db': g,
                               'at': time.strftime('%F %T'), 'timeline': name}
    job.save()
    say(f'口播降噪修音完成：人声隔离 {amount} → 修音（加 {g:+.1f} dB）→ {out.name}；响度 {lufs} LUFS，真峰值 {pk} dBFS，'
        f'底噪 {before} → {fl} dB（要 ≤ {FLOOR_MAX:g}）。审核线「{name}」已加「{R.POLISH_TRACK}」轨、关掉音频 1；以后重建审核线自动这样放，导出平衡按它算')
    for n in notes: say(n)
    if fl > FLOOR_MAX: say(f'【注意】底噪 {fl} dB 没到 {FLOOR_MAX:g} dB 以下：' + floor_hint(g, amount))
    say('请用户在达芬奇里听一下：开关“音频 1”和修音轨对比，听感不满意就从 job.json 删掉 voice_polish、打开音频 1 改回原口播')
    return out


def aroll_job(aroll):
    """剪口播工程 → 只给修音用的小项目 <剪口播工程>/修音/job.json（2026-11-27 261114 新1：以前要等动画项目的 job.json，
    口播重剪以后对不上就修不了）。主线换了（重新保存到达芬奇）就跟着换，旧的修音记录不带。"""
    from .aroll import detect
    from .common import rj, wj
    info = detect(aroll); d = Path(info['aroll']) / '修音'; f = d / 'job.json'
    old = rj(f) if f.is_file() else {}
    review = info['main_timeline'] + '_修音'
    cfg = {'说明': '只给修音用（thinkcut_edit.py aroll-polish 自动写）：剪口播主线的副本上整条修音，主线不动；动画项目另在 02_制作/自动剪辑',
           'aroll': info['aroll'], 'source': info['source'], 'project': info['project'], 'main_timeline': info['main_timeline'],
           'review_timeline': review, 'frames': info['frames'], 'fps': info['fps'], 'format': old.get('format') or '3:4'}
    if old.get('main_timeline') == info['main_timeline'] and old.get('voice_polish'): cfg['voice_polish'] = old['voice_polish']
    d.mkdir(parents=True, exist_ok=True); wj(f, cfg)
    return Job(d)


def aroll_cmd(aroll, amount=AMOUNT):
    """aroll-polish 命令：剪口播交付的主线复制一份「<主线>_修音」（已有就用它），在副本上做 voice-polish。"""
    from . import resolve as R
    job = aroll_job(aroll); name = job['review_timeline']
    r, pm, p = R.project(job)
    if name not in R.timelines(p):
        main = R.timeline(p, job['main_timeline']); p.SetCurrentTimeline(main)
        if not main.DuplicateTimeline(name): die(f'复制主线「{job["main_timeline"]}」失败')
        pm.SaveProject(); say(f'已复制主线 → 「{name}」（在它上面修音，主线不动）')
    out = run_cmd(job, name, amount)
    say(f'修音记录在 {job.dir / "job.json"}；以后 init 动画项目时口播用的是主线，审核线建好再跑一次 voice-polish（或把这条修音轨拷过去）')
    return out

