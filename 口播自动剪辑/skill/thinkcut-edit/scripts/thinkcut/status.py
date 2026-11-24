# 进度：这条片子做到哪一步、哪些结果过期了（上游文件比它新）、下一步跑什么。换会话、换 Claude/Codex、换机器接着做时先看这个。
import json, re, time
from pathlib import Path
from .common import Job, THINKCUT_DIR, die, rj, say, wj

# 给用户看的清单类 MD（2026-11-21 新8，用户：“以后少用md文档跟我沟通……不然就对话框里或者claude 文档也行”）。
# 规则保守、宁可少报：只看 02_制作 和片子目录这两层（不往下钻），文件名含“交付清单”“交付说明”、以“V数字…目标”开头、
# 以“给用户”开头、或含“建议”的 .md 才算；程序要读的 反馈记录.md、项目文案 <项目名>.md、口播文案.md、片子复盘.md、给架构审查_*.md 不算。
USER_MD = re.compile(r'交付清单|交付说明|^V\d+.*目标|^给用户|建议')


def user_md(job: Job):
    root = next((q for q in job.dir.parents if (q / '项目.json').is_file()), None)
    if not root: return []
    keep = {'反馈记录.md', '片子复盘.md', '口播文案.md', f'{root.name}.md'}
    try: keep.add(f"{rj(root / '项目.json').get('name')}.md")
    except (ValueError, OSError, AttributeError): pass
    out = []
    for d in dict.fromkeys((root / '02_制作', job.dir)):
        if not d.is_dir(): continue
        out += [f for f in sorted(d.glob('*.md')) if f.name not in keep and not f.name.startswith('给架构审查_') and USER_MD.search(f.stem)]
    return out


def nav_target(job: Job):
    """导航条程序的 project.json 里 target 该填什么"""
    x, y, w, h = job.fmt['nav']
    t = {'width': job.W, 'height': job.H, 'nav_height': h, 'fps': job['fps']}
    say('出导航条时 project.json 的 target 填：\n' + json.dumps(t, ensure_ascii=False))
    say(f"覆盖区间：0 秒到 {job['frames'] / float(job.fps):.6f} 秒（成片 {job['frames']} 帧）；字幕用 {job['srt']}")
    say(f'出好后：python3 thinkcut_edit.py nav {job.dir} <navigation-full.mp4>（导航条会放在画面 x={x}, y={y}）')
    from . import overlay
    hint = overlay.nav_style_hint(job)      # 半透明/透明底时 style 要写 background_mode、出的是 navigation-full.mov（2026-11-21 新19）
    if hint: say('project.json 的 style 里加：' + json.dumps(hint, ensure_ascii=False) + '；出好后 nav 要给 navigation-full.mov（带透明通道），不是 mp4')
    return t


