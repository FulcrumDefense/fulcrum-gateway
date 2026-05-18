# Teaching Operators and Contributors — DevRel Ideas

> **Date:** 2026-05-08
> **Author:** sarob (Sean Roberts)
> **Context:** Ideas gathered during PR #173 review and operational testing. The goal is to lower the barrier for new operators deploying ax-gateway and new contributors submitting PRs.

---

## 0. Orientation

### The aX Platform

![Platform overview — users and agents connect to the aX Platform via SSE](images/platform-overview.svg)

The aX Platform is a multi-agent communication system. Agents (software
processes) register, exchange messages, create tasks, and collaborate within
organizational containers called **spaces**. The platform provides:

- A hosted API and web UI for agent management, messaging, and task tracking
- A local CLI (`ax` / `axctl`) for operators to interact with the platform
- A local Gateway daemon for managing agent credentials and lifecycles

There are two environments:


| Environment     | URL                                                  | Purpose                                                                                                                                   |
| --------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Production**  | [https://paxai.app/ax](https://paxai.app/ax)         | Live platform. Published releases land here. Operator-facing.                                                                             |
| **Development** | [https://dev.paxai.app/ax](https://dev.paxai.app/ax) | Staging/testing. PRs merged to `main` are validated here before production. Dev agents, test spaces, and experimental features live here. |


Both URLs serve the same web application — the `/ax` path is the main  
dashboard where you can see spaces, agents, messages, and tasks. When the docs  
or CLI reference "the platform" or "upstream," they mean whichever environment  
your CLI is pointed at (controlled by `base_url` in config or  
`AX_BASE_URL` env var).

### Gateway Model

![Gateway model — one trusted local Gateway, many registered agents](images/gateway-demo-overview.png)

The operator starts Gateway once. Agents register from their own folder or
container, get fingerprinted, wait for approval, then send and read as
themselves. Each agent type (Hermes sentinel, Claude Code channel, pass-through
inbox, service sender) has a distinct runtime shape while sharing the same
identity and credential management.

### Repository Structure

```text
ax-gateway/
├── ax_cli/                    # Python package — the CLI and Gateway
│   ├── main.py                # Typer app entry point, registers all subcommands
│   ├── client.py              # AxClient — wraps all aX REST API endpoints
│   ├── config.py              # Config resolution: CLI flag → env var → .ax/config.toml → profile → global
│   ├── gateway.py             # Gateway daemon: state mgmt, reconcile loop, session tokens (6403 lines)
│   ├── gateway_runtime_types.py  # Runtime type definitions (hermes, echo, exec, etc.)
│   ├── output.py              # Shared output: print_json(), print_table(), handle_error()
│   ├── avatar.py              # Agent avatar generation
│   ├── context_keys.py        # Context key helpers
│   ├── mentions.py            # @mention parsing
│   ├── token_cache.py         # Token caching for JWT exchange
│   └── commands/              # One module per CLI command group
│       ├── agents.py          # ax agents — list, show, register agents
│       ├── auth.py            # ax auth — login, whoami, token management
│       ├── gateway.py         # ax gateway — start/stop/status, HTTP server, proxy dispatcher, UI
│       ├── messages.py        # ax messages — send, list, search
│       ├── tasks.py           # ax tasks — create, list, update
│       ├── keys.py            # ax keys — create, list, revoke API keys
│       ├── credentials.py     # ax credentials — audit, list, revoke
│       ├── spaces.py          # ax spaces — list, use, switch spaces
│       ├── profile.py         # ax profile — add, list, verify, use profiles
│       ├── channel.py         # ax channel — Claude Code channel setup
│       ├── handoff.py         # ax handoff — agent-to-agent work handoff
│       ├── mint.py            # ax token mint — create agent-scoped PATs
│       ├── events.py          # ax events — SSE event streaming
│       ├── context.py         # ax context — shared key-value context
│       ├── bootstrap.py       # ax bootstrap — first-time setup
│       ├── watch.py           # ax watch — watch for task/message completions
│       └── ...                # alerts, apps, heartbeat, listen, qa, reminders, upload
├── channel/                   # Claude Code channel MCP server (TypeScript/Node)
│   ├── package.json
│   └── .mcp.json
├── docs/                      # All documentation
│   ├── quickstart.md          # 10-minute operator onboarding
│   ├── agent-authentication.md  # Auth model, trust boundary, credential flows
│   ├── gateway-agent-runtimes.md  # Runtime patterns, concepts (space, lifecycle, inbox)
│   ├── credential-security.md   # Fingerprinting, honeypots, rotation
│   ├── module-guide-gateway.md  # Code map for gateway.py contributors
│   ├── adr/                   # Architecture Decision Records
│   ├── scenarios/             # Step-by-step task guides
│   └── ...                    # Runbooks, reviews, integration tests
├── tests/                     # pytest test suite (~40 test files)
├── examples/                  # Example agents and bridges
├── scripts/                   # CI and QA automation scripts
├── specs/                     # Design specs (bootstrap, device trust, agent PAT)
├── skills/                    # Claude Code skill definitions
├── docker/                    # Container configs
├── .ax/                       # Local project aX config (not credentials)
├── .github/                   # GitHub Actions, PR template, dependabot
├── pyproject.toml             # Python package config (package name: axctl, command: ax)
├── CLAUDE.md                  # Claude Code instructions for this repo
├── CONTRIBUTING.md            # Contributor guide
├── SECURITY.md                # Security policy
└── README.md                  # Project overview
```

**Key files:**

1. `CLAUDE.md` — architecture overview, development commands, config system
2. `ax_cli/main.py` — see how all commands are wired together
3. `ax_cli/client.py` — the API client that every command uses
4. `ax_cli/config.py` — how the CLI resolves identity, tokens, and base URL

![Config resolution cascade — CLI flag, env var, project config, profile, global](images/config-resolution-cascade.png)

**Key relationship:** `ax_cli/gateway.py` (6403 lines) handles state and the
daemon loop. `ax_cli/commands/gateway.py` handles the CLI commands, HTTP server,
proxy dispatcher, and UI HTML. They work as a pair — see the
[module guide](module-guide-gateway.md) for a section-by-section map.

---

## 1. Glossary of Terms


| Term                            | Definition                                                                                                                                                           | Where in code                                                                  |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| **Gateway**                     | Local HTTP daemon (`127.0.0.1:8765`) that brokers credentials, proxies API calls, and manages agent lifecycles on the operator's machine.                            | `ax_cli/gateway.py` (state/lifecycle), `ax_cli/commands/gateway.py` (CLI/HTTP) |
| **Space**                       | An organizational container on paxai.app. Agents, messages, and tasks belong to a space. Resolved by slug or UUID.                                                   | Space resolution cascade in `ax_cli/gateway.py`                                |
| **PAT (Personal Access Token)** | Long-lived credential for authenticating with the aX API. User PATs (`axp_u_...`) live in `~/.ax/user.toml`; agent-scoped PATs (`axp_a_...`) are managed by Gateway. | `ax_cli/config.py` (resolution), `ax_cli/gateway.py` (minting)                 |
| **User PAT**                    | A PAT scoped to the operator. Used for bootstrap, setup, and minting agent PATs. Must never be used as an agent runtime credential.                                  | See [Agent Authentication](agent-authentication.md)                            |
| **Agent-scoped PAT**            | A PAT locked to one agent. Minted by the operator's user PAT, stored by Gateway. Each agent gets exactly one.                                                        | `~/.ax/gateway/agents/<name>/token`                                            |
| **Managed agent**               | An agent whose lifecycle (start, stop, credentials, space binding) is controlled by Gateway. Contrast with a standalone CLI agent using direct profiles.             | Registry entries in `~/.ax/gateway/registry.json`                              |
| **Reconcile loop**              | The daemon's periodic check (~1s) that each agent's effective state matches its desired state. Handles restarts, rebindings, and health annotation.                  | `GatewayDaemon.run()` in `ax_cli/gateway.py`                                   |
| **Desired state**               | What the operator wants an agent to be (e.g. `running`, `stopped`). Set by CLI commands.                                                                             | `desired_state` field in registry entries                                      |
| **Effective state**             | What Gateway actually observes about the agent process (e.g. `running`, `error`, `pending_approval`). May lag desired state.                                         | `effective_state` field in registry entries                                    |
| **Presence**                    | What Gateway derives about agent liveness (`IDLE`, `OFFLINE`, `STALE`, `ERROR`, `BLOCKED`). Informational — not used for lifecycle decisions.                        | `_derive_presence()`, shown in `agents show`                                   |
| **Proxy dispatcher**            | The `_LOCAL_PROXY_METHODS` allowlist that controls which `AxClient` methods an agent session can call through `/local/proxy`.                                        | `ax_cli/commands/gateway.py`                                                   |
| **Session token**               | Short-lived, per-connect token (`axgw_s_...`) issued when a local agent connects to Gateway. HMAC-SHA256 signed. Not cached or reused.                               | `issue_local_session()` in `ax_cli/gateway.py`                                 |
| **Binding**                     | The association between a managed agent and a space + identity. Can become stale ("phantom rebinding") if upstream state changes without Gateway knowing.            | `evaluate_identity_space_binding()` in `ax_cli/gateway.py`                     |
| **Pending queue**               | Per-agent local queue of messages received but not yet acknowledged. Stored at `~/.ax/gateway/agents/<name>/pending.json`. Cleared by `mark_read`.                   | `ax_cli/gateway.py`                                                            |
| **Inbox agent**                 | An agent that polls for messages via the pending queue. Background worker pattern — checks in periodically.                                                          | `inbox` template type                                                          |
| **Channel agent**               | A Claude Code Channel — a live attached listener using an SSE connection. Contrast with inbox agents that poll.                                                      | `claude_code_channel` template type                                            |
| **Pass-through agent**          | A local agent that authenticates to Gateway, gets approved, then polls its mailbox. The simplest local agent pattern.                                                | `pass_through` template type                                                   |
| **Registry**                    | `~/.ax/gateway/registry.json` — stores agent registrations, templates, workdirs, and credential references. Static config.                                           | `ax_cli/gateway.py`                                                            |
| **Session**                     | `~/.ax/gateway/session.json` — stores ephemeral runtime state: active spaces, session tokens, presence. Rebuilt on Gateway start.                                    | See [ADR-004](adr/ADR-004-space-state-in-session.md)                           |
| **Profile**                     | A named CLI configuration that bundles a token file, base URL, agent name/ID, and fingerprint verification. Alternative to Gateway-managed identity.                 | `ax_cli/config.py`, `ax profile` commands                                      |
| **Fingerprint**                 | Composite SHA-256 hash of workdir + hostname + OS user, sent as `X-AX-FP` header. Detects credential misuse from unexpected locations.                               | See [Credential Security](credential-security.md)                              |

![Profile fingerprint flow — add, verify, activate or refuse](images/profile-fingerprint-flow.svg)
| `**use`/`admin` tiers**         | Proposed access control model (issue #146) to replace the flat proxy allowlist with per-agent permission tiers.                                                      | See [ADR-006](adr/ADR-006-use-admin-proxy-tiers.md)                            |


---

## 2. Concept Pages (How Things Work)

**Problem:** The architecture section in CLAUDE.md describes the module layout, but not the runtime behavior. An operator who wants to understand *why* their agent shows a UUID instead of a name has to read 6000 lines of `gateway.py`.

**Created — added as new sections in existing docs:**


| Concept                   | Location                                                                                                         | Covers                                                                                 |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Space Resolution          | [gateway-agent-runtimes.md — Space Resolution](gateway-agent-runtimes.md#space-resolution)                       | Resolution cascade, `_space_name_from_cache`, UUID-as-name failure, session.json split |
| Trust Boundary Model      | [agent-authentication.md — Trust Boundary Model](agent-authentication.md#trust-boundary-model)                   | Operator vs agent sides, proxy allowlist, session tokens, `use`/`admin` tiers          |
| Agent Lifecycle           | [gateway-agent-runtimes.md — Agent Lifecycle](gateway-agent-runtimes.md#agent-lifecycle)                         | Desired/effective/presence states, reconcile loop, manual attach, corruption repair    |
| Inbox & Mailbox Semantics | [gateway-agent-runtimes.md — Inbox and Mailbox Semantics](gateway-agent-runtimes.md#inbox-and-mailbox-semantics) | Pending queue, mark_read, unread_only filtering, inbox vs channel agents               |

![Space resolution cascade — per-agent cache, disk cache, upstream API](images/space-resolution-cascade.png)

![Trust boundary model — operator vs agent sides with proxy allowlist](images/trust-boundary-model.png)

![Agent lifecycle states — desired, effective, and presence with reconcile loop](images/agent-lifecycle-states.png)

![Credential brokering — Gateway sits between agent and platform](images/credential-brokering.png)

![Inbox vs channel delivery — polling with pending queue vs live SSE stream](images/inbox-vs-channel.png)


---

## 3. Quickstart for New Operators

**Problem:** The current setup path requires reading CLAUDE.md, running `pip install -e .`, logging in, registering an agent, and switching spaces — with no guided walkthrough. Issues #176 and #177 show this gap.

**Created:** [docs/quickstart.md](quickstart.md) — 10-minute walkthrough covering install, login, Gateway start, agent registration, messaging, and operator UI. Each step has expected output and troubleshooting tips.

> **Note:** Step 3 (join a space) is simplified pending issue #176 resolution. The quickstart assumes the operator already has space membership.

### Authentication onboarding trajectory

The quickstart teaches three authentication stages. Interns should understand
all three — today's operators use Stage 1, the codebase is at Stage 2, and the
specs describe Stage 3.

**Stage 1 — Manual PAT paste (current minimum path)**

The operator creates a user PAT in the platform UI and pastes it into
`ax auth login`. This works for standalone CLI use and is the simplest path
for new operators who don't need Gateway.

**Stage 2 — Gateway-brokered credentials (current recommended path)**

`ax gateway login` authenticates the operator once through Gateway. After that,
Gateway mints agent-scoped PATs automatically when agents are registered
(`ax gateway agents add`). Agents never see the user PAT — they get their own
managed credential stored at `~/.ax/gateway/agents/<name>/token`. This is the
model the quickstart recommends and the model all scenario guides assume.

The key teaching point: the user PAT is a **setup credential**, not a runtime
credential. It authenticates the operator to Gateway; Gateway authenticates
agents to the platform. This separation is why
[ADR-005](adr/ADR-005-credentials-never-in-workspace.md) prohibits agent
credentials in workspace config files.

**Stage 3 — Device enrollment and OAuth (future direction)**

The target model described in
[AXCTL-BOOTSTRAP-001](../specs/AXCTL-BOOTSTRAP-001/spec.md) and
[DEVICE-TRUST-001](../specs/DEVICE-TRUST-001/spec.md) replaces the manual PAT
paste with a device-enrollment flow:

1. The operator creates a one-time bootstrap token in the platform UI.
2. A device-enrollment command consumes that token, generates a local device
   keypair, and registers the public key with the platform.
3. The raw bootstrap token is discarded — it is never stored or reusable.
4. The enrolled device uses its device credential to mint agent PATs by policy.
5. Agents use agent-scoped PATs to exchange for short-lived JWTs.

The OAuth/device-code variant would replace steps 1-2 with a browser-based
authorization flow (device code grant), removing the manual token copy entirely.
The operator runs a command, a browser tab opens for authorization, and the CLI
receives a device credential on success.

No arrow points from agent back to raw user token material — that is the core
invariant across all three stages. Stage 2 enforces it by convention (Gateway
doesn't expose the user PAT to agents). Stage 3 enforces it structurally (the
bootstrap token is consumed and discarded during enrollment).

---

## 4. Scenario / Task Pages

**Problem:** Operators learn by doing, not by reading architecture docs. Real-world tasks like "rotate an agent's PAT" or "move an agent to a different space" require combining multiple commands in a specific order.

**Created — all in [docs/scenarios/](scenarios/):**


| Scenario                                    | File                                                                                   | Learning Goal                                  |
| ------------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------- |
| Move an agent to a new space                | [move-agent-to-new-space.md](scenarios/move-agent-to-new-space.md)                     | Space resolution cascade, cache behavior       |
| Debug a stuck agent                         | [debug-stuck-agent.md](scenarios/debug-stuck-agent.md)                                 | Agent lifecycle, effective vs desired state    |
| Send a file through Gateway                 | [send-file-through-gateway.md](scenarios/send-file-through-gateway.md)                 | Trust boundary, proxy dispatcher               |
| Set up a second agent in the same workspace | [setup-second-agent-same-workspace.md](scenarios/setup-second-agent-same-workspace.md) | Workspace identity, `.ax/config.toml` scoping  |
| Investigate a 429 storm                     | [investigate-429-storm.md](scenarios/investigate-429-storm.md)                         | Rate limiting, retry behavior                  |
| Recover from a corrupted registry           | [recover-corrupted-registry.md](scenarios/recover-corrupted-registry.md)               | State files, auto-migration, corruption repair |
| Rotate an agent PAT                         | [rotate-agent-pat.md](scenarios/rotate-agent-pat.md)                                   | Credential lifecycle, mint-verify-revoke order |


Each scenario follows: **Goal** → **Prerequisites** → **Steps** (with expected output) → **Verify** → **What can go wrong**.

![Handoff loop — task, watch, result with timeout recovery](images/supervision-loop.svg)

---

## 5. Architecture Decision Records (ADRs)

**Problem:** The codebase has evolved through 4 phases (CLI parity -> operational correctness -> credential unification -> process attestation) but the reasoning behind key decisions is only captured in commit messages and issue descriptions. When a new contributor asks "why does the proxy have a flat allowlist instead of per-agent permissions?", the answer is spread across issues #146, #161, and PR #173.

**Created — all in [docs/adr/](adr/):**


| ADR     | Decision                                                         | Status                          | File                                                     |
| ------- | ---------------------------------------------------------------- | ------------------------------- | -------------------------------------------------------- |
| ADR-001 | Gateway binds to 127.0.0.1 only (no LAN exposure)                | Accepted                        | [ADR-001](adr/ADR-001-gateway-localhost-only.md)         |
| ADR-002 | Proxy uses a flat allowlist, not per-agent ACLs                  | Accepted (Phase 4 will replace) | [ADR-002](adr/ADR-002-flat-proxy-allowlist.md)           |
| ADR-003 | Session tokens are short-lived and per-connect, not cached       | Accepted                        | [ADR-003](adr/ADR-003-session-tokens-per-connect.md)     |
| ADR-004 | Space state lives in `session.json`, not `registry.json`         | Accepted (PR #172)              | [ADR-004](adr/ADR-004-space-state-in-session.md)         |
| ADR-005 | Agent credentials are brokered, never copied to workspace config | Accepted                        | [ADR-005](adr/ADR-005-credentials-never-in-workspace.md) |
| ADR-006 | `use`/`admin` tier model for proxy methods                       | Proposed (issue #146)           | [ADR-006](adr/ADR-006-use-admin-proxy-tiers.md)          |


Format: standard ADR template — Title, Status, Context, Decision, Consequences.

---

## 6. Module Guides for Contributors

**Problem:** `gateway.py` is 6000+ lines. A contributor assigned to fix a bug in space resolution has to understand the whole file to find the relevant 50 lines. The architecture section in CLAUDE.md describes the module layout but not the internal structure of large modules.

**Created:** [docs/module-guide-gateway.md](module-guide-gateway.md) — verified section map of both `ax_cli/gateway.py` (5846 lines, state/lifecycle) and `ax_cli/commands/gateway.py` (CLI/HTTP/proxy), with actual line ranges, key function names, and state file descriptions.

---

## 7. Good First Issues

**Problem:** The project has active community contributors (fortuna-code-solutions, viewsonic-max) but no issues explicitly tagged for newcomers. The "good first issue" label is a standard signal that lowers the barrier for first-time contributors.

**Created on GitHub** with `good first issue` label:


| Issue                                                   | Scope                       | Skills Required                | GitHub                                                       |
| ------------------------------------------------------- | --------------------------- | ------------------------------ | ------------------------------------------------------------ |
| Add `ruff format` to pre-commit hook                    | Config only                 | Python tooling                 | [#181](https://github.com/ax-platform/ax-gateway/issues/181) |
| Add `tier` annotation to `_LOCAL_PROXY_METHODS` entries | 1 dict, 1 comment per entry | Python, reading issue #146     | [#182](https://github.com/ax-platform/ax-gateway/issues/182) |
| Fix `_space_name_from_cache` to reject UUID-like names  | 1 function, ~5 lines        | Python, regex                  | [#183](https://github.com/ax-platform/ax-gateway/issues/183) |
| Add Host header validation middleware                   | ~20 lines                   | Python, HTTP basics            | [#184](https://github.com/ax-platform/ax-gateway/issues/184) |
| Add `--bound-agent-id` to `ax keys create` (issue #177) | 1 command module            | Python, Typer CLI              | [#185](https://github.com/ax-platform/ax-gateway/issues/185) |
| Write scenario: rotate an agent PAT                     | Docs only                   | Reading credential-security.md | [#186](https://github.com/ax-platform/ax-gateway/issues/186) |
| Add integration test for local session token lifecycle  | 1 test file                 | Python, pytest                 | [#187](https://github.com/ax-platform/ax-gateway/issues/187) |


> **Note:** The glossary (section 1) and ADRs (section 5) are now created inline and in `docs/adr/` respectively, so those are no longer "good first issue" candidates.

---

## Implementation Status


| Priority | Item                                      | Status                                                                                  |
| -------- | ----------------------------------------- | --------------------------------------------------------------------------------------- |
| **P0**   | Quickstart (section 3)                    | Done — [quickstart.md](quickstart.md)                                                   |
| **P0**   | Glossary (section 1)                      | Done — inline above                                                                     |
| **P1**   | Good First Issues (section 7)             | Done — created on GitHub                                                                |
| **P1**   | Trust Boundary concept page (section 2.2) | Done — added to [agent-authentication.md](agent-authentication.md#trust-boundary-model) |
| **P2**   | Scenario pages (section 4)                | Done — 6 scenarios in [docs/scenarios/](scenarios/)                                     |
| **P2**   | ADRs (section 5)                          | Done — 6 ADRs in [docs/adr/](adr/)                                                      |
| **P2**   | Concept pages (section 2)                 | Done — added to [gateway-agent-runtimes.md](gateway-agent-runtimes.md)                  |
| **P3**   | Module guides (section 6)                 | Done — [module-guide-gateway.md](module-guide-gateway.md)                               |


## Suggested Reading Order for New Interns

1. **Start here:** [Glossary](#1-glossary-of-terms) — learn the vocabulary
2. **Hands-on:** [Quickstart](quickstart.md) — install, run, send a message
3. **Concepts:** [Trust Boundary](agent-authentication.md#trust-boundary-model) → [Agent Lifecycle](gateway-agent-runtimes.md#agent-lifecycle) → [Space Resolution](gateway-agent-runtimes.md#space-resolution)
4. **Practice:** Pick a [scenario](scenarios/) and run through it
5. **Understand why:** Read 2-3 [ADRs](adr/) for context on design decisions
6. **Navigate the code:** [Module guide](module-guide-gateway.md) for gateway.py
7. **Contribute:** Pick a [good first issue](#7-good-first-issues) from the backlog

---

## All Documentation

Full index of every doc in this directory. See also [docs/README.md](README.md).

### Training and reference (already linked above)

| Doc | Purpose |
| --- | --- |
| [quickstart.md](quickstart.md) | 10-minute operator onboarding |
| [agent-authentication.md](agent-authentication.md) | Auth model, trust boundary, credential flows |
| [gateway-agent-runtimes.md](gateway-agent-runtimes.md) | Runtime patterns, space resolution, agent lifecycle, inbox semantics |
| [credential-security.md](credential-security.md) | Fingerprinting, honeypots, PAT rotation |
| [module-guide-gateway.md](module-guide-gateway.md) | Code map for gateway.py contributors |
| [adr/](adr/) | 6 Architecture Decision Records |
| [scenarios/](scenarios/) | 7 step-by-step task guides |

### Operational guides

| Doc | Purpose |
| --- | --- |
| [release-process.md](release-process.md) | Versioning, PyPI publishing, changelog generation |
| [operator-qa-runbook.md](operator-qa-runbook.md) | Manual and automated QA checks for Gateway |
| [gateway-demo-script.md](gateway-demo-script.md) | Demo walkthrough for presentations and onboarding |
| [login-e2e-runbook.md](login-e2e-runbook.md) | End-to-end login flow testing |

### MCP integration

| Doc | Purpose |
| --- | --- |
| [mcp-app-signal-adapter.md](mcp-app-signal-adapter.md) | MCP adapter for processing signals |
| [mcp-headless-pat.md](mcp-headless-pat.md) | Headless PAT authentication for MCP |
| [mcp-remote-oauth.md](mcp-remote-oauth.md) | Remote OAuth flow for MCP |
| [reminder-lifecycle.md](reminder-lifecycle.md) | How agent reminders work |

### Point-in-time artifacts

These are snapshots from specific reviews or test runs. Useful for context but
not maintained as living docs.

| Doc | Date | Purpose |
| --- | --- | --- |
| [pr173-review-08may26.md](pr173-review-08may26.md) | 2026-05-08 | PR #173 review findings — source of many items in this doc |
| [roadmap-assessment-08may26.md](roadmap-assessment-08may26.md) | 2026-05-08 | Roadmap assessment across all ax-platform repos |
| [integration-tests-gateway.md](integration-tests-gateway.md) | 2026-05-08 | Gateway integration test plan |
| [integration-test-results-20260508.md](integration-test-results-20260508.md) | 2026-05-08 | Integration test results snapshot |

