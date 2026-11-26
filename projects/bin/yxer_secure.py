#!/usr/bin/env python3
"""钥匙串凭据 + 仅在内存中提供配置；禁止通过此入口发布。"""
import ctypes
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SERVICE = b"cn.thinkcut.yixiaoer.cli"
ACCOUNT = b"api-key"


def keychain():
    lib = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    lib.SecKeychainFindGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    lib.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    lib.SecKeychainAddGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    lib.SecKeychainItemModifyAttributesAndData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p]
    lib.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    lib.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    return lib


def credential(value=None):
    if os.environ.get('THINKCUT_AI_BACKGROUND'):      # 后台 AI 不直接读钥匙（2026-11-26）：上传、存草稿由审片台代办跑这个程序
        raise RuntimeError('后台 AI 不直接读蚁小二的钥匙：yxer-safe 会自动交给审片台代办')
    lib = keychain()
    size, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
    status = lib.SecKeychainFindGenericPassword(None, len(SERVICE), SERVICE, len(ACCOUNT), ACCOUNT, ctypes.byref(size), ctypes.byref(data), ctypes.byref(item))
    old = None
    if status == 0:
        old = ctypes.string_at(data, size.value)
        lib.SecKeychainItemFreeContent(None, data)
    elif status != -25300:
        raise RuntimeError(f'无法访问 macOS 钥匙串（OSStatus {status}）；不会降级为明文。')
    try:
        if value is None:
            if old is None:
                raise RuntimeError('钥匙串里还没有蚁小二 Key。运行 yxer-safe key set。')
            return old.decode()
        raw = value.encode()
        if not raw or '\n' in value:
            raise RuntimeError('Key 不能为空或包含换行。')
        status = (lib.SecKeychainItemModifyAttributesAndData(item, None, len(raw), raw) if old is not None else lib.SecKeychainAddGenericPassword(None, len(SERVICE), SERVICE, len(ACCOUNT), ACCOUNT, len(raw), raw, None))
        if status:
            raise RuntimeError(f'钥匙串写入失败（OSStatus {status}）。')
    finally:
        if item.value:
            core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            core.CFRelease.argtypes = [ctypes.c_void_p]
            core.CFRelease(item)


def native_cli():
    command = shutil.which('yxer')
    if not command:
        raise RuntimeError('未安装 yxer CLI。')
    entry = Path(command).resolve()
    candidate = entry.parent.parent / 'bin-native' / 'yxer'
    return str(candidate if candidate.is_file() else entry)


# 2026-11-13 审查：以前把当前环境变量原样传给 yxer。CLI 认 YIXIAOER_* 这类能改服务器地址、配置位置的变量，
# 被人（或别的工具）设了以后 Key 可能发到别的地址。现在只传白名单里的，其余一律不传。
KEEP_ENV = ('PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'LANGUAGE', 'TMPDIR', 'TZ', 'TERM')
# 下面这些被设置时在标准错误提示一句“已忽略”（其余不在白名单的变量也不传，只是不提示）：改地址、走代理、换证书、往进程里塞代码的
RISKY_ENV = ('YIXIAOER_', 'YXER_', 'DYLD_', 'LD_', 'SSL_CERT_', 'CURL_CA_')
RISKY_NAMES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'NODE_OPTIONS', 'NODE_EXTRA_CA_CERTS', 'NODE_TLS_REJECT_UNAUTHORIZED')
TIMEOUT = 300                # 普通命令最多等几秒；用环境变量 THINKCUT_YXER_TIMEOUT 调
UPLOAD_TIMEOUT = 6 * 3600    # 传文件最多等几秒（几个 GB 的成片要很久）；用 THINKCUT_YXER_UPLOAD_TIMEOUT 调


def child_env(environ):
    """传给 yxer 的环境：只留白名单和 LC_*。返回 (环境, 被去掉的有风险变量名)，名字给人看，值不打印。"""
    env = {k: v for k, v in environ.items() if k in KEEP_ENV or k.startswith('LC_')}
    ignored = sorted(k for k in environ if k not in env and (k.upper().startswith(RISKY_ENV) or k.upper() in RISKY_NAMES))
    return env, ignored


