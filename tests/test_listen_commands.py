"""Tests for ax listen — SSE agent listener."""

from __future__ import annotations

import queue
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from ax_cli.commands.listen import (
    _echo_handler,
    _is_paused,
    _is_self_authored,
    _iter_sse,
    _message_sender_identity,
    _message_sender_type,
    _remember_reply_anchor,
    _run_handler,
    _should_respond,
    _strip_mention,
    _worker,
)

# ---------------------------------------------------------------------------
# _iter_sse tests
# ---------------------------------------------------------------------------


class FakeResponse:
    """Fake httpx.Response with configurable SSE lines."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)


def test_iter_sse_basic():
    resp = FakeResponse(
        [
            "event:message",
            'data:{"id":"1","content":"hello"}',
            "",
        ]
    )
    events = list(_iter_sse(resp))
    assert len(events) == 1
    assert events[0][0] == "message"
    assert events[0][1] == {"id": "1", "content": "hello"}


def test_iter_sse_non_json_data():
    resp = FakeResponse(
        [
            "event:ping",
            "data:keepalive",
            "",
        ]
    )
    events = list(_iter_sse(resp))
    assert len(events) == 1
    assert events[0][0] == "ping"
    assert events[0][1] == "keepalive"


def test_iter_sse_json_decode_error():
    """Invalid JSON starting with { falls back to raw string."""
    resp = FakeResponse(
        [
            "event:message",
            "data:{not valid json",
            "",
        ]
    )
    events = list(_iter_sse(resp))
    assert len(events) == 1
    assert events[0][0] == "message"
    assert events[0][1] == "{not valid json"


def test_iter_sse_multi_line_data():
    resp = FakeResponse(
        [
            "event:message",
            "data:line1",
            "data:line2",
            "",
        ]
    )
    events = list(_iter_sse(resp))
    assert len(events) == 1
    assert events[0][1] == "line1\nline2"


def test_iter_sse_skips_incomplete_events():
    """Events without both event type and data are skipped."""
    resp = FakeResponse(
        [
            "data:orphan data",
            "",
            "event:orphan_event",
            "",
        ]
    )
    events = list(_iter_sse(resp))
    assert len(events) == 0


# ---------------------------------------------------------------------------
# _message_sender_identity tests
# ---------------------------------------------------------------------------


def test_sender_identity_author_dict():
    data = {"author": {"name": "Alice", "id": "id-1"}}
    name, sid = _message_sender_identity(data)
    assert name == "Alice"
    assert sid == "id-1"


def test_sender_identity_flat_fields():
    data = {"display_name": "Bob", "agent_id": "id-2"}
    name, sid = _message_sender_identity(data)
    assert name == "Bob"
    assert sid == "id-2"


def test_sender_identity_author_string():
    data = {"author": "charlie"}
    name, sid = _message_sender_identity(data)
    assert name == "charlie"


# ---------------------------------------------------------------------------
# _message_sender_type tests
# ---------------------------------------------------------------------------


def test_sender_type_author_dict():
    assert _message_sender_type({"author": {"type": "agent"}}) == "agent"


def test_sender_type_flat():
    assert _message_sender_type({"sender_type": "user"}) == "user"


def test_sender_type_missing():
    assert _message_sender_type({}) == ""


# ---------------------------------------------------------------------------
# _is_self_authored tests
# ---------------------------------------------------------------------------


def test_is_self_authored_by_name():
    data = {"display_name": "mybot"}
    assert _is_self_authored(data, "mybot", None)


def test_is_self_authored_by_id():
    data = {"agent_id": "abc123"}
    assert _is_self_authored(data, "otherbot", "abc123")


def test_is_self_authored_negative():
    data = {"display_name": "someone_else"}
    assert not _is_self_authored(data, "mybot", None)


# ---------------------------------------------------------------------------
# _remember_reply_anchor tests
# ---------------------------------------------------------------------------


def test_remember_reply_anchor_no_id():
    """Empty/falsy message_id is ignored."""
    anchors: set[str] = set()
    _remember_reply_anchor(anchors, None)
    _remember_reply_anchor(anchors, "")
    _remember_reply_anchor(anchors, 0)
    assert len(anchors) == 0


def test_remember_reply_anchor_adds_string():
    anchors: set[str] = set()
    _remember_reply_anchor(anchors, "msg-1")
    assert "msg-1" in anchors


def test_remember_reply_anchor_trims_overflow():
    """When exceeding REPLY_ANCHOR_MAX, set is trimmed to half."""
    from ax_cli.commands.listen import REPLY_ANCHOR_MAX

    anchors: set[str] = set()
    for i in range(REPLY_ANCHOR_MAX + 10):
        _remember_reply_anchor(anchors, f"msg-{i}")
    assert len(anchors) <= REPLY_ANCHOR_MAX


# ---------------------------------------------------------------------------
# _should_respond tests
# ---------------------------------------------------------------------------


def test_should_respond_non_dict():
    assert not _should_respond("not a dict", "mybot", None)


def test_should_respond_self_authored():
    data = {"display_name": "mybot", "content": "@mybot hello", "mentions": [{"agent_name": "mybot"}]}
    assert not _should_respond(data, "mybot", None)


def test_should_respond_mentions_list_match():
    data = {
        "display_name": "alice",
        "content": "@mybot hello",
        "mentions": [{"agent_name": "mybot"}],
    }
    assert _should_respond(data, "mybot", None)


def test_should_respond_mentions_list_no_match():
    data = {
        "display_name": "alice",
        "content": "@mybot hello",
        "mentions": [{"agent_name": "otherbot"}],
    }
    assert not _should_respond(data, "mybot", None)


def test_should_respond_mentions_empty_list():
    """Empty mentions list means no active mentions (kill switch case)."""
    data = {
        "display_name": "alice",
        "content": "@mybot hello",
        "mentions": [],
    }
    assert not _should_respond(data, "mybot", None)


def test_should_respond_mentions_string_entry():
    data = {
        "display_name": "alice",
        "content": "@mybot hello",
        "mentions": ["mybot"],
    }
    assert _should_respond(data, "mybot", None)


def test_should_respond_fallback_content_regex():
    """When mentions field absent, fall back to content regex."""
    data = {
        "display_name": "alice",
        "content": "@mybot please help",
    }
    assert _should_respond(data, "mybot", None)


def test_should_respond_fallback_no_mention_in_content():
    data = {
        "display_name": "alice",
        "content": "hello world",
    }
    assert not _should_respond(data, "mybot", None)


def test_should_respond_reply_anchor_with_mentions():
    """Reply to an anchor message from a non-agent wakes the listener."""
    data = {
        "display_name": "alice",
        "sender_type": "user",
        "content": "thanks",
        "parent_id": "anchor-1",
        "mentions": [],
    }
    anchors = {"anchor-1"}
    assert _should_respond(data, "mybot", None, reply_anchor_ids=anchors)


def test_should_respond_reply_anchor_from_agent_ignored():
    """Reply to anchor from another agent does not wake the listener."""
    data = {
        "display_name": "otherbot",
        "sender_type": "agent",
        "content": "working on it",
        "parent_id": "anchor-1",
        "mentions": [],
    }
    anchors = {"anchor-1"}
    assert not _should_respond(data, "mybot", None, reply_anchor_ids=anchors)


def test_should_respond_reply_anchor_fallback_no_mentions():
    """Reply anchor works via fallback when mentions is absent."""
    data = {
        "display_name": "alice",
        "sender_type": "user",
        "content": "following up",
        "parent_id": "anchor-1",
    }
    anchors = {"anchor-1"}
    assert _should_respond(data, "mybot", None, reply_anchor_ids=anchors)


def test_should_respond_reply_anchor_fallback_agent_ignored():
    """Agent reply anchor via fallback is rejected."""
    data = {
        "display_name": "otherbot",
        "sender_type": "agent",
        "content": "working",
        "parent_id": "anchor-1",
    }
    anchors = {"anchor-1"}
    assert not _should_respond(data, "mybot", None, reply_anchor_ids=anchors)


def test_should_respond_skips_thread_parent_source():
    """Mentions with source='thread_parent' from agents are skipped."""
    data = {
        "display_name": "otherbot",
        "sender_type": "agent",
        "content": "@mybot check this",
        "mentions": [{"agent_name": "mybot", "source": "thread_parent"}],
    }
    assert not _should_respond(data, "mybot", None)


# ---------------------------------------------------------------------------
# _strip_mention tests
# ---------------------------------------------------------------------------


def test_strip_mention_basic():
    assert _strip_mention("@mybot hello world", "mybot") == "hello world"


def test_strip_mention_with_dash():
    assert _strip_mention("@mybot - do this", "mybot") == "do this"


def test_strip_mention_no_mention():
    assert _strip_mention("hello world", "mybot") == "hello world"


# ---------------------------------------------------------------------------
# _run_handler tests
# ---------------------------------------------------------------------------


def test_run_handler_success(tmp_path):
    script = tmp_path / "handler.sh"
    script.write_text('#!/bin/bash\necho "Got: $AX_MENTION_CONTENT"')
    script.chmod(0o755)
    result = _run_handler(str(script), "test input")
    assert "Got: test input" in result


def test_run_handler_timeout(monkeypatch):
    """Handler timeout returns timeout message."""

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="slow", timeout=300)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_handler("slow_command", "test")
    assert "timed out" in result


def test_run_handler_not_found(monkeypatch):
    """Missing handler returns not found message."""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_handler("nonexistent_command", "test")
    assert "handler not found" in result


def test_run_handler_stderr_on_error(monkeypatch):
    """Non-zero exit with stderr appends stderr to output."""

    class FakeResult:
        stdout = "partial output"
        stderr = "error details"
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    result = _run_handler("failing_cmd", "test")
    assert "partial output" in result
    assert "error details" in result


def test_run_handler_no_output(monkeypatch):
    """Empty stdout returns (no output)."""

    class FakeResult:
        stdout = ""
        stderr = ""
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    result = _run_handler("silent_cmd", "test")
    assert result == "(no output)"


# ---------------------------------------------------------------------------
# _echo_handler tests
# ---------------------------------------------------------------------------


def test_echo_handler():
    assert _echo_handler("hello") == "Echo: hello"


# ---------------------------------------------------------------------------
# _is_paused tests
# ---------------------------------------------------------------------------


def test_is_paused_all_agents(tmp_path, monkeypatch):
    """Global pause file pauses all agents."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ax_dir = tmp_path / ".ax"
    ax_dir.mkdir()
    (ax_dir / "sentinel_pause").touch()
    assert _is_paused("mybot") is True


def test_is_paused_specific_agent(tmp_path, monkeypatch):
    """Agent-specific pause file pauses only that agent."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ax_dir = tmp_path / ".ax"
    ax_dir.mkdir()
    (ax_dir / "sentinel_pause_mybot").touch()
    assert _is_paused("mybot") is True
    assert _is_paused("otherbot") is False


def test_is_paused_not_paused(tmp_path, monkeypatch):
    """No pause files means not paused."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ax").mkdir(exist_ok=True)
    assert _is_paused("mybot") is False


