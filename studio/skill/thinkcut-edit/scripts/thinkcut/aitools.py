# -*- coding: utf-8 -*-
"""后台 AI（提交 AI、对话侧栏）默认带哪些技能、MCP、依赖；设置页“AI 工具”体检（2026-11-28）。

用户原话：“thinkcut 的对话栏没有装 mcp 和各种必须的依赖和应用，包括必须的 skill，所以他干活总是错的。……claude 和 codex 都装上，也默认都装。”
2026-11-28 实测（Claude Code 2.1.316、Codex 0.160）：
- 后台 Claude：“提交 AI”一个 ThinkCut 技能都没有（只有对话侧栏带了插件）；没有 MCP，但 claude.ai 账号里的连接器（Claude Docs 等）照样进来。
- 后台 Codex：读用户自己的 ~/.codex/config.toml，带着 136 个技能、9 个 MCP（达芬奇、chrome-devtools、node_repl、lightroom、
  ChatGPT 连接器里的 GitHub/Canva 写操作……），MCP 跑在沙盒外、带着用户的全部权限。两边能用的东西不一样。
现在两边一样：
- 技能：ThinkCut 的 5 个 + HyperFrames 写法参考 2 个（SKILLS）。Claude 用 --plugin-dir 加载一个插件文件夹（--setting-sources 仍只读项目设置）；
  Codex 关掉插件，并用 skills.config 关掉白名单以外的技能（Codex 只认它自己技能文件夹里的，所以靠 ~/.codex/skills 的链接，体检能一键补）。
- MCP：白名单（两边同一份，allowed_mcp）：2026-11-28 起是达芬奇 MCP（经 bin/davinci_mcp_guard.py 护栏，规矩 bin/davinci_mcp_policy.py）；下面“现在白名单是空的”是开它之前的记录。Claude --strict-mcp-config（只用给它的，claude.ai 连接器也不进来）；Codex 关掉插件、ChatGPT 连接器、
  浏览器、操控电脑，并把用户配置里白名单以外的 MCP 一个个设 enabled=false。现在白名单是空的：剪辑流程里没有哪一步必须让 AI 直接用 MCP，
  要连达芬奇、钥匙、生图的都由审片台代办（bin/thinkcut_broker.py）跑我们自己的程序。每个已知 MCP 为什么不给写在 MCP_KNOWN。
- 依赖：给 AI 的 PATH 补上 Homebrew、~/.local/bin、node 的位置（和 Mac 程序外壳补的一样），PYTHONUTF8、HYPERFRAMES_NO_TELEMETRY 和出片脚本一致。
"""
import contextlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import engine, slash

def home(): return Path.home()          # 每次现取（测试换 HOME）

# ---------- 技能 ----------
# 名字 → 用途（体检页显示）。ThinkCut 的 5 个在程序里；辅助技能在用户自己装的技能文件夹里找。
THINKCUT_SKILLS = {
    'thinkcut-edit': '剪辑总入口：口播、B-roll、动画、字幕、导航、花字音效、审核线、导出',
    'thinkcut-project': '建项目、整理素材、存档、登记封面',
    'thinkcut-cover': '封面',
    'thinkcut-upload': '存蚁小二草稿（正式发布由你在客户端点）',
    'thinkcut-feedback': '收尾反馈、复盘',
}
HELPER_SKILLS = {
    'hyperframes': 'HyperFrames 网页动画的写法规矩（做 HF_ 镜头时参考；ThinkCut 的模板和 动画做法.md 优先）',
    'hyperframes-cli': 'HyperFrames 命令行（检查、预览、出帧）的用法',
}
SKILLS = {**THINKCUT_SKILLS, **HELPER_SKILLS}
# 不给后台的（体检页说明为什么）：联网、花钱或和剪辑流程不相干的
SKILLS_OFF = {
    'hyperframes-registry': '要联网下组件；ThinkCut 的 HyperFrames 模板已经定好',
    'media-use': '会走 HeyGen 等联网、收费的服务；配音、素材由 ThinkCut 自己的程序和代办做',
    'style-sampling': '照现成风格另做一集，不是剪辑流程',
    'reverse-any-video': '复刻别的视频，不是剪辑流程',
    'web-stand-in': '要开浏览器上网调研；后台不联网',
}


