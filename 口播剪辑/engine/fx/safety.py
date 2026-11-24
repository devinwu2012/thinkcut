"""Font-metric screen bounds and offline/live layout safety checks.

All boxes use normalized bottom-left coordinates. No Resolve writes here.
"""
from functools import lru_cache
import math
from pathlib import Path


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def box_valid(b):
    return (isinstance(b, (list, tuple)) and len(b) == 4 and all(finite(x) for x in b)
            and 0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1)


SYSTEM_FONT_DIRS = (Path('/Library/Fonts'), Path('/System/Library/Fonts'))


def system_font_dirs():
    """系统字体目录；苹方（PingFang.ttc）在 macOS 的字体资源目录里，路径带一串随系统版本变的编号，按通配找。"""
    assets = sorted(Path('/System/Library/AssetsV2').glob('com_apple_MobileAsset_Font*/*/AssetData'))
    return (*SYSTEM_FONT_DIRS, *assets)


MEMBER = {}   # '合集文件#配置里的序号' → 实际序号。只有合集里的顺序和配置对不上（macOS 升级后会变）时才有内容，由 font_path 按字体名核对后记下


def open_font(path):
    """`字体.ttc#序号` 打开字体合集里的第几个字体（如系统自带的苹方 PingFang.ttc#11＝苹方 SC 中粗）。"""
    from fontTools.ttLib import TTFont
    name, _, index = str(path).rpartition('#')
    return TTFont(name, fontNumber=MEMBER.get(str(path), int(index))) if name and index.isdigit() else TTFont(str(path))


@lru_cache(maxsize=64)
def collection_member(file, index, family, weight, mtime_ns, size):
    """合集 file 里第 index 个是不是 family／weight 这个字体：是就返回 index；不是就按字体名在合集里找，返回找到的序号；找不到报错。
    序号会随 macOS 版本变，字体名不会。mtime_ns、size 只让字体文件换了以后重新核对。"""
    import struct
    from fontTools.ttLib import TTFont, TTLibError
    with open(file, 'rb') as stream:
        header = stream.read(12)
    count = struct.unpack('>L', header[8:12])[0] if len(header) == 12 and header[:4] == b'ttcf' else 1   # 不是合集就只有一个
    def named(number):
        try:
            with TTFont(file, fontNumber=number, lazy=True) as font:
                names = font['name'].names
                return (family in {n.toUnicode(errors='replace') for n in names if n.nameID in (1, 4, 6, 16)}
                        and weight in {n.toUnicode(errors='replace') for n in names if n.nameID in (2, 17)})
        except (TTLibError, KeyError):
            return False
    if index < count and named(index):
        return index
    for number in range(count):
        if named(number):
            return number
    raise ValueError(f'字体合集 {file} 里找不到「{family} {weight}」（配置写的是第 {index} 个，已经不是它；macOS 升级后合集里的顺序会变）。'
                     '下一步：在“字体册”确认这个字体还在；在的话把这段话交给 AI 核对 engine/config/fx_styles.json 的 font_file')


@lru_cache(maxsize=32)
def font_metrics(path, text):
    from fontTools.pens.boundsPen import BoundsPen
    with open_font(path) as font:
        cmap, glyphs = font.getBestCmap(), font.getGlyphSet()
        lines = []
        for line in text.split('\n'):
            advance, boxes = 0, []
            for char in line:
                name = cmap.get(ord(char))
                if name is None:
                    raise ValueError(f'字体缺少字形「{char}」，不能安全估算；请换含该字的字体')
                pen = BoundsPen(glyphs)
                glyphs[name].draw(pen)
                if pen.bounds:
                    x0, y0, x1, y1 = pen.bounds
                    boxes.append([advance+x0, y0, advance+x1, y1])
                advance += font['hmtx'][name][0]
            lines.append((advance, boxes))
        os2, hhea = font['OS/2'], font['hhea']
        return {'lines': lines, 'upem': font['head'].unitsPerEm,
                'span': os2.sTypoAscender-os2.sTypoDescender,
                'ascent': hhea.ascent, 'descent': hhea.descent,
                'line_height': hhea.ascent-hhea.descent+hhea.lineGap}


