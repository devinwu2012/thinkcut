# -*- coding: utf-8 -*-
# 初稿模式（2026-11-22 用户定“先做出来再改”）：接到新片子任务，口播剪辑、动画、审核线、封面、上传文案五项按 AI 的判断一口气做成初稿，
# 发到审片台，用户边看边改；中间不停下来问。用户原话：“每次我一发这个新的任务的时候，他就直接这五项一起做……按照AI的想法直接做就行了，
# 不用来问我，等做完了我边看边改……我需要它像一个剪辑师一样，先给我做出来”。
# 这里放：占位（机器配音、生成图）记在哪和怎么列、占位图怎么生成、只留的最终闸（整片导出前用户在审片台点过审核线“这一栏定了”）、五项做到哪。
import json, os, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .common import Job, die, ip_config, rj, say, wj

MACHINE = '机器配音'
PH_DIR = ('实拍', '占位图')
# 生图和封面同一种做法：Codex 自带的生图工具（2026-11-21 用户定：Codex 只用于生图）。测试、别的生图工具用环境变量 THINKCUT_IMAGE_GEN 换成别的命令
# （按空格分开的命令，后面会接上“工作目录、任务单路径”两个参数）。
CODEX = ['codex', 'exec', '--skip-git-repo-check', '-m', 'gpt-6.1-sol', '-c', 'model_reasoning_effort="high"']
GEN_TIMEOUT = 15 * 60


# ───────────────────────── 占位：机器配音、生成图 ─────────────────────────
def machine_voice(job: Job):
    """口播是机器配音占位时返回那条记录（init 从剪口播工程的 project_runtime.json 带进 job.json），否则 None。"""
    p = job.get('placeholder') if hasattr(job, 'get') else None
    return p if isinstance(p, dict) and p.get('kind') == MACHINE else None


def voice_name(job: Job, mv):
    """机器配音用的音色名：先看配音记录（.机器配音.json 的 音色名），没有就用这条片子所属 IP 配置的（配置档 配音.音色名）。"""
    try:
        name = rj(mv['record']).get('音色名') if mv.get('record') else None
    except (OSError, ValueError):
        name = None
    return name or ip_config(job.dir).get('配音.音色名', '机器')


def placeholder_files(sid, n):
    """镜头的占位图放哪（相对片子目录）：一张 实拍/占位图/<镜头>.png，几张 <镜头>_1.png、_2.png…"""
    base = '/'.join(PH_DIR)
    return [f'{base}/{sid}.png'] if n == 1 else [f'{base}/{sid}_{k + 1}.png' for k in range(n)]


def placeholder_shots(job: Job):
    """分镜规格.json 里写了「占位图」的镜头：[{id, 描述: [..], 文件: [片子目录下的路径..], 有: [在不在..]}]。
    占位不另记一份清单：分镜规格里有「占位图」就是占位，换成真素材（删掉 占位图、写 实拍/图片）它就不在这里了。"""
    f = job.p('分镜规格.json')
    if not f.exists(): return []
    out = []
    for s in rj(f).get('shots') or []:
        if '占位图' not in s: continue
        ds = s['占位图'] if isinstance(s['占位图'], list) else [s['占位图']]
        files = [job.dir / x for x in placeholder_files(s['id'], len(ds))]
        ctx = [str(x) for x in ([s.get('画面')] if isinstance(s.get('画面'), str) else []) + (s.get('口播要点') if isinstance(s.get('口播要点'), list) else []) if x]
        out.append({'id': s['id'], '描述': [str(x) for x in ds], '文件': files, '有': [p.is_file() for p in files], '口播': ctx})
    return out


def placeholders(job: Job):
    """这条片子现在有哪些占位：[{kind: 机器配音|占位图, where: 哪里, text: 给用户看的一句话, shots: […]}]。审片台、status、check 都用它。"""
    out = []
    mv = machine_voice(job)
    if mv:
        out.append({'kind': MACHINE, 'where': '整条口播', 'text': f'口播是机器配音（豆包“{voice_name(job, mv)}”照定稿文案念的），没有你的画面和声音；你录了真口播跟 AI 说一声就换'})
    ph = placeholder_shots(job)
    if ph:
        times = {}
        bf = job.p('分镜.json')
        if bf.exists():
            try: times = {s['id']: (s['a'], s['b']) for s in rj(bf)['shots']}
            except (ValueError, KeyError, OSError): times = {}
        def at(sid):
            t = times.get(sid)
            return f"{sid}（{int(t[0] // 60)}:{t[0] % 60:04.1f}）" if t else sid
        out.append({'kind': '占位图', 'where': '、'.join(at(x['id']) for x in ph), 'shots': [x['id'] for x in ph],
                    'text': f"{len(ph)} 个镜头的画面是 AI 生成的占位图（{'、'.join(at(x['id']) for x in ph)}）；你有真素材了跟 AI 说一声就换"})
    return out


