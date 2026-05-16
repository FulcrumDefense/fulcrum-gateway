"""Tests for ax_cli/runtimes/hermes/runtimes/openai_sdk.py

Covers pure/helper functions: token candidates, auth error detection,
token fingerprinting, token blocking, and tool display formatting.
Does NOT test the full execute() loop (requires OpenAI SDK mocking).
"""

from __future__ import annotations

import hashlib
import time
from unittest.mock import patch

from ax_cli.runtimes.hermes.runtimes.openai_sdk import (
    _BLOCKED_TOKENS,
    TOKEN_BLOCK_SECONDS,
    _block_token,
    _is_auth_error,
    _is_token_blocked,
    _oauth_token_candidates,
    _token_fingerprint,
    _tool_display,
    _unblock_token,
)

# ── _is_auth_error ───────────────────────────────────────────────────────


class TestIsAuthError:
    def test_token_expired(self):
        assert _is_auth_error(Exception("token_expired")) is True

    def test_provided_token_expired(self):
        assert _is_auth_error(Exception("provided authentication token is expired")) is True

    def test_oauth_expired(self):
        assert _is_auth_error(Exception("oauth token has expired")) is True

    def test_authentication_error(self):
        assert _is_auth_error(Exception("authentication_error")) is True

    def test_401_status(self):
        assert _is_auth_error(Exception("HTTP 401 Unauthorized")) is True

    def test_non_auth_error(self):
        assert _is_auth_error(Exception("connection timeout")) is False

    def test_rate_limit_not_auth(self):
        assert _is_auth_error(Exception("429 rate limited")) is False

    def test_case_insensitive(self):
        assert _is_auth_error(Exception("TOKEN_EXPIRED")) is True


# ── _token_fingerprint ───────────────────────────────────────────────────


class TestTokenFingerprint:
    def test_returns_sha256_hex(self):
        token = "test_token_123"
        expected = hashlib.sha256(token.encode()).hexdigest()
        assert _token_fingerprint(token) == expected

    def test_different_tokens_different_fingerprints(self):
        assert _token_fingerprint("token_a") != _token_fingerprint("token_b")

    def test_deterministic(self):
        assert _token_fingerprint("same") == _token_fingerprint("same")


# ── _is_token_blocked / _block_token / _unblock_token ────────────────────


class TestTokenBlocking:
    def setup_method(self):
        """Clear blocked tokens before each test."""
        _BLOCKED_TOKENS.clear()

    def test_unblocked_by_default(self):
        assert _is_token_blocked("new_token") is False

    def test_block_then_check(self):
        _block_token("my_token")
        assert _is_token_blocked("my_token") is True

    def test_unblock_token(self):
        _block_token("my_token")
        assert _is_token_blocked("my_token") is True
        _unblock_token("my_token")
        assert _is_token_blocked("my_token") is False

    def test_block_expires_after_timeout(self):
        _block_token("expiring_token")
        # Check with a "now" far in the future
        future = time.time() + TOKEN_BLOCK_SECONDS + 100
        assert _is_token_blocked("expiring_token", now=future) is False

    def test_block_still_active_before_timeout(self):
        _block_token("fresh_block")
        now = time.time() + 1  # 1 second after block
        assert _is_token_blocked("fresh_block", now=now) is True

    def test_unblock_nonexistent_is_safe(self):
        _unblock_token("never_blocked")  # Should not raise

    def test_multiple_tokens_independent(self):
        _block_token("token_a")
        _block_token("token_b")
        _unblock_token("token_a")
        assert _is_token_blocked("token_a") is False
        assert _is_token_blocked("token_b") is True


# ── _tool_display ────────────────────────────────────────────────────────


class TestToolDisplay:
    def test_read_file_with_path(self):
        assert _tool_display("read_file", {"path": "/home/user/project/main.py"}) == "Read main.py"

    def test_read_file_no_slash(self):
        assert _tool_display("read_file", {"path": "file.py"}) == "Read file.py"

    def test_write_file_with_path(self):
        assert _tool_display("write_file", {"path": "/tmp/output.txt"}) == "Write output.txt"

    def test_edit_file_with_path(self):
        assert _tool_display("edit_file", {"path": "/src/lib/utils.py"}) == "Edit utils.py"

    def test_bash_command(self):
        result = _tool_display("bash", {"command": "python3 test.py"})
        assert result == "Run: python3 test.py"

    def test_bash_long_command_truncated(self):
        long_cmd = "x" * 100
        result = _tool_display("bash", {"command": long_cmd})
        assert len(result) <= 70  # "Run: " + 60 chars

    def test_grep_pattern(self):
        assert _tool_display("grep", {"pattern": "def main"}) == "Search: def main"

    def test_glob_files_pattern(self):
        assert _tool_display("glob_files", {"pattern": "**/*.py"}) == "Find: **/*.py"

    def test_unknown_tool_name_passthrough(self):
        assert _tool_display("custom_tool", {}) == "custom_tool"

    def test_empty_path(self):
        assert _tool_display("read_file", {"path": ""}) == "Read "

    def test_read_file_missing_path(self):
        assert _tool_display("read_file", {}) == "Read "


