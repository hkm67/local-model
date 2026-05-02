#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

create_if_present() {
  local base="$1"
  local alias_name="$2"
  local modelfile="$3"
  local tmp_file

  if ! ollama show "$base" >/dev/null 2>&1; then
    echo "skip: $base is not installed"
    return 0
  fi

  tmp_file="/tmp/local-model-${alias_name//[:\/]/-}.Modelfile"
  sed "s|^FROM .*|FROM $base|" "$modelfile" > "$tmp_file"

  echo "create: $alias_name from $base"
  ollama create "$alias_name" -f "$tmp_file"
}

create_if_present "qwen3.6:35b" "qwen3.6-agent:35b" "$root/modelfiles/qwen36-agent.Modelfile"
create_if_present "gemma4:26b" "gemma4-agent:26b" "$root/modelfiles/gemma-agent.Modelfile"
create_if_present "gemma4:31b" "gemma4-agent:31b" "$root/modelfiles/gemma-agent.Modelfile"
create_if_present "qwen3-coder-next:latest" "qwen3-coder-next-agent:latest" "$root/modelfiles/qwen-coder-agent.Modelfile"

echo
ollama list
