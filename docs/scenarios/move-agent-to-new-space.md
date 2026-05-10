# Scenario: Move an Agent to a New Space

## Goal

Move a managed agent from one space to another without losing its registration
or credentials.

## Prerequisites

- Gateway running (`ax gateway status` shows running)
- Agent registered and running (`ax gateway agents show <agent>`)
- You are a member of the target space

## Steps

### 1. Check current space binding

```bash
ax gateway agents show dev-sentinel
```

Note the `active_space_name` and `active_space_id` fields. This is where the
agent currently operates.

### 2. List available spaces

```bash
ax spaces list
```

Find the target space name or ID.

### 3. Move the agent to the target space

```bash
ax gateway agents move dev-sentinel --space <target-space-id>
```

This calls `_move_managed_agent_space()`, which updates the agent's
`active_space_id` in the registry and rebinds the runtime. Do not use
`ax spaces use` — that only changes the CLI's active space, not the agent's
Gateway binding.

### 4. Verify the switch

```bash
ax gateway agents show dev-sentinel
```

**Expected:** `active_space_name` shows the new space name. `active_space_id`
shows the new space UUID.

### 5. Test message delivery

```bash
ax send "space switch test" --to dev-sentinel --skip-ax
ax gateway agents inbox dev-sentinel
```

The message should appear in the inbox under the new space context.

## Verify

- `agents show` displays the new space name (not a UUID)
- Messages route correctly in the new space
- The agent's credentials remain valid (no re-registration needed)

## What can go wrong

| Problem | Cause | Fix |
| --- | --- | --- |
| `active_space_name` shows a UUID | Space cache has UUID-as-name from upstream | Wait for cache refresh, or restart Gateway to force a fresh `list_spaces` |
| Agent stops responding after switch | Space binding not propagated | Check `ax gateway agents show <agent>` for the new `active_space_id`, then `ax gateway agents stop <agent> && ax gateway agents start <agent>` |
| "Not a member of space" error | Your user PAT doesn't have access to the target space | Ask admin to add you to the space |

## Learning goal

Understanding the space resolution cascade: how the registry agent row
(`active_space_id`), per-agent `allowed_spaces` cache, and upstream API work
together when an operator moves an agent. See [Gateway Agent Runtimes — Space Resolution](../gateway-agent-runtimes.md#space-resolution).
