"""Credential and dependency diagnostics with temporary dummy files only."""
import hashlib
import http.client
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import doctor
import doubao_credentials as credentials


class CredentialAndDoctorTests(unittest.TestCase):
    def test_secrets_dir_can_point_elsewhere_for_the_dev_copy(self):
        # 2026-11-24：开发版不复制钥匙，用 THINKCUT_SECRETS_DIR 指到使用版那一份；不设就是本仓库的 secrets/
        import os, sys
        here = Path(__file__).resolve().parent
        code = "import doubao_credentials as c; print(c.KEY_FILE); print(c.VERIFIED_FILE); print(c.load_key() == 'dummy-key-0000000000000')"
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "doubao_api_key").write_text("dummy-key-0000000000000")
            env = {k: v for k, v in os.environ.items() if k != "THINKCUT_SECRETS_DIR"}
            got = subprocess.run([sys.executable, "-B", "-c", code], cwd=here, env={**env, "THINKCUT_SECRETS_DIR": folder}, capture_output=True, text=True).stdout.split()
            self.assertEqual(got, [str(Path(folder) / "doubao_api_key"), str(Path(folder) / "doubao_verified.json"), "True"])
            # 不设变量：本仓库的 secrets/（把程序拷到一个不叫“-开发”的临时仓库里测，免得开发版自己的“找使用版”兜底干扰）
            repo = Path(folder).resolve() / "仓库"; (repo / "口播剪辑" / "bin").mkdir(parents=True)
            shutil.copy(here / "doubao_credentials.py", repo / "口播剪辑" / "bin")
            got = subprocess.run([sys.executable, "-B", "-c", "import doubao_credentials as c; print(c.KEY_FILE)"], cwd=repo / "口播剪辑" / "bin", env=env, capture_output=True, text=True).stdout.strip()
            self.assertEqual(got, str(repo / "口播剪辑" / "secrets" / "doubao_api_key"))
        self.assertIs(doctor.SECRETS_DIR, credentials.SECRETS)      # 体检和配音读同一处钥匙位置（doctor 不再自己拼路径）

    def test_dev_copy_and_its_worktree_find_the_prod_key_without_the_variable(self):
        # 2026-11-24 初稿模式第二次实测：助手在开发版 worktree 里直接跑 doubao_tts（没经 dev.sh run）读不到钥匙
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve()
            prod, dev, wt, plain = base / "ThinkCut", base / "ThinkCut-开发", base / "cutdesk-遗留", base / "别的仓库"
            (prod / "口播剪辑" / "secrets").mkdir(parents=True); (prod / "口播剪辑" / "secrets" / "doubao_api_key").write_text("dummy-key-0000000000000")
            (dev / ".git" / "worktrees" / "遗留").mkdir(parents=True); wt.mkdir(); plain.mkdir()
            (wt / ".git").write_text("gitdir: %s\n" % (dev / ".git" / "worktrees" / "遗留"))
            want = prod / "口播剪辑" / "secrets"
            self.assertEqual(credentials.pick_secrets_dir({}, dev), want)                  # 开发版本身
            self.assertEqual(credentials.pick_secrets_dir({}, wt), want)                   # 开发版的 worktree
            self.assertEqual(credentials.pick_secrets_dir({}, plain), plain / "口播剪辑" / "secrets")   # 不是开发版：不乱找
            self.assertEqual(credentials.pick_secrets_dir({"THINKCUT_SECRETS_DIR": "/x"}, wt), Path("/x"))   # 变量优先
            (wt / "口播剪辑" / "secrets").mkdir(parents=True); (wt / "口播剪辑" / "secrets" / "doubao_api_key").write_text("dummy-key-0000000000000")
            self.assertEqual(credentials.pick_secrets_dir({}, wt), wt / "口播剪辑" / "secrets")           # 自己有就用自己的

    def test_verification_matches_used_key_during_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "verification.json"
            old_key, new_key = "dummy-old-key-000000000", "dummy-new-key-000000000"
            marker.write_text(json.dumps({"resource_id": "turbo", "key_sha256": hashlib.sha256(new_key.encode()).hexdigest()}))
            with patch.object(credentials, "VERIFIED_FILE", marker), \
                 patch.object(credentials, "load_key", return_value=new_key):
                self.assertTrue(credentials.verified_for_current_key("turbo"))
                self.assertFalse(credentials.verified_for_current_key("turbo", old_key))

    def test_invalid_marker_does_not_crash_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "verification.json"
            marker.write_text("[]")
            with patch.object(credentials, "VERIFIED_FILE", marker):
                self.assertFalse(credentials.verified_for_current_key("turbo", "dummy-key-0000000000000"))

    def test_reading_key_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "actual"
            target.write_text("dummy-key-0000000000000")
            link = Path(folder) / "key"
            link.symlink_to(target)
            with patch.object(credentials, "KEY_FILE", link), self.assertRaises(RuntimeError):
                credentials.load_key()

    def test_ffmpeg_path_alone_does_not_mean_extraction_works(self):
        with patch.object(doctor.shutil, "which", return_value="/dummy/ffmpeg"), \
             patch.object(doctor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "aac only", "")):
            _path, ready, reason = doctor.working_ffmpeg()
            self.assertFalse(ready)
            self.assertIn("libmp3lame", reason)

    def test_ffmpeg_timeout_is_reported_without_hanging(self):
        with patch.object(doctor.shutil, "which", return_value="/dummy/ffmpeg"), \
             patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 10)):
            self.assertFalse(doctor.working_ffmpeg()[1])

    def test_hdr_check_requires_the_actual_zscale_filter(self):
        with patch.object(doctor.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "Filters:\n .S zscale V->V Convert colorspace\n", "")):
            self.assertTrue(doctor.ffmpeg_has_zscale('/dummy/ffmpeg'))
        with patch.object(doctor.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "Filters:\n .. scale V->V resize (zscale unavailable)\n", "")):
            self.assertFalse(doctor.ffmpeg_has_zscale('/dummy/ffmpeg'))

    def test_hdr_check_reports_missing_failed_and_timed_out_ffmpeg(self):
        self.assertFalse(doctor.ffmpeg_has_zscale(None))
        with patch.object(doctor.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "error")):
            self.assertFalse(doctor.ffmpeg_has_zscale('/dummy/ffmpeg'))
        with patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired('ffmpeg', 10)):
            self.assertFalse(doctor.ffmpeg_has_zscale('/dummy/ffmpeg'))

    def test_atomic_key_failure_keeps_previous_key(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "secrets" / "key"
            with patch.object(credentials, "KEY_FILE", key):
                credentials.save_key("dummy-old-key-000000000")
                with patch.object(credentials.os, "replace", side_effect=OSError("disconnected")), self.assertRaises(OSError):
                    credentials.save_key("dummy-new-key-000000000")
                self.assertEqual(key.read_text().strip(), "dummy-old-key-000000000")
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)
                self.assertEqual(key.parent.stat().st_mode & 0o777, 0o700)

    def test_local_key_page_rejects_cross_site_and_bad_requests(self):
        servers, ready = [], threading.Event()
        real_server = credentials.ThreadingHTTPServer

        def capture_server(*args, **kwargs):
            server = real_server(*args, **kwargs)
            servers.append(server)
            ready.set()
            return server

        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "secrets" / "key"
            with patch.object(credentials, "KEY_FILE", key), \
                 patch.object(credentials, "ThreadingHTTPServer", side_effect=capture_server), \
                 patch("builtins.print"):
                thread = threading.Thread(target=credentials.serve, daemon=True)
                thread.start()
                self.assertTrue(ready.wait(5))
                server = servers[0]
                host = f"127.0.0.1:{server.server_port}"

                def request(method, path, body=None, headers=None):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    try:
                        connection.request(method, path, body=body, headers=headers or {})
                        response = connection.getresponse()
                        return response.status, response.read().decode()
                    finally:
                        connection.close()

                try:
                    status, page = request("GET", "/")
                    self.assertEqual(status, 200)
                    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
                    self.assertEqual(request("GET", "/", headers={"Host": "elsewhere.test"})[0], 403)
                    body = urlencode({"csrf": token, "key": "dummy-key-0000000000000"})
                    self.assertEqual(request("POST", "/save", body, {"Origin": "https://elsewhere.test"})[0], 403)
                    self.assertFalse(key.exists())
                    self.assertEqual(request("POST", "/save", "", {"Content-Length": "wrong"})[0], 400)
                    self.assertEqual(request("POST", "/save", urlencode({"csrf": "错误", "key": "dummy-key-0000000000000"}))[0], 403)
                    self.assertEqual(request("POST", "/save", body, {"Origin": f"http://{host}"})[0], 200)
                    self.assertEqual(key.stat().st_mode & 0o777, 0o600)
                finally:
                    server.shutdown()
                    thread.join(5)
                    self.assertFalse(thread.is_alive())


