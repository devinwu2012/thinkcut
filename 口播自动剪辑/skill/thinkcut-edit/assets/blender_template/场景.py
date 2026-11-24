# 本期场景（每期重写这个文件；其余文件原样用）。在 Blender 里运行（worker.py 调用），不要在外面 import。
# 约定（worker.py 只认这几样）：
#   Scene(task)            按 布局.json 建模、设好渲染（分辨率由 task['resolution'] 给，别自己写死）
#   evaluate(shot, t)      把场景摆到成片第 t 秒（相机、动画、显隐都由 t 算出来，同一个 t 每次一样）
#   points(shot, t)        标签要指的目标点、钉字的世界坐标点：{名字: (x, y, z)}，标签.json 的 point / fixed.world 用这些名字
#   protected(shot, t)     字不许压的重点物体名（它们在画面上的方框整块避开）
#   events()               画面事件 [{shot, t(成片秒), name}]（音效节拍只读这张表）
# 下面是示例：一个 2.6×2.2 米卫生间，暗场白模，相机从全景推到淋浴间上方的换气扇。
import json, math
from pathlib import Path
import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
def srgb_to_linear(hexs):
    """#RRGGBB 显示值 → Blender 要的线性值（标准 sRGB 公式）。世界/材质颜色直接写显示值，Blender 当线性值，渲出来会偏灰。"""
    h = hexs.lstrip('#'); out = []
    for k in (0, 2, 4):
        c = int(h[k:k + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


BG = srgb_to_linear('#16181D')      # 暗场底色（动画风格.md），写进 Blender 前已换成线性值
WHITE = (0.92, 0.92, 0.92, 1)


def smooth(u): u = max(0.0, min(1.0, u)); return u * u * (3 - 2 * u)


class Scene:
    def __init__(self, task):
        self.task = task; self.plan = json.loads((HERE / '布局.json').read_text(encoding='utf-8')); self.shots = task['shots']
        bpy.ops.wm.read_factory_settings(use_empty=True); sc = self.scene = bpy.context.scene
        for eng in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
            try: sc.render.engine = eng; break
            except TypeError: pass
        sc.render.resolution_x, sc.render.resolution_y = task['resolution']; sc.render.resolution_percentage = 100
        sc.render.image_settings.file_format = 'PNG'; sc.render.film_transparent = False; sc.view_settings.view_transform = 'Standard'
        if hasattr(sc, 'eevee'): sc.eevee.taa_render_samples = task.get('samples', 32)
        w = bpy.data.worlds.new('暗场'); w.use_nodes = True; w.node_tree.nodes['Background'].inputs[0].default_value = (*BG, 1); sc.world = w
        mat = bpy.data.materials.new('白模'); mat.use_nodes = True
        bsdf = mat.node_tree.nodes['Principled BSDF']; bsdf.inputs['Base Color'].default_value = WHITE; bsdf.inputs['Roughness'].default_value = 0.8
        self.mat = mat; t = self.plan['wall_thickness']
        self.box('地面', (1.3, 1.1, -0.01), (2.6, 2.2, 0.02))
        for wl in self.plan['walls']:
            (ax, ay), (bx, by) = wl['a'], wl['b']
            if wl['id'] == 's': continue                                  # 剖开朝相机的那面墙
            self.box('墙_' + wl['id'], ((ax + bx) / 2, (ay + by) / 2, 0.6), (abs(bx - ax) or t, abs(by - ay) or t, 1.2))
        for it in self.plan['items']:
            w_, d_, h_ = it['size']
            if abs(it.get('rotation', 0)) % 180 == 90: w_, d_ = d_, w_
            self.box(it['id'], (*it['center'], h_ / 2), (w_, d_, h_))
        sh = self.plan['showers'][0]; r = sh['rect']
        self.box('淋浴间', ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, 1.0), (r[2] - r[0], r[3] - r[1], 2.0), alpha=0.25)
        self.fan = self.box('换气扇', (sh['head'][0], (r[1] + r[3]) / 2, 2.3), (0.3, 0.3, 0.12))
        key = bpy.data.lights.new('主光', 'AREA'); key.energy = 250; key.size = 3
        lo = bpy.data.objects.new('主光', key); lo.location = (1.3, -1.5, 3.5); lo.rotation_euler = (math.radians(40), 0, 0); sc.collection.objects.link(lo)
        cam = bpy.data.cameras.new('相机'); cam.lens = 30; self.cam = bpy.data.objects.new('相机', cam); sc.collection.objects.link(self.cam); sc.camera = self.cam

    def box(self, name, loc, size, alpha=1.0):
        bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object; o.name = name; o.scale = size
        o.data.materials.append(self.mat); return o

    def look(self, pos, target):
        self.cam.location = pos; d = Vector(target) - Vector(pos); self.cam.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()

    def evaluate(self, shot, t):
        a, b = self.shots[shot]['a'], self.shots[shot]['b']; u = smooth((t - a - 2.0) / 0.7)   # 第 2 秒起 0.7 秒推近（动画风格：推拉约 0.7 秒）
        wide, near = ((1.3, -2.6, 2.6), (1.3, 1.0, 1.0)), ((1.5, -1.0, 2.2), (1.7, 1.7, 1.5))
        self.look(tuple(w + (n - w) * u for w, n in zip(wide[0], near[0])), tuple(w + (n - w) * u for w, n in zip(wide[1], near[1])))

    def points(self, shot, t):
        sh = self.plan['showers'][0]; r = sh['rect']; toilet = next(i for i in self.plan['items'] if i['kind'] == '马桶')
        return {'toilet': (*toilet['center'], 0.8), 'fan': (sh['head'][0], (r[1] + r[3]) / 2, 2.24), 'fan_text': (0.9, 2.13, 0.9)}   # fan_text：字钉在北墙上这块墙面正中（随镜头走）

    def protected(self, shot, t): return ['马桶', '换气扇']

    def events(self):
        return [dict(shot=s, t=round(v['a'] + 2.0, 3), name='推近换气扇') for s, v in self.shots.items()]
