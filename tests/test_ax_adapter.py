"""Tests for ax_cli/plugins/platforms/ax/adapter.py

Covers pure/helper methods: _is_self_authored(), _is_for_me(),
_clean_agent_trigger_text(), _seen_or_record(), and module-level
functions check_requirements(), is_connected(), _env_enablement().

Uses importlib to load the adapter module directly, matching the pattern
from test_ax_adapter_activity.py — the gateway.* dependencies from
hermes-agent are not in ax-gateway's own venv.
"""

from __future__ import annotations

import importlib.util
import os
import re
from collections import OrderedDict
from pathlib import Path

import pytest

# Load the adapter module using spec_from_file_location, matching the
# pattern in test_ax_adapter_activity.py.
_SPEC = importlib.util.spec_from_file_location(
    "ax_adapter_for_test",
    Path(__file__).resolve().parents[1] / "ax_cli" / "plugins" / "platforms" / "ax" / "adapter.py",
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
try:
    _SPEC.loader.exec_module(_MODULE)
except ModuleNotFoundError as exc:
    if "gateway" in str(exc) or "hermes" in str(exc):
        pytest.skip(
            f"hermes-agent not importable from this venv: {exc}",
            allow_module_level=True,
        )
    raise

AxAdapter = _MODULE.AxAdapter
check_requirements = _MODULE.check_requirements
is_connected = _MODULE.is_connected
_env_enablement = _MODULE._env_enablement
SEEN_MESSAGE_LRU_MAX = _MODULE.SEEN_MESSAGE_LRU_MAX


# ── Test helpers ──────────────────────────────────────────────────────────


def _make_adapter() -> "AxAdapter":
    """Create an AxAdapter with minimal mock config for unit testing."""
    adapter = AxAdapter.__new__(AxAdapter)
    adapter.base_url = "https://test.paxai.app"
    adapter.token = "axp_a_test_token_12345678"
    adapter.space_id = "space_abc123"
    adapter.agent_name = "testbot"
    adapter.agent_id = "agent_xyz789"
    adapter.local_gateway_url = "http://127.0.0.1:8765"
    adapter._mention_pattern = re.compile(
        rf"(?<!\w)@{re.escape('testbot')}(?!\w)",
        re.IGNORECASE,
    )
    adapter._seen_message_ids = OrderedDict()
    return adapter


# ── _is_self_authored ─────────────────────────────────────────────────────


class TestIsSelfAuthored:
    def test_self_by_name(self):
        adapter = _make_adapter()
        assert adapter._is_self_authored({"sender": "testbot"}) is True

    def test_self_by_name_case_insensitive(self):
        adapter = _make_adapter()
        assert adapter._is_self_authored({"sender": "TestBot"}) is True

    def test_self_by_agent_name_key(self):
        adapter = _make_adapter()
        assert adapter._is_self_authored({"agent_name": "testbot"}) is True

    def test_self_by_id(self):
        adapter = _make_adapter()
        assert adapter._is_self_authored({"sender_id": "agent_xyz789"}) is True

    def test_self_by_agent_id_key(self):
        adapter = _make_adapter()
        assert adapter._is_self_authored({"agent_id": "agent_xyz789"}) is True

    def test_other_agent(self):
        adapter = _make_adapter()
        assert adapter._is_self_authored({"sender": "otherbot"}) is False

    def test_no_sender_info(self):
        adapter = _make_adapter()
        assert adapter._is_self_authored({}) is False


# ── _is_for_me ────────────────────────────────────────────────────────────


class TestIsForMe:
    def test_mention_in_content(self):
        adapter = _make_adapter()
        assert adapter._is_for_me({"content": "Hey @testbot help me"}) is True

    def test_mention_case_insensitive(self):
        adapter = _make_adapter()
        assert adapter._is_for_me({"content": "Hey @TestBot help me"}) is True

    def test_no_mention(self):
        adapter = _make_adapter()
        assert adapter._is_for_me({"content": "Hey everyone"}) is False

    def test_mentions_array_string(self):
        adapter = _make_adapter()
        assert adapter._is_for_me({"content": "hi", "mentions": ["testbot"]}) is True

    def test_mentions_array_dict(self):
        adapter = _make_adapter()
        assert (
            adapter._is_for_me(
                {
                    "content": "hi",
                    "mentions": [{"name": "testbot"}],
                }
            )
            is True
        )

    def test_mentions_array_dict_agent_name(self):
        adapter = _make_adapter()
        assert (
            adapter._is_for_me(
                {
                    "content": "hi",
                    "mentions": [{"agent_name": "testbot"}],
                }
            )
            is True
        )

    def test_word_boundary_no_false_positive(self):
        adapter = _make_adapter()
        # @testbot2 should not match @testbot
        assert adapter._is_for_me({"content": "Hey @testbot2 help me"}) is False

    def test_email_no_false_positive(self):
        adapter = _make_adapter()
        assert adapter._is_for_me({"content": "email@testbot.com"}) is False

    def test_mention_at_end(self):
        adapter = _make_adapter()
        assert adapter._is_for_me({"content": "Hey @testbot"}) is True

    def test_mention_with_punctuation(self):
        adapter = _make_adapter()
        assert adapter._is_for_me({"content": "Hey @testbot, what's up?"}) is True

    def test_text_key_fallback(self):
        adapter = _make_adapter()
        assert adapter._is_for_me({"text": "Hey @testbot help"}) is True


# ── _clean_agent_trigger_text ─────────────────────────────────────────────


class TestCleanAgentTriggerText:
    def test_strips_leading_mention(self):
        adapter = _make_adapter()
        assert adapter._clean_agent_trigger_text("@testbot do this") == "do this"

    def test_strips_mention_with_colon(self):
        adapter = _make_adapter()
        assert adapter._clean_agent_trigger_text("@testbot: do this") == "do this"

    def test_strips_mention_with_comma(self):
        adapter = _make_adapter()
        assert adapter._clean_agent_trigger_text("@testbot, do this") == "do this"

    def test_strips_mention_with_dash(self):
        adapter = _make_adapter()
        assert adapter._clean_agent_trigger_text("@testbot- do this") == "do this"

    def test_case_insensitive(self):
        adapter = _make_adapter()
        assert adapter._clean_agent_trigger_text("@TestBot /help") == "/help"

    def test_preserves_non_leading_mentions(self):
        adapter = _make_adapter()
        result = adapter._clean_agent_trigger_text("@testbot talk to @otherbot")
        assert "@otherbot" in result

    def test_empty_string(self):
        adapter = _make_adapter()
        assert adapter._clean_agent_trigger_text("") == ""

    def test_only_mention_returns_original(self):
        adapter = _make_adapter()
        # If stripping produces empty, returns original
        result = adapter._clean_agent_trigger_text("@testbot")
        assert result == "@testbot"

    def test_leading_whitespace(self):
        adapter = _make_adapter()
        assert adapter._clean_agent_trigger_text("  @testbot hello") == "hello"

    def test_slash_command_exposed(self):
        adapter = _make_adapter()
        result = adapter._clean_agent_trigger_text("@testbot /status")
        assert result == "/status"


# ── _seen_or_record ───────────────────────────────────────────────────────


class TestSeenOrRecord:
    def test_first_time_returns_false(self):
        adapter = _make_adapter()
        assert adapter._seen_or_record("msg_001") is False

    def test_second_time_returns_true(self):
        adapter = _make_adapter()
        adapter._seen_or_record("msg_001")
        assert adapter._seen_or_record("msg_001") is True

    def test_different_ids_independent(self):
        adapter = _make_adapter()
        adapter._seen_or_record("msg_001")
        assert adapter._seen_or_record("msg_002") is False

    def test_lru_eviction(self):
        adapter = _make_adapter()
        # Fill beyond the max
        for i in range(SEEN_MESSAGE_LRU_MAX + 10):
            adapter._seen_or_record(f"msg_{i:05d}")
        # The oldest should have been evicted
        assert adapter._seen_or_record("msg_00000") is False
        # Recent ones should still be there
        assert adapter._seen_or_record(f"msg_{SEEN_MESSAGE_LRU_MAX + 9:05d}") is True

    def test_moves_to_end_on_re_access(self):
        adapter = _make_adapter()
        adapter._seen_or_record("msg_old")
        for i in range(SEEN_MESSAGE_LRU_MAX - 2):
            adapter._seen_or_record(f"msg_{i:05d}")
        # Access msg_old again to move it to end
        adapter._seen_or_record("msg_old")
        # Add more to evict the oldest non-refreshed
        adapter._seen_or_record("msg_new_1")
        adapter._seen_or_record("msg_new_2")
        # msg_old should still be present (was moved to end)
        assert adapter._seen_or_record("msg_old") is True


# ── check_requirements ────────────────────────────────────────────────────


class TestCheckRequirements:
    def test_returns_true_with_httpx(self):
        assert check_requirements() is True


# ── is_connected ──────────────────────────────────────────────────────────


class TestIsConnected:
    def test_all_env_set(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        assert is_connected() is True

    def test_missing_token(self, monkeypatch):
        monkeypatch.delenv("AX_TOKEN", raising=False)
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        assert is_connected() is False

    def test_missing_space_id(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.delenv("AX_SPACE_ID", raising=False)
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        assert is_connected() is False

    def test_missing_agent_name(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.delenv("AX_AGENT_NAME", raising=False)
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        assert is_connected() is False

    def test_missing_agent_id(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.delenv("AX_AGENT_ID", raising=False)
        assert is_connected() is False

    def test_all_missing(self, monkeypatch):
        monkeypatch.delenv("AX_TOKEN", raising=False)
        monkeypatch.delenv("AX_SPACE_ID", raising=False)
        monkeypatch.delenv("AX_AGENT_NAME", raising=False)
        monkeypatch.delenv("AX_AGENT_ID", raising=False)
        assert is_connected() is False


# ── _env_enablement ───────────────────────────────────────────────────────


class TestEnvEnablement:
    def test_all_env_set(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        monkeypatch.delenv("AX_BASE_URL", raising=False)
        monkeypatch.delenv("AX_HOME_CHANNEL", raising=False)

        result = _env_enablement()
        assert result is not None
        assert result["token"] == "axp_a_test"
        assert result["extra"]["space_id"] == "space123"
        assert result["extra"]["agent_name"] == "mybot"
        assert result["extra"]["agent_id"] == "agent456"
        # Should set AX_HOME_CHANNEL to space_id
        assert os.environ.get("AX_HOME_CHANNEL") == "space123"

    def test_custom_base_url(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        monkeypatch.setenv("AX_BASE_URL", "https://custom.paxai.app")
        monkeypatch.delenv("AX_HOME_CHANNEL", raising=False)

        result = _env_enablement()
        assert result["extra"]["base_url"] == "https://custom.paxai.app"

    def test_returns_none_if_missing_token(self, monkeypatch):
        monkeypatch.delenv("AX_TOKEN", raising=False)
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        assert _env_enablement() is None

    def test_returns_none_if_missing_space(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.delenv("AX_SPACE_ID", raising=False)
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        assert _env_enablement() is None

    def test_home_channel_in_result(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        monkeypatch.delenv("AX_HOME_CHANNEL", raising=False)

        result = _env_enablement()
        assert "home_channel" in result
        assert result["home_channel"]["chat_id"] == "space123"

    def test_custom_home_channel(self, monkeypatch):
        monkeypatch.setenv("AX_TOKEN", "axp_a_test")
        monkeypatch.setenv("AX_SPACE_ID", "space123")
        monkeypatch.setenv("AX_AGENT_NAME", "mybot")
        monkeypatch.setenv("AX_AGENT_ID", "agent456")
        monkeypatch.setenv("AX_HOME_CHANNEL", "custom_channel")

        result = _env_enablement()
        assert result["home_channel"]["chat_id"] == "custom_channel"
