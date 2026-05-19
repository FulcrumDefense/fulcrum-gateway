"""ax gateway messaging — send-as-agent + inbox + pending-queue sync.

Extracted from ``commands/gateway.py`` per issue #28 Phase 1. Owns the
identity-guard, manual passive-queue sync, post-send inbox poll, and the
core send / inbox helpers used by managed-agent operator surfaces. The
matching ``agents send`` and ``agents inbox`` CLI commands live here too
and are registered onto ``agents_app`` from ``commands/gateway.py``.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import typer

from .. import gateway as gateway_core
from ..gateway import (
    annotate_runtime_health,
    ensure_gateway_identity_binding,
    find_agent_entry,
    load_agent_pending_messages,
    load_gateway_registry,
    load_gateway_session,
    record_gateway_activity,
    save_agent_pending_messages,
    save_gateway_registry,
)
from ..output import JSON_OPTION, console, err_console, print_json


def _identity_space_send_guard(entry: dict, *, explicit_space_id: str | None = None) -> dict:
    registry = load_gateway_registry()
    stored = find_agent_entry(registry, str(entry.get("name") or "")) or entry
    ensure_gateway_identity_binding(registry, stored, session=load_gateway_session())
    snapshot = annotate_runtime_health(stored, registry=registry, explicit_space_id=explicit_space_id)
    save_gateway_registry(registry)
    if str(snapshot.get("confidence") or "").upper() == "BLOCKED":
        reason = str(snapshot.get("confidence_reason") or "blocked")
        detail = str(snapshot.get("confidence_detail") or "Gateway blocked this action.")
        raise ValueError(f"{detail} ({reason})")
    return snapshot


def _sync_passive_queue_after_manual_send(
    *,
    entry: dict,
    handled_message_id: str | None,
    reply_message_id: str | None,
    reply_preview: str | None,
) -> None:
    runtime_type = str(entry.get("runtime_type") or "").lower()
    if runtime_type not in {"inbox", "passive", "monitor"}:
        return

    pending_items = gateway_core.remove_agent_pending_message(str(entry.get("name") or ""), handled_message_id)
    registry = load_gateway_registry()
    stored = find_agent_entry(registry, str(entry.get("name") or "")) or entry
    backlog_depth = len(pending_items)
    last_pending = pending_items[-1] if pending_items else {}

    if handled_message_id:
        stored["processed_count"] = int(stored.get("processed_count") or 0) + 1
        stored["last_work_completed_at"] = datetime.now(timezone.utc).isoformat()

    stored["backlog_depth"] = backlog_depth
    stored["current_status"] = "queued" if backlog_depth > 0 else None
    stored["current_activity"] = (
        gateway_core._gateway_pickup_activity(runtime_type, backlog_depth)[:240] if backlog_depth > 0 else None
    )
    stored["last_reply_message_id"] = reply_message_id or stored.get("last_reply_message_id")
    stored["last_reply_preview"] = reply_preview or stored.get("last_reply_preview")
    if last_pending:
        stored["last_received_message_id"] = last_pending.get("message_id")
        stored["last_work_received_at"] = (
            last_pending.get("queued_at") or last_pending.get("created_at") or stored.get("last_work_received_at")
        )
    elif handled_message_id:
        stored["last_received_message_id"] = None
        stored["last_work_received_at"] = None

    save_gateway_registry(registry)
    if handled_message_id:
        record_gateway_activity(
            "manual_queue_acknowledged",
            entry=stored,
            message_id=handled_message_id,
            reply_message_id=reply_message_id,
            backlog_depth=backlog_depth,
        )


def _poll_managed_agent_inbox_after_send(
    *,
    name: str,
    space_id: str | None,
    limit: int,
    wait_seconds: int,
    channel: str = "main",
    poll_interval: float = 1.0,
) -> dict:
    """Bundle "what arrived while you were drafting" for a managed-agent send.

    Mirrors ``_poll_local_inbox_over_http``'s wait loop, but uses the
    in-process ``_inbox_for_managed_agent`` (Live Listener / managed-agent
    path) instead of the local-session HTTP proxy. Closes aX task
    ``663d9e6f``: every send-as-agent path should return inbound messages
    that arrived during the send so two agents don't talk past each other.

    ``mark_read=True`` so the same messages don't re-appear on the next
    poll. The wait loop exits as soon as we have messages or the deadline
    elapses.
    """
    from . import gateway as _gateway_cmd

    _inbox = getattr(_gateway_cmd, "_inbox_for_managed_agent", _inbox_for_managed_agent)

    deadline = time.monotonic() + max(0, int(wait_seconds))
    while True:
        result = _inbox(
            name=name,
            limit=max(1, int(limit)),
            channel=channel,
            space_id=space_id,
            unread_only=True,
            mark_read=True,
        )
        if result.get("messages") or wait_seconds <= 0 or time.monotonic() >= deadline:
            return result
        time.sleep(poll_interval)


def _send_from_managed_agent(
    *,
    name: str,
    content: str,
    to: str | None = None,
    parent_id: str | None = None,
    space_id: str | None = None,
    sent_via: str = "gateway_cli",
    metadata_extra: dict[str, object] | None = None,
    include_inbox: bool = True,
    inbox_wait: int = 2,
    inbox_limit: int = 10,
    inbox_channel: str = "main",
) -> dict:
    from . import gateway as _gateway_cmd

    _load_or_exit = getattr(_gateway_cmd, "_load_managed_agent_or_exit")
    _load_client = getattr(_gateway_cmd, "_load_managed_agent_client")
    _guard = getattr(_gateway_cmd, "_identity_space_send_guard", _identity_space_send_guard)
    _sync = getattr(_gateway_cmd, "_sync_passive_queue_after_manual_send", _sync_passive_queue_after_manual_send)
    _poll = getattr(_gateway_cmd, "_poll_managed_agent_inbox_after_send", _poll_managed_agent_inbox_after_send)

    if not content.strip():
        raise ValueError("Message content is required.")
    entry = _load_or_exit(name)
    if str(entry.get("desired_state") or "").strip().lower() == "stopped":
        raise ValueError(f"@{name} is stopped. Start it before it can send.")
    snapshot = _guard(entry, explicit_space_id=space_id)
    client = _load_client(entry)
    selected_space_id = str(space_id or snapshot.get("active_space_id") or entry.get("space_id") or "")
    if not selected_space_id:
        raise ValueError(f"Managed agent is missing a space id: @{name}")

    message_content = content.strip()
    mention = str(to or "").strip().lstrip("@")
    if mention:
        prefix = f"@{mention}"
        if not message_content.startswith(prefix):
            message_content = f"{prefix} {message_content}".strip()

    metadata = {
        "control_plane": "gateway",
        "gateway": {
            "managed": True,
            "agent_name": entry.get("name"),
            "agent_id": entry.get("agent_id"),
            "runtime_type": entry.get("runtime_type"),
            "transport": entry.get("transport", "gateway"),
            "credential_source": entry.get("credential_source", "gateway"),
            "sent_via": sent_via,
        },
    }
    if metadata_extra:
        gateway_meta = metadata["gateway"]
        if isinstance(gateway_meta, dict):
            gateway_meta.update(metadata_extra)
    result = client.send_message(
        selected_space_id,
        message_content,
        agent_id=str(entry.get("agent_id") or "") or None,
        parent_id=parent_id or None,
        metadata=metadata,
    )
    payload = result.get("message", result) if isinstance(result, dict) else result
    if isinstance(payload, dict):
        record_gateway_activity(
            "manual_message_sent",
            entry=entry,
            message_id=payload.get("id"),
            reply_preview=message_content[:120] or None,
        )
        _sync(
            entry=entry,
            handled_message_id=parent_id,
            reply_message_id=str(payload.get("id") or "") or None,
            reply_preview=message_content[:120] or None,
        )
    response: dict = {"agent": entry.get("name"), "message": payload, "content": message_content}
    if include_inbox:
        try:
            response["inbox"] = _poll(
                name=str(entry.get("name") or name),
                space_id=selected_space_id,
                limit=inbox_limit,
                wait_seconds=inbox_wait,
                channel=inbox_channel,
            )
        except Exception as exc:
            # Inbox bundling is a best-effort enhancement on top of the send.
            # If it fails (transient API error, etc.) we still return the send
            # result the operator/agent actually depends on.
            response["inbox_error"] = str(exc)
    return response


def _inbox_for_managed_agent(
    *,
    name: str,
    limit: int = 20,
    channel: str = "main",
    space_id: str | None = None,
    unread_only: bool = False,
    mark_read: bool = False,
) -> dict:
    """Read a Gateway-managed agent's inbox using its Gateway-loaded credentials.

    Mirrors the read side of ``_send_from_managed_agent``. Works uniformly
    across Live Listener (claude_code_channel, hermes) and pass-through
    templates so the operator surface is the same regardless of how the
    agent is wired — that's the P1 the original task (``70f08787``) calls
    out: a Live Listener seat without a channel MCP attached has no way to
    peek its own inbox today.

    Defaults are deliberately peek-friendly (``unread_only=False``,
    ``mark_read=False``) because the typical caller is an operator
    inspecting on the agent's behalf, not the agent consuming work.
    """
    from . import gateway as _gateway_cmd

    _load_client = getattr(_gateway_cmd, "_load_managed_agent_client")

    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")
    selected_space = str(space_id or entry.get("space_id") or "").strip() or None
    if not selected_space:
        raise ValueError(f"Managed agent is missing a space id: @{name}")
    client = _load_client(entry)
    # Capture the local pending queue first — it's the Gateway's view of
    # "messages addressed to this agent that haven't been picked up yet".
    # The drawer's "X unread messages" badge counts these. Use it to filter
    # the upstream listing when unread_only=True so the drawer's body matches
    # its own header (without this, upstream returns ALL messages and the
    # drawer says "3 unread" while showing 20).
    agent_name = str(entry.get("name") or name)
    pending_items_for_filter = load_agent_pending_messages(agent_name)
    pending_ids = {
        str(item.get("message_id") or item.get("id") or "").strip()
        for item in pending_items_for_filter
        if str(item.get("message_id") or item.get("id") or "").strip()
    }
    data = client.list_messages(
        limit=limit,
        channel=channel,
        space_id=selected_space,
        agent_id=str(entry.get("agent_id") or "") or None,
        unread_only=unread_only,
        mark_read=mark_read,
    )
    messages = data if isinstance(data, list) else data.get("messages", [])
    if unread_only:
        if pending_ids:
            messages = [
                msg for msg in messages if str(msg.get("id") or msg.get("message_id") or "").strip() in pending_ids
            ]
        else:
            messages = []
    # Mirror `_local_session_inbox`: when the operator explicitly marks read,
    # the local pending queue (which powers `backlog_depth` and the UI badge)
    # must also be cleared. Without this, the upstream returns
    # `marked_read_count=N` but the side app keeps showing N unread because
    # `backlog_depth` is read straight off the queue file.
    local_marked_read_count = 0
    if mark_read:
        local_marked_read_count = len(pending_items_for_filter)
        save_agent_pending_messages(agent_name, [])
        registry_after = load_gateway_registry()
        stored = find_agent_entry(registry_after, agent_name)
        if stored is not None:
            stored["backlog_depth"] = 0
            stored["queue_depth"] = 0
            stored["current_status"] = None
            stored["current_activity"] = None
            save_gateway_registry(registry_after)
    record_gateway_activity(
        "managed_inbox_polled",
        entry=entry,
        message_count=len(messages),
        mark_read=mark_read,
        space_id=selected_space,
        local_marked_read_count=local_marked_read_count,
    )
    return {
        "agent": entry.get("name"),
        "agent_id": entry.get("agent_id"),
        "space_id": selected_space,
        "messages": messages,
        # When unread_only=True, the count returned reflects the pending
        # queue intersection (what the drawer actually shows), not the
        # upstream's idea of unread. Operators see one consistent number.
        "unread_count": (
            len(messages) if unread_only else (data.get("unread_count") if isinstance(data, dict) else None)
        ),
        "marked_read_count": data.get("marked_read_count") if isinstance(data, dict) else None,
        "local_marked_read_count": local_marked_read_count if mark_read else None,
    }


def _ack_managed_agent_message(
    name: str,
    *,
    message_id: str,
    reply_id: str | None = None,
    reply_preview: str | None = None,
) -> dict:
    """Pass-through ack: agent reports it processed message_id and optionally
    sent reply_id. Updates local registry's reply timestamps + counters, drops
    the message from the pending queue, fires reply_sent activity event so
    the simple-gateway drawer surfaces 'Replied · just now' on the row.
    """
    name = name.strip()
    if not name:
        raise ValueError("Managed agent name is required.")
    message_id = (message_id or "").strip()
    if not message_id:
        raise ValueError("message_id is required.")
    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")
    now_iso = datetime.now(timezone.utc).isoformat()
    # Drop from pending queue (best-effort; the agent may have already cleaned
    # it up locally).
    items = load_agent_pending_messages(name)
    remaining = [item for item in items if str(item.get("message_id") or "") != message_id]
    if len(remaining) != len(items):
        save_agent_pending_messages(name, remaining)
    # Update registry entry so the row's last-action label and counters reflect
    # the reply that just went out via the agent's PAT.
    entry["last_work_completed_at"] = now_iso
    entry["last_reply_at"] = now_iso
    entry["last_received_message_id"] = message_id
    if reply_id:
        entry["last_reply_message_id"] = reply_id
    if reply_preview:
        entry["last_reply_preview"] = reply_preview[:240]
    entry["processed_count"] = int(entry.get("processed_count") or 0) + 1
    save_gateway_registry(registry)
    record_gateway_activity(
        "reply_sent",
        entry=entry,
        message_id=message_id,
        reply_message_id=reply_id,
        reply_preview=reply_preview,
    )
    return annotate_runtime_health(entry, registry=registry)


# ---------------------------------------------------------------------------
# agents sub-app commands (send / inbox). Registered against ``agents_app``
# in ``commands/gateway.py``.
# ---------------------------------------------------------------------------


def send_as_agent(
    name: str = typer.Argument(..., help="Managed agent name to send as"),
    content: str = typer.Argument(..., help="Message content"),
    to: str = typer.Option(None, "--to", help="Prepend a mention like @codex automatically"),
    parent_id: str = typer.Option(None, "--parent-id", help="Reply inside an existing thread"),
    include_inbox: bool = typer.Option(
        True,
        "--inbox/--no-inbox",
        help="After sending, include unread messages addressed to this agent in the response. "
        "Default ON so two agents don't talk past each other when one replies while the other is mid-draft.",
    ),
    inbox_wait: int = typer.Option(
        2,
        "--inbox-wait",
        min=0,
        help="Seconds to wait for inbound messages after sending. 0 only checks immediately.",
    ),
    inbox_limit: int = typer.Option(
        10, "--inbox-limit", min=1, max=100, help="Max inbound messages to bundle in the response."
    ),
    as_json: bool = JSON_OPTION,
):
    """Send a message as a Gateway-managed agent."""
    from . import gateway as _gateway_cmd

    _send = getattr(_gateway_cmd, "_send_from_managed_agent", _send_from_managed_agent)

    try:
        result = _send(
            name=name,
            content=content,
            to=to,
            parent_id=parent_id,
            include_inbox=include_inbox,
            inbox_wait=inbox_wait,
            inbox_limit=inbox_limit,
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        print_json(result)
        return
    err_console.print(f"[green]Sent as managed agent:[/green] @{result['agent']}")
    if isinstance(result["message"], dict) and result["message"].get("id"):
        err_console.print(f"  id = {result['message']['id']}")
    err_console.print(f"  content = {result['content']}")
    inbox = result.get("inbox") if isinstance(result.get("inbox"), dict) else None
    if inbox:
        unread = inbox.get("unread_count") or 0
        if unread:
            err_console.print(
                f"[yellow]Inbox while drafting:[/yellow] {unread} unread message(s) addressed to @{result['agent']}"
            )
            for msg in (inbox.get("messages") or [])[:5]:
                if not isinstance(msg, dict):
                    continue
                sender = msg.get("agent_name") or msg.get("user_name") or msg.get("sender") or "unknown"
                preview = str(msg.get("content") or "").strip().splitlines()[0][:120] if msg.get("content") else ""
                err_console.print(f"  - @{sender}: {preview}")
    elif result.get("inbox_error"):
        err_console.print(f"[dim]Inbox poll failed: {result['inbox_error']}[/dim]")


def inbox_for_agent(
    name: str = typer.Argument(..., help="Managed agent name"),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Max messages to return"),
    channel: str = typer.Option("main", "--channel", help="Message channel"),
    space_id: str = typer.Option(
        None,
        "--space",
        "--space-id",
        "-s",
        help="Override the agent's home space. Accepts a slug, name, or UUID.",
    ),
    unread_only: bool = typer.Option(
        False,
        "--unread-only/--all",
        help="Filter to unread messages only (default: show recent regardless of read state)",
    ),
    mark_read: bool = typer.Option(
        False,
        "--mark-read/--no-mark-read",
        help=(
            "Mark returned messages as read. Defaults to peek (no-mark-read) so an "
            "operator inspecting on an agent's behalf does not silently consume work."
        ),
    ),
    as_json: bool = JSON_OPTION,
):
    """Read a Gateway-managed agent's recent inbox.

    Works for both Live Listeners (claude_code_channel, hermes) and pass-through
    agents — uses the agent's Gateway-loaded credentials, so no PAT is exposed
    to the caller. Pairs with `ax gateway agents send` for a uniform read/write
    surface from any operator seat without needing the channel MCP attached.

    The ``--space`` option accepts a slug, name, or UUID. Slugs and names
    resolve through the local space cache; the operator's user PAT is not
    required for this lookup.
    """
    from . import gateway as _gateway_cmd

    _resolve_via_cache = getattr(_gateway_cmd, "_resolve_space_via_cache")
    _inbox = getattr(_gateway_cmd, "_inbox_for_managed_agent", _inbox_for_managed_agent)

    if space_id:
        resolved = _resolve_via_cache(space_id)
        if resolved is None:
            err_console.print(
                f"[red]Could not resolve space '{space_id}' from the local space cache. "
                "Pass a UUID, or run `ax spaces list` once to populate the cache.[/red]"
            )
            raise typer.Exit(1)
        space_id = resolved
    try:
        result = _inbox(
            name=name,
            limit=limit,
            channel=channel,
            space_id=space_id,
            unread_only=unread_only,
            mark_read=mark_read,
        )
    except LookupError:
        err_console.print(f"[red]Managed agent not found:[/red] {name}")
        raise typer.Exit(1)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        print_json(result)
        return
    messages = result.get("messages") or []
    console.print(f"[bold]inbox[/bold] @{result.get('agent')}: {len(messages)} message(s)")
    unread = result.get("unread_count")
    if unread is not None:
        console.print(f"  [dim]unread_count = {unread}[/dim]")
    for message in messages:
        if not isinstance(message, dict):
            continue
        created = str(message.get("created_at") or "")
        author = str(message.get("display_name") or message.get("agent_name") or message.get("sender") or "-")
        content = str(message.get("content") or "").replace("\n", " ")
        console.print(f"  {created} {author}: {content[:160]}")
