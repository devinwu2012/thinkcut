# 动画（B 肉）：Blender 和 HyperFrames 网页动画两种，按镜头选（2026-11-20 用户定，见 references/动画风格.md）；老的代码动画（Three.js 网页，无头 Chrome 出帧）新片子停用，已有老工程的片子还能重出。
# 本文件管两种动画共用的：
#   ① 硬步骤（2026-11-12 水电反馈）：讲关系/对比/档位的镜头先出逻辑草图静帧给用户确认（新5）；整段渲染前要有“样张已确认”（新3）；
#      每个镜头写清对应的口播原话要点（2026-11-13 起写在 分镜规格.json 的 shots 里，老工程的 镜头.json 照读）；确认记录都在 合成/确认记录.json（样张/草图路径＋用户原话＋当时动画代码的指纹）。
#   ② 出片登记：合成/动画清单.json（哪个工程出了哪个镜头、画面事件表）、合成/渲染尺寸.json（预览降分辨率的要在导出前换成 100%，新8）。
#   ③ 总装：按 分镜.json 把各镜头逐帧拼成 合成/B肉.mp4，并逐镜头逐像素比对（新11⑪）。
#   ④ 代码动画出片：分段开 Chrome、每段限时、首帧就绪再截（新11⑥）；几段同时出（2026-11-21 新3）；默认出半尺寸（审片轮），导出前 --4k 才出 4K（2026-11-21 Q159 复发）。
#   ⑤ 实拍镜头、图片镜头、在动画镜头某区间盖实拍（2026-11-21 新16）：写在 分镜规格.json，总装时自动做成片段、登记、拼进去。
import contextlib, datetime, hashlib, json, math, os, shlex, shutil, signal, socket, subprocess, sys, tempfile, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .common import Job, ASSETS, die, rj, wj, say, run

CAP = Path(__file__).resolve().parents[1] / 'tools' / 'cap.mjs'   # 无头 Chrome 截图程序（连同 chrome.mjs）。老代码动画模板 2026-11-21 下线，重出老片子还要用的只有它
BLENDER_TEMPLATE = ASSETS / 'blender_template'
HF_TEMPLATE = BLENDER_TEMPLATE.parent / 'hyperframes_template'
RELATION = ('关系', '对比', '档位')          # 这几类镜头做动画前必须先确认逻辑草图
IMAGES = ('.jpg', '.jpeg', '.png', '.webp')   # 样张/草图确认要带的图片
DIRECTOR = ('讲什么', '口播要点', '画面', '运镜', '工程', '不涉及布局')   # 分镜规格.json 每个镜头可写的导演分镜字段
# 地图（2026-11-21《装修最后那点细节》新3）：标准地图要审图号，改淡、裁切、放大都算编辑要送审；那条片子做了 3 版才改成距离圈。
# 镜头的 画面/讲什么 写到这些词就拦，用户确认要用地图（anim-confirm --kind 地图）才放行。同一个半句里前面写了“不画/不用/没有/无…”的（如“不画任何国界、省界”）不算。
MAP_WORDS = ('地图', '国界', '省界', '省份', '边界线', '行政区')
MAP_NOT = r'不(?:画|用|要|带|放|做|出|显示|是|含|再)|没有|别(?:画|用)|去掉|无(?=任何|地图|国界|省界|省份|边界|行政区)'
MAP_HINT = ('标准地图要有审图号，改淡、裁切、放大、换色都算编辑，要重新送审。先跟用户说清这条规则，建议改用不画边界的图表：HyperFrames 模板的距离圈组件'
            '（共用/距离圈.js：圆心城市＋按真实方位和距离摆城市，写法见 references/动画做法.md 第 7b 节），并把分镜规格里的 画面 改成距离圈的写法。'
            '用户听完仍确认要地图，再记 thinkcut_edit.py anim-confirm <项目文件夹> --kind 地图 --project <工程文件夹> --shots <镜头> --words "用户原话"')


def map_words(d):
    """镜头的 画面/讲什么 里提到的地图类词（同一个半句里前面有否定的不算），没有返回空列表。"""
    import re
    text = ' '.join(str(d.get(k) or '') for k in ('画面', '讲什么'))
    def said(w):      # 这个词有没有哪一处不在否定的半句里（半句＝往前到上一个逗号、句号、分号、冒号、括号、箭头）
        return any(not re.search(MAP_NOT, re.split(r'[，。；：,;:！!？?（）()→\n]', text[:m.end()])[-1]) for m in re.finditer(w, text))
    return [w for w in MAP_WORDS if said(w)]


def map_unconfirmed(job, info, project=None):
    """info={镜头: 导演分镜字段}：画面写到地图、用户还没确认“就要地图”的镜头 → [(镜头, [词])]。project 给了只认记在这个工程名下的确认（渲染前硬拦用）。"""
    recs = [r for r in _records(job)['items'] if r['kind'] == '地图' and (project is None or r['project'] == project)]
    out = []
    for s, d in info.items():
        mw = map_words(d or {})
        if mw and not any(s in r['shots'] for r in recs): out.append((s, mw))
    return out


def adir(job): return job.dir / '动画'


def cap(job):
    """无头 Chrome 截图程序：统一用 scripts/tools/cap.mjs（分段限时、首帧不黑都在它里面），用 --page 指向老工程的 动画.html，老工程不用改文件。"""
    return ['node', str(CAP), '--page', str(adir(job) / '动画.html')]


# ───────────────────────── 确认记录与硬拦 ─────────────────────────
def _spec_director(film: Path):
    """分镜规格.json 里每个镜头写的导演分镜字段：{镜头: {讲什么, 口播要点, 画面, 运镜, 工程, 不涉及布局}}（没写的镜头不在里面）。"""
    sp = Path(film) / '分镜规格.json'
    if not sp.exists(): return {}
    out = {}
    for s in rj(sp).get('shots', []):
        info = {k: s[k] for k in DIRECTOR if k in s}
        if info: out[s['id']] = info
    return out


def shots_doc(project: Path, film: Path = None):
    """导演分镜：每个动画镜头讲什么类型、对应的口播原话要点、画面和运镜。
    2026-11-13 起口播要点只写一份：片子目录 分镜规格.json 的 shots 里（和卡点、镜头起止同一处，Q045）。
    老片子工程里的 镜头.json 照样读；同一镜头两处都写了，以 分镜规格.json 为准。
    返回 ({镜头: 说明}, 顶层)；两处都没有返回 None。film 默认是工程的上一级（片子目录）。"""
    project = Path(project); film = Path(film) if film else project.parent
    shots, top, found = {}, {}, False
    old = project / '镜头.json'
    if old.exists():
        d = rj(old); found = True
        top = {k: v for k, v in d.items() if k != 'shots'}
        shots = {s['shot']: dict(s) for s in d.get('shots', [])}
    for sid, info in _spec_director(film).items():
        found = True; shots[sid] = {**shots.get(sid, {}), 'shot': sid, **info}
    return (shots, top) if found else None


def project_shots(project: Path, film: Path = None):
    """这个动画工程负责哪些镜头：老工程看 镜头.json；新写法看 分镜规格.json 里 "工程" 写的是不是这个工程的文件夹名。"""
    project = Path(project); film = Path(film) if film else project.parent
    old = project / '镜头.json'
    if old.exists(): return [s['shot'] for s in rj(old).get('shots', [])]
    return [sid for sid, info in _spec_director(film).items() if info.get('工程') == project.name]


def _legacy_shared_js(project):
    """老 HyperFrames 工程 共用/ 里基础脚本的旧名（bin/旧名兼容.sh 的 THINKCUT_LEGACY_SHARED_JS；没有就不认）：照旧算进指纹，老片子的样张确认不作废。"""
    name = os.environ.get('THINKCUT_LEGACY_SHARED_JS')
    return list(project.glob('共用/' + name)) if name else []


def _code_files(project: Path, shots=None):
    """算进指纹的动画代码文件（顺序固定，改了老确认记录会全部作废）。shots 给了：镜头/ 下只算这些镜头自己的 html（HyperFrames 工程，改一个镜头不作废别的镜头的确认）。"""
    files = sorted([*project.glob('*.py'), *project.glob('src/*.js'), *project.glob('*.json'), *project.glob('timeline.js'), *project.glob('shell.html')])
    files = [f for f in files if not (f.name in ('标签定位.json',) or f.name.startswith(('事件', '续出', '出片')))]
    # HyperFrames 工程（2026-11-20）：镜头页面和共用样式脚本也算。接在后面，老工程没有这两个文件夹，指纹不变。
    pages = [f for f in project.glob('镜头/*.html') if f.name != '示例.html' and (shots is None or f.stem in shots)]
    return files + sorted(f for f in [*pages, *project.glob('共用/*.css'), *project.glob('共用/基础.js'), *_legacy_shared_js(project), *project.glob('共用/距离圈.js'), *project.glob('共用/本片_*.js')])    # 本片_*.js＝这条片子自己的共用画法（2026-11-24 初稿模式第二次实测），改了全部镜头重新确认


def fingerprint(job: Job, project: Path, shots):
    """动画代码的指纹：脚本、场景/标签/布局/镜头数据和这些镜头在成片里的起止。改了任何一个，之前的样张确认就不作数。"""
    h = hashlib.sha256(); project = Path(project)
    for f in _code_files(project, set(shots)):
        h.update(f.name.encode()); h.update(f.read_bytes())
    b = job.p('分镜.json')
    if b.exists():
        for s in rj(b)['shots']:
            if s['id'] in shots: h.update(f"{s['id']}:{s['a']:.4f}-{s['b']:.4f}".encode())
    d = _spec_director(job.dir)       # 写在 分镜规格.json 里的口播要点/画面改了，确认同样作废（原来改 镜头.json 也作废）
    for s in sorted(x for x in shots if x in d):
        h.update(f"{s}:{json.dumps(d[s], ensure_ascii=False, sort_keys=True)}".encode())
    return h.hexdigest()[:16]


