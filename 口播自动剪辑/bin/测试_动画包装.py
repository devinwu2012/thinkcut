#!/usr/bin/env python3
"""离线测试（动画与包装系统，2026-11-12 水电反馈）：不开 Blender、不开 Chrome、不碰 NAS 片子。
查：包装系统组件（及格线、网速对比等比、口播大字兼容）、标签选位和逐帧重叠自检、布局常识检查、渲染前硬拦（草图/样张/标签位置）、
渲染尺寸拦截、画面事件只读当前版本、连续帧号分份、cap.mjs 分段参数、命令入口。"""
import json, os, subprocess, sys, tempfile, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'
sys.path.insert(0, str(SK / 'scripts')); sys.path.insert(0, str(SK / 'assets/包装系统'))
os.environ['THINKCUT_SKILL'] = str(SK)
from thinkcut.common import Job, wj, rj   # noqa: E402
from thinkcut import anim                 # noqa: E402
import huazi, biaoqian                # noqa: E402
from PIL import Image, ImageDraw      # noqa: E402
fails = []
_imgd = tempfile.TemporaryDirectory(); IMG = [str(Path(_imgd.name) / '样张.jpg')]; Image.new('RGB', (8, 8)).save(IMG[0])   # 样张/草图确认必须带图（C12）


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


# —— 包装系统 ——
check(set(huazi.COMPONENTS) == set(huazi.KIND) - {'人挡字'} and {'jigexian', 'wangsu', 'koubo', 'comment'} <= set(huazi.COMPONENTS), '包装系统 12 个组件都分了文字类/卡片类，含及格线、网速对比、口播大字、评论弹窗')
check(huazi.RHYTHM['inn'] == 0.40 and huazi.RHYTHM['out'] == 0.30 and huazi.ACCENT == (100, 169, 232) and 'PingFang' in huazi.FONT_FILE, '统一节奏 0.40/0.30 秒、强调蓝 #64A9E8、苹方')
ws = dict(bars=[dict(t='外网', v=2000), dict(t='5 类线', v=100), dict(t='内网', v=10000)])
L = huazi.wangsu_lengths(ws, 1000)
check(abs(L[0] / L[2] - 0.2) < 1e-9 and abs(L[1] / L[2] - 0.01) < 1e-9, '网速对比横条按真实数值等比（2000:100:10000）')
spec = dict(use='wangsu', bg=True, _at=0.0, _until=3.0, bars=ws['bars'], race=dict(title='传 50GB', lanes=[dict(t='千兆', time='约 7 分钟', sec=430), dict(t='万兆', time='约 45 秒', sec=45, hi=True)]))
im = huazi.still(spec, 2.5); check(im.size == (2160, 2880) and im.getpixel((5, 5))[:3] == huazi.BG, '网速对比出得了 4K 静帧，bg=true 铺满底色')
jg = dict(use='jigexian', _at=0.0, _until=3.0, title='网线怎么选', up='预算够就升级', rows=[dict(t='5 类线', level='below', _at=0.2), dict(t='6 类线', level='on', _at=0.8), dict(t='超 6 类线', level='above', _at=1.4, tag='万兆')])
a, b = huazi.still(jg, 0.5), huazi.still(jg, 2.5)
check(a.getbbox() is not None and sum(b.getchannel('A').histogram()[1:]) > sum(a.getchannel('A').histogram()[1:]), '及格线说到哪一档出哪一档（后面画面比前面多）')
check(huazi.koubo(['为什么老讲材料', '却很少讲【工艺】？']).mode == 'RGBA', '旧花字库 huazi.koubo 照旧能调（返回透明图）')
tok = [dict(c=c, t0=i * 0.2, t1=i * 0.2 + 0.2) for i, c in enumerate('我们先说网线不要用五类最好超六类')]
r = huazi.resolve([dict(use='jigexian', at='网线', until='超六类', rows=[dict(t='5', level='below', at='五类'), dict(t='6', level='above', at='超六类')])], huazi.Words(tok))
check(abs(r[0]['_at'] - 0.8) < 1e-9 and abs(r[0]['rows'][0]['_at'] - 1.8) < 1e-9 and abs(r[0]['rows'][1]['_at'] - 2.6) < 1e-9, '花字.json 原话自动换成秒（子项按顺序在上一处之后找）')
w = huazi.rules([dict(n=1, use='statement', start=0, end=100), dict(n=2, use='number', start=90, end=200), dict(n=3, use='statement', start=300, end=400), dict(n=4, use='statement', start=2000, end=2100)])
check(any('同时' in x for x in w) and any('金句 3 次' in x for x in w), '克制档规矩：同时出现、金句超过 2 次都会提示')

# —— 标签选位、重叠自检 ——
frame = Image.new('RGB', (2160, 2880), (22, 24, 29)); d = ImageDraw.Draw(frame); d.rectangle((800, 1200, 1500, 2000), fill=(230, 230, 230))
lab = dict(id='马桶', text='马桶', point='p', _a=0, _b=9); pt = (1150, 1600)
p = biaoqian.choose(frame, lab, pt, frame.size)
check(p['rect'] is not None and not biaoqian.overlap(p['rect'], (800, 1200, 1500, 2000)), '自动选位不压白模，挑暗处')
rec = dict(points=dict(p=dict(x=1150, y=1600, in_frame=True)))
_, boxes = biaoqian.draw(frame, [lab], rec, {'马桶': p}, 1.0)
check(biaoqian.check_frame(frame, boxes, [lab]) == [], '放对了：逐帧自检 0 问题')
bad = {'马桶': dict(mode='画面区域', rect=(1000, 1500, 1250, 1600))}; _, boxes = biaoqian.draw(frame, [lab], rec, bad, 1.0)
check(any('压模型' in x for x in biaoqian.check_frame(frame, boxes, [lab])), '字压在模型上：自检报出来')
l2 = dict(lab, id='水箱'); _, boxes = biaoqian.draw(frame, [lab, l2], rec, {'马桶': p, '水箱': p}, 1.0)
check(any('字压字' in x for x in biaoqian.check_frame(frame, boxes, [lab, l2])), '两个字叠在一起：自检报字压字')
fx = biaoqian.choose(frame, dict(lab, fixed=dict(region=[100, 500, 700, 700])), pt, frame.size)
check(fx['mode'] == '画面区域' and abs((fx['rect'][0] + fx['rect'][2]) / 2 - 400) < 1, '固定在画面区域：字在区域里居中，不再自动找')
twin = Image.new('RGB', (2160, 2880), (22, 24, 29)); d = ImageDraw.Draw(twin); d.rectangle((1000, 400, 1300, 2400), fill=(230, 230, 230))
u = biaoqian.choose(twin, lab, (1150, 1400), twin.size)
check(u['unsure'] and len(u['options']) >= 2, '左右两边一样空：拿不准，给出 2–3 个候选位置样张')
with tempfile.TemporaryDirectory() as t:
    out = biaoqian.position_sheet(twin, lab, dict(x=1150, y=1400), u, Path(t) / 's.jpg'); check(out and Path(out).exists(), '位置样张能出图（A/B/C 拼一张）')

# —— 布局常识检查 ——
BT = SK / 'assets/blender_template'; C = load(BT / '布局检查.py', 'buju'); base = json.loads((BT / '布局.json').read_text(encoding='utf-8'))
check(C.check(base)['errors'] == [], '模板示例卫生间布局检查通过')
def lay(f):
    q = json.loads(json.dumps(base)); f(q); return ' '.join(C.check(q)['errors'])
check('空了' in lay(lambda q: q['showers'][0].update(rect=[2.0, 1.2, 2.54, 2.14], head=[2.27, 2.14])), '马桶和淋浴间之间留大空：拦（水电第 4 轮）')
check('中线' in lay(lambda q: q['showers'][0].update(head=[1.8, 2.14])), '花洒不在淋浴间中线：拦')
check('挡住了门' in lay(lambda q: q['items'][0].update(center=[2.0, 0.8], size=[0.4, 0.5, 0.8], rotation=180, back_wall=None)), '马桶挡淋浴房玻璃门：拦')
check('互撞' in lay(lambda q: q['doors'].append(dict(id='储物门', wall='w', hinge=[0, 0.3], width=0.7, closed=90, opened=0))), '两扇门开的时候互撞：拦')
check('背面没靠墙' in lay(lambda q: q['items'].append(dict(id='沙发', kind='沙发', center=[0.9, 0.9], size=[0.3, 0.3, 0.4], rotation=0))), '沙发/床不靠墙：拦')

