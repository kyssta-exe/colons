> Current release: **v0.0.1 beta**

# Colons

**An open-source, self-hosted AI assistant workspace.**

For the recommended installer and deployment instructions, see the [repository README](../README.md) and [installation guide](../docs/installation.md).

Colons is a complete agentic harness: it plans, calls tools, remembers, runs tasks in the
background, talks to any AI provider you have, speaks with Microsoft Edge TTS, and ships a
ChatGPT-style web UI + a full CLI. One Python package, zero required services, MIT licensed.

```
┌──────────────┐  ┌──────────────┐  ┌───────────────┐
│  Web UI      │  │  CLI         │  │  REST / WS    │
│  (React SPA) │  │  (colons …)  │  │  /v1 (OpenAI) │
└──────┬───────┘  └──────┬───────┘  └──────┬────────┘
       └─────────────────┼─────────────────┘
                    ┌────▼─────────────┐
                    │  Colons API      │
                    │  + Agent Harness │
                    └────┬─────────────┘
        ┌────────────────┼──────────────────┬─────────────┐
   ┌────▼─────┐    ┌─────▼─────┐      ┌─────▼─────┐  ┌────▼────┐
   │ Providers│    │  Memory   │      │   Tools   │  │  Voice  │
   │ 29 APIs  │    │ SQLite/PG │      │ 21 built- │  │ Edge    │
   │ + custom │    │ +cache    │      │ ins       │  │ TTS     │
   └──────────┘    └───────────┘      └───────────┘  └─────────┘
```

## Features

- **Agentic harness** — reasoning loop with tool calling, iteration budgets, cancellation
- **29 providers out of the box** — OpenAI, Anthropic, Google, OpenRouter, Groq, Together,
  DeepSeek, xAI, Mistral, Perplexity, Cerebras, SambaNova, NVIDIA, GitHub Models, Ollama,
  LM Studio, vLLM, llama.cpp, LocalAI, Jan, KoboldCpp, plus **any OpenAI-compatible endpoint**
- **Long-term memory** — SQLite by default (zero-config), Postgres + pgvector for scale;
  semantic recall, per-user/per-agent isolation, pruning
- **Caching** — in-memory by default, Redis optional; exact-match semantic response cache
- **21 built-in tools** — filesystem, shell, web search/fetch, HTTP, Python, calculator,
  regex, JSON, time, memory tools — with **permission levels** (auto / pre-approved / ask / handoff)
- **Autonomous tasks** — queue, plan, execute, and report; background worker
- **Bots** — a roster of named agents with their own persona, avatar, model and chats
- **Bot-to-bot messaging** — `message_agent` tool with attribution, canonical Bot Chats, `[SILENT]` passes
- **Group rooms** — 2–6 bots in a shared room; serial rounds, @mentions, `@user` escalation, hard caps
- **Peers** — message bots on other Colons instances over HTTP (`peer/agent` handles)
- **Cronjobs** — cron / `@every` / `@at` schedules, continuity, notepad and monitor mode
- **Proactive mode** — periodic background reviews without being asked
- **Workspace dashboard** — setup, background tasks, schedules, memory, browser, and diagnostics
- **Native browser** — warm Chromium shared across agents, isolated sessions, compact page reads, element refs, gated actions, screenshots
- **Chat attachments** — images and text/code/data files, with persistent history and native provider translation
- **ChatGPT-style web UI** — streaming replies, tool-call cards, session history, responsive
- **Full CLI** — interactive chat, cron, doctor, logs, messaging, tasks, memory, tools, voice
- **Messaging apps** — Telegram (long polling), Discord (gateway), Slack (webhooks/Events API),
  generic webhooks — talk to the same agent from any chat
- **Microsoft Edge TTS** — 300+ neural voices, free; `pyttsx3` offline fallback
- **`colons doctor`** — full environment/config/provider/storage diagnostics
- **Logs & debug** — in-memory ring buffer, rotating files, `/api/debug/*`, request IDs & timing
- **Avatars & pets** — 14 built-in characters, deterministic SVG generation, fully local
- **OpenAI-compatible endpoint** — streaming and non-streaming, point any existing tool at `http://localhost:8000/v1`
- **Speech-to-text** — `/api/voice/transcribe` forwards audio to Whisper-compatible providers
- **Usage tracking** — tokens, cached tokens, per-tool stats, per-agent counters
- **Security** — approval gating, workspace sandbox, API keys, rate limiting, no telemetry

