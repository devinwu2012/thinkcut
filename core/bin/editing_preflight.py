"""Check installed alignment dependencies without loading/downloading a model."""
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[2] / 'bin')); import thinkcut_config      # noqa: E402  本机文件夹名（含旧名兼容）在配置档模块里


def alignment_python(python=None):
    """对齐环境的 Python：--align-python、AI_EDIT_ALIGN_PYTHON，否则默认位置（气口精修的语音检测也用它，engine/src/pause_refine.py）。"""
    # 2026-10-29：原件在 NAS 上程序目录的 环境/；macOS 27.2 不准运行 NAS 上的程序，用 Mac 上的运行副本（在使用版程序目录跑 bash bin/sync_env.sh 恢复）
    default=thinkcut_config.app_dir()/'环境'/thinkcut_config.env_name('align')/'bin'/'python'
    return Path(python or os.environ.get('AI_EDIT_ALIGN_PYTHON',str(default))).expanduser()


def alignment_runtime(python=None):
    path=alignment_python(python)
    result={'alignment_python':str(path),'alignment_dependencies_ready':False,'alignment_error':None}
    if not path.is_file() or not os.access(path,os.X_OK):
        result['alignment_error']='缺少本机对齐环境；用 --align-python 或 AI_EDIT_ALIGN_PYTHON 指定已有 qwen_asr Python'
        return result
    try:
        check=subprocess.run([str(path),'-c',
            'import qwen_asr, torch, transformers, soundfile, accelerate; '
            'import json, importlib.metadata as m; '
            'print(json.dumps({k:m.version(k) for k in ["qwen-asr","torch","transformers","soundfile","accelerate"]}))'],
            capture_output=True,text=True,timeout=30)
        if check.returncode:
            result['alignment_error']='对齐依赖无法导入，请检查该 Python 的 qwen_asr/torch/transformers/soundfile/accelerate'
        else:
            result['alignment_versions']=json.loads(check.stdout.strip().splitlines()[-1])
            result['alignment_dependencies_ready']=True
    except (OSError,subprocess.TimeoutExpired,ValueError,IndexError):
        result['alignment_error']='对齐依赖检查失败或超过 30 秒；没有下载/加载模型'
    return result
