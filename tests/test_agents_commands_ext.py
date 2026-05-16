"""Tests for agents CLI commands — uses mocked clients to avoid hangs."""

from unittest.mock import MagicMock

from typer.testing import CliRunner

from ax_cli.main import app

runner = CliRunner()


def _mock_client(monkeypatch, **methods):
    client = MagicMock()
    for name, return_value in methods.items():
        getattr(client, name).return_value = return_value
    monkeypatch.setattr("ax_cli.commands.agents.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.agents.resolve_space_id", lambda c, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.agents.resolve_agent_name", lambda client=None: "me")
    monkeypatch.setattr("ax_cli.commands.agents.resolve_gateway_config", lambda: None)
    return client


# ---- delete ----


def test_delete_json(monkeypatch):
    _mock_client(monkeypatch, delete_agent={"message": "deleted"})
    result = runner.invoke(app, ["agents", "delete", "bot-1", "--yes"])
    assert result.exit_code == 0
    assert "Deleted" in result.output or "deleted" in result.output


# ---- status ----


def test_status_json(monkeypatch):
    _mock_client(
        monkeypatch,
        get_agents_presence={
            "agents": [
                {"name": "alice", "presence": "online", "agent_type": "assistant", "last_active": "2026-01-01"},
                {"name": "bob", "presence": "offline", "agent_type": "sentinel", "last_active": "2026-01-01"},
            ]
        },
    )
    result = runner.invoke(app, ["agents", "status", "--json"])
    assert result.exit_code == 0
    assert "alice" in result.output


def test_status_text(monkeypatch):
    _mock_client(
        monkeypatch,
        get_agents_presence={
            "agents": [
                {"name": "alice", "presence": "online", "agent_type": "assistant"},
            ]
        },
    )
    result = runner.invoke(app, ["agents", "status"])
    assert result.exit_code == 0
    assert "alice" in result.output


# ---- check ----


def test_check_json(monkeypatch):
    _mock_client(
        monkeypatch,
        get_agent_presence={
            "name": "alice",
            "presence": "online",
            "responsive": True,
            "last_active": "2026-01-01",
            "agent_type": "assistant",
        },
    )
    result = runner.invoke(app, ["agents", "check", "alice", "--json"])
    assert result.exit_code == 0
    assert "alice" in result.output


def test_check_text_online(monkeypatch):
    _mock_client(
        monkeypatch,
        get_agent_presence={
            "name": "alice",
            "presence": "online",
            "responsive": True,
            "last_active": "2026-01-01",
            "agent_type": "assistant",
        },
    )
    result = runner.invoke(app, ["agents", "check", "alice"])
    assert result.exit_code == 0
    assert "alice" in result.output


def test_check_text_offline(monkeypatch):
    _mock_client(
        monkeypatch,
        get_agent_presence={
            "name": "bob",
            "presence": "offline",
            "last_active": "2025-12-01",
            "agent_type": "sentinel",
        },
    )
    result = runner.invoke(app, ["agents", "check", "bob"])
    assert result.exit_code == 0
    assert "bob" in result.output


def test_check_with_avail_contract_fields(monkeypatch):
    _mock_client(
        monkeypatch,
        get_agent_presence={
            "name": "alice",
            "presence": "online",
            "badge_state": "live",
            "badge_label": "Live - Event Listener",
            "connection_path": "sse",
            "expected_response": "immediate",
            "confidence": 0.95,
            "status_explanation": "Agent is connected and responding",
            "pre_send_warning": {
                "severity": "warning",
                "title": "High load",
                "body": "Response may be delayed",
            },
        },
    )
    result = runner.invoke(app, ["agents", "check", "alice"])
    assert result.exit_code == 0
    assert "alice" in result.output


def test_check_runtime_error(monkeypatch):
    client = _mock_client(monkeypatch)
    client.get_agent_presence.side_effect = RuntimeError("agent not found")
    result = runner.invoke(app, ["agents", "check", "ghost"])
    assert result.exit_code == 1
    assert "agent not found" in result.output


# ---- placement get ----


def test_placement_get_json(monkeypatch):
    _mock_client(
        monkeypatch,
        get_agent_placement={
            "agent_id": "a-1",
            "space_id": "s-1",
            "pinned": True,
        },
    )
    result = runner.invoke(app, ["agents", "placement", "get", "alice", "--json"])
    assert result.exit_code == 0
    assert "a-1" in result.output


def test_placement_get_text(monkeypatch):
    _mock_client(
        monkeypatch,
        get_agent_placement={
            "agent_id": "a-1",
            "space_id": "s-1",
            "pinned": True,
        },
    )
    result = runner.invoke(app, ["agents", "placement", "get", "alice"])
    assert result.exit_code == 0


def test_placement_get_runtime_error(monkeypatch):
    client = _mock_client(monkeypatch)
    client.get_agent_placement.side_effect = RuntimeError("not found")
    result = runner.invoke(app, ["agents", "placement", "get", "ghost"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---- placement set ----


def test_placement_set_json(monkeypatch):
    _mock_client(monkeypatch, set_agent_placement={"ok": True})
    result = runner.invoke(
        app,
        [
            "agents",
            "placement",
            "set",
            "alice",
            "--space-id",
            "s-1",
            "--pinned",
            "--json",
        ],
    )
    assert result.exit_code == 0


def test_placement_set_text(monkeypatch):
    _mock_client(monkeypatch, set_agent_placement={"agent_name": "alice"})
    result = runner.invoke(
        app,
        [
            "agents",
            "placement",
            "set",
            "alice",
            "--space-id",
            "s-1",
        ],
    )
    assert result.exit_code == 0
    assert "alice" in result.output
