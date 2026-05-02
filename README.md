# local-model

Portable local-agent helpers for using a LAN Ollama server with Codex, Claude Code, and OpenCode.

## What This Installs

- `local-model`: switch and inspect models exposed by an Ollama server.
- `codex-local`: run Codex against the selected local model.
- `claude-local`: run Claude Code against the selected local model.
- `opencode-local`: run OpenCode against the selected local model.

Plain `codex`, `claude`, and `opencode` keep their normal cloud behavior.

## Client Install

```bash
git clone git@github.com:YOUR_USER/local-model.git
cd local-model
bin/install-client.sh \
  --base-url http://YOUR_OLLAMA_HOST:11434 \
  --default-model gemma4:26b \
  --small-fast-model qwen3.5:4b
source ~/.bashrc
```

## Usage

```bash
local-model current
local-model list
local-model caps qwen3.6-agent:35b
local-model use qwen3.6-agent:35b
local-model use-small qwen3.5:4b

codex-local
claude-local
opencode-local
```

## Ollama Host Setup

Run this on the Ollama host to create tuned `-agent` aliases without changing base model tags:

```bash
ollama-host/create-agent-models.sh
```

Then switch from any installed client:

```bash
local-model use qwen3.6-agent:35b
local-model use-small qwen3.5:4b
```

## Why Agent Aliases

Ollama tags can carry runtime defaults. Agent workflows need larger context, larger output budgets, and model-specific sampling. Separate `-agent` aliases let you keep base models unchanged while giving coding agents stable defaults.
