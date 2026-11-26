# 界面规格（2026-11-26 用户定：“把界面规格固化下来，避免以后改乱”）：仓库根目录 design/tokens.css 是全站唯一一份颜色、
# 发丝线、圆角、阴影、字号、间距、动效变量。页面（首页、审片台、口播精剪编辑器、设置页）里写 <!--THINKCUT_TOKENS-->，
# 服务发页面时换成这份变量；页面自己不再写死颜色和数值（bin/test_design.py 查）。说明见 design/README.md。
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]          # 仓库根目录（studio/skill/thinkcut-edit/scripts/thinkcut/ 往上五层）
TOKENS = ROOT / 'design' / 'tokens.css'
MARK = b'<!--THINKCUT_TOKENS-->'


def css():
    """规格变量原文；找不到文件时给空（页面照样能开，只是没有颜色——测试会先拦住这种情况）。"""
    try: return TOKENS.read_bytes()
    except OSError: return b''


def inject(page: bytes) -> bytes:
    """把页面里的占位换成 <style>规格变量</style>。"""
    return page.replace(MARK, b'<style id="thinkcut-tokens">' + css() + b'</style>', 1)
