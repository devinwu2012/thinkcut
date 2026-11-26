"""豆包 API Key：2026-11-25 起存在这台 Mac 的钥匙串里（每台 Mac 各存一份；用户定：不放 NAS 共用）。

读：先读钥匙串；没有就读老位置的文件（NAS 上 core/secrets/doubao_api_key，以前各台 Mac 共用的那份），读到就顺手存进钥匙串，
旧文件不删（别的 Mac 第一次用时也从它导入）。存：只存钥匙串。设了 THINKCUT_NO_KEYCHAIN（测试都设）时照旧只用文件。
钥匙不打印、不写进项目和说明。"""

import argparse
import html
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "bin"))
import keychain  # noqa: E402
import locations  # noqa: E402  开发版、使用版各在哪
import thinkcut_config  # noqa: E402  本机文件夹名（含旧名兼容）
KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT = "cn.thinkcut.doubao", "api-key"
# 钥匙只有使用版那一份；开发版不复制，用 THINKCUT_SECRETS_DIR 指过去（bin/dev.sh run 会设；2026-11-24）
# 2026-11-24 初稿模式第二次实测：助手在开发版的 worktree 里直接跑 doubao_tts，没经 dev.sh run，读不到钥匙。
# 所以不设变量时也会自己找：本仓库没有钥匙、又是开发版（目录名以“-开发”结尾）或它的 worktree，就读使用版那一份（只读，不复制）。
# 2026-11-26 起开发版不再是使用版的兄弟目录：使用版按开发版的 git origin 找（bin/locations.py）。


def prod_secrets_dir(repo=None):
    repo = Path(repo) if repo else ROOT.parent
    if not locations.is_dev(locations.main_repo(repo)) and not locations.is_dev(repo):
        return None
    prod = locations.prod_of(repo)
    return prod / "core" / "secrets" if prod else None


def pick_secrets_dir(env=None, repo=None):
    env = os.environ if env is None else env
    if env.get("THINKCUT_SECRETS_DIR"):
        return Path(env["THINKCUT_SECRETS_DIR"])
    own = (Path(repo) / "core" if repo else ROOT) / "secrets"
    if (own / "doubao_api_key").is_file():
        return own
    prod = prod_secrets_dir(repo)
    return prod if prod and (prod / "doubao_api_key").is_file() else own


SECRETS = pick_secrets_dir()      # 老位置（文件）：钥匙串里没有时从这里读、导入
KEY_FILE = SECRETS / "doubao_api_key"
OLD_VERIFIED_FILE = SECRETS / "doubao_verified.json"
# “这把钥匙验证过能用”的记号（只有钥匙的指纹，不是钥匙）跟着钥匙走：钥匙串开着时放这台 Mac 自己的程序数据文件夹，老位置照读
VERIFIED_FILE = (thinkcut_config.app_dir() / "doubao_verified.json") if keychain.enabled() else OLD_VERIFIED_FILE


def validate_key(key):
    key = key.strip()
    if not 20 <= len(key) <= 1024 or not key.isascii() or any(ord(ch) < 33 or ord(ch) == 127 for ch in key):
        raise ValueError("API Key 格式无效")
    return key


def key_source():
    """钥匙在哪：'钥匙串'、'文件'，都没有 None。钥匙串打不开时报 RuntimeError。"""
    if keychain.get(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT): return "钥匙串"
    return "文件" if load_file_key() else None


