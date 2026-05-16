import json
from unittest.mock import MagicMock

from typer.testing import CliRunner

from ax_cli.commands.credentials import build_credential_audit
from ax_cli.main import app

runner = CliRunner()


def _credential(agent_id: str, credential_id: str, *, state: str = "active", created_at: str = "2026-04-15T00:00:00Z"):
    return {
        "credential_id": credential_id,
        "key_id": f"key-{credential_id}",
        "name": f"credential {credential_id}",
        "bound_agent_id": agent_id,
        "audience": "both",
        "lifecycle_state": state,
        "created_at": created_at,
        "expires_at": "2026-05-15T00:00:00Z",
        "last_used_at": None,
    }


def test_build_credential_audit_classifies_active_agent_pat_counts():
    report = build_credential_audit(
        [
            _credential("agent-ok", "ok-1"),
            _credential("agent-rotate", "rotate-1", created_at="2026-04-14T00:00:00Z"),
            _credential("agent-rotate", "rotate-2", created_at="2026-04-15T00:00:00Z"),
            _credential("agent-cleanup", "cleanup-1"),
            _credential("agent-cleanup", "cleanup-2"),
            _credential("agent-cleanup", "cleanup-3"),
            _credential("agent-cleanup", "revoked-ignored", state="revoked"),
            {"credential_id": "user-credential", "lifecycle_state": "active", "bound_agent_id": None},
        ]
    )

    by_agent = {agent["agent_id"]: agent for agent in report["agents"]}
    assert by_agent["agent-ok"]["status"] == "ok"
    assert by_agent["agent-rotate"]["status"] == "rotation_window"
    assert by_agent["agent-rotate"]["severity"] == "warning"
    assert by_agent["agent-cleanup"]["status"] == "cleanup_required"
    assert by_agent["agent-cleanup"]["severity"] == "violation"
    assert report["summary"] == {
        "agents_checked": 3,
        "ok": 1,
        "rotation_windows": 1,
        "cleanup_required": 1,
    }


def test_credentials_audit_json_reports_rotation_and_cleanup(monkeypatch):
    class FakeClient:
        def mgmt_list_credentials(self):
            return [
                _credential("agent-rotate", "rotate-1"),
                _credential("agent-rotate", "rotate-2"),
                _credential("agent-cleanup", "cleanup-1"),
                _credential("agent-cleanup", "cleanup-2"),
                _credential("agent-cleanup", "cleanup-3"),
            ]

    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["credentials", "audit", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["policy"]["max_active_agent_pats"] == 2
    assert report["summary"]["rotation_windows"] == 1
    assert report["summary"]["cleanup_required"] == 1


def test_credentials_audit_strict_fails_only_for_cleanup_required(monkeypatch):
    class RotationWindowClient:
        def mgmt_list_credentials(self):
            return [_credential("agent-rotate", "rotate-1"), _credential("agent-rotate", "rotate-2")]

    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: RotationWindowClient())

    rotation_result = runner.invoke(app, ["credentials", "audit", "--strict", "--json"])
    assert rotation_result.exit_code == 0, rotation_result.output

    class CleanupClient:
        def mgmt_list_credentials(self):
            return [
                _credential("agent-cleanup", "cleanup-1"),
                _credential("agent-cleanup", "cleanup-2"),
                _credential("agent-cleanup", "cleanup-3"),
            ]

    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: CleanupClient())

    cleanup_result = runner.invoke(app, ["credentials", "audit", "--strict", "--json"])
    assert cleanup_result.exit_code == 2, cleanup_result.output


# ---------- issue-agent-pat ----------


