#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试（2026-11-21《261118 装修最后那点细节》反馈单 新3）：不开 Chrome、不启动 HyperFrames、不连达芬奇、不碰 NAS 片子。
地图硬拦：分镜规格的 画面/讲什么 写到地图、国界、省份等，出样张/出片前停下说审图号和编辑送审规则、建议距离圈；写了“不画…”的不拦；
  用户确认要地图（anim-confirm --kind 地图）后放行；编分镜（beats）时就先提醒。
距离圈组件（HyperFrames 模板 共用/距离圈.js，用 node 跑、页面是假的）：城市按真实大圆距离和方位摆，数值和 261118 B01/B03 一致；
  卡片不互压、刻度字不压卡片；城市不认识、没写 at 都报错。"""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; SK = ROOT / 'skill/thinkcut-edit'; S = SK / 'scripts/thinkcut_edit.py'
sys.path.insert(0, str(SK / 'scripts'))
from thinkcut.common import Job, wj, rj      # noqa: E402
from thinkcut import anim                    # noqa: E402
fails = []


def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)


# —— 哪些写法算“要用地图” ——
cases = [('中国地图上标出武汉、广东', ['地图']), ('别墅所在省份的地图', ['地图', '省份']), ('按省份依次亮起', ['省份']), ('不用距离圈，用标准地图标出武汉', ['地图']),
         ('标准地图不改色', ['地图']), ('不画地图。后半段出现省界线', ['省界']), ('画出国界和行政区', ['国界', '行政区']),
         # 261118 B01/B03 定稿的写法，和说明里建议的写法：都不算
         ('北京为圆心的距离圈（1000/2000/3000 km 虚线），城市按真实方位和直线距离摆放；不画任何地图边界', []), ('距离圈，不画国界、省界、海岸线', []),
         ('无国界、省界的示意', []), ('无边界图表', []), ('左半手机聊天，右半白模墙', [])]
got = [(t, anim.map_words({'画面': t})) for t, _ in cases]
check([g for _, g in got] == [w for _, w in cases] and anim.map_words({'讲什么': '地图', '画面': ''}) == ['地图'],
      '画面/讲什么 写到地图、国界、省界、省份、行政区算要用地图；同一个半句里写了“不画/不用/无…”的不算（261118 定稿的“不画任何地图边界”不算）'
      + ''.join(f'：{t} → {g}' for (t, g), (_, w) in zip(got, cases) if g != w))


def film(t, 画面):
    d = Path(t) / '片'; d.mkdir()
    wj(d / 'job.json', dict(fps='30000/1001', format='3:4满屏', nav_h=224, frames=600, aroll='', source='', project='', main_timeline='', srt=''))
    wj(d / '口播字时间.json', dict(tokens=[dict(c=c, t0=i * 0.5, t1=i * 0.5 + 0.5) for i, c in enumerate('甲乙丙丁戊己庚辛壬癸子丑')]))
    wj(d / '分镜规格.json', dict(anchors=dict(a=['乙'], b=['辛']), shots=[dict(id='B01', a='a', b='b', 工程='HF_试', 讲什么='场景', 口播要点=['做过两个外地的灯光设计'], 画面=画面)]))
    wj(d / '分镜.json', dict(anchors=dict(a=0.5, b=3.5), shots=[dict(id='B01', kind='B', a=0.5, b=3.5)]))
    return d


def cli(*a): r = subprocess.run([sys.executable, '-B', str(S), *map(str, a)], capture_output=True, text=True, stdin=subprocess.DEVNULL); return r.returncode, r.stdout + r.stderr


# —— 渲染前硬拦、用户确认后放行、beats 提醒 ——
with tempfile.TemporaryDirectory() as t:
    d = film(t, '深色底上一张中国地图，标出北京、武汉、广东，弧线连起来'); job = Job(d); anim.hf_new(job, '试'); proj = d / 'HF_试'
    check((proj / '共用' / '距离圈.js').exists() and '距离圈.js' in [f.name for f in anim._code_files(proj, {'B01'})], 'hf-new 把距离圈组件带进工程，算进样张确认的指纹（改了组件要重新确认）')
    bad = anim.gate(job, proj, ['B01'], False)
    try: anim.require(job, proj, ['B01'], False); stopped = ''
    except SystemExit as e: stopped = str(e)
    check(len(bad) == 1 and 'B01' in bad[0] and '“地图”' in bad[0] and '审图号' in bad[0] and '送审' in bad[0] and '距离圈' in bad[0] and 'anim-confirm' in bad[0] and '--kind 地图' in bad[0]
          and '还不能渲染' in stopped, '画面写到地图：出样张前就停下，说明审图号和编辑送审规则，建议改用距离圈，并给出用户确认后怎么记')
    rc, out = cli('beats', d)
    check(rc == 0 and '【注意】镜头 B01 的画面写到“地图”' in out and '审图号' in out and '距离圈' in out, 'beats 编分镜时就提醒（不等画完出样张才知道）' + ('' if rc == 0 else '：' + out[-300:]))
    rc, out = cli('anim-confirm', d, '--kind', '地图', '--project', proj, '--shots', 'B01', '--words', '就用地图，原图照用')
    rec = rj(d / '合成' / '确认记录.json')['items'] if (d / '合成' / '确认记录.json').exists() else []
    check(rc == 0 and len(rec) == 1 and rec[0]['kind'] == '地图' and rec[0]['words'] == '就用地图，原图照用' and anim.gate(job, proj, ['B01'], False) == [],
          '用户确认要地图后记一条（用户原话，不用带图），出样张放行' + ('' if rc == 0 else '：' + out[-300:]))
    rc, out = cli('beats', d); check(rc == 0 and '写到“地图”' not in out, '确认过的镜头 beats 不再提醒')

with tempfile.TemporaryDirectory() as t:
    d = film(t, '北京为圆心的距离圈，城市按真实方位和直线距离摆放；不画任何地图边界'); job = Job(d); anim.hf_new(job, '试'); proj = d / 'HF_试'
    rc, out = cli('beats', d)
    check(anim.gate(job, proj, ['B01'], False) == [] and rc == 0 and '写到“' not in out, '画面写距离圈、“不画任何地图边界”：不拦、不提醒')
    rc, out = cli('anim-confirm', d, '--kind', '地图', '--project', proj, '--shots', 'B01', '--words', '就用地图')
    check(rc != 0 and '不用记地图确认' in out and not (d / '合成' / '确认记录.json').exists(), '镜头没写到地图却记地图确认：拦下不记')

# —— 距离圈组件（node 跑，假页面）——
node_js = r'''
const fs = require("fs");
const el = (tag) => ({ tag, style: {}, attrs: {}, children: [], className: "", innerHTML: "", classList: { add() {} },
  appendChild(c) { this.children.push(c); return c; }, setAttribute(k, v) { this.attrs[k] = v; } });
global.window = { THINKCUT: { shot: "B01", a: 2, b: 12, dur: 10, fps: 29.97, W: 1080, H: 1440, k: 1, card: [0, 112, 1080, 1328], anchors: { wuhan: 5 } } };
const byId = {}; global.document = { getElementById: (id) => byId[id] || (byId[id] = el("div")), createElement: el, createElementNS: (ns, t) => el(t), head: el("head") };
eval(fs.readFileSync(process.argv[1], "utf8")); eval(fs.readFileSync(process.argv[2], "utf8")); const D = window.THINKCUT, out = {};
const mk = () => { const ops = []; return { ops, fromTo(t, a, b, at) { ops.push(at); }, to(t, a, at) { ops.push(at); }, set() {} }; };
const geo = (a, b) => { const g = D.geo(a, b); return [Math.round(g.km), +g.bearing.toFixed(1)]; };
out.geo = { 武汉: geo("北京", "武汉"), 广东: geo("北京", "广东"), 海南: geo("北京", "海南"), 甘肃: geo("北京", "甘肃"), 自写: geo("北京", { name: "某地", lat: 30.59, lon: 114.31 }) };
// 261118 B01 的摆法：北京在 (600,140)，0.45 设计稿像素/公里
let tl = mk(); const R1 = D.distanceRing(tl, { center: "北京", origin: [600, 140], scale: 0.45, cities: [
  { name: "武汉", title: "武汉 · 灯光设计", at: D.at("wuhan") - 2 }, { name: "广东", at: 4.5, note: "" }] });
R1.pulse("武汉", 7);
out.b01 = { pos: R1.cities.map((c) => [c.name, Math.round(c.x), Math.round(c.y), c.side]), rings: R1.rings.map((r) => r.km), tags: R1.cities.map((c) => c.tag.innerHTML),
  kmLabels: R1.rings.map((r) => r.l ? [parseFloat(r.l.style.left), parseFloat(r.l.style.top)] : null), boxes: R1.cities.map((c) => c.box), ops: tl.ops.length, opsOk: tl.ops.every((x) => isFinite(x) && x >= 0) };
// 自动摆放（261118 B03 的四个城市）
tl = mk(); const R2 = D.distanceRing(tl, { center: "北京", cities: ["武汉", "广东", "海南", "甘肃"].map((n, i) => ({ name: n, at: 1 + i })) });
out.b03 = { K: R2.K, rings: R2.rings.map((r) => r.km), pos: R2.cities.map((c) => [c.name, Math.round(c.x), Math.round(c.y), c.side]), boxes: R2.cities.map((c) => c.box),
  kmLabels: R2.rings.map((r) => r.l ? [parseFloat(r.l.style.left), parseFloat(r.l.style.top)] : null), W: D.designW, H: D.designH };
const err = (f) => { try { f(); return ""; } catch (e) { return e.message; } };
out.err = { 城市: err(() => D.distanceRing(mk(), { center: "北京", cities: [{ name: "火星", at: 1 }] })), at: err(() => D.distanceRing(mk(), { center: "北京", cities: [{ name: "武汉" }] })),
  空: err(() => D.distanceRing(mk(), { center: "北京", cities: [] })), side: err(() => D.distanceRing(mk(), { center: "北京", cities: [{ name: "武汉", at: 1, side: "上" }] })) };
const css = document.head.children.map((c) => c.textContent).join("");
out.css = css.includes(".dr-arc") && css.includes("var(--blue)");
console.log(JSON.stringify(out));'''
T = SK / 'assets/hyperframes_template/共用'
r = subprocess.run(['node', '-e', node_js, str(T / '基础.js'), str(T / '距离圈.js')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
try: jr = json.loads(r.stdout)
except ValueError: jr = {}
if not jr: check(False, '距离圈.js 在 node 里跑不起来：' + (r.stdout + r.stderr)[-600:])
else:
    g = jr['geo']
    check(g['武汉'] == [1052, 191.0] and g['广东'] == [1888, 189.9] and g['海南'] == [2286, 196.8] and g['甘肃'] == [1180, 252.8] and g['自写'] == g['武汉'],
          f'大圆距离和方位角和 261118 B01/B03 一致（武汉 1052 km/191.0°、广州 1888/189.9°、海口 2286/196.8°、兰州 1180/252.8°），省名按省会、也能自己写经纬度：{g}')
    b = jr['b01']
    import math
    want = [[n, 600 + km * .45 * math.sin(math.radians(br)), 140 - km * .45 * math.cos(math.radians(br))] for n, (km, br) in (('武汉', g['武汉']), ('广东', g['广东']))]
    check(all(p[0] == w[0] and abs(p[1] - w[1]) <= 2 and abs(p[2] - w[2]) <= 2 and p[3] == 'right' for p, w in zip(b['pos'], want)) and b['rings'] == [1000, 2000] and '武汉 · 灯光设计' in b['tags'][0] and '直线约 1050 km' in b['tags'][0]
          and 'class="s"' not in b['tags'][1] and b['ops'] > 10 and b['opsOk'], f"按 B01 的圆心和比例：落点和 B01 手算的一样、两圈距离圈、默认小字“直线约 N km”（note 写空就不要）：{b['pos']}")

    def hit(a, b_, m=0): return a[0] < b_[2] + m and b_[0] < a[2] + m and a[1] < b_[3] + m and b_[1] < a[3] + m
    def clean(x):      # 卡片两两不压；刻度字（按 120×40 算）不压卡片
        boxes = x['boxes']; labs = [[p[0] - 60, p[1] - 20, p[0] + 60, p[1] + 20] for p in x['kmLabels'] if p]
        return not any(hit(boxes[i], boxes[j]) for i in range(len(boxes)) for j in range(i)) and not any(hit(l, bx) for l in labs for bx in boxes)
    c = jr['b03']; W, H = c['W'], c['H']
    check(clean(b) and clean(c) and all(p for p in b['kmLabels'] + c['kmLabels']), '标签卡互相不压、每圈的公里数刻度字都在且不压标签卡（B01 两城、B03 四城自动摆放）')
    check(c['rings'] == [1000, 2000, 3000] and all(0 <= bx[0] and bx[2] <= W and 0 <= bx[1] and bx[3] <= H - 200 for bx in c['boxes']) and all(60 <= p[1] <= W - 60 and 60 <= p[2] <= H - 300 for p in c['pos'])
          and dict((p[0], p[3]) for p in c['pos'])['甘肃'] == 'below', f"自动摆放：四个城市和标签卡都在画面区里、底部给字幕留空，三圈距离圈；正西的甘肃卡片放落点下方（不压从北京出来的弧线）：{c['pos']}")
    e = jr['err']
    check('城市表里没有「火星」' in e['城市'] and 'lat' in e['城市'] and '要写 at' in e['at'] and '至少写一个城市' in e['空'] and 'side 只能写' in e['side'] and jr['css'],
          '城市不在表里（提示自己写经纬度）、没写 at、没写城市、side 写错都报错说清楚，不悄悄画错')
src = (T / '距离圈.js').read_text(encoding='utf-8')
check(not any(w in src for w in ('<path d="M0', 'geojson', 'topojson', '.svg"')) and '不画国界' in src, '距离圈组件里没有任何地图轮廓数据（只有圆、弧线、点、卡片）')

print('地图硬拦/距离圈测试全部通过' if not fails else f'地图硬拦/距离圈测试有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
