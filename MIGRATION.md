# Migration

Use this repo on fresh machines and machines with older hand-written helpers.

## Fresh Client

```bash
bin/install-client.sh --base-url http://YOUR_OLLAMA_HOST:11434 --default-model gemma4:26b
source ~/.bashrc
```

## Existing Client

If `~/.bashrc` already has a `BEGIN local-ollama-agent-setup` block, leave it while testing this repo. The installer appends a separate `# local-model` source line.

After testing, remove the old block manually:

```text
# BEGIN local-ollama-agent-setup
...
# END local-ollama-agent-setup
```

Then open a new shell or run:

```bash
source ~/.bashrc
```

## Ollama Host

Run:

```bash
ollama-host/create-agent-models.sh
```

The script skips models that are not installed and does not remove base model tags.
