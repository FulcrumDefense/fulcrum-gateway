"""Tests for the svg_viz post_to_chat tool.

post_to_chat lazy-imports `ax_cli.config.get_client` + `resolve_space_id`
from inside the function body, so we test the seam by injecting a fake
`ax_cli.config` module into ``sys.modules`` before the call. This avoids
pulling in the real `ax_cli.config` (which depends on typer + httpx and
isn't necessary for these unit tests).

The full live path gets exercised on the VM separately — see the
corresponding work log.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

from ax_cli.runtimes.mcp_servers.svg_viz.post_to_chat import (
    SVG_LABEL_PREFIX,
    _handle_post_to_chat,
    _make_label,
    post_svg_to_chat,
)
from ax_cli.runtimes.mcp_servers.svg_viz.tools import build_tools

# ── pure unit tests (no client, no env) ────────────────────────────────────


def test_build_tools_includes_post_to_chat():
    tools = build_tools()
    names = [t.name for t in tools]
    assert names == ["chart", "status_card", "post_to_chat"]


def test_make_label_includes_prefix_and_slug():
    label = _make_label("CENTCOM Status Report")
    assert label.startswith(f"{SVG_LABEL_PREFIX}:")
    assert label.endswith(":centcom-status-report")
    parts = label.split(":")
    assert len(parts) == 4  # svg_viz : ts : random : slug
    assert parts[1].isdigit()  # timestamp
    assert len(parts[2]) == 8  # short random id


def test_make_label_handles_punctuation_safely():
    label = _make_label("/etc/passwd & other ../surprises!")
    slug = label.split(":")[3]
    assert "/" not in slug
    assert ".." not in slug
    assert "&" not in slug


def test_make_label_truncates_long_titles():
    label = _make_label("a" * 200)
    slug = label.split(":")[3]
    assert len(slug) <= 48


def test_make_label_handles_empty_title_safely():
    label = _make_label("!!!")
    slug = label.split(":")[3]
    assert slug  # not empty


# ── input validation ───────────────────────────────────────────────────────


def test_post_svg_rejects_non_svg_string():
    result = post_svg_to_chat(svg="not svg content", title="title")
    assert result["code"] == "INVALID_SVG"


def test_post_svg_rejects_empty_title():
    result = post_svg_to_chat(svg="<svg></svg>", title="")
    assert result["code"] == "MISSING_TITLE"


def test_post_svg_rejects_whitespace_only_title():
    result = post_svg_to_chat(svg="<svg></svg>", title="   ")
    assert result["code"] == "MISSING_TITLE"


def test_post_svg_rejects_non_string_svg():
    result = post_svg_to_chat(svg=12345, title="title")  # type: ignore[arg-type]
    assert result["code"] == "INVALID_SVG"


# ── happy-path with a fake ax_cli.config injected into sys.modules ─────────


def _install_fake_ax_cli_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    get_client_impl=None,
    resolve_space_id_impl=None,
):
    """Replace `ax_cli.config` in sys.modules with a fake exposing the two
    callables post_to_chat depends on. Reverts on test teardown via monkeypatch."""
    fake_module = types.ModuleType("ax_cli.config")
    fake_module.get_client = get_client_impl or (lambda: MagicMock())
    fake_module.resolve_space_id = resolve_space_id_impl or (
        lambda client, explicit=None: explicit or "space-default"
    )
    monkeypatch.setitem(sys.modules, "ax_cli.config", fake_module)


@pytest.fixture
def mocked_client():
    """A mock AxClient that records file-upload and message-send calls."""
    client = MagicMock()
    client.upload_file.return_value = {
        "id": "att-test-1",
        "url": "https://paxai.app/uploads/att-test-1.svg",
        "content_type": "image/svg+xml",
    }
    client.send_message.return_value = {"message": {"id": "msg-test-123"}}
    return client


def test_post_svg_happy_path_uploads_file_then_sends_message(monkeypatch, mocked_client):
    """Verify the two-step flow: upload SVG as a file, then post a message
    with it attached (the path that renders inline in paxai)."""
    _install_fake_ax_cli_config(
        monkeypatch,
        get_client_impl=lambda: mocked_client,
    )

    result = post_svg_to_chat(
        svg="<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        title="Status Report",
        summary="A test summary",
    )

    assert result["ok"] is True
    assert result["message_id"] == "msg-test-123"
    assert result["space_id"] == "space-default"
    assert result["title"] == "Status Report"
    assert result["attachment_id"] == "att-test-1"
    assert result["label"].startswith("svg_viz:")

    # upload_file was called with a temp .svg path + the resolved space
    upload_call = mocked_client.upload_file.call_args
    assert upload_call.args[0].endswith(".svg")
    assert upload_call.kwargs["space_id"] == "space-default"

    # send_message carried the SVG via the attachments param (the render trigger)
    send_call = mocked_client.send_message.call_args
    attachments = send_call.kwargs["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["attachment_id"] == "att-test-1"
    assert attachments[0]["content_type"] == "image/svg+xml"
    assert send_call.kwargs["message_type"] == "text"
    # message body defaults to the summary
    assert send_call.args[1] == "A test summary"


def test_post_svg_propagates_upload_failure(monkeypatch):
    """If upload_file raises, return SVG_UPLOAD_FAILED and DON'T call send_message."""
    failing_client = MagicMock()
    failing_client.upload_file.side_effect = Exception("network down")
    failing_client.send_message = MagicMock()

    _install_fake_ax_cli_config(monkeypatch, get_client_impl=lambda: failing_client)

    result = post_svg_to_chat(svg="<svg></svg>", title="t")
    assert result["code"] == "SVG_UPLOAD_FAILED"
    assert "network down" in result["error"]
    failing_client.send_message.assert_not_called()


