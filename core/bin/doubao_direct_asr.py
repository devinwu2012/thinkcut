#!/usr/bin/env python3
"""Local-only entry for Doubao recording ASR turbo's documented audio.data API."""

import argparse
import base64
import http.client
import hashlib
import html
import json
import secrets
import threading
import time
import uuid
import os
import tempfile
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
from doubao_credentials import clear_verified, load_key, mark_verified, validate_key, verified_for_current_key


API = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
RESOURCE_ID = "volc.bigasr.auc_turbo"
# 2026-11-18：账号可能只开通了“录音文件识别 2.0 标准版”（先提交、再轮询取结果）。两种都支持，极速版没开通就自动用标准版。
STANDARD_RESOURCE_ID = "volc.seedasr.auc"
SUBMIT_API = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
QUERY_API = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
RESOURCES = (RESOURCE_ID, STANDARD_RESOURCE_ID)
NAMES = {RESOURCE_ID: "极速版", STANDARD_RESOURCE_ID: "2.0 标准版"}
POLL_SECONDS = 2.0
RETRY_SECONDS = 5.0      # 标准版轮询遇到断网、超时、豆包 5xx 时，隔几秒再查；绝不重新提交


class NotGranted(RuntimeError):
    """这个 Key 没开通所请求的那种识别（豆包 45000030）。"""


class Transient(RuntimeError):
    """暂时性错误：连不上、读超时、豆包 HTTP 5xx。"""


class TaskGone(RuntimeError):
    """标准版任务已提交，但豆包明确说它不存在或已失败，结果取不回来了。"""


SILENT = "豆包说这段音频里没有听到人声（20000003），没有可用的文字结果。请确认原片音轨里有口播、选对了音轨（--audio-stream）；这次识别可能已计费，请到豆包控制台核对用量"
MAX_AUDIO_BYTES = 100 * 1024 * 1024
FORMATS = {".wav": "wav", ".mp3": "mp3", ".ogg": "ogg", ".opus": "ogg"}
OPTIONS = {
    "model_name": "bigmodel",
    "enable_punc": True,
    "enable_ddc": False,
    "enable_itn": False,
    "show_utterances": True,
}


def count_timed_tokens(result):
    if not isinstance(result, dict) or not isinstance(result.get("audio_info"), dict) or not isinstance(result.get("result"), dict):
        raise RuntimeError("豆包响应结构异常；不能用于逐字剪辑")
    duration = result["audio_info"].get("duration")
    transcript = result["result"]
    utterances = transcript.get("utterances", [])
    if type(duration) is not int or duration <= 0:
        raise RuntimeError("豆包未返回有效音频时长；不能用于逐字剪辑")
    if not isinstance(utterances, list) or not utterances:
        raise RuntimeError("豆包未返回逐句结果；不能用于逐字剪辑")
    count = 0
    previous_end = -1
    sentence_text = []
    for utterance in utterances:
        if not isinstance(utterance, dict) or not isinstance(utterance.get("text"), str):
            raise RuntimeError("豆包逐句正文结构异常；不能用于逐字剪辑")
        words = utterance.get("words", [])
        if not isinstance(words, list):
            raise RuntimeError("豆包逐句字词结构异常；不能用于逐字剪辑")
        spoken = "".join(char.casefold() for char in utterance.get("text", "") if char.isalnum())
        sentence_text.append(spoken)
        if any(not isinstance(word, dict) or not isinstance(word.get("text"), str) for word in words):
            raise RuntimeError("豆包字词正文结构异常；不能用于逐字剪辑")
        timed_text = "".join(char.casefold() for word in words for char in word["text"] if char.isalnum())
        if spoken != timed_text:
            raise RuntimeError("豆包逐句文字与逐字时间项不完整对应；不能用于逐字剪辑")
        for word in words:
            if not any(char.isalnum() for char in word.get("text", "")):
                continue  # 空格等格式项可能以 -1/-1 返回，不能用于剪辑。
            start, end = word.get("start_time"), word.get("end_time")
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= duration:
                raise RuntimeError("豆包返回无效字词时间戳；不能用于逐字剪辑")
            if start < previous_end:
                raise RuntimeError("豆包字词时间戳重叠或倒序；不能用于逐字剪辑")
            previous_end = end
            count += 1
        # Some historical responses omit utterance bounds. If present, reject
        # malformed bounds before the preview formatter or a timeline uses them.
        for name in ("start_time", "end_time"):
            if name in utterance and (type(utterance[name]) is not int or not 0 <= utterance[name] <= duration):
                raise RuntimeError("豆包返回无效逐句时间戳；不能用于逐字剪辑")
        if "start_time" in utterance and "end_time" in utterance and utterance["start_time"] > utterance["end_time"]:
            raise RuntimeError("豆包逐句时间戳倒序；不能用于逐字剪辑")
    if "text" in transcript:
        if not isinstance(transcript["text"], str) or "".join(char.casefold() for char in transcript["text"] if char.isalnum()) != "".join(sentence_text):
            raise RuntimeError("豆包全文与逐句结果不完整对应；不能用于逐字剪辑")
    if count == 0:
        raise RuntimeError("豆包未返回可用的字词时间戳；不能用于逐字剪辑")
    return count