## Quick start

### Docker (recommended)

```bash
cp .env.example .env
docker compose up -d          # Colons + Ollama
# open http://localhost:8000
```

Pull a model into Ollama once:

```bash
docker exec -it colons-ollama ollama pull llama3.1
```

### Local install

```bash
pip install -e ".[full]"      # includes CLI, voice, rich, redis, postgres extras
colons init                   # writes colons.yaml
colons serve                  # http://localhost:8000   (UI + API)
colons                        # interactive CLI chat (in another terminal)
```

No Ollama? Point Colons at any provider:

```bash
COLONS_PROVIDER=openai COLONS_API_KEY=sk-... colons serve
# or
COLONS_PROVIDER=anthropic COLONS_API_KEY=sk-ant-... colons serve
# or any OpenAI-compatible server
COLONS_PROVIDER=custom COLONS_BASE_URL=http://localhost:8001/v1 COLONS_MODEL=my-model colons serve
```

## CLI

```bash
colons                                    # interactive chat (streaming, tool cards)
colons run "summarize the latest AI news" # one-shot
colons task add "audit my AWS bill"       # queue an autonomous task
colons task list
colons memory search "deployment server"  # semantic recall
colons tools list                         # all tools + permissions
colons tools run calculate '{"expression": "2**10"}'
colons provider list                      # 29 providers
colons provider switch --name groq --model llama-3.3-70b-versatile
colons voice voices --locale en-US        # Edge TTS voices
colons voice speak --text "Hello there"
colons voice transcribe recording.webm    # speech-to-text (Whisper-compatible)
colons status                             # usage & tool stats

# cronjobs
colons cron add "0 9 * * 1-5" "Morning standup summary" --name standup
colons cron add "@every 30m" "Check the deploy queue" --continuity --notes "known flaky: X"
colons cron add "@every 5m" "Handle new alerts" --monitor-command "curl -s status-api"
colons cron list
colons cron run <id>                      # run now
colons cron history
colons cron disable <id>
colons cron validate "*/15 9-17 * * 1-5"

# bots (named agents)
colons bots list
colons bots create --name Researcher --title "Deep Researcher" --model gpt-4o
colons bots update researcher --description "Finds and summarizes sources"
colons bots delete researcher

# group rooms (2-6 bots)
colons rooms list
colons rooms create --name standup --members researcher,writer
colons rooms send <room-id> --text "How is the release looking?"
colons rooms history <room-id>

# peers (cross-instance)
colons peer list
colons peer dm spark/researcher --text "any updates?"
colons peer inbox --target default       # read a bot's Bot Chat"

# diagnostics, logs, debug
colons doctor                             # environment + config + provider checks
colons doctor --json                      # machine-readable, exit code = health
colons logs --level WARNING --limit 50
colons logs --level-set DEBUG             # change the server log level live
colons debug stats                        # process, agents, cache, memory
colons debug config / colons debug env    # redacted config & environment

# messaging
colons messaging status                   # adapter connection state
colons messaging send --adapter telegram --chat-id 123456 --text "Build finished"
```

## Cronjobs (scheduling)

Colons has a built-in scheduler with three schedule kinds:

| Syntax | Example | Meaning |
|---|---|---|
| cron | `0 9 * * 1-5` | weekdays at 09:00 |
| interval | `@every 30m` | every 30 minutes (min 5s) |
| one-shot | `@at 2026-10-01T09:00:00` | a single run, then disabled |

Schedules are stored in `<data_dir>/schedules.db`, survive restarts, and each run
executes the prompt through the agent (tools and memory included). Results can be
delivered to any messaging adapter:

```bash
colons cron add "0 18 * * 1-5" "Summarize today's commits" \
  --name eod --notify-adapter telegram --notify-chat-id 12345678
```

REST: `GET/POST /api/schedules`, `PATCH/DELETE /api/schedules/{id}`,
`POST /api/schedules/{id}/run`, `GET /api/schedules/history/recent`,
`GET /api/schedules/validate/{expression}`.

## Messaging apps

Talk to the same agent from Telegram, Discord, Slack, or a generic webhook.
Each conversation gets its own session, and platform users map to separate agents.