# —— 老片子：工程里的 镜头.json 照样读（Q045 兼容）——
with tempfile.TemporaryDirectory() as t:
    F = Path(t); wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=300))
    wj(F / '分镜.json', dict(anchors={}, shots=[dict(id='A01', kind='B', a=1.0, b=7.0)]))
    wj(F / '分镜规格.json', dict(anchors={}, shots=[dict(id='A01', a=1.0, b=7.0)]))
    job = Job(F); anim.blender_new(job, '老'); P = F / 'Blender_老'
    wj(P / '镜头.json', dict(shots=[dict(shot='A01', 讲什么='关系', 口播要点=['马桶和淋浴间挨着放'])]))
    fp0 = anim.fingerprint(job, P, ['A01'])
    check(anim.project_shots(P) == ['A01'] and any('草图' in x for x in anim.gate(job, P, ['A01'], False)), '老工程 镜头.json：照样读镜头和口播要点')
    try: anim.confirm(job, '草图', P, ['A01'], IMG, '可以', []); ok = False
    except SystemExit as e: ok = '还缺' in str(e)
    check(ok, '老工程 镜头.json：草图照样要逐条对照')
    anim.confirm(job, '草图', P, ['A01'], IMG, '可以', ['马桶和淋浴间挨着放=紧贴'])
    check(anim.gate(job, P, ['A01'], False) == [] and anim.fingerprint(job, P, ['A01']) == fp0, '老工程 镜头.json：确认后放行，指纹算法没变（老确认记录不作废）')
    sp = rj(F / '分镜规格.json'); sp['shots'][0]['口播要点'] = ['马桶和淋浴间挨着放', '换气扇在正上方']; wj(F / '分镜规格.json', sp)
    check(anim.shots_doc(P)[0]['A01']['口播要点'] == ['马桶和淋浴间挨着放', '换气扇在正上方'] and anim.shots_doc(P)[0]['A01']['讲什么'] == '关系', '两处都写了：以 分镜规格.json 为准（没写的字段沿用 镜头.json）')

# —— 渲染前硬拦、登记、尺寸 ——
with tempfile.TemporaryDirectory() as t:
    F = Path(t); wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=300))
    wj(F / '分镜.json', dict(anchors={}, shots=[dict(id='A01', kind='B', a=1.0, b=7.0), dict(id='R02', kind='B', a=7.0, b=9.0)]))
    job = Job(F); anim.blender_new(job, '测'); P = F / 'Blender_测'
    check(not (P / '镜头.json').exists(), '新 Blender 工程不再带 镜头.json（口播要点只写在 分镜规格.json，Q045）')
    check(any('口播要点' in x for x in anim.gate(job, P, ['A01'], False)), '分镜规格.json 没写口播要点：不让渲染')
    # 口播要点只写一份：分镜规格.json 的镜头里（2026-11-13，Q045）
    wj(F / '分镜规格.json', dict(anchors={}, shots=[dict(id='A01', a=1.0, b=7.0, 工程='Blender_测', 讲什么='关系', 口播要点=['马桶和淋浴间挨着放', '换气扇装在淋浴间正上方'], 画面='全景 → 推到换气扇', 运镜='全景 → 中景（推）'), dict(id='R02', a=7.0, b=9.0)]))
    check(anim.project_shots(P) == ['A01'], '出片.py 默认镜头：分镜规格里 "工程" 写本工程的那些')
    g = anim.gate(job, P, ['A01'], False); check(any('草图' in x for x in g), '讲关系的镜头没确认逻辑草图：连样张都不让出')
    try: anim.confirm(job, '草图', P, ['A01'], IMG, '可以', ['马桶和淋浴间挨着放=紧贴']); ok = False
    except SystemExit as e: ok = '还缺' in str(e)
    check(ok, '草图确认要逐条对照口播要点，缺一条就不记')
    anim.confirm(job, '草图', P, ['A01'], IMG, '可以', ['马桶和淋浴间挨着放=紧贴', '换气扇装在淋浴间正上方=推到顶上'])
    check(anim.gate(job, P, ['A01'], False) == [], '草图确认后可以出样张')
    check(any('没有样张确认' in x for x in anim.gate(job, P, ['A01'], True)), '没有样张确认：整段渲染硬拦')
    try: anim.confirm(job, '样张', P, ['A01'], IMG, ''); ok = False
    except SystemExit: ok = True
    check(ok, '样张确认必须写用户原话')
    anim.confirm(job, '样张', P, ['A01'], IMG, '样张可以')
    check(anim.gate(job, P, ['A01'], True) == [], '有样张确认（代码没改）：放行整段渲染')
    sp = rj(F / '分镜规格.json'); sp['shots'][0]['口播要点'].append('多说一句'); wj(F / '分镜规格.json', sp)
    check(any('改过' in x for x in anim.gate(job, P, ['A01'], True)), '确认之后改了 分镜规格.json 里的口播要点：样张确认作废')
    sp['shots'][0]['口播要点'].pop(); wj(F / '分镜规格.json', sp)
    check(anim.gate(job, P, ['A01'], True) == [], '口播要点改回去：确认又对得上')
    (P / '场景.py').write_text((P / '场景.py').read_text(encoding='utf-8') + '\n# 改了一行\n', encoding='utf-8')
    check(any('改过' in x for x in anim.gate(job, P, ['A01'], True)), '确认之后改了动画代码：之前的样张确认作废，要重出样张')
    wj(P / '标签定位.json', {'马桶': dict(unsure=True, why='前两个位置分不出高低', options=[dict(rect=[1, 2, 3, 4])] * 3)})
    check(any('拿不准' in x for x in anim.gate(job, P, ['A01'], True)), '标签位置拿不准：整段渲染硬拦')
    anim.confirm(job, '标签位置', P, ['A01'], [], '选 B', label='马桶', pick='B')
    check(rj(P / '标签定位.json')['马桶']['unsure'] is False and rj(P / '标签定位.json')['马桶']['mode'] == '画面区域', '用户选了位置样张：写成固定区域')
    (P / '布局.json').write_text(json.dumps(dict(base, showers=[dict(base['showers'][0], head=[1.8, 2.14])]), ensure_ascii=False), encoding='utf-8')
    check(any('布局检查没过' in x for x in anim.gate(job, P, ['A01'], False)), '布局检查不过：不让渲染')
    anim.register(job, P, 'blender', ['A01'], 50, {'A01': P / 'A01_预览50.mp4'}, [dict(shot='A01', t=3.0, name='推近')], [1080, 1440])
    check(len(anim.render_size_problems(job)) == 1, '动画镜头还是 50% 预览：导出前检查报出来')
    anim.register(job, P, 'blender', ['A01'], 100, {'A01': P / 'A01.mp4'}, None, [2160, 2880])
    check(anim.render_size_problems(job) == [], '重渲 100% 后：导出前检查放行')
    wj(F / '旧事件.json', [dict(shot='A01', t=99, name='旧')]); lst = rj(F / '合成/动画清单.json')
    lst['projects'].append(dict(name='动画_旧', kind='code', events=str(F / '旧事件.json'))); wj(F / '合成/动画清单.json', lst)
    ev = anim.current_events(job); check([e['name'] for e in ev] == ['推近'], '画面事件只读当前进成片的那一版（旧工程的事件表不读）')
    ps = anim.pieces(job); check([(p['shot'], p['start'], p['frames']) for p in ps] == [('A01', 30, 180), ('R02', 210, 60)] and ps[1]['file'] is None, '总装拼接表按 分镜.json 帧位置排，没登记的镜头用黑帧')

