"""MCP server integration tests (US-1b.09b).

Tests the full MCP tool chain end-to-end through the SDK dispatch table.
These tests differ from test_mcp_scaffold.py (which tests stubs) and the
per-tool tests (which call handle_* directly).  Here every assertion flows
through the same code path a real MCP client uses:

    CallToolRequest → server.request_handlers[CallToolRequest]
                    → _DISPATCH[name](args, config)
                    → TextContent(json.dumps(result))

Sections
--------
1. Protocol tests  — initialize, tools/list, unknown tool, malformed input
2. Tool integration — real (non-stub) results for each of the 5 tools
3. End-to-end workflow — estimate → report_step_cost × 2 → report_session → history
4. Drift detection note — test_data_modules_drift.py covers pricing/heuristics drift

Running
-------
These tests require the mcp package (Python >= 3.10).  They are skipped
gracefully when running under Python 3.9 (/usr/bin/python3):

    python3.11 -m pytest tests/test_mcp_integration.py -v

To run the whole suite (drift tests, etc.) on the system Python:

    /usr/bin/python3 -m pytest tests/
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so tokencast and tokencast_mcp are importable
# when running without an editable install (python3.11 -m pytest tests/).
_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Skip entire module if mcp is not available (e.g. Python 3.9 test runner).
mcp = pytest.importorskip("mcp")

# After the importorskip guard, MCP is confirmed importable.
from mcp.types import (  # noqa: E402
    CallToolRequest,
    CallToolRequestParams,
    ListToolsRequest,
)

from tokencast_mcp.config import ServerConfig  # noqa: E402
from tokencast_mcp.server import build_server  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def _config(tmp_path: Path) -> ServerConfig:
    """Return a ServerConfig backed by a temporary calibration directory."""
    return ServerConfig.from_args(str(tmp_path / "calibration"), None)


def _config_with_project(tmp_path: Path) -> ServerConfig:
    """Return a ServerConfig with both calibration and project dirs."""
    return ServerConfig.from_args(
        str(tmp_path / "calibration"), str(tmp_path / "project")
    )


async def _list_tools(server):
    """Call tools/list and return the tool list."""
    handler = server.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list"))
    return result.root.tools


async def _call_tool(server, name: str, arguments: dict):
    """Call a tool by name and return the parsed JSON payload."""
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
    )
    ctr = result.root
    return ctr


def _run(coro):
    return asyncio.run(coro)


def _write_active_estimate(cal_dir: Path, data: dict = None) -> Path:
    """Write a minimal active-estimate.json to cal_dir."""
    cal_dir.mkdir(parents=True, exist_ok=True)
    payload = data or {
        "size": "M",
        "files": 5,
        "complexity": "medium",
        "project_type": "feature",
        "language": "python",
        "steps": ["Research Agent", "Implementation"],
        "step_count": 2,
        "review_cycles_estimated": 2,
        "expected_cost": 5.0,
        "optimistic_cost": 3.0,
        "pessimistic_cost": 15.0,
        "parallel_groups": [],
        "parallel_steps_detected": 0,
        "file_brackets": None,
        "files_measured": 0,
        "step_costs": {
            "Research Agent": 1.5,
            "Implementation": 3.0,
        },
        "continuation": False,
        "baseline_cost": 0.0,
    }
    p = cal_dir / "active-estimate.json"
    p.write_text(json.dumps(payload))
    return p


def _read_history(cal_dir: Path) -> list:
    history = cal_dir / "history.jsonl"
    if not history.exists():
        return []
    records = []
    for line in history.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


# ---------------------------------------------------------------------------
# 1. Protocol Tests
# ---------------------------------------------------------------------------


class TestProtocol:
    """Verify the MCP JSON-RPC protocol layer works correctly."""

    def test_tools_list_returns_five_tools(self, tmp_path):
        """tools/list must return exactly 5 registered tools."""
        server = build_server(_config(tmp_path))
        tools = _run(_list_tools(server))
        assert len(tools) == 5

    def test_tools_list_names_are_correct(self, tmp_path):
        """Every expected tool name must appear in tools/list."""
        server = build_server(_config(tmp_path))
        tools = _run(_list_tools(server))
        names = {t.name for t in tools}
        assert names == {
            "estimate_cost",
            "get_calibration_status",
            "get_cost_history",
            "report_session",
            "report_step_cost",
        }

    def test_each_tool_has_description(self, tmp_path):
        """Every tool must have a non-empty description string."""
        server = build_server(_config(tmp_path))
        tools = _run(_list_tools(server))
        for tool in tools:
            assert tool.description, f"Tool {tool.name!r} has no description"

    def test_each_tool_has_input_schema(self, tmp_path):
        """Every tool must carry an inputSchema."""
        server = build_server(_config(tmp_path))
        tools = _run(_list_tools(server))
        for tool in tools:
            assert tool.inputSchema is not None, (
                f"Tool {tool.name!r} is missing inputSchema"
            )

    def test_unknown_tool_returns_error_content(self, tmp_path):
        """Calling a non-existent tool must produce error text in the response.

        The server's call_tool handler catches ValueError (raised for unknown
        tool names) and returns TextContent with an error message.  The SDK
        sets isError=False in this case; the error is signalled by the text
        content beginning with "Error:".
        """
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "no_such_tool", {}))
        assert len(ctr.content) >= 1
        text = ctr.content[0].text
        assert text  # non-empty
        assert "no_such_tool" in text or "unknown" in text.lower() or "error" in text.lower()

    def test_unknown_tool_error_content_is_string(self, tmp_path):
        """Error content for unknown tool must be a non-empty string."""
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "no_such_tool", {}))
        assert len(ctr.content) >= 1
        assert ctr.content[0].text

    def test_null_arguments_treated_as_empty_dict(self, tmp_path):
        """tools/call with arguments=None must not crash."""
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_cost_history", None))
        assert ctr.isError is False

    def test_malformed_estimate_cost_missing_required_fields(self, tmp_path):
        """Missing required params (size, files, complexity) must not crash the server.

        The server returns an error message in the content rather than raising.
        """
        server = build_server(_config(tmp_path))
        # Completely empty arguments — all required fields absent.
        ctr = _run(_call_tool(server, "estimate_cost", {}))
        # The server catches the ValueError and returns isError=False with
        # an error message in content (not a protocol-level error).
        assert ctr.content[0].text  # some non-empty response exists

    def test_estimate_cost_invalid_size_value_returns_error_content(self, tmp_path):
        """Invalid enum value for 'size' must produce error content."""
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "INVALID", "files": 5, "complexity": "medium"},
            )
        )
        # Response text should mention the validation error.
        text = ctr.content[0].text.lower()
        assert "error" in text or "size" in text or "invalid" in text

    def test_report_session_without_actual_cost_returns_error_content(self, tmp_path):
        """Calling report_session without actual_cost produces error content."""
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "report_session", {}))
        text = ctr.content[0].text.lower()
        assert "error" in text or "actual_cost" in text


# ---------------------------------------------------------------------------
# 2. Tool Integration Tests
# ---------------------------------------------------------------------------


class TestEstimateCostIntegration:
    """Integration tests for estimate_cost via MCP dispatch table."""

    def test_valid_params_returns_success(self, tmp_path):
        """estimate_cost with valid params must return isError=False."""
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        assert ctr.isError is False

    def test_result_has_version(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        payload = json.loads(ctr.content[0].text)
        assert "version" in payload
        assert payload["version"]

    def test_result_has_estimate_with_three_bands(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        payload = json.loads(ctr.content[0].text)
        est = payload["estimate"]
        assert "optimistic" in est
        assert "expected" in est
        assert "pessimistic" in est
        assert est["optimistic"] > 0
        assert est["expected"] > 0
        assert est["pessimistic"] > 0

    def test_result_has_steps_list(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        payload = json.loads(ctr.content[0].text)
        assert isinstance(payload["steps"], list)
        assert len(payload["steps"]) >= 1

    def test_result_has_metadata(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "S", "files": 2, "complexity": "low"},
            )
        )
        payload = json.loads(ctr.content[0].text)
        meta = payload["metadata"]
        assert meta["size"] == "S"
        assert meta["files"] == 2
        assert meta["complexity"] == "low"

    def test_minimal_params_uses_defaults(self, tmp_path):
        """Only required fields provided — optional fields should use defaults."""
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "XS", "files": 1, "complexity": "low"},
            )
        )
        payload = json.loads(ctr.content[0].text)
        assert "estimate" in payload
        assert payload["metadata"]["file_brackets"] is None

    def test_no_stub_flag_in_result(self, tmp_path):
        """Real implementation must not include the legacy _stub marker."""
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        payload = json.loads(ctr.content[0].text)
        assert payload.get("_stub") is not True

    def test_active_estimate_json_written_to_calibration_dir(self, tmp_path):
        """estimate_cost must persist active-estimate.json via the server config."""
        config = _config(tmp_path)
        server = build_server(config)
        _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        assert config.active_estimate_path.exists()

    def test_optimistic_le_expected_le_pessimistic(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "L", "files": 10, "complexity": "high"},
            )
        )
        payload = json.loads(ctr.content[0].text)
        est = payload["estimate"]
        assert est["optimistic"] <= est["expected"] <= est["pessimistic"]

    def test_text_field_contains_tokencast_header(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        payload = json.loads(ctr.content[0].text)
        assert "tokencast estimate" in payload.get("text", "").lower()

    def test_all_valid_sizes_succeed(self, tmp_path):
        server = build_server(_config(tmp_path))
        for size in ["XS", "S", "M", "L"]:
            ctr = _run(
                _call_tool(
                    server,
                    "estimate_cost",
                    {"size": size, "files": 1, "complexity": "low"},
                )
            )
            assert ctr.isError is False, f"size={size!r} returned isError=True"


class TestGetCalibrationStatusIntegration:
    """Integration tests for get_calibration_status via MCP dispatch table."""

    def test_empty_calibration_returns_no_data(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_calibration_status", {}))
        assert ctr.isError is False
        payload = json.loads(ctr.content[0].text)
        assert payload["health"]["status"] == "no_data"

    def test_schema_version_is_1(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_calibration_status", {}))
        payload = json.loads(ctr.content[0].text)
        assert payload["schema_version"] == 1

    def test_empty_calibration_recommendations_is_list(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_calibration_status", {}))
        payload = json.loads(ctr.content[0].text)
        assert isinstance(payload["recommendations"], list)

    def test_text_summary_present_and_non_empty(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_calibration_status", {}))
        payload = json.loads(ctr.content[0].text)
        assert "text_summary" in payload
        assert len(payload["text_summary"]) > 0

    def test_window_param_accepted(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_calibration_status", {"window": "30d"}))
        assert ctr.isError is False

    def test_no_stub_flag(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_calibration_status", {}))
        payload = json.loads(ctr.content[0].text)
        assert payload.get("_stub") is not True


class TestGetCostHistoryIntegration:
    """Integration tests for get_cost_history via MCP dispatch table."""

    def test_empty_history_returns_empty_records(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_cost_history", {}))
        assert ctr.isError is False
        payload = json.loads(ctr.content[0].text)
        assert payload["records"] == []

    def test_empty_history_summary_is_zeroed(self, tmp_path):
        """Empty history → session_count=0, ratio stats are None (no data to average)."""
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_cost_history", {}))
        payload = json.loads(ctr.content[0].text)
        summary = payload["summary"]
        assert summary["session_count"] == 0
        # mean_ratio, median_ratio, and pct_within_expected are None when there
        # are no records (not 0.0 — that would imply a computed zero average).
        assert summary["mean_ratio"] is None
        assert summary["median_ratio"] is None
        assert summary["pct_within_expected"] is None

    def test_summary_has_all_keys(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_cost_history", {}))
        payload = json.loads(ctr.content[0].text)
        summary = payload["summary"]
        assert "session_count" in summary
        assert "mean_ratio" in summary
        assert "median_ratio" in summary
        assert "pct_within_expected" in summary

    def test_include_outliers_param_accepted(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(
            _call_tool(server, "get_cost_history", {"include_outliers": True})
        )
        assert ctr.isError is False

    def test_window_param_accepted(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_cost_history", {"window": "30d"}))
        assert ctr.isError is False

    def test_no_stub_flag(self, tmp_path):
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "get_cost_history", {}))
        payload = json.loads(ctr.content[0].text)
        assert payload.get("_stub") is not True

    def test_populated_history_appears_in_records(self, tmp_path):
        """Write history directly and verify it's returned via MCP tool."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir(parents=True)
        record = {
            "timestamp": "2026-03-26T12:00:00Z",
            "size": "M",
            "expected_cost": 5.0,
            "actual_cost": 6.0,
            "ratio": 1.2,
            "steps": ["Research Agent", "Implementation"],
            "attribution_method": "proportional",
        }
        (cal_dir / "history.jsonl").write_text(json.dumps(record) + "\n")
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        ctr = _run(_call_tool(server, "get_cost_history", {}))
        payload = json.loads(ctr.content[0].text)
        assert payload["summary"]["session_count"] == 1
        assert payload["summary"]["mean_ratio"] == pytest.approx(1.2)


