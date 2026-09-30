# Beta status

Colons v0.0.2 beta is usable for trusted self-hosted work. It is not yet a production
multi-user service. Passing CI verifies covered behavior, not every deployment or provider.

Current checks cover Python 3.10/3.12, file tool creation/editing/approvals, frontend builds,
native Chromium operations, desktop/mobile UI smoke checks, packaged UI installation,
and Docker image startup. Real provider support depends on the selected endpoint/model.

Before relying on it for unattended or public production workloads:

- Add durable task execution, restart recovery, cancellation, and retries. Tasks and usage
  totals currently reset on server restart.
- Define authenticated user identity and authorization across bots, rooms, memory, tools,
  and messaging. Shared server API keys are intended for a trusted instance.
- Persist web model changes. Web provider/model changes are runtime-only;
  `colons setup provider` and environment/config files provide restart persistence.
- Review tool isolation for your deployment. File tools enforce workspace boundaries;
  shell commands execute with the server account's access. Permission prompts are not
  an operating-system sandbox.
- Establish tested backups/restores and operational monitoring for your deployment.

Browser sessions are currently transient. Downloads, iframe targeting, and PDF/office
attachment extraction are future features. Terminal is a command console rather than
an interactive PTY. Agent and scheduler pause controls are separate.

Keep public deployments behind authentication and HTTPS. Browser-origin checks restrict
API/WebSocket access to the same origin or explicitly allowed UI origins; they do not
replace authentication. See [installation](installation.md) and [security](../SECURITY.md).
