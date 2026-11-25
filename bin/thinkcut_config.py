#!/usr/bin/env python3
"""配置档：一个 IP 一套配置，“公共层＋每个 IP 自己一层”（2026-11-24 用户定）。五块共用，只用 Python 3.9 标准库。

判断标准（用户）：换一个人用，这个要不要变——不变的是程序，要变的是配置，他做出来的是数据。

合并顺序（后面的盖前面的；字典逐层合并，列表和单个值整个替换）：
  1. profiles/公共.json            所有 IP 都继承的默认值（跟着程序走，进存档）
  2. <IP 文件夹>/ip.json          这个 IP 和公共层不一样的地方
  3. profiles/本机.json            这台机器自己的（片子放别处、默认 IP 换人）；不进存档，发版不碰，可以没有
  4. 项目.json 里的 "配置覆盖"    单条片子临时改（可以没有）
IP 文件夹放哪（2026-11-25 QA 1.6：首页新建／删除 IP 以后发版被拦）：程序文件夹的 profiles/ 里只放随程序发的公共层和示例（SHIPPED）；
用户自己的 IP（升级前已有的和首页“新建 IP”建的）放程序文件夹外面的“用户配置档”文件夹（公共.json／本机.json 的 "用户配置档"，默认 ../ThinkCut配置档，
和片子文件夹一样按程序目录算，正式版、开发版落到同一个地方）。同名时用户配置档里的优先。发版只管程序文件，不碰用户配置档。
以前放在 profiles/ 里的用户 IP（升级前放在 profiles/<IP>/ 的、首页建的）第一次读配置时自动挪过去（migrate；发版前 release.py 也先复制一份）。
删掉随程序发的 IP（示例IP）不挪程序文件：本机.json 记 "隐藏的IP"，回收站里放一份拷贝，还原时放进用户配置档。
开发版（程序文件夹名以“-开发”结尾或是 git worktree，和 build_app.sh 同一条规矩）默认看“试验田”不看“片子文件夹”（QA 1.1：开发中的代码不碰真片子）；
THINKCUT_APP_FLAVOR=release 可强制当正式版（测试、或确实要看真片子时显式设）。
给 AI 看的 IP 规矩：profiles/<IP>/规矩.md（技能说明里写“先读当前 IP 的规矩”，用下面的 rules 命令拿）。

用哪个 IP：--ip／环境变量 THINKCUT_IP → 片子的 项目.json 里的 "ip"（从给的文件夹往上找）→ 本机.json／公共.json 的 "默认IP"。
写路径的值：相对路径按程序目录（放 bin/、profiles/ 的这一层）算；里面的 {IP} 换成 IP 名字。

命令：
  python3 bin/thinkcut_config.py show  [--ip 名字] [--project 片子文件夹] [--key 封面.标题色]   看合并后的配置
  python3 bin/thinkcut_config.py rules [--ip 名字] [--project 片子文件夹]                       这个 IP 给 AI 看的规矩（路径＋全文）
  python3 bin/thinkcut_config.py list                                                          有哪些 IP、默认是谁
  python3 bin/thinkcut_config.py new 新名字 [--from 示例IP | --blank]                           新建一个 IP（从示例或现有 IP 复制；--blank＝只写名字，其余跟公共层）
  python3 bin/thinkcut_config.py remove 名字                                                    删一个 IP：配置挪到回收站（能还原），片子文件夹不动；默认 IP 不能删
首页（thinkcut_edit.py home）左上角切 IP：选的 IP 记在 本机.json 的 "首页IP"（只管首页看哪个 IP，不改 "默认IP"）。
"""
import argparse
import copy
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

PROGRAM = Path(__file__).resolve().parents[1]        # 程序目录（使用版／开发版／worktree 都一样按自己的位置算）
EXAMPLE = '示例IP'
SHIPPED = (EXAMPLE,)                                  # 随程序发的 IP（进存档）；别的 IP 都在用户配置档
USER_DIR = '../ThinkCut配置档'                         # 用户配置档默认位置（相对程序目录）
LOCAL_ONLY = ('默认IP', '首页IP')                     # 本机.json 里只管“用哪个 IP”的键，不合进配置值
_MISSING = object()
LEGACY_FILE = PROGRAM / 'bin' / 'legacy_names.sh'


def legacy(path=None):
    """旧名兼容表 {名字: 值}：从改名前的版本升级来的安装才有 bin/legacy_names.sh（导出的干净版没有，就是空表）。"""
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
    """程序带的配置档文件夹（公共.json、本机.json、示例IP）；THINKCUT_PROFILES 只给离线测试和“把配置放到别处”的人用。"""
    return Path(os.environ.get('THINKCUT_PROFILES') or PROGRAM / 'profiles')


