"""Tests for ax_cli/commands/listen.py — SSE parsing, matcher, handler helpers."""

from pathlib import Path

from ax_cli.commands.listen import (
    REPLY_ANCHOR_MAX,
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
)

# ---- _iter_sse ----


class FakeResponse:
    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        yield from self._lines


def test_iter_sse_basic():
    resp = FakeResponse(["event: message", 'data: {"content": "hi"}', ""])
    events = list(_iter_sse(resp))
    assert len(events) == 1
    assert events[0] == ("message", {"content": "hi"})


def test_iter_sse_non_json():
    resp = FakeResponse(["event: ping", "data: alive", ""])
    events = list(_iter_sse(resp))
    assert events[0] == ("ping", "alive")


def test_iter_sse_multiline():
    resp = FakeResponse(["event: message", "data: a", "data: b", ""])
    events = list(_iter_sse(resp))
    assert events[0][1] == "a\nb"


# ---- _message_sender_identity ----


def test_sender_identity_from_author_dict():
    name, uid = _message_sender_identity({"author": {"name": "bot", "id": "id-1"}})
    assert name == "bot"
    assert uid == "id-1"


def test_sender_identity_from_flat_fields():
    name, uid = _message_sender_identity({"display_name": "alice", "agent_id": "a-1"})
    assert name == "alice"
    assert uid == "a-1"


def test_sender_identity_from_string_author():
    name, uid = _message_sender_identity({"author": "bob"})
    assert name == "bob"


# ---- _message_sender_type ----


def test_sender_type_from_author_dict():
    assert _message_sender_type({"author": {"type": "agent"}}) == "agent"


def test_sender_type_from_flat():
    assert _message_sender_type({"sender_type": "user"}) == "user"


# ---- _is_self_authored ----


def test_self_authored_by_name():
    assert _is_self_authored({"author": {"name": "mybot", "id": ""}}, "mybot", None)


def test_self_authored_by_id():
    assert _is_self_authored({"author": {"name": "x", "id": "id-1"}}, "other", "id-1")


def test_not_self_authored():
    assert not _is_self_authored({"author": {"name": "someone", "id": "id-2"}}, "mybot", "id-1")


# ---- _remember_reply_anchor ----


def test_remember_reply_anchor_adds():
    ids = set()
    _remember_reply_anchor(ids, "msg-1")
    assert "msg-1" in ids


def test_remember_reply_anchor_ignores_empty():
    ids = set()
    _remember_reply_anchor(ids, "")
    _remember_reply_anchor(ids, None)
    assert len(ids) == 0


def test_remember_reply_anchor_evicts():
    ids = set()
    for i in range(REPLY_ANCHOR_MAX + 10):
        _remember_reply_anchor(ids, f"msg-{i}")
    assert len(ids) <= REPLY_ANCHOR_MAX


# ---- _should_respond ----


def test_should_respond_self_message():
    assert not _should_respond(
        {"content": "@mybot hello", "author": {"name": "mybot", "id": ""}},
        "mybot",
        None,
    )


def test_should_respond_mention_in_list():
    assert _should_respond(
        {"content": "hello", "author": {"name": "bob", "id": ""}, "mentions": [{"agent_name": "mybot"}]},
        "mybot",
        None,
    )


def test_should_respond_mention_as_string():
    assert _should_respond(
        {"content": "hello", "author": {"name": "bob", "id": ""}, "mentions": ["mybot"]},
        "mybot",
        None,
    )


def test_should_respond_empty_mentions_means_no():
    assert not _should_respond(
        {"content": "@mybot hello", "author": {"name": "bob", "id": ""}, "mentions": []},
        "mybot",
        None,
    )


def test_should_respond_no_mentions_field_fallback_to_content():
    assert _should_respond(
        {"content": "@mybot hello", "author": {"name": "bob", "id": ""}},
        "mybot",
        None,
    )


def test_should_respond_non_dict():
    assert not _should_respond("not a dict", "mybot", None)


def test_should_respond_reply_anchor_with_no_mentions():
    anchors = {"parent-1"}
    assert _should_respond(
        {"content": "reply here", "author": {"name": "bob", "id": ""}, "parent_id": "parent-1", "sender_type": "user"},
        "mybot",
        None,
        reply_anchor_ids=anchors,
    )


def test_should_respond_reply_anchor_agent_rejected():
    anchors = {"parent-1"}
    assert not _should_respond(
        {"content": "reply", "author": {"name": "other", "type": "agent"}, "parent_id": "parent-1", "mentions": []},
        "mybot",
        None,
        reply_anchor_ids=anchors,
    )


def test_should_respond_skips_thread_parent_source():
    assert not _should_respond(
        {
            "content": "hi",
            "author": {"name": "otherbot", "id": "", "type": "agent"},
            "mentions": [{"agent_name": "mybot", "source": "thread_parent"}],
        },
        "mybot",
        None,
    )


# ---- _strip_mention ----


def test_strip_mention():
    assert _strip_mention("@bot hello world", "bot") == "hello world"


def test_strip_mention_with_dash():
    assert _strip_mention("@bot - hello", "bot") == "hello"


# ---- _run_handler ----


def test_run_handler_echo():
    output = _run_handler("echo", "hello")
    assert "hello" in output


def test_run_handler_not_found():
    output = _run_handler("/nonexistent/cmd", "test")
    assert "handler not found" in output


# ---- _echo_handler ----


def test_echo_handler():
    assert _echo_handler("test") == "Echo: test"


# ---- _is_paused ----


def test_is_paused_false(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ax").mkdir()
    assert not _is_paused("agent1")


def test_is_paused_global(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ax").mkdir()
    (tmp_path / ".ax" / "sentinel_pause").touch()
    assert _is_paused("agent1")


def test_is_paused_per_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ax").mkdir()
    (tmp_path / ".ax" / "sentinel_pause_agent1").touch()
    assert _is_paused("agent1")
