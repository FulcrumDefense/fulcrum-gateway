import json
import re
from unittest.mock import MagicMock

import httpx
import typer
from typer.testing import CliRunner

from ax_cli.commands.messages import (
    _processing_status_from_event,
    _processing_status_text,
    _ProcessingStatusWatcher,
)
from ax_cli.main import app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def test_send_file_stores_context_and_includes_context_key(monkeypatch, tmp_path):
    calls = {}
    sample = tmp_path / "WidgetContractProbe.java"
    sample.write_text(
        'public final class WidgetContractProbe { String status() { return "ok"; } }\n',
        encoding="utf-8",
    )

    class FakeClient:
        _base_headers = {}

        def upload_file(self, path, *, space_id=None):
            calls["upload"] = {"path": path, "space_id": space_id}
            return {
                "id": "att-1",
                "attachment_id": "att-1",
                "url": "/api/v1/uploads/files/probe.java",
                "content_type": "text/plain",
                "size": sample.stat().st_size,
                "original_filename": sample.name,
            }

        def set_context(self, space_id, key, value):
            calls["context"] = {"space_id": space_id, "key": key, "value": value}
            return {"ok": True}

        def send_message(
            self,
            space_id,
            content,
            *,
            channel="main",
            parent_id=None,
            attachments=None,
        ):
            calls["message"] = {
                "space_id": space_id,
                "content": content,
                "channel": channel,
                "parent_id": parent_id,
                "attachments": attachments,
            }
            return {"id": "msg-1"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)

    result = runner.invoke(
        app,
        [
            "send",
            "sharing source",
            "--file",
            str(sample),
            "--skip-ax",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["upload"]["space_id"] == "space-1"

    context_key = calls["context"]["key"]
    context_value = json.loads(calls["context"]["value"])
    assert context_key.startswith("upload:")
    assert context_value["type"] == "file_upload"
    assert context_value["context_key"] == context_key
    assert context_value["source"] == "message_attachment"
    assert "WidgetContractProbe" in context_value["content"]

    attachment = calls["message"]["attachments"][0]
    assert attachment["context_key"] == context_key
    assert attachment["filename"] == sample.name
    assert attachment["content_type"] == "text/plain"
    assert attachment["size"] == sample.stat().st_size
    assert attachment["size_bytes"] == sample.stat().st_size


def test_send_uses_gateway_native_identity_without_space_override(monkeypatch):
    posts = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(url, *, json=None, headers=None, timeout=None):
        posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if url.endswith("/local/connect"):
            return FakeResponse({"session_token": "gw-session", "status": "approved", "agent": {"name": "mac-backend"}})
        if url.endswith("/local/proxy"):
            # Pre-send pending-reply check goes through /local/proxy; return empty.
            return FakeResponse({"result": {"messages": []}})
        if url.endswith("/local/send"):
            assert headers == {"X-Gateway-Session": "gw-session"}
            return FakeResponse(
                {
                    "agent": "mac-backend",
                    "message": {
                        "id": "msg-1",
                        "display_name": "mac-backend",
                        "sender_type": "agent",
                    },
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_gateway_config",
        lambda: {"url": "http://127.0.0.1:8765", "agent_name": "mac-backend"},
    )
    monkeypatch.setattr("ax_cli.commands.messages._local_process_fingerprint", lambda **kwargs: {"fingerprint": "fp"})
    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)
    monkeypatch.setattr(
        "ax_cli.commands.messages.get_client", lambda: (_ for _ in ()).throw(AssertionError("direct client"))
    )

    result = runner.invoke(app, ["send", "hello from backend", "--no-wait", "--json"])

    assert result.exit_code == 0, result.output
    connects = [p for p in posts if p["url"].endswith("/local/connect")]
    sends = [p for p in posts if p["url"].endswith("/local/send")]
    assert connects, "expected at least one /local/connect call"
    assert connects[0]["json"] == {"fingerprint": {"fingerprint": "fp"}, "agent_name": "mac-backend"}
    assert len(sends) == 1
    assert sends[0]["json"] == {"content": "hello from backend", "space_id": None, "parent_id": None}
    payload = json.loads(result.output)
    assert payload["message"]["id"] == "msg-1"


def test_send_gateway_native_identity_uses_explicit_space_only(monkeypatch):
    sends = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(url, *, json=None, headers=None, timeout=None):
        if url.endswith("/local/connect"):
            sends.append(("connect", json))
            return FakeResponse({"session_token": "gw-session", "status": "approved"})
        if url.endswith("/local/send"):
            sends.append(("send", json))
            return FakeResponse({"message": {"id": "msg-2"}})
        raise AssertionError(url)

    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_gateway_config",
        lambda: {"url": "http://127.0.0.1:8765", "agent_name": "mac-backend"},
    )
    monkeypatch.setattr("ax_cli.commands.messages._local_process_fingerprint", lambda **kwargs: {"fingerprint": "fp"})
    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)

    result = runner.invoke(app, ["send", "hello", "--space-id", "space-db-override", "--no-wait", "--json"])

    assert result.exit_code == 0, result.output
    assert sends[0][1]["space_id"] == "space-db-override"
    assert sends[1][1]["space_id"] == "space-db-override"


def test_send_gateway_native_identity_pending_approval_guides_agent(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "pending",
                "approval_id": "approval-123",
                "agent": {
                    "name": "backend_sentinel",
                    "workdir": "/Users/jacob/claude_home/ax-backend-extract",
                    "active_space_name": "madtank's Workspace",
                },
                "approval": {
                    "approval_kind": "new_binding",
                    "risk": "medium",
                },
            }

    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_gateway_config",
        lambda: {
            "url": "http://127.0.0.1:8765",
            "agent_name": "backend_sentinel",
            "workdir": "/Users/jacob/claude_home/ax-backend-extract",
        },
    )
    monkeypatch.setattr("ax_cli.commands.messages._local_process_fingerprint", lambda **kwargs: {"fingerprint": "fp"})
    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", lambda *args, **kwargs: FakeResponse())

    result = runner.invoke(app, ["send", "hello from backend", "--no-wait"])

    assert result.exit_code != 0
    assert "Gateway approval required for @backend_sentinel" in result.output
    assert "open http://127.0.0.1:8765" in result.output.lower()
    assert "approval_id=approval-123" in result.output
    assert "workdir=/Users/jacob/claude_home/ax-backend-extract" in result.output
    assert "Do not fall back to a direct PAT" in result.output


