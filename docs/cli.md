# Colons commands

Install with `./scripts/install.sh`. It exposes `colons` in `~/.local/bin`; ensure that
directory is on your PATH. A pip/pipx installation also provides the command directly.

```bash
colons setup          # Guided configuration menu
colons                # Full-screen terminal interface
colons start          # Run the server in the foreground
colons web            # Run the server and open the web UI
colons --version
```

The terminal and web interfaces use the same server, conversations, agent roster, messaging,
and permission settings. The terminal connects to an existing local server when available.
Otherwise it starts a local server and stops that process when you quit. It never shuts down
a server you started separately. `colons web` follows the same ownership rule: it reuses an
existing server, or keeps its own server running until Ctrl+C. `colons start` stays in the
foreground; it is not a background system service.

## Terminal interface

Choose an agent and reopen conversations in the sidebar. Enter sends a message; the
paper-plane button becomes a stop button during a reply. Tool approvals show their
arguments and require an explicit Allow once or Deny choice. Esc cancels an active reply
or declines an approval. The server's configured permission mode remains in effect.

| Shortcut | Action |
| --- | --- |
| Ctrl+N | New chat |
| Ctrl+B | Toggle sidebar |
| F2 | Guided setup (restart the server to apply changes) |
| F3 | Open the web UI |
| Esc | Stop reply / deny approval |
| Ctrl+Q | Quit |

`colons chat` keeps the plain terminal chat interface. For scripts, use
`colons run "your message"` against a running server. The full-screen interface requires
an interactive terminal.

## Guided setup

`colons setup` presents Provider & model, Messaging, Permissions & workspace, Server,
and Voice sections. You can also go straight to a section:

```bash
colons setup provider
colons setup messaging
colons setup permissions
```

Prompts run one by one. Provider keys, bot tokens, signing secrets, and server keys use
hidden input; Enter keeps an existing saved secret. Provider setup supports catalog names
and custom OpenAI-compatible endpoints (`custom`). Messaging includes Telegram, Discord,
and Slack instructions and optional allowed user/channel IDs.

Configuration is written atomically with owner-only permissions. Existing sections are
preserved. Messaging and permission modes use the same private settings files as the
web interface. The wizard edits persisted settings; restart a running server to apply
them. Environment variables override config values.

## Paths and connection options

Configuration discovery prefers `COLONS_CONFIG`, a local `colons.yaml`/`colons.json`,
then an existing user configuration. A new installation uses
`~/.config/colons/config.json`, with data and workspace under `~/.local/share/colons`.
Existing source-checkout data is retained when creating the first profile.

```bash
colons --config /path/to/config.json setup
colons start --port 8137
colons web --port 8137 --no-open
colons --url https://your-server.example --api-key SERVER_KEY tui --no-start
```

Place global connection/configuration options before the subcommand. `COLONS_URL` and
`COLONS_CLIENT_API_KEY` provide client defaults. A local client can use a key from the
server's saved configuration. The provider credential `COLONS_API_KEY` is separate and
is never reused as a client authentication key.

`colons serve` remains an alias for `colons start`. The source `scripts/run.sh` remains
available and loads the checkout's trusted `colons/.env` before starting the same CLI.
The CLI itself reads config/environment; it does not execute `.env` files as shell code.
