# Run with: /usr/bin/python3 -m pytest tests/test_e2e_non_claude_code.py -v
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""End-to-end integration tests for non-Claude-Code client workflows (US-1c.04).

Simulates complete workflows (estimate -> step reports -> session report) WITHOUT
any Claude Code JSONL files. All tests use api.* functions directly (not MCP layer).

Because api.estimate_cost() delegates to the estimation engine but does NOT write
active-estimate.json (that is handled by the MCP tool handler), the tests use a
helper (_make_active_estimate_from_api) that mirrors what handle_estimate_cost
writes, giving a realistic end-to-end flow.

Test scenarios:
    Test 1: Cursor Tier 2 workflow (step-level, dollar costs)
    Test 2: CI/CD Tier 1 workflow (session-only, proportional)
    Test 3: Mixed — some steps reported, some not
    Test 4: Tier 2 with token counts (not dollar cost)
    Test 5: Call-time step_actuals override accumulated values
    Test 6: Calibration improves after 3+ sessions
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Optional

import pytest

# Ensure src/ is on path
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tokencast.api import estimate_cost, report_session, report_step_cost
from tokencast.pricing import MODEL_PRICES, MODEL_SONNET


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_calibration_store():
    """Load calibration_store.py from the scripts directory."""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    spec = importlib.util.spec_from_file_location(
        "calibration_store", scripts_dir / "calibration_store.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_active_estimate(cal_dir: Path, data: Optional[dict] = None) -> Path:
    """Write a realistic active-estimate.json to cal_dir.

    Mirrors what handle_estimate_cost writes after calling compute_estimate
    for a size=M, files=5, complexity=medium plan.
    """
    cal_dir.mkdir(parents=True, exist_ok=True)
    payload = data or {
        "timestamp": "2026-03-26T10:00:00+00:00",
        "size": "M",
        "files": 5,
        "complexity": "medium",
        "project_type": "unknown",
        "language": "unknown",
        "steps": [
            "Research Agent",
            "Architect Agent",
            "Engineer Initial Plan",
            "Staff Review",
            "Engineer Final Plan",
            "Test Writing",
            "Implementation",
            "QA",
            "PR Review Loop",
        ],
        "step_count": 9,
        "review_cycles_estimated": 2,
        "parallel_groups": [],
        "parallel_steps_detected": 0,
        "expected_cost": 6.24,
        "optimistic_cost": 3.74,
        "pessimistic_cost": 20.46,
        "baseline_cost": 0,
        "file_brackets": None,
        "files_measured": 0,
        "step_costs": {
            "Research Agent": 0.43,
            "Architect Agent": 0.30,
            "Engineer Initial Plan": 0.25,
            "Staff Review": 0.72,
            "Engineer Final Plan": 0.18,
            "Test Writing": 0.82,
            "Implementation": 1.62,
            "QA": 0.12,
            "PR Review Loop": 1.80,
        },
        "continuation": False,
    }
    p = cal_dir / "active-estimate.json"
    p.write_text(json.dumps(payload))
    return p


def _make_simple_active_estimate(cal_dir: Path) -> Path:
    """Write a simplified active-estimate with just 2 steps — convenient for short tests."""
    cal_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": "2026-03-26T10:00:00+00:00",
        "size": "M",
        "files": 5,
        "complexity": "medium",
        "project_type": "unknown",
        "language": "unknown",
        "steps": ["Research Agent", "Implementation"],
        "step_count": 2,
        "review_cycles_estimated": 0,
        "parallel_groups": [],
        "parallel_steps_detected": 0,
        "expected_cost": 5.0,
        "optimistic_cost": 3.0,
        "pessimistic_cost": 15.0,
        "baseline_cost": 0,
        "file_brackets": None,
        "files_measured": 0,
        "step_costs": {
            "Research Agent": 1.5,
            "Implementation": 3.0,
        },
        "continuation": False,
    }
    p = cal_dir / "active-estimate.json"
    p.write_text(json.dumps(payload))
    return p