def nav_draft(job: Job, chapters, force=False):
    """nav-draft：一条命令写出章节导航的 project.json 初稿（导航/project.json），不用去抄上一条片子的（2026-11-23 初稿模式实测）。
    chapters 每项写 “短标签@从哪开始”：从哪开始写秒数，或口播里的一句原话（按 口播字时间.json 找，在上一章之后找）；
    每章到下一章开头为止，最后一章到片尾，第一章从 0 开始。画布、条高、帧率、覆盖区间、底的做法、字幕和它的指纹都自动填。"""
    import hashlib
    out = job.p('导航', 'project.json')
    if not chapters:
        die('要写章节：--chapter "短标签@从哪开始"，每章一个（短标签两到五个字；从哪开始写秒数或口播里的一句原话）。例：\n'
            f'  thinkcut_edit.py nav-draft {job.dir} --chapter "随手定@0" --chapter "开关高度@先说开关" --chapter "插座高度@再说插座"')
    dur = round(job['frames'] / float(job.fps), 6); srt = Path(job['srt'])
    if not srt.is_file(): die(f'字幕文件不在：{srt}')
    tok = rj(job.p('口播字时间.json'))['tokens'] if job.p('口播字时间.json').exists() else []
    text = ''.join(t['c'] for t in tok); pos = [i for i, t in enumerate(tok) for _ in t['c']]
    rows = []; last = -1.0
    for i, c in enumerate(chapters):
        label, sep, at = str(c).partition('@'); label, at = label.strip(), at.strip()
        if not sep or not label or not at or '\n' in label: die(f'章节要写成 “短标签@从哪开始”：{c}')
        try: t = float(at)
        except ValueError:
            if not tok: die('按原话找章节开头要先有 口播字时间.json：先跑 words，或把“从哪开始”写成秒数')
            k = text.find(at)
            while k >= 0 and tok[pos[k]]['t0'] <= last + 1e-6: k = text.find(at, k + 1)
            if k < 0: die(f'章节「{label}」的开头“{at}”在口播里（上一章之后）找不到：照口播实际说的字写，或写秒数')
            t = float(tok[pos[k]]['t0'])
        if i == 0 and t > 0: say(f'第一章「{label}」写的是从 {t:.2f} 秒起，导航条要盖住全片：已改成从 0 开始'); t = 0.0
        if t <= last + 1e-6 or t >= dur: die(f'章节「{label}」的开头 {t:.2f} 秒不在上一章之后、片尾之前（上一章 {max(last, 0):.2f}，片长 {dur:.2f}）：章节按先后顺序写')
        rows.append((label, round(t, 3))); last = t
    segs = [{'chapter': f'c{i + 1}', 'title': label, 'label': label, 'question': '', 'start': a, 'end': rows[i + 1][1] if i + 1 < len(rows) else dur}
            for i, (label, a) in enumerate(rows)]
    x, y, w, h = job.fmt['nav']
    from . import overlay
    proj = {'schema_version': 1, 'source': {'subtitles': str(srt), 'sha256': hashlib.sha256(srt.read_bytes()).hexdigest()},
            'target': {'width': job.W, 'height': job.H, 'nav_height': h, 'fps': job['fps']}, 'duration': dur, 'coverage': [0, dur],
            'subtitle_warnings_reviewed': False, 'style': overlay.nav_style_hint(job), 'segments': segs}
    if out.exists():
        if not force: die(f'{out} 已经有了：要重写加 --force（旧的改名留着）；只改标签直接改这个文件里的 label')
        out.rename(out.with_name(f"project.旧_{time.strftime('%m%d-%H%M%S')}.json"))
    out.parent.mkdir(parents=True, exist_ok=True); wj(out, proj)
    nav = THINKCUT_DIR / '章节导航' / 'scripts' / 'navigation.py'
    say(f'导航条初稿 → {out}（{len(segs)} 章：' + '、'.join(f"{s['label']} {s['start']:.1f}–{s['end']:.1f}" for s in segs) + '）')
    say(f'接着（不等确认，2026-11-22）：python3 "{nav}" validate "{out}" → python3 "{nav}" render "{out}" "{out.parent / "输出"}" → '
        f'thinkcut_edit.py nav "{job.dir}" <输出里的 navigation-full.{"mp4" if not proj["style"] else "mov"}>；'
        'validate 报“字幕异常尚未审核”：逐条看过没问题就把 subtitle_warnings_reviewed 改成 true')
    return out


def anim_state(job: Job):
    """“4K 动画”这一步要不要重做、怎么做（2026-11-13 C8/C13）。返回 (要重做吗, 说明, 下一步命令)。
    以前只比文件时间：任何一次 beats 重跑（哪怕只改音效）都说 4K 动画要重跑。现在看三件事：
    ① 动画镜头的清单/起止和上次总装时不一样（合成/总装检查.json 里存的“镜头起止”）→ 重出动画；
    ② 动画代码比登记的出片新 → 重出；③ 登记的出片比 B肉 新 → 只要重新总装。
    下一步命令按动画清单里登记的工程类型给：代码动画 anim-render，Blender 是工程里的 出片.py 再 anim-assemble。"""
    from . import anim
    d = job.dir; b = d / '合成' / 'B肉.mp4'; lst = d / '合成' / '动画清单.json'; rp = d / '合成' / '总装检查.json'; beats = d / '分镜.json'
    L = rj(lst) if lst.exists() else {}; live = {v.get('project') for v in L.get('shots', {}).values()}
    kinds = {x.get('kind') for x in L.get('projects', []) if x['name'] in live}
    if not kinds:      # 还没登记过：看片子目录里有哪种工程
        kinds = ({'code'} if (d / '动画' / 'build.py').exists() else set()) | ({'blender'} if any(d.glob('Blender_*/出片.py')) else set()) | ({'hyperframes'} if any(d.glob('HF_*/出片.py')) else set())
    how = {'code': 'anim-render（代码动画）', 'blender': '在 Blender_<名字>/ 里 python3 出片.py（出完自动登记）→ anim-assemble',
           'hyperframes': '在 HF_<名字>/ 里 python3 出片.py（出完自动登记）→ anim-assemble'}
    cmd = ' ＋ '.join(how[k] for k in ('code', 'blender', 'hyperframes') if k in kinds) or '先建动画工程：网页动画 hf-new（HyperFrames）、真实空间 blender-new（Blender），在工程里 python3 出片.py → anim-assemble'
    if not b.exists() or not beats.exists(): return False, '', cmd
    was = rj(rp).get('镜头起止') if rp.exists() else None
    if was is None:    # 老片子没存镜头起止：照老办法比文件时间
        return (True, '（上游改过，要重跑）', cmd) if beats.stat().st_mtime > b.stat().st_mtime else (False, '', cmd)
    now = anim.shot_spans(job)
    if now != was:
        ch = sorted({x[0] for x in now if x not in was} | {x[0] for x in was if x not in now})
        return True, f"（动画镜头的清单或起止变了：{'、'.join(ch[:8])}{'…' if len(ch) > 8 else ''}，这些要重出再总装）", cmd
    code = anim.code_newer(job)
    if code:
        return True, '（动画代码比登记的出片新：' + '；'.join(f"{k} 的 {'、'.join(v[:3])}" for k, v in code.items()) + '。改过画面就重出再总装）', cmd
    newer = anim.newer_than_broll(job)
    if newer: return True, f"（登记的出片比 B肉 新：{'、'.join(newer[:8])}，要重新总装）", 'anim-assemble'
    return False, '', cmd


