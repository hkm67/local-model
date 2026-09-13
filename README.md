# local-model

Shell helpers for using a private OpenAI-compatible Responses router from coding CLIs,
without touching their normal cloud configuration.

- `local-model current|list|caps MODEL|use MODEL|check|ask PROMPT` — inspect and switch
  the selected model, with bounded, authenticated requests (`local_model.py`).
- `local-model doctor [--probe]` — diagnose the endpoint end to end (config, DNS, TCP,
  tailnet peer, discovery, selected model; `--probe` also sends one tiny request). It
  distinguishes an unreachable host from a reachable endpoint whose model backend is down.
- `local-model connect [curl|python|node|env]` — print ready-to-paste instructions for
  connecting another project directly to the router. Snippets reference
  `$LOCAL_MODEL_BASE_URL`/`$LOCAL_MODEL_API_KEY`, so they never embed the secret.
- `codex-local [args]` — run Codex against the selected model through a per-run loopback
  adapter (`codex_bridge.py`); Codex keeps all tool execution and approvals.
- `opencode-local [args]` — the same adapter in OpenCode mode.
- `claude-local` — refuses with an explanation: the router speaks Responses, not the
  Anthropic Messages API Claude Code needs.

Plain `codex`, `claude` and `opencode` are unchanged.

## Install (per account, per machine)

```bash
git clone https://github.com/YOUR_USER/local-model.git
cd local-model
bin/install-client.sh --base-url https://YOUR_ROUTER/v1 --api-key YOUR_KEY --model qwen3.8:27B
source ~/.bashrc
local-model current && local-model check
```

The endpoint and key live only in `~/.config/local-model/env` (0600). The shell sources
`local-model.sh` from the checkout, so updating the checkout updates the helpers. Without
the env file every command exits 2 with a message saying so.

## Timeouts and overrides

`LOCAL_MODEL_CONNECT_TIMEOUT` (5s), `LOCAL_MODEL_TIMEOUT` (15s, metadata),
`LOCAL_MODEL_INFERENCE_TIMEOUT` (180s), `LOCAL_MODEL_REASONING_EFFORT` (low). `local-model
use` validates the ID case-sensitively against `GET /v1/models` before switching. `check`
proves discovery, not inference; use `ask` for that.

## Connecting another project

Two supported paths, both discoverable from `local-model connect`:

1. **Shell out to this CLI** — `local-model ask 'prompt'`, `local-model list`. Simplest;
   nothing to wire up. Good for scripts and agents that can run a command.
2. **Connect directly** — `local-model connect python|node|curl|env` prints a snippet for
   an OpenAI-compatible **Responses** client (`POST /responses`, `stream:false`,
   `store:false`). The router is *not* a Chat Completions endpoint for general text, does
   not stream function calls, and does not advertise embeddings. `local-model connect`
   with no target prints the overview and these caveats. Reachable only over Tailscale.

## Ollama host

`ollama-host/create-agent-models.sh` creates tuned `-agent` aliases on the machine that
runs the models, leaving base tags untouched.

## Tests

```bash
python3 -m pytest -q test_local_model.py test_codex_bridge.py
bin/scan-secrets.sh     # must print nothing but placeholders before a push
```
