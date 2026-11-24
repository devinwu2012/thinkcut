"""Direct Resolve SDK writes, guarded by project identity; no MCP wrappers."""
from pathlib import Path
import time
from .connect import find_timeline
from .ledger import equal
from .plan import config, color_table
from .snapshot import frame_timecode, safe, snapshot

SETTINGS = ('timelineFrameRate', 'timelineResolutionWidth', 'timelineResolutionHeight',
            'timelineDropFrameTimecode')
REOPEN_TRIES = 4           # 保存关闭后重开工程：第一次没开成再试 3 次
REOPEN_WAIT_SECONDS = 2    # 每次之间等一小会儿（达芬奇刚关完工程，可能还没缓过来）


def objects(timeline, kind=None):
    for typ in ((kind,) if kind else ('video', 'audio', 'subtitle')):
        for track in range(1, timeline.GetTrackCount(typ)+1):
            for item in timeline.GetItemListInTrack(typ, track) or []:
                yield item


def folder_clips(folder):
    yield from folder.GetClipList() or []
    for sub in folder.GetSubFolderList() or []:
        yield from folder_clips(sub)


def motion_keys(effect, values, duration, fps, cfg):
    """包装系统统一节奏（2026-11-12 用户看对比片后定，取代 10-16 的“默认静态”）：
    0.40 秒缓出淡入＋上移，0.30 秒缓入淡出；只做透明度和位移，不做模糊。
    返回 {输入名: {相对帧: 关键帧}}，'LineOffset.Y' 是行偏移的纵向（单位＝画宽，字、投影、底框一起动）。
    样式写 static=true（用户明确要静态）时不加；连续状态清单由 timing 写 motion.in/out 只在首尾淡。"""
    m = cfg.get('motion')
    if not m or effect['style'].get('static'):
        return {}
    flags = effect.get('motion') or {}
    fade_in, fade_out = flags.get('in', True), flags.get('out', True)
    inn, out = round(m['in_seconds']*fps), round(m['out_seconds']*fps)
    if inn+out > duration:   # 片段太短：按比例缩短，保证首尾都在片段里
        inn = max(1, round(duration*inn/(inn+out))); out = max(1, duration-inn)
    result = {}
    for n in (1, 2, 3):
        if not values.get(f'Enabled{n}'):
            continue
        v = values.get(f'Opacity{n}', 1)
        keys = {}
        if fade_in:
            keys.update({0: {1: 0, 'RH': {1: inn*.33, 2: v}}, inn: {1: v, 'LH': {1: -inn*.32, 2: 0}}})
        if fade_out:   # 片段很短时淡入终点和淡出起点可能同一帧：两边手柄合在一个关键帧上
            keys.setdefault(duration-out, {1: v}).update({1: v, 'RH': {1: out*.32, 2: 0}})
            keys[duration] = {1: 0, 'LH': {1: -out*.33, 2: v}}
        if keys:
            result[f'Opacity{n}'] = keys
    if fade_in and m.get('rise_width_ratio'):
        rise = m['rise_width_ratio']
        result['LineOffset.Y'] = {0: {1: -rise, 'RH': {1: inn*.33, 2: rise}}, inn: {1: 0, 'LH': {1: -inn*.32, 2: 0}}}
    return result


