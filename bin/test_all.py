#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑全套离线测试（根目录工具＋五块），发版时 bin/release.py 跑的也是这一套。

  python3 bin/test_all.py              全部（约 5 分钟）
  python3 bin/test_all.py cover chapters  只跑这几块（根目录写 .）

不连达芬奇、不开浏览器、不调付费接口；要先放好 素材库/字体/ 和 素材库/音效素材/，装好 requirements/dev.txt。
全过返回 0，有没过的返回 1。跑完还查一遍：测试有没有在程序文件夹里留下没存档的文件（留了也算没过）。
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ('.', 'core', 'studio', 'chapters', 'projects', 'cover')      # 根目录＋五块：一个仓库，测试每个文件夹都跑
FONTS = ('SourceHanSansSC-Heavy.otf', 'SourceHanSansSC-Light.otf', 'SourceHanSerifSC-Heavy.otf', 'Inter/InterDisplay-Black.ttf', 'Inter/InterDisplay-Light.ttf')      # 人挡字字体（studio 测试要用；清单同 bin/fetch_fonts.sh）
LOG_NAME = '发版记录.jsonl'      # 使用版根目录的发版记录（数据，不算测试留下的）


def label(name): return '根目录' if name == '.' else name


def test_commands(repo, name):
    py = sys.executable
    if name == '.':
        cmds = [c for c in ([py, 'bin/test_placeholder.py'], ['bash', 'bin/test_sync_env.sh'], [py, 'bin/test_release.py'], [py, 'bin/test_merge_once.py'], [py, 'bin/test_thinkcut_config.py'], [py, 'bin/test_thinkcut_app.py'], [py, 'bin/test_legacy_names.py'], [py, 'bin/test_design.py']) if (repo / c[1]).exists()]
    elif (repo / 'bin' / 'run_tests.sh').exists(): cmds = [['bash', 'bin/run_tests.sh']]
    elif (repo / 'bin' / 'test_project.py').exists(): cmds = [[py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'bin/test_project.py']]
    else: cmds = []
    return cmds


def leftovers(repo):
    """没存档的改动和新文件（发版记录不算）；不是 git 仓库（比如下载的压缩包）就不查。"""
    r = subprocess.run(['git', '-C', str(repo), '-c', 'core.quotepath=off', 'status', '--porcelain', '--untracked-files=all'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode: return []
    return [x for x in r.stdout.splitlines() if x[3:].strip().strip('"') != LOG_NAME]


def run_tests(dev, say=print, folders=FOLDERS):
    """跑测试：返回没过的 [(文件夹, 命令, 输出末尾)]。测试输出放本机临时文件，跑完删。"""
    before = set(leftovers(dev))      # 开跑前就有的改动（正在改的程序）不算测试留下的
    base = {k: v for k, v in os.environ.items() if k != 'THINKCUT_NO_OPEN'}
    base.update({'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'THINKCUT_APP_FLAVOR': 'release', 'THINKCUT_NO_KEYCHAIN': '1', 'THINKCUT_AI_PATH': ''})      # 也不碰真的 AI 命令行（2026-11-26 AI 引擎）      # 测试不碰真的钥匙串      # 测试在开发版跑，但测的是正式版的读法（开发版会把片子文件夹换成试验田）
    failed = []
    fonts = Path(os.environ.get('THINKCUT_FONT_DIR') or dev / '素材库' / '字体')
    missing = [f for f in FONTS if not (fonts / f).is_file()] if 'studio' in folders and (dev / 'studio/skill/thinkcut-edit/assets/包装系统/rendang.py').is_file() else []      # 只查真有人挡字程序的（发版测试里的假仓库没有）
    if missing:      # 公开版、新克隆没有 素材库/（数据不进存档）：先说清缺什么、怎么补，不然 studio 的人挡字测试半路崩掉、报错看不懂
        say(f'  缺字体  {fonts} 里没有 ' + '、'.join(missing) + '：先跑 bash bin/fetch_fonts.sh（或拷使用版的 素材库/字体/）；studio 的人挡字测试会没过')
        failed.append(('studio', '缺人挡字字体', f'{fonts} 里没有 ' + '、'.join(missing) + '。先跑 bash bin/fetch_fonts.sh（按 sha256 下载核对），或从使用版拷 素材库/字体/'))
    for name in folders:
        repo = dev / name; cmds = test_commands(repo, name)
        env = {**base, 'THINKCUT_NO_OPEN': '1'} if name == 'studio' else base      # 只有它的测试会真去开浏览器；口播剪辑有一项测试专门查“没设这个变量时”的行为，不能带
        if not cmds: failed.append((name, '（没找到测试）', '这个文件夹没有 bin/run_tests.sh，也没有 bin/test_project.py')); continue
        for cmd in cmds:
            t = time.time()
            with tempfile.TemporaryFile() as out:
                code = subprocess.run(cmd, cwd=str(repo), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT).returncode
                out.seek(0); tail = out.read().decode('utf-8', 'replace')[-1500:]
            say(f'  {"通过" if code == 0 else "没过"}  {label(name)}：{" ".join(cmd[1:] if cmd[0] == sys.executable else cmd)}（{time.time() - t:.0f} 秒）')
            if code: failed.append((name, ' '.join(cmd), tail))
    left = [x for x in leftovers(dev) if x not in before]
    if left: failed.append(('.', '测试后检查', '测试在程序文件夹里留下了没存档的文件：' + '、'.join(x[3:] for x in left[:5])))
    return failed


def main(argv):
    names = argv or list(FOLDERS)
    unknown = [n for n in names if n not in FOLDERS]
    if unknown:
        print('没有这块：' + '、'.join(unknown) + '（可选：' + '、'.join(FOLDERS) + '）')
        return 2
    failed = run_tests(ROOT, folders=names)
    if failed:
        print(f'\n没过 {len(failed)} 项：')
        for name, cmd, tail in failed:
            print(f'--- {label(name)}：{cmd}\n{tail}')
        return 1
    print('\n全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
