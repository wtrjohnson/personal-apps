#!/usr/bin/env bash
# Launch the Notes Dashboard.
# Usage: ./run.sh
# Then open http://127.0.0.1:5050/ in your browser.

set -e
cd "$(dirname "$0")"

# Use the project's Python if a venv exists, else system python3.
if [ -x "./.venv/bin/python3" ]; then
  PY="./.venv/bin/python3"
else
  PY="python3"
fi

# Install deps if Flask isn't available yet.
if ! "$PY" -c "import flask" 2>/dev/null; then
  echo "Installing dashboard dependencies..."
  "$PY" -m pip install --user -r requirements.txt
fi

exec "$PY" app.py
