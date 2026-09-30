#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/colons"
VENV_DIR="${COLONS_VENV:-$APP_DIR/.venv}"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  printf '%s\n' 'Run ./scripts/install.sh first, or set COLONS_VENV to your existing environment.' >&2
  exit 1
fi
# This optional file is user-owned shell configuration. Never print credentials.
ENV_FILE="${COLONS_ENV_FILE:-$APP_DIR/.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
cd "$APP_DIR"
export COLONS_DATA_DIR="${COLONS_DATA_DIR:-$APP_DIR/data}"
export COLONS_STORE_URL="${COLONS_STORE_URL:-$COLONS_DATA_DIR/memory.db}"
export COLONS_WORKSPACE="${COLONS_WORKSPACE:-$APP_DIR/workspace}"
export COLONS_WEB_DIST="${COLONS_WEB_DIST:-$APP_DIR/web/dist}"
mkdir -p "$COLONS_DATA_DIR" "$COLONS_WORKSPACE"
exec "$VENV_DIR/bin/python" -m uvicorn colons_api.main:app --host "${COLONS_HOST:-127.0.0.1}" --port "${COLONS_PORT:-8000}" "$@"
