# Run with: /usr/bin/python3 -m pytest tests/test_session_recorder.py
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""Unit tests for src/tokencast/session_recorder.py — build_history_record().

Covers:
- All three attribution paths (mcp, sidecar, proportional)
- Schema completeness (all 26 keys)
- Edge cases (empty estimate, zero expected_cost, etc.)
- Equivalence with the original learn.sh inline RECORD block logic
"""

import re
import sys
from pathlib import Path

import pytest

# Ensure src/ is on path
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tokencast.session_recorder import build_history_record

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FULL_ESTIMATE = {
    "size": "L",
    "files": 15,
    "complexity": "high",
    "project_type": "feature",
    "language": "python",
    "steps": ["Research Agent", "Implementation", "QA", "Staff Review"],
    "step_count": 4,
    "review_cycles_estimated": 2,
    "expected_cost": 10.0,
    "optimistic_cost": 6.0,
    "pessimistic_cost": 30.0,
    "parallel_groups": [["Implementation", "QA"]],
    "parallel_steps_detected": 2,
    "file_brackets": {"small": 3, "medium": 8, "large": 4},
    "files_measured": 15,
    "step_costs": {
        "Research Agent": 1.5,
        "Implementation": 4.0,
        "QA": 2.0,
        "Staff Review": 1.5,
        "PR Review Loop": 3.0,  # this key must be excluded from step_costs_estimated
    },
    "continuation": False,
}

STEP_COSTS_EXPECTED_NO_PR = {
    "Research Agent": 1.5,
    "Implementation": 4.0,
    "QA": 2.0,
    "Staff Review": 1.5,
}

# All 26 required keys in the record
REQUIRED_KEYS = [
    "timestamp",
    "size",
    "files",
    "complexity",
    "expected_cost",
    "optimistic_cost",
    "pessimistic_cost",
    "actual_cost",
    "ratio",
    "turn_count",
    "steps",
    "pipeline_signature",
    "project_type",
    "language",
    "step_count",
    "review_cycles_estimated",
    "review_cycles_actual",
    "parallel_groups",
    "parallel_steps_detected",
    "file_brackets",
    "files_measured",
    "step_costs_estimated",
    "step_ratios",
    "step_actuals",
    "attribution_method",
    "continuation",
]


# ---------------------------------------------------------------------------
# TestBuildHistoryRecordProportional
# ---------------------------------------------------------------------------


class TestBuildHistoryRecordProportional:
    def test_proportional_fallback_no_step_actuals(self):
        """Both mcp and sidecar are None → proportional."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=12.0,
            step_actuals_mcp=None,
            step_actuals_sidecar=None,
        )
        assert record["attribution_method"] == "proportional"
        assert record["step_actuals"] is None

    def test_proportional_fallback_empty_dicts(self):
        """Both passed as {} (empty, not None) → same result as None case."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=12.0,
            step_actuals_mcp={},
            step_actuals_sidecar={},
        )
        assert record["attribution_method"] == "proportional"
        assert record["step_actuals"] is None

    def test_step_ratios_proportional_uniform(self):
        """All step keys in step_ratios have the same uniform value."""
        actual = 12.0
        expected = 10.0
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=actual,
        )
        expected_ratio = round(actual / expected, 4)
        ratios = record["step_ratios"]
        # Should have an entry for each step in step_costs_estimated
        assert set(ratios.keys()) == set(STEP_COSTS_EXPECTED_NO_PR.keys())
        for v in ratios.values():
            assert v == expected_ratio

    def test_session_expected_floor(self):
        """expected_cost=0 → session_expected clamps to 0.001 (no ZeroDivision)."""
        est = dict(FULL_ESTIMATE, expected_cost=0)
        record = build_history_record(estimate=est, actual_cost=5.0)
        # ratio = round(5.0 / 0.001, 4) = 5000.0
        assert record["ratio"] == round(5.0 / 0.001, 4)

    def test_ratio_field(self):
        """ratio == round(actual / session_expected, 4)."""
        actual = 8.5
        expected = 10.0
        record = build_history_record(estimate=FULL_ESTIMATE, actual_cost=actual)
        assert record["ratio"] == round(actual / expected, 4)


# ---------------------------------------------------------------------------
# TestBuildHistoryRecordMcp
# ---------------------------------------------------------------------------


class TestBuildHistoryRecordMcp:
    MCP_ACTUALS = {
        "Research Agent": 1.8,
        "Implementation": 5.0,
        "QA": 1.5,
    }

    def test_mcp_attribution_method(self):
        """Non-empty step_actuals_mcp → attribution_method == 'mcp'."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=12.0,
            step_actuals_mcp=self.MCP_ACTUALS,
        )
        assert record["attribution_method"] == "mcp"

    def test_mcp_step_ratios_per_step(self):
        """Per-step ratio = actual / estimated for each step."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=12.0,
            step_actuals_mcp=self.MCP_ACTUALS,
        )
        ratios = record["step_ratios"]
        assert ratios["Research Agent"] == pytest.approx(round(1.8 / 1.5, 4))
        assert ratios["Implementation"] == pytest.approx(round(5.0 / 4.0, 4))
        assert ratios["QA"] == pytest.approx(round(1.5 / 2.0, 4))

    def test_mcp_step_ratios_skip_zero_estimated(self):
        """Steps with estimated == 0 are excluded from step_ratios."""
        est = dict(FULL_ESTIMATE)
        est["step_costs"] = {"Research Agent": 0.0, "Implementation": 4.0}
        actuals = {"Research Agent": 1.0, "Implementation": 3.0}
        record = build_history_record(
            estimate=est, actual_cost=5.0, step_actuals_mcp=actuals
        )
        assert "Research Agent" not in record["step_ratios"]
        assert "Implementation" in record["step_ratios"]

    def test_mcp_step_ratios_skip_zero_actual(self):
        """Steps with actual == 0 are excluded from step_ratios."""
        actuals = dict(self.MCP_ACTUALS, **{"Staff Review": 0.0})
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=12.0,
            step_actuals_mcp=actuals,
        )
        assert "Staff Review" not in record["step_ratios"]

    def test_mcp_wins_over_sidecar(self):
        """When both mcp and sidecar are provided, mcp wins."""
        sidecar = {"Research Agent": 2.5}
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=12.0,
            step_actuals_mcp=self.MCP_ACTUALS,
            step_actuals_sidecar=sidecar,
        )
        assert record["attribution_method"] == "mcp"
        assert record["step_actuals"] == self.MCP_ACTUALS

    def test_mcp_step_actuals_in_record(self):
        """step_actuals field in record equals the mcp input."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=12.0,
            step_actuals_mcp=self.MCP_ACTUALS,
        )
        assert record["step_actuals"] == self.MCP_ACTUALS


