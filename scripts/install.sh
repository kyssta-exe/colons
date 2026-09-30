#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/colons"
VENV_DIR="${COLONS_VENV:-$APP_DIR/.venv}"
PYTHON_BIN="${COLONS_PYTHON:-python3}"
INSTALL_BROWSER=true
BUILD_WEB=true
INSTALL_HERMES=false
EXTRAS=full
for argument in "$@"; do
  case "$argument" in
    --dev) EXTRAS=full,dev ;;
    --skip-browser) INSTALL_BROWSER=false ;;
    --skip-web) BUILD_WEB=false ;;
    --hermes) INSTALL_HERMES=true ;;
    --help|-h) printf '%s\n' 'Usage: scripts/install.sh [--dev] [--hermes] [--skip-browser] [--skip-web]' 'Requires Python 3.10+ and Node.js 20+ with npm (unless --skip-web).' 'COLONS_PYTHON selects Python; COLONS_VENV selects the virtual environment.'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$argument" >&2; exit 2 ;;
  esac
done
if "$INSTALL_HERMES"; then EXTRAS="$EXTRAS,hermes"; fi
command -v "$PYTHON_BIN" >/dev/null || { printf '%s\n' 'Install Python 3.10+ first.' >&2; exit 1; }
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required")'
if "$BUILD_WEB"; then
  command -v npm >/dev/null || { printf '%s\n' 'Install Node.js 20+ and npm first.' >&2; exit 1; }
  node -e 'if (Number(process.versions.node.split(".")[0]) < 20) { throw new Error("Node.js 20+ is required") }'
fi
if [[ ! -x "$VENV_DIR/bin/python" ]]; then "$PYTHON_BIN" -m venv "$VENV_DIR"; fi
"$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1 || "$VENV_DIR/bin/python" -m ensurepip --upgrade
"$VENV_DIR/bin/python" -m pip install -e "$APP_DIR[$EXTRAS]"
if "$BUILD_WEB"; then
  (cd "$APP_DIR/web" && npm ci --no-audit --no-fund && npm run build)
fi
if "$INSTALL_BROWSER"; then
  "$VENV_DIR/bin/python" -m playwright install chromium
fi
BIN_DIR="${COLONS_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN_DIR"
if [[ ! -e "$BIN_DIR/colons" || -L "$BIN_DIR/colons" ]]; then
  ln -sfn "$VENV_DIR/bin/colons" "$BIN_DIR/colons"
fi
printf '\n%s\n' 'Colons installed. Run:' 'colons setup    # Choose provider, messaging, permissions, and more' 'colons          # Terminal interface' 'colons start    # Server in this terminal' 'colons web      # Open the web interface'
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  printf '\nAdd %s to PATH, or use %s/bin/colons directly.\n' "$BIN_DIR" "$VENV_DIR"
fi
