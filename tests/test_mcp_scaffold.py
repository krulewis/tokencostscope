"""Tests for the tokencast MCP server scaffold (US-1b.03).

Covers:
- ServerConfig construction and path resolution
- Tool input schema constants
- Tool handler stubs (happy path, edge cases, error cases)
- Server build and in-process dispatch
- Protocol smoke tests via subprocess (tagged @pytest.mark.slow)

Note on async tests: asyncio.run() is used in synchronous test wrappers to
avoid adding pytest-asyncio as a dependency. If nested-loop issues arise
(e.g. in environments that already run an event loop), switch to
pytest-asyncio.

Note on Python version: mcp requires Python >= 3.10. These tests are skipped
gracefully when mcp is not importable (e.g. when running under Python 3.9
with /usr/bin/python3). To run these tests:
    python3.11 -m pytest tests/test_mcp_scaffold.py -v
"""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Skip entire module if mcp is not available (e.g. Python 3.9 test runner)
mcp = pytest.importorskip("mcp")

# Now that mcp is confirmed importable, import project modules
from tokencast_mcp.config import ServerConfig  # noqa: E402
from tokencast_mcp.server import build_server  # noqa: E402
from tokencast_mcp.tools.estimate_cost import (  # noqa: E402
    ESTIMATE_COST_SCHEMA,
    handle_estimate_cost,
)
from tokencast_mcp.tools.get_calibration_status import (  # noqa: E402
    GET_CALIBRATION_STATUS_SCHEMA,
    handle_get_calibration_status,
)
from tokencast_mcp.tools.get_cost_history import (  # noqa: E402
    GET_COST_HISTORY_SCHEMA,
    handle_get_cost_history,
)
from tokencast_mcp.tools.report_session import (  # noqa: E402
    REPORT_SESSION_SCHEMA,
    handle_report_session,
)
from tokencast_mcp.tools.report_step_cost import (  # noqa: E402
    REPORT_STEP_COST_SCHEMA,
    handle_report_step_cost,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def _config_with_tmpdir(tmp_path: Path) -> ServerConfig:
    """Build a ServerConfig pointing at a temporary calibration directory."""
    return ServerConfig.from_args(str(tmp_path / "calibration"), None)


# ---------------------------------------------------------------------------
# TestServerConfig
# ---------------------------------------------------------------------------


class TestServerConfig:
    def test_default_calibration_dir_is_absolute(self):
        config = ServerConfig.from_args(None, None)
        assert config.calibration_dir.is_absolute()

    def test_calibration_dir_override(self, tmp_path):
        cal_dir = str(tmp_path / "my-cal")
        config = ServerConfig.from_args(cal_dir, None)
        assert config.calibration_dir == Path(cal_dir).resolve()

    def test_project_dir_none_when_not_provided(self):
        config = ServerConfig.from_args(None, None)
        assert config.project_dir is None

    def test_project_dir_set_when_provided(self, tmp_path):
        config = ServerConfig.from_args(None, str(tmp_path))
        assert config.project_dir == tmp_path.resolve()

    def test_derived_paths_are_children_of_calibration_dir(self, tmp_path):
        cal_dir = tmp_path / "calibration"
        config = ServerConfig.from_args(str(cal_dir), None)
        assert config.history_path.parent == config.calibration_dir
        assert config.factors_path.parent == config.calibration_dir
        assert config.active_estimate_path.parent == config.calibration_dir
        assert config.last_estimate_path.parent == config.calibration_dir

    def test_derived_path_names(self, tmp_path):
        config = ServerConfig.from_args(str(tmp_path / "cal"), None)
        assert config.history_path.name == "history.jsonl"
        assert config.factors_path.name == "factors.json"
        assert config.active_estimate_path.name == "active-estimate.json"
        assert config.last_estimate_path.name == "last-estimate.md"

    def test_ensure_dirs_creates_calibration_dir(self, tmp_path):
        non_existent = tmp_path / "new-cal-dir"
        assert not non_existent.exists()
        config = ServerConfig.from_args(str(non_existent), None)
        config.ensure_dirs()
        assert non_existent.exists()

    def test_from_args_does_not_create_dirs(self, tmp_path):
        """from_args should NOT create directories — only ensure_dirs() does."""
        non_existent = tmp_path / "not-yet"
        ServerConfig.from_args(str(non_existent), None)
        assert not non_existent.exists()

    def test_calibration_dir_defaults_to_project_dir_subdir(self, tmp_path):
        """When --project-dir is given but --calibration-dir is not, default
        to project_dir/calibration (M4)."""
        config = ServerConfig.from_args(None, str(tmp_path))
        assert config.calibration_dir == tmp_path.resolve() / "calibration"

    def test_calibration_dir_fallback_to_home_when_no_project_dir(self):
        config = ServerConfig.from_args(None, None)
        home = Path.home()
        assert config.calibration_dir == home / ".tokencast" / "calibration"


# ---------------------------------------------------------------------------
# TestToolSchemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def test_estimate_cost_schema_type_is_object(self):
        assert ESTIMATE_COST_SCHEMA["type"] == "object"

    def test_estimate_cost_schema_required_fields(self):
        required = set(ESTIMATE_COST_SCHEMA["required"])
        assert "size" in required
        assert "files" in required
        assert "complexity" in required

    def test_estimate_cost_schema_size_enum(self):
        size_prop = ESTIMATE_COST_SCHEMA["properties"]["size"]
        assert set(size_prop["enum"]) == {"XS", "S", "M", "L"}

    def test_estimate_cost_schema_complexity_enum(self):
        complexity_prop = ESTIMATE_COST_SCHEMA["properties"]["complexity"]
        assert set(complexity_prop["enum"]) == {"low", "medium", "high"}

    def test_report_session_schema_required_fields(self):
        required = set(REPORT_SESSION_SCHEMA["required"])
        assert "actual_cost" in required

    def test_all_tools_have_schemas_of_type_object(self):
        schemas = [
            ESTIMATE_COST_SCHEMA,
            GET_CALIBRATION_STATUS_SCHEMA,
            GET_COST_HISTORY_SCHEMA,
            REPORT_SESSION_SCHEMA,
            REPORT_STEP_COST_SCHEMA,
        ]
        for schema in schemas:
            assert isinstance(schema, dict)
            assert schema.get("type") == "object"

    def test_get_calibration_status_schema_type(self):
        assert GET_CALIBRATION_STATUS_SCHEMA["type"] == "object"

    def test_get_cost_history_schema_type(self):
        assert GET_COST_HISTORY_SCHEMA["type"] == "object"


# ---------------------------------------------------------------------------
# TestToolStubs
# ---------------------------------------------------------------------------


class TestToolStubs:
    def _make_config(self, tmp_path):
        return ServerConfig.from_args(str(tmp_path / "calibration"), None)

    # -- estimate_cost --

    def test_estimate_cost_stub_returns_correct_shape(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config)
        )
        assert "version" in result
        assert "estimate" in result
        assert "steps" in result
        assert "metadata" in result
        assert "text" in result
        assert "step_costs" in result

    def test_estimate_cost_estimate_keys(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config)
        )
        estimate = result["estimate"]
        assert "optimistic" in estimate
        assert "expected" in estimate
        assert "pessimistic" in estimate

    def test_estimate_cost_metadata_keys(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_estimate_cost({"size": "S", "files": 2, "complexity": "low"}, config)
        )
        meta = result["metadata"]
        assert meta["size"] == "S"
        assert meta["files"] == 2
        assert meta["complexity"] == "low"
        assert meta["file_brackets"] is None

    def test_estimate_cost_zero_files_is_valid(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_estimate_cost({"size": "XS", "files": 0, "complexity": "low"}, config)
        )
        assert result["metadata"]["files"] == 0

    def test_estimate_cost_empty_file_paths_is_valid(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_estimate_cost(
                {"size": "S", "files": 1, "complexity": "low", "file_paths": []},
                config,
            )
        )
        assert "estimate" in result

    def test_estimate_cost_missing_size_raises_value_error(self, tmp_path):
        config = self._make_config(tmp_path)
        with pytest.raises(ValueError, match="size"):
            asyncio.run(
                handle_estimate_cost({"files": 5, "complexity": "medium"}, config)
            )

    def test_estimate_cost_missing_files_raises_value_error(self, tmp_path):
        config = self._make_config(tmp_path)
        with pytest.raises(ValueError, match="files"):
            asyncio.run(
                handle_estimate_cost({"size": "M", "complexity": "medium"}, config)
            )

    def test_estimate_cost_missing_complexity_raises_value_error(self, tmp_path):
        config = self._make_config(tmp_path)
        with pytest.raises(ValueError, match="complexity"):
            asyncio.run(
                handle_estimate_cost({"size": "M", "files": 5}, config)
            )

    def test_estimate_cost_invalid_size_raises_value_error(self, tmp_path):
        config = self._make_config(tmp_path)
        with pytest.raises(ValueError, match="size"):
            asyncio.run(
                handle_estimate_cost(
                    {"size": "XL", "files": 5, "complexity": "medium"}, config
                )
            )

    def test_estimate_cost_negative_files_raises_value_error(self, tmp_path):
        config = self._make_config(tmp_path)
        with pytest.raises(ValueError, match="files"):
            asyncio.run(
                handle_estimate_cost(
                    {"size": "M", "files": -1, "complexity": "medium"}, config
                )
            )

    def test_estimate_cost_all_valid_sizes(self, tmp_path):
        config = self._make_config(tmp_path)
        for size in ["XS", "S", "M", "L"]:
            result = asyncio.run(
                handle_estimate_cost({"size": size, "files": 1, "complexity": "low"}, config)
            )
            assert "estimate" in result

    def test_estimate_cost_all_valid_complexities(self, tmp_path):
        config = self._make_config(tmp_path)
        for complexity in ["low", "medium", "high"]:
            result = asyncio.run(
                handle_estimate_cost(
                    {"size": "M", "files": 1, "complexity": complexity}, config
                )
            )
            assert "estimate" in result

    # -- get_calibration_status --

    def test_get_calibration_status_stub_returns_schema_version(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert result["schema_version"] == 1

    def test_get_calibration_status_stub_flag(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert "health" in result

    def test_get_calibration_status_accepts_window_param(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({"window": "7d"}, config))
        assert "health" in result

    # -- get_cost_history --

    def test_get_cost_history_stub_returns_empty_records(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_cost_history({}, config))
        assert result["records"] == []

    def test_get_cost_history_stub_flag(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_cost_history({}, config))
        assert isinstance(result["records"], list)

    def test_get_cost_history_summary_keys(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_cost_history({}, config))
        summary = result["summary"]
        assert "session_count" in summary
        assert "mean_ratio" in summary
        assert "median_ratio" in summary
        assert "pct_within_expected" in summary

    def test_get_cost_history_accepts_include_outliers(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_get_cost_history({"include_outliers": True}, config)
        )
        assert isinstance(result["records"], list)

    # -- report_session --

    def test_report_session_stub_missing_actual_cost_raises(self, tmp_path):
        config = self._make_config(tmp_path)
        with pytest.raises(ValueError, match="actual_cost"):
            asyncio.run(handle_report_session({}, config))

    def test_report_session_stub_negative_actual_cost_raises(self, tmp_path):
        config = self._make_config(tmp_path)
        with pytest.raises(ValueError, match="actual_cost"):
            asyncio.run(handle_report_session({"actual_cost": -1.0}, config))

    def test_report_session_returns_attribution_protocol_version(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_report_session({"actual_cost": 1.5}, config)
        )
        assert result["attribution_protocol_version"] == 1

    def test_report_session_stub_zero_cost_is_valid(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_report_session({"actual_cost": 0}, config)
        )
        assert "attribution_protocol_version" in result

    def test_report_session_stub_with_optional_fields(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(
            handle_report_session(
                {
                    "actual_cost": 5.25,
                    "turn_count": 100,
                    "review_cycles_actual": 3,
                    "step_actuals": {"implementation": 2.0, "qa": 0.5},
                },
                config,
            )
        )
        assert result["attribution_protocol_version"] == 1


# ---------------------------------------------------------------------------
# TestServerBuildAndDispatch
# ---------------------------------------------------------------------------


class TestServerBuildAndDispatch:
    def _make_config(self, tmp_path):
        return ServerConfig.from_args(str(tmp_path / "calibration"), None)

    def test_build_server_returns_server_instance(self, tmp_path):
        from mcp.server import Server

        config = self._make_config(tmp_path)
        server = build_server(config)
        assert isinstance(server, Server)

    def test_tools_list_returns_four_tools(self, tmp_path):
        config = self._make_config(tmp_path)
        server = build_server(config)

        # The list_tools handler is registered; call it via the SDK's
        # request_handlers dict
        from mcp.types import ListToolsRequest

        async def _get_tools():
            handler = server.request_handlers[ListToolsRequest]
            result = await handler(ListToolsRequest(method="tools/list"))
            return result

        result = asyncio.run(_get_tools())
        # result is a ServerResult wrapping a ListToolsResult
        tools = result.root.tools
        tool_names = {t.name for t in tools}
        assert len(tools) == 6
        assert tool_names == {
            "estimate_cost",
            "get_calibration_status",
            "get_cost_history",
            "report_session",
            "report_step_cost",
            "disable_telemetry",
        }

    def test_unknown_tool_returns_is_error(self, tmp_path):
        config = self._make_config(tmp_path)
        server = build_server(config)

        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            handler = server.request_handlers[CallToolRequest]
            result = await handler(
                CallToolRequest(
                    method="tools/call",
                    params=CallToolRequestParams(name="nonexistent_tool", arguments={}),
                )
            )
            return result

        result = asyncio.run(_call())
        ctr = result.root
        # The MCP SDK sets isError=False when the handler returns a TextContent
        # list rather than raising. Unknown-tool errors are reported via the
        # content text. This is a known SDK limitation — isError=False here
        # does not mean the call succeeded.
        assert ctr.isError is False
        assert "Error" in ctr.content[0].text

    def test_call_tool_with_null_arguments(self, tmp_path):
        """tools/call with arguments=null should not crash."""
        config = self._make_config(tmp_path)
        server = build_server(config)

        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            handler = server.request_handlers[CallToolRequest]
            result = await handler(
                CallToolRequest(
                    method="tools/call",
                    params=CallToolRequestParams(name="get_cost_history", arguments=None),
                )
            )
            return result

        result = asyncio.run(_call())
        # Should succeed (not crash), not an error
        ctr = result.root
        assert ctr.isError is False

    def test_tool_call_estimate_cost_in_process(self, tmp_path):
        config = self._make_config(tmp_path)
        server = build_server(config)

        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            handler = server.request_handlers[CallToolRequest]
            result = await handler(
                CallToolRequest(
                    method="tools/call",
                    params=CallToolRequestParams(
                        name="estimate_cost",
                        arguments={"size": "M", "files": 5, "complexity": "medium"},
                    ),
                )
            )
            return result

        result = asyncio.run(_call())
        ctr = result.root
        assert ctr.isError is False
        payload = json.loads(ctr.content[0].text)
        assert "estimate" in payload


# ---------------------------------------------------------------------------
# TestProtocolSmoke — subprocess-based, tagged @pytest.mark.slow
# ---------------------------------------------------------------------------

# The MCP Python SDK (mcp >= 1.0) stdio transport uses newline-delimited JSON:
# - Each message is a single JSON object followed by "\n"
# - No Content-Length headers (unlike the MCP spec's HTTP+SSE or LSP framing)
# - stdin_reader iterates `async for line in stdin` and parses each line
# - stdout_writer writes `json + "\n"` for each outbound message


def _encode_message(msg: dict) -> bytes:
    """Encode a JSON-RPC message as a newline-terminated JSON line."""
    return (json.dumps(msg) + "\n").encode("utf-8")


def _read_all_messages(stdout_bytes: bytes) -> list[dict]:
    """Parse all newline-delimited JSON-RPC messages from stdout bytes."""
    messages = []
    for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def _send_and_receive(
    tmp_path: Path,
    messages: list[dict],
    timeout: int = 10,
) -> tuple[bytes, bytes]:
    """Spawn the tokencast-mcp server as a subprocess, send messages, and
    collect stdout/stderr.

    The server is spawned with PYTHONPATH pointing at src/ so imports resolve.

    Returns:
        (stdout_bytes, stderr_bytes)
    """
    import os

    cal_dir = str(tmp_path / "smoke-cal")
    env = os.environ.copy()
    src_dir = str(REPO_ROOT / "src")
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing_path}" if existing_path else src_dir

    proc = subprocess.Popen(
        [sys.executable, "-m", "tokencast_mcp", "--calibration-dir", cal_dir],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    stdin_data = b"".join(_encode_message(m) for m in messages)
    try:
        stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise
    return stdout, stderr


# Standard MCP initialize request
_INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0.0.1"},
    },
}

_INITIALIZED_NOTIF = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
}


