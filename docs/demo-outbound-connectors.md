# Outbound Connectors Demo Script

**Audience:** Non-technical staff  
**Duration:** ~8 minutes  
**Platform:** [https://paxai.app](https://paxai.app) (web UI only — no terminal shown)  
**Status:** Shipped on `main` (Composio v3 adapter, Hermes connector tools, optional `langgraph_composio` template)

---

## Before the Demo (presenter only — not shown)

```bash
# 1. Install from main
cd ~/repositories/fd-ax-gateway
git checkout main && git pull
uv pip install -e .

# 2. One-time connector setup (Composio v3 API)
ax gateway connectors add demo --provider composio --managed-auth
ax gateway connectors auth write demo COMPOSIO_API_KEY=<your key>
ax gateway connectors set demo entity_id "<your entity id>"

# 3. Verify connected apps
ax gateway connectors apps demo
# Should show: gmail  status=ACTIVE, slack  status=ACTIVE, etc.

# 4. Connect apps if needed
ax gateway connectors connect demo --app gmail
ax gateway connectors connect demo --app slack

# 5. Hermes agent (recommended for web UI — natural-language search + execute)
ax gateway agents show sarob-bot
# Presence should be IDLE, Reachability "Live listener ready to claim work."

# Optional: LangGraph + Composio demo agent (search + RUN:<TOOL> execute)
# ax gateway agents add composio-demo --template langgraph_composio --connector-ref demo

# 6. Clear stale session history so the agent starts fresh (Hermes)
rm ~/.ax/gateway/agents/sarob-bot/hermes-home/sessions/session_*.json

# 7. Quick smoke test
ax send "@sarob-bot list the connected apps on the demo connector" --space ax-gateway
```

See also: `docs/composio-integration.md`, `skills/gateway-composio-connectors/SKILL.md`

---

## The Demo

### Opening (1 min) — The Problem

> "Our agents can talk to each other. But what if one needs to send an
> email, file a Jira ticket, or post to a Slack channel?
>
> Today I'm going to show you how our agents reach into the real world —
> Gmail, Slack, GitHub, 500+ services — just by asking in plain English."

---

### Step 1 — The workspace (1 min)

Open **[https://paxai.app](https://paxai.app)** and navigate to the **ax-gateway** workspace.

Point out on screen:

- The **ax-gateway** workspace name
- The agent list showing **@sarob-bot** with a green/IDLE status
- The message thread area

> "This is our workspace. Think of it like a Slack workspace, but for
> agents. Every agent, every message, every task lives here."
>
> "See @sarob-bot? That's an AI agent — always on, always listening,
> ready to pick up work. Let's talk to it."

---

### Step 2 — Ask the agent what it can reach (1 min)

In the paxai.app message composer, type and send:

```
@sarob-bot What apps are connected to the demo connector?
```

Wait for the reply. The agent will list the connected services.

**What they see:**

```
Slack    ACTIVE
Gmail    ACTIVE
```

> "The agent knows what services it can reach. Right now it has
> Gmail and Slack. Let's put them to work."

---

### Step 3 — Send a live email (2 min) ★ The wow moment

In paxai.app, type and send:

```
@sarob-bot send an email to sean@fulcrumdefense.ai with subject "Hello from paxai" and body "This email was sent by an aX agent through the web UI." Use connector=demo.
```

Wait for the reply — the agent will confirm the email was sent.

*(Open the recipient's inbox on screen to show the email arrived.)*

> "We just typed a sentence and the agent sent a real email. No one
> opened Gmail. No one looked up an API. The agent figured out the
> right tool, called it through the gateway, and delivered the email."
>
> "The agent handles the conversation. The connector handles the action.
> The gateway manages the credentials. The user just typed a sentence."

---

### Step 4 — Post to Slack (1 min) ★ Multiple services

In paxai.app, type and send:

```
@sarob-bot search for Slack tools that can post a message. Use connector=demo.
```

Wait for the reply showing available Slack tools.

> "Same pattern — describe what you need in plain English and the
> agent finds the right tool. This works for any connected service."

---

### Step 5 — Ask about adding new services (1 min)

In paxai.app, type and send:

```
@sarob-bot How do I add GitHub to the demo connector?
```

**What they see:**

```
ax gateway connectors connect demo --app github
```

> "The agent knows how to guide you. One command to connect a new
> service — GitHub, Jira, Notion, Google Calendar, 500+ options.
> No code changes, no redeployment. Connect it and the agent can
> use it immediately."

---

### Step 6 — Show the audit trail (30 sec)

Scroll up in the paxai.app thread to show:

- The original request ("send an email to ...")
- The agent's reply confirming the action
- Timestamps and agent identity

> "Everything is logged. Who asked, which agent acted, what it did,
> when it happened. Full audit trail."

---

### Closing (1 min) — The Full Picture

> "Let's put it all together:
>
> 1. **You type a sentence** — 'send an email,' 'post to Slack,'
>   'file a Jira ticket.'
> 2. **The agent figures out how** — searches for the right tool,
>   calls the right service, handles the details.
> 3. **The gateway manages security** — credentials, access control,
>   audit trail. No passwords floating around.
> 4. **500+ services available** — Gmail, Slack, GitHub, Jira, and
>   more. Connect a new one with a single command.
>
> This is agents that work for your whole team, not just the engineers.
> Anyone can open this web UI, type a message, and get real work done."

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Connector not found` | `ax gateway connectors list` to check the name |
| `COMPOSIO_API_KEY not found` | Re-run `auth write` with the key |
| `No connected account found` | `ax gateway connectors connect demo --app <app>` |
| `HTTP 404` / `HTTP 410` on tool call | Ensure v3 base URL; use `tools search` for the correct slug |
| Agent guessing wrong commands | Clear Hermes sessions under `~/.ax/gateway/agents/<name>/hermes-home/sessions/` |
| Agent not responding | `ax gateway agents show <name>` — check Presence is IDLE |
| Reply shows `(no output)` | Restart gateway after bridge updates; exec handler drains stdout before close (issue #104) |
| Check unread replies | `ax messages list --unread --space ax-gateway` |