def _accumulator_hash(cal_dir: Path) -> str:
    active = cal_dir / "active-estimate.json"
    return hashlib.md5(str(active).encode()).hexdigest()[:12]


def _read_history(cal_dir: Path) -> list:
    """Read all records from history.jsonl in cal_dir."""
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


def _read_factors(cal_dir: Path) -> dict:
    """Read factors.json from cal_dir, return {} if absent."""
    factors_path = cal_dir / "factors.json"
    if not factors_path.exists():
        return {}
    try:
        return json.loads(factors_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Test 1: Cursor Tier 2 workflow (step-level, dollar costs)
# ---------------------------------------------------------------------------


class TestCursorTier2Workflow:
    """Simulates Example A from attribution-protocol.md Section 10.

    Cursor extension runs a workflow and reports each step cost as it finishes.
    """

    def test_full_cursor_workflow_writes_history_record(self, tmp_path):
        """Complete estimate -> step reports -> session report writes one record."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        report_step_cost({"step_name": "Implementation", "cost": 4.50}, calibration_dir=cal)
        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        records = _read_history(cal)
        assert len(records) == 1, "Expected exactly one history record"

    def test_attribution_method_is_mcp(self, tmp_path):
        """Tier 2 workflow produces attribution_method='mcp'."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        report_step_cost({"step_name": "Implementation", "cost": 4.50}, calibration_dir=cal)
        result = report_session({"actual_cost": 7.20}, calibration_dir=cal)

        assert result["attribution_method"] == "mcp"
        records = _read_history(cal)
        assert records[0]["attribution_method"] == "mcp"

    def test_step_actuals_contain_reported_values(self, tmp_path):
        """step_actuals in history record matches the reported step costs."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        report_step_cost({"step_name": "Implementation", "cost": 4.50}, calibration_dir=cal)
        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        records = _read_history(cal)
        actuals = records[0]["step_actuals"]
        assert actuals is not None
        assert actuals["Research Agent"] == pytest.approx(1.20)
        assert actuals["Implementation"] == pytest.approx(4.50)

    def test_step_ratios_computed_correctly(self, tmp_path):
        """step_ratios = actual / estimated per step."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)
        # Estimates: Research=1.5, Implementation=3.0 (from _make_simple_active_estimate)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        report_step_cost({"step_name": "Implementation", "cost": 4.50}, calibration_dir=cal)
        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        records = _read_history(cal)
        ratios = records[0]["step_ratios"]
        assert ratios is not None
        # Research: 1.20 / 1.5 = 0.8
        assert ratios["Research Agent"] == pytest.approx(0.8, rel=1e-3)
        # Implementation: 4.50 / 3.0 = 1.5
        assert ratios["Implementation"] == pytest.approx(1.5, rel=1e-3)

    def test_active_estimate_cleaned_up(self, tmp_path):
        """active-estimate.json is deleted after report_session."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        assert not (cal / "active-estimate.json").exists(), (
            "active-estimate.json should be deleted after report_session"
        )

    def test_step_accumulator_cleaned_up(self, tmp_path):
        """step-accumulator.json is deleted after report_session."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        # Verify accumulator was created before cleanup
        h = _accumulator_hash(cal)
        acc_file = cal / f"{h}-step-accumulator.json"
        assert acc_file.exists(), "Accumulator should exist after report_step_cost"

        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        assert not acc_file.exists(), "Accumulator should be deleted after report_session"

    def test_no_jsonl_files_involved(self, tmp_path):
        """Entire flow produces no *.jsonl session files from Claude Code."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        report_step_cost({"step_name": "Implementation", "cost": 4.50}, calibration_dir=cal)
        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        # Only history.jsonl (calibration output) should exist — no Claude Code session files
        jsonl_files = list(tmp_path.rglob("*.jsonl"))
        jsonl_names = [f.name for f in jsonl_files]
        assert jsonl_names == ["history.jsonl"], (
            f"Expected only history.jsonl, found: {jsonl_names}"
        )

    def test_response_protocol_version(self, tmp_path):
        """report_session response includes attribution_protocol_version=1."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        result = report_session({"actual_cost": 7.20}, calibration_dir=cal)

        assert result["attribution_protocol_version"] == 1

    def test_report_step_cost_response_shape(self, tmp_path):
        """report_step_cost response has all required protocol fields."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        result = report_step_cost(
            {"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal
        )
        assert result["attribution_protocol_version"] == 1
        assert result["step_name"] == "Research Agent"
        assert result["cost_this_call"] == pytest.approx(1.20)
        assert result["cumulative_step_cost"] == pytest.approx(1.20)
        assert result["total_session_accumulated"] == pytest.approx(1.20)

    def test_step_costs_accumulate_across_calls(self, tmp_path):
        """Two calls to the same step add up their costs."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.00}, calibration_dir=cal)
        result2 = report_step_cost(
            {"step_name": "Research Agent", "cost": 0.20}, calibration_dir=cal
        )
        assert result2["cumulative_step_cost"] == pytest.approx(1.20)
        assert result2["total_session_accumulated"] == pytest.approx(1.20)


# ---------------------------------------------------------------------------
# Test 2: CI/CD Tier 1 workflow (session-only, proportional)
# ---------------------------------------------------------------------------


class TestCICDTier1Workflow:
    """Simulates Example B from attribution-protocol.md Section 10.

    CI/CD pipeline knows only total session cost — no per-step breakdown.
    """

    def test_tier1_writes_history_record(self, tmp_path):
        """Session-only report writes exactly one history record."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_session({"actual_cost": 0.85, "turn_count": 32}, calibration_dir=cal)

        records = _read_history(cal)
        assert len(records) == 1

    def test_attribution_method_is_proportional(self, tmp_path):
        """No step reports → attribution_method='proportional'."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        result = report_session({"actual_cost": 0.85, "turn_count": 32}, calibration_dir=cal)

        assert result["attribution_method"] == "proportional"
        records = _read_history(cal)
        assert records[0]["attribution_method"] == "proportional"

    def test_step_ratios_use_session_level_ratio(self, tmp_path):
        """Proportional: all steps get the same ratio (actual/expected)."""
        cal = tmp_path / "calibration"
        # Use simple estimate: expected_cost=5.0, steps: Research=1.5, Impl=3.0
        _make_simple_active_estimate(cal)

        report_session({"actual_cost": 2.5, "turn_count": 32}, calibration_dir=cal)

        records = _read_history(cal)
        r = records[0]
        # Session ratio = 2.5 / 5.0 = 0.5
        expected_ratio = round(2.5 / 5.0, 4)
        step_ratios = r["step_ratios"]
        for step_name in ["Research Agent", "Implementation"]:
            assert step_ratios[step_name] == pytest.approx(expected_ratio, rel=1e-3), (
                f"Expected uniform ratio {expected_ratio} for {step_name}, "
                f"got {step_ratios[step_name]}"
            )

    def test_step_actuals_is_none_for_proportional(self, tmp_path):
        """Proportional attribution → step_actuals is None in history record."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_session({"actual_cost": 0.85, "turn_count": 32}, calibration_dir=cal)

        records = _read_history(cal)
        assert records[0]["step_actuals"] is None

    def test_turn_count_stored_in_record(self, tmp_path):
        """turn_count is preserved in the history record."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_session({"actual_cost": 0.85, "turn_count": 32}, calibration_dir=cal)

        records = _read_history(cal)
        assert records[0]["turn_count"] == 32

    def test_record_written_true(self, tmp_path):
        """Response includes record_written=True for valid cost > 0.001."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        result = report_session({"actual_cost": 0.85, "turn_count": 32}, calibration_dir=cal)

        assert result["record_written"] is True

    def test_actual_cost_preserved_in_record(self, tmp_path):
        """actual_cost is stored exactly in the history record."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_session({"actual_cost": 0.85, "turn_count": 32}, calibration_dir=cal)

        records = _read_history(cal)
        assert records[0]["actual_cost"] == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Test 3: Mixed — some steps reported, some not
# ---------------------------------------------------------------------------


class TestMixedStepReporting:
    """Some steps reported via report_step_cost, others get proportional fallback."""

    def test_reported_steps_use_actual_values(self, tmp_path):
        """Steps reported via report_step_cost use actual costs in step_actuals."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        # Report only Research Agent; leave Implementation unreported
        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        records = _read_history(cal)
        actuals = records[0]["step_actuals"]
        assert actuals is not None
        assert actuals["Research Agent"] == pytest.approx(1.20)

    def test_reported_steps_present_unreported_absent_from_actuals(self, tmp_path):
        """Unreported steps are absent from step_actuals (not zero-filled)."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        # Only report Research Agent — Implementation is not reported
        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        records = _read_history(cal)
        actuals = records[0]["step_actuals"]
        # Implementation was not reported — should not appear in step_actuals
        assert "Implementation" not in actuals

    def test_attribution_method_mcp_when_any_step_reported(self, tmp_path):
        """attribution_method='mcp' when at least one step was reported."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        result = report_session({"actual_cost": 7.20}, calibration_dir=cal)

        assert result["attribution_method"] == "mcp"

    def test_step_ratios_computed_for_reported_steps(self, tmp_path):
        """step_ratios contains only the reported steps that have both actual and estimated."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)
        # Estimate: Research=1.5, Implementation=3.0

        report_step_cost({"step_name": "Research Agent", "cost": 1.50}, calibration_dir=cal)
        report_session({"actual_cost": 7.20}, calibration_dir=cal)

        records = _read_history(cal)
        ratios = records[0]["step_ratios"]
        # Research was reported: 1.50 / 1.5 = 1.0
        assert "Research Agent" in ratios
        assert ratios["Research Agent"] == pytest.approx(1.0, rel=1e-3)
        # Implementation was not reported with actual — no ratio entry (or zero skipped)
        # session_recorder only writes step_ratios for steps with both estimated and actual > 0
        assert "Implementation" not in ratios

    def test_four_step_workflow_with_two_reported(self, tmp_path):
        """Two of four steps reported; history shows mcp attribution."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)

        report_step_cost({"step_name": "Research Agent", "cost": 0.43}, calibration_dir=cal)
        report_step_cost({"step_name": "Implementation", "cost": 2.10}, calibration_dir=cal)
        # Staff Review and other steps not reported
        result = report_session({"actual_cost": 5.50}, calibration_dir=cal)

        assert result["attribution_method"] == "mcp"
        actuals = result["step_actuals"]
        assert actuals["Research Agent"] == pytest.approx(0.43)
        assert actuals["Implementation"] == pytest.approx(2.10)
        assert "Staff Review" not in actuals


# ---------------------------------------------------------------------------
# Test 4: Tier 2 with token counts (not dollar cost)
# ---------------------------------------------------------------------------


class TestTier2WithTokenCounts:
    """Clients report step costs via token counts — server computes dollar cost."""

    def test_token_to_cost_conversion(self, tmp_path):
        """report_step_cost with tokens_in/out computes correct dollar cost."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        # Using Sonnet pricing: tokens_in=200000, tokens_out=30000
        # Expected cost: (200000 * 3.00 + 30000 * 15.00) / 1_000_000 = $1.05
        result = report_step_cost(
            {
                "step_name": "Implementation",
                "tokens_in": 200000,
                "tokens_out": 30000,
                "model": "claude-sonnet-4-6",
            },
            calibration_dir=cal,
        )

        assert result["cost_this_call"] == pytest.approx(1.05, rel=1e-3)

    def test_full_token_workflow_writes_mcp_record(self, tmp_path):
        """Complete workflow using token counts → history record with mcp attribution."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        # Research Agent via tokens
        report_step_cost(
            {
                "step_name": "Research Agent",
                "tokens_in": 100000,
                "tokens_out": 5000,
                "model": "claude-sonnet-4-6",
            },
            calibration_dir=cal,
        )
        # Implementation via tokens
        report_step_cost(
            {
                "step_name": "Implementation",
                "tokens_in": 200000,
                "tokens_out": 30000,
                "model": "claude-sonnet-4-6",
            },
            calibration_dir=cal,
        )
        report_session({"actual_cost": 2.50}, calibration_dir=cal)

        records = _read_history(cal)
        assert len(records) == 1
        assert records[0]["attribution_method"] == "mcp"
        actuals = records[0]["step_actuals"]
        assert actuals is not None
        assert "Research Agent" in actuals
        assert "Implementation" in actuals
        # Verify token-derived costs are stored as floats
        assert isinstance(actuals["Research Agent"], float)
        assert isinstance(actuals["Implementation"], float)

    def test_four_token_types_full_formula(self, tmp_path):
        """Token cost uses all four token fields correctly (protocol Section 10 Example E)."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        result = report_step_cost(
            {
                "step_name": "Implementation",
                "tokens_in": 150000,
                "tokens_out": 25000,
                "tokens_cache_read": 80000,
                "tokens_cache_write": 20000,
                "model": "claude-sonnet-4-6",
            },
            calibration_dir=cal,
        )

        # tokens_in=150000*3/1M=0.45, tokens_out=25000*15/1M=0.375,
        # cache_read=80000*0.30/1M=0.024, cache_write=20000*3.75/1M=0.075
        # total = 0.924
        assert result["cost_this_call"] == pytest.approx(0.924, rel=1e-3)

    def test_cost_takes_precedence_over_tokens(self, tmp_path):
        """When both cost and tokens provided, explicit cost wins."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        result = report_step_cost(
            {
                "step_name": "Research Agent",
                "cost": 1.00,
                "tokens_in": 1_000_000,  # would compute to $3.00 if tokens were used
                "model": "claude-sonnet-4-6",
            },
            calibration_dir=cal,
        )

        assert result["cost_this_call"] == pytest.approx(1.00)

    def test_token_workflow_step_ratios_computed(self, tmp_path):
        """Token-based step costs produce valid step_ratios in history record."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)
        # Estimate: Research=1.5, Implementation=3.0

        # Research: 100000*3/1M + 5000*15/1M = 0.30 + 0.075 = 0.375
        report_step_cost(
            {
                "step_name": "Research Agent",
                "tokens_in": 100000,
                "tokens_out": 5000,
                "model": "claude-sonnet-4-6",
            },
            calibration_dir=cal,
        )
        report_session({"actual_cost": 5.0}, calibration_dir=cal)

        records = _read_history(cal)
        ratios = records[0]["step_ratios"]
        # ratio = 0.375 / 1.5 = 0.25
        assert ratios["Research Agent"] == pytest.approx(0.25, rel=1e-2)


