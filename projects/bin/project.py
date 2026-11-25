#!/usr/bin/env python3
"""标准项目、本地项目.json和蚁小二内部草稿；没有发布或删除命令。"""
import argparse
import copy
from datetime import datetime
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
AI_ROOT = ROOT.parent      # 程序目录（本仓库的上一级）
sys.path.append(str(AI_ROOT / 'bin')); import thinkcut_config      # noqa: E402  配置档：公共层＋每个 IP 一层（2026-11-24）
CFG = thinkcut_config.load()        # 默认 IP 的配置；某条片子用 thinkcut_config.load(project=d)
VIDEO_ROOT = CFG.path('片子文件夹')      # 片子放哪：配置档里写（相对程序目录，和以前按本仓库位置推的一样）
FINISHED = CFG.get('做完的片子子文件夹')      # 2026-11-17 用户定：做完的项目移进 片子文件夹/finish，根目录只留正在做的
MATERIAL_CATEGORIES = ('A roll', 'B roll', '音乐')
DIRS = tuple('01_原始素材/' + kind for kind in MATERIAL_CATEGORIES) + ('02_制作/临时', '03_成片/封面', '03_成片/字幕', '04_达芬奇归档', '05_上传记录')


def project_guidance(d):
    files = {
        'AGENTS.md': '''# 本视频项目的协作约定

先读 `{ai}/projects/AGENTS.md`，再读这个 IP 的规矩（`python3 {ai}/bin/thinkcut_config.py rules --project .`）。片子只用 thinkcut- 开头的技能：thinkcut-project（建项目、整理素材、存档）、thinkcut-edit（剪辑全流程）、thinkcut-cover（封面）、thinkcut-upload（上传蚁小二草稿）、thinkcut-feedback（反馈）。

- 本文件夹名称与项目.json 的 name 完全一致。标题、封面文字、简介、标签、素材路径和目标账号都直接填写在项目.json，不使用飞书文档。
- 开始操作前读取项目.json；素材和目标账号未确认时不上传。
- 新剪辑产物放02_制作下；a-roll内部的审核、交付结构保持原样。thinkcut-edit的片子DIR放02_制作/自动剪辑，不再另建散落工作目录。
- 原始素材按A roll、B roll、音乐分类，用“内容_用途_序号”命名；先看画面、听音频或核对既有转写，不凭原文件名猜内容。01_原始素材/素材清单.json记录原名、新路径、内容、用途和摘要，素材目录.md供人查找。旧引用须核对后再改名，归档媒体与旧交付包不批量重命名。
- 最终文案：定稿（标题、封面文字、口播正文，有简介标签也写上；不带时间戳）存 03_成片/<项目名>.md（文件名和本项目文件夹名一样，2026-11-22 用户定和成片放一起），每条片子一份；存档和封存时会检查，缺了会拦。文案里用「## 标题」「## 封面文字」「## 口播正文」三个小标题分开，各阶段用 project.py script DIR --part 标题|封面|口播 按用途取。
- 确认后的成片、三种封面和SRT放03_成片；不覆盖用户手调，不重新生成既有字幕。
- 最终视频文件名为YYMMDD_项目名加原扩展名，例如261018_项目名.mov，按UTC+8导出完成日期命名。封存交付包沿用此名；复制或上传时不更新时间。
- 定稿收尾：project.py register-final 登记成片、字幕、工程和最终时间线 → archive 存档 → archive-stills 出对比图人看确认 → seal 封存；上传读取封存交付包（thinkcut-upload）。
- 做封面先用cover-brief读取项目.json的封面文字，按thinkcut-cover制作后用cover-register登记；封面文字不代写。
- 上传只保存蚁小二内部草稿；正式发布由本人在客户端完成。使用yxer-safe读取本机钥匙串，禁止调用yxer publish或把Key写进文件。
- 公共音效在 {ai}/素材库/音效素材。旧资料保留在02_制作/历史资料，迁移记录可供撤回；未经具体授权不删除。
- **随手记反馈**（2026-11-11）：用户每纠正一处、返修一处、新定一条规矩，或 AI 自己查出问题、工具报错绕路，马上在 02_制作/反馈记录.md 末尾加一行：`时间 | 环节 | 谁先发现（用户/AI自查/工具拦下） | 发生了什么（用户原话） | 当时怎么处理`；用户在时间线或网页完整看完一轮提意见时，先加一行 `## 第 N 轮审核`。不用问用户、不发给用户。用户说“反馈”或片子收尾时用 thinkcut-feedback。
- 做这条片子的会话不改 {ai} 里的任何东西；要改的用 thinkcut-feedback 交给“架构审查”。
''',
        'CLAUDE.md': '先读取本目录AGENTS.md，并遵守其中的共享工具、存档与内部草稿边界。\n'
    }
    files['AGENTS.md'] = files['AGENTS.md'].replace('{ai}', str(AI_ROOT))      # 程序目录按实际位置写（使用版就是原来那个路径）
    for name, content in files.items():
        path = d / name
        if not path.exists():
            path.write_text(content)


THINKCUT_PLATFORMS = tuple(CFG.get('发布.平台'))   # 默认 IP 全部分发的平台（2026-11-01）；某条片子按它自己的 IP：platforms_of(d)


def platforms_of(d):
    return tuple(thinkcut_config.load(project=d).get('发布.平台'))


VIDEO_EXT = ('.mp4', '.mov', '.m4v', '.mxf', '.mkv', '.avi')     # 认“已有成片”只看视频文件（03_成片 里还放定稿文案、封面、字幕）


def name_title_warning(meta):
    """项目名要等于“6 位编号＋空格＋发布标题”（261122 反馈单新3，用户：“项目名字要跟标题名字一样”）。不一样返回一句提醒，一样返回 None。"""
    name, title = str(meta.get('name') or ''), str(meta.get('title') or '').strip()
    if not title: return None
    bare = re.sub(r'^\d{6}\s+', '', name).strip()
    squash = lambda x: re.sub(r'\s+', '', x)
    if squash(bare) == squash(title): return None
    return (f'【注意】项目名「{name}」和发布标题「{title}」不一样：项目名要等于“编号＋空格＋发布标题”。'
            f'标题定了就改名：python3 {Path(__file__).resolve()} rename <项目目录> "{name[:6]} {title}"')


def warn_unused_fields(meta):
    """项目.json 里程序不认的旧字段：提醒，不悄悄忽略（2026-11-21：261117 的 platform_copy 没有任何程序读它，上传用的是顶层 description）"""
    if meta.get('platform_copy'):
        print('【注意】项目.json 里的 platform_copy 程序不认，里面的标题、简介、标签不会上传。各平台单独的简介请用 copy-apply 的 platform_descriptions（写进 platform_extra.<平台>.description）。', file=sys.stderr)


def platform_gaps(accounts, targets=None, platforms=None):
    """对照默认 6 个平台，缺哪个、为什么（2026-11-12《水电》：少了视频号，预检和回执都没说）"""
    gaps = {}
    for name in (platforms or THINKCUT_PLATFORMS):
        rows = [a for a in accounts if a.get('platformName') == name]
        if not rows: gaps[name] = '蚁小二里没绑定这个平台的账号'
        elif not any(a.get('status') == 1 for a in rows): gaps[name] = '账号失效（状态 ' + '、'.join(str(a.get('status')) for a in rows) + '），在蚁小二客户端重新登录'
        elif targets is not None and not any(t.get('platform') == name and t.get('enabled') for t in targets): gaps[name] = '项目.json 里没启用'
    return gaps


def write_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.' + path.name, suffix='.tmp', delete=False) as f:
        tmp = Path(f.name)
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text())


HEARTBEAT = 60               # 传文件时每隔几秒报一次“还在传”
COMMAND_TIMEOUT = 300        # 普通命令最多等几秒（和 yxer-safe 相同，环境变量 THINKCUT_YXER_TIMEOUT 调）
UPLOAD_TIMEOUT = 6 * 3600    # 传文件最多等几秒（环境变量 THINKCUT_YXER_UPLOAD_TIMEOUT 调）
GRACE = 30                   # yxer-safe 自己按同一个时限中止并说明原因；这里多等这几秒，只在它也没反应时兜底


def size_text(n):
    return f'{n / 1024 ** 3:.2f} GB' if n >= 1024 ** 3 else f'{n / 1024 ** 2:.1f} MB' if n >= 1024 ** 2 else f'{max(n, 0) / 1024:.0f} KB'


def minutes_text(seconds):
    return f'{seconds / 60:.0f} 分钟' if seconds >= 90 else f'{seconds:.0f} 秒'


