# -*- coding: utf-8 -*-
"""项目文案（03_成片/<项目名>.md；2026-11-22 前在 02_制作/）按用途分三类取：标题、封面文字、口播正文。

2026-11-20 用户定：文案里除了口播，还有发布用的标题和封面文字；各阶段从文案里分类提取——
做封面取“封面文字”，上传写标题取“标题”，剪口播对稿取“口播正文”。

标准写法（以后的文案都这样标）：
    ## 标题
    一行标题
    ## 封面文字
    每行一行（空行不算）
    ## 口播正文
    口播全文（### 小标题、分隔线、> 备注不算口播）

也认已有的写法：`## 发布信息`／`## 标题与封面` 下面的 `标题（…）：xxx`、`**标题**：xxx`、
`封面文字（…）：` 后跟 ```text 代码块或几行字；老的纯口播文案（整份没有一个 `##` 小标题）整份算口播正文。
认不出某一类就返回 None，不猜；由调用方决定报错。

纯标准库，不 import project.py：别的仓库可以按文件路径加载本文件。
"""
import re
from pathlib import Path

PARTS = ('标题', '封面文字', '口播正文')
# 发布用的三类（2026-11-21 用户定：“写封面、写标题的时候，咱们在MD文档里都有了，让他去那里找去”）：可以没有；
# 没有时上传由 AI 写一版给用户过目，确认后 copy-apply 写回文案（「## 简介」「## 标签」「## 小红书标题」）
EXTRA = ('简介', '标签', '小红书标题')
ALIASES = {'标题': '标题', '封面': '封面文字', '封面文字': '封面文字', '口播': '口播正文', '口播正文': '口播正文', '正文': '口播正文',
           '简介': '简介', '标签': '标签', '话题': '标签', '小红书标题': '小红书标题'}

H2 = re.compile(r'^##(?!#)\s*(.*?)\s*#*\s*$')                     # 二级标题（### 不算）
H1 = re.compile(r'^#(?!#)\s')
SUBHEAD = re.compile(r'^#{3,}\s')
RULE = re.compile(r'^\s*([-*_])(\s*\1){2,}\s*$')                   # --- *** ___ 分隔线
FENCE = re.compile(r'^\s*(```|~~~)')
# 已有写法里的“标签：值”行，如 标题（所有平台通用）：xxx、**标题**：xxx、**封面文字**：
LABEL = re.compile(r'^\s*(?:\*\*|__)?\s*(标题|封面文字)\s*(?:[（(][^）)]*[）)])?\s*(?:\*\*|__)?\s*[：:]\s*(?:\*\*|__)?\s*(.*?)\s*$')


def clean(line):
    """去掉 Markdown 记号和缩进，只留文字本身。"""
    s = line.replace('　', ' ').strip()
    s = re.sub(r'<!--.*?-->', '', s)
    s = re.sub(r'^(?:[-*+]|\d+[.、)])\s+', '', s)                  # 列表记号 “1. ” “- ”
    s = re.sub(r'(\*\*|__)(.+?)\1', r'\2', s)                       # **加粗**
    s = re.sub(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])', r'\1', s)   # *斜体*
    s = s.replace('**', '')
    return s.strip()


def sections(lines):
    """按二级标题切段：[(标题名 或 None 表示第一个 ## 之前, [行…])]。代码块里的 # 不算标题。"""
    out, name, body, fenced = [], None, [], False
    for line in lines:
        if FENCE.match(line): fenced = not fenced
        m = None if fenced else H2.match(line)
        if m:
            out.append((name, body)); name, body = m.group(1).rstrip('：:').strip(), []
        else:
            body.append(line)
    out.append((name, body))
    return out


def cover_lines(lines):
    """封面文字的几行：有代码块就只取代码块里的；没有就取到下一个标签、分隔线或以句号结尾的说明句为止。"""
    start = next((i for i, l in enumerate(lines) if FENCE.match(l)), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if FENCE.match(lines[i])), len(lines))
        picked = lines[start + 1:end]
    else:
        picked = []
        for l in lines:
            if LABEL.match(l) or RULE.match(l) or SUBHEAD.match(l): break
            if l.strip().startswith('>') or not clean(l): continue
            if clean(l).endswith('。'): break                     # 封面字不以句号结尾；这样的是解释句
            picked.append(l)
    rows = [clean(l) for l in picked]
    return [r for r in rows if r] or None