def test_messages_list_shows_short_ids_but_json_keeps_full_ids(monkeypatch):
    message_id = "12345678-90ab-cdef-1234-567890abcdef"
    calls = {}

    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None):
            calls["space_id"] = space_id
            return {
                "messages": [
                    {
                        "id": message_id,
                        "content": "hello",
                        "display_name": "orion",
                        "created_at": "2026-04-13T15:00:00Z",
                    }
                ]
            }

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")

    table_result = runner.invoke(app, ["messages", "list"])
    assert table_result.exit_code == 0, table_result.output
    assert "12345678" in table_result.output
    assert message_id not in table_result.output
    assert calls["space_id"] == "space-1"

    json_result = runner.invoke(app, ["messages", "list", "--json"])
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output)[0]["id"] == message_id


def test_messages_list_can_request_unread_and_mark_read(monkeypatch):
    calls = {}

    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None, unread_only=False, mark_read=False):
            calls["list"] = {
                "limit": limit,
                "channel": channel,
                "space_id": space_id,
                "unread_only": unread_only,
                "mark_read": mark_read,
            }
            return {
                "messages": [
                    {
                        "id": "12345678-90ab-cdef-1234-567890abcdef",
                        "content": "unread update",
                        "display_name": "orion",
                        "created_at": "2026-04-13T15:00:00Z",
                    }
                ],
                "unread_count": 3,
                "marked_read_count": 1,
            }

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["messages", "list", "--unread", "--mark-read"])

    assert result.exit_code == 0, result.output
    assert calls["list"] == {
        "limit": 20,
        "channel": "main",
        "space_id": "space-1",
        "unread_only": True,
        "mark_read": True,
    }
    assert "Unread: 3" in result.output
    assert "Marked read: 1" in result.output


def test_messages_list_accepts_space_slug_alias(monkeypatch):
    calls = {}

    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None, unread_only=False, mark_read=False):
            calls["space_id"] = space_id
            return {"messages": []}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: f"resolved:{explicit}"
    )

    result = runner.invoke(app, ["messages", "list", "--space", "ax-cli-dev", "--json"])

    assert result.exit_code == 0, result.output
    assert calls["space_id"] == "resolved:ax-cli-dev"


def test_send_accepts_space_slug_alias(monkeypatch):
    calls = {}

    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            calls["message"] = {"space_id": space_id, "content": content}
            return {"id": "msg-1"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: f"resolved:{explicit}"
    )
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)

    result = runner.invoke(app, ["send", "checkpoint", "--space", "ax-cli-dev", "--no-wait", "--json"])

    assert result.exit_code == 0, result.output
    assert calls["message"] == {"space_id": "resolved:ax-cli-dev", "content": "checkpoint"}


def test_messages_read_marks_single_message(monkeypatch):
    calls = {}

    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None):
            return {"messages": [{"id": "12345678-90ab-cdef-1234-567890abcdef"}]}

        def mark_message_read(self, message_id):
            calls["message_id"] = message_id
            return {"status": "success", "message_id": message_id}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["messages", "read", "12345678", "--json"])

    assert result.exit_code == 0, result.output
    assert calls["message_id"] == "12345678-90ab-cdef-1234-567890abcdef"


def test_messages_read_all_marks_space_read(monkeypatch):
    calls = {}

    class FakeClient:
        def mark_all_messages_read(self):
            calls["all"] = True
            return {"status": "success", "marked_read": 2}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())

    result = runner.invoke(app, ["messages", "read", "--all", "--json"])

    assert result.exit_code == 0, result.output
    assert calls["all"] is True
    assert json.loads(result.output)["marked_read"] == 2


def test_send_help_prefers_no_wait_language(monkeypatch):
    result = runner.invoke(app, ["send", "--help"], terminal_width=80)
    output = _strip_ansi(result.output)

    assert result.exit_code == 0, result.output
    assert "--no-wait" in output
    assert "--skip-ax" not in output
    assert "--to" in output
    assert "intercom" in output

    calls = {}

    def fake_send(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr("ax_cli.main.messages.send", fake_send)

    no_wait_result = runner.invoke(app, ["send", "notify only", "--no-wait"])

    assert no_wait_result.exit_code == 0, no_wait_result.output
    assert calls["wait"] is False
    assert calls["skip_ax"] is False


def test_messages_get_resolves_short_id_prefix(monkeypatch):
    message_id = "12345678-90ab-cdef-1234-567890abcdef"
    calls = {}

    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None):
            calls["list_limit"] = limit
            calls["space_id"] = space_id
            return {"messages": [{"id": message_id}]}

        def get_message(self, requested_id):
            calls["get_id"] = requested_id
            return {"id": requested_id, "content": "hello"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["messages", "get", "12345678", "--json"])
    assert result.exit_code == 0, result.output
    assert calls["list_limit"] == 100
    assert calls["space_id"] == "space-1"
    assert calls["get_id"] == message_id
    assert json.loads(result.output)["id"] == message_id


def test_messages_send_resolves_short_parent_id(monkeypatch):
    parent_id = "abcdef12-3456-7890-abcd-ef1234567890"
    calls = {}

    class FakeClient:
        _base_headers = {}

        def list_messages(self, limit=20, channel="main", *, space_id=None):
            calls["list_limit"] = limit
            calls["space_id"] = space_id
            return {"messages": [{"id": parent_id}]}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            calls["message"] = {
                "space_id": space_id,
                "content": content,
                "channel": channel,
                "parent_id": parent_id,
                "attachments": attachments,
            }
            return {"id": "reply-message-id"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)

    result = runner.invoke(app, ["messages", "send", "reply", "--parent", "abcdef12", "--skip-ax", "--json"])
    assert result.exit_code == 0, result.output
    assert calls["list_limit"] == 100
    assert calls["space_id"] == "space-1"
    assert calls["message"]["parent_id"] == parent_id