def reply_json(text):
    """命令输出里的 JSON。前后夹了别的提示行（如 yxer-safe 的“已忽略…”）时，取大括号那一段。"""
    try:
        return json.loads(text)
    except ValueError:
        i, j = text.find('{'), text.rfind('}')
        if i < 0 or j < i: raise
        return json.loads(text[i:j + 1])


def command(args):
    """运行命令并返回它输出的 JSON。传文件（yxer-safe upload）时边等边报“还在传”；等太久就中止并说明原因。
    2026-11-13 审查：以前传 2.7GB 全程没有输出，卡住了也无限等。"""
    args = [str(x) for x in args]
    uploading = args[1:2] == ['upload']
    name, default = ('THINKCUT_YXER_UPLOAD_TIMEOUT', UPLOAD_TIMEOUT) if uploading else ('THINKCUT_YXER_TIMEOUT', COMMAND_TIMEOUT)
    raw = os.environ.get(name, '')
    try:
        limit = float(raw) if raw.strip() else default
        if limit <= 0: raise ValueError
    except ValueError:
        raise RuntimeError(f'环境变量 {name} 要写成大于 0 的秒数，现在是「{raw}」。改对或去掉它再试。')
    limit += GRACE
    started = time.monotonic()
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        while True:
            waited = time.monotonic() - started
            try:
                out, err = proc.communicate(timeout=max(0.05, min(HEARTBEAT, limit - waited))); break
            except subprocess.TimeoutExpired:
                waited = time.monotonic() - started
                if waited >= limit:
                    raise RuntimeError(f'{args[0]} {" ".join(args[1:3])} 等了 {minutes_text(waited)}还没结束，已中止。先检查网络和蚁小二服务；确实需要更久时把环境变量 {name} 设成更大的秒数再试。')
                if uploading: print(f'还在传 {Path(args[2]).name if len(args) > 2 else ""}，已 {minutes_text(waited)}', flush=True)
    except BaseException:    # 超时、Ctrl-C：连同它启动的子进程一起结束，不留在后台继续传
        if proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except OSError: proc.kill()
            proc.communicate()
        raise
    p = subprocess.CompletedProcess(args, proc.returncode, out, err)
    for line in (p.stderr or '').splitlines() if p.returncode == 0 else ():
        if line.startswith('提示：'): print(line, file=sys.stderr)      # 成功时也把 yxer-safe 的提示（如“已忽略…”）带出来
    try:
        obj = reply_json(p.stdout if p.returncode == 0 else p.stderr)
    except ValueError:
        raise RuntimeError(f'命令未返回 JSON：{args[0]}（退出码 {p.returncode}）' + ''.join('\n' + t.strip()[-500:] for t in (p.stderr, p.stdout) if t and t.strip()))
    if p.returncode or not obj.get('ok'):
        err = obj.get('error', {})
        raise RuntimeError(f'{args[0]}：{err.get("message", err)}；{err.get("hint", "")}')
    return obj


def load(directory):
    d = Path(directory).expanduser().resolve()
    meta = read_json(d / '项目.json')
    if d.name != meta['name']:
        raise RuntimeError('文件夹名与项目名不一致；请先核对，不会自动改名。')
    return d, meta


def asset_path(d, value):
    if not value or Path(value).is_absolute():
        raise RuntimeError('素材路径必须为项目内的相对路径。')
    p = (d / value).resolve()
    if not p.is_relative_to(d) or not p.is_file():
        raise RuntimeError(f'素材不存在或超出项目目录：{value}')
    return p


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def check_final_video_name(value, project_name):
    name = Path(value)
    try:
        match = re.fullmatch(r'([0-9]{6})_(.+)', name.stem)
        if not name.suffix or not match or match[2] != project_name:
            raise ValueError
        datetime.strptime('20' + match[1], '%Y%m%d')
    except ValueError:
        raise RuntimeError('最终视频文件名须为YYMMDD_项目名加扩展名；项目名须与文件夹及项目.json的name完全相同，日期使用UTC+8导出完成日期，并同步项目.json素材路径。')
    return name.name


def initialize_materials(d):
    folder = d / '01_原始素材'
    for kind in MATERIAL_CATEGORIES:
        (folder / kind).mkdir(parents=True, exist_ok=True)
    index = folder / '素材清单.json'
    if not index.exists():
        write_json(index, {'schema_version': 1, 'items': []})
    catalog = folder / '素材目录.md'
    if not catalog.exists():
        catalog.write_text('# 原始素材目录\n\n按A roll、B roll、音乐分类。文件名使用“内容_用途_序号”加原扩展名；核对内容后登记，不凭原名猜用途。\n\n| 当前路径 | 原文件名 | 内容与用途 |\n| --- | --- | --- |\n\nAI索引：素材清单.json。收到新素材后同步维护两份清单；生成的动画和过程产物放02_制作。\n')


def check_materials(d):
    folder = d / '01_原始素材'
    index = read_json(folder / '素材清单.json')
    if index.get('schema_version') != 1 or not isinstance(index.get('items'), list):
        raise RuntimeError('原始素材清单格式错误。')
    registered = set()
    for item in index['items']:
        category = item.get('category')
        if category not in MATERIAL_CATEGORIES:
            raise RuntimeError('素材分类必须使用标准分类。')
        values = [item.get('content'), item.get('purpose'), item.get('original_name')]
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() or any(c in v for c in ('/', '\\', '\n', '\r')) for v in values):
            raise RuntimeError('素材清单须记录已核实的内容、用途和原文件名。')
        sequence = item.get('sequence')
        if type(sequence) is not int or sequence < 1:
            raise RuntimeError('素材序号须为正整数。')
        path = asset_path(d, item.get('path', ''))
        expected = f'{item["content"]}_{item["purpose"]}_{sequence:02d}' + Path(item['original_name']).suffix
        if not Path(item['original_name']).suffix or path.parent != folder / category or path.name != expected:
            raise RuntimeError('素材路径或文件名与清单不一致：' + item['path'])
        relative = str(path.relative_to(d))
        if relative in registered:
            raise RuntimeError('素材清单有重复路径：' + relative)
        registered.add(relative)
        if digest(path) != item.get('sha256'):
            raise RuntimeError('原始素材摘要不一致：' + relative)
    ignored = {folder / '素材清单.json', folder / '素材目录.md'}
    actual = {str(p.relative_to(d)) for p in folder.rglob('*') if p.is_file() and p not in ignored and p.name != '.DS_Store' and not p.name.startswith('._')}
    if actual != registered:
        raise RuntimeError('原始素材存在未登记文件：' + '、'.join(sorted(actual - registered)))
    return {'state': 'materials_verified', 'files': len(registered), 'categories': sorted({x['category'] for x in index['items']})}


SCRIPT_DIR = '03_成片'          # 2026-11-22 用户定：定稿文案（标题、封面文字、口播正文、简介、标签）和成片放一起，AI 找标题、封面字先来这里
OLD_SCRIPT_DIR = '02_制作'      # 2026-11-17～11-21 放这里；还没搬的老项目照读（script-move 搬过去）


def script_path(d):
    """项目文案放哪：03_成片/<项目名>.md（文件名和项目名一样）。还没搬的老项目只有 02_制作/<项目名>.md 时读那份。"""
    d = Path(d); new = d / SCRIPT_DIR / (d.name + '.md'); old = d / OLD_SCRIPT_DIR / (d.name + '.md')
    return old if old.is_file() and not new.exists() else new


def script_rel(d):
    """给人看的位置：03_成片/<项目名>.md（老项目没搬时是 02_制作/…）。"""
    f = script_path(d); return f'{f.parent.name}/{f.name}'


