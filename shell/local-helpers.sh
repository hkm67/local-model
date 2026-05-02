# local-model shell helpers

if [ -f "$HOME/.config/local-model/env" ]; then
  . "$HOME/.config/local-model/env"
fi

export LOCAL_OLLAMA_BASE_URL="${LOCAL_OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export LOCAL_OLLAMA_MODEL="${LOCAL_OLLAMA_MODEL:-gemma4:26b}"
export LOCAL_CODEX_MODEL_CATALOG="${LOCAL_CODEX_MODEL_CATALOG:-$HOME/.codex/local-ollama-model-catalog.json}"
export LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW="${LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW:-131072}"

local-help() {
  cat <<'EOF'
Local model helpers

Commands:
  claude-local [args...]     Run Claude Code against the local Ollama model
  codex-local [args...]      Run Codex against the local Ollama model
  opencode-local [args...]   Run OpenCode against the local Ollama model
  local-model help           Show this help
  local-model current        Show the active local model and endpoint
  local-model list           List models available from Ollama
  local-model use MODEL      Switch the local model for this shell
  local-model caps MODEL     Show Ollama capabilities for MODEL
EOF
}

local-model() {
  case "${1:-help}" in
    help|-h|--help)
      local-help
      ;;
    current)
      printf 'LOCAL_OLLAMA_MODEL=%s\n' "$LOCAL_OLLAMA_MODEL"
      printf 'LOCAL_OLLAMA_BASE_URL=%s\n' "$LOCAL_OLLAMA_BASE_URL"
      printf 'LOCAL_CODEX_MODEL_CATALOG=%s\n' "$LOCAL_CODEX_MODEL_CATALOG"
      printf 'LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW=%s\n' "$LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW"
      ;;
    list)
      curl -fsS "$LOCAL_OLLAMA_BASE_URL/api/tags" | python3 -c '
import json, sys
models = json.load(sys.stdin).get("models", [])
print("{:<40} {:>8}  {}".format("MODEL", "SIZE", "FAMILY"))
for model in models:
    details = model.get("details", {}) or {}
    print("{:<40} {:>8}  {}".format(
        model.get("name", "-"),
        details.get("parameter_size", "?"),
        details.get("family", "-"),
    ))
'
      ;;
    caps)
      if [ -z "${2:-}" ]; then
        printf 'usage: local-model caps MODEL\n' >&2
        return 1
      fi
      curl -fsS "$LOCAL_OLLAMA_BASE_URL/api/show" -d "{\"model\":\"$2\"}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
caps = d.get("capabilities", []) or []
info = d.get("model_info", {}) or {}
contexts = [v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, int)]
print("model: {}".format(sys.argv[1]))
print("capabilities: {}".format(", ".join(caps) if caps else "-"))
print("tools: {}".format("yes" if "tools" in caps else "no"))
print("thinking: {}".format("yes" if "thinking" in caps else "no"))
print("context_window: {}".format(max(contexts) if contexts else "unknown"))
' "$2"
      ;;
    use)
      if [ -z "${2:-}" ]; then
        printf 'usage: local-model use MODEL\n' >&2
        return 1
      fi
      if ! curl -fsS "$LOCAL_OLLAMA_BASE_URL/api/tags" | python3 -c '
import json, sys
requested = sys.argv[1]
names = sorted(model.get("name", "") for model in json.load(sys.stdin).get("models", []))
if requested in names:
    raise SystemExit(0)
print("unknown local model: {}".format(requested), file=sys.stderr)
if names:
    print("available models:", file=sys.stderr)
    for name in names:
        print("  {}".format(name), file=sys.stderr)
raise SystemExit(1)
' "$2"; then
        return 1
      fi
      export LOCAL_OLLAMA_MODEL="$2"
      printf 'Switched local model to %s\n' "$LOCAL_OLLAMA_MODEL"
      ;;
    *)
      printf 'unknown subcommand: %s\n' "$1" >&2
      return 1
      ;;
  esac
}

_codex_local_catalog() {
  mkdir -p "$(dirname "$LOCAL_CODEX_MODEL_CATALOG")"
  python3 - "$LOCAL_CODEX_MODEL_CATALOG" "$HOME/.codex/models_cache.json" "$LOCAL_OLLAMA_BASE_URL" "$LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW" <<'PY'
import json
import pathlib
import sys
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone

catalog_path = pathlib.Path(sys.argv[1])
base_catalog_path = pathlib.Path(sys.argv[2])
base_url = sys.argv[3].rstrip("/")
fallback_context_window = int(sys.argv[4])

def ollama_post(path, payload):
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())

def ollama_get(path):
    with urllib.request.urlopen(base_url + path, timeout=30) as response:
        return json.loads(response.read().decode())

