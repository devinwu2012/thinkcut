#!/usr/bin/env python3
"""口播自动剪辑（thinkcut-edit）：口播剪完之后，自动做卡点动画 B 肉、小圆圈、音效、新样式字幕、章节导航，
在达芬奇里搭好审核时间线，审完导出 4K。Claude 和 Codex 用同一套命令。完整流程见 ../references/动画成片.md（入口路由在 ../SKILL.md）。

用法：python3 thinkcut_edit.py <命令> <片子目录> [参数]     （python3 thinkcut_edit.py -h 看全部命令）"""
import argparse, json, os, shutil, sys, time
from pathlib import Path

if os.environ.get('PYTHONUTF8') != '1':          # 达芬奇脚本模块会把 locale 改成 C，中文输出要 UTF-8 模式
    os.execve(sys.executable, [sys.executable] + sys.argv, {**os.environ, 'PYTHONUTF8': '1'})
sys.path.insert(0, str(Path(__file__).resolve().parent))
from thinkcut.common import Job, wj, say, die   # noqa: E402
from thinkcut import formats   # noqa: E402


# init --force 重写 job.json 时带过来的字段（用户/AI 手填的设置，和命令记下的导航条位置）。其余的重新从剪口播工程读，或者要重新确认（export_ok）、重新建（review_timeline）。
INIT_KEEP = ('subtitle_keywords', 'subtitle_replace', 'subtitle_key_color', 'subtitle_font', 'voice_db', 'voice_peak_db', 'sfx_peak_db', 'sfx_under_voice_lu',
             'sfx_per_minute', 'sfx_library', 'sfx_favorites', 'pip_body', 'pip_min_seconds', 'max_gap', 'flicker_skip', 'ambience', 'nav_at',
             'subtitle_hide', 'voice_polish', '免修音')          # 免修音：用户说这条不修音的原话（2026-11-22 新4）


def default_title(d):
    """没写 --title 时的片名：项目用 项目.json 的 title（没有就用项目文件夹名去掉 6 位编号），不是标准项目才用片子目录名。
    2026-11-23 初稿模式实测：片子目录固定叫“自动剪辑”，审片台顶上片名就显示成了“自动剪辑”。"""
    import json, re
    root = next((q for q in [d, *d.parents] if (q / '项目.json').is_file()), None)
    if not root: return d.name
    try: t = json.loads((root / '项目.json').read_text(encoding='utf-8')).get('title')
    except (OSError, ValueError): t = None
    return (t or '').strip() or re.sub(r'^\d{6}\s+', '', root.name) or root.name


def cmd_note(a):
    """note：往项目的 02_制作/反馈记录.md 末尾加一行，时间用电脑的钟（2026-11-23 初稿模式实测：AI 手写的时间是估的，
    比审片台记的用户点击时间晚了半小时，看起来像“用户在发布前就点了”）。"""
    d = Path(a.dir).expanduser().resolve()
    root = next((q for q in [d, *d.parents] if (q / '项目.json').is_file()), None)
    f = (root / '02_制作' if root else d) / '反馈记录.md'
    if a.who not in ('用户', 'AI自查', '工具拦下'): die('谁先发现只能写：用户、AI自查、工具拦下')
    clean = lambda x: ' '.join(str(x).split()).replace('|', '｜')
    line = f"{time.strftime('%Y-%m-%d %H:%M')} | {clean(a.step)} | {a.who} | {clean(a.what)} | {clean(a.how)}"
    f.parent.mkdir(parents=True, exist_ok=True)
    old = f.read_text(encoding='utf-8') if f.exists() else '# 反馈记录\n\n'
    f.write_text(old + ('' if old.endswith('\n') else '\n') + line + '\n', encoding='utf-8')
    say(f'已记进 {f}：{line}')