def move_script(d, meta=None, recycle=None):
    """script-move：把项目文案从 02_制作/<项目名>.md 搬到 03_成片/<项目名>.md（2026-11-22 用户：“把过去所有项目的MD文档都放在里面”）。
    文案里缺的 标题／封面文字／简介／标签 用 项目.json 里登记的定稿值补上（上传、封面都用的这几个）；口播正文一字不动。
    搬之前核对：新文件读出的口播正文和原来一样、标题和封面文字和 项目.json 一致，不一样就停、什么都不动。原文件挪进回收站，不永久删。"""
    import script_doc
    d = Path(d); meta = meta if meta is not None else load(d)[1]
    new = d / SCRIPT_DIR / (d.name + '.md'); old = d / OLD_SCRIPT_DIR / (d.name + '.md')
    if new.exists(): return {'结果': '已在 03_成片', '文件': str(new)}
    if not old.is_file(): return {'结果': '没有项目文案，没搬', '缺': f'{OLD_SCRIPT_DIR}/{old.name}'}
    text = old.read_text(encoding='utf-8'); before = script_doc.read(old)
    cover = [x for x in str(meta.get('cover_main') or '').splitlines() if x.strip()] + ([meta['cover_sub']] if str(meta.get('cover_sub') or '').strip() else [])
    fill = [('标题', meta.get('title')), ('封面文字', cover), ('简介', meta.get('description')), ('标签', meta.get('tags'))]
    fill = [(k, v) for k, v in fill if v and not before.get(k)]
    if not any(script_doc.H2.match(l) for l in text.splitlines()):
        # 老的纯口播文案（整份没有 ## 小标题，整份算口播正文）：补的小节放前面，原文整份放进「## 口播正文」
        lines = text.split('\n'); head = lines[0] if lines and lines[0].startswith('# ') else f'# {d.name}'
        body = '\n'.join(lines[1:] if lines and lines[0].startswith('# ') else lines).strip('\n')
        block = ''.join(f'## {k}\n\n' + ('\n'.join(v) if k == '封面文字' else '、'.join(v) if isinstance(v, list) else str(v)).strip() + '\n\n' for k, v in fill)
        out = f'{head}\n\n{block}## 口播正文\n\n{body}\n'
    else:
        out = None
    new.parent.mkdir(parents=True, exist_ok=True)
    tmp = new.with_name('.' + new.name + '.搬家中')
    tmp.write_text(out if out is not None else text, encoding='utf-8')
    if out is None:
        for k, v in fill:          # 新式文案：缺的小节插在「## 口播正文」前面（封面文字一行一行写）
            if k == '封面文字':
                t = tmp.read_text(encoding='utf-8'); ls = t.split('\n')
                at = next((i for i, l in enumerate(ls) if l.strip().startswith('## 口播正文')), len(ls))
                tmp.write_text('\n'.join(ls[:at]) + '\n## 封面文字\n\n' + '\n'.join(v) + '\n\n' + '\n'.join(ls[at:]), encoding='utf-8')
            else:
                script_doc.add_sections(tmp, [(k, v)])
    after = script_doc.read(tmp); squash = lambda xs: ''.join(xs or []).replace(' ', '').replace('，', '').replace(',', '')
    bad = []
    if (after.get('口播正文') or '').strip() != (before.get('口播正文') or '').strip(): bad.append('口播正文和原来不一样')
    if meta.get('title') and after.get('标题') != meta.get('title') and not before.get('标题'): bad.append('标题对不上 项目.json')
    if cover and squash(after.get('封面文字')) != squash(cover) and not before.get('封面文字'): bad.append('封面文字对不上 项目.json')
    if bad:
        tmp.unlink(); raise RuntimeError(f'{d.name}：搬家核对没过（{"、".join(bad)}），什么都没动')
    tmp.replace(new)
    rc = Path(recycle) if recycle else thinkcut_config.load(project=d).path('回收站') / thinkcut_config.load(project=d).path('片子文件夹').name / f"文案搬家_{datetime.now().strftime('%Y%m%d')}" / d.name
    rc.mkdir(parents=True, exist_ok=True); target = rc / old.name
    if target.exists(): target = rc / f"{old.stem}_{datetime.now().strftime('%H%M%S')}.md"
    old.replace(target)
    return {'结果': '已搬到 03_成片', '文件': str(new), '补上的小节': [k for k, _ in fill], '原文件': str(target)}


def check_script(d):
    """每条片子都存一份最终口播文案（2026-11-14 用户定）：存档、封存前检查。"""
    f = script_path(d)
    if not f.is_file() or not f.read_text(encoding='utf-8', errors='replace').strip():
        old = Path(d) / '02_制作' / '口播文案.md'
        raise RuntimeError(f'缺最终文案：把定稿（标题、封面文字、口播正文，不带时间戳）存成 {SCRIPT_DIR}/{f.name}（文件名和项目名一样），再存档。'
                           + ('现在有一份 02_制作/口播文案.md，是定稿的话把它挪过去并改成这个名字。' if old.is_file() else ''))


def show_script(d, part=None):
    """script 子命令：从项目文案里按用途取 标题／封面文字／口播正文（2026-11-20 用户定：各阶段按用途分类提取）。
    给了 part 只打这一类，认不出就停；不给就三类都打，缺的标明。"""
    import script_doc
    f = script_path(d)
    if not f.is_file():
        raise RuntimeError(f'缺项目文案 {SCRIPT_DIR}/{f.name}：把定稿全文存成这个文件（文件名和项目名一样），文案里用「## 标题」「## 封面文字」「## 口播正文」三个小标题分开。')
    parts = script_doc.read(f)
    text = lambda v: '\n'.join(v) if isinstance(v, list) else v
    if part:
        name = script_doc.ALIASES[part]
        if not parts[name]: raise RuntimeError(f'文案 02_制作/{f.name} 里认不出「{name}」：{script_doc.MISSING_HINT[name]}。')
        return '、'.join(parts[name]) if name == '标签' else text(parts[name])
    out = [f'文案：{f}']
    for name in script_doc.PARTS:
        if not parts[name]: out.append(f'【{name}】缺：{script_doc.MISSING_HINT[name]}'); continue
        out += [f'【{name}】' + (f'（{script_doc.count(parts[name])} 字）' if name == '口播正文' else ''), text(parts[name])]
    for name in script_doc.EXTRA:
        v = parts[name]
        out.append(f'【{name}】' + ('、'.join(v) if name == '标签' else v) if v else f'【{name}】{script_doc.MISSING_HINT[name]}')
    return '\n'.join(out)


def sync_script(d, meta):
    """script-sync：把项目文案 03_成片/<项目名>.md 里的「标题」「封面文字」写进 项目.json 的 title、cover_main（文案是准的，2026-11-21 用户：MD 里有了就用 MD 里的）。
    封面文字不分主副（2026-11-21）：几行都进 cover_main，cover_sub 不动。文案里没写的那项不动。出处：2026-11-23 初稿模式实测（新项目的标题、封面文字没有命令能写进去）。"""
    import script_doc
    f = script_path(d)
    if not f.is_file(): raise RuntimeError(f'缺项目文案 {SCRIPT_DIR}/{f.name}：先把文案存成这个文件（「## 标题」「## 封面文字」「## 口播正文」）。')
    parts = script_doc.read(f); changed = {}
    for key, value in (('title', parts['标题']), ('cover_main', '\n'.join(parts['封面文字'] or []))):
        if value and (meta.get(key) or '') != value:
            changed[key] = {'原来': meta.get(key) or '（空）', '现在': value}; meta[key] = value
    if changed: write_json(d / '项目.json', meta)
    missing = [n for n in ('标题', '封面文字') if not parts[n]]
    return {'写进项目.json': changed or '没有要改的（已经一样）', **({'文案里没写、没动': missing} if missing else {})}


def project_number(name):
    m = re.match(r'(\d{6})(?!\d)', name)
    return m.group(1) if m else None


def used_numbers(root, skip=None):
    root = Path(root)      # 正在做的和 finish 里做完的一起算，编号全局不重复
    places = [root, root / FINISHED] + ([root.parent] if root.name == FINISHED else [])
    return {n for place in places if place.is_dir() for n in (project_number(x.name) for x in place.iterdir() if x.is_dir() and x.name != skip) if n}


def free_name(name, root, skip=None, also=()):
    """项目编号（名字开头 6 位 YYMMDD）不能和同目录已有项目重复（2026-11-17 用户定）：
    撞号就按日期往后顺延一天（261130 → 261201），直到没被占用。also＝另外也算用过的编号（首页传回收站里删掉的片子的编号）。返回 (可用的名字, 被占用的原编号或 None)。"""
    from datetime import timedelta
    n = project_number(name); used = used_numbers(root, skip) | set(also)
    if not n or n not in used: return name, None
    try: day = datetime.strptime(n, '%y%m%d')
    except ValueError: raise RuntimeError(f'项目编号 {n} 已被占用，而且不是一个真实日期，没法自动顺延；请换一个编号。') from None
    while day.strftime('%y%m%d') in used: day += timedelta(days=1)
    return day.strftime('%y%m%d') + name[6:], n


def backup_dblist(why):
    import resolve_library
    f = resolve_library.PREFS / 'dblist.conf'
    if not f.exists(): return None
    backup = f.with_name(f"dblist.conf.bak-{why}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"); backup.write_bytes(f.read_bytes())
    return backup