def test_top_level_send_accepts_parent_alias(monkeypatch):
    calls = {}

    def fake_send(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr("ax_cli.main.messages.send", fake_send)

    result = runner.invoke(app, ["send", "reply", "--parent", "abcdef12", "--skip-ax"])
    assert result.exit_code == 0, result.output
    assert calls["content"] == "reply"
    assert calls["parent"] == "abcdef12"


def test_send_to_prepends_missing_mention(monkeypatch):
    calls = {}

    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            calls["message"] = {
                "space_id": space_id,
                "content": content,
                "channel": channel,
                "parent_id": parent_id,
                "attachments": attachments,
            }
            return {"id": "msg-1"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)

    result = runner.invoke(app, ["send", "checkpoint", "--to", "orion", "--no-wait", "--json"])

    assert result.exit_code == 0, result.output
    assert calls["message"]["content"] == "@orion checkpoint"


def test_send_ask_ax_prepends_ax_mention(monkeypatch):
    calls = {}

    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            calls["message"] = {
                "space_id": space_id,
                "content": content,
                "channel": channel,
                "parent_id": parent_id,
                "attachments": attachments,
            }
            return {"id": "msg-1"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)

    result = runner.invoke(app, ["send", "please summarize unread", "--ask-ax", "--no-wait", "--json"])

    assert result.exit_code == 0, result.output
    assert calls["message"]["content"] == "@aX please summarize unread"


def test_send_ask_ax_does_not_duplicate_existing_ax_mention(monkeypatch):
    calls = {}

    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            calls["message"] = {"content": content}
            return {"id": "msg-1"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)

    result = runner.invoke(app, ["send", "@aX please summarize unread", "--ask-ax", "--no-wait", "--json"])

    assert result.exit_code == 0, result.output
    assert calls["message"]["content"] == "@aX please summarize unread"


def test_send_ask_ax_rejects_to_combination():
    result = runner.invoke(app, ["send", "route this", "--ask-ax", "--to", "orion"])

    assert result.exit_code == 1
    assert "use either --ask-ax or --to" in result.output


def test_send_to_does_not_duplicate_existing_mention_and_waits_for_target(monkeypatch):
    calls = {}

    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            calls["message"] = {
                "space_id": space_id,
                "content": content,
                "channel": channel,
                "parent_id": parent_id,
                "attachments": attachments,
            }
            return {"id": "msg-1"}

    def fake_wait(client, message_id, timeout=60, wait_label="reply", **kwargs):
        calls["wait"] = {
            "message_id": message_id,
            "timeout": timeout,
            "wait_label": wait_label,
            "processing_watcher": kwargs.get("processing_watcher"),
        }
        return {"id": "reply-1", "content": "ack"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)
    monkeypatch.setattr("ax_cli.commands.messages._wait_for_reply", fake_wait)

    result = runner.invoke(app, ["send", "@orion checkpoint", "--to", "orion", "--json"])

    assert result.exit_code == 0, result.output
    assert calls["message"]["content"] == "@orion checkpoint"
    assert calls["wait"]["wait_label"] == "@orion"
    assert calls["wait"]["processing_watcher"] is not None


def test_send_prints_sender_identity_in_human_output(monkeypatch):
    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            return {
                "message": {
                    "id": "msg-1",
                    "display_name": "codex",
                    "sender_type": "agent",
                }
            }

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: "codex")

    result = runner.invoke(app, ["send", "checkpoint", "--no-wait"])

    assert result.exit_code == 0, result.output
    output = _strip_ansi(result.output)
    assert "Sent. id=msg-1 as @codex" in output


def test_send_prints_gateway_reply_note_in_human_output(monkeypatch):
    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            return {
                "message": {
                    "id": "msg-1",
                    "display_name": "codex",
                    "sender_type": "agent",
                }
            }

    def fake_wait(client, message_id, timeout=60, wait_label="reply", **kwargs):
        return {
            "id": "reply-1",
            "content": "ack",
            "metadata": {
                "control_plane": "gateway",
                "gateway": {
                    "gateway_id": "12345678-90ab-cdef-1234-567890abcdef",
                    "agent_name": "echo-bot",
                    "runtime_type": "echo",
                    "transport": "gateway",
                },
            },
        }

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: "codex")
    monkeypatch.setattr("ax_cli.commands.messages._wait_for_reply", fake_wait)

    result = runner.invoke(app, ["send", "checkpoint"])

    assert result.exit_code == 0, result.output
    output = _strip_ansi(result.output)
    assert "Sent. id=msg-1 as @codex" in output
    assert "aX: ack" in output
    assert "via Gateway 12345678" in output
    assert "agent=@echo-bot" in output


def test_processing_status_from_event_matches_message():
    event = _processing_status_from_event(
        "msg-1",
        "agent_processing",
        {
            "message_id": "msg-1",
            "status": "processing",
            "agent_id": "agent-1",
            "agent_name": "orion",
            "activity": "Running command",
            "tool_name": "shell",
        },
    )

    assert event == {
        "message_id": "msg-1",
        "status": "processing",
        "agent_id": "agent-1",
        "agent_name": "orion",
        "activity": "Running command",
        "tool_name": "shell",
    }
    assert _processing_status_from_event("msg-2", "agent_processing", {"message_id": "msg-1"}) is None
    assert _processing_status_from_event("msg-1", "message", {"message_id": "msg-1"}) is None


def test_processing_status_watcher_buffers_fast_tooling_receipt_until_message_id_known():
    watcher = _ProcessingStatusWatcher(client=None, space_id="space-1", timeout=5)
    status_event = {
        "message_id": "msg-1",
        "status": "accepted",
        "agent_id": "agent-1",
        "agent_name": "orion",
        "activity": "Queued in Gateway",
    }

    watcher._accept_status_event(status_event)

    assert watcher.drain() == []

    watcher.set_message_id("msg-1")

    assert watcher.drain() == [status_event]


def test_processing_status_text_marks_tooling_as_the_source():
    text = _processing_status_text({"status": "accepted", "agent_name": "orion", "activity": "Queued in Gateway"})

    assert text == "tooling: @orion acknowledged the message — Queued in Gateway"


def test_processing_status_text_highlights_gateway_pickup():
    text = _processing_status_text({"status": "started", "agent_name": "orion", "activity": "Picked up by Gateway"})

    assert text == "tooling: @orion picked up the message — Picked up by Gateway"


def test_processing_status_text_handles_no_reply():
    text = _processing_status_text({"status": "no_reply", "agent_name": "orion", "activity": "Chose not to respond"})

    assert text == "tooling: @orion chose not to respond — Chose not to respond"