# ───────────────────────── 该做动画还是占位图（2026-11-23 初稿模式实测后定） ─────────────────────────
# 实测那条讲“开关离地一米三、插座离台面三十公分”，7 个镜头全用了占位图，不合格。判断标准（用户原话“像一个剪辑师一样”，架构审查 2026-11-23 定）：
# 讲结构、尺寸、位置、原理、步骤、对错对比的镜头做动画；只有需要真实场景、真实质感、真人的镜头才用占位图。
ANIM_KINDS = ('关系', '对比', '档位', '过程', '结构')          # 分镜「讲什么」里这几种一定是动画
import re as _re
ANIM_WORDS = _re.compile(r'[\d一二两三四五六七八九十百半]+\s*(?:公分|厘米|毫米|公里|米|cm|mm|m|寸|度|°|%|％|倍|层|根|个档)'
                         r'|离地|离墙|离台面|离顶|高度|间距|距离|尺寸|多高|多宽|多厚|多深|位置|点位|结构|原理|为什么会|怎么形成'
                         r'|第[一二三四五六七八九十\d]+步|步骤|先.{1,12}再|对比|区别|正确|错误|做对|做错|不能这样|应该这样')


def should_animate(shot):
    """这个占位图镜头是不是该做成动画：返回命中的词（没有返回 ''）。只看口播要点、画面、讲什么，不看「占位图」的描述（那是给生图看的）。"""
    if '占位图' not in shot: return ''
    if shot.get('讲什么') in ANIM_KINDS: return f"讲什么＝{shot['讲什么']}"
    text = ' '.join(str(x) for x in ([shot.get('画面')] + list(shot.get('口播要点') or [])) if x)
    m = ANIM_WORDS.search(text)
    return m.group(0) if m else ''


def animation_notes(spec):
    """分镜规格里“该做动画却写了占位图”的提醒（beats、status 都打印；不拦——有的镜头确实要真实场景）。"""
    shots = [s for s in spec.get('shots') or [] if isinstance(s, dict)]
    hits = [(s.get('id'), w) for s in shots if (w := should_animate(s))]
    notes = [f"镜头 {sid} 讲到“{w}”却写了占位图：讲结构、尺寸、位置、原理、步骤、对错对比的镜头要做动画（HyperFrames 或 Blender，见 动画成片.md），"
             '只有需要真实场景、真实质感、真人的镜头才用占位图。确实该用图的不用改' for sid, w in hits]
    pics = [s for s in shots if any(k in s for k in ('占位图', '实拍', '图片', '文字卡'))]
    if shots and len(pics) == len(shots) and any('占位图' in s for s in shots):
        notes.append(f'全片 {len(shots)} 个镜头没有一个动画镜头：像剪辑师一样，该做动画的做动画（初稿模式.md「什么时候做动画」）；'
                     '确实整条都不需要动画的，把原因写成全片假设（thinkcut_edit.py assume DIR "没做动画，因为…"）')
    return notes


# ───────────────────────── 文字卡：机器配音片子里“本该露脸”的句子放什么 ─────────────────────────
CARD_DIR = ('实拍', '文字卡')
CARD_BG, CARD_FG, CARD_SUB = (22, 24, 29), (255, 255, 255), (170, 176, 188)      # 包装系统整屏卡片的深底 #16181D、白字、第二行以后浅灰


def text_card_file(sid):
    return '/'.join(CARD_DIR) + f'/{sid}.png'