# —— 2026-11-20 授权免看：用户事先说过“不用等我确认”，AI 可放行整段出片；样张图和原话照样要带，交片时提醒补看 ——
with tempfile.TemporaryDirectory() as t:
    F = Path(t); wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=300))
    wj(F / '分镜.json', dict(anchors={}, shots=[dict(id='A01', kind='B', a=1.0, b=7.0)]))
    wj(F / '分镜规格.json', dict(anchors={}, shots=[dict(id='A01', a=1.0, b=7.0, 工程='HF_免', 讲什么='过程', 口播要点=['先后三步'])]))
    job = Job(F); anim.hf_new(job, '免'); P = F / 'HF_免'
    check(any('没有样张确认' in x for x in anim.gate(job, P, ['A01'], True)) and anim.unreviewed_waivers(job) == [], '没有免看也没有确认：整段出片照旧被拦，也没有补看提醒')
    for kind, kw in (('草图', dict(checks=['先后三步=三步排开'])), ('标签位置', dict(label='x', pick='A'))):
        try: anim.confirm(job, kind, P, ['A01'], IMG, '这条不用等我确认', waive=True, **kw); ok = False
        except SystemExit as e: ok = '没有免看' in str(e)
        check(ok, f'{kind}用授权免看：被拒（只有样张能免看）')
    try: anim.confirm(job, '样张', P, ['A01'], [], '这条不用等我确认', waive=True); ok = False
    except SystemExit as e: ok = '要带' in str(e)
    check(ok, '授权免看也要带样张图')
    try: anim.confirm(job, '样张', P, ['A01'], IMG, '', waive=True); ok = False
    except SystemExit: ok = True
    check(ok, '授权免看也要写用户授权的原话')
    anim.confirm(job, '样张', P, ['A01'], IMG, '这条不用等我确认', waive=True)
    check(anim.gate(job, P, ['A01'], True) == [] and rj(F / '合成/确认记录.json')['items'][-1].get('免看') is True, '授权免看：放行整段出片，记录里标了免看')
    w = anim.unreviewed_waivers(job)
    check([(a, b, [Path(f).name for f in c]) for a, b, c in w] == [('HF_免', 'A01', ['样张.jpg'])], '状态检查列出没补看的授权免看样张（镜头号＋图片）')
    import io, contextlib; from thinkcut import status
    buf = io.StringIO()
    wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=300, project='测试')); job = Job(F)
    with contextlib.redirect_stdout(buf): status.run(job)
    check('授权免看' in buf.getvalue() and 'A01' in buf.getvalue(), 'status 输出里有“交片时发给用户补看”的提醒')
    anim.confirm(job, '样张', P, ['A01'], IMG, '样张我看了，可以')
    check(anim.unreviewed_waivers(job) == [] and anim.gate(job, P, ['A01'], True) == [], '用户补看后记普通样张确认：提醒消失，仍放行')
    anim.confirm(job, '样张', P, ['A01'], IMG, '这条不用等我确认', waive=True)
    check(anim.unreviewed_waivers(job) == [], '补看过之后再有对得上的普通确认在，免看记录不再提醒')