def test_post_svg_propagates_send_message_failure(monkeypatch):
    """upload_file works but send_message fails -> MESSAGE_POST_FAILED.

    Returns the attachment_id + label so callers can trace/clean up.
    """
    half_failing = MagicMock()
    half_failing.upload_file.return_value = {
        "id": "att-x",
        "attachment_id": "att-x",
        "url": "https://paxai.app/uploads/att-x.svg",
        "content_type": "image/svg+xml",
    }
    half_failing.send_message.side_effect = Exception("server 500")

    _install_fake_ax_cli_config(monkeypatch, get_client_impl=lambda: half_failing)

    result = post_svg_to_chat(svg="<svg></svg>", title="t")
    assert result["code"] == "MESSAGE_POST_FAILED"
    assert "server 500" in result["error"]
    assert result["attachment_id"] == "att-x"
    assert "label" in result


def test_post_svg_returns_no_credentials_when_get_client_raises(monkeypatch):
    def _boom():
        raise RuntimeError("no token file")

    _install_fake_ax_cli_config(monkeypatch, get_client_impl=_boom)

    result = post_svg_to_chat(svg="<svg></svg>", title="t")
    assert result["code"] == "NO_CREDENTIALS"


def test_post_svg_returns_no_space_when_resolution_returns_none(monkeypatch):
    _install_fake_ax_cli_config(
        monkeypatch,
        get_client_impl=lambda: MagicMock(),
        resolve_space_id_impl=lambda client, explicit=None: None,
    )

    result = post_svg_to_chat(svg="<svg></svg>", title="t")
    assert result["code"] == "NO_SPACE"


# ── MCP tool handler wrapping ──────────────────────────────────────────────


def test_handler_wraps_result_as_mcp_text_content(monkeypatch, mocked_client):
    _install_fake_ax_cli_config(monkeypatch, get_client_impl=lambda: mocked_client)

    result = _handle_post_to_chat(
        {"svg": "<svg></svg>", "title": "x", "summary": "y"}
    )
    assert "content" in result
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["message_id"] == "msg-test-123"


def test_handler_passes_through_validation_errors():
    """Handler should NOT raise on bad input — returns structured error in MCP content."""
    result = _handle_post_to_chat({"svg": "not svg", "title": "x"})
    payload = json.loads(result["content"][0]["text"])
    assert payload["code"] == "INVALID_SVG"
