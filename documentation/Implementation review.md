# Colons implementation review

The active product is `colons/`: a Python agent harness exposed through FastAPI and a
CLI, with a React/Vite workspace. The root-level backend and Next.js frontend are an
older prototype. Root documentation and Make targets now direct development to the
packaged implementation.

The product is an always-on, self-hosted assistant workspace. Named bots share services
but keep conversation identities, roles, and memories. Users work through chat, group
rooms, background tasks, recurring schedules, and messaging adapters.

## Improvements implemented

- Light chat workspace inspired by the supplied mockup: spacious messages, rounded
  composer, conversation rail, Lucide icons, and colon-mark avatars with color variants.
- Dashboard for setup, tasks, schedules, agent controls, memory search, native browser,
  and system diagnostics. Requests use the selected bot; failures are visible.
- Text and image attachments with previews, removal, validation, native provider
  translation, and SQLite history persistence. Existing session databases migrate
  automatically to add attachment storage.
- A native Playwright browser runtime: shared warm Chromium, isolated agent contexts,
  tab reuse, bounded snapshots, versioned element references, gated actions, and captures.
- Per-turn interactive tool approvals in chat, without mutating an agent's global
  approval callback. Approvals expire or cancel with the active turn.
- WebSocket generation runs alongside the receive loop, so Stop and ping work while
  providers are waiting. Disconnect cancels work, and switching chats ignores stale events.
- REST chat returns its generated session ID. Session-store access checks ownership
  before loading, deleting, or creating over another user's/agent's session.
- Task failures during planning or execution remain failed with errors and timestamps.
  Pausing blocks queued tasks and proactive reviews; handoff tools stay denied even with
  tool auto-approval enabled.
- Settings use a responsive navigation rail, labeled provider form with explicit apply,
  separate server credentials, accessible modal keyboard controls, and visible errors.
  Usage is scoped to the selected agent. Messaging has inline Telegram/Discord/Slack
  setup forms, official instructions, masked credentials, access limits, live per-service
  reconnect/disconnect, and durable settings in a private `data_dir/messaging.json` file.
  UI-saved service settings override that service's environment/config defaults on restart.
  The favicon uses vertically stacked dark dots on a white circle.
- Both workspace sidebars collapse. Desktop visibility is saved in this browser; smaller
  screens use drawers. The terminal dock runs explicit commands in the server workspace,
  supports a directory override and command history, and displays output and exit codes.
  It uses the existing shell tool through an authenticated, command-only endpoint. It is
  a command console, not a persistent PTY: each command starts a new shell, interactive
  programs are unsupported, and closing the panel does not cancel a running command.
- Colons identity is injected into the system context each turn using the current
  assistant name, configured model, and provider. Runtime model switches update those
  facts without rewriting stored chat history. Named bot roles retain the standard
  assistant guidance, plus their persona and role; custom system prompts keep an identity
  layer. Model aliases are reported as configured rather than guessing upstream versions.
- Settings → Permissions persists the server default (Auto, Approval, Full access),
  applies it to existing and newly created agents, and preserves workspace/disabled-tool
  restrictions. Auto follows per-tool rules; Approval gates every agent tool call; Full
  access skips approvals except user-reserved handoff tools. Background jobs have no
  interactive approval callback, so gated calls are declined. Direct terminal submission
  authorizes that single command in every mode. Tests create/edit/read real temporary files
  through the WebSocket tool loop and check that denied writes leave no file behind.
- Release is v0.0.1 beta (Python `0.0.1b1`, npm `0.0.1-beta.1`).
- Repeatable API/browser regression coverage and browser UI smoke scripts.

## Remaining product gaps

- Tasks and usage counters live in memory. Durable task execution needs a task store,
  restart recovery, explicit cancellation, and a retry policy that avoids duplicate effects.
- Provider changes from settings are runtime-only. Permanent configuration and secret
  storage need a deliberate save flow; switching also needs per-bot model compatibility checks.
- Browser sessions currently last for an agent's runtime. Restart persistence, download
  handling and iframe targeting are future work. Screenshot results are forwarded to
  vision-capable models during the active turn.
- Attachments currently support images and text/code/data files. PDF and office-document
  extraction are not implemented.
- Session ownership checks do not constitute a full multi-tenant authentication design.
  The shared API-key model, bot roster, rooms, peer messaging, and agent registry need a
  consistent authenticated identity model before claiming user isolation across the product.
- Scheduler pause controls are separate from agent background-worker pause. A unified
  stop/resume policy for all channels still needs definition.
- App integrations are messaging adapters rather than a general connector/plugin system.
  Browser tools and filesystem tools cover some workflows, but dedicated app integrations
  remain separate work.

## Verification

Run the Python suite from `colons/` using the installed development environment. Set
`COLONS_BROWSER_EXECUTABLE` to run Chromium integration tests with an existing executable.
Build the frontend with `npm run build` from `colons/web/`.

For UI checks, start Colons and run:

```bash
python tests/web_smoke.py --url http://localhost:8000
```

The smoke script uses real dashboard/browser APIs and scripts chat events in the browser
for attachment, cancellation, and approval interactions. It saves screenshots to a
new temporary directory unless `--output` is supplied. It checks desktop and mobile
navigation, native browser capture, attachment payloads, stream switching, approval
buttons, page errors, and horizontal overflow. Run it against a development instance
without server authentication configured.

Settings, sidebar persistence, responsive drawers, and a harmless real shell command can
also be checked against a development instance with:

```bash
python tests/web_workspace_smoke.py --url http://localhost:8000
```

The provider failure in this check is scripted to verify that selecting a provider does
not mutate settings and applying a failed configuration shows an error. Shell execution
uses the real server endpoint. API tests separately verify authentication and agent scope.