def cmd_init(a):
    from thinkcut import aroll, overlay
    from thinkcut.common import ip_config, rj
    d = Path(a.dir).expanduser().resolve(); jf = d / 'job.json'
    if jf.exists() and not a.force: die(f'{jf} 已存在（要重写加 --force：会先备份原文件，并把重点词等手填的设置带过来）')
    a.format = a.format or ip_config(d).get('剪辑.画幅')      # 不写画幅用这条片子所属 IP 配置的
    formats.get(a.format)
    info = aroll.detect(a.aroll)
    job = {'title': a.title or default_title(d), 'format': a.format, 'nav_h': formats.NAV_H_NEW, 'nav_bg': overlay.NAV_BG_NEW, **info, 'subtitle_keywords': [], 'subtitle_replace': {}, 'voice_db': 'auto'}
    note = ''
    if jf.exists():      # 2026-11-13 C5：以前 --force 直接覆盖，用户补的重点词、pip_body 全丢
        old = rj(jf); bak = d / f"job.备份_{time.strftime('%Y%m%d-%H%M%S')}.json"; shutil.copy2(jf, bak)
        kept = [k for k in INIT_KEEP if k in old]; job.update({k: old[k] for k in kept})
        job.pop('nav_h'); job.pop('nav_bg'); job.update({'nav_h': old['nav_h']} if old.get('nav_h') else {}); job.update({'nav_bg': old['nav_bg']} if old.get('nav_bg') else {})      # 导航条高度跟原来的：老片子做到一半不能变版式
        if not a.title and old.get('title') and old['title'] != d.name: job['title'] = old['title']      # 老的默认片名（文件夹名“自动剪辑”）不带过来
        lost = [k for k in old if k not in job]
        note = (f"\n原 job.json 已备份：{bak}\n带过来的设置：{'、'.join(kept) or '没有'}"
                + (f"\n没带过来（要重新确认或重新生成，原值在备份里）：{'、'.join(lost)}" if lost else ''))
    wj(jf, job)
    words_f = d / '口播字时间.json'
    if note and old.get('aroll') != job['aroll'] and words_f.exists():
        # 换了口播：字时间跟着重算，不然审片栏跟播放的字还按旧口播的时间走（2026-11-27 261114：换成真口播后达芬奇放 v4，审片栏高亮的是占位口播的字，差到十几秒）
        wbak = d / f"口播字时间.备份_{time.strftime('%Y%m%d-%H%M%S')}.json"; shutil.copy2(words_f, wbak)
        aroll.words(Job(d)); note += f'\n换了口播，口播字时间.json 已按新口播重算（旧的备份：{wbak.name}）'
    spec = d / '分镜规格.json'
    if not spec.exists():
        wj(spec, {'说明': '卡点 [词, 从第几秒之后找, 偏移秒, 取词尾?]；镜头 a/b 写卡点名或秒；写法见 references/分镜规格.md',
                  'anchors': {}, 'shots': [], 'pip': {'min_seconds': 6, 'right': [], 'never': []},
                  'sounds': {'POP': 'UI+弹出，弹出+6.mp3', 'RISE': '咻-起身01+可爱.mp3', 'WHOOSH': '咻+唰+转场音效.mp3', 'ALERT': '弹出窗口提示音效.mp3',
                             'DING': '闪-想到+叮.mp3', 'TECH': '电子触摸-科技-综艺特效.mp3', 'TYPE': '打字声.mp3', 'TICK': '科技单击3.mp3', 'ZOOM': '唰音效刷出标题.mp3',
                             'MARKER': 'mixkit-pen-marker-line-2998.wav', 'SLASH': 'mixkit-paper-quick-slice-2384.wav', 'SCRAPE': 'mixkit-sand-swish-1494.wav',
                             'SPRAY': 'mixkit-spray-water-or-liquid-3215.wav', 'BUBBLE': 'mixkit-water-bubble-1317.wav'},
                  'gain': {}, 'sfx': []})
    f = formats.get(a.format, job.get('nav_h'))
    say(f"项目文件夹 {d}\n画幅 {a.format} → {f['W']}×{f['H']}（4K），导航条 {f['nav']}，画面区/卡片 {f['card']}{'' if f['verified'] else '【这个画幅的版式还没被用户验收过，第一次出图要先给用户看】'}\n"
        f"达芬奇工程「{info['project']}」主线「{info['main_timeline']}」，{info['frames']} 帧\n字幕 {info['srt']}" + note)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    def c(name, help, dir=True):
        p = sp.add_parser(name, help=help, description=help)
        if dir: p.add_argument('dir', help='项目文件夹（里面有 job.json）')
        return p
    p = c('init', '新建项目目录：读 a-roll 工程，记下画幅、达芬奇工程/主线、字幕'); p.add_argument('--aroll', required=True, help='a-roll 工程目录（有 审核/、交付/）')
    p.add_argument('--format', choices=list(formats.FORMATS), help='不写用 IP 配置的默认画幅（用户 2026-11-24 定）'); p.add_argument('--title'); p.add_argument('--force', action='store_true', help='job.json 已存在也重写：先备份成 job.备份_时间.json，重点词、pip_body、voice_db 等手填设置带过来；export_ok 不带，要重新确认')
    p = c('note', '随手记反馈：往项目 02_制作/反馈记录.md 加一行，时间自动用电脑的钟（dir 写项目文件夹）')
    p.add_argument('--step', required=True, help='环节，如 口播、分镜、封面'); p.add_argument('--who', required=True, help='谁先发现：用户、AI自查、工具拦下')
    p.add_argument('--what', required=True, help='发生了什么（用户原话）'); p.add_argument('--how', required=True, help='当时怎么处理')
    p = c('assume', '全片层面的假设（画幅、没做动画的原因等）：记进 job.json，显示在审片栏栏头下方；不带文字只列出'); p.add_argument('text', nargs='?'); p.add_argument('--clear', action='store_true', help='清空')
    c('doctor', '检查环境（项目文件夹可省）', dir=False).add_argument('dir', nargs='?')
    c('resolve-status', '只读看一眼达芬奇：开没开、哪个工程、当前时间线、每条画面轨（名字、片段数、锁没锁）。后台 AI 用它代替 pgrep 和自己写脚本连达芬奇（会交给审片台代办）', dir=False)
    c('status', '这个项目做到哪一步、哪些要重跑、下一步跑什么（换会话或换机器接着做时先看）')
    c('nav-target', '出导航条时章节导航（程序目录下的 chapters）该用的画布、条高、帧率和覆盖区间')
    p = c('nav-draft', '写出章节导航 project.json 初稿（导航/project.json）：画布、条高、帧率、覆盖区间、字幕指纹自动填，章节用 --chapter 给')
    p.add_argument('--chapter', action='append', metavar='短标签@从哪开始', help='每章一个：短标签两到五个字；从哪开始写秒数或口播里的一句原话'); p.add_argument('--force', action='store_true', help='已有 project.json 也重写（旧的改名留着）')
    c('words', '每个字在成片上的时间 → 口播字时间.json')
    c('aroll-render', '按 a-roll 保留段出 4K 口播画面 → 合成/A肉.mov（做小圆圈和检查图用）')
    c('beats', '分镜规格.json → 分镜.json（卡点秒数、人脸/动画镜头、小圆圈、音效）')
    c('anim-new', '（已下线：老代码动画不再新建，新项目用 blender-new、hf-new；老项目重出用 anim-render）')
    c('anim-build', '把动画工程拼成 动画/动画.html（双击可预览）')
    p = c('anim-render', '出动画 → 合成/B肉.mp4（默认 1080P 审片版，导出前加 --4k 出 4K；只画动画镜头，几段同时出）'); p.add_argument('--all', action='store_true', help='每一帧都画')
    p.add_argument('--4k', dest='full4k', action='store_true', help='只在用户看完 OK、导出前用：出 4K（出片/<镜头>.mp4、登记 100%%）。不加就是 1080P 审片版（出片/<镜头>_预览50.mp4、登记 50%%）')
    p.add_argument('--preview', action='store_true', help=argparse.SUPPRESS)   # 老写法：现在默认就是 1080P，加不加一样
    p.add_argument('--workers', type=int, help='同时开几个 Chrome（默认按本机核数：4K 最多 8、预览最多 10）')
    p.add_argument('--from', dest='t_from', type=float); p.add_argument('--to', dest='t_to', type=float); p.add_argument('--out'); p.add_argument('--force', dest='force_over', action='store_true', help='当前审核线引用着要覆盖的文件也照写（不加就停下：先建新一版审核线，2026-11-22 新3）')
    p = c('anim-stills', '动画抽帧（半尺寸），拼成一张图在“预览”里弹出，并给出“请看哪里、确认什么”'); p.add_argument('--t', required=True, help='秒，逗号分隔')
    # —— 动画硬步骤与总装（2026-11-12 水电反馈，“动画与包装系统”会话加）——
    p = c('blender-new', '复制 Blender 出片模板到 项目文件夹/Blender_<名字>'); p.add_argument('name')
    p = c('hf-new', '复制 HyperFrames 网页动画模板到 项目文件夹/HF_<名字>（文字、数据、卡片、示意类镜头）'); p.add_argument('name')
    p = c('anim-confirm', '记一条用户确认（标签位置/地图；样张授权免看）。样张、草图只认用户在审片台点的“可以／选这个”（点了自动记上），不收对话里的原话（2026-11-22 新1）；整段渲染前没有就拦')
    p.add_argument('--kind', required=True, choices=['样张', '草图', '标签位置', '地图']); p.add_argument('--project', required=True, help='动画工程目录（动画/ 或 Blender_xxx/）')
    p.add_argument('--shots', default='', help='镜头 id，逗号分隔'); p.add_argument('--files', default='', help='样张/草图图片，逗号分隔（样张、草图必须带至少一张存在的图片）')
    p.add_argument('--words', required=True, help='用户原话'); p.add_argument('--check', action='append', help='草图逐条对照：“口播要点=画面里怎么表现”，每条要点一个')
    p.add_argument('--label'); p.add_argument('--pick', help='位置样张里选的字母 A/B/C')
    p.add_argument('--免看', dest='waive', action='store_true', help='授权免看（只样张能用）：用户事先说过“这条不用等我确认”，--words 写这句原话；仍要带样张图，交片时要发给用户补看')
    p = c('anim-gate', '查这个动画工程能不能渲染（缺哪些确认）'); p.add_argument('--project', required=True); p.add_argument('--shots', required=True); p.add_argument('--full', action='store_true')
    p = c('anim-assemble', '按 分镜.json 把登记的动画镜头逐帧拼成 合成/B肉.mp4，并逐镜头逐像素比对'); p.add_argument('--force', dest='force_over', action='store_true', help='当前审核线引用着要覆盖的文件也照写（不加就停下：先建新一版审核线，2026-11-22 新3）')
    p = c('anim-labelcheck', '代码动画标签逐帧自检：字压物体的帧数 → 动画/自查/标签重叠.json（要 0 帧；有就停，check 不过、review 不建审核线）'); p.add_argument('--shots', help='只查这几个镜头，逗号分隔')
    p.add_argument('--step', type=int, default=5, help='粗查间隔帧数（默认 5；有压字或字附近在动的区间再逐帧查；1=全部逐帧）'); p.add_argument('--w', type=int, default=1080, help='截图宽度')
    p.add_argument('--workers', type=int, help='同时开几个 Chrome（默认按本机核数，最多 12）')
    p = c('placeholder-images', '缺画面时生成占位图：分镜规格.json 里写了 "占位图": "画面描述"（几个场景写成列表）的镜头，缺图的用 Codex 生图到 实拍/占位图/（不加角标、一个场景一张、同组同机位）')
    p.add_argument('--shots', help='只生成这几个镜头，逗号分隔'); p.add_argument('--force', action='store_true', help='已有的也重出（旧图改名留着）'); p.add_argument('--workers', type=int, default=3, help='同时生成几个镜头（默认 3）')
    c('pip', '把人脸小圆圈烧进动画 → 合成/B肉_小圆圈.mp4').add_argument('--force', dest='force_over', action='store_true', help='当前审核线引用着要覆盖的文件也照写（不加就停下：先建新一版审核线，2026-11-22 新3）')
    p = c('sfx', '音效表：起音对齐卡点 → 合成/音效表.json（用了用户否掉的音效、音效和关键词起点差超过 0.15 秒就停）')
    p.add_argument('--allow-sounds', action='store_true', help='用户听过认可后，放行音效规矩违规（黑名单音效、和关键词差超过 0.15 秒），放行的在音效表里留记录；check 只提醒')
    p = c('subs', '新样式字幕图片 → 合成/字幕/，接着自动重拼字幕整轨（job.json 的 subtitle_hide 里的句子不进字幕表）'); p.add_argument('--srt'); p.add_argument('--force', dest='force_over', action='store_true', help='当前审核线引用着要覆盖的文件也照写（不加就停下：先建新一版审核线，2026-11-22 新3）')
    c('subs-track', '字幕图片按帧拼成透明整轨 → 合成/字幕/字幕整轨.mov').add_argument('--force', dest='force_over', action='store_true', help='当前审核线引用着要覆盖的文件也照写（不加就停下：先建新一版审核线，2026-11-22 新3）')
    c('subs-check', '逐条把字幕叠到当时画面上检查 → 合成/字幕/检查/')
    p = c('subfix', '改一条字幕里的字并在达芬奇里换掉那一段'); p.add_argument('--n', type=int, required=True); p.add_argument('--old', required=True); p.add_argument('--new', required=True)
    p.add_argument('--srt', action='store_true', help='同时改 SRT 文件'); p.add_argument('--name', help='时间线（默认审核时间线）')
    p = c('nav', '章节导航条 MP4 → 整帧透明片'); p.add_argument('bar'); p.add_argument('--at', type=float, default=0.0, help='从成片第几秒放起'); p.add_argument('--force', dest='force_over', action='store_true', help='当前审核线引用着要覆盖的文件也照写（不加就停下：先建新一版审核线，2026-11-22 新3）')
    p = c('set-format', '把一条时间线设成 4K 画幅（要写 --name；不能是主线）'); p.add_argument('--name')
    p = c('review', '在达芬奇复制主线，搭好卡点审核时间线'); p.add_argument('--name'); p.add_argument('--broll'); p.add_argument('--force-len', action='store_true')
    p.add_argument('--allow-gaps', action='store_true', help='成片上有 ≥0.27 秒停顿也照建（用户要留的停顿）')
    p.add_argument('--changes', help='改动清单 json：在改动处打绿色标记（名称“改：…”、备注写改了什么），不再建待确认线')
    p.add_argument('--extra', help='额外轨道清单 json（花字轨、实拍原声会自动放，别的轨道写这里）')
    c('check', '交审前自检：气口、音效对画面、动画闪烁、露脸段空白、几何遮挡（圈、花字、字幕、人脸）、人声响度底噪、黑名单音效 → 合成/自检结果.json')
    p = c('snap', '时间线快照（改动前后各拍一次再 diff）'); p.add_argument('--name', required=True); p.add_argument('--out')
    p = sp.add_parser('diff', help='比对两个快照'); p.add_argument('a'); p.add_argument('b')
    p = c('stills', '从达芬奇导出关键帧'); p.add_argument('--name', required=True); p.add_argument('--frames', required=True, help='成片帧号，逗号分隔')
    p = c('export', '导出 4K 成片（mp4）'); p.add_argument('--name', help='要导的时间线（默认审核线；不能是主线）'); p.add_argument('--seconds', type=float, help='只导前几秒（测试，最多 30 秒；不做音量平衡，不改音效表/音量表）')
    p.add_argument('--codec', default='H265')
    p.add_argument('--out', help='写成 .mp4/.mov 结尾就直接导成这个文件；写文件夹就放进去；不写：整片导到 03_成片/YYMMDD_项目名.mp4（同名旧版挪进 历史/）＋同名 .srt')
    p.add_argument('--user-ok', help='用户说过的原话，只当备注记进导出记录。整片导出的闸是：用户在审片台点过审核线“定稿”（2026-11-22 用户定“先做出来再改”，取代原来的“带原话才导”；AI 不能替用户点）')
    p.add_argument('--未看样张用户同意', dest='unseen_ok', help='有用户没看过样张的动画镜头时（check 会列出），用户同意照导的原话；记进导出记录')
    g = p.add_mutually_exclusive_group()   # 默认先用达芬奇 21.1 原生“标准化音频电平”平衡（人声真峰值 -1、音效各自 -26 LKFS），发现手调就停下
    g.add_argument('--balance', dest='balance', action='store_const', const=True, default=None, help='强制平衡，连手调过的音量也覆盖')
    g.add_argument('--no-balance', dest='balance', action='store_const', const=False, help='不平衡，按审核线上现在的音量直接导')
    p = c('export-peak', '只重跑导出后的“量响度和真峰值、超了就限幅”这一步（export 导完但量不出来时用）'); p.add_argument('--file', required=True, help='已导出的成片')
    p = c('voice-polish', '口播降噪修音：审核线上达芬奇人声隔离渲纯口播 → ffmpeg 修音 → 放“口播（隔离修音）”轨、关音频 1；以后 review/export 都按它')
    p.add_argument('--name', help='时间线（默认审核线）'); p.add_argument('--amount', type=int, default=70, help='人声隔离强度（默认 70）')
    p = c('aroll-polish', '剪口播交付后直接修音（2026-11-27 261114 新1）：不要动画项目，在主线副本「<主线>_修音」上做 voice-polish（主线不动）；dir 写剪口播工程（<片名>_AI剪辑）')
    p.add_argument('--amount', type=int, default=70, help='人声隔离强度（默认 70）')
    p = c('workbench', '审片台：起本机网页（8 个栏目：写稿＝页面上的“文稿”、文案＝页面上的“口播精剪”、口播试听＝“配音试听”、动画、审核线＝“审片”、导航文字（并入审片）、封面、上传文案＝“发布”），打印网址；已在跑就复用。还没 init 时也能开（项目文件夹写项目文件夹：只有文案栏；剪完口播多一个口播精剪栏）。发布一项给用户看、读结果、回复也用它。说明见 references/审片工作台.md')
    p.add_argument('--round', type=int, help='换到第几轮（每轮新审核线建好后写）'); p.add_argument('--port', type=int, help='指定端口（默认从 8765 起找空的）')
    p.add_argument('--aroll', help='口播阶段（还没 job.json）用：剪口播工程目录；有 job.json 时以它的 aroll 为准。项目文件夹也可以直接写剪口播工程或项目根')
    p.add_argument('--column', help='打开到哪一栏（review 建好审核线后打开到“审核线”）')
    p.add_argument('--stop', action='store_true', help='停掉本项目的审片台'); p.add_argument('--list', action='store_true', help='读结果：这一轮审核线的标记＋其余栏目用户已定稿或写了要求、AI 还没处理的项和用户改的字')
    p.add_argument('--all', action='store_true', help='和 --list 一起用：列全部轮次、全部项'); p.add_argument('--reply', metavar='编号', help='回复：审核线标记写数字（要 --status）；其余栏目的项写 [] 里的编号（如 动3、封1、改2）')
    p.add_argument('--status', choices=['已改', '没改', '问你'], help='和 --reply 一起用（审核线标记）：已改／没改；拿不准范围就写 问你（--note 写问题，最好给两三种理解让用户挑），等用户在卡片上回答');
    p.add_argument('--options', metavar='选项', help='和 --reply 一起用：给用户点的选项按钮，竖线分开，写成一眼看懂的大白话，如 "A 片尾多留 1 秒|B 不加时长、提前出|C 只在最后闪一下"（审核线标记配 --status 问你；其余栏目的项直接 --reply 编号 --note 问题 --options …）');
    p.add_argument('--where', metavar='时间段', help='和 --reply 数字 --status 已改/没改 一起用：这条意见实际改了哪几处，逗号分隔的时间段，秒或 分:秒，如 "0:12-0:15,1:03-1:08"（页面上列成可点的时间，全文、时间条上标“AI 连带改了这里”）'); p.add_argument('--note', help='和 --reply 一起用：改了什么／为什么没改；和 --publish 一起用：这一版的说明')
    p.add_argument('--publish', metavar='栏目', help='发布一项给用户看：文案、口播试听、动画、导航文字、封面、上传文案')
    p.add_argument('--wait', action='store_true', help='挂着等用户在页面上点“提交 AI”：点了就打印要处理的清单并退出（Claude Code 放后台跑，退出会叫醒 AI；处理完再起一次接着等）')
    p.add_argument('--wait-timeout', type=float, default=0, metavar='秒', help='和 --wait 一起用：最多等多少秒（默认一直等）')
    p.add_argument('--rewrite', metavar='稿N', help='文案栏：针对用户的一条意见交改写建议（把哪几段改成什么，可以多处、可以整篇），配 --json 改写.json、可带 --note 说明；只是建议，用户在页面上逐处点“采纳”才写进文案文件'); p.add_argument('--json', metavar='文件', help='和 --rewrite 一起用：改写内容（格式见 references/审片工作台.md「文案栏」）')
    p.add_argument('--withdraw', metavar='编号', help='撤回一项（发布错了或用不上了；不删，页面和 --list 不再显示），可带 --note 说明');
    p.add_argument('--add-material', nargs='+', metavar='路径', help='往项目 01_原始素材 里放素材：这台 Mac 上的文件或文件夹（复制，原文件不动；同名不覆盖、改成“名字 (2)”；复制到一半断了不留半截）。和文稿栏“素材”、对话框拖进来的是同一套，记在 审片工作台/备料.json（2026-11-27 对话框）')
    p.add_argument('--title', help='和 --publish 一起用：卡片标题'); p.add_argument('--look', help='和 --publish 一起用：一句“看哪里、确认什么”')
    p.add_argument('--kind', choices=['多选一', '可以', '试听', '改字'], help='和 --publish 一起用：多选一（每个选项下“选这个”）、可以、试听（音频）、改字（默认 可以）')
    p.add_argument('--files', help='和 --publish/--reply 一起用：图片、视频（mp4/mov/webm）或音频，逗号分隔（--reply 带文件＝换新版）'); p.add_argument('--labels', help='每个文件的名字，逗号分隔（多选一默认 A、B、C）')
    p.add_argument('--key', help='和 --publish 一起用：这一项的固定名字，同名再发布＝换新版'); p.add_argument('--id', help='和 --publish 一起用：换新版的那一项的编号')
    p.add_argument('--assume', metavar='假设', help='和 --publish 一起用：初稿里 AI 拿不准、没问用户、按最合理的做了的地方，卡片上显示“我是这么假设的：…”（2026-11-22 用户定：先做出来再改）')
    p.add_argument('--draft', action='store_true', help='和 --publish 一起用：这一版是没问用户、按 AI 判断做的初稿（卡片上挂“初稿”小标签）')
    p.add_argument('--placeholder', action='store_true', help='和 --publish 一起用：里面是占位的东西（机器配音、生成图），卡片上挂“占位”小标签')
    p.add_argument('--anim', choices=['样张', '草图'], help='动画样张/草图：用户点了就记进 合成/确认记录.json（anim.gate 认）；要 --project、--shots')
    p.add_argument('--project', help='和 --anim 一起用：动画工程文件夹'); p.add_argument('--shots', help='和 --anim 一起用：镜头，逗号分隔')
    p.add_argument('--check', action='append', help='和 --anim 草图 一起用：“口播要点=画面里怎么表现”，每条要点一个；只对某个选项的写“A:要点=画面”')
    p = c('home', '首页：一个固定入口（本机 8760 端口）看到所有项目、新建项目，双击一个项目进入它的审片台；--wait＝AI 一条命令等所有项目。说明见 references/审片工作台.md「首页」', dir=False)
    p.add_argument('--root', help='放项目的文件夹（默认是配置档里默认 IP 的项目文件夹；也可设环境变量 THINKCUT_VIDEO_ROOT）'); p.add_argument('--port', type=int, help='首页端口（默认 8760）')
    p.add_argument('--film-ports', help='首页替项目起审片台时用的端口范围，如 8765-8784（默认就是这个）')
    p.add_argument('--stop', action='store_true', help='停掉首页（各个项目的审片台不受影响）')
    p.add_argument('--wait', action='store_true', help='AI 用：挂着等所有项目，任何一个项目有人点“提交 AI”就打印是哪个项目的什么事并退出（Claude Code 放后台跑）')
    p.add_argument('--wait-timeout', type=float, default=0, metavar='秒', help='和 --wait 一起用：最多等多少秒（默认一直等）')
    a = ap.parse_args()
    if a.cmd == 'init': return cmd_init(a)
    if a.cmd == 'note': return cmd_note(a)
    if a.cmd == 'assume':
        from thinkcut import draft; return draft.assume_cmd(Job(a.dir), a.text, a.clear)
    if a.cmd == 'diff':
        from thinkcut import resolve; return resolve.diff(a.a, a.b)
    if a.cmd == 'doctor':
        from thinkcut import doctor; sys.exit(0 if doctor.run(Job(a.dir) if a.dir else None) else 1)
    if a.cmd == 'resolve-status':
        from thinkcut import resolve; print(json.dumps(resolve.live_status(), ensure_ascii=False, indent=1)); return
    if a.cmd == 'home':
        from thinkcut import home; return home.cli(a)
    if a.cmd == 'workbench':            # 口播阶段（还没 job.json）也能开，只有“口播剪辑”栏（2026-11-22 用户定）
        from thinkcut import workbench; return workbench.cli(workbench.open_film(a.dir, a.aroll), a)
    if a.cmd == 'aroll-polish':
        from thinkcut import voice; return voice.aroll_cmd(a.dir, a.amount)
    job = Job(a.dir)
    if getattr(a, 'force_over', False):           # subs/subs-track/pip/anim-assemble/anim-render/nav 的 --force：当前审核线引用着也覆盖（2026-11-22 新3）
        from thinkcut.common import allow_overwrite; allow_overwrite()
    from thinkcut import aroll, beats, anim, overlay, sfx, subs, resolve, status, check, voice
    if a.cmd == 'check': sys.exit(0 if check.run(job) else 1)
    if a.cmd == 'placeholder-images':
        from thinkcut import draft; return draft.gen_images(job, a.shots.split(',') if a.shots else None, a.force, a.workers)
    {'status': lambda: status.run(job), 'nav-target': lambda: status.nav_target(job), 'nav-draft': lambda: status.nav_draft(job, a.chapter, a.force),
     'words': lambda: aroll.words(job), 'aroll-render': lambda: aroll.render(job), 'beats': lambda: beats.run(job),
     'anim-new': lambda: anim.new(job), 'anim-build': lambda: anim.build(job),
     'anim-render': lambda: anim.render(job, a.all, a.t_from, a.t_to, Path(a.out) if a.out else None, preview=not a.full4k, n_workers=a.workers),
     'anim-stills': lambda: anim.stills(job, a.t.split(',')),
     'blender-new': lambda: anim.blender_new(job, a.name), 'hf-new': lambda: anim.hf_new(job, a.name), 'anim-assemble': lambda: anim.assemble(job), 'anim-labelcheck': lambda: anim.label_check(job, a.step, a.w, a.shots.split(',') if a.shots else None, a.workers),
     'anim-confirm': lambda: anim.confirm_cmd(job, a.kind, a.project, a.shots.split(','), [f for f in a.files.split(',') if f], a.words, a.check, a.label, a.pick, a.waive),
     'anim-gate': lambda: say('可以渲染' if not (g := anim.gate(job, Path(a.project), a.shots.split(','), a.full)) else '还不能渲染：\n- ' + '\n- '.join(g)), 'pip': lambda: overlay.pip(job), 'sfx': lambda: sfx.run(job, a.allow_sounds),
     'subs': lambda: subs.render(job, a.srt, with_track=True), 'subs-track': lambda: subs.track(job), 'subs-check': lambda: subs.sheet(job),
     'subfix': lambda: resolve.subfix(job, a.n, a.old, a.new, a.name, a.srt), 'nav': lambda: overlay.nav(job, a.bar, a.at),
     'set-format': lambda: resolve.set_format(job, a.name), 'review': lambda: resolve.review(job, a.name, a.broll, a.force_len, a.allow_gaps, a.changes, a.extra),
     'snap': lambda: resolve.snap(job, a.name, a.out), 'stills': lambda: resolve.stills(job, a.name, [int(x) for x in a.frames.split(',')]),
     'export-peak': lambda: resolve.limit_peak(Path(a.file).expanduser()),
     'voice-polish': lambda: voice.run_cmd(job, a.name, a.amount),
     'export': lambda: resolve.export(job, a.name, a.out, a.codec, seconds=a.seconds, balance=a.balance, user_ok=a.user_ok, unseen_ok=a.unseen_ok)}[a.cmd]()


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'bin')); import thinkcut_broker; thinkcut_broker.hook(__file__)      # noqa: E402  后台 AI：交给审片台代办（2026-11-26）
    main()
