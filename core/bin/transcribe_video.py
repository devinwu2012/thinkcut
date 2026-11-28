#!/usr/bin/env python3
"""Extract one video's speech locally and send only its audio to Doubao Turbo."""

import argparse
import fcntl
import hashlib
import math
from array import array
from operator import mul
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from doubao_credentials import clear_verified, load_key, mark_verified, verified_for_current_key
from doubao_direct_asr import (MAX_AUDIO_BYTES, NAMES, OPTIONS, RESOURCE_ID, RESOURCES, STANDARD_RESOURCE_ID, NotGranted, TaskGone,
                               count_timed_tokens, fetch, recognize_any, save_result)


ROOT = Path(__file__).resolve().parents[1]
MAX_SECONDS = 2 * 60 * 60


def decoded_pcm(audio, ffmpeg):
    out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(audio),
                          "-f", "s16le", "-ac", "1", "-ar", "16000", "-"], capture_output=True, check=True).stdout
    return array("h", out[:len(out) // 2 * 2])


def same_sound(saved, fresh, ffmpeg):
    """Different ffmpeg builds encode identical audio to different MP3 bytes; compare the sound."""
    try:
        a, b = decoded_pcm(saved, ffmpeg), decoded_pcm(fresh, ffmpeg)
    except (OSError, subprocess.CalledProcessError):
        return False
    # One MP3 frame of padding is the only length difference an encoder may add.
    if not a or abs(len(a) - len(b)) > 1152:
        return False
    n = min(len(a), len(b))
    # Every audible second must match, so a short re-dubbed stretch cannot hide in a long file.
    for start in range(0, n, 16000):
        x, y = a[start:start + 16000], b[start:start + 16000]
        xx, yy = sum(map(mul, x, x)), sum(map(mul, y, y))
        if max(xx, yy) < len(x) * 100 ** 2:  # both near silent (RMS below 100 of 32768)
            continue
        if not xx or not yy or sum(map(mul, x, y)) / math.sqrt(xx * yy) < 0.99:
            return False
    return True


def probe_duration(video, ffmpeg):
    process = subprocess.run([ffmpeg, "-hide_banner", "-i", str(video)], capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", process.stderr)
    if not match:
        raise RuntimeError("无法读取视频时长或音频流；请检查媒体文件")
    seconds = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    if not 0 < seconds <= MAX_SECONDS:
        raise ValueError("视频时长需大于 0 且不超过 2 小时（程序一次最多送豆包识别 2 小时）")
    stream_lines = [line for line in process.stderr.splitlines() if re.search(r"Stream #\d+:\d+", line)]
    audio_streams = [line for line in stream_lines if ": Audio:" in line]
    # 2026-11-27 起剪口播送识别的是只有声音的 1.2 倍速声音文件（倍速在达芬奇里做），所以不再要求有画面
    if not audio_streams:
        raise ValueError("文件里没有声音")
    return seconds, len(audio_streams)


def extract_audio(video, audio, ffmpeg, audio_stream):
    temporary = audio.with_name("source_audio.partial-" + uuid.uuid4().hex + ".mp3")
    try:
        process = subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-i", str(video), "-map", f"0:a:{audio_stream}", "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "96k", str(temporary),
        ], capture_output=True, text=True)
        if process.returncode:
            raise RuntimeError("本机音频提取失败：" + process.stderr.strip()[-400:])
        if not 0 < temporary.stat().st_size <= MAX_AUDIO_BYTES:
            raise RuntimeError("提取的音频为空或超过 100 MB 上限（程序对极速版和 2.0 标准版统一按这个上限）")
        os.chmod(temporary, 0o600)
        os.replace(temporary, audio)
    finally:
        temporary.unlink(missing_ok=True)


def source_record(video):
    stat_result = video.stat()
    return {"path": str(video), "size": stat_result.st_size, "mtime_ns": stat_result.st_mtime_ns,
            "ctime_ns": stat_result.st_ctime_ns}


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(path):
    """Bounded video identity check; explicitly not a whole-file SHA-256."""
    size = path.stat().st_size
    block = 1024 * 1024
    offsets = sorted({round(max(0, size - block) * i / 15) for i in range(16)})
    checksum = hashlib.sha256(str(size).encode())
    with path.open('rb') as stream:
        for offset in offsets:
            stream.seek(offset)
            checksum.update(str(offset).encode() + b':' + stream.read(block))
    return {'algorithm': 'sha256-sampled-16x1MiB-v1', 'size': size, 'digest': checksum.hexdigest()}


def response_sha256(result):
    return hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def private_regular_file(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"运行目录的 {path.name} 必须是普通文件，不能是符号链接")


def write_private_text(path, content):
    fd, temporary = tempfile.mkstemp(prefix=".transcribe-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


PRESET_PREFIX = "tts."      # 机器配音占位（bin/doubao_tts.py）：字和时间合成时就有了，不调识别；清单里“识别方式”记成 tts.<合成资源>


def run(video, work_dir, parser, requested_audio_stream=None, retry_uncertain=False, full_source_check=False, preset=None):
    """preset：机器配音记录里的逐字结果 {"resource_id": "tts.…", "result": 和豆包识别同样格式}；给了就不调豆包识别（2026-11-22 用户定的机器配音占位）。"""
    started = time.perf_counter()
    timings = {}
    def timed(name, operation):
        tick = time.perf_counter()
        result = operation()
        timings[name] = round(time.perf_counter() - tick, 3)
        print(f'{name}: {timings[name]:.3f} 秒', file=sys.stderr, flush=True)
        return result
    if work_dir.exists() and not work_dir.is_dir():
        parser.error("运行目录路径不是文件夹")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("本机没有 ffmpeg")
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(work_dir.stat().st_mode) & 0o077:
        parser.error("运行目录可被其他本机用户访问；请选择权限为 0700 的个人目录")
    private_regular_file(work_dir / ".transcribe.lock")
    lock_fd = os.open(work_dir / ".transcribe.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):  # 另一台 Mac 持有时 NAS 报 Permission denied
            parser.error("此运行目录已有转写任务；请等待它完成")
        initial_source = source_record(video)
        source_record_path = work_dir / "source_manifest.json"
        allowed = {"source_manifest.json", "source_audio.mp3", "source_audio.partial.mp3",
                   "doubao_turbo.json", "转写预览.txt", "transcription_manifest.json", ".transcribe.lock",
                   "asr_request.json", "timings.json"}
        names = {path.name for path in work_dir.iterdir()}
        recovery_temps = {name for name in names if
                          (name.startswith("source_audio.partial-") and name.endswith(".mp3")) or
                          (name.startswith(".source-validation-") and name.endswith(".mp3")) or
                          name.startswith(".doubao-result-") or name.startswith(".transcribe-")}
        unknown = names - allowed - recovery_temps
        persistent = names - recovery_temps - {".transcribe.lock"}
        for name in allowed:
            private_regular_file(work_dir / name)
        if unknown or (persistent and not source_record_path.is_file()):
            parser.error("运行目录包含其他数据；请使用新目录，避免覆盖审核结果")
        duration, audio_stream_count = timed('probe_seconds', lambda: probe_duration(video, ffmpeg))
        if requested_audio_stream is None and audio_stream_count > 1:
            parser.error(f"视频有 {audio_stream_count} 条音轨；请试听后用 --audio-stream N 指定，从 0 开始")
        audio_stream = 0 if requested_audio_stream is None else requested_audio_stream
        if not 0 <= audio_stream < audio_stream_count:
            parser.error(f"音轨编号超出范围；此视频有 {audio_stream_count} 条音轨，从 0 开始")
        if source_record_path.is_file():
            saved_source = json.loads(source_record_path.read_text(encoding="utf-8"))
            if not isinstance(saved_source, dict):
                parser.error("已有来源清单格式无效；原数据已保留，请检查后使用新目录")
            if not saved_source.get("sha256") and not saved_source.get("fingerprint"):
                parser.error("旧运行目录缺少来源校验，原数据已保留，请人工核对；改用新目录会重新调用 API，可能再次计费")
            if {k: saved_source.get(k) for k in initial_source} != initial_source or saved_source.get("audio_stream", 0) != audio_stream:
                parser.error("运行目录属于另一份视频、音轨或原片已修改")
        else:
            saved_source = {}
        fingerprint = timed('source_fingerprint_seconds', lambda: source_fingerprint(video))
        if saved_source.get('fingerprint') and saved_source['fingerprint'] != fingerprint:
            parser.error('原片抽样内容已修改；原数据已保留，请核对后使用新目录')
        source_sha256 = saved_source.get('sha256')
        full_digest = None
        if full_source_check:
            print('严格模式：正在完整读取原视频校验…', file=sys.stderr, flush=True)
            full_digest = timed('full_source_hash_seconds', lambda: file_sha256(video))
            expected = saved_source.get('full_sha256') or source_sha256
            if expected and expected != full_digest:
                parser.error('原片内容已修改；原数据已保留，请核对后使用新目录')
            if not saved_source:
                source_sha256 = full_digest
        if source_record(video) != initial_source:
            raise RuntimeError("校验期间原片发生变化；请稍后重试")
        initial_source["audio_stream"] = audio_stream
        if not source_record_path.exists():
            save_result(source_record_path, {**initial_source, "sha256": source_sha256, 'fingerprint': fingerprint})
        audio = work_dir / "source_audio.mp3"
        result_path = work_dir / "doubao_turbo.json"
        if not audio.exists() or (not saved_source.get("audio_sha256") and not result_path.exists()):
            print('正在本机提取口播音轨…', file=sys.stderr, flush=True)
            timed('extract_audio_seconds', lambda: extract_audio(video, audio, ffmpeg, audio_stream))
        elif not audio.is_file() or not 0 < audio.stat().st_size <= MAX_AUDIO_BYTES:
            parser.error("已有提取音频为空或超过 100 MB；请检查后使用新目录")
        os.chmod(audio, 0o600)
        audio_sha256 = file_sha256(audio)
        if saved_source.get("audio_sha256") and saved_source["audio_sha256"] != audio_sha256:
            parser.error("提取音频已修改；原数据已保留，请核对后使用新目录")
        # On reuse, bind the cached transcript to freshly extracted source audio,
        # including changes outside the bounded video samples. Never overwrite it.
        if saved_source.get('audio_sha256') and not full_source_check:
            validation = work_dir / ('.source-validation-' + uuid.uuid4().hex + '.mp3')
            try:
                timed('revalidate_source_audio_seconds', lambda: extract_audio(video, validation, ffmpeg, audio_stream))
                if file_sha256(validation) != audio_sha256 and not same_sound(audio, validation, ffmpeg):
                    parser.error('原片音轨内容已修改；已有转写已保留，不会重新提交 API')
            finally:
                validation.unlink(missing_ok=True)
        save_result(source_record_path, {**initial_source, "sha256": source_sha256, "audio_sha256": audio_sha256,
                    'fingerprint': fingerprint, 'full_sha256': full_digest or saved_source.get('full_sha256')})
        if {**source_record(video), "audio_stream": audio_stream} != initial_source:
            raise RuntimeError("提音期间原片发生变化；请使用新运行目录")
        resource_used = RESOURCE_ID
        provenance = {"source_sha256": source_sha256, "audio_sha256": audio_sha256,
                      "audio_stream": audio_stream, "resource_id": RESOURCE_ID, "options": OPTIONS}
        if result_path.exists():
            print('正在校验并复用已保存的转写结果…', file=sys.stderr, flush=True)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            saved = result.get("_local_provenance", {}).get("resource_id") if isinstance(result, dict) and isinstance(result.get("_local_provenance"), dict) else None
            if saved in RESOURCES or (isinstance(saved, str) and saved.startswith(PRESET_PREFIX)):      # 当初用哪一种识别的（或机器配音自带的），就按哪一种核对
                resource_used = saved; provenance = {**provenance, "resource_id": saved}
            response = {key: value for key, value in result.items() if key != "_local_provenance"} if isinstance(result, dict) else result
            expected_provenance = {**provenance, "response_sha256": response_sha256(response)}
            if not isinstance(result, dict) or result.get("_local_provenance") != expected_provenance:
                parser.error("已有转写缺少匹配的来源校验，不能自动复用；原数据已保留，请人工核对")
            saved_manifest = work_dir / "transcription_manifest.json"
            if saved_manifest.exists():
                manifest = json.loads(saved_manifest.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict) or manifest.get("asr_sha256") != file_sha256(result_path):
                    parser.error("已有转写结果已修改；原数据已保留，请人工核对")
            count_timed_tokens(result)
        elif preset is not None:      # 机器配音占位：不调识别、不花钱，字和时间用合成时给的
            resource_used = preset["resource_id"]
            if not resource_used.startswith(PRESET_PREFIX):
                parser.error("机器配音记录的来源标记不对；请用 bin/doubao_tts.py 重新生成")
            result = {key: value for key, value in preset["result"].items() if key != "_local_provenance"}
            count_timed_tokens(result)
            if abs(result["audio_info"]["duration"] / 1000 - duration) > 1.0:
                parser.error("机器配音记录的时长和这个口播源对不上；请用 bin/doubao_tts.py 重新生成")
            provenance = {**provenance, "resource_id": resource_used}
            result = {**result, "_local_provenance": {**provenance, "response_sha256": response_sha256(result)}}
            print('机器配音占位：字和时间用合成时给的，不调豆包识别（不计费）。', file=sys.stderr, flush=True)
            save_result(result_path, result)
        else:
            request_path = work_dir / "asr_request.json"
            try:
                previous = json.loads(request_path.read_text(encoding="utf-8")) if request_path.exists() else None
            except ValueError:
                previous = None
            pending = previous if (isinstance(previous, dict) and previous.get("request_id")
                                   and previous.get("resource_id") == STANDARD_RESOURCE_ID
                                   and previous.get("audio_sha256") == audio_sha256) else None
            if request_path.exists() and not pending and not retry_uncertain:
                parser.error("上次识别请求没有完整结果，可能已计费；请先核对豆包用量，确认重试后加 --retry-uncertain")
            key = load_key()
            if not key:
                parser.error("NAS 尚未配置豆包 Key；先运行 python3 bin/doubao_credentials.py")
            result = None
            if pending:      # 上次标准版已提交成功：先凭请求号接着取结果，只查询、不重新提交
                print('上次已提交的豆包 2.0 标准版任务还没取回结果，正在接着取（只查询，不重新提交）…', file=sys.stderr, flush=True)
                try:
                    result = timed('asr_fetch_seconds', lambda: fetch(pending["request_id"], key, STANDARD_RESOURCE_ID))
                    resource_used = STANDARD_RESOURCE_ID
                except TaskGone as gone:
                    if not retry_uncertain:
                        parser.error(f"上次的任务取不回来了：{gone}。那次识别可能已计费；请先核对豆包用量，确认重新提交后加 --retry-uncertain")
                    print(f'上次的任务取不回来了：{gone}\n按 --retry-uncertain 重新提交…', file=sys.stderr, flush=True)
            if result is None:
                verified = next((r for r in RESOURCES if verified_for_current_key(r, key)), None)
                if verified is None:
                    parser.error("NAS 上的 Key 尚未通过短音频调用验证（极速版或 2.0 标准版都行）；先用 bin/doubao_direct_asr.py 测试")
                record = {"state": "submitted", "local_attempt_id": str(uuid.uuid4()),
                          "resource_id": verified, "audio_sha256": audio_sha256}
                save_result(request_path, record)
                print(f'正在提交豆包{NAMES[verified]}识别；请等待结果保存…', file=sys.stderr, flush=True)
                def switching(refused, other):      # 验证过的那种现在被拒（账号权益变了）：先改记录再换另一种；被拒的请求不计费
                    record["resource_id"] = other
                    save_result(request_path, record)
                    print(f'{refused}\n改用豆包{NAMES[other]}重新提交…', file=sys.stderr, flush=True)
                def submitted(request_id, resource):      # 标准版提交已被接受：记下请求号，断了下次接着取
                    record.update(resource_id=resource, request_id=request_id)
                    try:
                        save_result(request_path, record)
                    except OSError as exc:
                        print(f'记录豆包请求号失败（{exc}）；这次继续等结果，中途断了要按 --retry-uncertain 处理', file=sys.stderr, flush=True)
                try:
                    result, resource_used = timed('asr_round_trip_seconds', lambda: recognize_any(
                        audio, key, expected_audio_sha256=audio_sha256, first=verified, on_switch=switching, on_submitted=submitted))
                except NotGranted:
                    try: clear_verified()
                    except OSError: pass
                    request_path.unlink(missing_ok=True)      # 两种都被直接拒绝，没有提交成功，不算“可能已计费”
                    raise
                if resource_used != verified:
                    try: mark_verified(key, resource_used)
                    except OSError: pass
            provenance = {**provenance, "resource_id": resource_used}
            if isinstance(result, dict):
                result = {**result, "_local_provenance": {**provenance, "response_sha256": response_sha256(result)}}
            save_result(result_path, result)
            count_timed_tokens(result)
        if {**source_record(video), "audio_stream": audio_stream} != initial_source:
            raise RuntimeError("识别期间原片发生变化；转写结果已保存在本机，请用新目录核对原片")
        if file_sha256(audio) != audio_sha256:
            raise RuntimeError("识别期间提取音频发生变化；转写结果已保存在本机，请核对原片及音频")
        lines = []
        for row in result["result"].get("utterances", []):
            ms = row.get("start_time", 0)
            lines.append(f"[{ms//60000:02d}:{(ms//1000)%60:02d}.{ms%1000:03d}] {row.get('text', '')}")
        write_private_text(work_dir / "转写预览.txt", "\n".join(lines) + "\n")
        manifest = {
            "source": str(video), "source_size": initial_source["size"],
            "source_sha256": source_sha256, "audio_sha256": audio_sha256,
            "asr_sha256": file_sha256(result_path),
            "source_mtime_ns": initial_source["mtime_ns"], "source_duration_seconds": duration,
            "source_ctime_ns": initial_source["ctime_ns"],
            "audio": str(audio), "audio_stream": audio_stream,
            "asr": str(result_path), "resource_id": resource_used,
            "timed_tokens": count_timed_tokens(result), "stage": "transcribed_needs_review",
            'source_fingerprint': fingerprint,
            'source_verification': 'full_sha256' if full_source_check else 'sampled_video_and_verified_audio',
            'full_source_sha256_verified_this_run': full_digest,
        }
        save_result(work_dir / "transcription_manifest.json", manifest)
        timings['total_seconds'] = round(time.perf_counter() - started, 3)
        save_result(work_dir / 'timings.json', {'stages': timings, 'api_called': 'asr_round_trip_seconds' in timings,
                    'source_verification': manifest['source_verification']})
        print(json.dumps({"work_dir": str(work_dir), "timed_tokens": manifest["timed_tokens"], "stage": manifest["stage"]}, ensure_ascii=False))
    finally:
        os.close(lock_fd)


def main():
    parser = argparse.ArgumentParser(description="视频路径 → 本机提音 → 豆包（极速版或 2.0 标准版）逐字结果；不生成达芬奇时间线")
    parser.add_argument("--video", required=True, type=Path, help="本机可访问的原视频")
    parser.add_argument("--work-dir", required=True, type=Path, help="个人项目的新运行目录，不能在 NAS 共享能力目录内")
    parser.add_argument("--audio-stream", type=int, help="多音轨视频要显式选择音轨，从 0 开始")
    parser.add_argument("--retry-uncertain", action="store_true", help="核对上次请求用量后，明确允许重新提交无完整结果的识别请求")
    parser.add_argument("--preset", type=Path, help="机器配音记录（bin/doubao_tts.py 写的 .机器配音.json）：用里面的逐字结果，不调豆包识别")
    parser.add_argument('--full-source-check', action='store_true', help='严格完整视频哈希校验；NAS 大视频可能需要数分钟。默认校验抽样视频及实际源音轨')
    args = parser.parse_args()
    video = args.video.expanduser().resolve(strict=True)
    if not video.is_file():
        parser.error("视频路径不是文件")
    work_dir = args.work_dir.expanduser().resolve()
    if work_dir == ROOT or ROOT in work_dir.parents:
        parser.error("运行目录必须位于个人项目，不能写入 NAS 共享能力目录")
    preset = None
    if args.preset:
        try:
            record = json.loads(args.preset.expanduser().read_text(encoding="utf-8"))
            preset = {"resource_id": PRESET_PREFIX + str(record["资源"]), "result": record["asr"]}
        except (OSError, ValueError, KeyError, TypeError):
            parser.error("机器配音记录读不了或格式不对；请用 bin/doubao_tts.py 重新生成")
    run(video, work_dir, parser, args.audio_stream, args.retry_uncertain, args.full_source_check, preset)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin')); import thinkcut_broker      # noqa: E402
    if thinkcut_broker.active(): sys.exit(thinkcut_broker.forward(__file__, sys.argv[1:]))     # 后台 AI 不拿钥匙：交给审片台代办（2026-11-26）
    main()
