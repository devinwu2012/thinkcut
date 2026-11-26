#!/usr/bin/env python3
"""Mac 程序外壳 Cutdesk.app 的启动／退出逻辑（2026-11-24 用户定：做成本地 Mac 程序，外壳套住现有网页界面，不内嵌模型、不碰账号）。

外壳（bin/app/ThinkCut.swift）只管窗口；“首页服务在不在、要不要起、退出时停不停”都在这里判断，方便离线测试。只用 Python 3.9 标准库。

  python3 bin/thinkcut_app.py start [--port 8760] [--root 放片子的文件夹]
      首页已在这个端口跑 → 直接用（started=false，退出时不停它）；
      没在跑 → 用 thinkcut_edit.py home 起一个（started=true，记下 pid）；
      起不来 → ok=false，error 是一句人话，log 是日志位置。
      只打印一行 JSON：{"ok", "url", "started", "pid", "error", "log"}
  python3 bin/thinkcut_app.py stop --port 8760 --pid 123
      只停自己起的那个：端口上的首页 pid 还是 123 才停；各片子的审片台不停。
"""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROGRAM = Path(__file__).resolve().parents[1]
THINKCUT_EDIT = PROGRAM / 'studio' / 'skill' / 'thinkcut-edit' / 'scripts' / 'thinkcut_edit.py'
PORT = 8760
# 从程序坞打开的程序拿不到终端里的 PATH：把 Homebrew 的位置补上，首页替片子起的审片台要用 ffmpeg 这些
_H = str(Path.home())
EXTRA_PATH = ('/opt/homebrew/bin', '/usr/local/bin', _H + '/.local/bin', _H + '/.local/node-system/bin', _H + '/.claude/local', _H + '/.npm-global/bin',
              _H + '/.bun/bin', _H + '/.volta/bin')      # 程序坞打开时 PATH 很短：Homebrew 和用户自己装的命令行（claude、codex、yxer、yxer-safe）都补上（2026-11-26 设置页显示“未装”）


def ping(port, timeout=0.5):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=timeout) as r: return json.loads(r.read())
    except Exception: return None


def port_busy(port):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s.bind(('127.0.0.1', port)); return False
        except OSError: return True


def mine(prog_dir):
    """首页 ping 里的程序文件夹是不是这一份程序里的。"""
    try: Path(prog_dir).resolve().relative_to(PROGRAM.resolve()); return True
    except (ValueError, OSError): return False


def log_path(port):
    return Path(os.environ.get('TMPDIR', '/tmp')) / f'ThinkCut首页_{port}.log'      # 和 home.start 写的是同一份


def child_env():
    env = {**os.environ, 'THINKCUT_NO_OPEN': '1', 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    have = env.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin').split(':')
    env['PATH'] = ':'.join([p for p in EXTRA_PATH if p not in have] + have)
    return env


def start(port=PORT, root=None, script=THINKCUT_EDIT, python=sys.executable, wait=15.0):
    url = f'http://127.0.0.1:{port}/'; out = {'ok': False, 'url': url, 'started': False, 'pid': None, 'error': '', 'log': ''}
    pg = ping(port)
    if pg and pg.get('app') == '首页':
        if root and pg.get('root') != str(Path(root).expanduser().resolve()):
            out['error'] = f"{port} 端口上已经有一个首页，看的是 {pg.get('root')}，不是 {root}。先关掉它，或换一个端口。"; return out
        other = (pg.get('prog') or {}).get('dir')
        if other and not mine(other):          # 别的程序文件夹（开发版／某个 worktree）起的首页：不复用，免得用的是哪份代码没人知道（QA 第一节）
            out['error'] = f"{port} 端口上的首页是另一份程序起的（{other}），不是这一份（{PROGRAM}）。先关掉它，或换一个端口。"; return out
        out.update(ok=True, pid=pg.get('pid')); return out
    if pg or port_busy(port):
        out['error'] = f'{port} 端口被别的程序占着（不是首页）。关掉那个程序，或换一个端口再打开。'; return out
    if not Path(script).is_file():
        out['error'] = f'找不到程序文件 {script}。程序文件夹可能挪过地方：重新构建一次外壳（bash bin/build_app.sh）。'; return out
    cmd = [python, '-B', str(script), 'home', '--port', str(port)] + (['--root', str(root)] if root else [])
    log = log_path(port); out['log'] = str(log)
    code = 1
    try:
        r = subprocess.run(cmd, cwd=str(PROGRAM), env=child_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=wait + 15)
        said = (r.stdout + r.stderr).strip(); code = r.returncode
    except subprocess.TimeoutExpired:
        said = '启动命令一直没回来'
    except OSError as e:
        said = f'运行不了 {python}：{e}'
    t0 = time.time()
    while code == 0 and time.time() - t0 < wait:          # home 命令自己会等服务起来再返回；这里只是再确认一下
        pg = ping(port, 0.3)
        if pg and pg.get('app') == '首页':
            out.update(ok=True, started=True, pid=pg.get('pid')); return out
        time.sleep(0.2)
    last = [x for x in said.splitlines() if x.strip()][-3:]
    out['error'] = '首页没起来。' + ('程序说：' + ' '.join(last) if last else '')
    return out


def stop(port, pid):
    """只停自己起的：端口上的首页还是这个 pid 才停。返回一句话。"""
    pg = ping(port)
    if not pg or pg.get('app') != '首页': return '首页已经不在了'
    if not pid or pg.get('pid') != pid: return '端口上的首页不是这个程序起的，不停'
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: return '首页已经不在了'
    for _ in range(50):
        if not ping(port, 0.2): return '已停'
        time.sleep(0.1)
    return '发了停止信号，还没停下'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Cutdesk.app 外壳用：起／停首页服务')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('start'); s.add_argument('--port', type=int, default=PORT); s.add_argument('--root')
    t = sub.add_parser('stop'); t.add_argument('--port', type=int, default=PORT); t.add_argument('--pid', type=int, required=True)
    a = ap.parse_args(argv)
    if a.cmd == 'start': print(json.dumps(start(a.port, a.root), ensure_ascii=False)); return 0
    print(stop(a.port, a.pid)); return 0


if __name__ == '__main__':
    sys.exit(main())
