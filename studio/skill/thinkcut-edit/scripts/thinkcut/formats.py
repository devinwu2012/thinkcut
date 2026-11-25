# 画幅预设：默认全部 4K。坐标统一用 4K 像素（动画工程内部按 1080 宽的“逻辑坐标”画，出片时乘 2）。
# 三种版式都已由用户确认：3:4 是烟道那条实际用过的；9:16、16:9 于 2026-10-18 看样图后定稿。

FORMATS = {
    '3:4': dict(
        W=2160, H=2880, anim_fmt='p', verified=True,
        card=(60, 360, 2040, 2020),                 # 动画卡片 x, y, w, h（底边 2380）
        sub=dict(cy=2604, size=104, min=88, maxw=1880),   # 字幕：中心高度、字号、最小字号、单行最宽
        nav=(0, 0, 2160, 340),                      # 章节导航条放的位置（烟道那条实测：顶部 0–340）
        body=(80, 340, 2000),                       # 小圆圈从口播画面取的方块（胸口以上）x, y, 边长
    ),
    '3:4满屏': dict(                                 # 2026-10-31 用户（261030 别墅地下室防水）：B-roll、动画铺满导航条以下整个画面，
        W=2160, H=2880, anim_fmt='p', verified=True,  # 人脸圆圈直接盖在画面上，字幕（自带半透明黑底）叠在画面下部
        card=(0, 340, 2160, 2540),                  # 画面区：导航条下沿到底
        pip_xy=(104, 1496),                         # 小圆圈左上角，和 3:4 卡片版同一位置（在字幕上方）
        sub_over_card=True,                         # 字幕可以叠在画面上，subs-check 不再报“压到动画卡片”
        sub=dict(cy=2604, size=104, min=88, maxw=1880),
        nav=(0, 0, 2160, 340),
        body=(80, 340, 2000),
    ),
    '9:16': dict(
        W=2160, H=3840, anim_fmt='v', verified=True,
        card=(80, 1140, 2000, 1700),                # 底边 2840
        sub=dict(cy=2990, size=104, min=88, maxw=1880),
        nav=(0, 780, 2160, 340),                    # 卡片正上方（最顶上会被手机状态栏挡）
        body=(80, 820, 2000),
    ),
    '16:9': dict(
        W=3840, H=2160, anim_fmt='h', verified=True,
        card=(80, 240, 3680, 1600),                 # 顶部导航条、通栏卡片（底边 1840）、底部字幕带
        sub=dict(cy=2000, size=96, min=80, maxw=3200),
        nav=(0, 0, 3840, 200),
        body=(1320, 80, 1200),
    ),
}
NAV_H_NEW = 224      # 2026-11-14 用户定：导航条换成短标签样式后更矮，新开的片子条高 224（老片子 job.json 里没有 nav_h，仍是 340／横屏 200）
NAV_H_MORE = 40      # nav_h 最多比画幅原来的条高多这么多（16:9 原来 200，新片子 224 要合法）
PIP_D, PIP_GAP, RING = 840, 44, 12   # 小圆圈直径、离卡片边的距离、白边宽（4K 像素）


def get(name, nav_h=None):
    """nav_h：这条片子的导航条高度（job.json 的 nav_h）。不给就是老版式，一个像素都不变。"""
    if name not in FORMATS:
        raise SystemExit(f'不认识的画幅「{name}」，只能是 ' + '、'.join(FORMATS))
    f = dict(FORMATS[name]); f['name'] = name
    if nav_h:
        # 只能是正整数，最多比原来高 NAV_H_MORE，且条子不能压到画面区（3:4 最高 360、16:9 最高 240、9:16 最高 380、3:4满屏不能比 340 高）
        ok = isinstance(nav_h, int) and not isinstance(nav_h, bool) and nav_h > 0
        if ok:
            nx, ny, nw, old = f['nav']; top = ny + old - nav_h if ny > 0 else 0
            ok = nav_h <= old + NAV_H_MORE and top >= 0 and top + nav_h <= f['card'][1]
        if not ok: raise SystemExit(f'job.json 的 nav_h（导航条高度）写成了 {nav_h!r}：{name} 画幅要写正整数，不超过 {f["nav"][3]}+{NAV_H_MORE}，且条子不能压到画面区（新片子是 {NAV_H_NEW}；不写或写 0 是老版式）')
        nx, ny, nw, old = f['nav']; d = old - int(nav_h)
        if ny > 0: ny += d                                   # 9:16：条子在卡片正上方，变矮后往下靠，仍贴着卡片
        elif f.get('sub_over_card') and d > 0:               # 3:4满屏：画面区跟着上移，从条子下沿铺到底（不然 B-roll、动画顶上会露一截空底）
            x, y, w, h = f['card']; f['card'] = (x, y - d, w, h + d)
        f['nav'] = (nx, ny, nw, int(nav_h))                  # 3:4 卡片版、16:9：只改条子，卡片不动
    x, y, w, h = f['card']
    py = y + h - PIP_GAP - PIP_D
    f['pip'] = dict(d=PIP_D, ring=RING, L=(x + PIP_GAP, py), R=(x + w - PIP_GAP - PIP_D, py))
    if f.get('pip_xy'):                      # 满屏版：圆圈位置单独定，不跟画面区底边走
        px, py = f['pip_xy']; f['pip'].update(L=(px, py), R=(f['W'] - px - PIP_D, py))
    f['anim_w'] = f['W']                     # 动画按 4K 宽度出图（网址参数 w）
    return f
