#!/usr/bin/env python3
"""Demo Amazon Bedrock AgentCore Runtime agent — a team "Standup Bot".

This is the AWS-side agent you deploy *to* AgentCore Runtime. Once deployed,
its runtime ARN is wired into ax-gateway with the `bedrock_agentcore`
template (see README.md), so the same agent answers @mentions inside an aX
space without any aX-specific code living here.

Framework: Strands Agents on Amazon Bedrock (Claude). The entrypoint streams
its reply, so Gateway's activity feed shows the tool call live before the
final answer posts back to the space.

Entry contract (AgentCore Runtime InvokeAgentRuntime):
    request payload : {"prompt": "<user text>"}      # matches the bridge default
    response        : Server-Sent Events stream of reply-text chunks

Run locally (mimics the runtime container on http://localhost:8080):
    pip install -r requirements.txt
    python agent.py

Deploy + get the runtime ARN: see README.md (the `agentcore` CLI).
"""

from __future__ import annotations

import datetime as _dt

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool

app = BedrockAgentCoreApp()


# --- A tiny tool, so the demo surfaces tool_start/tool_result in the feed -----
# The gateway bridge classifies AgentCore stream events; a real tool call makes
# the integration's activity stream visibly do something during the demo.

_ROSTER = {
    "monday": "Ada (backend), Grace (frontend)",
    "tuesday": "Linus (infra), Ada (backend)",
    "wednesday": "Grace (frontend), Margaret (data)",
    "thursday": "Linus (infra), Margaret (data)",
    "friday": "Ada (backend), Grace (frontend), Linus (infra)",
}


@tool
def on_call_today() -> str:
    """Return who is on call today, by weekday. Call this for any question
    about the current on-call rotation or who is available right now."""
    weekday = _dt.datetime.now().strftime("%A").lower()
    return f"{weekday.title()}: {_ROSTER.get(weekday, 'nobody scheduled — weekend')}"


SYSTEM_PROMPT = (
    "You are Standup Bot, a concise assistant for an engineering team that "
    "coordinates over the aX multi-agent platform. Answer standup-style "
    "questions directly. When asked who is on call or who is available, call "
    "the on_call_today tool rather than guessing. Keep replies under four "
    "sentences."
)

# Strands defaults to a Bedrock Claude model. To pin a model, pass e.g.
# Agent(model="us.anthropic.claude-sonnet-4-20250514-v1:0", ...).
agent = Agent(system_prompt=SYSTEM_PROMPT, tools=[on_call_today])


@app.entrypoint
async def invoke(payload: dict, context=None):
    """AgentCore Runtime entrypoint: read {"prompt": ...}, stream reply text."""
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        yield 'Send me a prompt, e.g. {"prompt": "who is on call today?"}'
        return

    # Strands emits incremental assistant text under the "data" key; yielding
    # each chunk makes BedrockAgentCoreApp serve the response as an SSE stream.
    async for event in agent.stream_async(prompt):
        if "data" in event:
            yield event["data"]


if __name__ == "__main__":
    app.run()
