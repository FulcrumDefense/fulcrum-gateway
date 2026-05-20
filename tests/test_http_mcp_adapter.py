"""Tests for the HTTP MCP adapter with mocked responses."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from ax_cli.connectors.errors import ConnectorProviderError
from ax_cli.connectors.providers.http_mcp_adapter import execute_tool, list_tools


@pytest.fixture()
def config() -> dict[str, Any]:
    return {
        "base_url": "http://localhost:8080",
        "auth_header_name": "Authorization",
        "auth_prefix": "Bearer",
    }


@pytest.fixture()
def auth_env() -> dict[str, str]:
    return {"HTTP_MCP_API_KEY": "test-key-123"}


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    if json_data is not None:
        return httpx.Response(
            status_code=status_code,
            json=json_data,
            request=httpx.Request("POST", "http://localhost:8080"),
        )
    return httpx.Response(
        status_code=status_code,
        text="",
        request=httpx.Request("POST", "http://localhost:8080"),
    )


# ── list_tools ───────────────────────────────────────────────────────────────


class TestListTools:
    def test_list_success(self, auth_env: dict, config: dict):
        mock_data = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {"name": "get_weather", "description": "Get weather data"},
                    {"name": "send_email", "description": "Send an email"},
                ],
            },
        }
        with patch("httpx.post", return_value=_mock_response(200, mock_data)) as mock_post:
            result = list_tools(auth_env, config, "test-mcp")
            assert "tools" in result
            assert len(result["tools"]) == 2
            assert result["tools"][0]["name"] == "get_weather"

            call_kwargs = mock_post.call_args
            body = call_kwargs.kwargs["json"]
            assert body["jsonrpc"] == "2.0"
            assert body["method"] == "tools/list"

    def test_list_with_auth_header(self, auth_env: dict, config: dict):
        mock_data = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        with patch("httpx.post", return_value=_mock_response(200, mock_data)) as mock_post:
            list_tools(auth_env, config, "test-mcp")
            headers = mock_post.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-key-123"

    def test_list_no_auth(self, config: dict):
        mock_data = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        with patch("httpx.post", return_value=_mock_response(200, mock_data)) as mock_post:
            list_tools({}, config, "test-mcp")
            headers = mock_post.call_args.kwargs["headers"]
            assert "Authorization" not in headers

    def test_list_missing_base_url(self, auth_env: dict):
        with pytest.raises(ConnectorProviderError, match="base_url not configured"):
            list_tools(auth_env, {}, "test-mcp")

    def test_list_http_error(self, auth_env: dict, config: dict):
        with patch("httpx.post", return_value=_mock_response(500)):
            with pytest.raises(ConnectorProviderError) as exc_info:
                list_tools(auth_env, config, "test-mcp")
            assert exc_info.value.status_code == 500

    def test_list_timeout(self, auth_env: dict, config: dict):
        with patch("httpx.post", side_effect=httpx.ReadTimeout("timeout")):
            with pytest.raises(ConnectorProviderError, match="Timeout"):
                list_tools(auth_env, config, "test-mcp")

    def test_list_jsonrpc_error(self, auth_env: dict, config: dict):
        mock_data = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
        with patch("httpx.post", return_value=_mock_response(200, mock_data)):
            with pytest.raises(ConnectorProviderError, match="Invalid Request"):
                list_tools(auth_env, config, "test-mcp")

    def test_list_result_as_array(self, auth_env: dict, config: dict):
        mock_data = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": [
                {"name": "tool_a"},
                {"name": "tool_b"},
            ],
        }
        with patch("httpx.post", return_value=_mock_response(200, mock_data)):
            result = list_tools(auth_env, config, "test-mcp")
            assert "tools" in result
            assert len(result["tools"]) == 2


# ── execute_tool ─────────────────────────────────────────────────────────────


class TestExecuteTool:
    def test_execute_success(self, auth_env: dict, config: dict):
        mock_data = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "sunny, 72F"}]},
        }
        with patch("httpx.post", return_value=_mock_response(200, mock_data)) as mock_post:
            result = execute_tool("get_weather", {"city": "SF"}, auth_env, config, "test-mcp")
            assert result["content"][0]["text"] == "sunny, 72F"

            body = mock_post.call_args.kwargs["json"]
            assert body["method"] == "tools/call"
            assert body["params"]["name"] == "get_weather"
            assert body["params"]["arguments"] == {"city": "SF"}

    def test_execute_http_error(self, auth_env: dict, config: dict):
        with patch("httpx.post", return_value=_mock_response(404)):
            with pytest.raises(ConnectorProviderError) as exc_info:
                execute_tool("nonexistent", {}, auth_env, config, "test-mcp")
            assert exc_info.value.status_code == 404

    def test_execute_timeout(self, auth_env: dict, config: dict):
        with patch("httpx.post", side_effect=httpx.ReadTimeout("timeout")):
            with pytest.raises(ConnectorProviderError, match="Timeout"):
                execute_tool("slow_tool", {}, auth_env, config, "test-mcp")

    def test_execute_connect_error(self, auth_env: dict, config: dict):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(ConnectorProviderError, match="HTTP error"):
                execute_tool("tool", {}, auth_env, config, "test-mcp")

    def test_execute_jsonrpc_error(self, auth_env: dict, config: dict):
        mock_data = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        }
        with patch("httpx.post", return_value=_mock_response(200, mock_data)):
            with pytest.raises(ConnectorProviderError, match="Method not found"):
                execute_tool("missing_tool", {}, auth_env, config, "test-mcp")

    def test_execute_custom_auth_header(self, config: dict):
        config["auth_header_name"] = "X-API-Key"
        config["auth_prefix"] = ""
        auth = {"HTTP_MCP_API_KEY": "raw-key"}
        mock_data = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        with patch("httpx.post", return_value=_mock_response(200, mock_data)) as mock_post:
            execute_tool("tool", {}, auth, config, "test-mcp")
            headers = mock_post.call_args.kwargs["headers"]
            assert headers["X-API-Key"] == "raw-key"