def text_cards(job: Job, shots):
    """分镜里写了「文字卡」的镜头：画一张中性的字卡（深底、白字居中，第一行大、后面小）到 实拍/文字卡/<镜头>.png，字没变就不重画。
    用在机器配音的片子里自我介绍、结尾引导关注这类“本该露脸”的句子（2026-11-23 定：不再给这种句子生成一张不相干的图）。返回新画的镜头。"""
    todo = [s for s in shots if s.get('文字卡')]
    if not todo: return []
    from PIL import Image, ImageDraw, ImageFont
    x, y, w, h = job.fmt['card']; made = []
    for s in todo:
        out = job.dir / text_card_file(s['id']); lines = s['文字卡']; mark = out.with_suffix('.txt'); sig = f'{w}x{h}\n' + '\n'.join(lines)
        if out.is_file() and mark.is_file() and mark.read_text(encoding='utf-8') == sig: continue
        im = Image.new('RGB', (w, h), CARD_BG); g = ImageDraw.Draw(im)
        sizes = [round(w * 0.085)] + [round(w * 0.052)] * (len(lines) - 1); fonts = []
        for t, size in zip(lines, sizes):
            f = ImageFont.truetype(job['subtitle_font'], size, index=1)
            while f.getlength(t) > w * 0.84 and size > 40: size -= 4; f = ImageFont.truetype(job['subtitle_font'], size, index=1)
            fonts.append(f)
        gap = round(w * 0.03); total = sum(f.size for f in fonts) + gap * (len(lines) - 1); top = (h - total) / 2
        for i, (t, f) in enumerate(zip(lines, fonts)):
            g.text(((w - f.getlength(t)) / 2, top), t, font=f, fill=CARD_FG if i == 0 else CARD_SUB); top += f.size + gap
        out.parent.mkdir(parents=True, exist_ok=True); im.save(out); mark.write_text(sig, encoding='utf-8'); made.append(s['id'])
    return made


def assumptions(job: Job):
    """全片层面的假设（画幅、没做动画的原因等，不属于哪一张卡片的）：job.json 的 assumptions，审片台审核线栏头下面和占位说明排在一起。"""
    xs = job.get('assumptions') if hasattr(job, 'get') else None
    return [str(x) for x in xs if str(x).strip()] if isinstance(xs, list) else []


def assume_cmd(job: Job, text=None, clear=False):
    """assume：记一条全片假设（不带文字就只列出；--clear 清空）。"""
    xs = [] if clear else assumptions(job)
    if text and text.strip() and text.strip() not in xs: xs.append(text.strip())
    if text or clear: job.cfg['assumptions'] = xs; job.save()
    say('全片假设（审片栏栏头下方显示）：' + ('；'.join(xs) if xs else '没有'))
    return xs


