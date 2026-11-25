#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macOS 钥匙串里存取一条“通用密码”（2026-11-25 起豆包钥匙存这里，每台 Mac 各存各的；用户定：不放 NAS 共用）。

直接调系统的 Security 框架（ctypes），不经 `security` 命令：那样钥匙会出现在命令行参数里，别的进程看得见。
设了环境变量 THINKCUT_NO_KEYCHAIN（测试都设）就当没有钥匙串：get 返回 None，put 报错，不碰用户真的钥匙串。
"""
import ctypes
import os
import sys

NOT_FOUND = -25300      # errSecItemNotFound


def enabled():
    return sys.platform == 'darwin' and not os.environ.get('THINKCUT_NO_KEYCHAIN')


def _lib():
    lib = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    lib.SecKeychainFindGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p,
                                                   ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    lib.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    lib.SecKeychainAddGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p,
                                                  ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    lib.SecKeychainItemModifyAttributesAndData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p]
    lib.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    lib.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    return lib


def _release(item):
    if item.value:
        core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        core.CFRelease.argtypes = [ctypes.c_void_p]
        core.CFRelease(item)


def _find(lib, service, account):
    s, a = service.encode(), account.encode()
    size, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
    status = lib.SecKeychainFindGenericPassword(None, len(s), s, len(a), a, ctypes.byref(size), ctypes.byref(data), ctypes.byref(item))
    if status == NOT_FOUND: return None, item
    if status: raise RuntimeError(f'无法读取 macOS 钥匙串（OSStatus {status}）')
    value = ctypes.string_at(data, size.value)
    lib.SecKeychainItemFreeContent(None, data)
    return value.decode('utf-8'), item


def get(service, account):
    """取出来的字符串；没有这一条返回 None；钥匙串打不开时报 RuntimeError。"""
    if not enabled(): return None
    lib = _lib()
    value, item = _find(lib, service, account)
    _release(item)
    return value


def put(service, account, value):
    """新建或改写这一条。"""
    if not enabled(): raise RuntimeError('钥匙串没开（THINKCUT_NO_KEYCHAIN 或不是 macOS）')
    raw = value.encode('utf-8')
    lib = _lib()
    old, item = _find(lib, service, account)
    try:
        if old is not None: status = lib.SecKeychainItemModifyAttributesAndData(item, None, len(raw), raw)
        else:
            s, a = service.encode(), account.encode()
            status = lib.SecKeychainAddGenericPassword(None, len(s), s, len(a), a, len(raw), raw, None)
        if status: raise RuntimeError(f'写入 macOS 钥匙串失败（OSStatus {status}）')
    finally:
        _release(item)