def finish(directory, back=False, library=True):
    """做完的项目整个移进同目录的 finish 文件夹（back=True 是移回来接着做）。同一块盘上只是换位置，不复制、不删除。
    library=False：这条片子没登记过达芬奇库（首页新建、还没开项的），不碰库列表、不用退出达芬奇（首页用，2026-11-24）。"""
    import resolve_library
    d, meta = load(directory); inside = d.parent.name == FINISHED
    if back and not inside: raise RuntimeError(f'这个项目不在 {FINISHED} 里，不用移回。')
    if not back and inside: raise RuntimeError(f'这个项目已经在 {FINISHED} 里了。')
    target = (d.parent.parent if back else d.parent / FINISHED) / d.name
    if target.exists(): raise RuntimeError(f'目标位置已有同名文件夹，不覆盖：{target}')
    if library and resolve_library.resolve_running():
        raise RuntimeError('达芬奇开着：它退出时会覆盖库列表。先保存并退出达芬奇，再移动项目。')
    notes = []
    if not back:
        if not meta['assets'].get('video'): notes.append('项目.json 里还没登记成片')
        if not any(d.rglob('交付包')): notes.append('还没有封存的交付包')
        if not script_path(d).is_file(): notes.append(f'缺最终文案 {SCRIPT_DIR}/{d.name}.md')
    backup = backup_dblist('移动') if library else None
    target.parent.mkdir(exist_ok=True)
    d.rename(target)
    state = resolve_library.register(target, running=False) if library else '没登记过，不用改'
    return {'项目': meta['name'], '原位置': str(d), '新位置': str(target), '达芬奇库列表': state, '库列表备份': str(backup) if backup else '原来没有库列表',
            '提醒': notes + ['项目里记的绝对路径（达芬奇工程里的素材链接、自动剪辑的 job.json 等）还指着原位置；要接着改这条片子，先用 reopen 移回来。'] if not back else ['已移回原来的根目录，项目里记的路径恢复可用。']}


def _same(a, b):
    try: return os.path.samefile(a, b)
    except OSError: return False


RENAME_SKIP = ('反馈记录.md', 'AGENTS.md', 'CLAUDE.md')      # 用户原话和协作约定，改名不碰


def replace_old_name(text, old, new):
    """把 text 里的旧名换成新名。新名包含旧名时（如 灯 → 灯光），本来就是新名的地方不再换（不出现“灯光光”）。"""
    k = new.find(old); pre, post = (new[:k], new[k + len(old):]) if k >= 0 else ('', '')
    out, i = [], 0
    while (j := text.find(old, i)) >= 0:
        already = k >= 0 and j >= len(pre) and text[j - len(pre):j] == pre and text.startswith(post, j + len(old))
        out += [text[i:j], old if already else new]; i = j + len(old)
    return ''.join(out) + text[i:]


def rename(directory, new_name, user_ok=False, library=True):
    """改项目编号/名称，一次做完：文件夹名、项目.json 的 name、项目说明里的旧名、其它文案标题行里的旧名、口播文案文件名、达芬奇库列表（删旧登记、登记新路径）。
    文件夹改名之后任何一步出错，都恢复原样再报错（2026-11-20 审查）。
    library=False：这条片子没登记过达芬奇库（首页新建、还没开项的），不碰库列表、不用退出达芬奇（首页用，2026-11-24）。"""
    import resolve_library
    d, meta = load(directory); old = d.name; target = d.parent / new_name
    if Path(new_name).name != new_name or new_name in ('', '.', '..') or ':' in new_name:
        raise RuntimeError('新名字不能包含路径或英文冒号。')
    if new_name == old: raise RuntimeError('新名字和现在的一样。')
    if target.exists() and not _same(target, d): raise RuntimeError(f'「{new_name}」已经存在，不覆盖。')      # 只改大小写：不分大小写的盘上是同一个文件夹，放行
    n = project_number(new_name)
    if n and n in used_numbers(d.parent, skip=old):
        raise RuntimeError(f'编号 {n} 已被另一个项目占用；下一个没被占用的是 {free_name(new_name, d.parent, skip=old)[0][:6]}。')
    if library and resolve_library.resolve_running():
        raise RuntimeError('达芬奇开着：它退出时会覆盖库列表。先保存并退出达芬奇，再改名。')
    lst = resolve_library.PREFS / 'dblist.conf'
    for line in lst.read_text(encoding='utf-8').splitlines() if library and lst.exists() else []:
        part = line.split(':')
        if len(part) > 1 and part[0] == new_name and part[1] != str(resolve_library.folder(target)) and Path(part[1]).exists():
            raise RuntimeError(f'达芬奇里已有同名项目库「{new_name}」指向另一个还在的文件夹（{part[1]}）；两个片子不能同名，换个名字。')
    has = []
    if any(resolve_library.folder(d).rglob('Project.db')): has.append('达芬奇工程')
    if any((d / '02_制作').rglob('job.json')): has.append('剪辑工程')
    # 261122 反馈单新1：03_成片 里放着定稿文案（2026-11-22 起）、空的 字幕/封面 文件夹，不算成片；只认登记的成片和视频文件
    if meta['assets'].get('video') or any(x.is_file() and not x.name.startswith('.') and x.suffix.lower() in VIDEO_EXT for x in (d / '03_成片').rglob('*')): has.append('成片')
    if any(x for x in (d / '04_达芬奇归档').glob('*') if not x.name.startswith('.')) or any(d.rglob('交付包')): has.append('存档或交付包')
    if has and not user_ok:
        raise RuntimeError(f"这个项目已有{'、'.join(has)}：工程里的素材路径、剪辑工程 job.json 里的绝对路径、成片文件名、交付包都带着旧名字，改名会让它们对不上。先问用户；用户同意后加 --user-ok 再跑，改完还要逐项核对。")
    original_json = (d / '项目.json').read_bytes()
    backup = backup_dblist('改名') if library else None
    d.rename(target)
    script = next((target / x / (old + '.md') for x in (SCRIPT_DIR, OLD_SCRIPT_DIR) if (target / x / (old + '.md')).is_file()), target / SCRIPT_DIR / (old + '.md'))
    renamed_to = script.with_name(new_name + '.md'); moved_script = False; originals = {}
    try:
        meta['name'] = new_name; write_json(target / '项目.json', meta)
        if script.is_file() and (not renamed_to.exists() or _same(renamed_to, script)):      # 文案的文件名跟项目名走（在哪个文件夹就在哪个文件夹里改名）
            script.rename(renamed_to); moved_script = True
        changed, mentions = [], []
        for doc in sorted([*target.glob('*.md'), *target.glob('*.txt'), *(target / '02_制作').glob('*.md'), *(target / '02_制作').glob('*.txt')]):
            try: text = doc.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError): continue
            new_text = text
            if doc.name == '项目说明.md' and doc.parent == target:
                new_text = replace_old_name(text, old, new_name)
            elif doc.name not in RENAME_SKIP:      # 其它文件只改标题行，正文（口播文案、用户原话）不动
                new_text = ''.join(replace_old_name(l, old, new_name) if l.startswith('#') else l for l in text.splitlines(keepends=True))
            if new_text != text:
                originals[doc] = text; doc.write_text(new_text, encoding='utf-8'); changed.append(str(doc.relative_to(target)))
            if replace_old_name(new_text, old, new_name) != new_text: mentions.append(str(doc.relative_to(target)))
        removed = resolve_library.unregister(old, resolve_library.folder(d)) if library else 0
        state = resolve_library.register(target, running=False) if library else '没登记过，不用改'
    except BaseException as exc:
        failed = []
        def undo(what, step):
            try: step()
            except BaseException as e: failed.append(f'{what}（{e}）')
        def restore_list():
            if backup: lst.write_bytes(backup.read_bytes())
            else: lst.unlink(missing_ok=True)
        if library: undo('达芬奇库列表', restore_list)
        for doc, text in originals.items(): undo('文档 ' + doc.name, lambda doc=doc, text=text: doc.write_text(text, encoding='utf-8'))
        if moved_script: undo('口播文案文件名', lambda: renamed_to.rename(script))
        undo('项目.json 的 name', lambda: (target / '项目.json').write_bytes(original_json))
        undo('文件夹名', lambda: target.rename(d))
        if not failed: raise RuntimeError(f'改名失败，已恢复原样：{exc}') from exc
        where = d if d.exists() else target if target.exists() else '找不到（原名、新名都不在）'
        raise RuntimeError(f'改名失败：{exc}。恢复时这几项没恢复成功：{"；".join(failed)}。现在项目文件夹在：{where}；库列表备份在：{backup or "原来没有库列表"}。'
                           '先别再跑别的命令，把这段原样告诉用户，逐项手动核对。') from exc
    return {'旧名': old, '新名': new_name, '项目': str(target), '改了旧名的文件': changed, '还提到旧名、没自动改的文件': mentions,
            '达芬奇库列表': f'删掉旧登记 {removed} 行，新库{state}', '库列表备份': str(backup) if backup else '原来没有库列表', '还要人工核对': has or '没有'}


