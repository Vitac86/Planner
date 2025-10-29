#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

nohup "$SCRIPT_DIR/start_planner.sh" "$@" >/dev/null 2>&1 &
# Detach the job from the current shell if possible.
if command -v disown >/dev/null 2>&1; then
  disown
fi
