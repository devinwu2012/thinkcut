"""发布文案（标题、简介、标签）的草稿素材和定稿回写。

2026-11-12《水电阶段没做好这4样》用户定：标题、简介、标签放进一个 Claude 文档里讨论，用户在文档里评论修改、确认后再写进项目.json；
标题要有“筛客、面向高净值人群”方向的备选；简介是写给平台搜索的关键词版（问题词＋方案词＋产品词串成通顺的句子），
数字和价格只用口播原话；标签 8–10 个按重要度排（用户原定 10–12 个，抖音、B 站话题最多 10 个，见下面 TAGS）。写文案是 AI 的事，这里只备素材（copy-draft）和核对回写（copy-apply）。"""
import re
from pathlib import Path

from project import read_json, write_json, script_path, script_rel, THINKCUT_PLATFORMS, warn_unused_fields      # project.py 只在命令里才 import 本文件，这里顶层 import 不会循环
import script_doc

TAGS = (8, 10)          # 11-12 用户定 10–12 个，但抖音、B 站话题最多 10 个（实时平台结构），上限按 10
TITLE_XHS = 20          # 小红书标题最多 20 字
NUM = re.compile(r'\d+(?:\.\d+)?')


def spoken_text(d, meta):
    """口播原话：优先登记的成片 SRT；没有就用最终文案 03_成片/<项目名>.md 里的“口播正文”
    （2026-11-20：只取口播正文，标题、封面文字里的数字不算口播说过）；再没有才用老的 02_制作/口播稿.txt"""
    srt = meta.get('assets', {}).get('subtitles')
    if srt and (d / srt).is_file():
        lines = (d / srt).read_text(encoding='utf-8').splitlines()
        return ''.join(l.strip() for l in lines if l.strip() and not l.strip().isdigit() and '-->' not in l)
    if script_path(d).is_file():
        body = script_doc.read(script_path(d))['口播正文']
        if body: return body
    old = d / '02_制作' / '口播稿.txt'
    if old.is_file() and old.read_text(encoding='utf-8').strip(): return old.read_text(encoding='utf-8')
    return ''


def cover_text(d, meta):
    """封面文字：项目.json 的 cover_main/cover_sub 和文案里的“封面文字”。只有一边有就用那边；两边都有且不一样时都列出、标明来源，不自动选。"""
    fields = {k: meta.get(k, '') for k in ('cover_main', 'cover_sub')}
    doc = script_doc.read(script_path(d))['封面文字'] if script_path(d).is_file() else None
    squash = lambda xs: re.sub(r'[\s，,]', '', ''.join(xs))
    if not doc: return fields
    if not any(str(v).strip() for v in fields.values()): return {'来源': f'项目文案 {script_rel(d)}', '行': doc}
    if squash(fields.values()) == squash(doc): return fields
    return {'项目.json': fields, f'项目文案 {script_rel(d)}': doc,
            '注意': '两边不一样，没有自动选哪个：问用户以哪份为准'}


def draft(d, meta):
    """写 02_制作/发布文案素材.json：给做文案的 AI 用（口播原话、口播里的数字、封面文字、现有字段、写法要求），不是给用户看的"""
    text = spoken_text(d, meta)
    if not text: raise RuntimeError(f'找不到口播原话：先 register-final 登记成片 SRT，或把最终文案存成 03_成片/{Path(d).name}.md（文件名和项目名一样；'
                                    '文案里有别的小标题时，口播开始的地方加「## 口播正文」）。')
    doc = script_doc.read(script_path(d)) if script_path(d).is_file() else dict.fromkeys(script_doc.PARTS + script_doc.EXTRA)
    doc_title = doc['标题']
    # 2026-11-21 用户定：“写封面、写标题的时候，咱们在MD文档里都有了，让他去那里找去”——文案里有的直接用，不出备选；没有的才写
    out = {'说明': ('项目文案里已经有的（标题、简介、标签、小红书标题）直接用，不出备选、不建文档；文案里没有的才照“写法要求”写一版（不出多个备选），'
                  '不等用户过目（2026-11-22 用户定“先做出来再改”），直接写成 JSON（title、description、tags，可带 bilibili_category、platform_titles），用 copy-apply 回写项目.json，同时把新写的补进项目文案；用户在审片台“上传文案”栏直接改。'
                  '文案里连标题都没有时，才按老办法出两组标题备选：AI 先选一个写进去，其余发到审片台卡片上让用户想换再换。'),
           '写法要求': {'标题': ('文案里有标题：直接用，不出备选（2026-11-21）。文案里没有标题时才出两组：常规方向、筛客/高净值方向（预算充足、改善型、在意品质的人），'
                              '每个备选注明筛的是谁；AI 先选一个最合适的写进去，其余发到审片台“上传文案”栏的卡片上，用户想换再换（2026-11-22）'),
                    '简介': '写给平台搜索看的关键词版：问题词＋方案词＋产品词串成通顺的句子；数字和价格只用口播原话（见“口播里的数字”）',
                    '标签': f'{TAGS[0]}–{TAGS[1]} 个（抖音、B 站最多 10 个），不带 #，按重要度排',
                    '小红书标题': f'文案里有「小红书标题」就用；没有且主标题超过 {TITLE_XHS} 字时按原标题缩一版 {TITLE_XHS} 字以内的直接写进去（不等过目，2026-11-22），JSON 里放 platform_titles.小红书',
                    '各平台简介': '某个平台的简介要和通用简介不同时（比如小红书写长一点），定稿 JSON 里放 platform_descriptions: {\"小红书\": \"…\"}，平台名用 抖音、快手、小红书、视频号、哔哩哔哩、Youtube；没写的平台用通用简介；数字照样只用口播原话',
                    '没改的部分': '用户在文档里没改的，按文档推荐或已确定的来'},
           '封面文字': cover_text(d, meta),
           '现有字段': {k: meta.get(k) for k in ('title', 'description', 'tags', 'bilibili_category')},
           '文案里的标题': doc_title or '（项目文案里没有标题）',
           '文案里的简介': doc['简介'] or '（没写：写一版直接 copy-apply，用户在审片台“上传文案”栏改）',
           '文案里的标签': doc['标签'] or '（没写：写一版直接 copy-apply，用户在审片台“上传文案”栏改）',
           '文案里的小红书标题': doc['小红书标题'] or '（没写）',
           '口播里的数字': sorted(set(NUM.findall(text)), key=float),
           '口播原话': text}
    path = d / '02_制作' / '发布文案素材.json'; write_json(path, out)
    return path


