"""ax gateway — local Gateway control plane."""

from __future__ import annotations

import shutil  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd
import subprocess  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd
import sys  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd
import time  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd
import webbrowser  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd

import httpx  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd
import typer

from .. import gateway as gateway_core  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd
from ..client import AxClient  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd
from ..commands import auth as auth_cmd  # noqa: F401
from ..commands.bootstrap import (  # noqa: F401 — re-exported for tests that monkeypatch via gateway_cmd
    _create_agent_in_space,
    _find_agent_in_space,
    _mint_agent_pat,
    _polish_metadata,
)
from ..config import resolve_space_id, resolve_user_base_url, resolve_user_token  # noqa: F401
from ..gateway import (  # noqa: F401
    AX_PLUGIN_NAME,
    GatewayDaemon,
    _format_daemon_log_line,
    _hermes_plugin_home,
    _is_passive_runtime,
    _is_system_agent,
    _plugin_source_dir,
    active_gateway_pid,
    active_gateway_pids,
    active_gateway_ui_pid,
    active_gateway_ui_pids,
    activity_log_path,
    agent_dir,
    agent_token_path,
    annotate_runtime_health,
    apply_entry_current_space,
    approve_gateway_approval,
    archive_stale_gateway_approvals,
    clear_gateway_ui_state,
    daemon_log_path,
    daemon_status,
    deny_gateway_approval,
    ensure_gateway_identity_binding,
    ensure_local_asset_binding,
    evaluate_runtime_attestation,
    find_agent_entry,
    find_agent_entry_by_ref,
    gateway_dir,
    gateway_environment,
    get_gateway_approval,
    hermes_setup_status,
    infer_asset_descriptor,
    issue_local_session,
    list_gateway_approvals,
    load_agent_pending_messages,
    load_gateway_managed_agent_token,
    load_gateway_registry,
    load_gateway_session,
    load_recent_gateway_activity,
    load_space_cache,
    looks_like_space_uuid,
    lookup_space_in_cache,
    ollama_setup_status,
    record_gateway_activity,
    remove_agent_entry,
    save_agent_pending_messages,
    save_gateway_registry,
    save_gateway_session,
    save_space_cache,
    space_name_from_cache,
    ui_log_path,
    ui_status,
    upsert_agent_entry,
    upsert_space_cache_entry,
    verify_local_session_token,
    write_gateway_ui_state,
)
from ..gateway_runtime_types import (  # noqa: F401
    agent_template_list,
    runtime_type_list,
)
from ..output import JSON_OPTION, console, err_console, print_json  # noqa: F401 — err_console re-exported for tests

# Agent CRUD helpers + the `agents` sub-app commands live in gateway_agents.
# Re-exported here so existing test imports
# (``from ax_cli.commands.gateway import _register_managed_agent``, etc.)
# keep working without churn.
from .gateway_agents import (  # noqa: F401
    _AGENT_CONTEXT_MARKER_BEGIN,
    _AGENT_CONTEXT_MARKER_END,
    _UNSET,
    _agent_runtime_context_target,
    _agent_workspace_context_text,
    _agent_workspace_readme_text,
    _agents_cache_path,
    _archive_managed_agent,
    _hide_managed_agents,
    _load_agents_cache,
    _load_managed_agent_client,
    _load_managed_agent_or_exit,
    _normalize_runtime_type,
    _normalize_timeout_seconds,
    _read_recovery_evidence,
    _recover_managed_agents_from_evidence,
    _register_managed_agent,
    _registry_ref_for_agent,
    _reject_managed_agent_approval,
    _remove_managed_agent,
    _render_agent_persona_markdown,
    _resolve_system_prompt_input,
    _restore_hidden_managed_agents,
    _restore_managed_agent,
    _save_agent_token,
    _save_agents_cache,
    _set_managed_agent_pin,
    _update_managed_agent,
    _validate_runtime_registration,
    _with_registry_refs,
    _write_agent_context_hint,
    _write_agent_workspace_config,
    _write_marker_section,
    add_agent,
    archive_agent,
    list_agents,
    recover_agents,
    remove_agent,
    restore_agent,
    update_agent,
)