def rules_banner():
    """开工先读的“用户已定规矩”（2026-11-22：返工里约 34 条是规矩写了没照做）。从 内部/架构审查交接.md 第 3 节的表生成，不另抄一份。"""
    from .guard import rules_digest
    lines = rules_digest()
    if not lines: return
    say('【这条片子要遵守的已定规矩】（来自 <程序目录>/内部/架构审查交接.md 第 3 节；细则在各说明，冲突以日期新的为准；check 会拦其中能用程序判断的）')
    for x in lines: say('  - ' + x)
    say('')


def run(job: Job):
    rules_banner()
    d = job.dir
    def f(*p): return d.joinpath(*p)
    def t(p): return p.stat().st_mtime if p.exists() else None
    spec = f('分镜规格.json'); spec_ok = spec.exists() and bool(rj(spec).get('anchors'))
    review = job.get('review_timeline')
    # 整片导出默认写进项目 03_成片/YYMMDD_<项目名>.mp4（2026-11-12 新15），老片子在片子目录 成片/；两处都认，按修改时间取最新
    proj = next((q for q in d.parents if (q / '项目.json').is_file()), None)
    outs = sorted([x for x in ((f('成片').glob('*.mp4') if f('成片').exists() else [])) if '测试' not in x.name] +
                  ([x for x in (proj / '03_成片').glob('*.mp4')] if proj and (proj / '03_成片').exists() else []), key=lambda x: x.stat().st_mtime)
    # 动画不只代码动画（动画/build.py）：Blender 等出片登记在 合成/动画清单.json（2026-11-12 Blender 收进能力）
    lst = f('合成', '动画清单.json'); anim_ok = f('动画', 'build.py').exists() or any(d.glob('Blender_*/出片.py')) or any(d.glob('HF_*/出片.py')) or (lst.exists() and bool(rj(lst).get('projects'))) or f('合成', 'B肉.mp4').exists()   # 登记之前的老片子：有 B肉 就算动画做过
    chk = f('合成', '字幕', '检查', '检查表.json'); bad = [x for x in rj(chk) if x['问题']] if chk.exists() else []
    # (名字, 结果文件, 依赖的上游文件, 下一步命令, 额外判断)
    a_stale, a_note, a_cmd = anim_state(job)
    aroll = Path(job.get('aroll') or '') / '审核' / 'latest_delivery.json'      # 字时间跟着剪口播交付走
    srt = Path(job.get('srt') or '')                                             # 字幕图跟着交付 SRT 走（subfix --srt 会先改 SRT 再写字幕表，不会误报）
    steps = [
        ('字时间', f('口播字时间.json'), [aroll] if job.get('aroll') else [], 'words', None),
        ('分镜规格（AI 写）', spec, [], '照 references/分镜规格.md 写 分镜规格.json', spec_ok),
        ('分镜', f('分镜.json'), [spec] + ([f(*rj(spec)['推拉文件'].split('/'))] if spec.exists() and rj(spec).get('推拉文件') else []), 'beats', None),
        ('动画工程', None, [], 'hf-new（网页动画，HyperFrames）或 blender-new（三维实景），然后照 references/动画做法.md 做', anim_ok),
        ('4K 动画', f('合成', 'B肉.mp4'), [], a_cmd, None),
        ('4K 口播画面', f('合成', 'A肉.mov'), [], 'aroll-render', None),
        ('小圆圈', f('合成', 'B肉_小圆圈.mp4'), [f('合成', 'B肉.mp4'), f('合成', 'A肉.mov')], 'pip', None),
        ('音效表', f('合成', '音效表.json'), [f('分镜.json')], 'sfx', None),
        ('字幕图', f('合成', '字幕', '字幕表.json'), [srt] if job.get('srt') else [], 'subs', None),
        ('字幕整轨', f('合成', '字幕', '字幕整轨.mov'), [f('合成', '字幕', '字幕表.json')], 'subs-track', None),
        ('字幕检查', chk, [f('合成', '字幕', '字幕表.json'), f('合成', 'B肉_小圆圈.mp4')], 'subs-check', not bad),
        ('导航条', f('合成', '导航', '导航整帧.mov'), [], f'nav-target 看参数 → 章节导航（{THINKCUT_DIR / "章节导航"}，设 DBS_VIDEO_NAV_HOME={THINKCUT_DIR / "章节导航" / "配置"}）出条 → nav', None),
        ('达芬奇审核线', f('快照', f'{review}_建好时.json') if review else None, [f('合成', 'B肉_小圆圈.mp4'), f('合成', '字幕', '字幕整轨.mov'), f('合成', '导航', '导航整帧.mov'), f('合成', '音效表.json')], 'review', bool(review)),
        ('4K 成片', None, [], 'export（用户审完之后）', bool(outs)),
    ]
    nxt = None
    say(f"片子「{job.get('title', d.name)}」 画幅 {job['format']}（{job.W}×{job.H}） 达芬奇工程「{job['project']}」")
    for name, out, deps, cmd, extra in steps:
        if out is None: done = bool(extra)
        else: done = out.exists() and (extra is None or extra)
        stale = done and out is not None and out.exists() and any(t(p) and t(p) > t(out) for p in deps)
        if name == '4K 动画': stale = done and a_stale
        mark = '✓' if done and not stale else ('↻' if stale else '·')
        note = ('（素材比审核线新：只改了一条字幕用 subfix，改动多就换个名字重建 review）' if name == '达芬奇审核线' else a_note if name == '4K 动画' else
                '（剪口播重新交付过，要重跑）' if name == '字时间' else '（SRT 改过，要重出字幕）' if name == '字幕图' else '（上游改过，要重跑）') if stale else ''
        if name == '字幕检查' and bad: note = f'（{len(bad)} 条有问题：' + '、'.join(f"#{x['n']}" for x in bad[:8]) + '）'
        if name == '达芬奇审核线' and review and not stale: note = f'（{review}）'
        if name == '4K 成片' and outs: note = f'（{outs[-1].name}）'
        say(f'{mark} {name}{note}')
        if nxt is None and mark != '✓': nxt = (name, cmd)
    if outs and nxt:          # 已导出的片子不要按上游时间去重跑（2026-11-12 体检：定稿片会被提示 anim-render）
        say(f'成片已导出（{outs[-1].name}）：上面标 ↻／· 的只在返修时才需要重跑，没有返修就不用动')
    else:
        say('下一步：' + (f'{nxt[0]} → {nxt[1]}' if nxt else '都做完了'))
    from .check import unseen_samples       # 用户没看过样张的镜头（授权免看、或确认原话写着没看）：交片时发给用户补看；导出前没补看会被拦（2026-11-21 新21）
    for proj, shot, files, why in unseen_samples(job):
        say(f"提醒：{proj} {shot} 的样张用户还没看过（{why}）：交片时把样张发给用户补看（{'、'.join(files)}）；把样张发到审片台“动画”栏，用户点了“可以”就算补看（对话里说的不算，2026-11-22 新1）。"
            "不单独拦导出（2026-11-22 用户定“先做出来再改”）：用户在审片台点了审核线“这一栏定了”就算整片看过")
    bf = f('合成', '审核线建线中.json')          # review 被硬杀留下的（2026-11-22 新2：半成品被当成建好，还删了上一版）
    if bf.exists():
        try: nm = rj(bf).get('名称')
        except (ValueError, OSError): nm = '?'
        say(f'提醒：上次建审核线「{nm}」没做完（进程被结束），达芬奇里那条是半成品，别当成建好、别删上一版；重跑 review 会先自动删掉这条半成品')
    md = user_md(job)
    if md: say(f"提醒：02_制作 下有给用户看的清单类 MD：{'、'.join(f.name for f in md)}。给用户看的内容放对话或 Claude 文档，不写成 MD（用户 2026-11-21 定）；"
               "以后别再写，已有的这几个要不要挪走问用户，别自己删")
    from .guard import old_templates      # 2026-11-22：旧模板 出片.py 不带参数出 4K（审片轮要 1080P，Q159→Q185 复发）
    olds = old_templates(job)
    if olds: say(f"提醒：{'、'.join(olds)} 里的 出片.py 是旧模板（没有 --4k，不带参数就出 4K）：审片轮加 --scale 50 出 1080P，或从 assets/ 里的新模板换一份（旧的改名留着）")
    if not job.fmt['verified']: say(f"提醒：{job['format']} 的版式还没被用户验收过，出第一张图后先给用户看")
    from . import draft      # 初稿五项、占位、最终闸（2026-11-22 用户定“先做出来再改”）
    draft.report(job)