def load_key():
    if os.environ.get('THINKCUT_AI_BACKGROUND'):      # 后台 AI 不拿钥匙原文（2026-11-26）：识别、合成由审片台代办
        raise RuntimeError('后台 AI 不直接读豆包钥匙：识别、合成照常运行 transcribe_video.py／doubao_tts.py／doubao_direct_asr.py，会自动交给审片台代办')
    try:
        key = keychain.get(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except RuntimeError as exc:
        print(f"提醒：{exc}，这次改用老位置的钥匙文件", file=sys.stderr); key = None
    if key:
        try: return validate_key(key)
        except ValueError: raise RuntimeError("钥匙串里的豆包密钥格式无效，请重新配置：python3 core/bin/doubao_credentials.py") from None
    key = load_file_key()
    if key and keychain.enabled():
        try: keychain.put(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, key)      # 第一次用：老文件里的钥匙存进这台 Mac 的钥匙串（文件不删）
        except RuntimeError as exc: print(f"提醒：{exc}，钥匙没存进钥匙串，这次照常用文件里的", file=sys.stderr)
    return key


def load_file_key():
    if KEY_FILE.parent.is_symlink() or KEY_FILE.is_symlink():
        raise RuntimeError("密钥路径不能是符号链接")
    if not KEY_FILE.is_file():
        return None
    try:
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        validate_key(key)
    except (ValueError, UnicodeError):
        raise RuntimeError("NAS 豆包密钥文件为空或格式无效") from None
    return key


def save_key(key):
    if os.environ.get('THINKCUT_AI_BACKGROUND'): raise RuntimeError('后台 AI 不能改钥匙：请用户在 ThinkCut 设置页里配')
    key = validate_key(key)
    if keychain.enabled():
        keychain.put(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, key); return "钥匙串"
    directory = KEY_FILE.parent
    if directory.is_symlink() or KEY_FILE.is_symlink():
        raise RuntimeError("密钥路径不能是符号链接")
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".doubao-key-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(key + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, KEY_FILE)
        os.chmod(KEY_FILE, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if stat.S_IMODE(KEY_FILE.stat().st_mode) & 0o077:
        raise RuntimeError("NAS 未保持密钥文件的受限权限，请检查共享权限")
    return "文件"


def mark_verified(key, resource_id):
    payload = {
        "key_sha256": hashlib.sha256(key.encode("utf-8")).hexdigest(),
        "resource_id": resource_id,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    VERIFIED_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".doubao-verified-", dir=VERIFIED_FILE.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, VERIFIED_FILE)
        os.chmod(VERIFIED_FILE, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def clear_verified():
    """验证过的识别现在被豆包拒绝了：去掉标记，自检就不会再说“可以转文字”。"""
    for f in {VERIFIED_FILE, OLD_VERIFIED_FILE}: f.unlink(missing_ok=True)


def verified_for_current_key(resource_id, key=None):
    # Check the exact key that will be used, even if the NAS key rotates now.
    key = load_key() if key is None else key
    marker = next((f for f in (VERIFIED_FILE, OLD_VERIFIED_FILE) if f.is_file()), None)
    if not key or marker is None:
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return isinstance(payload, dict) and payload.get("resource_id") == resource_id and payload.get("key_sha256") == hashlib.sha256(key.encode("utf-8")).hexdigest()


def serve():
    csrf = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, body, status=200):
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.allowed() or self.path != "/":
                return self.reply("拒绝访问", 403)
            where_page = ("只保存到这台 Mac 的钥匙串（别的 Mac 各自配一次）。" if keychain.enabled() else
                          f"只保存到受限文件 <code>{html.escape(str(KEY_FILE))}</code>；不要把这个文件夹公开分享。")
            page = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>配置豆包密钥</title>
<body style="font:17px system-ui;max-width:640px;margin:48px auto;line-height:1.6">
<h1>配置 AI 口播剪辑豆包 Key</h1>
<p>请使用<strong>豆包语音新版控制台</strong>的 API Key；火山方舟 API Key 调此语音接口会返回 401。</p>
<p>{where_page}</p>
<form method="post" action="/save"><input type="hidden" name="csrf" value="{csrf}">
<input type="password" name="key" required autocomplete="off" style="width:95%;padding:12px" placeholder="粘贴现有 API Key"><br>
<button style="padding:12px;margin-top:16px">保存</button></form></body></html>'''
            return self.reply(page)

        def do_POST(self):
            if not self.allowed() or self.path != "/save":
                return self.reply("拒绝访问", 403)
            if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}"):
                return self.reply("拒绝访问", 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply("请求无效", 400)
            if not 0 < length <= 4096:
                return self.reply("请求过大", 413)
            try:
                form = parse_qs(self.rfile.read(length).decode("utf-8"), max_num_fields=8)
            except (UnicodeDecodeError, ValueError):
                return self.reply("请求无效", 400)
            if not secrets.compare_digest(form.get("csrf", [""])[0].encode("utf-8"), csrf.encode("ascii")):
                return self.reply("请求无效", 403)
            try:
                where = save_key(form.get("key", [""])[0])
            except (ValueError, RuntimeError) as exc:
                return self.reply(f"<p>{html.escape(str(exc))}</p>", 400)
            except OSError:
                return self.reply("密钥保存失败，请检查钥匙串或文件夹写入权限", 500)
            self.reply(f"<meta charset='utf-8'><p>密钥已保存到{'这台 Mac 的钥匙串' if where == '钥匙串' else '受限文件'}。可以关闭此页。</p>")
            self.server.shutdown()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"本机密钥配置页：http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="配置豆包 API Key（存进这台 Mac 的钥匙串）；不会打印密钥")
    parser.add_argument("--from-stdin", action="store_true", help="从标准输入读取并保存；不会回显")
    args = parser.parse_args()
    if args.from_stdin:
        where = save_key(sys.stdin.read())
        print("豆包密钥已保存到" + ("这台 Mac 的钥匙串。" if where == "钥匙串" else "受限文件。"))
    else:
        serve()
