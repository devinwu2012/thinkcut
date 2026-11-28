# -*- coding: utf-8 -*-
"""对话框的斜杠命令（2026-11-27 用户要：“对话框里应该支持斜杠命令……比如说我用个 /goal 命令，整个AI后台也知道能用这 goal 命令完成”）。

2026-11-27 实测（Claude Code 2.1.316 `-p`、Codex 0.160 `exec`，用 AI 引擎同样的权限参数，在临时文件夹）：
- Claude `-p`：斜杠命令只在“用户的话一开头就是它”时才认，所以对话把 ThinkCut 的规矩放进 --append-system-prompt，用户的话原样发。
  启动事件里列着它这一次真能用的命令（slash_commands）。因为后台只读项目设置（--setting-sources project，不读你自己放宽的全局权限），
  ~/.claude/skills、~/.claude/commands 里你自己的技能和命令后台看不到（实测 /grill-me 和旧名技能 都说没装）。
  ThinkCut 自己的 5 个技能用 --plugin-dir 另外加载（实测出现为 /thinkcut:thinkcut-edit 这类）。
  真能用：/goal（实测让 AI 一直做到目标达成）、/compact、/context、/cost、/usage、/recap、/effort、/model（只管这一次，下一句又按设置）。
  不认（回“isn't available in this environment”）：/help、/permissions 等；/clear 不报错也不做事；/config key=value 会改你本机的 Claude 全局设置。
- Codex `exec`：一个斜杠命令都不认，原样当普通文字交给模型（实测 /status 是模型照着编的状态，/compact 是模型自己“总结”了一段，没有真压缩）。
  所以 Codex 下：/goal 改写成普通要求发过去（明说是改写的），技能按名字说给它，其余告诉用户这个命令后台用不了。

这里只分类，不跑 AI：classify(文字, 引擎) → 一个说明怎么处理的 dict（chat.py 照着做）。
"""
import re

CMD = re.compile(r'^/([A-Za-z][\w:.-]*)(?:\s+([\s\S]*))?$')        # “/名字 参数”；/Volumes/… 这种路径不算（名字后面紧跟着 /）
DANGER = re.compile(r'--dangerously|dangerously-skip|bypassPermissions|bypass-permissions|--permission-mode|--sandbox\b|--no-sandbox|allowUnsandboxed', re.I)

# ThinkCut 自己做的（两种引擎一样）
APP = {
    'clear': ('', '清空这段对话，下一句 AI 新开会话（项目里的会话和“提交 AI”共用，也一起新开）'),
    'stop': ('', '停止 AI 正在做的'),
    'model': ('[模型名]', '不带名字：看现在用哪个 AI 和模型、打开设置换；带名字：以后这个 AI 都用这个模型（和设置页填的是同一处）'),
    'help': ('', '列出能用的斜杠命令'),
    'settings': ('', '打开设置（选 AI、模型、豆包、蚁小二）'),
}
ALIAS = {'config': 'settings', 'exit': 'stop', 'quit': 'stop', 'new': 'clear', 'reset': 'clear', 'status': 'model'}

# 原样交给后台 AI（只有 Claude 真认；Codex 下另说）
AI = {
    'goal': ('目标', '定一个目标，AI 一直做到达成为止（例：/goal 把 01_原始素材 里的素材都分好类、登记好）'),
    'compact': ('[要留下的重点]', '压缩前面的对话，腾出上下文（会话太长时用）'),
    'context': ('', '看这条会话用了多少上下文'),
    'cost': ('', '看会员额度用了多少'),
    'usage': ('', '看会员额度用了多少'),
    'recap': ('', '让 AI 总结这条会话做过什么'),
    'effort': ('low|medium|high', '这一句让 AI 想得多细（只管这一句）'),
}

# ThinkCut 的技能（Claude：--plugin-dir 加载，命令名 /thinkcut:名字；Codex：按名字说给它）
SKILLS = {'thinkcut-project': '项目：开项、整理素材、存档、登记封面', 'thinkcut-edit': '剪辑：剪口播、配 B-roll、动画、花字音效、导航条',
          'thinkcut-cover': '封面', 'thinkcut-upload': '上传：存蚁小二草稿（正式发布仍在页面上点）', 'thinkcut-feedback': '反馈：整理这次的问题和新规矩'}
SKILL_DIRS = {'thinkcut-project': 'projects/skills/thinkcut-project', 'thinkcut-edit': 'studio/skill/thinkcut-edit', 'thinkcut-cover': 'cover/skill/thinkcut-cover',
              'thinkcut-upload': 'projects/skills/thinkcut-upload', 'thinkcut-feedback': 'studio/skill/thinkcut-feedback'}

