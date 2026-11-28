#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交反馈单（2026-11-28）：把项目里写好的反馈单放进程序文件夹的 internal/经验/待审/（thinkcut-feedback 第 4 步）。
后台 AI 在沙盒里写不了程序文件夹，这条命令会交给审片台代办；本地对话里也能用。只收 .md、名字照规矩，不覆盖已有的（同名加 _2、_3）。
  python3 bin/thinkcut_feedback.py submit <项目里的反馈单.md>
公开版没有 internal/：不放，告诉你反馈单留在项目里。"""
import argparse
import re
import shutil
import sys
from pathlib import Path

PROGRAM = Path(__file__).resolve().parents[1]
NAME = re.compile(r'\d{4}-\d{2}-\d{2}_.+_反馈单_.+\.md')


def submit(src, program=PROGRAM):
    src = Path(src).expanduser().resolve()
    if not src.is_file(): raise SystemExit(f'停止：没有这个文件：{src}')
    if not NAME.fullmatch(src.name): raise SystemExit(f'停止：反馈单名字要照 YYYY-MM-DD_<片名或主题>_反馈单_<反馈人>.md：{src.name}')
    if not src.read_text(encoding='utf-8').lstrip().startswith('# 反馈单'): raise SystemExit('停止：反馈单第一行要是“# 反馈单：…”')
    if not (program / 'internal').is_dir():
        print(f'这是公开版，没有 internal/：反馈单留在项目里 {src}'); return None
    box = program / 'internal' / '经验' / '待审'; box.mkdir(parents=True, exist_ok=True)
    dst, n = box / src.name, 2
    while dst.exists(): dst = box / f'{src.stem}_{n}{src.suffix}'; n += 1
    shutil.copyfile(src, dst); print(f'已交反馈单：{dst}')
    return dst


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('submit', help='交一张反馈单').add_argument('file')
    a = p.parse_args(argv)
    submit(a.file)
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent)); import thinkcut_broker; thinkcut_broker.hook(__file__)      # noqa: E402  后台 AI：交给审片台代办
    sys.exit(main())
