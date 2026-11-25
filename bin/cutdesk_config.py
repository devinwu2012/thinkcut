#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旧名：2026-11-25 起叫 bin/thinkcut_config.py，这里只转过去。
留着是因为：用户自己的 IP 规矩（ThinkCut配置档/<IP>/规矩.md）、老片子的说明、公开版的老 README 里写的是旧命令。
import 拿到的是同一个模块（不是一份拷贝）；当命令跑（python3 bin/cutdesk_config.py ...）也和新名字一样。"""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    runpy.run_path(str(HERE / 'thinkcut_config.py'), run_name='__main__')
else:
    sys.path.insert(0, str(HERE))
    import thinkcut_config  # noqa: E402
    sys.modules[__name__] = thinkcut_config
