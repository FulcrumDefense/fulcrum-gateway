"""Tests for ax_cli/runtimes/hermes/runtimes/claude_cli.py

Covers the _tool_summary() pure function and ClaudeCLIRuntime.execute()
with mocked subprocess.Popen so no actual Claude CLI is needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ax_cli.runtimes.hermes.runtimes.claude_cli import ClaudeCLIRuntime, _tool_summary

# ── _tool_summary() ─────────────────────────────────────────────────────


class TestToolSummary:
    def test_read_with_path(self):
        assert _tool_summary("Read", {"file_path": "/src/main.py"}) == "Reading main.py..."

    def test_read_lowercase(self):
        assert _tool_summary("read", {"file_path": "/src/main.py"}) == "Reading main.py..."

    def test_read_no_slash(self):
        assert _tool_summary("Read", {"file_path": "file.py"}) == "Reading file.py..."

    def test_write_with_path(self):
        assert _tool_summary("Write", {"file_path": "/out/result.txt"}) == "Writing result.txt..."

    def test_write_lowercase(self):
        assert _tool_summary("write", {"file_path": "/out/result.txt"}) == "Writing result.txt..."

    def test_edit_with_path(self):
        assert _tool_summary("Edit", {"file_path": "/lib/utils.py"}) == "Editing utils.py..."

    def test_edit_lowercase(self):
        assert _tool_summary("edit", {"file_path": "/lib/utils.py"}) == "Editing utils.py..."

    def test_bash_command(self):
        result = _tool_summary("Bash", {"command": "pytest -v"})
        assert result == "Running: pytest -v..."

    def test_bash_lowercase(self):
        result = _tool_summary("bash", {"command": "ls"})
        assert result == "Running: ls..."

    def test_bash_truncates_long(self):
        long_cmd = "x" * 200
        result = _tool_summary("Bash", {"command": long_cmd})
        assert len(result) <= 75  # "Running: " + 60 chars + "..."

    def test_grep(self):
        assert _tool_summary("Grep", {"pattern": "def foo"}) == "Searching: def foo..."

    def test_grep_lowercase(self):
        assert _tool_summary("grep", {"pattern": "import os"}) == "Searching: import os..."

    def test_glob(self):
        assert _tool_summary("Glob", {"pattern": "**/*.py"}) == "Finding: **/*.py..."

    def test_glob_lowercase(self):
        assert _tool_summary("glob", {"pattern": "*.txt"}) == "Finding: *.txt..."

    def test_unknown_tool(self):
        assert _tool_summary("CustomTool", {}) == "Using CustomTool..."


# ── ClaudeCLIRuntime.execute() ────────────────────────────────────────────


class TestClaudeCLIRuntimeExecute:
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

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_result_event_captures_text(self, mock_popen):
        events = [
            json.dumps({"type": "result", "result": "Task completed.", "session_id": "sess_abc"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("do something", workdir="/tmp/test")

        assert result.text == "Task completed."
        assert result.session_id == "sess_abc"
        assert result.exit_reason == "done"

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_assistant_text_block(self, mock_popen):
        events = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "Hello from Claude"}],
                    },
                }
            )
            + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.text == "Hello from Claude"

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_content_block_delta_accumulates(self, mock_popen):
        events = [
            json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello "}}) + "\n",
            json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "world"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.text == "Hello world"

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_tool_use_counted(self, mock_popen):
        events = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Read", "input": {"file_path": "/a.py"}},
                            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        ],
                    },
                }
            )
            + "\n",
            json.dumps({"type": "result", "result": "Done"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.tool_count == 2

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_write_tool_tracks_files(self, mock_popen):
        events = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/out.txt"}},
                            {"type": "tool_use", "name": "write", "input": {"file_path": "/tmp/data.json"}},
                        ],
                    },
                }
            )
            + "\n",
            json.dumps({"type": "result", "result": "ok"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert "/tmp/out.txt" in result.files_written
        assert "/tmp/data.json" in result.files_written

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_crash_exit_reason(self, mock_popen):
        events = []
        mock_popen.return_value = self._make_proc(events, returncode=1)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.exit_reason == "crashed"

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_invalid_json_skipped(self, mock_popen):
        events = [
            "garbage line\n",
            "\n",
            json.dumps({"type": "result", "result": "ok"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.text == "ok"

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_session_resume_flag(self, mock_popen):
        events = [
            json.dumps({"type": "result", "result": "resumed"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", session_id="sess_123")

        cmd = mock_popen.call_args[0][0]
        assert "--resume" in cmd
        assert "sess_123" in cmd

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_model_flag(self, mock_popen):
        events = [
            json.dumps({"type": "result", "result": "ok"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", model="opus")

        cmd = mock_popen.call_args[0][0]
        assert "--model" in cmd
        assert "opus" in cmd

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_system_prompt_flag(self, mock_popen):
        events = [
            json.dumps({"type": "result", "result": "ok"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        runtime.execute(
            "test",
            workdir="/tmp/test",
            system_prompt="You are a code reviewer.",
        )

        cmd = mock_popen.call_args[0][0]
        assert "--append-system-prompt" in cmd
        assert "You are a code reviewer." in cmd

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_extra_args_add_dir(self, mock_popen):
        events = [
            json.dumps({"type": "result", "result": "ok"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        runtime.execute(
            "test",
            workdir="/tmp/test",
            extra_args={"add_dir": "/shared/repos"},
        )

        cmd = mock_popen.call_args[0][0]
        assert "--add-dir" in cmd
        assert "/shared/repos" in cmd

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_extra_args_allowed_tools(self, mock_popen):
        events = [
            json.dumps({"type": "result", "result": "ok"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        runtime.execute(
            "test",
            workdir="/tmp/test",
            extra_args={"allowed_tools": "Read,Write,Bash"},
        )

        cmd = mock_popen.call_args[0][0]
        assert "--allowedTools" in cmd
        assert "Read,Write,Bash" in cmd

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_stream_callback_tool_start(self, mock_popen):
        from ax_cli.runtimes.hermes.runtimes import StreamCallback

        events = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x.py"}},
                        ],
                    },
                }
            )
            + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        cb = MagicMock(spec=StreamCallback)
        runtime = ClaudeCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", stream_cb=cb)

        cb.on_tool_start.assert_called_once()
        assert cb.on_tool_start.call_args[0][0] == "Read"

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_stream_callback_text_complete(self, mock_popen):
        from ax_cli.runtimes.hermes.runtimes import StreamCallback

        events = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "answer"}]},
                }
            )
            + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        cb = MagicMock(spec=StreamCallback)
        runtime = ClaudeCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", stream_cb=cb)

        cb.on_text_complete.assert_called_with("answer")

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_stream_callback_text_delta(self, mock_popen):
        from ax_cli.runtimes.hermes.runtimes import StreamCallback

        events = [
            json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        cb = MagicMock(spec=StreamCallback)
        runtime = ClaudeCLIRuntime()
        runtime.execute("test", workdir="/tmp/test", stream_cb=cb)

        cb.on_text_delta.assert_called_with("hi")

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_result_overrides_accumulated_text(self, mock_popen):
        events = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "partial"}]},
                }
            )
            + "\n",
            json.dumps({"type": "result", "result": "final answer", "session_id": ""}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.text == "final answer"

    @patch("ax_cli.runtimes.hermes.runtimes.claude_cli.subprocess.Popen")
    def test_empty_result_keeps_accumulated(self, mock_popen):
        events = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "keep this"}]},
                }
            )
            + "\n",
            json.dumps({"type": "result", "result": "", "session_id": "sess_1"}) + "\n",
        ]
        mock_popen.return_value = self._make_proc(events)

        runtime = ClaudeCLIRuntime()
        result = runtime.execute("test", workdir="/tmp/test")

        assert result.text == "keep this"
        assert result.session_id == "sess_1"