# ---------------------------------------------------------------------------
# TestBuildHistoryRecordSidecar
# ---------------------------------------------------------------------------


class TestBuildHistoryRecordSidecar:
    SIDECAR_ACTUALS = {
        "Research Agent": 1.6,
        "Implementation": 4.5,
    }

    def test_sidecar_attribution_method(self):
        """step_actuals_mcp=None, step_actuals_sidecar non-empty → 'sidecar'."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=10.0,
            step_actuals_mcp=None,
            step_actuals_sidecar=self.SIDECAR_ACTUALS,
        )
        assert record["attribution_method"] == "sidecar"

    def test_sidecar_step_ratios(self):
        """Per-step ratios computed correctly for sidecar path."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=10.0,
            step_actuals_mcp=None,
            step_actuals_sidecar=self.SIDECAR_ACTUALS,
        )
        ratios = record["step_ratios"]
        assert ratios["Research Agent"] == pytest.approx(round(1.6 / 1.5, 4))
        assert ratios["Implementation"] == pytest.approx(round(4.5 / 4.0, 4))

    def test_sidecar_step_actuals_in_record(self):
        """step_actuals field equals sidecar input."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=10.0,
            step_actuals_mcp=None,
            step_actuals_sidecar=self.SIDECAR_ACTUALS,
        )
        assert record["step_actuals"] == self.SIDECAR_ACTUALS


# ---------------------------------------------------------------------------
# TestBuildHistoryRecordSchema
# ---------------------------------------------------------------------------


class TestBuildHistoryRecordSchema:
    def test_all_required_keys_present(self):
        """All 26 expected keys are present in the returned dict."""
        record = build_history_record(estimate=FULL_ESTIMATE, actual_cost=10.0)
        for key in REQUIRED_KEYS:
            assert key in record, f"Missing key: {key}"

    def test_pr_review_loop_excluded_from_step_costs(self):
        """'PR Review Loop' key in estimate.step_costs is excluded from step_costs_estimated."""
        record = build_history_record(estimate=FULL_ESTIMATE, actual_cost=10.0)
        assert "PR Review Loop" not in record["step_costs_estimated"]
        assert "Research Agent" in record["step_costs_estimated"]

    def test_file_brackets_null_passthrough(self):
        """estimate has 'file_brackets': None → record['file_brackets'] is None."""
        est = dict(FULL_ESTIMATE, file_brackets=None)
        record = build_history_record(estimate=est, actual_cost=10.0)
        assert record["file_brackets"] is None

    def test_file_brackets_dict_passthrough(self):
        """estimate has file_brackets dict → round-trip preserved."""
        brackets = {"small": 1, "medium": 2, "large": 0}
        est = dict(FULL_ESTIMATE, file_brackets=brackets)
        record = build_history_record(estimate=est, actual_cost=10.0)
        assert record["file_brackets"] == brackets

    def test_continuation_false_default(self):
        """estimate has no 'continuation' key → record['continuation'] == False."""
        est = {k: v for k, v in FULL_ESTIMATE.items() if k != "continuation"}
        record = build_history_record(estimate=est, actual_cost=10.0)
        assert record["continuation"] is False

    def test_continuation_true(self):
        """estimate has 'continuation': True → passthrough."""
        est = dict(FULL_ESTIMATE, continuation=True)
        record = build_history_record(estimate=est, actual_cost=10.0)
        assert record["continuation"] is True

    def test_pipeline_signature_derivation(self):
        """steps list → signature is sorted, lowercased, underscored, joined with '+'."""
        est = dict(FULL_ESTIMATE, steps=["Research Agent", "Implementation"])
        record = build_history_record(estimate=est, actual_cost=10.0)
        assert record["pipeline_signature"] == "implementation+research_agent"

    def test_empty_estimate(self):
        """estimate={} → record is still valid with fallback values; no exception."""
        record = build_history_record(estimate={}, actual_cost=5.0)
        # Verify all required keys exist
        for key in REQUIRED_KEYS:
            assert key in record, f"Missing key: {key}"
        # Verify fallback values
        assert record["size"] == "M"
        assert record["files"] == 0
        assert record["complexity"] == "medium"
        assert record["project_type"] == "unknown"
        assert record["language"] == "unknown"
        assert record["steps"] == []
        assert record["pipeline_signature"] == ""

    def test_review_cycles_actual_none(self):
        """review_cycles_actual=None → record['review_cycles_actual'] is None."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=10.0,
            review_cycles_actual=None,
        )
        assert record["review_cycles_actual"] is None

    def test_review_cycles_actual_int(self):
        """review_cycles_actual=3 → record['review_cycles_actual'] == 3."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=10.0,
            review_cycles_actual=3,
        )
        assert record["review_cycles_actual"] == 3

    def test_timestamp_injected(self):
        """Passing fixed timestamp → exact value appears in record."""
        ts = "2026-01-01T00:00:00Z"
        record = build_history_record(
            estimate=FULL_ESTIMATE, actual_cost=10.0, timestamp=ts
        )
        assert record["timestamp"] == ts

    def test_timestamp_auto_generated(self):
        """timestamp=None → non-empty string matching ISO 8601 pattern."""
        record = build_history_record(
            estimate=FULL_ESTIMATE, actual_cost=10.0, timestamp=None
        )
        ts = record["timestamp"]
        assert isinstance(ts, str) and len(ts) > 0
        # Should match YYYY-MM-DDTHH:MM:SSZ
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts)

    def test_parallel_steps_detected_int(self):
        """parallel_steps_detected coerced to int from string digit."""
        est = dict(FULL_ESTIMATE, parallel_steps_detected="3")
        record = build_history_record(estimate=est, actual_cost=10.0)
        assert record["parallel_steps_detected"] == 3

    def test_parallel_steps_detected_bool_true(self):
        """parallel_steps_detected 'true' string → 1."""
        est = dict(FULL_ESTIMATE, parallel_steps_detected="true")
        record = build_history_record(estimate=est, actual_cost=10.0)
        assert record["parallel_steps_detected"] == 1

    def test_parallel_steps_detected_unknown_string(self):
        """parallel_steps_detected unknown string → 0."""
        est = dict(FULL_ESTIMATE, parallel_steps_detected="maybe")
        record = build_history_record(estimate=est, actual_cost=10.0)
        assert record["parallel_steps_detected"] == 0

    def test_step_costs_estimated_excludes_only_pr_review_loop(self):
        """Only 'PR Review Loop' is excluded; other keys pass through."""
        record = build_history_record(estimate=FULL_ESTIMATE, actual_cost=10.0)
        assert record["step_costs_estimated"] == STEP_COSTS_EXPECTED_NO_PR

    def test_step_actuals_proportional_is_none(self):
        """step_actuals in record is None when attribution is proportional."""
        record = build_history_record(
            estimate=FULL_ESTIMATE,
            actual_cost=10.0,
            step_actuals_mcp=None,
            step_actuals_sidecar=None,
        )
        assert record["step_actuals"] is None

    def test_actual_cost_stored_correctly(self):
        """actual_cost in record matches the input."""
        record = build_history_record(estimate=FULL_ESTIMATE, actual_cost=7.77)
        assert record["actual_cost"] == pytest.approx(7.77)

    def test_turn_count_default_zero(self):
        """turn_count defaults to 0."""
        record = build_history_record(estimate=FULL_ESTIMATE, actual_cost=5.0)
        assert record["turn_count"] == 0

    def test_turn_count_passed_through(self):
        """turn_count parameter stored correctly."""
        record = build_history_record(
            estimate=FULL_ESTIMATE, actual_cost=5.0, turn_count=42
        )
        assert record["turn_count"] == 42


# ---------------------------------------------------------------------------
# TestBuildHistoryRecordLearnShEquivalence
# ---------------------------------------------------------------------------


class TestBuildHistoryRecordLearnShEquivalence:
    """Regression guard: build_history_record() must produce the same record
    as the original learn.sh inline Python for the same inputs."""

    # Manually computed expected values derived from the learn.sh RECORD block logic
    ESTIMATE = {
        "size": "M",
        "files": 8,
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
        "file_brackets": {"small": 2, "medium": 4, "large": 2},
        "files_measured": 8,
        "step_costs": {
            "Research Agent": 1.5,
            "Implementation": 3.0,
            "PR Review Loop": 2.0,  # excluded
        },
        "continuation": False,
    }

    SIDECAR_ACTUALS = {
        "Research Agent": 1.8,
        "Implementation": 3.5,
    }

    def test_sidecar_record_matches_learn_sh_output(self):
        """Sidecar attribution path produces known-correct values."""
        actual = 7.0
        record = build_history_record(
            estimate=self.ESTIMATE,
            actual_cost=actual,
            turn_count=120,
            review_cycles_actual=3,
            step_actuals_sidecar=self.SIDECAR_ACTUALS,
            timestamp="2026-03-26T12:00:00Z",
        )

        # Field-by-field assertions matching what learn.sh would produce
        assert record["timestamp"] == "2026-03-26T12:00:00Z"
        assert record["size"] == "M"
        assert record["files"] == 8
        assert record["complexity"] == "medium"
        assert record["expected_cost"] == 5.0
        assert record["optimistic_cost"] == 3.0
        assert record["pessimistic_cost"] == 15.0
        assert record["actual_cost"] == 7.0
        # ratio = round(7.0 / 5.0, 4) = 1.4
        assert record["ratio"] == 1.4
        assert record["turn_count"] == 120
        assert record["steps"] == ["Research Agent", "Implementation"]
        # sig = '+'.join(sorted(['research_agent', 'implementation']))
        assert record["pipeline_signature"] == "implementation+research_agent"
        assert record["project_type"] == "feature"
        assert record["language"] == "python"
        assert record["step_count"] == 2
        assert record["review_cycles_estimated"] == 2
        assert record["review_cycles_actual"] == 3
        assert record["parallel_groups"] == []
        assert record["parallel_steps_detected"] == 0
        assert record["file_brackets"] == {"small": 2, "medium": 4, "large": 2}
        assert record["files_measured"] == 8
        # step_costs_estimated excludes PR Review Loop
        assert record["step_costs_estimated"] == {
            "Research Agent": 1.5,
            "Implementation": 3.0,
        }
        # step_ratios: per-step since both step_actuals and step_costs_estimated non-empty
        assert record["step_ratios"] == {
            "Research Agent": round(1.8 / 1.5, 4),
            "Implementation": round(3.5 / 3.0, 4),
        }
        assert record["step_actuals"] == self.SIDECAR_ACTUALS
        assert record["attribution_method"] == "sidecar"
        assert record["continuation"] is False

    def test_proportional_record_matches_learn_sh_output(self):
        """Proportional attribution path produces known-correct values."""
        actual = 8.0
        record = build_history_record(
            estimate=self.ESTIMATE,
            actual_cost=actual,
            turn_count=85,
            review_cycles_actual=None,
            step_actuals_mcp=None,
            step_actuals_sidecar=None,
            timestamp="2026-03-26T15:00:00Z",
        )

        assert record["attribution_method"] == "proportional"
        assert record["step_actuals"] is None
        # ratio = round(8.0 / 5.0, 4) = 1.6
        assert record["ratio"] == 1.6
        # step_ratios: uniform value = round(8.0 / 5.0, 4) = 1.6 for all steps
        assert record["step_ratios"] == {
            "Research Agent": 1.6,
            "Implementation": 1.6,
        }
        assert record["review_cycles_actual"] is None
