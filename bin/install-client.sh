#!/usr/bin/env bash
set -euo pipefail

base_url="${LOCAL_OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
default_model="${LOCAL_OLLAMA_MODEL:-gemma4:26b}"
install_dir="${LOCAL_MODEL_HOME:-$HOME/.local/share/local-model}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
helpers_src="$repo_root/shell/local-helpers.sh"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --base-url)
      base_url="$2"
      shift 2
      ;;
    --default-model)
      default_model="$2"
      shift 2
      ;;
    --install-dir)
      install_dir="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: bin/install-client.sh [options]

Options:
  --base-url URL          Ollama base URL, default http://127.0.0.1:11434
  --default-model MODEL   Default local model, default gemma4:26b
  --install-dir DIR       Helper install dir, default ~/.local/share/local-model
EOF
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$install_dir" "$HOME/.config/local-model"
cp "$helpers_src" "$install_dir/local-helpers.sh"

cat > "$HOME/.config/local-model/env" <<EOF
export LOCAL_OLLAMA_BASE_URL="\${LOCAL_OLLAMA_BASE_URL:-$base_url}"
export LOCAL_OLLAMA_MODEL="\${LOCAL_OLLAMA_MODEL:-$default_model}"
export LOCAL_CODEX_MODEL_CATALOG="\${LOCAL_CODEX_MODEL_CATALOG:-\$HOME/.codex/local-ollama-model-catalog.json}"
export LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW="\${LOCAL_CODEX_FALLBACK_CONTEXT_WINDOW:-131072}"
EOF

bashrc="$HOME/.bashrc"
source_line="[ -f \"$install_dir/local-helpers.sh\" ] && . \"$install_dir/local-helpers.sh\""
touch "$bashrc"
if ! grep -Fq "$source_line" "$bashrc"; then
  {
    echo ""
    echo "# local-model"
    echo "$source_line"
  } >> "$bashrc"
fi

python3 - "$base_url" <<'PY'
import pathlib
import sys

base_url = sys.argv[1].rstrip("/")
path = pathlib.Path.home() / ".codex" / "config.toml"
path.parent.mkdir(parents=True, exist_ok=True)
text = path.read_text() if path.exists() else ""

block = f'''
[model_providers.lan_ollama]
name = "LAN Ollama"
base_url = "{base_url}/v1"
wire_api = "responses"

[profiles.lan_ollama]
model_provider = "lan_ollama"
'''

if "[model_providers.lan_ollama]" not in text:
    if text and not text.endswith("\n"):
        text += "\n"
    text += block
    path.write_text(text)
PY

echo "Installed local-model helpers."
echo "Reload with: source ~/.bashrc"
