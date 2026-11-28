# -*- coding: utf-8 -*-
"""达芬奇 MCP 护栏（2026-11-28）：后台 AI（Claude、Codex）启动的是它，它再启动用户装的达芬奇 MCP，把调用一条条转过去。
规矩在 bin/davinci_mcp_policy.py：不列出、也不转整个禁用的工具；按动作名拒删除、渲染导出这类动作；写操作先拿达芬奇写入锁（和代办同一把），
拿不到就拒；第一次写之前开一个批次，同一次 AI 处理只多一条 _archived 备份时间线。
用达芬奇 MCP 自己的 Python 跑（要它装的 mcp 包）：<那个 python> davinci_mcp_guard.py [--server davinci-resolve]
测试：THINKCUT_DV_UPSTREAM 给一段 JSON（command、args、env）就不读 Codex 配置。
"""
import asyncio
import contextlib
import fcntl
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import davinci_mcp_policy as P          # noqa: E402

from mcp import ClientSession, StdioServerParameters, types      # noqa: E402
from mcp.client.stdio import stdio_client                        # noqa: E402
from mcp.server.lowlevel import Server                           # noqa: E402
from mcp.server.stdio import stdio_server                        # noqa: E402


def refuse(text):
    return types.CallToolResult(content=[types.TextContent(type='text', text='ThinkCut 护栏拦下：' + text)], isError=True)


@contextlib.contextmanager
def write_lock():
    """拿达芬奇写入锁（不等）：别的会话、代办正在写达芬奇就返回 None。"""
    path = P.lock_file(); path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, 'a+', encoding='utf-8')
    try:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None; return
        try:
            f.seek(0); f.truncate(); f.write(json.dumps({'谁': '后台 AI 经达芬奇 MCP', '进程': os.getpid(), '开始': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False)); f.flush()
            yield f
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        f.close()


async def run(name):
    up = json.loads(os.environ['THINKCUT_DV_UPSTREAM']) if os.environ.get('THINKCUT_DV_UPSTREAM') else P.upstream(name)
    if not up: sys.exit(f'没找到 {name} 的启动方式（Codex 配置 [mcp_servers.{name}]）')
    params = StdioServerParameters(command=up['command'], args=list(up.get('args') or []), env={**os.environ, **(up.get('env') or {})}, cwd=up.get('cwd') or None)
    state = {'run': False}
    async with stdio_client(params) as (r, w), ClientSession(r, w) as up_s:
        info = await up_s.initialize()
        server = Server(name, instructions=(getattr(info, 'instructions', None) or '') +
                        '\n\nThinkCut 护栏：渲染导出、删除、切换工程、装插件这些工具和动作已禁用；写操作要拿达芬奇写入锁，别的会话在写时会被拒，等一会儿再试。'
                        '设速度、交付口播、配 B-roll、花字、建审核线、导出走 ThinkCut 自己的程序（代办）。')

        @server.list_tools()
        async def list_tools():
            return [t for t in (await up_s.list_tools()).tools if t.name not in P.TOOLS_OFF]

        @server.call_tool(validate_input=False)
        async def call_tool(tool, arguments):
            arguments = arguments or {}
            ok, why, write = P.check(tool, arguments.get('action'))
            if not ok: return refuse(f'{tool} {arguments.get("action") or ""}：{why}')
            if not write: return await up_s.call_tool(tool, arguments)
            with write_lock() as held:
                if held is None: return refuse('另一处正在写达芬奇（审片台代办或别的会话），等它做完再试')
                if not state['run']:                   # 同一次处理的修改并成一批：只多一条备份时间线
                    with contextlib.suppress(Exception):
                        await up_s.call_tool('timeline_versioning', {'action': 'begin_run', 'params': {'label': 'ThinkCut 后台 AI', 'initiator': 'thinkcut'}})
                    state['run'] = True
                return await up_s.call_tool(tool, arguments)

        @server.list_prompts()
        async def list_prompts(): return []

        async with stdio_server() as (sr, sw):
            await server.run(sr, sw, server.create_initialization_options())


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    name = a[a.index('--server') + 1] if '--server' in a else P.NAME
    asyncio.run(run(name))


if __name__ == '__main__':
    main()
