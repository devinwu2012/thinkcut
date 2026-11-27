#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""挪进回收站（2026-11-27 对话框用；不是永久删除）。

  python3 bin/thinkcut_trash.py 路径 [路径 …]

每个文件或文件夹挪到它所在那块盘的回收站 /Volumes/<盘>/#recycle/ThinkCut/对话挪走/<原名>_<时间>（同一块盘只是换位置、不复制），
能在访达里拖回来。回收站不在、不在同一块盘：停下不挪。环境变量 THINKCUT_RECYCLE_ROOT 可指定回收站（测试用）。
后台 AI 跑它时交给审片台代办（回收站在项目文件夹以外，AI 自己写不了；代办只收项目里的路径）。
"""
import json
import os
import sys
import time
from pathlib import Path

BOX = 'ThinkCut/对话挪走'


def recycle_root(p):
    if os.environ.get('THINKCUT_RECYCLE_ROOT'): return Path(os.environ['THINKCUT_RECYCLE_ROOT'])
    parts = Path(p).resolve().parts
    return Path('/', parts[1], parts[2], '#recycle') if len(parts) > 3 and parts[1] == 'Volumes' else None


def trash(p):
    src = Path(p).expanduser()
    if not src.exists() and not src.is_symlink(): raise RuntimeError(f'找不到：{src}')
    src = src.parent.resolve() / src.name
    rb = recycle_root(src)
    if not rb or not rb.is_dir(): raise RuntimeError(f'没找到回收站（{rb or "这个文件不在网络盘上"}），没挪')
    box = rb / BOX; box.mkdir(parents=True, exist_ok=True)
    if os.stat(box).st_dev != os.stat(src.parent).st_dev: raise RuntimeError(f'回收站 {rb} 和 {src} 不在同一块盘上，挪过去等于复制，没挪')
    stamp = time.strftime('%Y%m%d-%H%M%S'); to = box / f'{src.name}_{stamp}'
    k = 2
    while to.exists(): to = box / f'{src.name}_{stamp}-{k}'; k += 1
    os.rename(src, to)
    return {'原位置': str(src), '回收站里': str(to)}


def main(argv):
    if not argv: print('用法：thinkcut_trash.py 路径 [路径 …]', file=sys.stderr); return 2
    out, code = [], 0
    for a in argv:
        try: out.append(trash(a))
        except (RuntimeError, OSError) as e: print('停止：' + str(e), file=sys.stderr); code = 1
    if out: print(json.dumps(out, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent)); import thinkcut_broker; thinkcut_broker.hook(__file__)      # noqa: E402  后台 AI：交给审片台代办
    sys.exit(main(sys.argv[1:]))
