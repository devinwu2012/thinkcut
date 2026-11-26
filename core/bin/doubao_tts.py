#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器配音占位（2026-11-22 用户定）：没有口播录像、只有定稿文案时，用豆包语音合成 2.0 把「口播正文」念出来，
做成一条能让剪口播流程照常跑的“口播源”（黑画面＋配音的 mov）和每个字的时间。

用户原话：“如果……只有一个口播的文案，都没有这个A-roll，那它就机器生成人声做上去得了……我有了之后我再替换。”

  python3 bin/doubao_tts.py --project <项目文件夹>            # 合成并接着 prepare（建剪口播工程、写好“什么都不删”的选择）
  python3 bin/doubao_tts.py --project <项目文件夹> --dry-run  # 只算字数和分段，不调豆包、不花钱

产物（都在项目的 02_制作/口播转码/ 下）：
  机器配音占位_口播.mov            黑画面＋配音，恒定 29.97 帧，声音从 0 开始（剪口播 prepare 用它当原片）
  机器配音占位_口播.机器配音.json   记录：这是机器配音占位、音色、字数、每个字的时间、换成豆包识别同样格式的逐字结果
  机器配音/段_<指纹>.mp3/.json      每段的合成结果（同一段文字再跑不重复合成、不重复花钱）

按字数收费（含标点）：合成前先打印字数和分段。钥匙只用 doubao_credentials.load_key() 取，不打印、不写进任何文件。
接口：POST https://openspeech.bytedance.com/api/v3/tts/unidirectional（HTTP 单向流式 V3，每行一个 JSON；
官方文档 https://www.volcengine.com/docs/6561/1598757，2026-11-23 核对：文档没写单次字数上限，只有“超长”错误码，所以这里按段落分段合成）。
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from doubao_credentials import load_key

ROOT = Path(__file__).resolve().parents[1]
API = 'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
RESOURCE_ID = 'seed-tts-2.0'
PRESET_RESOURCE = 'tts.' + RESOURCE_ID        # 转写清单里记的“识别方式”：不是识别出来的，是合成时就知道的
sys.path.append(str(ROOT.parent / 'bin')); import thinkcut_config      # noqa: E402  配置档（2026-11-24）
_VOICE = thinkcut_config.load().get('配音')
SPEAKER = _VOICE['音色']                       # 默认 IP 的默认音色（配置档 配音.音色）；某条片子按它的 IP
SPEAKER_NAMES = {'zh_female_xiaohe_uranus_bigtts': '小何', 'zh_female_vv_uranus_bigtts': 'vv', SPEAKER: _VOICE.get('音色名', SPEAKER)}
DONE = 20000000                               # 最后一行的 code：合成完成
CHUNK_CHARS = 300                             # 一次合成最多多少字（文档没写上限；一段话一段话合成，失败只重试那一段）
GAP_SECONDS = 0.35                            # 段和段之间留的停顿
RATE = 48000
TRIES = 3
RETRY_SECONDS = 3.0
SIZES = {'3:4': (2160, 3840), '3:4满屏': (2160, 3840), '9:16': (2160, 3840), '16:9': (3840, 2160)}   # 黑画面的尺寸；3:4 和真口播一样用竖拍 9:16 的原片
NAME = '机器配音占位_口播'
SIDECAR_SUFFIX = '.机器配音.json'
END = '。！？!?；;…'


class Transient(RuntimeError):
    """暂时性错误：连不上、读超时、豆包 5xx、流中途断了。可以重试。"""


class Refused(RuntimeError):
    """豆包明确拒绝（钥匙不对、没开通、文字超长…）。重试没用。"""


def hide(text, key):
    """报错里不带钥匙。"""
    return str(text).replace(key, '***') if key else str(text)


def clean_script(text):
    """口播正文 → 要念的段落列表：去掉 ### 小标题、> 备注、分隔线和空行（script_doc 取出来的已经去过，纯文本文案再去一遍）。"""
    rows = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        s = line.replace('　', ' ').strip()
        if not s or s.startswith('#') or s.startswith('>') or re.fullmatch(r'([-*_])(\s*\1){2,}', s):
            continue
        rows.append(s)
    return rows


