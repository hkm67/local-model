"""Per-CLI loopback adapter for the private router's Responses subset.

Optional client telemetry, cache hints and the request for encrypted reasoning are removed.
The router provides no encrypted reasoning to preserve; full input is replayed.
Other request semantics are passed through, never silently discarded. Inference is
buffered because the router supports function calls but not streamed calls.
Codex still receives standard Responses events and owns all tool execution,
approval and sandbox decisions. This adapter never executes model output.
"""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import select
import signal
import socket
import subprocess
import sys
import threading

import local_model as client

MAX_BODY = 8 * 1024 * 1024


def upstream_body(body):
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        raise client.ClientError("invalid Responses request", 2)
    result = deepcopy(body)
    result.pop("client_metadata", None)
    # This is a cache optimization hint, not conversation state or content.
    result.pop("prompt_cache_key", None)
    if result.get("include") in ([], ["reasoning.encrypted_content"]):
        result.pop("include")
    result["stream"] = False
    return result


def response_events(response):
    """Replay a completed JSON response without inventing tools or partial success."""
    if (response.get("status") != "completed" or response.get("error") or
            not isinstance(response.get("output"), list)):
        raise client.ClientError("router returned an incomplete or failed response")
    events = []

    def emit(kind, **fields):
        events.append({"type": kind, "sequence_number": len(events), **fields})

    initial = {**response, "status": "in_progress", "output": [], "usage": None}
    emit("response.created", response=initial)
    emit("response.in_progress", response=initial)
    for index, item in enumerate(response["output"]):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise client.ClientError("router returned an invalid output item")
        kind = item.get("type")
        start = {**item, "status": "in_progress"}
        if kind == "message":
            if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                raise client.ClientError("router returned an invalid message")
            start["content"] = []
        elif kind == "function_call":
            if any(not isinstance(item.get(key), str) for key in ("name", "call_id", "arguments")):
                raise client.ClientError("router returned an invalid function call")
            start["arguments"] = ""
        elif kind != "reasoning":
            raise client.ClientError("router returned an unsupported output item")
        emit("response.output_item.added", output_index=index, item=start)
        fields = {"output_index": index, "item_id": item["id"]}
        if kind == "message":
            for ci, part in enumerate(item["content"]):
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise client.ClientError("router returned unsupported message content")
                address = {**fields, "content_index": ci}
                emit("response.content_part.added", **address,
                     part={**part, "text": ""})
                emit("response.output_text.delta", **address, delta=part["text"])
                emit("response.output_text.done", **address, text=part["text"])
                emit("response.content_part.done", **address, part=part)
        elif kind == "function_call":
            emit("response.function_call_arguments.delta", **fields, delta=item["arguments"])
            emit("response.function_call_arguments.done", **fields,
                 name=item["name"], arguments=item["arguments"])
        emit("response.output_item.done", output_index=index, item=item)
    emit("response.completed", response=response)
    return b"".join(("event: " + event["type"] + "\ndata: " +
                     json.dumps(event) + "\n\n").encode() for event in events)