def time_limit(args, environ):
    name, default = ('THINKCUT_YXER_UPLOAD_TIMEOUT', UPLOAD_TIMEOUT) if args[:1] == ['upload'] else ('THINKCUT_YXER_TIMEOUT', TIMEOUT)
    raw = environ.get(name, '')
    try:
        value = float(raw) if raw.strip() else default
    except ValueError:
        value = 0
    if value <= 0:
        raise RuntimeError(f'环境变量 {name} 要写成大于 0 的秒数，现在是「{raw}」。改对或去掉它再试。')
    return name, value


def allowed(args):
    if not args:
        return False
    if args[0] in ('--version', '--help', 'doctor', 'prepare', 'schema', 'query', 'upload', 'validate'):
        return True
    if len(args) < 2:
        return False
    return (args[0] == 'config' and args[1] == 'get' or
            args[0] == 'accounts' and args[1] == 'list' or
            args[0] == 'draft' and args[1] == 'save' or
            args[0] == 'material' and args[1] in ('add', 'create', 'list', 'groups'))


def invoke(args):
    if not allowed(args):
        raise RuntimeError('此入口只允许检查、上传、素材登记和保存蚁小二内部草稿；发布请在客户端手动确认。')
    env, ignored = child_env(os.environ)
    limit_name, limit = time_limit(args, os.environ)
    if ignored:
        print('提示：已忽略这些环境变量，没有传给蚁小二 CLI（服务器地址固定为官方地址）：' + '、'.join(ignored), file=sys.stderr)
    secret = credential()
    config =json.dumps({'apiKey': secret, 'apiUrl': 'https://www.yixiaoer.cn/api'}).encode()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, config)
        os.close(write_fd)
        write_fd = None
        # npm 的 Node 启动器会关闭额外 fd；直接运行它随包安装的同一原生 CLI。
        # 目录只含指向内存 fd 的符号链接，不含凭据文件；沿用既有版本戳。
        base = Path.home() / '.yxer'
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.memory-config-', dir=base) as runtime:
            config_path = Path(runtime) / 'config.json'
            config_path.symlink_to(f'/dev/fd/{read_fd}')
            stamp = base / 'skills.stamp'
            if stamp.exists():
                (Path(runtime) / 'skills.stamp').symlink_to(stamp)
            timed_out = False
            try:
                result = subprocess.run([native_cli(), *args], env={**env, 'YIXIAOER_CONFIG': str(config_path)}, pass_fds=(read_fd,), capture_output=True, text=True, timeout=limit)
            except subprocess.TimeoutExpired as exc:      # run() 已经把子进程结束掉；把它已经输出的内容照样带出来
                result, timed_out = exc, True
        for stream, content in ((sys.stdout, result.stdout), (sys.stderr, result.stderr)):
            if isinstance(content, bytes): content = content.decode(errors='replace')
            stream.write((content or '').replace(secret, '[REDACTED]'))
        if timed_out:
            what = '传文件' if args[:1] == ['upload'] else '这条命令'
            unsure = '草稿可能已经存上，先让用户在蚁小二客户端看有没有这条草稿，不要直接重试。' if args[:2] == ['draft', 'save'] and '--dry-run' not in args else '先检查网络和蚁小二服务是否正常，再重试；'
            print(f'停止：蚁小二 CLI {what}（{" ".join(args[:2])}）等了 {limit:.0f} 秒还没结束，已中止。{unsure}确实需要更久时把环境变量 {limit_name} 设成更大的秒数。', file=sys.stderr)
            return 124
        return result.returncode
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def main():
    args = sys.argv[1:]
    try:
        if args == ['key', 'set']:
            secret = getpass.getpass('输入蚁小二 Key（不回显）：')
            credential(secret)
            print('已存入 macOS 登录钥匙串；未写入配置或项目明文文件。')
            return 0
        if args == ['key', 'check']:
            credential()
            print('钥匙串凭据可读取（内容不显示）。')
            return 0
        return invoke(args)
    except (RuntimeError, OSError) as exc:
        print('停止：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin')); import thinkcut_broker      # noqa: E402
    if thinkcut_broker.active(): sys.exit(thinkcut_broker.forward(__file__, sys.argv[1:]))     # 后台 AI 不拿钥匙：交给审片台代办（2026-11-26）
    sys.exit(main())