def _records(job): p = job.p('合成', '确认记录.json'); return rj(p) if p.exists() else {'items': []}


def confirm(job: Job, kind, project, shots, files, words, checks=None, label=None, pick=None, waive=False):
    """记一条用户确认。kind：样张 / 草图 / 标签位置 / 地图。words 必须是用户原话（不是 AI 的转述）。
    地图（2026-11-21 新3）：镜头的画面写到地图时，用户听过审图号和编辑送审规则后仍确认要用地图才记；不用带图。
    草图要逐条对照：checks=['口播要点=画面里怎么表现', …]，分镜规格.json（老工程：镜头.json）里这个镜头的每条口播要点都要有一条。
    标签位置：label=标签 id，pick=A/B/C（位置样张里的字母），定下来写成固定位置。
    waive（授权免看，2026-11-20）：只给样张用。用户事先说过“这条不用等我确认”，AI 自己放行整段出片；words 写这句原话，图照样要带，交片时要把样张发给用户补看。"""
    project = Path(project).resolve(); shots = [s for s in shots if s]
    if waive and kind != '样张': die(f'授权免看只有样张能用，{kind}没有免看（要用户自己确认）')
    if not project.is_dir(): die(f'动画工程文件夹不存在：{project}（--project 写工程文件夹的完整路径；写错了会记成一条对不上的确认）')
    if not words or len(words.strip()) < 2: die('要写用户原话（--words）')
    if kind == '样张' and not waive:      # 2026-11-21 新21：words 里写着用户没看过，却记成确认（Q023 复发）
        from .check import not_seen
        if not_seen(words): die('--words 里写着用户没看过（如“用户未逐张看”），这不是用户确认：用户事先授权过不用等他确认就改用 --免看（交片时把样张发给用户补看）；没授权就先给用户看')
    for f in files:
        if not Path(f).is_file(): die(f'样张/草图文件不存在：{f}')
    if kind == '地图':
        doc = shots_doc(project, job.dir) or ({}, {})
        if not any(map_words(doc[0].get(s, {})) for s in shots): die('这些镜头的 画面/讲什么 没写到地图，不用记地图确认')
    if kind in ('样张', '草图') and not any(Path(f).suffix.lower() in IMAGES for f in files):   # 2026-11-13 C12：以前不带图也能记
        die(f'{kind}确认要带给用户看过的那张图（--files 图片路径，{"/".join(IMAGES)}，至少一张）：没有图就没法回查用户确认的是什么')
    rec = dict(kind=kind, project=project.name, shots=shots, files=[str(Path(f).resolve()) for f in files], words=words.strip(),
               at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), fingerprint=fingerprint(job, project, shots),
               fingerprints={s: fingerprint(job, project, [s]) for s in shots})   # 每个镜头单独一份（2026-11-13 审查）：确认时和渲染时带的镜头不一样也认
    if waive: rec.update(免看=True, 工程路径=str(project))   # 记的是“用户授权免看”，不是“用户看过样张”
    if kind == '草图':
        doc = shots_doc(project, job.dir)
        if not doc: die(f'分镜规格.json 里这些镜头还没写口播要点（每个镜头 "口播要点": [原话要点, …]），先写它再确认草图')
        pairs = [c.split('=', 1) for c in (checks or []) if '=' in c]; got = {a.strip(): b.strip() for a, b in pairs}
        for s in shots:
            points = doc[0].get(s, {}).get('口播要点')
            if not points or not isinstance(points, list): die(f'{s} 在 分镜规格.json 里还没写口播要点（"口播要点": [原话要点, …]，要写成列表），先写它再确认草图')
            miss = [k for k in points if k not in got]
            if miss: die(f'{s} 草图要逐条对照口播要点，还缺：' + '；'.join(miss) + '（--check "要点=画面里怎么表现"）')
        rec['对照'] = [dict(要点=a, 画面=b) for a, b in got.items()]
    if kind == '标签位置':
        if not label or not pick: die('标签位置要写 --label 和 --pick（A/B/C）')
        lp = project / '标签定位.json'; pos = rj(lp) if lp.exists() else {}
        if label not in pos or not pos[label].get('options'): die(f'{label} 没有位置样张候选，先出样张')
        k = 'ABC'.index(pick.upper()); o = pos[label]['options'][k]
        pos[label].update(mode='画面区域', rect=o['rect'], unsure=False, decided=f'用户选 {pick.upper()}：{words.strip()}'); wj(lp, pos)
        rec.update(label=label, pick=pick.upper())
    r = _records(job); r['items'].append(rec); wj(job.p('合成', '确认记录.json'), r)
    say(f'已记下 {"样张授权免看（用户没看过，交片时要把样张发给用户补看）" if waive else kind + " 确认"}：{project.name} {",".join(shots)}（{rec["at"]}）')
    return rec


BOARD_HINT = '让用户在审片台“动画”栏点“可以”（样张）／“选这个”（草图）'


def board_clicks(job: Job, kind, project: Path, shot):
    """审片台（<片子目录>/审片工作台/审片台.json）上用户对这个镜头的样张/草图点过的“可以”“选这个”，而且点的那一版的代码指纹和现在一样、
    当时已记进 合成/确认记录.json（事件里 anim.ok）。返回 [(项编号, 事件)]。只读这个文件，不改（审片台页面归 board.py 管）。"""
    f = job.dir / '审片工作台' / '审片台.json'
    if not f.exists(): return []
    try: d = rj(f)
    except (ValueError, OSError): return []
    now = fingerprint(job, project, [shot]); out = []
    for it in d.get('items', []):
        if it.get('column') != '动画' or it.get('撤回'): continue
        vers = {v.get('v'): v for v in it.get('versions', [])}
        for e in it.get('events', []):
            a = (vers.get(e.get('v')) or {}).get('anim') or {}
            if (e.get('type') == 'user' and e.get('action') in ('可以', '选这个') and (e.get('anim') or {}).get('ok') and a.get('kind') == kind
                    and shot in a.get('shots', []) and Path(a.get('project', '')).resolve() == Path(project).resolve() and (a.get('fingerprints') or {}).get(shot) == now):
                out.append((it.get('id'), e))
    return out


def confirm_cmd(job: Job, kind, project, shots, files, words, checks=None, label=None, pick=None, waive=False):
    """命令行 anim-confirm（2026-11-22 261122 反馈单 新1，Q177 复发：把用户一句笼统的“都可以出”当成具体确认）。
    样张、草图不再收对话里的原话：只认审片台上用户点的（board.py 在用户点时已经自动记进 确认记录.json），这里只核对、不另记；没点过就停，
    提示让用户在审片台点。照旧走命令行的：授权免看（--免看，只样张，Q129）、标签位置、地图。"""
    if waive or kind not in ('样张', '草图'):
        return confirm(job, kind, project, shots, files, words, checks, label, pick, waive)
    project = Path(project).resolve(); shots = [s for s in shots if s]
    if not project.is_dir(): die(f'动画工程文件夹不存在：{project}（--project 写工程文件夹的完整路径；写错了会记成一条对不上的确认）')
    if not shots: die('要写镜头（--shots）')
    got = {s: board_clicks(job, kind, project, s) for s in shots}; miss = [s for s in shots if not got[s]]
    if miss:
        die(f"{'、'.join(miss)} 的{kind}确认不收对话里的原话（2026-11-22 新1：用户一句笼统的话被当成了具体确认）：{BOARD_HINT}，点了自动记上。"
            f"还没发布就先发布（workbench --publish 动画 --anim {kind} --project … --shots …）；用户事先说过“这条不用等我确认”的样张用 --免看")
    say('审片台上用户已经点过，确认记录里已有，不用再记：' + '；'.join(f"{s}（{got[s][-1][0]}，{got[s][-1][1].get('at')} 点了{got[s][-1][1].get('action')}）" for s in shots))
    return [e for s in shots for _, e in got[s]]


def _confirmed(job, kind, project, shot, fp, fp_shot=None):
    """fp＝这一批镜头合起来的指纹（老记录只有它）；fp_shot＝这个镜头自己的指纹（新记录每个镜头存一份）。对上任一个就算确认过。"""
    return [r for r in _records(job)['items'] if r['kind'] == kind and r['project'] == project.name and shot in r['shots']
            and (r.get('fingerprint') == fp or (fp_shot and (r.get('fingerprints') or {}).get(shot) == fp_shot))]


def unreviewed_waivers(job: Job):
    """授权免看（样张）放行了、用户还没补看的镜头：[(工程名, 镜头, [图片路径])]。补看＝之后有一条普通（不带免看）的样张确认、对得上当前指纹。check 会列出，export 前会拦（见 check.unseen_samples，2026-11-21 新21）。"""
    out = []
    for r in _records(job)['items']:
        if r['kind'] != '样张' or not r.get('免看'): continue
        proj = Path(r.get('工程路径') or job.dir / r['project'])
        if not proj.is_dir(): continue
        for s in r['shots']:
            fp, fs = fingerprint(job, proj, r['shots']), fingerprint(job, proj, [s])
            if not (r.get('fingerprint') == fp or (r.get('fingerprints') or {}).get(s) == fs): continue   # 免看之后代码改了：已经不作数，gate 会拦
            if any(not x.get('免看') for x in _confirmed(job, '样张', proj, s, fp, fs)): continue
            if (r['project'], s) not in [(a, b) for a, b, _ in out]: out.append((r['project'], s, r['files']))
    return out