def test_messages_edit_and_delete_resolve_short_id_prefix(monkeypatch):
    message_id = "12345678-90ab-cdef-1234-567890abcdef"
    calls = {}

    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None):
            calls["list_calls"] = calls.get("list_calls", 0) + 1
            calls.setdefault("space_ids", []).append(space_id)
            return {"messages": [{"id": message_id}]}

        def edit_message(self, requested_id, content):
            calls["edit"] = {"id": requested_id, "content": content}
            return {"id": requested_id, "content": content}

        def delete_message(self, requested_id):
            calls["delete_id"] = requested_id

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda client, explicit=None: "space-1")

    edit_result = runner.invoke(app, ["messages", "edit", "12345678", "updated", "--json"])
    assert edit_result.exit_code == 0, edit_result.output
    assert calls["edit"] == {"id": message_id, "content": "updated"}

    delete_result = runner.invoke(app, ["messages", "delete", "12345678", "--json"])
    assert delete_result.exit_code == 0, delete_result.output
    assert calls["delete_id"] == message_id
    assert calls["space_ids"] == ["space-1", "space-1"]
    assert json.loads(delete_result.output)["message_id"] == message_id


def test_gateway_local_send_extracts_mentions_into_metadata(monkeypatch):
    """The reply-routing fix: @mentions in content must reach Gateway's body.metadata."""
    from ax_cli.commands.messages import _gateway_local_send

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"id": "msg-1"}}

    def fake_post(url, *, json=None, headers=None, timeout=None):
        if url.endswith("/local/connect"):
            return FakeResponse()  # type: ignore[return-value]
        if url.endswith("/local/send"):
            captured["body"] = json
            return FakeResponse()  # type: ignore[return-value]
        raise AssertionError(url)

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"status": "approved", "session_token": "gw-session"},
    )
    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)

    _gateway_local_send(
        gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "wishy"},
        content="@nemotron @other_agent thanks for the review!",
        space_id="space-1",
        parent_id="parent-msg-7",
    )

    body = captured["body"]
    assert body["parent_id"] == "parent-msg-7"
    metadata = body.get("metadata") or {}
    assert metadata.get("mentions") == ["nemotron", "other_agent"]
    assert metadata.get("routing_intent") == "reply_with_mentions"


def test_gateway_local_send_omits_metadata_when_no_mentions_or_parent(monkeypatch):
    """Plain notify-only sends with no @mentions stay metadata-free."""
    from ax_cli.commands.messages import _gateway_local_send

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"id": "msg-1"}}

    def fake_post(url, *, json=None, headers=None, timeout=None):
        if url.endswith("/local/send"):
            captured["body"] = json
        return FakeResponse()  # type: ignore[return-value]

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"status": "approved", "session_token": "gw-session"},
    )
    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)

    _gateway_local_send(
        gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "wishy"},
        content="checking in, status update only",
        space_id="space-1",
        parent_id=None,
    )

    body = captured["body"]
    assert "metadata" not in body, f"unexpected metadata: {body.get('metadata')}"


def test_gateway_local_send_excludes_sender_from_mentions(monkeypatch):
    """If the sender's own handle appears in content, it must not be re-routed to itself."""
    from ax_cli.commands.messages import _gateway_local_send

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"id": "msg-1"}}

    def fake_post(url, *, json=None, headers=None, timeout=None):
        if url.endswith("/local/send"):
            captured["body"] = json
        return FakeResponse()  # type: ignore[return-value]

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"status": "approved", "session_token": "gw-session"},
    )
    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)

    _gateway_local_send(
        gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "wishy"},
        content="@wishy @nemotron — back atcha",
        space_id="space-1",
        parent_id="parent-1",
    )

    metadata = captured["body"].get("metadata") or {}
    assert metadata.get("mentions") == ["nemotron"], metadata.get("mentions")


# ---- Helper function unit tests ----


def test_gateway_local_connect_raises_on_empty_agent_name():
    import pytest

    from ax_cli.commands.messages import _gateway_local_connect

    with pytest.raises(typer.BadParameter, match="agent_name or \\[agent\\].registry_ref"):
        _gateway_local_connect(
            gateway_url="http://127.0.0.1:8765",
            agent_name="",
            registry_ref="",
            workdir="/tmp",
            space_id=None,
        )


def test_gateway_local_connect_http_error(monkeypatch):
    """HTTPStatusError from /local/connect produces a BadParameter with guidance."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_connect

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "forbidden"
    mock_response.json.return_value = {"error": "agent not approved"}

    def fake_post(url, *, json=None, timeout=None):
        raise httpx.HTTPStatusError("403", request=MagicMock(), response=mock_response)

    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)
    monkeypatch.setattr("ax_cli.commands.messages._local_process_fingerprint", lambda **kwargs: {"fp": "test"})
    monkeypatch.setattr(
        "ax_cli.commands.messages._local_route_failure_guidance",
        lambda **kwargs: f"guidance: {kwargs.get('detail')}",
    )

    with pytest.raises(typer.BadParameter, match="guidance: agent not approved"):
        _gateway_local_connect(
            gateway_url="http://127.0.0.1:8765",
            agent_name="test-agent",
            registry_ref=None,
            workdir="/tmp",
            space_id=None,
        )


def test_gateway_local_connect_generic_error(monkeypatch):
    """Non-HTTP exceptions from /local/connect produce a BadParameter."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_connect

    def fake_post(url, *, json=None, timeout=None):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)
    monkeypatch.setattr("ax_cli.commands.messages._local_process_fingerprint", lambda **kwargs: {"fp": "test"})
    monkeypatch.setattr(
        "ax_cli.commands.messages._local_route_failure_guidance",
        lambda **kwargs: f"guidance: {kwargs.get('detail')}",
    )

    with pytest.raises(typer.BadParameter, match="guidance: connection refused"):
        _gateway_local_connect(
            gateway_url="http://127.0.0.1:8765",
            agent_name="test-agent",
            registry_ref=None,
            workdir="/tmp",
            space_id=None,
        )


def test_gateway_local_connect_uses_registry_ref(monkeypatch):
    """When agent_name is absent, registry_ref should still work."""
    from ax_cli.commands.messages import _gateway_local_connect

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"session_token": "tok", "status": "approved"}

    def fake_post(url, *, json=None, timeout=None):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)
    monkeypatch.setattr("ax_cli.commands.messages._local_process_fingerprint", lambda **kwargs: {"fp": "test"})

    result = _gateway_local_connect(
        gateway_url="http://127.0.0.1:8765",
        agent_name=None,
        registry_ref="some/ref",
        workdir="/tmp",
        space_id="space-1",
    )
    assert result["session_token"] == "tok"
    assert captured["body"]["registry_ref"] == "some/ref"
    assert captured["body"]["space_id"] == "space-1"
    assert "agent_name" not in captured["body"]