def initialize(d, adopt=False, register=True, ip=None):
    """register=False：不登记达芬奇项目库（审片台首页“新建片子”用：程序自己建文件夹，达芬奇的事等 AI 来做，2026-11-24）。"""
    d = Path(d).expanduser()
    if d.exists() and not adopt:
        raise RuntimeError('目录已存在；维护已有项目请用 adopt。')
    if not d.exists() and adopt:
        raise RuntimeError('adopt 只用于已有目录。')
    d = d.resolve()
    if (d / '项目.json').exists():
        raise RuntimeError('已有项目索引，不覆盖。')
    meta = read_json(ROOT / 'templates/project.json'); meta['name'] = d.name
    cfg = thinkcut_config.load(ip=ip, project=d)      # 这条片子属于哪个 IP 写进 项目.json，以后各步按它读配置
    meta['ip'] = cfg.ip; meta['youtube_category'] = cfg.get('发布.youtube分类'); meta['original'] = cfg.get('发布.原创')
    d.mkdir(parents=True, exist_ok=True)
    for part in DIRS:
        (d / part).mkdir(parents=True, exist_ok=True)
    initialize_materials(d)
    project_guidance(d)
    write_json(d / '项目.json', meta)
    import resolve_library      # 2026-10-29：达芬奇工程放这个片子自己的项目库（02_制作/达芬奇工程），本机什么都不存
    try:
        if register: print('达芬奇项目库「' + d.name + '」：' + resolve_library.register(d))
    except RuntimeError as exc:
        # 2026-11-23 初稿模式实测：达芬奇开着登记不了库不用停、不用让用户退出——做到口播交付时 resolve-open 会把工程先建在当前开着的库里
        print('达芬奇项目库这次没登记（' + str(exc) + '）。初稿模式下不用管、不用问用户：口播交付（deliver）时会自动运行 project.py resolve-open，把工程先建在达芬奇当前开着的库里，存档后再迁回。', file=sys.stderr)
    if not (d / '项目说明.md').exists(): (d / '项目说明.md').write_text(f'# {d.name}\n\n- 标题、封面文字、简介、字幕和素材路径填写在项目.json。\n- 01 原始素材；02 制作（达芬奇工程在 02_制作/达芬奇工程，是这个片子自己的项目库，库名就是文件夹名）；03 成片；04 达芬奇归档；05 上传记录。\n- 原始素材分A roll、B roll、音乐；文件名写清内容和用途，目录见01_原始素材/素材目录.md。\n- 最终视频文件名为YYMMDD_项目名加原扩展名，按UTC+8导出完成日期命名；封存与上传保留同名。\n- 正式发布由本人在蚁小二客户端确认。\n- 原始文件默认保留；不要直接移动达芬奇正在引用的媒体。\n')
    return d, meta


@contextmanager
def upload_lock(d):
    # 锁由系统随进程退出释放；保留空锁文件，避免删锁导致另一个进程锁到不同inode。
    with (d / '.上传.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一进程正在上传本项目，停止以免重复创建草稿。')
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def register_final(d, video, srt, project_name, timeline):
    """登记定稿（2026-10-24 审计：原来没有命令写 assets.video/subtitles 和 resolve.project/timeline，新片子会卡在存档）。"""
    def rel(x):
        q = Path(x).expanduser(); q = q if q.is_absolute() else d / q
        q = q.resolve()
        if not q.is_relative_to(d) or not q.is_file(): raise RuntimeError(f'文件不在项目里或不存在：{x}')
        return str(q.relative_to(d))
    _, meta = load(d)
    v = rel(video); check_final_video_name(v, meta['name'])
    meta['assets']['video'] = v
    if srt: meta['assets']['subtitles'] = rel(srt)
    meta['resolve'].update(project=project_name, timeline=timeline)
    write_json(d / '项目.json', meta)
    return {'video': v, 'subtitles': meta['assets'].get('subtitles'), 'resolve': meta['resolve']}


REUSE_HOURS = 24    # 上次传好的素材多久内直接拿来用。蚁小二文档没写素材留多久，也没有查“素材还在不在”的命令，超过就重传


def earlier_upload(others, key, path, sha, now):
    """本项目别的回执里，同一个文件（sha256 没变）传好的结果（2026-11-21《装修最后那点细节》：--again 重存又把成片传了一遍）。
    返回 (结果, 回执编号)；不能用时返回 (None, 原因)。"""
    best, why = None, '上次的上传记录里没有这个文件'
    for q, r in others:
        v = (r.get('uploads') or {}).get(key)
        if not (isinstance(v, dict) and v.get('ok') and isinstance(v.get('data'), dict) and v['data'].get('key')): continue
        if (r.get('file_hashes') or {}).get(path) != sha:
            why = '文件和上次传的不一样了'; continue
        try: at = datetime.strptime(v.get('uploaded_at', ''), '%Y-%m-%d %H:%M:%S')
        except ValueError:
            why = '上次的记录没写传的时间（旧版程序传的），没法确认蚁小二那边还留着'; continue
        hours = (now - at).total_seconds() / 3600
        if not 0 <= hours <= REUSE_HOURS:
            why = f'上次是 {hours:.0f} 小时前传的，超过 {REUSE_HOURS} 小时，蚁小二那边可能已清掉，程序没法确认'; continue
        if not best or at > best[2]: best = (v, q.parent.name, at)
    return (best[0], best[1]) if best else (None, why)


def parse_schedule(text, now=None):
    """--schedule "2026-11-23 20:00"（北京时间）→ {'time': 原样, 'ms': 13 位毫秒时间戳}。格式不对、不到 10 分钟以后的时间都停下。"""
    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=8))
    try: t = datetime.strptime(str(text).strip(), '%Y-%m-%d %H:%M').replace(tzinfo=tz)
    except ValueError: raise RuntimeError(f'定时发布的时间写成“2026-11-23 20:00”这样（北京时间）：{text}') from None
    if t <= (now or datetime.now(tz)) + timedelta(minutes=10):
        raise RuntimeError(f'定时发布的时间 {text} 已经过了或太近（至少 10 分钟以后，北京时间）')
    return {'time': t.strftime('%Y-%m-%d %H:%M'), 'ms': int(t.timestamp() * 1000)}


def upload(d, meta, execute=False, again=None, schedule=None):
    with upload_lock(d):
        return _upload(d, meta, execute, again, schedule)


def final_gate(d):
    """上传前的闸（2026-11-22 用户定“先做出来再改”：前面都按 AI 的判断先做成初稿、不问，只在整片导出和上传前留闸）：
    这条片子走了剪辑（有 02_制作/自动剪辑/job.json）时，成片必须是用户确认过才导出的——job.json 的 export_ok
    （thinkcut-edit export 记的：用户在审片台点过审核线“这一栏定了”；这条规矩之前导出的老片子记的是用户说 OK 的原话，也认）。
    没有就停，AI 不能替用户确认。没走剪辑的项目（没有 job.json）不查，返回 None。返回给回执记的一句依据。"""
    job = d / '02_制作' / '自动剪辑' / 'job.json'
    if not job.is_file(): return None
    try: ok = read_json(job).get('export_ok') or {}
    except (ValueError, OSError): ok = {}
    basis = str(ok.get('via') or ok.get('user_said') or '').strip()
    if not basis:
        raise RuntimeError('这个项目的成片还没有用户确认过的导出记录（02_制作/自动剪辑/job.json 没有 export_ok）：'
                           '先请用户在达芬奇里看完审核线、在审片台审片栏点“定稿”，再用 thinkcut-edit 的 export 导出成片，然后存档、上传。'
                           'AI 不能替用户点，对话里转述的话也不算（2026-11-22 用户定：先做出来再改，闸留在导出和上传前）。')
    return f"{basis}（{ok.get('at') or '时间没记'}，时间线 {ok.get('timeline') or '没记'}）"


