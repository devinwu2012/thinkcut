# Blender 里跑的出帧进程（出片.py 开它，一次一份连续帧）。每帧：场景摆到时刻 t → 投影目标点和重点物体方框 → 渲 PNG（先写 .part 再改名，断了能续）。
# 用法：blender --background --factory-startup --python-exit-code 1 --python worker.py -- <任务.json>
import json, sys, time
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
import 场景

task = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
W, H = task['resolution']; fps = task['fps_num'] / task['fps_den']; S = 场景.Scene(task); sc = S.scene; cam = sc.camera


def proj(p):
    v = world_to_camera_view(sc, cam, Vector(p)); return dict(x=round(v.x * W, 2), y=round((1 - v.y) * H, 2), in_frame=bool(0 <= v.x <= 1 and 0 <= v.y <= 1 and v.z > 0))


def box(name):
    o = bpy.data.objects.get(name)
    if not o or o.hide_render: return None
    vs = [world_to_camera_view(sc, cam, o.matrix_world @ Vector(c)) for c in o.bound_box]; vs = [v for v in vs if v.z > 0]
    if not vs: return None
    return [round(min(v.x for v in vs) * W, 1), round((1 - max(v.y for v in vs)) * H, 1), round(max(v.x for v in vs) * W, 1), round((1 - min(v.y for v in vs)) * H, 1)]


raw = Path(task['raw']); raw.mkdir(parents=True, exist_ok=True); recs = []; t0 = time.time()
for i in task['indices']:
    t = max((task['frame0'] + i) / fps, task['shots'][task['shot']]['a'] + 1e-4)   # 第 1 帧按帧取整后可能比镜头起点早几毫秒，按起点画（否则首帧画成上一个镜头）
    S.evaluate(task['shot'], t); bpy.context.view_layer.update()
    recs.append(dict(i=i, t=round(t, 5), points={k: proj(v) for k, v in S.points(task['shot'], t).items()},
                     protected=[b for b in (box(n) for n in S.protected(task['shot'], t)) if b]))
    out = raw / f'{i:06d}.png'
    if out.exists() and out.stat().st_size > 0: continue
    part = raw / f'{i:06d}.part.png'; sc.render.filepath = str(part); bpy.ops.render.render(write_still=True); part.replace(out)
    print('FRAME', task['shot'], i, round(time.time() - t0, 1), flush=True)
Path(task['records']).write_text(json.dumps(dict(shot=task['shot'], size=[W, H], frames=recs, events=S.events()), ensure_ascii=False), encoding='utf-8')
print('DONE', len(recs), flush=True)
