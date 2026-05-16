import json
from pathlib import Path

from typer.testing import CliRunner

from ax_cli.commands import context
from ax_cli.context_keys import build_upload_context_key
from ax_cli.main import app

runner = CliRunner()


def test_context_download_uses_base_url_and_auth_headers(monkeypatch, tmp_path):
    calls = {}

    class FakeClient:
        base_url = "https://paxai.app"

        def get_context(self, key, *, space_id=None):
            assert key == "image.png"
            assert space_id == "space-1"
            return {
                "value": {
                    "type": "file_upload",
                    "filename": "image.png",
                    "url": "/api/v1/uploads/files/image.png",
                }
            }

        def _auth_headers(self):
            return {
                "Authorization": "Bearer exchanged.jwt",
                "Content-Type": "application/json",
                "X-AX-FP": "fp",
            }

    class FakeResponse:
        content = b"png-bytes"

        def raise_for_status(self):
            return None

    class FakeHttpClient:
        def __init__(self, *, headers, timeout, follow_redirects):
            calls["headers"] = headers
            calls["timeout"] = timeout
            calls["follow_redirects"] = follow_redirects

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, params=None):
            calls["url"] = url
            calls["params"] = params
            return FakeResponse()

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClient)

    output = tmp_path / "downloaded.png"
    result = runner.invoke(app, ["context", "download", "image.png", "--output", str(output)])

    assert result.exit_code == 0
    assert "Downloaded:" in result.output
    assert "[green]" not in result.output
    assert output.read_bytes() == b"png-bytes"
    assert calls["url"] == "https://paxai.app/api/v1/uploads/files/image.png"
    assert calls["params"] is None
    assert calls["headers"] == {
        "Authorization": "Bearer exchanged.jwt",
        "X-AX-FP": "fp",
    }
    assert calls["follow_redirects"] is True


def test_context_download_rejects_html_shell_for_binary_payload(monkeypatch, tmp_path):
    class FakeClient:
        base_url = "https://paxai.app"

        def get_context(self, key, *, space_id=None):
            assert key == "image.png"
            return {
                "value": {
                    "type": "file_upload",
                    "filename": "image.png",
                    "content_type": "image/png",
                    "url": "/api/v1/uploads/files/image.png",
                }
            }

        def _auth_headers(self):
            return {"Authorization": "Bearer exchanged.jwt"}

    class FakeResponse:
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<!DOCTYPE html><html><body>app shell</body></html>"

        def raise_for_status(self):
            return None

    class FakeHttpClient:
        def __init__(self, *, headers, timeout, follow_redirects):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClient)

    output = tmp_path / "downloaded.png"
    result = runner.invoke(app, ["context", "download", "image.png", "--output", str(output)])

    assert result.exit_code == 1
    assert "returned text/html instead" in result.output
    assert "app shell" in result.output
    assert not output.exists()


