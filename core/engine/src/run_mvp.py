"""Freeze the current review and build/verify its Resolve timeline via MCP; never renders."""
import os
import sys
import shutil
import subprocess
from pathlib import Path

from prepare_mvp_delivery import prepare


def resolve_mcp_runtime():
    # 2026-10-29：达芬奇 MCP 原件在 NAS 上程序目录的 环境/，macOS 27.2 不准运行 NAS 上的程序，用 Mac 运行副本（bin/sync_env.sh 恢复）
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'bin')); import thinkcut_config      # 本机文件夹名（含旧名兼容）
    root = Path(os.environ.get('DAVINCI_RESOLVE_MCP_ROOT', str(thinkcut_config.app_dir() / '环境' / 'davinci-resolve-mcp'))).expanduser().resolve()
    python = root / 'venv/bin/python'
    server = root / 'src/server.py'
    if not python.is_file() or not os.access(python, os.X_OK) or not server.is_file():
        raise RuntimeError('Resolve MCP installation not found; set DAVINCI_RESOLVE_MCP_ROOT to its directory')
    return root, python, server


def node_runtime(mcp_root):
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node.js is required for subtitle delivery')
    vendor = mcp_root / 'resolve-advanced/vendor/drp-format'
    env = {**os.environ, 'DAVINCI_RESOLVE_MCP_ROOT': str(mcp_root)}
    bundled = Path(__file__).resolve().parents[3] / '环境/node_modules'      # 字幕组件要的 jszip 等，放 NAS
    if bundled.is_dir():
        env['NODE_PATH'] = os.pathsep.join(filter(None, [env.get('NODE_PATH'), str(bundled)]))
    # Validate the real formatter and its dependencies before changing Resolve.
    result = subprocess.run([node, '-e', 'require(process.argv[1])', str(vendor / 'place-subtitles')],
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Resolve subtitle dependencies unavailable: ' + result.stderr.strip())
    return node, env


def main():
    root, python, _ = resolve_mcp_runtime()
    node_runtime(root)
    package = prepare()
    subprocess.run([str(python), str(Path(__file__).with_name('import_mvp_delivery.py')), str(package)],
                   check=True, env={**os.environ, 'PYTHONUTF8': '1'})
    from web_delivery import publish
    publish(package)


if __name__ == '__main__':
    main()