@contextmanager
def serving():
    """Random port and token; fixed upstream; no persistent service or request logs."""
    token = secrets.token_urlsafe(32)
    closing = threading.Event()
    connections = set()
    connections_lock = threading.Lock()

    class Server(ThreadingHTTPServer):
        # Wait for owned workers on close, after cancelling their requests and
        # closing their sockets. No daemon curl process survives this scope.
        daemon_threads = False

        def process_request(self, request, client_address):
            with connections_lock:
                connections.add(request)
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.close_request(request)
                raise

        def close_request(self, request):
            with connections_lock:
                connections.discard(request)
            super().close_request(request)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *_args):
            pass

        def cancelled(self):
            if closing.is_set():
                return True
            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
                return bool(readable) and self.connection.recv(
                    1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
            except BlockingIOError:
                return False
            except (OSError, ValueError):
                return True

        def reply(self, status, data, content_type="application/json"):
            raw = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            self.connection.settimeout(10)
            try:
                if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    return self.reply(401, {"error": {"message": "bridge authentication required"}})
                if self.path != "/v1/responses":
                    return self.reply(404, {"error": {"message": "unsupported bridge path"}})
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                    return self.reply(413, {"error": {"message": "invalid or oversized request"}})
                body = json.loads(self.rfile.read(size))
                request = upstream_body(body)
                response = client.request("/responses", request, cancelled=self.cancelled)
                if response.get("model") != request["model"]:
                    raise client.ClientError("router returned a different model")
                events = response_events(response)
                if body.get("stream"):
                    return self.reply(200, events, "text/event-stream")
                self.reply(200, response)
            except client.RequestCancelled:
                return  # the caller left; no inference retry or late result
            except client.ClientError as exc:
                # Never expose raw upstream bodies or submitted prompts.
                # This bounded attempt is terminal. OpenCode retries not only
                # 5xx but also message/body regex matches (even for 422). Keep
                # the HTTP explanation unambiguously terminal, with diagnostic
                # details on the invoking terminal, not an automatic retry cue.
                print("local-model: " + str(exc), file=sys.stderr)
                message = ("Local inference exceeded its configured time limit. "
                           "Stopped without a result. Increase LOCAL_MODEL_INFERENCE_TIMEOUT "
                           "or retry manually." if exc.code == 28 else
                           "The local service could not fulfill this inference. "
                           "Stopped without a result. Run local-model check for details.")
                self.reply(422, {"error": {"message": message,
                           "code": "local_limit_reached" if exc.code == 28
                           else "local_inference_failed", "type": "invalid_request_error"}})
            except (ValueError, TypeError, AttributeError):
                self.reply(400, {"error": {"message": "invalid request or response JSON"}})
            except (OSError, TimeoutError):
                pass  # disconnected or stalled client; no retries or tool execution

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", token
    finally:
        closing.set()
        server.shutdown()
        with connections_lock:
            pending = list(connections)
        for connection in pending:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        server.server_close()
        thread.join(timeout=2)


def run_codex(args):
    # Fail early on discovery and bad timeout settings, not after a UI opens.
    client.write_catalog(client.models())
    deadline = client.seconds("INFERENCE_TIMEOUT", "180")
    with serving() as (base, token):
        provider = {"name": "Local Tailscale (buffered)", "base_url": base,
                    "env_key": "LOCAL_CODEX_BRIDGE_KEY", "wire_api": "responses",
                    "request_max_retries": 0, "stream_max_retries": 0,
                    "stream_idle_timeout_ms": int((deadline + 5) * 1000)}
        toml = "{" + ", ".join(key + " = " + json.dumps(value) for key, value in provider.items()) + "}"
        catalog = os.environ.get("LOCAL_CODEX_MODEL_CATALOG", os.path.expanduser("~/.codex/local-ollama-model-catalog.json"))
        command = ["codex", "-c", "model_providers.local_tailscale=" + toml,
                   "-c", 'model_provider="local_tailscale"',
                   "-c", "model_catalog_json=" + json.dumps(catalog),
                   "-c", "model_supports_reasoning_summaries=false",
                   "-c", 'web_search="disabled"',
                   "-c", "features.apps=false",
                   "-c", "features.plugins=false",
                   "-c", "features.multi_agent=false",
                   "-c", "features.unbounded_connection_retries=false",
                   "-c", 'service_tier="default"',
                   "-c", "model_reasoning_effort=" + json.dumps(client.setting("REASONING_EFFORT", "low")),
                   "-m", client.chosen_model(), *args]
        env = {**os.environ, "LOCAL_CODEX_BRIDGE_KEY": token,
               "OPENAI_BASE_URL": base, "OPENAI_API_KEY": token}
        print("codex-local: private router compatibility mode; output arrives after each model response.", file=sys.stderr)
        return run_child(command, env)


def run_opencode(args):
    rows = client.models()
    client.require_model(client.chosen_model(), rows)
    with serving() as (base, token):
        config = client.opencode_config(rows, base=base, key_env="LOCAL_CODEX_BRIDGE_KEY")
        env = {**os.environ, "LOCAL_CODEX_BRIDGE_KEY": token,
               "OPENAI_BASE_URL": base, "OPENAI_API_KEY": token,
               "OPENCODE_CONFIG_CONTENT": json.dumps(config)}
        print("opencode-local: private router compatibility mode; output arrives after each model response.", file=sys.stderr)
        return run_child(["opencode", *args], env)


def run_child(command, env):
    child = subprocess.Popen(command, env=env)
    previous = {}
    # Keep the child in the terminal process group for its interactive TUI.
    # Forward TERM/HUP when only the wrapper PID is stopped; terminal INT
    # already reaches the CLI, which owns cancellation inside a live session.
    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, lambda sig, _frame: child.send_signal(sig))
    previous[signal.SIGINT] = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        result = child.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return result if result >= 0 else 128 - result


if __name__ == "__main__":
    try:
        sys.exit(run_opencode(sys.argv[2:]) if sys.argv[1:2] == ["--opencode"]
                 else run_codex(sys.argv[1:]))
    except client.ClientError as exc:
        print("codex-local: " + str(exc), file=sys.stderr)
        sys.exit(exc.code)
    except (OSError, ValueError) as exc:
        print("codex-local: " + str(exc), file=sys.stderr)
        sys.exit(1)