class TestReportSessionIntegration:
    """Integration tests for report_session via MCP dispatch table."""

    def test_with_active_estimate_record_written(self, tmp_path):
        """report_session with an active estimate must write a history record."""
        cal_dir = tmp_path / "calibration"
        _write_active_estimate(cal_dir)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        ctr = _run(_call_tool(server, "report_session", {"actual_cost": 4.5}))
        assert ctr.isError is False
        payload = json.loads(ctr.content[0].text)
        assert payload["record_written"] is True

    def test_record_written_to_disk(self, tmp_path):
        """Verify the history.jsonl file is created with 1 record."""
        cal_dir = tmp_path / "calibration"
        _write_active_estimate(cal_dir)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        _run(_call_tool(server, "report_session", {"actual_cost": 4.5}))
        records = _read_history(cal_dir)
        assert len(records) == 1

    def test_active_estimate_cleaned_up_after_report(self, tmp_path):
        """active-estimate.json must be removed after report_session."""
        cal_dir = tmp_path / "calibration"
        _write_active_estimate(cal_dir)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        _run(_call_tool(server, "report_session", {"actual_cost": 3.0}))
        assert not (cal_dir / "active-estimate.json").exists()

    def test_attribution_protocol_version_is_1(self, tmp_path):
        cal_dir = tmp_path / "calibration"
        _write_active_estimate(cal_dir)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        ctr = _run(_call_tool(server, "report_session", {"actual_cost": 3.0}))
        payload = json.loads(ctr.content[0].text)
        assert payload["attribution_protocol_version"] == 1

    def test_no_active_estimate_still_writes_record_with_warning(self, tmp_path):
        """No active-estimate.json → record written with a warning."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir(parents=True)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        ctr = _run(_call_tool(server, "report_session", {"actual_cost": 2.0}))
        payload = json.loads(ctr.content[0].text)
        assert payload["record_written"] is True
        assert "warning" in payload

    def test_zero_cost_produces_no_record(self, tmp_path):
        """actual_cost=0 must not write a history record."""
        cal_dir = tmp_path / "calibration"
        _write_active_estimate(cal_dir)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        ctr = _run(_call_tool(server, "report_session", {"actual_cost": 0.0}))
        payload = json.loads(ctr.content[0].text)
        assert payload["record_written"] is False

    def test_missing_actual_cost_produces_error_content(self, tmp_path):
        """Missing required actual_cost must produce error content, not a crash."""
        server = build_server(_config(tmp_path))
        ctr = _run(_call_tool(server, "report_session", {}))
        text = ctr.content[0].text.lower()
        assert "error" in text or "actual_cost" in text


class TestReportStepCostIntegration:
    """Integration tests for report_step_cost via MCP dispatch table."""

    def test_valid_step_cost_returns_success(self, tmp_path):
        cal_dir = tmp_path / "calibration"
        _write_active_estimate(cal_dir)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        ctr = _run(
            _call_tool(
                server,
                "report_step_cost",
                {"step_name": "Research Agent", "cost": 1.0},
            )
        )
        assert ctr.isError is False
        payload = json.loads(ctr.content[0].text)
        assert "error" not in payload
        assert payload["attribution_protocol_version"] == 1

    def test_step_cost_accumulates_across_calls(self, tmp_path):
        """Two calls for the same step must accumulate cost."""
        cal_dir = tmp_path / "calibration"
        _write_active_estimate(cal_dir)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        _run(
            _call_tool(
                server,
                "report_step_cost",
                {"step_name": "Research Agent", "cost": 0.5},
            )
        )
        ctr2 = _run(
            _call_tool(
                server,
                "report_step_cost",
                {"step_name": "Research Agent", "cost": 0.5},
            )
        )
        payload2 = json.loads(ctr2.content[0].text)
        assert payload2["cumulative_step_cost"] == pytest.approx(1.0)

    def test_step_cost_persists_accumulator_to_disk(self, tmp_path):
        """After report_step_cost, an accumulator JSON file must exist on disk."""
        import hashlib

        cal_dir = tmp_path / "calibration"
        _write_active_estimate(cal_dir)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        _run(
            _call_tool(
                server,
                "report_step_cost",
                {"step_name": "Implementation", "cost": 2.0},
            )
        )
        h = hashlib.md5(str(cal_dir / "active-estimate.json").encode()).hexdigest()[:12]
        acc_file = cal_dir / f"{h}-step-accumulator.json"
        assert acc_file.exists()

    def test_no_active_estimate_produces_error_content(self, tmp_path):
        """Calling report_step_cost without active-estimate.json must return error."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir(parents=True)
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)
        ctr = _run(
            _call_tool(
                server,
                "report_step_cost",
                {"step_name": "Research Agent", "cost": 1.0},
            )
        )
        # The handler raises ValueError which the server catches into error content.
        text = ctr.content[0].text.lower()
        assert "error" in text or "active" in text or "estimate" in text