class DoctorSummaryTests(unittest.TestCase):
    def test_missing_items_name_the_next_step(self):
        checks = {'shared_skill_ready': True,
                  'skills': {name: {'skill_source_present': True, 'shared_skill_ready': True}
                             for name in ('a-roll', 'b-roll')},
                  'ffmpeg_can_extract_mp3': True, 'shared_doubao_key_present': True,
                  'shared_doubao_key_access_error': None, 'shared_doubao_key_api_verified': True,
                  'alignment_dependencies_ready': False, 'subtitle_dependencies_ready': True,
                  'resolve_app': '/Applications/DaVinci Resolve', 'resolve_mcp_server': 's', 'resolve_mcp_python': 'p'}
        self.assertEqual(doctor.missing_items(checks), ['声学对齐环境缺失：运行 bash bin/setup_mac.sh'])
        text = doctor.summary({**checks, 'shared_version': 'v', 'video_transcription_ready': True,
                               'missing': doctor.missing_items(checks), 'end_to_end_ready': False})
        self.assertIn('可以完整剪辑并放进达芬奇：否', text)

    def test_style_fonts_are_checked_by_name_and_reported(self):
        import sys
        sys.path.insert(0, str(Path(doctor.__file__).resolve().parents[1]))
        from engine.fx import safety
        config = {'styles': {'ok': {'font': 'PingFang SC', 'style': 'Semibold', 'font_file': 'PingFang.ttc#11'},
                             'moved': {'font': 'PingFang SC', 'style': 'Medium', 'font_file': 'PingFang.ttc#7'},
                             'gone': {'font': 'PingFang SC', 'style': 'Heavy', 'font_file': 'PingFang.ttc#3'}}}
        def font_path(style, overrides, cfg, fonts_dir):
            if style['style'] == 'Heavy':
                raise ValueError('字体合集里找不到「PingFang SC Heavy」')
            return Path('/fonts') / style['font_file']
        with patch.object(safety, 'font_path', side_effect=font_path), patch.dict(safety.MEMBER, {'/fonts/PingFang.ttc#7': 5}, clear=True):
            fonts = doctor.style_font_checks(config)
        self.assertEqual({k: v['found'] for k, v in fonts.items()}, {'ok': True, 'moved': True, 'gone': False})
        self.assertFalse(fonts['ok']['index_changed']); self.assertEqual(fonts['moved']['actual_index'], 5)
        checks = {**self.otherwise_ready(self.skill_checks()), 'fx_style_fonts': fonts, 'fx_fonts': {}, 'fx_skill_ready': True,
                  'fx_fonttools_importable': True, 'fx_sfx_directory_accessible': True, 'fx_matting_ready': True}
        missing = doctor.missing_items(checks)
        self.assertEqual(missing, ['花字样式「gone」量字要用的字体 PingFang SC Heavy 找不到：字体合集里找不到「PingFang SC Heavy」'])
        text = doctor.summary({**checks, 'shared_version': 'v', 'video_transcription_ready': True, 'missing': missing, 'end_to_end_ready': False})
        self.assertIn('花字样式「ok」量字用的字体 PingFang SC Semibold：能找到\n', text)
        self.assertIn('花字样式「moved」量字用的字体 PingFang SC Medium：能找到（字体合集里的序号变了，已按字体名找到第 5 个）', text)
        self.assertIn('花字样式「gone」量字用的字体 PingFang SC Heavy：找不到', text)

    def test_key_permission_check_is_honest_on_network_volume(self):
        def df(device):
            return subprocess.CompletedProcess([], 0, 'Filesystem 512-blocks Used Available Capacity Mounted on\n' + device + ' 1 1 1 1% /x\n', '')
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'doubao_api_key'
            key.write_text('dummy-key-0000000000000')
            key.chmod(0o700)      # SMB 挂载上每个文件都显示 0700：原来据此报“权限受限＝是”，其实什么都没查
            with patch.object(doctor.subprocess, 'run', return_value=df('//someone@192.168.1.2/work')):
                restricted, note = doctor.key_permissions(key)
            self.assertIsNone(restricted)
            self.assertIn('查不出来', note)
            self.assertIn('NAS 共享设置', note)
            with patch.object(doctor.subprocess, 'run', return_value=df('/dev/disk3s5')):
                self.assertEqual(doctor.key_permissions(key), (True, '只有本人可读'))
                key.chmod(0o644)
                restricted, note = doctor.key_permissions(key)
            self.assertIs(restricted, False)
            self.assertIn('其他用户也能读', note)
            with patch.object(doctor.subprocess, 'run', side_effect=OSError('no df')):
                self.assertIsNone(doctor.key_permissions(key)[0])
        text = doctor.summary({'shared_version': 'v', 'skills': {}, 'video_transcription_ready': True, 'end_to_end_ready': True, 'missing': [],
                               'shared_doubao_key_permissions_note': note})
        self.assertIn('豆包 Key 文件权限：这台 Mac 的其他用户也能读', text)

    def skill_checks(self, missing_source=None, missing_entry=False):
        # 2026-10-23 起分项是剪辑（thinkcut-edit）下的步骤：只要求说明文件在、入口 thinkcut-edit 已链接
        with tempfile.TemporaryDirectory() as folder:
            root, codex_home = Path(folder) / 'workspace', Path(folder) / 'codex'
            (codex_home / 'skills').mkdir(parents=True)
            for name in ('a-roll', 'b-roll'):
                source = root / 'skills' / name
                source.mkdir(parents=True)
                if name != missing_source:
                    (source / 'SKILL.md').write_text('dummy skill')
            if not missing_entry:
                (codex_home / 'skills' / 'thinkcut-edit').mkdir()
                (codex_home / 'skills' / 'thinkcut-edit' / 'SKILL.md').write_text('entry')
            return doctor.collect_skill_checks(root, codex_home)

    def otherwise_ready(self, skill_checks):
        return {**skill_checks, 'ffmpeg_can_extract_mp3': True, 'shared_doubao_key_present': True,
                'shared_doubao_key_access_error': None, 'shared_doubao_key_api_verified': True,
                'alignment_dependencies_ready': True, 'subtitle_dependencies_ready': True,
                'resolve_app': 'app', 'resolve_mcp_server': 'server', 'resolve_mcp_python': 'python'}

    def test_all_three_skill_links_are_ready(self):
        checks = self.skill_checks()
        self.assertEqual(set(checks['skills']), {'a-roll', 'b-roll'})
        self.assertTrue(checks['skill_source_present'])
        self.assertTrue(checks['skill_installed_as_nas_link'])
        self.assertTrue(checks['shared_skill_ready'])
        self.assertEqual(doctor.missing_items(self.otherwise_ready(checks)), [])

    def test_missing_hdr_filter_names_the_full_formula(self):
        checks = {**self.otherwise_ready(self.skill_checks()), 'ffmpeg_has_zscale': False}
        missing = doctor.missing_items(checks)
        self.assertEqual(len(missing), 1)
        self.assertIn('ffmpeg-full', missing[0])
        text = doctor.summary({**checks, 'shared_version': 'v', 'video_transcription_ready': True,
                               'missing': missing, 'end_to_end_ready': False})
        self.assertIn('ffmpeg HDR 转换（zscale）：未就绪', text)

    def test_missing_entry_is_not_ready_and_names_each_step(self):
        checks = self.skill_checks(missing_entry=True)
        self.assertTrue(checks['skill_source_present'])
        self.assertFalse(checks['skill_installed_as_nas_link'])
        self.assertFalse(checks['shared_skill_ready'])
        missing = doctor.missing_items(self.otherwise_ready(checks))
        self.assertIn('Skill b-roll 未正确连接：运行 bash bin/setup_mac.sh', missing)
        text = doctor.summary({**checks, 'shared_version': 'v', 'video_transcription_ready': True,
                               'missing': missing, 'end_to_end_ready': False})
        self.assertIn('Skill b-roll：未就绪', text)

    def test_missing_source_is_not_ready_even_with_entry(self):
        checks = self.skill_checks(missing_source='b-roll')
        self.assertFalse(checks['skill_source_present'])
        self.assertTrue(checks['skill_installed_as_nas_link'])
        self.assertFalse(checks['shared_skill_ready'])
        self.assertEqual(doctor.missing_items(self.otherwise_ready(checks)),
                         ['Skill b-roll 源文件缺失：运行 bash bin/setup_mac.sh'])