@lru_cache(maxsize=512)
def font_names(path, mtime_ns, size):
    # File metadata invalidates parsed names when a font is replaced.
    with open_font(path) as font:
        names = font['name'].names
        return ({n.toUnicode() for n in names if n.nameID in (1, 4, 6, 16)},
                {n.toUnicode() for n in names if n.nameID in (2, 17)})


def font_path(style, overrides, cfg, fonts_dir):
    # A hand-edited family must be matched, never silently use the profile font.
    family = overrides.get('Font', style['font'])
    weight = overrides.get('Style', style['style'])
    profiles = [s for s in cfg['styles'].values() if s['font'] == family and s['style'] == weight]
    filename = profiles[0].get('font_file') if profiles else None
    root = Path(fonts_dir or Path.home()/'Library/Fonts')
    if filename and (root/filename).is_file():
        return root/filename
    if filename and '#' in filename:   # 字体合集里的一个，按 用户字体 → 系统字体 找
        name, _, index = filename.rpartition('#')
        for base in (root, *([] if fonts_dir else system_font_dirs())):
            if (base/name).is_file():
                if index.isdigit():   # 核对这个序号还是不是要的字体；不是就按字体名找，量字时 open_font 用找到的那个
                    stat = (base/name).stat()
                    actual = collection_member(str(base/name), int(index), family, weight, stat.st_mtime_ns, stat.st_size)
                    if actual != int(index):
                        MEMBER[f'{base/name}#{index}'] = actual
                    else:
                        MEMBER.pop(f'{base/name}#{index}', None)
                return Path(f'{base/name}#{index}')
    paths = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.otf', '.ttf'))
    if filename:
        matched = [p for p in paths if p.name == filename]
        if matched:
            return matched[0]
    from fontTools.ttLib import TTLibError
    for path in paths:
        if path.suffix.lower() not in ('.otf', '.ttf'):
            continue
        stat = path.stat()
        try:
            families, weights = font_names(str(path), stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError, TTLibError):
            continue
        if family in families and weight in weights:
            return path
    raise ValueError(f'找不到字体文件：{family} / {weight}（{root}）；请安装字体后重试')


def sizes(effect, cfg):
    values = [effect['style']['size']]
    overrides = effect.get('overrides', {})
    keys = overrides.get('SizeKeyframes')
    if keys is not None:
        # The Bezier control hull also bounds between-key overshoot.
        for key in keys.values():
            value = key.get('1', key.get(1))
            values.append(value)
            for side in ('LH', 'RH'):
                handle = key.get(side, {})
                values.append(value+handle.get('2', handle.get(2, 0)))
    elif effect.get('animation'):
        values += [a['value'] for a in effect['animation'] if a['input'] == 'Size']
    if not all(finite(v) and v > 0 for v in values):
        raise ValueError('字号或动画曲线无效，无法估算外框')
    return min(values), max(values)


def default_size(effect, cfg, fonts_dir=None):
    """Stored defaults are Text+ Size; Text uses an equivalent em at 1920 wide."""
    style = cfg['styles'][effect['style']['profile']]
    size = style['default_size']
    if effect['style'].get('engine', 'Text+') == 'Text':
        path = font_path(style, effect.get('overrides', {}), cfg, fonts_dir)
        metrics = font_metrics(str(path), effect['text'])
        size *= cfg['safety']['textplus_width_factor']*cfg['safety']['text_reference_width']*metrics['upem']/metrics['span']
    return size


def with_default_sizes(plan, cfg=None, fonts_dir=None):
    from copy import deepcopy
    if cfg is None:
        from .plan import config
        cfg = config('fx_styles.json')
    result = deepcopy(plan)
    for effect in result['effects']:
        if effect['kind'] in ('photo_label','compare_label') and effect['anchor'].get('subject_kind')=='photo':
            effect['anchor'].setdefault('overlap_ok',True)
        if 'size' not in effect['style']:
            effect['style']['size'] = default_size(effect, cfg, fonts_dir)
    return result


