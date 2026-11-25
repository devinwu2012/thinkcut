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


def noise_floor(f, win=0.05, seg=30.0):
    """底噪 dBFS：每 50 毫秒算一次均方根电平，每 30 秒取最低 5%（说话停顿处），报最差的那段（本片“从某部分开始有底噪”）。"""
    raw = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(f), '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'], capture_output=True).stdout
    y = np.frombuffer(raw, np.float32).astype(float); n = int(48000 * win)
    if len(y) < n * 20: die(f'量不了底噪（声音太短或读不出来）：{f}')
    db = 20 * np.log10(np.sqrt((y[:len(y) // n * n].reshape(-1, n) ** 2).mean(1)) + 1e-9)
    k = int(seg / win); parts = [db[i:i + k] for i in range(0, len(db), k)]
    parts = [x for x in parts if len(x) >= k // 3] or [db]             # 结尾不到 10 秒的零头不单算
    return round(max(float(np.percentile(x, 5)) for x in parts), 1)


def polish(src, dst, measure=None):
    """ffmpeg 修音（均衡、去齿音、轻压缩），响度定到 -12 LUFS、限幅 -1.5 dB。返回 (加了多少 dB, 响度, 真峰值, 底噪)。"""
    from .resolve import loudness
    measure = measure or loudness; dst = Path(dst); tmp = dst.with_name(dst.stem + '_未定响度.wav')
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(src), '-vn', '-af', POLISH, '-c:a', 'pcm_s24le', str(tmp)])
    m = measure(tmp) or die(f'修音后量不出响度：{tmp}')
    g = round(TARGET_LUFS - m[0], 2)
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(tmp), '-af', f'volume={g}dB,alimiter=limit=-1.5dB:level=false:attack=3:release=60',
         '-ar', '48000', '-c:a', 'pcm_s24le', str(dst)])
    tmp.unlink()
    m = measure(dst) or die(f'修音后量不出响度：{dst}')
    return g, m[0], m[1], noise_floor(dst)


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
    g, lufs, pk, fl = polish(iso, out)
    put_track(job, pm, p, tl, name, out, R.POLISH_TRACK)
    job.cfg['voice_polish'] = {'file': str(out), 'track': R.POLISH_TRACK, 'amount': amount, 'lufs': lufs, 'peak': pk, 'floor_db': fl,
                               'at': time.strftime('%F %T'), 'timeline': name}
    job.save()
    say(f'口播降噪修音完成：人声隔离 {amount} → 修音（加 {g:+.1f} dB）→ {out.name}；响度 {lufs} LUFS，真峰值 {pk} dBFS，'
        f'底噪 {before} → {fl} dB（要 ≤ {FLOOR_MAX:g}）。审核线「{name}」已加「{R.POLISH_TRACK}」轨、关掉音频 1；以后重建审核线自动这样放，导出平衡按它算')
    if fl > FLOOR_MAX: say(f'【注意】底噪 {fl} dB 没到 {FLOOR_MAX:g} dB 以下：人声隔离调大重跑（--amount 80），或请用户听一下')
    say('请用户在达芬奇里听一下：开关“音频 1”和修音轨对比，听感不满意就从 job.json 删掉 voice_polish、打开音频 1 改回原口播')
    return out
