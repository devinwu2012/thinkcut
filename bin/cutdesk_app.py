#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旧名：2026-11-25 起叫 bin/thinkcut_app.py，这里只转过去。
留着是因为：已经装好的 ThinkCut.app（11-25 之前构建的）按这个名字找程序文件夹、起停首页；重新构建后就不再用它。
import 拿到的是同一个模块（不是一份拷贝）；当命令跑（python3 bin/cutdesk_app.py ...）也和新名字一样。"""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    runpy.run_path(str(HERE / 'thinkcut_app.py'), run_name='__main__')
else:
    sys.path.insert(0, str(HERE))
    import thinkcut_app  # noqa: E402
    sys.modules[__name__] = thinkcut_app