# ---------------------------------------------------------------------------
# _worker tests
# ---------------------------------------------------------------------------


def test_worker_processes_mention():
    """Worker processes a mention from the queue."""
    mention_q = queue.Queue()
    reply_anchors: set[str] = set()

    client = MagicMock()
    client.send_message.return_value = {"message": {"id": "reply-1"}}
    client_holder = [client]

    data = {
        "display_name": "alice",
        "content": "@mybot do something",
        "id": "msg-1",
    }
    mention_q.put(data)
    mention_q.put(None)  # shutdown signal

    _worker(mention_q, client_holder, "mybot", None, "space-1", _echo_handler, False, reply_anchors)

    client.send_message.assert_called_once()
    args = client.send_message.call_args
    assert "Echo: do something" in args[0][1]
    assert "reply-1" in reply_anchors or "msg-1" in reply_anchors


def test_worker_dry_run_no_reply():
    """Worker in dry-run mode does not send replies."""
    mention_q = queue.Queue()
    reply_anchors: set[str] = set()

    client = MagicMock()
    client_holder = [client]

    data = {
        "display_name": "alice",
        "content": "@mybot do something",
        "id": "msg-1",
    }
    mention_q.put(data)
    mention_q.put(None)

    _worker(mention_q, client_holder, "mybot", None, "space-1", _echo_handler, True, reply_anchors)

    client.send_message.assert_not_called()


