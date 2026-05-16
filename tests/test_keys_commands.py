import json
from unittest.mock import MagicMock

import httpx
from typer.testing import CliRunner

from ax_cli.main import app

runner = CliRunner()


def test_keys_create_passes_bound_agent_id(monkeypatch):
    captured = {}

    class FakeClient:
        def create_key(self, name, *, allowed_agent_ids=None, bound_agent_id=None, audience=None):
            captured["name"] = name
            captured["allowed_agent_ids"] = allowed_agent_ids
            captured["bound_agent_id"] = bound_agent_id
            return {"credential_id": "c1", "token": "axp_a_test"}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    uuid = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    result = runner.invoke(
        app,
        ["keys", "create", "--name", "orion", "--bound-agent-id", uuid, "--json"],
    )
    assert result.exit_code == 0, result.output
    assert captured == {
        "name": "orion",
        "allowed_agent_ids": None,
        "bound_agent_id": uuid,
    }
    payload = json.loads(result.output)
    assert payload["credential_id"] == "c1"
    assert payload["token"] == "axp_a_test"


def test_keys_create_passes_scope_and_bound_agent_id(monkeypatch):
    captured = {}

    class FakeClient:
        def create_key(self, name, *, allowed_agent_ids=None, bound_agent_id=None, audience=None):
            captured["allowed_agent_ids"] = allowed_agent_ids
            captured["bound_agent_id"] = bound_agent_id
            return {"credential_id": "c2"}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    aid = "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22"
    result = runner.invoke(
        app,
        [
            "keys",
            "create",
            "--name",
            "combo",
            "--scope-to-agent",
            aid,
            "--bound-agent-id",
            aid,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["allowed_agent_ids"] == [aid]
    assert captured["bound_agent_id"] == aid


# ── create: text output path ──────────────────────────────────────────────


def test_keys_create_text_output(monkeypatch):
    class FakeClient:
        def create_key(self, name, *, allowed_agent_ids=None, bound_agent_id=None, audience=None):
            return {"credential_id": "cred-123", "token": "tok-secret"}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "create", "--name", "mykey"])
    assert result.exit_code == 0, result.output
    assert "Key created: cred-123" in result.output
    assert "Token: tok-secret" in result.output
    assert "Save this token" in result.output


def test_keys_create_text_output_no_token(monkeypatch):
    class FakeClient:
        def create_key(self, name, *, allowed_agent_ids=None, bound_agent_id=None, audience=None):
            return {"credential_id": "cred-456", "id": "id-456"}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "create", "--name", "notoken"])
    assert result.exit_code == 0, result.output
    assert "Key created: cred-456" in result.output
    assert "Token:" not in result.output
    assert "Save this token" in result.output


def _make_http_error(status_code=403, url="http://test.local/api/v1/keys"):
    request = httpx.Request("POST", url)
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = {"detail": "forbidden"}
    response.text = '{"detail": "forbidden"}'
    return httpx.HTTPStatusError("error", request=request, response=response)


def test_keys_create_http_error(monkeypatch):
    class FakeClient:
        def create_key(self, name, *, allowed_agent_ids=None, bound_agent_id=None, audience=None):
            raise _make_http_error()

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "create", "--name", "fail"])
    assert result.exit_code == 1


# ── list: JSON output ─────────────────────────────────────────────────────


def test_keys_list_json_output(monkeypatch):
    keys_data = [
        {"credential_id": "c1", "name": "k1", "scopes": "all", "created_at": "2026-01-01"},
        {"credential_id": "c2", "name": "k2", "scopes": "read", "created_at": "2026-02-01"},
    ]

    class FakeClient:
        def list_keys(self):
            return keys_data

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 2
    assert payload[0]["credential_id"] == "c1"


def test_keys_list_dict_wrapped_response(monkeypatch):
    class FakeClient:
        def list_keys(self):
            return {"keys": [{"credential_id": "c1", "name": "wrapped"}]}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["name"] == "wrapped"


def test_keys_list_text_output(monkeypatch):
    class FakeClient:
        def list_keys(self):
            return [
                {
                    "credential_id": "c1",
                    "name": "k1",
                    "scopes": "",
                    "allowed_agent_ids": "",
                    "last_used_at": "",
                    "created_at": "2026-01-01",
                    "revoked_at": "",
                }
            ]

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "list"])
    assert result.exit_code == 0, result.output


def test_keys_list_http_error(monkeypatch):
    class FakeClient:
        def list_keys(self):
            raise _make_http_error(status_code=500, url="http://test.local/api/v1/keys")

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "list"])
    assert result.exit_code == 1


# ── revoke ────────────────────────────────────────────────────────────────


def test_keys_revoke_success(monkeypatch):
    class FakeClient:
        def revoke_key(self, credential_id):
            return 204

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "revoke", "cred-abc"])
    assert result.exit_code == 0, result.output
    assert "Revoked." in result.output


def test_keys_revoke_http_error(monkeypatch):
    class FakeClient:
        def revoke_key(self, credential_id):
            raise _make_http_error(status_code=404, url="http://test.local/api/v1/keys/cred-abc")

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "revoke", "cred-abc"])
    assert result.exit_code == 1


# ── rotate ────────────────────────────────────────────────────────────────


def test_keys_rotate_json_output(monkeypatch):
    class FakeClient:
        def rotate_key(self, credential_id):
            return {"credential_id": "cred-new", "token": "new-secret"}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "rotate", "cred-old", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["credential_id"] == "cred-new"


def test_keys_rotate_text_output_with_token(monkeypatch):
    class FakeClient:
        def rotate_key(self, credential_id):
            return {"credential_id": "cred-rot", "token": "rotated-secret"}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "rotate", "cred-rot"])
    assert result.exit_code == 0, result.output
    assert "New token: rotated-secret" in result.output
    assert "Save this token" in result.output


def test_keys_rotate_text_output_key_fallback(monkeypatch):
    class FakeClient:
        def rotate_key(self, credential_id):
            return {"credential_id": "cred-rot", "key": "key-secret"}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "rotate", "cred-rot"])
    assert result.exit_code == 0, result.output
    assert "New token: key-secret" in result.output


def test_keys_rotate_text_output_no_token(monkeypatch):
    class FakeClient:
        def rotate_key(self, credential_id):
            return {"credential_id": "cred-rot"}

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "rotate", "cred-rot"])
    assert result.exit_code == 0, result.output
    assert "New token:" not in result.output
    assert "Save this token" in result.output


def test_keys_rotate_http_error(monkeypatch):
    class FakeClient:
        def rotate_key(self, credential_id):
            raise _make_http_error(status_code=404, url="http://test.local/api/v1/keys/cred-old/rotate")

    monkeypatch.setattr("ax_cli.commands.keys.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["keys", "rotate", "cred-old"])
    assert result.exit_code == 1