def user_skill_dirs():
    return [home() / '.claude' / 'skills', codex_home() / 'skills', home() / '.agents' / 'skills']


def codex_home(): return Path(os.environ.get('CODEX_HOME') or home() / '.codex')


def _skill_at(d):
    return d if (d / 'SKILL.md').is_file() else None


def skill_source(name):
    """技能原件在哪：ThinkCut 的在程序里；辅助技能在 ~/.claude/skills、~/.codex/skills、~/.agents/skills 里找第一个有 SKILL.md 的。没有给 None。"""
    if name in slash.SKILL_DIRS: return _skill_at(engine.PROGRAM / slash.SKILL_DIRS[name])
    for d in user_skill_dirs():
        got = _skill_at(d / name)
        if got: return got
    return None


def plugin_dir():
    """给后台 Claude 加载的插件文件夹（--plugin-dir；后台不读你的全局设置，~/.claude/skills 它看不到）：
    放在程序数据文件夹 ThinkCut/对话技能插件（2026-11-27 对话侧栏起用的名字，提交 AI 也用它），里面是指向技能原件的链接（AI 只读）。
    斜杠命令名 /thinkcut:thinkcut-edit 这类。找不到原件的技能不放（体检页会显示缺）。"""
    d = engine.settings_file().parent / '对话技能插件'
    meta = d / '.claude-plugin' / 'plugin.json'
    with contextlib.suppress(OSError):
        meta.parent.mkdir(parents=True, exist_ok=True)
        want = {'name': 'thinkcut', 'description': 'ThinkCut 思刻后台 AI 用的技能', 'version': '1.1.0'}
        if not meta.exists() or json.loads(meta.read_text(encoding='utf-8') or '{}') != want:
            meta.write_text(json.dumps(want, ensure_ascii=False), encoding='utf-8')
        (d / 'skills').mkdir(exist_ok=True)
        for n in SKILLS:
            link, to = d / 'skills' / n, skill_source(n)
            if link.is_symlink() and to and Path(os.readlink(link)) == to: continue
            if link.is_symlink(): link.unlink()
            if to and not link.exists(): link.symlink_to(to)
        for x in (d / 'skills').iterdir():          # 白名单里去掉的：只删我们自己放的链接
            if x.is_symlink() and x.name not in SKILLS: x.unlink()
    return d


def codex_skill_files():
    """Codex 会自己加载的技能（它自己的技能文件夹、~/.agents/skills，含 .system 和组织技能，最多往下三层）：[(SKILL.md, 真实位置)]。"""
    out = []
    for root in (codex_home() / 'skills', home() / '.agents' / 'skills'):
        if not root.is_dir(): continue
        for f in sorted(root.glob('*/SKILL.md')) + sorted(root.glob('*/*/SKILL.md')) + sorted(root.glob('*/*/*/SKILL.md')):
            with contextlib.suppress(OSError): out.append((f, f.resolve().parent))
    return out


def codex_skills_off():
    """白名单以外要关掉的 SKILL.md（按真实位置比：旧名链接和 thinkcut-* 指同一份，不能因为关旧名把新名也关了）。"""
    files = codex_skill_files()
    keep = {r for f, r in files if f.parent.name in SKILLS}
    keep |= {s.resolve() for s in map(skill_source, SKILLS) if s}
    return [str(f) for f, r in files if r not in keep]


# ---------- MCP ----------
# 白名单：名字 → {'does': 能做什么, 'command', 'args', 'env'}。两边同一份：Claude 写进 --mcp-config，Codex 用 -c 定义/放行。
# 2026-11-28 定为空——剪辑流程里没有哪一步必须让 AI 直接用 MCP（理由见 MCP_KNOWN）。以后要加：在这里加一项，两边自动一致，设置页自动显示。
MCP_ALLOW = {}
# 2026-11-28 用户定“开达芬奇 MCP”：白名单加 davinci-resolve，两边都经 ThinkCut 护栏（bin/davinci_mcp_guard.py）启动，规矩在 bin/davinci_mcp_policy.py。
# 启动方式从用户 Codex 配置 [mcp_servers.davinci-resolve] 读（环境变量只在护栏运行时读，不进仓库、不进命令行）；没装就没有。
DAVINCI_DOES = '看和改达芬奇里当前工程：时间线、轨道、片段、标记、媒体池、调色节点'