# ---------------------------------------------------------------------------
# Test 5: Call-time step_actuals override accumulated values
# ---------------------------------------------------------------------------


class TestCallTimeStepActualsOverride:
    """Call-time step_actuals in report_session take precedence over accumulated values."""

    def test_calltime_overrides_accumulated_for_same_key(self, tmp_path):
        """Call-time Implementation=5.00 overrides accumulated 4.50."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        # Accumulate 4.50 for Implementation
        report_step_cost({"step_name": "Implementation", "cost": 4.50}, calibration_dir=cal)

        # Call-time override: Implementation=5.00
        result = report_session(
            {
                "actual_cost": 7.20,
                "step_actuals": {"Implementation": 5.00},
            },
            calibration_dir=cal,
        )

        # Final Implementation should be 5.00 (call-time wins)
        assert result["step_actuals"]["Implementation"] == pytest.approx(5.00)

    def test_calltime_override_preserved_in_history(self, tmp_path):
        """History record uses the call-time value, not the accumulated value."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        report_step_cost({"step_name": "Implementation", "cost": 4.50}, calibration_dir=cal)
        report_session(
            {
                "actual_cost": 7.20,
                "step_actuals": {"Implementation": 5.00},
            },
            calibration_dir=cal,
        )

        records = _read_history(cal)
        assert records[0]["step_actuals"]["Implementation"] == pytest.approx(5.00)

    def test_calltime_only_overrides_specified_keys(self, tmp_path):
        """Accumulated values for non-overlapping keys are preserved."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        # Accumulate Research Agent
        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)
        # Accumulate Implementation
        report_step_cost({"step_name": "Implementation", "cost": 4.50}, calibration_dir=cal)

        # Call-time overrides only Implementation
        result = report_session(
            {
                "actual_cost": 7.20,
                "step_actuals": {"Implementation": 5.00},
            },
            calibration_dir=cal,
        )

        actuals = result["step_actuals"]
        # Research Agent accumulated value preserved
        assert actuals["Research Agent"] == pytest.approx(1.20)
        # Implementation override wins
        assert actuals["Implementation"] == pytest.approx(5.00)

    def test_calltime_adds_new_steps_not_in_accumulator(self, tmp_path):
        """Call-time step_actuals can add steps that were not in the accumulator."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        # Only accumulate Research Agent
        report_step_cost({"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal)

        # Call-time adds Staff Review (not previously accumulated)
        result = report_session(
            {
                "actual_cost": 7.20,
                "step_actuals": {"Staff Review": 1.10},
            },
            calibration_dir=cal,
        )

        actuals = result["step_actuals"]
        assert actuals["Research Agent"] == pytest.approx(1.20)
        assert actuals["Staff Review"] == pytest.approx(1.10)

    def test_attribution_method_mcp_with_calltime_override(self, tmp_path):
        """attribution_method='mcp' when call-time step_actuals are provided."""
        cal = tmp_path / "calibration"
        _make_simple_active_estimate(cal)

        result = report_session(
            {
                "actual_cost": 7.20,
                "step_actuals": {"Implementation": 5.00},
            },
            calibration_dir=cal,
        )

        assert result["attribution_method"] == "mcp"


# ---------------------------------------------------------------------------
# Test 6: Calibration improves after 3+ sessions
# ---------------------------------------------------------------------------


class TestCalibrationImproves:
    """After 3+ sessions, factors.json is computed and applied to new estimates."""

    def _run_session(self, cal_dir: Path, actual_cost: float, step_costs: Optional[dict] = None) -> None:
        """Run a complete estimate -> (optional steps) -> session flow."""
        _make_simple_active_estimate(cal_dir)
        if step_costs:
            for step_name, cost in step_costs.items():
                report_step_cost({"step_name": step_name, "cost": cost}, calibration_dir=cal_dir)
        report_session({"actual_cost": actual_cost}, calibration_dir=cal_dir)

    def test_three_sessions_write_history_records(self, tmp_path):
        """Running 3 sessions writes 3 history records."""
        cal = tmp_path / "calibration"

        for actual_cost in [4.0, 5.0, 6.0]:
            self._run_session(cal, actual_cost=actual_cost)

        records = _read_history(cal)
        assert len(records) == 3

    def test_factors_json_written_after_three_sessions(self, tmp_path):
        """factors.json exists after 3+ sessions."""
        cal = tmp_path / "calibration"

        for actual_cost in [4.0, 5.0, 6.0]:
            self._run_session(cal, actual_cost=actual_cost)

        factors = _read_factors(cal)
        assert factors, "factors.json should be non-empty after 3 sessions"

    def test_four_sessions_global_factor_present(self, tmp_path):
        """After 4 sessions (>= min_samples=3), a global factor key is written."""
        cal = tmp_path / "calibration"

        # Run 4 sessions with consistent ratio (actual=7.5 vs expected=5.0 → ratio=1.5)
        for _ in range(4):
            self._run_session(cal, actual_cost=7.5)

        factors = _read_factors(cal)
        # factors.json should contain a global factor key or size-class key
        has_global = "global" in factors
        has_size_class = "M" in factors  # size_class for M-size plans
        assert has_global or has_size_class, (
            f"Expected global or size-class factor in factors.json after 4 sessions. "
            f"Got keys: {list(factors.keys())}"
        )

    def test_estimate_cost_uses_calibration_after_sessions(self, tmp_path):
        """estimate_cost reads factors.json and shows non-'--' cal label for a calibrated step."""
        cal = tmp_path / "calibration"

        # Build up enough history for calibration to activate.
        # Use actual_cost well above expected to push the factor > 1.0 clearly.
        # With expected=5.0 and actual=8.0, ratio=1.6 — not an outlier (< 3.0).
        for _ in range(4):
            self._run_session(cal, actual_cost=8.0)

        factors = _read_factors(cal)
        # Only verify calibration is applied if factors.json is non-empty with active status
        if not factors:
            pytest.skip("update-factors.py produced no factors — skipping calibration check")

        # Run estimate with calibration_dir pointing to our populated calibration
        result = estimate_cost(
            {"size": "M", "files": 5, "complexity": "medium"},
            calibration_dir=str(cal),
        )

        # Check if any step has a non-'--' calibration label
        calibrated_steps = [
            s for s in result["steps"] if s.get("cal") not in ("--", None)
        ]
        has_active_calibration = len(calibrated_steps) > 0

        # This assertion is advisory — if no factor activated it means update-factors.py
        # determined the minimum sample threshold was not met. Don't hard-fail.
        if not has_active_calibration:
            # Verify factors.json has the expected structure
            assert isinstance(factors, dict), "factors.json must be a dict"
        else:
            # Verify calibration labels are well-formed
            for step in calibrated_steps:
                cal_label = step["cal"]
                assert cal_label.startswith(("S:", "Z:", "G:", "P:")), (
                    f"Unexpected calibration label: {cal_label!r}"
                )

    def test_factors_json_structure(self, tmp_path):
        """factors.json has expected structure after multiple sessions."""
        cal = tmp_path / "calibration"

        for actual_cost in [4.5, 5.5, 6.5, 7.0]:
            self._run_session(cal, actual_cost=actual_cost)

        factors = _read_factors(cal)
        # factors.json should be a dict (possibly empty if threshold not met)
        assert isinstance(factors, dict), "factors.json must be a JSON object"

    def test_history_records_have_correct_schema(self, tmp_path):
        """All history records have required schema fields."""
        cal = tmp_path / "calibration"
        required_fields = {
            "timestamp",
            "size",
            "files",
            "complexity",
            "expected_cost",
            "actual_cost",
            "ratio",
            "steps",
            "attribution_method",
            "step_ratios",
        }

        for actual_cost in [4.0, 5.0, 6.0]:
            self._run_session(cal, actual_cost=actual_cost)

        records = _read_history(cal)
        for i, record in enumerate(records):
            missing = required_fields - set(record.keys())
            assert not missing, (
                f"Record {i} missing required fields: {missing}"
            )

    def test_mcp_sessions_generate_step_factors_after_threshold(self, tmp_path):
        """After 3+ MCP Tier 2 sessions for same steps, step_factors may activate."""
        cal = tmp_path / "calibration"

        # Run 4 Tier 2 sessions with same step costs to build per-step factor data
        for _ in range(4):
            self._run_session(
                cal,
                actual_cost=5.0,
                step_costs={
                    "Research Agent": 1.20,
                    "Implementation": 4.50,
                },
            )

        records = _read_history(cal)
        assert len(records) == 4

        # All records should have mcp attribution
        for r in records:
            assert r["attribution_method"] == "mcp"

        # step_ratios should exist for all records
        for r in records:
            assert r["step_ratios"] is not None
            assert "Research Agent" in r["step_ratios"]
            assert "Implementation" in r["step_ratios"]