def _post(url, key, resource, request_id, payload, timeout, extra=None):
    """发一次请求，返回 (状态码, 说明, 正文, 响应头)。被拒时把豆包给的 code 和 message 原样带进中文报错（Key 隐藏）。"""
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                      headers={"Content-Type": "application/json", "X-Api-Key": key, "X-Api-Resource-Id": resource,
                               "X-Api-Request-Id": request_id, "X-Api-Sequence": "-1", **(extra or {})})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.headers.get("X-Api-Status-Code", ""), response.headers.get("X-Api-Message", ""), response.read(), response.headers
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")      # 先读完整正文，隐藏 Key 之后再截短，截断点不会落在 Key 中间
        except Exception:
            body = ""
        headers = exc.headers or {}
        detail = " ".join(x for x in (headers.get("X-Api-Status-Code", ""), headers.get("X-Api-Message", ""), body) if x).replace(key, "[已隐藏密钥]").strip()[:400]
        if "45000030" in detail or "not granted" in detail:
            raise NotGranted(f"豆包 HTTP {exc.code}：这个 Key 没开通{NAMES.get(resource, resource)}（{resource}）。豆包原话：{detail}") from None
        kind = Transient if exc.code >= 500 else RuntimeError
        raise kind(f"豆包 HTTP {exc.code}（{NAMES.get(resource, resource)}）。豆包原话：{detail or '没有返回说明'}") from None
    except URLError as exc:
        raise Transient(f"连接豆包失败：{str(exc.reason).replace(key, '[已隐藏密钥]')}") from None
    except (OSError, http.client.HTTPException) as exc:      # 读超时、连接被断开
        raise Transient(f"连接豆包中断：{type(exc).__name__} {str(exc).replace(key, '[已隐藏密钥]')}".strip()) from None


def _json(body):
    try:
        return json.loads(body)
    except ValueError:
        raise RuntimeError("豆包说识别成功，但返回了看不懂的内容（正文为空或不是合法的 JSON），这次结果没法用；"
                           "这次识别可能已计费，请到豆包控制台核对用量") from None


