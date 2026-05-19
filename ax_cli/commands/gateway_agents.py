"""ax gateway agents — agent CRUD helpers and the matching `agents` commands.

Extracted from ``commands/gateway.py`` per issue #28 Phase 1. Owns the
managed-agent registration / update / archive / restore / recover /
remove flow plus the workspace context-file writers that the runtime
launches need. The matching CLI commands (``add``, ``update``, ``list``,
``archive``, ``restore``, ``recover``, ``remove``) live here too; the
remaining ``agents`` sub-app commands stay in ``commands/gateway.py``
until later phases.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import typer

from .. import gateway as gateway_core
from ..client import AxClient
from ..commands.bootstrap import (
    _create_agent_in_space,
    _find_agent_in_space,
    _mint_agent_pat,
    _polish_metadata,
)
from ..config import resolve_space_id
from ..gateway import (
    activity_log_path,
    agent_token_path,
    annotate_runtime_health,
    archive_stale_gateway_approvals,
    deny_gateway_approval,
    ensure_gateway_identity_binding,
    ensure_local_asset_binding,
    evaluate_runtime_attestation,
    find_agent_entry,
    gateway_dir,
    get_gateway_approval,
    hermes_setup_status,
    load_gateway_managed_agent_token,
    load_gateway_registry,
    ollama_setup_status,
    record_gateway_activity,
    remove_agent_entry,
    save_gateway_registry,
    upsert_agent_entry,
)
from ..gateway_runtime_types import agent_template_definition, runtime_type_definition
from ..output import JSON_OPTION, err_console, print_json, print_table

_UNSET = object()

_AGENT_CONTEXT_MARKER_BEGIN = "<!-- BEGIN ax-gateway-agent-context (auto-generated; do not edit by hand) -->"
_AGENT_CONTEXT_MARKER_END = "<!-- END ax-gateway-agent-context -->"


# ---------------------------------------------------------------------------
# Agents-list cache: serves last-good upstream response when paxai.app
# rate-limits us, mirroring the spaces cache pattern in PR #148. The cache
# is best-effort — write/read failures are swallowed; we never fail a
# request because we couldn't update cache.
# ---------------------------------------------------------------------------


def _agents_cache_path() -> Path:
    return gateway_dir() / "agents.cache.json"


def _load_agents_cache() -> list[dict]:
    try:
        raw = json.loads(_agents_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("agents") if isinstance(raw, dict) else raw
    return [item for item in (items or []) if isinstance(item, dict)]


def _save_agents_cache(agents: list[dict]) -> None:
    payload = {"agents": agents, "saved_at": datetime.now(timezone.utc).isoformat()}
    try:
        _agents_cache_path().write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def _save_agent_token(name: str, token: str) -> Path:
    token_path = agent_token_path(name)
    token_path.write_text(token.strip() + "\n")
    token_path.chmod(0o600)
    return token_path


def _load_managed_agent_or_exit(name: str) -> dict:
    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        err_console.print(f"[red]Managed agent not found:[/red] {name}")
        raise typer.Exit(1)
    return entry


def _registry_ref_for_agent(registry: dict, target: dict) -> str | None:
    target_name = str(target.get("name") or "").lower()
    target_install_id = str(target.get("install_id") or "")
    for index, entry in enumerate(registry.get("agents", []), start=1):
        if (
            entry is target
            or (target_name and str(entry.get("name") or "").lower() == target_name)
            or (target_install_id and str(entry.get("install_id") or "") == target_install_id)
        ):
            return f"#{index}"
    return None


def _with_registry_refs(registry: dict, agent: dict) -> dict:
    annotated = dict(agent)
    ref = _registry_ref_for_agent(registry, agent)
    if ref:
        annotated["registry_ref"] = ref
        annotated["registry_index"] = int(ref.lstrip("#"))
    install_id = str(annotated.get("install_id") or "")
    if install_id:
        annotated["registry_code"] = install_id[:8]
    return annotated


def _load_managed_agent_client(entry: dict) -> AxClient:
    # Late-lookup so tests that monkeypatch ``gateway_cmd.AxClient`` continue
    # to work after this helper moved out of commands/gateway.py.
    from . import gateway as _gateway_cmd

    cls = getattr(_gateway_cmd, "AxClient", AxClient)
    try:
        token = load_gateway_managed_agent_token(entry)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    return cls(
        base_url=str(entry.get("base_url") or ""),
        token=token,
        agent_name=str(entry.get("name") or ""),
        agent_id=str(entry.get("agent_id") or "") or None,
    )


def _normalize_runtime_type(runtime_type: str) -> str:
    try:
        return str(runtime_type_definition(runtime_type)["id"])
    except KeyError as exc:
        raise ValueError(
            "Unsupported runtime type. Use echo, exec, hermes_plugin, hermes_sentinel, sentinel_cli, claude_code_channel, or inbox."
        ) from exc


def _validate_runtime_registration(runtime_type: str, exec_cmd: str | None) -> None:
    definition = runtime_type_definition(runtime_type)
    required = set(definition.get("requires") or [])
    if "exec_command" in required and not exec_cmd:
        raise ValueError("Exec runtimes require --exec.")
    if "exec_command" not in required and exec_cmd:
        raise ValueError("This runtime does not accept --exec.")


def _normalize_timeout_seconds(timeout_seconds: int | None) -> int | None:
    if timeout_seconds is None:
        return None
    try:
        normalized = int(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Timeout must be a whole number of seconds.") from exc
    if normalized < 1:
        raise ValueError("Timeout must be at least 1 second.")
    return normalized


def _resolve_system_prompt_input(
    *, system_prompt: str | None, system_prompt_file: str | None, current: str | None = None
) -> str | None:
    """Resolve the operator's system-prompt input from either a literal value
    or a file path. Mutual exclusion: only one of ``--system-prompt`` /
    ``--system-prompt-file`` may be set per call.

    Returns the resolved text, or ``current`` (the existing entry value) when
    neither flag was supplied. An empty string from either source is treated
    as "clear the prompt" and returns ``""``; ``None`` means "no change".
    """
    if system_prompt is not None and system_prompt_file is not None:
        raise ValueError("--system-prompt and --system-prompt-file are mutually exclusive.")
    if system_prompt_file is not None:
        path = Path(system_prompt_file).expanduser()
        if not path.is_file():
            raise ValueError(f"System prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()
    if system_prompt is not None:
        return system_prompt.strip()
    return current


def _register_managed_agent(
    *,
    name: str,
    runtime_type: str | None = None,
    template_id: str | None = None,
    exec_cmd: str | None = None,
    workdir: str | None = None,
    ollama_model: str | None = None,
    space_id: str | None = None,
    audience: str = "both",
    description: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    timeout_seconds: int | None = None,
    allow_all_users: bool = False,
    allowed_users: str | None = None,
    start: bool = True,
) -> dict:
    from . import gateway as _gateway_cmd

    _load_client = getattr(_gateway_cmd, "_load_gateway_user_client")
    _load_session = getattr(_gateway_cmd, "_load_gateway_session_or_exit")
    _existing_home = getattr(_gateway_cmd, "_existing_agent_home_space")
    _resolve_home = getattr(_gateway_cmd, "_resolve_gateway_agent_home_space")
    _retry = getattr(_gateway_cmd, "_with_upstream_429_retry")
    _retries = getattr(_gateway_cmd, "INTERACTIVE_429_MAX_RETRIES")
    _base_wait = getattr(_gateway_cmd, "INTERACTIVE_429_BASE_WAIT")
    _find_in_space = getattr(_gateway_cmd, "_find_agent_in_space", _find_agent_in_space)
    _create_in_space = getattr(_gateway_cmd, "_create_agent_in_space", _create_agent_in_space)
    _mint_pat = getattr(_gateway_cmd, "_mint_agent_pat", _mint_agent_pat)
    _polish = getattr(_gateway_cmd, "_polish_metadata", _polish_metadata)
    _save_token = getattr(_gateway_cmd, "_save_agent_token", _save_agent_token)
    _write_workspace = getattr(_gateway_cmd, "_write_agent_workspace_config", _write_agent_workspace_config)
    _ollama_status = getattr(_gateway_cmd, "ollama_setup_status", ollama_setup_status)

    name = name.strip()
    if not name:
        raise ValueError("Managed agent name is required.")
    template = None
    explicit_workdir = str(workdir or "").strip() or None
    if template_id:
        try:
            template = agent_template_definition(template_id)
        except KeyError as exc:
            raise ValueError(f"Unknown template: {template_id}") from exc
        if not bool(template.get("launchable", True)):
            raise ValueError(f"Template {template['label']} is not launchable yet.")
        defaults = template.get("defaults") or {}
        runtime_type = runtime_type or str(defaults.get("runtime_type") or "")
        exec_cmd = exec_cmd or (str(defaults.get("exec_command") or "").strip() or None)
        workdir = workdir or (str(defaults.get("workdir") or "").strip() or None)
        if "start" in defaults:
            start = bool(defaults.get("start"))
    runtime_type = runtime_type or "echo"
    runtime_type = _normalize_runtime_type(runtime_type)
    normalized_ollama_model = str(ollama_model or "").strip() or None
    template_effective_id = str(template.get("id") if template else "").strip().lower()
    if normalized_ollama_model and template_effective_id != "ollama":
        raise ValueError("--ollama-model is only supported with the Ollama template.")
    if template_effective_id == "ollama" and not normalized_ollama_model:
        normalized_ollama_model = str(_ollama_status().get("recommended_model") or "").strip() or None
    if template_effective_id in {"hermes", "sentinel_cli", "claude_code_channel"} and not explicit_workdir:
        raise ValueError(
            f"Template {template['label']} requires --workdir so Gateway can bind the agent to its runtime folder."
        )
    _validate_runtime_registration(runtime_type, exec_cmd)
    timeout_effective = _normalize_timeout_seconds(timeout_seconds)

    client = _load_client()
    session = _load_session()
    registry = load_gateway_registry()
    existing_home_space = _existing_home(client, name) if not space_id else None
    selected_space = _resolve_home(
        client=client,
        session=session,
        registry=registry,
        explicit_space_id=space_id or existing_home_space,
    )
    existing = _retry(
        lambda: _find_in_space(client, name, selected_space),
        max_retries=_retries,
        base_wait=_base_wait,
    )
    if existing:
        agent = existing
        if description or model:
            _retry(
                lambda: client.update_agent(
                    name, **{k: v for k, v in {"description": description, "model": model}.items() if v}
                ),
                max_retries=_retries,
                base_wait=_base_wait,
            )
    else:
        agent = _retry(
            lambda: _create_in_space(
                client,
                name=name,
                space_id=selected_space,
                description=description,
                model=model,
            ),
            max_retries=_retries,
            base_wait=_base_wait,
        )
    normalized_system_prompt = (system_prompt or "").strip() or None
    _polish(client, name=name, bio=None, specialization=None, system_prompt=normalized_system_prompt)

    agent_id = str(agent.get("id") or agent.get("agent_id") or "")
    token, pat_source = _retry(
        lambda: _mint_pat(
            client,
            agent_id=agent_id,
            agent_name=name,
            audience=audience,
            expires_in_days=90,
            pat_name=f"gateway-{name}",
            space_id=selected_space,
        ),
        max_retries=_retries,
        base_wait=_base_wait,
    )
    token_file = _save_token(name, token)

    requires_approval = bool((template or {}).get("requires_approval", False))
    entry_payload = {
        "name": name,
        "template_id": template.get("id") if template else None,
        "template_label": template.get("label") if template else None,
        "agent_id": agent_id,
        "space_id": selected_space,
        "base_url": session["base_url"],
        "runtime_type": runtime_type,
        "exec_command": exec_cmd,
        "workdir": workdir,
        "ollama_model": normalized_ollama_model,
        "timeout_seconds": timeout_effective,
        "token_file": str(token_file),
        "desired_state": "running" if start else "stopped",
        "effective_state": "stopped",
        "transport": "gateway",
        "credential_source": "gateway",
        "last_error": None,
        "backlog_depth": 0,
        "processed_count": 0,
        "dropped_count": 0,
        "pat_source": pat_source,
        "requires_approval": requires_approval,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    if normalized_system_prompt:
        entry_payload["system_prompt"] = normalized_system_prompt
    if allow_all_users:
        entry_payload["allow_all_users"] = True
    if allowed_users and str(allowed_users).strip():
        entry_payload["allowed_users"] = str(allowed_users).strip()
    if requires_approval:
        entry_payload["install_id"] = str(uuid.uuid4())
    entry = upsert_agent_entry(registry, entry_payload)
    if not requires_approval:
        ensure_local_asset_binding(registry, entry, created_via="cli", auto_approve=True)
    ensure_gateway_identity_binding(registry, entry, session=session, created_via="cli")
    entry.update(evaluate_runtime_attestation(registry, entry))
    _write_workspace(entry)
    hermes_status = hermes_setup_status(entry)
    if not hermes_status.get("ready", True):
        entry["effective_state"] = "error"
        entry["last_error"] = str(
            hermes_status.get("detail") or hermes_status.get("summary") or "Hermes setup is incomplete."
        )
        entry["current_activity"] = str(hermes_status.get("summary") or "Hermes setup is incomplete.")
    elif hermes_status.get("resolved_path"):
        entry["hermes_repo_path"] = str(hermes_status["resolved_path"])
    save_gateway_registry(registry)
    record_gateway_activity(
        "managed_agent_added",
        entry=entry,
        space_id=selected_space,
        token_file=str(token_file),
    )
    return annotate_runtime_health(entry, registry=registry)


def _agent_workspace_context_text(entry: dict, *, workdir: str) -> str:
    name = str(entry.get("name") or "agent").strip()
    template = str(entry.get("template_id") or entry.get("runtime_type") or "gateway").strip()
    runtime = str(entry.get("runtime_type") or "gateway").strip()
    operator_prompt = str(entry.get("system_prompt") or "").strip()
    persona_section = (
        f"""## Operator-supplied role instructions

