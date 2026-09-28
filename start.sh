#!/bin/sh
# LastMile Alert - macOS/Linux startup script.
# Installs dependencies if needed, then launches the app.
cd "$(dirname "$0")"
echo "=== LastMile Alert ==="
python3 -m pip install -r requirements.txt || {
  echo "[ERROR] pip failed. Install Python 3.11+ first: https://www.python.org/downloads/"
  exit 1
}
python3 app.py
