"""Tests for ax handoff composed workflow helpers."""

import json

from typer.testing import CliRunner

from ax_cli.commands.handoff import (
    _completion_promise_satisfied,
    _is_handoff_progress,
    _matches_handoff_progress,
    _matches_handoff_reply,
)
from ax_cli.main import app


def _json_tail(output: str) -> dict:
    start = output.rfind("\n{")
    if start == -1:
        start = output.find("{")
    else:
        start += 1
    return json.loads(output[start:])


def test_handoff_matches_thread_reply_from_target_agent():
    message = {
        "id": "reply-1",
        "content": "Reviewed and done.",
        "parent_id": "sent-1",
        "display_name": "demo-agent",
        "created_at": "2026-04-13T04:31:00+00:00",
    }

    assert _matches_handoff_reply(
        message,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc123",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_handoff_matches_fast_top_level_reply_with_token_and_mention():
    message = {
        "id": "reply-1",
        "content": "@ChatGPT handoff:abc123 reviewed the spec.",
        "conversation_id": "reply-1",
        "display_name": "demo-agent",
        "created_at": "2026-04-13T04:31:00+00:00",
    }

    assert _matches_handoff_reply(
        message,
        agent_name="@demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc123",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=True,
    )


def test_handoff_does_not_match_other_agent():
    message = {
        "id": "reply-1",
        "content": "@ChatGPT handoff:abc123 done.",
        "display_name": "cipher",
    }

    assert not _matches_handoff_reply(
        message,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc123",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_handoff_progress_does_not_count_as_reply():
    message = {
        "id": "reply-1",
        "content": "Working... (12 tools)\n  > checking repo\n  > running tests",
        "parent_id": "sent-1",
        "display_name": "mcp_sentinel",
        "metadata": {"streaming_reply": {"enabled": True, "final": False}},
    }

    assert _is_handoff_progress(message)
    assert _matches_handoff_progress(
        message,
        agent_name="mcp_sentinel",
        sent_message_id="sent-1",
        token="handoff:abc123",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )
    assert not _matches_handoff_reply(
        message,
        agent_name="mcp_sentinel",
        sent_message_id="sent-1",
        token="handoff:abc123",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_handoff_progress_can_change_without_matching_completion():
    message = {
        "id": "reply-2",
        "content": "Working... (41 tools)\n  > ax context load\n  > ax messages list",
        "conversation_id": "sent-1",
        "display_name": "mcp_sentinel",
    }

    assert _is_handoff_progress(message)
    assert _matches_handoff_progress(
        message,
        agent_name="mcp_sentinel",
        sent_message_id="sent-1",
        token="handoff:abc123",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )
    assert not _matches_handoff_reply(
        message,
        agent_name="mcp_sentinel",
        sent_message_id="sent-1",
        token="handoff:abc123",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=True,
    )


def test_handoff_streaming_reply_with_token_counts_as_reply():
    message = {
        "id": "reply-3",
        "content": "Received this. `handoff:abc123` Current state: smoke check acknowledged.",
        "parent_id": "sent-1",
        "display_name": "mcp_sentinel",
        "metadata": {"streaming_reply": {"enabled": True, "final": False}},
    }

    assert _is_handoff_progress(message)
    assert _matches_handoff_reply(
        message,
        agent_name="mcp_sentinel",
        sent_message_id="sent-1",
        token="handoff:abc123",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=True,
    )


def test_completion_promise_requires_exact_tag_or_line():
    assert _completion_promise_satisfied("<promise>DONE</promise>", "DONE")
    assert _completion_promise_satisfied("DONE\n", "DONE")
    assert not _completion_promise_satisfied("not DONE yet", "DONE")
    assert not _completion_promise_satisfied("<promise>DONE-ish</promise>", "DONE")


def test_handoff_loop_repeats_until_completion_promise(monkeypatch):
    runner = CliRunner()
    calls = {"messages": []}

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, space_id, title, description=None, priority=None, assignee_id=None):
            calls["task"] = {
                "space_id": space_id,
                "title": title,
                "description": description,
                "priority": priority,
                "assignee_id": assignee_id,
            }
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            message_id = f"msg-{len(calls['messages']) + 1}"
            calls["messages"].append({"space_id": space_id, "content": content, "parent_id": parent_id})
            return {"message": {"id": message_id}}

    replies = [
        {"id": "reply-1", "content": "handoff:abc still working", "display_name": "demo-agent"},
        {"id": "reply-2", "content": "<promise>DONE</promise>", "display_name": "demo-agent"},
    ]

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr("ax_cli.commands.handoff.uuid.uuid4", lambda: type("UUID", (), {"hex": "abc123456789"})())
    monkeypatch.setattr("ax_cli.commands.handoff._wait_for_handoff_reply", lambda *args, **kwargs: replies.pop(0))

    result = runner.invoke(
        app,
        [
            "handoff",
            "demo-agent",
            "Fix the regression with tests",
            "--loop",
            "--max-rounds",
            "3",
            "--completion-promise",
            "DONE",
            "--no-adaptive-wait",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert len(calls["messages"]) == 2
    assert "Agentic loop mode is enabled" in calls["messages"][0]["content"]
    assert calls["messages"][1]["content"].startswith("@demo-agent Continue agentic loop")
    assert calls["messages"][1]["parent_id"] == "reply-1"
    assert data["reply"]["id"] == "reply-2"
    assert data["loop"]["completed"] is True
    assert data["loop"]["stop_reason"] == "completion_promise"
    assert len(data["loop"]["rounds"]) == 2


def test_handoff_loop_stops_at_max_rounds_without_promise(monkeypatch):
    runner = CliRunner()
    calls = {"messages": []}

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, space_id, title, description=None, priority=None, assignee_id=None):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            message_id = f"msg-{len(calls['messages']) + 1}"
            calls["messages"].append({"space_id": space_id, "content": content, "parent_id": parent_id})
            return {"message": {"id": message_id}}

    replies = [
        {"id": "reply-1", "content": "round one", "display_name": "demo-agent"},
        {"id": "reply-2", "content": "round two", "display_name": "demo-agent"},
    ]

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr("ax_cli.commands.handoff._wait_for_handoff_reply", lambda *args, **kwargs: replies.pop(0))

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "Iterate twice", "--loop", "--max-rounds", "2", "--no-adaptive-wait", "--json"],
    )

    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert len(calls["messages"]) == 2
    assert data["reply"]["id"] == "reply-2"
    assert data["loop"]["completed"] is False
    assert data["loop"]["stop_reason"] == "max_rounds"
    assert len(data["loop"]["rounds"]) == 2


def test_handoff_loop_timeout_after_progress_uses_loop_timeout_status(monkeypatch):
    runner = CliRunner()
    calls = {"messages": []}

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, space_id, title, description=None, priority=None, assignee_id=None):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            message_id = f"msg-{len(calls['messages']) + 1}"
            calls["messages"].append({"space_id": space_id, "content": content, "parent_id": parent_id})
            return {"message": {"id": message_id}}

    replies = [{"id": "reply-1", "content": "round one", "display_name": "demo-agent"}, None]

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr("ax_cli.commands.handoff._wait_for_handoff_reply", lambda *args, **kwargs: replies.pop(0))

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "Iterate twice", "--loop", "--max-rounds", "2", "--no-adaptive-wait", "--json"],
    )

    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "loop_timeout"
    assert data["reply"] is None
    assert data["loop"]["stop_reason"] == "timeout"
    assert len(data["loop"]["rounds"]) == 2