def split_chunks(paragraphs, limit=CHUNK_CHARS):
    """按自然段分；一段超过 limit 字就在句末（。！？；）拆，句子本身还超就在逗号处拆，再不行硬切。每块都不超过 limit 字。"""
    chunks = []
    for para in paragraphs:
        if len(para) <= limit:
            chunks.append(para)
            continue
        pieces = [p for p in re.split(r'(?<=[%s])' % END, para) if p.strip()]
        finer = []
        for piece in pieces:
            if len(piece) <= limit:
                finer.append(piece)
                continue
            for part in (p for p in re.split(r'(?<=[，,、：:])', piece) if p.strip()):
                finer += [part[i:i + limit] for i in range(0, len(part), limit)]
        current = ''
        for piece in finer:
            if current and len(current) + len(piece) > limit:
                chunks.append(current)
                current = ''
            current += piece
        if current:
            chunks.append(current)
    return [c.strip() for c in chunks if c.strip()]


def count_chars(text):
    """计费字数的估计：不算空白，标点算（豆包 usage.text_words 含标点）。"""
    return len(re.sub(r'\s', '', text))


def request_once(text, key, speaker, rate=0, timeout=120, opener=urlopen):
    """合成一段。返回 {'audio': mp3 字节, 'words': [{'word', 'start', 'end'}]（秒，相对这一段开头）, 'usage': 计费字数或 None, 'logid': …}。"""
    body = {'user': {'uid': 'thinkcut-ai'},
            'req_params': {'text': text, 'speaker': speaker,
                           'audio_params': {'format': 'mp3', 'sample_rate': RATE, 'speech_rate': int(rate), 'enable_subtitle': True}}}
    request = Request(API, data=json.dumps(body, ensure_ascii=False).encode('utf-8'), method='POST',
                      headers={'Content-Type': 'application/json', 'X-Api-Key': key, 'X-Api-Resource-Id': RESOURCE_ID,
                               'X-Api-Request-Id': str(uuid.uuid4()), 'X-Control-Require-Usage-Tokens-Return': 'text_words'})
    audio = bytearray()
    words = []
    usage = None
    finished = False
    try:
        with opener(request, timeout=timeout) as response:
            logid = response.headers.get('X-Tt-Logid', '') if getattr(response, 'headers', None) else ''
            for raw in response:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    raise Transient('豆包返回的内容不是完整的一行 JSON（多半是连接中途断了）') from None
                code = row.get('code')
                if code not in (0, DONE):
                    message = hide(f"{code} {row.get('message', '')}".strip(), key)
                    if isinstance(code, int) and 55000000 <= code < 56000000:
                        raise Transient(f'豆包服务端出错（{message}）')
                    raise Refused(f'豆包拒绝了这段合成：{message}（日志号 {logid or "没有"}）')
                if row.get('data'):
                    audio += base64.b64decode(row['data'])
                for item in (row.get('sentence') or {}).get('words') or []:
                    words.append({'word': str(item.get('word', '')), 'start': float(item.get('startTime', 0)), 'end': float(item.get('endTime', 0))})
                if isinstance(row.get('usage'), dict) and isinstance(row['usage'].get('text_words'), int):
                    usage = row['usage']['text_words']
                if code == DONE:
                    finished = True
    except HTTPError as error:
        detail = hide(error.read().decode('utf-8', 'replace')[:300], key)
        if error.code >= 500:
            raise Transient(f'豆包 HTTP {error.code}：{detail}') from None
        hint = '（requested resource not granted＝账号没开通语音合成 2.0，要用户去火山引擎控制台开通）' if 'not granted' in detail else ''
        raise Refused(f'豆包拒绝了这段合成：HTTP {error.code} {detail}{hint}') from None
    except (URLError, TimeoutError, ConnectionError, OSError) as error:
        raise Transient(f'连不上豆包或读超时：{hide(error, key)}') from None
    if not finished:
        raise Transient('豆包的回应没有收完整（没等到结束标记）')
    if not audio:
        raise Refused('豆包说合成完成，但没有返回声音')
    if not words:
        raise Refused('豆包没有返回每个字的时间（enable_subtitle），这段不能用来对字幕')
    return {'audio': bytes(audio), 'words': words, 'usage': usage, 'logid': logid}


