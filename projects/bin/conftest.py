"""pytest 用：测试用自己的配置档（“测试IP”当默认 IP），不靠哪个真 IP 的配置（../../bin/make_test_profile.py，2026-11-24）。"""
import atexit, os, shutil, sys, tempfile
from pathlib import Path

if not os.environ.get('THINKCUT_PROFILES'):
    _d = tempfile.mkdtemp(prefix='测试配置档_'); atexit.register(shutil.rmtree, _d, True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
    import importlib; os.environ['THINKCUT_PROFILES'] = str(importlib.import_module('make_test_profile').make(_d))
