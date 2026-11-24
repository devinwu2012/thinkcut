#!/usr/bin/env python3
"""Blender 场景布局常识硬检查（2026-11-12 用户定，水电反馈新6）：不过就不许渲染。
查：门开向不互撞、门口不被家具/洁具挡、洁具相邻关系（同一面墙上的洁具之间不留大空、马桶前面留空）、
花洒在淋浴间一个方向的中线上、床和沙发靠墙；另外查东西出房间、互相重叠、穿墙。
布局数据只写在 布局.json（俯视，单位米，x 向右、y 向上）；场景.py 建模也只读它，两边不会对不上。
用法：python3 布局检查.py [布局.json]   → 打印问题，写 自查/布局检查.json 和 自查/布局俯视图.png；有问题退出码 1。"""
import json, math, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACK_TO_WALL = ('床', '沙发', '马桶', '洗手台', '浴室柜', '电视柜', '衣柜', '书柜', '鞋柜')   # 背面必须贴墙的
SANITARY = ('马桶', '洗手台', '浴室柜', '淋浴', '浴缸', '洗衣机')                           # 洁具
EPS = 0.02


def rot_dir(rot):
    """背面朝向：rotation=0 背朝 +y（北），逆时针转。"""
    a = math.radians(rot + 90); return round(math.cos(a)), round(math.sin(a))


def rect(it):
    x, y = it['center']; w, d = it['size'][:2]
    if abs(it.get('rotation', 0)) % 180 == 90: w, d = d, w
    return [x - w / 2, y - d / 2, x + w / 2, y + d / 2]


def ov(a, b, e=EPS): return min(a[2], b[2]) - max(a[0], b[0]) > e and min(a[3], b[3]) - max(a[1], b[1]) > e


def gap(a, b):
    dx = max(b[0] - a[2], a[0] - b[2], 0); dy = max(b[1] - a[3], a[1] - b[3], 0); return math.hypot(dx, dy)


def inside(p, r, m=0): return r[0] - m <= p[0] <= r[2] + m and r[1] - m <= p[1] <= r[3] + m


def wall_band(w, t):
    (ax, ay), (bx, by) = w['a'], w['b']
    return [min(ax, bx) - (t / 2 if ax == bx else 0), min(ay, by) - (t / 2 if ay == by else 0),
            max(ax, bx) + (t / 2 if ax == bx else 0), max(ay, by) + (t / 2 if ay == by else 0)]


def door_cut(d, walls):
    """门洞在墙上占的那一段（从合页沿墙方向开 width）；淋浴房玻璃门这类不在墙上的，直接写 line 两个端点。"""
    if 'line' in d: return tuple(d['line'][0]), tuple(d['line'][1])
    w = walls[d['wall']]; (ax, ay), (bx, by) = w['a'], w['b']; L = math.hypot(bx - ax, by - ay); ux, uy = (bx - ax) / L, (by - ay) / L
    s = d.get('side', 1); hx, hy = d['hinge']; return (hx, hy), (hx + ux * d['width'] * s, hy + uy * d['width'] * s)


def sweep(d):
    """平开门扫过的扇形，取一圈点（含边界和内部）。closed/opened 是门扇从合页指出去的角度（度）。"""
    if d.get('kind', 'swing') != 'swing': return []
    a0 = d['closed']; da = (d['opened'] - a0 + 180) % 360 - 180; hx, hy = d['hinge']; pts = []
    for i in range(41):
        a = math.radians(a0 + da * i / 40)
        for k in range(1, 13): r = d['width'] * k / 12; pts.append((hx + r * math.cos(a), hy + r * math.sin(a)))
    return pts


def in_sector(p, d):
    if d.get('kind', 'swing') != 'swing': return False
    hx, hy = d['hinge']; r = math.hypot(p[0] - hx, p[1] - hy)
    if r > d['width'] - EPS or r < EPS: return False
    a0 = d['closed']; da = (d['opened'] - a0 + 180) % 360 - 180; ang = math.degrees(math.atan2(p[1] - hy, p[0] - hx))
    rel = (ang - a0) % 360 if da > 0 else (a0 - ang) % 360
    return EPS < rel < abs(da) - EPS * 10


def front_zones(d, walls, depth=0.6):
    """门口两侧要留的通行区：门洞宽 × 0.6 米（门槛内外各一块）。"""
    (x0, y0), (x1, y1) = door_cut(d, walls)
    if abs(y0 - y1) < 1e-6: lo, hi = sorted([x0, x1]); return [[lo, y0 - depth, hi, y0 - EPS], [lo, y0 + EPS, hi, y0 + depth]]
    lo, hi = sorted([y0, y1]); return [[x0 - depth, lo, x0 - EPS, hi], [x0 + EPS, lo, x0 + depth, hi]]


