"""Protocol compatibility checks; no real tools or remote models are executed."""
import copy
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

import codex_bridge as bridge
import local_model as client


def message_response():
    return {"id": "resp_test", "model": "qwen3.8:27B", "status": "completed",
            "output": [{"id": "msg_test", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text",
                        "text": "OK", "annotations": []}]}],
            "usage": {"input_tokens": 9, "output_tokens": 1, "total_tokens": 10}}


def parse_events(raw):
    return [json.loads(line[6:]) for line in raw.decode().splitlines()
            if line.startswith("data: ")]


@contextmanager
def slow_upstream():
    """An actual HTTP peer that observes cancellation, not just a mock callback."""
    started, disconnected, stop = threading.Event(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            started.set()
            while not stop.wait(.02):
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    disconnected.set()
                    return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", started, disconnected
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        # The client refuses to run unconfigured, so every case gets a hermetic endpoint
        # and key here. Otherwise results depend on the shell that ran the tests: Fedora
        # exports them from ~/.bashrc, a non-interactive SSH session on the Pi does not.
        env = patch.dict(os.environ, {"LOCAL_MODEL_BASE_URL": "http://127.0.0.1:9/v1",
                                      "LOCAL_MODEL_API_KEY": "test-token"})
        env.start()
        self.addCleanup(env.stop)

    def test_only_known_optional_features_are_removed(self):
        original = {"model": "qwen3.8:27B", "input": [
            {"type": "function_call_output", "call_id": "call_1", "output": "result"}],
            "instructions": "Keep approvals", "client_metadata": {"x": "y"},
            "include": ["reasoning.encrypted_content"], "prompt_cache_key": "session-key",
            "stream": True, "tools": [{"type": "function", "name": "test"}],
            "store": False, "parallel_tool_calls": False}
        before = copy.deepcopy(original)
        body = bridge.upstream_body(original)
        expected = copy.deepcopy(before)
        for key in ("client_metadata", "include", "prompt_cache_key"):
            expected.pop(key)
        expected["stream"] = False
        self.assertEqual(body, expected)
        self.assertEqual(original, before)
        # Other semantics are NOT erased just to get a successful HTTP reply.
        body = bridge.upstream_body({**original, "include": ["different.feature"],
                                     "previous_response_id": "resp_prior"})
        self.assertEqual(body["previous_response_id"], "resp_prior")
        self.assertEqual(body["include"], ["different.feature"])

    def test_text_events_preserve_complete_output_and_usage(self):
        response = message_response()
        events = parse_events(bridge.response_events(response))
        self.assertEqual(events[0]["type"], "response.created")
        self.assertEqual([e["sequence_number"] for e in events], list(range(len(events))))
        deltas = [e["delta"] for e in events if e["type"] == "response.output_text.delta"]
        self.assertEqual("".join(deltas), "OK")
        self.assertEqual(events[-1], {"type": "response.completed",
                         "sequence_number": len(events) - 1, "response": response})

    def test_function_events_preserve_name_call_id_and_arguments(self):
        response = message_response()
        call = {"type": "function_call", "id": "fc_test", "name": "test_function",
                "call_id": "call_test", "arguments": '{"value":"hello"}', "status": "completed"}
        response["output"] = [call]
        events = parse_events(bridge.response_events(response))
        added = next(e for e in events if e["type"] == "response.output_item.added")
        self.assertEqual(added["item"]["arguments"], "")
        self.assertEqual(added["item"]["call_id"], "call_test")
        done = next(e for e in events if e["type"] == "response.output_item.done")
        self.assertEqual(done["item"], call)

    def test_failed_partial_or_unknown_outputs_do_not_become_success(self):
        for patch_value in ({"status": "incomplete"}, {"error": {"code": "failure"}},
                            {"output": [{"id": "x", "type": "unknown_tool"}]}):
            with self.subTest(patch_value=patch_value), self.assertRaises(client.ClientError):
                bridge.response_events({**message_response(), **patch_value})

    def call(self, base, token, body=None, path="/v1/responses"):
        conn = HTTPConnection(base.split("//")[1].split("/")[0], timeout=3)
        self.addCleanup(conn.close)
        conn.request("POST", path, body=json.dumps(body or {"model": "qwen3.8:27B", "stream": True}),
                     headers={"Authorization": "Bearer " + token})
        response = conn.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")

    def test_loopback_requires_token_and_known_path_then_replays_sse(self):
        with patch.object(client, "request", return_value=message_response()) as upstream:
            with bridge.serving() as (base, token):
                self.assertTrue(base.startswith("http://127.0.0.1:"))
                self.assertEqual(self.call(base, "wrong")[0], 401)
                self.assertEqual(self.call(base, token, path="/v1/other")[0], 404)
                upstream.assert_not_called()
                status, raw, kind = self.call(base, token)
                self.assertEqual((status, kind), (200, "text/event-stream"))
                self.assertEqual(parse_events(raw)[-1]["type"], "response.completed")
                self.assertEqual(upstream.call_args.args,
                                 ("/responses", {"model": "qwen3.8:27B", "stream": False}))
                self.assertTrue(callable(upstream.call_args.kwargs["cancelled"]))
                self.assertEqual(upstream.call_count, 1)
            # No listener survives the Codex invocation.
            with self.assertRaises(OSError):
                self.call(base, token)

    def test_timeouts_and_wrong_models_are_explicit_not_partial_answers(self):
        with bridge.serving() as (base, token):
            with patch.object(client, "request", side_effect=client.ClientError("request timed out", 28)):
                status, body, _ = self.call(base, token)
                self.assertEqual(status, 422)  # terminal, not an automatic retry cue
                self.assertIn(b"exceeded its configured time limit", body)
                self.assertIn(b"local_limit_reached", body)
                # OpenCode retries based on body regexes even when HTTP 422
                # marks the attempt terminal. Do not emit those retry cues.
                self.assertNotIn(b"request timed out", body)
            with patch.object(client, "request", return_value={**message_response(), "model": "wrong"}):
                self.assertEqual(self.call(base, token)[0], 422)

    def start_pending_request(self, base, token):
        conn = HTTPConnection(base.split("//")[1].split("/")[0], timeout=3)
        self.addCleanup(conn.close)
        conn.request("POST", "/v1/responses", body=json.dumps({
            "model": "qwen3.8:27B", "input": "synthetic", "stream": True}),
            headers={"Authorization": "Bearer " + token})
        return conn

    def test_client_disconnect_closes_upstream_and_reaps_curl(self):
        processes = []
        real_popen = client.subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with slow_upstream() as (endpoint, started, disconnected), patch.dict(os.environ, {
                "LOCAL_MODEL_BASE_URL": endpoint, "LOCAL_MODEL_INFERENCE_TIMEOUT": "10"}), \
                patch.object(client.subprocess, "Popen", side_effect=capture):
            with bridge.serving() as (base, token):
                conn = self.start_pending_request(base, token)
                self.assertTrue(started.wait(2))
                conn.close()
                self.assertTrue(disconnected.wait(2), "upstream kept running after client cancellation")
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].returncode, "curl was not reaped")

    def test_bridge_exit_cancels_pending_requests_without_waiting_for_deadline(self):
        with slow_upstream() as (endpoint, started, disconnected), patch.dict(os.environ, {
                "LOCAL_MODEL_BASE_URL": endpoint, "LOCAL_MODEL_INFERENCE_TIMEOUT": "10"}):
            with bridge.serving() as (base, token):
                self.start_pending_request(base, token)
                self.assertTrue(started.wait(2))
                before_close = time.monotonic()
            self.assertLess(time.monotonic() - before_close, 2)
            self.assertTrue(disconnected.wait(1))

    def test_already_cancelled_request_performs_no_provider_io(self):
        with patch.object(client.subprocess, "Popen") as spawn:
            with self.assertRaises(client.RequestCancelled):
                client.request("/responses", {"input": "synthetic"}, cancelled=lambda: True)
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