# ---------------------------------------------------------------------------
# 3. End-to-End Workflow Test
# ---------------------------------------------------------------------------


class TestEndToEndWorkflow:
    """Full workflow: estimate → report_step_cost × 2 → report_session → history."""

    def test_full_workflow_produces_history_record(self, tmp_path):
        """estimate_cost, then two report_step_cost calls, then report_session.

        Verifies:
        - estimate_cost writes active-estimate.json
        - report_step_cost accumulates step costs
        - report_session writes a history record and cleans up active-estimate.json
        - history record has mcp attribution method and correct step_actuals
        """
        cal_dir = tmp_path / "calibration"
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)

        # Step 1: estimate_cost → creates active-estimate.json
        ctr1 = _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        assert ctr1.isError is False
        assert config.active_estimate_path.exists(), (
            "active-estimate.json must exist after estimate_cost"
        )

        # Step 2a: report_step_cost for first pipeline step
        ctr2a = _run(
            _call_tool(
                server,
                "report_step_cost",
                {"step_name": "Research Agent", "cost": 1.20},
            )
        )
        assert ctr2a.isError is False
        payload2a = json.loads(ctr2a.content[0].text)
        assert payload2a.get("cumulative_step_cost") == pytest.approx(1.20)

        # Step 2b: report_step_cost for second pipeline step
        ctr2b = _run(
            _call_tool(
                server,
                "report_step_cost",
                {"step_name": "Implementation", "cost": 3.50},
            )
        )
        assert ctr2b.isError is False
        payload2b = json.loads(ctr2b.content[0].text)
        # total accumulated across both steps
        assert payload2b.get("total_session_accumulated") == pytest.approx(4.70)

        # Step 3: report_session → uses accumulated step costs, writes history
        actual_cost = 5.0
        ctr3 = _run(
            _call_tool(
                server,
                "report_session",
                {"actual_cost": actual_cost, "turn_count": 42},
            )
        )
        assert ctr3.isError is False
        payload3 = json.loads(ctr3.content[0].text)
        assert payload3["record_written"] is True
        assert payload3["attribution_protocol_version"] == 1

        # active-estimate.json must be cleaned up
        assert not config.active_estimate_path.exists(), (
            "active-estimate.json must be removed after report_session"
        )

        # History must contain exactly one record
        records = _read_history(cal_dir)
        assert len(records) == 1, f"Expected 1 history record, got {len(records)}"

        rec = records[0]
        assert rec["actual_cost"] == pytest.approx(actual_cost)
        assert rec["turn_count"] == 42

        # With accumulated step costs, attribution should be "mcp"
        assert rec["attribution_method"] == "mcp", (
            f"Expected mcp attribution, got: {rec['attribution_method']!r}"
        )

        # step_actuals must include the two reported steps
        step_actuals = rec.get("step_actuals", {})
        assert "Research Agent" in step_actuals
        assert "Implementation" in step_actuals
        assert step_actuals["Research Agent"] == pytest.approx(1.20)
        assert step_actuals["Implementation"] == pytest.approx(3.50)

    def test_second_estimate_clears_stale_accumulator(self, tmp_path):
        """A second estimate_cost call must clear any stale accumulator file."""
        import hashlib

        cal_dir = tmp_path / "calibration"
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)

        # First estimate + one step report
        _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "S", "files": 2, "complexity": "low"},
            )
        )
        _run(
            _call_tool(
                server,
                "report_step_cost",
                {"step_name": "Research Agent", "cost": 0.5},
            )
        )
        h1 = hashlib.md5(
            str(cal_dir / "active-estimate.json").encode()
        ).hexdigest()[:12]
        acc_file = cal_dir / f"{h1}-step-accumulator.json"
        assert acc_file.exists(), "Accumulator must exist after report_step_cost"

        # Second estimate — should clean up the previous accumulator
        _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "S", "files": 2, "complexity": "low"},
            )
        )
        assert not acc_file.exists(), (
            "Stale accumulator must be deleted when a new estimate is made"
        )

    def test_workflow_get_calibration_status_after_session(self, tmp_path):
        """After one reported session, get_calibration_status must show 1 record."""
        cal_dir = tmp_path / "calibration"
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)

        # estimate → report → session
        _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "M", "files": 5, "complexity": "medium"},
            )
        )
        _run(_call_tool(server, "report_session", {"actual_cost": 6.0}))

        # Check status
        ctr = _run(_call_tool(server, "get_calibration_status", {}))
        payload = json.loads(ctr.content[0].text)
        # 1 record is below the "active" threshold (needs 3+) — expect "collecting"
        assert payload["window"]["total_records"] == 1
        assert payload["health"]["status"] in ("collecting", "no_data")

    def test_workflow_get_cost_history_after_session(self, tmp_path):
        """After one reported session, get_cost_history must show it."""
        cal_dir = tmp_path / "calibration"
        config = ServerConfig.from_args(str(cal_dir), None)
        server = build_server(config)

        # estimate → report
        _run(
            _call_tool(
                server,
                "estimate_cost",
                {"size": "S", "files": 3, "complexity": "low"},
            )
        )
        _run(_call_tool(server, "report_session", {"actual_cost": 1.5}))

        # Query history
        ctr = _run(_call_tool(server, "get_cost_history", {}))
        payload = json.loads(ctr.content[0].text)
        assert payload["summary"]["session_count"] == 1