def estimate(effect, width, height, cfg, fonts_dir=None):
    style = cfg['styles'][effect['style']['profile']]
    overrides = effect.get('overrides', {})
    path = font_path(style, overrides, cfg, fonts_dir)
    metrics = font_metrics(str(path), effect['text'])
    rules = cfg['safety']
    engine = effect['style'].get('engine', 'Text+')
    calibration = rules['font_calibration'].get(path.name, {})
    mode = calibration.get(engine, {})
    baseline = mode.get('baseline_units', (metrics['ascent']+metrics['descent'])/2)
    pad = rules['measurement_padding_px']
    # Uncalibrated fonts use an explicitly conservative vertical allowance.
    uncertainty = 0 if mode else rules['uncalibrated_padding_em']*metrics['upem']
    hx = effect['anchor'].get('horizontal_align', 'center')
    vy = effect['anchor'].get('vertical_align', 'center')
    hmap = rules['textplus_horizontal_alignment']
    vmap = rules['textplus_vertical_alignment']
    for key, mapping in (('HorizontalJustificationNew', hmap), ('VerticalJustificationNew', vmap)):
        if key in overrides:
            value = mapping.get(str(int(overrides[key])))
            if value is None:
                raise ValueError('未校准的对齐方式：'+key)
            if key.startswith('Horizontal'): hx = value
            else: vy = value
    if hx not in ('left', 'center', 'right') or vy not in ('bottom', 'center', 'top', 'baseline'):
        raise ValueError('对齐方式无效')
    center = effect['anchor']['center']
    centers = [center]+[a['value'] for a in effect.get('animation', []) if a['input'] == 'Center']
    motion = cfg.get('motion')
    if (motion and engine == 'Text+' and not effect['style'].get('static')
            and (effect.get('motion') or {}).get('in', True)):
        # 默认淡入时从下方 rise（单位画宽）处升上来，起点也要在安全区里
        centers.append([center[0], center[1]-motion['rise_width_ratio']*width/height])
    boxes = []
    for size in sizes(effect, cfg):
        # 苹方等字体在 Text+ 里按行高排字，实测写在 font_calibration 的 span_units；没有就用 OS/2 的上下伸
        scale = (size*width*rules['textplus_width_factor']/mode.get('span_units', metrics['span']) if engine == 'Text+'
                 else size*width/rules['text_reference_width']/metrics['upem'])
        line_boxes = []
        line_count = len(metrics['lines'])
        for index, (advance, glyph_boxes) in enumerate(metrics['lines']):
            dx = {'left': 0, 'center': -advance/2, 'right': -advance}[hx]
            dy = -baseline+(line_count-1)/2*metrics['line_height']-index*metrics['line_height']
            for b in glyph_boxes:
                line_boxes.append([(b[0]+dx)*scale, (b[1]+dy)*scale,
                                   (b[2]+dx)*scale, (b[3]+dy)*scale])
        if not line_boxes:
            raise ValueError('文字没有可见字形')
        b = [min(x[0] for x in line_boxes), min(x[1] for x in line_boxes),
             max(x[2] for x in line_boxes), max(x[3] for x in line_boxes)]
        background = style.get('background')
        if background:
            border = mode.get('border_fill')
            if not border or engine != 'Text+' or hx != 'center' or vy != 'center' or line_count != 1:
                raise ValueError('未校准的底框形态，不能只用字形外框通过安全检查')
            native_size = size*width
            ex = (overrides.get('ExtendHorizontal2', background['extend_horizontal'])+border['horizontal_padding_ratio'])*native_size
            ey = (border['height_ratio']/2+overrides.get('ExtendVertical2', background['extend_vertical']))*native_size
            half_width = metrics['lines'][0][0]*scale/2+ex
            b = [min(b[0],-half_width),min(b[1],-ey),max(b[2],half_width),max(b[3],ey)]
        shift = {'center': 0, 'bottom': -b[1], 'top': -b[3], 'baseline': baseline*scale}[vy]
        b[1] += shift; b[3] += shift
        outline = max(overrides.get('Thickness2') or 0, overrides.get('Thickness3') or 0)*metrics['upem']*scale
        b = [b[0]-outline, b[1]-outline-uncertainty*scale,
             b[2]+outline, b[3]+outline+uncertainty*scale]
        if overrides.get('Enabled3', effect['style'].get('shadow_enabled',style.get('shadow_enabled',False))):
            # 投影参数：用户手调读到的 overrides 优先，没有就用样式自带的（2026-11-12 包装系统的柔和投影）
            shadow = style.get('shadow') or {}
            offset = overrides.get('Offset3', {'1': shadow['offset'][0], '2': shadow['offset'][1]} if 'offset' in shadow else None)
            if not isinstance(offset,dict):
                raise ValueError('启用阴影时需读取原生着色3的Offset3和柔化值到overrides，不用固定小偏移替代')
            # 实测：Text+ 的 Offset3 单位是 Size×画宽；柔化每单位约 0.002×画宽（fx_styles.json safety.textplus_shadow）
            unit = size*width if engine == 'Text+' else width
            dx = offset.get('1', offset.get(1, 0))*unit
            dy = offset.get('2', offset.get(2, 0))*unit
            soft = shadow.get('softness', 0)
            softness = max(overrides.get('SoftnessX3', soft) or 0, overrides.get('SoftnessY3', soft) or 0,
                           overrides.get('SoftnessGlow3') or 0)*rules.get('textplus_shadow', {}).get('softness_px_per_width', 1)*width
            b = [min(b[0], b[0]+dx-softness), min(b[1], b[1]+dy-softness),
                 max(b[2], b[2]+dx+softness), max(b[3], b[3]+dy+softness)]
        for cx, cy in centers:
            x0, y0, x1, y1 = b
            zx, zy = overrides.get('clip.ZoomX', 1), overrides.get('clip.ZoomY', 1)
            pan, tilt = overrides.get('clip.Pan', 0), overrides.get('clip.Tilt', 0)
            if not all(finite(v) for v in (zx, zy, pan, tilt)) or zx <= 0 or zy <= 0:
                raise ValueError('片段 Pan/Tilt/Zoom 无效')
            boxes.append([width/2+((cx-.5)*width+x0)*zx+pan-pad,
                          height/2+((cy-.5)*height+y0)*zy+tilt-pad,
                          width/2+((cx-.5)*width+x1)*zx+pan+pad,
                          height/2+((cy-.5)*height+y1)*zy+tilt+pad])
    b = [min(x[0] for x in boxes), min(x[1] for x in boxes),
         max(x[2] for x in boxes), max(x[3] for x in boxes)]
    return {'bbox': [b[0]/width, b[1]/height, b[2]/width, b[3]/height],
            'bbox_px': b, 'font_file': str(path), 'engine': engine,
            'calibrated_font': bool(mode), 'sizes': list(sizes(effect, cfg))}


