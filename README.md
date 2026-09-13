# local-model

Shell helpers for using a private OpenAI-compatible Responses router from coding CLIs,
without touching their normal cloud configuration.

- `local-model current|list|caps MODEL|use MODEL|check|ask PROMPT` — inspect and switch
  the selected model, with bounded, authenticated requests (`local_model.py`).
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

## Ollama host

`ollama-host/create-agent-models.sh` creates tuned `-agent` aliases on the machine that
runs the models, leaving base tags untouched.

## Tests

```bash
python3 -m pytest -q test_local_model.py test_codex_bridge.py
bin/scan-secrets.sh     # must print nothing but placeholders before a push
```