def body_text(lines):
    """口播正文：去掉小标题、分隔线、> 备注、代码块记号和 Markdown 记号，每段一行。"""
    rows = []
    for l in lines:
        if SUBHEAD.match(l) or H1.match(l) or RULE.match(l) or FENCE.match(l) or l.strip().startswith('>'): continue
        s = clean(l)
        if s: rows.append(s)
    return '\n'.join(rows) or None


def from_labels(lines):
    """已有写法：在一段里找 标题：/封面文字： 标签行。"""
    title = cover = None
    for i, l in enumerate(lines):
        m = LABEL.match(l)
        if not m: continue
        if m.group(1) == '标题' and title is None:
            title = clean(m.group(2)) or None
        elif m.group(1) == '封面文字' and cover is None:
            same_line = clean(m.group(2))
            cover = [same_line] if same_line else cover_lines(lines[i + 1:])
    return title, cover


def parse(text):
    """返回 {'标题': str 或 None, '封面文字': [行…] 或 None, '口播正文': str 或 None, '简介': str 或 None, '标签': [个…] 或 None, '小红书标题': str 或 None}。"""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    parts = sections(lines)
    result = dict.fromkeys(PARTS + EXTRA)
    if len(parts) == 1:                                          # 整份没有 ## 小标题：老的纯口播文案
        result['口播正文'] = body_text(lines)
        return result
    for name, body in parts:
        if name == '标题' and result['标题'] is None:
            m = next((LABEL.match(l) for l in body if LABEL.match(l)), None)
            first = next((clean(l) for l in body if clean(l) and not l.strip().startswith('>') and not FENCE.match(l)), None)
            result['标题'] = (clean(m.group(2)) or None) if m and m.group(1) == '标题' else first
        elif name == '封面文字' and result['封面文字'] is None:
            result['封面文字'] = cover_lines([l for l in body if not (LABEL.match(l) and not LABEL.match(l).group(2))])
        elif name == '口播正文' and result['口播正文'] is None:
            result['口播正文'] = body_text(body)
        elif name in EXTRA and result[name] is None:
            rows = [clean(l) for l in body if clean(l) and not l.strip().startswith('>') and not FENCE.match(l)]
            if name == '标签': result[name] = [t for t in (x.strip().lstrip('#').strip() for r in rows for x in re.split(r'[、，,；;\s#]+', r)) if t] or None
            elif rows: result[name] = rows[0] if name == '小红书标题' else '\n'.join(rows)
    for name, body in parts:                                     # 已有写法：其它段落里的 标题：/封面文字： 标签
        if name == '口播正文': continue
        title, cover = from_labels(body)
        if result['标题'] is None: result['标题'] = title
        if result['封面文字'] is None: result['封面文字'] = cover
    return result


def read(path):
    return parse(Path(path).read_text(encoding='utf-8'))


MISSING_HINT = {'标题': '在文案里加「## 标题」小标题，下面写一行标题',
                '封面文字': '在文案里加「## 封面文字」小标题，下面每行写一行封面字',
                '口播正文': '在文案里口播开始的地方加「## 口播正文」小标题',
                '简介': '还没写：上传时 AI 写一版给用户过目，确认后 copy-apply 写回文案「## 简介」',
                '标签': '还没写：上传时 AI 写一版给用户过目，确认后 copy-apply 写回文案「## 标签」（顿号分开，不带 #）',
                '小红书标题': '还没写：标题超过 20 字时 AI 缩一版给用户过目，确认后 copy-apply 写回文案「## 小红书标题」'}


def add_sections(path, items):
    """把还没有的 简介／标签／小红书标题 小节写进文案（放在「## 口播正文」前面，没有它就放最后）；已经有的一律不动。返回写了哪几节。"""
    path = Path(path); text = path.read_text(encoding='utf-8'); have = parse(text)
    new = [(k, v) for k, v in items if v and not have.get(k)]
    if not new: return []
    block = ''.join(f'## {k}\n\n' + ('、'.join(v) if isinstance(v, list) else str(v)).strip() + '\n\n' for k, v in new)
    lines = text.split('\n'); at = next((i for i, l in enumerate(lines) if H2.match(l) and H2.match(l).group(1).rstrip('：:').strip() == '口播正文'), None)
    text = '\n'.join(lines[:at]) + '\n' + block + '\n'.join(lines[at:]) if at is not None else text.rstrip('\n') + '\n\n' + block
    path.write_text(text, encoding='utf-8'); return [k for k, _ in new]