def synthesize(text, key, speaker, rate=0, tries=TRIES, sleep=time.sleep, opener=urlopen, log=print):
    """合成一段，暂时性错误重试（最多 tries 次）。重试的那几次豆包可能也计了费，会打印出来。"""
    for attempt in range(1, tries + 1):
        try:
            return request_once(text, key, speaker, rate, opener=opener)
        except Transient as error:
            if attempt == tries:
                raise Transient(f'{error}；已重试 {tries} 次还是不行，先停下（中断的那几次可能已计费，请到豆包控制台核对用量）') from None
            log(f'  这段没合成下来（{error}），{RETRY_SECONDS * attempt:.0f} 秒后重试第 {attempt + 1} 次…')
            sleep(RETRY_SECONDS * attempt)


def chunk_id(text, speaker, rate):
    return hashlib.sha256(json.dumps([text, speaker, int(rate), RESOURCE_ID, RATE], ensure_ascii=False).encode('utf-8')).hexdigest()[:16]


def synthesize_cached(text, cache, key, speaker, rate=0, **kw):
    """同一段文字、同一个音色合成过就直接用，不再花钱。"""
    cid = chunk_id(text, speaker, rate)
    mp3, meta = cache / f'段_{cid}.mp3', cache / f'段_{cid}.json'
    if mp3.is_file() and meta.is_file():
        try:
            saved = json.loads(meta.read_text(encoding='utf-8'))
            if saved.get('text') == text and saved.get('audio_sha256') == hashlib.sha256(mp3.read_bytes()).hexdigest():
                return {**saved, 'mp3': mp3, 'cached': True}
        except (ValueError, OSError):
            pass
    got = synthesize(text, key, speaker, rate, **kw)
    cache.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(got['audio'])
    saved = {'text': text, 'speaker': speaker, 'rate': int(rate), 'words': got['words'], 'usage': got['usage'], 'logid': got['logid'],
             'audio_sha256': hashlib.sha256(got['audio']).hexdigest(), 'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    meta.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding='utf-8')
    return {**saved, 'mp3': mp3, 'cached': False}


def decode_pcm(mp3, ffmpeg):
    """mp3 → 48k 单声道 16 位裸数据。"""
    out = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(mp3), '-f', 's16le', '-ac', '1', '-ar', str(RATE), '-'],
                         capture_output=True)
    if out.returncode or not out.stdout:
        raise RuntimeError(f'ffmpeg 解不开这段配音：{mp3.name} {out.stderr.decode("utf-8", "replace")[-200:]}')
    return out.stdout[:len(out.stdout) // 2 * 2]


def join(pieces, gap=GAP_SECONDS):
    """pieces：[(pcm 字节, words)]。拼成一条，段间留 gap 秒静音；每个字的时间加上它那段的起点。返回 (pcm, [{'word','start','end','chunk'}], 总秒数)。"""
    silence = b'\x00\x00' * int(RATE * gap)
    pcm = bytearray()
    words = []
    for index, (data, rows) in enumerate(pieces):
        if index:
            pcm += silence
        offset = len(pcm) / 2 / RATE
        length = len(data) / 2 / RATE
        for row in rows:
            start = min(max(row['start'], 0.0), length)
            words.append({'word': row['word'], 'start': offset + start, 'end': offset + min(max(row['end'], start), length), 'chunk': index})
        pcm += data
    return bytes(pcm), words, len(pcm) / 2 / RATE


def to_asr(words, total_seconds):
    """每个字的时间 → 和豆包识别一样的逐字结果（transcribe_video 认的格式），剪口播后面的步骤不用改。
    豆包合成把标点粘在字后面（“果，”“儿？”）：标点留在句子正文里，逐字项只留字；按句末标点和分段切成一句一句。
    时间整理成毫秒整数、一个接一个不重叠（合成给的英文数字词如 X500 的时间不准，后面的声学对齐会重新量）。"""
    duration = max(int(round(total_seconds * 1000)), 1)
    utterances = []
    current = {'text': '', 'words': []}
    previous_end = 0
    def close():
        nonlocal current
        if current['words']:
            utterances.append({'text': current['text'].strip(), 'start_time': current['words'][0]['start_time'],
                               'end_time': current['words'][-1]['end_time'], 'words': current['words']})
        elif utterances and current['text'].strip():
            utterances[-1]['text'] += current['text'].strip()        # 只有标点的尾巴并到上一句
        current = {'text': '', 'words': []}
    last_chunk = None
    for row in words:
        raw = re.sub(r'\s+', '', row['word'])
        if not raw:
            continue
        if last_chunk is not None and row.get('chunk') != last_chunk:
            close()
        last_chunk = row.get('chunk')
        spoken = ''.join(ch for ch in raw if ch.isalnum())
        current['text'] += raw
        if spoken:
            start = max(int(round(row['start'] * 1000)), previous_end)
            end = max(int(round(row['end'] * 1000)), start + 10)
            if end > duration:
                end = duration
                start = min(start, end - 1)
            if start < previous_end or start >= end:
                raise RuntimeError('配音的字时间排不进总时长里（合成结果异常），请重新合成这一段')
            previous_end = end
            current['words'].append({'text': spoken, 'start_time': start, 'end_time': end, 'confidence': 0})
        if raw[-1] in END:
            close()
    close()
    if not utterances:
        raise RuntimeError('配音没有可用的字时间')
    return {'audio_info': {'duration': duration},
            'result': {'additions': {'duration': str(duration)}, 'text': ''.join(u['text'] for u in utterances), 'utterances': utterances}}


def make_mov(pcm, seconds, out, size, ffmpeg):
    """黑画面＋配音：恒定 30000/1001 帧、单音轨、声音和画面都从 0 开始（剪口播 init 的要求）。画面时长取整到帧、不短于声音。"""
    frames = int(seconds * 30000 / 1001) + 2
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.pcm', dir=out.parent, delete=False) as handle:
        handle.write(pcm)
        raw = Path(handle.name)
    partial = out.with_name(out.stem + '.partial.mov')
    try:
        done = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                               '-f', 'lavfi', '-i', f'color=c=black:s={size[0]}x{size[1]}:r=30000/1001',
                               '-f', 's16le', '-ar', str(RATE), '-ac', '1', '-i', str(raw),
                               '-frames:v', str(frames), '-af', 'apad', '-shortest',
                               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '30', '-pix_fmt', 'yuv420p', '-bf', '0', '-g', '30',
                               '-c:a', 'pcm_s16le', '-video_track_timescale', '30000', str(partial)], capture_output=True, text=True)
        if done.returncode or not partial.is_file():
            raise RuntimeError('ffmpeg 没能做出配音的口播源：' + done.stderr.strip()[-400:])
        os.replace(partial, out)
    finally:
        raw.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return frames