# Auth, session loading, and upstream rate-limit retry primitives live in
# gateway_auth. Re-exported here so existing test imports
# (``from ax_cli.commands.gateway import _load_gateway_user_client``, etc.)
# keep working without churn.
from .gateway_auth import (  # noqa: F401
    BACKGROUND_429_BASE_WAIT,
    BACKGROUND_429_MAX_RETRIES,
    INTERACTIVE_429_BASE_WAIT,
    INTERACTIVE_429_MAX_RETRIES,
    UpstreamRateLimitedError,
    _build_session_client_silent,
    _load_gateway_session_or_exit,
    _load_gateway_user_client,
    _resolve_gateway_login_token,
    _with_upstream_429_retry,
    login,
)
from .gateway_daemon_cmd import (  # noqa: F401
    _emit_daemon_log,
    _gateway_cli_argv,
    _spawn_gateway_background_process,
    _tail_log_lines,
    _terminate_pids,
    _wait_for_daemon_ready,
    _wait_for_ui_ready,
    run_gateway,
    start_gateway,
    stop_gateway,
    watch_gateway,
)
from .gateway_diagnostics import (  # noqa: F401
    _CONFIDENCE_STYLES,
    _PRESENCE_ORDER,
    _PRESENCE_STYLES,
    _STATE_STYLES,
    _age_seconds,
    _agent_detail_payload,
    _agent_output_label,
    _agent_template_label,
    _agent_type_label,
    _approval_detail_payload,
    _approval_rows_payload,
    _confidence_text,
    _doctor_result_status,
    _doctor_summary,
    _ensure_gateway_test_sender,
    _format_age,
    _format_timestamp,
    _gateway_alerts,
    _gateway_test_sender_name,
    _metric_panel,
    _mode_text,
    _parse_iso8601,
    _presence_text,
    _reachability_copy,
    _recommended_test_message,
    _render_activity_table,
    _render_agent_detail,
    _render_agent_table,
    _render_alert_table,
    _render_gateway_dashboard,
    _render_gateway_overview,
    _reply_text,
    _run_gateway_doctor,
    _send_gateway_test_to_managed_agent,
    _sorted_agents,
    _space_cache_with,
    _state_text,
    _status_payload,
    _store_doctor_result,
    activity,
    approve_approval,
    cleanup_approvals,
    deny_approval,
    doctor_agent,
    list_approvals,
    move_agent,
    show_agent,
    show_approval,
    status,
    test_agent,
)
from .gateway_lifecycle import (  # noqa: F401
    _ATTACHED_SESSION_PROCESSES,
    _EXTERNAL_RUNTIME_COMPLETE_STATUSES,
    _EXTERNAL_RUNTIME_RUNNING_STATUSES,
    _EXTERNAL_RUNTIME_STOPPED_STATUSES,
    _announce_external_agent_runtime,
    _attach_command_for_payload,
    _launch_attached_agent_session,
    _mark_attached_agent_session,
    _prepare_attached_agent_payload,
    _set_managed_agent_desired_state,
    attach_agent,
    mark_attached_agent,
    start_agent,
    stop_agent,
)
from .gateway_local import (  # noqa: F401
    _approval_required_guidance,
    _check_local_pending_replies,
    _ensure_workdir,
    _gateway_local_config_from_workdir,
    _gateway_local_config_text,
    _local_route_failure_guidance,
    _poll_local_inbox_over_http,
    _print_pending_reply_warning_local,
    _request_local_connect,
    _resolve_local_gateway_identity,
    _resolve_local_gateway_session,
    local_connect,
    local_inbox,
    local_init,
    local_send,
)
from .gateway_messaging import (  # noqa: F401
    _ack_managed_agent_message,
    _identity_space_send_guard,
    _inbox_for_managed_agent,
    _poll_managed_agent_inbox_after_send,
    _send_from_managed_agent,
    _sync_passive_queue_after_manual_send,
    inbox_for_agent,
    send_as_agent,
)
from .gateway_runtime_cmd import (  # noqa: F401
    _RUNTIME_INSTALL_RECIPES,
    _agent_templates_payload,
    _annotate_template_taxonomy,
    _install_runtime_payload,
    _proc_error_msg,
    _resolve_install_target,
    _runtime_types_payload,
    _venv_module_unavailable_reason,
    runtime_install,
    runtime_status,
    runtime_types,
    templates,
)
from .gateway_session import (  # noqa: F401
    _LOCAL_PROXY_METHODS,
    _connect_local_pass_through_agent,
    _create_local_session_task,
    _ensure_session_challenge,
    _find_local_origin_collision,
    _find_local_session_record,
    _gateway_session_challenge_enabled,
    _generate_session_challenge_code,
    _local_fingerprint_verification,
    _local_origin_signature,
    _local_process_fingerprint,
    _local_session_inbox,
    _local_trust_signature,
    _no_invoking_principal_error,
    _proxy_local_session_call,
    _resolve_invoking_principal,
    _send_local_session_message,
)
from .gateway_spaces import (  # noqa: F401
    _agent_row_space_ids,
    _agent_space_id_from_backend_record,
    _agent_space_name_from_backend_record,
    _backend_agent_record,
    _existing_agent_home_space,
    _hydrate_entry_space_from_database,
    _move_managed_agent_space,
    _normalize_spaces_response,
    _resolve_gateway_agent_home_space,
    _resolve_space_via_cache,
    _space_list_from_response,
    _space_name_for_id,
    _spaces_payload,
    current_gateway_space,
    list_gateway_spaces,
    use_gateway_space,
)
from .gateway_ui import (  # noqa: F401
    _DEMO_HTML_PATH,
    _GATEWAY_FAVICON_SVG,
    _LOOPBACK_HOSTNAMES,
    _build_gateway_ui_handler,
    _GatewayUiServer,
    _is_request_host_allowed,
    _read_json_request,
    _render_gateway_demo_page,
    _render_gateway_ui_page,
    _write_html_response,
    _write_json_response,
    ui,
)

