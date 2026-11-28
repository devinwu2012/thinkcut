# -*- coding: utf-8 -*-
"""后台 AI 用达芬奇 MCP 的规矩（2026-11-28 用户定“开达芬奇 MCP”）：哪些工具、动作能用，哪些拦，写之前要拿哪把锁。
护栏程序 bin/davinci_mcp_guard.py（Claude、Codex 都启动它，它再启动用户装的达芬奇 MCP）和设置页、engine 参数都读这一份，两边一致。
只用标准库（护栏跑在达芬奇 MCP 自己的 Python 里，审片台跑在系统 Python 3.9 里）。

- 整个工具禁用（TOOLS_OFF）：两边的客户端参数禁一次（Claude --disallowedTools、Codex disabled_tools），护栏不列出、调用也拒，再拦一次。
- 动作禁用（ACTION_OFF）：达芬奇 MCP 是“一个工具很多动作”，删除、渲染导出这类动作夹在 timeline、media_pool 这些常用工具里，客户端参数拦不到，由护栏按动作拒。
- 写操作（不是只读的动作）：护栏先拿“同一时间只让一个会话写达芬奇”的锁（和代办跑的 thinkcut_edit.py、broll.py、fx.py 是同一把），拿不到就拒。
- 达芬奇 MCP 每改一次时间线会自动另存一条 “<名字>_archived_vNN” 备份（关不掉）：护栏在第一次写之前开一个“批次”（timeline_versioning begin_run），
  同一次 AI 处理里的多次修改只多一条备份；交付把这些备份认出来挪进“历史版本”（core/engine/src/import_mvp_delivery.py）。
"""
import json
import os
import re
from pathlib import Path

NAME = 'davinci-resolve'                  # 用户配置里这个 MCP 的名字（Codex 装的就叫这个）

# 整个工具不给：名字 → 为什么
TOOLS_OFF = {
    'render': '渲染、导出成片：整片导出只认你在审片台页面点的定稿',
    'render_presets': '导入导出渲染预设：和渲染导出一起不给',
    'resolve_control': '退出、重启达芬奇，切页面，改达芬奇偏好',
    'project_manager': '新建、打开、删除、导出工程：会换掉你正开着的工程',
    'project_manager_folders': '工程管理器里建、删文件夹',
    'project_manager_cloud': '云端工程',
    'project_manager_database': '切换、新建项目库：会换掉你正开着的库',
    'timeline_versioning': '回滚、清理备份时间线：会新建、删除时间线',
    'edit_engine': '自动粗剪、紧剪：会新建时间线；剪口播、配 B-roll 走 ThinkCut 自己的程序',
    'setup': '改达芬奇 MCP 自己的安全开关',
    'script_plugin': '往达芬奇里装脚本：脚本在沙盒外、带着你的全部权限运行',
    'fuse_plugin': '往达芬奇里装 Fusion 插件：同上',
    'dctl': '往达芬奇里装调色程序：同上',
    'lut': '往达芬奇里装、删 LUT 文件',
    'layout_presets': '改、删达芬奇的界面布局',
}
# 能用的工具里也不给的动作（按动作名认）
ACTION_OFF = [
    (re.compile(r'delete|remove|clear|purge|prune|unlink|relink|replace|reset'), '删除、清空、替换、断开媒体：删掉的东西 MCP 收不回来；要删请你在达芬奇里自己删'),
    (re.compile(r'render|export|deliver|burnin|grab_and_export'), '渲染、导出：整片导出只认你在审片台页面点的定稿；导出文件会写到沙盒外'),
    (re.compile(r'quit|restart|install|load_burnin|set_preset|open_page'), '退出达芬奇、装东西、换整套预设'),
]
# 只读的动作（不拿锁）：按动作名开头和结尾认；认不出来的一律当写操作（要拿锁）
READ = re.compile(r'^(get|list|is|has|probe|find|search|describe|inspect|read|count|preview|diff|lookup|explain|status|query|summar|check|validate'
                  r'|capture|current|show|help|analy[sz]e|compare|topics?|guide|recommend|estimate|plan|dry_run)'
                  r'|_(report|capabilities|boundary_report|info|status|list|count|summary|preview|dry_run)$')
# 达芬奇 MCP 自动存的备份时间线
BACKUP = re.compile(r'_archived_v\d+$|_rolled_back_\d{6}$')
GUARD = Path(__file__).resolve().with_name('davinci_mcp_guard.py')


def check(tool, action):
    """(能不能用, 不能用的原因, 是不是写操作)。"""
    if tool in TOOLS_OFF: return False, TOOLS_OFF[tool], False
    a = str(action or '').strip().lower()
    for pat, why in ACTION_OFF:
        if a and pat.search(a): return False, why, False
    return True, '', not (not a or READ.search(a))


def lock_file():
    """和 thinkcut_edit 的 resolve.LOCK_FILE、broll 的 workspace.write_lock 是同一个文件（THINKCUT_RESOLVE_LOCK 可换，测试用）。"""
    return Path(os.environ.get('THINKCUT_RESOLVE_LOCK') or Path.home() / '.cache' / 'ai-talking-head-edit' / 'resolve-write.lock')


def codex_config():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'config.toml'


def _value(text):
    text = text.strip()
    try: return json.loads(text)                     # TOML 的基本字符串、字符串数组、数字和 JSON 写法一样
    except ValueError: pass
    if text.startswith("'") and text.endswith("'"): return text[1:-1]       # TOML 字面字符串
    if text in ('true', 'false'): return text == 'true'
    return text


def upstream(name=NAME, config=None):
    """用户 Codex 配置里这个 MCP 怎么启动：{'command', 'args', 'env', 'cwd', 'startup_timeout_sec'}；没配返回 None。
    环境变量（可能有口令）只在运行时读，不写进仓库、不放进命令行参数（护栏自己读这一份再启动它）。"""
    try: lines = Path(config or codex_config()).read_text(encoding='utf-8').splitlines()
    except OSError: return None
    head = re.compile(r'^\s*\[\s*mcp_servers\.(?:"([^"]+)"|([A-Za-z0-9_-]+))(?:\.(env))?\s*\]\s*$')
    out, where = None, None
    for line in lines:
        s = line.strip()
        if not s or s.startswith('#'): continue
        if s.startswith('['):
            m = head.match(s); where = None
            if m and (m[1] or m[2]) == name:
                out = out or {'command': '', 'args': [], 'env': {}}
                where = 'env' if m[3] else 'top'
            continue
        if where and '=' in s:
            k, v = s.split('=', 1); k = k.strip().strip('"'); v = _value(v)
            if where == 'env': out['env'][k] = str(v)
            elif k in ('command', 'args', 'cwd', 'startup_timeout_sec', 'enabled'): out[k] = v
    if not out or not out.get('command') or out.get('enabled') is False: return None
    if not isinstance(out.get('args'), list): out['args'] = []
    return out


def launcher(name=NAME, config=None):
    """后台 AI 启动的是护栏：{'command': 达芬奇 MCP 自己的 Python, 'args': [护栏]}（没装、启动程序不是 Python 时返回 None）。"""
    up = upstream(name, config)
    if not up or not re.match(r'python[\d.]*$', Path(str(up['command'])).name): return None
    return {'command': str(up['command']), 'args': [str(GUARD), '--server', name], 'startup_timeout_sec': up.get('startup_timeout_sec')}
