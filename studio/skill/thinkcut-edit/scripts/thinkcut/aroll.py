# 从 a-roll（NAS 上的 core）的工程读出：源视频、达芬奇工程/主线、字幕 SRT、每个字在成片上的时间；按画幅出 4K 口播画面
import time
from pathlib import Path
from .common import Job, die, rj, wj, run, say, probe


def detect(aroll):
    """a-roll 工程目录 → init 需要的信息（读 审核/latest_delivery.json 与 project_runtime.json）"""
    R = Path(aroll).expanduser().resolve() / '审核'
    if not (R / 'latest_delivery.json').exists():
        die(f'{R} 里没有 latest_delivery.json：a-roll 还没交付到达芬奇，先用 a-roll 剪完口播并交付')
    d, rt = rj(R / 'latest_delivery.json'), rj(R / 'project_runtime.json')
    if d.get('status') != 'complete':
        die('a-roll 最近一次交付没有完成（status 不是 complete）')
    srts = sorted(Path(d['output']).glob('*.srt'), key=lambda p: p.stat().st_mtime)
    if not srts:
        die(f"{d['output']} 里没有 .srt 字幕")
    return {'aroll': str(R.parent), 'source': rt['source'], 'source_size': rt.get('resolution'), 'project': d['project'],
            'main_timeline': d['timeline'], 'frames': int(d['frames']), 'srt': str(srts[-1]), 'fps': '%d/%d' % tuple(rt['fps']),
            # 口播是机器配音占位时（口播剪辑 doubao_tts.py 记在 project_runtime.json）带过来：分镜不排露脸、不放小圆圈、不用修音，审片台标“占位”
            **({'placeholder': rt['placeholder']} if isinstance(rt.get('placeholder'), dict) else {})}


def words(job: Job):
    """每个字在成片时间线上的起止秒 → 口播字时间.json（已用字幕纠错替换同音错字）"""
    R = Path(job['aroll']) / '审核'
    K = rj(R / 'trial_keep_segments.json'); S = K['segments']; keep = set(K['retained_token_ids'])
    st = rj(R / 'review_v2_state.json')
    # 跨几个字的纠错（ids 有多个，如“贡献→攻陷”）：字数对得上就逐字换，对不上就并成一个字、时间从第一个到最后一个（2026-11-01《别墅地下室防水》）
    fix, merge = {}, {}
    for v in st.get('subtitle_token_corrections', []):
        ids, after = v.get('ids') or [v['id']], v['after']
        if len(ids) > 1 and len(after) == len(ids):
            fix.update(zip(ids, after))
        else:
            fix[ids[0]] = after; merge[ids[0]] = ids[1:]
    drop = {i for rest in merge.values() for i in rest}
    al = sorted((t for t in rj(R / 'character_alignment.json')['tokens'] if t['id'] in keep and t['end_ms'] > t['start_ms']), key=lambda t: t['start_ms'])
    end = {t['id']: t['end_ms'] for t in al}
    fps = float(job.fps)

    def tl(ms):
        f = ms * fps / 1000
        for s in S:
            if s['src_in'] <= f <= s['src_out']:
                return round((s['tl_in'] + f - s['src_in']) / fps, 3)
        nxt = [s for s in S if s['src_in'] > f]
        if not nxt:                                                   # 句尾的字超出最后一段：算在最后一段结尾
            s = max(S, key=lambda s: s['src_in']); return round((s['tl_in'] + s['src_out'] - s['src_in']) / fps, 3)
        return round(min(nxt, key=lambda s: s['src_in'])['tl_in'] / fps, 3)
    rows = [{'u': t['unit_id'], 'c': fix.get(t['id'], t['text']), 't0': tl(t['start_ms']),
             't1': tl(max([t['end_ms']] + [end[i] for i in merge.get(t['id'], []) if i in end]))} for t in al if t['id'] not in drop]
    if st.get('resolve_frame_cuts'):
        say('提醒：a-roll 里有达芬奇精修刀口（resolve_frame_cuts），字时间按 trial_keep_segments 算；若主线和它不一致，先用 snap/diff 核对')
    wj(job.p('口播字时间.json'), {'note': '每个字在成片时间线上的起止秒（thinkcut-edit words 生成）', 'fps': job['fps'], 'tokens': rows}, indent=None)
    say(f'{len(rows)} 个字，{rows[0]["t0"]}–{rows[-1]["t1"]} 秒 → 口播字时间.json')


def crop_for(sw, sh, W, H):
    """居中裁成目标比例（和达芬奇时间线的“缩放裁切”一致）"""
    if sw * H > sh * W:
        cw = round(sh * W / H / 2) * 2; return cw, sh, (sw - cw) // 2, 0
    ch = round(sw * H / W / 2) * 2; return sw, ch, 0, (sh - ch) // 2


