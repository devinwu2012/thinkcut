"""口播倍速在达芬奇里做（2026-11-27 起）：时间怎么换算。

用户 2026-11-27：“我觉得不需要重新压一遍视频……直接可以用达芬奇里1.2倍速就完了呗，然后你再把语音单独导出来”。
剪口播里所有的时间（识别、对齐、删句、气口、字幕、交付包的 src_in/src_out）都按“加速后的时间”记，
也就是成片时间线上的时间，单位是 29.97 帧；它就是 1.2 倍速声音文件（project_runtime.json 的 voice）里的时间。
只有碰到原片画面的地方（放进达芬奇、预览片段、动画成片拼口播）才换算：原片秒 = 加速帧 × 1001/30000 × 倍速。
老项目（project_runtime.json 没有 speed）原片就是旧的加速版文件，倍速按 1 算，不换算。"""
import math
from fractions import Fraction

FPS = Fraction(30000, 1001)


def speed_of(config):
    """项目的倍速；老项目、没写的都是 1。"""
    value = (config or {}).get('speed') or 1
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.5 <= value <= 4:
        raise ValueError(f'project_runtime.json 里的倍速 {value!r} 不对')
    return float(value)


def voice_of(config):
    """剪口播读声音用的文件：倍速在达芬奇里做的项目是加速声音文件，老项目就是口播源本身。"""
    return config.get('voice') or config.get('source')


def clip_range(src_in, src_out, speed, clip_fps):
    """交付包的一段（加速帧 [src_in, src_out)）→ 放进达芬奇用的 (startFrame, endFrame)，单位是原片在达芬奇里的帧（clip_fps）。

    达芬奇 21.1 实测（2026-11-27 试验田）：AppendToTimeline 放 n 个原片帧，时间线上占 floor(n × 29.97/原片帧率) 帧；
    之后 SetSpeed 设 120% 时片段在时间线上的长度不变、来源往后多取 1.2 倍。所以先按时间线长度反算 n 放进去，再设速度。
    原片帧率高于 29.97 时（30、60 帧）n = ceil(长度 / 比例) 正好占满；低于 29.97 的原片（25、24 帧）放不准，直接拒绝。"""
    rate = Fraction(clip_fps).limit_denominator(100000)
    ratio = FPS / rate
    if ratio > 1:
        raise ValueError(f'原片 {float(rate):g} 帧/秒，低于 29.97：达芬奇里放不准片段长度，这条请先转成 29.97 帧再剪')
    length = src_out - src_in
    if length <= 0:
        raise ValueError('片段长度不对')
    start = round(Fraction(src_in) / FPS * Fraction(speed).limit_denominator(1000) * rate)
    count = max(1, math.ceil(Fraction(length) / ratio))
    return start, start + count


def clip_start_ok(actual, expected, tolerance=1):
    """读回的来源起点（原片帧）和算的差不超过 1 帧：达芬奇把起点对到时间线的帧格上，30 帧原片会差 1 帧（约 33 毫秒，实测声音差不到 15 毫秒）。"""
    return abs(int(actual) - int(expected)) <= tolerance