def test_gateway_local_call_success(monkeypatch):
    """_gateway_local_call returns the 'result' field from the proxy response."""
    from ax_cli.commands.messages import _gateway_local_call

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"session_token": "gw-tok", "status": "approved"},
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"messages": [{"id": "m1"}]}}

    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", lambda url, **kw: FakeResponse())

    data = _gateway_local_call(
        gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "test"},
        method="list_messages",
        args={"limit": 10},
        space_id="space-1",
    )
    assert data == {"messages": [{"id": "m1"}]}


def test_gateway_local_call_pending_approval(monkeypatch):
    """_gateway_local_call raises BadParameter when session status is pending."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_call

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"status": "pending", "approval_id": "a-1"},
    )
    monkeypatch.setattr(
        "ax_cli.commands.messages._approval_required_guidance",
        lambda **kwargs: "approval needed",
    )

    with pytest.raises(typer.BadParameter, match="approval needed"):
        _gateway_local_call(
            gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "test"},
            method="list_messages",
        )


def test_gateway_local_call_rejected_status(monkeypatch):
    """_gateway_local_call raises BadParameter when session status is rejected."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_call

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"status": "rejected"},
    )

    with pytest.raises(typer.BadParameter, match="Gateway local session is rejected"):
        _gateway_local_call(
            gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "test"},
            method="list_messages",
        )


def test_gateway_local_call_http_error(monkeypatch):
    """_gateway_local_call wraps HTTPStatusError in BadParameter."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_call

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"session_token": "tok", "status": "approved"},
    )

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "internal error"
    mock_response.json.side_effect = Exception("not json")

    def fake_post(url, **kw):
        raise httpx.HTTPStatusError("500", request=MagicMock(), response=mock_response)

    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)
    monkeypatch.setattr(
        "ax_cli.commands.messages._local_route_failure_guidance",
        lambda **kwargs: f"route_fail: {kwargs.get('detail')}",
    )

    with pytest.raises(typer.BadParameter, match="route_fail: internal error"):
        _gateway_local_call(
            gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "test"},
            method="do_thing",
        )


def test_gateway_local_call_generic_error(monkeypatch):
    """_gateway_local_call wraps generic exceptions in BadParameter."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_call

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"session_token": "tok", "status": "approved"},
    )

    def fake_post(url, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)
    monkeypatch.setattr(
        "ax_cli.commands.messages._local_route_failure_guidance",
        lambda **kwargs: f"route_fail: {kwargs.get('detail')}",
    )

    with pytest.raises(typer.BadParameter, match="route_fail: boom"):
        _gateway_local_call(
            gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "test"},
            method="do_thing",
        )


def test_gateway_local_send_http_error(monkeypatch):
    """_gateway_local_send wraps HTTPStatusError from /local/send."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_send

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"session_token": "tok", "status": "approved"},
    )

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "bad request"
    mock_response.json.return_value = {"error": "invalid content"}

    def fake_post(url, **kw):
        if url.endswith("/local/send"):
            raise httpx.HTTPStatusError("400", request=MagicMock(), response=mock_response)
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)

    with pytest.raises(typer.BadParameter, match="Gateway local send failed: invalid content"):
        _gateway_local_send(
            gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "test"},
            content="hello",
            space_id=None,
            parent_id=None,
        )


def test_gateway_local_send_generic_error(monkeypatch):
    """_gateway_local_send wraps generic exceptions from /local/send."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_send

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"session_token": "tok", "status": "approved"},
    )

    def fake_post(url, **kw):
        if url.endswith("/local/send"):
            raise ConnectionError("refused")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("ax_cli.commands.messages.httpx.post", fake_post)

    with pytest.raises(typer.BadParameter, match="Gateway local send failed: refused"):
        _gateway_local_send(
            gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "test"},
            content="hello",
            space_id=None,
            parent_id=None,
        )


def test_gateway_local_send_rejected_status(monkeypatch):
    """_gateway_local_send raises on non-pending rejected session."""
    import pytest

    from ax_cli.commands.messages import _gateway_local_send

    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_connect",
        lambda **kwargs: {"status": "rejected"},
    )

    with pytest.raises(typer.BadParameter, match="Gateway local session is rejected"):
        _gateway_local_send(
            gateway_cfg={"url": "http://127.0.0.1:8765", "agent_name": "test"},
            content="hello",
            space_id=None,
            parent_id=None,
        )


# ---- Formatting / display helpers ----


def test_print_wait_status_only_prints_on_change():
    from ax_cli.commands.messages import _print_wait_status

    # Returns the current remaining value
    result = _print_wait_status(30, None, "reply")
    assert result == 30
    result2 = _print_wait_status(30, 30, "reply")
    assert result2 == 30
    result3 = _print_wait_status(29, 30, "reply")
    assert result3 == 29


def test_processing_status_text_all_statuses():
    """Exercise all status branches in _processing_status_text."""
    cases = [
        ({"status": "accepted", "agent_name": "orion"}, "acknowledged"),
        ({"status": "claimed", "agent_name": "orion"}, "picked up"),
        ({"status": "forwarded", "agent_name": "orion"}, "picked up"),
        ({"status": "queued", "agent_name": "orion"}, "queued"),
        ({"status": "queued locally", "agent_name": "orion"}, "queued"),
        ({"status": "working", "agent_name": "orion"}, "is working"),
        ({"status": "processing", "agent_name": "orion"}, "is working"),
        ({"status": "thinking", "agent_name": "orion"}, "is thinking"),
        ({"status": "tool_use", "agent_name": "orion"}, "is using tools"),
        ({"status": "tool_call", "agent_name": "orion"}, "is using tools"),
        ({"status": "tool_complete", "agent_name": "orion"}, "finished a tool step"),
        ({"status": "streaming", "agent_name": "orion"}, "is streaming a reply"),
        ({"status": "completed", "agent_name": "orion"}, "finished processing"),
        ({"status": "declined", "agent_name": "orion"}, "chose not to respond"),
        ({"status": "skipped", "agent_name": "orion"}, "chose not to respond"),
        ({"status": "not_responding", "agent_name": "orion"}, "chose not to respond"),
        ({"status": "error", "agent_name": "orion"}, "hit an error"),
        ({"status": "unknown_custom", "agent_name": "orion"}, "status=unknown_custom"),
    ]
    for event, expected_fragment in cases:
        text = _processing_status_text(event)
        assert expected_fragment in text, f"Expected '{expected_fragment}' in: {text}"
        assert "tooling:" in text


