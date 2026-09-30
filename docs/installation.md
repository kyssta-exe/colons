# Installation and configuration

## Source installation

Install Python 3.10+ and Node.js 20+ with npm. Linux, macOS, and WSL2 can use the root scripts:

```bash
git clone https://github.com/kyssta-exe/colons.git
cd colons
./scripts/install.sh
./scripts/run.sh
```

`install.sh` creates `colons/.venv`, installs the Python package with full extras, runs
`npm ci` and the production UI build, and downloads Playwright Chromium. It does not
install operating-system packages automatically. On Linux, use
`colons/.venv/bin/python -m playwright install --with-deps chromium` if libraries are missing.

Options: `--dev` adds test/lint dependencies; `--skip-browser` skips the browser download;
`--skip-web` skips the frontend build. Browser tools need Chromium, and the chat UI needs
the built frontend. `COLONS_PYTHON` selects a Python executable; `COLONS_VENV` selects an
absolute virtual-environment directory. Set the same `COLONS_VENV` when running the app.

## Model setup

The run script loads `colons/.env` when present. Copy `colons/.env.example` first, then
edit the values. It is shell configuration: quote values with spaces and use a trusted file.
Use `COLONS_ENV_FILE` to select another environment file. Existing files are never overwritten
by the installer. You can instead configure `colons/colons.yaml`, or export variables directly.

For Ollama, run Ollama and pull a model such as `llama3.1`. For a hosted provider, use the
catalog name and a model supported by that provider:

```bash
COLONS_PROVIDER=deepseek
COLONS_API_KEY=your-provider-key
COLONS_MODEL=your-model-id
```

Use the model ID supported by your account or endpoint; Colons does not guess versions.
An OpenAI-compatible endpoint uses:

```bash
COLONS_PROVIDER=custom
COLONS_BASE_URL=https://your-endpoint.example/v1
COLONS_API_KEY=your-endpoint-key
COLONS_MODEL=your-model-id
```

Settings → Models applies runtime changes to existing agents. Those values are not saved
permanently. Environment/configuration is the source of truth after restart.

## Data and permissions

The source run script defaults to `colons/data` for persistence and `colons/workspace` for
file tools. Set `COLONS_DATA_DIR`, `COLONS_STORE_URL`, and `COLONS_WORKSPACE` to override them.
Relative paths are resolved from the active `colons/` directory when using the run script.

Messaging settings are saved privately in `messaging.json` under the data directory; saved
service values override environment defaults for that service. Permission mode is stored
in `permissions.json`. Both are restored on restart. Back up the complete data directory
with Colons stopped, and back up the workspace separately.

Settings → Permissions offers Auto (per-tool approvals), Approval (every agent tool call),
and Full access (no prompts for available tools). Disabled and user-reserved tools stay
unavailable. File tools enforce the workspace root; shell commands run with the server
account's access. Jobs without an interactive chat decline tools requiring approval.

The terminal is a command console. Each command starts a new shell; interactive programs
are unsupported. Closing its panel does not cancel a running command.

## Server access

The run script binds to `127.0.0.1:8000`. Change the port with `COLONS_PORT`. To serve other
machines, set `COLONS_HOST=0.0.0.0`, configure `COLONS_API_KEYS`, and use HTTPS through a reverse
proxy. This beta has shared API-key authentication, not independent user accounts.

`COLONS_API_KEY` authenticates to your model provider. `COLONS_API_KEYS` is a comma-separated
list of keys accepted by the Colons server. Enter a server key in Settings → Server access
and pass it to CLI commands with `--api-key`. Keep model/provider credentials out of issues,
screenshots, shell logs, and commits.

## Docker

```bash
cd colons
docker compose up -d --build
docker compose exec ollama ollama pull llama3.1
docker compose logs -f colons
```

The Compose stack binds Colons to localhost by default. `colons-data` stores state, `ollama-models`
stores model downloads, and `colons/workspace` is mounted for file tools. The image contains
Chromium and its Linux dependencies. Optional Redis is enabled with `--profile redis` and
`COLONS_CACHE_URL=redis://redis:6379/0`.

For a hosted/custom provider, set its provider/key/model and the correct base URL in `colons/.env`
before running Compose. The default Compose base URL points to its Ollama service, so override
`COLONS_BASE_URL` when changing providers. The Ollama service remains part of the default stack.

Stop with `docker compose down`. Avoid `down --volumes` unless you intend to delete stored state.
The server's workspaces and data should be writable by container user UID 1000.

## Install a release wheel

When a release is available, download its `.whl` from the repository's GitHub Releases page:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install './colons-0.0.1b1-py3-none-any.whl[full]'
python -m playwright install chromium
colons serve --host 127.0.0.1 --port 8000
```

The wheel includes the production UI; Node.js is unnecessary at runtime. Set environment
variables or create `colons.yaml` in the directory where you start the server. Wheel installs
use the current directory as their default workspace/data location unless configured otherwise.
GitHub release publishing is automated for version tags; this repository does not automatically
publish to PyPI.

## Updating and troubleshooting

Stop the server, back up data, `git pull --ff-only`, rerun `./scripts/install.sh`, and start
`./scripts/run.sh`. Do not delete the data directory to update the app.

- Missing UI: rerun the installer without `--skip-web`, or build `colons/web` with `npm ci && npm run build`.
- Browser launch failure: install Chromium and its system dependencies as described above.
- Provider failure: check model ID, base URL, credentials, and `colons doctor` output.
- Permission denial in background work: interactive approvals are available in chat only.
- Port already in use: choose another `COLONS_PORT` or stop the existing Colons process.

Run diagnostics while the server is up:

```bash
colons/.venv/bin/colons --url http://127.0.0.1:8000 doctor
```
