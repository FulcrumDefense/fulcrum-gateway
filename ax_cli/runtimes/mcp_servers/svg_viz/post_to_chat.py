"""svg_viz post_to_chat tool — uploads an SVG as a file and posts a chat
message with it attached, so paxai renders it inline as an image (not as
a code block / raw text).

The render path (verified live 2026-05-28):

1. ``client.upload_file(svg_temp_path, space_id=...)`` — uploads the SVG
   as a file with content-type ``image/svg+xml`` (client.py maps the
   ``.svg`` suffix automatically). The uploads endpoint carries the
   content-type that makes paxai treat it as a renderable image.
2. ``client.send_message(space_id, body, attachments=[...], message_type="text")``
   — posts a normal chat message with the uploaded file in the
   ``attachments`` list. That attachments param is what makes paxai
   render the SVG inline — the same path a user dragging an SVG into
   chat uses.

Two earlier approaches did NOT render and were dropped:
  - storing the SVG as a raw ``set_context`` value (rendered as text);
  - referencing the upload only inside ``metadata.ui.widget`` signal
    cards (the message posted but the image didn't display). Per Jacob
    Taunton: "svg should work, but it needs to be uploaded as svg and
    not ctx" — the fix is the file upload + attachments param, not
    signal-card metadata.

Identity: this tool uses ``ax_cli.config.get_client()`` which reads the
agent's credentials from the env Gateway passes to the bridge subprocess
(``AX_TOKEN_FILE`` / ``AX_BASE_URL`` / ``AX_AGENT_ID`` / ``AX_SPACE_ID``).
No new credential handling here.

Failure mode: returns a structured error dict instead of raising. The
calling LLM gets a readable error and can recover; the rest of the
agent loop continues. We deliberately do NOT fall back to "post the
SVG inline as text" — that's the failure case this tool exists to prevent.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

SVG_LABEL_PREFIX = "svg_viz"
"""Prefix on the auto-generated, human-readable label returned to the caller."""


def _make_label(title: str) -> str:
    """Build a stable-shaped label for a posted SVG (returned to the caller
    for traceability/logging).

    Pattern: ``svg_viz:<timestamp>:<random>:<title-slug>``. The random
    component keeps simultaneous posts from colliding; the title slug
    is a human-readable hint.
    """
    ts = int(time.time())
    short_id = uuid.uuid4().hex[:8]
    slug = "".join(c if c.isalnum() else "-" for c in title.lower())[:48]
    slug = slug.strip("-") or "svg"
    return f"{SVG_LABEL_PREFIX}:{ts}:{short_id}:{slug}"


def post_svg_to_chat(
    svg: str,
    title: str,
    summary: str = "",
    *,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Upload `svg` as a file and post a chat message with it attached.

    paxai renders the attached SVG inline as an image. Returns a dict with
    ``message_id``, ``attachment_id``, ``space_id``, and ``title``. On
    failure returns ``{"error": "...", "code": "..."}``.

    The caller (typically the LLM through MCP) supplies the SVG string
    produced by ``chart`` or ``status_card``, a title, and an optional
    summary (used as the message body). The agent's AX identity comes from
    the env Gateway already set on the bridge subprocess; we don't override
    it.
    """
    if not isinstance(svg, str) or not svg.strip().startswith("<svg"):
        return {
            "error": "svg argument must be a string starting with '<svg'",
            "code": "INVALID_SVG",
        }
    if not isinstance(title, str) or not title.strip():
        return {"error": "title is required", "code": "MISSING_TITLE"}

    try:
        from ax_cli.config import get_client, resolve_space_id
    except Exception as exc:  # noqa: BLE001 - import is the failure
        return {
            "error": f"ax_cli not available in subprocess env: {exc}",
            "code": "AX_CLI_UNAVAILABLE",
        }

    try:
        client = get_client()
    except Exception as exc:  # noqa: BLE001 - credential resolution
        return {
            "error": f"could not build AX client: {exc}",
            "code": "NO_CREDENTIALS",
        }

    try:
        resolved_space = resolve_space_id(client, explicit=space_id)
    except Exception as exc:  # noqa: BLE001 - space resolution
        return {"error": f"could not resolve space: {exc}", "code": "NO_SPACE"}
    if not resolved_space:
        return {
            "error": "space_id is required (set AX_SPACE_ID or pass space_id)",
            "code": "NO_SPACE",
        }

    # Upload the SVG as a FILE (content-type image/svg+xml) rather than storing
    # it as a raw context value. paxai renders an uploaded svg file inline as
    # an image; a raw-value context entry renders as text (which is what an
    # earlier version did, and why cards showed markup instead of a chart).
    # Per Jacob Taunton: "svg should work, but it needs to be uploaded as svg
    # and not ctx." The uploads endpoint carries the content-type; client.py
    # already maps .svg -> image/svg+xml.
    import tempfile

    label = _make_label(title)
    tmp_path: str | None = None
    try:
        # upload_file takes a path, so stage the SVG to a temp .svg file.
        fd, tmp_path = tempfile.mkstemp(suffix=".svg", prefix="ax_svg_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(svg)
        upload = client.upload_file(tmp_path, space_id=resolved_space)
    except Exception as exc:  # noqa: BLE001 - HTTP errors etc
        return {
            "error": f"svg file upload failed: {exc}",
            "code": "SVG_UPLOAD_FAILED",
            "label": label,
        }
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # Pull the uploaded file's reference fields. The uploads endpoint returns
    # an id/url/content_type; exact key names vary, so probe defensively.
    upload_rec = upload.get("upload", upload) if isinstance(upload, dict) else {}
    attachment_id = upload_rec.get("attachment_id") or upload_rec.get("id") or upload_rec.get("file_id")
    file_url = upload_rec.get("url") or upload_rec.get("file_url")
    content_type = upload_rec.get("content_type") or "image/svg+xml"
    filename = upload_rec.get("filename")
    file_id = upload_rec.get("file_id")

    # Post a plain message with the SVG as a real attachment. send_message's
    # `attachments` param is what makes paxai render the SVG inline as an
    # image — the same path a user dragging an SVG into chat uses. (An earlier
    # version tried metadata.ui.widget signal cards; those posted but didn't
    # render the image. Verified 2026-05-28: the attachments param renders the
    # full status card inline.)
    attachment = {
        "attachment_id": attachment_id,
        "content_type": content_type,
        "kind": "svg",
    }
    if file_id:
        attachment["file_id"] = file_id
    if filename:
        attachment["filename"] = filename
    if file_url:
        attachment["url"] = file_url

    message_body = summary.strip() or title

    try:
        result = client.send_message(
            resolved_space,
            message_body,
            attachments=[attachment],
            message_type="text",
        )
    except Exception as exc:  # noqa: BLE001 - HTTP errors etc
        return {
            "error": f"message post failed: {exc}",
            "code": "MESSAGE_POST_FAILED",
            "label": label,
            "attachment_id": attachment_id,
        }

    message = result.get("message", result) if isinstance(result, dict) else {}
    return {
        "ok": True,
        "message_id": message.get("id") or message.get("message_id"),
        "attachment_id": attachment_id,
        "label": label,
        "space_id": resolved_space,
        "title": title,
    }


# ── MCP tool wrapper ──────────────────────────────────────────────────────


POST_TO_CHAT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "svg": {
            "type": "string",
            "description": (
                "Full SVG document string (must start with '<svg'). Typically "
                "the value returned by the chart or status_card tool's 'svg' field."
            ),
        },
        "title": {
            "type": "string",
            "description": (
                "Human-readable title for the chart (e.g., 'CENTCOM Ammo Status'). Used to name the uploaded file."
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "One-sentence description of what the SVG shows. Posted as the "
                "chat message body alongside the inline image. Defaults to the title."
            ),
        },
        "space_id": {
            "type": "string",
            "description": (
                "Optional: target aX space. Defaults to AX_SPACE_ID from env (the space the agent is bound to)."
            ),
        },
    },
    "required": ["svg", "title"],
}


def _handle_post_to_chat(arguments: dict[str, Any]) -> dict[str, Any]:
    result = post_svg_to_chat(
        svg=arguments.get("svg") or "",
        title=arguments.get("title") or "",
        summary=arguments.get("summary") or "",
        space_id=arguments.get("space_id"),
    )
    return {"content": [{"type": "text", "text": json.dumps(result)}]}