def check(p):
    err, note = [], []; t = p.get('wall_thickness', 0.12); walls = {w['id']: w for w in p['walls']}; rooms = {r['id']: r for r in p.get('rooms', [])}
    items = list(p.get('items', [])); showers = p.get('showers', []); doors = p.get('doors', [])
    obst = [(it['id'], rect(it), it) for it in items] + [(s['id'], s['rect'], dict(s, kind='淋浴')) for s in showers]
    # 0 基本：出房间、互相重叠、穿墙（门洞那段不算墙）
    for name, r, it in obst:
        if it.get('room') in rooms and not (inside(r[:2], rooms[it['room']]['rect'], EPS) and inside(r[2:], rooms[it['room']]['rect'], EPS)):
            err.append(f'{name} 出了房间 {it["room"]}')
        for w in p['walls']:
            band = wall_band(w, t)
            if ov(r, band):
                cuts = [door_cut(d, walls) for d in doors if d.get('wall') == w['id']]
                if not any(inside(((r[0] + r[2]) / 2, (r[1] + r[3]) / 2), [min(a[0], b[0]) - t, min(a[1], b[1]) - t, max(a[0], b[0]) + t, max(a[1], b[1]) + t]) for a, b in cuts):
                    err.append(f'{name} 穿墙 {w["id"]}')
    for i, (n1, r1, _) in enumerate(obst):
        for n2, r2, _ in obst[i + 1:]:
            if ov(r1, r2): err.append(f'{n1} 和 {n2} 重叠')
    # 1 门开向不互撞：两扇门扫过的范围有交叠
    for i, d1 in enumerate(doors):
        for d2 in doors[i + 1:]:
            if any(in_sector(q, d2) for q in sweep(d1)) or any(in_sector(q, d1) for q in sweep(d2)):
                err.append(f'门 {d1["id"]} 和门 {d2["id"]} 开的时候互撞')
    # 2 门口不被挡：门扇扫过的地方和门洞两侧 0.6 米通行区里不能有家具/洁具（淋浴房自己的门不算挡自己）
    for d in doors:
        zones = front_zones(d, walls, d.get('clear', 0.6))
        for name, r, it in obst:
            if it.get('door') == d['id'] or name in d.get('through', []): continue
            if any(inside(q, r, -EPS) for q in sweep(d)): err.append(f'门 {d["id"]} 开门会撞到 {name}')
            elif any(ov(r, z) for z in zones): err.append(f'{name} 挡住了门 {d["id"]} 的门口')
    # 3 洁具相邻：同一面墙上相邻的洁具之间不留大空（默认 ≤ 0.30 米，中间放了柜子就不算空）；马桶前面留 ≥ 0.5 米
    maxgap = p.get('sanitary_max_gap', 0.30)
    by_wall = {}
    for name, r, it in obst:
        if it.get('kind') in SANITARY and it.get('back_wall'): by_wall.setdefault(it['back_wall'], []).append((name, r, it))
    for wid, lst in by_wall.items():
        w = walls[wid]; horiz = abs(w['a'][1] - w['b'][1]) < 1e-6; key = (lambda e: e[1][0]) if horiz else (lambda e: e[1][1]); lst.sort(key=key)
        for (n1, r1, i1), (n2, r2, i2) in zip(lst, lst[1:]):
            g = (r2[0] - r1[2]) if horiz else (r2[1] - r1[3]); filler = [n for n, r, it in obst if n not in (n1, n2) and gap(r, r1) < EPS * 3 and gap(r, r2) < EPS * 3]
            ok = i1.get('gap_ok') or i2.get('gap_ok')
            if g > maxgap and not filler and not ok: err.append(f'{n1} 和 {n2} 之间空了 {g:.2f} 米（同一面墙上的洁具应挨着，超过 {maxgap} 米要写 gap_ok 说明理由）')
    for a, b, mg in p.get('adjacent', []):
        ra = next(r for n, r, _ in obst if n == a); rb = next(r for n, r, _ in obst if n == b)
        if gap(ra, rb) > mg: err.append(f'{a} 和 {b} 应该挨着（≤ {mg} 米），现在隔了 {gap(ra, rb):.2f} 米')
    for name, r, it in obst:
        if it.get('kind') == '马桶':
            bx, by = rot_dir(it.get('rotation', 0)); fz = {(0, 1): [r[0], r[1] - 0.5, r[2], r[1]], (0, -1): [r[0], r[3], r[2], r[3] + 0.5],
                                                           (1, 0): [r[0] - 0.5, r[1], r[0], r[3]], (-1, 0): [r[2], r[1], r[2] + 0.5, r[3]]}[(bx, by)]
            for n2, r2, _ in obst:
                if n2 != name and ov(fz, r2): err.append(f'马桶 {name} 前面 0.5 米内有 {n2}，坐不下')
    # 4 花洒在淋浴间一个方向的中线上，并且挂在淋浴间的一条边（墙）上
    for s in showers:
        if 'head' not in s: err.append(f'淋浴间 {s["id"]} 没写花洒位置 head'); continue
        r = s['rect']; hx, hy = s['head']; cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2; tol = s.get('tol', 0.05)
        on_edge = min(abs(hx - r[0]), abs(hx - r[2]), abs(hy - r[1]), abs(hy - r[3])) <= tol and inside((hx, hy), r, tol)
        if not ((abs(hx - cx) <= tol or abs(hy - cy) <= tol) and on_edge):
            err.append(f'花洒不在淋浴间 {s["id"]} 一个方向的中线上（花洒 {hx:.2f},{hy:.2f}，中心 {cx:.2f},{cy:.2f}）')
    # 5 床、沙发（及柜子、马桶、洗手台）背面靠墙
    for name, r, it in obst:
        if it.get('kind') not in BACK_TO_WALL or it.get('free_standing'): continue
        bx, by = rot_dir(it.get('rotation', 0)); edge = {(0, 1): ('y', r[3]), (0, -1): ('y', r[1]), (1, 0): ('x', r[2]), (-1, 0): ('x', r[0])}[(bx, by)]
        ok = False
        for w in p['walls']:
            horiz = abs(w['a'][1] - w['b'][1]) < 1e-6
            if (edge[0] == 'y') != horiz: continue
            pos = w['a'][1] if horiz else w['a'][0]; span = sorted([w['a'][0], w['b'][0]] if horiz else [w['a'][1], w['b'][1]])
            mid = (r[0] + r[2]) / 2 if horiz else (r[1] + r[3]) / 2
            if abs(abs(edge[1] - pos) - t / 2) <= 0.05 and span[0] - EPS <= mid <= span[1] + EPS: ok = True
        if not ok: err.append(f'{it["kind"]} {name} 背面没靠墙（背朝 {"北南东西"[[(0, 1), (0, -1), (1, 0), (-1, 0)].index((bx, by))]}）')
    return dict(errors=err, notes=note)


