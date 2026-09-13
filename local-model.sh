# Source this file from ~/.bashrc. Configuration is scoped to local wrappers;
# ordinary cloud CLIs retain their existing environment and credentials.
#
# The router's URL and key are per machine and never in this repo: they come from
# ~/.config/local-model/env, written by bin/install-client.sh. Without them the client
# refuses to run rather than guessing an endpoint.
if [ -f "$HOME/.config/local-model/env" ]; then
  . "$HOME/.config/local-model/env"
fi
export LOCAL_MODEL_BASE_URL="${LOCAL_MODEL_BASE_URL:-}"
export LOCAL_MODEL_API_KEY="${LOCAL_MODEL_API_KEY:-}"
export LOCAL_MODEL_MODEL="${LOCAL_MODEL_MODEL:-qwen3.8:27B}"
export LOCAL_MODEL_CONNECT_TIMEOUT="${LOCAL_MODEL_CONNECT_TIMEOUT:-5}"
export LOCAL_MODEL_TIMEOUT="${LOCAL_MODEL_TIMEOUT:-15}"
export LOCAL_MODEL_INFERENCE_TIMEOUT="${LOCAL_MODEL_INFERENCE_TIMEOUT:-180}"
export LOCAL_MODEL_REASONING_EFFORT="${LOCAL_MODEL_REASONING_EFFORT:-low}"
export LOCAL_CODEX_MODEL_CATALOG="${LOCAL_CODEX_MODEL_CATALOG:-$HOME/.codex/local-ollama-model-catalog.json}"
export LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW="${LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW:-32768}"
# Compatibility names used by existing local launchers. Never inherit the old
# LAN endpoint or Gemma default from a long-running desktop/tmux environment.
export LOCAL_OLLAMA_BASE_URL="${LOCAL_MODEL_BASE_URL%/v1}"
export LOCAL_OLLAMA_MODEL="$LOCAL_MODEL_MODEL"
_LOCAL_MODEL_CLIENT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/local_model.py"

_local_model_client() { python3 "$_LOCAL_MODEL_CLIENT" "$@"; }

local-help() { _local_model_client help; }

local-model() {
  case "${1:-help}" in
    use)
      if [ "$#" -ne 2 ]; then
        printf 'usage: local-model use MODEL\n' >&2
        return 2
      fi
      _local_model_client validate "$2" || return
      export LOCAL_MODEL_MODEL="$2" LOCAL_OLLAMA_MODEL="$2"
      printf 'Switched local model to %s\n' "$LOCAL_MODEL_MODEL"
      ;;
    *) _local_model_client "$@" ;;
  esac
}

_codex_local_catalog() { _local_model_client catalog; }

_codex_local() {
  python3 "${_LOCAL_MODEL_CLIENT%/*}/codex_bridge.py" "$@"
}

_opencode_local() {
  python3 "${_LOCAL_MODEL_CLIENT%/*}/codex_bridge.py" --opencode "$@"
}

_claude_local() {
  printf '%s\n' 'claude-local: this router exposes OpenAI Responses, not the Anthropic Messages API required by Claude Code.' >&2
  printf '%s\n' 'Use local-model ask or codex-local, or configure an Anthropic-compatible router first.' >&2
  return 2
}

# Functions also work in non-interactive shells, unlike aliases.
unalias claude-local codex-local opencode-local 2>/dev/null || true
claude-local() { _claude_local "$@"; }
codex-local() { _codex_local "$@"; }
opencode-local() { _opencode_local "$@"; }