def test_processing_status_text_tool_name_fallback():
    """When activity is absent but tool_name is present, it should append tool_name."""
    text = _processing_status_text({"status": "tool_use", "agent_name": "orion", "tool_name": "shell"})
    assert "shell" in text


def test_processing_status_from_event_ignores_non_dict():
    assert _processing_status_from_event("msg-1", "agent_processing", "not a dict") is None


def test_processing_status_from_event_ignores_empty_status():
    assert _processing_status_from_event("msg-1", "agent_processing", {"message_id": "msg-1", "status": ""}) is None


def test_processing_status_from_event_extra_fields():
    """All optional fields are included when present."""
    event = _processing_status_from_event(
        "msg-1",
        "agent_processing",
        {
            "message_id": "msg-1",
            "status": "working",
            "agent_id": "a-1",
            "agent_name": "orion",
            "progress": 50,
            "detail": "Step 2",
            "reason": "scheduled",
            "error_message": None,
            "retry_after_seconds": 10,
            "parent_message_id": "p-1",
        },
    )
    assert event["progress"] == 50
    assert event["detail"] == "Step 2"
    assert event["reason"] == "scheduled"
    assert event["retry_after_seconds"] == 10
    assert event["parent_message_id"] == "p-1"
    assert "error_message" not in event  # None values are excluded


def test_message_items_returns_list_for_various_inputs():
    from ax_cli.commands.messages import _message_items

    assert _message_items([{"id": "1"}]) == [{"id": "1"}]
    assert _message_items({"messages": [{"id": "2"}]}) == [{"id": "2"}]
    assert _message_items({"other": "value"}) == []
    assert _message_items("not a list or dict") == []
    assert _message_items(None) == []


def test_target_mention_prepends_at_sign():
    from ax_cli.commands.messages import _target_mention

    assert _target_mention("orion") == "@orion"
    assert _target_mention("@orion") == "@orion"


def test_starts_with_mention_case_insensitive():
    from ax_cli.commands.messages import _starts_with_mention

    assert _starts_with_mention("@Orion hello", "@orion") is True
    assert _starts_with_mention("  @ORION hello", "@orion") is True
    assert _starts_with_mention("hello @orion", "@orion") is False


def test_sender_label_variations():
    from ax_cli.commands.messages import _sender_label

    assert _sender_label({"display_name": "codex", "sender_type": "agent"}) == "@codex"
    assert _sender_label({"display_name": "@codex", "sender_type": "agent"}) == "@codex"
    assert _sender_label({"display_name": "jacob", "sender_type": "user"}) == "jacob"
    assert _sender_label({"display_name": "", "sender_type": "agent"}) == "agent"
    assert _sender_label({"display_name": "", "sender_type": ""}) is None
    assert _sender_label({}) is None


def test_extract_delivery_context_locations():
    from ax_cli.commands.messages import _extract_delivery_context

    # Top-level
    assert _extract_delivery_context({"delivery_context": {"path": "live"}}) == {"path": "live"}
    # In metadata
    assert _extract_delivery_context({"metadata": {"delivery_context": {"path": "queued"}}}) == {"path": "queued"}
    # In message.metadata
    assert _extract_delivery_context({"message": {"metadata": {"delivery_context": {"path": "warm"}}}}) == {
        "path": "warm"
    }
    # Not present
    assert _extract_delivery_context({"other": "data"}) is None
    # Non-dict input
    assert _extract_delivery_context(None) is None
    assert _extract_delivery_context("string") is None


def test_delivery_context_chip_variations():
    from ax_cli.commands.messages import _delivery_context_chip

    # Empty context
    assert _delivery_context_chip({}) is None
    assert _delivery_context_chip(None) is None
    # Just delivery_path
    assert _delivery_context_chip({"delivery_path": "live_session"}) == "delivered live"
    assert _delivery_context_chip({"delivery_path": "inbox_queue"}) == "queued"
    # Disagreement signal
    chip = _delivery_context_chip({"delivery_path": "live_session", "expected_response_at_send": "queued"})
    assert "predicted" in chip
    assert "Queued" in chip
    # Expected only, no delivery path
    chip2 = _delivery_context_chip({"expected_response_at_send": "warming"})
    assert "Warming" in chip2
    # Warning
    chip3 = _delivery_context_chip({"delivery_path": "live_session", "warning": "slow agent"})
    assert "warning: slow agent" in chip3


def test_delivery_matches_expectation():
    from ax_cli.commands.messages import _delivery_matches_expectation

    assert _delivery_matches_expectation("live_session", "immediate") is True
    assert _delivery_matches_expectation("warm_wake", "warming") is True
    assert _delivery_matches_expectation("warm_wake", "dispatch_delayed") is True
    assert _delivery_matches_expectation("inbox_queue", "queued") is True
    assert _delivery_matches_expectation("live_session", "queued") is False
    assert _delivery_matches_expectation("live_session", "unknown") is True
    assert _delivery_matches_expectation("blocked_unroutable", "unlikely") is True


def test_gateway_reply_note_formatting():
    from ax_cli.commands.messages import _gateway_reply_note

    # Full gateway metadata
    msg = {
        "metadata": {
            "control_plane": "gateway",
            "gateway": {
                "gateway_id": "abcdef12-3456-7890",
                "agent_name": "echo",
                "runtime_type": "echo",
                "transport": "sse",
            },
        }
    }
    note = _gateway_reply_note(msg)
    assert "via Gateway abcdef12" in note
    assert "agent=@echo" in note
    assert "runtime=echo" in note
    assert "transport=sse" in note

    # Not gateway metadata
    assert _gateway_reply_note({"metadata": {"control_plane": "direct"}}) is None
    assert _gateway_reply_note({"metadata": {"control_plane": "gateway"}}) is None
    assert _gateway_reply_note({}) is None


