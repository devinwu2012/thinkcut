// 距离圈（2026-11-21《装修最后那点细节》B01/B03 提炼，新3）：讲“异地、多城市、离得多远”用它，不画带边界的地图。
// 用户原话：“最好是简洁点，不要把地图画那么明显……能看清楚是个异地的感觉就行。”（标准地图要审图号，改淡、裁切、放大都算编辑要送审。）
// 画法：圆心城市一个蓝点，外面几圈虚线距离圈（1000 km、2000 km…）；目标城市按真实的大圆距离和初始方位角摆（正北朝上，半径和距离成正比）；
//   说到哪个城市，从圆心飞出一条弧线、小飞机沿线飞，落点亮起、旁边出标签卡（城市＋一行小字，默认“直线约 N km”）。不画国界、省界、海岸线、地形。
// 用法（在镜头文件里 共用/基础.js 之后引入本文件）：
//   const R = THINKCUT.distanceRing(tl, { center: "北京", cities: [{ name: "武汉", title: "武汉 · 灯光设计", at: THINKCUT.at("wuhan") }, …] });
//   center：圆心城市名（下面城市表里的，省名也认，如“广东”=广州）或 {name, lat, lon}
//   cities：每个 { name 城市名或 {name,lat,lon}, at 这个镜头里第几秒飞出（可写 THINKCUT.at('卡点')）, title 标签卡大字（默认城市名）,
//                 note 小字（默认“直线约 N km”，N 按 50 取整；写 "" 不要小字）, bow 弧线往哪边拱（1 / -1，默认按方位自动）, side 卡片在落点的 "right"/"left"/"below"/"above"（默认右边，右边放不下放左边；城市在圆心正西一带放正下方，免得压住弧线） }
//   可选：origin [x,y] 圆心在设计稿的位置、scale 每公里几个设计稿像素（都不写就自动：所有城市连同标签卡放进 box 里）；
//         box [左,上,右,下] 自动摆放时可用的范围（默认画面区四周留 60，底部留 300 给字幕）；rings 距离圈公里数（默认按最远城市自动 1000/2000…）；
//         start 圆心和距离圈出现的时间（默认 0.05 秒）；fly 弧线飞多久（默认 0.5 秒）；parent 放进哪个元素（默认 #design）
//   返回 R：{ K 比例, origin, rings, cities:[{name, km, bearing, x, y, side, box 卡片的[左,上,右,下], dot, tag, path, plane}], pulse(城市名, 秒) 落点放大闪一下并给卡片描蓝边 }
//   自动摆放只保证城市和卡片在 box 里、卡片互相不压、刻度字不压卡片；卡片压到别的弧线或想换构图时，出样张看了再用 origin / scale / side / bow 手调。
//   R.geo(a, b) / THINKCUT.geo(a, b)：两城之间的 {km 大圆距离, bearing 初始方位角（度，正北 0、顺时针）}。
(function () {
  const D = window.THINKCUT;
  if (!D) throw new Error("距离圈.js 要放在 共用/基础.js 后面引入");
  // 常用城市（市中心大致经纬度；省名指省会，直辖市、港澳台指本市）。不在表里的写 {name, lat, lon}。
  const CITY = {
    北京: [39.90, 116.40], 天津: [39.13, 117.20], 上海: [31.23, 121.47], 重庆: [29.56, 106.55],
    石家庄: [38.04, 114.51], 太原: [37.87, 112.55], 呼和浩特: [40.84, 111.75], 沈阳: [41.80, 123.43], 大连: [38.91, 121.61],
    长春: [43.82, 125.32], 哈尔滨: [45.80, 126.53], 南京: [32.06, 118.80], 苏州: [31.30, 120.58], 无锡: [31.49, 120.31],
    杭州: [30.27, 120.16], 宁波: [29.87, 121.54], 合肥: [31.82, 117.23], 福州: [26.07, 119.30], 厦门: [24.48, 118.09],
    南昌: [28.68, 115.86], 济南: [36.65, 117.12], 青岛: [36.07, 120.38], 郑州: [34.75, 113.62], 武汉: [30.59, 114.31],
    长沙: [28.23, 112.94], 广州: [23.13, 113.26], 深圳: [22.54, 114.06], 珠海: [22.27, 113.58], 东莞: [23.02, 113.75],
    佛山: [23.02, 113.12], 南宁: [22.82, 108.37], 桂林: [25.27, 110.29], 海口: [20.04, 110.20], 三亚: [18.25, 109.51],
    成都: [30.57, 104.07], 贵阳: [26.65, 106.63], 昆明: [25.04, 102.71], 大理: [25.61, 100.27], 拉萨: [29.65, 91.14],
    西安: [34.34, 108.94], 兰州: [36.06, 103.83], 西宁: [36.62, 101.78], 银川: [38.49, 106.23], 乌鲁木齐: [43.83, 87.62],
    香港: [22.32, 114.17], 澳门: [22.20, 113.55], 台北: [25.03, 121.57],
  };
  const PROV = { 河北: "石家庄", 山西: "太原", 内蒙古: "呼和浩特", 辽宁: "沈阳", 吉林: "长春", 黑龙江: "哈尔滨", 江苏: "南京", 浙江: "杭州",
    安徽: "合肥", 福建: "福州", 江西: "南昌", 山东: "济南", 河南: "郑州", 湖北: "武汉", 湖南: "长沙", 广东: "广州", 广西: "南宁", 海南: "海口",
    四川: "成都", 贵州: "贵阳", 云南: "昆明", 西藏: "拉萨", 陕西: "西安", 甘肃: "兰州", 青海: "西宁", 宁夏: "银川", 新疆: "乌鲁木齐", 台湾: "台北" };
  const RAD = Math.PI / 180;
  const place = (c) => {
    if (c && typeof c === "object") { if (!(isFinite(c.lat) && isFinite(c.lon))) throw new Error("距离圈：城市 " + c.name + " 要写 lat、lon"); return { name: c.name, lat: c.lat, lon: c.lon }; }
    const k = CITY[c] ? c : PROV[c];
    if (!k) throw new Error("距离圈：城市表里没有「" + c + "」，写成 {name: \"" + c + "\", lat: 纬度, lon: 经度}");
    return { name: c, lat: CITY[k][0], lon: CITY[k][1] };
  };
  // 大圆距离（km，地球平均半径 6371.0088）和初始方位角（度，正北 0、顺时针）
  const geo = (a, b) => {
    a = place(a); b = place(b);
    const p1 = a.lat * RAD, p2 = b.lat * RAD, dl = (b.lon - a.lon) * RAD;
    const km = 2 * 6371.0088 * Math.asin(Math.sqrt(Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2));
    const br = Math.atan2(Math.sin(dl) * Math.cos(p2), Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)) / RAD;
    return { km, bearing: (br + 360) % 360 };
  };
  D.geo = geo; D.cities = CITY;

  const css = document.createElement("style");
  css.textContent = `
    .dr-ov { position: absolute; left: 0; top: 0; overflow: visible; }
    .dr-arc { fill: none; stroke: var(--blue); stroke-width: 4px; stroke-linecap: round; }
    .dr-lead { stroke: rgba(255, 255, 255, 0.45); stroke-width: 2px; }
    .dr-circ { position: absolute; border-radius: 50%; border: 2px dashed rgba(255, 255, 255, 0.26); }
    .dr-km { position: absolute; transform: translate(-50%, -50%); padding: 2px 10px; background: var(--bg); font-size: 24px; font-weight: 500; color: var(--dim); white-space: nowrap; }
    .dr-pin { position: absolute; width: 0; height: 0; }
    .dr-dot { position: absolute; left: -11px; top: -11px; width: 22px; height: 22px; border-radius: 50%; background: var(--blue);
      box-shadow: 0 0 0 9px rgba(100, 169, 232, 0.16), 0 0 26px 6px rgba(100, 169, 232, 0.25); }
    .dr-name { position: absolute; font-size: 34px; font-weight: 600; white-space: nowrap; }
    .dr-plane { position: absolute; left: 0; top: 0; width: 36px; height: 36px; offset-anchor: 50% 50%; offset-rotate: auto; opacity: 0; }
    .dr-plane svg { width: 36px; height: 36px; display: block; } .dr-plane path { fill: var(--blue); }
    .dr-tag { position: absolute; padding: 12px 20px; border-radius: 14px; background: #24272D; border: 2px solid rgba(255, 255, 255, 0.14); white-space: nowrap; }
    .dr-tag .t { font-size: 36px; font-weight: 600; line-height: 46px; }
    .dr-tag .s { font-size: 24px; font-weight: 500; line-height: 32px; color: var(--dim); }`;
  document.head.appendChild(css);
  const PLANE = '<svg viewBox="0 0 24 24"><path d="M21.5 12c0-1-1-1.5-2-1.5h-5L10 4H8l2.5 6.5H6L4.5 8.5H3l1 3.5-1 3.5h1.5L6 13.5h4.5L8 20h2l4.5-6.5h5c1 0 2-.5 2-1.5z"/></svg>';
  const NS = "http://www.w3.org/2000/svg";

  D.distanceRing = function (tl, o) {
    const parent = o.parent || document.getElementById("design"), W = D.designW, H = D.designH;
    const C = place(o.center), list = o.cities || [];
    if (!list.length) throw new Error("距离圈：cities 至少写一个城市");
    const box = o.box || [60, 60, W - 60, H - 300], TAGW = 330, TAGH = 92;
    const cs = list.map((c) => { const p = place(c.name), g = geo(C, p); return Object.assign({}, c, { name: p.name, km: g.km, bearing: g.bearing,
      ux: Math.sin(g.bearing * RAD) * g.km, uy: -Math.cos(g.bearing * RAD) * g.km }); });
    // 自动比例和圆心：所有城市（连同右边一张标签卡的宽度）放进 box；只写一个也行
    let K = o.scale, O = o.origin;
    if (!K || !O) {
      const xs = [0, ...cs.map((c) => c.ux)], ys = [0, ...cs.map((c) => c.uy)];
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      const bw = box[2] - box[0] - TAGW, bh = box[3] - box[1] - TAGH;
      K = K || Math.min(bw / Math.max(x1 - x0, 1), bh / Math.max(y1 - y0, 1), Math.min(W, H) * 0.6 / Math.max(...cs.map((c) => c.km)));
      O = O || [box[0] + (bw - (x1 - x0) * K) / 2 - x0 * K, box[1] + TAGH / 2 + (bh - (y1 - y0) * K) / 2 - y0 * K];
    }
    const maxKm = Math.max(...cs.map((c) => c.km));
    const step = o.rings ? null : [500, 1000, 2000, 5000].find((s) => maxKm / s <= 3.2) || 5000;
    const rings = o.rings || Array.from({ length: Math.ceil(maxKm / step - 0.1) }, (_, i) => (i + 1) * step);
    const div = (cls, html, p) => { const e = document.createElement("div"); e.className = cls; if (html) e.innerHTML = html; (p || parent).appendChild(e); return e; };
    const at = (el, x, y) => { el.style.left = x + "px"; el.style.top = y + "px"; return el; };
    const svgEl = (tag, attrs, p) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); p.appendChild(e); return e; };
    const ringBox = div(""), ov = svgEl("svg", { class: "dr-ov", width: W, height: H }, parent), over = div("");
    [ringBox, over].forEach((e) => Object.assign(e.style, { position: "absolute", left: 0, top: 0, width: W + "px", height: H + "px" }));
    const gArc = svgEl("g", {}, ov), gLead = svgEl("g", {}, ov);
    cs.forEach((c) => { c.x = O[0] + c.ux * K; c.y = O[1] + c.uy * K; });
    // 标签卡先摆好（刻度字要躲它）：默认在落点右边，右边放不下放左边；城市在圆心正西一带时放落点正下方（放右边会压住从圆心出来的弧线，
    //   261118 B03 的甘肃就是这么摆的）。两张卡片压住了，靠下的那张再往下挪。
    const hit = (a, b, m) => a[0] < b[2] + m && b[0] < a[2] + m && a[1] < b[3] + m && b[1] < a[3] + m;
    const placed = [];
    cs.slice().sort((a, b) => a.y - b.y).forEach((c) => {
      const note = c.note == null ? `直线约 ${Math.round(c.km / 50) * 50} km` : c.note;
      c.tag = div("dr-tag", `<div class="t">${c.title || c.name}</div>` + (note ? `<div class="s">${note}</div>` : ""), over);
      const tw = c.tag.offsetWidth || TAGW, th = c.tag.offsetHeight || TAGH;
      const west = c.ux < 0 && Math.abs(c.ux) > Math.abs(c.uy);
      c.side = c.side || (west ? "below" : c.x + 52 + tw <= W - 20 ? "right" : "left");
      if (!["right", "left", "below", "above"].includes(c.side)) throw new Error("距离圈：城市 " + c.name + " 的 side 只能写 right / left / below / above");
      const tx = c.side === "right" ? c.x + 52 : c.side === "left" ? c.x - 52 - tw : Math.max(20, Math.min(W - 20 - tw, c.x - 60));
      let ty = c.side === "below" ? c.y + 40 : c.side === "above" ? c.y - 40 - th : c.y - 40;
      for (let again = true; again;) {
        again = false;
        placed.forEach((b) => { if (hit([tx, ty, tx + tw, ty + th], b, 12)) { ty = c.side === "above" ? b[1] - 12 - th : b[3] + 12; again = true; } });
      }
      c.box = [tx, ty, tx + tw, ty + th]; placed.push(c.box);
      at(c.tag, tx, ty); c.tag.style.opacity = 0;
    });
    // 距离圈和刻度字：刻度字放在离城市方位最远、在画面里、又不压标签卡和圆心城市名的方向
    const gap = (deg) => Math.min(...cs.map((c) => { const d = Math.abs(((deg - c.bearing) % 360 + 540) % 360 - 180); return d; }));
    const busy = placed.concat([[O[0] - 20, O[1] - 40, O[0] + 130, O[1] + 30]]);
    const R = rings.map((km) => {
      const r = km * K, c = div("dr-circ", "", ringBox);
      Object.assign(c.style, { left: O[0] - r + "px", top: O[1] - r + "px", width: 2 * r + "px", height: 2 * r + "px", opacity: 0 });
      let best = null;
      for (let deg = 0; deg < 360; deg += 15) {
        const x = O[0] + r * Math.sin(deg * RAD), y = O[1] - r * Math.cos(deg * RAD);
        if (x < 70 || x > W - 70 || y < 30 || y > H - 30) continue;
        if (busy.some((b) => hit([x - 60, y - 20, x + 60, y + 20], b, 8))) continue;
        if (!best || gap(deg) > best.g) best = { g: gap(deg), x, y };
      }
      const l = best ? at(div("dr-km", km + " km", ringBox), best.x, best.y) : null;
      if (l) l.style.opacity = 0;
      return { km, c, l };
    });
    const dot = (x, y) => { const g = at(div("dr-pin", "", over), x, y); div("dr-dot", "", g); g.style.opacity = 0; return g; };
    const cDot = dot(O[0], O[1]), cName = at(div("dr-name", C.name, over), O[0] + 30, O[1] - 22); cName.style.opacity = 0;
    cs.forEach((c) => {
      // 弧线：三次贝塞尔，控制点在两端连线三分之一、三分之二处往一侧拱；默认往卡片的反方向拱
      const dx = c.x - O[0], dy = c.y - O[1], len = Math.hypot(dx, dy) || 1, nx = -dy / len, ny = dx / len;
      const toCard = { right: [1, 0], left: [-1, 0], below: [0, 1], above: [0, -1] }[c.side];
      const bow = c.bow || (nx * toCard[0] + ny * toCard[1] <= 0 ? 1 : -1), h = Math.min(len * 0.22, 160) * bow;
      const d = `M${O[0]} ${O[1]} C${O[0] + dx / 3 + nx * h} ${O[1] + dy / 3 + ny * h} ${O[0] + dx * 2 / 3 + nx * h} ${O[1] + dy * 2 / 3 + ny * h} ${c.x} ${c.y}`;
      c.path = svgEl("path", { class: "dr-arc", d, pathLength: 1, "stroke-dasharray": "1 2", "stroke-dashoffset": 1 }, gArc);
      c.plane = div("dr-plane", PLANE, over); c.plane.style.offsetPath = `path("${d}")`; c.plane.style.offsetDistance = "0%";
      c.dot = dot(c.x, c.y);
      // 引线：左右的卡片一条横线（卡片被挪开时斜着连过去），上下的一条竖线
      const b = c.box, line = c.side === "right" ? [c.x + 14, c.y, b[0], b[1] + 40] : c.side === "left" ? [c.x - 14, c.y, b[2], b[1] + 40]
        : c.side === "below" ? [c.x, c.y + 14, c.x, b[1]] : [c.x, c.y - 14, c.x, b[3]];
      c.lead = svgEl("line", { class: "dr-lead", x1: line[0], y1: line[1], x2: line[2], y2: line[3], opacity: 0 }, gLead);
    });

    // ===== 时间轴 =====
    const E = "power2.out", t0 = o.start == null ? 0.05 : o.start, fly = o.fly || 0.5;
    tl.fromTo(cDot, { opacity: 0, scale: 0.5 }, { opacity: 1, scale: 1, duration: 0.4, ease: E }, t0);
    tl.fromTo(cName, { opacity: 0 }, { opacity: 1, duration: 0.4, ease: E }, t0 + 0.1);
    R.forEach((r, i) => {
      tl.fromTo(r.c, { opacity: 0, scale: 0.6 }, { opacity: 1, scale: 1, duration: 0.7, ease: E }, t0 + 0.15 + i * 0.3);
      if (r.l) tl.fromTo(r.l, { opacity: 0 }, { opacity: 1, duration: 0.4, ease: E }, t0 + 0.55 + i * 0.3);
    });
    cs.forEach((c) => {
      if (!isFinite(c.at)) throw new Error("距离圈：城市 " + c.name + " 要写 at（第几秒飞出，可写 THINKCUT.at('卡点')）");
      tl.to(c.path, { attr: { "stroke-dashoffset": 0 }, duration: fly, ease: "power2.inOut" }, c.at);
      tl.fromTo(c.plane, { offsetDistance: "0%" }, { offsetDistance: "100%", duration: fly, ease: "power2.inOut" }, c.at);
      tl.fromTo(c.plane, { opacity: 0 }, { opacity: 1, duration: 0.1, ease: "none" }, c.at);
      tl.to(c.plane, { opacity: 0, duration: 0.2, ease: "power2.in" }, c.at + fly - 0.05);
      const land = c.at + fly;
      tl.fromTo(c.dot, { opacity: 0, scale: 0.5 }, { opacity: 1, scale: 1, duration: 0.35, ease: E }, land - 0.08);
      tl.to(c.lead, { attr: { opacity: 1 }, duration: 0.3, ease: E }, land);
      tl.fromTo(c.tag, { opacity: 0, x: c.side === "right" ? -10 : c.side === "left" ? 10 : 0 }, { opacity: 1, x: 0, duration: 0.4, ease: E }, land);
    });
    const pulse = (name, t) => {
      const c = cs.find((x) => x.name === name);
      if (!c) throw new Error("距离圈：没有城市 " + name);
      tl.to(c.dot, { scale: 1.8, duration: 0.2, ease: E }, t);
      tl.to(c.dot, { scale: 1, duration: 0.35, ease: "power2.inOut" }, t + 0.2);
      tl.to(c.tag, { borderColor: "rgba(100,169,232,1)", duration: 0.3, ease: E }, t);
    };
    return { K, origin: O, rings: R, cities: cs, pulse, geo };
  };
})();