def gate(job: Job, project: Path, shots, full: bool, split=False):
    """渲染前的检查。full=False（出样张/静帧）：镜头说明齐全、关系类有草图确认、有布局就要过布局检查；
    full=True（整段渲染，不管多大分辨率）：再加上每个镜头都有对得上当前代码的样张确认、标签位置没有拿不准的。返回问题列表（空=都齐）。
    2026-11-22 用户定“先做出来再改”：其中“等用户确认”的三类（草图没选、样张没点可以、标签位置拿不准）不再拦渲染——require 只拦其余的硬问题，
    这三类照样列出来当“初稿·未确认”（split=True 时分开返回 (硬问题, 未确认)）；用户最后在审片台点审核线“这一栏定了”才是确认（draft.export_gate）。"""
    project = Path(project); bad = []; soft = []; doc = shots_doc(project, job.dir)
    def out(): return ([x for x in bad if x not in soft], soft) if split else bad
    if doc is None:
        bad = ['分镜规格.json 里这些镜头没写导演分镜：每个动画镜头要写 "讲什么" 和 "口播要点"（写法见 references/分镜规格.md）']; return out()
    fp = fingerprint(job, project, shots)
    for s in shots:
        d = doc[0].get(s)
        if not d or not d.get('口播要点'): bad.append(f'{s} 在 分镜规格.json 里没写口播要点（"口播要点": [原话要点, …]）'); continue
        if not d.get('讲什么'): bad.append(f'{s} 在 分镜规格.json 里没写 "讲什么"（讲关系/对比/档位的要先确认逻辑草图，不写就没法判断）'); continue
        if d['讲什么'] in RELATION:
            sk = [r for r in _records(job)['items'] if r['kind'] == '草图' and r['project'] == project.name and s in r['shots']]
            if not sk: bad.append(f'{s} 讲{d["讲什么"]}，要先出 2–3 张不同画法的逻辑草图，发到审片台“动画”栏让用户选（workbench --publish 动画 --kind 多选一 --anim 草图，见 references/动画做法.md）；用户选定才能出样张'); soft.append(bad[-1])
            elif not any(set(d['口播要点']) <= {x.get('要点') for x in r.get('对照', [])} for r in sk):   # 草图确认之后又加了要点（2026-11-13 审查）
                bad.append(f'{s} 的口播要点在草图确认之后加过或改过，草图要按现在的要点重新逐条对照、重新发到审片台让用户选（workbench --publish 动画 --kind 多选一 --anim 草图 --check …）'); soft.append(bad[-1])
    for s, mw in map_unconfirmed(job, {s: doc[0].get(s) for s in shots}, project.name):      # 2026-11-21 新3：地图类画面先停下说规则
        bad.append(f'{s} 的画面要用地图（分镜规格里写到“{"、".join(mw)}”）：' + MAP_HINT)
    if (project / '布局.json').exists():
        r = subprocess.run(['python3', '-B', str(project / '布局检查.py')], cwd=project, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if r.returncode: bad.append('布局检查没过：\n' + r.stdout.strip())
    elif project.joinpath('场景.py').exists() and not (doc[1].get('不涉及布局') or any(doc[0].get(s, {}).get('不涉及布局') for s in shots)):
        bad.append('Blender 工程没有 布局.json；场景不是房间的，在 分镜规格.json 这个镜头里写 "不涉及布局": "原因"')
    if full:
        for s in shots:
            if not _confirmed(job, '样张', project, s, fp, fingerprint(job, project, [s])):
                old = [r for r in _records(job)['items'] if r['kind'] == '样张' and r['project'] == project.name and s in r['shots']]
                bad.append(f'{s} 没有样张确认' + ('（确认之后动画代码或镜头起止改过，要重新出样张给用户看）' if old else '') + '：先出样张（自动发到审片台“动画”栏），用户在审片台点“可以”就记上了；对话里说的不算，让用户去审片台点（2026-11-22 新1）'); soft.append(bad[-1])
        lp = project / '标签定位.json'
        for k, v in (rj(lp).items() if lp.exists() else []):
            if v.get('unsure'): bad.append(f'标签“{k}”位置拿不准（{v.get("why")}），位置样张给用户选了再定（anim-confirm --kind 标签位置）'); soft.append(bad[-1])
    return out()


def require(job, project, shots, full):
    """出样张、整段出片前真正拦的：只拦硬问题（没写导演分镜、要用地图、布局检查没过）。等用户确认的（草图、样张、标签位置）不拦，
    打印成“初稿·未确认”——按 AI 自己的判断先做出来，用户在审片台事后看（2026-11-22 用户定：“按照AI的想法直接做就行了，不用来问我，等做完了我边看边改”）。"""
    hard, soft = gate(job, project, shots, full, split=True)
    if hard: die('还不能渲染：\n- ' + '\n- '.join(hard))
    if soft:
        say('【初稿·未确认】下面这些以前要等用户确认，现在不拦、按 AI 的判断先做（画法自己选最能讲清口播要点的一种，标签位置自己定；'
            '样张照旧发到审片台“动画”栏，卡片上写清“我是这么假设的”；用户看审核线时一起看）：\n- ' + '\n- '.join(x.split('：')[0].split('，要先')[0] for x in soft))


# ───────────────────────── 出片登记：动画清单、渲染尺寸、画面事件 ─────────────────────────
def register(job: Job, project, kind, shots, scale, files, events=None, size=None):
    """出片后登记。files={镜头: 视频路径}；events=[{shot, t(成片秒), name}]（不给就用工程里已有的 事件.json）；scale=渲染分辨率百分比（100=4K）。
    动画清单.json：总装、音效节拍和 check 只认这里登记的“当前进成片的那一版”。渲染尺寸.json：export 前不是 100 就拦。"""
    project = Path(project); now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ev = project / '事件.json'
    if events is not None:
        # 只重渲部分镜头时按镜头合并（2026-11-13 C3：以前整份改写，别的镜头的事件丢了）：只替换这次出的镜头的事件，其余保留
        mine = set(shots) | set(files); events = list(events); old = rj(ev) if ev.exists() else []
        keep = [e for e in old if (e['shot'] not in mine if e.get('shot') else e not in events)]
        wj(ev, sorted(keep + events, key=lambda e: e.get('t', 0)))
    lst = job.p('合成', '动画清单.json'); d = rj(lst) if lst.exists() else {'projects': [], 'shots': {}}
    d['projects'] = [p for p in d['projects'] if p['name'] != project.name] + [dict(name=project.name, kind=kind, dir=str(project), events=str(ev) if ev.exists() else None, version=now)]
    for s, f in files.items(): d['shots'][s] = dict(project=project.name, file=str(f), scale=scale, at=now)
    wj(lst, d)
    rs = job.p('合成', '渲染尺寸.json'); r = rj(rs) if rs.exists() else {'items': []}
    keep = [i for i in r['items'] if i.get('shot') not in files]
    for s, f in files.items(): keep.append(dict(name=project.name, shot=s, file=str(f), scale=scale, w=(size or [0, 0])[0], h=(size or [0, 0])[1], at=now))
    wj(rs, dict(说明='每个动画镜头当前进成片的那一版是多大分辨率渲的；scale<100 是预览，导出前要重渲 4K', items=keep))


def render_size_problems(job: Job):
    """给 export 用：成片里用到的动画镜头有没有还是预览分辨率的。返回问题列表（空=可以导出）。"""
    rs = job.p('合成', '渲染尺寸.json')
    if not rs.exists(): return []
    B = rj(job.p('分镜.json')) if job.p('分镜.json').exists() else {'shots': []}; used = {s['id'] for s in B['shots'] if s['kind'] == 'B'}
    lst = job.p('合成', '动画清单.json'); projs = {p['name']: p for p in (rj(lst)['projects'] if lst.exists() else [])}
    def how(i):     # 出 4K 的命令：只有导出前才出 4K，平时默认都是半尺寸（2026-11-21 Q159 复发）
        p = projs.get(i['name'], {})
        if p.get('kind') == 'code': return f'thinkcut_edit.py anim-render {shlex.quote(str(job.dir))} --4k'
        return f"cd {shlex.quote(str(p.get('dir') or i['name']))} && python3 出片.py --shot {i['shot']} --4k"      # 片子文件夹名里有空格，路径加引号
    bad = [f"{i['shot']}（{i['name']}）还是 {i['scale']}% 预览分辨率渲的：{i['file']}；出 4K：{how(i)}" for i in rj(rs)['items'] if i.get('scale', 100) != 100 and i.get('shot') in used]
    # 登记表是 100% 不等于审核线里就是 100%：重渲登记后没重新总装，B肉 还是旧的（2026-11-13 审查）
    newer = newer_than_broll(job, used)
    if newer: bad.append('这些镜头登记的出片比 合成/B肉.mp4 新（重渲后没重新总装，审核线里还是旧画面）：' + '、'.join(newer) + '。先 anim-assemble → pip → review，用户看过再导')
    return bad


def newer_than_broll(job: Job, used=None):
    """登记的出片比 合成/B肉.mp4 新的镜头（重渲、重新登记后没重新总装）。used 给了就只看这些镜头。export 和 status 都用。"""
    b = job.p('合成', 'B肉.mp4'); lst = job.p('合成', '动画清单.json')
    if not (b.exists() and lst.exists()): return []
    return sorted(k for k, v in rj(lst)['shots'].items() if (used is None or k in used) and Path(v['file']).exists() and Path(v['file']).stat().st_mtime > b.stat().st_mtime + 2)


def code_newer(job: Job):
    """登记过出片的动画工程里，动画代码（算指纹的那些文件；自动重排的 layout_*.json 不算）比它登记的出片新：{工程: [文件名]}。改了代码还没重出。status 用。"""
    lst = job.p('合成', '动画清单.json')
    if not lst.exists(): return {}
    d = rj(lst); out = {}
    for p in d['projects']:
        fs = [Path(v['file']) for v in d['shots'].values() if v.get('project') == p['name'] and Path(v['file']).exists()]
        if not fs or not Path(p.get('dir') or '').is_dir(): continue
        t = min(f.stat().st_mtime for f in fs) + 2
        new = [f.name for f in _code_files(Path(p['dir'])) if not f.name.startswith('layout_') and f.stat().st_mtime > t]
        if new: out[p['name']] = new
    return out


def shot_spans(job: Job):
    """动画镜头清单和起止（帧）：[[镜头, 起帧, 帧数], …]。总装时存一份进 合成/总装检查.json，status 拿它和现在的分镜比，只有这个变了才要重出动画。"""
    return [[p['shot'], p['start'], p['frames']] for p in pieces(job)]


def label_overlap(job: Job):
    """代码动画标签逐帧自检的结果（动画/自查/标签重叠.json，anim-labelcheck 写）。返回 (问题列表, 提示)：
    有字压物体的帧 = 问题（check 不过、review 不建审核线）；文件没有但有代码动画镜头登记 = 只提示还没跑（老片子没有这个文件，不硬拦）。"""
    f = adir(job) / '自查' / '标签重叠.json'
    if f.exists():
        r = rj(f)
        if int(r.get('重叠帧数') or 0) <= 0: return [], (f"标签自检还有镜头没查（{'、'.join(r['没查'])}：改过动画或只查了别的镜头），交审前重跑 anim-labelcheck" if r.get('没查') else None)
        return [f'{k} 字压物体 {len(v)} 帧' for k, v in (r.get('镜头') or {}).items()] or [f"字压物体 {r['重叠帧数']} 帧"], None
    lst = job.p('合成', '动画清单.json'); d = rj(lst) if lst.exists() else {}
    code = {p['name'] for p in d.get('projects', []) if p.get('kind') == 'code'}
    mine = sorted(k for k, v in d.get('shots', {}).items() if v.get('project') in code)
    return [], (f"有代码动画镜头登记（{'、'.join(mine)}），但还没跑 anim-labelcheck（标签逐帧自检，交审前要 0 帧）" if mine else None)


def current_events(job: Job):
    """当前进成片的各动画工程的画面事件，合成一张表（按时间排）。音效节拍、check 只读这个，不读旧工程的事件表。"""
    lst = job.p('合成', '动画清单.json')
    if not lst.exists(): return []
    d = rj(lst); live = {v['project'] for v in d['shots'].values()}; out = []
    for p in d['projects']:
        if p['name'] in live and p.get('events') and Path(p['events']).exists():
            # 镜头改给别的工程重做后，旧工程事件表里它的事件不再算（2026-11-13 审查：音效卡到过旧版动画的时间）
            out += [dict(e, project=p['name']) for e in rj(p['events']) if not e.get('shot') or d['shots'].get(e['shot'], {}).get('project') == p['name']]
    return sorted(out, key=lambda e: e['t'])


# ───────────────────────── 总装：逐帧拼接＋逐镜头逐像素比对 ─────────────────────────
def _ffprobe_frames(p):
    from .common import packets          # 读 JSON 的第一条视频流：带时间码轨（tmcd）的素材以前在这里 ValueError（2026-11-22 新5）
    return packets(p)


def _frame(path, i, W, H):
    """取第 i 帧（从 0 数），缩放到 W×H，返回原始 RGB 字节。用 select 按帧号取，不靠时间戳。"""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', f'select=eq(n\\,{i}),scale={W}:{H}:flags=bicubic,format=rgb24', '-frames:v', '1',
                        '-f', 'rawvideo', '-'], capture_output=True, stdin=subprocess.DEVNULL)
    return r.stdout


