# -*- coding: utf-8 -*-
"""气口精修用：口播源每个视频帧（30000/1001）的人声概率（Silero VAD）。
由对齐环境的 Python 跑（要 torch 和 silero_vad，模型在 silero_vad 包里，不联网）；主程序 pause_refine.vad_probs 起它、存缓存。
做法和试验田《气口对比》方法 C 一致（2026-11-22 用户定）：16k 单声道，512 个采样一块按顺序喂模型（带状态），
每个视频帧取与它重叠的块里的最大概率。
用法：python vad_frames.py <口播源> <总帧数> <音轨序号>；结果是一行 JSON 列表，打到标准输出。
退出码 3：这个 Python 里没有 silero_vad 或 torch。"""
import array
import json
import math
import shutil
import subprocess
import sys

FPS = 30000 / 1001
RATE = 16000
BLOCK = 512


def frame_probs(probs, total):
    per = RATE / FPS; out = []
    for f in range(total):
        part = probs[int(f * per) // BLOCK:int(math.ceil((f + 1) * per / BLOCK))]
        out.append(round(max(part), 4) if part else 0.0)
    return out


def main():
    source, total, stream = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    try:
        import torch
        from silero_vad import load_silero_vad
    except ImportError as error:
        print(f'没有 silero-vad 或 torch：{error}', file=sys.stderr); sys.exit(3)
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        print('找不到 ffmpeg', file=sys.stderr); sys.exit(1)
    raw = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', source, '-map', f'0:a:{stream}', '-ac', '1', '-ar', str(RATE), '-f', 'f32le', '-'],
                         capture_output=True, check=True).stdout
    samples = array.array('f'); samples.frombytes(raw[:len(raw) // 4 * 4])
    if sys.byteorder == 'big': samples.byteswap()
    torch.set_num_threads(4)
    pcm = torch.tensor(samples); model = load_silero_vad(); model.reset_states()
    with torch.no_grad():
        probs = [model(pcm[k:k + BLOCK], RATE).item() for k in range(0, len(pcm) - BLOCK + 1, BLOCK)]
    print(json.dumps(frame_probs(probs, total)))


if __name__ == '__main__':
    main()