The operator registered this agent with the following system prompt. These
take precedence over the generic guidance below. They were passed to the
runtime via `--system-prompt` (Hermes / OpenAI-compatible) or
`--append-system-prompt` (Claude Code).

```
{operator_prompt}
```

"""
        if operator_prompt
        else """## Operator-supplied role instructions

No operator-supplied system prompt is configured for this agent. To set one,
run from your control workspace:

```bash
ax gateway agents update {name} --system-prompt "Your role instructions..."
# or, from a file:
ax gateway agents update {name} --system-prompt-file ./role.md
```

""".replace("{name}", name)
    )
    return f"""# aX Agent Context

You are `@{name}`, an agent connected to the aX multi-user, multi-agent network through the local Gateway.

Identity and runtime:

- Agent name: `@{name}`
- Agent type: `{template}`
- Runtime: `{runtime}`
- Runtime folder: `{workdir}`
- Gateway URL: `http://127.0.0.1:8765`

{persona_section}## How to use aX from this folder

```bash
ax gateway local connect --workdir .
ax gateway local inbox --workdir .
ax gateway local send --workdir . "@agent_name message"
```

## Guidelines

- Use the Gateway CLI from this folder for aX messages, inbox checks, tasks, and context.
- Do not ask the user for a PAT and do not store user tokens in this folder.
- If Gateway says approval is required, tell the user to open `http://127.0.0.1:8765` and approve the pending binding.
- Treat aX as your shared agent network: messages may come from users, service accounts, or other agents.
- Keep replies concise unless the task needs detail, and surface useful progress through the runtime when possible.
- Keep self-description updates, preferences, avatar metadata, and capability notes aligned with Gateway-backed agent settings as those commands become available.
"""


def _agent_workspace_readme_text(entry: dict, *, workdir: str) -> str:
    name = str(entry.get("name") or "agent").strip()
    template = str(entry.get("template_id") or entry.get("runtime_type") or "gateway").strip()
    return f"""# aX Gateway Agent

