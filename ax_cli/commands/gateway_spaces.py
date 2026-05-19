"""ax gateway spaces — space resolution, move, and the `spaces` CLI sub-app.

Extracted from ``commands/gateway.py`` per issue #28 Phase 1. Owns the
space cache/lookup helpers used across Gateway commands, the
move-managed-agent flow, and the three ``ax gateway spaces`` commands
(``use`` / ``current`` / ``list``).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import typer

from ..client import AxClient
from ..commands import auth as auth_cmd
from ..config import resolve_space_id
from ..gateway import (
    _is_passive_runtime,
    active_gateway_pid,
    annotate_runtime_health,
    apply_entry_current_space,
    ensure_gateway_identity_binding,
    find_agent_entry,
    load_gateway_registry,
    load_gateway_session,
    load_recent_gateway_activity,
    load_space_cache,
    looks_like_space_uuid,
    lookup_space_in_cache,
    record_gateway_activity,
    save_gateway_registry,
    save_gateway_session,
    save_space_cache,
    space_name_from_cache,
    upsert_space_cache_entry,
)
from ..output import JSON_OPTION, err_console, print_json, print_table


def _resolve_space_via_cache(value: str | None) -> str | None:
    """Cache-only space resolver for the pass-through (`local_*`) commands.

    Pass-through agents must not need the user PAT, so we cannot fall back
    to a fresh `client.list_spaces()` here — that would defeat the trust
    boundary. The on-disk space cache (populated by any prior user-side
    Gateway command) is the authoritative source on the agent side.

    Returns the canonical UUID for a slug or name when found, the original
    UUID-like input verbatim, or ``None`` if neither (caller decides whether
    to error or pass through).

    This intentionally diverges from `config.resolve_space_id()`, which
    requires an authoring client and falls back to upstream `list_spaces`.
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # UUID-like passes through unchanged.
    try:
        from uuid import UUID

        UUID(raw)
        return raw
    except ValueError:
        pass
    cached = lookup_space_in_cache(raw)
    if cached:
        sid = str(cached.get("id") or cached.get("space_id") or "").strip()
        if sid:
            return sid
    return None


def _agent_row_space_ids(registry: dict) -> set[str]:
    return {
        str(item.get("space_id") or "").strip()
        for item in registry.get("agents", [])
        if isinstance(item, dict) and str(item.get("space_id") or "").strip()
    }


def _space_list_from_response(raw: object) -> list[dict]:
    items = raw.get("spaces", raw) if isinstance(raw, dict) else raw
    return [item for item in (items or []) if isinstance(item, dict)]


def _space_name_for_id(client: AxClient, space_id: str) -> str | None:
    """Friendly-name lookup with persistent-cache short-circuit.

    Hits the local space cache first so we don't pay an upstream `list_spaces`
    call (and risk a 429) for a name we already know. Only falls through to
    upstream when the cache has no entry for this id, and refreshes the cache
    on a successful fetch so future calls stay in-process.
    """
    cached = space_name_from_cache(space_id)
    if cached:
        return cached
    try:
        rows = _space_list_from_response(client.list_spaces())
    except Exception:
        return None
    refreshed: list[dict] = []
    match: str | None = None
    for item in rows:
        sid = auth_cmd._candidate_space_id(item)
        if not sid:
            continue
        name = str(item.get("name") or item.get("slug") or sid)
        slug = str(item.get("slug") or "").strip() or None
        refreshed.append({"id": sid, "name": name, "slug": slug})
        if sid == space_id:
            match = name
    if refreshed:
        save_space_cache(refreshed)
    return match


def _resolve_gateway_agent_home_space(
    *,
    client: AxClient,
    session: dict,
    registry: dict,
    explicit_space_id: str | None = None,
) -> str:
    from . import gateway as _gateway_cmd

    _resolve_sid = getattr(_gateway_cmd, "resolve_space_id", resolve_space_id)
    explicit = str(explicit_space_id or "").strip()
    if explicit:
        if looks_like_space_uuid(explicit):
            return explicit
        # Caller passed a name/slug — resolve through the backend so we never
        # store a non-UUID in the registry's space_id field.
        return _resolve_sid(client, explicit=explicit)
    session_space = str(session.get("space_id") or "").strip()
    if session_space:
        return session_space

    row_spaces = _agent_row_space_ids(registry)
    if len(row_spaces) == 1:
        return next(iter(row_spaces))

    try:
        selected = auth_cmd._select_login_space(_space_list_from_response(client.list_spaces()))
        selected_id = auth_cmd._candidate_space_id(selected or {})
        if selected_id:
            return selected_id
    except Exception:
        pass

    if len(row_spaces) > 1:
        raise ValueError(
            "Multiple agent spaces are present. Pick a home space once with --space-id, "
            "or move an existing agent row to the intended space."
        )
    raise ValueError(
        "No agent home space could be inferred. Pick a home space once with --space-id; "
        "after the agent row exists, Gateway will use the row's space_id."
    )


