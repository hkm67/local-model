# Migration

## From the Ollama-native helpers (before September 2026)

The earlier generation of this repo talked to Ollama's native API (`/api/tags`,
`/api/chat`) and installed a *copy* of `shell/local-helpers.sh` under
`~/.local/share/local-model`, wired by a `# local-model` line in `~/.bashrc`, with
`LOCAL_OLLAMA_*` variables in `~/.config/local-model/env` and a `lan_ollama` /
`lan_gemma` provider in `~/.codex/config.toml`.

None of that is used any more. On each machine:

1. Remove the old `# local-model` source line and any `BEGIN local-ollama-agent-setup`
   block from `~/.bashrc`; delete `~/.local/share/local-model`.
2. Run `bin/install-client.sh --base-url https://ROUTER/v1 --api-key KEY` from the
   checkout. It overwrites `~/.config/local-model/env` with `LOCAL_MODEL_*` values and
   sources the checkout directly.
3. The `lan_*` Codex providers can stay or go; `codex-local` configures its own
   provider per run and does not read them.
4. `source ~/.bashrc`, then `local-model current` and `local-model check`.

`LOCAL_OLLAMA_BASE_URL` / `LOCAL_OLLAMA_MODEL` are still exported as compatibility
aliases derived from the new values, for launchers that read the old names.
