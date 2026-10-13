#!/usr/bin/env bash
# Double-click this file (Mac), or run:  bash START_HERE_Mac_Linux.command
cd "$(dirname "$0")" || exit 1

pause_and_exit() { echo; read -r -p "Press Enter to close this window." _; exit "${1:-1}"; }

PY=""
for c in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "HydroWitness needs Python 3.11 or newer, and it was not found."
  echo "Install it from https://www.python.org/downloads/ , then double-click this file again."
  pause_and_exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "First run: setting up a private Python environment (about a minute)..."
  "$PY" -m venv .venv || { echo "Could not create the environment. On Ubuntu/Debian run: sudo apt install python3-venv"; pause_and_exit 1; }
fi
if [ ! -f ".venv/.ready" ]; then
  echo "Installing the packages HydroWitness needs (needs internet, first run only)..."
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements-run.txt \
    || { echo "The install failed. Check your internet connection and try again."; pause_and_exit 1; }
  touch .venv/.ready
fi

.venv/bin/python run_local.py "$@"
status=$?
[ $status -ne 0 ] && pause_and_exit $status
