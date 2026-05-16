"""Tests for ax_cli/commands/events.py — SSE streaming."""

import json
from unittest.mock import MagicMock

import httpx
from typer.testing import CliRunner

from ax_cli.main import app

runner = CliRunner()


def _json_lines(output):
    results = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("{"):
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


def _make_sse_client(lines, *, status_code=200):
    client = MagicMock()
    client.base_url = "http://localhost:8001"
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error body"
    resp.iter_lines.return_value = iter(lines)
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    client.connect_sse.return_value = resp
    return client


def test_stream_json_output(monkeypatch):
    client = _make_sse_client(["event: message", 'data: {"content": "hello"}'])
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream", "--max-events", "1", "--json"])
    assert result.exit_code == 0, result.output
    events = _json_lines(result.output)
    assert len(events) >= 1
    assert events[0]["event"] == "message"


def test_stream_text_output(monkeypatch):
    client = _make_sse_client(["event: message", 'data: {"content": "hi"}'])
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream", "--max-events", "1"])
    assert result.exit_code == 0


def test_stream_filter_routing(monkeypatch):
    client = _make_sse_client(
        [
            "event: message",
            'data: {"skip": true}',
            "event: routing_status",
            'data: {"status": "ok"}',
        ]
    )
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream", "--filter", "routing", "--max-events", "1", "--json"])
    assert result.exit_code == 0
    events = _json_lines(result.output)
    assert len(events) >= 1
    assert events[0]["event"] == "routing_status"


def test_stream_filter_messages(monkeypatch):
    client = _make_sse_client(
        [
            "event: routing_status",
            'data: {"skip": true}',
            "event: mention",
            'data: {"who": "bot"}',
        ]
    )
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream", "--filter", "messages", "--max-events", "1", "--json"])
    events = _json_lines(result.output)
    assert len(events) >= 1
    assert events[0]["event"] == "mention"


def test_stream_filter_custom(monkeypatch):
    client = _make_sse_client(
        [
            "event: message",
            'data: {"skip": true}',
            "event: custom_type",
            'data: {"hit": true}',
        ]
    )
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream", "--filter", "custom_type", "--max-events", "1", "--json"])
    events = _json_lines(result.output)
    assert len(events) >= 1


def test_stream_error_status_code(monkeypatch):
    client = _make_sse_client([], status_code=403)
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream"])
    assert result.exit_code == 1


def test_stream_invalid_json_data(monkeypatch):
    client = _make_sse_client(["event: message", "data: not-valid-json"])
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream", "--max-events", "1", "--json"])
    assert result.exit_code == 0


def test_stream_max_events_limit(monkeypatch):
    client = _make_sse_client(
        [
            "event: message",
            'data: {"n": 1}',
            "event: message",
            'data: {"n": 2}',
            "event: message",
            'data: {"n": 3}',
        ]
    )
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream", "--max-events", "2", "--json"])
    assert result.exit_code == 0


def test_stream_keyboard_interrupt(monkeypatch):
    client = MagicMock()
    client.base_url = "http://localhost:8001"
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.side_effect = KeyboardInterrupt()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    client.connect_sse.return_value = resp
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream"])
    assert result.exit_code == 0
    assert "Stopped" in result.output


def test_stream_http_error(monkeypatch):
    client = MagicMock()
    client.base_url = "http://localhost:8001"
    client.connect_sse.side_effect = httpx.HTTPError("connection failed")
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream"])
    assert result.exit_code == 1


def test_stream_filter_echoed(monkeypatch):
    client = _make_sse_client(["event: message", 'data: {"m": 1}'])
    monkeypatch.setattr("ax_cli.commands.events.get_client", lambda: client)
    monkeypatch.setattr("ax_cli.commands.events.resolve_space_id", lambda c: "space-123456789012")
    result = runner.invoke(app, ["events", "stream", "--filter", "routing", "--max-events", "0"])
    assert "Filtering" in result.output
