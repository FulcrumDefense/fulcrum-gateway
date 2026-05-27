# Amazon Bedrock AgentCore ↔ ax-gateway integration

End-to-end demo: deploy an agent to **Amazon Bedrock AgentCore Runtime**, then
let **ax-gateway** route `@mentions` from an aX space to it using the
`bedrock_agentcore` managed-agent template. The AgentCore agent has no
aX-specific code — Gateway brokers the connection.

```
  aX space                 ax-gateway                       AWS
 ┌─────────┐   mention    ┌────────────────────────┐  InvokeAgentRuntime  ┌──────────────────┐
 │ @standup├─────────────►│ bedrock_agentcore bridge├─────────────────────►│ AgentCore Runtime│
 │  -bot   │              │  (one run per mention) │  payload {"prompt"}  │   (agent.py)     │
 │         │◄─────────────┤  reply + activity feed │◄─────────────────────┤   SSE reply      │
 └─────────┘   reply      └────────────────────────┘   text/event-stream  └──────────────────┘
```

Based on the AWS tutorial
[Get started with the AgentCore CLI](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html).

## Files

| File | Side | Purpose |
|---|---|---|
| `agent.py` | AWS | Demo AgentCore agent — a Strands "Standup Bot" with one tool. Deployed to AgentCore Runtime. |
| `requirements.txt` | AWS | Deps deployed *with* the agent (`bedrock-agentcore`, `strands-agents`). |
| `invoke_agent.py` | local | Direct boto3 smoke test of the deployed runtime — verify it works before involving Gateway. |

> The gateway side needs nothing from this directory: the bridge that calls
> AgentCore ships in axctl (`ax_cli/bridges/bedrock_agentcore_bridge.py`).

## Prerequisites

- **AWS account + credentials** configured (`aws sts get-caller-identity` works).
- **Node.js 20+**, **Python 3.10+**, and **AWS CDK** — required by the `agentcore` CLI.
- **Bedrock model access**: enable Anthropic Claude in the Bedrock console, in your target region (the tutorial defaults to `us-west-2`).
- **axctl with the bedrock extra**, wherever the gateway runs:
  ```bash
  pip install 'axctl[bedrock]'      # pulls boto3>=1.38
  ```
- A **running aX gateway session** bound to a space (`ax gateway ...`). See the repo's gateway docs.

---

## Part 1 — Deploy the agent to AgentCore Runtime (AWS side)

**1.1 Install the AgentCore CLI**
```bash
npm install -g @aws/agentcore
agentcore --help
```

**1.2 Scaffold a project and drop in the demo agent**
```bash
agentcore create --name StandupBot --framework Strands --model-provider Bedrock --memory none
```
Replace the generated entrypoint with this demo's logic — copy the body of
[`agent.py`](agent.py) into the scaffolded entrypoint (`app/StandupBot/main.py`)
and add `strands-agents` / `bedrock-agentcore` to its dependencies (see
[`requirements.txt`](requirements.txt)). The `@app.entrypoint` contract is
identical, so the agent works whether you scaffold it or hand-write it.

**1.3 Test locally first**
```bash
cd StandupBot
agentcore dev                       # serves http://localhost:8080, opens the inspector
agentcore dev "who is on call today?"   # in a second terminal
```

**1.4 Deploy**
```bash
agentcore deploy
```
This packages the agent, then uses the AWS CDK to create the IAM role and the
AgentCore Runtime. (First time on a fresh account: `cdk bootstrap`.)

**1.5 Get the runtime ARN** — you'll hand this to Gateway:
```bash
agentcore status
# ⇒ arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/StandupBot-abc123
```

**1.6 Smoke-test the deployed runtime directly** (no Gateway yet):
```bash
python invoke_agent.py "who is on call today?" \
    --arn arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/StandupBot-abc123
```
If you get a sensible reply, the AWS side is good. This script invokes the
runtime the same way the gateway bridge does, so a pass here means the bridge
will work too.

---

## Part 2 — Wire it into ax-gateway (Gateway side)

