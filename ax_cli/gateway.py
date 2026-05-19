"""Local Gateway runtime and state management.

The Gateway is a local control-plane daemon that owns bootstrap and agent
credentials, supervises managed runtimes, and keeps lightweight desired vs
effective state in a registry file. The first slice intentionally uses
filesystem state plus a foreground daemon so it can ship quickly without
introducing a second backend.
"""

from __future__ import annotations

import os  # noqa: F401 — re-exported for tests that monkeypatch via gateway.os
import shutil  # noqa: F401 — re-exported for tests/operators that monkeypatch via gateway.shutil
import subprocess  # noqa: F401 — re-exported for tests that monkeypatch via gateway.subprocess
from pathlib import Path  # noqa: F401 — re-exported for tests that monkeypatch via gateway.Path

import httpx  # noqa: F401 — re-exported for tests that monkeypatch via gateway.httpx

from .client import AxClient  # noqa: F401 — re-exported for downstream imports

# Asset type inference, setup detection (Hermes, Ollama), and operator profile
# helpers live in gateway_assets. Re-exported here so existing imports keep
# working without churn.
from .gateway_assets import (  # noqa: F401
    _attached_session_log_is_ready,
    _hermes_repo_candidates,
    _ollama_model_rows,
    _recommended_ollama_model,
    hermes_setup_status,
    infer_operator_profile,
    ollama_setup_status,
)

# Constants and pure normalization helpers live in gateway_constants.
# Re-exported here so existing imports keep working without churn.
from .gateway_constants import (  # noqa: F401
    _BLOCKED_STATUSES,
    _CONTROLLED_ACTIVATIONS,
    _CONTROLLED_ACTIVE_SPACE_SOURCES,
    _CONTROLLED_APPROVAL_STATES,
    _CONTROLLED_ASSET_CLASSES,
    _CONTROLLED_ATTESTATION_STATES,
    _CONTROLLED_CONFIDENCE,
    _CONTROLLED_CONFIDENCE_REASONS,
    _CONTROLLED_ENVIRONMENT_STATUSES,
    _CONTROLLED_IDENTITY_STATUSES,
    _CONTROLLED_INTAKE_MODELS,
    _CONTROLLED_LIVENESS,
    _CONTROLLED_MODES,
    _CONTROLLED_PLACEMENTS,
    _CONTROLLED_PRESENCE,
    _CONTROLLED_REACHABILITY,
    _CONTROLLED_REPLY,
    _CONTROLLED_REPLY_MODES,
    _CONTROLLED_RETURN_PATHS,
    _CONTROLLED_SPACE_STATUSES,
    _CONTROLLED_TELEMETRY_LEVELS,
    _CONTROLLED_TELEMETRY_SHAPES,
    _CONTROLLED_TRIGGER_SOURCES,
    _CONTROLLED_WORK_STATES,
    _CONTROLLED_WORKER_MODELS,
    _GATEWAY_PROCESS_RE,
    _GATEWAY_UI_PROCESS_RE,
    _LIFECYCLE_PHASES,
    _NO_REPLY_STATUSES,
    _WORKING_STATUSES,
    DEFAULT_ACTIVITY_LIMIT,
    DEFAULT_HANDLER_TIMEOUT_SECONDS,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_QUEUE_SIZE,
    ENV_DENYLIST,
    GATEWAY_ACTIVITY_EVENTS,
    GATEWAY_ACTIVITY_PHASES,
    GATEWAY_EVENT_PREFIX,
    LOCAL_SESSION_TTL_SECONDS,
    MIN_HANDLER_TIMEOUT_SECONDS,
    REPLY_ANCHOR_MAX,
    RUNTIME_HEARTBEAT_INTERVAL_SECONDS,
    RUNTIME_HIDDEN_AFTER_SECONDS,
    RUNTIME_STALE_AFTER_SECONDS,
    SEEN_IDS_MAX,
    SETUP_ERROR_BACKOFF_SECONDS,
    SSE_IDLE_TIMEOUT_SECONDS,
    _asset_type_label,
    _bool_with_fallback,
    _format_daemon_log_line,
    _hide_after_stale_seconds,
    _is_passive_runtime,
    _is_system_agent,
    _normalized_base_url,
    _normalized_controlled,
    _normalized_controlled_list,
    _normalized_optional_controlled,
    _normalized_string_list,
    _output_label,
    _override_fields,
    _template_asset_defaults,
    _template_operator_defaults,
    infer_asset_descriptor,
    phase_for_event,
)