def test_handoff_default_adaptive_wait_queues_when_probe_times_out(monkeypatch):
    runner = CliRunner()
    calls = {"messages": []}

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "cli_sentinel"}]}

        def create_task(self, space_id, title, description=None, priority=None, assignee_id=None):
            calls["task"] = {
                "space_id": space_id,
                "title": title,
                "description": description,
                "priority": priority,
                "assignee_id": assignee_id,
            }
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            message_id = f"msg-{len(calls['messages']) + 1}"
            calls["messages"].append({"space_id": space_id, "content": content, "parent_id": parent_id})
            return {"message": {"id": message_id}}

    wait_calls = []

    def fake_wait(*args, **kwargs):
        wait_calls.append(kwargs)
        return None

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr("ax_cli.commands.handoff._wait_for_handoff_reply", fake_wait)

    result = runner.invoke(
        app,
        ["handoff", "cli_sentinel", "Review CLI docs", "--probe-timeout", "1", "--json"],
    )

    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "queued_not_listening"
    assert data["contact_probe"]["contact_mode"] == "unknown_or_not_listening"
    assert data["reply"] is None
    assert len(calls["messages"]) == 2
    assert calls["messages"][0]["content"].startswith("@cli_sentinel Contact-mode ping")
    assert "queued for the target's next check-in" in calls["messages"][1]["content"]
    assert len(wait_calls) == 1


