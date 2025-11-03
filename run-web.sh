#!/bin/bash
set -e
# Activate pimoroni virtualenv if present
if [ -f "$HOME/.virtualenvs/pimoroni/bin/activate" ]; then
  # shellcheck source=/dev/null
  . "$HOME/.virtualenvs/pimoroni/bin/activate"
fi
# Move to the repo directory and run the web UI (webui.py)
cd "$HOME/squirt" || exit 1
exec python3 webui.py