#!/usr/bin/env python3
"""一键导出“不带我个人信息的干净版本”（2026-11-24 用户要）：给别人用、或备份一份可以公开的程序。

  python3 bin/export_clean.py <目标文件夹>          # 目标文件夹不能已存在
  python3 bin/export_clean.py <目标文件夹> --list   # 只列会带走/不带的，不写文件

做法：
  1. 只取已存档的当前文件（git 快照）：**不带 git 历史**（提交说明里有片子名、原话、人名），也不带没存档的东西（素材库、环境、钥匙、本机.json 本来就不进存档）。
  2. 去掉只属于自己的：所有 IP 的配置档（只留 公共.json 和 示例IP/），内部/（交接、台账、反馈单），各 CHANGELOG.md，经验/，
     内部维护工具（发版、占位、仓库合并、开发版小工具），旧名兼容（bin/旧名兼容.sh、它的测试、仓库里给旧名字留的软链接——仓库里的软链接都是这个用途），
     以及下面 DROP 里列的文件。默认 IP 改成 示例IP。
  3. 导出后在新文件夹里查一遍还剩哪些个人痕迹（人名、盘符路径、IP 名、片子编号），按文件列出来——这些是写在说明正文里、要人改写的，脚本不替你改。
清单依据 内部/开源版不能带出去的东西.md（“说明搬家”盘点）和 内部/配置档设计.md 第 5 节。
"""
import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DROP_DIRS = ('内部/', '口播自动剪辑/经验/', '口播剪辑/经验/')
DROP_FILES = ('bin/release.py', 'bin/test_release.py', 'bin/merge_once.py', 'bin/test_merge_once.py', 'bin/placeholder.py',
              'bin/test_placeholder.py', 'bin/dev.sh', 'bin/旧名兼容.sh', 'bin/test_旧名兼容.py', '架构审查交接.md', '口播自动剪辑/skill/thinkcut-edit/assets/包装系统/人挡字样张/调研和定稿过程.md')
KEEP_PROFILES = ('配置档/公共.json', '配置档/示例IP/')
# 导出后要查的个人痕迹：名字 → 正则
MARKS = {'人名': r'De' r'vin',      # 拆开写，免得查到这个文件自己
         '盘符路径': r'/Volumes/work|/Users/[A-Za-z]', '片子编号': r'(?<!\d)26[01]\d{3}(?!\d)',
         '旧名字': r'大' r'喜|da' r'xi|DA' r'XI'}      # 改名 ThinkCut 前的专属名字（拆开写，同上）


def dropped(path, ips):
    if path.startswith('配置档/'):
        return not (path == KEEP_PROFILES[0] or path.startswith(KEEP_PROFILES[1]))
    return path.startswith(DROP_DIRS) or path in DROP_FILES or path.rsplit('/', 1)[-1] == 'CHANGELOG.md'


def tracked(repo=ROOT):
    """存档里的文件（不含软链接：仓库里的软链接只给改名前的旧名字留兼容入口，干净版不带）。"""
    out = subprocess.run(['git', '-c', 'core.quotepath=off', 'ls-tree', '-r', '-z', 'HEAD'], cwd=repo, capture_output=True, check=True).stdout
    rows = [x.split('\t', 1) for x in out.decode('utf-8').split('\0') if x]
    return [p for meta, p in rows if not meta.startswith('120000')]


def ip_names(repo=ROOT):
    d = repo / '配置档'
    return sorted(p.name for p in d.iterdir() if p.is_dir() and p.name != '示例IP') if d.is_dir() else []


def export(dest, repo=ROOT, list_only=False, log=print):
    dest = Path(dest).expanduser().resolve()
    if not list_only and dest.exists(): raise SystemExit(f'停止：{dest} 已经存在，换一个新文件夹名（不会覆盖）')
    if repo.resolve() in [dest, *dest.parents]: raise SystemExit('停止：目标文件夹不能放在程序目录里面')
    ips = ip_names(repo)
    files = tracked(repo)
    keep = [p for p in files if not dropped(p, ips)]
    log(f'存档里 {len(files)} 个文件：带走 {len(keep)} 个，不带 {len(files) - len(keep)} 个（不带 git 历史）')
    if list_only:
        for p in files:
            if dropped(p, ips): log('  不带 ' + p)
        return None
    tar = subprocess.run(['git', 'archive', '--format=tar', 'HEAD', '--', *keep], cwd=repo, capture_output=True, check=True).stdout
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dest)
    pub = dest / '配置档' / '公共.json'
    if pub.is_file():
        data = json.loads(pub.read_text(encoding='utf-8')); data['默认IP'] = '示例IP'
        pub.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    gi = dest / '.gitignore'
    if gi.is_file():
        gi.write_text(gi.read_text(encoding='utf-8') + '# 自己的 IP 配置档建在这里，分享前用 bin/export_clean.py 导出\n', encoding='utf-8')
    marks = dict(MARKS, **({'IP 名': '|'.join(map(re.escape, ips))} if ips else {}))
    left = scan(dest, marks)
    log(f'已导出到 {dest}（默认 IP 改成 示例IP；新建自己的 IP：python3 bin/cutdesk_config.py new 名字）')
    if left:
        log('还剩这些个人痕迹（写在正文里，要人改写；脚本没改）：')
        for kind, rows in left.items():
            log(f'  {kind}：{sum(n for _, n in rows)} 处，{len(rows)} 个文件；最多的：' + '、'.join(f'{p}（{n}）' for p, n in rows[:5]))
    else:
        log('没查到人名、盘符路径、IP 名、片子编号')
    return {'dest': str(dest), 'files': len(keep), 'left': {k: sum(n for _, n in v) for k, v in left.items()}}


def scan(dest, marks):
    left = {}
    for p in sorted(dest.rglob('*')):
        if not p.is_file(): continue
        try: text = p.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError): text = None
        rel = str(p.relative_to(dest))
        for kind, rx in marks.items():
            n = len(re.findall(rx, text)) if text is not None else 0
            n += len(re.findall(rx, rel))
            if n: left.setdefault(kind, []).append((rel, n))
    return {k: sorted(v, key=lambda x: -x[1]) for k, v in left.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description='导出不带个人信息的干净版本（不带 git 历史、不带自己的 IP 配置档和内部记录）')
    ap.add_argument('dest'); ap.add_argument('--list', action='store_true', help='只列会带走、不带的文件，不写')
    a = ap.parse_args(argv)
    export(a.dest, list_only=a.list)
    return 0


if __name__ == '__main__':
    sys.exit(main())