def script_doc():
    """projects/ 的文案解析（环境变量 THINKCUT_PM 优先，否则是兄弟目录）。"""
    import importlib.util
    path = Path(os.environ.get('THINKCUT_PM') or ROOT.parent / 'projects' / 'bin').expanduser() / 'script_doc.py'
    if not path.is_file():
        raise SystemExit(f'找不到文案解析程序 {path}（projects/）；用 --script 指定纯文本文案')
    spec = importlib.util.spec_from_file_location('thinkcut_script_doc', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_script(root):
    """项目文案 03_成片/<项目名>.md 的「口播正文」（没搬的老项目读 02_制作/ 那份）。"""
    new, old = root / '03_成片' / (root.name + '.md'), root / '02_制作' / (root.name + '.md')
    doc = old if old.is_file() and not new.exists() else new
    if not doc.is_file():
        raise SystemExit(f'没有定稿文案：缺 {doc}。把定稿全文存成这个文件（里面要有「## 口播正文」），或用 --script 指定纯文本文案')
    text = script_doc().read(doc)['口播正文']
    if not text:
        raise SystemExit(f'{doc.name} 里认不出「口播正文」：在口播开始的地方加「## 口播正文」小标题再运行')
    return text, doc


def sidecar_of(video):
    """口播源旁边的机器配音记录（有它＝这条口播是机器配音占位）。"""
    video = Path(video)
    return video.with_name(video.stem + SIDECAR_SUFFIX)


def load_sidecar(video):
    path = sidecar_of(video)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) and data.get('占位') and isinstance(data.get('asr'), dict) else None