def _upload(d, meta, execute=False, again=None, schedule=None):
    # 定时发布（2026-11-22 261122 反馈单 新9）：--schedule 的时间写进有定时字段的平台；没有的（YouTube）合在同一条草稿里，点发布就发
    # （用户 2026-11-22：“那YouTube就直接发了就行了，没有定时功能就没有吧，不需要”，并确认以后都这样）
    if schedule: meta = {**meta, 'schedule': parse_schedule(schedule)}
    confirmed_by = final_gate(d)      # 成片是用户在审片台确认后导出的才往下走（预检也查）
    if confirmed_by: print('成片的用户确认：' + confirmed_by, file=sys.stderr)
    if not meta.get('materials_confirmed'):
        raise RuntimeError('项目.json 的 materials_confirmed 尚未设为 true。')
    if not meta.get('title', '').strip():
        raise RuntimeError('视频标题尚未填写。')
    targets = [t for t in meta['targets'] if t.get('enabled')]
    if not targets:
        raise RuntimeError('项目.json 的 targets 尚未启用账号。')
    if len({t['account_id'] for t in targets}) != len(targets):
        raise RuntimeError('目标账号重复。')
    from delivery import verify_package
    package = verify_package(d, meta)
    video = asset_path(d, package['files']['video']['path'])
    files = {str(video): digest(video)}
    report = read_json(asset_path(d, meta['resolve'].get('report', '')))
    if not report.get('structural_verification') or report.get('visual_review_required', True) or report.get('delivery_sha256') != files[str(video)]:
        raise RuntimeError('存档未验证，或最终视频在存档后发生变化；先重新核对存档。')
    for t in targets:
        for ratio in [t['cover']] + ([t['horizontal_cover']] if t.get('horizontal_cover') else []):
            cover = asset_path(d, package['files'].get('cover:' + ratio, {}).get('path', ''))
            files[str(cover)] = digest(cover)
    warn_unused_fields(meta)
    command(['yxer-safe', 'doctor'])
    accounts = command(['yxer-safe', 'accounts', 'list', '--all', '--json'])['data']
    current = {str(a['id']): a for a in accounts}
    gaps = platform_gaps(accounts, targets, platforms_of(d))
    if gaps: print('【注意】这次不发这些平台：' + '；'.join(f'{k}（{v}）' for k, v in gaps.items()), file=sys.stderr)
    schemas = {}
    from publish_fields import content_form, verify_draft_preview, saved_draft_id
    mapped_forms, field_sources = {}, {}
    for t in targets:
        a = current.get(t['account_id'])
        if not a or a.get('status') != 1 or a.get('platformName') != t['platform'] or a.get('platformAccountName') != t['account_name']:
            raise RuntimeError('目标账号与当前在线账号不一致：' + t['account_name'])
        command(['yxer-safe', 'prepare', t['platform'], 'video'])
        command(['yxer-safe', 'schema', 'fields', t['platform'], 'video'])
        schemas[t['platform']] = command(['yxer-safe', 'schema', 'get', t['platform'], 'video'])['data']
        mapped_forms[t['account_id']] = content_form(meta, t, schemas[t['platform']], field_sources)
    if field_sources.get('limit_problems'):
        raise RuntimeError('这些资料超出平台上限，尚未上传文件：\n- ' + '\n- '.join(field_sources.pop('limit_problems')) +
                           '\n改 项目.json：标题、话题整体改短改少；只有个别平台超的，在 platform_extra 里给那个平台单独写（如 {"小红书": {"title": "…"}}）。')
    signature = {k: meta[k] for k in ('name', 'title', 'description', 'tags', 'targets', 'assets')}
    signature['file_hashes'] = files
    signature['delivery_package'] = meta['delivery_package']
    signature['platform_forms'] = mapped_forms          # 平台字段（原创、话题、开关等）变了就算新的一次
    if again:
        # 用户已在客户端删掉草稿、明确要求重传：按原因另开一次上传记录。不带时间（2026-11-13 审查）：同一条命令重跑不会多存一份，中断后能接着传
        signature['again'] = {'reason': again}
    sig = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    run_dir = d / '05_上传记录' / sig[:16]
    receipt_path = run_dir / '回执.json'
    others = [(q, read_json(q)) for q in sorted((d / '05_上传记录').glob('*/回执.json')) if q != receipt_path]
    # uploading = 还没发出存草稿请求（发之前会先改成 draft_save_pending），服务端不会有这份草稿，不算不明确（2026-10-29 审查）
    unclear = [str(q.parent.name) for q, r in others if r.get('state') not in ('draft_saved', 'checked_in_client', 'uploading')]
    if unclear and execute:
        # 上次结果不明确时，服务端可能已经存上；--again 也不能绕过（2026-10-24 审计）
        raise RuntimeError('有上传记录结果不明确：' + '、'.join(unclear) + '。先让用户在客户端核对；核对后把那份回执的 state 改成 checked_in_client 再上传。')
    same = [str(q.parent.name) for q, r in others if r.get('state') in ('draft_saved', 'checked_in_client')   # 核对过的那份客户端里可能有（2026-11-13 审查）
            and read_json(q.parent / '本次发布资料.json').get('title') == meta['title']] if not again else []
    if same and execute and not receipt_path.exists():
        raise RuntimeError('这个标题已经存过草稿（' + '、'.join(same) + '）。确认用户已在客户端删掉旧草稿、要再存一份时，加 --again "原因"。')
    resume = None
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get('state') == 'draft_saved':
            print('同一份资料已有成功草稿回执，不重复创建；可在客户端核对：' + str(receipt_path) + ('。确实要再存一份：--again 换一个原因（如加“第二次”）。' if again else '')); return receipt
        if receipt.get('state') == 'checked_in_client':
            raise RuntimeError('这份资料上次结果不明确、已在客户端核对过。客户端里有那条草稿就不用再传；没有或已删、要重存就加 --again "原因"：' + str(receipt_path))
        if receipt.get('state') != 'uploading':
            raise RuntimeError('已有未完成上传记录；先在客户端核对，不会自动重复建草稿：' + str(receipt_path))
        resume = receipt      # 上次在存草稿之前就停了（传素材断网、校验没过）：接着传，已传好的素材不重传
    plan = field_sources.get('schedule')
    if plan: print('定时发布 ' + meta['schedule']['time'] + '（北京时间）：' + '；'.join(f'{k} {v}' for k, v in plan.items()), file=sys.stderr)
    if not execute:
        print(json.dumps({'state': 'precheck_passed', 'mode': '仅蚁小二内部草稿', 'files': list(files), 'targets': targets, 'missing_platforms': gaps, **({'schedule': {'time': meta['schedule']['time'], 'platforms': plan}} if plan else {}), 'resume': bool(resume), 'next': '确认本次上传范围后加 --execute'}, ensure_ascii=False, indent=2)); return
    run_dir.mkdir(exist_ok=True)
    receipt = resume or {'state': 'uploading', **({'again': {'reason': again, 'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}} if again else {}), 'signature': sig, 'delivery_package': meta['delivery_package'], 'materials_confirmed': True, 'file_hashes': files, 'missing_platforms': gaps, 'uploads': {}, 'drafts': []}
    if plan: receipt['schedule'] = {'time': meta['schedule']['time'], 'platforms': plan}
    write_json(receipt_path, receipt)
    write_json(run_dir / '本次发布资料.json', meta)
    write_json(run_dir / '实时平台结构.json', schemas)
    write_json(run_dir / '平台字段来源.json', field_sources)
    # 重存（--again、改了字段再存）时，上次已传好、文件没变、时间不久的素材直接用；要重传的先说清多大、为什么
    planned = {str(video): video}
    for t in targets:
        for ratio in [t['cover']] + ([t['horizontal_cover']] if t.get('horizontal_cover') else []):
            c = asset_path(d, package['files']['cover:' + ratio]['path'])
            planned[str(c) + ('@视频号' if t['platform'] == '视频号' else '')] = c
    now, again_needed = datetime.now(), {}
    for key, path in planned.items():
        if key in receipt['uploads']: continue
        found, note = earlier_upload(others, key, str(path), files[str(path)], now)
        if found:
            receipt['uploads'][key] = {**found, 'reused_from': note}
            print(f'不重传：{path.name}（上次 {note} 已传过，文件没变，{found["uploaded_at"]} 传的）', flush=True)
        else: again_needed[key] = (path, note)
    write_json(receipt_path, receipt)
    if again_needed and any(r.get('uploads') for _, r in others):
        total = sum(path.stat().st_size for path, _ in again_needed.values())
        print(f'要重传 {size_text(total)}（' + '；'.join(f'{path.name}：{note}' for path, note in again_needed.values()) + '）', flush=True)
    def resource(path, platform=None):
        key = str(path) + ('@' + platform if platform else '')
        if key not in receipt['uploads']:
            # 传素材库桶：默认 cloud-publish 桶的文件，本机客户端发快手/小红书/视频号/B站时去 lite-asserts 地址取会 404
            args = ['yxer-safe', 'upload', str(path), '--auto-meta', '--bucket', 'material-library']
            if platform == '视频号':
                args += ['--platform', '视频号', '--usage', 'cover']
            # 2026-11-13 审查：传之前说清传哪个、多大，传完报用时；等的过程中 command() 每隔一会儿报一次“还在传”
            began = time.monotonic()
            print(f'开始传：{Path(path).name}（{size_text(Path(path).stat().st_size)}）' + (f' → {platform}' if platform else ''), flush=True)
            receipt['uploads'][key] = {**command(args), 'uploaded_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}   # 下次重存凭这个时间判断能不能直接用
            write_json(receipt_path, receipt)
            print(f'传完：{Path(path).name}，用时 {minutes_text(time.monotonic() - began)}', flush=True)
        value = receipt['uploads'][key]['data']
        if not isinstance(value, dict) or not value.get('key'):
            raise RuntimeError('上传返回中没有真实资源 key；保留回执，停止组装草稿。')
        return value
    video_resource = resource(video)
    payloads = []
    for i, t in enumerate(targets):
        schema = schemas[t['platform']]
        payload = copy.deepcopy(schema['minimalTemplate'])
        form = payload['publishArgs']['accountForms'][0]
        form.update(platformAccountId=t['account_id'], video=video_resource)
        cover = asset_path(d, package['files']['cover:' + t['cover']]['path'])
        cover_resource = resource(cover, t['platform'] if t['platform'] == '视频号' else None)
        form.update(cover=cover_resource, coverKey=cover_resource['key'])
        form['contentPublishForm'] = copy.deepcopy(mapped_forms[t['account_id']])
        if t.get('horizontal_cover'):
            # 2026-10-24 用户：抖音、视频号补横版封面（OldCover 结构，填在 contentPublishForm.horizontalCover）
            if 'horizontalCover' not in schema['businessFields']:
                raise RuntimeError(t['platform'] + ' 当前平台结构没有横版封面字段 horizontalCover')
            hpath = asset_path(d, package['files']['cover:' + t['horizontal_cover']]['path'])
            h = resource(hpath, t['platform'] if t['platform'] == '视频号' else None)
            form['contentPublishForm']['horizontalCover'] = {k: h[k] for k in ('key', 'width', 'height', 'size', 'format') if k in h}
        path = run_dir / f'{i + 1:02d}_草稿参数.json'
        write_json(path, payload)
        command(['yxer-safe', 'validate', t['platform'], 'video', str(path)])
        preview = command(['yxer-safe', 'draft', 'save', str(path), '--dry-run'])
        write_json(run_dir / f'{i + 1:02d}_草稿预检.json', preview)
        if not verify_draft_preview(preview, payload):
            raise RuntimeError('预检回执未明确显示蚁小二内部草稿标记；保留预检，不提交。')
        payloads.append((t, payload))
    # 2026-10-24 用户：一个发布任务就能装下所有平台 → 各平台单独校验通过后，合成一份草稿保存（客户端里是一条，一次点发布）
    combined = copy.deepcopy(payloads[0][1])
    combined['platforms'] = [t['platform'] for t, _ in payloads]
    combined['publishArgs']['accountForms'] = [p['publishArgs']['accountForms'][0] for _, p in payloads]
    path = run_dir / '00_合并草稿参数.json'; write_json(path, combined)
    preview = command(['yxer-safe', 'draft', 'save', str(path), '--dry-run'])
    write_json(run_dir / '00_合并草稿预检.json', preview)
    req = preview.get('data', {}).get('request', {})
    if not verify_draft_preview(preview, combined) or req.get('platforms') != combined['platforms'] or len(req.get('publishArgs', {}).get('accountForms', [])) != len(payloads):
        raise RuntimeError('合并草稿预检未通过或平台/账号数对不上；保留预检，不提交。')
    # 草稿命令自身负责转换请求；保留预检，再记录“结果未知”以防中断后重复提交。
    receipt['state'] = 'draft_save_pending'; write_json(receipt_path, receipt)
    try:
        saved = command(['yxer-safe', 'draft', 'save', str(path)])
    except RuntimeError as exc:      # 结果仍算不明确（请求可能已到服务端），但把原因留在回执里（2026-11-13 审查）
        receipt['save_error'] = str(exc); write_json(receipt_path, receipt); raise
    draft_id = saved_draft_id(saved)
    receipt['drafts'].append({'platform': '、'.join(combined['platforms']), 'platforms': combined['platforms'],
                              'account_ids': [t['account_id'] for t, _ in payloads], 'task_set_id': draft_id, 'result': saved})
    if not draft_id:
        receipt['state'] = 'draft_save_result_unverified'
        write_json(receipt_path, receipt)
        raise RuntimeError('草稿响应未提供可验证的草稿ID，已保存原始回执；先查客户端，不自动重复提交。')
    write_json(receipt_path, receipt)
    receipt['state'] = 'draft_saved'; write_json(receipt_path, receipt)
    print('内部草稿已保存；请在蚁小二客户端核对后手动发布。回执：' + str(receipt_path))
    return receipt


def main():
    ap = argparse.ArgumentParser(description=__doc__); sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('init'); p.add_argument('name'); p.add_argument('--root', help='不写就用这个 IP 配置的片子文件夹'); p.add_argument('--ip', help='这条片子属于哪个 IP（不写用默认 IP）')
    p = sp.add_parser('rename', help='改项目编号/名称'); p.add_argument('directory'); p.add_argument('new_name')
    p.add_argument('--user-ok', action='store_true', help='项目已有达芬奇工程、成片或存档时，用户同意后才加')
    p = sp.add_parser('finish', help='做完的项目移进 finish 文件夹'); p.add_argument('directory')
    p = sp.add_parser('reopen', help='把 finish 里的项目移回根目录接着做'); p.add_argument('directory')
    p = sp.add_parser('script-move', help='把项目文案从 02_制作/<项目名>.md 搬到 03_成片/<项目名>.md，缺的标题／封面文字／简介／标签用 项目.json 补上'); p.add_argument('directory')
    p = sp.add_parser('script-sync', help='把项目文案里的 标题、封面文字 写进 项目.json（title、cover_main）'); p.add_argument('directory')
    p = sp.add_parser('script', help='从项目文案 03_成片/<项目名>.md 取 标题／封面文字／口播正文'); p.add_argument('directory')
    p.add_argument('--part', choices=('标题', '封面', '封面文字', '口播', '口播正文', '简介', '标签', '话题', '小红书标题'), help='只取这一类；不写时全打出来并标明缺哪类')
    for name in ('adopt', 'resolve-library', 'resolve-open', 'check', 'materials-check', 'accounts', 'cover-brief', 'cover-register', 'register-final', 'archive', 'archive-stills', 'seal', 'verify', 'upload', 'activities', 'copy-draft', 'copy-apply'):
        p = sp.add_parser(name); p.add_argument('directory')
        if name == 'resolve-library':
            p.add_argument('--migrate', action='store_true', help='工程是借用别的库建的（达芬奇开着时建的新片子）：迁回片子自己的库')
        if name == 'accounts':
            p.add_argument('--fill-missing', action='store_true', help='项目已有目标账号时，只给缺的平台补账号，已有平台的选择和设置不动')
        if name == 'upload':
            p.add_argument('--execute', action='store_true')
            p.add_argument('--again', metavar='原因', help='用户已在客户端删掉草稿、要求重传时用，例如 --again "客户端已清空"')
            p.add_argument('--schedule', metavar='时间', help='定时发布：北京时间，写成 "2026-11-23 20:00"。写进有定时字段的平台；没有的（YouTube）合在同一条草稿里，点发布就发')
        if name in ('archive', 'archive-stills'):
            p.add_argument('--switch', action='store_true', help='达芬奇开着别的工程时，用户同意后才加：先保存那个工程，做完切回')
        if name == 'register-final':
            p.add_argument('--video', required=True, help='03_成片 里的成片（项目内相对路径或绝对路径）')
            p.add_argument('--srt', help='03_成片/字幕 里的 SRT'); p.add_argument('--project', required=True, help='达芬奇工程名'); p.add_argument('--timeline', required=True, help='最终时间线名')
        if name == 'cover-register':
            p.add_argument('--file', required=True); p.add_argument('--ratio', required=True, choices=['3:4', '4:3', '16:9'])
        if name == 'copy-apply':
            p.add_argument('--file', required=True, help='用户在文档里确认的定稿 JSON（title、description、tags，可带 bilibili_category）')
    a = ap.parse_args()
    try:
        if a.cmd == 'init':
            a.root = a.root or str(thinkcut_config.load(ip=a.ip).path('片子文件夹'))
            if Path(a.name).name != a.name or a.name in ('', '.', '..'):
                raise RuntimeError('项目名不能包含路径。')
            if (Path(a.root) / a.name).exists():      # 2026-11-20 审查：同名建两次不顺延出第二个项目；顺延只在编号被别的项目占用时
                raise RuntimeError('目录已存在；维护已有项目请用 adopt。')
            if (Path(a.root) / FINISHED / a.name).exists():
                raise RuntimeError(f'{FINISHED} 里已有同名项目（做完归档的那条）；要接着做用 reopen 移回来，要新做一条请换个名字。')
            name, was = free_name(a.name, a.root)
            if was: print(f'原编号 {was} 已被占用，改用 {name[:6]}：项目名是「{name}」')
            d, meta = initialize(Path(a.root) / name, ip=a.ip)
        elif a.cmd == 'adopt':
            d0 = Path(a.directory).expanduser().resolve(); name, was = free_name(d0.name, d0.parent, skip=d0.name)
            if was: raise RuntimeError(f'编号 {was} 和同目录另一个项目重复。已有的文件夹不自动改名：下一个没被占用的编号是 {name[:6]}，先把文件夹改名为「{name}」再 adopt。')
            d, meta = initialize(a.directory, adopt=True)
        elif a.cmd in ('finish', 'reopen'):
            print(json.dumps(finish(a.directory, back=a.cmd == 'reopen'), ensure_ascii=False, indent=2)); return 0
        elif a.cmd == 'rename':
            print(json.dumps(rename(a.directory, a.new_name, a.user_ok), ensure_ascii=False, indent=2)); return 0
        else:
            d, meta = load(a.directory)
            if a.cmd == 'script':
                print(show_script(d, a.part)); return
            if a.cmd == 'script-sync':
                print(json.dumps(sync_script(d, meta), ensure_ascii=False, indent=2)); return
            if a.cmd == 'script-move':
                print(json.dumps(move_script(d, meta), ensure_ascii=False, indent=2)); return
            if a.cmd == 'resolve-library':
                import resolve_library
                if a.migrate:
                    print(json.dumps(resolve_library.migrate(d), ensure_ascii=False, indent=2)); return
                print(json.dumps({'库名': d.name, '位置': str(resolve_library.folder(d)), '结果': resolve_library.register(d)}, ensure_ascii=False, indent=2)); return
            if a.cmd == 'resolve-open':      # 2026-11-23 初稿模式实测：达芬奇开没开都能备好这条片子的工程，不问
                import resolve_library
                print(json.dumps(resolve_library.open_project(d), ensure_ascii=False, indent=2)); return
            if a.cmd == 'materials-check':
                print(json.dumps(check_materials(d), ensure_ascii=False, indent=2)); return
            if a.cmd == 'accounts':
                if meta['targets'] and not a.fill_missing:
                    raise RuntimeError('项目已有目标账号，不覆盖已填写的选择。某个平台账号后来才修好、只想补缺的平台：加 --fill-missing（已有平台一个字不动）。')
                if a.fill_missing and not meta['targets']:
                    raise RuntimeError('项目还没有目标账号，--fill-missing 用不上：直接跑 accounts。')
                command(['yxer-safe', 'doctor'])
                rows = command(['yxer-safe', 'accounts', 'list', '--all', '--json'])['data']
                # 默认（2026-10-24）：竖版视频 B 站 16:9、其余 3:4；抖音、视频号加 4:3 横版
                # 2026-11-01 用户：“这个不要问，每次都是全部平台全部分发” → 能用的账号默认全部启用
                # 2026-11-21：--fill-missing 只给 项目.json 里一条都没有的平台追加（用户关掉的平台也算有，不动）
                have = {t.get('platform') for t in meta['targets']}
                cfg = thinkcut_config.load(project=d); plats = tuple(cfg.get('发布.平台'))
                if cfg.get('发布.只发这些平台', False): rows = [r for r in rows if r.get('platformName') in plats]      # 默认不限（能用的账号全启用）
                new = [dict(enabled=True, platform=r['platformName'], account_id=str(r['id']), account_name=r['platformAccountName'],
                            cover='16:9' if r['platformName'] == '哔哩哔哩' else '3:4', **({'horizontal_cover': '4:3'} if r['platformName'] in ('抖音', '视频号') else {}))
                       for r in rows if r.get('status') == 1 and r['platformName'] not in have]
                meta['targets'] = meta['targets'] + new
                write_json(d / '项目.json', meta)
                if a.fill_missing: print('补上了：' + ('、'.join(f"{t['platform']}（{t['account_name']}）" for t in new) if new else '没有（缺的平台蚁小二里还没有可用账号）') + '；已有平台没动。')
                if a.fill_missing:
                    alive = {str(r['id']) for r in rows if r.get('status') == 1}
                    stale = [f"{t['platform']}（{t['account_name']}）" for t in meta['targets'] if t.get('enabled') and t.get('account_id') not in alive]
                    if stale: print('【注意】项目里已有但现在不在线的账号（没动）：' + '、'.join(stale) + '；要换账号请手动改 项目.json 的 targets。')
                gaps = platform_gaps(rows, meta['targets'], plats)
                print((f'【注意】默认 {len(plats)} 个平台里缺：' + '；'.join(f'{k}（{v}）' for k, v in gaps.items())) if gaps else f'{len(plats)} 个平台账号都在，已全部启用')
            if a.cmd in ('cover-brief', 'cover-register', 'seal', 'verify'):
                from delivery import cover_brief, cover_register, seal, verify_package
                if a.cmd == 'seal': check_script(d)
                if a.cmd == 'cover-brief': result = cover_brief(d, meta)
                elif a.cmd == 'cover-register': result = cover_register(d, meta, a.file, a.ratio)
                elif a.cmd == 'seal': result = seal(d, meta)
                else: result = verify_package(d, meta)
                print(json.dumps(result, ensure_ascii=False, indent=2)); return
            if a.cmd == 'archive-stills':
                from resolve_archive import archive_stills
                archive_stills(d, switch=a.switch); return
            if a.cmd == 'register-final':
                print(json.dumps(register_final(d, a.video, a.srt, a.project, a.timeline), ensure_ascii=False, indent=2)); return
            if a.cmd == 'archive':
                from resolve_archive import archive
                check_final_video_name(meta['assets']['video'], meta['name'])
                check_script(d)
                check_materials(d)
                for line in filter(None, (name_title_warning(meta),)): print(line, file=sys.stderr)      # 2026-11-24 用户定：文案只在审片台“文案”栏写，不再提醒和 Claude 文档对一遍
                archive(d, switch=a.switch); return
            if a.cmd == 'upload':
                for line in filter(None, (name_title_warning(meta),)): print(line, file=sys.stderr)      # 2026-11-24 用户定：文案只在审片台“文案”栏写，不再提醒和 Claude 文档对一遍
                upload(d, meta, a.execute, a.again, a.schedule); return
            if a.cmd in ('copy-draft', 'copy-apply'):
                import copywriting
                if a.cmd == 'copy-draft': print('发布文案素材：' + str(copywriting.draft(d, meta))); return
                print(json.dumps(copywriting.apply(d, meta, a.file), ensure_ascii=False, indent=2)); return
            if a.cmd == 'activities':
                import activities
                if not meta['targets']: raise RuntimeError('项目还没有目标账号，先跑 accounts。')
                out = activities.candidates(d, meta)
                print('\n'.join(activities.summary(out))); print('完整结果：' + str(d / '02_制作' / '平台活动候选.json')); return
            if a.cmd == 'check' and meta['assets'].get('video'):
                check_final_video_name(meta['assets']['video'], meta['name'])
            if a.cmd == 'check' and name_title_warning(meta): print(name_title_warning(meta), file=sys.stderr)
        print(json.dumps({'project': str(d), 'name': meta['name'], 'mode': meta['delivery']}, ensure_ascii=False, indent=2))
        if a.cmd == 'init':      # 2026-11-24 用户定：建完项目的下一步是在审片台“文案”栏写文案（写稿 → 和 AI 一起改 → 定稿 → AI 出初稿）
            skill = Path(os.environ.get('THINKCUT_SKILL') or ROOT.parent / 'studio' / 'skill' / 'thinkcut-edit')
            print(f'下一步：打开审片台写文案——python3 "{skill}/scripts/thinkcut_edit.py" workbench "{d}"（这时只有文稿栏能用：用户在页面里写标题、封面文字、口播正文，'
                  '点“提交 AI”和 AI 一起改，点“定稿”后 AI 按初稿模式接着做；打开后挂 --wait 等。标题还没定可以先用工作名建项目，定稿后再 rename）')
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print('停止：' + str(exc), file=sys.stderr); return 1
    except (AttributeError, IndexError, TypeError) as exc:
        # 2026-11-13 审查：达芬奇接口拿不到东西时返回空，接着用就是这类错，以前会漏出一屏英文
        import traceback
        last = traceback.extract_tb(exc.__traceback__)[-1]
        where = f'（{Path(last.filename).name} 第 {last.lineno} 行：{type(exc).__name__}: {exc}）'
        if a.cmd in ('archive', 'archive-stills'):
            print('停止：达芬奇接口没返回对象，可能是达芬奇正在弹窗或忙、工程或时间线没打开、临时工程没导进去，或达芬奇版本的接口有变化' + where +
                  '。下一步：到达芬奇里关掉弹窗，确认本项目工程和最终时间线能正常打开，再重跑这条命令；还不行就把这行原样交给“架构审查”。', file=sys.stderr)
        else:
            print('停止：程序内部出错' + where + '。这不是操作问题：把这行原样交给“架构审查”，先不要重复执行。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