def _policy():
    import sys
    if str(engine.PROGRAM / 'bin') not in sys.path: sys.path.insert(0, str(engine.PROGRAM / 'bin'))
    import davinci_mcp_policy
    return davinci_mcp_policy


def allowed_mcp():
    """白名单里这台 Mac 起得来的 MCP：名字 → {does, command, args, env?, tools_off?}。两边都用这一份。"""
    out = dict(MCP_ALLOW)
    P = _policy(); go = P.launcher()
    if go: out[P.NAME] = {'does': DAVINCI_DOES, 'command': go['command'], 'args': go['args'], 'tools_off': list(P.TOOLS_OFF), 'guard': True}
    return out


def mcp_wanted(): return [*MCP_ALLOW, _policy().NAME]       # 白名单上的名字（没装的体检显示缺）
# 用户这台 Mac 上配过的 MCP（Claude、Codex、Codex 插件带的），为什么不给后台。MCP 跑在沙盒外，带着你的全部权限。
MCP_KNOWN = {
    'davinci-resolve': ('读写达芬奇：开工程、改时间线、调色、渲染导出',
                        '2026-11-28 起白名单里有它（经护栏）；这一行只在这台 Mac 没装时显示'),
    'chrome-devtools': ('操作你的 Chrome：开网页、点按钮、填表、读页面', '能用你已登录的网站；后台不联网，动画样张由出片脚本自己用无头浏览器截'),
    'computer-use': ('截屏并点鼠标、敲键盘，控制整台电脑', '能动任何程序和文件，后台不需要'),
    'node_repl': ('ChatGPT 的浏览器／操控电脑执行器', '同上：能控制浏览器和电脑'),
    'cua_repl': ('ChatGPT 的操控电脑执行器', '同上：能控制电脑'),
    'computer-history': ('读你电脑上的操作记录', '涉及隐私，剪辑用不上'),
    'event-stream': ('录制、回放你的操作', '涉及隐私，剪辑用不上'),
    'codex_app': ('Codex 桌面程序的工具', '后台用不上'),
    'codex_apps': ('ChatGPT 连接器：GitHub、Canva、Google 文档等，含写操作', '会替你在外部网站上改东西；后台不对外'),
    'lightroom': ('读写 Lightroom 照片库', '和剪辑无关'),
    'sketchup-motuyun': ('连 SketchUp 插件', '和剪辑无关'),
    'claude.ai': ('Claude 账号里的连接器（Claude Docs、Meetings 等）', '会替你读写外部文档；后台不对外'),
}
# Codex 关掉的功能：插件（带技能和 MCP）、ChatGPT 连接器、浏览器、操控电脑、跨项目记忆、自己生图（生图走代办）
CODEX_FEATURES_OFF = ('plugins', 'apps', 'browser_use', 'browser_use_external', 'computer_use', 'memories', 'chronicle', 'image_generation')


def codex_user_mcp():
    """用户 Codex 配置里写了哪些 MCP（只认 [mcp_servers.名字] 表头；关一个配置里没有的名字 Codex 会报错，所以只关有的）。"""
    try: text = (codex_home() / 'config.toml').read_text(encoding='utf-8')
    except OSError: return []
    out = []
    for m in re.finditer(r'^\s*\[\s*mcp_servers\.(?:"([^"]+)"|([A-Za-z0-9_-]+))', text, re.M):
        n = m[1] or m[2]
        if n not in out: out.append(n)
    return out


