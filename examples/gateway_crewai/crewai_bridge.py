#!/usr/bin/env python3
"""Gateway-managed bridge for a CrewAI agent.

This bridge is designed for `ax gateway agents add ... --template crewai`.
It runs once per inbound mention: read the prompt, route it through a
CrewAI Crew, and print the reply on stdout.

The initial cut intentionally ships with a stub Crew. The point of
this slice is the Gateway-side plumbing: prove the runtime registers,
emits AX_GATEWAY_EVENT lifecycle signals (processing -> completed),
and rounds a reply through the Gateway end to end. Real CrewAI
execution with multi-agent crews, tool calls, and LLM-driven kickoff()
is a follow-up that requires LLM provisioning.

If `crewai` is importable, the bridge constructs (but does NOT invoke)
a one-Agent / one-Task / one-Crew object set so the API surface is
exercised without requiring an LLM. If `crewai` is not importable, it
falls back to a string template. Either path emits the same lifecycle
events.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

EVENT_PREFIX = "AX_GATEWAY_EVENT "


def emit_event(payload: dict[str, Any]) -> None:
    print(f"{EVENT_PREFIX}{json.dumps(payload, sort_keys=True)}", flush=True)


def _read_prompt() -> str:
    if len(sys.argv) > 1 and sys.argv[-1] != "-":
        return sys.argv[-1]
    env_prompt = os.environ.get("AX_MENTION_CONTENT", "").strip()
    if env_prompt:
        return env_prompt
    return sys.stdin.read().strip()


def _agent_name() -> str:
    return (
        os.environ.get("AX_GATEWAY_AGENT_NAME", "").strip()
        or os.environ.get("AX_AGENT_NAME", "").strip()
        or "crewai-bot"
    )


def _run_stub_crew(prompt: str) -> str:
    """Construct (but do not invoke) a one-Agent Crew if crewai is available,
    else a string template.

    The Crew is intentionally trivial AND not kickoff()'d. The point of this
    slice is to prove the Gateway-side adapter, not the orchestration. CrewAI
    requires an LLM to actually execute (via OPENAI_API_KEY or similar), so
    the stub stops at object construction. Future iterations will introduce
    a real kickoff() with an LLM-backed Crew once provisioning is sorted.
    """
    try:
        from crewai import Agent, Crew, Task
    except ImportError:
        emit_event(
            {
                "kind": "activity",
                "activity": "crewai not installed; using stub reply (install crewai for real Crew execution)",
            }
        )
        return f"CrewAI stub ack from @{_agent_name()}: {prompt}"

    emit_event(
        {
            "kind": "activity",
            "activity": "constructing one-Agent Crew (stub, no LLM kickoff)",
        }
    )

    # CrewAI's Agent normally requires an LLM via env vars or explicit
    # config; calling crew.kickoff() without one raises. The stub bridge
    # exercises the import + class-construction surface only and skips
    # the kickoff() call so the bridge round-trips without LLM creds.
    try:
        agent = Agent(
            role="Acknowledger",
            goal="Acknowledge incoming prompts and echo them back",
            backstory="A minimal agent for the bridge stub.",
            allow_delegation=False,
            verbose=False,
        )
        task = Task(
            description=f"Acknowledge this prompt: {prompt}",
            expected_output="A brief acknowledgment that echoes the prompt back",
            agent=agent,
        )
        Crew(agents=[agent], tasks=[task], verbose=False)
    except Exception as exc:
        emit_event(
            {
                "kind": "activity",
                "activity": f"crewai stub construction raised {exc!r}; falling back to string template",
            }
        )

    return f"CrewAI ack from @{_agent_name()}: {prompt}"


def main() -> int:
    prompt = _read_prompt()
    if not prompt:
        print("(no mention content received)", file=sys.stderr)
        return 1

    started = time.monotonic()
    emit_event(
        {
            "kind": "status",
            "status": "processing",
            "message": "Routing prompt through CrewAI bridge",
        }
    )

    try:
        reply = _run_stub_crew(prompt)
    except Exception as exc:
        emit_event({"kind": "status", "status": "error", "error_message": str(exc)})
        print(f"CrewAI bridge failed: {exc}", file=sys.stderr)
        return 1

    duration_ms = int((time.monotonic() - started) * 1000)
    emit_event(
        {
            "kind": "status",
            "status": "completed",
            "message": f"CrewAI bridge completed in {duration_ms}ms",
            "detail": {"duration_ms": duration_ms, "stub": True},
        }
    )
    print(reply or f"CrewAI bridge for @{_agent_name()} finished without text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
