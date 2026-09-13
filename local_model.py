"""Bounded discovery and text requests for the private OpenAI Responses router."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


class ClientError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class RequestCancelled(ClientError):
    def __init__(self):
        super().__init__("local inference cancelled", 130)


def setting(name, default):
    return os.environ.get("LOCAL_MODEL_" + name) or default


def seconds(name, default):
    try:
        value = float(setting(name, default))
        if not math.isfinite(value) or not 0 < value <= 3600:
            raise ValueError
        return value
    except ValueError:
        raise ClientError(f"LOCAL_MODEL_{name} must be between 0 and 3600 seconds", 2)


UNCONFIGURED = ("LOCAL_MODEL_{} is not set: run bin/install-client.sh --base-url URL "
                "--api-key KEY (it writes ~/.config/local-model/env), then reload the shell")


def base_url():
    # No built-in endpoint: the router is private to one tailnet and this repo is public.
    base = setting("BASE_URL", "").rstrip("/")
    if not base:
        raise ClientError(UNCONFIGURED.format("BASE_URL"), 2)
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            not parsed.path.endswith("/v1")):
        raise ClientError("LOCAL_MODEL_BASE_URL must be an HTTP(S) API base ending in /v1", 2)
    return base


def _run_request(args, payload, deadline, cancelled):
    """Own the curl child until exit, including client cancellation and Ctrl-C."""
    if cancelled and cancelled():
        raise RequestCancelled()
    end = time.monotonic() + deadline + 2
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        pending_input = json.dumps(payload) if payload is not None else None
        try:
            while True:
                if cancelled and cancelled():
                    raise RequestCancelled()
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise ClientError(f"request timed out after {deadline:g}s", 28)
                try:
                    stdout, stderr = process.communicate(
                        input=pending_input, timeout=min(.1, remaining))
                    if cancelled and cancelled():
                        raise RequestCancelled()
                    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    # communicate retains its output and unsent input across
                    # retries; neither data nor the total deadline is reset.
                    pending_input = None
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()


def request(path, payload=None, *, cancelled=None):
    connect = seconds("CONNECT_TIMEOUT", "5")
    deadline = seconds("INFERENCE_TIMEOUT" if payload is not None else "TIMEOUT",
                       "180" if payload is not None else "15")
    token = setting("API_KEY", "")
    if not token:
        raise ClientError(UNCONFIGURED.format("API_KEY"), 2)
    if "\n" in token or "\r" in token:
        raise ClientError("Invalid local API key", 2)
    # curl enforces a wall-clock deadline including slow response bodies. A
    # socket read timeout alone would allow a trickling response to hang forever.
    args = ["curl", "--disable", "--silent", "--show-error", "--connect-timeout",
            str(connect), "--max-time", str(deadline), "--retry", "0",
            "--header", "Authorization: Bearer " + token,
            "--header", "Content-Type: application/json",
            "--write-out", "\n%{http_code}", base_url() + path]
    if payload is not None:
        args += ["--data-binary", "@-"]
    result = _run_request(args, payload, deadline, cancelled)
    if result.returncode:
        if result.returncode == 28:
            raise ClientError(f"request timed out ({path}; connect {connect:g}s, total {deadline:g}s)", 28)
        detail = result.stderr.strip().replace(token, "[redacted]")
        raise ClientError(f"cannot reach {base_url()}{path}: {detail}", result.returncode)
    raw, _, status_text = result.stdout.rpartition("\n")
    try:
        status = int(status_text)
    except ValueError:
        raise ClientError("missing HTTP status from curl")
    if not 200 <= status < 300:
        # Do not print server bodies: validation errors may echo submitted text.
        hint = {401: "check LOCAL_MODEL_API_KEY", 403: "access denied",
                404: "endpoint not supported by router", 422: "router rejected the request or output schema",
                503: "model backend unavailable"}.get(status, "router request failed")
        raise ClientError(f"HTTP {status} from {path}: {hint}", 22)
    try:
        data = json.loads(raw)
    except ValueError:
        raise ClientError(f"invalid JSON from {path}")
    if not isinstance(data, dict):
        raise ClientError(f"expected a JSON object from {path}")
    return data


def models():
    rows = request("/models").get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) or
            not isinstance(row.get("id"), str) or not row["id"] for row in rows):
        raise ClientError("invalid model list from /models")
    return rows


def chosen_model():
    return setting("MODEL", "qwen3.8:27B")


def require_model(name, rows=None):
    rows = models() if rows is None else rows
    names = [row["id"] for row in rows]
    if name not in names:
        raise ClientError(f"unknown model {name!r}; identifiers are case-sensitive. Available: " + ", ".join(names), 2)


def final_text(response):
    if response.get("status") != "completed" or response.get("error"):
        raise ClientError("model response did not complete; try a larger output limit or check the router")
    output = response.get("output")
    if not isinstance(output, list):
        raise ClientError("model response has no output list")
    parts = []
    for item in output:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            continue
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
            raise ClientError("model returned unexpected output to a text-only request")
        content = item.get("content")
        if not isinstance(content, list):
            raise ClientError("model response has invalid message content")
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise ClientError("model response has no usable final text")
            parts.append(part["text"])
    text = "".join(parts)
    if not text.strip():
        raise ClientError("model returned no final text")
    return text.strip()


def write_catalog(rows):
    require_model(chosen_model(), rows)
    base_path = Path.home() / ".codex/models_cache.json"
    base = json.loads(base_path.read_text()) if base_path.exists() else {}
    templates = base.get("models", [])
    if not templates:
        raise ClientError("Codex model catalog is missing; run plain codex once to populate it")
    original = next((row for row in templates if row.get("slug") == "gpt-5.4"), templates[0])
    template = {key: value for key, value in original.items() if key not in {
        "tool_mode", "multi_agent_version", "multi_agent_reasoning_effort",
        "availability_nux", "max_context_window", "comp_hash"}}
    context = int(os.environ.get("LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW", "32768"))
    if context < 1024:
        raise ClientError("LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW is too small", 2)
    entries = []
    for row in rows:
        # Reuse the installed CLI's catalog schema, but no cloud model IDs or
        # unverified provider features are copied into the local model list.
        entry = {**template, "slug": row["id"], "display_name": row["id"],
                 "description": "Local model via Tailscale; context limit is operator-configured",
                 "visibility": "list", "supported_in_api": True, "priority": 100,
                 "shell_type": "shell_command", "support_verbosity": False,
                 "experimental_supported_tools": [], "use_responses_lite": False,
                 "node_repl_disabled": True, "additional_speed_tiers": [], "service_tiers": [],
                 "supported_reasoning_levels": [{"effort": "low", "description": "Low reasoning"}],
                 "context_window": context, "auto_compact_token_limit": int(context * .8),
                 "input_modalities": ["text"], "supports_parallel_tool_calls": False,
                 "supports_search_tool": False, "supports_reasoning_summaries": False,
                 "default_reasoning_level": "low",
                 "apply_patch_tool_type": None, "prefer_websockets": False}
        entries.append(entry)
    target = Path(os.environ.get("LOCAL_CODEX_MODEL_CATALOG",
                                 str(Path.home() / ".codex/local-ollama-model-catalog.json")))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as output:
        json.dump({**base, "models": entries, "etag": "local-router"}, output)
        staged = output.name
    os.replace(staged, target)


def opencode_config(rows, *, base=None, key_env="LOCAL_MODEL_API_KEY"):
    require_model(chosen_model(), rows)
    return {"enabled_providers": ["local_tailscale"],
        "small_model": "local_tailscale/" + chosen_model(),
        "provider": {"local_tailscale": {
        "npm": "@ai-sdk/openai", "name": "Local Tailscale",
        "options": {"baseURL": base or base_url(), "apiKey": "{env:" + key_env + "}"},
        "models": {row["id"]: {"name": row["id"], "api": {"id": row["id"]},
            "options": {"reasoningEffort": setting("REASONING_EFFORT", "low"), "store": False}}
            for row in rows}}}, "model": "local_tailscale/" + chosen_model()}


def main(args):
    command = args[0] if args else "help"
    if command in {"help", "-h", "--help"}:
        print("""Local model helpers (Tailscale Responses router)
  local-model current       Show endpoint, exact model ID, and timeouts
  local-model list          List router model IDs
  local-model use MODEL     Switch this shell (case-sensitive)
  local-model caps MODEL    Show router capabilities; model metadata may be unknown
  local-model check         Check discovery and selected model without inference
  local-model ask PROMPT    Make one text-only Responses request (no tools)
  codex-local [args...]     Run Codex with the selected local model
  opencode-local [args...]  Run OpenCode using Responses and the selected model