def resolve_notes(job: Job):
    """达芬奇被切走过、工程借用了别的库：汇报里要告诉用户的话（2026-11-23 定：切工程不问，但保存、留记录、做完说一声）。"""
    root = next((q for q in job.dir.parents if (q / '项目.json').is_file()), None); out = []
    if not root: return out
    try: rows = json.loads((root / '02_制作' / '达芬奇切换记录.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): rows = []
    seen = []
    for r in rows:
        k = (r.get('原项目库'), r.get('原工程'))
        if r.get('原工程') and r.get('原工程') != job.get('project') and k not in seen: seen.append(k)
    if seen:
        out.append('达芬奇被切到了这个项目（切之前都保存过）：原来开着' + '、'.join(f'「{d}」库的《{p}》' for d, p in seen) + '。汇报时跟用户说一句“达芬奇从《…》切到了这个项目，要回去说一声”')
    try: b = (json.loads((root / '项目.json').read_text(encoding='utf-8')).get('resolve') or {}).get('borrowed')
    except (OSError, ValueError): b = None
    if b: out.append(f"这个项目的工程暂存在「{b.get('库')}」库（建工程时达芬奇开着，登记不了自己的库）；存档后 project.py resolve-library <项目> --migrate 迁回，不迁也不影响用")
    return out


# ───────────────────────── 占位图：按分镜的画面描述生成 ─────────────────────────
TASK = """不打开、不参考任何旧图，按文字新生成。

Use your built-in image generation tool to create {n} brand-new photorealistic image(s) for a home-renovation / interior-lighting video.
They are placeholder B-roll pictures: plain, believable real-life photos, like stills from a documentary. {orient}

{context}{scenes}

Rules for every image:
- Never ask a question and never stop to clarify: nobody can answer. If a description is short or unclear, pick the most plausible reading for a home-renovation / lighting video (use the narration context above) and generate anyway.
- Photorealistic, natural light and colour, an ordinary Chinese home or renovation site unless the scene says otherwise.
- NO text, NO captions, NO labels, NO watermark, NO logo, NO "AI generated" badge anywhere in the image.
- No recognisable real person's face; no brand names.
- Keep the main subject in the central 80% of the frame (the edges may be cropped).
{same}
Save each image as PNG exactly at the path given for it (create the folder if needed). Do not write any other file.
"""


def gen_command():
    custom = os.environ.get('THINKCUT_IMAGE_GEN')
    return (custom.split(), True) if custom else (CODEX, False)


def write_task(job: Job, item, work):
    x, y, w, h = job.fmt['card']
    orient = (f'Portrait orientation, about 1024×1536 (the picture area is {w}×{h}, taller than wide).' if h > w
              else f'Landscape orientation, about 1536×1024 (the picture area is {w}×{h}, wider than tall).')
    scenes = '\n'.join(f'Image {k + 1}: {d}\n  Save to: {p}' for k, (d, p) in enumerate(zip(item['描述'], item['文件'])))
    same = ('- These images are one comparison group: use the SAME camera position, framing and room for all of them, and change ONLY the thing being compared.\n'
            if len(item['描述']) > 1 else '')
    t = work / f"任务_{item['id']}.md"
    context = ('What the narrator is saying while these pictures are on screen (Chinese; context only, do NOT put any of it into the image as text):\n'
               + '\n'.join(f'- {x}' for x in item.get('口播') or []) + '\n\n') if item.get('口播') else ''
    t.write_text(TASK.format(n=len(item['描述']), orient=orient, scenes=scenes, same=same, context=context), encoding='utf-8')
    return t


def gen_one(job: Job, item, work):
    """生成一个镜头的占位图（一组对比一次出齐）。返回 (镜头, 没生成出来的文件, 日志)。"""
    task = write_task(job, item, work); log = work / f"日志_{item['id']}.txt"
    cmd, custom = gen_command()
    full = cmd + [str(work), str(task)] if custom else cmd + ['-C', str(job.dir), '-s', 'workspace-write', f'Read {task} and do the task exactly as specified.']
    for p in item['文件']: p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, 'w', encoding='utf-8') as f:
            rc = subprocess.call(full, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, timeout=GEN_TIMEOUT)
    except subprocess.TimeoutExpired:
        rc = 'timeout'
    except OSError as e:
        rc = f'起不来（{e}）'
    with open(work / '生图记录.log', 'a', encoding='utf-8') as f:
        f.write(f"{time.strftime('%F %T')}\t{item['id']}\t{len(item['描述'])} 张\trc={rc}\n")
    return item['id'], [p for p in item['文件'] if not p.is_file()], log


def gen_images(job: Job, shots=None, force=False, workers=3):
    """placeholder-images：分镜规格.json 里写了「占位图」的镜头，缺图的按画面描述生成到 实拍/占位图/。已有的不重生成（--force 才重出，旧图改名留着）。
    规矩（分镜规格.md）：不加“AI 生成”角标；口播列举、对比几个场景时一个场景一张，同一组同机位、一次出齐。"""
    items = placeholder_shots(job)
    if shots: items = [x for x in items if x['id'] in shots]
    if not items: say('分镜规格.json 里没有写「占位图」的镜头（缺画面的镜头写 "占位图": "画面描述"，几个场景写成列表）'); return []
    work = job.dir.joinpath(*PH_DIR, '_生成记录'); work.mkdir(parents=True, exist_ok=True)
    todo = []
    for x in items:
        if force:
            for p in x['文件']:
                if p.is_file(): p.rename(p.with_name(f'{p.stem}_旧{int(time.time())}{p.suffix}'))
            x['有'] = [False] * len(x['文件'])
        if not all(x['有']): todo.append(x)
    if not todo:
        say(f'{len(items)} 个占位图镜头的图都在，不用生成（要重出加 --force）'); sheet = overview(job)
        if sheet: say(f'占位图总览 {sheet}（发之前自己看一遍）')
        return []
    n = sum(len(x['描述']) for x in todo)
    say(f'生成占位图：{len(todo)} 个镜头、{n} 张（Codex 生图，一个镜头一次、同组一次出齐；同时跑 {min(workers, len(todo))} 个）…')
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
        res = list(ex.map(lambda x: gen_one(job, x, work), todo))
    bad = [(sid, miss, log) for sid, miss, log in res if miss]
    for sid, miss, log in res:
        if not miss: say(f'  {sid}：好了')
    if bad:
        die('这些镜头的占位图没生成出来：' + '；'.join(f"{sid} 缺 {'、'.join(p.name for p in miss)}（日志 {log}）" for sid, miss, log in bad)
            + '\n生图失败也算一次额度；看日志后同一条命令重跑（已有的不重出），或把这个镜头改成动画／别的素材')
    sheet = overview(job)
    if sheet: say(f'占位图总览 {sheet}：【先自己看一遍再发】逐张对口播——画的是不是说的那个东西、有没有画错（国标插座画成英式方孔这类）、有没有字和水印；不对的 --shots <镜头> --force 重出，重出还不对就写进卡片的 --assume')
    say('占位图都生成好了：接着 beats → anim-assemble（它们按 实拍/占位图/ 里的图排进去）。用户给了真素材后：把这个镜头的 "占位图" 删掉，'
        '换成 "实拍": 路径 或 "图片": 路径，再 beats → anim-assemble → pip → 建新一版审核线')
    return [x['id'] for x in todo]


def overview(job: Job, cols=4, cell=480):
    """把全部占位图拼成一张总览（实拍/占位图/总览.jpg），AI 发之前自己看一遍（2026-11-23 初稿模式实测：全是占位图时没有“看图自查”这一步）。
    每格左上角烧上镜头名。拼不出来（没有图、ffmpeg 出错）返回 None，不拦。"""
    files = [(x['id'], p) for x in placeholder_shots(job) for p in x['文件'] if p.is_file()]
    if not files: return None
    out = job.dir.joinpath(*PH_DIR, '总览.jpg'); rows = -(-len(files) // cols)
    cmd = ['ffmpeg', '-v', 'error', '-y']
    for _, p in files: cmd += ['-i', str(p)]
    fl = ''.join(f'[{i}:v]scale={cell}:{cell}:force_original_aspect_ratio=decrease,pad={cell}:{cell}:(ow-iw)/2:(oh-ih)/2:white[v{i}];' for i in range(len(files)))
    lay = '|'.join(f'{(i % cols) * cell}_{(i // cols) * cell}' for i in range(len(files)))
    fl += ''.join(f'[v{i}]' for i in range(len(files))) + (f'xstack=inputs={len(files)}:layout={lay}:fill=white[o]' if len(files) > 1 else 'copy[o]')
    try:
        r = subprocess.run(cmd + ['-filter_complex', fl, '-map', '[o]', '-frames:v', '1', '-q:v', '3', str(out)], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired): return None
    if r.returncode or not out.is_file(): return None
    (out.with_name('总览_顺序.txt')).write_text('总览里从左到右、从上到下（每行 %d 张）：\n' % cols + '\n'.join(f'{i + 1}. {sid}  {p.name}' for i, (sid, p) in enumerate(files)) + '\n', encoding='utf-8')
    return out


# ───────────────────────── 最终闸：整片导出前，用户在审片台点过审核线“这一栏定了” ─────────────────────────
GATE_WORDS = ('整片导出只认用户在审片台上的确认（2026-11-22 用户定“先做出来再改”：前面都不问、先做成初稿，只在导出、上传前留闸）：'
              '请用户在达芬奇里看完审核线，在审片台审片栏点栏头的“定稿”。AI 不能替用户点，对话里转述的“可以了”也不算。')


def export_gate(job: Job, name=None):
    """整片导出前：审片台“审核线”栏用户点过“这一栏定了”，而且之后内容没变（换了审核线、换轮、有新意见都算变）。
    返回记进导出记录的 {via, column, at}；没有就停。只读审片台数据，不改。"""
    from . import board
    review = job.get('review_timeline')
    if name and review and name != review:
        die(f'要导出的「{name}」不是审片台上用户定的那条审核线（现在是「{review}」）：用户定的是哪一条就导哪一条。' + GATE_WORDS)
    try: info = board.settle_info(job).get('审核线') or {}
    except Exception as e:      # 审片台数据读不了：不能当成确认过
        die(f'读不了审片台的确认记录（{type(e).__name__}：{e}）。' + GATE_WORDS)
    if info.get('state') == 'ok':
        return {'via': '审片台：用户点了审片栏“定稿”', 'column': '审核线', 'at': info.get('at')}
    why = info.get('why') or ''
    had = (board.load(job).get('settled') or {}).get('审核线')
    die(('用户之前点过“定稿”，但之后审核线这一栏的内容变了（换了审核线、换轮或有新意见），要重新点。' if had else '用户还没在审片台点审片栏“定稿”。')
        + (f'现在还不能点的原因：{why}。' if why else '') + GATE_WORDS + '只想试导几秒用 --seconds（最多 30 秒）。')


# ───────────────────────── 五项初稿做到哪 ─────────────────────────
def five(job: Job):
    """新片子任务的五项（口播剪辑、动画、审核线、封面、上传文案）初稿各做到哪：[(项, 做了没, 说明/下一步)]。status 打印它。"""
    from . import board
    d = job.dir; root = next((q for q in d.parents if (q / '项目.json').is_file()), None)
    try: items = board.live(board.load(job)['items'])
    except Exception: items = []
    cols = {c: [x for x in items if x['column'] == c] for c in board.COLUMNS}
    out = []
    mv = machine_voice(job)
    out.append(('口播剪辑', bool(job.get('main_timeline')), '机器配音占位（没有口播录像）' if mv else f"主线 {job.get('main_timeline', '')}"))
    shots = [s for s in (rj(job.p('分镜.json'))['shots'] if job.p('分镜.json').exists() else []) if s['kind'] == 'B']
    ph = placeholder_shots(job); lack = [x['id'] for x in ph if not all(x['有'])]
    b = job.p('合成', 'B肉.mp4').exists()
    out.append(('动画和画面', b and not lack, (f"{len(shots)} 个画面镜头" + (f"，其中 {len(ph)} 个是占位图" if ph else '')) if b and not lack else
                (f"占位图还缺：{'、'.join(lack)} → placeholder-images" if lack else '还没总装：写分镜 → beats → 出动画／占位图 → anim-assemble')))
    rv = job.get('review_timeline')
    out.append(('审核线', bool(rv), f'{rv}（审片台审片栏）' if rv else '还没建：check → review（建好自动打开审片台）'))
    covers = sorted((root / '03_成片' / 'cover').glob('*.*')) if root and (root / '03_成片' / 'cover').is_dir() else []
    out.append(('封面', bool(cols['封面'] or covers), f"审片台“封面”栏 {len(cols['封面'])} 项" + (f"，03_成片/封面 {len(covers)} 张" if covers else '') if (cols['封面'] or covers) else
                '还没做：thinkcut-cover 出 3 个方向、AI 先挑一个出齐三种比例 → workbench --publish 封面'))
    desc = tags = None
    if root:
        try: meta = rj(root / '项目.json'); desc, tags = meta.get('description'), meta.get('tags')
        except (ValueError, OSError): pass
    out.append(('上传文案', bool(desc and tags), '简介、标签已写进 项目.json（审片台发布栏能直接改）' if desc and tags else
                '还没写：AI 写一版简介和标签 → project.py copy-apply 写进 项目.json（不用等用户过目，他在发布栏直接改）'))
    return out


def report(job: Job):
    """status 末尾打印：五项初稿、占位、最终闸现在的状态。"""
    rows = five(job)
    say('\n【初稿五项】（2026-11-22 用户定：接到新任务五项一起做成初稿、发到审片台，中间不问；做完挂 workbench --wait 等用户边看边改）')
    for name, ok, note in rows: say(f"{'✓' if ok else '·'} {name}：{note}")
    left = [n for n, ok, _ in rows if not ok]
    say('初稿还差：' + ('、'.join(left) if left else '都有了（确认审片台开着、workbench --wait 挂着）'))
    for p in placeholders(job): say(f"占位：{p['text']}")
    for x in assumptions(job): say(f'全片假设：{x}')
    if job.p('分镜规格.json').exists():
        try:
            for x in animation_notes(rj(job.p('分镜规格.json'))): say('【注意】' + x)
        except (ValueError, OSError): pass
    for x in resolve_notes(job): say('达芬奇：' + x)
    say('自查：AI 没法真的把整条从头播一遍听——做 check、subs-check 总览、占位图总览、每条花字和下划线出现后抽一帧看；汇报里写明“没有真听”')
    try:
        from . import board
        ok = board.settle_info(job).get('审核线', {}).get('state') == 'ok'
    except Exception: ok = False
    say('最终闸：' + ('用户已在审片台点了审片栏“定稿”，可以整片导出' if ok else '用户还没在审片台点审片栏“定稿”——整片 4K 导出、上传存草稿前会被拦（AI 不能替用户点）'))