def _poll(key, resource, request_id, timeout, extra=None):
    """标准版：用已提交的请求号反复查询直到出结果。断网、超时、5xx 隔几秒再查，直到总期限；这里绝不重新提交。"""
    hide = lambda text: str(text).replace(key, "[已隐藏密钥]")[:160]
    deadline = time.monotonic() + timeout
    while True:
        time.sleep(POLL_SECONDS)
        try:
            code, message, body, _ = _post(QUERY_API, key, resource, request_id, {}, 60, extra)
        except Transient as exc:
            if time.monotonic() > deadline:
                raise RuntimeError(f"豆包 2.0 标准版任务已提交，但 {timeout} 秒内一直没查到结果（最后一次：{exc}）；"
                                   f"任务可能还在处理、可能已计费。再运行一次会接着查，不会重新提交") from None
            time.sleep(RETRY_SECONDS)
            continue
        except RuntimeError as exc:      # HTTP 4xx 等明确拒绝：查不到这个任务
            raise TaskGone(f"豆包查询 2.0 标准版任务被拒：{exc}") from None
        if code == "20000000":
            return _json(body)
        if code == "20000003":      # 静音也算“这个任务没有结果可取”：下次不再反复去查它，要重新提交得加 --retry-uncertain
            raise TaskGone(SILENT)
        if code not in ("20000001", "20000002"):      # 处理中 / 排队中 以外都是失败
            raise TaskGone(f"豆包识别失败（2.0 标准版）：{code or '无状态码'} {hide(message)}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"等豆包 2.0 标准版出结果超过 {timeout} 秒；任务可能还在处理、可能已计费。再运行一次会接着查，不会重新提交")


def fetch(request_id, key, resource=STANDARD_RESOURCE_ID, timeout=900):
    """只查不提交：用上次标准版的请求号取结果（不会再计费）。豆包明确说任务不存在或已失败时抛 TaskGone。"""
    if resource != STANDARD_RESOURCE_ID:
        raise ValueError("只有 2.0 标准版能凭请求号接着取结果")
    return _poll(validate_key(key), resource, request_id, timeout)


def recognize_any(audio_path, key, timeout=900, expected_audio_sha256=None, first=None, on_switch=None, on_submitted=None):
    """先试 first（默认极速版），没开通就自动换另一种。返回 (结果, 实际用的资源)。
    on_switch(被拒原话, 下一种资源) 在换另一种提交之前调用；on_submitted 原样交给 recognize。
    两种都被拒时抛 NotGranted，原话里带着两种被拒的豆包说明；清不清验证标记由调用方决定。"""
    order = [r for r in RESOURCES if r == first] + [r for r in RESOURCES if r != first]
    refused = []
    for resource in order:
        if refused and on_switch:
            on_switch(refused[-1], resource)
        try:
            return recognize(audio_path, key, timeout=timeout, expected_audio_sha256=expected_audio_sha256,
                             resource=resource, on_submitted=on_submitted), resource
        except NotGranted as exc:
            refused.append(str(exc))
    raise NotGranted("这个 Key 极速版和 2.0 标准版都没开通，没法识别。到豆包语音控制台开通其中一种，或换一个开通了的 Key。" + "；".join(refused))


def recognize(audio_path, key, timeout=900, expected_audio_sha256=None, resource=RESOURCE_ID, on_submitted=None):
    """on_submitted(request_id, resource)：标准版在提交之前调用，调用方据此记下请求号，提交后断了也能接着取。"""
    if resource not in RESOURCES:
        raise ValueError("不认识的豆包识别资源：" + str(resource))
    key = validate_key(key)
    audio_path = Path(audio_path).expanduser().resolve(strict=True)
    audio_format = FORMATS.get(audio_path.suffix.lower())
    if not audio_format:
        raise ValueError("请先把音频转成 WAV、MP3 或 OGG/OPUS")
    before = audio_path.stat()
    size = before.st_size
    if not 0 < size <= MAX_AUDIO_BYTES:
        raise ValueError("音频必须大于 0，且不超过 100 MB（程序对极速版和 2.0 标准版统一按这个上限）")
    audio_bytes = audio_path.read_bytes()
    after = audio_path.stat()
    if len(audio_bytes) != size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("读取期间音频已变化，请确认源文件后重试")
    if expected_audio_sha256 is not None and hashlib.sha256(audio_bytes).hexdigest() != expected_audio_sha256:
        raise ValueError("待上传音频与来源校验不一致；未提交识别，请核对原片及音频")
    payload = {
        "user": {"uid": "local-talking-head-edit"},
        "audio": {"format": audio_format, "data": base64.b64encode(audio_bytes).decode("ascii")},
        "request": OPTIONS,
    }
    request_id = str(uuid.uuid4())
    hide = lambda text: str(text).replace(key, "[已隐藏密钥]")[:160]
    if resource == RESOURCE_ID:
        code, message, body, _ = _post(API, key, resource, request_id, payload, timeout)
        if code == "20000003":
            raise RuntimeError(SILENT)
        if code != "20000000":
            raise RuntimeError(f"豆包拒绝请求：{code or '无状态码'} {hide(message)}")
        return _json(body)
    # 标准版：提交任务，再用同一个请求号轮询，直到出结果
    # 请求号是本机生成的，提交之前就交给调用方记下：提交发出去后断线、没收到回应时，下次也能凭它去查（查不到再按“可能已计费”处理）
    if on_submitted:
        on_submitted(request_id, resource)
    code, message, _, headers = _post(SUBMIT_API, key, resource, request_id, payload, timeout)
    if code != "20000000":
        raise RuntimeError(f"豆包拒绝请求（2.0 标准版提交）：{code or '无状态码'} {hide(message)}")
    extra = {"X-Tt-Logid": headers.get("X-Tt-Logid")} if headers.get("X-Tt-Logid") else None
    return _poll(key, resource, request_id, timeout, extra)


def save_result(output, result):
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".doubao-result-", dir=output.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description="本地音频直传豆包录音文件识别：先试极速版，没开通自动改用 2.0 标准版，可能产生按量费用；密钥仅用于本次请求")
    parser.add_argument("--audio", required=True, type=Path, help="这台 Mac 可读取的 WAV/MP3/OGG 音频路径")
    parser.add_argument("--output", required=True, type=Path, help="本机输出 JSON 路径，不得在共享能力目录内")
    args = parser.parse_args()
    audio = args.audio.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    shared_root = Path(__file__).resolve().parents[1]
    if output == shared_root or shared_root in output.parents:
        parser.error("转写结果必须保存在个人项目目录，不能写入 NAS 共享能力目录")
    if output.exists():
        parser.error("输出文件已存在；请指定新文件，避免覆盖审核数据")
    if FORMATS.get(audio.suffix.lower()) is None:
        parser.error("音频格式只支持 WAV、MP3、OGG/OPUS；视频请先用 ffmpeg 提取音频")
    if not 0 < audio.stat().st_size <= MAX_AUDIO_BYTES:
        parser.error("音频必须大于 0，且不超过 100 MB")

    shared_key = load_key()
    if shared_key:
        try:
            result, used = recognize_any(audio, shared_key)
        except NotGranted:
            try:
                clear_verified()      # 两种都被拒：去掉验证标记，自检不再说“可以转文字”
            except OSError:
                pass
            raise
        save_result(output, result)
        word_count = count_timed_tokens(result)
        if not verified_for_current_key(used, shared_key):
            try:
                mark_verified(shared_key, used)
            except OSError:
                print("识别结果已保存在本机；NAS 验证标记写入失败，请由管理员检查共享权限", file=sys.stderr)
        print(json.dumps({"status": "complete", "output": str(output), "timed_tokens": word_count, "resource_id": used, "用的是": NAMES[used]}, ensure_ascii=False))
        return

    token = secrets.token_urlsafe(32)
    state = {"stage": "waiting_for_key", "audio": audio.name, "output": str(output)}
    guard = threading.Lock()

    def worker(key):
        try:
            result, _used = recognize_any(audio, key)
            save_result(output, result)
            words = count_timed_tokens(result)
            with guard:
                state.update(stage="complete", words=words)
        except Exception as exc:
            with guard:
                state.update(stage="failed", error=str(exc)[:200])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, content, content_type="application/json", status=200):
            raw = content.encode("utf-8") if isinstance(content, str) else json.dumps(content, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.allowed():
                return self.reply({}, status=403)
            if self.path == "/status":
                with guard:
                    return self.reply(state.copy())
            if self.path != "/":
                return self.reply({}, status=404)
            page = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>豆包音频直传</title>
<style>body{{font:17px system-ui;max-width:680px;margin:50px auto;padding:20px;line-height:1.6}}input{{width:95%;padding:12px}}button{{padding:12px;margin-top:16px}}pre{{white-space:pre-wrap}}</style>
<h1>豆包录音识别 · 本机直传</h1><p>音频：{html.escape(audio.name)}</p>
<p>此次调用先试<strong>极速版</strong> <code>{RESOURCE_ID}</code>；这个 Key 没开通极速版时，自动改用<strong>2.0 标准版</strong> <code>{STANDARD_RESOURCE_ID}</code>。没有赠送额度时，提交可能产生按量费用。点击提交后，音频会从这台 Mac 直接传到豆包，不经过 TOS。结果保存在这台 Mac；密钥不写入文件。</p>
<form method="post" action="/start"><input type="hidden" name="csrf" value="{token}">
<input type="password" name="key" required autocomplete="off" placeholder="粘贴豆包语音新版控制台 API Key"><br>
<button>确认识别并上传音频（先试极速版，没开通改用 2.0 标准版）</button></form><pre id="status"></pre>
<script>setInterval(async()=>{{document.querySelector('#status').textContent=JSON.stringify(await(await fetch('/status')).json(),null,2)}},2000)</script></html>'''
            return self.reply(page, "text/html")

        def do_POST(self):
            if not self.allowed() or self.path != "/start":
                return self.reply({}, status=403)
            if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}"):
                return self.reply({}, status=403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply({}, status=400)
            if not 0 < length <= 8192:
                return self.reply({}, status=413)
            try:
                form = parse_qs(self.rfile.read(length).decode("utf-8"), max_num_fields=8)
            except (UnicodeDecodeError, ValueError):
                return self.reply({}, status=400)
            key = form.get("key", [""])[0].strip()
            if not secrets.compare_digest(form.get("csrf", [""])[0].encode("utf-8"), token.encode("ascii")) or not key:
                return self.reply({}, status=400)
            with guard:
                if state["stage"] != "waiting_for_key":
                    return self.reply({"error": "本次任务已启动"}, status=409)
                state["stage"] = "uploading_and_recognizing"
            threading.Thread(target=worker, args=(key,), daemon=True).start()
            return self.reply('<meta charset="utf-8"><p>已提交。请返回页面查看状态。</p><a href="/">查看状态</a>', "text/html")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"本机密钥输入页：http://127.0.0.1:{server.server_port}/", flush=True)
    print("按 Ctrl+C 关闭；音频只在点击提交后上传。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin')); import thinkcut_broker      # noqa: E402
    if thinkcut_broker.active(): sys.exit(thinkcut_broker.forward(__file__, sys.argv[1:]))     # 后台 AI 不拿钥匙：交给审片台代办（2026-11-26）
    main()
