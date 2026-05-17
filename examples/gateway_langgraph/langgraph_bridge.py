#!/usr/bin/env python3
"""Gateway-managed bridge for a LangGraph agent.

This bridge is designed for `ax gateway agents add ... --template langgraph`.
It runs once per inbound mention: read the prompt, route it through a
LangGraph StateGraph, and print the reply on stdout.

Three execution tiers, picked at runtime by what is installed and
configured.

  1. Real LLM path. If `langgraph` AND `groq` are importable AND
     GROQ_API_KEY is set, the bridge builds a one-node StateGraph
     whose node calls Groq's chat completions and returns the model's
     reply. AX_BRIDGE_LLM_MODEL overrides the default model
     (llama-3.3-70b-versatile).

  2. Stub graph path. If `langgraph` is importable but Groq is not
     configured, the bridge builds the same one-node StateGraph but
     wires it to a synthetic ack node that does not call any LLM.
     This proves the langgraph wiring without requiring credentials.

  3. String fallback path. If `langgraph` itself is not installed,
     the bridge returns a plain string template. Same lifecycle
     events still fire.

The three-tier shape lets the bridge round-trip a reply through the
Gateway end to end in CI / dev without LLM creds, and switch to real
LLM execution in production environments where GROQ_API_KEY is
provisioned. Multi-node graphs, tool calls, and streaming-event
forwarding are deliberate follow-ups.
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
        or "langgraph-bot"
    )


DEFAULT_LLM_MODEL = "llama-3.3-70b-versatile"


def _build_llm_node(model: str):
    """Build a LangGraph node that calls Groq for the given model.

    The node takes a state dict with a "prompt" key and returns a dict
    with a "reply" key holding the model's text response. A short system
    prompt names the routed agent so the model knows who it is replying
    as.

    Raises ImportError if the groq SDK is not installed, which the
    caller treats as a signal to fall back to the stub ack node.
    """
    from groq import Groq

    client = Groq()  # picks up GROQ_API_KEY from the environment

    def _llm_node(state: dict[str, Any]) -> dict[str, Any]:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are @{_agent_name()}, an assistant routed through the aX Gateway. Reply concisely."
                    ),
                },
                {"role": "user", "content": state.get("prompt", "")},
            ],
        )
        reply = ""
        if response.choices:
            reply = response.choices[0].message.content or ""
        return {"reply": reply}

    return _llm_node


def _run_graph(prompt: str) -> str:
    """Run a one-node LangGraph (real LLM or stub) if langgraph is
    available, else a plain string template.

    See the module docstring for the three-tier behavior. The graph
    itself is intentionally one node for now. Real multi-node graphs
    with tool-call telemetry mapped to Gateway tool bubbles are a
    follow-up.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        emit_event(
            {
                "kind": "activity",
                "activity": "langgraph not installed; using stub reply (install langgraph for real graph execution)",
            }
        )
        return f"LangGraph stub ack from @{_agent_name()}: {prompt}"

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    model = os.environ.get("AX_BRIDGE_LLM_MODEL", DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL
    llm_node = None
    if groq_key:
        try:
            llm_node = _build_llm_node(model)
        except ImportError:
            emit_event(
                {
                    "kind": "activity",
                    "activity": "GROQ_API_KEY set but groq SDK not installed; falling back to stub node",
                }
            )

    if llm_node is not None:
        emit_event(
            {
                "kind": "activity",
                "activity": f"building one-node StateGraph with Groq LLM node (model={model})",
            }
        )
        node = llm_node
        used_llm = True
    else:
        emit_event(
            {
                "kind": "activity",
                "activity": "building one-node StateGraph with stub ack node (no LLM configured)",
            }
        )

        def _ack_node(state: dict[str, Any]) -> dict[str, Any]:
            return {"reply": f"LangGraph ack from @{_agent_name()}: {state.get('prompt', '')}"}

        node = _ack_node
        used_llm = False

    graph = StateGraph(dict)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    app = graph.compile()

    result = app.invoke({"prompt": prompt})
    reply = str(result.get("reply") or "")
    # Stash the path that was taken so main() can report it accurately
    # in the completion event detail.
    _run_graph.last_used_llm = used_llm  # type: ignore[attr-defined]
    return reply


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
            "message": "Routing prompt through LangGraph bridge",
        }
    )

    try:
        reply = _run_graph(prompt)
    except Exception as exc:
        emit_event({"kind": "status", "status": "error", "error_message": str(exc)})
        print(f"LangGraph bridge failed: {exc}", file=sys.stderr)
        return 1

    used_llm = bool(getattr(_run_graph, "last_used_llm", False))
    duration_ms = int((time.monotonic() - started) * 1000)
    emit_event(
        {
            "kind": "status",
            "status": "completed",
            "message": f"LangGraph bridge completed in {duration_ms}ms",
            "detail": {"duration_ms": duration_ms, "stub": not used_llm, "used_llm": used_llm},
        }
    )
    print(reply or f"LangGraph bridge for @{_agent_name()} finished without text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