# 不放行的：说清楚为什么（没列的命令统一说“后台用不了”）
PERM = '会改 AI 的权限、沙盒或设置：后台的权限是 ThinkCut 收住的（只能写这个项目、不联网、拿不到钥匙），对话里改不了'
DENY = {
    **{k: PERM for k in ('permissions', 'update-config', 'fewer-permission-prompts', 'auto-mode-setup', 'sandbox', 'hooks', 'add-dir', 'allowed-tools',
                         'mcp', 'plugin', 'plugins', 'reload-plugins', 'reload-skills', 'agents', 'output-style', 'autocompact', 'memory', 'privacy-settings')},
    'login': '登录、退出账号在“设置”里点（会弹出终端，由你自己登）', 'logout': '登录、退出账号在“设置”里点（会弹出终端，由你自己登）',
    'init': '会在项目文件夹里写一份 CLAUDE.md，ThinkCut 的规矩已经由程序带给 AI，不用它',
    'schedule': '会建云端定时任务、往外发东西：后台不做', 'loop': '会让 AI 一直定时重复跑：后台不做，要反复做的事跟 AI 说清楚或用 /goal',
    'deep-research': '要联网查资料：后台 AI 不联网', 'workflow-launch-exec': '多个 AI 并行的大任务：后台不做', 'batch': '多个 AI 并行的大任务：后台不做',
    'fast': '实测后台用不了（“Fast mode is not available”）', 'resume': '会话由 ThinkCut 自己接（每个项目一条），不用它', 'rename': '会话名字后台用不到',
    'export': '导出整片只认你在审片栏亲手点“这一栏定了”', 'publish': '上传、发布只认你在发布栏亲手点', 'upload': '上传、发布只认你在发布栏亲手点',
    'doctor': '只在终端界面里有效', 'color': '只在终端界面里有效', 'focus': '只在终端界面里有效', 'vim': '只在终端界面里有效', 'terminal-setup': '只在终端界面里有效',
    'ide': '只在终端界面里有效', 'heapdump': '只在终端界面里有效', 'import': '只在终端界面里有效', 'team-onboarding': '只在终端界面里有效',
    'usage-credits': '买额度、改账单要在 Claude 官网自己操作', 'extra-usage': '买额度、改账单要在 Claude 官网自己操作',
}


def parse(text):
    m = CMD.match(str(text or '').strip())
    return (m[1], (m[2] or '').strip()) if m else (None, '')


def commands(eid):
    """给页面补全用：这个引擎下能用的斜杠命令（名字、参数提示、一句说明、谁来做）。"""
    out = [{'name': n, 'args': a, 'desc': d, 'kind': 'app'} for n, (a, d) in APP.items()]
    if eid == 'claude': out += [{'name': n, 'args': a, 'desc': d, 'kind': 'ai'} for n, (a, d) in AI.items()]
    else: out.append({'name': 'goal', 'args': '目标', 'desc': AI['goal'][1] + '（Codex 没有这个命令：ThinkCut 改写成普通要求发给它）', 'kind': 'ai'})
    out += [{'name': n, 'args': '[要做的事]', 'desc': d, 'kind': 'skill'} for n, d in SKILLS.items()]
    return out


def classify(text, eid):
    """用户的这句话怎么处理：
    {'kind': 'text'}                         普通的话，照常交给 AI
    {'kind': 'deny', 'say': 原因}             不放行（不交给 AI）
    {'kind': 'app', 'name': …, 'arg': …}     ThinkCut 自己做
    {'kind': 'ai', 'send': 发给 AI 的字, 'note': 给用户看的说明（可空）}"""
    t = str(text or '').strip()
    if DANGER.search(t): return {'kind': 'deny', 'say': '这句话想改 AI 的权限或关掉沙盒：' + PERM + '。'}
    name, arg = parse(t)
    if not name: return {'kind': 'text'}
    low = name.lower(); low = ALIAS.get(low, low)
    if low in APP: return {'kind': 'app', 'name': low, 'arg': arg}
    sk = low.split(':', 1)[-1]
    if sk in SKILLS:
        if eid == 'claude': return {'kind': 'ai', 'send': f'/thinkcut:{sk}' + (f' {arg}' if arg else ''), 'note': ''}
        return {'kind': 'ai', 'send': f'用 {sk} 技能（先读它的 SKILL.md）' + (f'：{arg}' if arg else '：看看这个项目现在该做什么'), 'note': f'Codex 没有斜杠命令：已改写成“用 {sk} 技能…”发给它。'}
    if low in AI:
        if low == 'goal' and not arg: return {'kind': 'deny', 'say': '/goal 后面要写目标，例如：/goal 把 01_原始素材 里的素材都分好类、登记好'}
        if eid == 'claude': return {'kind': 'ai', 'send': f'/{low}' + (f' {arg}' if arg else ''), 'note': ''}
        if low == 'goal':
            return {'kind': 'ai', 'send': f'目标：{arg}\n一直做到这个目标达成为止：中间不要停下来问能问可不问的，做完自己核对一遍是否真的达成，再说结果；达成不了就说清卡在哪。',
                    'note': 'Codex 没有 /goal 命令：已改写成“一直做到达成为止”的普通要求发给它（不是真的目标循环）。'}
        return {'kind': 'deny', 'say': f'/{low} 在 Codex 后台用不了：Codex 的后台模式一个斜杠命令都不认，发过去它只会当普通文字瞎猜。要用就到“设置”里换成 Claude Code。'}
    why = DENY.get(low)
    if why: return {'kind': 'deny', 'say': f'/{name} 后台用不了：{why}。输入 /help 看能用的命令。'}
    return {'kind': 'deny', 'say': f'/{name} 后台用不了（不是后台 AI 认得的命令，也不是 ThinkCut 的命令）。输入 /help 看能用的命令；要说的是普通的话，就别用 / 开头。'}


def help_text(eid, name):
    lines = [f'能用的斜杠命令（现在用 {name}）：']
    for c in commands(eid):
        lines.append(f"- **/{c['name']}{(' ' + c['args']) if c['args'] else ''}**：{c['desc']}")
    lines.append('导出、上传发布、定稿不能用命令代替，只认你在页面上亲手点；改权限、关沙盒的命令一律不放行。')
    return '\n'.join(lines)