def flavor(program=None):
    """dev＝开发版（程序文件夹名以“-开发”结尾，或是 git worktree）；release＝正式版。THINKCUT_APP_FLAVOR 可强制。"""
    f = os.environ.get('THINKCUT_APP_FLAVOR')
    if f in ('dev', 'release'): return f
    p = Path(program or PROGRAM)
    return 'dev' if p.name.endswith('-开发') or (p / '.git').is_file() else 'release'


def user_dir(program=None, profiles=None):
    """用户配置档：用户自己的 IP 放这里（程序文件夹外面，发版不碰）。THINKCUT_USER_PROFILES ＞ 设了 THINKCUT_PROFILES 时就是它（测试照旧一个文件夹）＞
    本机.json／公共.json 的 "用户配置档"（相对程序目录）＞ 程序目录旁边的 ThinkCut配置档/。"""
    if os.environ.get('THINKCUT_USER_PROFILES'): return Path(os.environ['THINKCUT_USER_PROFILES'])
    if program is None and os.environ.get('THINKCUT_PROFILES'): return Path(os.environ['THINKCUT_PROFILES'])
    prog = Path(program or PROGRAM); root = Path(profiles or prog / 'profiles')
    v = _read(root / '本机.json', need=False).get('用户配置档') or _read(root / '公共.json', need=False).get('用户配置档') or USER_DIR
    q = Path(str(v)).expanduser()
    return q if q.is_absolute() else (prog / q).resolve()


def hidden():
    """删掉的随程序发的 IP（本机.json 的 "隐藏的IP"）。"""
    v = _read(profiles_dir() / '本机.json', need=False).get('隐藏的IP') or []
    return [x for x in v if isinstance(x, str)]


def _write_local(update):
    """改 本机.json（别的键原样留着；临时文件名不固定，两个进程同时写不会撞）。update(data) 就地改。"""
    f = profiles_dir() / '本机.json'; data = {}
    if f.exists():
        try: data = json.loads(f.read_text(encoding='utf-8'))
        except ValueError as e: raise SystemExit(f'停止：配置文件 {f} 不是合法的 JSON，没改它：{e}')
        if not isinstance(data, dict): raise SystemExit(f'停止：配置文件 {f} 最外层要是 {{…}}，没改它')
    update(data)
    tmp = f.with_name(f'.本机.json.{os.getpid()}.{os.urandom(3).hex()}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8'); os.replace(tmp, f)


def migrate(program=None, to=None, move=True):
    """把程序文件夹 profiles/ 里不是随程序发的 IP（升级前已有的、首页建的）放进用户配置档。用户配置档里已经有同名的就不动（那份为准）。
    move=False 只复制（发版前用：那些文件还在存档里，快进时 git 会删掉它们）。返回 [(名字, 去哪)]。"""
    prog = Path(program or PROGRAM); src = Path(os.environ.get('THINKCUT_PROFILES') or prog / 'profiles') if program is None else next((prog / n for n in ('profiles', '配置档') if (prog / n).is_dir() and not (prog / n).is_symlink()), prog / 'profiles')      # 发版前的使用版还是旧名 配置档/（2026-11-25 改名）
    dst = Path(to) if to else user_dir(program)
    if not src.is_dir() or src.resolve() == dst.resolve(): return []
    out = []
    for d in sorted(src.iterdir()):
        if not d.is_dir() or d.name in SHIPPED or d.name.startswith(('.', '_')) or not (d / 'ip.json').is_file(): continue
        if (dst / d.name).exists(): continue
        dst.mkdir(parents=True, exist_ok=True)
        if move: shutil.move(str(d), str(dst / d.name))
        else: shutil.copytree(d, dst / d.name, ignore=shutil.ignore_patterns('.DS_Store'))
        out.append((d.name, str(dst / d.name)))
    return out


_migrated = set()


def _auto_migrate():
    key = (str(profiles_dir()), str(user_dir()))
    if key in _migrated: return
    _migrated.add(key)
    try: migrate()
    except OSError as e: print(f'提醒：把 profiles/ 里的 IP 挪到用户配置档没成功（{e}），这次先照旧读', file=sys.stderr)


