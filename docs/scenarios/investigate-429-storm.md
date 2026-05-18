# Scenario: Investigate a 429 Rate-Limiting Storm

## Goal

Diagnose and resolve a burst of HTTP 429 (Too Many Requests) errors from the
aX platform API.

## Prerequisites

- Gateway running with one or more active agents
- Access to Gateway log files

## Steps

### 1. Check activity log for 429 events

Most 429 responses surface as backoff events in the activity log, not in
`gateway.log`:

```bash
grep -i "429\|backoff\|rate.limit" ~/.ax/gateway/activity.jsonl | tail -20
```

If the count is high (dozens in a short window), you have a rate-limiting storm.

### 2. Identify which agents are triggering 429s

```bash
grep -i "429\|backoff" ~/.ax/gateway/activity.jsonl | \
  grep "$(date +%Y-%m-%d)" | tail -20
```

Look for the agent name in each event. Common culprits:

- Agents polling `list_messages` too frequently
- Multiple agents starting simultaneously, each calling `whoami` + `list_spaces`
- Hermes sentinel runtimes retrying on transient errors

### 3. Check overall activity volume

```bash
grep "$(date +%Y-%m-%d)" ~/.ax/gateway/activity.jsonl | wc -l
```

Compare total activity today vs. a normal day. A spike often correlates with
an agent restart cascade.

### 4. Check backoff behavior

Gateway should back off automatically on 429 responses. Backoff events appear
in the activity log:

```bash
grep -i "backoff\|retry" ~/.ax/gateway/activity.jsonl | tail -10
```

If you see no backoff messages, the retry logic may not be handling 429s
correctly — file a bug (see also issue #27).

### 5. Reduce load

If the storm is ongoing, reduce the number of active agents:

```bash
# Stop non-essential agents
ax gateway agents stop echo-bot
ax gateway agents stop monitor-agent

# Keep only critical agents running
ax gateway agents list
```

### 6. Stagger restarts

When restarting agents after a 429 storm, stagger them to avoid a
reconnection stampede:

```bash
ax gateway agents start dev-sentinel
sleep 10
ax gateway agents start review-agent
sleep 10
ax gateway agents start echo-bot
```

### 7. Verify recovery

```bash
ax gateway status
ax gateway agents show dev-sentinel
```

Check that agents are healthy and no new 429 events appear:

```bash
tail -f ~/.ax/gateway/activity.jsonl | grep -i "429\|backoff"
```

Wait 60 seconds. If no new lines appear, the storm has passed.

## Verify

- No new 429 events in `activity.jsonl` for at least 60 seconds
- All critical agents show `effective_state: running`
- Messages are being delivered normally

## What can go wrong

| Problem | Cause | Fix |
| --- | --- | --- |
| 429s continue after reducing agents | Platform-wide rate limit, not per-agent | Wait for the rate limit window to expire (usually 1-5 minutes) |
| Agent enters error state after 429 storm | Too many consecutive failures triggered a health check failure | Restart the agent after the storm passes |
| Reconcile loop itself triggers 429s | Loop calls upstream API for each agent every ~1 second | Reduce registered agent count, or wait for batch API support |

## Learning goal

Understanding Gateway's relationship with the upstream API rate limits. The
reconcile loop, agent startups, and space resolution all make API calls. With
many agents, these calls can exceed platform rate limits. Operators need to
understand that agent count directly affects API call volume and plan agent
registrations accordingly.