# The GatewayDaemon supervisor (reconciliation loop, signal handling) lives in
# gateway_daemon. Re-exported here so existing imports keep working without
# churn.
from .gateway_daemon import GatewayDaemon  # noqa: F401
from .gateway_entries import (  # noqa: F401
    GatewayRuntimeTimeoutError,
    _apply_placement_event,
    _hash_tool_arguments,
    _parse_gateway_exec_event,
    _post_placement_ack,
    runtime_timeout_seconds,
    sanitize_exec_env,
)
from .gateway_governance import (  # noqa: F401
    _approval_is_stale,
    _approval_status,
    _create_binding_approval,
    _entry_requires_operator_approval,
    _find_approval_by_id,
    _find_approval_for_signature,
    _record_governance_activity,
    _refresh_attestation_for_matching_entries,
    approve_gateway_approval,
    archive_stale_gateway_approvals,
    deny_gateway_approval,
    ensure_local_asset_binding,
    evaluate_runtime_attestation,
    get_gateway_approval,
    list_gateway_approvals,
)
from .gateway_health import (  # noqa: F401
    _age_seconds,
    _now_iso,
    _pid_is_alive,
    annotate_runtime_health,
)
from .gateway_hermes import (  # noqa: F401
    AX_PLUGIN_NAME,
    _agents_dir_for_entry,
    _build_hermes_plugin_cmd,
    _build_hermes_plugin_env,
    _build_hermes_sentinel_cmd,
    _build_hermes_sentinel_env,
    _build_sentinel_claude_cmd,
    _build_sentinel_codex_cmd,
    _compose_agent_system_prompt,
    _gateway_environment_context,
    _gateway_repo_root,
    _hermes_bin,
    _hermes_plugin_home,
    _hermes_plugin_workdir,
    _hermes_sentinel_model,
    _hermes_sentinel_python,
    _hermes_sentinel_script,
    _hermes_sentinel_workdir,
    _plugin_source_dir,
    _render_hermes_plugin_config_yaml,
    _scaffold_hermes_plugin_home,
    _sentinel_model,
    _sentinel_runtime_name,
    _sentinel_session_key,
    _sentinel_session_scope,
    _sentinel_tool_summary,
    _summarize_sentinel_command,
)

# Pure state derivation helpers (liveness, work state, mode, presence, reply,
# reachability, confidence) live in gateway_state. Re-exported here so existing
# imports keep working without churn.
from .gateway_identity import (  # noqa: F401
    _UUID_RE,
    _asset_id_for_entry,
    _b64url_decode,
    _b64url_encode,
    _binding_candidate_for_entry,
    _binding_type_for_entry,
    _bindings_for_asset,
    _command_executable_path,
    _ensure_registry_lists,
    _environment_label_for_base_url,
    _fallback_allowed_spaces,
    _fetch_allowed_spaces_for_entry,
    _file_sha256,
    _gateway_id_from_registry,
    _host_fingerprint,
    _identity_bindings_for_asset,
    _launch_spec_for_entry,
    _local_session_signature,
    _normalize_allowed_spaces_payload,
    _parse_iso8601,
    _payload_hash,
    _redacted_path,
    _runtime_origin_fingerprint,
    _safe_file_sha256,
    _space_cache_rows,
    _space_id_allowed,
    _space_name_from_cache,
    _without_none,
    apply_entry_current_space,
    ensure_gateway_identity_binding,
    evaluate_identity_space_binding,
    find_binding,
    find_identity_binding,
    issue_local_session,
    load_local_secret,
    local_secret_path,
    upsert_binding,
    upsert_identity_binding,
    verify_local_session_token,
)

# ManagedAgentRuntime (listener + worker threads, supervised subprocess
# lifecycle) plus its dedicated helpers live in gateway_runtime. Re-exported
# here so existing imports keep working without churn — GatewayDaemon uses
# late-lookup against these names so test monkeypatches on gateway still apply.
from .gateway_runtime import (  # noqa: F401
    ManagedAgentRuntime,
    RuntimeLogger,
    _echo_handler,
    _gateway_pickup_activity,
    _is_hermes_plugin_runtime,
    _is_hermes_sentinel_runtime,
    _is_sentinel_cli_runtime,
    _is_supervised_subprocess_runtime,
    _run_exec_handler,
)
from .gateway_state import (  # noqa: F401
    _derive_confidence,
    _derive_liveness,
    _derive_mode,
    _derive_presence,
    _derive_reachability,
    _derive_reply,
    _derive_work_state,
    _doctor_has_failed,
    _doctor_summary,
    _external_runtime_connected,
    _external_runtime_expected,
    _looks_like_setup_error,
    _setup_error_detail,
)

# Filesystem paths, registry/session/space-cache I/O, agent persistence,
# activity log, PID files, and UI state live in gateway_storage. Re-exported
# here so existing imports keep working without churn.
from .gateway_storage import (  # noqa: F401
    _ACTIVITY_LOCK,
    _LOAD_SNAPSHOT_KEY,
    _OPERATOR_AUTHORITATIVE_FIELDS,
    _SPACE_UUID_RE,
    _chmod_quiet,
    _default_pending_queue,
    _default_registry,
    _default_ui_state,
    _pid_alive,
    _read_json,
    _scan_gateway_process_pids,
    _scan_gateway_ui_process_pids,
    _scan_process_pids,
    _write_json,
    active_gateway_pid,
    active_gateway_pids,
    active_gateway_ui_pid,
    active_gateway_ui_pids,
    activity_log_path,
    agent_dir,
    agent_pending_queue_path,
    agent_token_path,
    append_agent_pending_message,
    clear_gateway_pid,
    clear_gateway_ui_state,
    daemon_log_path,
    daemon_status,
    find_agent_entry,
    find_agent_entry_by_ref,
    gateway_agents_dir,
    gateway_dir,
    gateway_environment,
    load_agent_pending_messages,
    load_gateway_managed_agent_token,
    load_gateway_registry,
    load_gateway_session,
    load_gateway_ui_state,
    load_recent_gateway_activity,
    load_space_cache,
    looks_like_space_uuid,
    lookup_space_in_cache,
    pid_path,
    reconcile_corrupt_space_ids,
    record_gateway_activity,
    registry_path,
    remove_agent_entry,
    remove_agent_pending_message,
    save_agent_pending_messages,
    save_gateway_registry,
    save_gateway_session,
    save_gateway_ui_state,
    save_space_cache,
    session_path,
    space_cache_path,
    space_name_from_cache,
    ui_log_path,
    ui_state_path,
    ui_status,
    upsert_agent_entry,
    upsert_space_cache_entry,
    write_gateway_pid,
    write_gateway_ui_state,
)