# ---------------------------------------------------------------------------
# 4. Drift Detection Note
# ---------------------------------------------------------------------------
#
# The drift detection tests (Python pricing/heuristics modules vs markdown
# sources) live in tests/test_data_modules_drift.py and run as part of the
# standard pytest suite:
#
#   /usr/bin/python3 -m pytest tests/test_data_modules_drift.py -v
#
# No additional tests are needed here — the existing drift suite covers all
# table values in references/pricing.md and references/heuristics.md.
#
# To verify CI includes them: look for test_data_modules_drift.py in the
# test output of a full /usr/bin/python3 -m pytest tests/ run.


class TestDriftDetectionIntegration:
    """Smoke-test that the drift detection module is importable and runs.

    This class does not re-implement the drift checks — it verifies that
    the drift module itself can be imported and its test classes are visible,
    confirming the module will be collected by pytest in CI.
    """

    def test_drift_module_is_importable(self):
        """test_data_modules_drift.py must be importable without errors."""
        import importlib.util

        drift_path = Path(__file__).parent / "test_data_modules_drift.py"
        assert drift_path.exists(), (
            f"Expected drift test file at {drift_path}"
        )
        spec = importlib.util.spec_from_file_location(
            "test_data_modules_drift", drift_path
        )
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        # Verify key test classes are present
        assert hasattr(mod, "TestPricingDrift"), (
            "TestPricingDrift class not found in test_data_modules_drift.py"
        )
        assert hasattr(mod, "TestHeuristicsDrift"), (
            "TestHeuristicsDrift class not found in test_data_modules_drift.py"
        )

    def test_pricing_module_importable(self):
        """tokencast.pricing must be importable (guards against broken installs)."""
        import tokencast.pricing as pricing
        assert hasattr(pricing, "MODEL_PRICES")
        assert hasattr(pricing, "LAST_UPDATED")

    def test_heuristics_module_importable(self):
        """tokencast.heuristics must be importable."""
        import tokencast.heuristics as heuristics
        assert hasattr(heuristics, "COMPLEXITY_MULTIPLIERS")
        assert hasattr(heuristics, "BAND_MULTIPLIERS")