def claude_user_mcp():
    """用户 Claude 设置里的 MCP（~/.claude.json 顶层和各项目的 mcpServers；后台用 --strict-mcp-config，一个都不进来）。"""
    try: d = json.loads((home() / '.claude.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): return []
    names = list((d.get('mcpServers') or {}).keys())
    for v in (d.get('projects') or {}).values():
        if isinstance(v, dict): names += [n for n in (v.get('mcpServers') or {}) if n not in names]
    return names


def _toml(v): return json.dumps(v, ensure_ascii=False)       # 字符串、列表在 TOML 里和 JSON 写法一样


def claude_args():
    servers = {n: {'command': s['command'], 'args': list(s.get('args') or []), **({'env': s['env']} if s.get('env') else {})} for n, s in allowed_mcp().items()}
    return ['--strict-mcp-config', '--mcp-config', json.dumps({'mcpServers': servers}, ensure_ascii=False)]


def claude_allow():
    """dontAsk 模式下 MCP 工具也要放进允许名单：整台白名单 MCP 放行，再由 claude_deny 和护栏拦。"""
    return [f'mcp__{n}' for n in allowed_mcp()]


def claude_deny():
    return [f'mcp__{n}__{t}' for n, s in allowed_mcp().items() for t in s.get('tools_off') or []]


def codex_args():
    a = []
    for f in CODEX_FEATURES_OFF: a += ['-c', f'features.{f}=false']
    a += ['-c', 'web_search="disabled"']
    allow = allowed_mcp()
    for n in codex_user_mcp():
        if n not in allow: a += ['-c', f'mcp_servers.{_key(n)}.enabled=false']
    for n, s in allow.items():
        k = _key(n); a += ['-c', f'mcp_servers.{k}.command={_toml(s["command"])}', '-c', f'mcp_servers.{k}.args={_toml(list(s.get("args") or []))}',
                           '-c', f'mcp_servers.{k}.enabled=true']
        if s.get('tools_off'): a += ['-c', f'mcp_servers.{k}.disabled_tools={_toml(list(s["tools_off"]))}']
        a += ['-c', f'mcp_servers.{k}.default_tools_approval_mode="approve"']      # 后台不弹审批（approval never）：不设的话 Codex 把 MCP 调用全拒；把关靠 disabled_tools 和护栏（2026-11-28 实测）
        for ek, ev in (s.get('env') or {}).items(): a += ['-c', f'mcp_servers.{k}.env.{_key(ek)}={_toml(ev)}']
    off = codex_skills_off()
    if off: a += ['-c', 'skills.config=[' + ','.join('{path=%s,enabled=false}' % _toml(p) for p in off) + ']']
    return a


def _key(n): return n if re.fullmatch(r'[A-Za-z0-9_-]+', n) else _toml(n)


# ---------- 依赖（给 AI 的环境） ----------
def extra_path():          # 和 bin/thinkcut_app.py 的 EXTRA_PATH 一样
    h = home()
    return ('/opt/homebrew/bin', '/usr/local/bin', *(str(h / x) for x in ('.local/bin', '.local/node-system/bin', '.claude/local', '.npm-global/bin', '.bun/bin', '.volta/bin')))


def env_extra(env):
    """在给 AI 的环境上补：PATH 里常用位置（从程序坞打开的 ThinkCut PATH 很短）、和出片脚本一样的 PYTHONUTF8、HyperFrames 不上报。"""
    have = [p for p in env.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin').split(os.pathsep) if p]
    env['PATH'] = os.pathsep.join(have + [p for p in extra_path() if p not in have])
    env['PYTHONUTF8'] = '1'; env['HYPERFRAMES_NO_TELEMETRY'] = '1'
    return env


def _out(argv, env, timeout=30):
    try: r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL); return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e: return None, type(e).__name__


def deps(env=None):
    """后台 AI 跑剪辑要用的命令行和 Python 包（按 AI 拿到的 PATH 找）。need=False 的是有那类镜头才要。"""
    from . import doctor
    env = env_extra(dict(env or os.environ)); path = env['PATH']; w = lambda n: shutil.which(n, path=path)
    rows = []
    def row(name, ok, use, fix, need=True, ver=''): rows.append({'name': name, 'ok': bool(ok), 'use': use, 'fix': '' if ok else fix, 'need': need, 'version': ver})
    py = w('python3'); code, out = _out([py, '-c', 'import numpy, PIL; print("ok")'], env) if py else (None, '')
    row('python3（numpy、Pillow）', code == 0, '跑 ThinkCut 的剪辑程序', 'python3 -m pip install --user numpy pillow', ver=py or '')
    ff = w('ffmpeg'); enc = _out([ff, '-hide_banner', '-encoders'], env)[1] if ff else ''
    row('ffmpeg、ffprobe', ff and w('ffprobe') and 'prores_ks' in enc and 'libx264' in enc, '查素材、转码、抽帧', 'brew install ffmpeg')
    node = w('node'); v = _out([node, '--version'], env)[1] if node else ''
    row('Node.js ≥ 22', v.startswith('v') and v[1:].split('.')[0].isdigit() and int(v[1:].split('.')[0]) >= 22, '出动画、字幕组件', 'brew install node', ver=v)
    hf = w('hyperframes'); hv = _out([hf, '--version'], env)[1].splitlines()[-1:] if hf else []
    row('HyperFrames', hf, '出网页动画（HF_ 镜头）', 'npm install -g hyperframes@0.9.39', need=False, ver=(hv or [''])[0])
    row('Blender', doctor.find_blender() or w('blender'), '出 Blender 动画', '安装 Blender（放 /Applications）', need=False)
    row('Google Chrome', doctor.find_chrome(), '无头截图、动画样张', '安装 Google Chrome')
    row('达芬奇脚本模块', Path('/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py').exists(),
        '代办连达芬奇用（AI 自己不直接连）', '安装 DaVinci Resolve Studio')
    row('yxer-safe', w('yxer-safe'), '存蚁小二草稿（经代办）', 'bash bin/setup_mac.sh', need=False)
    return rows


# ---------- 体检 ----------
def report():
    """设置页“AI 工具”：每个 AI 能用的技能、MCP、依赖，缺什么、怎么补。"""
    claude_mcp, codex_mcp = claude_user_mcp(), codex_user_mcp()
    codex_names = {f.parent.name for f, _ in codex_skill_files()}
    skills = []
    for n, use in SKILLS.items():
        src = skill_source(n)
        links = {k: (d / n / 'SKILL.md').is_file() for k, d in (('claude_link', home() / '.claude' / 'skills'), ('codex_link', codex_home() / 'skills'))}
        skills.append({'name': n, 'use': use, 'claude': bool(src), 'codex': n in codex_names, 'source': str(src or ''), 'helper': n in HELPER_SKILLS, **links})
    off = [{'name': n, 'why': why, 'installed': bool(skill_source(n))} for n, why in SKILLS_OFF.items()]
    allow = allowed_mcp(); P = _policy()
    seen = list(dict.fromkeys([*mcp_wanted(), *codex_mcp, *claude_mcp, *MCP_KNOWN]))
    mcp = []
    for n in seen:
        does, why = MCP_KNOWN.get(n, ('（没登记过）', '不在白名单'))
        on = n in allow; want = n in mcp_wanted()
        if want and not on: why = '在白名单里，但这台 Mac 没装（或 Codex 配置里没有它的启动方式）'
        row = {'name': n, 'does': allow[n]['does'] if on else does, 'why': '' if on else why, 'backend': on, 'wanted': want}
        if n == P.NAME:
            row['tools_off'] = [{'name': t, 'why': w} for t, w in P.TOOLS_OFF.items()]
            row['actions_off'] = [{'match': pat.pattern, 'why': w} for pat, w in P.ACTION_OFF]
            row['notes'] = ['经 ThinkCut 护栏启动：上面这些工具不给，能用的工具里删除、渲染导出这类动作也拒',
                            '写操作先拿达芬奇写入锁（和审片台代办同一把），别处在写时拒',
                            '每改一次时间线达芬奇 MCP 会多存一条 “_archived” 备份时间线（关不掉）：同一次处理并成一条，交付时挪进“历史版本”',
                            '它不在系统沙盒里，带着你这台 Mac 的权限']
        mcp.append({**row,
                    'claude_cfg': n in claude_mcp, 'codex_cfg': n in codex_mcp})
    missing = [s['name'] for s in skills if not (s['claude'] and s['codex'] and s['claude_link'] and s['codex_link'])]      # 后台用不上，或你平时对话的技能文件夹里没链上
    return {'capabilities': capabilities(), 'skills': skills, 'skills_off': off, 'mcp': mcp, 'mcp_allow': list(allow), 'deps': deps(), 'missing_links': missing,
            'can_fix': any(fixable(n) for n in missing)}


def link_target(name):
    """补链接时链到哪：ThinkCut 的技能链到使用版（在开发版里也是：用户平时做项目用的是使用版，和 bin/setup_mac.sh 一样）；辅助技能链到找到的那份。"""
    if name in slash.SKILL_DIRS:
        import sys
        if str(engine.PROGRAM / 'bin') not in sys.path: sys.path.insert(0, str(engine.PROGRAM / 'bin'))
        import locations
        root = engine.PROGRAM
        if locations.is_dev(root): root = locations.prod_of(root)
        return _skill_at(Path(root) / slash.SKILL_DIRS[name]) if root else None
    return skill_source(name)


def fixable(name): return bool(link_target(name))


def fix_links():
    """一键补：把白名单技能链接进 ~/.claude/skills 和 ~/.codex/skills（只建链接，不覆盖已有的文件夹或链接）。返回做了什么。"""
    done, skipped = [], []
    for n in SKILLS:
        to = link_target(n)
        if not to: skipped.append(f'{n}：这台 Mac 上没有原件'); continue
        for d in (home() / '.claude' / 'skills', codex_home() / 'skills'):
            link = d / n
            if (link / 'SKILL.md').is_file(): continue
            if link.exists() or link.is_symlink(): skipped.append(f'{link}：已有同名的，没动'); continue
            d.mkdir(parents=True, exist_ok=True); link.symlink_to(to); done.append(f'{link} → {to}')
    return {'linked': done, 'skipped': skipped}


# ---------- 本地对话能做 vs 后台能做（2026-11-28 用户：“新加的mcp等工具和能力 是本地版本原来我们有的 /thinkcut-edit 这里的所有能力他也应该有”） ----------
# 每行：能力、本地对话怎么做、后台怎么做、差别（一样／代办／护栏／安全规矩）、凭什么说能做（实测或测试）。
# 代办＝后台 AI 照常敲命令，程序入口自动交给审片台在沙盒外跑（bin/thinkcut_broker.py）；“安全规矩”是故意留的差别。
CAPABILITIES = [
    ('读技能说明和项目文件', '直接读', '一样：7 个技能（插件／技能文件夹）', '一样', '后台 init 事件里 7 个技能都在（实测）'),
    ('审片台：读意见、回复、发布到栏、交文稿改写、放素材', 'thinkcut_edit.py workbench …', '一样：AI 那边直接跑', '一样', 'workbench --list 两边沙盒实测'),
    ('剪口播：识别、精修、交付达芬奇、修音', 'edit_project.py、transcribe_video.py（豆包）', '同一条命令，交给代办（钥匙、达芬奇在那边）', '代办', '代办名单测试；261114 真记录里后台跑过 deliver、broll'),
    ('豆包合成占位配音', 'doubao_tts.py', '同一条命令，代办', '代办', '两边沙盒实测'),
    ('配 B-roll（含主线没空轨时加轨）', 'broll.py plan／apply／adjust／add-track', '同一条命令，代办', '代办', 'test_add_track；真达芬奇只读演练'),
    ('花字音效、人挡字', 'fx.py', '同一条命令，代办', '代办', '代办名单测试'),
    ('动画成片：init、字时间、卡点、字幕、导航、审核线、检查、截图', 'thinkcut_edit.py 各命令', '同一条命令，代办（workbench 回写那几种留在 AI 这边）', '代办', 'doctor、status、resolve-status 两边沙盒实测'),
    ('HyperFrames 镜头：写镜头、检查', '改 镜头/*.html，hyperframes check', '一样：AI 那边直接跑（含浏览器检查）', '一样', 'hyperframes check 两边沙盒实测'),
    ('HyperFrames 出样张、出片', '出片.py --样张／--shot', '同一条命令，代办（程序自己的文件和模板一字不差）', '代办', '代办测试'),
    ('Blender 镜头：改 场景.py、冒烟、样张、出片', '改 场景.py，出片.py --冒烟／--样张／--shot', '同一条命令，代办在专用沙盒里跑（能用显卡，不联网、读不到钥匙、只写项目）', '代办', '真 Claude 改 场景.py 后冒烟通过；test_broker_sandbox'),
    ('直接开 Blender 跑别的脚本', 'blender --background --python …', '做不到：AI 自己的沙盒挡显卡，Blender 起不来；改用 出片.py', '安全规矩', '两边沙盒实测（崩在显卡检测）'),
    ('直接开 Chrome 截图、hyperframes preview 开窗口看', '开浏览器', '后台没人看：截图用 出片.py --样张、thinkcut_edit.py anim-stills／snap（代办），发到审片台给你看', '代办', 'Chrome 直接截图两边沙盒都起不来（实测）'),
    ('ffmpeg、ffprobe、Node、Python 包', '直接跑', '一样（PATH 和你终端里一样）', '一样', '两边沙盒实测'),
    ('导航条：准备字幕、核对、确认单', 'navigation.py prepare／validate／outline／confirm', '一样：AI 那边直接跑', '一样', 'navigation doctor 两边沙盒实测'),
    ('导航条出片、存样式预设', 'navigation.py render／preset-save', '同一条命令，代办（Codex 沙盒里 swift 跑不了；预设在程序文件夹）', '代办', '路由两边沙盒实测'),
    ('封面（Codex 生图）', 'thinkcut_cover.py', '同一条命令，代办', '代办', '两边沙盒实测'),
    ('建项目、整理素材、素材检查、存档', 'project.py', '同一条命令，代办（首页对话也能）', '代办', 'AI对话分支实测开项、materials-check'),
    ('素材库：找素材', 'library.py find', '一样：AI 那边直接跑', '一样', '两边沙盒实测'),
    ('素材库：写本期索引、素材说明', 'library.py add-project／notes／build', '同一条命令，代办（索引在程序文件夹）', '代办', '两边沙盒实测'),
    ('交反馈单给架构审查', '写进 internal/经验/待审', 'thinkcut_feedback.py submit（代办；本地也用这条）', '代办', '两边沙盒实测'),
    ('IP 配置：读规矩', 'thinkcut_config.py rules／show', '一样', '一样', '两边沙盒实测'),
    ('IP 配置：新建、删除、迁移', 'thinkcut_config.py new／remove／migrate', '同一条命令，代办（配置档在项目以外）', '代办', '路由两边沙盒实测'),
    ('看达芬奇、小改', '达芬奇 MCP；resolve-status', '一样：达芬奇 MCP 经护栏；查状态 resolve-status（代办）', '护栏', '真 Claude、真 Codex 经护栏读时间线'),
    ('达芬奇里渲染导出、删除、切换工程、装插件', '达芬奇 MCP 全开', '护栏拦', '安全规矩', '护栏测试；真跑 export 被拦'),
    ('整片导出', '对话里说了 AI 去跑', '只认审片栏点“定稿”后的导出', '安全规矩', '代办照样过闸（测试）'),
    ('上传蚁小二草稿', '对话里说“上传”', '提交 AI 能存草稿（代办）；对话栏只认发布栏按钮；正式发布都由你在蚁小二客户端点', '安全规矩', 'chat_policy 测试'),
    ('删除文件', '挪进回收站', '只能挪进回收站（thinkcut_trash.py，代办），不用 rm', '安全规矩', '两边沙盒实测'),
    ('钥匙（豆包、蚁小二）', '程序用', '后台 AI 读不到原文，要用钥匙的程序在代办那边跑', '安全规矩', '钥匙测试'),
    ('联网查资料', 'WebSearch／浏览器', '后台命令不联网', '安全规矩', '沙盒实测'),
]


def capabilities():
    return [dict(zip(('what', 'local', 'backend', 'kind', 'checked'), r)) for r in CAPABILITIES]