def _frames_at(path, idxs, W, H, chunk=40):
    """一次顺序解码取多帧（2026-11-13 C15：原来每帧都从头解码一遍 4K 成片）：返回 {帧号: 原始 RGB 字节}，和逐帧 _frame 取的逐字节一样。
    取到最后一个要的帧就停；视频里没有的帧号给空字节。帧号多时每 chunk 个一趟（默认 40；select 表达式太长 ffmpeg 会解析失败、取回空帧，一趟 120 帧实测就不行）。
    某一趟取回的帧数比要的少、而缺的帧号视频里其实有，就报错停下，不拿空帧去比出“差 255”。"""
    idxs = sorted({int(i) for i in idxs if i >= 0}); out = {i: b'' for i in idxs}; size = W * H * 3
    for k in range(0, len(idxs), chunk):
        part = idxs[k:k + chunk]; sel = '+'.join(f'eq(n\\,{i})' for i in part)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', f'select={sel},scale={W}:{H}:flags=bicubic,format=rgb24', '-fps_mode', 'passthrough',
                              '-frames:v', str(len(part)), '-f', 'rawvideo', '-'], capture_output=True, stdin=subprocess.DEVNULL).stdout
        for j, i in enumerate(part[:len(raw) // size]): out[i] = raw[j * size:(j + 1) * size]
        if len(raw) // size < len(part):
            try: nf = _ffprobe_frames(path)
            except Exception as e: nf = 0; say(f'读视频帧数出错：{e}')
            if nf <= 0: die(f'取帧失败：{Path(path).name} 这一趟要 {len(part)} 帧只取回 {len(raw) // size} 帧，又读不出视频共几帧，没法判断缺的是不是超出了结尾，不拿空帧去比对')
            lost = [i for i in part[len(raw) // size:] if i < nf]
            if lost: die(f'取帧失败：{Path(path).name} 这一趟要 {len(part)} 帧只取回 {len(raw) // size} 帧（缺帧号 {lost[:5]}…，视频共 {nf} 帧），不拿空帧去比对')
    return out


def _diff(a, b):
    import numpy as np
    if len(a) != len(b) or not a: return 255.0
    return float(np.abs(np.frombuffer(a, np.uint8).astype(np.int16) - np.frombuffer(b, np.uint8).astype(np.int16)).mean())


def pieces(job: Job):
    """B肉 的拼接表：分镜.json 里每个 B 镜头 → 动画清单里登记的视频（没有就黑帧）。帧位置 round(秒×帧率)。"""
    fps = float(job.fps); B = rj(job.p('分镜.json')); lst = job.p('合成', '动画清单.json'); reg = rj(lst)['shots'] if lst.exists() else {}
    out = []
    for s in B['shots']:
        if s['kind'] != 'B': continue
        a, b = round(s['a'] * fps), round(s['b'] * fps)
        out.append(dict(shot=s['id'], start=a, frames=b - a, file=reg.get(s['id'], {}).get('file')))
    return out


# ───────────────── 实拍镜头、图片镜头、盖实拍（2026-11-21 新16：以前靠片子里的脚本做好再手改 动画清单.json、渲染尺寸.json）─────────────────
# 写法在 分镜规格.json 的镜头里（references/分镜规格.md「实拍和图片」）：beats 把它们编进 分镜.json，总装时做成本片画幅、正好帧数的片段：
#   "实拍": 整个镜头放一段做好的实拍 → 合成/实拍镜头/<镜头>.mp4，登记进动画清单（工程名 实拍镜头）
#   "图片": 整个镜头放一张或几张图（几张就平分时长），按画面区摆 → 同上
#   "盖实拍": 动画镜头里某一段换成实拍 → 合成/实拍镜头/<镜头>_盖<第几段>.mp4，不另登记，总装时盖到那一段；这一段动画标签不画、标签自检不查（新15）
LIVE = '实拍镜头'
BG = (22, 24, 29)          # 动画底色 #16181D（图片没铺满画面区的地方）


def is_live(s):
    """整个镜头是实拍/图片（R 开头，或 分镜规格.json 里写了 实拍/图片）：代码动画不画、标签自检不查。"""
    return s['id'].startswith('R') or '实拍' in s or '图片' in s


def _live_dir(job): d = job.dir / '合成' / LIVE; d.mkdir(parents=True, exist_ok=True); return d


def cover_list(job: Job):
    """盖实拍的区间：{镜头: [(镜头里第几帧起, 到第几帧（不含）, 做好的片段路径, 源文件, 入点), …]}。帧号和总装同一种算法。"""
    bf = job.p('分镜.json'); fps = float(job.fps); out = {}
    for s in (rj(bf)['shots'] if bf.exists() else []):
        a = round(s['a'] * fps)
        for k, c in enumerate(s.get('盖实拍') or []):
            out.setdefault(s['id'], []).append((round(c['a'] * fps) - a, round(c['b'] * fps) - a, job.dir / '合成' / LIVE / f"{s['id']}_盖{k + 1}.mp4", c['文件'], c.get('入点', 0.0)))
    return out


def covered(cov, sid, i):
    """镜头 sid 里第 i 帧（从镜头开头数）是不是被盖了实拍。"""
    return any(k0 <= i < k1 for k0, k1, *_ in cov.get(sid, ()))


def _src(job, name):
    from .sfx import local_sound          # 和项目里自己下的音效同一种找法：绝对路径，或相对片子目录、02_制作
    p = local_sound(job, name)
    if not p: die(f'实拍/图片文件找不到：{name}（写绝对路径，或相对项目文件夹、02_制作 的路径）')
    return Path(p)


def _clip(job, src, t_in, n, out):
    """实拍 → 正好 n 帧、本片 4K 画幅（等比放大铺满、居中裁）、本片帧率，从源文件第 t_in 秒起。
    只取第一条视频流、不要声音和数据流（-map 0:v:0 -dn），不写时间码轨（-write_tmcd 0）：源文件带 tmcd 时以前会跟着带进片段（2026-11-22 新5）。
    实拍原声另走 合成/实拍原声.wav，这里不需要音频。"""
    W, H = job.W, job.H
    run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{float(t_in):.3f}', '-i', src, '-map', '0:v:0', '-frames:v', n, '-an', '-dn', '-write_tmcd', '0',
         '-vf', f"fps={job['fps']},scale={W}:{H}:force_original_aspect_ratio=increase:flags=bicubic,crop={W}:{H},format=yuv420p",
         '-c:v', 'libx264', '-preset', 'medium', '-crf', '14', '-pix_fmt', 'yuv420p', '-r', job['fps'], out], stdin=subprocess.DEVNULL)
    got = _ffprobe_frames(out)
    if got != n: Path(out).unlink(missing_ok=True); die(f'{Path(src).name} 从第 {t_in} 秒起不够 {n} 帧（只有 {got} 帧）：换长一点的片段或改 入点')


def _pictures(job, files, fit, n, out):
    """一张或几张图 → n 帧（几张平分），每张按画面区摆：完整＝整张放进画面区（空的地方动画底色），裁满＝铺满画面区、居中裁。"""
    from PIL import Image
    x, y, w, h = job.fmt['card']; tmp = Path(tempfile.mkdtemp(prefix='thinkcut_pic_')); parts = []
    if n < len(files): die(f'{out.stem}：{len(files)} 张图，镜头只有 {n} 帧')
    try:
        for k, f in enumerate(files):
            m = n * (k + 1) // len(files) - n * k // len(files)
            im = Image.open(f).convert('RGB'); s = (max if fit == '裁满' else min)(w / im.width, h / im.height)
            im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
            if fit == '裁满': l, u = (im.width - w) // 2, (im.height - h) // 2; im = im.crop((l, u, l + w, u + h))
            cv = Image.new('RGB', (job.W, job.H), BG); cv.paste(im, (x + (w - im.width) // 2, y + (h - im.height) // 2)); cv.save(tmp / f'{k}.png')
            run(['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-framerate', job['fps'], '-i', tmp / f'{k}.png', '-frames:v', m, '-c:v', 'libx264', '-preset', 'medium',
                 '-crf', '14', '-pix_fmt', 'yuv420p', '-r', job['fps'], tmp / f'{k}.mp4'], stdin=subprocess.DEVNULL); parts.append(tmp / f'{k}.mp4')
        _join(parts, Path(out), job['fps'], n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def prepare_live(job: Job):
    """总装前：分镜.json 里写了 实拍/图片 的镜头做成片段并登记进 动画清单.json、渲染尺寸.json（100%）；盖实拍的区间做成片段。
    源文件、入点、帧数、画幅都没变就不重做（签名记在 合成/实拍镜头_签名.json）。返回 cover_list(job)。"""
    bf = job.p('分镜.json')
    if not bf.exists(): return {}
    fps = float(job.fps); d = _live_dir(job); sf = job.p('合成', '实拍镜头_签名.json'); sig = rj(sf) if sf.exists() else {}; reg = {}

    def make(name, kind, srcs, t_in, n, fit=None):
        out = d / f'{name}.mp4'; key = json.dumps([kind, [[str(p), p.stat().st_size, int(p.stat().st_mtime)] for p in srcs], t_in, n, fit, job.W, job.H, job['fps']], ensure_ascii=False)
        if sig.get(name) != key or not out.exists():
            say(f'做{kind}片段 {name}（{n} 帧）…')
            if kind == '实拍': _clip(job, srcs[0], t_in, n, out)
            else: _pictures(job, srcs, fit, n, out)
            sig[name] = key; wj(sf, sig)
        return out
    for s in rj(bf)['shots']:
        n = round(s['b'] * fps) - round(s['a'] * fps)
        if s.get('实拍'): reg[s['id']] = make(s['id'], '实拍', [_src(job, s['实拍']['文件'])], s['实拍'].get('入点', 0.0), n)
        elif s.get('图片'): reg[s['id']] = make(s['id'], '图片', [_src(job, f) for f in s['图片']['文件']], 0, n, s['图片'].get('铺法', '完整'))
    cov = cover_list(job)
    for sid, cs in cov.items():
        for k0, k1, out, f, t_in in cs: make(out.stem, '实拍', [_src(job, f)], t_in, k1 - k0)
    lst = job.p('合成', '动画清单.json'); old = rj(lst)['shots'] if lst.exists() else {}
    new = {k: v for k, v in reg.items() if (old.get(k) or {}).get('file') != str(v) or (old.get(k) or {}).get('project') != LIVE}
    if new: register(job, d, 'live', list(new), 100, new, None, [job.W, job.H]); say('已登记实拍/图片镜头：' + '、'.join(new))
    return cov


class _Reader:
    """把一个视频解码成本片画幅的裸画面（yuv420p），一帧一帧读；读完返回 None。"""
    def __init__(self, path, W, H):
        self.size = W * H * 3 // 2
        self.p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', f'scale={W}:{H}:flags=bicubic,format=yuv420p', '-fps_mode', 'passthrough', '-f', 'rawvideo', '-'],
                                  stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)

    def read(self):
        if not self.p: return None
        buf = self.p.stdout.read(self.size)
        if len(buf) < self.size: self.close(); return None
        return buf

    def close(self):
        if self.p: self.p.stdout.close(); self.p.kill(); self.p.wait(); self.p = None


def assemble(job: Job, out=None):
    """逐帧拼接：每段解码成裸画面、缩放进 4K 画布，严格按帧数送进同一个编码器（少了补最后一帧并报出来，多了截掉），不靠 concat 和时间戳。
    先做好实拍/图片镜头和盖实拍的片段（prepare_live）；盖实拍的那几帧用实拍顶替动画。拼完逐镜头比对首/中/尾帧（shot_compare），对不上就停。"""
    W, H = job.W, job.H; total = job['frames']; out = Path(out or job.p('合成', 'B肉.mp4'))
    from .common import guard_overwrite
    guard_overwrite(job, out)          # 当前审核线用着 B肉.mp4（没做小圆圈时）就先停（2026-11-22 新3）
    cov = prepare_live(job); ps = pieces(job); miss = [p['shot'] for p in ps if not p['file']]
    if miss: say('这些动画镜头还没有登记的视频，先用黑帧占位：' + '、'.join(miss))
    tmp = out.with_name(out.stem + '.拼接中.mp4')
    enc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-s', f'{W}x{H}', '-r', job['fps'], '-i', '-',
                            '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p', '-r', job['fps'], str(tmp)], stdin=subprocess.PIPE)
    black = bytes(W * H) + bytes([128]) * (W * H // 2); t = 0; short = []
    def put(buf, n):
        for _ in range(n): enc.stdin.write(buf)
    for p in ps:
        if p['start'] > t: put(black, p['start'] - t)
        cs = cov.get(p['shot'], [])
        if not p['file'] and not cs: put(black, p['frames']); t = p['start'] + p['frames']; continue
        dec = _Reader(p['file'], W, H) if p['file'] else None; ovs = [(k0, k1, _Reader(f, W, H)) for k0, k1, f, *_ in cs]
        last, k = None, 0
        for i in range(p['frames']):
            buf = dec.read() if dec else black       # 被盖住的帧照样读掉动画那一帧，前后才对得上
            if buf is None: buf = last or black
            elif dec: k += 1
            for k0, k1, r in ovs:
                if k0 <= i < k1: buf = r.read() or buf
            enc.stdin.write(buf); last = buf
        for r in [dec] + [r for *_, r in ovs]:
            if r: r.close()
        if dec and k < p['frames']: short.append(f"{p['shot']} 只有 {k} 帧，应为 {p['frames']}，补了 {p['frames'] - k} 帧")
        t = p['start'] + p['frames']
    if t < total: put(black, total - t)
    enc.stdin.close(); enc.wait()
    if enc.returncode: die('总装编码失败')
    got = _ffprobe_frames(tmp)
    if got != total: die(f'总装出了 {got} 帧，成片是 {total} 帧')
    os.replace(tmp, out); say(f'→ {out}（{got} 帧）')
    rep = shot_compare(job, out); rep['补帧'] = short; rep['镜头起止'] = [[p['shot'], p['start'], p['frames']] for p in ps]
    if cov: rep['盖实拍'] = {sid: [[k0, k1, str(f)] for k0, k1, f, *_ in cs] for sid, cs in cov.items()}
    wj(job.p('合成', '总装检查.json'), rep)
    if rep['bad'] or short: die('总装比对没过：\n- ' + '\n- '.join(rep['bad'] + short) + '\n详见 合成/总装检查.json')
    say(f"逐镜头比对通过：{len(rep['shots'])} 个镜头首/中/尾帧都对得上，没有错帧")
    return rep


def _most_motion(path, n):
    """源视频里画面变化最大的那一帧（静止画面错一帧看不出来，要在动得最厉害的地方查）。"""
    import numpy as np
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', 'scale=96:128,format=gray', '-f', 'rawvideo', '-'], capture_output=True, stdin=subprocess.DEVNULL).stdout
    a = np.frombuffer(raw, np.uint8)[:n * 96 * 128].reshape(-1, 128, 96).astype(np.int16)
    if len(a) < 3: return 0
    d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2)); return int(d.argmax()) + 1


def shot_compare(job: Job, out=None, tol=4.0):
    """逐镜头逐像素比对：总装成片每个镜头的首/中/尾帧和动得最厉害的那一帧，和源视频对应帧比（平均每像素差 ≤ tol，满分 255）；
    同时和前后各错一帧比，若错一帧反而更像，说明接缝错帧。给 check 也能直接调用。"""
    out = Path(out or job.p('合成', 'B肉.mp4')); W, H = job.W // 4, job.H // 4; res, bad = [], []
    # 取帧一次取完（C15）：每个源视频解码一遍、总装成片顺序解码一遍（帧号多时分几趟），比对的帧和算法都没变
    # 盖实拍（新16）：被盖住的帧和实拍片段比（每段首/中/尾），动画那边只比没被盖住的帧
    cov = cover_list(job); ps = []
    for p in pieces(job):
        cs = [(k0, k1, str(f)) for k0, k1, f, *_ in cov.get(p['shot'], []) if Path(f).exists()]
        at = {0, p['frames'] // 2, p['frames'] - 1, _most_motion(p['file'], p['frames'])} if p['file'] else set()
        at = {i for i in at if not any(k0 <= i < k1 for k0, k1, _ in cs)} | {i for k0, k1, _ in cs for i in (k0, (k0 + k1) // 2, k1 - 1)}
        if at: ps.append(dict(p, at=sorted(at), cs=cs))
    def src(p, i):         # 镜头里第 i 帧该和哪个文件的第几帧比
        if not 0 <= i < p['frames']: return None
        c = next(((f, i - k0) for k0, k1, f in p['cs'] if k0 <= i < k1), None)
        return c or ((p['file'], i) if p['file'] else None)
    G = _frames_at(out, [p['start'] + i for p in ps for i in p['at']], W, H); need = {}
    for p in ps:
        for i in p['at']:
            for j in (i - 1, i, i + 1):
                s = src(p, j)
                if s and (j == i or s[0] == src(p, i)[0]): need.setdefault(s[0], set()).add(s[1])
    S = {f: _frames_at(f, sorted(js), W, H) for f, js in need.items()}
    for p in ps:
        for i in p['at']:
            f, j = src(p, i); got = G.get(p['start'] + i, b''); d0 = _diff(S[f].get(j, b''), got)
            near = [_diff(S[f][s[1]], got) for s in (src(p, i - 1), src(p, i + 1)) if s and s[0] == f]
            ok = d0 <= tol and all(d0 <= x + 0.05 for x in near)
            res.append(dict(shot=p['shot'], frame=i, diff=round(d0, 2), neighbors=[round(x, 2) for x in near], ok=ok, **({'盖实拍': True} if f != p['file'] else {})))
            if not ok: bad.append(f"{p['shot']} 第 {i} 帧{'（盖实拍）' if f != p['file'] else ''}对不上（差 {d0:.1f}，前后帧 {[round(x, 1) for x in near]}）")
    return dict(file=str(out), shots=sorted({r['shot'] for r in res}), frames=res, bad=bad)


# ───────────────────────── 代码动画（Three.js 网页）─────────────────────────
def new(job: Job):
    # 老代码动画 2026-11-20 起新片子停用，模板 2026-11-21 下线：不再新建。已有老工程（动画/build.py）的片子照旧能 anim-build / anim-stills / anim-render / anim-labelcheck。
    die('老代码动画已下线，不再新建工程（2026-11-20 用户定停用，2026-11-21 模板下线）。新项目：真实空间用 blender-new，其余网页动画用 hf-new。'
        '已有老工程（动画/build.py）的老项目要重出，直接用 anim-render、anim-stills、anim-labelcheck。')


def blender_new(job: Job, name):
    d = job.dir / f'Blender_{name}'
    if d.exists(): die(f'{d} 已存在，不覆盖')
    shutil.copytree(BLENDER_TEMPLATE, d, ignore=shutil.ignore_patterns('__pycache__', '自查', '出片', '缓存', '镜头.json'))
    say(f'已复制 Blender 出片模板 → {d}\n先在 分镜规格.json 给这个工程的镜头写 讲什么、口播要点、画面、运镜，并写 "工程": "{d.name}"；再写 布局.json（房间布局）、改 场景.py；做法见 references/动画做法.md 第 6–7 节')


def hf_new(job: Job, name):
    d = job.dir / f'HF_{name}'
    if d.exists(): die(f'{d} 已存在，不覆盖')
    shutil.copytree(HF_TEMPLATE, d, ignore=shutil.ignore_patterns('__pycache__', '样张', '出片', '缓存', 'index.html', 'index.html.lock', 'node_modules', '.hyperframes', 'renders'))
    say(f'已复制 HyperFrames 网页动画模板 → {d}\n先在 分镜规格.json 给这个工程的镜头写 讲什么、口播要点、画面、运镜，并写 "工程": "{d.name}"；'
        f'每个镜头复制 镜头/示例.html 改名成 镜头号.html；做法见 references/动画做法.md 的“HyperFrames 工程”一节')


def build(job: Job, relayout=None):
    """拼网页；分镜比标签排版新（或还没排过）时，先重排卡点模式的标签位置（要开无头 Chrome）"""
    d = adir(job)
    if not (d / 'build.py').exists(): die('这个项目没有老的代码动画工程（动画/build.py）。新项目的网页动画用 hf-new，出片在 HF_名字/ 里跑 python3 出片.py，不走这条命令')
    lay = d / f"layout_{job.fmt['anim_fmt']}b.json"; beats = job.dir / '分镜.json'
    if relayout or (relayout is None and beats.exists() and (not lay.exists() or lay.stat().st_mtime < beats.stat().st_mtime)):
        say('分镜有更新，先重排动画标签…')
        run(['python3', d / 'build.py'], env={**os.environ, 'NOLAYOUT': '1'})
        run(['node', d / 'tools' / 'layout.mjs', job.fmt['anim_fmt'] + 'b'])
    run(['python3', d / 'build.py'])


def _q(job, extra=''):
    return f"mode=beats&ui=0&fmt={job.fmt['anim_fmt']}" + (('&' + extra) if extra else '')


def anim_shots(job, t_from=0.0, t_to=None):
    B = rj(job.p('分镜.json')); t_to = t_to if t_to is not None else 1e9
    return [s for s in B['shots'] if s['kind'] == 'B' and s['b'] > t_from and s['a'] < t_to]


def _kill_group(p):
    try: os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError): pass


def _run_group(cmd, timeout, **kw):
    """在独立进程组里跑（2026-11-13 C13：超时只杀 node，它启动的无头 Chrome、ffmpeg 不跟着退，越积越多）：
    超时、出错或被打断时杀整个进程组；正常结束也清一遍残留。超时抛 TimeoutExpired，退出码非 0 抛 CalledProcessError（和 subprocess.run(check=True) 一样）。"""
    p = subprocess.Popen([str(c) for c in cmd], stdin=subprocess.DEVNULL, start_new_session=True, **kw)
    try:
        rc = p.wait(timeout=timeout)
    finally:
        _kill_group(p); p.wait()
    if rc: raise subprocess.CalledProcessError(rc, cmd)


_PORTS, _PLOCK = set(), threading.Lock()


@contextlib.contextmanager
def _port():
    """给这次开的 Chrome 分一个调试端口（9800–9899，本进程里不重复、本机没人占）。cap.mjs 原来随机挑端口，几路同时开会撞上，
    撞上时后开的那个会去操作先开的 Chrome 的页面、截错图（2026-11-21 实测：撞端口的 Chrome 照样启动，调试口落到别人那边）。"""
    with _PLOCK:
        for p in range(9800, 9900):
            if p in _PORTS: continue
            with socket.socket() as so:
                try: so.bind(('127.0.0.1', p))
                except OSError: continue
            _PORTS.add(p); break
        else: die('9800–9899 的端口都被占了，开不了新的 Chrome')
    try: yield ['--port', str(p)]
    finally:
        with _PLOCK: _PORTS.discard(p)


def _cap_segment(job, a, b, out, w, timeout_per_frame=1.5, tries=3, extra_q='', min_t=None):
    """出成片第 a..b 帧（不含 b）这一段：开一个新 Chrome，限时（启动 60 秒＋每帧 1.5 秒），挂住就杀掉重试。"""
    fps = float(job.fps); n = b - a
    for k in range(tries):
        try:
            with _port() as port:
                _run_group(cap(job) + port + ['--q', _q(job, extra_q), '--w', str(w), '--video', str(out), '--fps', job['fps'],
                                              '--from', f'{a / fps:.6f}', '--to', f'{b / fps:.6f}', '--frames', str(n), '--frame-timeout', '30']
                           + (['--min-t', f'{min_t:.6f}'] if min_t is not None else []), timeout=60 + timeout_per_frame * n, cwd=adir(job))
            if _ffprobe_frames(out) == n: return
            say(f'  第 {a}–{b} 帧这段出了 {_ffprobe_frames(out)} 帧，重来')
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            say(f'  第 {a}–{b} 帧这段第 {k + 1} 次没出来（{type(e).__name__}），换新 Chrome 重来')
    die(f'第 {a}–{b} 帧连续 {tries} 次没出来')


def workers(n=None, top=12):
    """同时开几个无头 Chrome：写了就用写的；不写按本机核数取（约 2/3 的核，最多 top 个；M5 Max 18 核 → 12，再多瓶颈在显卡）。"""
    return max(1, int(n)) if n else max(2, min(top, (os.cpu_count() or 4) * 2 // 3))


def code_shots(job: Job, shots):
    """只要代码动画自己的镜头（2026-11-13 审查）：写了别的 "工程"、已登记给别的工程（Blender、实拍镜头等）、实拍/图片镜头都不碰。返回 (要的, 不要的镜头号)。"""
    owner = {k: v.get('工程') for k, v in _spec_director(job.dir).items()}; lst = job.p('合成', '动画清单.json')
    reg = {k: v.get('project') for k, v in (rj(lst)['shots'] if lst.exists() else {}).items()}; me = adir(job).name
    other = [s['id'] for s in shots if is_live(s) or (owner.get(s['id']) or reg.get(s['id']) or me) != me]
    return [s for s in shots if s['id'] not in other], other


def _parallel(fn, tasks, n):
    """几个任务同时跑（每个任务自己开 Chrome）；有一个失败就不再开新的，等在跑的结束后把错误报出来。"""
    with ThreadPoolExecutor(n) as ex:
        futs = [ex.submit(fn, t) for t in tasks]
        try: return [f.result() for f in futs]
        except BaseException:
            for f in futs: f.cancel()
            raise


def render(job: Job, all_frames=False, t_from=None, t_to=None, out=None, seg=40, preview=True, n_workers=None):
    """→ 合成/B肉.mp4（或 out）。每个动画镜头分成 ≤seg 帧一段，每段开一个新 Chrome 出到本机临时目录（连续出到约 300 帧会卡死，11-11），
    几段同时出（2026-11-21 新3：原来单路；本机默认 4K 8 路、预览 10 路）；出完登记进 动画清单 再总装（逐帧拼接＋比对）。
    preview=True（默认，审片轮）：半尺寸（3:4 是 1080 宽）出成 动画/出片/<镜头>_预览50.mp4、登记 scale=50，不动 4K 的 <镜头>.mp4；
    preview=False 只在导出前用（命令行 --4k）：export 见到 50% 的会拦并给出这条命令（2026-11-21 Q159 复发：不能靠 AI 记得加 --preview）。
    整段渲染前查样张确认（新3），关系类镜头查草图确认（新5）。"""
    from .common import guard_overwrite
    guard_overwrite(job, Path(out or job.p('合成', 'B肉.mp4')))       # 最后要总装覆盖 B肉.mp4：当前审核线用着就开头先停，不白出（2026-11-22 新3）
    build(job); fps = float(job.fps)
    shots, other = code_shots(job, anim_shots(job, t_from or 0, t_to))
    if other: say('这些镜头不是代码动画的，不渲：' + '、'.join(other))
    if not shots: die('这一段没有代码动画的镜头')
    require(job, adir(job), [s['id'] for s in shots], full=True)
    scale = 50 if preview else 100; w = job.fmt['anim_w'] * scale // 100; n = workers(n_workers, 10 if preview else 8)
    tmpd = Path(tempfile.mkdtemp(prefix='thinkcut_cap_')); files = {}
    outdir = adir(job) / '出片'; outdir.mkdir(exist_ok=True)
    tasks = [(s, k, f0, min(round(s['b'] * fps), f0 + seg)) for s in shots for k, f0 in enumerate(range(round(s['a'] * fps), round(s['b'] * fps), seg))]
    say(f"出 {'、'.join(s['id'] for s in shots)}（共 {sum(t[3] - t[2] for t in tasks)} 帧，{w} 宽{'预览' if preview else ''}，{len(tasks)} 段 {n} 路同时出）…")
    def one(t):
        s, k, f0, f1 = t; part = tmpd / f"{s['id']}_{k:03d}.mp4"; _cap_segment(job, f0, f1, part, w, min_t=s['a'] + 1e-4); return part
    try:
        parts = _parallel(one, tasks, n)
        for s in shots:
            dst = outdir / (f"{s['id']}.mp4" if scale == 100 else f"{s['id']}_预览{scale}.mp4")
            _join([p for t, p in zip(tasks, parts) if t[0] is s], dst, job['fps'], round(s['b'] * fps) - round(s['a'] * fps)); files[s['id']] = dst
            _first_frame_check(dst)
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    size = [w, round(job.H * w / job.W)] if preview else [job.W, job.H]
    register(job, adir(job), 'code', [s['id'] for s in shots], scale, files, None, size)   # 画面事件表：工程自己维护 动画/事件.json
    if preview: say(f'已登记为 {scale}% 预览（审片用，默认就是 1080P）；用户看完 OK、导出前才加 --4k 重渲 4K，不然 export 会拦')
    if all_frames: say('（--all 已不需要：非动画镜头总装时用黑帧）')
    return assemble(job, out)


def _join(parts, dst, fps_s, n):
    """把几段按帧拼成一个视频（解码后送进同一编码器），帧数必须正好 n。"""
    from .common import probe
    s = probe(parts[0], 'stream=width,height'); W, H = int(s['width']), int(s['height']); size = W * H * 3 // 2      # JSON 读第一条视频流（新5）
    enc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-s', f'{W}x{H}', '-r', fps_s, '-i', '-',
                            '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p', '-r', fps_s, str(dst)], stdin=subprocess.PIPE)
    k = 0
    for p in parts:
        dec = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(p), '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-fps_mode', 'passthrough', '-'], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size: break
            enc.stdin.write(buf); k += 1
        dec.wait()
    enc.stdin.close(); enc.wait()
    if k != n: die(f'{dst.name} 拼出 {k} 帧，应为 {n}')


def _first_frame_check(path):
    """首帧全黑检查（A20 第 1 帧黑过三次）：第 1 帧比第 2 帧暗一大截就停下。"""
    import numpy as np
    a = np.frombuffer(_frame(path, 0, 216, 288), np.uint8); b = np.frombuffer(_frame(path, 1, 216, 288), np.uint8)
    if not a.size: die(f'{Path(path).name} 读不出第 1 帧')
    if b.size and b.mean() > 12 and a.mean() < b.mean() * 0.4: die(f'{Path(path).name} 第 1 帧几乎全黑（{a.mean():.0f} vs 第 2 帧 {b.mean():.0f}），首帧没就绪')


def stills(job: Job, times, out_prefix=None):
    """抽静帧（样张、自查）。关系类镜头还没确认逻辑草图时也拦（先草图后样张）。"""
    build(job); fps = float(job.fps); ts = [float(t) for t in times]
    hit = [s['id'] for s in anim_shots(job) if any(s['a'] <= t < s['b'] for t in ts)]
    if hit: require(job, adir(job), hit, full=False)
    out_prefix = out_prefix or job.p('合成', '动画抽帧', 'f')
    run(cap(job) + ['--q', _q(job), '--w', str(job.fmt['anim_w'] // 2), '--t', ','.join(str(t) for t in times), '--out', out_prefix])   # 样张一律半尺寸（3:4 是 1080 宽，Q159 复发）
    say(f'→ {out_prefix}_<秒>.jpg（半尺寸）')
    spans = anim_shots(job); out_prefix = Path(out_prefix)
    items = [(next((s['id'] for s in spans if s['a'] <= float(t) < s['b']), '口播'), float(t), out_prefix.parent / f'{out_prefix.name}_{t}.jpg') for t in times]
    return still_sheet(job, [x for x in items if x[2].exists()], out_prefix.parent / '样张拼图.jpg', adir(job))


def open_file(path):
    """在用户的 Mac 上用“预览”打开图片（2026-11-21 新4：Claude 里点不开样张）。测试时 THINKCUT_NO_OPEN=1 不开（和审片工作台同一个开关）。"""
    if os.environ.get('THINKCUT_NO_OPEN') or sys.platform != 'darwin': return False
    with contextlib.suppress(Exception): return subprocess.run(['open', '-a', 'Preview', str(path)], timeout=10, stdin=subprocess.DEVNULL).returncode == 0
    return False


def still_words(project, film, items):
    """给用户的“请看哪里、确认什么”：按镜头从 分镜规格.json 的 讲什么/画面/口播要点 自动生成。items=[(镜头, 成片秒, 图片)]。"""
    doc = shots_doc(Path(project), Path(film)) if project else None; info = doc[0] if doc else {}
    order = []
    for sid, t, _ in items:
        if sid not in order: order.append(sid)
    lines = []
    for sid in order:
        ts = '、'.join(f'{t:g}' for s, t, _ in items if s == sid); d = info.get(sid, {})
        pts = [str(x) for x in (d.get('口播要点') or [])]; what = d.get('讲什么')
        look = f"画面应是“{d['画面']}”" if d.get('画面') else '画面和这段口播对得上'
        ok = ('确认画面讲清了：' + '；'.join(f'“{x}”' for x in pts)) if pts else '确认一眼看得懂、没有挡字和穿帮'
        lines.append(f"请看 {sid}（第 {ts} 秒{'，讲' + what if what else ''}）：{look}；{ok}。")
    return '\n'.join(lines + ['都对就回“可以”；哪张不对，说镜头号和哪里不对。'])


def still_sheet(job: Job, items, out, project=None):
    """样张拼成一张图 → out，在“预览”里弹出，打印一段给 AI 原样转述给用户的话（2026-11-21 新4）。items=[(镜头, 成片秒, 图片)]。返回 (拼图, 那段话)。"""
    from PIL import Image, ImageDraw, ImageFont
    if not items: return None, ''
    items = sorted(items, key=lambda x: x[1]); tw, bar, gap = 540, 56, 16
    ims = []
    for sid, t, f in items:
        im = Image.open(f).convert('RGB'); ims.append(im.resize((tw, round(tw * im.height / im.width))))
    th = max(i.height for i in ims); cols = min(4, len(ims)); rows = math.ceil(len(ims) / cols)
    sheet = Image.new('RGB', (cols * tw + (cols + 1) * gap, rows * (th + bar) + (rows + 1) * gap), (24, 24, 24)); dr = ImageDraw.Draw(sheet)
    try: font = ImageFont.truetype(job['subtitle_font'], 32, index=1)
    except Exception: font = ImageFont.load_default()
    for k, ((sid, t, _), im) in enumerate(zip(items, ims)):
        x = gap + (k % cols) * (tw + gap); y = gap + (k // cols) * (th + bar + gap)
        dr.text((x + 4, y + 8), f'{sid}  第 {t:g} 秒', fill=(255, 255, 255), font=font); sheet.paste(im, (x, y + bar))
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True); sheet.save(out, quality=90)
    words = still_words(project, job.dir, items)
    # 2026-11-22 审片台：样张自动发到审片台“动画”栏，用户在页面上点“可以”就是确认（记进 合成/确认记录.json）；页面开着就不再弹“预览”，没开照旧弹
    # 子助手、AI 自查出的样张（环境变量 THINKCUT_SELF_CHECK=1）：不发到审片台、不弹“预览”，用户只看 AI 自己看过的
    pub, live, own = None, False, bool(os.environ.get('THINKCUT_SELF_CHECK'))
    if not own:
        try:
            from . import board
            pub, live = board.publish_stills(job, items, project, words)
        except Exception as e: say(f'样张没发到审片台（照旧在“预览”里弹出）：{e}')
    opened = False if live or own else open_file(out)
    if own: say('自查样张（THINKCUT_SELF_CHECK）：没发到审片台、没弹“预览”。AI 看过没问题，要给用户看时不带 THINKCUT_SELF_CHECK 再出一次样张（会发到审片台）')
    if pub:
        say(f"样张已发到审片台“动画”栏：{pub['id']}（第 {len(pub['versions'])} 版）。用户在页面上点“可以”就是确认，自动记进 合成/确认记录.json，不用再 anim-confirm；"
            + (f'页面开着，没弹“预览”。读结果：workbench {job.dir} --list' if live else f'审片台页面没开着，跑 workbench {job.dir} --column 动画 打开给用户看'))
    say(f"样张拼图（{len(items)} 张）：{out}" + ('，已在“预览”里打开' if opened else ('' if live else '；没能自动打开：AI 用 open 打开这张图给用户看'))
        + '\n—— 请把下面这段原样转述给用户 ——\n' + words)
    return out, words


def _label_frame(L, C, biaoqian):
    """一个时刻的判定（和 2026-11-12 起的逐帧自检同一套，一个数没改）：带字图减不带字图＝字；字下面压到物体超过 8% 算一帧重叠。返回 (字的掩码, 是否重叠)。"""
    import numpy as np
    txt = np.abs(np.asarray(L, np.int16) - np.asarray(C, np.int16)).sum(axis=2) > 90
    if txt.sum() < 50: return txt, False
    return txt, float((txt & biaoqian.model_mask(C)).sum()) / txt.sum() > 0.08


def _same_around_text(x, y, pad=0.05):
    """两次粗查之间画面在字的附近动没动：字的位置一样、字周围（外扩 5% 画宽）不带字的画面也一样，才算没动（中间几帧就不用逐帧查）。
    x、y = (字的掩码, 不带字的灰度图)，都已缩成 1/4。"""
    import numpy as np
    (tx, gx), (ty, gy) = x, y
    if tx.shape != ty.shape or (tx != ty).any(): return False
    if not tx.any(): return True
    r = max(2, round(tx.shape[1] * pad)); ys, xs = np.nonzero(tx)
    y0, y1, x0, x1 = max(0, ys.min() - r), ys.max() + r + 1, max(0, xs.min() - r), xs.max() + r + 1
    return not (np.abs(gx[y0:y1, x0:x1].astype(np.int16) - gy[y0:y1, x0:x1]) > 6).any()


def _label_one(a):
    """一个时刻的两张图 → 判定（在子进程里跑：判定是纯计算，线程里跑会被 Python 的全局锁卡成单核）。看完删图。"""
    import numpy as np, sys
    from PIL import Image
    sys.path.insert(0, str(ASSETS / '包装系统')); import biaoqian
    fl, fc, keep = a; C = Image.open(fc).convert('RGB'); txt, hit = _label_frame(Image.open(fl).convert('RGB'), C, biaoqian); Path(fl).unlink(); Path(fc).unlink()
    if not keep: return (hit,)
    h, w = txt.shape      # 粗查的帧留缩小的字掩码和灰度图，给“中间动没动”比
    return hit, txt[:h // 4 * 4, :w // 4 * 4].reshape(h // 4, 4, w // 4, 4).any(axis=(1, 3)), np.asarray(C.convert('L').resize((w // 4, h // 4), Image.BILINEAR))


def label_check(job: Job, step=5, w=1080, shots=None, n_workers=None, chunk=None):
    """代码动画标签自检（新4）：同一时刻出一张带字的、一张不带字的（clean=1），差出来的就是字；字下面压到物体（biaoqian.model_mask）就算一帧重叠。
    2026-11-21 新2 提速（判定一个数没改，只改怎么跑）：
      ① 几个无头 Chrome 同时截（默认按核数，8–12 路）；② 实拍/图片镜头、不是代码动画的镜头、盖了实拍的区间（新15）不查；
      ③ 先每 step 帧（默认 5）粗查一帧；两次粗查之间有重叠、或字的附近画面在动，再把中间的帧逐帧查——静止又没压字的区间中间那几帧画面和两头一样，不用再截；
      ④ 镜头的动画代码、起止、卡点、盖实拍区间、宽度都没变，直接用上次这个镜头的结果（动画/自查/标签自检缓存.json）。
    step=1 就是全部逐帧查。shots 只查这几个镜头（别的镜头用缓存，没缓存的记进“没查”）。"""
    build(job); fps = float(job.fps); step = max(1, int(step)); n = workers(n_workers)
    allb, skip = code_shots(job, anim_shots(job))
    cov = cover_list(job); B = rj(job.p('分镜.json')); cf = adir(job) / '自查' / '标签自检缓存.json'; cache = rj(cf) if cf.exists() else {}
    want, todo, keys, frames = [], [], {}, {}
    for s in allb:
        a, b = round(s['a'] * fps), round(s['b'] * fps)
        frames[s['id']] = [i for i in range(a, b) if not covered(cov, s['id'], i - a)]
        keys[s['id']] = hashlib.sha256(json.dumps([fingerprint(job, adir(job), [s['id']]), B.get('anchors'), [[k0, k1] for k0, k1, *_ in cov.get(s['id'], [])], w, 'v1'],
                                                  ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        if shots and s['id'] not in shots: continue
        want.append(s['id'])
        if (cache.get(s['id']) or {}).get('key') == keys[s['id']]: continue
        todo.append(s)
    tmp = Path(tempfile.mkdtemp(prefix='thinkcut_lab_')); got = {}      # {镜头: {帧号: (重叠?, 字的掩码1/4, 不带字的灰度1/4)}}
    def shoot(task):
        s, k, idx, keep = task; ts = [f'{max(i / fps, s["a"] + 1e-4):.5f}' for i in idx]; pre = tmp / f"{s['id']}_{k}_"
        for q, tag in ((_q(job), 'L'), (_q(job, 'clean=1'), 'C')):
            for tr in range(3):
                try:
                    with _port() as port:
                        _run_group(cap(job) + port + ['--q', q, '--w', str(w), '--t', ','.join(ts), '--out', f'{pre}{tag}', '--frame-timeout', '30'],
                                   timeout=90 + 3 * len(ts), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                    if tr == 2: die(f"标签自检：{s['id']} 第 {idx[0]}–{idx[-1]} 帧连续 3 次没截出来（{type(e).__name__}）")
        return s['id'], dict(zip(idx, pool.map(_label_one, [(f'{pre}L_{t}.jpg', f'{pre}C_{t}.jpg', keep) for t in ts], chunksize=4)))
    def batch(lists, keep):
        tasks = []
        for s, idx in lists:
            c = chunk or max(8, min(40, -(-sum(len(x) for _, x in lists) // n)))
            tasks += [(s, k, idx[j:j + c], keep) for k, j in enumerate(range(0, len(idx), c))]
        for sid, out in _parallel(shoot, tasks, n): got.setdefault(sid, {}).update(out)
        return sum(len(t[2]) for t in tasks)
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    pool = ProcessPoolExecutor(os.cpu_count() or 4, mp_context=multiprocessing.get_context('fork'))
    try:
        pool.submit(int).result()      # 先把判定用的子进程都开好，再开截图线程（不在线程跑着的时候分叉进程）
        coarse = {s['id']: sorted(set(frames[s['id']][::step]) | set(frames[s['id']][-1:])) for s in todo}
        n1 = batch([(s, coarse[s['id']]) for s in todo], True) if todo else 0
        fine = []
        for s in todo:
            fs, cs = frames[s['id']], coarse[s['id']]; pos = {i: k for k, i in enumerate(fs)}; g = got.get(s['id'], {}); more = []
            for x, y in zip(cs, cs[1:]):
                if g[x][0] or g[y][0] or not _same_around_text(g[x][1:], g[y][1:]): more += fs[pos[x] + 1:pos[y]]
            if more: fine.append((s, more))
        n2 = batch(fine, False) if fine else 0
    finally:
        pool.shutdown(); shutil.rmtree(tmp, ignore_errors=True)
    for s in todo:
        cache[s['id']] = dict(key=keys[s['id']], 帧数=len(frames[s['id']]), 截了=len(got.get(s['id'], {})), 重叠=[float(f'{max(i / fps, s["a"] + 1e-4):.5f}') for i, v in sorted(got.get(s['id'], {}).items()) if v[0]])
    wj(cf, cache)
    ok = {sid for sid in keys if (cache.get(sid) or {}).get('key') == keys[sid]}
    bad = {sid: cache[sid]['重叠'] for sid in keys if sid in ok and cache[sid]['重叠']}
    rep = dict(查了帧数=sum(cache[sid]['帧数'] for sid in ok), 重叠帧数=sum(len(v) for v in bad.values()), 镜头=bad, 粗查间隔=step,
               没查=sorted(set(keys) - ok), 不查=skip, 盖实拍不查帧数=sum(round(s['b'] * fps) - round(s['a'] * fps) - len(frames[s['id']]) for s in allb))
    wj(adir(job) / '自查' / '标签重叠.json', rep)
    reuse = [sid for sid in want if sid not in {s['id'] for s in todo}]
    say(f"标签自检（{n} 路，每 {step} 帧粗查、有动静的区间逐帧）：这次截了 {n1} + {n2} 帧" + (f"，{'、'.join(reuse)} 没改过、用上次结果" if reuse else '')
        + f"；{len(ok)} 个镜头共 {rep['查了帧数']} 帧，字压物体 {rep['重叠帧数']} 帧" + (f"（{', '.join(f'{k} {len(v)} 帧' for k, v in bad.items())}）" if bad else '')
        + (f"；不查：{'、'.join(skip)}（实拍/图片或别的工程）" if skip else '') + (f"；还没查：{'、'.join(rep['没查'])}" if rep['没查'] else ''))
    if bad: die('标签自检没过（要 0 帧）：先挪标签位置再重跑 anim-labelcheck；结果在 动画/自查/标签重叠.json，check 不过、review 也不会建审核线')
    return rep