def _agent_space_id_from_backend_record(agent: dict) -> str | None:
    """Return the backend-owned current/default space for an agent row.

    Prefer the current row placement (`space_id`) over defaults so a Gateway
    local client that omits --space-id follows the database after a user moves
    the agent between spaces.
    """
    raw_current = agent.get("current_space")
    current_space_id = ""
    if isinstance(raw_current, dict):
        current_space_id = str(raw_current.get("space_id") or raw_current.get("id") or "").strip()
    elif raw_current:
        current_space_id = str(raw_current).strip()
    return (
        current_space_id
        or str(agent.get("active_space_id") or "").strip()
        or str(agent.get("space_id") or "").strip()
        or str(agent.get("default_space_id") or "").strip()
        or None
    )


def _agent_space_name_from_backend_record(agent: dict, space_id: str | None) -> str | None:
    raw_current = agent.get("current_space")
    if isinstance(raw_current, dict):
        current_id = str(raw_current.get("space_id") or raw_current.get("id") or "").strip()
        if not space_id or current_id == space_id:
            return str(raw_current.get("name") or raw_current.get("space_name") or "").strip() or None
    return (
        str(agent.get("space_name") or agent.get("active_space_name") or agent.get("default_space_name") or "").strip()
        or None
    )


def _backend_agent_record(client: AxClient, name: str) -> dict | None:
    """Look up an agent by name on the upstream backend.

    Falls back to the local agents cache when upstream is unavailable
    (e.g. paxai.app rate-limits us). Successful upstream responses
    seed/refresh the cache so the next failure has stale-but-usable
    data to serve.
    """
    # Late-lookup of cache helpers so they keep working while they still
    # live in commands/gateway.py (will move to gateway_agents.py later).
    from . import gateway as _gateway_cmd

    _save_agents_cache = getattr(_gateway_cmd, "_save_agents_cache", None)
    _load_agents_cache = getattr(_gateway_cmd, "_load_agents_cache", None)
    agents: list[dict] = []
    try:
        agents_data = client.list_agents()
        agents = agents_data if isinstance(agents_data, list) else (agents_data or {}).get("agents", []) or []
        if agents and _save_agents_cache is not None:
            _save_agents_cache([a for a in agents if isinstance(a, dict)])
    except Exception:
        # Upstream unavailable — fall back to last-good cache.
        agents = _load_agents_cache() if _load_agents_cache is not None else []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        if str(agent.get("name") or "") != name:
            continue
        return agent
    return None


def _existing_agent_home_space(client: AxClient, name: str) -> str | None:
    agent = _backend_agent_record(client, name)
    if not agent:
        return None
    return _agent_space_id_from_backend_record(agent)


def _hydrate_entry_space_from_database(registry: dict, entry: dict) -> str | None:
    """Refresh an existing registry entry's space from the backend agent row."""
    # Late-lookup for _load_gateway_user_client + _backend_agent_record so
    # tests that monkeypatch via gateway_cmd still reach.
    from . import gateway as _gateway_cmd

    _load_client = getattr(_gateway_cmd, "_load_gateway_user_client")
    _backend_record = getattr(_gateway_cmd, "_backend_agent_record", _backend_agent_record)
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    try:
        agent = _backend_record(_load_client(), name)
    except Exception:
        return None
    if not agent:
        return None
    space_id = _agent_space_id_from_backend_record(agent)
    if not space_id:
        return None
    space_name = _agent_space_name_from_backend_record(agent, space_id)
    apply_entry_current_space(entry, space_id, space_name=space_name, make_default=False)
    if str(agent.get("default_space_id") or "").strip():
        entry["default_space_id"] = str(agent.get("default_space_id") or "").strip()
    if str(agent.get("id") or agent.get("agent_id") or "").strip():
        entry["agent_id"] = str(agent.get("id") or agent.get("agent_id") or "").strip()
    save_gateway_registry(registry)
    return space_id