def check(fields, spoken, xhs_target=True):
    """回写前核对：标题、简介不能空；标签个数、不带 #、不重复；简介里的数字都要在口播原话里出现过；
    发小红书且标题超过 20 字时要有单独的小红书标题（20 字以内）"""
    errors = []
    for k in ('title', 'description'):
        if not str(fields.get(k, '')).strip(): errors.append(f'{k} 是空的')
    tags = fields.get('tags') or []
    if not TAGS[0] <= len(tags) <= TAGS[1]: errors.append(f'标签 {len(tags)} 个，要 {TAGS[0]}–{TAGS[1]} 个')
    if any('#' in t for t in tags): errors.append('标签不带 #')
    if len(set(tags)) != len(tags): errors.append('标签有重复')
    said = set(NUM.findall(spoken))
    xhs = (fields.get('platform_titles') or {}).get('小红书')
    if xhs_target and len(str(fields.get('title', ''))) > TITLE_XHS and not xhs:
        errors.append(f'标题 {len(fields["title"])} 字，小红书最多 {TITLE_XHS} 字：在 platform_titles 里给小红书单独写一个（发小红书时才需要）')
    if xhs and len(xhs) > TITLE_XHS: errors.append(f'小红书标题 {len(xhs)} 字，最多 {TITLE_XHS} 字')
    extra = [n for n in NUM.findall(str(fields.get('description', ''))) if n not in said]
    if extra: errors.append('简介里这些数字口播没说过：' + '、'.join(dict.fromkeys(extra)) + '（数字和价格只用口播原话）')
    # 2026-11-21：各平台单独的简介（platform_descriptions）；平台名要用蚁小二的叫法，数字照样只用口播原话
    for field in ('platform_titles', 'platform_descriptions'):
        for platform in (fields.get(field) or {}):
            if platform not in THINKCUT_PLATFORMS: errors.append(f'{field} 里的平台名“{platform}”不对，只能用：' + '、'.join(THINKCUT_PLATFORMS) + '（如 B站 要写成 哔哩哔哩）')
    for platform, text in (fields.get('platform_descriptions') or {}).items():
        if not str(text).strip(): errors.append(f'{platform} 的单独简介是空的')
        bad = [n for n in NUM.findall(str(text)) if n not in said]
        if bad: errors.append(f'{platform} 的单独简介里这些数字口播没说过：' + '、'.join(dict.fromkeys(bad)) + '（数字和价格只用口播原话）')
    return errors


def apply(d, meta, file):
    """把用户在文档里确认的定稿写进项目.json（只改 title、description、tags、bilibili_category；platform_titles、platform_descriptions 写进各平台的 platform_extra.title、.description）"""
    fields = read_json(Path(file))
    targets = meta.get('targets', [])
    xhs_target = not targets or any(t.get('platform') == '小红书' and t.get('enabled') for t in targets)   # 还没生成账号时按要发算
    errors = check(fields, spoken_text(d, meta), xhs_target)
    # 文案里写定的不许换（2026-11-21 用户：MD 里有了就用 MD 里的）：标题、简介、标签、小红书标题和文案不一样就拦
    doc = script_doc.read(script_path(d)) if script_path(d).is_file() else {}
    squash = lambda v: re.sub(r'\s', '', '、'.join(v) if isinstance(v, list) else str(v or ''))
    for k, name in (('title', '标题'), ('description', '简介'), ('tags', '标签')):
        if doc.get(name) and k in fields and squash(fields[k]) != squash(doc[name]):
            errors.append(f'{k} 和项目文案里的「{name}」不一样：文案里写定的直接用；用户要改就先改文案 {script_rel(d)}')
    xhs = (fields.get('platform_titles') or {}).get('小红书')
    if doc.get('小红书标题') and xhs and squash(xhs) != squash(doc['小红书标题']):
        errors.append(f'小红书标题和项目文案里的不一样：用文案里的「{doc["小红书标题"]}」')
    if errors: raise RuntimeError('定稿没写进项目.json：\n- ' + '\n- '.join(errors))
    for k in ('title', 'description', 'tags', 'bilibili_category'):
        if k in fields: meta[k] = fields[k]
    for platform, title in (fields.get('platform_titles') or {}).items():
        meta.setdefault('platform_extra', {}).setdefault(platform, {})['title'] = title
    for platform, text in (fields.get('platform_descriptions') or {}).items():
        meta.setdefault('platform_extra', {}).setdefault(platform, {})['description'] = text
    warn_unused_fields(meta)
    write_json(d / '项目.json', meta)
    added = script_doc.add_sections(script_path(d), [('简介', fields.get('description')), ('标签', fields.get('tags')), ('小红书标题', xhs)]) if script_path(d).is_file() else []
    out = {k: meta.get(k) for k in ('title', 'description', 'tags', 'bilibili_category')}
    if added: out['补进项目文案'] = added
    return out
