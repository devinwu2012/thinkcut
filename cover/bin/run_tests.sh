#!/usr/bin/env bash
# 离线测试：用样例底图排三种比例并检查边距、色块、箭头和报警。不生图、不消耗额度。
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P) || exit 1
# 测试用自己的配置档（“测试IP”当默认 IP），不靠哪个真 IP 的配置（bin/make_test_profile.py，2026-11-24）
if [[ -z "${THINKCUT_PROFILES:-}" ]]; then P=$(mktemp -d) || exit 1; trap 'rm -rf "$P"' EXIT; THINKCUT_PROFILES=$(python3 "$ROOT/../bin/make_test_profile.py" "$P") || exit 1; export THINKCUT_PROFILES; fi
python3 -B - "$ROOT" <<'PY'
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
root = Path(sys.argv[1]); S = root / "skill/thinkcut-cover/scripts/thinkcut_cover.py"; sample = root / "samples/烟道这样包"
fails = []
def check(ok, msg): print(("通过 " if ok else "失败 ") + msg); ok or fails.append(msg)
def run(d, cmd, *a): return subprocess.run([sys.executable, "-B", str(S), cmd, str(d), *a], capture_output=True, text=True)
SAME = "横版和竖版不是同一张画"          # 2026-11-21 另生横版底图时的提醒（样例的横版底图就是另生的）
def quiet(out): return all(SAME in w for l in out.splitlines() if "警告：" in l for w in l.split("警告：", 1)[1].split("；"))

