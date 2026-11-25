#!/usr/bin/env python3
# 把 three.js 和 src/*.js 拼成一个能双击打开的网页（不需要服务器、不需要联网）
import glob, os, re
D = os.path.dirname(os.path.abspath(__file__))
three = open(f'{D}/vendor/three.module.min.js', encoding='utf-8').read()
room = open(f'{D}/vendor/RoomEnvironment.js', encoding='utf-8').read()
room = re.sub(r'import\s*\{[^}]*\}\s*from\s*[\'"]three[\'"];?', '', room)
room = re.sub(r'\b(BackSide|BoxGeometry|Mesh|MeshBasicMaterial|MeshStandardMaterial|PointLight|Scene)\b(?!:)', r'THREE.\1', room)
room = room.replace('export { RoomEnvironment };', '').replace('THREE.THREE.', 'THREE.')
room = re.sub(r'class RoomEnvironment extends THREE\.Scene', 'class RoomEnvironment extends THREE.Scene', room)
tl = open(f'{D}/timeline.js', encoding='utf-8').read() if os.path.exists(f'{D}/timeline.js') else ''
# 文字排版结果（tools/layout.mjs 生成；改了节奏要重新生成）
if os.environ.get('NOLAYOUT') != '1':
    L = {f: open(f'{D}/layout_{f}.json', encoding='utf-8').read() for f in ('p', 'pb', 'v', 'vb', 'h', 'hb') if os.path.exists(f'{D}/layout_{f}.json')}
    tl += '\nwindow.LAYOUTS = {' + ','.join(f'{k}: {v}' for k, v in L.items()) + '};\n'
BEATS = next((p for p in (f'{D}/分镜.json', f'{D}/../分镜.json') if os.path.exists(p)), None)   # 片子目录里的 分镜.json（thinkcut-edit beats 生成）
if BEATS:
    tl += '\nwindow.BEATS_DATA = ' + open(BEATS, encoding='utf-8').read() + ';\n'
src = '\n'.join(open(p, encoding='utf-8').read() for p in sorted(glob.glob(f'{D}/src/*.js')))
shell = open(f'{D}/shell.html', encoding='utf-8').read()
out = shell.replace('/*TIMELINE*/', tl).replace('/*THREE*/', three).replace('/*APP*/', room + '\n' + src)
dst = f'{D}/动画.html'
open(dst, 'w', encoding='utf-8').write(out)
print(dst, f'{len(out)/1024:.0f} KB')
