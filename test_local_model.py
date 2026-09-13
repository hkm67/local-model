"""Regression checks for the hang and endpoint migration using an HTTP server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

import local_model as client


class LocalModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = []
        cls.mode = "ok"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                cls.seen.append((self.path, self.headers.get("Authorization")))
                if cls.mode == "stall":
                    time.sleep(1.0)
                status = 503 if cls.mode == "http-error" else 200
                self.send_response(status)
                self.end_headers()
                body = b"not JSON" if cls.mode == "invalid-json" else json.dumps({
                    "data": [{"id": "qwen3.8:27B"}]}).encode()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                cls.seen.append((self.path, body))
                self.send_response(200)
                self.end_headers()
                response = {"model": "qwen3.8:27B", "status": "completed", "output": [
                    {"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": "OK"}]}]}
                try:
                    raw = json.dumps(response).encode()
                    if cls.mode == "trickle":
                        # Data arrives often enough to defeat an idle read
                        # timeout, but must not extend the wall-clock budget.
                        for byte in raw:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.05)
                    else:
                        self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        type(self).mode = "ok"
        self.seen.clear()
        self.environment = patch.dict(os.environ, {
            "LOCAL_MODEL_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
            "LOCAL_MODEL_API_KEY": "test-token", "LOCAL_MODEL_TIMEOUT": "0.2",
            "LOCAL_MODEL_CONNECT_TIMEOUT": "0.1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_discovery_uses_v1_auth_and_case_sensitive_ids(self):
        client.require_model("qwen3.8:27B")
        with self.assertRaisesRegex(client.ClientError, "case-sensitive"):
            client.require_model("qwen3.8:27b")
        self.assertEqual(self.seen, [("/v1/models", "Bearer test-token")] * 2)

    def test_check_does_not_claim_inference_was_verified(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"LOCAL_MODEL_MODEL": "qwen3.8:27B"}), redirect_stdout(output):
            client.main(["check"])
        self.assertIn("Discovery OK", output.getvalue())
        self.assertIn("inference not tested", output.getvalue())
        self.assertEqual(self.seen, [("/v1/models", "Bearer test-token")])

    def test_unresponsive_server_exits_with_timeout(self):
        type(self).mode = "stall"
        started = time.monotonic()
        result = subprocess.run(["python3", str(Path(client.__file__)), "list"],
                                capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 28)
        self.assertIn("timed out", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_http_and_json_errors_are_distinct(self):
        type(self).mode = "http-error"
        with self.assertRaisesRegex(client.ClientError, "HTTP 503"):
            client.models()
        type(self).mode = "invalid-json"
        with self.assertRaisesRegex(client.ClientError, "invalid JSON"):
            client.models()

    def test_shell_switch_changes_only_after_validation(self):
        shell = Path(client.__file__).with_name("local-model.sh")
        result = subprocess.run(["bash", "--noprofile", "--norc", "-c", f'''
source "{shell}"
local-model use qwen3.8:27B || exit
local-model use qwen3.8:27b && exit 9
test "$LOCAL_MODEL_MODEL" = 'qwen3.8:27B' || exit 8
test "$LOCAL_OLLAMA_MODEL" = 'qwen3.8:27B'
'''], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_completed_final_text_is_returned(self):
        response = {"status": "completed", "output": [
            {"type": "reasoning", "summary": [{"text": "private reasoning"}]},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "OK"}]}]}
        self.assertEqual(client.final_text(response), "OK")
        response["status"] = "incomplete"
        with self.assertRaisesRegex(client.ClientError, "did not complete"):
            client.final_text(response)

    def test_inference_trickling_body_cannot_extend_deadline(self):
        type(self).mode = "trickle"
        with patch.dict(os.environ, {"LOCAL_MODEL_INFERENCE_TIMEOUT": "0.2"}):
            started = time.monotonic()
            result = subprocess.run(["python3", str(Path(client.__file__)), "ask", "Say OK"],
                                    capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 28)
        self.assertEqual(result.stdout, "")
        self.assertIn("timed out", result.stderr)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(self.seen[0][0], "/v1/responses")

    def test_opencode_background_model_cannot_fall_back_to_cloud(self):
        with patch.dict(os.environ, {"LOCAL_MODEL_MODEL": "qwen3.8:27B"}):
            config = client.opencode_config([{"id": "qwen3.8:27B"}])
        self.assertEqual(config["enabled_providers"], ["local_tailscale"])
        self.assertEqual(config["small_model"], config["model"])

    def test_doctor_passes_when_endpoint_healthy(self):
        output = io.StringIO()
        with redirect_stdout(output):
            client.main(["doctor"])
        text = output.getvalue()
        self.assertIn("TCP connect", text)
        self.assertIn("GET /models -> 1 model(s)", text)
        self.assertIn("all checks passed", text)

    def test_doctor_diagnoses_live_frontend_over_dead_backend(self):
        # TCP connects (loopback) but the backend returns 5xx: the exact 502-behind-
        # tailscale-serve incident. Doctor must name it, not just print "failed".
        type(self).mode = "http-error"
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(client.ClientError) as ctx:
            client.main(["doctor"])
        text = output.getvalue()
        self.assertIn("TCP connect", text)          # reachability succeeded
        self.assertIn("HTTP 503", text)
        self.assertIn("restart the router", text)   # actionable remediation
        self.assertEqual(ctx.exception.code, 1)

    def test_doctor_rejects_unknown_flags(self):
        with self.assertRaisesRegex(client.ClientError, "usage: local-model doctor"):
            client.main(["doctor", "--nope"])

    def test_connect_snippets_never_print_the_api_key(self):
        for target in ("overview", "curl", "python", "node", "env"):
            snippet = client.connect_snippet(target)
            self.assertNotIn("test-token", snippet)          # the real key value
            self.assertIn("LOCAL_MODEL_API_KEY", snippet)     # referenced by name
        self.assertIn("/responses", client.connect_snippet("curl"))
        with self.assertRaisesRegex(client.ClientError, "usage: local-model connect"):
            client.connect_snippet("ruby")


if __name__ == "__main__":
    unittest.main()


class UnconfiguredTests(unittest.TestCase):
    """No env file means no endpoint: refuse clearly instead of guessing one."""

    def test_missing_base_url_and_key_fail_fast_with_the_install_hint(self):
        with patch.dict(os.environ, {"LOCAL_MODEL_BASE_URL": "", "LOCAL_MODEL_API_KEY": ""}):
            with self.assertRaises(client.ClientError) as ctx:
                client.base_url()
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("install-client.sh", str(ctx.exception))
        with patch.dict(os.environ, {"LOCAL_MODEL_BASE_URL": "http://127.0.0.1:1/v1", "LOCAL_MODEL_API_KEY": ""}):
            with self.assertRaises(client.ClientError) as ctx:
                client.request("/models")
            self.assertIn("LOCAL_MODEL_API_KEY", str(ctx.exception))