def test_handoff_default_adaptive_wait_continues_when_probe_replies(monkeypatch):
    runner = CliRunner()
    calls = {"messages": []}

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, space_id, title, description=None, priority=None, assignee_id=None):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            message_id = f"msg-{len(calls['messages']) + 1}"
            calls["messages"].append({"space_id": space_id, "content": content, "parent_id": parent_id})
            return {"message": {"id": message_id}}

    replies = [
        {"id": "probe-reply", "content": "ping:ok", "display_name": "demo-agent"},
        {"id": "handoff-reply", "content": "reviewed", "display_name": "demo-agent"},
    ]

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr("ax_cli.commands.handoff._wait_for_handoff_reply", lambda *args, **kwargs: replies.pop(0))

    result = runner.invoke(app, ["handoff", "demo-agent", "Review CLI docs", "--json"])

    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "replied"
    assert data["contact_probe"]["contact_mode"] == "event_listener"
    assert data["reply"]["id"] == "handoff-reply"
    assert len(calls["messages"]) == 2


def test_handoff_no_adaptive_wait_skips_contact_probe(monkeypatch):
    runner = CliRunner()
    calls = {"messages": []}

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, space_id, title, description=None, priority=None, assignee_id=None):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            message_id = f"msg-{len(calls['messages']) + 1}"
            calls["messages"].append({"space_id": space_id, "content": content, "parent_id": parent_id})
            return {"message": {"id": message_id}}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr(
        "ax_cli.commands.handoff._wait_for_handoff_reply",
        lambda *args, **kwargs: {"id": "reply-1", "content": "reviewed", "display_name": "demo-agent"},
    )

    result = runner.invoke(app, ["handoff", "demo-agent", "Review CLI docs", "--no-adaptive-wait", "--json"])

    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "replied"
    assert data["contact_probe"] is None
    assert len(calls["messages"]) == 1


def test_progress_label_empty_content():
    from ax_cli.commands.handoff import _progress_label

    assert _progress_label({}) == "Working..."
    assert _progress_label({"content": ""}) == "Working..."
    assert _progress_label({"content": None}) == "Working..."


def test_progress_label_multiline_truncation():
    from ax_cli.commands.handoff import _progress_label

    msg = {"content": "Line one\nLine two\nLine three\nLine four\nLine five"}
    label = _progress_label(msg)
    assert "Line one" in label
    assert "Line two" in label
    assert "Line three" in label
    assert "Line four" not in label


def test_message_items_list_payload():
    from ax_cli.commands.handoff import _message_items

    assert _message_items([{"id": "1"}, "not-dict", {"id": "2"}]) == [{"id": "1"}, {"id": "2"}]


def test_message_items_non_dict_non_list():
    from ax_cli.commands.handoff import _message_items

    assert _message_items("string") == []
    assert _message_items(42) == []
    assert _message_items(None) == []


def test_message_items_dict_with_various_keys():
    from ax_cli.commands.handoff import _message_items

    assert _message_items({"messages": [{"id": "a"}]}) == [{"id": "a"}]
    assert _message_items({"replies": [{"id": "b"}]}) == [{"id": "b"}]
    assert _message_items({"items": [{"id": "c"}]}) == [{"id": "c"}]
    assert _message_items({"results": [{"id": "d"}]}) == [{"id": "d"}]
    # No matching key returns empty
    assert _message_items({"other": [{"id": "e"}]}) == []


def test_sender_name_author_dict():
    from ax_cli.commands.handoff import _sender_name

    msg = {"author": {"name": "Alice", "username": "alice"}}
    assert _sender_name(msg) == "Alice"


def test_sender_name_author_string():
    from ax_cli.commands.handoff import _sender_name

    msg = {"author": "bob-agent"}
    assert _sender_name(msg) == "bob-agent"


