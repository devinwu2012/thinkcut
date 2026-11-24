"""Read-only client check for the NAS-shared editing workspace."""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from doubao_credentials import verified_for_current_key, SECRETS as SECRETS_DIR
from editing_preflight import alignment_runtime


def working_ffmpeg():
    executable = shutil.which('ffmpeg')
    if not executable:
        return None, False, '本机没有 ffmpeg'
    try:
        result = subprocess.run([executable, '-hide_banner', '-encoders'], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return executable, False, 'ffmpeg 无法运行或检查超时'
    if result.returncode:
        return executable, False, 'ffmpeg 运行失败'
    if 'libmp3lame' not in result.stdout:
        return executable, False, 'ffmpeg 缺少提音所需的 libmp3lame 编码器'
    return executable, True, None


def collect_skill_checks(root, codex_home):
    skills = {}
    for name in ('a-roll', 'b-roll'):
        source = root / 'skills' / name
        # 2026-10-23 起这些是“剪辑（thinkcut-edit）”下的步骤：说明文件在、入口 thinkcut-edit 已链接即可
        present = (source / 'SKILL.md').is_file()
        linked = (codex_home / 'skills' / 'thinkcut-edit' / 'SKILL.md').is_file()
        skills[name] = {'skill_source_present': present, 'skill_installed_as_nas_link': linked,
                        'shared_skill_ready': present and linked}
    return {
        'skills': skills,
        'skill_source_present': all(s['skill_source_present'] for s in skills.values()),
        'skill_installed_as_nas_link': all(s['skill_installed_as_nas_link'] for s in skills.values()),
        'shared_skill_ready': all(s['shared_skill_ready'] for s in skills.values()),
    }


def ffmpeg_has_zscale(executable):
    if not executable:
        return False
    try:
        result = subprocess.run([executable, '-hide_banner', '-filters'], capture_output=True,
                                text=True, timeout=10)
        return result.returncode == 0 and any(
            len(row.split()) > 1 and row.split()[1] == 'zscale'
            for row in result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return False


def on_network_volume(path):
    """文件是不是在网络共享卷上（SMB/NFS/AFP）：是 True、本地盘 False、查不出来 None。"""
    try:
        result = subprocess.run(['df', '-P', str(path)], capture_output=True, text=True, timeout=10)
        device = result.stdout.strip().splitlines()[-1].split()[0] if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None
    if not device:
        return None
    return not device.startswith('/dev/')


def key_permissions(key_file):
    """(只有本人可读吗, 中文说明)。网络卷上所有文件的权限位都显示成一样的（SMB 挂载是 0700），读出来没有意义，如实说查不出来。"""
    network = on_network_volume(key_file)
    if network is not False:
        return None, '查不出来（Key 在 NAS 上，谁能读由 NAS 共享设置决定；这里显示的权限位不代表真实权限）' if network else '查不出来（判断不了 Key 在本地盘还是网络盘）'
    if stat.S_IMODE(key_file.stat().st_mode) & 0o077:
        return False, '这台 Mac 的其他用户也能读（建议 chmod 600）'
    return True, '只有本人可读'


def _app_dir():
    sys.path.append(str(Path(__file__).resolve().parents[2] / 'bin')); import cutdesk_config      # 本机文件夹名（含旧名兼容）
    return cutdesk_config.app_dir()


def collect_checks(align_python=None):
    root = Path(__file__).resolve().parents[1]
    codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    mcp_root = Path(os.environ.get('DAVINCI_RESOLVE_MCP_ROOT', str(_app_dir() / '环境' / 'davinci-resolve-mcp')))   # NAS 原件的 Mac 运行副本（2026-10-29）
    key_file = SECRETS_DIR / 'doubao_api_key'      # 开发版不复制钥匙，指到使用版那一份
    key_present = False
    key_verified = False
    key_resource = None
    key_restricted, key_permissions_note = None, '没有 Key 文件，未检查'
    key_access_error = None
    try:
        if not os.access(key_file.parent, os.R_OK | os.X_OK):
            key_access_error = 'NAS secrets 目录不可读；请检查 SMB 账户权限'
        elif key_file.is_file():
            if not os.access(key_file, os.R_OK):
                key_access_error = 'NAS 豆包 Key 不可读；请检查 SMB 账户权限'
            else:
                key_present = key_file.stat().st_size > 0
                if key_present:
                    key_restricted, key_permissions_note = key_permissions(key_file)
                # 2026-11-18：极速版、2.0 标准版哪种验证过都算；记下是哪一种，自检里如实显示
                key_resource = next((r for r in ('volc.bigasr.auc_turbo', 'volc.seedasr.auc') if key_present and verified_for_current_key(r)), None)
                key_verified = key_resource is not None
    except (OSError, RuntimeError):
        key_access_error = 'NAS 豆包 Key 检查失败；请检查文件与 SMB 权限'
    ffmpeg, ffmpeg_ready, ffmpeg_error = working_ffmpeg()
    subtitle_ready=False;subtitle_error=None
    try:
        sys.path.insert(0,str(root/'engine/src'))
        from run_mvp import node_runtime
        node_runtime(mcp_root)
        subtitle_ready=True
    except (OSError,RuntimeError) as error:
        subtitle_error=str(error)
    transcription_entry = root / 'bin' / 'transcribe_video.py'
    transcription_ready = bool(transcription_entry.is_file() and ffmpeg_ready and key_present and key_verified and not key_access_error)
    resolve_apps = [Path('/Applications/DaVinci Resolve/DaVinci Resolve.app'), Path('/Applications/DaVinci Resolve.app')]
    resolve_app = next((path for path in resolve_apps if path.is_dir()), None)
    checks = {
        **collect_fx_checks(root, codex_home),
        **alignment_runtime(align_python),
        'nas_workspace': str(root),
        # Someone is changing the shared program right now; paid or Resolve runs may hit half-edited code.
        'shared_code_being_modified': (root / '正在修改中.txt').read_text(encoding='utf-8').strip() if (root / '正在修改中.txt').is_file() else None,
        'shared_version': (root.parent / 'VERSION').read_text().strip() if (root.parent / 'VERSION').is_file() else None,   # 全家一个版本号（根目录，2026-11-24 起）
        **collect_skill_checks(root, codex_home),
        'python3': sys.executable,
        'node': shutil.which('node'),
        'subtitle_dependencies_ready': subtitle_ready,
        'subtitle_dependencies_error': subtitle_error,
        'ffmpeg': ffmpeg,
        'ffmpeg_can_extract_mp3': ffmpeg_ready,
        'ffmpeg_has_zscale': ffmpeg_has_zscale(ffmpeg),
        'ffmpeg_error': ffmpeg_error,
        'ffprobe': shutil.which('ffprobe'),
        'codex': shutil.which('codex'),
        'resolve_app': str(resolve_app) if resolve_app else None,
        'resolve_mcp_server': str(mcp_root / 'src' / 'server.py') if (mcp_root / 'src' / 'server.py').is_file() else None,
        'resolve_mcp_python': str(mcp_root / 'venv' / 'bin' / 'python') if (mcp_root / 'venv' / 'bin' / 'python').is_file() else None,
        'doubao_direct_asr_entry': str(root / 'bin' / 'doubao_direct_asr.py') if (root / 'bin' / 'doubao_direct_asr.py').is_file() else None,
        'video_transcription_entry': str(root / 'bin' / 'transcribe_video.py') if (root / 'bin' / 'transcribe_video.py').is_file() else None,
        'shared_doubao_key_present': key_present,
        # True＝只有本人可读，False＝其他用户也能读，None＝查不出来（Key 在 NAS 上时权限位没有意义）
        'shared_doubao_key_permissions_restricted': key_restricted,
        'shared_doubao_key_permissions_note': key_permissions_note,
        'shared_doubao_key_access_error': key_access_error,
        'shared_doubao_key_api_verified': key_verified,
        'shared_doubao_key_api_resource': key_resource,
        'video_transcription_ready': transcription_ready,
        'resolve_mcp_connection_verified': False,
        'parameterized_editing_entry': str(root/'bin/edit_project.py') if (root/'bin/edit_project.py').is_file() else None,
        'verified_editing_fps': [[30000,1001]],
    }
    checks['missing'] = missing_items(checks)
    checks['end_to_end_ready'] = not checks['missing']
    return checks


def style_font_checks(config, fonts_dir=None):
    """每个花字样式量字要读的字体文件：在不在、能不能按字体名找到（字体合集里的序号会随 macOS 版本变）。"""
    from engine.fx import safety
    result = {}
    for profile, style in config['styles'].items():
        entry = {'font': f"{style['font']} {style['style']}", 'font_file': style.get('font_file')}
        try:
            path = str(safety.font_path(style, {}, config, fonts_dir))
            entry.update(found=True, path=path, index_changed=path in safety.MEMBER)
            if entry['index_changed']:
                entry['actual_index'] = safety.MEMBER[path]
        except (ValueError, OSError, ImportError) as error:
            entry.update(found=False, error=str(error))
        result[profile] = entry
    return result


def collect_fx_checks(root, codex_home):
    """Separate FX fields preserve the existing three editing-skill checks."""
    config = json.loads((root / 'engine/config/fx_styles.json').read_text())
    font_dir = Path.home() / 'Library/Fonts'
    # 字体.ttc#序号 是系统自带字体合集里的一个；苹方 PingFang.ttc 在系统字体资源目录（路径带随系统变的编号）
    dirs = (font_dir, Path('/Library/Fonts'), Path('/System/Library/Fonts'), *Path('/System/Library/AssetsV2').glob('com_apple_MobileAsset_Font*/*/AssetData'))
    fonts = {name: {'filename': filename, 'installed': any((d / filename.split('#')[0]).is_file() for d in dirs)}
             for name, filename in config['font_files'].items()}
    sfx = json.loads((root / 'engine/config/sfx_favorites.json').read_text())
    try:
        import fontTools
        fonttools = True
    except ImportError:
        fonttools = False
    source = root / 'skills/fx'
    link = codex_home / 'skills/thinkcut-edit'   # 花字音效是 thinkcut-edit 下的步骤
    sys.path.insert(0,str(root))
    from engine.fx.matte import environment_check
    matting_ready,matting_error=environment_check()
    from engine.fx.plan import sfx_roots
    sfx_dir = sfx_roots(sfx)[0]   # AI_EDIT_SFX_DIR 优先；配置里写的目录不在时退回按本仓库位置推的 <程序目录>/素材库/音效素材
    return {'fx_matting_ready':matting_ready,'fx_matting_error':matting_error,'fx_fonts': fonts, 'fx_fonttools_importable': fonttools,
            'fx_style_fonts': style_font_checks(config),
            'fx_sfx_directory': str(sfx_dir),
            'fx_sfx_directory_accessible': sfx_dir.is_dir() and os.access(sfx_dir, os.R_OK | os.X_OK),
            'fx_skill_ready': (source / 'SKILL.md').is_file() and (link / 'SKILL.md').is_file()}


def missing_items(c):
    """Chinese list of what this Mac still lacks, each with the next step."""
    rows = [
        (s['shared_skill_ready'], f'Skill {name} ' + ('源文件缺失' if not s['skill_source_present'] else '未正确连接')
         + '：运行 bash bin/setup_mac.sh') for name, s in c['skills'].items()
    ] + [
        (c['ffmpeg_can_extract_mp3'], 'ffmpeg 不可用：运行 bash bin/setup_mac.sh'),
        (c['shared_doubao_key_present'] and not c['shared_doubao_key_access_error'], '读不到豆包 Key：确认 NAS 已连接且有权限'),
        (c['shared_doubao_key_api_verified'], '豆包 Key 未验证：管理员按 README 用短音频测试一次'),
        (c.get('alignment_dependencies_ready'), '声学对齐环境缺失：运行 bash bin/setup_mac.sh'),
        (c['subtitle_dependencies_ready'], '字幕组件缺失：运行 bash bin/setup_mac.sh'),
        (c['resolve_app'], '没有安装 DaVinci Resolve：本人从官网安装'),
        (c['resolve_mcp_server'] and c['resolve_mcp_python'], '达芬奇 MCP 缺失：运行 bash bin/setup_mac.sh'),
    ]
    if 'ffmpeg_has_zscale' in c:
        rows.append((c['ffmpeg_has_zscale'], 'ffmpeg 缺 zscale（B-roll HDR 转换需要）：运行 bash bin/setup_mac.sh 安装/切换 ffmpeg-full'))
    rows += [(entry['installed'], f'花字字体「{name}」缺失：它是 macOS 自带字体，系统里应当有，检查字体册')
             for name, entry in c.get('fx_fonts', {}).items()]
    rows += [(entry['found'], f"花字样式「{profile}」量字要用的字体 {entry['font']} 找不到：{entry.get('error')}")
             for profile, entry in c.get('fx_style_fonts', {}).items()]
    for key, message in (
        ('fx_matting_ready', '自动抠人环境或模型缺失：运行 bash bin/setup_mac.sh'),
        ('fx_fonttools_importable', 'fontTools 缺失：运行 python3 -m pip install --user fonttools'),
        ('fx_sfx_directory_accessible', '音效库不可访问：挂载素材 NAS，或设置 AI_EDIT_SFX_DIR 指向音效目录'),
        ('fx_skill_ready', 'Skill fx 未正确连接：运行 bash bin/setup_mac.sh'),
    ):
        if key in c:
            rows.append((c[key], message))
    return [message for ready, message in rows if not ready]


def summary(c):
    lines = [f"程序版本 {c['shared_version']}"]
    if c.get('shared_code_being_modified'):
        lines.append('⚠ 有会话正在修改程序，先别开始识别或交付：' + c['shared_code_being_modified'].replace('\n', '；'))
    lines += [f"Skill {name}：" + ('已就绪' if s['shared_skill_ready'] else '未就绪')
              for name, s in c['skills'].items()]
    if 'ffmpeg_has_zscale' in c:
        lines.append('ffmpeg HDR 转换（zscale）：' + ('已就绪' if c['ffmpeg_has_zscale'] else '未就绪'))
    if 'fx_skill_ready' in c:
        lines.append('Skill fx：' + ('已就绪' if c['fx_skill_ready'] else '未就绪'))
        lines += [f'花字字体「{name}」：' + ('已安装' if entry['installed'] else '缺失')
                  for name, entry in c['fx_fonts'].items()]
        lines += [f"花字样式「{profile}」量字用的字体 {entry['font']}：" + ('找不到' if not entry['found'] else
                  f"能找到（字体合集里的序号变了，已按字体名找到第 {entry.get('actual_index')} 个）" if entry.get('index_changed') else '能找到')
                  for profile, entry in c.get('fx_style_fonts', {}).items()]
        lines.append('花字字形检查（fontTools）：' + ('已就绪' if c['fx_fonttools_importable'] else '未就绪'))
        lines.append('音效素材库：' + ('可访问' if c['fx_sfx_directory_accessible'] else '不可访问'))
        lines.append('自动抠人环境与模型（RVM）：' + ('已就绪' if c['fx_matting_ready'] else '未就绪'))
    if c.get('shared_doubao_key_permissions_note'):
        lines.append('豆包 Key 文件权限：' + c['shared_doubao_key_permissions_note'])
    kind = {'volc.bigasr.auc_turbo': '豆包极速版', 'volc.seedasr.auc': '豆包 2.0 标准版'}.get(c.get('shared_doubao_key_api_resource'))
    lines.append('可以转文字：' + ('是' if c['video_transcription_ready'] else '否') + (f'（上次验证通过的是{kind}；账号权益变了要到真识别时才知道）' if kind and c['video_transcription_ready'] else ''))
    lines.append('可以完整剪辑并放进达芬奇：' + ('是（首次连接达芬奇时以实际交付为准）' if c['end_to_end_ready'] else '否'))
    lines += ['还缺：' + item for item in c['missing']]
    return '\n'.join(lines)


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--align-python',type=Path)
    parser.add_argument('--summary',action='store_true',help='只输出中文清单')
    args=parser.parse_args()
    checks=collect_checks(args.align_python)
    print(summary(checks) if args.summary else json.dumps(checks, ensure_ascii=False, indent=2))