def build(root, text, source_note, args, key_loader=load_key, opener=urlopen, log=print):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('本机没有 ffmpeg：运行 bash bin/setup_mac.sh 装好再来')
    paragraphs = clean_script(text)
    chunks = split_chunks(paragraphs)
    if not chunks:
        raise SystemExit('口播正文是空的，没有可念的字')
    total = sum(count_chars(c) for c in chunks)
    out_dir = root / '02_制作' / '口播转码'
    mov = out_dir / f'{NAME}.mov'
    name = SPEAKER_NAMES.get(args.speaker, args.speaker)
    log(f'机器配音：{source_note}，共 {total} 字（含标点，豆包按字数收费），分 {len(chunks)} 段合成；音色 {name}（{args.speaker}），语速 {args.rate}。')
    # 2026-11-23 初稿模式实测：字数和分段每次都先打印出来再合成（不用先跑 --dry-run、不用等人确认）；--dry-run 打印完就停
    for index, chunk in enumerate(chunks, 1):
        log(f'  预计第 {index} 段 {count_chars(chunk)} 字：{chunk[:24]}…')
    if args.dry_run:
        log('（--dry-run：只算字数，没有调豆包，没有花钱）')
        return None
    text_sha = hashlib.sha256('\n'.join(chunks).encode('utf-8')).hexdigest()
    old = load_sidecar(mov) if mov.is_file() else None
    if old and old.get('文案指纹') == text_sha and old.get('音色') == args.speaker and old.get('语速') == int(args.rate) and not args.force:
        log(f'这份文案已经配过音了，直接用：{mov}（要重新合成加 --force）')
        return mov
    if mov.exists() and not args.force and (out_dir.parent / f'{NAME}_AI剪辑' / '审核' / 'project_runtime.json').is_file():
        raise SystemExit(f'文案改过了，但旧的机器配音已经建了剪口播工程（{NAME}_AI剪辑），直接覆盖会让它对不上原片。'
                         '要按新文案重配：先把旧的 机器配音占位_口播.mov、.机器配音.json 和 机器配音占位_口播_AI剪辑 文件夹挪进回收站（配置档里的“回收站”），再运行；'
                         '或加 --force 只重做配音文件（旧剪口播工程就不能再用了）')
    key = key_loader()
    if not key:
        raise SystemExit('NAS 上还没有配置豆包 Key：先运行 python3 bin/doubao_credentials.py')
    cache = out_dir / '机器配音'
    pieces = []
    billed = 0
    fresh = 0
    for index, chunk in enumerate(chunks, 1):
        try:
            got = synthesize_cached(chunk, cache, key, args.speaker, args.rate, opener=opener, log=log)
        except (Transient, Refused) as error:
            raise SystemExit(f'第 {index}/{len(chunks)} 段没合成成功：{hide(error, key)}\n前面合成好的段已经存着（{cache}），处理后同一条命令重跑只会合成剩下的段。') from None
        if not got['cached']:
            fresh += 1
            billed += got['usage'] if isinstance(got['usage'], int) else count_chars(chunk)
        log(f"  第 {index}/{len(chunks)} 段 {count_chars(chunk)} 字：{'用上次合成好的' if got['cached'] else '已合成'}")
        pieces.append((decode_pcm(got['mp3'], ffmpeg), got['words']))
    pcm, words, seconds = join(pieces)
    asr = to_asr(words, seconds)
    frames = make_mov(pcm, seconds, mov, SIZES[args.format], ffmpeg)
    record = {'占位': True, '类型': '机器配音', '说明': '没有口播录像，用豆包语音合成 2.0 照定稿文案念的占位配音；用户录了真口播后整条换掉（见 skills/a-roll/SKILL.md「机器配音占位」）',
              '引擎': '豆包语音合成 2.0', '资源': RESOURCE_ID, '音色': args.speaker, '音色名': name, '语速': int(args.rate),
              '字数': total, '这次新合成的计费字数': billed, '段数': len(chunks), '秒数': round(seconds, 3), '帧数': frames,
              '画面': '黑画面 %d×%d（没有人脸：分镜不排露脸镜头、不放人脸小圆圈）' % SIZES[args.format],
              '文案': source_note, '文案指纹': text_sha, '生成时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
              '段': chunks, 'words': [{'word': w['word'], 'start': round(w['start'], 4), 'end': round(w['end'], 4)} for w in words], 'asr': asr}
    sidecar_of(mov).write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    log(f'配音做好了：{mov}（{seconds:.1f} 秒，{frames} 帧；这次新合成 {fresh} 段、计费约 {billed} 字）')
    log(f'记录：{sidecar_of(mov)}（标着“机器配音占位”，带每个字的时间）')
    log(f'发试听（审片台“口播试听”栏，init 之后）：thinkcut_edit.py workbench <片子目录> --publish 口播试听 --kind 试听 --files "{mov}" --title "机器配音占位" --look "…" --draft --placeholder；'
        '直接给这个 .mov 就行，审片台自动抽出声音，不用自己转 mp3')
    return mov


