"""ax gateway diagnostics — status, doctor, dashboard, alerts, approvals.

Extracted from ``commands/gateway.py`` per issue #28 Phase 1. Owns the
``activity`` / ``status`` top-level commands, the ``agents show / test /
move / doctor`` operator surfaces, and the ``approvals`` sub-app. Hosts the
status payload, doctor checks, Gateway-brokered test sender, and the Rich
dashboard rendering used by ``ax gateway status`` and ``ax gateway watch``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer
from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..gateway import (
    AX_PLUGIN_NAME,
    _hermes_plugin_home,
    _is_system_agent,
    _plugin_source_dir,
    agent_dir,
    annotate_runtime_health,
    approve_gateway_approval,
    archive_stale_gateway_approvals,
    daemon_status,
    deny_gateway_approval,
    ensure_gateway_identity_binding,
    find_agent_entry,
    gateway_dir,
    gateway_environment,
    get_gateway_approval,
    hermes_setup_status,
    list_gateway_approvals,
    load_gateway_registry,
    load_gateway_session,
    load_recent_gateway_activity,
    ollama_setup_status,
    record_gateway_activity,
    save_gateway_registry,
    ui_status,
)
from ..gateway_runtime_types import agent_template_definition
from ..output import JSON_OPTION, console, err_console, print_json, print_table

_STATE_STYLES = {
    "running": "green",
    "starting": "cyan",
    "reconnecting": "yellow",
    "stale": "yellow",
    "error": "red",
    "stopped": "dim",
}
_PRESENCE_STYLES = {
    "IDLE": "green",
    "QUEUED": "cyan",
    "WORKING": "green",
    "BLOCKED": "yellow",
    "STALE": "yellow",
    "OFFLINE": "dim",
    "ERROR": "red",
}
_CONFIDENCE_STYLES = {
    "HIGH": "green",
    "MEDIUM": "cyan",
    "LOW": "yellow",
    "BLOCKED": "red",
}
_PRESENCE_ORDER = {
    "ERROR": 0,
    "BLOCKED": 1,
    "WORKING": 2,
    "QUEUED": 3,
    "STALE": 4,
    "OFFLINE": 5,
    "IDLE": 6,
}


def _gateway_test_sender_name(space_id: str) -> str:
    normalized = "".join(ch for ch in str(space_id or "") if ch.isalnum()).lower()
    suffix = normalized[:8] or "default"
    return f"switchboard-{suffix}"


def _space_cache_with(space_rows: object, space_id: str, *, name: str | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    if isinstance(space_rows, list):
        for item in space_rows:
            if isinstance(item, dict):
                item_space_id = str(item.get("space_id") or item.get("id") or "").strip()
                item_name = str(item.get("name") or item.get("space_name") or item_space_id)
                is_default = bool(item.get("is_default", False))
            else:
                item_space_id = str(item or "").strip()
                item_name = item_space_id
                is_default = False
            if not item_space_id or item_space_id in seen:
                continue
            seen.add(item_space_id)
            rows.append({"space_id": item_space_id, "name": item_name, "is_default": is_default})
    if space_id and space_id not in seen:
        rows.append({"space_id": space_id, "name": name or space_id, "is_default": not rows})
    return rows


def _ensure_gateway_test_sender(target_entry: dict) -> dict:
    """Auto-register or fetch the per-space switchboard service account.

    Service-account-only utility. Used by service-event flows (reminders, log
    fan-outs, system notifications) that legitimately need a Gateway-managed
    service identity. Must NOT be called from the default `agents test` path —
    principal-invoked surfaces author as the invoking principal, not as a
    service account. See `feedback_invoking_principal_default` (Madtank/
    supervisor, 2026-05-02) for the conceptual model.
    """
    from . import gateway as _gateway_cmd

    _register = getattr(_gateway_cmd, "_register_managed_agent")

    target_space = str(target_entry.get("space_id") or "").strip()
    if not target_space:
        raise ValueError("Managed agent is missing a space id for Gateway test delivery.")
    sender_name = _gateway_test_sender_name(target_space)
    registry = load_gateway_registry()
    existing = find_agent_entry(registry, sender_name)
    if existing:
        return annotate_runtime_health(existing, registry=registry)
    return _register(
        name=sender_name,
        template_id="inbox",
        space_id=target_space,
        description="Gateway-managed passive sender for service-event sends.",
        start=True,
    )


def _status_payload(*, activity_limit: int = 10, include_hidden: bool = False) -> dict:
    from . import gateway as _gateway_cmd

    _with_refs = getattr(_gateway_cmd, "_with_registry_refs")
    _alerts = getattr(_gateway_cmd, "_gateway_alerts", _gateway_alerts)

    daemon = daemon_status()
    ui = ui_status()
    session = load_gateway_session()
    registry = daemon["registry"]
    all_agents = [
        _with_refs(registry, annotate_runtime_health(agent, registry=registry)) for agent in registry.get("agents", [])
    ]
    # Partition out archived + hidden + system agents so default surfaces
    # stay tidy. System agents (switchboards, service accounts) are
    # infrastructure plumbing; hidden agents are stale ones the daemon swept
    # away; archived agents are user-disabled entries that are sticky.
    archived_agents_list = [a for a in all_agents if str(a.get("lifecycle_phase") or "active") == "archived"]
    hidden_agents_list = [a for a in all_agents if str(a.get("lifecycle_phase") or "active") == "hidden"]
    system_agents_list = [a for a in all_agents if _is_system_agent(a)]
    visible_agents = [
        a
        for a in all_agents
        if a not in archived_agents_list and a not in hidden_agents_list and a not in system_agents_list
    ]
    agents = all_agents if include_hidden else visible_agents
    approvals = list_gateway_approvals()
    pending_approvals = [item for item in approvals if str(item.get("status") or "") == "pending"]
    live_agents = [a for a in agents if str(a.get("mode") or "") == "LIVE"]
    on_demand_agents = [a for a in agents if str(a.get("mode") or "") == "ON-DEMAND"]
    inbox_agents = [a for a in agents if str(a.get("mode") or "") == "INBOX"]
    connected_agents = [a for a in agents if bool(a.get("connected"))]
    stale_agents = [a for a in agents if str(a.get("presence") or "") == "STALE"]
    offline_agents = [a for a in agents if str(a.get("presence") or "") == "OFFLINE"]
    errored_agents = [a for a in agents if str(a.get("presence") or "") == "ERROR"]
    low_confidence_agents = [a for a in agents if str(a.get("confidence") or "") in {"LOW", "BLOCKED"}]
    blocked_agents = [a for a in agents if str(a.get("confidence") or "") == "BLOCKED"]
    gateway = dict(registry.get("gateway", {}))
    if not daemon["running"]:
        gateway["effective_state"] = "stopped"
        gateway["pid"] = None
    # Active space fallback. The gateway session sometimes ships without a
    # space_id (older sessions, sessions minted before we resolved the user's
    # default workspace). Without this, the operator overview shows Space=—
    # even though every managed agent has a space_id. Pick the most-used space
    # across agents as the implicit active space for display.
    fallback_space_id: str | None = None
    if not (session and session.get("space_id")):
        space_counts: dict[str, int] = {}
        for agent in agents:
            sid = str(agent.get("space_id") or "").strip()
            if not sid:
                continue
            space_counts[sid] = space_counts.get(sid, 0) + 1
        if space_counts:
            fallback_space_id = max(space_counts.items(), key=lambda item: item[1])[0]

    payload = {
        "gateway_dir": str(gateway_dir()),
        "gateway_environment": gateway_environment(),
        "connected": bool(session),
        "base_url": session.get("base_url") if session else None,
        "space_id": (session.get("space_id") if session else None) or fallback_space_id,
        "space_name": session.get("space_name") if session else None,
        "user": session.get("username") if session else None,
        "daemon": {
            "running": daemon["running"],
            "pid": daemon["pid"],
        },
        "ui": {
            "running": ui["running"],
            "pid": ui["pid"],
            "host": ui["host"],
            "port": ui["port"],
            "url": ui["url"],
            "log_path": ui["log_path"],
        },
        "gateway": gateway,
        "agents": agents,
        "approvals": approvals,
        "recent_activity": load_recent_gateway_activity(limit=activity_limit),
        "summary": {
            "managed_agents": len(agents),
            "live_agents": len(live_agents),
            "on_demand_agents": len(on_demand_agents),
            "inbox_agents": len(inbox_agents),
            "connected_agents": len(connected_agents),
            "stale_agents": len(stale_agents),
            "offline_agents": len(offline_agents),
            "errored_agents": len(errored_agents),
            "low_confidence_agents": len(low_confidence_agents),
            "blocked_agents": len(blocked_agents),
            "hidden_agents": len(hidden_agents_list),
            "system_agents": len(system_agents_list),
            "archived_agents": len(archived_agents_list),
            "pending_approvals": len(pending_approvals),
        },
    }
    alerts = _alerts(payload)
    payload["alerts"] = alerts
    payload["summary"]["alert_count"] = len(alerts)
    return payload


def _gateway_alerts(payload: dict, *, limit: int = 6) -> list[dict]:
    alerts: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def push(severity: str, title: str, detail: str, *, agent_name: str | None = None) -> None:
        key = (severity, title, agent_name or "")
        if key in seen:
            return
        seen.add(key)
        alerts.append(
            {
                "severity": severity,
                "title": title,
                "detail": detail,
                "agent_name": agent_name,
            }
        )

    if not payload.get("connected"):
        push("error", "Gateway is not logged in", "Run `ax gateway login` to bootstrap the local control plane.")
    elif not payload.get("daemon", {}).get("running"):
        push(
            "error",
            "Gateway daemon is stopped",
            "Start it with `uv run ax gateway start` or relaunch the local service.",
        )

    if not payload.get("ui", {}).get("running"):
        push(
            "warning", "Gateway UI is stopped", "Start it with `uv run ax gateway start` to launch the local dashboard."
        )

    for agent in payload.get("agents", []):
        name = str(agent.get("name") or "")
        presence = str(agent.get("presence") or "").upper()
        approval_state = str(agent.get("approval_state") or "").lower()
        attestation_state = str(agent.get("attestation_state") or "").lower()
        preview = str(agent.get("last_reply_preview") or "")
        lowered_preview = preview.lower()
        setup_error_preview = (
            preview.startswith("(stderr:")
            or " repo not found" in lowered_preview
            or lowered_preview.startswith("ollama bridge failed:")
        )
        if approval_state == "pending":
            detail = str(agent.get("confidence_detail") or "Gateway needs approval before this runtime can be trusted.")
            push("warning", f"@{name} needs Gateway approval", detail, agent_name=name)
        elif approval_state == "rejected" or attestation_state == "blocked":
            detail = str(agent.get("confidence_detail") or "Gateway blocked this runtime.")
            push("error", f"@{name} is blocked by Gateway", detail, agent_name=name)
        elif attestation_state == "drifted":
            detail = str(agent.get("confidence_detail") or "Runtime changed since approval and needs review.")
            push("warning", f"@{name} changed since approval", detail, agent_name=name)
        elif presence == "BLOCKED":
            detail = str(
                agent.get("confidence_detail")
                or "Gateway blocked this runtime until identity, space, or approval state is fixed."
            )
            push("error", f"@{name} is blocked", detail, agent_name=name)
        elif presence == "ERROR":
            if setup_error_preview:
                push("error", f"@{name} has a runtime setup error", preview[:180], agent_name=name)
            else:
                detail = str(agent.get("confidence_detail") or agent.get("last_error") or "Runtime reported an error.")
                push("error", f"@{name} hit an error", detail, agent_name=name)
        elif presence == "STALE":
            detail = f"No heartbeat for {_format_age(agent.get('last_seen_age_seconds'))}."
            push("warning", f"@{name} looks stale", detail, agent_name=name)
        elif presence == "OFFLINE" and str(agent.get("mode") or "") == "LIVE":
            detail = str(
                agent.get("confidence_detail")
                or "Expected a live runtime, but Gateway does not currently have a working path."
            )
            push("warning", f"@{name} is offline", detail, agent_name=name)
        if setup_error_preview and presence != "ERROR":
            push("error", f"@{name} has a runtime setup error", preview[:180], agent_name=name)
        if int(agent.get("backlog_depth") or 0) > 0 and presence in {"OFFLINE", "ERROR", "STALE"}:
            detail = f"{agent.get('backlog_depth')} queued item(s) may be stuck until the agent is healthy."
            push("warning", f"@{name} has queued work", detail, agent_name=name)

    for item in reversed(payload.get("recent_activity", [])):
        event = str(item.get("event") or "")
        if event == "gateway_start_blocked":
            existing = item.get("existing_pid") or item.get("existing_pids")
            push("warning", "Another Gateway instance is already running", f"Existing process: {existing}.")
        elif event in {"listener_error", "listener_timeout"}:
            agent_name = str(item.get("agent_name") or "")
            detail = str(item.get("error") or "Listener lost contact and is reconnecting.")
            push("warning", f"@{agent_name} had a listener interruption", detail, agent_name=agent_name or None)
        if len(alerts) >= limit:
            break

    return alerts[:limit]


def _agent_detail_payload(name: str, *, activity_limit: int = 12) -> dict | None:
    from . import gateway as _gateway_cmd

    _status = getattr(_gateway_cmd, "_status_payload", _status_payload)

    payload = _status(activity_limit=activity_limit)
    entry = next((agent for agent in payload["agents"] if str(agent.get("name") or "").lower() == name.lower()), None)
    if not entry:
        return None
    activity = load_recent_gateway_activity(limit=activity_limit, agent_name=name)
    return {
        "gateway": {
            "connected": payload["connected"],
            "base_url": payload["base_url"],
            "space_id": payload["space_id"],
            "daemon": payload["daemon"],
        },
        "agent": entry,
        "recent_activity": activity,
    }


def _approval_rows_payload(*, status: str | None = None, include_archived: bool = False) -> dict:
    approvals = list_gateway_approvals(status=status, include_archived=include_archived)
    return {
        "approvals": approvals,
        "count": len(approvals),
        "pending": len([item for item in approvals if str(item.get("status") or "") == "pending"]),
    }


def _approval_detail_payload(approval_id: str) -> dict:
    approval = get_gateway_approval(approval_id)
    return {"approval": approval}


def _recommended_test_message(entry: dict) -> str:
    template_id = str(entry.get("template_id") or "").strip()
    if template_id:
        try:
            template = agent_template_definition(template_id)
            message = str(template.get("recommended_test_message") or "").strip()
            if message:
                return message
        except KeyError:
            pass
    runtime_type = str(entry.get("runtime_type") or "").lower()
    if runtime_type == "echo":
        return "gateway test ping"
    if runtime_type == "inbox":
        return "Queue this test job, mark it received, and do not reply inline."
    return "Reply with exactly: Gateway test OK."


def _send_gateway_test_to_managed_agent(
    name: str,
    *,
    content: str | None = None,
    author: str = "agent",
    sender_agent: str | None = None,
) -> dict:
    """Send a Gateway-brokered test message to a managed agent.

    Default sender = invoking principal resolved from the workspace's local
    Gateway config (per Madtank/supervisor 2026-05-02: principal-invoked
    surfaces author as user/agent, never as a service account). Pass an
    explicit `sender_agent` to author as a named service account or other
    Gateway-managed identity. Fails hard when no invoking principal resolves
    AND no `sender_agent` override is provided — the alternative is silent
    misattribution, which is the bug this signature replaces.
    """
    from . import gateway as _gateway_cmd

    _load_or_exit = getattr(_gateway_cmd, "_load_managed_agent_or_exit")
    _send = getattr(_gateway_cmd, "_send_from_managed_agent")
    _load_user_client = getattr(_gateway_cmd, "_load_gateway_user_client")
    _resolve_principal = getattr(_gateway_cmd, "_resolve_invoking_principal")
    _no_principal = getattr(_gateway_cmd, "_no_invoking_principal_error")

    entry = _load_or_exit(name)
    if str(entry.get("desired_state") or "").strip().lower() == "stopped":
        raise ValueError(f"@{name} is stopped. Start it before sending a test.")
    registry = load_gateway_registry()
    stored = find_agent_entry(registry, str(entry.get("name") or "")) or entry
    ensure_gateway_identity_binding(registry, stored, session=load_gateway_session())
    snapshot = annotate_runtime_health(stored, registry=registry)
    save_gateway_registry(registry)
    reachability = str(snapshot.get("reachability") or "").strip().lower()
    if reachability == "sse_disconnected":
        raise ValueError(
            f"@{name} is attached but the platform SSE subscription is down — "
            "messages will not be delivered. Reload the MCP server or restart the gateway to reconnect."
        )
    if reachability == "attach_required":
        workdir = str(snapshot.get("workdir") or stored.get("workdir") or "").strip()
        suffix = f" Start Claude Code from {workdir}." if workdir else " Start Claude Code first."
        raise ValueError(f"@{name} is stopped and cannot receive messages yet.{suffix}")
    space_id = str(snapshot.get("active_space_id") or stored.get("space_id") or entry.get("space_id") or "")
    if not space_id:
        raise ValueError(f"Managed agent is missing a space id: @{name}")

    prompt = (content or "").strip() or _recommended_test_message(entry)
    target = str(entry.get("name") or "").lstrip("@")
    normalized_author = str(author or "agent").strip().lower()
    if normalized_author not in {"agent", "user"}:
        raise ValueError("Gateway test author must be one of: agent, user.")

    sender_name = None
    if normalized_author == "agent":
        if sender_agent:
            sender_name = str(sender_agent).strip()
        else:
            sender_name = _resolve_principal()
            if not sender_name:
                raise _no_principal()
        result = _send(
            name=sender_name,
            content=prompt,
            to=target,
            space_id=space_id,
            sent_via="gateway_test",
            metadata_extra={
                "managed_target": True,
                "target_agent_name": stored.get("name"),
                "target_agent_id": stored.get("agent_id"),
                "target_template": stored.get("template_id"),
                "target_runtime_type": stored.get("runtime_type"),
                "test_author": "agent",
                "test_sender_explicit": bool(sender_agent),
            },
        )
        payload = result.get("message", result) if isinstance(result, dict) else result
        message_content = str(result.get("content") or f"@{target} {prompt}".strip())
    else:
        client = _load_user_client()
        message_content = f"@{target} {prompt}".strip()
        metadata = {
            "control_plane": "gateway",
            "gateway": {
                "managed_target": True,
                "target_agent_name": stored.get("name"),
                "target_agent_id": stored.get("agent_id"),
                "target_template": stored.get("template_id"),
                "target_runtime_type": stored.get("runtime_type"),
                "sent_via": "gateway_test",
                "test_author": "user",
            },
        }
        result = client.send_message(space_id, message_content, metadata=metadata)
        payload = result.get("message", result) if isinstance(result, dict) else result

    if isinstance(payload, dict):
        record_gateway_activity(
            "gateway_test_sent",
            entry=entry,
            message_id=payload.get("id"),
            reply_preview=message_content[:120] or None,
            sender_agent_name=sender_name,
            test_author=normalized_author,
        )
    return {
        "target_agent": entry.get("name"),
        "sender_agent": sender_name,
        "author": normalized_author,
        "message": payload,
        "content": message_content,
        "recommended_prompt": prompt,
    }


def _doctor_result_status(checks: list[dict]) -> str:
    statuses = {str(item.get("status") or "").strip().lower() for item in checks}
    if "failed" in statuses:
        return "failed"
    if "warning" in statuses:
        return "warning"
    return "passed"


def _doctor_summary(checks: list[dict], status: str) -> str:
    failures = [
        str(item.get("detail") or item.get("name") or "").strip()
        for item in checks
        if str(item.get("status") or "").strip().lower() == "failed"
    ]
    warnings = [
        str(item.get("detail") or item.get("name") or "").strip()
        for item in checks
        if str(item.get("status") or "").strip().lower() == "warning"
    ]
    if status == "failed" and failures:
        return failures[0]
    if status == "warning" and warnings:
        return warnings[0]
    return "Gateway path looks healthy."


def _store_doctor_result(name: str, result: dict[str, object]) -> dict:
    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")
    completed_at = str(result.get("completed_at") or datetime.now(timezone.utc).isoformat())
    entry["last_doctor_result"] = result
    entry["last_doctor_at"] = completed_at
    if str(result.get("status") or "").lower() != "failed":
        entry["last_successful_doctor_at"] = completed_at
    save_gateway_registry(registry)
    record_gateway_activity(
        "doctor_completed",
        entry=entry,
        activity_message=str(result.get("summary") or ""),
        error=None if str(result.get("status") or "").lower() != "failed" else str(result.get("summary") or ""),
    )
    return annotate_runtime_health(entry, registry=registry)


def _run_gateway_doctor(name: str, *, send_test: bool = False) -> dict:
    from . import gateway as _gateway_cmd

    _send_test_fn = getattr(_gateway_cmd, "_send_gateway_test_to_managed_agent", _send_gateway_test_to_managed_agent)
    _store = getattr(_gateway_cmd, "_store_doctor_result", _store_doctor_result)

    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")
    ensure_gateway_identity_binding(registry, entry, session=load_gateway_session(), verify_spaces=False)
    snapshot = annotate_runtime_health(entry, registry=registry)
    checks: list[dict[str, str]] = []
    asset_class = str(snapshot.get("asset_class") or "")
    intake_model = str(snapshot.get("intake_model") or "")
    return_paths = [str(item) for item in (snapshot.get("return_paths") or []) if str(item)]

    def add_check(check_name: str, status: str, detail: str) -> None:
        checks.append({"name": check_name, "status": status, "detail": detail})

    def has_check(check_name: str) -> bool:
        return any(str(item.get("name") or "") == check_name for item in checks)

    session = load_gateway_session()
    add_check(
        "gateway_auth",
        "passed" if session else "failed",
        "Gateway bootstrap session is present." if session else "Gateway is not logged in.",
    )

    identity_status = str(snapshot.get("identity_status") or "").lower()
    if identity_status == "verified":
        add_check(
            "identity_binding",
            "passed",
            f"Gateway is acting as {snapshot.get('acting_agent_name') or entry.get('name')}.",
        )
    elif identity_status == "bootstrap_only":
        add_check(
            "identity_binding",
            "failed",
            "Gateway would need to use a bootstrap credential for an agent-authored action.",
        )
    else:
        add_check(
            "identity_binding",
            "failed",
            str(snapshot.get("confidence_detail") or "Gateway does not have a valid acting identity binding."),
        )

    environment_status = str(snapshot.get("environment_status") or "").lower()
    if environment_status == "environment_allowed":
        add_check(
            "environment_binding",
            "passed",
            f"Requested environment matches {snapshot.get('environment_label') or snapshot.get('base_url') or entry.get('base_url')}.",
        )
    elif environment_status == "environment_mismatch":
        add_check(
            "environment_binding",
            "failed",
            str(snapshot.get("confidence_detail") or "Requested environment does not match the bound environment."),
        )
    else:
        add_check("environment_binding", "warning", "Gateway could not fully verify the bound environment.")

    allowed_spaces = snapshot.get("allowed_spaces") if isinstance(snapshot.get("allowed_spaces"), list) else []
    if allowed_spaces:
        add_check("allowed_spaces", "passed", f"Gateway resolved {len(allowed_spaces)} allowed space(s).")
    else:
        add_check("allowed_spaces", "warning", "Gateway does not have a cached allowed-space list yet.")

    space_status = str(snapshot.get("space_status") or "").lower()
    if space_status == "active_allowed":
        add_check(
            "space_binding",
            "passed",
            f"Active space is {snapshot.get('active_space_name') or snapshot.get('active_space_id')}.",
        )
    elif space_status == "no_active_space":
        add_check("space_binding", "failed", "Gateway does not have an active space selected for this asset.")
    elif space_status == "active_not_allowed":
        add_check(
            "space_binding",
            "failed",
            str(snapshot.get("confidence_detail") or "Active space is not allowed for this identity."),
        )
    else:
        add_check("space_binding", "warning", "Gateway could not fully verify the active space.")

    attestation_state = str(snapshot.get("attestation_state") or "").lower()
    approval_state = str(snapshot.get("approval_state") or "").lower()
    if approval_state == "pending":
        add_check(
            "binding_approval",
            "warning",
            str(snapshot.get("confidence_detail") or "Gateway needs approval before trusting this runtime binding."),
        )
    elif approval_state == "rejected" or attestation_state == "blocked":
        add_check(
            "binding_approval",
            "failed",
            str(snapshot.get("confidence_detail") or "Gateway blocked this runtime binding."),
        )
    elif attestation_state == "drifted":
        add_check(
            "binding_attestation",
            "failed",
            str(snapshot.get("confidence_detail") or "Runtime binding drifted from its approved launch spec."),
        )
    elif attestation_state == "verified":
        add_check("binding_attestation", "passed", "Runtime matches the approved local binding.")

    token_file = Path(str(entry.get("token_file") or "")).expanduser()
    if token_file.exists() and token_file.read_text().strip():
        add_check("agent_token", "passed", "Managed agent token file is present.")
    else:
        add_check("agent_token", "failed", f"Managed agent token is missing or empty at {token_file}.")

    if asset_class == "background_worker" or intake_model == "queue_accept":
        probe = agent_dir(name) / ".doctor-queue-check"
        try:
            probe.write_text("ok\n")
            probe.unlink(missing_ok=True)
            add_check("queue_writable", "passed", "Gateway queue is writable.")
        except OSError as exc:
            add_check("queue_writable", "failed", f"Gateway queue is not writable: {exc}")
        if bool(snapshot.get("connected")):
            add_check("worker_attached", "passed", "A queue worker is attached.")
        else:
            add_check("worker_attached", "warning", "Queue writable; no worker currently attached.")
        if "summary_post" in return_paths:
            add_check("summary_path", "passed", "Gateway is configured to post a summary after queued work completes.")
    else:
        exec_command = str(entry.get("exec_command") or "").strip()
        runtime_type = str(entry.get("runtime_type") or "").strip().lower()
        if intake_model == "live_listener":
            if snapshot.get("activation") == "attach_only":
                reachability_val = str(snapshot.get("reachability") or "")
                if reachability_val == "sse_disconnected":
                    add_check(
                        "claude_code_session",
                        "passed",
                        "Claude Code is attached to Gateway.",
                    )
                    add_check(
                        "channel_sse",
                        "failed",
                        "Claude Code is attached but the platform SSE subscription is down — "
                        "messages will not be delivered. Reload the MCP server or restart the gateway to reconnect.",
                    )
                elif reachability_val == "attach_required":
                    add_check("claude_code_session", "warning", "Start Claude Code before sending.")
                elif bool(snapshot.get("connected")):
                    add_check("claude_code_session", "passed", "Claude Code is connected to Gateway.")
                    add_check("channel_sse", "passed", "Platform SSE subscription is active.")
                else:
                    add_check("claude_code_session", "failed", "Gateway does not currently have Claude Code running.")
            elif runtime_type != "echo":
                if exec_command:
                    add_check("runtime_launch", "passed", "Gateway has a launch command for this runtime.")
                else:
                    add_check("runtime_launch", "failed", "Gateway does not have a launch command for this runtime.")
        elif intake_model == "launch_on_send":
            if runtime_type == "echo" or exec_command:
                add_check("launch_ready", "passed", "Gateway can launch this runtime when work arrives.")
            else:
                add_check(
                    "launch_ready", "failed", "Gateway does not have a launch command for this on-demand runtime."
                )
        elif intake_model == "scheduled_run":
            add_check(
                "schedule_ready",
                "warning",
                "Scheduled asset support is taxonomy-defined but not fully implemented in Gateway yet.",
            )
        elif intake_model == "event_triggered":
            add_check(
                "event_source",
                "warning",
                "Alert-driven asset support is taxonomy-defined but not fully implemented in Gateway yet.",
            )
        elif asset_class == "service_proxy":
            if exec_command:
                add_check("runtime_launch", "passed", "Gateway has a launch command for this runtime.")
            else:
                add_check("runtime_launch", "failed", "Gateway does not have a launch command for this runtime.")

    template_id = str(entry.get("template_id") or "").strip().lower()
    if template_id == "hermes":
        hermes_status = hermes_setup_status(entry)
        if hermes_status.get("ready", True):
            add_check("hermes_repo", "passed", str(hermes_status.get("summary") or "Hermes checkout found."))
        else:
            add_check("hermes_repo", "failed", str(hermes_status.get("summary") or "Hermes checkout not found."))
    elif template_id == "ollama":
        ollama_model = str(entry.get("ollama_model") or "").strip()
        ollama_status = ollama_setup_status(preferred_model=ollama_model or None)
        if bool(ollama_status.get("server_reachable")):
            add_check("ollama_server", "passed", str(ollama_status.get("summary") or "Ollama server is reachable."))
        else:
            add_check("ollama_server", "failed", str(ollama_status.get("summary") or "Ollama server is not reachable."))
        if ollama_model:
            if bool(ollama_status.get("preferred_model_available")):
                add_check("ollama_model", "passed", f"Gateway will launch Ollama with model {ollama_model}.")
            else:
                add_check("ollama_model", "failed", f"Configured Ollama model is not installed: {ollama_model}.")
        else:
            recommended_model = str(ollama_status.get("recommended_model") or "").strip()
            if recommended_model:
                add_check(
                    "ollama_model", "passed", f"Gateway will use the recommended local model {recommended_model}."
                )
            else:
                add_check("ollama_model", "warning", "No Ollama model is selected yet.")
        add_check("launch_path", "passed", "Gateway can launch the Ollama bridge on send.")

    runtime_type = str(entry.get("runtime_type") or "").strip().lower()
    if runtime_type == "hermes_plugin":
        # Two distinct failure modes silently break the Hermes plugin path,
        # and each presents identically (agent shows running, no replies).
        # Surface them as separate checks so the operator can tell which
        # broke without source-diving.
        try:
            hermes_home = _hermes_plugin_home(entry)
            plugin_link = hermes_home / "plugins" / "ax"
            plugin_source = _plugin_source_dir()
            if plugin_link.is_symlink() and plugin_link.resolve() == plugin_source.resolve():
                add_check(
                    "ax_platform_symlink",
                    "passed",
                    f"{plugin_link} → {plugin_source} (Hermes can load the aX adapter).",
                )
            elif plugin_link.exists():
                add_check(
                    "ax_platform_symlink",
                    "warning",
                    f"{plugin_link} exists but does not resolve to {plugin_source}. "
                    f"Delete it; Gateway will re-link on the next start.",
                )
            else:
                add_check(
                    "ax_platform_symlink",
                    "failed",
                    f"{plugin_link} is missing. Run `ax gateway agents start {entry.get('name') or '<name>'}` "
                    f"to trigger the scaffold.",
                )
        except Exception as exc:
            add_check("ax_platform_symlink", "warning", f"Could not inspect plugin symlink: {exc}")

        try:
            hermes_home = _hermes_plugin_home(entry)
            cfg_path = hermes_home / "config.yaml"
            if cfg_path.exists():
                import yaml as _yaml  # local — gateway import cost

                try:
                    loaded = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    loaded = None
                    parse_error = exc
                else:
                    parse_error = None
                if not isinstance(loaded, dict):
                    if parse_error is not None:
                        add_check(
                            "ax_platform_enabled",
                            "failed",
                            f"{cfg_path} did not parse as YAML: {parse_error}",
                        )
                    else:
                        add_check(
                            "ax_platform_enabled",
                            "failed",
                            f"{cfg_path} is not a YAML mapping.",
                        )
                else:
                    plugins_cfg = loaded.get("plugins")
                    enabled_list = plugins_cfg.get("enabled") if isinstance(plugins_cfg, dict) else None
                    if isinstance(enabled_list, list) and AX_PLUGIN_NAME in enabled_list:
                        add_check(
                            "ax_platform_enabled",
                            "passed",
                            f"`plugins.enabled` contains `{AX_PLUGIN_NAME}` (Hermes will load the adapter).",
                        )
                    else:
                        add_check(
                            "ax_platform_enabled",
                            "failed",
                            f"`plugins.enabled` in {cfg_path} does not contain `{AX_PLUGIN_NAME}`. "
                            f"Hermes is opt-in for user plugins; without this the runtime comes up "
                            f"but logs `No messaging platforms enabled` and never replies.",
                        )
            else:
                add_check(
                    "ax_platform_enabled",
                    "failed",
                    f"{cfg_path} is missing. Run `ax gateway agents start {entry.get('name') or '<name>'}` "
                    f"to trigger the scaffold.",
                )
        except Exception as exc:
            add_check("ax_platform_enabled", "warning", f"Could not inspect per-agent config.yaml: {exc}")

    if str(snapshot.get("mode") or "") == "LIVE":
        if str(snapshot.get("presence") or "") == "IDLE":
            add_check("live_path", "passed", "Live listener is connected.")
        elif str(snapshot.get("reachability") or "") == "sse_disconnected":
            pass  # channel_sse check covers this with a more specific message
        elif str(snapshot.get("reachability") or "") == "attach_required":
            add_check("live_path", "warning", "Start Claude Code before sending.")
        elif str(snapshot.get("presence") or "") in {"STALE", "OFFLINE"}:
            add_check("live_path", "failed", str(snapshot.get("confidence_detail") or _reachability_copy(snapshot)))
    elif str(snapshot.get("mode") or "") == "ON-DEMAND" and not has_check("launch_ready"):
        add_check("launch_ready", "passed", "Gateway can launch this runtime on send.")

    if send_test:
        try:
            sent = _send_test_fn(name)
            message_id = None
            if isinstance(sent.get("message"), dict):
                message_id = sent["message"].get("id")
            add_check("test_send", "passed", f"Gateway test message sent{f' ({message_id})' if message_id else ''}.")
        except Exception as exc:
            add_check("test_send", "failed", f"Gateway test send failed: {exc}")

    status = _doctor_result_status(checks)
    completed_at = datetime.now(timezone.utc).isoformat()
    result = {
        "status": status,
        "completed_at": completed_at,
        "checks": checks,
        "summary": _doctor_summary(checks, status),
    }
    annotated = _store(name, result)
    return {
        "name": name,
        "status": status,
        "completed_at": completed_at,
        "summary": result["summary"],
        "checks": checks,
        "agent": annotated,
    }


def _parse_iso8601(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(value: object) -> int | None:
    parsed = _parse_iso8601(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))


def _format_age(seconds: object) -> str:
    if seconds is None:
        return "-"
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "-"
    if total < 60:
        return f"{total}s"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def _format_timestamp(value: object) -> str:
    return _format_age(_age_seconds(value))


def _state_text(state: object) -> Text:
    label = str(state or "unknown").lower()
    style = _STATE_STYLES.get(label, "white")
    return Text(f"● {label}", style=style)


def _presence_text(presence: object) -> Text:
    label = str(presence or "OFFLINE").upper()
    style = _PRESENCE_STYLES.get(label, "white")
    return Text(label, style=style)


def _confidence_text(confidence: object) -> Text:
    label = str(confidence or "MEDIUM").upper()
    style = _CONFIDENCE_STYLES.get(label, "white")
    return Text(label, style=style)


def _mode_text(mode: object) -> Text:
    label = str(mode or "ON-DEMAND").upper()
    style = {
        "LIVE": "green",
        "ON-DEMAND": "cyan",
        "INBOX": "blue",
    }.get(label, "white")
    return Text(label, style=style)


def _reply_text(reply: object) -> Text:
    label = str(reply or "REPLY").upper()
    style = {
        "REPLY": "green",
        "SUMMARY": "yellow",
        "SILENT": "dim",
    }.get(label, "white")
    return Text(label, style=style)


def _reachability_copy(agent: dict) -> str:
    reachability = str(agent.get("reachability") or "unavailable")
    mode = str(agent.get("mode") or "")
    if reachability == "live_now":
        return "Live listener ready to claim work."
    if reachability == "queue_available":
        return "Gateway can safely queue work now."
    if reachability == "launch_available":
        return "Gateway can launch this runtime on send."
    if reachability == "sse_disconnected":
        return "Claude Code is attached but the SSE subscription is down — messages will not be delivered."
    if reachability == "attach_required":
        return "Start Claude Code before sending."
    if mode == "INBOX":
        return "Queue path is unavailable."
    return "Gateway does not currently have a working path."


def _agent_template_label(agent: dict) -> str:
    return str(agent.get("template_label") or agent.get("runtime_type") or "-")


def _agent_type_label(agent: dict) -> str:
    return str(agent.get("asset_type_label") or "Connected Asset")


def _agent_output_label(agent: dict) -> str:
    return str(agent.get("output_label") or agent.get("reply") or "Reply")


def _metric_panel(label: str, value: object, *, tone: str = "cyan", subtitle: str | None = None) -> Panel:
    body = Text()
    body.append(str(value), style=f"bold {tone}")
    body.append(f"\n{label}", style="dim")
    if subtitle:
        body.append(f"\n{subtitle}", style="dim")
    return Panel(body, border_style=tone, padding=(1, 2))


def _sorted_agents(agents: list[dict]) -> list[dict]:
    return sorted(
        agents,
        key=lambda agent: (
            _PRESENCE_ORDER.get(str(agent.get("presence") or "").upper(), 99),
            str(agent.get("name") or "").lower(),
        ),
    )


def _render_gateway_overview(payload: dict) -> Panel:
    gateway = payload.get("gateway") or {}
    ui = payload.get("ui") or {}
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column(ratio=2)
    grid.add_column(style="bold")
    grid.add_column(ratio=2)
    grid.add_row(
        "Gateway",
        str(gateway.get("gateway_id") or "-")[:8],
        "Daemon",
        "running" if payload["daemon"]["running"] else "stopped",
    )
    grid.add_row("User", str(payload.get("user") or "-"), "Base URL", str(payload.get("base_url") or "-"))
    space_label = str(payload.get("space_name") or payload.get("space_id") or "-")
    grid.add_row("Space", space_label, "Environment", str(payload.get("gateway_environment") or "default"))
    grid.add_row("PID", str(payload["daemon"].get("pid") or "-"), "State Dir", str(payload.get("gateway_dir") or "-"))
    grid.add_row("UI", str(ui.get("url") or "-"), "UI PID", str(ui.get("pid") or "-"))
    grid.add_row(
        "Session",
        "connected" if payload.get("connected") else "disconnected",
        "Last Reconcile",
        _format_timestamp(gateway.get("last_reconcile_at")),
    )
    return Panel(grid, title="Gateway Overview", border_style="cyan")


def _render_agent_table(agents: list[dict]) -> Table:
    table = Table(expand=True, box=box.SIMPLE_HEAVY)
    table.add_column("Agent", style="bold")
    table.add_column("Type")
    table.add_column("Mode")
    table.add_column("Presence")
    table.add_column("Output")
    table.add_column("Confidence")
    table.add_column("Acting As")
    table.add_column("Current Space")
    table.add_column("Queue", justify="right")
    table.add_column("Seen", justify="right")
    table.add_column("Activity", overflow="fold")
    if not agents:
        table.add_row(
            "No managed agents",
            "-",
            Text("ON-DEMAND", style="dim"),
            Text("OFFLINE", style="dim"),
            Text("Reply", style="dim"),
            Text("MEDIUM", style="dim"),
            "-",
            "-",
            "0",
            "-",
            "-",
        )
        return table
    for agent in _sorted_agents(agents):
        activity = str(
            agent.get("current_activity")
            or agent.get("confidence_detail")
            or agent.get("current_tool")
            or agent.get("last_reply_preview")
            or "-"
        )
        table.add_row(
            f"@{agent.get('name')}",
            _agent_type_label(agent),
            _mode_text(agent.get("mode")),
            _presence_text(agent.get("presence")),
            Text(
                _agent_output_label(agent),
                style="green" if str(agent.get("output_label") or "").lower() == "reply" else "yellow",
            ),
            _confidence_text(agent.get("confidence")),
            str(agent.get("acting_agent_name") or agent.get("name") or "-"),
            str(agent.get("active_space_name") or agent.get("active_space_id") or agent.get("space_id") or "-"),
            str(agent.get("backlog_depth") or 0),
            _format_age(agent.get("last_seen_age_seconds")),
            activity,
        )
    return table


def _render_activity_table(activity: list[dict]) -> Table:
    table = Table(expand=True, box=box.SIMPLE_HEAVY)
    table.add_column("When", justify="right", no_wrap=True)
    table.add_column("Event", no_wrap=True)
    table.add_column("Agent", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    if not activity:
        table.add_row("-", "idle", "-", "No activity yet")
        return table
    for item in activity:
        detail = (
            item.get("activity_message")
            or item.get("reply_preview")
            or item.get("tool_name")
            or item.get("error")
            or item.get("message_id")
            or "-"
        )
        agent_name = item.get("agent_name")
        table.add_row(
            _format_timestamp(item.get("ts")),
            str(item.get("event") or "-"),
            f"@{agent_name}" if agent_name else "-",
            str(detail),
        )
    return table


def _render_alert_table(alerts: list[dict]) -> Table:
    table = Table(expand=True, box=box.SIMPLE_HEAVY)
    table.add_column("Level", no_wrap=True)
    table.add_column("Alert", no_wrap=True)
    table.add_column("Agent", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    if not alerts:
        table.add_row("info", "No active alerts", "-", "Gateway looks healthy.")
        return table
    for item in alerts:
        severity = str(item.get("severity") or "info").lower()
        style = {"error": "red", "warning": "yellow", "info": "cyan"}.get(severity, "white")
        agent_name = str(item.get("agent_name") or "")
        table.add_row(
            Text(severity, style=style),
            str(item.get("title") or "-"),
            f"@{agent_name}" if agent_name else "-",
            str(item.get("detail") or "-"),
        )
    return table


def _render_gateway_dashboard(payload: dict) -> Group:
    agents = payload.get("agents", [])
    summary = payload.get("summary", {})
    queue_depth = sum(int(agent.get("backlog_depth") or 0) for agent in agents)
    metrics = Columns(
        [
            _metric_panel("managed agents", summary.get("managed_agents", 0), tone="cyan"),
            _metric_panel("live", summary.get("live_agents", 0), tone="green"),
            _metric_panel("on-demand", summary.get("on_demand_agents", 0), tone="blue"),
            _metric_panel("inbox", summary.get("inbox_agents", 0), tone="cyan"),
            _metric_panel("pending approvals", summary.get("pending_approvals", 0), tone="yellow"),
            _metric_panel("low confidence", summary.get("low_confidence_agents", 0), tone="yellow"),
            _metric_panel("blocked", summary.get("blocked_agents", 0), tone="red"),
            _metric_panel("queue depth", queue_depth, tone="blue"),
        ],
        expand=True,
        equal=True,
    )
    return Group(
        _render_gateway_overview(payload),
        metrics,
        Panel(_render_alert_table(payload.get("alerts", [])), title="Alerts", border_style="red"),
        Panel(_render_agent_table(agents), title="Managed Agents", border_style="green"),
        Panel(
            _render_activity_table(payload.get("recent_activity", [])), title="Recent Activity", border_style="magenta"
        ),
    )


def _render_agent_detail(entry: dict, *, activity: list[dict]) -> Group:
    overview = Table.grid(expand=True, padding=(0, 2))
    overview.add_column(style="bold")
    overview.add_column(ratio=2)
    overview.add_column(style="bold")
    overview.add_column(ratio=2)
    overview.add_row("Agent", f"@{entry.get('name')}", "Type", _agent_type_label(entry))
    overview.add_row("Template", _agent_template_label(entry), "Output", _agent_output_label(entry))
    overview.add_row("Mode", str(entry.get("mode") or "-"), "Presence", str(entry.get("presence") or "-"))
    overview.add_row("Reply", str(entry.get("reply") or "-"), "Confidence", str(entry.get("confidence") or "-"))
    overview.add_row(
        "Asset Class", str(entry.get("asset_class") or "-"), "Intake", str(entry.get("intake_model") or "-")
    )
    overview.add_row(
        "Trigger",
        str((entry.get("trigger_sources") or [None])[0] or "-"),
        "Return",
        str((entry.get("return_paths") or [None])[0] or "-"),
    )
    overview.add_row(
        "Telemetry", str(entry.get("telemetry_shape") or "-"), "Worker", str(entry.get("worker_model") or "-")
    )
    overview.add_row(
        "Attestation", str(entry.get("attestation_state") or "-"), "Approval", str(entry.get("approval_state") or "-")
    )
    overview.add_row(
        "Acting As", str(entry.get("acting_agent_name") or "-"), "Identity", str(entry.get("identity_status") or "-")
    )
    overview.add_row(
        "Environment",
        str(entry.get("environment_label") or entry.get("base_url") or "-"),
        "Env Status",
        str(entry.get("environment_status") or "-"),
    )
    overview.add_row(
        "Current Space",
        str(entry.get("active_space_name") or entry.get("active_space_id") or "-"),
        "Space Status",
        str(entry.get("space_status") or "-"),
    )
    overview.add_row(
        "Default Space",
        str(entry.get("default_space_name") or entry.get("default_space_id") or "-"),
        "Allowed Spaces",
        str(entry.get("allowed_space_count") or 0),
    )
    overview.add_row(
        "Install", str(entry.get("install_id") or "-"), "Runtime Instance", str(entry.get("runtime_instance_id") or "-")
    )
    overview.add_row("Reachability", _reachability_copy(entry), "Reason", str(entry.get("confidence_reason") or "-"))
    overview.add_row(
        "Desired", str(entry.get("desired_state") or "-"), "Effective", str(entry.get("effective_state") or "-")
    )
    overview.add_row(
        "Connected", "yes" if entry.get("connected") else "no", "Queue", str(entry.get("backlog_depth") or 0)
    )
    overview.add_row(
        "Seen",
        _format_age(entry.get("last_seen_age_seconds")),
        "Reconnect",
        _format_age(entry.get("reconnect_backoff_seconds")),
    )
    overview.add_row(
        "Processed", str(entry.get("processed_count") or 0), "Dropped", str(entry.get("dropped_count") or 0)
    )
    overview.add_row(
        "Last Work",
        _format_timestamp(entry.get("last_work_received_at")),
        "Completed",
        _format_timestamp(entry.get("last_work_completed_at")),
    )
    overview.add_row(
        "Phase", str(entry.get("current_status") or "-"), "Activity", str(entry.get("current_activity") or "-")
    )
    overview.add_row(
        "Tool",
        str(entry.get("current_tool") or "-"),
        "Timeout",
        f"{entry.get('timeout_seconds')}s" if entry.get("timeout_seconds") else "-",
    )
    overview.add_row("Adapter", str(entry.get("runtime_type") or "-"), "Space", str(entry.get("space_id") or "-"))
    overview.add_row(
        "Cred Source", str(entry.get("credential_source") or "-"), "Token", str(entry.get("token_file") or "-")
    )
    overview.add_row(
        "Agent ID", str(entry.get("agent_id") or "-"), "Last Reply", str(entry.get("last_reply_preview") or "-")
    )
    overview.add_row(
        "Last Error",
        str(entry.get("last_error") or "-"),
        "Confidence Detail",
        str(entry.get("confidence_detail") or "-"),
    )
    overview.add_row(
        "Doctor",
        str(entry.get("last_successful_doctor_at") or "-"),
        "Doctor Status",
        str(
            (entry.get("last_doctor_result") or {}).get("status")
            if isinstance(entry.get("last_doctor_result"), dict)
            else "-"
        ),
    )

    paths = Table.grid(expand=True, padding=(0, 2))
    paths.add_column(style="bold")
    paths.add_column(ratio=3)
    paths.add_row("Token File", str(entry.get("token_file") or "-"))
    paths.add_row("Workdir", str(entry.get("workdir") or "-"))
    paths.add_row("Exec", str(entry.get("exec_command") or "-"))
    paths.add_row("Added", _format_timestamp(entry.get("added_at")))

    panels = [
        Panel(overview, title=f"Managed Agent · @{entry.get('name')}", border_style="cyan"),
        Panel(paths, title="Runtime Details", border_style="blue"),
    ]

    operator_prompt = str(entry.get("system_prompt") or "").strip()
    if operator_prompt:
        prompt_panel_body = operator_prompt
    else:
        prompt_panel_body = (
            "(none) — set with: ax gateway agents update "
            f"{entry.get('name') or '<name>'} --system-prompt '<your role instructions>'"
        )
    panels.append(Panel(prompt_panel_body, title="Operator System Prompt", border_style="green"))
    panels.append(Panel(_render_activity_table(activity), title="Recent Agent Activity", border_style="magenta"))

    return Group(*panels)


# ---------------------------------------------------------------------------
# Top-level commands (activity / status) and agents / approvals sub-app
# commands. Registered against the relevant Typer apps in
# ``commands/gateway.py``.
# ---------------------------------------------------------------------------


def activity(
    message_id: str = typer.Option(None, "--message-id", help="Filter to a single source message_id"),
    agent: str = typer.Option(None, "--agent", help="Filter to a single managed agent name"),
    limit: int = typer.Option(0, "--limit", help="Cap rows returned (0 = no cap)"),
    as_json: bool = JSON_OPTION,
):
    """Inspect Gateway-recorded activity for one message or agent.

    Reads the local activity log Gateway already owns
    (``~/.ax/gateway/activity.jsonl``) and emits the rows in chronological
    order. Each row carries the canonical ``phase`` field for any registered
    event so supervisor loops and the aX UI can consume a stable shape across
    runtime types.

    This command is read-only. It does not authenticate to the backend, does
    not construct an ``AxClient``, and does not surface any new credential
    path — Gateway remains the trust boundary.
    """
    import json

    from . import gateway as _gateway_cmd

    _log_path = getattr(_gateway_cmd, "activity_log_path")

    log_path = _log_path()
    rows: list[dict] = []
    if log_path.exists():
        try:
            for raw in log_path.read_text(encoding="utf-8").splitlines():
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    item = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                rows.append(item)
        except OSError:
            rows = []

    msg_filter = (message_id or "").strip()
    agent_filter = (agent or "").strip().lower()
    filtered = []
    for item in rows:
        if msg_filter and str(item.get("message_id") or "") != msg_filter:
            continue
        if agent_filter and str(item.get("agent_name") or "").lower() != agent_filter:
            continue
        filtered.append(item)

    filtered.sort(key=lambda r: str(r.get("ts") or ""))
    if limit and limit > 0:
        filtered = filtered[-limit:]

    if as_json:
        if msg_filter:
            print_json({"message_id": msg_filter, "events": filtered})
        else:
            print_json({"events": filtered})
        return

    if not filtered:
        target = msg_filter or agent_filter or "(any)"
        err_console.print(f"No Gateway activity for {target}.")
        return
    print_table(
        ["Time", "Phase", "Event", "Agent", "Message", "Tool", "Detail"],
        [
            {
                "ts": item.get("ts"),
                "phase": item.get("phase") or "-",
                "event": item.get("event"),
                "agent_name": item.get("agent_name") or "-",
                "message_id": item.get("message_id") or "-",
                "tool_name": item.get("tool_name") or "-",
                "detail": item.get("activity_message") or item.get("reply_preview") or item.get("error") or "",
            }
            for item in filtered
        ],
        keys=["ts", "phase", "event", "agent_name", "message_id", "tool_name", "detail"],
    )


def status(
    as_json: bool = JSON_OPTION,
    show_all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Include hidden (auto-swept stale) and system (switchboard / service-account) agents.",
    ),
):
    """Show Gateway status, daemon state, and managed runtimes."""
    from . import gateway as _gateway_cmd

    _status = getattr(_gateway_cmd, "_status_payload", _status_payload)
    _type_label = getattr(_gateway_cmd, "_agent_type_label", _agent_type_label)
    _output_label = getattr(_gateway_cmd, "_agent_output_label", _agent_output_label)

    payload = _status(include_hidden=show_all)
    if as_json:
        print_json(payload)
        return

    err_console.print("[bold]ax gateway status[/bold]")
    err_console.print(f"  gateway_dir = {payload['gateway_dir']}")
    err_console.print(f"  connected   = {payload['connected']}")
    err_console.print(f"  daemon      = {'running' if payload['daemon']['running'] else 'stopped'}")
    if payload["daemon"]["pid"]:
        err_console.print(f"  pid         = {payload['daemon']['pid']}")
    err_console.print(f"  ui          = {'running' if payload['ui']['running'] else 'stopped'}")
    if payload["ui"]["pid"]:
        err_console.print(f"  ui_pid      = {payload['ui']['pid']}")
    err_console.print(f"  ui_url      = {payload['ui']['url']}")
    err_console.print(f"  base_url    = {payload['base_url']}")
    err_console.print(f"  space_id    = {payload['space_id']}")
    if payload.get("space_name"):
        err_console.print(f"  space_name  = {payload['space_name']}")
    err_console.print(f"  user        = {payload['user']}")
    err_console.print(f"  agents      = {payload['summary']['managed_agents']}")
    err_console.print(f"  live        = {payload['summary']['live_agents']}")
    err_console.print(f"  on_demand   = {payload['summary']['on_demand_agents']}")
    err_console.print(f"  inbox       = {payload['summary']['inbox_agents']}")
    hidden_n = payload["summary"].get("hidden_agents", 0)
    system_n = payload["summary"].get("system_agents", 0)
    if hidden_n or system_n:
        hint = "" if show_all else "  (run with --all to include)"
        err_console.print(f"  hidden      = {hidden_n}{hint}")
        err_console.print(f"  system      = {system_n}")
    err_console.print(f"  alerts      = {payload['summary'].get('alert_count', 0)}")
    err_console.print(f"  approvals   = {payload['summary'].get('pending_approvals', 0)} pending")
    if payload.get("alerts"):
        print_table(
            ["Level", "Alert", "Agent", "Detail"],
            payload["alerts"],
            keys=["severity", "title", "agent_name", "detail"],
        )
    if payload["agents"]:
        print_table(
            [
                "Agent",
                "Type",
                "Mode",
                "Presence",
                "Output",
                "Confidence",
                "Acting As",
                "Current Space",
                "Seen",
                "Backlog",
                "Reason",
            ],
            [{**agent, "type": _type_label(agent), "output": _output_label(agent)} for agent in payload["agents"]],
            keys=[
                "name",
                "type",
                "mode",
                "presence",
                "output",
                "confidence",
                "acting_agent_name",
                "active_space_name",
                "last_seen_age_seconds",
                "backlog_depth",
                "confidence_reason",
            ],
        )
    if payload["recent_activity"]:
        print_table(
            ["Time", "Event", "Agent", "Message", "Preview"],
            payload["recent_activity"],
            keys=["ts", "event", "agent_name", "message_id", "reply_preview"],
        )


def show_agent(
    name: str = typer.Argument(..., help="Managed agent name"),
    activity_limit: int = typer.Option(12, "--activity-limit", help="Number of recent agent events to display"),
    as_json: bool = JSON_OPTION,
):
    """Show one managed agent in detail."""
    from . import gateway as _gateway_cmd

    _detail = getattr(_gateway_cmd, "_agent_detail_payload", _agent_detail_payload)
    _render = getattr(_gateway_cmd, "_render_agent_detail", _render_agent_detail)

    result = _detail(name, activity_limit=activity_limit)
    if result is None:
        err_console.print(f"[red]Managed agent not found:[/red] {name}")
        raise typer.Exit(1)
    if as_json:
        print_json(result)
        return
    console.print(_render(result["agent"], activity=result["recent_activity"]))


def test_agent(
    name: str = typer.Argument(..., help="Managed agent name"),
    message: str = typer.Option(None, "--message", help="Override the recommended Gateway test prompt"),
    author: str = typer.Option("agent", "--author", help="Who should author the test message: agent | user"),
    sender_agent: str = typer.Option(None, "--sender-agent", help="Managed sender identity to use when --author agent"),
    as_json: bool = JSON_OPTION,
):
    """Send a Gateway-authored test message to one managed agent."""
    from . import gateway as _gateway_cmd

    _send_test = getattr(_gateway_cmd, "_send_gateway_test_to_managed_agent", _send_gateway_test_to_managed_agent)

    try:
        result = _send_test(name, content=message, author=author, sender_agent=sender_agent)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        print_json(result)
        return

    err_console.print(f"[green]Gateway test sent:[/green] @{result['target_agent']}")
    err_console.print(f"  prompt = {result['recommended_prompt']}")
    message_payload = result.get("message") or {}
    if isinstance(message_payload, dict) and message_payload.get("id"):
        err_console.print(f"  message_id = {message_payload['id']}")


def move_agent(
    name: str = typer.Argument(..., help="Managed agent name"),
    space_id: str = typer.Option(None, "--space", "--space-id", "-s", help="Target space slug, name, or id"),
    revert: bool = typer.Option(
        False,
        "--revert",
        help=(
            "Move the agent back to its previous space. "
            "Mutually exclusive with --space; requires a prior move on this entry."
        ),
    ),
    as_json: bool = JSON_OPTION,
):
    """Move a Gateway-managed agent to another allowed space.

    Pass ``--space`` to move to a specific space, or ``--revert`` to move
    back to the previously-recorded space without retyping its id. The
    revert pointer is captured automatically on every successful move,
    so the standard "move out, move back" loop works without bookkeeping.
    """
    from . import gateway as _gateway_cmd

    _move = getattr(_gateway_cmd, "_move_managed_agent_space")

    if not revert and not (space_id and space_id.strip()):
        err_console.print("[red]Provide --space or --revert.[/red]")
        raise typer.Exit(1)
    try:
        result = _move(name, space_id, revert=revert)
    except (LookupError, ValueError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        print_json(result)
        return

    err_console.print(f"[green]Managed agent moved:[/green] @{name}")
    err_console.print(
        f"  space = {result.get('active_space_name') or result.get('active_space_id') or result.get('space_id')}"
    )
    if result.get("previous_space_id"):
        previous_label = result.get("previous_space_name") or result.get("previous_space_id")
        err_console.print(f"  previous = {previous_label} (use --revert to move back)")


def doctor_agent(
    name: str = typer.Argument(..., help="Managed agent name"),
    send_test: bool = typer.Option(False, "--send-test", help="Also send a Gateway-authored smoke test"),
    as_json: bool = JSON_OPTION,
):
    """Run Gateway Doctor checks for one managed agent."""
    from . import gateway as _gateway_cmd

    _doctor = getattr(_gateway_cmd, "_run_gateway_doctor", _run_gateway_doctor)

    try:
        result = _doctor(name, send_test=send_test)
    except (LookupError, ValueError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        print_json(result)
        return

    tone = {"passed": "green", "warning": "yellow", "failed": "red"}.get(result["status"], "cyan")
    err_console.print(f"[{tone}]Gateway Doctor {result['status']}:[/{tone}] @{name}")
    err_console.print(f"  summary = {result['summary']}")
    print_table(["Check", "Status", "Detail"], result["checks"], keys=["name", "status", "detail"])


def list_approvals(
    status: str | None = typer.Option(
        None, "--status", help="Optional filter: pending | approved | rejected | archived"
    ),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived/stale approvals"),
    as_json: bool = JSON_OPTION,
):
    """List local Gateway approval requests."""
    from . import gateway as _gateway_cmd

    _rows = getattr(_gateway_cmd, "_approval_rows_payload", _approval_rows_payload)

    payload = _rows(status=status, include_archived=include_archived)
    if as_json:
        print_json(payload)
        return
    err_console.print("[bold]ax gateway approvals list[/bold]")
    err_console.print(f"  approvals = {payload['count']}")
    err_console.print(f"  pending   = {payload['pending']}")
    if not payload["approvals"]:
        err_console.print("[dim]No Gateway approvals found.[/dim]")
        return
    print_table(
        ["Approval", "Asset", "Kind", "Status", "Risk", "Reason", "Requested"],
        payload["approvals"],
        keys=["approval_id", "asset_id", "approval_kind", "status", "risk", "reason", "requested_at"],
    )


def cleanup_approvals(as_json: bool = JSON_OPTION):
    """Archive stale approval requests that no longer match managed agents."""
    from . import gateway as _gateway_cmd

    _archive = getattr(_gateway_cmd, "archive_stale_gateway_approvals", archive_stale_gateway_approvals)

    payload = _archive()
    if as_json:
        print_json(payload)
        return
    archived_count = int(payload.get("archived_count") or 0)
    err_console.print(f"[green]Archived stale approvals:[/green] {archived_count}")
    err_console.print(f"  pending = {payload.get('remaining_pending', 0)}")


def show_approval(
    approval_id: str = typer.Argument(..., help="Approval request id"),
    as_json: bool = JSON_OPTION,
):
    """Show one local Gateway approval request."""
    from . import gateway as _gateway_cmd

    _detail = getattr(_gateway_cmd, "_approval_detail_payload", _approval_detail_payload)

    try:
        payload = _detail(approval_id)
    except LookupError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if as_json:
        print_json(payload)
        return
    approval = payload["approval"]
    print_table(
        ["Field", "Value"],
        [
            {"field": "approval_id", "value": approval.get("approval_id")},
            {"field": "asset_id", "value": approval.get("asset_id")},
            {"field": "gateway_id", "value": approval.get("gateway_id")},
            {"field": "install_id", "value": approval.get("install_id")},
            {"field": "kind", "value": approval.get("approval_kind")},
            {"field": "status", "value": approval.get("status")},
            {"field": "risk", "value": approval.get("risk")},
            {"field": "action", "value": approval.get("action")},
            {"field": "resource", "value": approval.get("resource")},
            {"field": "reason", "value": approval.get("reason")},
            {"field": "requested_at", "value": approval.get("requested_at")},
            {"field": "decided_at", "value": approval.get("decided_at")},
            {"field": "decision_scope", "value": approval.get("decision_scope")},
        ],
        keys=["field", "value"],
    )
    candidate = approval.get("candidate_binding") if isinstance(approval.get("candidate_binding"), dict) else None
    if candidate:
        print_table(
            ["Candidate Field", "Value"],
            [
                {"field": "path", "value": candidate.get("path")},
                {"field": "binding_type", "value": candidate.get("binding_type")},
                {"field": "launch_spec_hash", "value": candidate.get("launch_spec_hash")},
                {"field": "candidate_signature", "value": candidate.get("candidate_signature")},
            ],
            keys=["field", "value"],
        )


def approve_approval(
    approval_id: str = typer.Argument(..., help="Approval request id"),
    scope: str = typer.Option("asset", "--scope", help="Recorded approval scope: once | asset | gateway"),
    as_json: bool = JSON_OPTION,
):
    """Approve a local Gateway binding request."""
    from . import gateway as _gateway_cmd

    _approve = getattr(_gateway_cmd, "approve_gateway_approval", approve_gateway_approval)

    try:
        payload = _approve(approval_id, scope=scope)
    except (LookupError, ValueError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if as_json:
        print_json(payload)
        return
    approval = payload["approval"]
    err_console.print(f"[green]Approved:[/green] {approval['approval_id']}")
    err_console.print(f"  asset = {approval.get('asset_id')}")
    err_console.print(f"  scope = {approval.get('decision_scope')}")


def deny_approval(
    approval_id: str = typer.Argument(..., help="Approval request id"),
    as_json: bool = JSON_OPTION,
):
    """Deny a local Gateway binding request."""
    from . import gateway as _gateway_cmd

    _deny = getattr(_gateway_cmd, "deny_gateway_approval", deny_gateway_approval)

    try:
        payload = _deny(approval_id)
    except LookupError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if as_json:
        print_json({"approval": payload})
        return
    err_console.print(f"[yellow]Denied:[/yellow] {payload['approval_id']}")
    err_console.print(f"  asset = {payload.get('asset_id')}")