### Telegram

```bash
# 1. Create a bot with @BotFather and copy the token
export TELEGRAM_BOT_TOKEN=123456:ABC...
colons serve
# 2. Message your bot. Commands: /new /status /help
```

Uses long polling — **no public URL required**. Markdown is converted to
Telegram HTML; long replies are chunked automatically.

### Discord

```bash
# 1. Create an application at https://discord.com/developers/applications
# 2. Bot -> enable the "MESSAGE CONTENT" privileged intent
# 3. Invite the bot to your server
export DISCORD_BOT_TOKEN=MTIz...
colons serve
```

Raw Gateway v10 implementation — no `discord.py` dependency. The bot replies
when mentioned in servers and always in DMs.

### Slack

```bash
# Outgoing only (notifications):
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Two-way (bot token + Events API):
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=...
# Point Slack's Events API at https://your-host/api/messaging/slack/events
```

### Generic webhooks

Any system can talk to Colons. Configure in `colons.yaml`:

```yaml
messaging:
  webhooks:
    alerts:
      enabled: true
      incoming_token: "secret-token"       # required to accept messages
      outgoing_url: "https://example.com/hook"  # where replies go
```

```bash
curl -X POST http://localhost:8000/api/messaging/webhook/alerts?token=secret-token \
  -H 'content-type: application/json' \
  -d '{"text": "Disk usage hit 95% on db-1", "user_id": "monitoring"}'
# The agent's reply is POSTed to outgoing_url
```

## Doctor

`colons doctor` (or `GET /api/doctor`) checks and explains:

- Python version and **required/optional dependencies**
- Config loading, data-dir/workspace writability, auth posture
- **Provider** reachability (TCP + model listing) and API key presence
- **Memory store** read/write probe and cache backend (in-memory/Redis)
- Voice engine availability, scheduler state, messaging adapter connections
- Server port availability and free disk space

Exit code is `0` when healthy and `1` when any check fails, so it fits into CI
and container healthchecks.

## Logs & debugging

- Server logs stream to stderr, an optional rotating file (`COLONS_LOG_FILE`),
  and an in-memory ring buffer served by `GET /api/debug/logs`
- `GET /api/debug/stats` — pid, uptime, RSS, threads, asyncio tasks, agents,
  schedules, cache, memory entries
- `GET /api/debug/config` / `GET /api/debug/env` — redacted views for support
- `POST /api/debug/log-level?level=DEBUG` — change verbosity live
- Every request gets a `x-request-id` (echoed in logs) and `x-response-time-ms`

## API

Full OpenAPI docs at `/docs`. Key endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | non-streaming chat |
| `WS /ws/chat` | streaming agent events (typed) |
| `GET/POST /api/sessions` | session management |
| `POST /api/tasks` · `POST /api/tasks/{id}/run` | autonomous tasks |
| `GET /api/memory/search` · `POST /api/memory` | long-term memory |
| `GET /api/tools` · `POST /api/tools/execute` | tools |
| `GET /api/avatars` · `POST /api/avatars/select` | avatars |
| `GET /api/voice/voices` · `POST /api/voice/tts` | Edge TTS |
| `POST /api/voice/transcribe` | speech-to-text (multipart upload) |
| `GET /api/providers` · `POST /api/providers/switch` | provider management |
| `GET /api/usage` | token & tool usage |
| `GET/POST /api/schedules` · `POST /api/schedules/{id}/run` | cronjobs (continuity, notepad, monitor) |
| `GET/POST /api/bots` · `PATCH/DELETE /api/bots/{id}` | bot roster |
| `GET/POST /api/rooms` · `POST /api/rooms/{id}/messages` | group rooms |
| `POST /api/peer/message` · `GET /api/peers` | cross-instance bot DMs |
| `GET /api/doctor` | diagnostics report |
| `GET /api/debug/logs` · `GET /api/debug/stats` | logs & runtime stats |
| `GET /api/messaging/status` · `POST /api/messaging/send` | messaging |
| `POST /api/messaging/slack/events` | Slack Events API receiver |
| `POST /api/messaging/webhook/{name}` | generic inbound webhook |
| `POST /v1/chat/completions` | **OpenAI-compatible** passthrough |

## Providers

