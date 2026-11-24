#!/usr/bin/env python3
"""配置档：一个 IP 一套配置，“公共层＋每个 IP 自己一层”（2026-11-24 用户定）。五块共用，只用 Python 3.9 标准库。

判断标准（用户）：换一个人用，这个要不要变——不变的是程序，要变的是配置，他做出来的是数据。

合并顺序（后面的盖前面的；字典逐层合并，列表和单个值整个替换）：
  1. 配置档/公共.json            所有 IP 都继承的默认值（跟着程序走，进存档）
  2. 配置档/<IP>/ip.json         这个 IP 和公共层不一样的地方（进存档，发版跟着走）
  3. 配置档/本机.json            这台机器自己的（片子放别处、默认 IP 换人）；不进存档，发版不碰，可以没有
  4. 项目.json 里的 "配置覆盖"    单条片子临时改（可以没有）
给 AI 看的 IP 规矩：配置档/<IP>/规矩.md（技能说明里写“先读当前 IP 的规矩”，用下面的 rules 命令拿）。

用哪个 IP：--ip／环境变量 THINKCUT_IP → 片子的 项目.json 里的 "ip"（从给的文件夹往上找）→ 本机.json／公共.json 的 "默认IP"。
写路径的值：相对路径按程序目录（放 bin/、配置档/ 的这一层）算；里面的 {IP} 换成 IP 名字。

命令：
  python3 bin/cutdesk_config.py show  [--ip 名字] [--project 片子文件夹] [--key 封面.标题色]   看合并后的配置
  python3 bin/cutdesk_config.py rules [--ip 名字] [--project 片子文件夹]                       这个 IP 给 AI 看的规矩（路径＋全文）
  python3 bin/cutdesk_config.py list                                                          有哪些 IP、默认是谁
  python3 bin/cutdesk_config.py new 新名字 [--from 示例IP]                                     新建一个 IP（从示例或现有 IP 复制）
"""
import argparse
import copy
import json
import os
import re
import shutil
import sys
from pathlib import Path

PROGRAM = Path(__file__).resolve().parents[1]        # 程序目录（使用版／开发版／worktree 都一样按自己的位置算）
EXAMPLE = '示例IP'
_MISSING = object()
LEGACY_FILE = PROGRAM / 'bin' / '旧名兼容.sh'


def legacy(path=None):
    """旧名兼容表 {名字: 值}：从改名前的版本升级来的安装才有 bin/旧名兼容.sh（导出的干净版没有，就是空表）。"""
    out = {}
    try: text = Path(path or LEGACY_FILE).read_text(encoding='utf-8')
    except OSError: return out
    for line in text.splitlines():
        m = re.match(r'\s*([A-Z][A-Z0-9_]*)="(?:\$\{\1:-)?([^"}]*)\}?"', line)
        if m: out[m[1]] = m[2]
    return out


def apply_legacy(environ=None, table=None):
    """把旧名兼容表当环境变量的默认值补上（真设了的优先），再让旧前缀和 THINKCUT_ 前缀的环境变量两边互通（新的优先）。导入本模块时自动做一次。"""
    environ = os.environ if environ is None else environ
    table = legacy() if table is None else table
    for k, v in table.items(): environ.setdefault(k, v)
    old = table.get('THINKCUT_LEGACY_ENV_PREFIX')
    if not old: return environ
    for k in list(environ):
        if k.startswith(old): environ.setdefault('THINKCUT_' + k[len(old):], environ[k])
    for k in list(environ):
        if k.startswith('THINKCUT_') and not k.startswith('THINKCUT_LEGACY_'): environ.setdefault(old + k[len('THINKCUT_'):], environ[k])
    return environ


def app_dir():
    """这台 Mac 上放环境运行副本、审片台按空格小服务的文件夹：~/Library/Application Support/ThinkCut（THINKCUT_APP_DIR 可换名字）。"""
    return Path.home() / 'Library' / 'Application Support' / (os.environ.get('THINKCUT_APP_DIR') or 'ThinkCut')


def env_name(kind):
    """环境里 Python 环境的文件夹名：kind＝align（对齐）／matting（抠人）；THINKCUT_ALIGN_ENV／THINKCUT_MATTING_ENV 可换。"""
    return os.environ.get(f'THINKCUT_{kind.upper()}_ENV') or f'thinkcut-{kind}'


apply_legacy()


def profiles_dir():
    """配置档文件夹；THINKCUT_PROFILES 只给离线测试和“把配置放到别处”的人用。"""
    return Path(os.environ.get('THINKCUT_PROFILES') or PROGRAM / '配置档')


def _read(p, need=True):
    p = Path(p)
    if not p.exists():
        if need: raise SystemExit(f'停止：找不到配置文件 {p}')
        return {}
    try:
        with open(p, encoding='utf-8') as f: data = json.load(f)
    except ValueError as e:
        raise SystemExit(f'停止：配置文件 {p} 不是合法的 JSON：{e}')
    if not isinstance(data, dict): raise SystemExit(f'停止：配置文件 {p} 最外层要是 {{…}}')
    return {k: v for k, v in data.items() if not str(k).startswith('_')}     # “_说明”这类键只给人看


def merge(base, over):
    """字典逐层合并，别的整个替换；不改原对象。"""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _sub(v, ip):
    if isinstance(v, str): return v.replace('{IP}', ip)
    if isinstance(v, list): return [_sub(x, ip) for x in v]
    if isinstance(v, dict): return {k: _sub(x, ip) for k, x in v.items()}
    return v


