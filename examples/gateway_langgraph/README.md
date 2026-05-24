# Gateway-managed LangGraph template

A one-shot bridge that routes an inbound aX mention through a
LangGraph `StateGraph` and prints the reply on stdout. Designed for
`ax gateway agents add --template langgraph`.

The bridge runs in three tiers, picked at runtime by what is
installed and configured. The same Gateway lifecycle events fire on
every tier, so an operator's activity feed looks consistent whether
the bridge is wired to a real LLM or a stub.

## Execution tiers

1. **Real LLM path.** If `langgraph` and `groq` are both importable
   and `GROQ_API_KEY` is set, the bridge builds a StateGraph powered
   by a Groq chat completion. Sprint 04 task #976 extended this tier
   to a **two-node agentic loop** when `langchain_core` + `langchain_groq`
   are also installed and tool dispatch is enabled (default):
   `llm_call ↔ tool_node` with a conditional edge for the cycle.
   The agent gets three default tools (`echo`, `read_file`, `list_dir`)
   wrapped by a `wrap_tool_call` security middleware ported from
   `ax_cli/runtimes/hermes/tools/`. When tool dispatch is disabled
   (`AX_BRIDGE_TOOLS_DISABLED=1`) or `langchain_groq` is missing,
   the bridge falls back to Avrohom's original single-node streaming
   behavior from PR #38. Either way it emits throttled `activity` events
   (~1s heartbeat with a rolling preview) so the feed stays live.

2. **Stub graph path.** If `langgraph` is importable but Groq is not
   configured (no `GROQ_API_KEY`, or the SDK is not installed), the
   bridge builds a one-node StateGraph wired to a synthetic ack node
   that does not call any LLM. This proves the LangGraph wiring end-to-end
   without requiring credentials, useful in CI and local development.
   The stub tier does NOT exercise the multi-node loop or security
   wrapper — those run only when there is a real LLM to drive them.

3. **String fallback path.** If `langgraph` itself is not installed,
   the bridge returns a plain string template. The same lifecycle
   events still fire, so a Gateway operator sees the round trip even
   in the most stripped-down environment.

## Multi-node agent loop (Sprint 04 #976)

The bridge ships three small tools by default so a real-LLM-driven
agent has something to call. The tools are:

| Tool | Purpose | Security check |
|---|---|---|
| `echo` | Echoes its `message` argument. No filesystem touch. Useful for verifying the cycle wires correctly. | None (default-allow) |
| `read_file` | Reads a file as text (truncated to ~64KB). | `_check_read_path` rejects `~/.ssh/`, `~/.aws/`, `~/.codex/`, `~/.ax/`, `.env`, and `/credentials*` paths |
| `list_dir` | Lists the entries in a directory, sorted. | Same `_check_read_path` rules as `read_file` |

