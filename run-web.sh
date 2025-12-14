#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer repo-local virtualenv, fall back to pimoroni if present
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
  # shellcheck source=/dev/null
  . "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f "$HOME/.virtualenvs/pimoroni/bin/activate" ]; then
  # shellcheck source=/dev/null
  . "$HOME/.virtualenvs/pimoroni/bin/activate"
fi

cd "$SCRIPT_DIR" || exit 1
exec python3 webui.py
