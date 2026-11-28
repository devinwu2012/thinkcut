# -*- coding: utf-8 -*-
"""设置页（2026-11-26 用户定）：首页和审片台都能进，配 AI（Claude Code／Codex）、豆包语音 Key、蚁小二 Key。

- AI：自动检测本机装了哪些 AI 命令行、登没登录；“登录”在终端里跑官方的登录命令（账号由用户自己登，程序不碰密码）；选默认引擎。
- 豆包语音：Key 存这台 Mac 的钥匙串，复用 core/bin/doubao_credentials.py（save_key／load_key／验证记号）；“测试”用系统朗读生成两秒的话实测识别。
- 蚁小二：Key 存钥匙串里 yxer-safe 用的那一条（服务 cn.thinkcut.yixiaoer.cli，账户 api-key），用 bin/keychain.py 存取。
钥匙不回显（接口只回“配好了没有、存在哪”）、不进文件、不进日志、不给 AI。所有写操作只收设置页自己（带页面口令）发的请求，由首页／审片台的服务把关。
测试设 THINKCUT_NO_KEYCHAIN：豆包照 doubao_credentials 的老规矩落到 THINKCUT_SECRETS_DIR 的受限文件，蚁小二不存（钥匙串没开）。
"""
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import aitools, design, engine

PAGE = engine.SKILL / 'assets' / '设置' / 'index.html'
YX_SERVICE, YX_ACCOUNT = 'cn.thinkcut.yixiaoer.cli', 'api-key'


class Oops(Exception):
    pass


def _paths():
    for p in (engine.PROGRAM / 'bin', engine.PROGRAM / 'core' / 'bin'):
        if str(p) not in sys.path: sys.path.insert(0, str(p))


def _keychain():
    _paths(); import keychain
    return keychain


def _doubao():
    _paths(); import doubao_credentials
    return doubao_credentials


# ---------- 读 ----------
def doubao_info():
    try:
        dc = _doubao(); where = dc.key_source()
    except Exception as e: return {'configured': False, 'where': '', 'error': f'读不了：{type(e).__name__}'}
    ok = False
    with contextlib.suppress(Exception):
        import doubao_direct_asr as A
        ok = any(dc.verified_for_current_key(r) for r in A.RESOURCES) if where else False
    return {'configured': bool(where), 'where': where or '', 'verified': ok}


def yxer_info():
    kc = _keychain(); have = None; err = ''
    if not kc.enabled(): err = '测试模式：不碰钥匙串'
    else:
        try: have = kc.get(YX_SERVICE, YX_ACCOUNT) is not None
        except RuntimeError as e: err = str(e)
    return {'configured': bool(have), 'installed': bool(shutil.which('yxer-safe') or shutil.which('yxer')), 'error': err}


def info(fresh=False):
    return {'ok': True, 'engines': [{**e, 'model': engine.model_of(e['id'])} for e in engine.all_status(fresh)], 'engine': engine.chosen(), 'doubao': doubao_info(), 'yxer': yxer_info(),
            'keychain': _keychain().enabled()}


# ---------- 写（只收设置页带口令的请求） ----------
def set_engine(q):
    try: st = engine.choose(str(q.get('engine') or ''))
    except ValueError as e: raise Oops(str(e)) from None
    return {'engine': st['id'], 'status': st}


def set_model(q):
    try: return {'model': engine.set_model(str(q.get('engine') or ''), q.get('model'))}
    except ValueError as e: raise Oops(str(e)) from None


def do_login(q):
    try: return engine.login(str(q.get('engine') or ''))
    except ValueError as e: raise Oops(str(e)) from None


def save_doubao(q):
    key = str(q.get('key') or '')
    try: where = _doubao().save_key(key)
    except ValueError: raise Oops('Key 格式不对：要粘贴豆包语音控制台里那串完整的 API Key（不含空格、换行）') from None
    except (RuntimeError, OSError) as e: raise Oops(f'没存上：{e}') from None
    return {'where': where}