This folder is registered with the local aX Gateway as `@{name}`.

- Agent type: `{template}`
- Runtime folder: `{workdir}`
- Gateway URL: `http://127.0.0.1:8765`

Read `.ax/AGENT_CONTEXT.md` first. It explains your aX identity and the Gateway CLI path.

Use the Gateway CLI from this folder when you need platform context:

```bash
ax gateway local connect --workdir .
ax gateway local inbox --workdir .
ax gateway local send --workdir . "@agent_name message"
```

Do not add a user PAT here. Gateway owns credential minting and the local
fingerprint binding for this agent. Keep self-description updates, preferences,
avatar metadata, and capability notes in Gateway-backed agent settings as those
commands become available.
"""


def _write_agent_context_hint(path: Path, *, agent_name: str, context_path: Path) -> None:
    if path.exists():
        return
    path.write_text(
        "\n".join(
            [
                f"# {agent_name} on aX",
                "",
                "This workspace is connected to aX through the local Gateway.",
                f"Read `{context_path}` before using aX tools.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _render_agent_persona_markdown(entry: dict, *, workdir: str) -> str:
    """Body of the auto-generated section that's written into the runtime's
    native context file (CLAUDE.md for Claude Code, AGENTS.md for Hermes).

    Layout: operator-supplied role first (the agent's identity), then the
    generic aX network/CLI guidance the agent needs to collaborate. Mirrors
    `_compose_agent_system_prompt` in ax_cli/gateway.py — same ordering, so
    what the runtime gets via `--system-prompt` matches what the human sees
    in the workdir doc.
    """
    name = str(entry.get("name") or "agent").strip()
    operator_prompt = str(entry.get("system_prompt") or "").strip()
    persona_block = (
        f"## Role\n\n{operator_prompt}\n"
        if operator_prompt
        else (
            "## Role\n\n"
            "_No operator-supplied system prompt is configured for this agent._\n\n"
            "To set one, from your control workspace run:\n\n"
            "```bash\n"
            f'ax gateway agents update {name} --system-prompt "Your role instructions..."\n'
            "```\n"
        )
    )
    return f"""# `@{name}` — aX agent context

You are `@{name}`, an agent on the aX multi-agent network. Other agents may
@-mention you. The Gateway daemon brokers your credentials; you don't manage
tokens directly.

- Workdir: `{workdir}`
- Gateway: http://127.0.0.1:8765

{persona_block}
## Collaboration model

- Reply on the same thread by passing the incoming message_id as parent_id.
- @-mention other agents by name to delegate or ask for help.
- See who is online, route work, and read your inbox via the CLI below.

## CLI

```bash
ax send "@target your message"           # send a new message
ax send -p <message_id> "..."             # reply on a thread
ax messages list                           # read your inbox
ax tasks create "title" --assign-to <agent>  # delegate work
ax tasks list                              # open tasks for you
ax agents list                             # see who is online
```
"""


def _write_marker_section(path: Path, *, body: str) -> None:
    """Idempotently install or refresh the auto-generated agent-context
    section in the given file.

    - File missing: write a new file containing only the section.
    - File exists with the markers: replace the section in place.
    - File exists without the markers: prepend the section so the LLM sees
      the persona before any user content. Preserves user content.
    """
    section = f"{_AGENT_CONTEXT_MARKER_BEGIN}\n\n{body.rstrip()}\n\n{_AGENT_CONTEXT_MARKER_END}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(section, encoding="utf-8")
        return
    existing = path.read_text(encoding="utf-8")
    if _AGENT_CONTEXT_MARKER_BEGIN in existing and _AGENT_CONTEXT_MARKER_END in existing:
        head, _, rest = existing.partition(_AGENT_CONTEXT_MARKER_BEGIN)
        _, _, tail = rest.partition(_AGENT_CONTEXT_MARKER_END)
        # Strip the leftover newline immediately after the end marker so the
        # tail re-attaches cleanly. Preserve the rest of tail verbatim.
        if tail.startswith("\n"):
            tail = tail[1:]
        path.write_text(head + section + tail, encoding="utf-8")
        return
    # No markers — prepend so the persona is the first thing the LLM reads.
    path.write_text(section + "\n" + existing, encoding="utf-8")


def _agent_runtime_context_target(entry: dict, *, workdir: Path) -> Path | None:
    """Map a managed-agent entry to the runtime-native context file.

    Claude Code reads CLAUDE.md from the workdir; Hermes' sentinel reads
    AGENTS.md (with CLAUDE.md fallback). Returns None for templates that
    don't have a workdir-based runtime convention.
    """
    template = str(entry.get("template_id") or "").strip().lower()
    runtime = str(entry.get("runtime_type") or "").strip().lower()
    if template == "claude_code_channel" or runtime == "claude_code_channel":
        return workdir / "CLAUDE.md"
    if template in {"hermes", "sentinel_cli"} or runtime in {"hermes_sentinel", "sentinel_cli"}:
        return workdir / "AGENTS.md"
    return None


def _write_agent_workspace_config(entry: dict) -> None:
    from . import gateway as _gateway_cmd

    _local_config_text = getattr(_gateway_cmd, "_gateway_local_config_text")
    template = str(entry.get("template_id") or "").strip().lower()
    runtime = str(entry.get("runtime_type") or "").strip().lower()
    if template not in {"hermes", "sentinel_cli", "claude_code_channel"} and runtime not in {
        "hermes_sentinel",
        "sentinel_cli",
        "claude_code_channel",
    }:
        return
    workdir = str(entry.get("workdir") or "").strip()
    name = str(entry.get("name") or "").strip()
    if not workdir or not name:
        return
    root = Path(workdir).expanduser().resolve()
    config_dir = root / ".ax"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        _local_config_text(agent_name=name, gateway_url="http://127.0.0.1:8765", workdir=str(root))
    )
    (config_dir / "config.toml").chmod(0o600)
    (config_dir / "README.md").write_text(_agent_workspace_readme_text(entry, workdir=str(root)))
    context_path = config_dir / "AGENT_CONTEXT.md"
    context_path.write_text(_agent_workspace_context_text(entry, workdir=str(root)), encoding="utf-8")

    # Also write the persona into the file the runtime reads natively
    # (CLAUDE.md for Claude Code, AGENTS.md for Hermes). Use a marker-bounded
    # section so user-authored content in those files is preserved on re-write.
    target = _agent_runtime_context_target(entry, workdir=root)
    if target is not None:
        _write_marker_section(target, body=_render_agent_persona_markdown(entry, workdir=str(root)))


def _update_managed_agent(
    *,
    name: str,
    template_id: str | None = None,
    runtime_type: str | None = None,
    exec_cmd: str | object = _UNSET,
    workdir: str | object = _UNSET,
    ollama_model: str | object = _UNSET,
    description: str | None = None,
    model: str | None = None,
    system_prompt: str | object = _UNSET,
    timeout_seconds: int | object = _UNSET,
    allow_all_users: bool | object = _UNSET,
    allowed_users: str | object = _UNSET,
    desired_state: str | None = None,
) -> dict:
    from . import gateway as _gateway_cmd

    _load_session = getattr(_gateway_cmd, "_load_gateway_session_or_exit")
    _load_client = getattr(_gateway_cmd, "_load_gateway_user_client")
    _write_workspace = getattr(_gateway_cmd, "_write_agent_workspace_config", _write_agent_workspace_config)

    name = name.strip()
    if not name:
        raise ValueError("Managed agent name is required.")

    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")

    template = None
    if template_id:
        try:
            template = agent_template_definition(template_id)
        except KeyError as exc:
            raise ValueError(f"Unknown template: {template_id}") from exc
        if not bool(template.get("launchable", True)):
            raise ValueError(f"Template {template['label']} is not launchable yet.")

    runtime_candidate = (
        runtime_type or (template.get("defaults") or {}).get("runtime_type") if template else runtime_type
    )
    runtime_effective = str(runtime_candidate or entry.get("runtime_type") or "echo")
    runtime_effective = _normalize_runtime_type(runtime_effective)
    template_effective_id = str(template.get("id") if template else entry.get("template_id") or "").strip().lower()

    if template:
        defaults = template.get("defaults") or {}
        exec_effective = (
            str(exec_cmd).strip() or None
            if exec_cmd is not _UNSET
            else (str(defaults.get("exec_command") or "").strip() or None)
        )
        workdir_effective = (
            str(workdir).strip() or None
            if workdir is not _UNSET
            else (str(defaults.get("workdir") or "").strip() or None)
        )
    else:
        exec_effective = (
            str(entry.get("exec_command") or "").strip() or None
            if exec_cmd is _UNSET
            else (str(exec_cmd).strip() or None)
        )
        workdir_effective = (
            str(entry.get("workdir") or "").strip() or None if workdir is _UNSET else (str(workdir).strip() or None)
        )

    if ollama_model is _UNSET:
        ollama_model_effective = str(entry.get("ollama_model") or "").strip() or None
    else:
        ollama_model_effective = str(ollama_model).strip() or None
    if ollama_model_effective and template_effective_id != "ollama":
        raise ValueError("--ollama-model is only supported with the Ollama template.")
    if template_effective_id == "ollama" and ollama_model is _UNSET and not ollama_model_effective:
        ollama_model_effective = str(ollama_setup_status().get("recommended_model") or "").strip() or None

    _validate_runtime_registration(runtime_effective, exec_effective)

    if desired_state is not None:
        normalized_desired = desired_state.lower().strip()
        if normalized_desired not in {"running", "stopped"}:
            raise ValueError("Desired state must be running or stopped.")
        entry["desired_state"] = normalized_desired
    if timeout_seconds is not _UNSET:
        entry["timeout_seconds"] = _normalize_timeout_seconds(timeout_seconds)  # type: ignore[arg-type]

    session = _load_session()
    upstream_fields: dict = {}
    if description:
        upstream_fields["description"] = description
    if model:
        upstream_fields["model"] = model
    if system_prompt is not _UNSET:
        sp_value = str(system_prompt).strip() if system_prompt else ""  # type: ignore[arg-type]
        upstream_fields["system_prompt"] = sp_value or None
    if upstream_fields:
        client = _load_client()
        client.update_agent(name, **upstream_fields)
    if system_prompt is not _UNSET:
        sp_value = str(system_prompt).strip() if system_prompt else ""  # type: ignore[arg-type]
        if sp_value:
            entry["system_prompt"] = sp_value
        else:
            entry.pop("system_prompt", None)

    if template:
        entry["template_id"] = template.get("id")
        entry["template_label"] = template.get("label")
    entry["runtime_type"] = runtime_effective
    entry["exec_command"] = exec_effective
    entry["workdir"] = workdir_effective
    if allow_all_users is not _UNSET:
        if allow_all_users:
            entry["allow_all_users"] = True
        else:
            entry.pop("allow_all_users", None)
    if allowed_users is not _UNSET:
        allowed_clean = str(allowed_users or "").strip()
        if allowed_clean:
            entry["allowed_users"] = allowed_clean
        else:
            entry.pop("allowed_users", None)
    if template_effective_id == "ollama":
        entry["ollama_model"] = ollama_model_effective
    else:
        entry.pop("ollama_model", None)
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    entry.setdefault("transport", "gateway")
    entry.setdefault("credential_source", "gateway")

    if template and template.get("id") != "hermes":
        entry.pop("hermes_repo_path", None)

    ensure_gateway_identity_binding(registry, entry, session=session)
    ensure_local_asset_binding(registry, entry, created_via="cli", auto_approve=True, replace_existing=True)
    entry.update(evaluate_runtime_attestation(registry, entry))
    _write_workspace(entry)
    hermes_status = hermes_setup_status(entry)
    if not hermes_status.get("ready", True):
        entry["effective_state"] = "error"
        entry["last_error"] = str(
            hermes_status.get("detail") or hermes_status.get("summary") or "Hermes setup is incomplete."
        )
        entry["current_activity"] = str(hermes_status.get("summary") or "Hermes setup is incomplete.")
    elif hermes_status.get("resolved_path"):
        entry["hermes_repo_path"] = str(hermes_status["resolved_path"])

    save_gateway_registry(registry)
    record_gateway_activity(
        "managed_agent_updated",
        entry=entry,
        template_id=entry.get("template_id"),
        runtime_type=runtime_effective,
        workdir=workdir_effective,
        exec_command=exec_effective,
        desired_state=entry.get("desired_state"),
        timeout_seconds=entry.get("timeout_seconds"),
    )
    return annotate_runtime_health(entry, registry=registry)


def _hide_managed_agents(names: list[str], *, reason: str = "operator_cleanup") -> dict:
    normalized_names = []
    seen = set()
    for raw_name in names:
        name = str(raw_name or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        normalized_names.append(name)
        seen.add(key)
    if not normalized_names:
        raise ValueError("Choose at least one managed agent to hide.")

    registry = load_gateway_registry()
    hidden: list[dict] = []
    missing: list[str] = []
    hidden_reason = str(reason or "").strip() or "operator_cleanup"
    hidden_at = gateway_core._now_iso()
    for name in normalized_names:
        entry = find_agent_entry(registry, name)
        if not entry:
            missing.append(name)
            continue
        if str(entry.get("desired_state") or "").strip().lower() != "stopped":
            entry["desired_state_before_hide"] = entry.get("desired_state") or "running"
        entry["desired_state"] = "stopped"
        entry["lifecycle_phase"] = "hidden"
        entry["hidden_at"] = hidden_at
        entry["hidden_reason"] = hidden_reason
        hidden.append(entry)

    save_gateway_registry(registry)
    for entry in hidden:
        record_gateway_activity(
            "managed_agent_hidden",
            entry=entry,
            hidden_reason=hidden_reason,
            operator_action=True,
        )
    return {
        "count": len(hidden),
        "missing": missing,
        "hidden": [annotate_runtime_health(entry, registry=registry) for entry in hidden],
    }


def _restore_hidden_managed_agents(names: list[str]) -> dict:
    """Symmetric inverse of _hide_managed_agents.

    Clears lifecycle_phase=hidden + hide bookkeeping, restores desired_state
    to whatever the operator-driven hide had captured (desired_state_before_hide).
    Refuses to restore agents that are not in the hidden phase — the
    archived phase has its own restore path (PR #147), and "active" agents
    don't need restoration.
    """
    normalized_names: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = str(raw_name or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        normalized_names.append(name)
        seen.add(key)
    if not normalized_names:
        raise ValueError("Choose at least one managed agent to restore.")

    registry = load_gateway_registry()
    restored: list[dict] = []
    missing: list[str] = []
    not_hidden: list[str] = []
    for name in normalized_names:
        entry = find_agent_entry(registry, name)
        if not entry:
            missing.append(name)
            continue
        if str(entry.get("lifecycle_phase") or "") != "hidden":
            not_hidden.append(name)
            continue
        prior = str(entry.get("desired_state_before_hide") or "").strip() or "running"
        entry["lifecycle_phase"] = "active"
        entry["desired_state"] = prior
        entry.pop("desired_state_before_hide", None)
        entry.pop("hidden_at", None)
        entry.pop("hidden_reason", None)
        restored.append(entry)

    save_gateway_registry(registry)
    for entry in restored:
        record_gateway_activity(
            "managed_agent_unhidden",
            entry=entry,
            operator_action=True,
        )
    return {
        "count": len(restored),
        "missing": missing,
        "not_hidden": not_hidden,
        "restored": [annotate_runtime_health(entry, registry=registry) for entry in restored],
    }


def _read_recovery_evidence(name: str) -> dict | None:
    """Reconstruct a minimal registry row for an agent from local evidence.

    Used when a managed_agent_added activity event was recorded but the
    registry row was lost (pre-race-fix damage). Reads from three sources,
    all verifiable:

    - Activity log: most recent managed_agent_added for ``name`` →
      agent_id, asset_id, install_id, gateway_id, runtime_type,
      transport, space_id, token_file, credential_source, ts.
    - Token directory: ``~/.ax/gateway/agents/<name>/token`` must exist
      (we don't fabricate credentials).
    - Workdir ``.ax/AGENT_CONTEXT.md`` if present, for the workdir hint.

    Returns None if no managed_agent_added event is recorded or the
    token file is missing — both required for a safe recovery.
    """
    target_event: dict | None = None
    activity_path = activity_log_path()
    try:
        with activity_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if ev.get("agent_name") != name or ev.get("event") != "managed_agent_added":
                    continue
                target_event = ev  # later writes win — pick the most recent
    except OSError:
        return None
    if not isinstance(target_event, dict):
        return None
    token_file = str(target_event.get("token_file") or "").strip()
    if not token_file or not Path(token_file).is_file():
        return None
    return target_event


def _recover_managed_agents_from_evidence(names: list[str]) -> dict:
    """Recover registry rows for agents present locally (token + activity)
    but absent from registry.json (pre-race-fix row loss).

    Refuses to recover agents that are already in the registry — use
    archive/restore or hide/unhide for state changes on existing rows.
    The reconstructed row is minimal: enough fields for the daemon to
    pick it up on next reconcile and hydrate the rest from upstream.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in names:
        n = str(raw or "").strip()
        if not n or n.lower() in seen:
            continue
        normalized.append(n)
        seen.add(n.lower())
    if not normalized:
        raise ValueError("Choose at least one agent to recover.")

    registry = load_gateway_registry()
    recovered: list[dict] = []
    already_present: list[str] = []
    no_evidence: list[str] = []

    for name in normalized:
        if find_agent_entry(registry, name) is not None:
            already_present.append(name)
            continue
        evidence = _read_recovery_evidence(name)
        if evidence is None:
            no_evidence.append(name)
            continue
        # Build minimal row — sourced fields only.
        entry: dict = {
            "name": name,
            "agent_id": str(evidence.get("agent_id") or "").strip(),
            "asset_id": str(evidence.get("asset_id") or evidence.get("agent_id") or "").strip(),
            "install_id": str(evidence.get("install_id") or "").strip(),
            "gateway_id": str(evidence.get("gateway_id") or "").strip(),
            "runtime_type": str(evidence.get("runtime_type") or "").strip(),
            "transport": str(evidence.get("transport") or "gateway").strip(),
            "credential_source": str(evidence.get("credential_source") or "gateway").strip(),
            "token_file": str(evidence.get("token_file") or "").strip(),
            "space_id": str(evidence.get("space_id") or "").strip(),
            "added_at": str(evidence.get("ts") or "").strip(),
            "lifecycle_phase": "active",
            "desired_state": "stopped",  # safe default — operator restarts deliberately
            "drift_reason": "registry_row_recovered_from_evidence",
        }
        # Pick a sensible template_id from runtime_type; daemon hydrates from
        # upstream on reconcile.
        rt = entry["runtime_type"]
        if rt == "claude_code_channel":
            entry["template_id"] = "claude_code_channel"
            entry["template_label"] = "Claude Code Channel"
        elif rt == "hermes_sentinel":
            entry["template_id"] = "hermes"
            entry["template_label"] = "Hermes"
        elif rt == "inbox":
            entry["template_id"] = "pass_through"
            entry["template_label"] = "Pass-through"
        registry.setdefault("agents", []).append(entry)
        recovered.append(entry)

    save_gateway_registry(registry)
    for entry in recovered:
        record_gateway_activity(
            "managed_agent_recovered",
            entry=entry,
            operator_action=True,
            recovery_source="local_evidence",
        )

    return {
        "count": len(recovered),
        "already_present": already_present,
        "no_evidence": no_evidence,
        "recovered": [annotate_runtime_health(entry, registry=registry) for entry in recovered],
    }


def _archive_managed_agent(name: str, *, reason: str | None = None, client_factory=None) -> dict:
    """Archive a managed agent. Sticky — sweep won't auto-restore.

    Sets `lifecycle_phase=archived` and `desired_state=stopped` so the daemon
    reconciler stops the runtime. Captures `desired_state_before_archive` so
    `restore` can put it back. Best-effort upstream signal `archived`. The
    local registry is authoritative; upstream failure is logged, never fatal.
    """
    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")
    if str(entry.get("lifecycle_phase") or "active") == "archived":
        return annotate_runtime_health(entry, registry=registry)
    prior_desired_state = str(entry.get("desired_state") or "running")
    entry["lifecycle_phase"] = "archived"
    entry["archived_at"] = datetime.now(timezone.utc).isoformat()
    if reason and str(reason).strip():
        entry["archived_reason"] = str(reason).strip()[:240]
    else:
        entry.pop("archived_reason", None)
    entry["desired_state_before_archive"] = prior_desired_state
    entry["desired_state"] = "stopped"
    save_gateway_registry(registry, merge_archive=False)
    record_gateway_activity(
        "managed_agent_archived",
        entry=entry,
        reason=str(reason).strip() if reason else None,
    )
    return annotate_runtime_health(entry, registry=registry)


def _restore_managed_agent(name: str, *, client_factory=None) -> dict:
    """Restore an archived agent to active. Honors prior desired_state.

    If `desired_state_before_archive` was captured at archive time, the
    runtime restores to that state. Otherwise defaults to `stopped` (safer
    than auto-resuming a runtime the operator may have intentionally
    disabled). Best-effort upstream signal `connected`.
    """
    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")
    if str(entry.get("lifecycle_phase") or "active") != "archived":
        return annotate_runtime_health(entry, registry=registry)
    prior = str(entry.get("desired_state_before_archive") or "stopped")
    entry["lifecycle_phase"] = "active"
    entry.pop("archived_at", None)
    entry.pop("archived_reason", None)
    entry.pop("desired_state_before_archive", None)
    entry["desired_state"] = prior if prior in {"running", "stopped"} else "stopped"
    save_gateway_registry(registry, merge_archive=False)
    record_gateway_activity("managed_agent_restored", entry=entry)
    return annotate_runtime_health(entry, registry=registry)


def _remove_managed_agent(name: str, *, client_factory=None) -> dict:
    from . import gateway as _gateway_cmd

    _silent_client = getattr(_gateway_cmd, "_build_session_client_silent")

    registry = load_gateway_registry()
    peek = find_agent_entry(registry, name)
    if not peek:
        raise LookupError(f"Managed agent not found: {name}")
    # Best-effort upstream delete BEFORE local removal so the platform-side
    # record can be retired in lockstep. Missing session, 404, or network
    # failure are recorded as audit events but never block the local
    # removal — the local registry is authoritative for the gateway.
    agent_id = str(peek.get("agent_id") or "").strip()
    if agent_id:
        user_client = client_factory() if client_factory is not None else _silent_client()
        if user_client is not None:
            try:
                user_client.delete_agent(agent_id)
            except Exception as exc:  # noqa: BLE001
                record_gateway_activity(
                    "managed_agent_remove_upstream_failed",
                    entry=peek,
                    error=str(exc)[:360],
                )
    entry = remove_agent_entry(registry, name)
    if not entry:
        # Should be unreachable since peek succeeded; defensive only.
        raise LookupError(f"Managed agent not found: {name}")
    save_gateway_registry(registry)
    archive_stale_gateway_approvals()
    token_file_value = str(entry.get("token_file") or "").strip()
    token_file = Path(token_file_value) if token_file_value else None
    if token_file and token_file.is_file():
        token_file.unlink()
    record_gateway_activity("managed_agent_removed", entry=entry)
    return entry


def _reject_managed_agent_approval(name: str) -> dict:
    from . import gateway as _gateway_cmd

    _detail = getattr(_gateway_cmd, "_agent_detail_payload")
    _remove = getattr(_gateway_cmd, "_remove_managed_agent", _remove_managed_agent)

    detail = _detail(name, activity_limit=1)
    if detail is None:
        raise LookupError(f"Managed agent not found: {name}")
    agent = detail.get("agent") or {}
    approval_id = str(agent.get("approval_id") or "").strip()
    if not approval_id:
        raise ValueError(f"@{name} does not have a pending Gateway approval.")
    approval = get_gateway_approval(approval_id)
    rejected = deny_gateway_approval(approval_id)
    removed = None
    if (
        str(approval.get("status") or "").lower() == "pending"
        and str(approval.get("approval_kind") or "") == "new_binding"
    ):
        try:
            removed = _remove(name)
        except LookupError:
            removed = None
    return {
        "approval": rejected,
        "removed_agent": removed,
        "removed": removed is not None,
    }


def _set_managed_agent_pin(name: str, pinned: bool) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Managed agent name is required.")
    registry = load_gateway_registry()
    entry = find_agent_entry(registry, name)
    if not entry:
        raise LookupError(f"Managed agent not found: {name}")
    entry["pinned"] = bool(pinned)
    save_gateway_registry(registry)
    record_gateway_activity(
        "managed_agent_pinned" if pinned else "managed_agent_unpinned",
        entry=entry,
    )
    return annotate_runtime_health(entry, registry=registry)


# ---------------------------------------------------------------------------
# agents sub-app commands (add / update / list / archive / restore /
# recover / remove). Register against ``agents_app`` in
# ``commands/gateway.py``.
# ---------------------------------------------------------------------------


def add_agent(
    name: str = typer.Argument(..., help="Managed agent name"),
    template_id: str = typer.Option(
        None, "--template", help="Agent template: echo_test | ollama | hermes | sentinel_cli | claude_code_channel"
    ),
    runtime_type: str = typer.Option(
        None,
        "--type",
        help="Advanced/internal runtime backend: echo | exec | hermes_plugin | hermes_sentinel | sentinel_cli | claude_code_channel | inbox",
    ),
    exec_cmd: str = typer.Option(None, "--exec", help="Advanced override for exec-based templates"),
    workdir: str = typer.Option(None, "--workdir", help="Advanced working directory override"),
    ollama_model: str = typer.Option(None, "--ollama-model", help="Ollama model override for the Ollama template"),
    space_id: str = typer.Option(
        None,
        "--space",
        "--space-id",
        "-s",
        help="Target space (defaults to gateway session). Accepts a slug, name, or UUID.",
    ),
    audience: str = typer.Option("both", "--audience", help="Minted PAT audience"),
    description: str = typer.Option(None, "--description", help="Create/update description"),
    model: str = typer.Option(None, "--model", help="Create/update model"),
    system_prompt: str = typer.Option(
        None,
        "--system-prompt",
        help="Operator-supplied system instructions describing the agent's role. Appended with the gateway's environment context (multi-agent network awareness + CLI usage) when handed to the runtime.",
    ),
    system_prompt_file: str = typer.Option(
        None,
        "--system-prompt-file",
        help="Path to a file containing the system prompt. Mutually exclusive with --system-prompt.",
    ),
    timeout_seconds: int = typer.Option(
        None, "--timeout", "--timeout-seconds", help="Max seconds a runtime may process one message"
    ),
    allow_all_users: bool = typer.Option(
        False,
        "--allow-all-users",
        help=(
            "Hermes plugin runtime only: open the agent to mentions from anyone in its space. "
            "Sets AX_ALLOW_ALL_USERS=1 + GATEWAY_ALLOW_ALL_USERS=true in the scaffolded "
            "HERMES_HOME/.env. Default-closed; without this (or --allowed-users) the agent "
            "denies all incoming mentions."
        ),
    ),
    allowed_users: str = typer.Option(
        None,
        "--allowed-users",
        help="Hermes plugin runtime only: comma-separated agent/user names allowed to mention this agent.",
    ),
    start: bool = typer.Option(True, "--start/--no-start", help="Desired running state after registration"),
    as_json: bool = JSON_OPTION,
):
    """Register a managed agent and mint a Gateway-owned PAT for it.

    The ``--space`` option accepts a slug, name, or UUID. Slug/name resolution
    runs through the local space cache first; if that misses, the resolution
    falls through to the gateway user client's ``list_spaces`` lookup.
    """
    from . import gateway as _gateway_cmd

    _resolve_via_cache = getattr(_gateway_cmd, "_resolve_space_via_cache")
    _load_client = getattr(_gateway_cmd, "_load_gateway_user_client")
    _resolve_sid = getattr(_gateway_cmd, "resolve_space_id", resolve_space_id)
    _resolve_prompt = getattr(_gateway_cmd, "_resolve_system_prompt_input", _resolve_system_prompt_input)
    _register = getattr(_gateway_cmd, "_register_managed_agent", _register_managed_agent)

    if space_id:
        cached = _resolve_via_cache(space_id)
        if cached is not None:
            space_id = cached
        else:
            try:
                client = _load_client()
                space_id = _resolve_sid(client, explicit=space_id)
            except (typer.Exit, typer.BadParameter):
                raise
            except Exception as exc:
                err_console.print(f"[red]Could not resolve space '{space_id}': {exc}[/red]")
                raise typer.Exit(1)
    selected_template = template_id or ("echo_test" if not runtime_type else None)
    try:
        resolved_prompt = _resolve_prompt(
            system_prompt=system_prompt,
            system_prompt_file=system_prompt_file,
            current=None,
        )
        entry = _register(
            name=name,
            template_id=selected_template,
            runtime_type=runtime_type,
            exec_cmd=exec_cmd,
            workdir=workdir,
            ollama_model=ollama_model,
            space_id=space_id,
            audience=audience,
            description=description,
            model=model,
            system_prompt=resolved_prompt,
            timeout_seconds=timeout_seconds,
            allow_all_users=allow_all_users,
            allowed_users=allowed_users,
            start=start,
        )
    except (ValueError, LookupError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        print_json(entry)
    else:
        err_console.print(f"[green]Managed agent ready:[/green] @{name}")
        if entry.get("template_label"):
            err_console.print(f"  type = {entry['template_label']}")
        if entry.get("asset_type_label"):
            err_console.print(f"  asset = {entry['asset_type_label']}")
        err_console.print(f"  desired_state = {entry['desired_state']}")
        if entry.get("timeout_seconds"):
            err_console.print(f"  timeout = {entry.get('timeout_seconds')}s")
        err_console.print(f"  token_file = {entry['token_file']}")


def update_agent(
    name: str = typer.Argument(..., help="Managed agent name"),
    template_id: str = typer.Option(None, "--template", help="Replace the agent template"),
    runtime_type: str = typer.Option(
        None,
        "--type",
        help="Advanced/internal runtime backend override: echo | exec | hermes_plugin | hermes_sentinel | sentinel_cli | claude_code_channel | inbox",
    ),
    exec_cmd: str = typer.Option(None, "--exec", help="Advanced override for exec-based templates"),
    workdir: str = typer.Option(None, "--workdir", help="Advanced working directory override"),
    ollama_model: str = typer.Option(None, "--ollama-model", help="Ollama model override for the Ollama template"),
    description: str = typer.Option(None, "--description", help="Update platform agent description"),
    model: str = typer.Option(None, "--model", help="Update platform agent model"),
    system_prompt: str = typer.Option(
        None,
        "--system-prompt",
        help="Replace the operator-supplied system instructions. Pass an empty string to clear. Appended with the gateway's environment context at runtime.",
    ),
    system_prompt_file: str = typer.Option(
        None,
        "--system-prompt-file",
        help="Path to a file containing the system prompt. Mutually exclusive with --system-prompt.",
    ),
    timeout_seconds: int = typer.Option(
        None, "--timeout", "--timeout-seconds", help="Max seconds a runtime may process one message"
    ),
    allow_all_users: bool = typer.Option(
        None,
        "--allow-all-users/--no-allow-all-users",
        help=(
            "Hermes plugin runtime only: open the agent to mentions from anyone in its space "
            "(or close it back down). Sets AX_ALLOW_ALL_USERS / GATEWAY_ALLOW_ALL_USERS in "
            "the scaffolded HERMES_HOME/.env on the next start."
        ),
    ),
    allowed_users: str = typer.Option(
        None,
        "--allowed-users",
        help=(
            "Hermes plugin runtime only: comma-separated agent/user names allowed to mention this agent. "
            "Pass an empty string to clear."
        ),
    ),
    desired_state: str = typer.Option(None, "--desired-state", help="running | stopped"),
    as_json: bool = JSON_OPTION,
):
    """Update a managed agent without redoing Gateway bootstrap."""
    from . import gateway as _gateway_cmd

    _resolve_prompt = getattr(_gateway_cmd, "_resolve_system_prompt_input", _resolve_system_prompt_input)
    _update = getattr(_gateway_cmd, "_update_managed_agent", _update_managed_agent)

    try:
        prompt_unset = system_prompt is None and system_prompt_file is None
        resolved_prompt: str | object = _UNSET
        if not prompt_unset:
            resolved_prompt = (
                _resolve_prompt(
                    system_prompt=system_prompt,
                    system_prompt_file=system_prompt_file,
                    current=None,
                )
                or ""
            )
        entry = _update(
            name=name,
            template_id=template_id,
            runtime_type=runtime_type,
            exec_cmd=exec_cmd if exec_cmd is not None else _UNSET,
            workdir=workdir if workdir is not None else _UNSET,
            ollama_model=ollama_model if ollama_model is not None else _UNSET,
            description=description,
            model=model,
            system_prompt=resolved_prompt,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else _UNSET,
            allow_all_users=allow_all_users if allow_all_users is not None else _UNSET,
            allowed_users=allowed_users if allowed_users is not None else _UNSET,
            desired_state=desired_state,
        )
    except (LookupError, ValueError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        print_json(entry)
        return
    err_console.print(f"[green]Managed agent updated:[/green] @{name}")
    err_console.print(f"  type = {entry.get('template_label') or entry.get('runtime_type')}")
    err_console.print(f"  desired_state = {entry.get('desired_state')}")
    if entry.get("timeout_seconds"):
        err_console.print(f"  timeout = {entry.get('timeout_seconds')}s")


def list_agents(
    as_json: bool = JSON_OPTION,
    show_all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Include archived, hidden (auto-swept stale), and system (switchboard / service-account) agents.",
    ),
    archived_only: bool = typer.Option(
        False,
        "--archived",
        help="Show only archived (user-disabled) agents — the inactive section.",
    ),
):
    """List Gateway-managed agents."""
    from . import gateway as _gateway_cmd

    _status = getattr(_gateway_cmd, "_status_payload")
    _type_label = getattr(_gateway_cmd, "_agent_type_label")
    _output_label = getattr(_gateway_cmd, "_agent_output_label")

    payload = _status(include_hidden=show_all or archived_only)
    agents = payload["agents"]
    if archived_only:
        agents = [a for a in agents if str(a.get("lifecycle_phase") or "active") == "archived"]
    if as_json:
        print_json(
            {
                "agents": agents,
                "count": len(agents),
                "archived": payload["summary"].get("archived_agents", 0),
                "hidden": payload["summary"].get("hidden_agents", 0),
                "system": payload["summary"].get("system_agents", 0),
            }
        )
        return
    print_table(
        ["Ref", "Agent", "Type", "Mode", "Presence", "Output", "Confidence", "Space"],
        [{**agent, "type": _type_label(agent), "output": _output_label(agent)} for agent in agents],
        keys=["registry_ref", "name", "type", "mode", "presence", "output", "confidence", "space_id"],
    )
    archived_n = payload["summary"].get("archived_agents", 0)
    hidden_n = payload["summary"].get("hidden_agents", 0)
    system_n = payload["summary"].get("system_agents", 0)
    if not show_all and not archived_only and (archived_n or hidden_n or system_n):
        err_console.print(
            f"[dim]({archived_n} archived, {hidden_n} hidden, {system_n} system — "
            "pass --all to include, --archived to show only archived)[/dim]"
        )


def archive_agent(
    names: list[str] = typer.Argument(..., help="One or more managed agent names to archive"),
    reason: str = typer.Option(None, "--reason", "-r", help="Optional note describing why this is archived"),
    as_json: bool = JSON_OPTION,
):
    """Archive (disable) one or more managed agents.

    Archived agents are sticky-hidden — they don't appear in default views
    and the daemon will not auto-restore them on reconnect. Use
    `agents restore` to bring them back.
    """
    from . import gateway as _gateway_cmd

    _archive = getattr(_gateway_cmd, "_archive_managed_agent", _archive_managed_agent)

    archived: list[dict] = []
    not_found: list[str] = []
    for name in names:
        try:
            archived.append(_archive(name, reason=reason))
        except LookupError:
            not_found.append(name)
    if as_json:
        print_json({"archived": archived, "not_found": not_found, "count": len(archived)})
        if not_found and not archived:
            raise typer.Exit(1)
        return
    for entry in archived:
        err_console.print(f"[green]Archived:[/green] @{entry.get('name')}")
    for name in not_found:
        err_console.print(f"[red]Managed agent not found:[/red] {name}")
    if not archived and not_found:
        raise typer.Exit(1)


def restore_agent(
    names: list[str] = typer.Argument(..., help="One or more archived agent names to restore"),
    as_json: bool = JSON_OPTION,
):
    """Restore (re-enable) one or more archived agents.

    Restores `lifecycle_phase=active`. The runtime returns to the desired
    state captured at archive time; if none was captured, defaults to
    stopped. Start the runtime explicitly with `agents start <name>`.
    """
    from . import gateway as _gateway_cmd

    _restore = getattr(_gateway_cmd, "_restore_managed_agent", _restore_managed_agent)

    restored: list[dict] = []
    not_found: list[str] = []
    for name in names:
        try:
            restored.append(_restore(name))
        except LookupError:
            not_found.append(name)
    if as_json:
        print_json({"restored": restored, "not_found": not_found, "count": len(restored)})
        if not_found and not restored:
            raise typer.Exit(1)
        return
    for entry in restored:
        ds = str(entry.get("desired_state") or "stopped")
        err_console.print(f"[green]Restored:[/green] @{entry.get('name')} (desired_state={ds})")
    for name in not_found:
        err_console.print(f"[red]Managed agent not found:[/red] {name}")
    if not restored and not_found:
        raise typer.Exit(1)


def recover_agents(
    names: list[str] = typer.Argument(..., help="One or more agent names whose registry rows were lost"),
    as_json: bool = JSON_OPTION,
):
    """Recover registry rows from local evidence (token + activity log).

    Use when a managed_agent_added event was recorded but the registry
    row is missing — typically pre-race-fix damage. Reads the most
    recent managed_agent_added event for each name from the activity
    log, confirms the token file exists, and inserts a minimal row
    with the verified fields. The daemon hydrates the rest from
    upstream on the next reconcile pass.

    Refuses to recover agents already present in the registry. Refuses
    to recover agents lacking either the activity event or the token
    file (we don't fabricate credentials).
    """
    from . import gateway as _gateway_cmd

    _recover = getattr(_gateway_cmd, "_recover_managed_agents_from_evidence", _recover_managed_agents_from_evidence)

    try:
        result = _recover(list(names))
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(2) from exc
    if as_json:
        print_json(result)
        if result["count"] == 0:
            raise typer.Exit(1)
        return
    for entry in result.get("recovered", []):
        err_console.print(f"[green]Recovered:[/green] @{entry.get('name')} (agent_id={entry.get('agent_id')})")
    for name in result.get("already_present", []):
        err_console.print(f"[yellow]Already present:[/yellow] @{name} (no recovery needed)")
    for name in result.get("no_evidence", []):
        err_console.print(
            f"[red]No recovery evidence:[/red] @{name} (need both managed_agent_added activity + token file)"
        )
    if result["count"] == 0 and (result.get("no_evidence") or not result.get("already_present")):
        raise typer.Exit(1)


def remove_agent(name: str = typer.Argument(..., help="Managed agent name")):
    """Remove a managed agent from local Gateway control."""
    from . import gateway as _gateway_cmd

    _remove = getattr(_gateway_cmd, "_remove_managed_agent", _remove_managed_agent)

    try:
        _remove(name)
    except LookupError:
        err_console.print(f"[red]Managed agent not found:[/red] {name}")
        raise typer.Exit(1)
    err_console.print(f"[green]Removed managed agent:[/green] @{name}")