Anything OpenAI-compatible works by name or as `custom`. Full catalog in
`backend/colons_core/providers/__init__.py`. Adding a provider is usually one dict entry:

```python
PROVIDER_CATALOG["my-provider"] = {
    "kind": "openai_compat",
    "base_url": "https://api.my-provider.com/v1",
    "env": ["MY_PROVIDER_API_KEY"],
    "default_model": "my-model",
    "display": "My Provider",
}
```

Native (non-OpenAI) APIs — Anthropic Messages, Gemini generateContent — have their own
adapters in the same package.

## Tools & permissions

| Level | Behaviour |
|---|---|
| `auto` | runs without asking (e.g. `read_file`, `calculate`, `web_search`) |
| `pre_approved` | runs only when the user explicitly asked (e.g. `write_file`) |
| `ask` | requires approval callback / confirmation (e.g. `run_command`, `run_python`) |
| `handoff` | never runs autonomously — always handed to the human |

Add your own:

```python
from colons_core.tools import tool, PermissionLevel

@tool(description="Look up a stock price", category="finance")
async def stock_price(symbol: str) -> float:
    ...
```

## Memory

- **Default:** SQLite file (`colons.db`), in-process cosine similarity — nothing to install
- **Scale:** `COLONS_STORE_URL=postgresql://user:pass@host/db` with the `pgvector` extension
- **Cache:** in-memory by default; `COLONS_CACHE_URL=redis://…` for Redis
- Embeddings use your active provider when they support them, with a deterministic local
  fallback so memory works even fully offline
- **Sessions persist** in `<data_dir>/sessions.db` — chat history in the web UI, CLI and
  messaging apps survives restarts; sessions are capped at 500 messages and auto-pruned

## Streaming & compatibility

`POST /v1/chat/completions` accepts `"stream": true` and returns OpenAI-style SSE
(`data: {...}\n\n` … `data: [DONE]`), so editors, SDKs and gateways that speak the
OpenAI protocol work against Colons unchanged.

## Bots, rooms and peers

A **bot** is a named agent with its own persona, avatar, model and chat history.
Bots message each other with the `message_agent` tool; DMs land in the target's
canonical **Bot Chat** (`bot-chat-<id>`) with attribution
(`Message from 🤖 Researcher (@researcher): …`). A bot with nothing to add answers
`[SILENT]`, and the caller gets an empty reply instead of the token.

```
# Ask your main bot to delegate:
> researcher have a look at the deploy logs
# -> the active bot calls message_agent("@researcher", ...), waits, and reports back
```

**Group rooms** put 2–6 bots in one place. Your message triggers up to three serial
rounds: @-mentioned bots respond (everyone when nobody is mentioned), each bot replies
briefly or passes, and mentions inside replies pull teammates into the next round.
`@user` escalates a decision — the room shows a **needs you** badge. Hard caps
(10 messages, 3 rounds) keep rooms from spinning.

**Peers** let bots on one Colons instance message bots on another:

```yaml
# colons.yaml
peers:
  spark:
    url: http://spark.lan:8000
    api_key: <spark API key>
    agents: [researcher]      # optional, shown in the roster
```

```bash
colons peer dm spark/researcher --text "any updates?"
```

The remote instance receives `POST /api/peer/message`, runs one turn in the target
bot's Bot Chat, and returns the reply. Set `COLONS_API_KEYS` on both sides so peer
calls authenticate with the same keys.

### Cron continuity, notepad and monitor mode

- `continuity: true` — the previous run's result is included in the next prompt
  (great for monitors that must dedupe what they already reported)
- `notes` — a durable notepad injected into every run
- `monitor_command` — a shell command whose output is hashed; **when nothing changed
  the LLM is skipped entirely** (`last_status: unchanged`)

## Voice

Edge TTS is free, keyless, and has 300+ neural voices:

```bash
pip install edge-tts
COLONS_VOICE_ENABLED=true colons serve
```

Click **speak** under any reply in the UI, or `colons voice speak --text "…"`.
`pyttsx3` is used automatically when `edge-tts` is unavailable.

Speech-to-text works through the active provider's transcription endpoint
(OpenAI/Whisper-compatible):

```bash
colons voice transcribe meeting.webm --language en
curl -F "file=@meeting.webm" http://localhost:8000/api/voice/transcribe
```

