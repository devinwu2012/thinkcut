#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面规格检查（2026-11-26 用户定：“把界面规格固化下来，避免以后改乱”）。

规格只有一份：design/tokens.css。页面（首页、审片台、口播精剪编辑器、设置页）和样张页必须从这里取颜色、圆角、阴影、
字体、字号，自己写死就不过。画在视频画面上的东西（字幕预览、成片导航条预览）按成片样子画、不套界面规格，
这类例外只放行下面 EXCEPTIONS 里登记的具体位置（和 design/README.md 的“例外清单”一一对应），别的地方一律不许写死。

  python3 bin/test_design.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / 'design' / 'tokens.css'
ASSETS = ROOT / 'studio/skill/thinkcut-edit/assets'
PAGES = [ASSETS / '首页/index.html', ASSETS / '审片工作台/index.html', ASSETS / '审片工作台/文案剪辑.html', ASSETS / '设置/index.html']
PARTS = [ASSETS / '对话/对话.html']          # 页面片段（服务发页面时塞进页面，自己不带规格占位）：AI 对话侧栏（2026-11-27）
SAMPLE = ROOT / 'design' / '样张.html'
MARK = '<!--THINKCUT_TOKENS-->'
# 例外清单：(文件, 这一行里一定有的一段原文, 原因)。改了这些行就要同步改这里和 design/README.md
EXCEPTIONS = [
    ('审片工作台/文案剪辑.html', '.caption{position:absolute;left:5%;right:5%;bottom:6%;', '画面字幕预览＝成片字幕的样子：白字、黑描边、阴影，字号跟画面宽度缩放'),
    ('审片工作台/文案剪辑.html', '@media(max-width:640px){.copy{font-size:17px}}', '窄屏时口播正文的阅读字号（正文字号由用户 A−/A+ 调，不进字号表）'),
    ('审片工作台/index.html', "g.fillStyle='rgba(18,18,20,.94)'", '导航条预览画的是成片里的导航条：底色和成片一致'),
    ('审片工作台/index.html', "g.fillStyle=i<k?'#64A9E8'", '导航条预览：已播 / 当前 / 未播的格子颜色和成片一致'),
    ('审片工作台/index.html', "g.fillStyle=i===k?'#fff':'#C4C4C4'", '导航条预览：章节字颜色和成片一致'),
    ('审片工作台/index.html', "g.fillStyle='#64A9E8';g.fillRect(c.x,92,px-c.x,12);g.fillStyle='#FF5C5C'", '导航条预览：进度和播放头颜色和成片一致'),
]
FONT_SIZES = {'11px', '12px', '13px', '15px', '20px', '28px'}

# 写死的颜色：#rgb/#rrggbb/#rrggbbaa（前面是 & 的是 HTML 字符实体，不算）、rgb()/rgba()/hsl()、常见颜色单词
HEX = re.compile(r'(?<![&\w])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![\w-])')
FUNC = re.compile(r'\b(?:rgba?|hsla?)\(')
NAMED = re.compile(r'(?:color|background|border(?:-[a-z]+)?|fill|stroke|outline)\s*:\s*[^;"}]*?(?<![-\w])(white|black|red|blue|green|gray|grey|orange|purple|yellow)(?![-\w])')
RADIUS = re.compile(r'border-radius\s*:\s*([^;"}]+)')
SHADOW = re.compile(r'box-shadow\s*:\s*([^;"}]+)')
FONTFAM = re.compile(r'font-family\s*:\s*([^;"}]+)')
FONTSIZE = re.compile(r'font-size\s*:\s*([^;"}]+)')
FONTSHORT = re.compile(r'\bfont\s*:\s*([^;"}]+)')

fails = []


def check(ok, msg):
    print(('通过 ' if ok else '失败 ') + msg, flush=True)
    if not ok: fails.append(msg)


def radius_ok(v):
    v = v.strip()
    return 'var(' in v or v in ('0', 'inherit') or v.startswith('0 ') and 'var(' in v or v == '50%'


