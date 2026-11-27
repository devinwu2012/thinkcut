#!/usr/bin/env python3
"""重出 GitHub 介绍（README）用的程序截图：浅色、深色各一套，2 倍高清，窗口尺寸统一（2026-11-27 起，发版前由介绍会话跑）。

  python3 bin/readme_shots.py                  # 全部重出到 docs/screenshots/（约 2 分钟）
  python3 bin/readme_shots.py --only home,script   # 只出这几张（名字见下面 SHOTS）
  python3 bin/readme_shots.py serve            # 只把演示首页、审片台起起来（打印网址），手动看或摆演示数据；用完 stop
  python3 bin/readme_shots.py stop
  python3 bin/readme_shots.py save-demo        # 把摆好的演示项目存回“原样”（下次重出从它开始）
  python3 bin/readme_shots.py art <设计源文件夹>  # 重出头图和功能说明图到 docs/images/：先把 docs/screenshots 的截图拷进源文件夹的 截图/，
                                               # 再把里面每个 .html（不以 _ 开头）按页面的 --w/--h 截成 2 倍 PNG，浅色深色各一张

演示数据：一个专门截图用的项目文件夹（默认 <开发资料>/试验项目/截图演示，--demo 换）：
  原样/   演示项目的原样（内容通用、没有真实 IP 名和片子名），每次截图先复制到 项目/ 再起服务，截完不影响原样；
  项目/   截图时首页和审片台读写的那一份；
  配置档/ 截图用的空用户配置档（只看得到程序带的 示例IP）；
  截图.json  {"审片台项目": "<项目文件夹名>"}：审片台各栏截哪个项目。
不碰真片子、不碰这台 Mac 的 ThinkCut 设置：服务用 THINKCUT_APP_FLAVOR=release（没有橙色“开发版”标签）、THINKCUT_APP_DIR 换成单独的
程序数据文件夹、THINKCUT_AI_PATH 指一个假的 Claude Code（只演“AI 正在做什么”、不联网不花钱）。截图由 bin/readme_shots.mjs 驱动无头 Chrome。
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import locations  # noqa: E402

EDIT = ROOT / 'studio/skill/thinkcut-edit/scripts/thinkcut_edit.py'
OUT = ROOT / 'docs/screenshots'
HOME_PORT, BENCH_PORT = 8851, 8852
APP_DIR = 'ThinkCut-截图演示'
WIDTH, HEIGHT, SCALE = 1440, 900, 2
# 名字 → 打开哪一页、截之前做什么（动作在 readme_shots.mjs 里）
SHOTS = ['home', 'home-list', 'script', 'talk-edit', 'animation', 'review', 'cover', 'publish', 'settings', 'chat', 'ai-working']

FAKE_AI = r'''#!/usr/bin/env python3
# 截图用的假 Claude Code：按 FAKE_PLAN（JSON 文件）演一遍；不联网、不花钱
import json, os, re, subprocess, sys, time
a = sys.argv[1:]
if a[:2] == ['auth', 'status']: print(json.dumps({'loggedIn': True})); sys.exit(0)
if a[:2] == ['login', 'status']: print('Logged in using ChatGPT'); sys.exit(0)
def ev(x): print(json.dumps(x, ensure_ascii=False), flush=True)
f = os.environ.get('FAKE_PLAN', '')
plan = json.load(open(f, encoding='utf-8')) if f and os.path.exists(f) else {}
prompt = a[a.index('-p') + 1] if '-p' in a else (a[-1] if a else '')
m = re.search(r'python3 "([^"]+)" workbench "([^"]+)"', prompt)
sid = a[a.index('--session-id') + 1] if '--session-id' in a else (a[a.index('--resume') + 1] if '--resume' in a else 's1')
ev({'type': 'system', 'subtype': 'init', 'session_id': sid})
for t in plan.get('steps', []):
    ev({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': t[0], 'input': t[1]}]}}); time.sleep(plan.get('gap', 0.3))
for args in plan.get('do', []):
    if m: subprocess.run([sys.executable, '-B', m[1], 'workbench', m[2], *args], capture_output=True)
time.sleep(plan.get('hold', 0))
final = plan.get('final', '好了。')
ev({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': final}]}})
ev({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': final, 'session_id': sid})
'''


def demo_dir(arg):
    if arg: return Path(arg).expanduser().resolve()
    return locations.main_repo(ROOT).parent / '试验项目' / '截图演示'


def tmp_dir(demo):
    d = demo / '截图临时'; d.mkdir(exist_ok=True); return d


def env_for(demo):
    t = tmp_dir(demo); fake = t / 'ai'; fake.mkdir(exist_ok=True)
    for n in ('claude', 'codex'):        # 设置页里两个都显示“已登录”；截图只用 Claude
        f = fake / n; f.write_text(FAKE_AI.replace('/usr/bin/env python3', sys.executable), encoding='utf-8'); f.chmod(0o755)
    (demo / '配置档').mkdir(exist_ok=True)
    e = dict(os.environ, THINKCUT_APP_FLAVOR='release', THINKCUT_APP_DIR=APP_DIR, THINKCUT_USER_PROFILES=str(demo / '配置档'),
             THINKCUT_IP='示例IP', THINKCUT_AI_PATH=str(fake), THINKCUT_NO_OPEN='1', FAKE_PLAN=str(t / 'plan.json'), PYTHONDONTWRITEBYTECODE='1')
    for k in ('THINKCUT_VIDEO_ROOT', 'THINKCUT_PROFILES'): e.pop(k, None)
    return e


def bench_dir(demo):
    """审片台各栏截的是哪个演示项目：演示文件夹里 截图.json 的 "审片台项目"（没有就取第一个有审核线的项目）。"""
    try: name = json.loads((demo / '截图.json').read_text(encoding='utf-8'))['审片台项目']
    except (OSError, ValueError, KeyError):
        name = next((p.parent.parent.parent.name for p in sorted((demo / '项目').glob('*/02_制作/自动剪辑/job.json'))), '')
    return demo / '项目' / name / '02_制作' / '自动剪辑'


def up(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1).read(1); return True
        except OSError: time.sleep(0.3)
    return False


def stop(demo):
    e = env_for(demo)
    if bench_dir(demo).is_dir():
        subprocess.run([sys.executable, str(EDIT), 'workbench', str(bench_dir(demo)), '--stop'], env=e, capture_output=True)
    subprocess.run([sys.executable, str(EDIT), 'home', '--stop', '--port', str(HOME_PORT)], env=e, capture_output=True)
    pid = tmp_dir(demo) / 'home.pid'
    if pid.is_file():
        try: os.kill(int(pid.read_text()), signal.SIGTERM)
        except (ValueError, OSError): pass
        pid.unlink()


def rebind_sources(root):
    """复制出来的演示项目：原片的改动时间（ctime）复制后一定变，剪口播的“原片没换过”检查会拦。只在截图用的副本里把记录对上现在的文件。"""
    for m in root.glob('*/02_制作/*/转写/source_manifest.json'):
        rec = json.loads(m.read_text(encoding='utf-8')); src = Path(rec.get('path', ''))
        if not src.is_file(): continue
        st = src.resolve().stat()
        for _ in range(15):         # 网络盘上刚复制的文件，先报本机缓存的改动时间、过几秒才换成盘上的：刚复制过就等它换过来
            if time.time() - st.st_ctime > 60: break
            time.sleep(1); st2 = src.resolve().stat()
            if st2.st_ctime_ns != st.st_ctime_ns: st = st2; break
        rec.update(path=str(src.resolve()), size=st.st_size, mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns)
        m.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding='utf-8')
        ap = m.parent.parent / '审核' / 'delivery_approved_v1' / 'approval_manifest.json'
        if ap.is_file():
            old = json.loads(ap.read_text(encoding='utf-8'))
            old.update(source=rec['path'], source_size=rec['size'], source_mtime_ns=rec['mtime_ns'])
            ap.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding='utf-8')


def serve(demo, fresh=True):
    src = demo / '原样'
    if not src.is_dir(): raise SystemExit(f'停止：没有演示项目原样 {src}（见本文件开头的说明）')
    stop(demo)
    if fresh:
        subprocess.run(['rsync', '-a', '--delete', '--modify-window=2', '--exclude', '.DS_Store', f'{src}/', f'{demo / "项目"}/'], check=True)
    rebind_sources(demo / '项目')
    shutil.rmtree(Path.home() / 'Library/Application Support' / APP_DIR, ignore_errors=True)       # 截图专用的程序数据（AI 设置、首页对话），每次从空的开始
    e = env_for(demo); log = open(tmp_dir(demo) / 'home.log', 'w')
    p = subprocess.Popen([sys.executable, str(EDIT), 'home', '--root', str(demo / '项目'), '--port', str(HOME_PORT)], env=e, stdout=log, stderr=log, start_new_session=True)
    (tmp_dir(demo) / 'home.pid').write_text(str(p.pid))
    r = subprocess.run([sys.executable, str(EDIT), 'workbench', str(bench_dir(demo)), '--port', str(BENCH_PORT)], env=e, capture_output=True, text=True)
    if not (up(HOME_PORT) and up(BENCH_PORT)): stop(demo); raise SystemExit('停止：首页或审片台没起来：\n' + r.stdout[-800:] + r.stderr[-800:])
    return {'home': f'http://127.0.0.1:{HOME_PORT}/', 'bench': f'http://127.0.0.1:{BENCH_PORT}/', 'plan': str(tmp_dir(demo) / 'plan.json')}


def shoot(demo, only, themes, out):
    node = shutil.which('node')
    if not node: raise SystemExit('停止：要 Node 22 或以上（无头 Chrome 截图用）')
    urls = serve(demo)
    try:
        out.mkdir(parents=True, exist_ok=True)
        job = {**urls, 'out': str(out), 'shots': only, 'themes': themes, 'width': WIDTH, 'height': HEIGHT, 'scale': SCALE}
        r = subprocess.run([node, str(ROOT / 'bin/readme_shots.mjs'), json.dumps(job, ensure_ascii=False)], cwd=ROOT)
        if r.returncode: raise SystemExit(f'截图没出齐（退出码 {r.returncode}）')
    finally:
        stop(demo)
    print(f'已出到 {out}：' + '、'.join(f'{s}-{t}.png' for s in only for t in themes))


def art(src, themes, out):
    files = sorted(str(p) for p in src.glob('*.html') if not p.name.startswith('_'))
    if not files: raise SystemExit(f'停止：{src} 里没有设计稿（.html）')
    shots = src / '截图'; shots.mkdir(exist_ok=True)
    for p in OUT.glob('*.png'): shutil.copy2(p, shots / p.name)      # 头图里放的是最新的程序截图
    out.mkdir(parents=True, exist_ok=True)
    job = {'art': files, 'out': str(out), 'themes': themes, 'scale': SCALE}
    r = subprocess.run([shutil.which('node') or 'node', str(ROOT / 'bin/readme_shots.mjs'), json.dumps(job, ensure_ascii=False)], cwd=ROOT)
    return r.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description='重出 README 用的程序截图（浅色、深色各一套，2 倍高清）')
    ap.add_argument('cmd', nargs='?', default='shoot', choices=['shoot', 'serve', 'stop', 'save-demo', 'art'])
    ap.add_argument('src', nargs='?', help='art：设计源文件夹（HTML）')
    ap.add_argument('--demo', help='截图演示文件夹（默认 <开发资料>/试验项目/截图演示）')
    ap.add_argument('--only', help='只出这几张，逗号分隔：' + ','.join(SHOTS))
    ap.add_argument('--theme', default='light,dark', help='light、dark 或两个都要（默认）')
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--keep', action='store_true', help='serve 时不从原样重新复制（接着上次摆的）')
    a = ap.parse_args(argv)
    demo = demo_dir(a.demo)
    if a.cmd == 'stop': stop(demo); print('已停'); return 0
    if a.cmd == 'serve':
        u = serve(demo, fresh=not a.keep)
        print(f'首页 {u["home"]}\n审片台 {u["bench"]}\n假 AI 的剧本：{u["plan"]}\n用完：python3 bin/readme_shots.py stop'); return 0
    if a.cmd == 'save-demo':
        stop(demo)
        subprocess.run(['rsync', '-a', '--delete', '--modify-window=2', '--exclude', '.DS_Store', f'{demo / "项目"}/', f'{demo / "原样"}/'], check=True)
        print('已存回原样'); return 0
    if a.cmd == 'art':
        return art(Path(a.src or '').expanduser().resolve(), [x for x in a.theme.split(',') if x in ('light', 'dark')],
                   Path(a.out).resolve() if a.out != str(OUT) else ROOT / 'docs/images')
    only = [s for s in (a.only.split(',') if a.only else SHOTS) if s]
    bad = [s for s in only if s not in SHOTS]
    if bad: raise SystemExit('不认识：' + '、'.join(bad))
    shoot(demo, only, [t for t in a.theme.split(',') if t in ('light', 'dark')], Path(a.out).resolve())
    return 0


if __name__ == '__main__':
    sys.exit(main())
