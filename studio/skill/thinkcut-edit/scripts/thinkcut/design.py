# 界面规格（2026-11-26 用户定：“把界面规格固化下来，避免以后改乱”）：仓库根目录 design/tokens.css 是全站唯一一份颜色、
# 发丝线、圆角、阴影、字号、间距、动效变量。页面（首页、审片台、口播精剪编辑器、设置页）里写 <!--THINKCUT_TOKENS-->，
# 服务发页面时换成这份变量；页面自己不再写死颜色和数值（bin/test_design.py 查）。说明见 design/README.md。
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]          # 仓库根目录（studio/skill/thinkcut-edit/scripts/thinkcut/ 往上五层）
TOKENS = ROOT / 'design' / 'tokens.css'
MARK = b'<!--THINKCUT_TOKENS-->'


def css():
    """规格变量原文；找不到文件时给空（页面照样能开，只是没有颜色——测试会先拦住这种情况）。"""
    try: return TOKENS.read_bytes()
    except OSError: return b''


def is_dev():
    """开发版（程序文件夹名以“-开发”结尾，或是 git worktree；THINKCUT_APP_FLAVOR 可强制）：和 bin/thinkcut_config.py 的 flavor() 同一条规矩。"""
    f = os.environ.get('THINKCUT_APP_FLAVOR')
    if f in ('dev', 'release'): return f == 'dev'
    return ROOT.name.endswith('-开发') or (ROOT / '.git').is_file()


# 开发版标签（2026-11-27 用户：“开发版的软件的左上角应该是一直写‘开发版’，要不然我分不清”）：首页、审片台、设置页的程序名／标题后面一直挂一个橙色“开发版”，窄窗口也不藏；正式版没有。
DEV_BADGE = (b'<style id="thinkcut-dev">.tc-dev{display:inline-flex;align-items:center;flex:none;margin-left:8px;padding:1px 7px;border-radius:var(--r-s);'
             b'background:var(--s-todo);color:var(--card);font-size:var(--fs-11);font-weight:var(--fw-b);line-height:var(--lh-11);letter-spacing:0;vertical-align:middle}</style>'
             b'<script>addEventListener("DOMContentLoaded",()=>{const t=document.querySelector(".appname")||document.querySelector(".tb-title h1")||document.querySelector(".toolbar h1");'
             b'const b=document.createElement("span");b.className="tc-dev";b.textContent="\u5f00\u53d1\u7248";b.title="\u5f00\u53d1\u7248\uff1a\u6539\u7a0b\u5e8f\u7528\u7684\u90a3\u4e00\u4efd";'
             b'if(t)t.insertAdjacentElement("afterend",b);document.title="\u3010\u5f00\u53d1\u7248\u3011"+document.title})</script>')


CHAT = ROOT / 'studio' / 'skill' / 'thinkcut-edit' / 'assets' / '对话' / '对话.html'      # AI 对话侧栏（2026-11-27），首页和审片台共用
CHAT_MARK = b'<!--THINKCUT_CHAT-->'


def inject(page: bytes) -> bytes:
    """把页面里的占位换成 <style>规格变量</style>（开发版再挂上“开发版”标签）；有对话侧栏占位的，换成侧栏（两边共用一份）。"""
    page = page.replace(MARK, b'<style id="thinkcut-tokens">' + css() + b'</style>' + (DEV_BADGE if is_dev() else b''), 1)
    if CHAT_MARK in page:
        try: chat = CHAT.read_bytes()
        except OSError: chat = b''
        page = page.replace(CHAT_MARK, chat, 1)
    return page