def test_sender_name_no_candidates():
    from ax_cli.commands.handoff import _sender_name

    assert _sender_name({}) == ""


def test_message_timestamp_z_suffix():
    from ax_cli.commands.handoff import _message_timestamp

    ts = _message_timestamp({"created_at": "2026-04-13T04:31:00Z"})
    assert ts is not None


def test_message_timestamp_invalid_string():
    from ax_cli.commands.handoff import _message_timestamp

    assert _message_timestamp({"created_at": "not-a-date"}) is None


def test_message_timestamp_non_string():
    from ax_cli.commands.handoff import _message_timestamp

    assert _message_timestamp({"created_at": 12345}) is None
    assert _message_timestamp({}) is None


def test_completion_promise_empty_string():
    from ax_cli.commands.handoff import _completion_promise_satisfied

    assert not _completion_promise_satisfied("anything", "")
    assert not _completion_promise_satisfied("anything", "   ")
    assert not _completion_promise_satisfied("anything", None)


def test_matches_handoff_reply_no_id():
    assert not _matches_handoff_reply(
        {"content": "done", "display_name": "demo-agent"},
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_matches_handoff_reply_same_id_as_sent():
    assert not _matches_handoff_reply(
        {"id": "sent-1", "content": "done", "display_name": "demo-agent"},
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_matches_handoff_reply_before_started_at():
    msg = {
        "id": "reply-1",
        "content": "done",
        "parent_id": "sent-1",
        "display_name": "demo-agent",
        "created_at": "2026-04-13T04:30:00Z",
    }
    assert not _matches_handoff_reply(
        msg,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=999999999999,
        require_completion=False,
    )


def test_matches_handoff_reply_no_thread_or_token_or_mention():
    msg = {
        "id": "reply-1",
        "content": "done",
        "display_name": "demo-agent",
        "created_at": "2026-04-13T04:31:00+00:00",
    }
    assert not _matches_handoff_reply(
        msg,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_matches_handoff_reply_require_completion_without_completion_words():
    msg = {
        "id": "reply-1",
        "content": "still thinking about it",
        "parent_id": "sent-1",
        "display_name": "demo-agent",
        "created_at": "2026-04-13T04:31:00+00:00",
    }
    assert not _matches_handoff_reply(
        msg,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=True,
    )


def test_matches_handoff_progress_before_started_at():
    msg = {
        "id": "reply-1",
        "content": "Working... checking repo",
        "parent_id": "sent-1",
        "display_name": "demo-agent",
        "created_at": "2020-01-01T00:00:00Z",
    }
    assert not _matches_handoff_progress(
        msg,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=999999999999,
        require_completion=False,
    )


def test_matches_handoff_progress_wrong_agent():
    msg = {
        "id": "reply-1",
        "content": "Working... checking repo",
        "parent_id": "sent-1",
        "display_name": "other-agent",
    }
    assert not _matches_handoff_progress(
        msg,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_recent_match_finds_reply(monkeypatch):
    from ax_cli.commands.handoff import _recent_match

    class FakeClient:
        def list_replies(self, msg_id):
            return {
                "replies": [
                    {
                        "id": "reply-1",
                        "content": "done",
                        "parent_id": "sent-1",
                        "display_name": "demo-agent",
                        "created_at": "2026-04-13T04:31:00+00:00",
                    },
                ]
            }

        def list_messages(self, limit=30, space_id=None):
            return {"messages": []}

    result = _recent_match(
        FakeClient(),
        space_id="space-1",
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )
    assert result is not None
    assert result["id"] == "reply-1"


def test_recent_match_fires_on_progress(monkeypatch):
    from ax_cli.commands.handoff import _recent_match

    progress_seen = []

    class FakeClient:
        def list_replies(self, msg_id):
            return {"replies": []}

        def list_messages(self, limit=30, space_id=None):
            return {
                "messages": [
                    {
                        "id": "reply-1",
                        "content": "Working... building",
                        "parent_id": "sent-1",
                        "display_name": "demo-agent",
                    },
                ]
            }

    result = _recent_match(
        FakeClient(),
        space_id="space-1",
        on_progress=lambda m: progress_seen.append(m),
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )
    assert result is None
    assert len(progress_seen) == 1


def test_recent_match_deduplicates():
    from ax_cli.commands.handoff import _recent_match

    class FakeClient:
        def list_replies(self, msg_id):
            return {
                "replies": [
                    {
                        "id": "reply-1",
                        "content": "done",
                        "parent_id": "sent-1",
                        "display_name": "demo-agent",
                        "created_at": "2026-04-13T04:31:00+00:00",
                    },
                ]
            }

        def list_messages(self, limit=30, space_id=None):
            return {
                "messages": [
                    {
                        "id": "reply-1",
                        "content": "done",
                        "parent_id": "sent-1",
                        "display_name": "demo-agent",
                        "created_at": "2026-04-13T04:31:00+00:00",
                    },
                ]
            }

    result = _recent_match(
        FakeClient(),
        space_id="space-1",
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )
    assert result is not None


def test_recent_match_handles_api_exceptions():
    from ax_cli.commands.handoff import _recent_match

    class FailingClient:
        def list_replies(self, msg_id):
            raise RuntimeError("replies fail")

        def list_messages(self, limit=30, space_id=None):
            raise RuntimeError("messages fail")

    result = _recent_match(
        FailingClient(),
        space_id="space-1",
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )
    assert result is None


def test_resolve_agent_id_list_format():
    from ax_cli.commands.handoff import _resolve_agent_id

    class FakeClient:
        def list_agents(self):
            return [{"id": "agent-1", "name": "demo-agent"}, {"id": "agent-2", "name": "other"}]

    assert _resolve_agent_id(FakeClient(), "demo-agent") == "agent-1"
    assert _resolve_agent_id(FakeClient(), "missing") is None


def test_resolve_agent_id_exception():
    from ax_cli.commands.handoff import _resolve_agent_id

    class FailingClient:
        def list_agents(self):
            raise RuntimeError("fail")

    assert _resolve_agent_id(FailingClient(), "demo-agent") is None


def test_resolve_agent_id_non_list_agents():
    from ax_cli.commands.handoff import _resolve_agent_id

    class FakeClient:
        def list_agents(self):
            return {"agents": "not-a-list"}

    assert _resolve_agent_id(FakeClient(), "demo-agent") is None


def test_resolve_agent_id_skips_non_dict_agents():
    from ax_cli.commands.handoff import _resolve_agent_id

    class FakeClient:
        def list_agents(self):
            return {"agents": ["not-a-dict", {"id": "agent-1", "name": "demo-agent"}]}

    assert _resolve_agent_id(FakeClient(), "demo-agent") == "agent-1"


def test_resolve_agent_id_no_id():
    from ax_cli.commands.handoff import _resolve_agent_id

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"name": "demo-agent"}]}

    assert _resolve_agent_id(FakeClient(), "demo-agent") == ""


def test_handoff_invalid_intent(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: None)
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")

    result = runner.invoke(app, ["handoff", "demo-agent", "test", "--intent", "bad_intent"])
    assert result.exit_code != 0
    assert "unknown intent" in result.output


def test_handoff_loop_with_no_watch(monkeypatch):
    runner = CliRunner()
    result = runner.invoke(app, ["handoff", "demo-agent", "test", "--loop", "--no-watch"])
    assert result.exit_code != 0
    assert "requires --watch" in result.output


def test_handoff_loop_with_follow_up(monkeypatch):
    runner = CliRunner()
    result = runner.invoke(app, ["handoff", "demo-agent", "test", "--loop", "--follow-up"])
    assert result.exit_code != 0
    assert "separate modes" in result.output


def test_handoff_loop_max_rounds_zero(monkeypatch):
    runner = CliRunner()
    result = runner.invoke(app, ["handoff", "demo-agent", "test", "--loop", "--max-rounds", "0"])
    assert result.exit_code != 0
    assert "max-rounds" in result.output.lower()


def test_handoff_no_watch_no_sent_id(monkeypatch):
    """When watch is disabled, result status is 'sent'."""
    runner = CliRunner()
    calls = {"messages": []}

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, space_id, title, description=None, priority=None, assignee_id=None):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            calls["messages"].append(content)
            return {"message": {"id": "msg-1"}}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "test instructions", "--no-watch", "--no-adaptive-wait", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "sent"
    assert data["reply"] is None


