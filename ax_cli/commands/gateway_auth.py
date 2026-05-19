"""ax gateway auth — login, session loading, and upstream rate-limit retry.

Extracted from ``commands/gateway.py`` per issue #28 Phase 1. Owns the
Gateway bootstrap login flow, the session-loading helpers that other
gateway commands use, and the upstream 429 retry primitive that every
helper calling paxai.app routes through.
"""

from __future__ import annotations

import time

import httpx
import typer

from ..client import AxClient
from ..commands import auth as auth_cmd
from ..config import resolve_space_id, resolve_user_base_url, resolve_user_token
from ..gateway import (
    load_gateway_registry,
    load_gateway_session,
    record_gateway_activity,
    save_gateway_registry,
    save_gateway_session,
)
from ..output import JSON_OPTION, err_console, print_json

# ---------------------------------------------------------------------------
# Upstream rate-limit handling: retry with exponential backoff + structured
# error so operator-visible flows (Connect agent modal, CLI commands) degrade
# cleanly when paxai.app rate-limits us. Two retry budgets:
#   - Interactive (Connect agent modal, CLI invocations): 2 retries × 1s/2s
#     base_wait → ~3s ceiling so the operator's UI doesn't hang.
#   - Background (reconcile loop, cache refresh): 5 retries × exponential.
# ---------------------------------------------------------------------------

INTERACTIVE_429_MAX_RETRIES = 2
INTERACTIVE_429_BASE_WAIT = 1.0
BACKGROUND_429_MAX_RETRIES = 5
BACKGROUND_429_BASE_WAIT = 1.0


class UpstreamRateLimitedError(RuntimeError):
    """Raised when an upstream call returned 429 even after retries.

    Carries the original ``httpx.HTTPStatusError`` plus a parsed
    ``retry_after_seconds`` (from the Retry-After header, when present)
    so callers can surface operator-actionable guidance without having
    to re-parse the upstream response.
    """

    def __init__(self, last_exc: httpx.HTTPStatusError, retries_attempted: int) -> None:
        self.last_exc = last_exc
        self.retries_attempted = retries_attempted
        retry_after: int | None = None
        try:
            response = last_exc.response
            header_value = response.headers.get("retry-after") if response is not None else None
            if header_value:
                retry_after = int(float(header_value))
        except (ValueError, AttributeError, TypeError):
            retry_after = None
        self.retry_after_seconds = retry_after
        super().__init__(f"Upstream rate-limited after {retries_attempted} retries")


def _with_upstream_429_retry(
    call,
    *,
    max_retries: int,
    base_wait: float = 1.0,
    max_wait: float = 120.0,
):
    """Run ``call`` and retry on httpx 429, honoring ``Retry-After`` when present.

    Per-attempt wait = ``max(base_wait * 2**attempt, retry_after_seconds)``,
    capped at ``max_wait``. paxai.app sends ``Retry-After: <seconds>`` on its
    per-user rate-limit responses; ignoring it and falling back to a 1s/2s
    exponential backoff exhausts the retry budget far below the server's
    cooldown and surfaces as a spurious ``UpstreamRateLimitedError``.

    Other httpx exceptions (4xx/5xx that aren't 429, network errors) propagate
    immediately. After the configured retry budget is exhausted on a
    persistent 429, raises ``UpstreamRateLimitedError`` carrying the
    final exception.
    """
    attempts = 0
    while True:
        try:
            return call()
        except httpx.HTTPStatusError as exc:
            if exc.response is None or exc.response.status_code != 429:
                raise
            if attempts >= max_retries:
                raise UpstreamRateLimitedError(exc, attempts) from exc
            retry_after_raw = exc.response.headers.get("retry-after")
            try:
                hint = float(retry_after_raw) if retry_after_raw is not None else 0.0
            except (TypeError, ValueError):
                hint = 0.0
            exp = base_wait * (2**attempts)
            wait = min(max(exp, hint), max_wait)
            time.sleep(wait)
            attempts += 1


def _resolve_gateway_login_token(explicit_token: str | None) -> str:
    # Late-lookup so tests that monkeypatch ``gateway_cmd.resolve_user_token``
    # continue to work after this function moved out of commands/gateway.py.
    from . import gateway as _gateway_cmd

    _resolve_user_token = getattr(_gateway_cmd, "resolve_user_token", resolve_user_token)
    if explicit_token and explicit_token.strip():
        return auth_cmd._resolve_login_token(explicit_token)
    existing = _resolve_user_token()
    if existing:
        err_console.print("[cyan]Using existing axctl user login for Gateway bootstrap.[/cyan]")
        return existing
    return auth_cmd._resolve_login_token(None)


def _load_gateway_user_client() -> AxClient:
    # AxClient is resolved via the commands.gateway namespace at call time so
    # existing tests that monkeypatch ``gateway_cmd.AxClient`` keep working
    # after this function moved out of commands/gateway.py.
    from . import gateway as _gateway_cmd

    cls = getattr(_gateway_cmd, "AxClient", AxClient)
    session = load_gateway_session()
    if not session:
        err_console.print("[red]Gateway is not logged in.[/red] Run `ax gateway login` first.")
        raise typer.Exit(1)
    token = str(session.get("token") or "")
    if not token:
        err_console.print("[red]Gateway session is missing its bootstrap token.[/red]")
        raise typer.Exit(1)
    if not token.startswith("axp_u_"):
        err_console.print("[red]Gateway bootstrap currently requires a user PAT (axp_u_).[/red]")
        raise typer.Exit(1)
    return cls(base_url=str(session.get("base_url") or auth_cmd.DEFAULT_LOGIN_BASE_URL), token=token)


