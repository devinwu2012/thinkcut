// ===== 工具：确定性随机、噪声、缓动 =====
const U = {};
U.clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
U.lerp = (a, b, t) => a + (b - a) * t;
U.inv = (a, b, x) => U.clamp((x - a) / (b - a));
U.smooth = t => { t = U.clamp(t); return t * t * (3 - 2 * t); };
U.ease = t => { t = U.clamp(t); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; };
U.sine = t => { t = U.clamp(t); return -(Math.cos(Math.PI * t) - 1) / 2; };
U.out = t => { t = U.clamp(t); return 1 - Math.pow(1 - t, 3); };
U.back = t => { t = U.clamp(t); const c = 1.4; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };
// 区间内的进度：t 在 [a,b] 中走 0→1，带缓动
U.win = (t, a, b, e = U.ease) => e(U.inv(a, b, t));
// 出现-保持-消失 包络
U.env = (t, a, b, fi = 0.4, fo = 0.4) => Math.min(U.smooth((t - a) / fi), U.smooth((b - t) / fo));
U.hash = n => { n = Math.sin(n * 127.1 + 311.7) * 43758.5453123; return n - Math.floor(n); };
U.hash2 = (a, b) => U.hash(a * 57.31 + b * 91.17);
U.rng = seed => { let s = seed >>> 0 || 1; return () => { s += 0x6D2B79F5; let t = s; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; }; };

// 梯度噪声（Perlin 型，比值噪声更圆润、近看不出方格），输出约 0..1
U.makeNoise = seed => {
  const r = U.rng(seed), perm = new Uint8Array(512), gx = new Float32Array(256), gy = new Float32Array(256);
  const p = [...Array(256).keys()];
  for (let i = 255; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [p[i], p[j]] = [p[j], p[i]]; }
  for (let i = 0; i < 512; i++) perm[i] = p[i & 255];
  for (let i = 0; i < 256; i++) { const a = r() * Math.PI * 2; gx[i] = Math.cos(a); gy[i] = Math.sin(a); }
  const fade = t => t * t * t * (t * (t * 6 - 15) + 10);
  const n = (x, y) => {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi, X = xi & 255, Y = yi & 255;
    const h00 = perm[X + perm[Y]], h10 = perm[X + 1 + perm[Y]], h01 = perm[X + perm[Y + 1]], h11 = perm[X + 1 + perm[Y + 1]];
    const d00 = gx[h00] * xf + gy[h00] * yf, d10 = gx[h10] * (xf - 1) + gy[h10] * yf;
    const d01 = gx[h01] * xf + gy[h01] * (yf - 1), d11 = gx[h11] * (xf - 1) + gy[h11] * (yf - 1);
    const u = fade(xf), v = fade(yf);
    const a = d00 + (d10 - d00) * u, b = d01 + (d11 - d01) * u;
    return 0.5 + (a + (b - a) * v) * 0.75;
  };
  n.fbm = (x, y, oct = 4, lac = 2, gain = 0.5) => {
    let s = 0, amp = 0.5, norm = 0, X = x, Y = y;
    for (let i = 0; i < oct; i++) {
      s += amp * n(X + i * 17.3, Y + i * 9.1); norm += amp; amp *= gain;
      const nx = (X * 0.8 - Y * 0.6) * lac, ny = (X * 0.6 + Y * 0.8) * lac; X = nx; Y = ny;   // 每层旋转，消除方向感
    }
    return s / norm;
  };
  return n;
};
// 平滑噪声（随时间的晃动用）
U.wob = (t, seed, f = 1) => Math.sin(t * f * 1.7 + seed * 6.1) * 0.5 + Math.sin(t * f * 2.9 + seed * 3.7) * 0.3 + Math.sin(t * f * 4.3 + seed * 1.3) * 0.2;