def test_matching_reply_skips_ax_relay_routing():
    from ax_cli.commands.messages import _matching_reply

    seen = set()
    replies = [
        {
            "id": "relay-1",
            "parent_id": "msg-1",
            "metadata": {"routing": {"mode": "ax_relay", "target_agent_name": "orion"}},
        },
        {
            "id": "real-reply",
            "parent_id": "msg-1",
            "content": "here is the answer",
        },
    ]
    reply, routing_announced = _matching_reply("msg-1", replies, seen)
    assert reply["id"] == "real-reply"
    assert routing_announced is True


def test_matching_reply_returns_none_for_empty():
    from ax_cli.commands.messages import _matching_reply

    reply, routing_announced = _matching_reply("msg-1", [], set())
    assert reply is None
    assert routing_announced is False


def test_matching_reply_skips_already_seen():
    from ax_cli.commands.messages import _matching_reply

    seen = {"reply-1"}
    replies = [{"id": "reply-1", "parent_id": "msg-1", "content": "old"}]
    reply, _ = _matching_reply("msg-1", replies, seen)
    assert reply is None


def test_matching_reply_matches_conversation_id():
    from ax_cli.commands.messages import _matching_reply

    seen = set()
    replies = [{"id": "reply-1", "conversation_id": "msg-1", "content": "via conversation"}]
    reply, _ = _matching_reply("msg-1", replies, seen)
    assert reply is not None
    assert reply["id"] == "reply-1"


def test_attachment_ref_includes_context_key():
    from ax_cli.commands.messages import _attachment_ref

    ref = _attachment_ref(
        attachment_id="att-1",
        content_type="text/plain",
        filename="file.txt",
        size=100,
        url="/uploads/file.txt",
        context_key="upload:file.txt:att-1",
    )
    assert ref["context_key"] == "upload:file.txt:att-1"
    assert ref["kind"] == "file"
    assert ref["size_bytes"] == 100


def test_attachment_ref_omits_context_key_when_none():
    from ax_cli.commands.messages import _attachment_ref

    ref = _attachment_ref(
        attachment_id="att-1",
        content_type="text/plain",
        filename="file.txt",
        size=100,
        url="/uploads/file.txt",
        context_key=None,
    )
    assert "context_key" not in ref


def test_context_upload_value_includes_content_for_small_text(tmp_path):
    from ax_cli.commands.messages import _context_upload_value

    local = tmp_path / "small.txt"
    local.write_text("hello world")

    value = _context_upload_value(
        attachment_id="att-1",
        context_key="upload:small.txt:att-1",
        filename="small.txt",
        content_type="text/plain",
        size=11,
        url="/uploads/small.txt",
        local_path=local,
    )
    assert value["content"] == "hello world"
    assert value["type"] == "file_upload"


def test_context_upload_value_skips_content_for_large_files(tmp_path):
    from ax_cli.commands.messages import _context_upload_value

    local = tmp_path / "big.txt"
    local.write_text("x" * 100_000)

    value = _context_upload_value(
        attachment_id="att-1",
        context_key="upload:big.txt:att-1",
        filename="big.txt",
        content_type="text/plain",
        size=100_000,
        url="/uploads/big.txt",
        local_path=local,
    )
    assert "content" not in value


def test_context_upload_value_skips_content_for_binary_types(tmp_path):
    from ax_cli.commands.messages import _context_upload_value

    local = tmp_path / "image.png"
    local.write_bytes(b"\x89PNG\r\n")

    value = _context_upload_value(
        attachment_id="att-1",
        context_key="upload:image.png:att-1",
        filename="image.png",
        content_type="image/png",
        size=6,
        url="/uploads/image.png",
        local_path=local,
    )
    assert "content" not in value


# ---- pending-reply helper unit tests live in test_pending_reply_warning.py ----


def test_resolve_message_id_full_uuid(monkeypatch):
    from ax_cli.commands.messages import _resolve_message_id

    class FakeClient:
        pass

    # Full UUID passes through without resolution
    full_id = "12345678-90ab-cdef-1234-567890abcdef"
    result = _resolve_message_id(FakeClient(), full_id)
    assert result == full_id


def test_resolve_message_id_ambiguous_prefix(monkeypatch):
    import pytest

    from ax_cli.commands.messages import _resolve_message_id

    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None):
            return {"messages": [{"id": "abc12345-1"}, {"id": "abc12345-2"}]}

    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda c, explicit=None: "s1")

    with pytest.raises(typer.Exit):
        _resolve_message_id(FakeClient(), "abc12345")


# ---- Gateway send with --act-as rejection ----


def test_send_gateway_rejects_act_as(monkeypatch):
    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_gateway_config",
        lambda: {"url": "http://127.0.0.1:8765", "agent_name": "test"},
    )
    result = runner.invoke(app, ["send", "hello", "--act-as", "other", "--no-wait"])
    assert result.exit_code == 1
    assert "--act-as is not supported" in result.output


def test_send_gateway_rejects_custom_channel(monkeypatch):
    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_gateway_config",
        lambda: {"url": "http://127.0.0.1:8765", "agent_name": "test"},
    )
    result = runner.invoke(app, ["messages", "send", "hello", "--channel", "alerts", "--no-wait"])
    assert result.exit_code != 0
    assert "custom --channel is not supported" in result.output


# ---- Messages list via gateway ----