def _normalize_spaces_response(items: list) -> list[dict]:
    """Normalize an upstream `list_spaces` response into [{id, name, slug}].

    If a row arrives with an empty/missing name (we've seen this happen for
    brand-new spaces), fall back to the local cache before defaulting to the
    UUID — avoids the "raw UUID rendered in picker" symptom for any space the
    operator has seen at least once.
    """
    spaces: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        space_id = str(item.get("id") or item.get("space_id") or "").strip()
        if not space_id:
            continue
        upstream_name = str(item.get("name") or item.get("space_name") or "").strip()
        cached_name = space_name_from_cache(space_id) if not upstream_name else None
        spaces.append(
            {
                "id": space_id,
                "name": upstream_name or cached_name or space_id,
                "slug": str(item.get("slug") or "").strip() or None,
            }
        )
    return spaces


def _spaces_payload() -> dict:
    """Return the spaces visible to the Gateway bootstrap session.

    Always surfaces ``active_space_id`` / ``active_space_name`` from session
    state, even when the upstream ``list_spaces`` call fails (e.g. paxai.app
    rate-limits). Successful upstream responses are cached on disk so the UI
    keeps a usable picker through transient outages.
    """
    from . import gateway as _gateway_cmd

    _load_client = getattr(_gateway_cmd, "_load_gateway_user_client")
    session = load_gateway_session() or {}
    active_space_id = str(session.get("space_id") or "").strip() or None
    active_space_name = str(session.get("space_name") or "").strip() or None

    error: str | None = None
    cached = False
    try:
        client = _load_client()
        raw = client.list_spaces()
        items = raw.get("spaces", raw) if isinstance(raw, dict) else raw
        spaces = _normalize_spaces_response(items or [])
        if spaces:
            save_space_cache(spaces)
    except Exception as exc:  # noqa: BLE001 — upstream errors are routine here
        error = str(exc)
        spaces = load_space_cache()
        cached = bool(spaces)

    if active_space_id and not any(s["id"] == active_space_id for s in spaces):
        spaces = [
            {"id": active_space_id, "name": active_space_name or active_space_id, "slug": None},
            *spaces,
        ]

    payload: dict = {
        "spaces": spaces,
        "active_space_id": active_space_id,
        "active_space_name": active_space_name,
    }
    if error:
        payload["error"] = error
        payload["cached"] = cached
    return payload