def _load_gateway_session_or_exit() -> dict:
    session = load_gateway_session()
    if not session:
        err_console.print("[red]Gateway is not logged in.[/red] Run `ax gateway login` first.")
        raise typer.Exit(1)
    return session


def _build_session_client_silent() -> AxClient | None:
    """Build a user-PAT session client without raising. Returns None when
    the gateway is not logged in or the session token is missing/invalid.

    Used for best-effort upstream calls during local cleanup paths where a
    missing session must not abort the command.
    """
    # Same late-lookup pattern as _load_gateway_user_client — preserves test
    # monkeypatches that target ``gateway_cmd.AxClient``.
    from . import gateway as _gateway_cmd

    cls = getattr(_gateway_cmd, "AxClient", AxClient)
    session = load_gateway_session()
    if not session:
        return None
    token = str(session.get("token") or "")
    if not token:
        return None
    try:
        return cls(
            base_url=str(session.get("base_url") or auth_cmd.DEFAULT_LOGIN_BASE_URL),
            token=token,
        )
    except Exception:  # noqa: BLE001
        return None


def login(
    token: str = typer.Option(
        None, "--token", "-t", help="User PAT (prompted or reused from axctl login when omitted)"
    ),
    base_url: str = typer.Option(
        None, "--url", "-u", help="API base URL (defaults to existing axctl login or paxai.app)"
    ),
    space_id: str = typer.Option(None, "--space-id", "-s", help="Optional default space for managed agents"),
    as_json: bool = JSON_OPTION,
):
    """Store the Gateway bootstrap session.

    The Gateway keeps the user PAT centrally and uses it to mint agent PATs for
    managed runtimes. Managed runtimes themselves never receive the PAT or JWT.
    """
    resolved_token = _resolve_gateway_login_token(token)
    if not resolved_token.startswith("axp_u_"):
        err_console.print("[red]Gateway bootstrap requires a user PAT (axp_u_).[/red]")
        raise typer.Exit(1)
    from ..token_cache import TokenExchanger
    from . import gateway as _gateway_cmd

    # Late-lookup so tests that monkeypatch ``gateway_cmd.{AxClient,resolve_*}``
    # continue to work after this command moved out of commands/gateway.py.
    cls = getattr(_gateway_cmd, "AxClient", AxClient)
    _resolve_base_url = getattr(_gateway_cmd, "resolve_user_base_url", resolve_user_base_url)
    _resolve_space_id = getattr(_gateway_cmd, "resolve_space_id", resolve_space_id)
    _save_session = getattr(_gateway_cmd, "save_gateway_session", save_gateway_session)
    resolved_base_url = base_url or _resolve_base_url() or auth_cmd.DEFAULT_LOGIN_BASE_URL

    err_console.print(f"[cyan]Verifying Gateway login against {resolved_base_url}...[/cyan]")

    try:
        exchanger = TokenExchanger(resolved_base_url, resolved_token)
        exchanger.get_token(
            "user_access",
            scope="messages tasks context agents spaces search",
            force_refresh=True,
        )
        client = cls(base_url=resolved_base_url, token=resolved_token)
        me = client.whoami()
    except Exception as exc:
        err_console.print(f"[red]Gateway login failed:[/red] {exc}")
        raise typer.Exit(1)

    selected_space = space_id
    selected_space_name = None
    if not selected_space:
        try:
            spaces = client.list_spaces()
            space_list = spaces.get("spaces", spaces) if isinstance(spaces, dict) else spaces
            selected = auth_cmd._select_login_space([s for s in space_list if isinstance(s, dict)])
            if selected:
                selected_space = auth_cmd._candidate_space_id(selected)
                selected_space_name = str(selected.get("name") or selected_space)
        except Exception:
            selected_space = None
    elif selected_space:
        try:
            selected_space = _resolve_space_id(client, explicit=selected_space)
            spaces = client.list_spaces()
            space_list = spaces.get("spaces", spaces) if isinstance(spaces, dict) else spaces
            selected_space_name = next(
                (
                    str(item.get("name") or selected_space)
                    for item in space_list
                    if isinstance(item, dict) and auth_cmd._candidate_space_id(item) == selected_space
                ),
                None,
            )
        except Exception:
            selected_space_name = None

    payload = {
        "token": resolved_token,
        "base_url": resolved_base_url,
        "principal_type": "user",
        "space_id": selected_space,
        "space_name": selected_space_name,
        "username": me.get("username"),
        "email": me.get("email"),
        "saved_at": None,
    }
    path = _save_session(payload)
    registry = load_gateway_registry()
    registry.setdefault("gateway", {})
    registry["gateway"]["session_connected"] = True
    save_gateway_registry(registry)
    record_gateway_activity(
        "gateway_login", username=me.get("username"), base_url=resolved_base_url, space_id=selected_space
    )

    result = {
        "session_path": str(path),
        "base_url": resolved_base_url,
        "space_id": selected_space,
        "space_name": selected_space_name,
        "username": me.get("username"),
        "email": me.get("email"),
    }
    if as_json:
        print_json(result)
    else:
        err_console.print(f"[green]Gateway login saved:[/green] {path}")
        for key, value in result.items():
            err_console.print(f"  {key} = {value}")