def metadata_for(name):
    try:
        shown = ollama_post("/api/show", {"model": name})
    except Exception:
        return fallback_context_window, []
    info = shown.get("model_info", {}) or {}
    context_values = [
        value for key, value in info.items()
        if key.endswith(".context_length") and isinstance(value, int)
    ]
    return max(context_values) if context_values else fallback_context_window, shown.get("capabilities", []) or []

ollama = ollama_get("/api/tags")
base = json.loads(base_catalog_path.read_text()) if base_catalog_path.exists() else {"models": []}
models = [model for model in base.get("models", []) if model.get("slug")]
models_by_slug = {model["slug"]: model for model in models}
template = deepcopy(models_by_slug.get("gpt-5.4") or (models[0] if models else {}))

for model in ollama.get("models", []):
    name = model.get("name")
    if not name:
        continue
    details = model.get("details", {}) or {}
    family = details.get("family", "ollama")
    parameter_size = details.get("parameter_size", "")
    context_window, capabilities = metadata_for(name)
    entry = deepcopy(template)
    entry.update({
        "slug": name,
        "display_name": name,
        "description": " - ".join(part for part in ["Local Ollama model", parameter_size, family] if part),
        "visibility": "list",
        "supported_in_api": True,
        "priority": 100,
        "context_window": context_window,
        "auto_compact_token_limit": int(context_window * 0.85),
        "input_modalities": ["text"],
        "supports_parallel_tool_calls": "tools" in capabilities,
        "supports_search_tool": False,
    })
    models_by_slug[name] = entry

catalog = {
    "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "etag": "local-ollama",
    "client_version": base.get("client_version", "local"),
    "models": list(models_by_slug.values()),
}
catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
PY
}

_codex_local_model_supports_tools() {
  curl -fsS "$LOCAL_OLLAMA_BASE_URL/api/show" -d "{\"model\":\"$LOCAL_OLLAMA_MODEL\"}" | python3 -c '
import json, sys
caps = set((json.load(sys.stdin).get("capabilities", []) or []))
if "tools" in caps:
    raise SystemExit(0)
print("local model does not advertise Ollama tool support: {}".format(sys.argv[1]), file=sys.stderr)
print("capabilities: {}".format(", ".join(sorted(caps)) if caps else "-"), file=sys.stderr)
raise SystemExit(1)
' "$LOCAL_OLLAMA_MODEL"
}

_claude_local() {
  local base="$HOME/.claude-local-profile"
  mkdir -p "$base/home" "$base/config" "$base/data" "$base/state" "$base/cache"

  HOME="$base/home" \
  XDG_CONFIG_HOME="$base/config" \
  XDG_DATA_HOME="$base/data" \
  XDG_STATE_HOME="$base/state" \
  XDG_CACHE_HOME="$base/cache" \
  ANTHROPIC_AUTH_TOKEN=ollama \
  ANTHROPIC_API_KEY=ollama \
  ANTHROPIC_MODEL="$LOCAL_OLLAMA_MODEL" \
  ANTHROPIC_SMALL_FAST_MODEL="$LOCAL_OLLAMA_MODEL" \
  ANTHROPIC_BASE_URL="$LOCAL_OLLAMA_BASE_URL" \
  claude --model "$LOCAL_OLLAMA_MODEL" "$@"
}

_codex_local() {
  _codex_local_catalog || return
  _codex_local_model_supports_tools || return
  codex \
    --profile lan_ollama \
    -c 'model_provider="lan_ollama"' \
    -m "$LOCAL_OLLAMA_MODEL" \
    -c "model_catalog_json=\"$LOCAL_CODEX_MODEL_CATALOG\"" \
    "$@"
}

_opencode_local_sync_config() {
  mkdir -p "$HOME/.config/opencode"
  python3 - "$LOCAL_OLLAMA_BASE_URL" "$LOCAL_OLLAMA_MODEL" "$HOME/.config/opencode/opencode.json" <<'PY'
import json
import pathlib
import sys
import urllib.request

base_url = sys.argv[1].rstrip("/")
active_model = sys.argv[2]
config_path = pathlib.Path(sys.argv[3])

with urllib.request.urlopen(base_url + "/api/tags", timeout=30) as response:
    data = json.loads(response.read().decode())

models = {}
for model in data.get("models", []):
    name = model.get("name")
    if name:
        models[name] = {"name": name}
if active_model and active_model not in models:
    models[active_model] = {"name": active_model}

config = {
    "$schema": "https://opencode.ai/config.json",
    "provider": {
        "ollama": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Ollama LAN",
            "options": {"baseURL": base_url + "/v1"},
            "models": models,
        }
    },
    "model": "ollama/" + active_model,
}
config_path.write_text(json.dumps(config, indent=2) + "\n")
PY
}

_opencode_local() {
  _opencode_local_sync_config || return
  opencode -m "ollama/$LOCAL_OLLAMA_MODEL" "$@"
}

alias claude-local='_claude_local'
alias codex-local='_codex_local'
alias opencode-local='_opencode_local'