# ── _oauth_token_candidates ──────────────────────────────────────────────


class TestOauthTokenCandidates:
    def setup_method(self):
        _BLOCKED_TOKENS.clear()

    @patch.dict(
        "os.environ",
        {
            "AX_CODEX_TOKEN": "env_token_123",
            "AX_CODEX_TOKEN_FILE": "",
        },
        clear=False,
    )
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._read_token_file", return_value="")
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._load_auth_json_token", return_value="")
    def test_env_token_first(self, mock_auth, mock_read):
        candidates = _oauth_token_candidates()
        assert len(candidates) >= 1
        assert candidates[0] == ("env:AX_CODEX_TOKEN", "env_token_123")

    @patch.dict(
        "os.environ",
        {
            "AX_CODEX_TOKEN": "",
            "AX_CODEX_TOKEN_FILE": "",
        },
        clear=False,
    )
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._read_token_file", return_value="file_token")
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._load_auth_json_token", return_value="")
    def test_shared_token_file(self, mock_auth, mock_read):
        candidates = _oauth_token_candidates()
        # Should pick up the shared token file
        sources = [src for src, tok in candidates]
        assert any("file:" in s for s in sources)

    @patch.dict(
        "os.environ",
        {
            "AX_CODEX_TOKEN": "dedup_token",
            "AX_CODEX_TOKEN_FILE": "",
        },
        clear=False,
    )
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._read_token_file", return_value="dedup_token")
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._load_auth_json_token", return_value="dedup_token")
    def test_dedup_same_token(self, mock_auth, mock_read):
        candidates = _oauth_token_candidates()
        tokens = [tok for src, tok in candidates]
        # Should only appear once despite being returned by multiple sources
        assert tokens.count("dedup_token") == 1

    @patch.dict(
        "os.environ",
        {
            "AX_CODEX_TOKEN": "",
            "AX_CODEX_TOKEN_FILE": "",
        },
        clear=False,
    )
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._read_token_file", return_value="")
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._load_auth_json_token", return_value="")
    def test_no_tokens_available(self, mock_auth, mock_read):
        candidates = _oauth_token_candidates()
        assert candidates == []

    @patch.dict(
        "os.environ",
        {
            "AX_CODEX_TOKEN": "blocked_token",
            "AX_CODEX_TOKEN_FILE": "",
        },
        clear=False,
    )
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._read_token_file", return_value="")
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._load_auth_json_token", return_value="")
    def test_blocked_token_excluded(self, mock_auth, mock_read):
        _block_token("blocked_token")
        candidates = _oauth_token_candidates()
        tokens = [tok for src, tok in candidates]
        assert "blocked_token" not in tokens

    @patch.dict(
        "os.environ",
        {
            "AX_CODEX_TOKEN": "",
            "AX_CODEX_TOKEN_FILE": "~/custom/token",
        },
        clear=False,
    )
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._read_token_file")
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._load_auth_json_token", return_value="")
    def test_custom_token_file(self, mock_auth, mock_read):
        mock_read.return_value = "custom_file_token"
        candidates = _oauth_token_candidates()
        sources = [src for src, tok in candidates]
        assert any("custom/token" in s for s in sources)

    @patch.dict(
        "os.environ",
        {
            "AX_CODEX_TOKEN": "  whitespace_token  ",
            "AX_CODEX_TOKEN_FILE": "",
        },
        clear=False,
    )
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._read_token_file", return_value="")
    @patch("ax_cli.runtimes.hermes.runtimes.openai_sdk._load_auth_json_token", return_value="")
    def test_whitespace_stripped(self, mock_auth, mock_read):
        candidates = _oauth_token_candidates()
        if candidates:
            assert candidates[0][1] == "whitespace_token"
