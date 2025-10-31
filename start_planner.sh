#!/usr/bin/env bash
set -euo pipefail

# Change working directory to the location of this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Try to activate a virtual environment if it exists.
activate_venv() {
  local candidates=(".venv/bin/activate" "venv/bin/activate")
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      # shellcheck disable=SC1090
      source "$candidate"
      # After activation, rely on the python from the venv.
      PY_CMD="python"
      return 0
    fi
  done
  return 1
}

PY_CMD=""
if activate_venv; then
  :
else
  # Search for specific Python versions first.
  for ver in 3.13 3.12 3.11 3; do
    if command -v "python$ver" &>/dev/null; then
      PY_CMD="python$ver"
      break
    fi
  done

  # Fallback to the generic commands if nothing was found yet.
  if [[ -z "$PY_CMD" ]] && command -v python3 &>/dev/null; then
    PY_CMD="python3"
  fi
  if [[ -z "$PY_CMD" ]] && command -v python &>/dev/null; then
    PY_CMD="python"
  fi
fi

if [[ -z "$PY_CMD" ]]; then
  echo "[ERROR] Python 3.11+ не найден. Установите Python или создайте виртуальное окружение." >&2
  exit 1
fi

if ! "$PY_CMD" -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  echo "[ERROR] Требуется Python 3.11 или новее. Текущая версия: $($PY_CMD -V 2>&1)" >&2
  exit 1
fi

exec "$PY_CMD" -u main.py "$@"
