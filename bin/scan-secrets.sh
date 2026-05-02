#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

grep -RInE \
  '([0-9]{1,3}\.){3}[0-9]{1,3}|api[_-]?key|secret|password|token|PRIVATE KEY|github.com:[^/]+/|/home/[^/]+|/mnt/data|tailscale' \
  "$root" \
  --exclude-dir=.git \
  --exclude='scan-secrets.sh' \
  || true