def shift_of(cfg):
    """job.json 的 "人像下移"：审核线口播画面（V1）整体往下挪多少（4K 像素，正数往下；达芬奇里是 Tilt = 负这个数）。不写或 0＝不挪。"""
    v = cfg.get('人像下移') or 0
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v): die(f'job.json 的 "人像下移" 要写整数像素（4K），现在是 {v!r}')
    return int(v)


def shifted_name(dy):
    """取景下移的口播画面文件名（人挡字用它，和审核线同取景）；值写进文件名，改了值就是另一个文件。"""
    return f'A肉_人像下移{dy}.mov'


BATCH = 'A肉_批次.json'          # 每遍 aroll-render 出了哪些文件（人挡字 rendang.same_batch 读，同名）


def shift_crop(cw, ch, cx, cy, sh, dy_src):
    """裁切框往上挪 dy_src（源像素）＝画面里人往下挪；挪出源画面的部分补黑（和达芬奇里 Tilt 露出的黑边一样）。返回滤镜串。"""
    y0 = cy - dy_src; top = max(0, -y0); bot = max(0, y0 + ch - sh); h = ch - top - bot
    if h <= 0: die(f'"人像下移" 太大：整幅都挪出了口播画面')
    return f'crop={cw}:{h}:{cx}:{max(0, y0)}' + (f',pad={cw}:{ch}:0:{top}:black' if top or bot else '')


def render(job: Job):
    """按 a-roll 保留段拼出口播，裁成画幅、放大到 4K → 合成/A肉.mov（做小圆圈和检查图用，不进成片）。
    job.json 写了 "人像下移"（审核线里人像整体往下挪，2026-11-21 新10）时，同一遍再出一份 合成/A肉_人像下移<值>.mov，人挡字用它，和审核线同取景。"""
    R = Path(job['aroll']) / '审核'; S = rj(R / 'trial_keep_segments.json')['segments']
    src = job['source']; s = probe(src); sw, sh = int(s['width']), int(s['height'])
    cw, ch, cx, cy = crop_for(sw, sh, job.W, job.H); fps = float(job.fps); dy = shift_of(job.cfg)
    v = [f"[0:v]trim=start_frame={x['src_in']}:end_frame={x['src_out']},setpts=PTS-STARTPTS[v{i}]" for i, x in enumerate(S)]
    a = [f"[0:a]atrim=start={x['src_in'] / fps:.6f}:end={x['src_out'] / fps:.6f},asetpts=PTS-STARTPTS[a{i}]" for i, x in enumerate(S)]
    tail = f'scale={job.W}:{job.H}:flags=lanczos,setsar=1'
    if dy:
        g2 = f'[vc]split=2[c0][c1];[c0]crop={cw}:{ch}:{cx}:{cy},{tail}[vo];[c1]{shift_crop(cw, ch, cx, cy, sh, round(dy * ch / job.H))},{tail}[vs]'
    else:
        g2 = f'[vc]crop={cw}:{ch}:{cx}:{cy},{tail}[vo]'
    g = ';\n'.join(v + a) + ';\n' + ''.join(f'[v{i}][a{i}]' for i in range(len(S))) + f'concat=n={len(S)}:v=1:a=1[vc][ac];' + g2
    gf = job.p('合成', 'A肉_拼接.txt'); gf.write_text(g, encoding='utf-8'); out = job.p('合成', 'A肉.mov')
    enc = ['-c:v', 'libx264', '-preset', 'medium', '-crf', '15', '-pix_fmt', 'yuv420p', '-r', job['fps']]
    extra = ['-map', '[vs]'] + enc + ['-an', job.p('合成', shifted_name(dy))] if dy else []      # 只要画面（人挡字的声音用口播原声）
    enc = enc + ['-c:a', 'pcm_s16le', '-ar', '48000']
    say(f'出口播画面 {job.W}×{job.H}（{len(S)} 段）' + (f'，另出人像下移 {dy} 像素的一份（人挡字用）' if dy else '') + '…')
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', src, '-/filter_complex', gf, '-map', '[vo]', '-map', '[ac]'] + enc + [out] + extra)
    # 2026-11-21 261118 新8：记下这一遍出的文件（大小、修改时间）。同一遍的 A肉 和 A肉_人像下移 写完有先后，人挡字按这个认同一批，不按修改时间判谁旧
    files = [Path(out)] + ([Path(job.p('合成', shifted_name(dy)))] if dy else [])
    wj(job.p('合成', BATCH), dict(批次=time.strftime('%Y-%m-%d %H:%M:%S'), 文件={p.name: [p.stat().st_size, int(p.stat().st_mtime)] for p in files}))
    say('→', out, *([job.p('合成', shifted_name(dy))] if dy else []))