def prepare(root, mov, log=print):
    """接着建剪口播工程：prepare 发现旁边有机器配音记录就不调豆包识别（字和时间合成时就有了）；再写好“什么都不删”的选择。"""
    folder = root / '02_制作' / f'{NAME}_AI剪辑'
    code = subprocess.run([sys.executable, str(ROOT / 'bin' / 'edit_project.py'), 'prepare', '--project-dir', str(folder), '--video', str(mov)],
                          stdin=subprocess.DEVNULL).returncode
    if code:
        raise SystemExit(code)
    decisions = folder / '审核' / 'review_decisions.json'
    if not decisions.exists():
        decisions.write_text(json.dumps({'reviewer': '机器配音占位：照定稿文案合成，没有重录、口误可删', 'limitations': ['机器配音，不是用户的声音；换真口播后按正常流程重剪'],
                                         'cuts': []}, ensure_ascii=False, indent=1), encoding='utf-8')
        log(f'已写好“什么都不删”的选择：{decisions}')
    log(f'下一步：python3 bin/edit_project.py finish --project-dir "{folder}" --no-serve → corrections --done（字幕就是文案原文，通读一遍）→ deliver；'
        '机器配音不用 1.2 倍速、不用修音、不用人工气口精修。')
    return folder


def main(argv=None, **kw):
    parser = argparse.ArgumentParser(description='机器配音占位：定稿文案的口播正文 → 豆包语音合成 2.0 → 黑画面口播源＋每个字的时间（按字数收费）')
    parser.add_argument('--project', required=True, type=Path, help='项目文件夹（有 项目.json 和 01_原始素材）')
    parser.add_argument('--script', type=Path, help='纯文本文案；不写就取项目文案 03_成片/<项目名>.md 的「口播正文」')
    parser.add_argument('--speaker', help='音色（不写用这条片子所属 IP 配置的音色）')
    parser.add_argument('--rate', type=int, default=0, help='语速 -50～100（0 正常，100 是 2 倍速）')
    parser.add_argument('--format', choices=list(SIZES), help='片子画幅（决定黑画面的尺寸；不写用 IP 配置的默认画幅）')
    parser.add_argument('--dry-run', action='store_true', help='只算字数和分段，不调豆包、不花钱')
    parser.add_argument('--force', action='store_true', help='已经配过也重新做配音文件（合成过的段仍然复用，不重复花钱）')
    parser.add_argument('--no-prepare', action='store_true', help='只做配音文件，不接着建剪口播工程')
    args = parser.parse_args(argv)
    root = args.project.expanduser().resolve()
    if not (root / '项目.json').is_file() or not (root / '01_原始素材').is_dir():
        parser.error('--project 要写项目文件夹（里面有 项目.json 和 01_原始素材）；还没有项目先用 thinkcut-project 建')
    if not -50 <= args.rate <= 100:
        parser.error('--rate 只能是 -50 到 100')
    cfg = thinkcut_config.load(project=root)      # 这条片子所属 IP 的配置：默认音色、画幅
    if not args.speaker:
        args.speaker = cfg.get('配音.音色'); SPEAKER_NAMES.setdefault(args.speaker, cfg.get('配音.音色名', args.speaker))
    args.format = args.format or cfg.get('剪辑.画幅')
    if args.format not in SIZES: parser.error(f'IP 配置的画幅 {args.format} 不支持（可选 {"、".join(SIZES)}）')
    if args.script:
        text, note = args.script.expanduser().read_text(encoding='utf-8'), f'文案 {args.script.name}'
    else:
        text, doc = project_script(root)
        note = f'{doc.name} 的「口播正文」'
    mov = build(root, text, note, args, **kw)
    if mov and not args.no_prepare:
        prepare(root, mov)
    return mov


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin')); import thinkcut_broker      # noqa: E402
    if thinkcut_broker.active(): sys.exit(thinkcut_broker.forward(__file__, sys.argv[1:]))     # 后台 AI 不拿钥匙：交给审片台代办（2026-11-26）
    main()