def test_handoff_task_creation_fails_continues(monkeypatch):
    """Task creation failure should not block message handoff."""
    runner = CliRunner()

    class FakeClient:
        def list_agents(self):
            return {"agents": []}

        def create_task(self, *args, **kwargs):
            raise RuntimeError("task creation exploded")

        def send_message(self, space_id, content, parent_id=None):
            return {"message": {"id": "msg-1"}}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "do stuff", "--no-watch", "--no-adaptive-wait", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["task_error"] is not None
    assert "exploded" in data["task_error"]


def test_handoff_nudge_on_no_reply(monkeypatch):
    """--nudge sends a follow-up nudge when first wait returns None."""
    runner = CliRunner()
    calls = {"messages": []}
    wait_count = [0]

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, *args, **kwargs):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            msg_id = f"msg-{len(calls['messages']) + 1}"
            calls["messages"].append({"content": content, "parent_id": parent_id})
            return {"message": {"id": msg_id}}

    def fake_wait(*args, **kwargs):
        wait_count[0] += 1
        if wait_count[0] == 1:
            return None
        return {"id": "reply-1", "content": "done", "display_name": "demo-agent"}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr("ax_cli.commands.handoff._wait_for_handoff_reply", fake_wait)

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "do stuff", "--nudge", "--no-adaptive-wait", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "replied"
    # handoff message + nudge message
    assert len(calls["messages"]) == 2
    assert "nudge" in calls["messages"][1]["content"].lower()


