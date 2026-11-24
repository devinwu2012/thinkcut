// HyperFrames 镜头共用脚本：按 出片.py 填好的 window.THINKCUT 摆好画面区，并提供卡点换算。
// window.THINKCUT = { shot, a, b, dur, fps, W, H, k, card:[x,y,w,h], anchors:{卡点名: 成片秒}, transparent }
//   a/b：这个镜头在成片里的起止秒；dur：镜头时长；k：设计稿像素到出片像素的倍数（4K=2，预览=1）；card：画面区在整帧里的位置（出片像素）
(function () {
  const D = window.THINKCUT;
  if (!D) throw new Error("没有 window.THINKCUT：镜头文件要用 出片.py 生成 index.html 后再出片或预览，不能直接打开");
  const [x, y, w, h] = D.card;
  const stage = document.getElementById("stage"), design = document.getElementById("design");
  Object.assign(stage.style, { left: x + "px", top: y + "px", width: w + "px", height: h + "px" });
  Object.assign(design.style, { width: w / D.k + "px", height: h / D.k + "px", transform: "scale(" + D.k + ")" });
  if (D.transparent) document.getElementById("root").classList.add("transparent");
  D.designW = w / D.k; D.designH = h / D.k;      // 设计稿上画面区的宽高，排版时可以用
  // 卡点名 → 这个镜头里的第几秒。卡点不存在、不在这个镜头的起止里（加上 offset 后也算）直接报错，不悄悄当 0、不悄悄截掉。
  D.at = function (name, offset) {
    if (!(name in D.anchors)) throw new Error("分镜.json 里没有卡点「" + name + "」");
    const t = D.anchors[name] - D.a + (offset || 0);
    if (t < 0 || t > D.dur) throw new Error("卡点「" + name + "」在成片 " + D.anchors[name] + " 秒" + (offset ? "（加 " + offset + " 秒）" : "") +
      "，不在镜头 " + D.shot + " 的起止 " + D.a + "–" + D.b + " 秒里：改卡点或镜头起止，不要用别的镜头的卡点");
    return t;
  };
  // 把时间轴补到镜头结尾（不然 HyperFrames 认为片子在最后一个动作处就结束）。动作排到镜头结尾以后（超过 1 帧）直接报错，不悄悄截掉。
  D.pad = function (tl) {
    if (tl.duration() > D.dur + 1 / D.fps) throw new Error("镜头 " + D.shot + " 的时间轴排到第 " + tl.duration().toFixed(3) + " 秒，镜头只有 " +
      D.dur.toFixed(3) + " 秒（成片 " + D.a + "–" + D.b + " 秒）：结尾的动作会被截掉，把动作挪进镜头里或改镜头起止");
    tl.set({}, {}, D.dur); return tl;
  };
  // 标签（2026-11-21 新14，用户：“为什么不直接就一个引线上去，还得拐个弯？明明直接上去更直观，拐个弯更容易重叠”）：
  //   字放在目标点正上方，引线一条竖线直上直下，不拐弯。D.label(tl, {x, y, text, from, to, up})
  //   x、y：目标点（设计稿像素，画面区左上角是 0,0）；from、to：这个镜头里第几秒出现、消失（可写 D.at('卡点')，to 不写到镜头结尾）；up：引线长（默认 120）。
  //   同一画面要标两个东西时，第二个比第一个晚出现（先后出现，不同时弹出来，见 动画风格.md）。
  //   这个镜头盖了实拍的那几段（出片.py 填进 D.covered，镜头里的秒）标签自动藏起来（新15）。
  D.covered = D.covered || [];
  D.label = function (tl, o) {
    const up = o.up == null ? 120 : o.up, from = o.from || 0, to = o.to == null ? D.dur : o.to;
    if (!(to - from >= 0.7)) throw new Error("标签「" + o.text + "」出现到消失只有 " + (to - from).toFixed(2) + " 秒：from 要早于 to（标签至少停 2.5 秒，见 动画风格.md）");
    const mk = (cls) => { const e = document.createElement("div"); e.className = cls; return e; };
    const g = mk("brand-label"), ring = mk("ring"), line = mk("line"), text = mk("text");
    Object.assign(g.style, { left: o.x + "px", top: o.y + "px" }); line.style.height = Math.max(0, up - 13) + "px";
    text.style.bottom = up + 6 + "px"; text.textContent = o.text;
    g.appendChild(ring); g.appendChild(line); g.appendChild(text); (o.parent || design).appendChild(g);
    tl.fromTo(g, { opacity: 0 }, { opacity: 1, duration: 0.4, ease: "power2.out" }, from);
    tl.to(g, { opacity: 0, duration: 0.3, ease: "power2.in" }, to - 0.3);
    D.covered.forEach(([c0, c1]) => {
      if (c1 <= from || c0 >= to) return;
      tl.set(g, { visibility: "hidden" }, Math.max(c0, from));
      if (c1 < to) tl.set(g, { visibility: "visible" }, c1);
    });
    return g;
  };
  window.__timelines = window.__timelines || {};
})();
