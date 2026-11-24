"""封面文字交接和可校验的交付包；不发布、不删除用户文件。"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

from project import AI_ROOT, asset_path, check_final_video_name, digest, load, read_json, write_json

CONTENT_KEYS = ('name', 'title', 'cover_main', 'cover_sub', 'description', 'tags', 'assets')


def content(meta):
    value = {key: copy.deepcopy(meta[key]) for key in CONTENT_KEYS}
    if meta.get('bilibili_category'):
        value['bilibili_category'] = meta['bilibili_category']
    return value


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def cover_lines(text):
    """封面主标题两行。只有一行时按逗号拆（2026-11-12 水电反馈单新12）：挑拆完两边字数最接近的逗号，逗号不上封面。
    和 thinkcut-cover 的 split_title 规则相同。"""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if len(lines) != 1:
        return lines
    t = lines[0]; cut = [i for i, c in enumerate(t) if c in '，,']
    if not cut:
        return lines
    i = min(cut, key=lambda i: abs(len(t[:i].strip()) - len(t[i + 1:].strip())))
    return [x for x in (t[:i].strip(' ，,'), t[i + 1:].strip(' ，,')) if x]


def cover_brief(d, meta):
    lines = cover_lines(meta['cover_main'])
    sub = (meta.get('cover_sub') or '').strip()      # 2026-11-21 用户定：封面文字不分主副，副标题可以不填（封面上就不画色块）
    if len(lines) not in (2, 3) or not all(x.strip() for x in lines):      # 2026-11-20 用户定：一般两行，三行也可以
        raise RuntimeError('项目.json 的封面主标题须为两行或三行（两行可以写成一行、用逗号隔开）；不代写文案。')
    work = d / '02_制作/封面制作'
    work.mkdir(exist_ok=True)
    brief = {
        'project': meta['name'], 'source': '项目.json',
        'skill': str(AI_ROOT / '封面/skill/thinkcut-cover/SKILL.md'),
        'line1': lines[0], 'line2': lines[1], **({'line3': lines[2]} if len(lines) == 3 else {}), 'sub': sub,
        'work_directory': str(work), 'delivery_directory': str(d / '03_成片/封面'),
        'instruction': '按 thinkcut-cover Skill 制作。文字逐字使用上述字段' + ('' if sub else '（没有副标题：init 不给 --sub）') + '；scene/subject 按本期素材确定。3:4 出 3 个方向，AI 先挑一个把 4:3 及 16:9 出齐，发到审片台让用户事后选（2026-11-22 起不等用户先确认 3:4）（横版复用竖版画）。完成后用cover-register登记；不自动生图。'
    }
    write_json(work / '封面任务.json', brief)
    (work / '封面任务.md').write_text(
        '# 封面任务\n\n文字来源：项目.json\n\n'
        + '主标题第一行：' + lines[0] + '\n\n主标题第二行：' + lines[1] + ('\n\n主标题第三行：' + lines[2] if len(lines) == 3 else '')
        + '\n\n副标题：' + (sub or '（无）') + '\n\n' + brief['instruction'] + '\n')
    return brief


def cover_register(d, meta, source, ratio):
    source = Path(source).expanduser().resolve()
    spec_path = source.parent / '_制作文件/spec.json'; spec = read_json(spec_path)
    if [spec[k] for k in ('line1', 'line2', 'line3') if spec.get(k)] != cover_lines(meta['cover_main']) or (spec.get('sub') or '').strip() != (meta.get('cover_sub') or '').strip():
        raise RuntimeError('封面制作文字与当前项目.json不一致；先按最新文字出图。')
    # 2026-11-13 审查：改字重新登记（init）后没重新出图，旧字的图还在原处，以前只比文字记录、旧图也能登记成功
    if source.stat().st_mtime < spec_path.stat().st_mtime - 2:
        raise RuntimeError('这张封面图比文字登记（_制作文件/spec.json）旧：改字后还没重新出图。先用 thinkcut-cover 重新 render，再登记。')
    check = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,codec_name', '-of', 'json', str(source)], capture_output=True, text=True, check=True)
    stream = json.loads(check.stdout)['streams'][0]
    a, b = (int(x) for x in ratio.split(':'))
    if abs(stream['width'] * b - stream['height'] * a) > max(a, b) or stream['codec_name'] != 'mjpeg':
        raise RuntimeError('封面须为指定比例的 JPEG 文件。')
    sha = digest(source)
    relative = f'03_成片/封面/封面_{ratio.replace(":", "x")}_{sha[:8]}.jpg'
    target = d / relative
    if target.exists() and digest(target) != sha:
        raise RuntimeError('目标封面同名但内容不同，不覆盖。')
    if not target.exists():
        shutil.copy2(source, target)
    if digest(target) != sha:
        raise RuntimeError('封面复制摘要不一致。')
    latest = load(d)[1]
    latest['assets']['covers'][ratio] = relative
    write_json(d / '项目.json', latest)
    (d / '02_制作/封面制作').mkdir(exist_ok=True)
    write_json(d / f'02_制作/封面制作/登记_{ratio.replace(":", "x")}.json', {
        'source': str(source), 'path': relative, 'sha256': sha,
        'cover_main': meta['cover_main'], 'cover_sub': meta['cover_sub']})
    return {'path': relative, 'sha256': sha, 'note': '已写入项目.json 的封面路径。'}


def media_target(base, value):
    """媒体清单.json 里的 target → 存档目录里的真实文件；找不到或跑出存档目录返回 None。
    新清单记相对存档目录的路径。旧清单记的是存档当时的绝对路径：项目文件夹挪过、NAS 换了挂载名后那个路径不在了
    （或指向别处的旧副本），就按它末尾的几段（媒体/摘要前 12 位/文件名）在现在的存档目录里找。找到后调用方还会核对摘要。"""
    base = base.resolve(); t = Path(value)
    candidates = [t] + [base.joinpath(*t.parts[i:]) for i in range(1, len(t.parts))] if t.is_absolute() else [base / t]
    for c in candidates:
        c = c.resolve()
        if c.is_relative_to(base) and c.is_file():
            return c
    return None


def seal(d, meta):
    """对已验收的工程添加交付包；同一内容复用，变化创建新包。"""
    video = asset_path(d, meta['assets']['video'])
    check_final_video_name(video.name, meta['name'])
    report_path = asset_path(d, meta['resolve'].get('report', ''))
    report = read_json(report_path)
    if not report.get('structural_verification') or report.get('visual_review_required', True):
        raise RuntimeError('先完成达芬奇结构、媒体和关键画面验收，再封存交付包。')
    if report.get('delivery_sha256') != digest(video):
        raise RuntimeError('当前成片与已验收的工程存档不一致。')
    drp = asset_path(d, meta['resolve'].get('archive', ''))
    base = drp.parent
    dependencies = {str(p.relative_to(d)): digest(p) for p in (drp, report_path, base / '最终时间线.drt', base / '时间线快照.json', base / '媒体清单.json')}
    if (base / '工程设置.json').is_file():
        dependencies[str((base / '工程设置.json').relative_to(d))] = digest(base / '工程设置.json')
    for item in read_json(base / '媒体清单.json').values():
        p = media_target(base, item['target'])
        if p is None or digest(p) != item['sha256']:
            raise RuntimeError('达芬奇收集媒体缺失、越界或摘要改变：' + str(item['target']) + '。核对存档目录里的“媒体”文件夹是否完整；确实缺了就重新 archive。')
        dependencies[str(p.relative_to(d))] = item['sha256']
    inputs = {'video': video}
    if meta['assets'].get('subtitles'):
        inputs['subtitles'] = asset_path(d, meta['assets']['subtitles'])
    for ratio, value in meta['assets']['covers'].items():
        if value:
            inputs['cover:' + ratio] = asset_path(d, value)
    hashes = {key: digest(p) for key, p in inputs.items()}
    version = signature({'content': content(meta), 'hashes': hashes, 'dependencies': dependencies, 'video_filename': video.name})
    out = base / '交付包' / version[:16]
    manifest = out / '交付清单.json'
    if not out.exists():
        temp = out.with_name('.准备_' + uuid.uuid4().hex[:10])
        temp.mkdir(parents=True)
        try:
            files = {}
            for key, source in inputs.items():
                name = source.name if key == 'video' else ('字幕' if key == 'subtitles' else '封面_' + key[6:].replace(':', 'x')) + source.suffix.lower()
                target = temp / name
                shutil.copy2(source, target)
                if digest(target) != hashes[key]:
                    raise RuntimeError('交付包复制摘要不一致：' + name)
                files[key] = {'path': str((out / name).relative_to(d)), 'source': str(source.relative_to(d)), 'sha256': hashes[key]}
            write_json(temp / '项目资料.json', meta)
            dependencies[str((out / '项目资料.json').relative_to(d))] = digest(temp / '项目资料.json')
            write_json(temp / '交付清单.json', {'schema_version': 1, 'project': meta['name'], 'signature': version, 'content': content(meta), 'files': files, 'dependencies': dependencies})
            (temp / '打开说明.md').write_text('# ' + meta['name'] + '\n\n本目录是已封存的发布素材和项目资料快照。上传读取本目录的成片与封面。达芬奇DRP与媒体在上两级，请一起保留。\n\n标题、封面或成片变化后需重新封存；旧版本不覆盖。正式发布由本人在蚁小二客户端完成。\n')
            temp.rename(out)
        finally:
            if temp.exists():
                shutil.rmtree(temp)  # 仅本次生成失败的临时副本。
    latest = load(d)[1]
    latest['delivery_package'] = {'manifest': str(manifest.relative_to(d)), 'signature': version}
    write_json(d / '项目.json', latest)
    verify_package(d, latest)
    return latest['delivery_package']


def verify_package(d, meta):
    binding = meta.get('delivery_package', {})
    package = read_json(asset_path(d, binding.get('manifest', '')))
    if package.get('project') != meta['name'] or package.get('signature') != binding.get('signature') or package.get('content') != content(meta):
        raise RuntimeError('项目.json 发布资料与封存版本不一致；请重新封存。')
    expected = check_final_video_name(meta['assets']['video'], meta['name'])
    if Path(package['files']['video']['path']).name != expected:
        raise RuntimeError('交付包成片文件名须与项目.json登记的时间文件名一致；请重新封存。')
    for relative, sha in package['dependencies'].items():
        if digest(asset_path(d, relative)) != sha:
            raise RuntimeError('工程存档或资料快照已改变：' + relative)
    for item in package['files'].values():
        if digest(asset_path(d, item['path'])) != item['sha256']:
            raise RuntimeError('封存素材已改变：' + item['path'])
        source = (d / item['source']).resolve()
        if not source.is_relative_to(d):
            raise RuntimeError('封存来源路径越界。')
        if source.is_file() and digest(source) != item['sha256']:
            raise RuntimeError('工作目录素材在封存后发生变化；先重新核对存档：' + item['source'])
    return package