Defaults: qwen3.8:27B; connect 5s; metadata 15s; inference 180s.
Override with LOCAL_MODEL_BASE_URL, LOCAL_MODEL_API_KEY, LOCAL_MODEL_MODEL,
LOCAL_MODEL_CONNECT_TIMEOUT, LOCAL_MODEL_TIMEOUT, LOCAL_MODEL_INFERENCE_TIMEOUT.
Cloud CLI defaults are unchanged. Reload with: source ~/.bashrc""")
    elif command == "current":
        print(f"LOCAL_MODEL_MODEL={chosen_model()}\nLOCAL_MODEL_BASE_URL={base_url()}")
        for key, default in [("CONNECT_TIMEOUT", "5"), ("TIMEOUT", "15"), ("INFERENCE_TIMEOUT", "180")]:
            print(f"LOCAL_MODEL_{key}={seconds(key, default):g}")
        print("LOCAL_MODEL_API_KEY=[configured]")
    elif command == "list":
        for row in models():
            print(row["id"])
    elif command in {"validate", "caps"}:
        if len(args) != 2:
            raise ClientError(f"usage: local-model {command} MODEL", 2)
        require_model(args[1])
        if command == "caps":
            caps = request("/capabilities").get("responses", {})
            print(f"model: {args[1]}\ncontext_window: unknown (not advertised by router)")
            print("Router capabilities (not model-specific qualification):")
            print(json.dumps(caps, indent=2, sort_keys=True))
    elif command == "check":
        require_model(chosen_model())
        print(f"Discovery OK: {chosen_model()} at {base_url()} (inference not tested)")
    elif command == "ask":
        if len(args) != 2 or not args[1].strip():
            raise ClientError("usage: local-model ask 'PROMPT'", 2)
        data = request("/responses", {
            "model": chosen_model(), "input": args[1], "stream": False, "store": False,
            "reasoning": {"effort": setting("REASONING_EFFORT", "low")},
            "max_output_tokens": 2048})
        if data.get("model") != chosen_model():
            raise ClientError("router returned a different model")
        print(final_text(data))
    elif command == "catalog":
        write_catalog(models())
    elif command == "opencode-config":
        print(json.dumps(opencode_config(models())))
    else:
        raise ClientError(f"unknown subcommand {command!r}; run local-model help", 2)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except ClientError as exc:
        print(f"local-model: {exc}", file=sys.stderr)
        sys.exit(exc.code)
    except (OSError, ValueError) as exc:
        print(f"local-model: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
