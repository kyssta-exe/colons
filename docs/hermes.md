# Hermes adapter

Connect [Hermes Agent](https://github.com/NousResearch/hermes-agent) to a running Colons
instance through its native [MCP support](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/).
Hermes can discover and create Colons bots, delegate background work, check results,
continue chats, and participate in existing collaboration rooms. The adapter speaks
stdio MCP using the official Python SDK; it does not need changes to Hermes itself.

## Setup

From the repository root:

```bash
./scripts/install.sh --hermes
./scripts/run.sh
```

For an already installed Colons instance, installing just the adapter dependency is enough:

```bash
colons/.venv/bin/python -m pip install -e './colons[hermes]'
```

Merge this server entry into `~/.hermes/config.yaml`, replacing the command with the
absolute path to your checkout's executable. Preserve your existing servers/settings.

```yaml
mcp_servers:
  colons:
    command: /absolute/path/to/colons/colons/.venv/bin/colons-hermes
    timeout: 150
    env:
      COLONS_ADAPTER_URL: http://127.0.0.1:8000
      COLONS_ADAPTER_USER_ID: default
      COLONS_ADAPTER_TIMEOUT: "120"
      # If the Colons server requires authentication, supply one of its API keys:
      # COLONS_ADAPTER_API_KEY: your-server-api-key
```

For a custom virtual environment, use its `bin/colons-hermes` executable instead.
The adapter URL must be reachable from the machine running Hermes. Hermes filters
subprocess environment variables, so configure the adapter variables explicitly in
the server's `env` block. Store real keys in your private Hermes configuration;
never add them to this repository. Start a new Hermes chat after changing configuration.

The adapter's key is a **Colons server key** (`COLONS_API_KEYS`), not your LLM provider key
(`COLONS_API_KEY`). Use HTTPS for remote servers and the same trusted-instance security
model as the rest of Colons.

## Delegate and collaborate

Ask Hermes:

> Find my Colons agents. Assign the researcher a comparison of three options. Keep
> working while it runs, then check its result and summarize it.

The tool sequence is `list_colons` → `assign_work` → `get_work`. Assignment returns a
task ID and agent ID immediately; Colons starts execution in its own worker. Hermes
must use that **same agent ID** when listing or polling the task. Poll occasionally,
not in a tight loop. A pending/running task is not a completed result.

| Tool | Purpose |
| --- | --- |
| `list_colons` | Discover named bots and active agents with their agent IDs |
| `create_colon` | Create a named bot owned by the configured user |
| `assign_work` | Queue work on a selected agent, or the user's default agent |
| `get_work` | Read a task's status, plan, result, and error |
| `list_work` | List an agent's tasks, optionally filtered by status |
| `chat_with_colon` | Talk to an agent; reuse the returned session ID for follow-ups |
| `list_rooms` | Discover rooms owned by the configured user |
| `read_room` | Read collaboration history |
| `send_to_room` | Post as Hermes and collect participating agents' replies |

Use `assign_work` for long jobs. Chat and room calls wait for replies and can reach the
configured timeout. A timeout does not prove the server rejected a request: check
`list_work` before resubmitting an assignment to avoid duplicate work.

## Permissions and limits

Colons keeps its configured permission mode. The adapter does not expose direct shell
execution, credential management, or permission-setting tools. Creating chats and tasks
can still cause agents to call tools under that policy.

This first adapter does not forward interactive tool approvals to Hermes. A tool that
requires approval is denied when no approval callback is available. Do not assume a
natural-language reply means every requested operation succeeded. Review results and
denials; use the Colons chat UI when interactive approval is needed. The adapter never
switches an instance to Full access automatically.

Tasks currently live in server memory and disappear on restart. There is no automatic
push of completed work into an idle Hermes chat, task cancellation tool, or reverse
Hermes invocation from Colons in this version. Hermes retrieves updates with `get_work`.
See [beta status](status.md) for deployment limitations.

## Troubleshooting

- **Tools do not appear:** verify the executable path, install the `hermes` extra, and
  start a new Hermes chat. The MCP subprocess needs no working-directory configuration.
- **Cannot reach Colons:** start the Colons server and verify the adapter URL/port.
- **HTTP 401:** supply a valid `COLONS_ADAPTER_API_KEY`.
- **HTTP 404 for a task:** check its agent ID; a server restart also clears task history.
- **HTTP 429:** wait before polling again.

Run the bridge only through an MCP client. Its stdout is reserved for protocol messages.
