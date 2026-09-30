#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${COLONS_PYTHON:-python3}"
(cd "$ROOT_DIR/colons/web" && npm ci --no-audit --no-fund && npm run build)
"$PYTHON_BIN" "$ROOT_DIR/scripts/package-web.py"
"$PYTHON_BIN" -m build --outdir "$ROOT_DIR/dist" "$ROOT_DIR/colons"
printf '%s\n' 'Release artifacts are in dist/. The wheel includes the web UI.'