app = typer.Typer(name="gateway", help="Run the local Gateway control plane", no_args_is_help=True)
agents_app = typer.Typer(name="agents", help="Manage Gateway-controlled agents", no_args_is_help=True)
spaces_app = typer.Typer(name="spaces", help="Manage Gateway current space", no_args_is_help=True)
approvals_app = typer.Typer(name="approvals", help="Review and decide Gateway approval requests", no_args_is_help=True)
runtime_app = typer.Typer(
    name="runtime", help="Install and inspect runtime templates (Hermes, etc.)", no_args_is_help=True
)
local_app = typer.Typer(name="local", help="Connect local pass-through agents to Gateway", no_args_is_help=True)
app.add_typer(agents_app, name="agents")
app.add_typer(spaces_app, name="spaces")
app.add_typer(approvals_app, name="approvals")
app.add_typer(runtime_app, name="runtime")
app.add_typer(local_app, name="local")

# login lives in gateway_auth so it can be reused without dragging in the
# full gateway commands module. Register it on the main app here.
app.command("login")(login)

# spaces sub-app commands live in gateway_spaces.
spaces_app.command("use")(use_gateway_space)
spaces_app.command("current")(current_gateway_space)
spaces_app.command("list")(list_gateway_spaces)

# Agent CRUD commands live in gateway_agents. Other agents commands
# (start/stop/show/test/move/doctor/send/inbox/attach/mark-attached)
# still live in this module.
agents_app.command("add")(add_agent)
agents_app.command("update")(update_agent)
agents_app.command("list")(list_agents)
agents_app.command("archive")(archive_agent)
agents_app.command("restore")(restore_agent)
agents_app.command("recover")(recover_agents)
agents_app.command("remove")(remove_agent)

# Lifecycle commands live in gateway_lifecycle.
agents_app.command("start")(start_agent)
agents_app.command("stop")(stop_agent)
agents_app.command("mark-attached")(mark_attached_agent)
agents_app.command("attach")(attach_agent)

# Messaging commands live in gateway_messaging.
agents_app.command("send")(send_as_agent)
agents_app.command("inbox")(inbox_for_agent)

# Diagnostics commands live in gateway_diagnostics.
app.command("activity")(activity)
app.command("status")(status)
agents_app.command("show")(show_agent)
agents_app.command("test")(test_agent)
agents_app.command("move")(move_agent)
agents_app.command("doctor")(doctor_agent)
approvals_app.command("list")(list_approvals)
approvals_app.command("cleanup")(cleanup_approvals)
approvals_app.command("show")(show_approval)
approvals_app.command("approve")(approve_approval)
approvals_app.command("deny")(deny_approval)

# UI / HTTP-server module lives in gateway_ui.
app.command("ui")(ui)

# Runtime install + template catalog commands live in gateway_runtime_cmd.
runtime_app.command("install")(runtime_install)
runtime_app.command("status")(runtime_status)
app.command("runtime-types")(runtime_types)
app.command("templates")(templates)

# Daemon process commands live in gateway_daemon_cmd.
app.command("start")(start_gateway)
app.command("stop")(stop_gateway)
app.command("watch")(watch_gateway)
app.command("run")(run_gateway)

# Local pass-through sub-app commands live in gateway_local.
local_app.command("connect")(local_connect)
local_app.command("init")(local_init)
local_app.command("send")(local_send)
local_app.command("inbox")(local_inbox)
