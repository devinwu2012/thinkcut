// README 截图的浏览器那一半：无头 Chrome（DevTools 协议，不装别的包），按 bin/readme_shots.py 给的清单逐张截图。
// 也出配图（job.art：本地 HTML 设计稿的路径列表，每个文件一张）。用法见 bin/readme_shots.py；单独跑：node bin/readme_shots.mjs '<JSON>'（home、bench、out、shots、themes、width、height、scale）。
// 每张截之前查页面文字：出现真实 IP 名、盘符路径、“开发版”标签就停下（不出图）。
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const { findChrome, chromeCandidates, NO_CHROME } = await import(resolve(HERE, '../studio/skill/thinkcut-edit/scripts/tools/chrome.mjs'));
const job = JSON.parse(process.argv[2] || '{}');
const W = job.width || 1440, H = job.height || 900, S = job.scale || 2;
const FORBID = new RegExp(['大' + '囍', '大' + '喜', 'da' + 'xi', '/Volumes/', '/Users/', '开发版'].join('|'), 'i');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const bench = col => job.bench + '#' + encodeURIComponent(col);

// 每张：打开哪一页、截之前做什么（在页面里跑的 JS 或 Node 这边的动作）、截完怎么收
const SHOTS = {
  'home': { url: () => job.home, ready: '!document.body.innerText.includes("正在读取项目")', wait: 1500 },
  'home-list': { url: () => job.home, ready: '!document.body.innerText.includes("正在读取项目")', before: [['click', 'button', '列表']], wait: 1200, after: [['click', 'button', '看板']] },
  'script': { url: () => bench('写稿'), wait: 2500 },
  'talk-edit': { url: () => bench('文案'), wait: 6000 },
  'animation': { url: () => bench('动画'), wait: 3500, before: [['js', 'for (const v of document.querySelectorAll("video")) if (v.duration) v.currentTime = Math.min(2.6, v.duration * 0.6)']], settle: 1200 },
  'review': { url: () => bench('审核线'), wait: 3000 },
  'cover': { url: () => bench('封面'), wait: 3000 },
  'publish': { url: () => bench('上传文案'), wait: 2500 },
  'settings': { url: () => job.home + 'settings', wait: 3000 },
  'chat': { url: () => bench('写稿'), wait: 2000, before: [['click', 'button', '对话']], after: [['click', 'button', '对话']] },
  // 会改演示数据（意见交给假 AI）：放最后；第二套颜色不再提交，只换颜色重截
  'ai-working': { url: () => bench('审核线'), wait: 2500, before: [['click', 'button', '提交 AI（']], settle: 4500 },
};

const CHROME = findChrome();
if (!CHROME) { console.error(NO_CHROME + chromeCandidates().join('、')); process.exit(1); }
const port = 9800 + Math.floor(Math.random() * 150);
const prof = mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'readme-shots-'));
const ch = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`, `--window-size=${W},${H}`, '--hide-scrollbars',
  '--no-first-run', '--no-default-browser-check', '--lang=zh-CN', 'about:blank'], { stdio: 'ignore' });
let ws, id = 0; const pend = new Map(); const waiters = [];
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pend.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
const until = async (expr, ms = 15000) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await ev(expr)) return true; } catch { } await sleep(200); } return false; };

async function open(url) {
  await send('Page.navigate', { url: 'about:blank' }); await sleep(150);
  const loaded = new Promise(r => waiters.push(r));
  await send('Page.navigate', { url });
  await Promise.race([loaded, sleep(15000)]);
}

async function act([kind, sel, text]) {
  if (kind === 'js') { await ev(`(() => { ${sel}; return 1 })()`); }
  else if (kind === 'click') {
    const ok = await ev(`(() => { const t = ${JSON.stringify(text)}; const el = [...document.querySelectorAll(${JSON.stringify(sel)})]
      .find(e => e.offsetParent && e.textContent.trim().startsWith(t) && !e.disabled); if (el) el.click(); return !!el; })()`);
    if (!ok) throw new Error(`页面上找不到能点的“${text}”`);
  }
  await sleep(600);
}

async function theme(name) {
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: name }, { name: 'prefers-reduced-motion', value: 'reduce' }] });
}

async function capture(name, th) {
  const text = await ev('document.body.innerText');
  const bad = text.match(FORBID);
  if (bad) throw new Error(`${name}（${th}）页面上有不该公开的字：“${bad[0]}”`);
  await ev('document.activeElement && document.activeElement.blur && document.activeElement.blur(), getSelection().removeAllRanges(), 1');
  await sleep(300);
  const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const f = join(job.out, `${name}-${th}.png`); writeFileSync(f, Buffer.from(r.data, 'base64')); console.log('  ' + f);
}

let code = 0;
try {
  let tgt;
  for (let k = 0; k < 60; k++) { try { tgt = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(x => x.type === 'page'); if (tgt) break; } catch { } await sleep(150); }
  if (!tgt) throw new Error('Chrome 没起来');
  ws = new WebSocket(tgt.webSocketDebuggerUrl); await new Promise(r => ws.onopen = r);
  ws.onmessage = m => { const d = JSON.parse(m.data);
    if (d.id && pend.has(d.id)) { const p = pend.get(d.id); pend.delete(d.id); d.error ? p.rej(new Error(d.error.message)) : p.res(d.result); }
    else if (d.method === 'Page.loadEventFired') waiters.splice(0).forEach(r => r()); };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: S, mobile: false });
  if (job.art) {        // 配图：本地 HTML 设计稿，每个文件一张，尺寸读页面里的 --w/--h
    for (const th of job.themes || ['light', 'dark']) {
      await theme(th); console.log(`${th}：`);
      for (const f of job.art) {
        await open(pathToFileURL(f).href); await ev('document.fonts.ready.then(() => 1)'); await sleep(400);
        const [w, h] = await ev('["--w","--h"].map(k => parseInt(getComputedStyle(document.documentElement).getPropertyValue(k)))');
        await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: S, mobile: false }); await sleep(500);
        const name = f.split('/').pop().replace(/\.html$/, '');
        const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
        const out = join(job.out, `${name}-${th}.png`); writeFileSync(out, Buffer.from(r.data, 'base64')); console.log('  ' + out);
      }
    }
  }
  const names = job.art ? [] : (job.shots || Object.keys(SHOTS)).filter(n => SHOTS[n]);
  const normal = names.filter(n => n !== 'ai-working');
  for (const th of normal.length ? job.themes || ['light', 'dark'] : []) {
    await theme(th); console.log(`${th}：`);
    for (const n of normal) {
      const s = SHOTS[n]; await open(s.url());
      if (s.ready) await until(s.ready);
      await sleep(s.wait || 1500);
      await ev('(document.getElementById("aiX")?.click(), 1)');      // 上一次 AI 处理完的小卡（用户点 × 就收起）
      for (const a of s.before || []) await act(a);
      await sleep(s.settle || 0);
      await capture(n, th);
      for (const a of s.after || []) await act(a);
    }
  }
  if (names.includes('ai-working')) {
    const s = SHOTS['ai-working']; let first = true;
    for (const th of job.themes || ['light', 'dark']) {
      await theme(th);
      if (first) { await open(s.url()); await sleep(s.wait); for (const a of s.before) await act(a); await sleep(s.settle); first = false; }
      else await sleep(800);
      await capture('ai-working', th);
    }
  }
} catch (e) { console.error('截图停下：' + e.message); code = 2; }
finally { try { ch.kill(); } catch { } await sleep(300); rmSync(prof, { recursive: true, force: true }); }
process.exit(code);
