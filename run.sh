#!/usr/bin/env bash
# Start Recap Studio from source on Linux (after ./setup.sh). Extra options go to the app: --browser, --no-window.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run ./setup.sh first."
  exit 1
fi
exec .venv/bin/python -m recap "$@"
