# Run with: /usr/bin/python3 -m pytest tests/test_report_session_tool.py
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""MCP tool layer tests for report_session (US-1b.07).

Covers:
- Tier 1: session-only reporting (actual_cost only, proportional attribution)
- Tier 2: with step_actuals from accumulated report_step_cost calls
- Merge behavior: call-time step_actuals override accumulated values
- History record written with correct schema
- Cleanup: active-estimate.json and step-accumulator removed after recording
- Error: actual_cost = 0 (record_written=False, warning present)
- Error: negative actual_cost raises ValueError
- Error: missing actual_cost raises ValueError
- MCP handler delegates to API with config.calibration_dir
- attribution_protocol_version == 1 in every response
"""

import asyncio
import json
import hashlib
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — ensure src/ is importable
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path("/Volumes/Macintosh HD2/Cowork/Projects/costscope")
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# ---------------------------------------------------------------------------
# Conditional import of MCP layer
# ---------------------------------------------------------------------------

try:
    from tokencast_mcp.config import ServerConfig
    from tokencast_mcp.tools.report_session import (
        REPORT_SESSION_SCHEMA,
        handle_report_session,
    )
    from tokencast_mcp.tools.report_step_cost import handle_report_step_cost
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

from tokencast.api import report_session, report_step_cost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_active_estimate(cal_dir: pathlib.Path, data: dict = None) -> pathlib.Path:
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


def _accumulator_hash(cal_dir: pathlib.Path) -> str:
    active = cal_dir / "active-estimate.json"
    return hashlib.md5(str(active).encode()).hexdigest()[:12]


def _read_history(cal_dir: pathlib.Path) -> list:
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


def _make_config(cal_dir: pathlib.Path) -> "ServerConfig":
    return ServerConfig.from_args(str(cal_dir), None)


# ---------------------------------------------------------------------------
# TestReportSessionSchema
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not available")
class TestReportSessionSchema:
    def test_schema_has_actual_cost_required(self):
        assert "actual_cost" in REPORT_SESSION_SCHEMA.get("required", [])

    def test_schema_has_step_actuals_property(self):
        assert "step_actuals" in REPORT_SESSION_SCHEMA["properties"]

    def test_schema_has_turn_count_property(self):
        assert "turn_count" in REPORT_SESSION_SCHEMA["properties"]

    def test_schema_has_review_cycles_actual_property(self):
        assert "review_cycles_actual" in REPORT_SESSION_SCHEMA["properties"]

    def test_schema_no_additional_properties(self):
        assert REPORT_SESSION_SCHEMA.get("additionalProperties") is False


# ---------------------------------------------------------------------------
# TestReportSessionToolTier1
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not available")
class TestReportSessionToolTier1:
    """Tier 1 (session-only): no step actuals → proportional attribution."""

    def test_tier1_record_written(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 3.0}, config))
        assert result["record_written"] is True

    def test_tier1_attribution_protocol_version(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 3.0}, config))
        assert result["attribution_protocol_version"] == 1

    def test_tier1_attribution_method_proportional(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 3.0}, config))
        assert result["attribution_method"] == "proportional"

    def test_tier1_actual_cost_in_response(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 4.5}, config))
        assert result["actual_cost"] == pytest.approx(4.5)

    def test_tier1_history_record_written_to_disk(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        records = _read_history(cal)
        assert len(records) == 1

    def test_tier1_history_record_has_correct_fields(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        asyncio.run(handle_report_session({"actual_cost": 2.0, "turn_count": 30}, config))
        records = _read_history(cal)
        r = records[0]
        assert r["size"] == "M"
        assert r["actual_cost"] == pytest.approx(2.0)
        assert r["turn_count"] == 30
        assert r["attribution_method"] == "proportional"

    def test_tier1_cleans_up_active_estimate(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert not (cal / "active-estimate.json").exists()

    def test_tier1_cleans_up_accumulator(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        # Create an accumulator file directly
        h = _accumulator_hash(cal)
        acc_file = cal / f"{h}-step-accumulator.json"
        acc_file.write_text(json.dumps({
            "attribution_protocol_version": 1,
            "steps": {"Research Agent": 0.5},
            "last_updated": "2026-01-01T00:00:00Z",
        }))
        asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert not acc_file.exists()

    def test_tier1_no_stub_marker(self, tmp_path):
        """Response must not contain the legacy _stub marker."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 1.5}, config))
        assert "_stub" not in result

    def test_tier1_uses_config_calibration_dir(self, tmp_path):
        """Handler uses config.calibration_dir, not the default ~/.tokencast."""
        cal_a = tmp_path / "cal_a"
        cal_b = tmp_path / "cal_b"
        _make_active_estimate(cal_a)
        config_a = _make_config(cal_a)
        asyncio.run(handle_report_session({"actual_cost": 1.0}, config_a))
        # Record should be in cal_a, not cal_b
        assert len(_read_history(cal_a)) == 1
        assert len(_read_history(cal_b)) == 0