def ip_folder(name):
    """这个 IP 的配置文件夹：用户配置档里有就用它，没有再看程序带的（示例IP）。"""
    _auto_migrate()
    u = user_dir() / name
    if (u / 'ip.json').is_file() or name not in SHIPPED: return u
    return profiles_dir() / name


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
    _auto_migrate(); hide = set(hidden()); names = set()
    for root, only in ((profiles_dir(), SHIPPED), (user_dir(), None)):
        if not root.is_dir(): continue
        for p in root.iterdir():
            if p.is_dir() and (p / 'ip.json').is_file() and (only is None or p.name in only): names.add(p.name)
    shipped_hidden = {n for n in hide if not (user_dir() / n / 'ip.json').is_file()}       # 隐藏只管程序带的那份；用户配置档里有同名的照样能用
    return sorted(names - shipped_hidden)


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
            elif default is _MISSING: raise SystemExit(f'停止：IP「{self.ip}」的配置里没有 {key}（公共层也没有）；在 profiles/公共.json 或 <用户配置档>/{self.ip}/ip.json 里补上')
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
    folder = ip_folder(name)
    if not (folder / 'ip.json').is_file():
        have = '、'.join(ips()) or '（一个都没有）'
        raise SystemExit(f'停止：没有叫「{name}」的 IP 配置（{folder}/ip.json）。现有：{have}。新建：python3 {PROGRAM}/bin/thinkcut_config.py new {name}')
    data = merge(_read(root / '公共.json'), _read(folder / 'ip.json'))
    local = _read(root / '本机.json', need=False)
    for k in LOCAL_ONLY: local.pop(k, None)
    data = merge(data, local)
    _, meta = project_meta(project)
    if isinstance(meta.get('配置覆盖'), dict): data = merge(data, meta['配置覆盖'])
    data.pop('默认IP', None); data.pop('隐藏的IP', None); data.pop('用户配置档', None); data['名字'] = data.get('名字') or name
    if flavor() == 'dev' and data.get('试验田'): data['片子文件夹'] = data['试验田']      # 开发版默认不碰真片子（QA 1.1）
    return Config(_sub(data, name), name, folder)