def test_context_load_fetches_to_preview_cache(monkeypatch, tmp_path):
    calls = {}

    class FakeClient:
        base_url = "https://paxai.app"

        def get_context(self, key, *, space_id=None):
            assert key == "upload-key"
            assert space_id == "space-1"
            return {
                "value": {
                    "type": "file_upload",
                    "filename": "../image.png",
                    "content_type": "image/png",
                    "url": "/api/v1/uploads/files/image.png",
                }
            }

        def _auth_headers(self):
            return {
                "Authorization": "Bearer exchanged.jwt",
                "Content-Type": "application/json",
            }

    class FakeResponse:
        content = b"png-bytes"

        def raise_for_status(self):
            return None

    class FakeHttpClient:
        def __init__(self, *, headers, timeout, follow_redirects):
            calls["headers"] = headers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, params=None):
            calls["url"] = url
            calls["params"] = params
            return FakeResponse()

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClient)

    result = runner.invoke(
        app,
        [
            "context",
            "load",
            "upload-key",
            "--cache-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    preview_files = list(tmp_path.glob("*/image.png"))
    assert len(preview_files) == 1
    assert preview_files[0].read_bytes() == b"png-bytes"
    assert calls["url"] == "https://paxai.app/api/v1/uploads/files/image.png"
    assert calls["params"] is None
    assert calls["headers"] == {"Authorization": "Bearer exchanged.jwt"}
    assert '"text_like": false' in result.output


def test_context_load_can_include_text_content(monkeypatch, tmp_path):
    class FakeClient:
        base_url = "https://paxai.app"

        def get_context(self, key, *, space_id=None):
            return {
                "value": {
                    "type": "file_upload",
                    "filename": "notes.md",
                    "content_type": "text/markdown",
                    "url": "/api/v1/uploads/files/notes.md",
                }
            }

        def _auth_headers(self):
            return {"Authorization": "Bearer exchanged.jwt"}

    class FakeResponse:
        content = b"# Notes\nUseful context."

        def raise_for_status(self):
            return None

    class FakeHttpClient:
        def __init__(self, *, headers, timeout, follow_redirects):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClient)

    result = runner.invoke(
        app,
        [
            "context",
            "load",
            "notes-key",
            "--cache-dir",
            str(tmp_path),
            "--content",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"text_like": true' in result.output
    assert "# Notes" in result.output


def test_default_upload_context_key_is_unique(monkeypatch):
    monkeypatch.setattr("ax_cli.context_keys.time.time", lambda: 1775880839.429)

    first = build_upload_context_key("image.png", "df9b1d15-e9c5-4e60-851e-53ea35b4f5e7")
    second = build_upload_context_key("image.png", "774758d4-8451-4570-bca4-e4c4d34706ac")

    assert first == "upload:1775880839429:image.png:df9b1d15-e9c5-4e60-851e-53ea35b4f5e7"
    assert second == "upload:1775880839429:image.png:774758d4-8451-4570-bca4-e4c4d34706ac"
    assert first != second


def test_context_upload_file_vault_stores_context_before_promote(monkeypatch, tmp_path):
    calls = {}
    sample = tmp_path / "vault.md"
    sample.write_text("# Vault\nkeep this\n")

    class FakeClient:
        def upload_file(self, path, *, space_id=None):
            calls["upload"] = {"path": path, "space_id": space_id}
            return {
                "attachment_id": "att-1",
                "url": "/api/v1/uploads/files/vault.md",
                "content_type": "text/markdown",
                "size": sample.stat().st_size,
                "original_filename": "vault.md",
            }

        def set_context(self, space_id, key, value, *, ttl=None):
            calls["context"] = {"space_id": space_id, "key": key, "value": value, "ttl": ttl}
            return {"status": "stored"}

        def promote_context(self, space_id, key, *, artifact_type="RESEARCH", agent_id=None):
            calls["promote"] = {
                "space_id": space_id,
                "key": key,
                "artifact_type": artifact_type,
                "agent_id": agent_id,
            }
            return {"status": "created", "key": key}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(
        app,
        [
            "context",
            "upload-file",
            str(sample),
            "--vault",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert calls["upload"]["space_id"] == "space-1"
    assert calls["context"]["space_id"] == "space-1"
    assert calls["promote"]["space_id"] == "space-1"
    assert calls["promote"]["key"] == calls["context"]["key"]
    assert calls["promote"]["artifact_type"] == "RESEARCH"


def test_context_upload_file_mention_sends_context_signal(monkeypatch, tmp_path):
    calls = {}
    sample = tmp_path / "notes.md"
    sample.write_text("# Notes\n")

    class FakeClient:
        def upload_file(self, path, *, space_id=None):
            return {
                "attachment_id": "att-1",
                "url": "/api/v1/uploads/files/notes.md",
                "content_type": "text/markdown",
                "size": sample.stat().st_size,
                "original_filename": "notes.md",
            }

        def set_context(self, space_id, key, value, *, ttl=None):
            calls["context"] = {"space_id": space_id, "key": key, "value": value, "ttl": ttl}
            return {"status": "stored"}

        def send_message(self, space_id, content):
            calls["message"] = {"space_id": space_id, "content": content}
            return {"id": "msg-1"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(
        app,
        ["context", "upload-file", str(sample), "--mention", "@demo-agent", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert calls["message"]["space_id"] == "space-1"
    assert calls["message"]["content"].startswith("@demo-agent Context uploaded:")
    assert calls["context"]["key"] in calls["message"]["content"]
    assert '"message_id": "msg-1"' in result.output


def test_context_set_mention_sends_context_signal(monkeypatch):
    calls = {}

    class FakeClient:
        def set_context(self, space_id, key, value, *, ttl=None):
            calls["context"] = {"space_id": space_id, "key": key, "value": value, "ttl": ttl}
            return {"status": "stored", "key": key}

        def send_message(self, space_id, content):
            calls["message"] = {"space_id": space_id, "content": content}
            return {"id": "msg-1"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(
        app,
        ["context", "set", "spec:cli", "ready", "--mention", "mcp_sentinel", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert calls["message"]["content"] == "@mcp_sentinel Context updated: `spec:cli`"
    assert '"message_id": "msg-1"' in result.output


def test_send_context_mention_no_mention(monkeypatch):
    """_send_context_mention returns None when mention is None or empty."""
    result = context._send_context_mention(None, "space-1", None, "test")
    assert result is None
    result = context._send_context_mention(None, "space-1", "", "test")
    assert result is None


def test_send_context_mention_http_error(monkeypatch):
    """_send_context_mention swallows HTTPStatusError and returns None."""
    import httpx

    class FakeClient:
        def send_message(self, space_id, content):
            raise httpx.HTTPStatusError(
                "test",
                request=httpx.Request("POST", "http://test"),
                response=httpx.Response(500, text="fail"),
            )

    result = context._send_context_mention(FakeClient(), "space-1", "@agent", "test")
    assert result is None


def test_normalize_upload_with_attachment():
    """_normalize_upload extracts nested attachment dict."""
    payload = {"attachment": {"attachment_id": "att-1", "filename": "f.txt"}}
    assert context._normalize_upload(payload) == {"attachment_id": "att-1", "filename": "f.txt"}


def test_normalize_upload_flat():
    """_normalize_upload handles flat payload keys."""
    payload = {
        "id": "att-1",
        "original_filename": "f.txt",
        "content_type": "text/plain",
        "size_bytes": 100,
        "url": "/uploads/f.txt",
    }
    result = context._normalize_upload(payload)
    assert result["attachment_id"] == "att-1"
    assert result["filename"] == "f.txt"
    assert result["size"] == 100


def test_optional_space_id_with_explicit(monkeypatch):
    """_optional_space_id with explicit value calls resolve_space_id."""
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: explicit or "default")

    class FakeClient:
        pass

    result = context._optional_space_id(FakeClient(), "explicit-space")
    assert result == "explicit-space"


def test_optional_space_id_with_env(monkeypatch):
    """_optional_space_id reads AX_SPACE_ID env when no explicit value."""
    monkeypatch.setenv("AX_SPACE_ID", "env-space")
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "env-space")

    class FakeClient:
        pass

    result = context._optional_space_id(FakeClient(), None)
    assert result == "env-space"


def test_optional_space_id_none(monkeypatch):
    """_optional_space_id returns None when no space_id available."""
    monkeypatch.delenv("AX_SPACE_ID", raising=False)

    class FakeClient:
        pass

    result = context._optional_space_id(FakeClient(), None)
    assert result is None


def test_safe_filename_path_traversal():
    """_safe_filename strips directory components."""
    assert context._safe_filename("../../../etc/passwd") == "passwd"
    assert context._safe_filename("") == "context-preview.bin"
    assert context._safe_filename("   ") == "context-preview.bin"


def test_context_file_payload_string_value():
    """_context_file_payload parses JSON string values."""
    import json

    data = {"value": json.dumps({"url": "/uploads/f.txt", "filename": "f.txt"})}
    result = context._context_file_payload(data, "key")
    assert result["url"] == "/uploads/f.txt"
    assert result["filename"] == "f.txt"


def test_context_file_payload_invalid_string():
    """_context_file_payload raises ValueError for non-JSON string."""
    import pytest

    with pytest.raises(ValueError, match="not a file upload"):
        context._context_file_payload({"value": "just a plain string"}, "key")


def test_context_file_payload_no_url():
    """_context_file_payload raises ValueError when no url present."""
    import pytest

    with pytest.raises(ValueError, match="not a file upload"):
        context._context_file_payload({"value": {"filename": "f.txt"}}, "key")


def test_context_file_payload_nested_value():
    """_context_file_payload handles nested value dict."""
    data = {"value": {"value": {"url": "/uploads/f.txt", "filename": "f.txt"}}}
    result = context._context_file_payload(data, "key")
    assert result["url"] == "/uploads/f.txt"


def test_looks_like_html():
    """_looks_like_html detects HTML content."""
    assert context._looks_like_html(b"<!DOCTYPE html><html></html>")
    assert context._looks_like_html(b"<html></html>")
    assert context._looks_like_html(b"  <!doctype html>...")
    assert not context._looks_like_html(b"just some text")
    assert not context._looks_like_html(b'{"json": true}')


def test_is_text_like():
    """_is_text_like identifies text content types and extensions."""
    assert context._is_text_like({"content_type": "text/plain"})
    assert context._is_text_like({"content_type": "application/json"})
    assert context._is_text_like({"content_type": "application/javascript"})
    assert context._is_text_like({"filename": "test.py"})
    assert context._is_text_like({"filename": "data.json"})
    assert not context._is_text_like({"content_type": "image/png", "filename": "image.png"})


def test_preview_cache_dir_explicit():
    """_preview_cache_dir uses explicit path when provided."""
    result = context._preview_cache_dir("/tmp/custom")
    assert result == Path("/tmp/custom")


def test_preview_cache_dir_env(monkeypatch):
    """_preview_cache_dir reads AX_PREVIEW_CACHE_DIR env."""
    monkeypatch.setenv("AX_PREVIEW_CACHE_DIR", "/tmp/env-cache")
    result = context._preview_cache_dir()
    assert result == Path("/tmp/env-cache")


def test_preview_cache_dir_xdg(monkeypatch):
    """_preview_cache_dir uses XDG_CACHE_HOME when set."""
    monkeypatch.delenv("AX_PREVIEW_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg")
    result = context._preview_cache_dir()
    assert result == Path("/tmp/xdg/axctl/previews")


def test_preview_cache_dir_default(monkeypatch):
    """_preview_cache_dir falls back to ~/.cache/axctl/previews."""
    monkeypatch.delenv("AX_PREVIEW_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    result = context._preview_cache_dir()
    assert result == Path.home() / ".cache" / "axctl" / "previews"


def test_context_set_human_output(monkeypatch):
    """set_ctx in human mode prints 'Set: <key>'."""

    class FakeClient:
        def set_context(self, space_id, key, value, *, ttl=None):
            return {"status": "stored", "key": key}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["context", "set", "mykey", "myvalue"])
    assert result.exit_code == 0, result.output
    assert "Set: mykey" in result.output


def test_context_get_via_gateway(monkeypatch):
    """get_ctx uses gateway local call when gateway config is present."""
    calls = {}

    def fake_gateway_call(*, gateway_cfg, method, args, space_id):
        calls["method"] = method
        calls["args"] = args
        return {"key": "test", "value": "result"}

    monkeypatch.setattr(context, "resolve_gateway_config", lambda: {"url": "http://localhost:9090"})
    monkeypatch.setattr(context, "get_client", lambda: (_ for _ in ()).throw(AssertionError("direct client path")))

    # get_ctx imports this helper lazily from messages.
    import ax_cli.commands.messages as msg_mod

    monkeypatch.setattr(msg_mod, "_gateway_local_call", fake_gateway_call, raising=False)

    result = runner.invoke(app, ["context", "get", "mykey", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["value"] == "result"
    assert calls == {
        "method": "get_context",
        "args": {"key": "mykey", "space_id": None},
    }


def test_context_get_human_output(monkeypatch):
    """get_ctx in human mode calls print_kv."""
    monkeypatch.setattr(context, "resolve_gateway_config", lambda: None)

    class FakeClient:
        def get_context(self, key, *, space_id=None):
            return {"key": key, "value": "world"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.delenv("AX_SPACE_ID", raising=False)

    result = runner.invoke(app, ["context", "get", "mykey"])
    assert result.exit_code == 0, result.output
    assert "world" in result.output


def test_context_list_dict_of_pairs(monkeypatch):
    """list_ctx handles dict-of-key-metadata-pairs format."""
    monkeypatch.setattr(context, "resolve_gateway_config", lambda: None)

    class FakeClient:
        def list_context(self, prefix=None, space_id=None):
            return {
                "key1": {"value": "val1", "ttl": 3600},
                "key2": {"value": "val2", "ttl": 7200},
            }

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.delenv("AX_SPACE_ID", raising=False)

    result = runner.invoke(app, ["context", "list"])
    assert result.exit_code == 0, result.output
    assert "key1" in result.output
    assert "key2" in result.output


def test_context_list_dict_with_items(monkeypatch):
    """list_ctx handles dict with 'items' key."""
    monkeypatch.setattr(context, "resolve_gateway_config", lambda: None)

    class FakeClient:
        def list_context(self, prefix=None, space_id=None):
            return {"items": [{"key": "k1", "value": "v1", "ttl": 3600}]}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.delenv("AX_SPACE_ID", raising=False)

    result = runner.invoke(app, ["context", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert "k1" in result.output


def test_context_list_as_list(monkeypatch):
    """list_ctx handles list format directly."""
    monkeypatch.setattr(context, "resolve_gateway_config", lambda: None)

    class FakeClient:
        def list_context(self, prefix=None, space_id=None):
            return [{"key": "k1", "value": "v1"}]

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.delenv("AX_SPACE_ID", raising=False)

    result = runner.invoke(app, ["context", "list", "--json"])
    assert result.exit_code == 0, result.output


def test_context_delete(monkeypatch):
    """delete_ctx deletes a context key."""
    calls = {}

    class FakeClient:
        def delete_context(self, key, *, space_id=None):
            calls["key"] = key
            calls["space_id"] = space_id

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.delenv("AX_SPACE_ID", raising=False)

    result = runner.invoke(app, ["context", "delete", "mykey"])
    assert result.exit_code == 0, result.output
    assert "Deleted: mykey" in result.output
    assert calls["key"] == "mykey"


def test_context_download_value_error(monkeypatch, tmp_path):
    """download_file exits with code 1 when context value is not a file."""

    class FakeClient:
        base_url = "https://paxai.app"

        def get_context(self, key, *, space_id=None):
            return {"value": "just a string, not a file"}

        def _auth_headers(self):
            return {"Authorization": "Bearer test"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["context", "download", "mykey"])
    assert result.exit_code == 1
    assert "not a file upload" in result.output


def test_context_upload_file_not_found(monkeypatch, tmp_path):
    """upload_file shows error when file doesn't exist."""

    class FakeClient:
        def upload_file(self, path, *, space_id=None):
            raise FileNotFoundError(f"No such file: {path}")

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["context", "upload-file", "/nonexistent/file.txt"])
    assert result.exit_code == 1
    assert "No such file" in result.output


def test_context_upload_file_context_store_fails(monkeypatch, tmp_path):
    """Upload succeeds but context store fails gracefully."""
    import httpx

    sample = tmp_path / "test.txt"
    sample.write_text("test content")

    class FakeClient:
        def upload_file(self, path, *, space_id=None):
            return {
                "attachment_id": "att-1",
                "url": "/uploads/test.txt",
                "content_type": "text/plain",
                "size_bytes": 12,
                "original_filename": "test.txt",
            }

        def set_context(self, space_id, key, value, *, ttl=None):
            raise httpx.HTTPStatusError(
                "test",
                request=httpx.Request("POST", "http://test"),
                response=httpx.Response(500, text="fail"),
            )

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["context", "upload-file", str(sample), "--json"])
    assert result.exit_code == 0, result.output
    assert "context store failed" in result.output


def test_context_upload_file_human_output(monkeypatch, tmp_path):
    """upload_file in human mode calls print_kv."""
    sample = tmp_path / "test.txt"
    sample.write_text("test content")

    class FakeClient:
        def upload_file(self, path, *, space_id=None):
            return {
                "attachment_id": "att-1",
                "url": "/uploads/test.txt",
                "content_type": "text/plain",
                "size_bytes": 12,
                "original_filename": "test.txt",
            }

        def set_context(self, space_id, key, value, *, ttl=None):
            return {"status": "stored"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["context", "upload-file", str(sample)])
    assert result.exit_code == 0, result.output
    assert "test.txt" in result.output


def test_context_promote(monkeypatch):
    """promote_ctx promotes a context key to vault."""
    calls = {}

    class FakeClient:
        def promote_context(self, space_id, key, *, artifact_type="RESEARCH", agent_id=None):
            calls["key"] = key
            calls["artifact_type"] = artifact_type
            return {"status": "created", "key": key}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["context", "promote", "mykey"])
    assert result.exit_code == 0, result.output
    assert "Promoted: mykey" in result.output
    assert calls["artifact_type"] == "RESEARCH"


def test_context_promote_json(monkeypatch):
    """promote_ctx in JSON mode outputs JSON."""

    class FakeClient:
        def promote_context(self, space_id, key, *, artifact_type="RESEARCH", agent_id=None):
            return {"status": "created", "key": key}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["context", "promote", "mykey", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output[result.output.index("{") :])
    assert data["key"] == "mykey"


def test_context_load_value_error(monkeypatch, tmp_path):
    """load_file exits with code 1 when context value is not a file."""

    class FakeClient:
        base_url = "https://paxai.app"

        def get_context(self, key, *, space_id=None):
            return {"value": "not a file upload"}

        def _auth_headers(self):
            return {"Authorization": "Bearer test"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")

    result = runner.invoke(app, ["context", "load", "mykey", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "not a file upload" in result.output


def test_context_load_human_output(monkeypatch, tmp_path):
    """load_file in human mode calls print_kv."""

    class FakeClient:
        base_url = "https://paxai.app"

        def get_context(self, key, *, space_id=None):
            return {
                "value": {
                    "type": "file_upload",
                    "filename": "test.txt",
                    "content_type": "text/plain",
                    "url": "/uploads/test.txt",
                }
            }

        def _auth_headers(self):
            return {"Authorization": "Bearer test"}

    class FakeResponse:
        content = b"hello world"

        def raise_for_status(self):
            return None

    class FakeHttpClient:
        def __init__(self, *, headers, timeout, follow_redirects):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClient)

    result = runner.invoke(app, ["context", "load", "mykey", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "test.txt" in result.output


def test_context_fetch_url_text_direct_store(monkeypatch):
    """fetch_url stores text content directly in context without --upload."""
    calls = {}

    class FakeResponse:
        headers = {"content-type": "text/plain"}
        content = b"plain text content"
        text = "plain text content"

        def raise_for_status(self):
            return None

    class FakeClient:
        def set_context(self, space_id, key, value, *, ttl=None):
            calls["context"] = {"key": key, "value": value, "ttl": ttl}
            return {"status": "stored"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "get", lambda *a, **kw: FakeResponse())

    result = runner.invoke(
        app,
        ["context", "fetch-url", "https://example.com/readme.txt", "--key", "readme", "--json"],
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output[result.output.index("{") :])
    assert output["type"] == "url_fetch_text"
    assert output["storage"] == "ephemeral"
    # Text was stored directly, not as JSON upload
    assert calls["context"]["value"] == "plain text content"


def test_context_fetch_url_http_error(monkeypatch):
    """fetch_url exits with code 1 on HTTP error."""
    import httpx

    monkeypatch.setattr(context, "get_client", lambda: type("C", (), {})())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(
        context.httpx,
        "get",
        lambda *a, **kw: (_ for _ in ()).throw(httpx.HTTPError("fail")),
    )

    result = runner.invoke(app, ["context", "fetch-url", "https://example.com/fail"])
    assert result.exit_code == 1
    assert "Error fetching URL" in result.output


def test_context_fetch_url_vault_promote(monkeypatch):
    """fetch_url with --vault stores then promotes."""
    calls = {}

    class FakeResponse:
        headers = {"content-type": "text/plain"}
        content = b"vault content"
        text = "vault content"

        def raise_for_status(self):
            return None

    class FakeClient:
        def set_context(self, space_id, key, value, *, ttl=None):
            calls["set"] = True
            return {"status": "stored"}

        def promote_context(self, space_id, key, *, artifact_type="RESEARCH", agent_id=None):
            calls["promote"] = True
            return {"status": "created"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "get", lambda *a, **kw: FakeResponse())

    result = runner.invoke(app, ["context", "fetch-url", "https://example.com/doc.txt", "--vault", "--json"])
    assert result.exit_code == 0, result.output
    assert calls.get("set") is True
    assert calls.get("promote") is True
    output = json.loads(result.output[result.output.index("{") :])
    assert output["storage"] == "vault"


def test_context_fetch_url_human_output(monkeypatch):
    """fetch_url in human mode calls print_kv."""

    class FakeResponse:
        headers = {"content-type": "text/plain"}
        content = b"text"
        text = "text"

        def raise_for_status(self):
            return None

    class FakeClient:
        def set_context(self, space_id, key, value, *, ttl=None):
            return {"status": "stored"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "get", lambda *a, **kw: FakeResponse())

    result = runner.invoke(app, ["context", "fetch-url", "https://example.com/doc.txt"])
    assert result.exit_code == 0, result.output


def test_context_fetch_url_upload_stores_renderable_file_upload(monkeypatch):
    calls = {}

    class FakeResponse:
        headers = {"content-type": "text/markdown; charset=utf-8"}
        content = b"# Article\nFetched markdown.\n"
        text = "# Article\nFetched markdown.\n"

        def raise_for_status(self):
            return None

    class FakeClient:
        def upload_file(self, path, *, space_id=None):
            calls["upload"] = {"path": path, "space_id": space_id}
            assert Path(path).name == "article.md"
            assert Path(path).read_bytes() == FakeResponse.content
            return {
                "attachment_id": "att-1",
                "url": "/api/v1/uploads/files/article.md",
                "content_type": "text/markdown",
                "size": len(FakeResponse.content),
                "original_filename": "article.md",
            }

        def set_context(self, space_id, key, value, *, ttl=None):
            calls["context"] = {"space_id": space_id, "key": key, "value": value, "ttl": ttl}
            return {"status": "stored"}

    monkeypatch.setattr(context, "get_client", lambda: FakeClient())
    monkeypatch.setattr(context, "resolve_space_id", lambda client, explicit=None: "space-1")
    monkeypatch.setattr(context.httpx, "get", lambda *args, **kwargs: FakeResponse())

    result = runner.invoke(
        app,
        [
            "context",
            "fetch-url",
            "https://example.com/article.md",
            "--upload",
            "--key",
            "article",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    stored = json.loads(calls["context"]["value"])
    assert stored["type"] == "file_upload"
    assert stored["filename"] == "article.md"
    assert stored["source"] == "url_fetch"
    assert stored["source_url"] == "https://example.com/article.md"
    assert stored["content"] == FakeResponse.text
    assert calls["context"]["key"] == "article"
    output = json.loads(result.output[result.output.index("{") :])
    assert output["type"] == "file_upload"