# ---------------------------------------------------------------------------
# TestReportSessionToolTier2
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not available")
class TestReportSessionToolTier2:
    """Tier 2: with accumulated step_actuals → mcp attribution."""

    def test_tier2_mcp_attribution_from_report_step_cost(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        # Accumulate via report_step_cost (api layer, same calibration_dir)
        report_step_cost({"step_name": "Research Agent", "cost": 1.2}, calibration_dir=cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 3.0}, config))
        assert result["attribution_method"] == "mcp"

    def test_tier2_step_actuals_in_response(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 3.0}, config))
        assert result["step_actuals"] is not None
        assert result["step_actuals"]["Research Agent"] == pytest.approx(1.0)

    def test_tier2_call_time_step_actuals(self, tmp_path):
        """Passing step_actuals directly at call time → mcp attribution."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(
            handle_report_session(
                {"actual_cost": 3.0, "step_actuals": {"Implementation": 2.0}},
                config,
            )
        )
        assert result["attribution_method"] == "mcp"
        assert result["step_actuals"]["Implementation"] == pytest.approx(2.0)

    def test_tier2_call_time_overrides_accumulated(self, tmp_path):
        """Call-time step_actuals take precedence over accumulated values."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        # Accumulate 1.0 for Implementation via api
        report_step_cost({"step_name": "Implementation", "cost": 1.0}, calibration_dir=cal)
        config = _make_config(cal)
        result = asyncio.run(
            handle_report_session(
                {"actual_cost": 4.0, "step_actuals": {"Implementation": 2.5}},
                config,
            )
        )
        assert result["step_actuals"]["Implementation"] == pytest.approx(2.5)

    def test_tier2_accumulated_and_calltime_merged(self, tmp_path):
        """Non-overlapping accumulated and call-time keys are merged."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 0.9}, calibration_dir=cal)
        config = _make_config(cal)
        result = asyncio.run(
            handle_report_session(
                {"actual_cost": 4.0, "step_actuals": {"Implementation": 2.1}},
                config,
            )
        )
        actuals = result["step_actuals"]
        assert actuals["Research Agent"] == pytest.approx(0.9)
        assert actuals["Implementation"] == pytest.approx(2.1)

    def test_tier2_history_record_attribution_mcp(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 0.8}, calibration_dir=cal)
        config = _make_config(cal)
        asyncio.run(handle_report_session({"actual_cost": 2.5}, config))
        records = _read_history(cal)
        assert records[0]["attribution_method"] == "mcp"


# ---------------------------------------------------------------------------
# TestReportSessionToolErrors
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not available")
class TestReportSessionToolErrors:
    """Error and edge-case handling at the MCP handler layer."""

    def test_missing_actual_cost_raises_value_error(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        config = _make_config(cal)
        with pytest.raises(ValueError):
            asyncio.run(handle_report_session({}, config))

    def test_negative_actual_cost_raises_value_error(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        config = _make_config(cal)
        with pytest.raises(ValueError):
            asyncio.run(handle_report_session({"actual_cost": -1.0}, config))

    def test_negative_step_actual_raises_value_error(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        config = _make_config(cal)
        with pytest.raises(ValueError):
            asyncio.run(
                handle_report_session(
                    {"actual_cost": 2.0, "step_actuals": {"Research Agent": -0.5}},
                    config,
                )
            )

    def test_zero_cost_no_record_written(self, tmp_path):
        """actual_cost=0 → record_written=False, warning present, no ValueError."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 0.0}, config))
        assert result["record_written"] is False
        assert "warning" in result
        assert _read_history(cal) == []

    def test_cost_at_threshold_no_record(self, tmp_path):
        """actual_cost=0.001 is not > 0.001 threshold → no record written."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 0.001}, config))
        assert result["record_written"] is False

    def test_cost_just_above_threshold_record_written(self, tmp_path):
        """actual_cost=0.0011 is just above 0.001 → record IS written."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 0.0011}, config))
        assert result["record_written"] is True

    def test_no_active_estimate_warning_in_response(self, tmp_path):
        """No active-estimate.json → warning key present, record IS written."""
        cal = tmp_path / "calibration"
        cal.mkdir(parents=True)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 1.5}, config))
        assert result["record_written"] is True
        assert "warning" in result
        assert "no_active_estimate" in result["warning"]

    def test_attribution_protocol_version_always_1(self, tmp_path):
        """attribution_protocol_version must be 1 in every successful response."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert result["attribution_protocol_version"] == 1

    def test_review_cycles_actual_passed_through_to_record(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        config = _make_config(cal)
        asyncio.run(
            handle_report_session(
                {"actual_cost": 2.0, "review_cycles_actual": 3}, config
            )
        )
        records = _read_history(cal)
        assert records[0]["review_cycles_actual"] == 3
