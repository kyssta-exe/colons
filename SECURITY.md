# Security

Colons v0.0.2 is a beta intended for trusted, self-hosted use. It can execute commands and
change files with its server account's access. Permission defaults do not sandbox shell
commands. Shared API keys are not a full multi-tenant identity system.

Report vulnerabilities through the repository owner's GitHub profile/contact rather than
including exploit details or secrets in a public issue. For ordinary bugs, use the issue
template and remove private content from logs.

Run local deployments on loopback by default. Configure server authentication and HTTPS
before exposing the service. Protect backups and messaging configuration files, which
contain credentials. Review the effect of Full access before enabling unattended tools.

API and chat WebSocket requests from browsers must use the server's own origin or an
origin explicitly listed in `COLONS_CORS_ORIGINS`. Avoid a wildcard origin on an instance
with tool access. These checks complement authentication; command-line clients without
an Origin header still need an API key when authentication is configured.