def find_project(start):
    """从 start 往上找第一个带 项目.json 的文件夹（片子文件夹）；找不到给 None。"""
    if not start: return None
    p = Path(start).expanduser().resolve()
    for q in [p] + list(p.parents):
        if (q / '项目.json').is_file(): return q
    return None


def project_meta(start):
    d = find_project(start)
    if not d: return None, {}
    try:
        with open(d / '项目.json', encoding='utf-8') as f: meta = json.load(f)
        return d, meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        return d, {}


def ips():
    root = profiles_dir()
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / 'ip.json').is_file()) if root.is_dir() else []


def default_ip():
    root = profiles_dir()
    return _read(root / '本机.json', need=False).get('默认IP') or _read(root / '公共.json').get('默认IP') or EXAMPLE


def which_ip(ip=None, project=None):
    if ip: return ip
    if os.environ.get('THINKCUT_IP'): return os.environ['THINKCUT_IP']
    _, meta = project_meta(project)
    return meta.get('ip') or default_ip()


class Config(dict):
    """合并后的配置。cfg.get('封面.标题色') 按点分层取；cfg.path('片子文件夹') 给绝对路径。"""

    def __init__(self, data, ip, folder):
        super().__init__(data); self.ip = ip; self.folder = folder

    def get(self, key, default=_MISSING):
        cur = self
        for part in str(key).split('.'):
            if isinstance(cur, dict) and part in cur: cur = dict.__getitem__(cur, part)
            elif default is _MISSING: raise SystemExit(f'停止：IP「{self.ip}」的配置里没有 {key}（公共层也没有）；在 配置档/公共.json 或 配置档/{self.ip}/ip.json 里补上')
            else: return default
        return cur

    def path(self, key, default=_MISSING):
        v = self.get(key, default)
        if v is None or v is default: return v
        p = Path(str(v)).expanduser()
        return p if p.is_absolute() else (PROGRAM / p).resolve()

    @property
    def rules_file(self):
        return self.folder / '规矩.md'


def load(ip=None, project=None):
    root = profiles_dir()
    name = which_ip(ip, project)
    folder = root / name
    if not (folder / 'ip.json').is_file():
        have = '、'.join(ips()) or '（一个都没有）'
        raise SystemExit(f'停止：没有叫「{name}」的 IP 配置（{folder}/ip.json）。现有：{have}。新建：python3 {PROGRAM}/bin/cutdesk_config.py new {name}')
    data = merge(_read(root / '公共.json'), _read(folder / 'ip.json'))
    local = _read(root / '本机.json', need=False); local.pop('默认IP', None)
    data = merge(data, local)
    _, meta = project_meta(project)
    if isinstance(meta.get('配置覆盖'), dict): data = merge(data, meta['配置覆盖'])
    data.pop('默认IP', None); data['名字'] = data.get('名字') or name
    return Config(_sub(data, name), name, folder)


def new_ip(name, src=EXAMPLE):
    root = profiles_dir()
    bad = set('/\\:') & set(name)
    if not name or name.startswith(('.', '_')) or bad: raise SystemExit(f'停止：IP 名字「{name}」不能带 / \\ : 也不能以 . _ 开头')
    dst, from_ = root / name, root / src
    if dst.exists(): raise SystemExit(f'停止：已经有 {dst}，没动它')
    if not (from_ / 'ip.json').is_file(): raise SystemExit(f'停止：没有可复制的 IP「{src}」（现有：{"、".join(ips())}）')
    shutil.copytree(from_, dst, ignore=shutil.ignore_patterns('.DS_Store'))
    data = json.loads((dst / 'ip.json').read_text(encoding='utf-8'))
    data['名字'] = name
    data['片子文件夹'] = '../{IP}视频'               # 不和被复制的 IP 共用片子文件夹，免得串
    (dst / 'ip.json').write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(description='配置档：公共层＋每个 IP 一层')
    sp = ap.add_subparsers(dest='cmd', required=True)
    for c in ('show', 'rules'):
        p = sp.add_parser(c); p.add_argument('--ip'); p.add_argument('--project')
        if c == 'show': p.add_argument('--key')
    sp.add_parser('list')
    p = sp.add_parser('new'); p.add_argument('name'); p.add_argument('--from', dest='src', default=EXAMPLE)
    a = ap.parse_args(argv)
    if a.cmd == 'list':
        d = default_ip()
        for n in ips(): print(n + ('（默认）' if n == d else ''))
        return 0
    if a.cmd == 'new':
        dst = new_ip(a.name, a.src)
        print(f'已建好 IP「{a.name}」：{dst}（从「{a.src}」复制）\n下一步：改 {dst}/ip.json（片子文件夹、封面颜色字体、配音音色、发布平台）和 {dst}/规矩.md（给 AI 看的这个 IP 的规矩）；'
              f'建片子时 project.py init 名字 --ip {a.name}；不写的项用 配置档/公共.json 的。')
        return 0
    cfg = load(a.ip, a.project)
    if a.cmd == 'rules':
        f = cfg.rules_file
        print(f'当前 IP：{cfg.ip}　规矩文件：{f}\n')
        print(f.read_text(encoding='utf-8') if f.is_file() else '（这个 IP 还没写规矩文件：只按技能说明里的通用规矩做）')
        return 0
    out = cfg.get(a.key) if a.key else dict(cfg)
    print(json.dumps({'IP': cfg.ip, '值': out} if a.key else {'IP': cfg.ip, **out}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