def subtitle_zone(timeline, rules):
    """Read all subtitle clips, including disabled/off-time cues. Unknown => fallback.

    SDK currently returns empty properties on Resolve 21.1. Explicit normalized
    BoundingBox / SubtitleBoundingBox is accepted when supplied by a snapshot
    adapter. Generic Pan/Tilt alone is NOT evidence of subtitle glyph extents.
    """
    boxes = []
    unreadable = False
    for item in timeline.get('items', []):
        if item.get('type') != 'subtitle': continue
        props = item.get('props', {})
        b = props.get('SubtitleBoundingBox', props.get('BoundingBox'))
        if box_valid(b): boxes.append(list(b))
        else: unreadable = True
    if not boxes or unreadable:
        boxes.append([0, 0, 1, rules['subtitle_fallback_bottom_ratio']])
    # Reserve a consistent band for the entire timeline, independent of cue time.
    return {'bbox': [0, min(b[1] for b in boxes), 1, max(b[3] for b in boxes)],
            'source': '字幕轨外框' if boxes and not unreadable and timeline.get('items')
                      and any(i.get('type') == 'subtitle' for i in timeline['items']) else '默认底部保留区',
            'fallback_used': not any(i.get('type') == 'subtitle' for i in timeline.get('items', [])) or unreadable}


def intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def distance(a, b):
    return math.hypot(max(b[0]-a[2], a[0]-b[2], 0), max(b[1]-a[3], a[1]-b[3], 0))


def overlap_fraction(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0]))*max(0, min(a[3], b[3])-max(a[1], b[1]))
    return area/((a[2]-a[0])*(a[3]-a[1]))


def check_layout(plan, timeline=None, cfg=None, fonts_dir=None, allow_unsafe=False, check_person_bbox=True):
    if cfg is None:
        from .plan import config
        cfg = config('fx_styles.json')
    rules = cfg['safety']
    plan = with_default_sizes(plan, cfg, fonts_dir)
    tl = timeline or plan['timeline']
    width, height = float(tl.get('width') or 0), float(tl.get('height') or 0)
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError('安全距离检查需要时间线 width / height；请提供分辨率或 --snapshot')
    zone = subtitle_zone(tl, rules)
    sub = [zone['bbox'][i]*(width if i%2 == 0 else height) for i in range(4)]
    violations, bounds = [], []
    for effect in plan['effects']:
        uid = effect['effect_id']; tag = f'花字「{uid} / {effect["text"]}」'
        margin_ratio = (rules['relaxed_edge_short_side_ratio'] if effect['kind'] in rules['relaxed_edge_kinds']
                        else rules['edge_short_side_ratio'])
        margin = min(width, height)*margin_ratio
        safe = [margin, margin, width-margin, height-margin]
        try:
            estimated = estimate(effect, width, height, cfg, fonts_dir)
        except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
            # --allow-unsafe never turns unknown geometry into a successful estimate.
            raise ValueError(tag+'：无法估算外框：'+str(exc)) from exc
        minimum = default_size(effect, cfg, fonts_dir)*rules['minimum_size_ratio']
        zoom = min(effect.get('overrides', {}).get('clip.ZoomX', 1), effect.get('overrides', {}).get('clip.ZoomY', 1))
        actual_minimum = sizes(effect, cfg)[0]*zoom
        bounds.append({'effect_id': uid, 'minimum_size': minimum, 'edge_ratio': margin_ratio, **estimated})
        b = estimated['bbox_px']; text_height = b[3]-b[1]
        raw = effect['anchor'].get('subject_bbox')
        subject = [v*(width if i%2 == 0 else height) for i, v in enumerate(raw)] if raw else None
        obstacles = [[v*(width if i%2 == 0 else height) for i,v in enumerate(box)]
                     for box in effect['anchor'].get('avoid_bboxes', [])]
        gap = rules['subject_gap_text_height_ratio']*text_height
        overlap_ok = effect['anchor'].get('overlap_ok', False) and effect['anchor'].get('subject_kind') is None
        photo_top_overlap = (effect['kind'] in ('photo_label','compare_label') and effect['anchor'].get('subject_kind')=='photo' and effect['anchor'].get('overlap_ok',False))
        person = effect['anchor'].get('subject_kind') == 'person'

        def subject_unsafe(box):
            if any(intersects(box,o) or distance(box,o) < gap-1e-6 for o in obstacles):
                return True
            if not subject or overlap_ok:
                return False
            if photo_top_overlap and box[1]>=subject[3]-rules.get('photo_top_overlap_text_height_ratio',.35)*(box[3]-box[1])-1e-6:
                return False
            if person and not check_person_bbox:
                return False
            if person:
                return overlap_fraction(box, subject) > rules['person_overlap_area_ratio']+1e-9
            return intersects(box, subject) or distance(box, subject) < gap-1e-6

        def suggestion(options):
            for dx, dy in sorted(options, key=lambda d: abs(d[0])+abs(d[1])):
                c = [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy]
                if (c[0] < safe[0]-1e-6 or c[1] < safe[1]-1e-6 or c[2] > safe[2]+1e-6
                        or c[3] > safe[3]+1e-6 or intersects(c, sub)):
                    continue
                if subject_unsafe(c):
                    continue
                axis = '右' if dx > 0 else '左' if dx < 0 else '上' if dy > 0 else '下'
                amount = abs(dx or dy)
                ratio = amount/(width if dx else height)
                return f'建议向{axis}移至少 {amount:.1f}px（画面比例 {ratio:.4f}）', [dx/width, dy/height]
            return '当前位置无法仅平移满足全部规则；需要用户决定：换留白位置、调整主体布局或取消此效果；不自动缩字', None

        def issue(rule, deficit, options, detail):
            advice, delta = suggestion(options)
            violations.append({'effect_id': uid, 'rule': rule, 'excess_px': round(deficit, 3),
                               'suggested_delta': delta,
                               'message': f'{tag}：{detail} {deficit:.1f}px；{advice}。'})
        if actual_minimum < minimum-1e-9:
            violations.append({'effect_id': uid, 'rule': 'minimum_size', 'minimum_size': minimum,
                               'actual_minimum_size': actual_minimum, 'suggested_delta': None,
                               'message': f'{tag}：字号/动画/片段缩放后的最小字号 {actual_minimum:.6g} 低于样式下限 {minimum:.6g}；需要用户决定：恢复字号并换位置或取消此效果；不自动缩字。'})
        for label, deficit, delta in (
                ('左', safe[0]-b[0], (safe[0]-b[0], 0)), ('右', b[2]-safe[2], (safe[2]-b[2], 0)),
                ('下', safe[1]-b[1], (0, safe[1]-b[1])), ('上', b[3]-safe[3], (0, safe[3]-b[3]))):
            if deficit > 1e-6: issue('edge', deficit, [delta], f'越过{label}边安全线')
        if intersects(b, sub):
            options = [(0, sub[3]-b[1]), (0, sub[1]-b[3]), (sub[0]-b[2], 0), (sub[2]-b[0], 0)]
            issue('subtitle', min(abs(dx or dy) for dx, dy in options), options, '进入字幕保留区，需退出')
        if subject_unsafe(b):
            relevant = ([subject] if subject else [])+obstacles
            options = [(dx,dy) for box in relevant for dx,dy in
                       [(box[0]-gap-b[2],0),(box[2]+gap-b[0],0),(0,box[1]-gap-b[3]),(0,box[3]+gap-b[1])]]
            if person and subject:
                allowance=rules['person_overlap_area_ratio']*(b[2]-b[0])*(b[3]-b[1])
                overlap_x=max(0,min(b[2],subject[2])-max(b[0],subject[0]))
                overlap_y=max(0,min(b[3],subject[3])-max(b[1],subject[1]))
                if overlap_y:
                    allowed_width=allowance/overlap_y
                    options += [(subject[0]+allowed_width-b[2],0),(subject[2]-allowed_width-b[0],0)]
                if overlap_x:
                    allowed_height=allowance/overlap_x
                    options += [(0,subject[1]+allowed_height-b[3]),(0,subject[3]-allowed_height-b[1])]
            deficit = min(abs(dx or dy) for dx, dy in options)
            detail = (f'与人物重叠占外框 {overlap_fraction(b, subject):.1%}，超过 {rules["person_overlap_area_ratio"]:.0%}，需移动'
                      if person else '与主体重叠或半个字高的间距不足')
            issue('subject', deficit, options, detail)
    decisions = []
    for uid in dict.fromkeys(v['effect_id'] for v in violations):
        rows = [v for v in violations if v['effect_id'] == uid]
        decisions.append({'effect_id': uid, 'status': '需要用户决定',
                          'reasons': [v['message'] for v in rows],
                          'options': ['按建议移动文字，保持不低于字号下限', '调整主体布局后重新审核', '取消此效果', '用户明确接受后用 --allow-unsafe 强制写入']})
    return {'valid': not violations or allow_unsafe, 'allow_unsafe': bool(allow_unsafe),
            'needs_user_decision': decisions,
            'blocked_effect_ids': [] if allow_unsafe else [d['effect_id'] for d in decisions],
            'unsafe_skipped': bool(violations and allow_unsafe), 'violations': violations,
            'warnings': (['已使用 --allow-unsafe 跳过安全距离违规'] if allow_unsafe else []),
            'subtitle_zone': zone, 'bounds': bounds, 'frame_size': [width, height]}