def _move_managed_agent_space(name: str, new_space_id: str | None, *, revert: bool = False) -> dict:
    from . import gateway as _gateway_cmd

    _load_client = getattr(_gateway_cmd, "_load_gateway_user_client")
    _space_name = getattr(_gateway_cmd, "_space_name_for_id", _space_name_for_id)
    _resolve_sid = getattr(_gateway_cmd, "resolve_space_id", resolve_space_id)

    name = name.strip()
    if not name:
        raise ValueError("Managed agent name is required.")
    if revert:
        if new_space_id and new_space_id.strip():
            raise ValueError("Pass either --space or --revert, not both.")
        registry_for_revert = load_gateway_registry()
        revert_entry = find_agent_entry(registry_for_revert, name)
        if not revert_entry:
            raise LookupError(f"Managed agent not found: {name}")
        previous = str(revert_entry.get("previous_space_id") or "").strip()
        if not previous:
            raise ValueError(f"@{name} has no recorded previous space to revert to. Use --space <id> instead.")
        new_space_id = previous
    else:
        new_space_id = (new_space_id or "").strip()
        if not new_space_id:
            raise ValueError("Target space is required.")
    client = _load_client()
    new_space_id = _resolve_sid(client, explicit=new_space_id)
    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")
    if bool(entry.get("pinned")):
        raise ValueError(f"@{name} is pinned to its current space. Unlock it before moving.")
    if str(entry.get("space_id") or "").strip() == new_space_id:
        apply_entry_current_space(entry, new_space_id, space_name=_space_name(client, new_space_id))
        ensure_gateway_identity_binding(registry, entry, session=load_gateway_session())
        save_gateway_registry(registry)
        return annotate_runtime_health(entry, registry=registry)
    identifier = str(entry.get("agent_id") or name)
    try:
        client.set_agent_placement(identifier, space_id=new_space_id, pinned=bool(entry.get("pinned")))
    except AttributeError:
        try:
            client.update_agent(identifier, space_id=new_space_id)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Backend rejected move: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Backend rejected move: {exc}") from exc
    # Re-read the canonical record from backend — gateway local registry is a view,
    # never the source of truth.
    backend_space_id = new_space_id
    backend_space_name = _space_name(client, new_space_id)
    backend_allowed_spaces: list[dict[str, object]] | None = None
    read_back_methods = [
        method
        for method in (getattr(client, "get_agent_placement", None), getattr(client, "get_agent", None))
        if callable(method)
    ]
    for read_back in read_back_methods:
        try:
            record = read_back(identifier)
            if isinstance(record, dict) and isinstance(record.get("_record"), dict):
                record = record["_record"]
            elif isinstance(record, dict):
                record = record.get("agent", record)
            if not isinstance(record, dict):
                continue
            canonical = str(
                record.get("space_id") or record.get("current_space") or record.get("default_space_id") or ""
            ).strip()
            if canonical:
                backend_space_id = canonical
                backend_space_name = _space_name(client, backend_space_id) or backend_space_name
            allowed = record.get("allowed_spaces")
            if isinstance(allowed, list):
                try:
                    space_names_by_id = {
                        str(item.get("id") or item.get("space_id") or "").strip(): str(
                            item.get("name") or item.get("space_name") or item.get("slug") or ""
                        ).strip()
                        for item in _space_list_from_response(client.list_spaces())
                        if isinstance(item, dict) and str(item.get("id") or item.get("space_id") or "").strip()
                    }
                except Exception:
                    space_names_by_id = {}
                backend_allowed_spaces = [
                    {
                        **item,
                        "name": str(
                            item.get("name")
                            or space_names_by_id.get(str(item.get("space_id") or item.get("id") or "").strip())
                            or item.get("space_id")
                            or item.get("id")
                        ),
                    }
                    if isinstance(item, dict)
                    else {
                        "space_id": str(item),
                        "name": space_names_by_id.get(str(item)) or str(item),
                        "is_default": str(item) == backend_space_id,
                    }
                    for item in allowed
                    if item
                ]
            break
        except Exception:  # noqa: BLE001
            # Resync best-effort; the placement write already succeeded.
            continue
    previous_space_id = str(entry.get("space_id") or "").strip() or None
    previous_space_name = str(entry.get("active_space_name") or entry.get("space_name") or "").strip() or None
    if backend_allowed_spaces is not None:
        entry["allowed_spaces"] = backend_allowed_spaces
    apply_entry_current_space(entry, backend_space_id, space_name=backend_space_name)
    ensure_gateway_identity_binding(registry, entry, session=load_gateway_session())
    # Persist the prior space so `ax gateway agents move <name> --revert` can
    # find its way back without the operator needing to remember the UUID.
    if previous_space_id and previous_space_id != backend_space_id:
        entry["previous_space_id"] = previous_space_id
        if previous_space_name:
            entry["previous_space_name"] = previous_space_name
    # Mark the entry as moving for any concurrent send guard / UI panel that
    # reads `current_status`. Cleared once the rebind wait below resolves
    # (or the deadline elapses) so a stuck move doesn't permanently freeze
    # sends. The send guard itself raises off `_identity_space_send_guard`
    # via `annotate_runtime_health`; this surface is for human-readable text.
    entry["current_status"] = "moving"
    entry["current_activity"] = f"Moving to {backend_space_name or backend_space_id}; sends paused until reconnect."
    # Capture the rebind marker BEFORE writing the registry so the wait below
    # is guaranteed to see only post-move runtime/listener events.
    rebind_marker = datetime.now(timezone.utc).isoformat()
    save_gateway_registry(registry)
    record_gateway_activity(
        "managed_agent_moved_space",
        entry=entry,
        new_space_id=backend_space_id,
        requested_space_id=new_space_id,
        previous_space_id=previous_space_id,
    )
    if backend_space_id != new_space_id:
        # Backend coerced the move (likely allowed_spaces enforcement). Surface to operator
        # logs so backend_sentinel can pick it up if it indicates a quarantine gap.
        record_gateway_activity(
            "managed_agent_move_coerced",
            entry=entry,
            requested_space_id=new_space_id,
            applied_space_id=backend_space_id,
        )
    # Wait for the daemon to finish the rebind before returning. The daemon
    # is a separate process polling the registry every ~1s; once it sees
    # space_id changed it stops the old runtime and starts a new one.
    # Without this wait, a follow-up POST /api/agents/<name>/test can land
    # on the new switchboard before the new SSE listener has connected,
    # stranding the message. Listener-backed runtimes are not ready at
    # runtime_started; wait for listener_connected so an immediate test send
    # does not race the new SSE connection. Cap at 5s — if no listener event
    # appears we still return with the refreshed registry state.
    # Skip when no daemon is running (e.g. tests, offline operator) since
    # nothing will produce the rebind events we are waiting on.
    _active_pid = getattr(_gateway_cmd, "active_gateway_pid", active_gateway_pid)
    _recent_activity = getattr(_gateway_cmd, "load_recent_gateway_activity", load_recent_gateway_activity)
    if previous_space_id and previous_space_id != backend_space_id and _active_pid() is not None:
        runtime_type = entry.get("runtime_type")
        ready_events = {"runtime_started"} if _is_passive_runtime(runtime_type) else {"listener_connected"}
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            recent = _recent_activity(limit=20, agent_name=name)
            if any((event.get("ts") or "") > rebind_marker and event.get("event") in ready_events for event in recent):
                break
            time.sleep(0.2)
    # Reconnect window has resolved (or its 5s deadline elapsed). Clear the
    # human-readable "moving" status so subsequent sends through the
    # send-guard read normal state. Re-read the registry first because a
    # concurrent runtime/listener event may have already updated the entry.
    registry_after = load_gateway_registry()
    settled = find_agent_entry(registry_after, name)
    if settled is not None and str(settled.get("current_status") or "") == "moving":
        settled["current_status"] = None
        settled["current_activity"] = None
        save_gateway_registry(registry_after)
        # Mirror onto the local entry so the return value reflects the cleared state.
        entry["current_status"] = None
        entry["current_activity"] = None
    return annotate_runtime_health(entry, registry=registry)