class ResolveIO:
    def __init__(self, resolve, project, timeline, project_name):
        self.r, self.p, self.main = resolve, project, timeline
        self.pm = resolve.GetProjectManager()
        self.name, self.pid = project_name, project.GetUniqueId()
        self.db = self.pm.GetCurrentDatabase()
        self.main_name, self.main_id = timeline.GetName(), timeline.GetUniqueId()
        self.guard()
        if find_timeline(project, self.main_name).GetUniqueId() != self.main_id:
            raise ValueError('目标时间线身份不匹配')

    def guard(self):
        p = self.pm.GetCurrentProject()
        if (not p or p.GetName() != self.name or p.GetUniqueId() != self.pid
                or self.pm.GetCurrentDatabase() != self.db):
            raise RuntimeError('当前工程/数据库与指定目标不一致，停止写入。')

    def write(self, fn, *args):
        self.guard()
        return fn(*args)

    def activate(self, timeline):
        self.guard()
        active = self.p.GetCurrentTimeline()
        if not active or active.GetUniqueId() != timeline.GetUniqueId():
            if not self.write(self.p.SetCurrentTimeline, timeline):
                raise RuntimeError('无法激活时间线：'+timeline.GetName())
            time.sleep(.3)
        if self.p.GetCurrentTimeline().GetUniqueId() != timeline.GetUniqueId():
            raise RuntimeError('活动时间线读回不符')

    def snapshot(self):
        self.activate(self.main)  # GetProperty has a different key set on inactive timelines.
        return snapshot(self.r, self.p, self.main)

    def seek(self,timeline,timecode):
        # Newly activated timelines may asynchronously restore their previous playhead.
        for _ in range(3):
            self.write(timeline.SetCurrentTimecode,timecode)
            stable=0
            for _ in range(10):
                time.sleep(.1)
                self.guard()
                stable=stable+1 if timeline.GetCurrentTimecode()==timecode else 0
                if stable>=4:return
        raise RuntimeError('花字播放头读回不符：目标 '+timecode+'，实际 '+str(timeline.GetCurrentTimecode()))

    def folder(self, name, parent=None):
        mp = self.p.GetMediaPool()
        parent = parent or mp.GetRootFolder()
        found = [f for f in parent.GetSubFolderList() or [] if f.GetName() == name]
        if len(found) > 1:
            raise ValueError('媒体池文件夹重名：'+name)
        folder = found[0] if found else self.write(mp.AddSubFolder, parent, name)
        if not folder:
            raise RuntimeError('无法创建媒体池文件夹：'+name)
        return folder

    def new_timeline(self, name):
        if any(self.p.GetTimelineByIndex(i).GetName() == name for i in range(1, self.p.GetTimelineCount()+1)):
            raise ValueError('不覆盖既有时间线：'+name)
        mp = self.p.GetMediaPool()
        folder = self.folder('花字音效')
        self.write(mp.SetCurrentFolder, folder)
        t = self.write(mp.CreateEmptyTimeline, name)
        if not t:
            raise RuntimeError('新建花字时间线失败')
        self.activate(t)
        self.write(t.SetSetting, 'useCustomSettings', '1')
        for key in SETTINGS:
            value = self.main.GetSetting(key)
            if str(t.GetSetting(key)) != str(value):
                self.write(t.SetSetting, key, str(value))
        self.write(t.SetStartTimecode, self.main.GetStartTimecode())
        if (any(str(t.GetSetting(k)) != str(self.main.GetSetting(k)) for k in SETTINGS)
                or t.GetStartTimecode() != self.main.GetStartTimecode()
                or t.GetStartFrame() != self.main.GetStartFrame()):
            raise RuntimeError('花字时间线设置读回不一致')
        if t.GetMediaPoolItem().GetUniqueId() not in {c.GetUniqueId() for c in folder.GetClipList()}:
            raise RuntimeError('花字时间线没有出现在指定媒体池文件夹')
        return t

    def insert(self, timeline, effect):
        self.activate(timeline)
        if any(i.GetEnd()>effect['start'] for i in objects(timeline, 'video')):
            raise ValueError('独立标题线插入点之后有片段，禁止会推移的插入')
        previous = {i.GetUniqueId(): (i.GetStart(), i.GetEnd()) for i in objects(timeline)}
        start, end = effect['start'], effect['end']
        base = int(timeline.GetStartFrame())
        marks = {'in': start-base, 'out': end-base-1}
        self.write(timeline.SetMarkInOut, marks['in'], marks['out'], 'all')
        if not all(v == marks for v in timeline.GetMarkInOut().values()):
            raise RuntimeError('花字入出点读回不符')
        tc = frame_timecode(start, timeline.GetSetting('timelineFrameRate'), ';' in timeline.GetStartTimecode())
        self.seek(timeline,tc)
        item = self.write(timeline.InsertFusionTitleIntoTimeline, 'Text+')
        if not item or (item.GetStart(), item.GetEnd()) != (start, end):
            raise RuntimeError('插入花字起止读回不符')
        now = {i.GetUniqueId(): (i.GetStart(), i.GetEnd()) for i in objects(timeline)}
        if any(now.get(k) != v for k, v in previous.items()) or len(now) != len(previous)+1:
            raise RuntimeError('插入使之前的花字移位，立即停止')
        self.write(item.SetName, effect.get('name', effect['effect_id']))
        if item.GetName() != effect.get('name', effect['effect_id']):
            raise RuntimeError('片段命名读回不符')
        self.style(item, effect, float(timeline.GetSetting('timelineFrameRate')))
        self.write(timeline.ClearMarkInOut, 'all')
        if any(timeline.GetMarkInOut().values()):
            raise RuntimeError('清除花字时间线入出点失败')
        return item

    def style(self, item, effect, fps=None):
        comp = item.GetFusionCompByIndex(1)
        tools = list((comp.GetToolList(False, 'TextPlus') or {}).values()) if comp else []
        if len(tools) != 1:
            raise RuntimeError('新标题没有唯一 Text+')
        tool = tools[0]
        tool.GetInputList()
        rs = int(comp.GetAttrs()['COMPN_RenderStart'])
        duration = int(item.GetDuration())
        cfg = config('fx_styles.json')
        style = cfg['styles'][effect['style']['profile']]
        # 文字颜色：计划 style.color 写登记的颜色名（white / accent＝#64A9E8），不写就用样式自带的白
        rgb = color_table(cfg).get(effect['style'].get('color')) or style.get('rgb', [1, 1, 1])
        x, y = effect['anchor']['center']
        values = dict(StyledText=effect['text'], Font=style['font'], Style=style['style'],
                      Size=effect['style']['size'], Center={1:x, 2:y},
                      HorizontalJustificationNew=3, VerticalJustificationNew=3,
                      Red1=rgb[0], Green1=rgb[1], Blue1=rgb[2], Alpha1=1, Opacity1=1,
                      Enabled1=1, Enabled2=0, Enabled3=int(effect['style'].get('shadow_enabled',style.get('shadow_enabled',False))),
                      ElementShape1=0,
                      GlobalIn=rs, GlobalOut=rs+duration-1)
        shadow = style.get('shadow')
        if shadow and values['Enabled3']:
            # 柔和投影（2026-11-12 包装系统）：样式里写全参数，不靠 Text+ 默认的大偏移硬阴影
            srgb = shadow['rgb']
            values.update(ElementShape3=0, Red3=srgb[0], Green3=srgb[1], Blue3=srgb[2], Alpha3=1,
                          Opacity3=shadow['opacity'], Offset3={1:shadow['offset'][0], 2:shadow['offset'][1]},
                          SoftnessX3=shadow['softness'], SoftnessY3=shadow['softness'])
        background = style.get('background')
        if background:
            brgb = background['rgb']
            values.update(Enabled2=1, ElementShape2=2, Level2=background['level'],
                          Red2=brgb[0], Green2=brgb[1], Blue2=brgb[2], Alpha2=1, Opacity2=background.get('opacity', 1),
                          ExtendHorizontal2=background['extend_horizontal'],
                          ExtendVertical2=background['extend_vertical'], Round2=background['round'],
                          SoftnessX2=0, SoftnessY2=0, Offset2={1:0,2:0})
        overrides = effect.get('overrides', {})
        for key, value in overrides.items():
            if not key.startswith('clip.') and key != 'SizeKeyframes':
                values[key] = {int(k): v for k, v in value.items()} if isinstance(value, dict) else value
        fps = fps or float(self.main.GetSetting('timelineFrameRate'))
        motion = motion_keys(effect, values, duration, fps, cfg)
        self.write(comp.StartUndo, '花字音效：完整写入')
        self.write(comp.Lock)
        try:
            for key, value in values.items():
                self.write(tool.SetInput, key, value)
            if 'SizeKeyframes' in overrides:
                keys = {rs+int(k): integer_keys(v) for k, v in overrides['SizeKeyframes'].items()}
            elif effect.get('animation'):
                keys = {rs+a['frame']-effect['start']: {1:a['value']} for a in effect['animation']}
            else:
                keys = {}  # Empty animation is explicitly static.
            if keys:
                self.write(tool.AddModifier, 'Size', 'BezierSpline')
                out = tool.Size.GetConnectedOutput()
                if not out or out.GetTool().GetAttrs()['TOOLS_RegID'] != 'BezierSpline':
                    raise RuntimeError('Size 的 BezierSpline 创建失败')
                self.write(out.GetTool().SetKeyFrames, keys, True)
            for name, rel in motion.items():
                target, _, axis = name.partition('.')
                if axis:   # 点输入（行偏移）先挂 XYPath，再给纵向挂曲线
                    self.write(tool.AddModifier, target, 'XYPath')
                    out = getattr(tool, target).GetConnectedOutput()
                    if not out or out.GetTool().GetAttrs()['TOOLS_RegID'] != 'XYPath':
                        raise RuntimeError(target+' 的 XYPath 创建失败')
                    owner, target = out.GetTool(), axis
                else:
                    owner = tool
                self.write(owner.AddModifier, target, 'BezierSpline')
                out = getattr(owner, target).GetConnectedOutput()
                if not out or out.GetTool().GetAttrs()['TOOLS_RegID'] != 'BezierSpline':
                    raise RuntimeError(name+' 的淡入淡出曲线创建失败')
                self.write(out.GetTool().SetKeyFrames, {rs+f: k for f, k in rel.items()}, True)
        finally:
            self.write(comp.Unlock)
            self.write(comp.EndUndo, True)
        for key, value in values.items():
            if key == 'Size' and keys:
                continue
            if key in motion:   # 淡入淡出的透明度：在完全显出的那一帧核对
                settled = min(f for f, k in motion[key].items() if k[1] == value)
                if not equal(tool.GetInput(key, rs+settled), value):
                    raise RuntimeError('花字输入读回不符：'+key)
                continue
            actual=safe(tool.GetInput(key, rs));expected=safe(value)
            matches=(isinstance(actual,dict) and all(equal(actual.get(k),v) for k,v in expected.items())) if isinstance(expected,dict) else equal(actual,expected)
            if not matches:
                raise RuntimeError('花字输入读回不符：'+key)
        for frame, key in keys.items():
            if not equal(tool.GetInput('Size', frame), key[1]):
                raise RuntimeError('花字弹出关键帧读回不符')
        for name, rel in motion.items():
            target, _, axis = name.partition('.')
            for f, k in rel.items():
                if f >= duration:
                    continue
                actual = tool.GetInput(target, rs+f)
                actual = safe(actual).get(2, safe(actual).get('2')) if axis else actual
                if not equal(actual, k[1]):
                    raise RuntimeError('淡入淡出关键帧读回不符：'+name)
        for key, value in overrides.items():
            if key.startswith('clip.'):
                self.write(item.SetProperty, key[5:], value)
                if not equal(item.GetProperty(key[5:]), value):
                    raise RuntimeError('片段位置缩放读回不符：'+key)

    def ensure_track(self, kind, name):
        t = self.main
        matches = [i for i in range(1, t.GetTrackCount(kind)+1) if t.GetTrackName(kind,i) == name]
        if len(matches) > 1:
            raise ValueError('专用轨道重名：'+name)
        if matches:
            index = matches[0]
            if not t.GetIsTrackEnabled(kind,index) or t.GetIsTrackLocked(kind,index):
                raise ValueError('目标轨道已停用或锁定：'+name)
            return index, False
        if kind=='audio':self.write(t.AddTrack,kind,'stereo')
        else:self.write(t.AddTrack,kind)
        index = t.GetTrackCount(kind)
        self.write(t.SetTrackName, kind, index, name)
        if t.GetTrackName(kind,index) != name:
            raise RuntimeError('轨道命名失败')
        return index, True

    def append(self, media, kind, track, start, source_start, length):
        self.activate(self.main)
        if any(i.GetStart() < start+length and start < i.GetEnd()
               for i in self.main.GetItemListInTrack(kind,track) or []):
            raise ValueError('目标区间已有片段，不覆盖：'+kind+str(track))
        args = dict(mediaPoolItem=media, startFrame=source_start, endFrame=source_start+length,
                    recordFrame=start, trackIndex=track, mediaType=1 if kind=='video' else 2)
        placed = self.write(self.p.GetMediaPool().AppendToTimeline, [args])
        if not placed or len(placed) != 1:
            raise RuntimeError('精确放置未返回一个片段')
        item = placed[0]
        where = safe(item.GetTrackTypeAndIndex())
        if (item.GetStart(), item.GetEnd(), item.GetDuration()) != (start, start+length, length):
            raise RuntimeError('放置片段起止/长度读回不符')
        actual = {i.GetUniqueId() for i in self.main.GetItemListInTrack(kind,track) or []}
        if item.GetUniqueId() not in actual:
            raise RuntimeError('放置轨道读回不符：'+str(where))
        self.write(item.SetClipColor, 'Pink')
        if item.GetClipColor() != 'Pink':
            raise RuntimeError('片段粉色标识读回不符')
        return item

    def media(self, path):
        mp = self.p.GetMediaPool()
        found = [m for m in folder_clips(mp.GetRootFolder())
                 if m.GetClipProperty('File Path') and Path(m.GetClipProperty('File Path')).resolve() == Path(path).resolve()]
        if found:
            return found[0]  # Existing duplicate imports are left untouched.
        folder = self.folder('音效', self.folder('花字音效'))
        self.write(mp.SetCurrentFolder, folder)
        imported = self.write(mp.ImportMedia, [str(path)])
        if not imported or len(imported) != 1 or Path(imported[0].GetClipProperty('File Path')).resolve() != Path(path).resolve():
            raise RuntimeError('音效导入读回不符')
        return imported[0]

    def save_reopen(self):
        self.activate(self.main)
        if not self.write(self.pm.SaveProject):
            raise RuntimeError('保存工程失败')
        if not self.write(self.pm.CloseProject, self.p):
            raise RuntimeError('关闭工程失败')
        for attempt in range(REOPEN_TRIES):
            if attempt:
                time.sleep(REOPEN_WAIT_SECONDS)
            # Only the just-saved project may be reopened; never switch databases.
            if self.pm.GetCurrentDatabase() != self.db:
                raise RuntimeError('关闭后数据库改变，停止重开')
            current = self.pm.GetCurrentProject()
            if current and current.GetName() not in ('Untitled Project', '无标题项目', self.name):
                raise RuntimeError('关闭后用户已打开其他工程，停止重开')
            project = self.pm.LoadProject(self.name)
            if project:
                break
        else:
            # 重开一直不成：工程现在是关着的。不切项目库、不开别的工程，说清楚让人手动打开
            raise RuntimeError(f'工程「{self.name}」已经保存成功，但关闭后重新打开失败（试了 {REOPEN_TRIES} 次），保存重开后的核对没有做。'
                               f'下一步：请在达芬奇里手动打开工程「{self.name}」后再继续（先 fx.py recover 演练看现场，再 --apply，然后照常 build）')
        self.p = project
        self.guard()
        self.main = find_timeline(self.p, self.main_name)
        if self.main.GetUniqueId() != self.main_id:
            raise RuntimeError('重开后主时间线身份变化')
        self.activate(self.main)


def integer_keys(value):
    if isinstance(value, dict):
        return {int(k) if str(k).lstrip('-').isdigit() else k: integer_keys(v) for k, v in value.items()}
    return value