# —— 2026-11-13 详细审查：样张按镜头认、草图跟着要点、事件只认当前登记、预览尺寸看实物、--seconds 上限 ——
with tempfile.TemporaryDirectory() as t:
    import os, time
    F = Path(t); wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=600))
    wj(F / '分镜.json', dict(anchors={}, shots=[dict(id='A01', kind='B', a=1.0, b=4.0), dict(id='A02', kind='B', a=4.0, b=8.0)]))
    sh = lambda **k: dict(dict(工程='Blender_审', 讲什么='过程', 不涉及布局='不是房间'), **k)
    wj(F / '分镜规格.json', dict(anchors={}, shots=[sh(id='A01', 口播要点=['先刷第一遍']), sh(id='A02', 口播要点=['再刷第二遍'])]))
    job = Job(F); anim.blender_new(job, '审'); P = F / 'Blender_审'
    anim.confirm(job, '样张', P, ['A01'], IMG, '第一个可以'); anim.confirm(job, '样张', P, ['A02'], IMG, '第二个可以')
    check(anim.gate(job, P, ['A01', 'A02'], True) == [], '两个镜头分开确认样张、一起整段渲染：不误拦')
    wj(F / '合成/确认记录.json', dict(items=[])); anim.confirm(job, '样张', P, ['A01', 'A02'], IMG, '都可以')
    check(anim.gate(job, P, ['A02'], True) == [], '两个镜头一起确认样张、单独渲其中一个：不误拦')
    sp = rj(F / '分镜规格.json'); sp['shots'][1]['口播要点'] = ['再刷第二遍', '等干透']; wj(F / '分镜规格.json', sp)
    g = anim.gate(job, P, ['A01', 'A02'], True); check(len(g) == 1 and g[0].startswith('A02') and '改过' in g[0], '只改了 A02 的口播要点：只有 A02 的样张确认作废')
    sp['shots'][0].pop('讲什么'); wj(F / '分镜规格.json', sp)
    check(any('讲什么' in x for x in anim.gate(job, P, ['A01'], False)), '镜头没写“讲什么”：不让渲染（以前不写就不要求草图）')
    sp['shots'][0].update(讲什么='关系', 口播要点=[]); wj(F / '分镜规格.json', sp)
    try: anim.confirm(job, '草图', P, ['A01'], IMG, '可以', []); ok = False
    except SystemExit: ok = True
    check(ok, '镜头还没写口播要点：草图确认记不上（以前 0 条对照也能记）')
    sp['shots'][0]['口播要点'] = ['马桶挨着淋浴间']; wj(F / '分镜规格.json', sp)
    anim.confirm(job, '草图', P, ['A01'], IMG, '可以', ['马桶挨着淋浴间=紧贴']); check(anim.gate(job, P, ['A01'], False) == [], '草图逐条对照后放行')
    sp['shots'][0]['口播要点'].append('换气扇在正上方'); wj(F / '分镜规格.json', sp)
    check(any('重新逐条对照' in x for x in anim.gate(job, P, ['A01'], False)), '草图确认后又加了口播要点：要按新要点重对')
    # 镜头换工程重做：旧工程还负责别的镜头，但它事件表里这个镜头的事件不再算
    Q = F / '动画'; Q.mkdir()
    anim.register(job, Q, 'code', ['A01', 'A02'], 100, {'A01': Q / 'A01.mp4', 'A02': Q / 'A02.mp4'}, [dict(shot='A01', t=1.5, name='旧A01'), dict(shot='A02', t=5.0, name='A02事件')], [2160, 2880])
    anim.register(job, P, 'blender', ['A01'], 100, {'A01': P / 'A01.mp4'}, [dict(shot='A01', t=2.5, name='新A01')], [2160, 2880])
    check([e['name'] for e in anim.current_events(job)] == ['新A01', 'A02事件'], '镜头改给别的工程重做后：旧工程里它的事件不再读（音效不会卡到旧时间）')
    # 登记表是 100% 但 B肉 比出片旧：导出前要拦
    for x in (Q / 'A02.mp4', P / 'A01.mp4', F / '合成/B肉.mp4'): x.write_bytes(b'x')
    now = time.time(); os.utime(F / '合成/B肉.mp4', (now, now)); os.utime(Q / 'A02.mp4', (now - 60, now - 60)); os.utime(P / 'A01.mp4', (now - 60, now - 60))
    check(anim.render_size_problems(job) == [], '出片都比 B肉 旧：放行')
    os.utime(P / 'A01.mp4', (now + 60, now + 60))
    r = anim.render_size_problems(job); check(len(r) == 1 and 'A01' in r[0] and '重新总装' in r[0], '重渲登记后没重新总装（出片比 B肉 新）：导出前拦')
    r = subprocess.run([sys.executable, '-B', str(SK / 'scripts/thinkcut_edit.py'), 'export', str(F), '--seconds', '189.9'], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    check(r.returncode != 0 and '最多 30 秒' in (r.stdout + r.stderr), 'export --seconds 超过 30 秒：拦（以前写成接近全片能绕过“用户说 OK”）')

# —— 2026-11-13 详细审查·小项：改字不丢、小圆圈首帧、主线不改画幅、包装系统画幅 ——
with tempfile.TemporaryDirectory() as t:
    import shutil
    from thinkcut import subs, overlay, resolve as RS
    sample = ROOT / 'samples/烟道反味'; F = Path(t) / 'p'; F.mkdir(); srt = F / 'x.srt'; shutil.copy(sample / '字幕前12条.srt', srt)
    wj(F / 'job.json', dict(rj(sample / 'job_样例.json'), format='3:4', srt=str(srt), frames=600, aroll='', source='', project='', main_timeline='主线X'))
    job = Job(F); subs.render(job); orig = srt.read_text(encoding='utf-8')
    subs.refix(job, 5, '项目', '工地'); row, _ = subs.refix(job, 5, '我们', '咱们')
    check(row['text'] == '咱们做了这么多工地' and srt.read_text(encoding='utf-8') == orig, '同一条字幕不带 --srt 改两次：两次都在（以前第二次会把第一次改回），SRT 没动')
    rows = subs.render(job); check(next(r for r in rows if r['n'] == 5)['text'] == '咱们做了这么多工地', '重跑 subs：没写进 SRT 的改字还在（以前全丢）')
    row, _ = subs.refix(job, 5, '我们', '咱们'); check(row['text'] == '咱们做了这么多工地', '改到一半失败后重试同一条命令：接着做，不报“没有旧字”')
    subs.refix(job, 5, '工地', '工程', write_srt=True)
    check('咱们做了这么多工程' in srt.read_text(encoding='utf-8') and '5' not in rj(F / '合成/字幕/改字记录.json'), '带 --srt 时把之前没写进去的改字一起写进 SRT，记录清掉')
    fps = 30000 / 1001; n0 = round(4.01 * fps)
    check(overlay.frame_edge(4.01, fps) <= n0 / fps < overlay.frame_edge(6.0, fps) and (round(6.0 * fps)) / fps > overlay.frame_edge(6.0, fps), '小圆圈按帧边界开关：镜头第一帧算在里面（以前起点 4.01 秒时首帧 4.004 秒没圆圈），下一个镜头的首帧不算')
    for nm in (None, '主线X'):
        try: RS.set_format(job, nm); ok = False
        except SystemExit: ok = True
        check(ok, f'set-format {"不写 --name" if nm is None else "指到主线"}：拒绝，主线不改画幅')
    HZ = load(SK / 'assets/包装系统/huazi.py', 'huazi_fmt'); G = Path(t) / 'g'; G.mkdir(); wj(G / 'job.json', dict(format='9:16'))
    try: HZ.render_list(G, items=[dict(use='人挡字', at=0)]); ok = False
    except SystemExit as e: ok = '人挡字现在只支持 3:4' in str(e)
    check(ok and (HZ.W, HZ.H) == (2160, 3840), '9:16 的片子：包装系统换成 9:16 版式（以前停下）；人挡字仍只支持 3:4，停下说明')
    # —— 2026-11-14 包装系统三画幅（Q068）：三种画幅各有安全区、字号、口播大字位置；整屏卡片装得下 ——
    def box(sp, t=3.0):
        sp = dict(sp, _at=0.0, _until=4.0); return HZ.still(sp, t).getchannel('A').point(lambda v: 255 if v > 110 else 0).getbbox()
    for fmt, size, nav_bottom in (('3:4', (2160, 2880), 340), ('3:4满屏', (2160, 2880), 340), ('9:16', (2160, 3840), 1120), ('16:9', (3840, 2160), 200)):
        HZ.set_format(fmt); m = HZ.SAFE; W_, H_ = size
        im = HZ.still(dict(use='number', value='2000', unit='兆', label='北京很多地区的宽带', _at=0.0, _until=4.0), 3.0); x0, y0, x1, y1 = box(dict(use='number', value='2000', unit='兆', label='北京很多地区的宽带'))
        check(im.size == size and m['left'] - 8 <= x0 and x1 <= m['right'] + 8 and m['top'] - 8 <= y0 and y1 <= m['bottom'] + 8, f'{fmt}：花字片段是 {W_}×{H_}，数字强调在安全区里')
        kb = box(dict(use='koubo', lines=['为什么供应商老讲材料', '却很少讲【工艺】？']))
        check(abs((kb[1] + kb[3]) / 2 - HZ.FMT['koubo']) < 60 and kb[0] >= m['left'] - 8 and kb[2] <= m['right'] + 8 and (fmt != '9:16' or kb[3] < 3100), f'{fmt}：口播大字默认落在这个画幅的胸口高度（{HZ.FMT["koubo"]}），不出左右安全区')
        st = box(dict(use='steps', steps=[dict(t='一拽'), dict(t='取下'), dict(t='一卡')]))
        check((st[2] < W_ / 2) if fmt == '16:9' else abs((st[0] + st[2]) / 2 - W_ / 2) < 40, f'{fmt}：步骤卡{"放左上，不压中间的人" if fmt == "16:9" else "顶部居中"}')
        ws2 = dict(use='wangsu', bg=True, title='网速对比', bars=[dict(t='外网宽带', v=2000, sub='x'), dict(t='5 类线', v=100, sub='x'), dict(t='内网', v=10000, sub='x')], race=dict(title='传 50GB', lanes=[dict(t='千兆', time='约 7 分钟', sec=430), dict(t='万兆', time='约 45 秒', sec=45, hi=True)], uses=['照片备份']), _at=0.0, _until=4.0)
        px = HZ.still(ws2, 3.5).convert('RGB'); bb = Image.merge('RGB', [px.getchannel(c).point(lambda v, b=b: 255 if abs(v - b) > 14 else 0) for c, b in zip('RGB', HZ.BG)]).convert('L').getbbox()
        check(bb[1] > nav_bottom + 40 and bb[3] <= m['bottom'] + 8 and bb[0] >= m['left'] and bb[2] <= m['right'], f'{fmt}：整屏的网速对比卡装得下（不碰导航条、不出安全区下边）')
    try: HZ.set_format('1:1'); ok = False
    except SystemExit as e: ok = '不认识画幅' in str(e)
    check(ok, '不认识的画幅：停下说明')
    HZ.set_format('3:4')

# —— 多进程分份、cap.mjs、命令入口 ——
OUT = load(BT / '出片.py', 'chupian')
parts = OUT.split(list(range(180)), 8)
check(len(parts) == 8 and all(p == list(range(p[0], p[-1] + 1)) for p in parts) and sum(map(len, parts)) == 180, 'Blender 多进程：每个进程一段连续帧号，不重不漏')
check(OUT.default_jobs(50) == 8 and OUT.default_jobs(100) == 4, '默认进程数：1080P 8 个、4K 4 个')
cap = (SK / 'scripts/tools/cap.mjs').read_text(encoding='utf-8')      # 老代码动画模板 2026-11-21 下线，截图工具搬到 scripts/tools/
check('--frames' in cap and 'frame-timeout' in cap and '预热' in cap, 'cap.mjs：按帧数出、单帧超时退出、首帧预热')
src = (SK / 'scripts/thinkcut/anim.py').read_text(encoding='utf-8')
check('timeout=60 + timeout_per_frame * n' in src and 'seg=40' in src, '代码动画每 ≤40 帧开一个新 Chrome、整段限时（2026-11-21 起几段同时出）')
class _J: dir = Path('/x/片子')
check('A.page' in cap and anim.cap(_J())[1].endswith('scripts/tools/cap.mjs') and Path(anim.cap(_J())[1]).is_file() and anim.cap(_J())[-1] == '/x/片子/动画/动画.html', '截图统一用 scripts/tools/cap.mjs（--page 指向老工程的 动画.html），老工程不用改文件')
h = subprocess.run([sys.executable, '-B', str(SK / 'scripts/thinkcut_edit.py'), '-h'], capture_output=True, text=True).stdout
check(all(c in h for c in ('blender-new', 'anim-confirm', 'anim-gate', 'anim-assemble', 'anim-labelcheck')), '命令入口有 blender-new、anim-confirm、anim-gate、anim-assemble、anim-labelcheck')
# —— 2026-11-13 C 组：确认必须带图、事件表按镜头合并、压字版不盖正式出片、标签自检进 check/review、status 重跑判断、doctor 查 Blender、杀进程组、取帧提速 ——
import contextlib, io, shutil, time
from thinkcut import status as ST, doctor as DR, resolve as RS2
def said(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): r = fn(*a, **k)
    return r, buf.getvalue()
def stops(fn, *a, **k):
    try: fn(*a, **k); return ''
    except SystemExit as e: return str(e) or '停'
with tempfile.TemporaryDirectory() as t:
    F = Path(t); wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=600, project='工程', main_timeline='主线', source='/x', title='测'))
    wj(F / '分镜.json', dict(anchors={}, shots=[dict(id='A01', kind='B', a=1.0, b=4.0), dict(id='A02', kind='B', a=4.0, b=8.0)], sfx=[dict(t=1.0, file='a.mp3')]))
    sh = lambda **k: dict(dict(工程='Blender_丙', 讲什么='关系', 不涉及布局='不是房间'), **k)
    wj(F / '分镜规格.json', dict(anchors={'x': ['词', None]}, shots=[sh(id='A01', 口播要点=['先刷第一遍']), sh(id='A02', 口播要点=['再刷第二遍'])]))
    job = Job(F); anim.blender_new(job, '丙'); P = F / 'Blender_丙'
    # C12 样张/草图确认必须带至少一张存在的图片
    txt = F / '说明.txt'; txt.write_text('不是图')
    r1 = stops(anim.confirm, job, '样张', P, ['A01'], [], '可以'); r2 = stops(anim.confirm, job, '草图', P, ['A01'], [], '可以', ['先刷第一遍=画了'])
    r3 = stops(anim.confirm, job, '样张', P, ['A01'], [str(F / '没有.jpg')], '可以'); r4 = stops(anim.confirm, job, '样张', P, ['A01'], [str(txt)], '可以')
    check('要带' in r1 and '要带' in r2 and '不存在' in r3 and '要带' in r4 and not (F / '合成/确认记录.json').exists(), '样张/草图确认不带图、图不存在、给的不是图片：都不记（以前 files 为空也能记）')
    anim.confirm(job, '草图', P, ['A01'], IMG, '可以', ['先刷第一遍=画了']); anim.confirm(job, '样张', P, ['A01'], IMG, '可以')
    check([x['files'] for x in rj(F / '合成/确认记录.json')['items']] == [IMG, IMG] or all(Path(x['files'][0]).exists() for x in rj(F / '合成/确认记录.json')['items']), '带了图：照常记下，记录里有图片路径')
    # C3 只重渲部分镜头：事件表按镜头合并
    anim.register(job, P, 'blender', ['A01', 'A02'], 100, {'A01': P / '出片/A01.mp4', 'A02': P / '出片/A02.mp4'},
                  [dict(shot='A01', t=1.5, name='A01旧事件'), dict(shot='A02', t=5.0, name='A02事件'), dict(shot='A02', t=6.0, name='A02事件二')], [2160, 2880])
    anim.register(job, P, 'blender', ['A01'], 100, {'A01': P / '出片/A01.mp4'}, [dict(shot='A01', t=2.5, name='A01新事件')], [2160, 2880])
    check([e['name'] for e in rj(P / '事件.json')] == ['A01新事件', 'A02事件', 'A02事件二'] and [e['name'] for e in anim.current_events(job)] == ['A01新事件', 'A02事件', 'A02事件二'],
          '只重渲 A01：事件.json 只换 A01 的事件，A02 的事件还在（以前整份改写，A02 的丢了）')
    anim.register(job, P, 'blender', ['A02'], 100, {'A02': P / '出片/A02.mp4'}, [], [2160, 2880])
    check([e['name'] for e in rj(P / '事件.json')] == ['A01新事件'], '重渲的镜头这次没有事件：只清它自己的')