def css_blocks(text):
    return re.findall(r'<style[^>]*>(.*?)</style>', text, re.S)


def bad_lines(text, css_only=False, name=''):
    """找写死的值：返回 [(行号, 说明, 原文片段)]。"""
    out = []
    lines = text.split('\n')
    in_style = False
    for i, line in enumerate(lines, 1):
        if '<style' in line: in_style = True
        style_line = in_style
        if '</style>' in line: in_style = False
        if any(name.endswith(f) and key in line for f, key, _ in EXCEPTIONS): continue
        if css_only and not style_line: continue
        for m in HEX.finditer(line): out.append((i, '写死颜色', m.group(0)))
        for m in FUNC.finditer(line): out.append((i, '写死颜色', m.group(0)))
        for m in NAMED.finditer(line): out.append((i, '写死颜色单词', m.group(1)))
        for m in RADIUS.finditer(line):
            if not radius_ok(m.group(1)): out.append((i, '圆角没用规格', m.group(0)[:40]))
        for m in SHADOW.finditer(line):
            v = m.group(1).strip()
            if not ('var(' in v or v in ('none', 'inherit')): out.append((i, '阴影没用规格', m.group(0)[:50]))
        for m in FONTFAM.finditer(line):
            v = m.group(1).strip()
            if not ('var(' in v or v in ('inherit',)): out.append((i, '字体没用规格', m.group(0)[:50]))
        if style_line:
            for m in FONTSIZE.finditer(line):
                v = m.group(1).strip()
                if not ('var(' in v or v in FONT_SIZES or v in ('inherit', '100%') or v.endswith('em') or 'calc(' in v):
                    out.append((i, '字号不在规格里（11/12/13/15/20/28）', m.group(0)[:40]))
            for m in FONTSHORT.finditer(line):
                v = m.group(1).strip()
                if v != 'inherit' and 'var(--font' not in v: out.append((i, 'font 简写没用规格字体', m.group(0)[:50]))
    return out


# 1. 规格文件
t = TOKENS.read_text(encoding='utf-8') if TOKENS.exists() else ''
check(bool(t), 'design/tokens.css 存在')
check('界面规格 v1' in t, '规格写了版本名（界面规格 v1）')
need = ['--win', '--bar', '--surface', '--card', '--t1', '--t2', '--t3', '--t4', '--hair', '--hair2', '--fill', '--blue', '--blue-t',
        '--s-wait', '--s-todo', '--s-ai', '--s-ok', '--s-err', '--s-off', '--r-s', '--r-m', '--r-l', '--r-xl', '--sh1', '--sh2', '--sh3',
        '--font', '--fs-11', '--fs-13', '--fs-28', '--sp-1', '--sp-10', '--ease', '--dur-1', '--dur-3']
light = t.split('@media')[0]
check(all(re.search(re.escape(n) + r'\s*:', light) for n in need), '规格里颜色、线、圆角、阴影、字号、间距、动效都有定义')
dark = re.search(r'prefers-color-scheme:\s*dark\)\s*\{(.*?)\}\}', t, re.S)
dk = dark.group(1) if dark else ''
check(all(re.search(re.escape(n) + r'\s*:', dk) for n in ['--win', '--surface', '--card', '--t1', '--t3', '--hair', '--blue', '--s-wait', '--s-err', '--sh3']),
      '深色模式单独定义了一套（不是反色）')
check('prefers-reduced-motion' in t, '规格照顾“减少动态效果”')
radii = re.findall(r'--r-(?:s|m|l|xl)\s*:\s*(\d+)px', light)
check(radii == ['6', '8', '12', '14'], '圆角只有 4 档：6 / 8 / 12 / 14')