# ---------------------------------------------------------------------------
# spaces sub-app commands (use / current / list).
# ---------------------------------------------------------------------------


def use_gateway_space(
    space: str = typer.Argument(..., help="Space id, slug, or name to make current for Gateway"),
    as_json: bool = JSON_OPTION,
):
    """Set the Gateway bootstrap session's current space by id, slug, or name."""
    from . import gateway as _gateway_cmd

    _load_session = getattr(_gateway_cmd, "_load_gateway_session_or_exit")
    _load_client = getattr(_gateway_cmd, "_load_gateway_user_client")
    _space_name = getattr(_gateway_cmd, "_space_name_for_id", _space_name_for_id)
    _resolve_sid = getattr(_gateway_cmd, "resolve_space_id", resolve_space_id)

    _save_session = getattr(_gateway_cmd, "save_gateway_session", save_gateway_session)
    session = _load_session()
    client = _load_client()
    sid = _resolve_sid(client, explicit=space)
    space_name = _space_name(client, sid)
    session["space_id"] = sid
    session["space_name"] = space_name
    path = _save_session(session)
    # Persist the resolved id/name into the spaces cache so subsequent slug
    # switches stay cache-served and stop hammering list_spaces.
    upsert_space_cache_entry(sid, name=space_name, slug=None)
    record_gateway_activity("gateway_space_use", space_id=sid, space_name=space_name)
    result = {
        "session_path": str(path),
        "space_id": sid,
        "space_name": space_name,
    }
    if as_json:
        print_json(result)
        return
    err_console.print(f"[green]Gateway current space:[/green] {space_name or sid} ({sid})")
    err_console.print(f"  session = {path}")


def current_gateway_space(as_json: bool = JSON_OPTION):
    """Show the Gateway bootstrap session's current space."""
    from . import gateway as _gateway_cmd

    _load_session = getattr(_gateway_cmd, "_load_gateway_session_or_exit")
    session = _load_session()
    result = {
        "space_id": session.get("space_id"),
        "space_name": session.get("space_name"),
        "base_url": session.get("base_url"),
        "username": session.get("username"),
    }
    if as_json:
        print_json(result)
        return
    err_console.print(f"Gateway current space: {result.get('space_name') or result.get('space_id') or '-'}")
    err_console.print(f"  space_id = {result.get('space_id') or '-'}")


def list_gateway_spaces(as_json: bool = JSON_OPTION):
    """List the spaces visible to the Gateway bootstrap session.

    Falls back to the locally cached list when the upstream API is
    unavailable (e.g. rate-limited), so the operator always sees something
    actionable.
    """
    from . import gateway as _gateway_cmd

    _spaces_payload_fn = getattr(_gateway_cmd, "_spaces_payload", _spaces_payload)
    payload = _spaces_payload_fn()
    if as_json:
        print_json(payload)
        return

    spaces = payload.get("spaces") or []
    active_id = payload.get("active_space_id")
    if not spaces:
        err_console.print("[yellow]No spaces available.[/yellow]")
        if payload.get("error"):
            err_console.print(f"  error = {payload['error']}")
        return

    rows = []
    for space in spaces:
        sid = str(space.get("id") or "")
        rows.append(
            {
                "current": "*" if sid and sid == active_id else "",
                "name": str(space.get("name") or sid),
                "space_id": sid,
                "slug": str(space.get("slug") or "") or "-",
            }
        )
    print_table(
        ["", "Name", "Space ID", "Slug"],
        rows,
        keys=["current", "name", "space_id", "slug"],
    )
    if payload.get("error"):
        marker = "cached" if payload.get("cached") else "session-only"
        err_console.print(f"[dim]Upstream unavailable ({marker}): {payload['error']}[/dim]")