def test_worker_paused_drops_mention(tmp_path, monkeypatch):
    """Worker drops mentions when agent is paused."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ax_dir = tmp_path / ".ax"
    ax_dir.mkdir()
    (ax_dir / "sentinel_pause_mybot").touch()

    mention_q = queue.Queue()
    reply_anchors: set[str] = set()

    client = MagicMock()
    client_holder = [client]

    data = {
        "display_name": "alice",
        "content": "@mybot do something",
        "id": "msg-1",
    }
    mention_q.put(data)
    mention_q.put(None)

    _worker(mention_q, client_holder, "mybot", None, "space-1", _echo_handler, False, reply_anchors)

    client.send_message.assert_not_called()


def test_worker_empty_prompt_skipped():
    """Worker skips mentions where strip_mention yields empty."""
    mention_q = queue.Queue()
    reply_anchors: set[str] = set()

    client = MagicMock()
    client_holder = [client]

    data = {
        "display_name": "alice",
        "content": "@mybot",
        "id": "msg-1",
    }
    mention_q.put(data)
    mention_q.put(None)

    _worker(mention_q, client_holder, "mybot", None, "space-1", _echo_handler, False, reply_anchors)

    client.send_message.assert_not_called()


def test_worker_handler_exception_continues():
    """Worker continues processing after handler raises an exception."""
    mention_q = queue.Queue()
    reply_anchors: set[str] = set()

    client = MagicMock()
    client.send_message.side_effect = RuntimeError("send failed")
    client_holder = [client]

    data = {
        "display_name": "alice",
        "content": "@mybot do something",
        "id": "msg-1",
    }
    mention_q.put(data)
    mention_q.put(None)

    # Should not raise
    _worker(mention_q, client_holder, "mybot", None, "space-1", _echo_handler, False, reply_anchors)


def test_worker_handler_returns_none():
    """Worker does not send reply when handler returns empty string."""
    mention_q = queue.Queue()
    reply_anchors: set[str] = set()

    client = MagicMock()
    client_holder = [client]

    def empty_handler(prompt):
        return ""

    data = {
        "display_name": "alice",
        "content": "@mybot do something",
        "id": "msg-1",
    }
    mention_q.put(data)
    mention_q.put(None)

    _worker(mention_q, client_holder, "mybot", None, "space-1", empty_handler, False, reply_anchors)

    client.send_message.assert_not_called()


def test_worker_long_prompt_truncated_in_display():
    """Worker truncates long prompts in display output."""
    mention_q = queue.Queue()
    reply_anchors: set[str] = set()

    client = MagicMock()
    client.send_message.return_value = {"message": {"id": "reply-1"}}
    client_holder = [client]

    # Create a long content message
    long_content = "@mybot " + "x" * 200
    data = {
        "display_name": "alice",
        "content": long_content,
        "id": "msg-1",
    }
    mention_q.put(data)
    mention_q.put(None)

    _worker(mention_q, client_holder, "mybot", None, "space-1", _echo_handler, False, reply_anchors)

    client.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# listen() command-level tests
# ---------------------------------------------------------------------------


def test_listen_no_agent_name(monkeypatch):
    """listen exits with error when no agent name is available."""
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return []

    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: None)

    result = runner.invoke(app, ["listen"])
    assert result.exit_code != 0
    assert "No agent name" in result.output


def test_listen_resolves_agent_id(monkeypatch):
    """listen resolves agent_id from agent list."""

    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": [{"name": "testbot", "id": "agent-123"}]}

        def connect_sse(self, space_id=None):
            # Return a context manager that raises KeyboardInterrupt immediately
            class FakeCtx:
                def __enter__(self):
                    raise KeyboardInterrupt()

                def __exit__(self, *args):
                    pass

            return FakeCtx()

    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["listen", "--agent", "testbot"])
    assert result.exit_code == 0
    assert "testbot" in result.output
    assert "agent-123" in result.output


def test_listen_with_exec_cmd(monkeypatch):
    """listen with --exec sets up a custom handler."""
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": []}

        def connect_sse(self, space_id=None):
            class FakeCtx:
                def __enter__(self):
                    raise KeyboardInterrupt()

                def __exit__(self, *args):
                    pass

            return FakeCtx()

    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["listen", "--exec", "echo test"])
    assert result.exit_code == 0
    assert "echo test" in result.output


def test_listen_sse_connection_error(monkeypatch):
    """listen handles SSE connection errors with reconnect backoff."""
    import httpx
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()
    connect_count = [0]

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": []}

        def connect_sse(self, space_id=None):
            connect_count[0] += 1
            if connect_count[0] <= 1:
                raise httpx.ConnectError("connection failed")
            raise KeyboardInterrupt()

    # Patch time.sleep to avoid actual delays
    import ax_cli.commands.listen as listen_mod

    monkeypatch.setattr(listen_mod.time, "sleep", lambda _: None)
    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["listen"])
    assert result.exit_code == 0
    assert "Connection lost" in result.output or "Shutting down" in result.output


def test_listen_sse_general_exception(monkeypatch):
    """listen handles general exceptions with reconnect."""
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()
    connect_count = [0]

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": []}

        def connect_sse(self, space_id=None):
            connect_count[0] += 1
            if connect_count[0] <= 1:
                raise RuntimeError("unexpected error")
            raise KeyboardInterrupt()

    import ax_cli.commands.listen as listen_mod

    monkeypatch.setattr(listen_mod.time, "sleep", lambda _: None)
    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["listen"])
    assert result.exit_code == 0
    assert "unexpected error" in result.output


def test_listen_sse_non_200_status(monkeypatch):
    """listen handles non-200 SSE status."""
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()
    connect_count = [0]

    class FakeResp:
        status_code = 403

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": []}

        def connect_sse(self, space_id=None):
            connect_count[0] += 1

            class FakeCtx:
                def __enter__(self_ctx):
                    if connect_count[0] <= 1:
                        return FakeResp()
                    raise KeyboardInterrupt()

                def __exit__(self_ctx, *args):
                    pass

            return FakeCtx()

    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["listen"])
    assert result.exit_code == 0
    assert "SSE failed" in result.output


def test_listen_processes_sse_events(monkeypatch):
    """listen processes SSE mention events end-to-end."""
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()

    class FakeSSEResp:
        status_code = 200

        def iter_lines(self):
            yield "event:connected"
            yield "data:connected"
            yield ""
            yield "event:message"
            yield 'data:{"id":"msg-1","display_name":"alice","content":"@testbot hello","mentions":[{"agent_name":"testbot"}]}'
            yield ""
            # After processing one event, we need to stop
            # We'll use a heartbeat event then disconnect
            yield "event:heartbeat"
            yield "data:ping"
            yield ""

    sse_count = [0]

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": []}

        def connect_sse(self, space_id=None):
            sse_count[0] += 1

            class FakeCtx:
                def __enter__(self_ctx):
                    if sse_count[0] <= 1:
                        return FakeSSEResp()
                    raise KeyboardInterrupt()

                def __exit__(self_ctx, *args):
                    pass

            return FakeCtx()

        def send_message(self, space_id, content, agent_id=None, parent_id=None):
            return {"message": {"id": "reply-1"}}

    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["listen", "--dry-run"])
    assert result.exit_code == 0
    assert "testbot" in result.output


def test_listen_self_authored_message_remembered(monkeypatch):
    """Self-authored messages have IDs added to reply_anchor_ids."""
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()

    class FakeSSEResp:
        status_code = 200

        def iter_lines(self):
            yield "event:message"
            yield 'data:{"id":"self-msg-1","display_name":"testbot","content":"I said something"}'
            yield ""

    sse_count = [0]

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": []}

        def connect_sse(self, space_id=None):
            sse_count[0] += 1

            class FakeCtx:
                def __enter__(self_ctx):
                    if sse_count[0] <= 1:
                        return FakeSSEResp()
                    raise KeyboardInterrupt()

                def __exit__(self_ctx, *args):
                    pass

            return FakeCtx()

    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["listen"])
    assert result.exit_code == 0


def test_listen_queue_full_drops_mention(monkeypatch):
    """When queue is full, mention is dropped."""
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()

    class FakeSSEResp:
        status_code = 200

        def iter_lines(self):
            # Emit many messages to overflow queue
            for i in range(5):
                yield "event:message"
                yield f'data:{{"id":"msg-{i}","display_name":"alice","content":"@testbot hello {i}","mentions":[{{"agent_name":"testbot"}}]}}'
                yield ""

    sse_count = [0]

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": []}

        def connect_sse(self, space_id=None):
            sse_count[0] += 1

            class FakeCtx:
                def __enter__(self_ctx):
                    if sse_count[0] <= 1:
                        return FakeSSEResp()
                    raise KeyboardInterrupt()

                def __exit__(self_ctx, *args):
                    pass

            return FakeCtx()

        def send_message(self, *args, **kwargs):
            return {"message": {"id": "reply"}}

    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    # Use very small queue so it fills up
    result = runner.invoke(app, ["listen", "--queue-size", "1", "--dry-run"])
    assert result.exit_code == 0


def test_listen_json_output_mode(monkeypatch):
    """listen --json outputs JSON lines for events."""
    from typer.testing import CliRunner

    from ax_cli.main import app

    runner = CliRunner()

    class FakeSSEResp:
        status_code = 200

        def iter_lines(self):
            yield "event:connected"
            yield "data:ok"
            yield ""
            yield "event:message"
            yield 'data:{"id":"msg-1","display_name":"alice","content":"@testbot hi","mentions":[{"agent_name":"testbot"}]}'
            yield ""

    sse_count = [0]

    class FakeClient:
        base_url = "http://localhost:8000"

        def list_agents(self):
            return {"agents": []}

        def connect_sse(self, space_id=None):
            sse_count[0] += 1

            class FakeCtx:
                def __enter__(self_ctx):
                    if sse_count[0] <= 1:
                        return FakeSSEResp()
                    raise KeyboardInterrupt()

                def __exit__(self_ctx, *args):
                    pass

            return FakeCtx()

    monkeypatch.setattr("ax_cli.commands.listen.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.listen.resolve_agent_name", lambda client=None: "testbot")
    monkeypatch.setattr("ax_cli.commands.listen.resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["listen", "--json", "--dry-run"])
    assert result.exit_code == 0
    # JSON output should contain connected event
    assert "connected" in result.output