## Configuration

Environment variables (`.env.example`) or `colons.yaml`:

```yaml
provider:
  name: ollama
  base_url: http://localhost:11434
  model: llama3.1
  temperature: 0.7
memory:
  store_url: colons.db          # or postgresql://…
  cache_url: ""                 # or redis://…
tools:
  workspace: .
  auto_approve: false
  shell_allowlist: [ls, cat, pwd, python3, git]
voice:
  enabled: false
  tts_engine: edge
  voice: en-US-AriaNeural
agent:
  name: Colons
  proactive: true
  max_iterations: 15
scheduler:
  enabled: true
  tick_interval: 15
  timezone: UTC
bot_messaging: true               # register the message_agent tool
peers: {}                         # name -> {url, api_key, agents}
peers_timeout: 120                # seconds a bot DM may hold the connection
messaging:
  telegram:  { enabled: false, bot_token: "" }
  discord:   { enabled: false, bot_token: "" }
  slack:     { enabled: false, webhook_url: "", signing_secret: "" }
  webhooks:  {}
logging:
  level: INFO
  file: ""                      # optional rotating log file
  buffer_size: 1000
server:
  host: 0.0.0.0
  port: 8000
  api_keys: []                  # empty => open local use
```

## Development

```bash
pip install -e ".[full,dev]"
pytest -q                       # unit + full-stack e2e tests
ruff check backend/

cd web
npm ci
npm run dev                     # UI on :5173, proxying the API on :8000
npm run build                   # outputs web/dist, served by Colons automatically
```

## Project layout

```
backend/
  colons_core/
    providers/     base + native adapters (openai_compat, anthropic, google, ollama) + catalog
    agents/        the harness: agentic loop, persistent sessions, tasks, proactive work
    tools/         registry, permission manager, 21 built-ins
    memory/        SQLite/Postgres stores, cache, embeddings
    scheduler/     cron parser, schedule store, scheduler (continuity/monitor)
    bots/          roster, bot-to-bot messenger, peers, silence tokens
    rooms/         group rooms: store + serial-round orchestration
    messaging/     Telegram, Discord, Slack, webhooks + bridge
    diagnostics/   the doctor checks
    observability/ logging, ring buffer, request ids, process stats
    audio/         Edge TTS + pyttsx3
    avatars/       characters, pets, SVG generation
    config/        env + YAML configuration
  colons_api/      FastAPI app: REST, WebSocket, static UI, /v1 passthrough
  colons_cli/      the `colons` command + async client
web/               React + Tailwind SPA (builds to ~78 KB gzipped)
tests/             unit + full-stack e2e tests
```

## License

MIT

## Native browser

Install the optional browser runtime and its Chromium binary:

```bash
pip install -e ".[browser]"
playwright install chromium
# Linux servers may need: playwright install --with-deps chromium
```

Open **Dashboard → Browser**, or ask your bot to browse in chat. The engine reuses a
warm Chromium process across agents, with separate contexts for cookies and tabs.
Navigation waits for DOM content rather than every network request, and snapshots
return up to 80 visible controls plus bounded page text. Actions return fresh element
references; use the latest refs for clicking, filling, and pressing keys. Screenshots are also passed to vision-capable models as image inputs during the active
turn. Read pages
with `browser_open`, `browser_snapshot`, and `browser_tabs`.

`browser_click`, `browser_fill`, `browser_select`, and `browser_press` require an **Allow once** decision
in chat unless tool auto-approval is configured. Approvals are scoped to the current
WebSocket turn and expire after two minutes. Browser sessions close with their agent;
they do not use your personal browser profile. Screenshots are saved under
`<workspace>/.colons/browser/`. Set `COLONS_BROWSER_EXECUTABLE` to use an existing
Chromium executable. The optional Chromium integration tests use that variable too.

## Chat attachments

Use the paperclip next to the message field, or paste an image. Messages accept up to
three files: PNG/JPEG/WebP/GIF images (2 MB each) and UTF-8 text, code, or data files
(up to 100,000 characters). Remove files before sending with the chip's close button.
Images require a vision-capable model. Attachments persist with the conversation and
are passed to OpenAI-compatible, Anthropic, Gemini, and Ollama adapters in their native
formats. PDFs and office documents are not supported yet.
