"""Tests for ax_cli/runtimes/hermes/runtimes/codex_cli.py

Covers the _summarize() pure function and CodexCLIRuntime.execute() with
mocked subprocess.Popen so no actual CLI is needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ax_cli.runtimes.hermes.runtimes.codex_cli import CodexCLIRuntime, _summarize

# ── _summarize() ──────────────────────────────────────────────────────────


class TestSummarize:
    def test_apply_patch(self):
        assert _summarize("bash apply_patch <<EOF...") == "Applying patch..."

    def test_rg_search(self):
        assert _summarize("cd /src && rg 'pattern' .") == "Searching..."

    def test_grep_search(self):
        assert _summarize("grep -r 'foo' /bar") == "Searching..."

    def test_find_search(self):
        assert _summarize("find . -name '*.py'") == "Searching..."

    def test_cat_read(self):
        assert _summarize("cat /path/to/file.py") == "Reading files..."

    def test_head_read(self):
        assert _summarize("head -20 file.txt") == "Reading files..."

    def test_ls_read(self):
        assert _summarize("ls -la /project") == "Reading files..."

    def test_git_read(self):
        assert _summarize("git diff HEAD~1") == "Reading files..."

    def test_generic_command(self):
        result = _summarize("python3 main.py")
        assert result.startswith("Running: ")
        assert result.endswith("...")

    def test_long_command_truncated(self):
        long_cmd = "echo " + "x" * 200
        result = _summarize(long_cmd)
        assert len(result) <= 105

    def test_whitespace_normalized(self):
        result = _summarize("echo   hello    world")
        assert "  " not in result.split("Running: ")[-1][:20]


# ── CodexCLIRuntime.execute() ─────────────────────────────────────────────


class TestCodexCLIRuntimeExecute:
    def _make_proc(self, stdout_lines, returncode=0):
        """Create a mock Popen with given stdout lines (as JSON strings)."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = iter(stdout_lines)
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = ""
        mock_proc.returncode = returncode
        mock_proc.wait.return_value = None
        mock_proc.kill.return_value = None
        mock_proc.pid = 12345
        return mock_proc

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_basic_text_response(self, mock_popen):
        events = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Hello!"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = CodexCLIRuntime()
        result = runtime.execute("say hi", workdir="/tmp/test")

        assert result.text == "Hello!"
        assert result.exit_reason == "done"
        assert result.tool_count == 0

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_session_id_captured(self, mock_popen):
        events = [
            json.dumps({"type": "thread.started", "thread_id": "sess_abc123"}) + "\n",
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = CodexCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.session_id == "sess_abc123"

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_tool_count_tracked(self, mock_popen):
        events = [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"type": "command_execution", "command": "ls -la"},
                }
            )
            + "\n",
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"type": "command_execution", "command": "cat foo.py"},
                }
            )
            + "\n",
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = CodexCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.tool_count == 2

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_crash_exit_reason(self, mock_popen):
        events = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events, returncode=1)

        runtime = CodexCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.exit_reason == "crashed"

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_invalid_json_lines_skipped(self, mock_popen):
        events = [
            "not json\n",
            "\n",
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = CodexCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.text == "ok"

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_session_resume_command(self, mock_popen):
        events = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "resumed"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = CodexCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", session_id="sess_xyz")

        # Check that the command was built with "resume" and session_id
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "resume" in cmd
        assert "sess_xyz" in cmd

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_model_flag(self, mock_popen):
        events = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = CodexCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", model="gpt-5")

        cmd = mock_popen.call_args[0][0]
        assert "-m" in cmd
        assert "gpt-5" in cmd

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_disable_mcp_flag(self, mock_popen):
        events = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = CodexCLIRuntime()
        runtime.execute(
            "test",
            workdir="/tmp/test",
            extra_args={"disable_mcp": True},
        )

        cmd = mock_popen.call_args[0][0]
        assert "-c" in cmd
        assert "mcp_servers.ax-platform.enabled=false" in cmd

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_empty_response(self, mock_popen):
        events = []  # No events
        mock_popen.return_value = self._make_proc(events)

        runtime = CodexCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.text == ""

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_stream_callback_on_tool_start(self, mock_popen):
        from ax_cli.runtimes.hermes.runtimes import StreamCallback

        events = [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"type": "command_execution", "command": "find . -name '*.py'"},
                }
            )
            + "\n",
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        cb = MagicMock(spec=StreamCallback)
        runtime = CodexCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", stream_cb=cb)

        cb.on_tool_start.assert_called_once()
        assert cb.on_tool_start.call_args[0][0] == "bash"

    @patch("ax_cli.runtimes.hermes.runtimes.codex_cli.subprocess.Popen")
    def test_stream_callback_on_text_complete(self, mock_popen):
        from ax_cli.runtimes.hermes.runtimes import StreamCallback

        events = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "final answer"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        cb = MagicMock(spec=StreamCallback)
        runtime = CodexCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", stream_cb=cb)

        cb.on_text_complete.assert_called_once_with("final answer")
