# Contributing

The Python backend/CLI and React SPA live in `colons/`. The repository contains one
application, plus root installation scripts, deployment documentation, and CI workflows.

Run `./scripts/install.sh --dev`, then activate `colons/.venv`. `make test` runs the Python
suite, `make lint` checks the active backend, and `make web` builds the SPA. For UI development,
run the API on port 8000 and `npm run dev` from `colons/web`; Vite proxies API/WebSocket traffic.

Tests must exercise behavior that could regress. UI changes should include a desktop and
mobile visual check. The browser smoke scripts in `colons/tests` require a running local
server and Playwright Chromium. `COLONS_BROWSER_EXECUTABLE` can point at an existing Chromium
executable. `web_smoke.py` tests native browser operations with scripted chat events;
`web_workspace_smoke.py` checks settings, sidebars, attachments, and a harmless real shell
command. Use a disposable data directory and do not connect production messaging services.

CI checks Python 3.10/3.12, lint, frontend compilation, native browser integration, Docker image startup, and UI
smoke tests. Keep credentials and personal database contents out of commits and logs.

To build a release, install the Python `build` package and run:

```bash
COLONS_PYTHON=colons/.venv/bin/python ./scripts/build-release.sh
```

This builds the SPA, copies its assets into Python package data, and writes wheel/sdist
artifacts to `dist/`. Generated static assets stay ignored in Git. Inspect the wheel and
try it in a clean environment before releasing.

Update Python's version in `colons/pyproject.toml` and `colons_core/__init__.py`, npm's version
in `colons/web/package.json` and its lockfile, and the user-facing version in Settings.
The beta uses Python `0.0.1b1` and npm/tag `0.0.1-beta.1` / `v0.0.1-beta.1`.
A pushed version tag triggers the GitHub release workflow, which attaches the built wheel
and source distribution. There is no automatic PyPI or Docker registry publication.