# C4 标签自检没过：正式出片不被压字版盖掉
OUT4 = load(BT / '出片.py', 'chupian4')
with tempfile.TemporaryDirectory() as t:
    d = Path(t); final = d / 'A10.mp4'; new = d / 'A10.新出.mp4'; final.write_bytes(b'old-ok'); new.write_bytes(b'new-bad')
    got = OUT4.settle({'A10': (new, final)}, False)
    check(final.read_bytes() == b'old-ok' and got['A10'].name == 'A10_待查.mp4' and got['A10'].read_bytes() == b'new-bad' and not new.exists(),
          '标签自检有压字：正式的 出片/A10.mp4 还是原来那版，新出的留成 A10_待查.mp4（以前同名文件已被压字版覆盖）')
    new.write_bytes(b'new-ok'); got = OUT4.settle({'A10': (new, final)}, True)
    check(final.read_bytes() == b'new-ok' and got['A10'] == final and not new.exists(), '标签自检通过：新出的才换成正式文件')
src4 = (BT / '出片.py').read_text(encoding='utf-8')
check(src4.index('files = settle(pending, total == 0)') < src4.index("anim.register(job, HERE, 'blender'") and "dst = final.with_name(final.stem + '.新出.mp4')" in src4 and 'files[sid] = dst' not in src4,
      '出片.py：ffmpeg 先出到 .新出.mp4，自检之后才换名、登记')
# C13① raw.mkdir 真的会执行（以前写在同一行 # 注释后面）
import tokenize
toks = list(tokenize.generate_tokens(io.StringIO(src4).readline))
code = ' '.join(x.string for x in toks if x.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT))
check('raw . mkdir ( parents = True , exist_ok = True )' in code and not any('mkdir' in x.string for x in toks if x.type == tokenize.COMMENT), '出片.py：raw.mkdir 是一行真代码，不在注释里')

# C7 代码动画标签自检结果进 check、review
with tempfile.TemporaryDirectory() as t:
    F = Path(t); wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=600, project='工程', main_timeline='主线', source='/x'))
    wj(F / '口播字时间.json', dict(tokens=[])); wj(F / '分镜.json', dict(anchors={}, shots=[dict(id='A01', kind='B', a=1.0, b=4.0)], sfx=[]))
    job = Job(F); Q = F / '动画'; Q.mkdir()
    check(anim.label_overlap(job) == ([], None), '没有代码动画登记、也没有标签重叠.json：不提示不拦（老片子）')
    anim.register(job, Q, 'code', ['A01'], 100, {'A01': Q / '出片/A01.mp4'}, None, [2160, 2880])
    bad, hint = anim.label_overlap(job); check(bad == [] and hint and 'anim-labelcheck' in hint and 'A01' in hint, '有代码动画镜头登记但没跑过 anim-labelcheck：只提示，不硬拦')
    msg = stops(RS2.review, job); check('标签' not in msg and '音效' in msg, '没有标签重叠.json：review 不因为标签拦（走到后面的检查）')
    wj(Q / '自查/标签重叠.json', dict(查了帧数=90, 重叠帧数=3, 镜头={'A01': [1.2, 1.23, 1.27]}))
    check(anim.label_overlap(job) == (['A01 字压物体 3 帧'], None), '标签重叠.json 有重叠帧：列成问题')
    msg = stops(RS2.review, job); check('标签自检没过' in msg and 'A01 字压物体 3 帧' in msg, '有字压物体的帧：review 建审核线前停下（不连达芬奇就拦住）')
    wj(Q / '自查/标签重叠.json', dict(查了帧数=90, 重叠帧数=0, 镜头={}))
    msg = stops(RS2.review, job); check(anim.label_overlap(job) == ([], None) and '标签' not in msg, '重叠 0 帧：放行')
srcA = (SK / 'scripts/thinkcut/anim.py').read_text(encoding='utf-8'); srcC = (SK / 'scripts/thinkcut/check.py').read_text(encoding='utf-8')
check("if bad: die('标签自检没过" in srcA and "res['代码动画标签压物体'], lab_hint = label_overlap(job)" in srcC, 'anim-labelcheck 有重叠帧不再正常退出；check 把标签重叠列为一项')

