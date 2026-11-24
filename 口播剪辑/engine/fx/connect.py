"""Only connect to the already open project. Never open/switch/save a project."""
import locale
import os
import sys
from pathlib import Path


def connect(timeline_name):
    api = os.environ.get('RESOLVE_SCRIPT_API', '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting')
    os.environ.setdefault('RESOLVE_SCRIPT_LIB', '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so')
    sys.path.insert(0, str(Path(api) / 'Modules'))
    previous = locale.setlocale(locale.LC_ALL)
    try:
        import DaVinciResolveScript
        resolve = DaVinciResolveScript.scriptapp('Resolve')
    finally:
        locale.setlocale(locale.LC_ALL, previous)
    if not resolve:
        raise RuntimeError('无法连接已打开的达芬奇，请先打开目标工程。')
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    if not project:
        raise RuntimeError('没有已打开的工程；本工具不会打开工程或切换数据库。')
    return resolve, project, find_timeline(project, timeline_name)


def find_timeline(project, name):
    matches = [t for t in (project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount()+1)) if t.GetName() == name]
    if len(matches) != 1:
        raise ValueError(f'时间线名称「{name}」必须唯一，实际找到 {len(matches)} 条。')
    return matches[0]


def guard(resolve, project, timeline=None):
    current = resolve.GetProjectManager().GetCurrentProject()
    if not current or current.GetUniqueId() != project.GetUniqueId():
        raise RuntimeError('当前工程已改变，停止读取。')
    if timeline is not None:
        active = current.GetCurrentTimeline()
        if not active or active.GetUniqueId() != timeline.GetUniqueId():
            raise RuntimeError('静帧只能导出当前时间线；请手动打开指定时间线后重试。')
