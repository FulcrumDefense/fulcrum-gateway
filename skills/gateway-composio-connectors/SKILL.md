# Gateway Composio Connectors

Use this skill when a user or agent needs to set up, configure, or use outbound tool connectors through Gateway.

## When to use

- Setting up a new connector for third-party tool access (GitHub, Jira, Slack, etc.)
- Configuring tool policy (allow/deny lists) for a connector
- Writing or managing auth credentials for a connector
- Searching for or executing tools through a connector
- Setting up an HTTP MCP connector for self-hosted servers
- Choosing between Hermes agents vs the `langgraph_composio` Gateway template

## Setup flow

1. **Create** the connector:
   ```bash
   ax gateway connectors add <name> --provider composio --managed-auth
   ```

2. **Write auth** credentials:
   ```bash
   ax gateway connectors auth write <name> COMPOSIO_API_KEY=<key>
   ```

3. **Configure** (optional):
   ```bash
   ax gateway connectors set <name> entity_id <entity>
   ax gateway connectors set <name> allowed_tools '["GITHUB_*"]'
   ```

4. **Discover** tools:
   ```bash
   ax gateway connectors tools search <name> --use-case "list pull requests"
   ax gateway connectors tools list <name>
   ```

5. **Execute** a tool (CLI):
   ```bash
   ax gateway connectors call <name> --tool GITHUB_LIST_PRS --args-json '{}'
   ```

## Which agent runtime to use

| Goal | Use |
| --- | --- |
| Natural-language search **and** execute from aX chat (web UI) | **Hermes** agent with `connector_search` / `connector_call` / `connector_apps` tools |
| Gateway-managed demo: search per mention + explicit tool run | **`langgraph_composio`** template + `--connector-ref <name>` |
| Operator-only, no agent | CLI (`connectors tools search`, `connectors call`) |

**Hermes (recommended for operators in paxai.app):** Register a Hermes plugin agent. Mention it with tasks like “search Gmail tools” or “send email using connector demo”. The agent uses Gateway-brokered credentials; never put `COMPOSIO_API_KEY` in agent config.

**LangGraph + Composio:** For a controlled demo or integration test:
```bash
ax gateway agents add composio-bot --template langgraph_composio --connector-ref <connector-name>
```
The bridge searches tools on each mention. To execute, append `RUN:<TOOL_SLUG> {"arg": "value"}` to the message (see `examples/gateway_langgraph_composio/README.md`). For everyday chat execute, prefer Hermes.

## Providers

- **composio** — Composio SaaS (500+ integrations). Requires `COMPOSIO_API_KEY`. API base: `https://backend.composio.dev/api/v3`.
- **http_mcp** — Any MCP-compliant server. Requires `base_url` in config.

## Key principles

- Gateway is the trust boundary. Credentials stay in managed auth files (0o600), never in config or logs.
- Tool policy (`allowed_tools`, `denied_tools`) should be set for production connectors.
- All tool executions are logged to `activity.jsonl` for audit.
- Use `--json` flag on any command for machine-readable output.
- Exec bridges must `flush=True` on the final stdout reply so Gateway does not record `(no output)` (issue #104).

## Reference

- `docs/composio-integration.md` — configuration keys, security checklist, troubleshooting
- `docs/demo-outbound-connectors.md` — non-technical demo script for paxai.app
