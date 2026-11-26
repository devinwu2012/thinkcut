#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开发版和使用版各在哪（2026-11-26 用户定：开发资料都放 Dev/ThinkCut/，开发版不再是使用版的兄弟目录）。

开发版的认法不变：程序文件夹名以“-开发”结尾，或是 git worktree（.git 是一个文件）。
开发版找使用版：看开发版主仓库的 git origin（开发版就是从使用版克隆的，origin 是使用版的本地路径）；没有再按旧规矩找兄弟目录（名字去掉“-开发”）。
使用版找开发版：环境变量 THINKCUT_DEV_ROOT ＞ 使用版 profiles/本机.json 的 "开发版" ＞ <使用版上一级>/Dev/ThinkCut/ThinkCut-开发 ＞ 旧的兄弟目录 <使用版>-开发。
相对路径都按使用版所在的文件夹算。

  python3 bin/locations.py roots [<程序文件夹>]   打印三行：开发版、使用版（找不到是空行）、是不是开发版（1/0）
  python3 bin/locations.py prod  [<程序文件夹>]   打印使用版位置
  python3 bin/locations.py dev   [<程序文件夹>]   打印开发版位置
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SUFFIX = '-开发'
DEFAULT_DEV = Path('Dev') / 'ThinkCut' / 'ThinkCut-开发'      # 相对使用版的上一级
HERE = Path(__file__).resolve().parents[1]


def is_dev(root):
    root = Path(root)
    return root.name.endswith(SUFFIX) or (root / '.git').is_file()


def main_repo(root):
    """worktree 给它的主仓库；普通仓库给它自己。"""
    root = Path(root); git = root / '.git'
    try:
        if git.is_file():       # 一行 “gitdir: <主仓库>/.git/worktrees/<名>”
            line = git.read_text(encoding='utf-8').strip()
            if line.startswith('gitdir:'):
                p = Path(line[7:].strip())
                return (p if p.is_absolute() else (root / p)).resolve().parents[2]
    except (OSError, IndexError):
        pass
    return root


def _is_repo(p): return p is not None and (p / '.git').exists()


def prod_of(dev):
    """开发版（或它的 worktree）对应的使用版；找不到给 None。"""
    main = main_repo(dev)
    r = subprocess.run(['git', '-C', str(main), 'config', '--get', 'remote.origin.url'], capture_output=True, text=True)
    url = r.stdout.strip() if r.returncode == 0 else ''
    if url and '://' not in url and not url.startswith('git@'):
        p = Path(url[7:] if url.startswith('file://') else url).expanduser()
        p = p if p.is_absolute() else (main / p).resolve()
        if p.is_dir(): return p
    if main.name.endswith(SUFFIX):
        p = main.parent / main.name[:-len(SUFFIX)]
        if p.is_dir(): return p
    return None


def _local_setting(prod):
    try: v = json.loads((Path(prod) / 'profiles' / '本机.json').read_text(encoding='utf-8')).get('开发版')
    except (OSError, ValueError, AttributeError): return None
    if not isinstance(v, str) or not v.strip(): return None
    p = Path(v).expanduser()
    return p if p.is_absolute() else (Path(prod) / p).resolve()


def dev_of(prod):
    """使用版对应的开发版：按顺序找第一个是仓库的；都没有就给默认位置（dev.sh setup 会在那里建）。"""
    prod = Path(prod)
    if os.environ.get('THINKCUT_DEV_ROOT'): return Path(os.environ['THINKCUT_DEV_ROOT'])
    tries = [_local_setting(prod), prod.parent / DEFAULT_DEV, prod.parent / (prod.name + SUFFIX)]
    for p in tries:
        if _is_repo(p): return p
    return next(p for p in tries if p is not None)


def roots(here=HERE):
    """(开发版, 使用版, 这里是不是开发版)。THINKCUT_DEV_ROOT、THINKCUT_PROD_ROOT 都设了就用它们（离线测试）。"""
    here = Path(here)
    dev, prod = os.environ.get('THINKCUT_DEV_ROOT'), os.environ.get('THINKCUT_PROD_ROOT')
    if dev and prod: return Path(dev), Path(prod), is_dev(here)
    if is_dev(here):
        p = Path(prod) if prod else prod_of(here)
        return Path(dev) if dev else here, p, True      # worktree 就是它自己（dev.sh run 要跑这一份的程序）
    return dev_of(here), Path(prod) if prod else here, False


def main(argv):
    what = argv[0] if argv else 'roots'
    here = Path(argv[1]).resolve() if len(argv) > 1 else HERE
    d, p, isdev = roots(here)
    if what == 'roots': print(f'{d}\n{p or ""}\n{int(isdev)}')
    elif what == 'prod': print(p or '')
    elif what == 'dev': print(d)
    else: print(__doc__); return 2
    return 0 if (p or what == 'dev') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