def new_ip(name, src=EXAMPLE):
    """新建一个 IP。src＝从哪个 IP 复制（默认示例IP）；src=None＝空白：ip.json 只写名字，其余全跟公共层（首页“新建 IP…”用这个，以后让 AI 改）。"""
    root = user_dir(); name = str(name or '').strip()
    bad = set('/\\:') & set(name)
    if not name or name.startswith(('.', '_')) or bad or len(name) > 40 or any(ord(c) < 32 for c in name):
        raise SystemExit(f'停止：IP 名字「{name}」不能空着、不能带 / \\ : 、不能以 . _ 开头、不超过 40 个字')
    dst = root / name
    if dst.exists() or name.lower() in {n.lower() for n in ips()}: raise SystemExit(f'停止：已经有叫「{name}」的 IP 了（{dst}），没动它')
    if src is None:
        root.mkdir(parents=True, exist_ok=True); dst.mkdir()
        (dst / 'ip.json').write_text(json.dumps({'_说明': f'{name} 这个 IP 和公共层不一样的地方；现在什么都没写，全跟 profiles/公共.json。以后让 AI 按这个 IP 改（封面颜色、配音、平台……），给 AI 看的规矩写同目录 规矩.md。',
                                                 '名字': name}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        return dst
    from_ = ip_folder(src)
    if src not in ips() or not (from_ / 'ip.json').is_file(): raise SystemExit(f'停止：没有可复制的 IP「{src}」（现有：{"、".join(ips())}）')
    root.mkdir(parents=True, exist_ok=True); shutil.copytree(from_, dst, ignore=shutil.ignore_patterns('.DS_Store'))
    data = json.loads((dst / 'ip.json').read_text(encoding='utf-8'))
    data['名字'] = name
    data['片子文件夹'] = '../{IP}视频'               # 不和被复制的 IP 共用片子文件夹，免得串
    (dst / 'ip.json').write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return dst


def remove_ip(name, to=None):
    """删一个 IP（2026-11-25 用户：“怎么没有删除ip的选项”）：把 配置档/<名字>/ 整个挪到回收站 <回收站>/ThinkCut配置档/<名字>_<时间>（不是永久删除），
    这个 IP 的片子文件夹不动（片子还在，以后再建同名 IP 又能看到）。默认 IP 不能删（先在 本机.json／公共.json 把 默认IP 换掉）；只剩一个 IP 不能删。
    首页正记着它（本机.json 的 首页IP）就一起去掉。to＝回收站文件夹（不给＝默认 IP 配置里的“回收站”）。返回挪到哪。"""
    name = str(name or '').strip(); have = ips()
    if name not in have: raise SystemExit(f'停止：没有叫「{name}」的 IP')
    if name == default_ip(): raise SystemExit(f'停止：「{name}」是默认 IP，不能删（要删先把 本机.json 或 公共.json 的 默认IP 换成别的）')
    if len(have) < 2: raise SystemExit('停止：只剩这一个 IP，不能删')
    box = Path(to) if to else load(ip=default_ip()).path('回收站')
    if not box.is_dir(): raise SystemExit(f'停止：回收站 {box} 不在（硬盘没接上？），没删')
    box = box / 'ThinkCut配置档'; box.mkdir(exist_ok=True)
    dst = box / f'{name}_{time.strftime("%Y%m%d-%H%M%S")}'
    if dst.exists(): raise SystemExit('停止：回收站里已经有同名的了，过一秒再试')
    mine = user_dir() / name
    if (mine / 'ip.json').is_file(): shutil.move(str(mine), str(dst))              # 用户自己的：整个挪进回收站
    else: shutil.copytree(profiles_dir() / name, dst, ignore=shutil.ignore_patterns('.DS_Store'))   # 随程序发的：程序文件不动，回收站放一份拷贝
    shipped = (profiles_dir() / name / 'ip.json').is_file()
    def g(data):
        if data.get('首页IP') == name: data.pop('首页IP')
        if shipped: data['隐藏的IP'] = sorted(set(data.get('隐藏的IP') or []) | {name})
    if shipped or (profiles_dir() / '本机.json').exists(): _write_local(g)
    return dst


def restore_ip(src):
    """删 IP 的撤销：把回收站 ThinkCut配置档/<名字>_<时间> 挪回 配置档/<名字>；已经有同名的不覆盖。返回名字。"""
    src = Path(str(src or '')); m = re.fullmatch(r'(.+)_\d{8}-\d{6}', src.name)
    if not m or src.parent.name != 'ThinkCut配置档' or not (src / 'ip.json').is_file(): raise SystemExit('停止：回收站里没有这个 IP 了')
    dst = user_dir() / m[1]
    if dst.exists() or m[1] in ips(): raise SystemExit(f'停止：已经有叫「{m[1]}」的 IP 了，没覆盖')
    dst.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(dst))
    if m[1] in hidden(): _write_local(lambda data: data.update({'隐藏的IP': [x for x in data.get('隐藏的IP') or [] if x != m[1]]}))
    return m[1]


def home_ip():
    """首页上次选的 IP（本机.json 的 "首页IP"）；没选过或那个 IP 已经没了，给 None。"""
    v = _read(profiles_dir() / '本机.json', need=False).get('首页IP')
    return v if v in ips() else None


def set_home_ip(name):
    """记下首页选的 IP（写进 本机.json 的 "首页IP"，别的键原样留着；本机.json 不进存档）。"""
    if name not in ips(): raise SystemExit(f'停止：没有叫「{name}」的 IP')
    _write_local(lambda data: data.update({'首页IP': name}))
    return name


def main(argv=None):
    ap = argparse.ArgumentParser(description='配置档：公共层＋每个 IP 一层')
    sp = ap.add_subparsers(dest='cmd', required=True)
    for c in ('show', 'rules'):
        p = sp.add_parser(c); p.add_argument('--ip'); p.add_argument('--project')
        if c == 'show': p.add_argument('--key')
    sp.add_parser('list')
    p = sp.add_parser('remove'); p.add_argument('name')
    p = sp.add_parser('migrate', help='把某个程序文件夹 profiles/ 里的用户 IP 复制进用户配置档（发版前、或开发版合并后用）')
    p.add_argument('--from', dest='src', help='程序文件夹（默认这一份）'); p.add_argument('--move', action='store_true')
    sp.add_parser('where', help='用户配置档在哪、程序带的配置档在哪')
    p = sp.add_parser('new'); p.add_argument('name'); p.add_argument('--from', dest='src', default=EXAMPLE); p.add_argument('--blank', action='store_true', help='只写名字，其余跟公共层')
    a = ap.parse_args(argv)
    if a.cmd == 'list':
        d = default_ip()
        for n in ips(): print(n + ('（默认）' if n == d else ''))
        return 0
    if a.cmd == 'where':
        print(json.dumps({'程序带的': str(profiles_dir()), '用户配置档': str(user_dir()), '版本': flavor()}, ensure_ascii=False, indent=1)); return 0
    if a.cmd == 'migrate':
        got = migrate(a.src, user_dir(a.src) if a.src else None, move=a.move)
        print('\n'.join(f'{n} → {d}' for n, d in got) or '没有要挪的（用户配置档里已经有了，或程序文件夹里没有用户 IP）'); return 0
    if a.cmd == 'remove':
        print(f'已删 IP「{a.name}」：配置挪到了 {remove_ip(a.name)}（能还原：挪回 {user_dir()}/{a.name}）；它的片子文件夹没动。')
        return 0
    if a.cmd == 'new':
        if not a.blank and a.src == EXAMPLE and EXAMPLE not in ips(): a.blank = True     # 示例IP 被删了：建空白的
        dst = new_ip(a.name, None if a.blank else a.src)
        print(f'已建好 IP「{a.name}」：{dst}（{"空白：其余跟公共层" if a.blank else "从「" + a.src + "」复制"}）\n下一步：改 {dst}/ip.json（片子文件夹、封面颜色字体、配音音色、发布平台）和 {dst}/规矩.md（给 AI 看的这个 IP 的规矩）；'
              f'建片子时 project.py init 名字 --ip {a.name}；不写的项用 profiles/公共.json 的。')
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
