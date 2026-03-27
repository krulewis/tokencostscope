# Run with: /usr/bin/python3 -m pytest tests/test_report_session.py
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""Integration tests for report_session() in api.py (US-1c.03).

Covers:
- Tier 1 (session-only, proportional attribution)
- Tier 2 (with step actuals via report_step_cost accumulation or call-time)
- No-estimate error/warning cases
- Validation errors
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

import pytest

# Ensure src/ is on path
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tokencast.api import report_session, report_step_cost, estimate_cost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_active_estimate(cal_dir: Path, data: Optional[dict] = None) -> Path:
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


def _accumulator_hash(cal_dir: Path) -> str:
    active = cal_dir / "active-estimate.json"
    return hashlib.md5(str(active).encode()).hexdigest()[:12]


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
# TestReportSessionTier1Proportional
# ---------------------------------------------------------------------------


class TestReportSessionTier1Proportional:
    """Session-only (no step actuals) → proportional attribution."""

    def test_tier1_writes_history_record(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_session({"actual_cost": 2.0}, calibration_dir=cal)
        records = _read_history(cal)
        assert len(records) == 1
        assert records[0]["attribution_method"] == "proportional"

    def test_tier1_response_fields(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session({"actual_cost": 2.0}, calibration_dir=cal)
        assert result["attribution_protocol_version"] == 1
        assert result["record_written"] is True
        assert result["attribution_method"] == "proportional"
        assert result["actual_cost"] == pytest.approx(2.0)

    def test_tier1_clears_accumulator(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        # Create an accumulator file (as if report_step_cost was called)
        h = _accumulator_hash(cal)
        acc_file = cal / f"{h}-step-accumulator.json"
        acc_file.write_text(
            json.dumps({
                "attribution_protocol_version": 1,
                "steps": {"Research Agent": 1.0},
                "last_updated": "2026-01-01T00:00:00Z",
            })
        )
        report_session({"actual_cost": 2.0}, calibration_dir=cal)
        assert not acc_file.exists(), "Accumulator file should be deleted after report_session"

    def test_tier1_clears_active_estimate(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_session({"actual_cost": 2.0}, calibration_dir=cal)
        assert not (cal / "active-estimate.json").exists(), (
            "active-estimate.json should be deleted after report_session"
        )

    def test_tier1_zero_cost_no_record(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session({"actual_cost": 0.0}, calibration_dir=cal)
        assert result["record_written"] is False
        assert "warning" in result
        records = _read_history(cal)
        assert len(records) == 0

    def test_tier1_boundary_cost_record_written(self, tmp_path):
        """actual_cost just above 0.001 → record IS written."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session({"actual_cost": 0.0011}, calibration_dir=cal)
        assert result["record_written"] is True

    def test_tier1_record_has_correct_fields(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_session({"actual_cost": 3.5, "turn_count": 50}, calibration_dir=cal)
        records = _read_history(cal)
        r = records[0]
        assert r["actual_cost"] == pytest.approx(3.5)
        assert r["turn_count"] == 50
        assert r["size"] == "M"
        assert r["steps"] == ["Research Agent", "Implementation"]


# ---------------------------------------------------------------------------
# TestReportSessionTier2Mcp
# ---------------------------------------------------------------------------


class TestReportSessionTier2Mcp:
    """Session with step actuals → MCP attribution."""

    def test_tier2_writes_mcp_record(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_step_cost(
            {"step_name": "Research Agent", "cost": 1.2}, calibration_dir=cal
        )
        report_session({"actual_cost": 3.0}, calibration_dir=cal)
        records = _read_history(cal)
        assert len(records) == 1
        r = records[0]
        assert r["attribution_method"] == "mcp"
        assert r["step_actuals"] is not None
        assert r["step_actuals"]["Research Agent"] == pytest.approx(1.2)

    def test_tier2_call_time_step_actuals(self, tmp_path):
        """Passing step_actuals directly at call time → mcp attribution."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 3.0, "step_actuals": {"Implementation": 2.0}},
            calibration_dir=cal,
        )
        assert result["attribution_method"] == "mcp"
        assert result["step_actuals"]["Implementation"] == pytest.approx(2.0)

    def test_tier2_call_time_overrides_accumulated(self, tmp_path):
        """Call-time step_actuals win over accumulated values for duplicate keys."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        # Accumulate 1.0 for Implementation
        report_step_cost({"step_name": "Implementation", "cost": 1.0}, calibration_dir=cal)
        # Call-time provides 2.5 for the same step
        result = report_session(
            {
                "actual_cost": 4.0,
                "step_actuals": {"Implementation": 2.5},
            },
            calibration_dir=cal,
        )
        assert result["step_actuals"]["Implementation"] == pytest.approx(2.5)

    def test_tier2_response_attribution_mcp(self, tmp_path):
        """Response attribution_method == 'mcp' when step actuals are present."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 0.8}, calibration_dir=cal)
        result = report_session({"actual_cost": 2.5}, calibration_dir=cal)
        assert result["attribution_method"] == "mcp"

    def test_tier2_accumulated_and_calltime_merged(self, tmp_path):
        """Accumulated and call-time step actuals are merged (non-overlapping keys)."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal)
        result = report_session(
            {
                "actual_cost": 4.0,
                "step_actuals": {"Implementation": 2.0},
            },
            calibration_dir=cal,
        )
        actuals = result["step_actuals"]
        assert actuals["Research Agent"] == pytest.approx(1.0)
        assert actuals["Implementation"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# TestReportSessionNoEstimate
# ---------------------------------------------------------------------------


class TestReportSessionNoEstimate:
    """Behavior when active-estimate.json is absent."""

    def test_no_estimate_no_last_estimate_md(self, tmp_path):
        """No active estimate, no last-estimate.md → warning, but record IS written."""
        cal = tmp_path / "calibration"
        cal.mkdir(parents=True)
        # No active-estimate.json, no last-estimate.md
        result = report_session({"actual_cost": 1.0}, calibration_dir=cal)
        assert result["record_written"] is True
        assert "warning" in result
        assert "no_active_estimate" in result["warning"]
        assert result["attribution_method"] == "proportional"

    def test_no_estimate_stale_accumulator_discarded(self, tmp_path):
        """Accumulator exists but no active estimate → accumulator discarded."""
        cal = tmp_path / "calibration"
        cal.mkdir(parents=True)

        # Create a stale accumulator (with no matching active estimate)
        # The accumulator hash is based on the active-estimate.json path, but we need
        # to create an accumulator by a known name since we can't derive the hash
        # without active-estimate.json existing (the function returns None).
        # Instead: write active estimate, record step cost, then delete active estimate.
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal)
        # Delete active estimate to simulate stale accumulator scenario
        (cal / "active-estimate.json").unlink()

        result = report_session({"actual_cost": 1.5}, calibration_dir=cal)
        assert "warning" in result
        assert "stale_accumulator_discarded" in result["warning"]
        # step_actuals should be None (proportional) since accumulator was discarded
        assert result["step_actuals"] is None


# ---------------------------------------------------------------------------
# TestReportSessionValidation
# ---------------------------------------------------------------------------


class TestReportSessionValidation:
    """Validation error cases."""

    def test_missing_actual_cost(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        result = report_session({}, calibration_dir=cal)
        assert "error" in result
        assert result["error"] == "missing_actual_cost"

    def test_negative_actual_cost(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        result = report_session({"actual_cost": -1.0}, calibration_dir=cal)
        assert "error" in result
        assert result["error"] == "invalid_cost"

    def test_negative_step_actual_value(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        result = report_session(
            {
                "actual_cost": 2.0,
                "step_actuals": {"Research Agent": -0.5},
            },
            calibration_dir=cal,
        )
        assert "error" in result
        assert result["error"] == "invalid_step_actual"

    def test_non_numeric_actual_cost(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        result = report_session({"actual_cost": "not-a-number"}, calibration_dir=cal)
        assert "error" in result

    def test_review_cycles_actual_passed_through(self, tmp_path):
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        report_session(
            {"actual_cost": 2.0, "review_cycles_actual": 4}, calibration_dir=cal
        )
        records = _read_history(cal)
        assert len(records) == 1
        assert records[0]["review_cycles_actual"] == 4