def test_messages_list_via_gateway(monkeypatch):
    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_gateway_config",
        lambda: {"url": "http://127.0.0.1:8765", "agent_name": "test"},
    )
    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_call",
        lambda **kwargs: {"messages": [{"id": "m1", "content": "hello", "created_at": "2026-01-01"}]},
    )
    result = runner.invoke(app, ["messages", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["id"] == "m1"


# ---- Messages get via gateway ----


def test_messages_get_via_gateway(monkeypatch):
    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_gateway_config",
        lambda: {"url": "http://127.0.0.1:8765", "agent_name": "test"},
    )
    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_call",
        lambda **kwargs: {"id": "m1", "content": "hello"},
    )
    result = runner.invoke(app, ["messages", "get", "m1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == "m1"


# ---- Messages search via gateway ----


def test_messages_search_via_gateway(monkeypatch):
    monkeypatch.setattr(
        "ax_cli.commands.messages.resolve_gateway_config",
        lambda: {"url": "http://127.0.0.1:8765", "agent_name": "test"},
    )
    monkeypatch.setattr(
        "ax_cli.commands.messages._gateway_local_call",
        lambda **kwargs: {"results": [{"id": "m1", "content": "match"}]},
    )
    result = runner.invoke(app, ["messages", "search", "match", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1


# ---- Messages read command ----


def test_messages_read_requires_id_or_all():
    result = runner.invoke(app, ["messages", "read"])
    assert result.exit_code == 1
    assert "provide a message ID or --all" in result.output


def test_messages_read_rejects_both_id_and_all():
    result = runner.invoke(app, ["messages", "read", "some-id", "--all"])
    assert result.exit_code == 1
    assert "use either a message ID or --all" in result.output


# ---- Messages delete non-json output ----


def test_messages_delete_non_json(monkeypatch):
    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None):
            return {"messages": [{"id": "12345678-abcd"}]}

        def delete_message(self, mid):
            pass

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda c, explicit=None: "s1")

    result = runner.invoke(app, ["messages", "delete", "12345678"])
    assert result.exit_code == 0
    assert "Deleted" in result.output


# ---- Messages edit non-json output ----


def test_messages_edit_non_json(monkeypatch):
    class FakeClient:
        def list_messages(self, limit=20, channel="main", *, space_id=None):
            return {"messages": [{"id": "12345678-abcd"}]}

        def edit_message(self, mid, content):
            return {"id": mid, "content": content}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda c, explicit=None: "s1")

    result = runner.invoke(app, ["messages", "edit", "12345678", "new text"])
    assert result.exit_code == 0


# ---- Send timeout without reply ----


def test_send_timeout_no_reply_human_output(monkeypatch):
    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            return {"message": {"id": "msg-1", "display_name": "codex", "sender_type": "agent"}}

    def fake_wait(client, message_id, timeout=60, wait_label="reply", **kwargs):
        return None  # timeout

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda c, explicit=None: "s1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: "codex")
    monkeypatch.setattr("ax_cli.commands.messages._wait_for_reply", fake_wait)

    result = runner.invoke(app, ["send", "test"])
    assert result.exit_code == 0
    output = _strip_ansi(result.output)
    assert "No reply within" in output


def test_send_timeout_json_output_with_processing_statuses(monkeypatch):
    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            return {"message": {"id": "msg-1"}}

        def list_messages(self, **kwargs):
            return {"messages": []}

    def fake_wait(client, message_id, timeout=60, wait_label="reply", **kwargs):
        return None  # timeout

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda c, explicit=None: "s1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)
    monkeypatch.setattr("ax_cli.commands.messages.resolve_gateway_config", lambda: None)
    monkeypatch.setattr("ax_cli.commands.messages._wait_for_reply", fake_wait)

    result = runner.invoke(app, ["send", "test", "--json"])
    assert result.exit_code == 0, result.output
    # The output has both human "Sent." line and JSON payload
    # Extract JSON portion from the output
    lines = result.output.strip().split("\n")
    json_start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("{"))
    json_text = "\n".join(lines[json_start:])
    data = json.loads(json_text)
    assert data["reply"] is None
    assert data["timeout"] is True


def test_send_timeout_with_processing_statuses_human_output(monkeypatch):
    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            return {"message": {"id": "msg-1"}}

        def list_messages(self, **kwargs):
            return {"messages": []}

        def connect_sse(self, *, space_id, timeout=None):
            raise httpx.ConnectError("no SSE in test")

    class FakeWatcher:
        events = [{"status": "working", "agent_name": "orion"}]

        def start(self):
            pass

        def wait_ready(self, timeout=1.5):
            return True

        def set_message_id(self, mid):
            pass

        def close(self):
            pass

        def drain(self):
            return []

    def fake_wait(client, message_id, timeout=60, wait_label="reply", **kwargs):
        # Simulate timeout with processing events present
        return None

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda c, explicit=None: "s1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)
    monkeypatch.setattr("ax_cli.commands.messages.resolve_gateway_config", lambda: None)
    monkeypatch.setattr("ax_cli.commands.messages._wait_for_reply", fake_wait)

    _orig_watcher_cls = _ProcessingStatusWatcher

    class PatchedWatcher(FakeWatcher):
        """Stand-in that mimics the constructor signature of _ProcessingStatusWatcher."""

        def __init__(self, client, *, space_id, timeout):
            super().__init__()

    monkeypatch.setattr(
        "ax_cli.commands.messages._ProcessingStatusWatcher",
        PatchedWatcher,
    )

    result = runner.invoke(app, ["send", "test", "--to", "orion"])
    assert result.exit_code == 0, result.output
    output = _strip_ansi(result.output)
    assert "processing status: working" in output


# ---- Send with reply JSON output ----


def test_send_reply_json_output_includes_processing_statuses(monkeypatch):
    class FakeClient:
        _base_headers = {}

        def send_message(self, space_id, content, *, channel="main", parent_id=None, attachments=None):
            return {"message": {"id": "msg-1"}}

        def list_messages(self, **kwargs):
            return {"messages": []}

    def fake_wait(client, message_id, timeout=60, wait_label="reply", **kwargs):
        return {"id": "reply-1", "content": "ack"}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_space_id", lambda c, explicit=None: "s1")
    monkeypatch.setattr("ax_cli.commands.messages.resolve_agent_name", lambda client=None: None)
    monkeypatch.setattr("ax_cli.commands.messages.resolve_gateway_config", lambda: None)
    monkeypatch.setattr("ax_cli.commands.messages._wait_for_reply", fake_wait)

    result = runner.invoke(app, ["send", "test", "--json"])
    assert result.exit_code == 0, result.output
    # Output has human "Sent." line followed by JSON payload
    lines = result.output.strip().split("\n")
    json_start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("{"))
    json_text = "\n".join(lines[json_start:])
    data = json.loads(json_text)
    assert data["reply"]["id"] == "reply-1"
    assert "processing_statuses" in data


# ---- Messages search non-json output ----


def test_messages_search_table_output(monkeypatch):
    class FakeClient:
        def search_messages(self, query, *, limit=20):
            return {"results": [{"id": "m1", "content": "found it", "sender_type": "user", "created_at": "2026-01-01"}]}

    monkeypatch.setattr("ax_cli.commands.messages.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.messages.resolve_gateway_config", lambda: None)

    result = runner.invoke(app, ["messages", "search", "found"])
    assert result.exit_code == 0
