// 用无头 Chrome 打开网页，按时间取帧。
// 静帧：node tools/cap.mjs --q "shot=A" --t 3,5 --out 审核/x   → 审核/x_3.jpg …
// 视频：node tools/cap.mjs --q "ui=0" --video 成片.mp4 --fps 30000/1001 [--from 0 --to 180] [--only 1.5-4.1,20.8-22.0]
//   --only：只画这些时间段（秒），其余帧用黑帧占位（达芬奇里只取动画镜头，省时间）；--w 出图宽度（4K：3:4/9:16 用 2160，16:9 用 3840）
//   --frames N：正好出 N 帧（按帧号算，不用秒数四舍五入）；--frame-timeout 秒：单帧超时就退出（退出码 3），外面换新 Chrome 重来
// 2026-11-12（水电反馈新11⑥）：连续出 4K 约 300 帧会无声卡死 → 外面（anim.py）每 ≤120 帧开一个新 Chrome、整段限时；
//   首帧全黑 → 根因是镜头起点按帧四舍五入后，第 1 帧的时刻比镜头开始早几毫秒，网页按“还没进镜头”画了黑帧：
//   --min-t 秒：早于这个时刻的帧按这个时刻画（anim.py 传镜头起点）；另外就绪后先把第一帧画一遍丢掉（预热）。
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { findChrome, chromeCandidates, NO_CHROME } from './chrome.mjs';

const A = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, arr) => (v.startsWith('--') && a.push([v.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : '1']), a), []));
const HERE = dirname(fileURLToPath(import.meta.url));
const page = A.page ? resolve(A.page) : resolve(HERE, '..', '动画.html');   // --page：指定网页（anim.py 统一用这份 cap.mjs 去截各条老片子工程的 动画.html；老代码动画模板 2026-11-21 下线后它搬到 scripts/tools/）
const W = +(A.w || 1920);
const url = pathToFileURL(page).href + '?' + (A.q || '') + (A.w ? `&w=${W}` : '');   // 不给 --w 就按画幅默认宽度
const CHROME = findChrome();      // 先找 /Applications/Google Chrome.app（和以前一样），没有再找别的常见位置
if (!CHROME) { console.error(NO_CHROME + chromeCandidates().join('、')); process.exit(1); }
const port = A.port ? +A.port : 9300 + Math.floor(Math.random() * 500);   // --port：几个 Chrome 同时开时由 anim.py 分配，不撞端口（2026-11-21：撞了会截到别的 Chrome 的页面）
const prof = mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'cap-'));
const ch = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`, `--window-size=${W},${Math.round(W * 9 / 16)}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let ws, id = 0; const pend = new Map(); const logs = [];
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pend.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
const FT = +(A['frame-timeout'] || 0) * 1000;
// 计时器 unref（2026-11-21）：原来每帧留一个 30 秒的计时器，截完要空等最后一个到点才退出，每开一次 Chrome 白等 30 秒
const withTimeout = (p) => FT ? Promise.race([p, new Promise((_, rej) => setTimeout(() => { const e = new Error(`单帧超过 ${FT / 1000} 秒没出来`); e.code = 3; rej(e); }, FT).unref())]) : p;
const ev = async (expr) => { const r = await withTimeout(send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 800)); return r.result.value; };
try {
  let tgt;
  for (let k = 0; k < 50; k++) { try { const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); tgt = l.find(x => x.type === 'page'); if (tgt) break; } catch { } await sleep(150); }
  ws = new WebSocket(tgt.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { const p = pend.get(d.id); pend.delete(d.id); d.error ? p.rej(new Error(d.error.message)) : p.res(d.result); }
    else if (d.method === 'Runtime.consoleAPICalled') logs.push(d.params.type + ': ' + d.params.args.map(a => a.value ?? a.description).join(' '));
    else if (d.method === 'Runtime.exceptionThrown') logs.push('EXC: ' + JSON.stringify(d.params.exceptionDetails).slice(0, 600)); };
  await send('Runtime.enable');
  await send('Page.navigate', { url });
  const t0 = Date.now();
  for (;;) { await sleep(200); let ok = false; try { ok = await ev('!!window.__ready'); } catch (e) { if (Date.now() - t0 > 4000) console.error('poll', e.message); } if (ok) break; let er = ''; try { er = await ev('window.__err'); } catch { } if (er) throw new Error('页面报错：' + er); if (Date.now() - t0 > 90000) throw new Error('页面 90 秒没准备好\n' + logs.join('\n')); }
  console.error(`就绪 ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (A.video) {
    const fpsS = String(A.fps || '30000/1001'), [fn, fd] = fpsS.includes('/') ? fpsS.split('/').map(Number) : [+fpsS, 1], fps = fn / fd;
    const from = +(A.from || 0), to = +(A.to ?? (await ev('window.pipe.duration')));
    const n = A.frames ? +A.frames : Math.round((to - from) * fps);
    await ev(`window.pipe.capture(${Math.max(from, +(A['min-t'] || -1)).toFixed(5)}, 'jpeg')`); await sleep(100);   // 预热：第一帧先画一遍丢掉（贴图、着色器编译完再截）
    const only = A.only ? A.only.split(',').map(r => r.split('-').map(Number)) : null;
    const want = (t) => !only || only.some(([a, b]) => t >= a - 1 / fps && t < b + 1 / fps);
    let black = null;
    const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', fpsS, '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', A.crf || '17', '-preset', 'medium', '-g', String(Math.round(fps)), '-movflags', '+faststart', resolve(A.video)], { stdio: ['pipe', 'inherit', 'inherit'] });
    const ts = Date.now();
    for (let i = 0; i < n; i++) {
      const t = Math.max(from + i * fd / fn, +(A['min-t'] || -1)); let buf;
      if (want(t)) buf = Buffer.from((await ev(`window.pipe.capture(${t.toFixed(5)}, 'jpeg')`)).split(',')[1], 'base64');
      else buf = black ??= Buffer.from((await ev(`(() => { const c = document.createElement('canvas'); const o = document.getElementById('ov'); c.width = o.width; c.height = o.height; const x = c.getContext('2d'); x.fillStyle = '#000'; x.fillRect(0, 0, c.width, c.height); return c.toDataURL('image/jpeg', 0.9); })()`)).split(',')[1], 'base64');
      if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
      if (i % 150 === 0) console.error(`帧 ${i}/${n}  ${((Date.now() - ts) / 1000).toFixed(0)}s`);
    }
    ff.stdin.end(); await new Promise(r => ff.on('close', r));
    console.error('视频完成', A.video);
  } else {
    const out = A.out || 'frame'; mkdirSync(dirname(resolve(out)), { recursive: true });
    const tl = (A.t || '3').split(','); await ev(`window.pipe.capture(${tl[0]})`);   // 预热
    for (const t of tl) {
      const d = await ev(`window.pipe.capture(${t})`);
      writeFileSync(`${out}_${t}.jpg`, Buffer.from(d.split(',')[1], 'base64'));
    }
  }
  if (logs.length) console.error(logs.slice(0, 20).join('\n'));
} catch (e) { console.error('失败：', e.message); if (logs.length) console.error(logs.join('\n')); process.exitCode = e.code === 3 ? 3 : 1; }
finally { try { ws?.close(); } catch { } ch.kill(); await sleep(300); rmSync(prof, { recursive: true, force: true }); }