def draw(p, out):
    """俯视图（给用户看布局、出逻辑草图时也能用）。"""
    from PIL import Image, ImageDraw
    xs = [c for w in p['walls'] for c in (w['a'][0], w['b'][0])]; ys = [c for w in p['walls'] for c in (w['a'][1], w['b'][1])]
    S = 200; L, T = 80, 80; W = int((max(xs) - min(xs)) * S) + 2 * L; H = int((max(ys) - min(ys)) * S) + 2 * T
    xy = lambda q: (L + (q[0] - min(xs)) * S, T + (max(ys) - q[1]) * S)
    im = Image.new('RGB', (W, H), 'white'); d = ImageDraw.Draw(im); t = p.get('wall_thickness', 0.12)
    for w in p['walls']: d.line([xy(w['a']), xy(w['b'])], fill='#273644', width=max(2, int(t * S)))
    for s in p.get('showers', []):
        r = s['rect']; d.rectangle([*xy((r[0], r[3])), *xy((r[2], r[1]))], outline='#40A4C9', width=3)
        if 'head' in s: c = xy(s['head']); d.ellipse((c[0] - 8, c[1] - 8, c[0] + 8, c[1] + 8), fill='#2F7BFF')
    for it in p.get('items', []):
        r = rect(it); d.rectangle([*xy((r[0], r[3])), *xy((r[2], r[1]))], fill='#E7DED2', outline='#867661', width=2)
        d.text(xy(it['center']), it['kind'], fill='#453C32', anchor='mm')
    for dr in p.get('doors', []):
        pts = [xy(q) for q in sweep(dr)[11::12]]
        if pts: d.line([xy(dr['hinge'])] + pts + [xy(dr['hinge'])], fill='#829183', width=2)
    out.parent.mkdir(parents=True, exist_ok=True); im.save(out)


def main(path=None):
    path = Path(path or HERE / '布局.json'); p = json.loads(path.read_text(encoding='utf-8')); r = check(p)
    out = path.parent / '自查'; out.mkdir(exist_ok=True)
    (out / '布局检查.json').write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding='utf-8')
    try: draw(p, out / '布局俯视图.png')
    except Exception as e: print('俯视图没画出来：', e)
    print('布局检查：' + ('通过' if not r['errors'] else f'{len(r["errors"])} 个问题'))
    for e in r['errors']: print('  ✗', e)
    return r


if __name__ == '__main__':
    sys.exit(1 if main(sys.argv[1] if len(sys.argv) > 1 else None)['errors'] else 0)