with tempfile.TemporaryDirectory() as t:
    d = Path(t) / "job"; (d / "_制作文件").mkdir(parents=True)
    for f in ("spec.json", "bg_portrait.png", "bg_landscape.png"):
        shutil.copy(sample / f, d / "_制作文件" / f)
    r = run(d, "render", "--ratios", "all"); check(r.returncode == 0, "render 三种比例运行成功" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
    check(quiet(r.stdout) and r.stdout.count(SAME) == 2 and SAME not in r.stdout.splitlines()[0], "样例除了横版提醒“横版和竖版不是同一张画”没有别的警告")
    qc = {q["ratio"]: q for q in json.loads((d / "_制作文件/qc_3x4_4x3_16x9.json").read_text())}
    sizes = {"3:4": (1080, 1440), "4:3": (1440, 1080), "16:9": (1920, 1080)}
    for ratio, size in sizes.items():
        f = Path(qc[ratio]["file"]); im = Image.open(f); check(im.size == size, f"{ratio} 尺寸 {im.size}")
        a = np.asarray(im.convert("RGB")).astype(int); M = qc[ratio]["margin"]
        title = (a[..., 2] - a[..., 0] > 50) & (np.abs(a - (38, 84, 160)).max(axis=2) < 40)   # 2026-10-24 起标题深蓝
        ys, xs = np.nonzero(title[: 420 * min(size) // 1080, : size[0] // 2])
        check(abs(ys.min() - M) <= 3 and abs(xs.min() - M) <= 3, f"{ratio} 标题上边距 {ys.min()}、左边距 {xs.min()} 都等于 {M}")
        bx, by, bw, bh = qc[ratio]["label"]; sb = qc[ratio]["subject_box"]; tx, ty = qc[ratio]["target"]
        check(by + bh < ty and abs(ty - sb[1]) <= 20 and sb[4] - 20 <= tx <= sb[5] + 20, f"{ratio} 色块在主角上方，箭头指向主角顶部")
    m34 = np.asarray(Image.open(qc["3:4"]["file"]).convert("RGB")).astype(int)
    sb = qc["3:4"]["subject_box"]; body = m34[sb[1] + 10: sb[3], sb[0]: sb[2]]          # 主角本身（箭头是红橙色，不算在内）
    warm = (body[..., 0] - body[..., 2] > 45) & (body[..., 0] > 140)
    check(warm.mean() < 0.0005, f"主角的陶土色已全部换成蓝色（残留 {warm.mean():.3%}）")
    tx, ty = qc["3:4"]["target"]; near = m34[ty - 60: ty + 4, tx - 50: tx + 50]
    red = (np.abs(near - (226, 84, 44)).max(axis=2) < 50)
    check(red.sum() > 200, "箭头是红橙色（2026-11-12 起）")
    check(abs(1080 - (qc["3:4"]["label"][0] + qc["3:4"]["label"][2]) - qc["3:4"]["margin"]) <= 1, "3:4 色块右边距等于标题边距")
    # no terracotta subject → must warn
    g = Image.open(d / "_制作文件/bg_portrait.png").convert("L").convert("RGB"); g.save(d / "_制作文件/bg_portrait.png")
    r = run(d, "render", "--ratios", "3:4"); check("没找到陶土色主角" in r.stdout, "底图没有上色主角时会报警")
    # 2026-11-23 初稿模式实测（Q193 复发）：主角本来就小（墙上的开关面板，上色只占 0.2%）也要认出来、画箭头；只有零星一个色点（0.02%）仍报警
    sm = Path(t) / "small"; (sm / "_制作文件").mkdir(parents=True)
    sp0 = json.loads((sample / "spec.json").read_text())
    (sm / "_制作文件/spec.json").write_text(json.dumps(dict(sp0, sub=""), ensure_ascii=False))
    def small_flag(on): (sm / "_制作文件/spec.json").write_text(json.dumps(dict(sp0, sub="", **({"small_subject": True} if on else {})), ensure_ascii=False))
    def small_bg(w, h):
        im = Image.new("RGB", (1024, 1536), (244, 240, 232)); dr = ImageDraw.Draw(im)
        dr.rectangle((300, 700, 760, 1400), outline=(70, 70, 70), width=3)                 # 灰线画的门
        dr.rectangle((820, 980, 820 + w, 980 + h), fill=(196, 112, 82), outline=(70, 70, 70))  # 陶土色的开关面板
        im.save(sm / "_制作文件/bg_portrait.png")
    small_bg(56, 56)                                                                       # 56×56 ≈ 0.20%
    r = run(sm, "render", "--ratios", "3:4"); check("没找到陶土色主角" in r.stdout and "small_subject" in r.stdout, "小主角没开开关时照旧报警，报警里写了怎么开（small_subject）")
    small_flag(True)
    r = run(sm, "render", "--ratios", "3:4"); q = json.loads((sm / "_制作文件/qc_3x4.json").read_text())[0] if r.returncode == 0 else {}
    check(r.returncode == 0 and q.get("subject_box") and q.get("target") and "没找到陶土色主角" not in r.stdout, "小主角开关（small_subject）打开后：上色只占 0.2% 的开关面板也认得出、画了箭头")
    small_bg(16, 16)                                                                       # 16×16 ≈ 0.016%：只是个色点
    r = run(sm, "render", "--ratios", "3:4"); check("没找到陶土色主角" in r.stdout, "小主角开关打开时，只有一个零星色点仍报“没找到主角”")
    r = run(Path(t) / "sflag", "init", "--line1", "开关插座", "--line2", "装多高", "--scene", "x", "--subject", "y", "--small-subject")
    check(r.returncode == 0 and json.loads((Path(t) / "sflag/_制作文件/spec.json").read_text()).get("small_subject") is True, "init --small-subject 写进 spec.json")
    # 一行主标题按逗号拆两行；关键词画红圈
    d2 = Path(t) / "one"; r = run(d2, "init", "--line1", "水电这四件事儿，多少钱都得花", "--sub", "十三年的装修经验", "--circle", "四件事儿", "--scene", "x", "--subject", "y")
    sp = json.loads((d2 / "_制作文件/spec.json").read_text()) if r.returncode == 0 else {}
    check([sp.get("line1"), sp.get("line2")] == ["水电这四件事儿", "多少钱都得花"], "一行主标题按逗号自动拆成两行")
    check(run(Path(t) / "bad", "init", "--line1", "没有逗号的一行", "--sub", "s", "--scene", "x", "--subject", "y").returncode != 0, "一行又没逗号时提示要两行")

    # 2026-11-13 审查：比例之间多一个空格以前报英文 KeyError；不认识的比例给中文提示
    for f in ("spec.json", "bg_portrait.png", "bg_landscape.png"):
        shutil.copy(sample / f, d / "_制作文件" / f)
    r = run(d, "render", "--ratios", "4:3, 16:9"); check(r.returncode == 0 and r.stdout.count("封面已出") == 2 and "Traceback" not in r.stderr, "比例之间多打空格也能出图")
    r = run(d, "render", "--ratios", "9:16"); check(r.returncode != 0 and "不认识的比例：9:16" in r.stderr and "Traceback" not in r.stderr, "不认识的比例给中文提示")
    # 2026-11-13 审查：主标题、副标题太长，缩到最小字号仍顶出边距时要报警（以前警告是空的）
    long = Path(t) / "long"; (long / "_制作文件").mkdir(parents=True)
    shutil.copy(sample / "bg_portrait.png", long / "_制作文件/bg_portrait.png")
    sp = json.loads((sample / "spec.json").read_text())
    (long / "_制作文件/spec.json").write_text(json.dumps(dict(sp, line1="烟道这样包住进去就反味老房翻新必看装修避坑", sub="老房翻新必看" * 5), ensure_ascii=False))
    r = run(long, "render", "--ratios", "3:4")
    check("主标题太长" in r.stdout and "请改短" in r.stdout, "主标题缩到最小字号仍超出右边距时报警")
    check("副标题太长" in r.stdout, "副标题缩到最小字号色块仍超出边距时报警")
    # 2026-11-13 审查：换色不再用 Pillow 13 要移除的写法，输出像素必须和旧写法完全一样
    import hashlib, importlib.util, warnings
    spec_ = importlib.util.spec_from_file_location("thinkcut_cover", S); dc = importlib.util.module_from_spec(spec_); spec_.loader.exec_module(dc)
    def old_to_blue(img):          # 改之前的 to_blue，原样留在这里做对照
        hsv = np.asarray(img.convert("HSV")).astype(np.float32) / 255
        h, s_, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        warm = ((h < 45 / 360) | (h > 345 / 360)) & (s_ > 0.14)
        w = np.clip((s_ - 0.14) / 0.12, 0, 1) * warm
        hsv[..., 0] = np.where(w > 0, dc.ACCENT_HUE, h)
        hsv[..., 1] = s_ * (1 - w) + np.minimum(s_ * 1.05, 0.62) * w
        hsv[..., 2] = v * (1 - w) + v * 0.96 * w
        return Image.fromarray((hsv * 255).round().astype(np.uint8), "HSV").convert("RGB")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        news = {n: dc.to_blue(Image.open(p).convert("RGB")) for n, p in (("竖版", sample / "bg_portrait.png"), ("横版", sample / "bg_landscape.png"), ("四宫格", root / "samples/水电四宫格/bg_grid.png"))}
    check(not [w for w in caught if issubclass(w.category, DeprecationWarning)], "换色不再触发 Pillow 弃用警告")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            same = all(np.array_equal(np.asarray(old_to_blue(Image.open(p).convert("RGB"))), np.asarray(news[n])) for n, p in (("竖版", sample / "bg_portrait.png"), ("横版", sample / "bg_landscape.png"), ("四宫格", root / "samples/水电四宫格/bg_grid.png")))
        check(same, "三张样例底图换色结果和旧写法逐像素相同")
    except TypeError:              # Pillow 13 起旧写法跑不了，只剩下面的摘要对照
        print("跳过 旧写法在这个 Pillow 版本已不能运行，只核对摘要")
    got = hashlib.sha256(news["竖版"].tobytes()).hexdigest()
    check(got == "22c6f80071649518c6e2a4fbb7667a6381d254e257d39dbb243a5df6bff72f9d", "竖版样例底图换色结果和改写前记录的摘要一致（" + got[:12] + "）")

    # 四宫格（水电那期底图）：不生图，三种比例直接出
    g = Path(t) / "grid"; (g / "_制作文件").mkdir(parents=True)
    for f in ("spec.json", "bg_grid.png"):
        shutil.copy(root / "samples/水电四宫格" / f, g / "_制作文件" / f)
    r = run(g, "gen", "grid", "--task-only"); check(r.returncode == 0 and "Top-left" in (g / "_制作文件/task_grid.md").read_text(), "四宫格生图任务单写出")
    r = run(g, "render", "--ratios", "all"); check(r.returncode == 0, "四宫格 render 三种比例运行成功" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
    check("警告" not in r.stdout, "四宫格样例没有任何警告")
    gq = {q["ratio"]: q for q in json.loads((g / "_制作文件/qc_3x4_4x3_16x9.json").read_text())}
    for ratio, size in sizes.items():
        q = gq[ratio]; im = np.asarray(Image.open(q["file"]).convert("RGB")).astype(int)
        check(im.shape[1::-1] == size and q["plan"]["cols"] == q["plan"]["rows"] == 2, f"四宫格 {ratio} 尺寸对、2×2")
        check(q["red_on_other_text"] == 0, f"四宫格 {ratio} 红圈和箭头不压别的字")
        L, R, cy, ry = q["oval"]; band = im[cy - 4: cy + 4, max(0, L - 12): L + 12]
        check((np.abs(band - (226, 84, 44)).max(axis=2) < 50).sum() > 10, f"四宫格 {ratio} 红橙圈画在关键词左边")
        check(abs(q["head_dir_deg"] - (90 if q["plan"]["side"] == "top" else 0)) < 15, f"四宫格 {ratio} 箭头朝向格子")
    want = {"3:4": ("top", 1.0, "top"), "4:3": ("top", 0.8, "side"), "16:9": ("left", 0.9, "top")}       # 和水电那期定稿一致
    check(all((gq[k]["plan"]["side"], gq[k]["plan"]["ts"], gq[k]["plan"]["mode"]) == v for k, v in want.items()), "四宫格三种比例排法和水电那期定稿一致")
    # 2026-11-14 四种新版式：不生图。数字大字用烟道样例底图；另外三种用水电四宫格底图裁象限现拼
    def sheet(idx, out):
        src = Image.open(root / "samples/水电四宫格/bg_grid.png").convert("RGB"); sw, sh = src.size
        paper = tuple(int(v) for v in np.median(np.asarray(src)[: sh // 10].reshape(-1, 3), axis=0)); im = Image.new("RGB", (sw, sh), paper); cw = sw // len(idx)
        for j, i in enumerate(idx):
            q = src.crop(((i % 2) * sw // 2, (i // 2) * sh // 2, (i % 2 + 1) * sw // 2, (i // 2 + 1) * sh // 2))
            ys, xs = np.nonzero(np.abs(np.asarray(q).astype(int) - paper).max(axis=2) > 40); q = q.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
            sc = min(0.82 * cw / q.width, 0.8 * sh / q.height); q = q.resize((round(q.width * sc), round(q.height * sc)), Image.LANCZOS)
            im.paste(q, (j * cw + (cw - q.width) // 2, (sh - q.height) // 2))
        im.save(out)
    cells = lambda *xs: [v for l in xs for v in ("--cell", l, "--cell-scene", "a drain", "--cell-subject", "the drain body")]
    jobs = {"compare": (["--line1", "同样是地漏", "--line2", "差的是这一截", "--sub", "十三年的装修经验", "--circle", "这一截", *cells("普通地漏|水封浅，返味", "水母地漏|水封 50 毫米")], [3, 3]),
            "steps": (["--line1", "换灯带变压器", "--line2", "就这三步", "--sub", "不用拆吊顶", "--circle", "三步", *cells("一拽|拉开检修口", "取下|拔掉旧变压器", "一卡|新的卡回去")], [2, 1, 3]),
            "zoom": (["--line1", "浴室柜里藏着的", "--line2", "这个小阀门", "--sub", "下水不返味全靠它", "--zoom-at", "0.45,0.3", *cells("整体", "特写")], [0, 3]),
            "number": (["--line1", "中央换气扇", "--line2", "一套只要", "--sub", "卫生间不返味", "--number", "400", "--unit", "块钱", "--scene", "x", "--subject", "y"], None)}
    cn = {"compare": "左右对比", "steps": "三步流程", "zoom": "局部放大", "number": "数字大字"}; nq = {}
    def near(im, x0, y0, x1, y1, rgb, tol=50): return int((np.abs(im[max(0, int(y0)): int(y1), max(0, int(x0)): int(x1)] - rgb).max(axis=2) < tol).sum())
    for lay, (args, idx) in jobs.items():
        j = Path(t) / lay; r = run(j, "init", "--layout", lay, *args); check(r.returncode == 0, f"{cn[lay]} 登记成功" + ("" if r.returncode == 0 else "：" + r.stderr[-200:]))
        if idx:
            sheet(idx, j / "_制作文件" / f"bg_{lay}.png")
            r = run(j, "gen", lay, "--task-only"); check(r.returncode == 0 and "terracotta" in (j / f"_制作文件/task_{lay}.md").read_text() and "a drain" in (j / f"_制作文件/task_{lay}.md").read_text(), f"{cn[lay]} 生图任务单写出")
            check(run(j, "gen", "grid", "--task-only").returncode != 0, f"{cn[lay]} 用错底图种类时拦住")
        else:                    # 数字大字只给竖版底图：横版复用竖版画（2026-11-21）
            shutil.copy(sample / "bg_portrait.png", j / "_制作文件/bg_portrait.png")
        r = run(j, "render", "--ratios", "all"); check(r.returncode == 0, f"{cn[lay]} render 三种比例运行成功" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
        check(r.returncode == 0 and "警告" not in r.stdout, f"{cn[lay]} 样例没有任何警告")
        if r.returncode: continue
        nq[lay] = {q["ratio"]: q for q in json.loads((j / "_制作文件/qc_3x4_4x3_16x9.json").read_text())}
        for ratio, size in sizes.items():
            q = nq[lay][ratio]; im = np.asarray(Image.open(q["file"]).convert("RGB")).astype(int); M = q["margin"]
            check(im.shape[1::-1] == size and q["red_on_other_text"] == 0, f"{cn[lay]} {ratio} 尺寸对、红线不压别的字")
            if lay in ("compare", "steps"):
                x0, y0, x1, y1 = q["box"]; n = len(q["cells"])
                check(0 < x0 and 0 < y0 and x1 < size[0] and y1 < size[1] and q["plan"]["cols"] * q["plan"]["rows"] == n, f"{cn[lay]} {ratio} 外框在画面里、{n} 格")
                check(all(x0 <= c["art"][0] and c["art"][0] + c["art"][2] <= x1 and y0 <= c["art"][1] and c["art"][1] + c["art"][3] <= y1 for c in q["cells"]), f"{cn[lay]} {ratio} 小图都在外框里")
            if lay == "compare":
                (ax, ay, aw, ah), (bx, by, bw, bh) = q["cells"][0]["art"], q["cells"][1]["art"]
                blue = lambda x, y, w, h: int(((im[y: y + h, x: x + w, 2] - im[y: y + h, x: x + w, 0]) > 50).sum())
                check(blue(ax, ay, aw, ah) < 50 and blue(bx, by, bw, bh) > 2000, f"左右对比 {ratio} 左格去色不上蓝、右格上蓝")
                check(near(im, ax + aw / 2 - 30, ay + ah / 2 - 30, ax + aw / 2 + 30, ay + ah / 2 + 30, (226, 84, 44)) > 100, f"左右对比 {ratio} 左格压了红橙叉")
                check(q["tip"][0] > (x0 + x1) / 2 and q["tip"][1] < y0 and abs(q["head_dir_deg"] - 90) < 15, f"左右对比 {ratio} 箭头朝下指向右格")
            if lay == "steps":
                check(near(im, x0, y0, x1, y1, (226, 84, 44)) > 300, f"三步流程 {ratio} 格子里有红橙小箭头")
            if lay == "number":
                nb = q["number_box"]; art = q["art"]
                check(nb[0] == M and nb[2] <= size[0] - M and (q["underline_bottom"] < art[1] or nb[2] < art[0]), f"数字大字 {ratio} 数字不出边距、不压插图")
                check(q["plan"]["side"] != "top" or art[1] - q["underline_bottom"] >= q["title_px"] * 0.9, f"数字大字 {ratio} 数字和插图之间留够一个字高")
                check(near(im, nb[0], nb[3], nb[2], q["underline_bottom"] + 4, (226, 84, 44)) > 500, f"数字大字 {ratio} 数字下面有红橙下划线")
            if lay == "zoom":
                cx, cy, R = q["circle"]; rx, ry, rr = q["ring"]; ox, oy, ow, oh = q["over"]; tb = q["title_box"]
                check(cx - R >= 0 and cy - R >= 0 and cx + R <= size[0] and cy + R <= size[1], f"局部放大 {ratio} 放大圆在画面里")
                check(ox <= rx <= ox + ow and oy <= ry <= oy + oh and np.hypot(rx - cx, ry - cy) > R + rr, f"局部放大 {ratio} 小圆环在整体小图里、没被大圆盖住")
                check(tb[0] == M and tb[2] <= size[0] - M and q["plan"]["by"] >= tb[3], f"局部放大 {ratio} 标题贴边距、色块在标题下方")
                o = im[oy: oy + oh, ox: ox + ow]; check(int(((o[..., 0] - o[..., 2] > 45) & (o[..., 0] > 140)).sum()) < 50, f"局部放大 {ratio} 整体小图已去色")
    if "number" in nq:
        q = nq["number"]["3:4"]; check(abs(q["number_px"] / q["title_px"] - 3.5) < 0.1 and q["plan"]["side"] == "top", "数字大字 3:4 数字字号约为主标题 3.5 倍、插图在数字下面")
    if "steps" in nq:
        check((nq["steps"]["3:4"]["plan"]["cols"], nq["steps"]["3:4"]["plan"]["rows"]) == (1, 3), "三步流程 3:4 是三行一列")
    bad = lambda *a: run(Path(t) / "badnew", "init", "--line1", "甲甲甲", "--line2", "乙乙乙", "--sub", "s", *a).returncode != 0
    check(bad("--layout", "number", "--scene", "x", "--subject", "y"), "数字大字不给 --number 时拦住")
    check(bad("--layout", "compare", "--circle", "甲", *cells("a")), "左右对比只给一组格子时拦住")
    check(bad("--layout", "steps", *cells("a", "b")), "三步流程只给两组格子时拦住")
    check(bad("--layout", "zoom", "--zoom-at", "2,0.5", *cells("a", "b")), "局部放大 --zoom-at 超出 0–1 时拦住")
    z = Path(t) / "zoom" / "_制作文件"; Image.open(z / "bg_zoom.png").convert("L").convert("RGB").save(z / "bg_zoom.png")
    r = run(Path(t) / "zoom", "render", "--ratios", "3:4"); check("第 2 张没找到陶土色部分" in r.stdout, "局部放大特写没上色时报警")
    lg = Path(t) / "number"; sp = json.loads((lg / "_制作文件/spec.json").read_text()); (lg / "_制作文件/spec.json").write_text(json.dumps(dict(sp, number="400000000000"), ensure_ascii=False))
    r = run(lg, "render", "--ratios", "3:4"); check("数字太长" in r.stdout, "数字缩到最小仍超出右边距时报警")

    # 2026-11-20 实拍留色画风：用合成底图（灰色照片上一块高饱和绿色），不生图
    rng = np.random.default_rng(7)
    def fake(w, h, box, col=(40, 170, 60)):
        g = np.clip(np.linspace(90, 200, w)[None, :] + rng.normal(0, 12, (h, w)), 0, 255)
        a = np.repeat(g[..., None], 3, axis=2); x0, y0, x1, y1 = box
        a[y0:y1, x0:x1] = np.clip(np.array(col) + rng.normal(0, 8, (y1 - y0, x1 - x0, 1)), 0, 255)
        return Image.fromarray(a.astype(np.uint8))
    def fsheet(cells, cols, rows, out, gut=12):
        im = Image.new("RGB", (1536, 1024), "white"); cw, ch = (1536 - gut * (cols + 1)) // cols, (1024 - gut * (rows + 1)) // rows
        for i, q in enumerate(cells):
            s = min(cw / q.width, ch / q.height); q = q.resize((round(q.width * s), round(q.height * s)))
            im.paste(q, (gut + (i % cols) * (cw + gut) + (cw - q.width) // 2, gut + (i // cols) * (ch + gut) + (ch - q.height) // 2))
        im.save(out)
    one = fake(600, 400, (360, 120, 460, 300))
    m, b = dc.find_colour(one); check(b is not None and max(abs(u - v) for u, v in zip(b, (360, 120, 459, 299))) <= 2, f"实拍留色 找留色区：框 {b}")
    check(dc.find_colour(one.convert("L").convert("RGB"))[1] is None, "实拍留色 黑白照片找不到留色区")
    q, win = dc.crop_for(one, (0, 0, 300, 300), dc.box_centre(one, b))
    check(win == (200, 0, 400, 400) and q.size == (300, 300) and [round(v) for v in dc.map_box(b, win, (0, 0, 300, 300))] == [round((b[0] - 200) * 0.75), round(b[1] * 0.75), round((b[2] - 200) * 0.75), round(b[3] * 0.75)],
          "实拍留色 等比裁切窗口和坐标换算对")
    tip = dc.snap_tip(m, win, (0, 0, 300, 300), (0, 0)); tx, ty = tip[0] / 0.75 + 200, tip[1] / 0.75
    check(b[0] <= tx <= b[2] and b[1] <= ty <= b[3], "实拍留色 箭头尖吸到彩色像素上")
    # 2026-11-24 初稿模式第二次实测：留色主体被裁出画面要警告（只测检测和警告，不动风格参数）
    def crop_warn(focus, rect=(0, 0, 200, 400)):
        qq = {"warnings": []}; _q, w_ = dc.crop_for(one, rect, focus); cb_ = dc.map_box(b, w_, rect)
        dc.photo_oval([(ImageDraw.Draw(Image.new("RGB", (400, 600))), (200, 40, 40))], cb_, 1.0, rect, qc=qq); return [x for x in qq["warnings"] if "裁掉" in x]
    check(len(crop_warn((300, 200))) == 1 and "%" in crop_warn((300, 200))[0], "实拍留色 主体被裁掉一大半时警告")
    check(crop_warn((410, 200)) == [] and crop_warn(dc.box_centre(one, b)) == [], "实拍留色 主体完整在画面里不误报")
    T = ["--line1", "千万精装房", "--line2", "收房前必查", "--sub", "10个隐患", "--style", "photo"]
    pc = lambda *ls: [v for l in ls for v in ("--cell", l, "--cell-scene", "x", "--cell-subject", "y")]
    tall = lambda: fake(400, 900, (150, 300, 250, 650))
    pjobs = {"single": ([*T, "--circle", "收房前", "--scene", "x", "--subject", "y"], {"bg_photo.png": one}, 1),
             "number": (["--line1", "千万精装房", "--line2", "收房前必查", "--sub", "CEO住两年才发现", "--number", "10", "--unit", "个隐患", "--style", "photo", "--scene", "x", "--subject", "y"], {"bg_photo.png": one}, 1),
             "zoom": ([*T, "--circle", "收房前", "--scene", "x", "--subject", "y"], {"bg_photo.png": one}, 1),
             "full": ([*T, "--circle", "收房前", "--scene", "x", "--subject", "y"], {"bg_photo_portrait.png": fake(400, 600, (200, 330, 300, 520)), "bg_photo_landscape.png": one}, 1),
             "grid": ([*T, "--circle", "收房前", *pc("电视墙|不承重", "变压器|漏电", "基材|不环保", "淋浴房|关不严")], {"sheet": ([one] * 4, 2, 2)}, 4),
             "six": ([*T, "--circle", "收房前", *pc("电视墙", "变压器", "基材", "淋浴房", "入户门", "网线")], {"sheet": ([one] * 6, 3, 2)}, 6),
             "triptych": ([*T, "--circle", "收房前", *pc("电视墙", "变压器", "淋浴房")], {"sheet": ([tall(), tall(), tall()], 3, 1)}, 3),
             "compare": ([*T, "--cell", "看得见的", "--cell-scene", "x", "--cell-subject", "none", *pc("看不见的")], {"sheet": ([one.convert("L").convert("RGB"), one], 2, 1)}, 1),
             "polaroid": ([*T, *pc("a", "b", "c", "d")], {"sheet": ([one] * 4, 2, 2)}, 1)}
    for lay, (args, bgs, ncol) in pjobs.items():
        j = Path(t) / f"photo_{lay}"; r = run(j, "init", "--layout", lay, *args); check(r.returncode == 0, f"实拍留色 {lay} 登记成功" + ("" if r.returncode == 0 else "：" + r.stderr[-200:]))
        kind = {"grid": "grid", "six": "six", "triptych": "triptych", "compare": "compare", "polaroid": "polaroid", "full": "portrait"}.get(lay, "photo")
        r = run(j, "gen", kind, "--task-only"); tk = next((j / "_制作文件").glob("task_photo*.md")).read_text() if r.returncode == 0 else ""
        check("SELECTIVE COLOUR" in tk and "no people" in tk and "terracotta" not in tk, f"实拍留色 {lay} 生图任务单用实拍留色模板")
        for f, im in bgs.items():
            if f == "sheet":
                fsheet(im[0], im[1], im[2], j / "_制作文件" / f"bg_photo_{lay}.png")
            else:
                im.save(j / "_制作文件" / f)
        r = run(j, "render", "--ratios", "all"); check(r.returncode == 0 and r.stdout.count("封面已出") == 3, f"实拍留色 {lay} 三种比例都出图" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
        if r.returncode:
            continue
        check("没找到留色区域" not in r.stdout and "红圈或箭头压到" not in r.stdout, f"实拍留色 {lay} 没有误报")
        for q in json.loads((j / "_制作文件/qc_3x4_4x3_16x9.json").read_text()):
            im = np.asarray(Image.open(q["file"]).convert("RGB")).astype(int); cbs = q.get("colour_boxes") or []
            check(len(cbs) == ncol and q["red_on_other_text"] == 0, f"实拍留色 {lay} {q['ratio']} 找到 {len(cbs)} 块留色、红线不压标题字")
            ok = True
            for x0, y0, x1, y1 in cbs:
                cx, cy = round((x0 + x1) / 2), round((y0 + y1) / 2); bw_ = (x1 - x0) / 2 + 70
                ring = (38, 84, 160) if lay == "zoom" else (226, 84, 44)           # 局部放大的小图上是深蓝小圈
                ok &= near(im, cx - bw_, cy - 4, cx, cy + 4, ring) > 3 and near(im, cx, cy - 4, cx + bw_, cy + 4, ring) > 3
                ok &= near(im, cx - 5, cy - 5, cx + 5, cy + 5, (40, 170, 60), 60) > 20     # 照片不调色：留色还是原来的绿色
            check(ok, f"实拍留色 {lay} {q['ratio']} 红圈圈住了留色块、留色没被调色")
    bad = lambda *a: run(Path(t) / "photobad", "init", "--line1", "甲甲甲", "--line2", "乙乙乙", "--sub", "s", *a)
    r = bad("--layout", "steps", "--style", "photo", *pc("a", "b", "c")); check(r.returncode != 0 and "三步流程暂时不支持实拍留色" in r.stderr, "三步流程选实拍留色时给中文提示拦住")
    check(bad("--layout", "six", *pc("a", "b", "c", "d", "e", "f"), "--circle", "甲").returncode != 0, "六宫格不加 --style photo 时拦住")
    check(run(Path(t) / "grid", "gen", "photo", "--task-only").returncode != 0, "线稿版式用 gen photo 时拦住")
    z = Path(t) / "photo_single"; one.convert("L").convert("RGB").save(z / "_制作文件/bg_photo.png")
    r = run(z, "render", "--ratios", "3:4"); check("没找到留色区域" in r.stdout, "实拍留色 底图没有颜色时报警")
    g6 = Path(t) / "photo_six" / "_制作文件"; Image.open(g6 / "bg_photo_six.png").convert("L").convert("RGB").save(g6 / "bg_photo_six.png")
    r = run(Path(t) / "photo_six", "render", "--ratios", "3:4"); check("第 6 张：没找到留色区域" in r.stdout, "实拍留色 拼图里某张没有颜色时报警")

    # 2026-11-20 用户：主标题一般两行，三行也可以（--line3）。两行的排法一点不变：样例的几何位置和改之前记录的一致
    want2 = {"3:4": (116, [510, 416, 489, 81], [700, 726]), "4:3": (116, [870, 351, 489, 81], [800, 510]), "16:9": (128, [1299, 381, 540, 91], [1056, 566])}
    check(all((qc[k]["title_px"], list(qc[k]["label"]), list(qc[k]["target"])) == v for k, v in want2.items()), "两行主标题 单主角样例的字号、色块、箭头位置和加三行之前一样")
    want2 = {"3:4": ([394, 834, 272, 82], [880, 560]), "4:3": ([332, 685, 233, 66], [847, 461]), "16:9": ([362, 757, 482, 74], [843, 706])}
    check(all((gq[k]["oval"], gq[k]["tip"]) == v for k, v in want2.items()), "两行主标题 四宫格样例的圈和箭头位置和加三行之前一样")
    check("line3" not in json.loads((d2 / "_制作文件/spec.json").read_text()), "两行主标题的登记里没有 line3")
    r = run(Path(t) / "l3bad", "init", "--line1", "为什么外地", "--line3", "我不接了", "--sub", "s", "--scene", "x", "--subject", "y")
    check(r.returncode != 0 and "--line2" in r.stderr, "给了 --line3 没给 --line2 时拦住")
    r = run(Path(t) / "l3comma", "init", "--line1", "为什么外地，灯光设计，我不接了", "--sub", "s", "--scene", "x", "--subject", "y")
    sp = json.loads((Path(t) / "l3comma/_制作文件/spec.json").read_text()) if r.returncode == 0 else {}
    check(len([k for k in sp if k.startswith("line")]) == 2, "一行里有两个逗号仍只拆成两行（三行要显式给 --line3）")
    T3 = ["--line1", "为什么外地", "--line2", "灯光设计", "--line3", "我不接了", "--sub", "十三年的装修经验"]
    def bands(im, x1, y1, hide=()):
        """标题深蓝墨迹按行分段（hide 里的框先抹掉，如色块）。返回 [(上, 下), …]。"""
        b = (im[..., 2] - im[..., 0] > 50) & (np.abs(im - (38, 84, 160)).max(axis=2) < 40)
        for x0_, y0_, w_, h_ in hide:
            b[max(0, y0_ - 4): y0_ + h_ + 4, max(0, x0_ - 4): x0_ + w_ + 4] = False
        rows = np.nonzero(b[:y1, :x1].any(axis=1))[0]; out = []
        for y in rows:
            if out and y - out[-1][1] <= 6:
                out[-1][1] = y
            else:
                out.append([y, y])
        return [tuple(v) for v in out if v[1] - v[0] > 20]
    s3 = Path(t) / "l3single"; r = run(s3, "init", *T3, "--circle", "不接了", "--scene", "x", "--subject", "y")
    sp = json.loads((s3 / "_制作文件/spec.json").read_text()) if r.returncode == 0 else {}
    check([k for k in sp if k.startswith("line")] == ["line1", "line2", "line3"] and sp.get("line3") == "我不接了", "三行主标题登记成功（line1、line2、line3）")
    for f in ("bg_portrait.png", "bg_landscape.png"):
        shutil.copy(sample / f, s3 / "_制作文件" / f)
    r = run(s3, "render", "--ratios", "all"); check(r.returncode == 0 and quiet(r.stdout), "三行主标题 单主角三种比例都出图、没有警告" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
    if r.returncode == 0:
        for q in json.loads((s3 / "_制作文件/qc_3x4_4x3_16x9.json").read_text()):
            im = np.asarray(Image.open(q["file"]).convert("RGB")).astype(int); M = q["margin"]; S_ = q["title_px"]; H_, W_ = im.shape[:2]
            bs = bands(im, W_ // 2, H_ // 2, [q["label"]])
            gaps_ok = len(bs) == 3 and all(bs[i][1] < bs[i + 1][0] for i in range(2)) and bs[-1][1] < H_
            pitch_ok = len(bs) == 3 and all(abs(bs[i + 1][0] - bs[i][0] - round(S_ * 1.3)) <= 4 for i in range(2))
            check(gaps_ok and pitch_ok and abs(bs[0][0] - M) <= 3, f"三行主标题 单主角 {q['ratio']} 三行墨迹都在画面里、互不重叠、行距 1.3 倍、上边距 {M}（{bs}）")
            check(q["red_on_other_text"] == 0 and (q["title_px"] == 104 or q["ratio"] == "16:9"), f"三行主标题 单主角 {q['ratio']} 圈在第三行、红线不压别的字、字号是两行的 0.9 倍（{q['title_px']}px）")
    for name, args, cl in (("l3grid_c3", T3, "不接了"), ("l3grid_c2", ["--line1", "千万精装房", "--line2", "收房前必查", "--line3", "10个隐患", "--sub", "CEO住两年才发现"], "收房前")):
        g3 = Path(t) / name
        r = run(g3, "init", "--layout", "grid", *args, "--circle", cl, *cells("一|甲", "二|乙", "三|丙", "四|丁"))
        shutil.copy(root / "samples/水电四宫格/bg_grid.png", g3 / "_制作文件/bg_grid.png")
        r = run(g3, "render", "--ratios", "all"); check(r.returncode == 0 and "警告" not in r.stdout, f"三行主标题 四宫格（圈在第 {name[-1]} 行）三种比例都出图、没有警告" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
        if r.returncode:
            continue
        for q in json.loads((g3 / "_制作文件/qc_3x4_4x3_16x9.json").read_text()):
            im = np.asarray(Image.open(q["file"]).convert("RGB")).astype(int); H_, W_ = im.shape[:2]; p_ = q["plan"]
            bs = bands(im, p_["gx0"] - 10 if p_["side"] == "left" else W_, p_["gy0"] - 10 if p_["side"] == "top" else H_)
            row = next((i for i, (a_, b_) in enumerate(bs[-3:]) if a_ <= q["oval"][2] <= b_ + 30), None)
            check(q["red_on_other_text"] == 0 and len(bs) == 4 and row == int(name[-1]) - 1, f"三行主标题 四宫格 {q['ratio']} 色块＋三行、圈在第 {name[-1]} 行、红圈和箭头不压别的字")
    p3 = Path(t) / "l3six"; run(p3, "init", "--layout", "six", "--style", "photo", *T3, "--circle", "不接了", *pc("电视墙", "变压器", "基材", "淋浴房", "入户门", "网线"))
    fsheet([one] * 6, 3, 2, p3 / "_制作文件/bg_photo_six.png")
    r = run(p3, "render", "--ratios", "all"); check(r.returncode == 0 and r.stdout.count("封面已出") == 3 and "警告" not in r.stdout, "三行主标题 实拍六宫格三种比例都出图、没有警告" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
    lg3 = Path(t) / "l3long"; run(lg3, "init", *T3[:4], "--line3", "我不接了外地灯光设计的活儿真的不接了谁来都不接", "--sub", "s", "--scene", "x", "--subject", "y")
    shutil.copy(sample / "bg_portrait.png", lg3 / "_制作文件/bg_portrait.png")
    r = run(lg3, "render", "--ratios", "3:4"); check("主标题太长" in r.stdout, "三行主标题 第三行太长时报警")
    r = run(lg3, "init", "--layout", "grid", *T3[:4], "--line3", "我不接了外地灯光设计的活儿真的不接", "--sub", "s", "--circle", "不接了", *cells("一", "二", "三", "四"))
    shutil.copy(root / "samples/水电四宫格/bg_grid.png", lg3 / "_制作文件/bg_grid.png")
    r = run(lg3, "render", "--ratios", "3:4"); check(r.returncode != 0 and "排不下" in r.stderr, "三行主标题 四宫格第三行太长时提示排不下")

    # 2026-11-21（261117 反馈单新5）封面文字不分主副：--sub 可以不给。不画色块，红圈圈关键词，红箭头从圈指向主角
    check(dc.default_circle(["7米挑空的采光井", "灯装哪？才够亮？"]) == "灯装哪？" and dc.default_circle(["烟道这样包", "住进去就反味"]) == "住进去就反味",
          "无副标题 默认圈最后一行，中间有标点时圈到第一个标点为止")
    ns = Path(t) / "nosub"; r = run(ns, "init", "--line1", "烟道这样包", "--line2", "住进去就反味", "--scene", "x", "--subject", "y")
    sp = json.loads((ns / "_制作文件/spec.json").read_text()) if r.returncode == 0 else {}
    check(sp.get("sub") == "" and sp.get("circle") == "住进去就反味" and "默认圈" in r.stdout, "无副标题 登记成功、副标题为空、默认圈写进登记")
    shutil.copy(sample / "bg_portrait.png", ns / "_制作文件/bg_portrait.png")         # 只有竖版底图：横版复用竖版画（新6）
    r = run(ns, "render", "--ratios", "all"); check(r.returncode == 0 and "警告" not in r.stdout, "无副标题 单主角三种比例都出图、没有警告" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
    if r.returncode == 0:
        for q in json.loads((ns / "_制作文件/qc_3x4_4x3_16x9.json").read_text()):
            im = np.asarray(Image.open(q["file"]).convert("RGB")).astype(int); H_, W_ = im.shape[:2]; tx, ty = q["target"]
            dk = np.abs(im - (38, 84, 160)).max(axis=2) < 30          # 深蓝色块会有连着 150px 以上的深蓝横条（标题笔画没这么长）
            label = any((lambda i: (i[1::2] - i[0::2]).max())(np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))) > 150 * min(W_, H_) / 1080 for row in dk if row.any())
            check(q["label"] is None and not label and q["red_on_other_text"] == 0 and near(im, tx - 60, ty - 60, tx + 60, ty + 4, (226, 84, 44)) > 200,
                  f"无副标题 单主角 {q['ratio']} 没有色块、红箭头指到主角顶部、红线不压别的字")
            check((q["ratio"] == "3:4") != ("reused_portrait" in q), f"无副标题 单主角 {q['ratio']} 横版复用竖版画、3:4 用原图")
    r = run(ns, "init", "--line1", "烟道这样包", "--line2", "住进去就反味", "--arrow-at", "0.5,0.5", "--scene", "x", "--subject", "y")
    r = run(ns, "render", "--ratios", "3:4"); q = json.loads((ns / "_制作文件/qc_3x4.json").read_text())[0]; b = q["subject_box"]
    check(r.returncode == 0 and abs(q["target"][0] - (b[0] + b[2]) / 2) < 40 and abs(q["target"][1] - (b[1] + b[3]) / 2) < 40, f"--arrow-at 0.5,0.5 箭头落在主角上色部分正中附近（{q['target']}）")
    check(run(Path(t) / "atbad", "init", "--layout", "grid", "--line1", "甲甲甲", "--line2", "乙乙乙", "--circle", "甲", "--arrow-at", "0.5,0.5", *cells("a", "b", "c", "d")).returncode != 0
          and run(Path(t) / "atbad", "init", "--line1", "甲甲甲", "--line2", "乙乙乙", "--arrow-at", "2,0", "--scene", "x", "--subject", "y").returncode != 0,
          "--arrow-at 用在四宫格、或数超出 0–1 时拦住")
    nsl = {"grid": (["--layout", "grid", *cells("一|甲", "二|乙", "三|丙", "四|丁")], {"bg_grid.png": root / "samples/水电四宫格/bg_grid.png"}),
           "compare": (["--layout", "compare", *cells("普通地漏|水封浅", "水母地漏|水封深")], "compare"),
           "steps": (["--layout", "steps", *cells("一拽|拉开", "取下|拔掉", "一卡|卡回")], "steps"), "zoom": (["--layout", "zoom", *cells("整体", "特写")], "zoom"),
           "number": (["--layout", "number", "--number", "400", "--unit", "块钱", "--scene", "x", "--subject", "y"], {"bg_portrait.png": sample / "bg_portrait.png"})}
    for lay, (args, bgs) in nsl.items():
        j = Path(t) / f"nosub_{lay}"; r = run(j, "init", "--line1", "同样是地漏", "--line2", "差的是这一截", *args)
        if isinstance(bgs, str):
            shutil.copy(Path(t) / bgs / f"_制作文件/bg_{bgs}.png", j / f"_制作文件/bg_{bgs}.png")
        else:
            for f, src in bgs.items():
                shutil.copy(src, j / "_制作文件" / f)
        r = run(j, "render", "--ratios", "all"); ok = r.returncode == 0 and r.stdout.count("封面已出") == 3
        qs = json.loads((j / "_制作文件/qc_3x4_4x3_16x9.json").read_text()) if ok else []
        reds = [near(np.asarray(Image.open(q["file"]).convert("RGB")).astype(int), 0, 0, 2000, 2000, (226, 84, 44)) for q in qs]
        check(ok and all(q.get("red_on_other_text", 0) == 0 for q in qs) and all(v > 1500 for v in reds),
              f"无副标题 {dc.LAYOUT_CN[lay]} 三种比例都出图、有红线（圈或下划线）、红线不压别的字" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))
    for lay, (args, bgs, ncol) in pjobs.items():
        j = Path(t) / f"photo_{lay}"; a2 = [v for i, v in enumerate(args) if v != "--sub" and (i == 0 or args[i - 1] != "--sub")]
        r = run(j, "init", "--layout", lay, *a2)
        r = run(j, "render", "--ratios", "all"); ok = r.returncode == 0 and r.stdout.count("封面已出") == 3
        qs = json.loads((j / "_制作文件/qc_3x4_4x3_16x9.json").read_text()) if ok else []
        check(ok and "压到" not in r.stdout and all(q["red_on_other_text"] == 0 and not (q.get("label") or [0, 0, 0])[2] for q in qs),
              f"无副标题 实拍留色 {lay} 三种比例都出图、没有色块、红线不压字和标签" + ("" if r.returncode == 0 else "：" + r.stderr[-300:]))

    # 2026-11-21 用户：全角问号在标题里占位比量出来的宽，圈收在问号中间。下一个字的墨迹起点改按“新起的一段墨迹”找
    S_ = 116; f_ = dc.title_font(S_, 1)
    for kw in ("灯装哪？", "才够亮？"):
        o = dc.keyword_oval("灯装哪？才够亮？", f_, S_, kw, 0, 0, 1.0)
        a_ = np.asarray(dc.line_img("灯装哪？才够亮？"[: "灯装哪？才够亮？".find(kw) + len(kw)], f_, S_ * 0.03, dc.TITLE_INK).getchannel("A")) > 128
        check(o["R"] - np.nonzero(a_.any(0))[0].max() > 0.05 * S_, f"关键词「{kw}」以全角问号结尾时圈的右边在问号外面（多出 {o['R'] - np.nonzero(a_.any(0))[0].max():.0f}px）")

    # 2026-11-21（新6）画被画面边缘裁掉就缩小上提；横版默认复用竖版画
    def vignette(top, bottom, size=(1024, 1536)):
        """米白纸上一幅四周留纸边的画：灰框 + 陶土色主角，画的上下边由参数定（默认竖版 1024×1536）。"""
        im = Image.new("RGB", size, (250, 248, 244)); d_ = ImageDraw.Draw(im)
        d_.rectangle((120, top, 900, bottom), outline=(90, 90, 90), width=4); d_.rectangle((420, top + 60, 600, bottom - 80), fill=(200, 110, 80), outline=(90, 90, 90), width=3)
        return im
    v = Path(t) / "vignette"; run(v, "init", "--line1", "烟道这样包", "--line2", "住进去就反味", "--sub", "老房翻新必看", "--scene", "x", "--subject", "y")
    vignette(820, 1500).save(v / "_制作文件/bg_portrait.png")
    r = run(v, "render", "--ratios", "3:4"); q = json.loads((v / "_制作文件/qc_3x4.json").read_text())[0]
    im = np.asarray(Image.open(q["file"]).convert("RGB")).astype(int)
    check(r.returncode == 0 and q.get("lifted") and "警告" not in r.stdout and (np.abs(im[-4:] - im[-4:].reshape(-1, 3).mean(0)).max() < 30),
          f"画贴底被 3:4 裁掉时自动缩小上提，底边不再有画、没有警告（{q.get('lifted')}）")
    check(not json.loads((d / "_制作文件/qc_3x4_4x3_16x9.json").read_text())[0].get("lifted"), "满幅场景（烟道样例）画到图边不算被裁，不动")
    vignette(30, 990, (1536, 1024)).save(v / "_制作文件/bg_landscape.png")         # 另生的横版底图画得满满当当：16:9 裁掉上下
    r = run(v, "render", "--ratios", "16:9"); check("仍放不下" in r.stdout, "画太高、缩到 6 成仍放不下时报警")
    (v / "_制作文件/bg_landscape.png").unlink()
    vignette(820, 1500).save(v / "_制作文件/bg_portrait.png")
    r = run(v, "render", "--ratios", "4:3,16:9"); qs = json.loads((v / "_制作文件/qc_4x3_16x9.json").read_text()) if r.returncode == 0 else []
    check(len(qs) == 2 and all(q.get("reused_portrait") for q in qs) and "警告" not in r.stdout, "没有另生横版底图时横版复用竖版画、没有警告")
    Image.new("RGB", (1536, 1024), (250, 248, 244)).save(v / "_制作文件/bg_landscape.png")
    r = run(v, "render", "--ratios", "4:3"); check(SAME in r.stdout, "另生了横版底图时提醒“横版和竖版不是同一张画”")

    # 2026-11-21（新7）线稿提示词写明平涂、无渐变；换色后残留粉橙色报警（红橙圈和箭头不算）
    tasks = [(Path(t) / "nosub", "portrait"), (g, "grid"), (Path(t) / "compare", "compare")]
    check(all(run(j, "gen", kd, "--task-only").returncode == 0 and "no gradient, no glow" in (j / f"_制作文件/task_{kd}.md").read_text() for j, kd in tasks), "线稿生图任务单写明平涂、无渐变无光晕")
    check(dc.warm_left(sample / "bg_portrait.png") < dc.WARM_LEFT and dc.warm_left(root / "samples/水电四宫格/bg_grid.png") < dc.WARM_LEFT, "合格样例底图换色后没有残留粉橙色")
    glow = vignette(820, 1500); gp = np.asarray(glow).astype(np.float32); yy, xx = np.mgrid[0:1536, 0:1024]
    w_ = np.clip(1 - np.hypot(xx - 510, yy - 700) / 420, 0, 1)[..., None] * 0.35        # 主角上方一团柔和的陶土色光晕（渐变）
    Image.fromarray((gp * (1 - w_) + np.array((215, 120, 90)) * w_).astype(np.uint8)).save(v / "_制作文件/bg_portrait.png")
    r = run(v, "render", "--ratios", "3:4"); check("残留" in r.stdout, f"底图光画成渐变、换色后留粉橙色时报警（{dc.warm_left(v / '_制作文件/bg_portrait.png'):.1%}）")

    # 2026-11-21（261118 反馈单新5／Q188）每张生图任务单开头都带“不打开、不参考任何旧图，按文字新生成”
    FRESH = "不打开、不参考任何旧图，按文字新生成"; skill = (root / "skill/thinkcut-cover/SKILL.md").read_text()
    r1, r2 = run(Path(t) / "nosub", "gen", "landscape", "--task-only"), run(Path(t) / "photo_full", "gen", "landscape", "--task-only")
    tks = sorted(Path(t).glob("*/_制作文件/task_*.md"))
    kinds = {"task_" + n + ".md" for n in ("portrait", "landscape", "grid", "compare", "steps", "zoom", "photo", "photo_portrait", "photo_landscape", "photo_grid", "photo_six", "photo_triptych", "photo_compare", "photo_polaroid")}
    check(r1.returncode == r2.returncode == 0 and kinds <= {p.name for p in tks} and all(p.read_text().startswith(FRESH) for p in tks),
          f"生图任务单 {len(kinds)} 种底图（线稿 6 种、实拍 8 种，共 {len(tks)} 份）开头都带“{FRESH}”")
    check(FRESH in skill, "说明里写了按文字新生成")      # 灯光类主角默认辅灯是 IP 的规矩，查在 bin/test_legacy_names.py
    # 2026-11-21（261118 反馈单新6／Q189）图片卡片点不开：说明里写了先把项目文件夹加进会话、点不开用 open 在“预览”里弹出
    check("加进会话" in skill and "open -a Preview" in skill, "说明里写了发图片卡片前先把项目文件夹加进会话、点不开用 open -a Preview 弹出")

    # 2026-11-21（261118 反馈单新10／Q193）几个同类小主角：合计面积认主角，箭头指最靠边的一个（离红圈近的那一头）；横版按缩放比例换算门槛
    def lights(n, rx, ry, frame=False, y=900):
        """竖版 1024×1536、四周留纸边的一幅画：灰线房间 + 天花（高度 y）上一排 n 个陶土色筒灯（椭圆，半径 rx×ry）。frame=True 再画一整圈外框。返回 (图, 各灯中心 x)。"""
        im = Image.new("RGB", (1024, 1536), (250, 248, 244)); d_ = ImageDraw.Draw(im); xs_ = [round(1024 * (i + 1) / (n + 1)) for i in range(n)]
        d_.rectangle((160, 1000, 860, 1420), outline=(90, 90, 90), width=4); d_.line((160, 1000, 70, y - 50), fill=(90, 90, 90), width=4); d_.line((860, 1000, 950, y - 50), fill=(90, 90, 90), width=4)
        for x_ in xs_:
            d_.ellipse((x_ - rx, y - ry, x_ + rx, y + ry), fill=(200, 110, 80), outline=(90, 90, 90), width=3)
        if frame:
            d_.rectangle((10, 10, 1013, 1525), outline=(70, 70, 70), width=3)
        return im, xs_
    mid = lambda b: (b[0] + b[2]) / 2
    im4, xs4 = lights(4, 42, 21); a4 = np.asarray(im4); one4 = np.pi * 42 * 21 / (1024 * 1536)
    b0, bl, br = dc.subject_box(a4), dc.subject_box(a4, near=(0, 600)), dc.subject_box(a4, near=(1024, 600))
    check(one4 < dc.SUBJECT_MIN / 2 and b0 is not None and abs(mid(bl) - xs4[0]) < 8 and abs(mid(br) - xs4[-1]) < 8 and abs(mid(b0) - xs4[-1]) < 8,
          f"多个小主角 4 个筒灯各占 {one4:.2%}（单个不够 0.4%）合计认主角；指离箭头出发点近的那一头，没给出发点指右头")
    check(dc.subject_box(a4, near=(500, 600)) in (bl, br), "多个小主角 出发点在正上方时也只指两头的，不指中间的")
    check(dc.subject_box(np.asarray(lights(1, 42, 21)[0])) is None, "多个小主角 只有一个小色块、面积不够时照旧认不出（会报警）")
    big, _ = lights(1, 120, 70); ImageDraw.Draw(big).ellipse((120, 880, 200, 920), fill=(200, 110, 80))        # 一个大主角 + 一件上色的小物件
    check(abs(mid(dc.subject_box(np.asarray(big), near=(0, 900))) - 512) < 8 and dc.subject_box(np.asarray(big)) == dc.subject_box(np.asarray(big), near=(0, 900)),
          "多个小主角 一大一小时照旧只认大的那块（小物件不算同类主角）")
    src_ = Image.new("RGB", (1024, 1536))
    check(dc.min_frac(src_, max(1080 / 1024, 1440 / 1536), 1080, 1440) == dc.SUBJECT_MIN and abs(dc.min_frac(src_, 0.6, 1920, 1080) - 0.004 * 0.36 * 1024 * 1536 / (1920 * 1080)) < 1e-9,
          "认主角门槛 竖版底图排 3:4 还是 0.4%，横版复用缩到 6 成时按面积比例降低")
    def runs(mask):
        """蒙版里最长的一段横向连续像素。"""
        best = 0
        for row in mask:
            i = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
            best = max(best, int((i[1::2] - i[0::2]).max()) if len(i) else 0)
        return best
    fill = np.array(dc.to_blue(Image.new("RGB", (4, 4), (200, 110, 80))).getpixel((0, 0)))                    # 筒灯换色后的蓝（比标题的深蓝浅）
    ml = Path(t) / "lights"; run(ml, "init", "--line1", "为什么外地", "--line2", "灯光设计", "--line3", "我不接了", "--circle", "我不接了", "--scene", "x", "--subject", "y")
    im4.save(ml / "_制作文件/bg_portrait.png")
    r = run(ml, "render", "--ratios", "all"); ok = r.returncode == 0 and r.stdout.count("封面已出") == 3
    check(ok and "警告" not in r.stdout, "多个小主角 三种比例都出图、没有警告（不报“没找到陶土色主角”）" + ("" if ok else "：" + r.stderr[-300:]))
    for q in (json.loads((ml / "_制作文件/qc_3x4_4x3_16x9.json").read_text()) if ok else []):
        im = np.asarray(Image.open(q["file"]).convert("RGB")).astype(int); b = q["subject_box"]; tx, ty = q["target"]
        lm = np.abs(im - fill).max(axis=2) < 18
        check(b is not None and near(im, tx - 60, ty - 60, tx + 60, ty + 6, (226, 84, 44)) > 150 and runs(lm[b[1]: b[3], : b[0] - 10]) < 15 and runs(lm[b[1]: b[3], b[2] + 10:]) >= 15
              and near(im, b[2] + 10, b[1], im.shape[1], b[3], (226, 84, 44)) == 0,
              f"多个小主角 {q['ratio']} 红箭头指到最左边（靠红圈那头）的筒灯，不横穿另外几个")
    s1 = Path(t) / "smallone"; run(s1, "init", "--line1", "烟道这样包", "--line2", "住进去就反味", "--sub", "老房翻新必看", "--scene", "x", "--subject", "y")
    lights(1, 60, 38, y=600)[0].save(s1 / "_制作文件/bg_portrait.png")                                         # 单个主角占竖版底图 0.46%，刚过门槛；画高，横版要缩小
    r = run(s1, "render", "--ratios", "all"); qs = json.loads((s1 / "_制作文件/qc_3x4_4x3_16x9.json").read_text()) if r.returncode == 0 else []
    share = [(q["subject_box"][2] - q["subject_box"][0]) * (q["subject_box"][3] - q["subject_box"][1]) / np.prod(sizes[q["ratio"]]) for q in qs if q["subject_box"]]
    check(len(share) == 3 and "没找到陶土色主角" not in r.stdout and max(share[1:]) < dc.SUBJECT_MIN,
          "认主角门槛 横版复用竖版画、主角缩到不足成图 0.4% 时仍认得出、画出箭头（" + "、".join(f"{v:.2%}" for v in share) + "）")
    lights(1, 42, 21)[0].save(s1 / "_制作文件/bg_portrait.png")
    r = run(s1, "render", "--ratios", "all"); check(r.stdout.count("没找到陶土色主角") == 3, "认主角门槛 底图里主角本来就太小（0.18%）时三种比例照旧报警")

    # 2026-11-21（261118 反馈单新11／Q194）底图被画了整圈外框：出图后、排版时都报警（不拦）；线稿提示词写明不要外框、四周留纸边
    fr = Path(t) / "framed"; run(fr, "init", "--line1", "为什么外地", "--line2", "灯光设计", "--line3", "我不接了", "--circle", "我不接了", "--scene", "x", "--subject", "y")
    fbg = fr / "_制作文件/bg_portrait.png"; lights(4, 60, 38, frame=True)[0].save(fbg); okbg = ml / "_制作文件/bg_portrait.png"
    two = Image.new("RGB", (1024, 1536), (250, 248, 244)); ImageDraw.Draw(two).line((20, 20, 20, 1500, 1010, 1500), fill=(90, 90, 90), width=4); two.save(Path(t) / "two.png")
    check(dc.frame_sides(fbg) == ["上", "下", "左", "右"], "外框 四边各有一条连到头的线时认出整圈外框")
    check(not any(dc.frame_sides(p) for p in (okbg, sample / "bg_portrait.png", sample / "bg_landscape.png", root / "samples/水电四宫格/bg_grid.png")) and len(dc.frame_sides(Path(t) / "two.png")) == 2,
          "外框 不带框的底图（含满幅场景的样例、四宫格）不误报；只有两边有长线（地面线、墙线）不算整圈")
    r = run(fr, "render", "--ratios", "3:4"); q = json.loads((fr / "_制作文件/qc_3x4.json").read_text())[0] if r.returncode == 0 else {"warnings": []}
    check(r.returncode == 0 and "警告：底图被画了一圈外框" in r.stdout and any("外框" in w for w in q["warnings"]), "外框 带外框的底图排版时报警（照样出图，警告记进 qc）")
    lt = [p.read_text() for p in tks if "photo" not in p.name]
    check(len(lt) >= 6 and all("Do NOT draw any frame, border" in v and "paper margins on all four sides" in v for v in lt), "外框 线稿生图任务单都写明不要外框、四周留纸边")
    import os
    fake = Path(t) / "fakebin"; fake.mkdir()          # 假的 codex：不联网、不生图，只把准备好的图拷到任务单写的路径，用来测 gen 出图后的自查
    (fake / "fake.py").write_text("import os, re, shutil, sys\ntask = re.search(r'Read (.+) and do the task', sys.argv[-1]).group(1)\n"
                                  "shutil.copy(os.environ['FAKE_BG'], re.search(r'Save the PNG exactly at: (.+)', open(task).read()).group(1).strip())\n")
    (fake / "codex").write_text(f'#!/bin/sh\nexec "{sys.executable}" -B "{fake / "fake.py"}" "$@"\n'); (fake / "codex").chmod(0o755)
    def gen(d, bg): return subprocess.run([sys.executable, "-B", str(S), "gen", str(d), "portrait"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                          env=dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ.get('PATH', '')}", FAKE_BG=str(bg)))
    shutil.copy(fbg, Path(t) / "framed.png"); shutil.copy(okbg, Path(t) / "clean.png")
    r1, r2 = gen(fr, Path(t) / "framed.png"), gen(ml, Path(t) / "clean.png")
    check(r1.returncode == 0 and "底图已生成" in r1.stdout and "警告：底图被画了一圈外框" in r1.stdout, "外框 gen 出图后马上自查，带外框就报警（用假的 codex，不真生图）" + ("" if r1.returncode == 0 else "：" + r1.stderr[-300:]))
    check(r2.returncode == 0 and "底图已生成" in r2.stdout and "警告" not in r2.stdout, "外框 gen 出的底图不带外框时不报警")
# 2026-11-21 体检：字体按名字核对序号，文件没了说清楚；弃用的仿宋字体不再跟着仓库走
import importlib.util
spec_ = importlib.util.spec_from_file_location("thinkcut_cover_fonts", S); dc = importlib.util.module_from_spec(spec_); spec_.loader.exec_module(dc)
check(dc.font(dc.SONG, 20, 6).getname() == ("Songti SC", "Regular") and dc.font(dc.SONG, 20, 3).getname() == ("Songti SC", "Light")
      and dc.font(dc.HEI, 20, 2).getname() == ("Hiragino Sans GB", "W6"), "字体 标题宋体常规、细体和副标题粗黑体取到的就是要的那三款")
dc._FONT_IDX.clear(); dc.FONT_WANT[(dc.SONG, 0)] = ("Songti SC", "Light")     # 假装系统更新后序号变了：0 号不再是要的那款
check(dc.font_index(dc.SONG, 0) == 3, "字体 序号对不上时按字体名在文件里找到正确的序号")
dc.FONT_WANT[(dc.SONG, 1)] = ("没有这款", "Regular")
try: dc.font_index(dc.SONG, 1); ok_ = False
except SystemExit as e: ok_ = "找不到“没有这款 Regular”" in str(e)
check(ok_, "字体 文件里按名字也找不到时停下并说明是哪一款")
try: dc.font_index("/没有/这个字体.ttc", 0); ok_ = False
except SystemExit as e: ok_ = "缺字体文件" in str(e) and "字体册" in str(e)
check(ok_, "字体 字体文件不在时停下并告诉去“字体册”下载")
check(not (root / "skill/thinkcut-cover/assets/STFANGSO.ttf").exists() and "STFANGSO" not in S.read_text(encoding="utf-8"), "弃用的仿宋字体（约 16MB）已不在仓库里，程序里也不再提它")
print("全部测试通过" if not fails else f"有 {len(fails)} 项失败")
sys.exit(1 if fails else 0)
PY
