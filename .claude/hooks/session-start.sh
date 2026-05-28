#!/bin/bash
set -euo pipefail

# Only run in remote Claude Code on the web environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Create venv with Python 3.12 if it doesn't exist
if [ ! -d ".venv" ]; then
  python3.12 -m venv .venv
fi

# Install project + dev dependencies
.venv/bin/pip install --quiet -e ".[dev]"

# Persist PYTHONPATH for the session
echo 'export PYTHONPATH="."' >> "$CLAUDE_ENV_FILE"