def _fmt(name, value):
    """写进文案的样子：封面文字一行一行；标签用顿号连；其余原样（去掉首尾空行）。"""
    if isinstance(value, (list, tuple)):
        value = '、'.join(str(x).strip() for x in value if str(x).strip()) if name == '标签' else '\n'.join(str(x).rstrip() for x in value if str(x).strip())
    return str(value or '').strip('\n').rstrip()


def section_body(path, name):
    """「## name」小节里原样的文字（审片台给人改口播正文用，带 ### 小标题和 > 备注）；没有这个小节返回 None。"""
    lines = Path(path).read_text(encoding='utf-8').split('\n'); span = _span(lines, name)
    return None if span is None else '\n'.join(lines[span[0] + 1:span[1]]).strip('\n')


def _span(lines, name):
    """「## name」小节在第几行开始（标题行）、到第几行之前结束（下一个 ## 或文件尾）。代码块里的 # 不算标题。"""
    start, fenced = None, False
    for i, line in enumerate(lines):
        if FENCE.match(line): fenced = not fenced; continue
        m = None if fenced else H2.match(line)
        if not m: continue
        if start is not None: return start, i
        if m.group(1).rstrip('：:').strip() == name: start = i
    return None if start is None else (start, len(lines))


def set_section(path, name, value):
    """审片台改字（2026-11-22）：只换「## name」这一节标题下面的字，文案其余部分一字不动；写完再读一遍核对，对不上就恢复原文件并报错。
    没有这一节时：整份没有 ## 小标题（老的纯口播文案）不改，要先整理成标准写法；这一类写在别的小节里（老写法 标题：xxx）也不改；
    都没有才新加一节，放在「## 口播正文」前面（没有它就放最后）。name 是 标题／封面文字／口播正文／简介／标签／小红书标题。返回写进去的字。"""
    if name not in PARTS + EXTRA: raise ValueError(f'不认识的小节：{name}')
    path = Path(path); old = path.read_text(encoding='utf-8'); lines = old.split('\n'); body = _fmt(name, value)
    before = parse(old); span = _span(lines, name)
    if span is not None:
        a, b = span; tail = lines[b:]
        new_lines = lines[:a + 1] + [''] + body.split('\n') + ([''] if tail else []) + tail
        if not tail and old.endswith('\n'): new_lines.append('')
    else:
        if not any(H2.match(l) for l in lines): raise RuntimeError('这份文案是老写法（整份没有 ## 小标题），先整理成「## 标题」「## 封面文字」「## 口播正文」的标准写法再改')
        if before.get(name): raise RuntimeError(f'文案里的「{name}」写在别的小节里（老写法），先把它整理成单独的「## {name}」小节再改')
        at = next((i for i, l in enumerate(lines) if H2.match(l) and H2.match(l).group(1).rstrip('：:').strip() == '口播正文'), None)
        block = [f'## {name}', ''] + body.split('\n') + ['']
        new_lines = lines[:at] + block + lines[at:] if at is not None else lines + ([''] if lines[-1:] != [''] else []) + block
    new = '\n'.join(new_lines); path.write_text(new, encoding='utf-8')
    after = parse(new); want = parse(f'## {name}\n\n{body}\n## 口播正文\n\nx\n')[name] if name != '口播正文' else parse(f'## 口播正文\n\n{body}\n')['口播正文']
    bad = [k for k in PARTS + EXTRA if k != name and after.get(k) != before.get(k)]
    if after.get(name) != want: bad.append(name)
    if bad:
        path.write_text(old, encoding='utf-8'); raise RuntimeError('改完核对没过（' + '、'.join(bad) + ' 读出来不对），已恢复原文件')
    return after.get(name)


def count(text):
    """口播字数：不算空白。"""
    return len(re.sub(r'\s', '', text or ''))