The MCP-to-LangChain tool adapter (a Sprint 05 follow-up, see PM
artifact #185 "MCP Tools design doc") will register additional tools
into this same `ToolNode` and inherit the security wrapper transparently.

### Security wrapper

The `wrap_tool_call` argument on `ToolNode` is LangGraph's first-class
interception point (see PM artifact #183 "LangGraph survey" §6.3 for
the architectural argument). The wrapper dispatches on the LLM-supplied
tool name and rejects calls that violate path or command policy before
the underlying tool function runs. The pure-Python check functions
(`_check_read_path`, `_check_write_path`, `_check_bash_command`,
`BLOCKED_READ_PATTERNS`) are imported verbatim from
`ax_cli.runtimes.hermes.tools` — no duplication.

Unknown tool names default-allow (so the bridge can grow new tools
without strict pre-registration). Flip to deny-on-unknown by setting
`AX_BRIDGE_STRICT_SECURITY=1`.

### Cycle limit

The agent loop is bounded by `AX_BRIDGE_MAX_ITERATIONS` (default 30,
clamped to [1, 200]). Half of Hermes's `max_iterations=60` default
because LangGraph's per-cycle cost includes both the LLM call AND
the tool dispatch round-trip; demos should ship with a conservative
ceiling that the operator can dial up.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | (unset) | Routes the bridge onto the real LLM path. Without it, the bridge falls back to the stub graph. |
| `AX_BRIDGE_LLM_MODEL` | `llama-3.3-70b-versatile` | Overrides the Groq model. Useful for swapping to a smaller/cheaper model or in response to a Groq deprecation. |
| `AX_BRIDGE_SYSTEM_PROMPT` | `Reply concisely.` | Replaces the trailing instruction on the system prompt. The leading agent-name framing (`You are @<agent>, ...`) is always emitted automatically. |
| `AX_BRIDGE_MAX_ITERATIONS` | `30` | Cycle limit on the multi-node agent loop. Clamped to `[1, 200]`. Each iteration is one LLM call (and possibly one tool dispatch). Conservative default; demos should dial up explicitly if needed. **Sprint 04 #976.** |
| `AX_BRIDGE_TOOLS_DISABLED` | (unset) | Set to `1`/`true`/`yes`/`on` to disable tool dispatch entirely and fall back to Avrohom's original single-node streaming. Operator escape hatch when the agent loop misbehaves. **Sprint 04 #976.** |
| `AX_BRIDGE_STRICT_SECURITY` | (unset) | Set to `1`/`true`/`yes`/`on` to flip the security wrapper from default-allow to default-deny on unrecognized tool names. Useful for production-leaning deployments; demos default to lax for iteration speed. **Sprint 04 #976.** |
| `AX_GATEWAY_WORKDIR` | (auto-resolved) | Agent's working directory for write-path checks in the security wrapper. Falls back to `os.getcwd()`, then `/tmp/<agent-name>`. **Sprint 04 #976.** |
| `AX_GATEWAY_AGENT_NAME` | `langgraph-bot` | Name the bridge replies as. Falls back to `AX_AGENT_NAME` then to the default. |
| `AX_MENTION_CONTENT` | (unset) | Prompt source. Also accepted as positional argv or on stdin. |

## Register with Gateway

```bash
ax gateway agents add --template langgraph --name my-langgraph-bot
```

The template advertises the bridge at
`examples/gateway_langgraph/langgraph_bridge.py` and runs through the
shared `exec` runtime adapter (same precedent as the Ollama template).

## Local validation

Run the bridge directly against an inbound prompt to verify wiring:

```bash
cd ~/path/to/ax-gateway
set -a; source ../.env; set +a   # GROQ_API_KEY in there for the real LLM path
AX_GATEWAY_AGENT_NAME=langgraph-bot \
AX_MENTION_CONTENT="Reply in one short sentence, what is the speed of light in km/s?" \
.venv/bin/python examples/gateway_langgraph/langgraph_bridge.py
```

Without `GROQ_API_KEY`, the bridge takes the stub graph path and
echoes the prompt through the synthetic ack node, still emitting the
full lifecycle event sequence.

## Lifecycle events

The bridge prints `AX_GATEWAY_EVENT <json>` lines to stdout. Gateway
parses them and routes them to the activity feed.

| Status | When | Detail keys |
|---|---|---|
| `processing` (start) | Bridge begins routing the prompt. | `message` |
| `processing` (LLM call) | `Calling Groq (<model>)` fires before the stream is opened (single-node tier) OR `Iteration N/MAX: calling Groq (<model>)` (multi-node tier). | `message` |
| `processing` (first token) | `Groq is responding (<model>)` fires when the first streamed chunk arrives (single-node tier only). | `message` |
| `processing` (tool call) | `Tool call(s) requested: <names>` fires when the LLM returns a tool call (multi-node tier). | `message` |
| `processing` (tool rejected) | `Tool '<name>' rejected by security wrapper` fires when `wrap_tool_call` denies a request (multi-node tier). | `message` |
| `activity` (heartbeat) | Throttled ~1s preview of accumulated text during streaming (single-node tier). | `activity` |
| `activity` (graph build) | `building two-node StateGraph (...)` or `building one-node StateGraph (...)` describes which tier was selected. | `activity` |
| `activity` (cycle limit) | `cycle limit reached (<N>); ending agent loop` fires when `AX_BRIDGE_MAX_ITERATIONS` is hit (multi-node tier). | `activity` |
| `activity` (loop completed) | `agent loop completed after <N> iteration(s)` fires when the multi-node tier reaches `END` cleanly. | `activity` |
| `completed` | Final status. `used_llm` reports which path ran. `stub` is kept for back-compat with the pre-LLM-validation schema. | `duration_ms`, `used_llm`, `stub` |
| `error` | Uncaught exception. | `error_message` |

## Follow-ups

The intentional scope for the initial cut. Items here are explicit
follow-ups, not gaps.

- ~~Multi-node graphs with branching/conditional edges.~~ **Done in Sprint 04 #976.**
- ~~Tool-call telemetry mapped to Gateway tool bubbles.~~ **Done in Sprint 04 #976** (`Tool call(s) requested:` events).
- Forwarding LangGraph's own streaming events (per-node state
  transitions) as `activity` events, not just the LLM token stream.
  Sprint 04 #976 forwards tool-call events but not the deeper
  per-iteration state-transition stream (`graph.stream()` API).
- Provider abstraction so the LLM tier is not Groq-specific. The
  multi-node tier uses `langchain_groq.ChatGroq`; mapping to other
  providers (Gemini, Claude, OpenAI, etc.) is a Sprint 05 / Sprint 06
  follow-up that hooks into LangChain's `init_chat_model` provider
  registry.
- MCP-to-LangChain tool adapter so the `report_gen` + `svg_viz` MCPs
  from PM artifact #185 plug into the existing `ToolNode` and inherit
  the `wrap_tool_call` security middleware. Tracked as a Sprint 05
  follow-up; when it lands, `_default_tools()` will grow to include
  the MCP-backed tools alongside the existing local ones.
