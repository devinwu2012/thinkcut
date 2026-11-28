# -*- coding: utf-8 -*-
"""达芬奇 MCP 规矩和护栏（2026-11-28）离线测试：不连真的达芬奇。护栏端到端那段要一个装了 mcp 包的 Python
（THINKCUT_MCP_PYTHON，或用户 Codex 配置里达芬奇 MCP 的那个），没有就只跑规矩部分。"""
import json, os, subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import davinci_mcp_policy as P      # noqa: E402

CFG = textwrap.dedent('''
    model = "x"
    [mcp_servers.davinci-resolve]
    command = "/opt/dv/venv/bin/python"
    args = ["/opt/dv/src/server.py"]
    startup_timeout_sec = 120.0

    [mcp_servers.davinci-resolve.env]
    PYTHONPATH = "/Library/Scripting/Modules"
    TOKEN = 'secret-1'

    [mcp_servers.other]
    command = "npx"
''')


class Policy(unittest.TestCase):
    def test_check(self):
        self.assertEqual(P.check('timeline', 'list'), (True, '', False))
        self.assertEqual(P.check('timeline', 'get_track_count')[2], False)
        self.assertEqual(P.check('timeline', 'add_track'), (True, '', True))                 # 认不出是只读的：当写操作，要拿锁
        for tool, action in (('render', 'get_formats'), ('project_manager', 'list'), ('resolve_control', 'quit'), ('setup', 'set')):
            self.assertFalse(P.check(tool, action)[0], tool)                                 # 整个工具不给，只读的动作也不给
        for action in ('delete_clips', 'delete_timelines', 'export', 'export_timeline_checked', 'replace_clip', 'unlink', 'clear_mark_in_out', 'grab_and_export'):
            self.assertFalse(P.check('timeline', action)[0], action)
        self.assertTrue(P.BACKUP.search('v4_主线_archived_v01') and P.BACKUP.search('x_rolled_back_101112') and not P.BACKUP.search('v4_主线'))

    def test_upstream_from_codex_config(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'config.toml'; f.write_text(CFG, encoding='utf-8')
            up = P.upstream(config=f)
            self.assertEqual(up['command'], '/opt/dv/venv/bin/python'); self.assertEqual(up['args'], ['/opt/dv/src/server.py'])
            self.assertEqual(up['env'], {'PYTHONPATH': '/Library/Scripting/Modules', 'TOKEN': 'secret-1'}); self.assertEqual(up['startup_timeout_sec'], 120.0)
            go = P.launcher(config=f)
            self.assertEqual(go['command'], '/opt/dv/venv/bin/python'); self.assertEqual(go['args'], [str(P.GUARD), '--server', 'davinci-resolve'])
            self.assertNotIn('secret-1', json.dumps(go))                                      # 口令不进命令行
            self.assertIsNone(P.upstream('nothing', config=f)); self.assertIsNone(P.launcher('other', config=f))     # 没配、不是 Python 起的
            f.write_text(CFG.replace('startup_timeout_sec = 120.0', 'enabled = false'), encoding='utf-8'); self.assertIsNone(P.upstream(config=f))


FAKE_UP = '''
import os, json
from mcp.server.fastmcp import FastMCP
m = FastMCP("fake-dv")
def rec(t, a): open(os.environ["FAKE_DV_LOG"], "a").write(json.dumps([t, a]) + "\\n")
@m.tool()
def timeline(action: str, params: dict | None = None) -> str:
    rec("timeline", action); return "timeline " + action + " ok"
@m.tool()
def render(action: str, params: dict | None = None) -> str:
    rec("render", action); return "rendered"
@m.tool()
def timeline_versioning(action: str, params: dict | None = None) -> str:
    rec("timeline_versioning", action); return "ok"
m.run()
'''
CLIENT = '''
import asyncio, os, sys, fcntl, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    out = {}
    p = StdioServerParameters(command=sys.executable, args=[sys.argv[1]], env={**os.environ})
    async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        out["tools"] = [t.name for t in (await s.list_tools()).tools]
        for t, a in [("timeline", "list"), ("timeline", "delete_clips"), ("render", "start"), ("timeline", "export"), ("timeline", "add_track"), ("timeline", "set_name")]:
            res = await s.call_tool(t, {"action": a}); out[t + " " + a] = res.isError
        f = open(os.environ["THINKCUT_RESOLVE_LOCK"], "a+"); fcntl.flock(f, fcntl.LOCK_EX)
        out["locked write"] = (await s.call_tool("timeline", {"action": "add_track"})).isError
        out["locked read"] = (await s.call_tool("timeline", {"action": "get_tracks"})).isError
    print(json.dumps(out))
asyncio.run(main())
'''


def mcp_python():
    for c in (os.environ.get('THINKCUT_MCP_PYTHON'), (P.upstream() or {}).get('command')):
        if c and Path(c).is_file() and subprocess.run([c, '-c', 'import mcp'], capture_output=True).returncode == 0: return c
    return None


class Guard(unittest.TestCase):
    def test_guard_end_to_end(self):
        py = mcp_python()
        if not py: print('（没有装了 mcp 包的 Python：护栏端到端这段没跑）'); return
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); (d / 'up.py').write_text(FAKE_UP, encoding='utf-8'); (d / 'client.py').write_text(CLIENT, encoding='utf-8')
            env = {**os.environ, 'FAKE_DV_LOG': str(d / 'log'), 'THINKCUT_RESOLVE_LOCK': str(d / 'lock'),
                   'THINKCUT_DV_UPSTREAM': json.dumps({'command': py, 'args': [str(d / 'up.py')]})}
            r = subprocess.run([py, str(d / 'client.py'), str(P.GUARD)], capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            out = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(out['tools'], ['timeline'])                                    # render、timeline_versioning 不列出
            self.assertEqual([out[k] for k in ('timeline list', 'timeline delete_clips', 'render start', 'timeline export', 'timeline add_track', 'timeline set_name')],
                             [False, True, True, True, False, False])
            self.assertTrue(out['locked write']); self.assertFalse(out['locked read'])       # 别处拿着写入锁：写拒、读照常
            calls = [json.loads(x) for x in (d / 'log').read_text().splitlines()]
            self.assertEqual(calls, [['timeline', 'list'], ['timeline_versioning', 'begin_run'], ['timeline', 'add_track'], ['timeline', 'set_name'], ['timeline', 'get_tracks']])


if __name__ == '__main__':
    unittest.main()