def test_handoff_nudge_exception_is_swallowed(monkeypatch):
    """Nudge send failure does not crash the CLI."""
    runner = CliRunner()

    class FakeClient:
        def list_agents(self):
            return {"agents": []}

        def create_task(self, *args, **kwargs):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            if "nudge" in content.lower():
                raise RuntimeError("nudge send failed")
            return {"message": {"id": "msg-1"}}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr("ax_cli.commands.handoff._wait_for_handoff_reply", lambda *a, **kw: None)

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "do stuff", "--nudge", "--no-adaptive-wait", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "timeout"


def test_handoff_loop_send_failed_status(monkeypatch):
    """Loop where a continue message returns no ID triggers loop_send_failed."""
    runner = CliRunner()
    calls = {"messages": []}
    send_count = [0]

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, *args, **kwargs):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            send_count[0] += 1
            calls["messages"].append(content)
            if send_count[0] >= 2:
                # Return message with no id to trigger send_failed
                return {"message": {}}
            return {"message": {"id": f"msg-{send_count[0]}"}}

    replies = [
        {"id": "reply-1", "content": "round one", "display_name": "demo-agent"},
    ]

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr(
        "ax_cli.commands.handoff._wait_for_handoff_reply", lambda *a, **kw: replies.pop(0) if replies else None
    )

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "iterate", "--loop", "--max-rounds", "3", "--no-adaptive-wait", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "loop_send_failed"
    assert data["loop"]["stop_reason"] == "send_failed"


def test_handoff_reply_non_json_output(monkeypatch):
    """When reply is received without --json, content is printed."""
    runner = CliRunner()

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, *args, **kwargs):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            return {"message": {"id": "msg-1"}}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr(
        "ax_cli.commands.handoff._wait_for_handoff_reply",
        lambda *a, **kw: {"id": "reply-1", "content": "Review complete.", "display_name": "demo-agent"},
    )

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "review docs", "--no-adaptive-wait"],
    )
    assert result.exit_code == 0, result.output
    assert "Review complete." in result.output


def test_handoff_adaptive_probe_error_handled(monkeypatch):
    """HTTPStatusError during probe is handled via handle_error."""
    runner = CliRunner()
    import httpx as _httpx

    class FakeClient:
        def list_agents(self):
            return {"agents": []}

        def create_task(self, *args, **kwargs):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            if "Contact-mode ping" in content:
                raise _httpx.HTTPStatusError(
                    "probe fail",
                    request=_httpx.Request("POST", "http://test/send"),
                    response=_httpx.Response(403, json={"detail": "forbidden"}),
                )
            return {"message": {"id": "msg-1"}}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "test", "--json"],
    )
    # HTTPStatusError triggers handle_error -> typer.Exit(1)
    assert result.exit_code != 0


