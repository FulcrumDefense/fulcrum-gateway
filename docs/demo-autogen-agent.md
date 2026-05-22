# AutoGen Agent Demo Script

**Audience:** Non-technical staff
**Duration:** ~6 minutes
**Platform:** [https://paxai.app](https://paxai.app) (web UI) plus one terminal for the initial registration
**Template:** `autogen` (Gateway-managed AutoGen bridge, see PR #72 / `examples/gateway_autogen/autogen_bridge.py`)

---

## Before the Demo (presenter only, not shown)

```bash
# 1. Pull the autogen bridge branch (or main once PR #72 is merged)
cd ~/repositories/fd-ax-gateway
git checkout feat/autogen-template-bridge
uv pip install -e .

# 2. Install the optional autogen-agentchat + autogen-ext packages
uv pip install autogen-agentchat 'autogen-ext[openai]'

# 3. Confirm GROQ_API_KEY is set
echo "$GROQ_API_KEY" | head -c 4   # should print: gsk_

# 4. Register the AutoGen agent
ax gateway agents add --template autogen --name autogen-demo
ax gateway agents start autogen-demo
ax gateway agents show autogen-demo
# Presence should be IDLE, Reachability ready to claim work

# 5. Smoke test from terminal
AX_GATEWAY_AGENT_NAME=autogen-demo \
AX_MENTION_CONTENT="Reply in one short sentence, what is the speed of light in km/s?" \
.venv/bin/python examples/gateway_autogen/autogen_bridge.py
# Should reply with the speed of light and exit_reason=done

# 6. Send a UI smoke test to make sure the agent shows up in the workspace
ax send "@autogen-demo Reply briefly, are you live" --space ax-gateway
# Should reply within a few seconds
```

---

## The Demo

### Opening (1 min). The Problem

> "We already have agents that respond in chat. But the agent ecosystem
> is fragmented across frameworks. Some teams build on LangGraph, some
> on CrewAI, some on AutoGen. Today I'm going to show how Gateway runs
> any of these frameworks side by side, with the same control plane,
> the same activity feed, the same operator experience.
>
> The agent I'm demoing today is built on Microsoft's AutoGen framework,
> running through our Gateway. From the outside it looks like any other
> @-mention in chat."

---

### Step 1. The workspace (1 min)

Open [https://paxai.app](https://paxai.app) and navigate to the **ax-gateway** workspace.

Point out.

- The familiar message surface
- The `@autogen-demo` agent in the participant list, presence shown as IDLE

> "This agent was registered through Gateway with a single command.
> Operator runs it, agent shows up, ready to receive mentions. No
> tokens copied around, no per-framework infrastructure work."

---

### Step 2. First mention (1 min)

In the chat input, type.

```
@autogen-demo Reply in one short sentence, what are the three pillars
of zero trust architecture?
```

Press send. Watch the activity feed.

Expected sequence on screen.

1. **Processing** status appears under the agent's avatar
2. Activity line "building AutoGen AssistantAgent with Groq model client (model=llama-3.3-70b-versatile)"
3. **Processing** status "Calling Groq (llama-3.3-70b-versatile) via AutoGen"
4. Reply appears in chat (about 4 seconds)

> "Behind that mention, Gateway launched the AutoGen bridge as a managed
> subprocess. The bridge built an AutoGen AssistantAgent, wired it to a
> Groq model client, and ran one agent turn. The agent's reply came
> back through Gateway and rendered as an ordinary chat message.
>
> Notice the activity feed showed each step. That's the same Gateway
> lifecycle event format every template uses: LangGraph, CrewAI, the
> Hermes runner, AutoGen. Operators get one consistent view."

---

### Step 3. A more complex question (1 min)

In the chat input.

```
@autogen-demo Explain the difference between OAuth2 and SAML in three
sentences.
```

Watch the same lifecycle play out. Reply is longer this time, takes a
few seconds more.

> "This is where the model choice matters. We're running Llama 3.3 70B
> on Groq, so the response is fast even on a more substantive question.
> The same AutoGen bridge can be repointed at OpenAI, Anthropic, or any
> OpenAI-compatible endpoint by setting one environment variable. The
> operator doesn't have to rebuild anything."

---

### Step 4. Compare to a sibling template (1 min, optional)

If a LangGraph or Hermes agent is already registered in the workspace,
mention it with the same prompt.

```
@langgraph-bot Explain the difference between OAuth2 and SAML in three
sentences.
```

Compare the activity feed.

- Both agents stream the same lifecycle event shape
- Both reply in similar time
- Different bridges, same operator experience

> "This is the point. Whichever framework the agent author chose,
> Gateway gives the operator one place to register, monitor, supervise,
> and replace it. We don't have to pick a framework winner. We host all
> of them."

---

### Step 5. Closing (1 min)

Show `ax gateway status` in a terminal as the closer.

```
ax gateway status
```

Point out.

- Daemon running, healthy
- Multiple agents registered, all IDLE
- Connector count (if PR #53 has landed, mention the connector framework
  unifies tool access across these templates too)

> "Gateway is the trust boundary and the supervisor. The frameworks are
> the implementation detail. When a new framework drops next quarter,
> we add a template, ship a bridge, and operators see one more option
> in their Add Agent menu. No re-platforming, no per-framework auth
> dance, no second control plane."

---

## What this demo is NOT showing yet

Intentional scope cuts for V1 of the AutoGen template (per PR #72).

- **Multi-agent teams.** AutoGen's `RoundRobinGroupChat` and
  `SelectorGroupChat` would let multiple agents collaborate on a single
  task. Single-`AssistantAgent` is V1 by design, multi-agent is a
  follow-up.
- **Tool calls.** The current bridge uses `agent.on_messages()` (no
  tools). Adding tool support, including Composio-connector tools once
  PR #53 lands, is a planned follow-up.
- **Token-level streaming.** Activity events fire at the
  build / Groq-call / completed phases, not on every token. Same V1
  cadence as the LangGraph bridge before PR #38's review pass added
  streaming.

These cuts let V1 ship fast and give operators a working baseline today.
Each one is a clear next-step PR.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent shows `error` state in `agents show` | autogen-agentchat or autogen-ext not installed in the bridge environment | `uv pip install autogen-agentchat 'autogen-ext[openai]'` |
| Bridge replies with `AutoGen stub ack from ...` instead of a real answer | GROQ_API_KEY not set, or autogen-ext missing | Check `echo $GROQ_API_KEY` returns a value; if it does, reinstall autogen-ext |
| Bridge replies with `AutoGen bridge for ... finished without text` | Model returned empty content (rare, model-side glitch) | Send the prompt again, or switch model via `AX_BRIDGE_LLM_MODEL` |
| `exit_reason=crashed` with "Agent could not start..." | autogen-agentchat itself not installed | `uv pip install autogen-agentchat` |
| Long pause with no activity events | Groq rate limit or network stall | Check `ax gateway activity` for the agent, may need to wait 30s and retry |

---

## Related reading

- `examples/gateway_autogen/autogen_bridge.py`. Bridge source
- `examples/gateway_autogen/README.md`. Bridge reference doc with the
  full env-var table and lifecycle event shape
- `docs/demo-outbound-connectors.md`. Composio connectors demo for the
  tool-use side of the story
- `docs/gateway-demo-script.md`. Broader Gateway shape demo, audience
  is more technical
