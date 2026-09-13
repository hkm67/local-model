#!/usr/bin/env bash
# Wire this checkout into the current account's shell.
#
#   bin/install-client.sh --base-url https://ROUTER/v1 --api-key KEY [--model ID]
#
# Writes ~/.config/local-model/env (0600) with the endpoint, key and default model, and
# appends one line to ~/.bashrc that sources local-model.sh FROM THIS CHECKOUT — no copy
# is made, so updating the checkout updates the shell. Re-run to change the endpoint.
set -euo pipefail

base_url=""; api_key=""; model=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --base-url) base_url="$2"; shift 2 ;;
    --api-key)  api_key="$2";  shift 2 ;;
    --model)    model="$2";    shift 2 ;;
    -h|--help)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done
[ -n "$base_url" ] && [ -n "$api_key" ] || { echo "usage: bin/install-client.sh --base-url URL --api-key KEY [--model ID]" >&2; exit 1; }
case "$base_url" in */v1) ;; *) echo "--base-url must end in /v1" >&2; exit 1 ;; esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$HOME/.config/local-model/env"

umask 077
mkdir -p "$HOME/.config/local-model"
# ${VAR:-value}: the file supplies defaults, so a value set for one shell (or a test's
# loopback server) still wins over the machine-wide endpoint.
{
  printf 'export LOCAL_MODEL_BASE_URL="${LOCAL_MODEL_BASE_URL:-%s}"\n' "$base_url"
  printf 'export LOCAL_MODEL_API_KEY="${LOCAL_MODEL_API_KEY:-%s}"\n' "$api_key"
  [ -n "$model" ] && printf 'export LOCAL_MODEL_MODEL="${LOCAL_MODEL_MODEL:-%s}"\n' "$model"
} > "$env_file"
chmod 600 "$env_file"

source_line="source $(printf '%q' "$repo_root/local-model.sh")"
touch "$HOME/.bashrc"
if ! grep -Fqx "$source_line" "$HOME/.bashrc"; then
  printf '\n# local-model (helpers sourced from the checkout)\n%s\n' "$source_line" >> "$HOME/.bashrc"
fi

echo "wrote $env_file and wired $repo_root/local-model.sh into ~/.bashrc"
echo "reload with: source ~/.bashrc   then: local-model current && local-model check"