def test_issue_agent_pat_json_with_uuid(monkeypatch):
    client = MagicMock()
    client.mgmt_issue_agent_pat.return_value = {
        "token": "axp_a_TestKey.Secret",
        "expires_at": "2026-08-01T00:00:00Z",
    }
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    agent_uuid = "12345678-1234-1234-1234-123456789012"
    result = runner.invoke(app, ["credentials", "issue-agent-pat", agent_uuid, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["token"] == "axp_a_TestKey.Secret"
    client.mgmt_issue_agent_pat.assert_called_once_with(agent_uuid, name=None, expires_in_days=90, audience="cli")


def test_issue_agent_pat_text_with_uuid(monkeypatch):
    client = MagicMock()
    client.mgmt_issue_agent_pat.return_value = {
        "token": "axp_a_TestKey.Secret",
        "expires_at": "2026-08-01T00:00:00Z",
    }
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    agent_uuid = "12345678-1234-1234-1234-123456789012"
    result = runner.invoke(app, ["credentials", "issue-agent-pat", agent_uuid])
    assert result.exit_code == 0, result.output
    assert "Agent PAT created" in result.output
    assert "Expires" in result.output
    assert "Token" in result.output


def test_issue_agent_pat_resolves_name(monkeypatch):
    client = MagicMock()
    client.mgmt_list_agents.return_value = [
        {"name": "my-bot", "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
    ]
    client.mgmt_issue_agent_pat.return_value = {
        "token": "axp_a_Key.Val",
        "expires_at": "2026-08-01T00:00:00Z",
    }
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "issue-agent-pat", "my-bot", "--json"])
    assert result.exit_code == 0, result.output
    client.mgmt_issue_agent_pat.assert_called_once_with(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name=None,
        expires_in_days=90,
        audience="cli",
    )


def test_issue_agent_pat_name_not_found(monkeypatch):
    client = MagicMock()
    client.mgmt_list_agents.return_value = [
        {"name": "other-bot", "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
    ]
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "issue-agent-pat", "ghost-bot"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_issue_agent_pat_with_options(monkeypatch):
    client = MagicMock()
    client.mgmt_issue_agent_pat.return_value = {
        "token": "axp_a_K.V",
        "expires_at": "2026-09-01T00:00:00Z",
    }
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    agent_uuid = "12345678-1234-1234-1234-123456789012"
    result = runner.invoke(
        app,
        [
            "credentials",
            "issue-agent-pat",
            agent_uuid,
            "--name",
            "prod-key",
            "--expires",
            "30",
            "--audience",
            "both",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    client.mgmt_issue_agent_pat.assert_called_once_with(
        agent_uuid, name="prod-key", expires_in_days=30, audience="both"
    )


# ---------- issue-enrollment ----------


def test_issue_enrollment_json(monkeypatch):
    client = MagicMock()
    client.mgmt_issue_enrollment.return_value = {
        "token": "axp_a_EnrollKey.Secret",
        "expires_at": "2026-05-12T01:00:00Z",
        "lifecycle_state": "pending",
    }
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "issue-enrollment", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["lifecycle_state"] == "pending"


def test_issue_enrollment_text(monkeypatch):
    client = MagicMock()
    client.mgmt_issue_enrollment.return_value = {
        "token": "axp_a_EnrollKey.Secret",
        "expires_at": "2026-05-12T01:00:00Z",
        "lifecycle_state": "pending",
    }
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "issue-enrollment"])
    assert result.exit_code == 0, result.output
    assert "Enrollment token created" in result.output
    assert "Expires" in result.output
    assert "pending" in result.output
    assert "axctl auth init" in result.output


def test_issue_enrollment_with_options(monkeypatch):
    client = MagicMock()
    client.mgmt_issue_enrollment.return_value = {
        "token": "axp_a_K.V",
        "expires_at": "2026-05-12T04:00:00Z",
        "lifecycle_state": "pending",
    }
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(
        app,
        [
            "credentials",
            "issue-enrollment",
            "--name",
            "test-enroll",
            "--expires",
            "4",
            "--audience",
            "mcp",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    client.mgmt_issue_enrollment.assert_called_once_with(name="test-enroll", expires_in_hours=4, audience="mcp")


# ---------- revoke ----------


def test_revoke_with_yes_flag(monkeypatch):
    client = MagicMock()
    client.mgmt_revoke_credential.return_value = None
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    cred_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = runner.invoke(app, ["credentials", "revoke", cred_id, "--yes"])
    assert result.exit_code == 0, result.output
    assert "Revoked" in result.output
    client.mgmt_revoke_credential.assert_called_once_with(cred_id)


def test_revoke_confirm_yes(monkeypatch):
    client = MagicMock()
    client.mgmt_revoke_credential.return_value = None
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    cred_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = runner.invoke(app, ["credentials", "revoke", cred_id], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Revoked" in result.output


def test_revoke_confirm_no(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    cred_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = runner.invoke(app, ["credentials", "revoke", cred_id], input="n\n")
    assert result.exit_code != 0


# ---------- audit text output ----------


def test_audit_text_with_agents(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = [
        _credential("agent-a", "cred-1"),
        _credential("agent-b", "cred-2"),
        _credential("agent-b", "cred-3"),
    ]
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "audit"])
    assert result.exit_code == 0, result.output
    assert "Agent PAT audit" in result.output
    assert "agent-a" in result.output
    assert "agent-b" in result.output


def test_audit_text_empty(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = []
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "audit"])
    assert result.exit_code == 0, result.output
    assert "No active agent-bound PATs" in result.output


def test_audit_strict_passes_when_ok(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = [_credential("agent-ok", "cred-1")]
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "audit", "--strict"])
    assert result.exit_code == 0, result.output


# ---------- list credentials ----------


def test_list_credentials_json(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = [
        _credential("agent-a", "cred-1"),
    ]
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 1


def test_list_credentials_text_active(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = [
        _credential("agent-a", "cred-1", state="active"),
    ]
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "list"])
    assert result.exit_code == 0, result.output
    assert "active" in result.output
    assert "key-cred-1" in result.output


def test_list_credentials_text_revoked(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = [
        _credential("agent-a", "cred-1", state="revoked"),
    ]
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "list"])
    assert result.exit_code == 0, result.output
    assert "revoked" in result.output


def test_list_credentials_text_expired(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = [
        _credential("agent-a", "cred-1", state="expired"),
    ]
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "list"])
    assert result.exit_code == 0, result.output
    assert "expired" in result.output


def test_list_credentials_text_no_bound_agent(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = [
        {
            "credential_id": "c1",
            "key_id": "key-c1",
            "name": "unbound",
            "bound_agent_id": None,
            "lifecycle_state": "active",
        }
    ]
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "list"])
    assert result.exit_code == 0, result.output
    assert "none" in result.output


def test_list_credentials_text_empty(monkeypatch):
    client = MagicMock()
    client.mgmt_list_credentials.return_value = []
    monkeypatch.setattr("ax_cli.commands.credentials.get_client", lambda: client)

    result = runner.invoke(app, ["credentials", "list"])
    assert result.exit_code == 0, result.output
    assert "No credentials found" in result.output
