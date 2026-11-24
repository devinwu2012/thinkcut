"""Offline check that a read-only NAS cannot discard a paid ASR response."""
import contextlib
import base64
import hashlib
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import doubao_direct_asr as app


class DirectAsrTests(unittest.TestCase):
    def test_result_survives_failed_nas_verification_write(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "source.mp3"
            audio.write_bytes(b"test audio")
            output = Path(root) / "result.json"
            result = {"audio_info": {"duration": 1000}, "result": {"utterances": [
                {"text": "你", "words": [{"text": "你", "start_time": 0, "end_time": 100}]}
            ]}}
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(app, "load_key", return_value="test-only"), \
                 patch.object(app, "recognize", return_value=result), \
                 patch.object(app, "verified_for_current_key", return_value=False), \
                 patch.object(app, "mark_verified", side_effect=PermissionError("read only")), \
                 patch("sys.argv", ["direct", "--audio", str(audio), "--output", str(output)]), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                app.main()
            self.assertTrue(output.is_file())
            self.assertIn('"status": "complete"', stdout.getvalue())
            self.assertIn("结果已保存在本机", stderr.getvalue())

    def test_mismatched_upload_bytes_are_rejected_before_request(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "source.mp3"
            audio.write_bytes(b"wrong audio")
            expected = hashlib.sha256(b"expected audio").hexdigest()
            with patch.object(app, "validate_key", return_value="test-only"), \
                 patch.object(app, "urlopen", side_effect=AssertionError("must not call API")) as request:
                with self.assertRaisesRegex(ValueError, "待上传音频与来源校验不一致"):
                    app.recognize(audio, "test-only", expected_audio_sha256=expected)
                request.assert_not_called()

    def test_verified_bytes_are_the_actual_uploaded_payload(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "source.mp3"
            content = b"expected audio"
            audio.write_bytes(content)
            response = Mock()
            response.headers = {"X-Api-Status-Code": "20000000"}
            response.read.return_value = b'{}'
            def fake_urlopen(request, **_kwargs):
                payload = json.loads(request.data)
                self.assertEqual(base64.b64decode(payload["audio"]["data"]), content)
                audio.write_bytes(b"changed after immutable payload")
                return contextlib.nullcontext(response)
            with patch.object(app, "validate_key", return_value="test-only"), \
                 patch.object(app, "urlopen", side_effect=fake_urlopen):
                self.assertEqual(app.recognize(audio, "test-only", expected_audio_sha256=hashlib.sha256(content).hexdigest()), {})

    def _refuse(self, status, body, headers=None):
        import io
        from urllib.error import HTTPError
        return HTTPError("https://x", status, "refused", headers or {}, io.BytesIO(body.encode("utf-8")))

    def test_turbo_not_granted_falls_back_to_standard_submit_and_query(self):
        # 2026-11-18 反馈单：账号只开通了 2.0 标准版，极速版 403（45000030）
        done = {"audio_info": {"duration": 1000}, "result": {"utterances": [{"text": "你", "words": [{"text": "你", "start_time": 0, "end_time": 100}]}]}}
        calls = []
        def fake_urlopen(request, **_kwargs):
            calls.append((request.full_url, request.get_header("X-api-resource-id"), request.get_header("X-api-request-id")))
            if request.full_url == app.API:
                raise self._refuse(403, '{"header":{"code":45000030,"message":"[resource_id=volc.bigasr.auc_turbo] requested resource not granted"}}')
            response = Mock(); response.read.return_value = json.dumps(done).encode()
            queries = sum(1 for c in calls if c[0] == app.QUERY_API)
            response.headers = {"X-Api-Status-Code": "20000000" if request.full_url == app.SUBMIT_API or queries >= 3 else ("20000002" if queries == 1 else "20000001"), "X-Tt-Logid": "log"}
            return contextlib.nullcontext(response)
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            with patch.object(app, "validate_key", return_value="test-only"), patch.object(app, "urlopen", side_effect=fake_urlopen), patch.object(app.time, "sleep") as nap:
                result, used = app.recognize_any(audio, "test-only")
        self.assertEqual((result, used), (done, app.STANDARD_RESOURCE_ID))
        self.assertEqual([c[0] for c in calls], [app.API, app.SUBMIT_API, app.QUERY_API, app.QUERY_API, app.QUERY_API])
        self.assertEqual({c[1] for c in calls[1:]}, {"volc.seedasr.auc"})
        self.assertEqual(len({c[2] for c in calls[1:]}), 1)      # 提交和轮询用同一个请求号
        self.assertEqual(nap.call_count, 3)
        self.assertEqual(app.count_timed_tokens(result), 1)

    def test_refusal_shows_doubao_code_and_message_without_key(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            with patch.object(app, "validate_key", return_value="secret-key-123"), \
                 patch.object(app, "urlopen", side_effect=lambda *_a, **_k: (_ for _ in ()).throw(self._refuse(401, '{"code":45000010,"message":"invalid key secret-key-123"}'))):
                with self.assertRaises(RuntimeError) as caught:
                    app.recognize(audio, "secret-key-123")
        text = str(caught.exception)
        self.assertIn("豆包 HTTP 401", text); self.assertIn("45000010", text); self.assertIn("invalid key", text)
        self.assertNotIn("secret-key-123", text); self.assertNotIsInstance(caught.exception, app.NotGranted)

    def test_neither_resource_granted_says_so_and_standard_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            with patch.object(app, "validate_key", return_value="test-only"), \
                 patch.object(app, "urlopen", side_effect=lambda *_a, **_k: (_ for _ in ()).throw(self._refuse(403, "45000030 requested resource not granted"))):
                with self.assertRaisesRegex(app.NotGranted, "极速版和 2.0 标准版都没开通"):
                    app.recognize_any(audio, "test-only")
            def failing(request, **_kwargs):
                response = Mock(); response.read.return_value = b"{}"
                response.headers = {"X-Api-Status-Code": "20000000" if request.full_url == app.SUBMIT_API else "45000151", "X-Api-Message": "audio format error"}
                return contextlib.nullcontext(response)
            with patch.object(app, "validate_key", return_value="test-only"), patch.object(app, "urlopen", side_effect=failing), patch.object(app.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "豆包识别失败（2.0 标准版）：45000151 audio format error"):
                    app.recognize(audio, "test-only", resource=app.STANDARD_RESOURCE_ID)

    # ---- 2026-11-20 审查：标准版提交后断线不重复扣费、Key 隐藏、报错说人话 ----
    DONE = {"audio_info": {"duration": 1000}, "result": {"utterances": [{"text": "你", "words": [{"text": "你", "start_time": 0, "end_time": 100}]}]}}

    def _standard(self, queries, audio, **kwargs):
        """标准版替身：submit 一律接受；query 依次按 queries 里的项回应（异常就抛，字符串当状态码）。"""
        calls, queue = [], list(queries)
        def fake_urlopen(request, **_kwargs):
            calls.append((request.full_url, request.get_header("X-api-request-id")))
            if request.full_url == app.API:
                raise self._refuse(403, "45000030 requested resource not granted")
            response = Mock(); response.read.return_value = json.dumps(self.DONE).encode()
            if request.full_url == app.SUBMIT_API:
                response.headers = {"X-Api-Status-Code": "20000000"}
                return contextlib.nullcontext(response)
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            code, sep, body = item.partition("|")
            if sep:
                response.read.return_value = body.encode()
            response.headers = {"X-Api-Status-Code": code, "X-Api-Message": "msg"}
            return contextlib.nullcontext(response)
        return calls, fake_urlopen

    def test_standard_poll_retries_transient_errors_and_never_resubmits(self):
        import socket
        from urllib.error import URLError
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            calls, fake = self._standard([URLError("network down"), socket.timeout("timed out"),
                                          self._refuse(503, "busy"), "20000001", "20000000"], audio)
            seen = []
            def submitted(request_id, resource):
                seen.append((request_id, resource, len(calls)))
            with patch.object(app, "validate_key", return_value="test-only"), patch.object(app, "urlopen", side_effect=fake), \
                 patch.object(app.time, "sleep") as nap:
                result = app.recognize(audio, "test-only", resource=app.STANDARD_RESOURCE_ID, on_submitted=submitted)
        self.assertEqual(result, self.DONE)
        self.assertEqual([c[0] for c in calls].count(app.SUBMIT_API), 1)      # 只提交一次
        self.assertEqual(len(calls), 6)
        self.assertEqual(len({c[1] for c in calls}), 1)
        self.assertEqual(seen, [(calls[0][1], app.STANDARD_RESOURCE_ID, 0)])      # 提交之前就交出请求号（和提交时用的是同一个）：提交发出后断线也能凭它去查
        self.assertEqual([c.args[0] for c in nap.call_args_list].count(app.RETRY_SECONDS), 3)

    def test_standard_poll_gives_up_at_deadline_but_never_resubmits(self):
        from urllib.error import URLError
        clock = iter(range(0, 100000, 400))
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            calls, fake = self._standard([URLError("down")] * 50, audio)
            with patch.object(app, "validate_key", return_value="test-only"), patch.object(app, "urlopen", side_effect=fake), \
                 patch.object(app.time, "sleep"), patch.object(app.time, "monotonic", side_effect=lambda: next(clock)):
                with self.assertRaisesRegex(RuntimeError, "不会重新提交") as caught:
                    app.recognize(audio, "test-only", resource=app.STANDARD_RESOURCE_ID)
        self.assertNotIsInstance(caught.exception, app.TaskGone)
        self.assertEqual([c[0] for c in calls].count(app.SUBMIT_API), 1)
        self.assertGreater(len(calls), 2)

    def test_standard_poll_4xx_and_failure_code_stop_at_once(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            for answer in (self._refuse(404, "task not found"), "45000151"):
                calls, fake = self._standard([answer], audio)
                with self.subTest(answer=answer), patch.object(app, "validate_key", return_value="test-only"), \
                     patch.object(app, "urlopen", side_effect=fake), patch.object(app.time, "sleep"):
                    with self.assertRaises(app.TaskGone):
                        app.recognize(audio, "test-only", resource=app.STANDARD_RESOURCE_ID)
                self.assertEqual([c[0] for c in calls], [app.SUBMIT_API, app.QUERY_API])

    def test_query_refused_after_submit_never_falls_back_to_other_resource(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            calls, fake = self._standard([self._refuse(403, "45000030 requested resource not granted")], audio)
            with patch.object(app, "validate_key", return_value="test-only"), patch.object(app, "urlopen", side_effect=fake), \
                 patch.object(app.time, "sleep"):
                with self.assertRaises(app.TaskGone):
                    app.recognize_any(audio, "test-only", first=app.STANDARD_RESOURCE_ID)
        self.assertEqual([c[0] for c in calls], [app.SUBMIT_API, app.QUERY_API])

    def test_fetch_only_queries_with_given_request_id(self):
        from urllib.error import URLError
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"
            calls, fake = self._standard([URLError("down"), "20000002", "20000000"], audio)
            with patch.object(app, "validate_key", side_effect=lambda k: k), patch.object(app, "urlopen", side_effect=fake), \
                 patch.object(app.time, "sleep"):
                self.assertEqual(app.fetch("req-1", "test-only-key-0123456789"), self.DONE)
            self.assertEqual(calls, [(app.QUERY_API, "req-1")] * 3)
            calls, fake = self._standard(["45000002"], audio)
            with patch.object(app, "validate_key", side_effect=lambda k: k), patch.object(app, "urlopen", side_effect=fake), \
                 patch.object(app.time, "sleep"):
                with self.assertRaises(app.TaskGone):
                    app.fetch("req-1", "test-only-key-0123456789")
            self.assertEqual(calls, [(app.QUERY_API, "req-1")])

    def test_key_cut_by_truncation_is_still_hidden(self):
        key = "SECRETKEY-abcdefghijklmnopqrstuvwxyz"
        body = "中" * 195 + key + " tail"      # 195 个中文 = 585 字节：旧写法在 600 字节处截断，Key 被切成半截替换不掉
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            with patch.object(app, "validate_key", return_value=key), \
                 patch.object(app, "urlopen", side_effect=lambda *_a, **_k: (_ for _ in ()).throw(self._refuse(400, body))):
                with self.assertRaises(RuntimeError) as caught:
                    app.recognize(audio, key)
        self.assertNotIn(key[:6], str(caught.exception))
        self.assertIn("[已隐藏密钥]", str(caught.exception))

    def test_silent_audio_and_unreadable_success_body_are_explained_in_chinese(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "a.mp3"; audio.write_bytes(b"audio")
            calls, fake = self._standard(["20000003"], audio)
            with patch.object(app, "validate_key", return_value="test-only"), patch.object(app, "urlopen", side_effect=fake), \
                 patch.object(app.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "豆包说这段音频里没有听到人声"):
                    app.recognize(audio, "test-only", resource=app.STANDARD_RESOURCE_ID)
            for body in (b"", b"<html>bad gateway</html>"):
                response = Mock(); response.headers = {"X-Api-Status-Code": "20000000"}; response.read.return_value = body
                with self.subTest(body=body), patch.object(app, "validate_key", return_value="test-only"), \
                     patch.object(app, "urlopen", return_value=contextlib.nullcontext(response)):
                    with self.assertRaises(RuntimeError) as caught:
                        app.recognize(audio, "test-only")
                text = str(caught.exception)
                self.assertIn("看不懂", text); self.assertIn("可能已计费", text)
                self.assertNotIn("Expecting", text)
            calls, fake = self._standard(["20000000|"], audio)
            with patch.object(app, "validate_key", return_value="test-only"), patch.object(app, "urlopen", side_effect=fake), \
                 patch.object(app.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "看不懂"):
                    app.recognize(audio, "test-only", resource=app.STANDARD_RESOURCE_ID)

    def test_cli_clears_verified_mark_when_both_refused(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "source.mp3"; audio.write_bytes(b"test audio")
            with patch.object(app, "load_key", return_value="test-only"), \
                 patch.object(app, "recognize", side_effect=app.NotGranted("没开通")), \
                 patch.object(app, "clear_verified") as cleared, \
                 patch("sys.argv", ["direct", "--audio", str(audio), "--output", str(Path(root) / "r.json")]):
                with self.assertRaisesRegex(app.NotGranted, "都没开通"):
                    app.main()
        cleared.assert_called_once_with()

    def test_incomplete_word_timing_is_rejected(self):
        result = {"audio_info": {"duration": 1000}, "result": {"utterances": [
            {"text": "你好", "words": [{"text": "你", "start_time": 0, "end_time": 100}]}
        ]}}
        with self.assertRaisesRegex(RuntimeError, "不完整对应"):
            app.count_timed_tokens(result)

    def test_missing_entire_utterance_is_rejected(self):
        result = {"audio_info": {"duration": 1000}, "result": {"text": "你。好。", "utterances": [
            {"text": "你。", "words": [{"text": "你", "start_time": 0, "end_time": 100}]}
        ]}}
        with self.assertRaisesRegex(RuntimeError, "全文与逐句"):
            app.count_timed_tokens(result)

    def test_invalid_structures_and_boolean_time_are_rejected(self):
        for result in ([], {"audio_info": None, "result": {}},
                       {"audio_info": {"duration": True}, "result": {"utterances": []}},
                       {"audio_info": {"duration": 1000}, "result": {"utterances": [
                           {"text": "你", "words": [{"text": "你", "start_time": False, "end_time": 100}]}
                       ]}}):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                app.count_timed_tokens(result)

    def test_invalid_key_is_rejected_without_echo_or_request(self):
        with patch.object(app, "urlopen", side_effect=AssertionError("must not call API")):
            key = "secret-key-not-for-logs中文"
            with self.assertRaises(ValueError) as raised:
                app.recognize("unread.mp3", key)
            self.assertNotIn(key, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