# 2. 每个页面：从规格取值，不自己写死
for p in PAGES:
    name = p.relative_to(ROOT)
    text = p.read_text(encoding='utf-8') if p.exists() else ''
    check(text.count(MARK) == 1, f'{name} 里有且只有一处规格占位 {MARK}')
    bad = bad_lines(text, name=str(name))
    check(not bad, f'{name} 没有绕过规格自己写颜色、圆角、阴影、字体、字号' + ('' if not bad else '：' + '；'.join(f'第{i}行 {k} {s}' for i, k, s in bad[:12]) + (f' 等 {len(bad)} 处' if len(bad) > 12 else '')))

for p in PARTS:
    name = p.relative_to(ROOT); text = p.read_text(encoding='utf-8') if p.exists() else ''
    check(bool(text) and MARK not in text, f'{name}（页面片段）存在，不另带规格占位')
    bad = bad_lines(text, name=str(name))
    check(not bad, f'{name} 没有绕过规格自己写颜色、圆角、阴影、字体、字号' + ('' if not bad else '：' + '；'.join(f'第{i}行 {k} {s}' for i, k, s in bad[:12])))
for p in (ASSETS / '首页/index.html', ASSETS / '审片工作台/index.html'):
    check(p.read_text(encoding='utf-8').count('<!--THINKCUT_CHAT-->') == 1, f'{p.relative_to(ROOT)} 里有一处对话侧栏占位')

# 例外清单要和 README 对得上，每一条在页面里都还真的存在（改掉了就删掉这条，别留着放水）
readme = (ROOT / 'design/README.md').read_text(encoding='utf-8') if (ROOT / 'design/README.md').exists() else ''
for f, key, why in EXCEPTIONS:
    page = ASSETS / f
    check(page.exists() and key in page.read_text(encoding='utf-8'), f'例外清单这一条在 {f} 里还存在：{key[:40]}')
check(all(why in readme for _, _, why in EXCEPTIONS), 'design/README.md 的“例外清单”列了测试放行的每一处')
check('规格外' not in ''.join(p.read_text(encoding='utf-8') for p in PAGES if p.exists()), '页面里没有自己写“规格外”注释给自己放行（例外只能登记在清单里）')

# 3. 服务发页面时把规格塞进去
sys.path.insert(0, str(ROOT / 'studio/skill/thinkcut-edit/scripts'))
from thinkcut import design  # noqa: E402
out = design.inject(('<html><head>' + MARK + '</head></html>').encode()).decode()
check('--win' in out and MARK not in out and 'thinkcut-tokens' in out, '服务发页面时把 design/tokens.css 换进占位')
import os  # noqa: E402
_old = os.environ.get('THINKCUT_APP_FLAVOR')
try:      # 2026-11-27 用户：开发版左上角一直写“开发版”；正式版没有
    os.environ['THINKCUT_APP_FLAVOR'] = 'dev'; dev_out = design.inject(MARK.encode()).decode()
    os.environ['THINKCUT_APP_FLAVOR'] = 'release'; rel_out = design.inject(MARK.encode()).decode()
finally:
    if _old is None: os.environ.pop('THINKCUT_APP_FLAVOR', None)
    else: os.environ['THINKCUT_APP_FLAVOR'] = _old
check('tc-dev' in dev_out and '\\u5f00\\u53d1\\u7248' in dev_out and 'tc-dev' not in rel_out, '开发版页面挂“开发版”标签，正式版不挂')
check(not bad_lines(design.DEV_BADGE.decode().split('<script>')[0]), '“开发版”标签的样式也只用规格变量')

# 4. 样张页：直接用规格变量，自己不写死
s = SAMPLE.read_text(encoding='utf-8') if SAMPLE.exists() else ''
check('href="tokens.css"' in s, 'design/样张.html 直接引用 tokens.css（变量一改样张跟着变）')
bad = bad_lines(s)
check(bool(s) and not bad, 'design/样张.html 没有自己写死颜色、圆角、阴影' + ('' if not bad else '：' + '；'.join(f'第{i}行 {k} {x}' for i, k, x in bad[:8])))
check((ROOT / 'design/README.md').exists(), 'design/README.md（规格说明）存在')

print(('全部通过' if not fails else f'{len(fails)} 项没过'))
sys.exit(1 if fails else 0)
