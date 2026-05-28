"""Tests for the connector dispatch layer — execute_tool toolkit derivation
and policy enforcement integration. Specifically covers the #128 regression
where the Hermes ``_connector_call`` path passed ``toolkit=None`` and got
every call rejected when an ``allowed_toolkits`` allow-list was set."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from ax_cli.connectors.errors import ConnectorPolicyError
from ax_cli.connectors.providers import dispatch
from ax_cli.connectors.types import ConnectorRow


@pytest.fixture()
def composio_row_with_github_allowlist() -> ConnectorRow:
    return ConnectorRow(
        id="connector-1",
        name="composio-gh",
        provider="composio",
        enabled=True,
        config={"allowed_toolkits": ["github"]},
    )


@pytest.fixture()
def http_mcp_row() -> ConnectorRow:
    # http_mcp has no toolkit_from_slug helper — verifies the dispatch
    # layer doesn't blow up on providers that don't ship one.
    return ConnectorRow(
        id="connector-2",
        name="local-mcp",
        provider="http_mcp",
        enabled=True,
        config={"base_url": "http://localhost:8080"},
    )


def _stub_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"ok": True}


class TestExecuteToolToolkitDerivation:
    def test_caller_omits_toolkit_for_composio_slug_derives_and_passes_policy(
        self, composio_row_with_github_allowlist: ConnectorRow
    ):
        # Reproduces #128: Hermes _connector_call passes toolkit=None.
        # Before the fix this raised ConnectorPolicyError because
        # _toolkit_allowed(None, policy) is False when an allow-list is set.
        with patch("ax_cli.connectors.providers.composio_adapter.execute_tool", side_effect=_stub_execute):
            result = dispatch.execute_tool(
                composio_row_with_github_allowlist,
                "GITHUB_LIST_PULL_REQUESTS",
                {},
                auth_env={"COMPOSIO_API_KEY": "ak_test"},
            )
            assert result == {"ok": True}

    def test_caller_omits_toolkit_for_non_matching_slug_still_rejects(
        self, composio_row_with_github_allowlist: ConnectorRow
    ):
        # The fix must not become an allow-all — a slug whose derived
        # toolkit doesn't match the allow-list must still be rejected.
        with patch("ax_cli.connectors.providers.composio_adapter.execute_tool", side_effect=_stub_execute) as mock_exec:
            with pytest.raises(ConnectorPolicyError):
                dispatch.execute_tool(
                    composio_row_with_github_allowlist,
                    "SLACK_SEND_MESSAGE",
                    {},
                    auth_env={"COMPOSIO_API_KEY": "ak_test"},
                )
            mock_exec.assert_not_called()

    def test_explicit_toolkit_overrides_derivation(self, composio_row_with_github_allowlist: ConnectorRow):
        # When the caller knows the toolkit (e.g. from a list_tools
        # context), the explicit value wins. Passing toolkit="slack" with
        # a github allow-list must be rejected even though the slug looks
        # like a github tool.
        with patch("ax_cli.connectors.providers.composio_adapter.execute_tool", side_effect=_stub_execute):
            with pytest.raises(ConnectorPolicyError):
                dispatch.execute_tool(
                    composio_row_with_github_allowlist,
                    "GITHUB_LIST_PULL_REQUESTS",
                    {},
                    auth_env={"COMPOSIO_API_KEY": "ak_test"},
                    toolkit="slack",
                )

    def test_provider_without_derive_helper_runs_normally(self, http_mcp_row: ConnectorRow):
        # http_mcp ships no toolkit_from_slug — getattr falls through to
        # None and we proceed with toolkit=None. No allow-list is set on
        # this row, so the call should succeed.
        with patch("ax_cli.connectors.providers.http_mcp_adapter.execute_tool", side_effect=_stub_execute) as mock_exec:
            result = dispatch.execute_tool(
                http_mcp_row,
                "get_weather",
                {"city": "SF"},
                auth_env={"HTTP_MCP_API_KEY": "raw"},
            )
            assert result == {"ok": True}
            mock_exec.assert_called_once()