# C8 status：只改音效重跑 beats 不提示重出动画；镜头起止变了、代码比出片新、出片比 B肉 新才提示；字幕图看 SRT、字时间看剪口播交付。C13③ 纯 Blender 的下一步不写 anim-render
with tempfile.TemporaryDirectory() as t:
    F = Path(t) / '片'; F.mkdir(); AR = Path(t) / '口播'; (AR / '审核').mkdir(parents=True); srt = Path(t) / 'x.srt'; srt.write_text('1\n00:00:00,000 --> 00:00:01,000\n字\n', encoding='utf-8')
    wj(AR / '审核/latest_delivery.json', dict(status='complete'))
    wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=600, project='工程', main_timeline='主线', source='/x', title='测', aroll=str(AR), srt=str(srt)))
    shots = [dict(id='F_0', kind='F', a=0.0, b=1.0), dict(id='A01', kind='B', a=1.0, b=4.0), dict(id='A02', kind='B', a=4.0, b=8.0)]
    wj(F / '分镜规格.json', dict(anchors={'x': ['词', None]}, shots=[]))
    wj(F / '口播字时间.json', dict(tokens=[])); wj(F / '分镜.json', dict(anchors={}, shots=shots, sfx=[dict(t=1.0, file='a.mp3')]))
    job = Job(F); Q = F / '动画'; (Q / 'src').mkdir(parents=True); (Q / 'build.py').write_text('# x\n'); (Q / 'src/80_a.js').write_text('// x\n'); (Q / '出片').mkdir()
    for x in ('A01', 'A02'): (Q / f'出片/{x}.mp4').write_bytes(b'x')
    anim.register(job, Q, 'code', ['A01', 'A02'], 100, {x: Q / f'出片/{x}.mp4' for x in ('A01', 'A02')}, None, [2160, 2880])
    (F / '合成/B肉.mp4').write_bytes(b'x'); wj(F / '合成/总装检查.json', dict(bad=[], 镜头起止=anim.shot_spans(job)))
    now = time.time()
    def age(path, sec): os.utime(path, (now + sec, now + sec))
    for path in list(Q.rglob('*')) + [F / '口播字时间.json', F / '分镜规格.json', AR / '审核/latest_delivery.json', srt]: age(path, -600)
    age(F / '合成/B肉.mp4', -300); age(F / '分镜.json', -400)
    check(ST.anim_state(job)[0] is False, 'status：没改过东西，4K 动画不用重跑')
    wj(F / '分镜.json', dict(anchors={}, shots=shots, sfx=[dict(t=2.0, file='b.mp3')])); age(F / '分镜.json', 0)      # 重跑 beats，只改了音效
    check(ST.anim_state(job)[0] is False, 'status：重跑 beats 只改了音效（分镜.json 变新、动画镜头起止没变）：不再提示 4K 动画要重跑')
    out = subprocess.run([sys.executable, '-B', str(SK / 'scripts/thinkcut_edit.py'), 'status', str(F)], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    check('✓ 4K 动画' in out and '✓ 字时间' in out, 'status 命令：同上，4K 动画、字时间都是 ✓')
    wj(F / '分镜.json', dict(anchors={}, shots=shots[:2] + [dict(shots[2], b=8.5)], sfx=[]))
    st = ST.anim_state(job); check(st[0] and 'A02' in st[1] and 'A01' not in st[1] and 'anim-render' in st[2], 'status：动画镜头 A02 的起止变了：提示重出（只点 A02），下一步按代码动画给 anim-render')
    wj(F / '分镜.json', dict(anchors={}, shots=shots, sfx=[])); check(ST.anim_state(job)[0] is False, 'status：起止改回去：不提示')
    age(Q / 'src/80_a.js', 100); st = ST.anim_state(job); check(st[0] and '80_a.js' in st[1] and '代码' in st[1], 'status：改了动画代码（比登记的出片新）：提示重出再总装')
    age(Q / 'src/80_a.js', -600); age(Q / '出片/A01.mp4', -100); st = ST.anim_state(job)
    check(st[0] and 'A01' in st[1] and '重新总装' in st[1] and st[2] == 'anim-assemble', 'status：重渲登记后没总装（出片比 B肉 新）：提示重新总装，下一步 anim-assemble')
    age(Q / '出片/A01.mp4', -600)
    r = rj(F / '合成/总装检查.json'); r.pop('镜头起止'); wj(F / '合成/总装检查.json', r); age(F / '分镜.json', 0)
    check(ST.anim_state(job)[0] is True, 'status：老片子的总装检查里没存镜头起止：照老办法比文件时间')
    wj(F / '合成/总装检查.json', dict(bad=[], 镜头起止=anim.shot_spans(job)))
    wj(F / '合成/字幕/字幕表.json', []); age(F / '合成/字幕/字幕表.json', -300); age(srt, 0); age(AR / '审核/latest_delivery.json', 0)
    out = subprocess.run([sys.executable, '-B', str(SK / 'scripts/thinkcut_edit.py'), 'status', str(F)], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    check('↻ 字幕图（SRT 改过' in out and '↻ 字时间（剪口播重新交付过' in out, 'status：SRT 比字幕表新 → 提示重出字幕；剪口播交付比字时间新 → 提示重跑 words')
    # 纯 Blender 的片子
    G = Path(t) / '纯B'; G.mkdir(); wj(G / 'job.json', dict(fps='30000/1001', format='3:4', frames=600, project='工程', main_timeline='主线', source='/x', title='纯B'))
    wj(G / '口播字时间.json', dict(tokens=[])); wj(G / '分镜规格.json', dict(anchors={'x': ['词', None]}, shots=[])); wj(G / '分镜.json', dict(anchors={}, shots=shots, sfx=[]))
    jg = Job(G); anim.blender_new(jg, '通风')
    out = subprocess.run([sys.executable, '-B', str(SK / 'scripts/thinkcut_edit.py'), 'status', str(G)], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    nxt = next((l for l in out.splitlines() if l.startswith('下一步')), '')
    check('4K 动画' in nxt and '出片.py' in nxt and 'anim-render' not in nxt, 'status：纯 Blender 的片子，下一步给 出片.py → anim-assemble，不再写 anim-render：' + nxt[:60])
    anim.register(jg, G / 'Blender_通风', 'blender', ['A01'], 100, {'A01': G / 'Blender_通风/出片/A01.mp4'}, [], [2160, 2880])
    check('anim-render' not in ST.anim_state(jg)[2] and '出片.py' in ST.anim_state(jg)[2], 'status：按动画清单里登记的工程类型（blender）给下一步')

# C13② doctor 查 Blender：找不到只提示，不算没就绪
keep = DR.find_blender
DR.find_blender = lambda: None; ok0, o0 = said(DR.run); DR.find_blender = lambda: '/x/Blender'; ok1, o1 = said(DR.run); DR.find_blender = keep
l0 = next((l for l in o0.splitlines() if 'Blender' in l), ''); l1 = next((l for l in o1.splitlines() if 'Blender' in l), '')
check(l0.startswith('· ') and '→' in l0 and l1.startswith('✓ ') and ok0 == ok1 and o0.splitlines()[-1] == o1.splitlines()[-1], 'doctor 有 Blender 一项；找不到只提示（·），不计入“没就绪”')

# C13⑤ 出帧超时：连它启动的子进程一起杀（进程组）
with tempfile.TemporaryDirectory() as t:
    pidf = Path(t) / 'pid'; sc = Path(t) / 'p.py'
    sc.write_text('# -*- coding: utf-8 -*-\nimport subprocess, sys, time\nc = subprocess.Popen(["sleep", "60"])\nopen(sys.argv[1], "w").write(str(c.pid))\ntime.sleep(60)\n', encoding='utf-8')
    try: anim._run_group([sys.executable, '-B', str(sc), str(pidf)], timeout=2); kind = '没超时'
    except subprocess.TimeoutExpired: kind = '超时'
    pid = int(pidf.read_text()) if pidf.exists() else 0; alive = True
    for _ in range(30):
        try: os.kill(pid, 0); time.sleep(0.1)
        except (ProcessLookupError, PermissionError): alive = False; break
    check(kind == '超时' and pid and not alive, '出帧进程超时：它启动的子进程（无头 Chrome 的替身）也被杀掉，不留在后台')
    try: anim._run_group([sys.executable, '-c', 'import sys; sys.exit(3)'], timeout=10); kind = ''
    except subprocess.CalledProcessError as e: kind = e.returncode
    check(kind == 3 and anim._run_group([sys.executable, '-c', 'pass'], timeout=10) is None, '出帧进程退出码非 0 照样报出来，正常结束不报')
check('start_new_session=True' in srcA and srcA.count('_run_group(cap(job)') == 2, '代码动画出帧、标签自检都用进程组启动')

# C15 总装比对取帧提速：一次解码取多帧，和原来逐帧取的逐字节一致，比对结果不变
with tempfile.TemporaryDirectory() as t:
    F = Path(t); wj(F / 'job.json', dict(fps='30000/1001', format='3:4', frames=150))
    wj(F / '分镜.json', dict(anchors={}, shots=[dict(id='A01', kind='B', a=1.0, b=3.0), dict(id='A02', kind='B', a=3.0, b=4.2)]))
    job = Job(F); job.fmt = dict(job.fmt, W=432, H=576); v1, v2 = F / 'a1.mp4', F / 'a2.mp4'
    for v, srcf, nf in ((v1, 'testsrc2', 60), (v2, 'testsrc', 36)):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'{srcf}=size=432x576:rate=30000/1001', '-frames:v', str(nf), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(v)], check=True, stdin=subprocess.DEVNULL)
    want = [0, 1, 17, 29, 30, 44, 58, 59, 500]
    one = {i: anim._frame(v1, i, 108, 144) for i in want}; many = anim._frames_at(v1, want + [17], 108, 144); small = anim._frames_at(v1, want, 108, 144, chunk=4)
    check(all(len(one[i]) == 108 * 144 * 3 for i in want[:-1]) and one[500] == b'' and many == one and small == one and len(set(one.values())) == len(want),
          '一次解码取多帧：每一帧和原来逐帧取的逐字节一致（含分几趟取、视频里没有的帧号给空）')
    anim.register(job, F, 'code', ['A01', 'A02'], 100, {'A01': v1, 'A02': v2}, None, [432, 576])
    rep, _ = said(anim.assemble, job)
    old = []          # 原来的做法：每取一帧从头解码一次
    for pc in anim.pieces(job):
        n = pc['frames']
        for i in sorted({0, n // 2, n - 1, anim._most_motion(pc['file'], n)}):
            srcb = anim._frame(pc['file'], i, 108, 144); got = anim._frame(F / '合成/B肉.mp4', pc['start'] + i, 108, 144); d0 = anim._diff(srcb, got)
            near = [anim._diff(anim._frame(pc['file'], j, 108, 144), got) for j in (i - 1, i + 1) if 0 <= j < n]
            old.append(dict(shot=pc['shot'], frame=i, diff=round(d0, 2), neighbors=[round(x, 2) for x in near], ok=d0 <= 4.0 and all(d0 <= x + 0.05 for x in near)))
    check(rep['frames'] == old and len(old) >= 6 and rep['bad'] == [], '逐镜头比对：新取帧办法的每一项结果（差值、前后帧、过没过）和原来逐帧取的完全一样')
    check(rj(F / '合成/总装检查.json')['镜头起止'] == anim.shot_spans(job) == [['A01', 30, 60], ['A02', 90, 36]], '总装时把动画镜头清单和起止存进 合成/总装检查.json（status 拿它判断要不要重出）')

# 总装取帧分批：一次取很多帧时 ffmpeg 解析 select 表达式会失败取回空帧（实测 120 帧一趟就不行）；默认分批、取不全要报错
with tempfile.TemporaryDirectory() as t:
    v = Path(t) / 'big.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=432x576:rate=30000/1001', '-frames:v', '400', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(v)], check=True, stdin=subprocess.DEVNULL)
    idx = list(range(0, 380, 2))
    ref = anim._frames_at(v, idx, 108, 144, chunk=10); dflt = anim._frames_at(v, idx, 108, 144)
    check(len(idx) >= 150 and len(dflt) == len(idx) and all(len(b) == 108 * 144 * 3 for b in dflt.values()) and dflt == ref and len(set(dflt.values())) == len(idx),
          f'取 {len(idx)} 帧：默认分批返回帧数正确，和每 10 帧一趟的结果逐帧相同，没有空帧')
    try: anim._frames_at(v, idx, 108, 144, chunk=300); big_ok = False
    except SystemExit: big_ok = True
    check(big_ok, '一趟塞太多帧取回不全时明确报错停下，不拿空帧去比')

# Blender 模板底色：sRGB 显示值先换成线性值再写进世界背景
import ast, re
_ts = (ROOT / 'skill/thinkcut-edit/assets/blender_template/场景.py').read_text(encoding='utf-8'); _tree = ast.parse(_ts)
_ns = {}; exec(compile(ast.Module([n for n in _tree.body if isinstance(n, ast.FunctionDef) and n.name == 'srgb_to_linear'], []), 'tpl', 'exec'), _ns)
_lin = _ns['srgb_to_linear']('#16181D')
check(all(abs(a - b) < 1e-4 for a, b in zip(_lin, (0.00802, 0.00910, 0.01233))) and _ns['srgb_to_linear']('#FFFFFF') == (1.0, 1.0, 1.0) and _ns['srgb_to_linear']('#000000') == (0.0, 0.0, 0.0),
      f'sRGB→线性换算：#16181D = ({_lin[0]:.5f}, {_lin[1]:.5f}, {_lin[2]:.5f})，白黑两端正确')
check(re.search(r"^BG = srgb_to_linear\('#16181D'\)", _ts, re.M) and "default_value = (*BG, 1)" in _ts, 'Blender 模板背景色 BG 走 srgb_to_linear，并写进世界背景')

# —— 人挡字风格库（2026-11-13）：小分辨率现造口播画面＋假人物遮罩，走和正式一样的 huazi.render_list → rendang ——
import rendang, numpy as np                                  # noqa: E402
from thinkcut import check as CK, resolve as RS2                 # noqa: E402
RW, RH, FR = 240, 320, 30000 / 1001


def fake_matte(top, x0=.3, x1=.7, calls=None):
    """假人物遮罩：从 top（占画高比例）往下、x0–x1 之间是人（代替 RVM 真模型）。"""
    def m(src, start, n, W, H, work_dir):
        a = np.zeros((H, W), np.uint8); a[int(top * H):, int(x0 * W):int(x1 * W)] = 255
        if calls is not None: calls.append((start, n))
        low = np.stack([np.asarray(Image.fromarray(a).resize((W // 2, H // 2), Image.BOX))] * n)
        return rendang.Matte(low, lambda: (a for _ in range(n)))
    return m


def film(t, name, secs=12.0, shots=None, job=None, items=(), size=(RW, RH), src=None):
    d = Path(t) / name; (d / '合成').mkdir(parents=True); n = round(secs * FR); w, h = size
    src = src or f"color=c=black:s={w}x{h}:r=30000/1001,geq=lum='mod(N*7\\,256)':cb=128:cr=128"   # 每帧亮度不同（N×7），查逐帧对齐
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', src, '-frames:v', str(n), '-c:v', 'libx264', '-qp', '0', '-pix_fmt', 'yuv420p',
                    str(d / '合成' / 'A肉.mov')], check=True, stdin=subprocess.DEVNULL)
    wj(d / 'job.json', dict(dict(format='3:4', fps='30000/1001'), **(job or {})))
    wj(d / '口播字时间.json', dict(tokens=[dict(c='万', t0=0.5, t1=0.7)]))
    if shots is not False: wj(d / '分镜.json', dict(anchors={}, shots=shots or [dict(id='F_0', kind='F', a=0.0, b=secs)]))
    wj(d / '花字.json', list(items)); return d


def run_rd(d):
    try: return huazi.render_list(d), None
    except SystemExit as e: return None, str(e)


def it(at, until, text='万兆', **k): return dict(use='人挡字', at=at, until=until, text=text, **k)


with tempfile.TemporaryDirectory() as t:
    rendang.MATTE = fake_matte(.4)
    # 4 套都能出片：帧数＝起止帧差、尺寸＝A肉、和 A肉 逐帧对齐（人物区域就是 A肉 那一帧）
    for k in (1, 2, 3, 4):
        d = film(t, f'套{k}', job={'人挡字风格': k}, items=[it(1.0, 3.6, 小字='第二件事' if k == 2 else None)])
        res, err = run_rd(d); rows = res[0] if res else [dict(file='', start=0, end=0, kind=err, bg=None)]
        r = rows[0]; f = Path(r['file']); n = int(rendang.probe(f)['nb_frames']); sz = rendang.probe(f)
        want = round(3.6 * FR) - round(1.0 * FR)
        check(n == want == r['end'] - r['start'] and (int(sz['width']), int(sz['height'])) == (RW, RH) and r['kind'] == '人挡字' and r['bg'] is True,
              f'人挡字第 {k} 套出片：{n} 帧＝起止帧差 {want}、{RW}×{RH} 和 A肉 一样，花字表 kind=人挡字、bg=true')
        if k != 2:      # 杂志那套整体调色会动人物，其余三套人物用原画面
            o = np.stack(list(rendang.decode(f, 0, n, RW, RH, 'gray'))).astype(float)
            a = np.stack(list(rendang.decode(d / '合成/A肉.mov', r['start'] - 1, n + 2, RW, RH, 'gray'))).astype(float)
            reg = (slice(int(.7 * RH), RH), slice(int(.35 * RW), int(.65 * RW)))
            err = {off: float(np.mean([np.abs(o[i][reg] - a[i + 1 + off][reg]).mean() for i in range(1, n - 1)])) for off in (-1, 0, 1)}
            check(err[0] < 2.5 and err[-1] > 4 and err[1] > 4, f'人挡字第 {k} 套和 A肉 逐帧对齐（人物区域和同一帧差 {err[0]:.1f}，和前后帧差 {err[-1]:.1f}/{err[1]:.1f}）')
    # 遮挡比例算法
    ink = np.zeros((10, 10)); ink[2:6, 2:8] = 1; al = np.zeros((10, 10)); al[4:, :] = 1
    check(abs(rendang.coverage(ink, al) - .5) < 1e-9 and rendang.coverage(ink, al * .5) == .25 and rendang.coverage(ink, np.zeros((10, 10))) == 0, '遮挡比例＝Σ(字墨×遮罩)/Σ字墨（半透明遮罩按比例算）')
    rec = json.loads((Path(t) / '套1/合成/花字/01_人挡字.json').read_text(encoding='utf-8'))
    check(abs(rec['遮挡']['平均'] - rec['遮挡']['估计']) < .05 and rec['遮挡']['通过'] and rec['入场帧'] == round(.8 * FR), '逐帧算的遮挡比例和按平均遮罩估的一致（去掉了 0.8 秒出场段）')
    # 头顶空间：宽松→默认；紧但人窄→头后；紧且人宽→头侧；人占满→停下不硬放
    A = lambda top, x0, x1: rendang.analyse(fake_matte(top, x0, x1)('', 0, 3, 2160, 2880, '').low, 2160, 2880)
    how = lambda k, a: rendang.place(rendang.STYLES[k], '万兆', None, a, 2160, 2880)[1]
    check(how(1, A(.4, .3, .7)).startswith('默认'), '头顶空间够：默认放法（大字压在头顶）')
    check(how(1, A(.2, .4, .6)).startswith('头后'), '头顶紧、人窄：下移到头后（挡得更多，仍在范围内）')
    h3 = how(1, A(.2, .3, .7)); _, _, c3, tried = rendang.place(rendang.STYLES[1], '万兆', None, A(.2, .3, .7), 2160, 2880)
    check(h3.startswith('上移') and any(x.startswith('头后') for x in tried), f'头顶紧、人宽：头后挡太多→往上挪到导航条下（{h3}，被挡最多的字 {c3:.0%}）')   # 2026-11-21：多了“上移”这一步
    h4 = how(1, A(.12, .3, .7)); _, _, c4, tried = rendang.place(rendang.STYLES[1], '万兆', None, A(.12, .3, .7), 2160, 2880)
    check(h4.startswith('头侧') and any(x.startswith('上移') for x in tried) and any(x.startswith('缩字') for x in tried), f'头顶更紧：头后、上移、放大、缩字都不行→改放头侧（{h4}，被挡最多的字 {c4:.0%}）')
    try: rendang.place(rendang.STYLES[1], '万兆', None, A(.15, 0, 1), 2160, 2880); ok = False
    except SystemExit as e: ok = '放不下' in str(e) and '头后' in str(e)
    check(ok, '人占满画面、哪种放法都不行：停下说明每种放法为什么不行，不硬放')
    msgs = {}
    for k in (1, 2):
        try: rendang.place(rendang.STYLES[k], '万兆', None, A(.15, 0, 1), 2160, 2880); msgs[k] = ''
        except SystemExit as e: msgs[k] = str(e)
    ok = '头侧' in msgs[1] and msgs[2] and '头侧' not in msgs[2] and all(x['放法'] != '头侧' for x in rendang.STYLES[2]['fallback'])
    check(ok, '退路是每套风格的参数：杂志刊名没有“头侧”这一步')
    # 规矩：处数、间隔、露脸段、分镜、风格、字体、画幅
    d, = [film(t, '四处', job={'人挡字风格': 1, '人挡字间隔秒': 0.5}, items=[it(0.5 + 2.8 * i, 2.8 + 2.8 * i) for i in range(4)])]
    _, err = run_rd(d); check(err and '最多 3 处' in err and not (d / '合成/花字/01_人挡字.mov').exists(), '超过 3 处：停下，一段都不出（间隔用 job.json 的 人挡字间隔秒 调小了也照样拦处数）')
    d = film(t, '间隔', job={'人挡字风格': 1}, items=[it(0.5, 3.0), it(4.0, 6.5)])
    _, err = run_rd(d); check(err and '至少 20 秒' in err, '两处只隔 1 秒：停下（默认至少 20 秒）')
    d = film(t, '镜头', job={'人挡字风格': 1}, shots=[dict(id='F_0', kind='F', a=0, b=2.0), dict(id='A01', kind='B', a=2.0, b=12.0)], items=[it(0.5, 3.0)])
    _, err = run_rd(d); check(err and '不是露脸段' in err and 'A01' in err, '压到 B-roll/动画镜头：停下')
    d = film(t, '无分镜', job={'人挡字风格': 1}, shots=False, items=[it(0.5, 3.0)])
    _, err = run_rd(d); check(err and '没有 分镜.json' in err and 'beats' in err, '没有分镜：明确提示先出分镜')
    d = film(t, '风格名', job={'人挡字风格': '没这套'}, items=[it(0.5, 3.0)])
    _, err = run_rd(d); check(err and '认不出' in err and '镜头对焦字' in err, '风格写错：停下列出 4 套')
    keep = rendang.FONT_DIR; rendang.FONT_DIR = Path(t) / '没有字体'
    d = film(t, '字体', job={'人挡字风格': 1}, items=[it(0.5, 3.0)])
    _, err = run_rd(d); rendang.FONT_DIR = keep
    check(err and '缺人挡字字体' in err and 'source-han-sans' in err and '素材库/字体' in err, '字体缺失：中文停下，说去哪找')
    d = film(t, '竖屏', job={'人挡字风格': 1, 'format': '9:16'}, items=[it(0.5, 3.0)])
    _, err = run_rd(d); check(err and '只支持 3:4' in err, '片子不是 3:4：停下')
    d = film(t, '方屏', job={'人挡字风格': 1}, items=[it(0.5, 3.0)], size=(320, 320), src="color=c=gray:s=320x320:r=30000/1001")
    _, err = run_rd(d); check(err and '不是 3:4' in err, 'A肉 不是 3:4：停下')
    d = film(t, '重叠', job={'人挡字风格': 1}, items=[it(0.5, 3.0), dict(use='note', at=1.0, text='提醒')])
    _, err = run_rd(d); check(err and '同时出现' in err, '人挡字和别的花字同时出现：停下（花字轨只有一条）')
    # 没写风格：按画面自动选、写回 job.json、第二处沿用；手写的不被覆盖
    calls = []; rendang.MATTE = fake_matte(.4, calls=calls)
    d = film(t, '自动', job={'人挡字间隔秒': 1}, items=[it(0.5, 3.0), it(5.0, 7.5, text='工艺')])
    res, err = run_rd(d); rows = res[0] if res else None
    jb = rj(d / 'job.json')
    check(rows and jb.get('人挡字风格') in (1, 2, 3, 4) and jb.get('人挡字风格来源') == '自动' and jb['人挡字风格依据'].get('规则')
          and rows[0]['风格'] == rows[1]['风格'] == jb['人挡字风格'], f'没写风格：自动选了第 {jb.get("人挡字风格")} 套、写回 job.json（来源 自动、依据），第二处沿用同一套')
    d = film(t, '手写', job={'人挡字风格': 2}, items=[it(0.5, 3.0)])
    res, err = run_rd(d); rows = res[0] if res else None; jb = rj(d / 'job.json')
    check(rows and rows[0]['风格'] == 2 and jb['人挡字风格'] == 2 and '人挡字风格来源' not in jb, '手写了风格：照用，不自动改、不写来源')
    d2 = film(t, '单条', job={'人挡字风格': 2}, items=[it(0.5, 3.0, 风格='镜头对焦字')])
    check(run_rd(d2)[0][0][0]['风格'] == 3, '单条写 "风格" 覆盖 job.json')
    # 选法规则（假画面）：亮背景→超大出画面；暗而干净、头顶有空→苹果式；背景乱→镜头对焦字；头顶紧→超大出画面；亮且字多→杂志
    def pick(bright, top, busy=False, word='万兆'):
        n, h, w = 4, 96, 72; low = np.zeros((n, h, w), np.uint8); low[:, int(top * h):, 22:50] = 255
        g = np.full((n, h, w), bright, np.float32)
        if busy: g = g + np.where((np.indices((h, w)).sum(0) % 2) == 0, 40, -40)[None]
        a = rendang.analyse(low, 2160, 2880); return rendang.auto_pick(rendang.features(np.clip(g, 0, 255), low, a, 2160, 2880, word))[0]
    check(pick(200, .4) == 4 and pick(60, .4) == 1 and pick(60, .4, busy=True) == 3, '选法：亮背景→超大出画面（深色字）；暗而干净、头顶有空→苹果式静默大字；背景乱→镜头对焦字')
    check(pick(60, .15) == 4 and pick(200, .4, word='水母地漏') == 2 and pick(60, .15, word='水母地漏') == 2, '选法：头顶紧→超大出画面；亮或紧且 4 个字以上→杂志刊名（不选会切掉很多字的超大出画面）')
    # 审核线和自检：人挡字不通过、不在露脸段、A肉 重出过都拦；“花字”轨在导航和字幕下面；音效只认大字出场那几帧
    d = Path(t) / '套1'; wj(d / 'job.json', dict(rj(d / 'job.json'), frames=360, aroll='', source='', project='', main_timeline=''))
    J = Job(d); check(CK.rendang_problems(J) == [], '人挡字通过时自检没有问题')
    tb = rj(d / '合成/花字/花字表.json'); tb['items'][0]['遮挡'] = dict(tb['items'][0]['遮挡'], 通过=False, 建议='缩字'); wj(d / '合成/花字/花字表.json', tb)
    wj(d / '分镜.json', dict(anchors={}, shots=[dict(id='B9', kind='B', a=0, b=12.0)]))
    os.utime(d / '合成/A肉.mov', (time_now := __import__('time').time() + 100, time_now))
    pr = CK.rendang_problems(J)
    check(len(pr) == 3 and any('被挡' in x for x in pr) and any('B9' in x for x in pr) and any('A肉' in x for x in pr), '人挡字自检：遮挡不通过、分镜改成非露脸、A肉 重出过，三样都报（review 有就不建）')
    rsrc = (SK / 'scripts/thinkcut/resolve.py').read_text(encoding='utf-8')
    check(rsrc.index("vh = add('video', NAMES['flower'])") < rsrc.index("vn = add('video', NAMES['nav'])") < rsrc.index("vs = add('video', NAMES['subs'])")
          and 'rendang_problems(job)' in rsrc and RS2.flower_items(J)[0][1] == tb['items'][0]['start'], '审核线：花字轨（含人挡字）在导航、字幕下面；人挡字没通过不建；flower_items 照旧给出人挡字')
    csrc = (SK / 'scripts/thinkcut/check.py').read_text(encoding='utf-8')
    check("h['kind'] == '人挡字' and h['start'] - 4 <= r <= h['start'] + h['inn'] + 4" in csrc and "res['人挡字'] = rendang_problems(job)" in csrc, '自检：音效在人挡字上只认大字出场那几帧（整幅画面一直在动不算）；人挡字列为一项')
    check(not huazi.rules([dict(n=1, use='人挡字', bg=True, start=0, end=100), dict(n=2, use='number', start=150, end=200)]), '人挡字是整幅片段，不参加普通花字的 3 秒间隔提示')
rendang.MATTE = rendang.rvm
print('动画包装测试全部通过' if not fails else f'动画包装测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
