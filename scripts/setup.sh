#!/usr/bin/env bash
# One-time setup: Python env for the road analysis (software/roadimu) + its tests.
#   ./scripts/setup.sh            (uses python3; override with PYTHON=/path/to/python ./scripts/setup.sh)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"

"$PY" -c 'import sys; assert sys.version_info >= (3, 10), f"Python 3.10+ needed, found {sys.version.split()[0]}"'
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r software/roadimu/requirements.txt

echo "Running tests..."
(cd software/roadimu && ../../.venv/bin/python -m pytest -q)
echo
echo "Setup OK. Next: ./scripts/demo.sh (no hardware needed) or ./scripts/start_servers.sh (real ride)."
