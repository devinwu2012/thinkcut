#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口播原片 → 1.2 倍速＋人声处理（整条重压视频）。

2026-11-27 起**不再是默认步骤**：用户“不需要重新压一遍视频……直接可以用达芬奇里1.2倍速”。新项目直接 edit_project.py prepare --video <原片>，
它只把声音做成 1.2 倍速、放进达芬奇时设 120%，人声剪完在达芬奇里修（voice-polish）。这个程序只留给：老项目要重做加速版、
低于 29.97 帧的原片要先转成 29.97（--speed 1，转好后 prepare 加 --speed 1）。

2026-11-01《别墅地下室防水》用户定：口播先 1.2 倍速再剪；人声要大（“再提升 30%”，约 -12.5 LUFS）。
做法：画面 setpts、声音先补齐到从 0 开始再 atempo；人声 高通 80 Hz → 轻降噪 → 压缩 → 整体固定增益到目标 → 限幅（-1.5 dBFS）。
2026-11-12《水电阶段》：原来第二遍用 loudnorm，原片太小（约 -33 LUFS）要拉 +20 dB 时真峰超限，loudnorm 会悄悄退回
动态模式，成片开头 30 秒小约 10 dB。现在改成固定增益＋限幅，做完每 10 秒测一次响度，有一段和全片中位数差超过 2 LU 就报错、不出片。
iPhone HLG/PQ（HDR）原片：自动用苹果系统取几帧显示画面，和 ffmpeg 直读的画面逐像素比，算出每个颜色通道的 1D 曲线，
转码时套上，颜色和在 Mac 上看原片一致（不再发灰）；曲线存在输出旁 <输出名>_苹果显示曲线.cube。
输出恒定 29.97 fps、H.264＋PCM，写在项目 02_制作/口播转码/，原片不动。