class SetupMacTests(unittest.TestCase):
    """在临时目录里跑 setup_mac.sh 的副本：brew 和 sync_env.sh 都是只记账的假程序，不会真安装任何东西。"""
    def run_setup(self, *args, folders=()):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in ('口播剪辑/bin', 'bin', 'fake', 'home', *folders):
                (base / name).mkdir(parents=True)
            script = base / '口播剪辑/bin/setup_mac.sh'
            script.write_text((Path(__file__).resolve().parent / 'setup_mac.sh').read_text(encoding='utf-8'), encoding='utf-8')
            log = base / 'calls.log'
            (base / 'bin/sync_env.sh').write_text(f'echo "sync_env $*" >> "{log}"\n')
            (base / 'fake/brew').write_text(f'#!/bin/bash\necho "brew $*" >> "{log}"\n[ "$1" = install ] && exit 1\nexit 0\n')
            (base / 'fake/brew').chmod(0o755)
            env = {'HOME': str(base / 'home'), 'PATH': f"{base / 'fake'}:/usr/bin:/bin", 'LANG': 'zh_CN.UTF-8'}
            result = subprocess.run(['bash', str(script), *args], env=env, capture_output=True, text=True, timeout=120)
            return result, log.read_text() if log.exists() else ''

    def test_check_mode_only_checks(self):
        result, calls = self.run_setup('--check')
        self.assertEqual(calls, '')                         # 原来：解析参数之前就调用了 sync_env.sh
        self.assertIn('✗ 环境的 Mac 运行副本', result.stdout)
        self.assertNotIn('→ 安装', result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertIn('还缺：', result.stdout)

    def test_sfx_library_is_found_next_to_the_repository_wherever_the_nas_is_mounted(self):
        # 音效库和本仓库同在程序目录下：按脚本所在位置找，不认死 /Volumes/work
        result, _ = self.run_setup('--check', folders=('素材库/音效素材',))
        self.assertIn('✓ 音效库可访问', result.stdout)
        result, _ = self.run_setup('--check')
        self.assertIn('✗ 音效库不可访问', result.stdout)

    def test_install_failure_names_the_item_not_the_command(self):
        result, calls = self.run_setup()
        self.assertIn('sync_env', calls)
        self.assertIn('brew install ffmpeg-full', calls)
        self.assertIn('✗ 安装失败：ffmpeg-full（含 zscale）', result.stdout)   # 原来显示成“安装失败：brew”
        self.assertEqual(result.returncode, 1)

    def test_unknown_argument_stops_before_doing_anything(self):
        result, calls = self.run_setup('--chek')
        self.assertEqual((result.returncode, calls), (2, ''))


if __name__ == "__main__":
    unittest.main()
