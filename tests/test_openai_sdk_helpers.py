"""Tests for openai_sdk.py — token management helpers."""

import hashlib
import json
import time

from ax_cli.runtimes.hermes.runtimes.openai_sdk import (
    _BLOCKED_TOKENS,
    _block_token,
    _is_auth_error,
    _is_token_blocked,
    _load_auth_json_token,
    _oauth_token_candidates,
    _read_token_file,
    _token_fingerprint,
)

# ---- _read_token_file ----


def test_read_token_file(tmp_path):
    f = tmp_path / "token"
    f.write_text("  secret  \n")
    assert _read_token_file(f) == "secret"


def test_read_token_file_missing(tmp_path):
    assert _read_token_file(tmp_path / "nope") == ""


# ---- _load_auth_json_token ----


def test_load_auth_json_token_valid(tmp_path, monkeypatch):
    auth = tmp_path / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"tokens": {"access_token": "oauth-tok"}}))
    import ax_cli.runtimes.hermes.runtimes.openai_sdk as mod

    monkeypatch.setattr(mod, "CODEX_AUTH_PATH", auth)
    assert _load_auth_json_token() == "oauth-tok"


# ---- _token_fingerprint ----


def test_token_fingerprint():
    fp = _token_fingerprint("secret")
    assert fp == hashlib.sha256(b"secret").hexdigest()


# ---- _is_token_blocked / _block_token ----


def test_is_token_blocked_no():
    assert not _is_token_blocked("fresh-token-xyz")


def test_block_and_check():
    _block_token("block-me-xyz")
    assert _is_token_blocked("block-me-xyz")
    fp = _token_fingerprint("block-me-xyz")
    del _BLOCKED_TOKENS[fp]


def test_blocked_token_expires():
    fp = _token_fingerprint("expired-tok-xyz")
    _BLOCKED_TOKENS[fp] = time.time() - 1
    assert not _is_token_blocked("expired-tok-xyz")
    del _BLOCKED_TOKENS[fp]


# ---- _is_auth_error ----


def test_is_auth_error_true():
    assert _is_auth_error(Exception("token_expired: please re-authenticate"))
    assert _is_auth_error(Exception("401 Unauthorized"))
    assert _is_auth_error(Exception("authentication_error occurred"))
    assert _is_auth_error(Exception("OAuth token has expired"))


def test_is_auth_error_false():
    assert not _is_auth_error(Exception("connection timeout"))
    assert not _is_auth_error(Exception("500 Internal Server Error"))


# ---- _oauth_token_candidates ----


def test_oauth_token_candidates_env(monkeypatch):
    monkeypatch.setenv("AX_CODEX_TOKEN", "env-token")
    candidates = _oauth_token_candidates()
    sources = [s for s, t in candidates]
    assert "env:AX_CODEX_TOKEN" in sources


def test_oauth_token_candidates_file(monkeypatch, tmp_path):
    token_file = tmp_path / "codex_token"
    token_file.write_text("file-token")
    monkeypatch.setenv("AX_CODEX_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("AX_CODEX_TOKEN", raising=False)
    candidates = _oauth_token_candidates()
    tokens = [t for s, t in candidates]
    assert "file-token" in tokens


def test_oauth_token_candidates_dedupe(monkeypatch):
    monkeypatch.setenv("AX_CODEX_TOKEN", "same-token")
    candidates = _oauth_token_candidates()
    tokens = [t for s, t in candidates if t == "same-token"]
    assert len(tokens) == 1