用法：python3 bin/speed_voice.py <原片> [--speed 1.2] [--lufs -12.5] [--out 输出.mov]
不要倍速时 --speed 1（只做人声处理和 29.97 转码）。"""
import argparse, json, math, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

VOICE = 'highpass=f=80,afftdn=nf=-25,acompressor=threshold=-20dB:ratio=3:attack=5:release=100:makeup=2'
LIMIT_DB = -1.5           # 限幅器上限（采样峰值）
WINDOW = 10.0             # 分段测响度的段长（秒）
MAX_SPREAD = 2.0          # 每段和全片中位数的响度差上限（LU）
HDR_TRANSFERS = {'arib-std-b67': 'HLG', 'smpte2084': 'PQ'}
APPLE_FRAMES = Path(__file__).with_name('apple_frames.swift')


def ffmpeg(*args):
    r = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', *map(str, args)], capture_output=True, text=True)
    if r.returncode != 0: sys.exit('ffmpeg 失败：' + r.stderr[-800:])
    return r.stderr


def loudness(path):
    o = ffmpeg('-i', path, '-vn', '-af', 'ebur128=peak=true', '-f', 'null', '-')
    return float(re.findall(r'I:\s+(-?[\d.]+) LUFS', o)[-1]), float(re.findall(r'Peak:\s+(-?[\d.]+) dBFS', o)[-1])


def momentary(path, chain=None):
    """每 0.1 秒一个瞬时响度（400 毫秒窗），[(秒, LUFS)]。"""
    af = (chain + ',' if chain else '') + 'ebur128=framelog=info'
    o = ffmpeg('-i', path, '-vn', '-map', '0:a:0', '-af', af, '-f', 'null', '-')
    return [(float(t), float(m)) for t, m in re.findall(r't:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+)', o)]


def gated(values):
    """按 BS.1770 的门限（-70 绝对、-10 相对）把一串 400 毫秒块合成一个积分响度；没有有效块返回 None。"""
    blocks = [v for v in values if v > -70]
    if not blocks: return None
    power = lambda xs: 10 * math.log10(sum(10 ** (x / 10) for x in xs) / len(xs))
    relative = power(blocks) - 10
    kept = [v for v in blocks if v > relative]
    return power(kept) if kept else None


def windows(points, size=WINDOW, min_speech=0.3):
    """每 size 秒一段的响度 [(起始秒, LUFS)]；说话太少（有声块不到 30%）或不满一段的结尾记 None，不参与比较。"""
    if not points: return []
    end = points[-1][0]
    result = []
    for k in range(int(end // size) + 1):
        values = [m for t, m in points if k * size < t <= (k + 1) * size]
        speech = sum(1 for m in values if m > -50)
        full = (k + 1) * size <= end + 0.2
        result.append((k * size, gated(values) if full and values and speech >= min_speech * len(values) else None))
    return result


def spread_problem(rows, limit=MAX_SPREAD):
    """有一段和全片中位数差超过 limit 时返回中文说明，否则 None（动态响度退回时开头会差 10 LU 左右）。"""
    measured = sorted(v for _, v in rows if v is not None)
    if len(measured) < 3: return None
    middle = measured[len(measured) // 2]
    worst = max(((s, v) for s, v in rows if v is not None), key=lambda r: abs(r[1] - middle))
    if abs(worst[1] - middle) <= limit: return None
    return (f'{worst[0]:.0f}–{worst[0] + WINDOW:.0f} 秒响度 {worst[1]:.1f} LUFS，和全片中位数 {middle:.1f} 差 '
            f'{abs(worst[1] - middle):.1f} LU（超过 {limit:g}）')


def window_report(rows):
    measured = [v for _, v in rows if v is not None]
    if not measured: return '分段响度：没有测到说话'
    return f'分段响度（每 {WINDOW:g} 秒 {len(measured)} 段）：{min(measured):.1f} ～ {max(measured):.1f} LUFS'


def voice_track(src, pre, target, wav):
    """固定增益＋限幅做人声 wav；返回 (增益 dB, 实测积分响度)。限幅会让整体略降，按实测补一次增益。"""
    o = ffmpeg('-i', src, '-vn', '-map', '0:a:0', '-af', pre + ',ebur128', '-f', 'null', '-')
    measured = float(re.findall(r'I:\s+(-?[\d.]+) LUFS', o)[-1])
    gain = target - measured
    limit = 10 ** (LIMIT_DB / 20)
    for _ in range(3):
        ffmpeg('-y', '-i', src, '-vn', '-map', '0:a:0',
               '-af', pre + f',volume={gain:.2f}dB,alimiter=limit={limit:.4f}:attack=3:release=60:level=false,aresample=48000',
               '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', wav)
        result, _ = loudness(wav)
        if abs(result - target) <= 0.5: break
        gain += min(target - result, 3.0)
    return gain, result


def hdr_kind(src):
    o = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=color_transfer', '-of', 'json', str(src)],
                       capture_output=True, text=True)
    try: transfer = json.loads(o.stdout)['streams'][0].get('color_transfer')
    except (ValueError, KeyError, IndexError): return None
    return HDR_TRANSFERS.get(transfer)


def fit_curve(pairs, size=256, min_count=50):
    """pairs：每帧 (ffmpeg 直读 RGB, 苹果显示 RGB) 两个 N×3 的 0–1 数组。返回 size×3 的单调曲线和平均误差。"""
    import numpy as np
    x = np.concatenate([a for a, _ in pairs]); y = np.concatenate([b for _, b in pairs])
    grid = np.linspace(0, 1, size); curve = np.zeros((size, 3))
    for c in range(3):
        index = np.clip(np.rint(x[:, c] * (size - 1)).astype(int), 0, size - 1)
        count = np.bincount(index, minlength=size); total = np.bincount(index, weights=y[:, c], minlength=size)
        ok = count >= min_count
        if ok.sum() < 8: raise ValueError('取到的画面颜色太少，算不出曲线')
        known = grid[ok]; value = total[ok] / count[ok]
        curve[:, c] = np.interp(grid, known, value)
        # 两头没有样本的地方按最近一段的斜率外推，再保证单调、不超出 0–1
        lo, hi = known[0], known[-1]
        if lo > 0 and ok.sum() > 2:
            k = np.flatnonzero(ok)[:4]; slope = np.polyfit(grid[k], total[k] / count[k], 1)[0]
            curve[grid < lo, c] = value[0] - slope * (lo - grid[grid < lo])
        if hi < 1 and ok.sum() > 2:
            k = np.flatnonzero(ok)[-4:]; slope = np.polyfit(grid[k], total[k] / count[k], 1)[0]
            curve[grid > hi, c] = value[-1] + slope * (grid[grid > hi] - hi)
        curve[:, c] = np.maximum.accumulate(np.clip(curve[:, c], 0, 1))
    mapped = np.stack([np.interp(x[:, c], grid, curve[:, c]) for c in range(3)], axis=1)
    return curve, float(np.abs(mapped - y).mean())


def write_cube(path, curve, title):
    lines = [f'TITLE "{title}"', f'LUT_1D_SIZE {len(curve)}'] + [' '.join(f'{v:.6f}' for v in row) for row in curve]
    Path(path).write_text('\n'.join(lines) + '\n')


def raw_rgb(args, width, height):
    import numpy as np
    r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *map(str, args), '-vf', f'scale={width}:{height}:flags=area',
                        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-'], capture_output=True)
    if r.returncode or len(r.stdout) != width * height * 6: raise ValueError('取帧失败：' + r.stderr.decode(errors='replace')[-300:])
    return np.frombuffer(r.stdout, dtype='<u2').reshape(-1, 3) / 65535.0


def apple_curve(src, cube, count=8):
    """苹果显示帧 vs ffmpeg 直读帧，逐像素拟合每通道 1D 曲线，写 cube；返回平均误差。"""
    duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(src)]).decode())
    times = [duration * (i + 0.5) / count for i in range(count)]
    with tempfile.TemporaryDirectory() as folder:
        r = subprocess.run(['swift', str(APPLE_FRAMES), str(src), folder, *[f'{t:.3f}' for t in times]], capture_output=True, text=True)
        rows = [line.split('\t') for line in r.stdout.splitlines() if line.count('\t') == 2]
        if r.returncode or len(rows) != count:
            raise ValueError('用苹果系统取显示帧失败（需要 Xcode 命令行工具里的 swift）：' + r.stderr[-300:])
        pairs = []
        for _, actual, png in rows:
            # 竖拍横拍都按显示方向：ffmpeg 默认按旋转信息转正，苹果取帧也按显示方向
            size = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', png]).decode().strip().split(',')
            w, h = (270, 480) if int(size[0]) < int(size[1]) else (480, 270)
            pairs.append((raw_direct(src, float(actual), w, h), raw_rgb(['-i', png], w, h)))
    curve, error = fit_curve(pairs)
    if error > 0.04: raise ValueError(f'苹果显示帧和 ffmpeg 帧对不上（平均差 {error:.3f}），可能取错了帧或方向；不套曲线')
    write_cube(cube, curve, 'ffmpeg 直读 HDR → 苹果系统显示（speed_voice 自动算）')
    return error


def raw_direct(src, seconds, width, height):
    import numpy as np
    r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{max(0.0, seconds - 0.001):.4f}', '-i', str(src),
                        '-vf', f'format=rgb48le,scale={width}:{height}:flags=area', '-frames:v', '1',
                        '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-'], capture_output=True)
    if r.returncode or len(r.stdout) != width * height * 6: raise ValueError('ffmpeg 取帧失败：' + r.stderr.decode(errors='replace')[-300:])
    return np.frombuffer(r.stdout, dtype='<u2').reshape(-1, 3) / 65535.0


def default_out(src, speed):
    proj = next((p for p in src.parents if (p / '项目.json').is_file()), None)
    d = (proj / '02_制作' / '口播转码') if proj else src.parent
    tag = (f'_{speed:g}倍速' if speed != 1 else '') + '_人声处理'
    return d / f'{src.stem}{tag}.mov'


def filter_path(path):
    """把文件路径写进 ffmpeg 滤镜串：先按“选项值”转义（反斜杠、冒号、单引号），再按“滤镜串”转义（反斜杠、单引号、逗号、方括号、分号）。"""
    value = ''.join('\\' + c if c in "\\:'" else c for c in str(path))
    return ''.join('\\' + c if c in "\\',[];" else c for c in value)


def clear_leftovers(out):
    """上次被强行中断（断电、强制退出）时留下的临时文件夹：同一个输出名的清掉并说一声。"""
    for old in sorted(p for p in out.parent.iterdir() if p.is_dir() and p.name.startswith(f'.speed_voice_{out.stem}_')):
        shutil.rmtree(old, ignore_errors=True)
        print(f'发现上次转码中断留下的临时文件，已清掉：{old.name}', flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('video'); ap.add_argument('--speed', type=float, default=1.2); ap.add_argument('--lufs', type=float, default=-12.5)
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    src = Path(a.video).expanduser().resolve(); out = Path(a.out).expanduser().resolve() if a.out else default_out(src, a.speed)
    if out.exists(): sys.exit(f'{out} 已存在：确认不用了再删，或用 --out 换个名字')
    out.parent.mkdir(parents=True, exist_ok=True)
    clear_leftovers(out)
    pre = f'aresample=async=1:first_pts=0' + (f',atempo={a.speed:g}' if a.speed != 1 else '') + ',' + VOICE
    vf = (f'setpts=PTS/{a.speed:g},' if a.speed != 1 else '') + 'fps=30000/1001'
    color = []
    kind = hdr_kind(src)
    if kind:
        cube = out.with_name(out.stem + '_苹果显示曲线.cube')
        print(f'原片是 {kind}（HDR）：用苹果系统显示帧算颜色曲线…', flush=True)
        try: error = apple_curve(src, cube)
        except ValueError as problem: sys.exit(f'{problem}\n没有转码。HDR 直接转会发灰，先解决取帧问题再来。')
        print(f'颜色曲线：{cube.name}（和苹果显示平均差 {error:.3f}）', flush=True)
        # 套完曲线就是普通 709 画面：帧上的标记也要改成 709，否则编码器照抄原片的 HLG 标记，达芬奇会再转一次
        vf += f',format=rgb48le,lut1d={filter_path(cube)},format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv'
        color = ['-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709']
    # 先写进同目录的临时文件夹，转完、能读、测过响度才改成最终名字：中断只会留下临时文件夹，不会留半个成品
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=f'.speed_voice_{out.stem}_') as folder:
        wav = Path(folder) / 'voice.wav'; partial = Path(folder) / out.name
        print(f'人声 {src.name}（{a.speed:g} 倍速，目标 {a.lufs} LUFS，固定增益＋限幅 {LIMIT_DB:g} dBFS）…', flush=True)
        gain, measured = voice_track(src, pre, a.lufs, wav)
        rows = windows(momentary(wav))
        print(f'固定增益 {gain:+.1f} dB，整体 {measured:.1f} LUFS；' + window_report(rows), flush=True)
        problem = spread_problem(rows)
        if problem: sys.exit(problem + '\n没有转码：先听一下原片这几段是不是本来就忽大忽小。')
        print(f'转码 {src.name} → {out.name}…', flush=True)
        ffmpeg('-y', '-i', src, '-i', wav, '-map', '0:v:0', '-map', '1:a:0', '-vf', vf,
               '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p', '-r', '30000/1001', *color,
               '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', partial)
        i, tp = loudness(partial)
        if out.exists(): sys.exit(f'{out} 在转码期间出现了：没有覆盖它。确认不用了再删，或用 --out 换个名字重跑')
        os.replace(partial, out)
    print(f'完成：{out}\n人声 {i:.1f} LUFS，真峰值 {tp:.1f} dBFS' + ('' if abs(i - a.lufs) <= 1 else '【和目标差超过 1 LU，先听一下再剪】'))


if __name__ == '__main__':
    main()