def test_handoff_unresolvable_agent_creates_task_without_assignee(monkeypatch):
    """Agent not in list creates task without assignee_id."""
    runner = CliRunner()
    calls = {}

    class FakeClient:
        def list_agents(self):
            return {"agents": []}

        def create_task(self, space_id, title, description=None, priority=None, assignee_id=None):
            calls["assignee_id"] = assignee_id
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            return {"message": {"id": "msg-1"}}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")

    result = runner.invoke(
        app,
        ["handoff", "nobody", "test", "--no-watch", "--no-adaptive-wait", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert calls["assignee_id"] is None
    assert "Could not resolve" in result.output


def test_recent_match_returns_none_on_no_reply():
    from ax_cli.commands.handoff import _recent_match

    class FakeClient:
        def list_replies(self, msg_id):
            return {"replies": []}

        def list_messages(self, limit=30, space_id=None):
            return {
                "messages": [
                    {"id": "other-1", "content": "irrelevant", "display_name": "other-agent"},
                ]
            }

    result = _recent_match(
        FakeClient(),
        space_id="space-1",
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )
    assert result is None


def test_loop_continue_content_with_promise():
    from ax_cli.commands.handoff import _loop_continue_content

    content = _loop_continue_content(
        agent_name="demo-agent",
        instructions="Fix the bug",
        handoff_id="handoff:abc",
        round_number=2,
        max_rounds=5,
        completion_promise="ALL_TESTS_PASS",
    )
    assert "Continue agentic loop" in content
    assert "demo-agent" in content
    assert "ALL_TESTS_PASS" in content
    assert "<promise>" in content
    assert "round 2/5" in content


def test_loop_continue_content_without_promise():
    from ax_cli.commands.handoff import _loop_continue_content

    content = _loop_continue_content(
        agent_name="demo-agent",
        instructions="Fix the bug",
        handoff_id="handoff:abc",
        round_number=1,
        max_rounds=3,
        completion_promise=None,
    )
    assert "No completion promise" in content
    assert "max round limit" in content


def test_matches_handoff_progress_same_id_as_sent():
    """Progress message with same ID as sent is rejected."""
    msg = {
        "id": "sent-1",
        "content": "Working... building",
        "parent_id": "sent-1",
        "display_name": "demo-agent",
    }
    assert not _matches_handoff_progress(
        msg,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_matches_handoff_progress_no_match_no_thread():
    """Progress message without thread or token match is not progress."""
    msg = {
        "id": "reply-1",
        "content": "Working... building",
        "display_name": "demo-agent",
    }
    assert not _matches_handoff_progress(
        msg,
        agent_name="demo-agent",
        sent_message_id="sent-1",
        token="handoff:abc",
        current_agent_name="ChatGPT",
        started_at=0,
        require_completion=False,
    )


def test_handoff_timeout_status(monkeypatch):
    """When wait returns None without nudge, status is 'timeout'."""
    runner = CliRunner()

    class FakeClient:
        def list_agents(self):
            return {"agents": [{"id": "agent-1", "name": "demo-agent"}]}

        def create_task(self, *args, **kwargs):
            return {"task": {"id": "task-1"}}

        def send_message(self, space_id, content, parent_id=None):
            return {"message": {"id": "msg-1"}}

    monkeypatch.setattr("ax_cli.commands.handoff.get_client", lambda: FakeClient())
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr("ax_cli.commands.handoff.resolve_agent_name", lambda client=None: "ChatGPT")
    monkeypatch.setattr("ax_cli.commands.handoff._wait_for_handoff_reply", lambda *a, **kw: None)

    result = runner.invoke(
        app,
        ["handoff", "demo-agent", "test", "--no-adaptive-wait", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = _json_tail(result.output)
    assert data["status"] == "timeout"
    assert data["reply"] is None


def test_handoff_is_registered_and_old_tone_verbs_are_removed():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "handoff" in result.output
    assert "ship" not in result.output
    assert "boss" not in result.output

    handoff_help = runner.invoke(app, ["handoff", "--help"])
    assert handoff_help.exit_code == 0
    assert "follow-up" in handoff_help.output
    assert "loop" in handoff_help.output

    old_command = runner.invoke(app, ["ship", "--help"])
    assert old_command.exit_code != 0
    assert "No such command" in old_command.output