**2.1 Confirm prerequisites where the gateway runs**
```bash
pip install 'axctl[bedrock]'
aws sts get-caller-identity          # boto3 default chain must resolve here
```
> Gateway is the trust boundary: AWS credentials stay in the boto3 default
> chain (env, `~/.aws/credentials`, SSO, instance profile). They are **not**
> copied into `.ax/config.toml`, messages, or logs. Only the runtime ARN —
> not a secret — is stored in the gateway registry.

**2.2 Register the AgentCore runtime as a managed agent**
```bash
ax gateway agents add standup-bot \
    --template bedrock_agentcore \
    --bedrock-runtime-arn arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/StandupBot-abc123 \
    --space my-space \
    --description "Bedrock AgentCore standup bot"
```
Useful extra flags (all optional):

| Flag | Meaning | Default |
|---|---|---|
| `--bedrock-region` | AWS region override | parsed from the ARN |
| `--aws-profile` | AWS profile to use (injects `AWS_PROFILE`) | default chain |
| `--bedrock-qualifier` | AgentCore endpoint qualifier | `DEFAULT` |
| `--bedrock-payload-key` | JSON key the agent reads the prompt from | `prompt` |

`--space` is matched explicitly and fails closed on an ambiguous slug/name —
confirm the resolved space is the one you intend.

**2.3 Confirm the gateway sees it**
```bash
ax gateway agents list
ax gateway doctor
```

**2.4 Talk to it from the space** — `@mention` the agent:
```
@standup-bot who is on call today?
```
Gateway runs the bridge once for that mention: you'll see `processing` →
`tool_start` (on_call_today) → `tool_result` → `completed` in the activity
feed, then the reply posts back to the space.

---

## Part 3 — How the integration works

- **One run per mention.** The bridge (`ax_cli/bridges/bedrock_agentcore_bridge.py`)
  is an exec-runtime invoked once per inbound mention; it calls
  `invoke_agent_runtime` and prints the reply on stdout per the Gateway
  exec-runtime contract.
- **Config flows as env, not secrets.** Gateway injects `AX_BEDROCK_RUNTIME_ARN`,
  `AX_BEDROCK_REGION`, `AX_BEDROCK_QUALIFIER`, `AX_BEDROCK_PAYLOAD_KEY`, and
  `AWS_PROFILE` (see `sanitize_exec_env` in `ax_cli/gateway.py`). AWS auth itself
  is the boto3 default chain — Gateway never holds AWS secrets.
- **Per-caller session continuity.** The bridge derives a deterministic
  `runtimeSessionId` from `(agent_id, space_id, sender_id)`, so two people
  mentioning the agent in the same space get isolated AgentCore sessions
  instead of a shared, cross-contaminated one.
- **Streaming → activity feed.** SSE chunks are classified into reply text,
  `tool_start`, `tool_result`, `activity`, and throttled heartbeats, so the
  operator sees progress while the agent works.
- **Payload key.** The demo agent reads `payload["prompt"]`, matching the
  default. If your agent expects a different key, set `--bedrock-payload-key`.

---

## Part 4 — Update, switch, and clean up

Point the gateway agent at a new deployment (region re-derived from the new ARN
unless you also pass `--bedrock-region`):
```bash
ax gateway agents update standup-bot \
    --bedrock-runtime-arn arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/StandupBot-def456
```

Remove it from the gateway:
```bash
ax gateway agents remove standup-bot
```

Tear down the AWS resources:
```bash
agentcore remove all
agentcore deploy        # applies the removal — tears down the runtime + role
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `boto3 ... does not include the bedrock-agentcore service client` | `pip install --upgrade 'boto3>=1.38' 'axctl[bedrock]'` |
| `ARN looks like a Bedrock foundation-model ARN` | Use the **runtime** ARN from `agentcore status`, not a model-catalog ARN. |
| `AccessDenied` on invoke | Caller needs `bedrock-agentcore:InvokeAgentRuntime`; confirm the region matches the ARN. |
| No reply text, only activity | Check `agentcore logs` / CloudWatch `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`. The `completed` event reports the chunk count. |
| Model access denied | Enable Anthropic Claude in the Bedrock console in the correct region. |