def sample_audio(folder):
    """用系统朗读生成一小段 16k 单声道 WAV（没有麦克风、不用素材）。"""
    aiff, wav = Path(folder) / 'test.aiff', Path(folder) / 'test.wav'
    voice = ['-v', 'Tingting'] if 'Tingting' in subprocess.run(['say', '-v', '?'], capture_output=True, text=True).stdout else []
    subprocess.run(['say', *voice, '-o', str(aiff), '你好，这是思刻的语音识别测试。'], check=True, capture_output=True, timeout=30)
    subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', str(aiff), str(wav)], check=True, capture_output=True, timeout=30)
    return wav


def _text_of(result):
    r = result.get('result') if isinstance(result, dict) else None
    if isinstance(r, list): r = r[0] if r else {}
    return (r or {}).get('text') or ''


def test_doubao(q=None):
    """拿存好的 Key 实测识别一小段（按量计费，几分钱以内）。不回显 Key，报错里的 Key 由 doubao_direct_asr 隐藏。"""
    dc = _doubao()
    try: key = dc.load_key()
    except RuntimeError as e: raise Oops(str(e)) from None
    if not key: raise Oops('还没有豆包 Key：先在上面粘贴保存')
    import doubao_direct_asr as A
    with tempfile.TemporaryDirectory(prefix='thinkcut-asrtest-') as d:
        try: wav = sample_audio(d)
        except Exception as e: raise Oops(f'生成测试音频失败（系统朗读不可用）：{type(e).__name__}') from None
        try: result, used = A.recognize_any(wav, key, timeout=120)
        except Exception as e: raise Oops(f'识别没通过：{str(e).replace(key, "[已隐藏]")}') from None
    with contextlib.suppress(OSError): dc.mark_verified(key, used)
    return {'text': _text_of(result), 'resource': A.NAMES.get(used, used)}


def save_yxer(q):
    key = str(q.get('key') or '').strip()
    if not key or '\n' in key or len(key) > 1024: raise Oops('Key 不能为空、不能有换行')
    kc = _keychain()
    try: kc.put(YX_SERVICE, YX_ACCOUNT, key)
    except RuntimeError as e: raise Oops(f'没存上：{e}') from None
    return {'where': '钥匙串'}


def set_col_order(q):
    """审片台顶部栏目的顺序（2026-11-26 用户：“这几个顺序应该是可以拖动的……根据用户的喜好”）：存在本机设置里，所有项目共用；空＝恢复默认顺序。"""
    o = q.get('order') or []
    if not isinstance(o, list) or len(o) > 16 or not all(isinstance(x, str) and 0 < len(x) <= 12 for x in o): raise Oops('栏目顺序不对')
    engine.write_settings(栏目顺序=o)
    return {'order': o}


def tools_info():
    """“AI 工具”体检（2026-11-28）：每个 AI 能用的技能、MCP、依赖。"""
    return {'ok': True, **aitools.report()}


def fix_tools(q=None):
    """一键补：把白名单技能链接进 ~/.claude/skills 和 ~/.codex/skills（只建链接，不覆盖）。依赖要装软件，只给命令、不替你装。"""
    return {**aitools.fix_links(), **aitools.report()}


POST = {'/api/settings/tools/fix': fix_tools, '/api/settings/engine': set_engine, '/api/settings/colorder': set_col_order, '/api/settings/model': set_model, '/api/settings/login': do_login, '/api/settings/refresh': lambda q: info(True),
        '/api/settings/doubao': save_doubao, '/api/settings/doubao/test': test_doubao, '/api/settings/yxer': save_yxer}


def page(session):
    return design.inject(PAGE.read_bytes().replace(b'__SHENPIAN_TOKEN__', session.encode()))


def dumps(x): return json.dumps(x, ensure_ascii=False)
