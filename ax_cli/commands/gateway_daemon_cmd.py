"""ax gateway daemon — start/stop/watch/run + process management."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import typer
from rich.console import Group
from rich.live import Live

from .. import gateway as gateway_core
from ..gateway import (
    GatewayDaemon,
    _format_daemon_log_line,
    active_gateway_pid,
    active_gateway_pids,
    active_gateway_ui_pid,
    active_gateway_ui_pids,
    clear_gateway_ui_state,
    daemon_log_path,
    daemon_status,
    gateway_dir,
    load_gateway_session,
    record_gateway_activity,
    ui_log_path,
    ui_status,
)
from ..output import console, err_console


def _gateway_cli_argv(*args: str) -> list[str]:
    current_argv0 = str(sys.argv[0] or "").strip()
    if current_argv0:
        current_path = Path(current_argv0).expanduser()
        if current_path.exists() and current_path.name in {"ax", "axctl"}:
            return [str(current_path.resolve()), *args]
    python_bin = Path(sys.executable).resolve().parent
    for candidate in (python_bin / "ax", python_bin / "axctl"):
        if candidate.exists():
            return [str(candidate), *args]
    resolved = shutil.which("ax") or shutil.which("axctl")
    if resolved:
        return [resolved, *args]
    command = "import sys; from ax_cli.main import main; sys.argv = ['ax'] + sys.argv[1:]; main()"
    return [sys.executable, "-c", command, *args]


def _spawn_gateway_background_process(command: list[str], *, log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as handle:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            cwd=str(Path.cwd()),
            start_new_session=True,
            close_fds=True,
        )
    return process


def _tail_log_lines(path: Path, *, lines: int = 12) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    chunks = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(chunks[-lines:])


def _wait_for_daemon_ready(process: subprocess.Popen[bytes], *, timeout: float = 3.0) -> bool:
    from . import gateway as _gateway_cmd

    _daemon_status = getattr(_gateway_cmd, "daemon_status", daemon_status)
    _active_pid = getattr(_gateway_cmd, "active_gateway_pid", active_gateway_pid)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        if _daemon_status().get("running") or _active_pid():
            return True
        time.sleep(0.1)
    return process.poll() is None and bool(_daemon_status().get("running") or _active_pid())


def _wait_for_ui_ready(process: subprocess.Popen[bytes], *, host: str, port: int, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _terminate_pids(pids: list[int], *, timeout: float = 8.0) -> tuple[list[int], list[int]]:
    requested: list[int] = []
    forced: list[int] = []
    for pid in sorted(set(pids)):
        try:
            os.kill(pid, signal.SIGTERM)
            requested.append(pid)
        except ProcessLookupError:
            continue
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [pid for pid in requested if gateway_core._pid_alive(pid)]
        if not alive:
            return requested, forced
        time.sleep(0.1)
    for pid in requested:
        if not gateway_core._pid_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            forced.append(pid)
        except ProcessLookupError:
            continue
    return requested, forced


def start_gateway(
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Registry reconcile interval in seconds"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind the local Gateway UI"),
    port: int = typer.Option(8765, "--port", help="Port for the local Gateway UI"),
    activity_limit: int = typer.Option(24, "--activity-limit", help="Number of recent events to expose in the UI"),
    refresh: float = typer.Option(2.0, "--refresh", help="Browser auto-refresh interval in seconds"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the local UI in a browser"),
):
    """Start the Gateway daemon and local UI in the background."""
    from . import gateway as _gateway_cmd

    _load_session = getattr(_gateway_cmd, "load_gateway_session", load_gateway_session)
    _active_pid = getattr(_gateway_cmd, "active_gateway_pid", active_gateway_pid)
    _active_ui_pid = getattr(_gateway_cmd, "active_gateway_ui_pid", active_gateway_ui_pid)
    _spawn = getattr(_gateway_cmd, "_spawn_gateway_background_process", _spawn_gateway_background_process)
    _argv = getattr(_gateway_cmd, "_gateway_cli_argv", _gateway_cli_argv)
    _wait_daemon = getattr(_gateway_cmd, "_wait_for_daemon_ready", _wait_for_daemon_ready)
    _wait_ui = getattr(_gateway_cmd, "_wait_for_ui_ready", _wait_for_ui_ready)
    _tail = getattr(_gateway_cmd, "_tail_log_lines", _tail_log_lines)
    _terminate = getattr(_gateway_cmd, "_terminate_pids", _terminate_pids)
    _ui_status = getattr(_gateway_cmd, "ui_status", ui_status)
    _daemon_log_path = getattr(_gateway_cmd, "daemon_log_path", daemon_log_path)
    _ui_log_path = getattr(_gateway_cmd, "ui_log_path", ui_log_path)

    session = _load_session()
    daemon_pid = _active_pid()
    ui_pid = _active_ui_pid()
    daemon_started = False
    ui_started = False
    daemon_note: str | None = None

    if daemon_pid is None:
        if session:
            daemon_process = _spawn(
                _argv("gateway", "run", "--poll-interval", str(poll_interval)),
                log_path=_daemon_log_path(),
            )
            if _wait_daemon(daemon_process):
                daemon_pid = _active_pid() or daemon_process.pid
                daemon_started = True
            else:
                detail = _tail(_daemon_log_path())
                err_console.print(
                    f"[red]Failed to start Gateway daemon.[/red] {detail or 'Check gateway.log for details.'}"
                )
                raise typer.Exit(1)
        else:
            daemon_note = "Gateway is not logged in yet; the UI can still start in disconnected mode."

    if ui_pid is None:
        ui_process = _spawn(
            _argv(
                "gateway",
                "ui",
                "--host",
                host,
                "--port",
                str(port),
                "--activity-limit",
                str(activity_limit),
                "--refresh",
                str(refresh),
                "--no-open",
            ),
            log_path=_ui_log_path(),
        )
        if _wait_ui(ui_process, host=host, port=port):
            ui_pid = _active_ui_pid() or ui_process.pid
            ui_started = True
        else:
            detail = _tail(_ui_log_path())
            if daemon_started and daemon_pid:
                _terminate([daemon_pid])
                gateway_core.clear_gateway_pid()
            err_console.print(f"[red]Failed to start Gateway UI.[/red] {detail or 'Check gateway-ui.log for details.'}")
            raise typer.Exit(1)

    ui_meta = _ui_status()
    if open_browser and ui_meta.get("running"):
        try:
            webbrowser.open_new_tab(str(ui_meta.get("url") or f"http://{host}:{port}"))
        except Exception:
            err_console.print("[yellow]Could not open a browser automatically.[/yellow]")

    err_console.print("[bold]ax gateway start[/bold]")
    err_console.print(f"  daemon    = {'started' if daemon_started else 'running' if daemon_pid else 'not started'}")
    if daemon_pid:
        err_console.print(f"  daemon_pid= {daemon_pid}")
    err_console.print(f"  ui        = {'started' if ui_started else 'running' if ui_pid else 'not started'}")
    if ui_pid:
        err_console.print(f"  ui_pid    = {ui_pid}")
    err_console.print(f"  url       = {ui_meta.get('url') or f'http://{host}:{port}'}")
    err_console.print(f"  logs      = {_daemon_log_path()}")
    err_console.print(f"  ui_logs   = {_ui_log_path()}")
    if daemon_note:
        err_console.print(f"[yellow]{daemon_note}[/yellow]")


def stop_gateway():
    """Stop the background Gateway daemon and local UI."""
    from . import gateway as _gateway_cmd

    _active_pids = getattr(_gateway_cmd, "active_gateway_pids", active_gateway_pids)
    _active_ui_pids = getattr(_gateway_cmd, "active_gateway_ui_pids", active_gateway_ui_pids)
    _terminate = getattr(_gateway_cmd, "_terminate_pids", _terminate_pids)
    _clear_ui = getattr(_gateway_cmd, "clear_gateway_ui_state", clear_gateway_ui_state)
    _record = getattr(_gateway_cmd, "record_gateway_activity", record_gateway_activity)

    daemon_pids = _active_pids()
    ui_pids = _active_ui_pids()
    if not daemon_pids and not ui_pids:
        _clear_ui()
        gateway_core.clear_gateway_pid()
        err_console.print("[yellow]Gateway daemon and UI are already stopped.[/yellow]")
        return

    ui_requested, ui_forced = _terminate(ui_pids)
    daemon_requested, daemon_forced = _terminate(daemon_pids)
    _clear_ui()
    gateway_core.clear_gateway_pid()
    _record(
        "gateway_services_stopped",
        daemon_pids=daemon_requested,
        ui_pids=ui_requested,
        daemon_forced=daemon_forced,
        ui_forced=ui_forced,
    )

    err_console.print("[bold]ax gateway stop[/bold]")
    err_console.print(f"  daemon = {daemon_requested or []}")
    err_console.print(f"  ui     = {ui_requested or []}")
    if daemon_forced or ui_forced:
        err_console.print(f"[yellow]Forced kill:[/yellow] daemon={daemon_forced or []} ui={ui_forced or []}")


def watch_gateway(
    interval: float = typer.Option(2.0, "--interval", "-n", help="Dashboard refresh interval in seconds"),
    activity_limit: int = typer.Option(8, "--activity-limit", help="Number of recent events to display"),
    once: bool = typer.Option(False, "--once", help="Render one dashboard frame and exit"),
    show_all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Include hidden (auto-swept stale) and system (switchboard / service-account) agents.",
    ),
):
    """Watch the Gateway in a live terminal dashboard."""
    from . import gateway as _gateway_cmd

    def render_dashboard() -> Group:
        _status = getattr(_gateway_cmd, "_status_payload")
        _render = getattr(_gateway_cmd, "_render_gateway_dashboard")
        return _render(_status(activity_limit=activity_limit, include_hidden=show_all))

    if once:
        console.print(render_dashboard())
        return

    try:
        with Live(render_dashboard(), console=console, screen=True, auto_refresh=False) as live:
            while True:
                live.update(render_dashboard(), refresh=True)
                time.sleep(interval)
    except KeyboardInterrupt:
        err_console.print("[yellow]Gateway watch stopped.[/yellow]")


def _emit_daemon_log(message: str) -> None:
    """GatewayDaemon log callback — writes one timestamped line to err_console.

    When `ax gateway run` is launched in the background, err_console's stream
    is redirected to `daemon_log_path()` (gateway.log). Each line carries an
    ISO-8601 UTC timestamp matching activity.jsonl's `ts` shape so the two
    streams correlate by their leading column.
    """
    from . import gateway as _gateway_cmd

    _format = getattr(_gateway_cmd, "_format_daemon_log_line", _format_daemon_log_line)
    _err = getattr(_gateway_cmd, "err_console", err_console)
    _err.print(f"[dim]{_format(message)}[/dim]")


def run_gateway(
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Registry reconcile interval in seconds"),
    once: bool = typer.Option(False, "--once", help="Run one reconcile pass and exit"),
):
    """Run the foreground Gateway supervisor."""
    from . import gateway as _gateway_cmd

    _load_or_exit = getattr(_gateway_cmd, "_load_gateway_session_or_exit")
    _gateway_dir = getattr(_gateway_cmd, "gateway_dir", gateway_dir)
    _GatewayDaemon = getattr(_gateway_cmd, "GatewayDaemon", GatewayDaemon)
    _emit = getattr(_gateway_cmd, "_emit_daemon_log", _emit_daemon_log)

    _load_or_exit()
    err_console.print("[bold]ax gateway[/bold] — local control plane")
    err_console.print(f"  state_dir = {_gateway_dir()}")
    err_console.print(f"  interval  = {poll_interval}s")
    err_console.print(f"  mode      = {'single-pass' if once else 'foreground'}")
    daemon = _GatewayDaemon(logger=_emit, poll_interval=poll_interval)
    try:
        daemon.run(once=once)
    except RuntimeError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        daemon.stop()
        err_console.print("[yellow]Gateway stopped.[/yellow]")
