// 找无头截图用的浏览器（2026-11-21 体检：以前 cap.mjs 写死 /Applications/Google Chrome.app，装在别处就起不来、也不说原因）。
// 依次找：Chrome（/Applications、~/Applications）→ Chromium → Edge；都没有返回 null，由 cap.mjs 报错。
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export const chromeCandidates = (home = homedir()) => [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  join(home, 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  join(home, 'Applications/Chromium.app/Contents/MacOS/Chromium'),
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  join(home, 'Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
];
export const findChrome = (cands = chromeCandidates(), exists = existsSync) => cands.find(p => exists(p)) || null;
export const NO_CHROME = '停止：找不到 Chrome，出不了动画截图：在 /Applications 里安装 Google Chrome（Chromium、Microsoft Edge 也可以）后再跑。找过：';
