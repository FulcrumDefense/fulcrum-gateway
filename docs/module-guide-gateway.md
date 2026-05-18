# Module Guide: gateway.py

> **Last verified:** 2026-05-10 against `ax_cli/gateway.py` (6403 lines)
> and `ax_cli/commands/gateway.py` (8854 lines)

Gateway is split across two files. `ax_cli/gateway.py` owns state, lifecycle,
and the daemon loop. `ax_cli/commands/gateway.py` owns the CLI commands, HTTP
server, UI rendering, and the proxy dispatcher.

---

## ax_cli/gateway.py — State and Lifecycle

| Lines | Section | Key Functions | State Read/Written |
| --- | --- | --- | --- |
| 1–100 | Imports, constants, type aliases | — | — |
| 1339–1407 | **Session tokens** | `local_secret_path()`, `load_local_secret()`, `issue_local_session()`, `verify_local_session_token()` | `~/.ax/gateway/local_secret.bin` |
| 1565–1686 | **Space resolution and cache** | `_space_cache_rows()`, `_space_name_from_cache()`, `apply_entry_current_space()`, `_fallback_allowed_spaces()` | `allowed_spaces` in registry entries, `spaces.cache.json` |
| 1854–1865 | **Space normalization** | `_normalize_allowed_spaces_payload()`, `_fetch_allowed_spaces_for_entry()` | API upstream, entry cache |
| 2004–2084 | **Identity-space binding** | `evaluate_identity_space_binding()` | `registry.json`, `session.json` |
| 3021–3069 | **Pending queue (inbox)** | `agent_pending_queue_path()`, `load_agent_pending_messages()`, `save_agent_pending_messages()`, `append_agent_pending_message()`, `remove_agent_pending_message()` | `~/.ax/gateway/agents/{name}/pending.json` |
| 4413–5917 | **ManagedAgentRuntime** | Worker/listener pair, message intake, runtime dispatch | In-memory queue, pending files |
| 5917–6403 | **GatewayDaemon (reconcile loop)** | `_reconcile_runtime()`, `_reconcile_registry()`, `_sweep_lifecycle()`, `run()` | `registry.json`, `session.json`, upstream API |

### Session tokens

`issue_local_session()` creates signed tokens (`axgw_s_<payload>.<signature>`)
for local agent sessions. Tokens are HMAC-SHA256 signed with a secret stored at
`~/.ax/gateway/local_secret.bin`. `verify_local_session_token()` validates the signature
and decodes the payload. Tokens are short-lived and per-connect — they are not
cached or reused across sessions.

### Space resolution cascade

1. Per-agent `allowed_spaces` cache (in-memory, in the registry entry)
2. Global disk cache (`spaces.cache.json`)
3. Upstream `list_spaces` API call

`_space_name_from_cache(allowed_spaces, space_id)` does the per-agent lookup.
`space_name_from_cache(space_id)` (line 2980) does the global disk cache
lookup. `_fallback_allowed_spaces()` synthesizes per-agent rows from
entry/session fields when the per-agent cache is empty.

Common failure: if the upstream API returns a space record where `name` is a
UUID string, the per-agent cache stores that UUID as the "name". This causes
the operator UI and `agents show` to display a UUID instead of a readable
name.

### Reconcile loop

`GatewayDaemon.run()` is the main loop (line 6334). Each cycle:

1. Loads `registry.json` and `session.json`
2. Calls `_reconcile_registry()` which iterates all entries
3. For each entry, calls `_reconcile_runtime()` — compares desired state to
   effective state, starts/stops/restarts as needed
4. Runs `_sweep_lifecycle()` — hides stale agents, signals upstream liveness
5. Saves updated state
6. Sleeps ~10 seconds

### Pending queue

Each managed agent has a local pending queue at
`~/.ax/gateway/agents/{name}/pending.json`. The queue stores messages that
have been received but not yet acknowledged by the agent runtime. `mark_read`
clears messages from this queue.

---

## ax_cli/commands/gateway.py — CLI, HTTP, and Proxy

| Lines | Section | Key Functions |
| --- | --- | --- |
| 425–657 | **Local session connect** | `_connect_local_pass_through_agent()` |
| 658–781 | **Local session send/tasks** | `_send_local_session_message()`, `_create_local_session_task()` |
| 782–806 | **Proxy allowlist** | `_LOCAL_PROXY_METHODS` dict |
| 807–859 | **Proxy dispatcher** | `_proxy_local_session_call()` |
| 860–904 | **Local inbox** | `_local_session_inbox()` |
| 4190–5598 | **Operator UI HTML** | `_render_gateway_ui_page()` — CSS, layout, JavaScript |
| 5599–5625 | **Demo page and favicon** | `_render_gateway_demo_page()`, `_GATEWAY_FAVICON_SVG` |
| 5626–5696 | **HTTP server setup** | `_GatewayUiServer`, `_write_json_response()`, `_read_json_request()` |
| 5697–5900 | **HTTP routes** | `do_GET()`, `do_POST()` — all `/api/` and `/local/` endpoints |

### Proxy allowlist

`_LOCAL_PROXY_METHODS` (line 782) is a dict with per-method tier annotations
controlling which `AxClient` methods an agent session can call through
`/local/proxy`. Current entries:

```
whoami, list_spaces, list_agents, list_agents_availability,
list_context, get_context, list_messages, get_message,
search_messages, list_tasks, get_task, update_task,
upload_file (admin tier, workdir-sandboxed)
```

Methods are annotated with `tier: "use"` or `tier: "admin"`. Write operations
like `send_message` and `create_task` go through dedicated endpoints
(`/local/send`, `/local/tasks`) with additional validation.

### HTTP routes (local Gateway server, 127.0.0.1:8765)

**GET endpoints:**
- `/` — redirect to operator page
- `/operator` — full operator dashboard
- `/demo` — demo page
- `/healthz` — health check
- `/api/status` — runtime status and metrics
- `/local/inbox` — local session messages
- `/local/sessions` — list all local sessions
- `/api/runtime-types` — available runtime templates
- `/api/templates` — agent template definitions
- `/api/approvals` — list pending approvals

**POST endpoints:**
- `/api/agents` — register managed agent
- `/local/connect` — connect local pass-through agent
- `/local/send` — send session message
- `/local/tasks` — create task
- `/local/proxy` — proxy arbitrary allowlisted call
- `/api/agents/{name}/start` — start agent
- `/api/agents/{name}/stop` — stop agent
- `/api/agents/{name}/attach` — attach session