@pytest.mark.slow
class TestProtocolSmoke:
    def test_server_starts_and_responds_to_initialize(self, tmp_path):
        stdout, stderr = _send_and_receive(tmp_path, [_INIT_REQUEST])
        assert len(stdout) > 0, (
            f"No stdout received from server. stderr: {stderr.decode(errors='replace')}"
        )
        messages = _read_all_messages(stdout)
        assert len(messages) >= 1, f"No JSON-RPC messages parsed from: {stdout[:500]!r}"
        # Find the initialize response (id=1)
        init_resp = next(
            (m for m in messages if m.get("id") == 1), None
        )
        assert init_resp is not None, f"No response with id=1 in: {messages}"
        assert "result" in init_resp, f"No 'result' in: {init_resp}"
        assert "protocolVersion" in init_resp["result"], (
            f"No 'protocolVersion' in result: {init_resp['result']}"
        )

    def test_tools_list_via_stdio(self, tmp_path):
        tools_list_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        stdout, stderr = _send_and_receive(
            tmp_path,
            [_INIT_REQUEST, _INITIALIZED_NOTIF, tools_list_request],
        )
        messages = _read_all_messages(stdout)
        tools_resp = next(
            (m for m in messages if m.get("id") == 2), None
        )
        assert tools_resp is not None, (
            f"No tools/list response (id=2) in messages: {messages}\n"
            f"stderr: {stderr.decode(errors='replace')}"
        )
        assert "result" in tools_resp, f"Expected 'result' in: {tools_resp}"
        tools = tools_resp["result"].get("tools", [])
        assert len(tools) == 5, f"Expected 5 tools, got: {tools}"
        tool_names = {t["name"] for t in tools}
        assert tool_names == {
            "estimate_cost",
            "get_calibration_status",
            "get_cost_history",
            "report_session",
            "report_step_cost",
        }

    def test_call_unknown_tool_returns_error(self, tmp_path):
        call_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "bogus_tool", "arguments": {}},
        }
        stdout, stderr = _send_and_receive(
            tmp_path,
            [_INIT_REQUEST, _INITIALIZED_NOTIF, call_request],
        )
        messages = _read_all_messages(stdout)
        call_resp = next(
            (m for m in messages if m.get("id") == 3), None
        )
        assert call_resp is not None, (
            f"No call response (id=3) in messages: {messages}\n"
            f"stderr: {stderr.decode(errors='replace')}"
        )
        # The MCP SDK returns isError=False with error text in content rather than
        # raising a JSON-RPC error for unknown tool calls. Accept either:
        #   (a) a result whose content text contains "Error" (SDK behaviour), or
        #   (b) a JSON-RPC error object
        is_content_error = (
            "result" in call_resp
            and isinstance(call_resp["result"].get("content"), list)
            and any(
                "Error" in (item.get("text") or "")
                for item in call_resp["result"]["content"]
            )
        )
        is_jsonrpc_error = "error" in call_resp
        assert is_content_error or is_jsonrpc_error, (
            f"Expected error response for bogus tool, got: {call_resp}"
        )
