#!/usr/bin/env bash
# PlotSpectra one-click launcher (macOS: double-click; Linux: run ./PlotSpectra.command).
# First run creates a private Python environment in .venv and installs the packages.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python 3.10+ not found. Install it from https://www.python.org/downloads/ and run this again."
  read -r -p "Press Enter to close"; exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Creating private Python environment (.venv) ..."
  "$PY" -m venv .venv || { echo "Could not create .venv"; read -r; exit 1; }
fi
if [ ! -f ".venv/.installed" ]; then
  echo "Installing packages - once only, a few minutes ..."
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt || { echo "Installation failed (internet?)"; read -r; exit 1; }
  echo ok > .venv/.installed
fi
echo "Starting PlotSpectra ... keep this window open while you use the app (Ctrl+C stops it)."
.venv/bin/python app/server.py "$@"
