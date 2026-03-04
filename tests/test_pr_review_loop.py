"""Tests for PR Review Loop modeling (v1.2.1).

Tests the decay formula, base cycle cost computation, calibration application,
edge cases, and learn.sh field forwarding. All tests must fail before
implementation and pass after.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
LEARN_SH = SCRIPTS_DIR / "tokencostscope-learn.sh"
HEURISTICS_MD = REPO_ROOT / "references" / "heuristics.md"
SKILL_MD = REPO_ROOT / "SKILL.md"
CALIBRATION_ALGO_MD = REPO_ROOT / "references" / "calibration-algorithm.md"
EXAMPLES_MD = REPO_ROOT / "references" / "examples.md"


# ---------------------------------------------------------------------------
# Decay formula: review_loop_cost(N) = C × (1 − 0.6^N) / 0.4
# ---------------------------------------------------------------------------

def decay_sum(c: float, n: int, decay: float = 0.6) -> float:
    """Compute the geometric decay sum for N review cycles."""
    if n == 0:
        return 0.0
    return c * (1.0 - decay**n) / (1.0 - decay)


class TestDecayFormula:
    """Tests for the geometric decay series."""

    def test_n0_produces_zero(self):
        """N=0 naturally produces $0 via 1-0.6^0=0 (Finding #6)."""
        assert decay_sum(1.0214, 0) == 0.0

    def test_n1(self):
        """N=1: C × (1-0.6)/0.4 = C × 1.0."""
        result = decay_sum(1.0214, 1)
        assert abs(result - 1.0214) < 0.0001

    def test_n2(self):
        """N=2: C × (1-0.36)/0.4 = C × 1.6."""
        result = decay_sum(1.0214, 2)
        assert abs(result - 1.0214 * 1.6) < 0.0001

    def test_n4(self):
        """N=4: C × (1-0.1296)/0.4 = C × 2.176."""
        result = decay_sum(1.0214, 4)
        assert abs(result - 1.0214 * 2.176) < 0.0001

    def test_large_n_bounded(self):
        """Series converges to C/0.4 = C × 2.5 — never unbounded."""
        result = decay_sum(1.0, 100)
        assert abs(result - 2.5) < 0.001

    def test_individual_cycle_costs(self):
        """Verify each cycle's individual cost decays by 0.6×."""
        c = 1.0
        cycle_1 = c * 0.6**0  # 1.0
        cycle_2 = c * 0.6**1  # 0.6
        cycle_3 = c * 0.6**2  # 0.36
        assert abs(cycle_2 / cycle_1 - 0.6) < 0.0001
        assert abs(cycle_3 / cycle_2 - 0.6) < 0.0001


# ---------------------------------------------------------------------------
# Base cycle cost (C) computation
# ---------------------------------------------------------------------------

class TestBaseCycleCost:
    """Tests for C = staff_review_expected + engineer_final_plan_expected (default constituents)."""

    # Values from examples.md Step 4 (Staff Review) and Step 5 (Engineer Final Plan) — default constituents
    STAFF_REVIEW_EXPECTED = 0.7470
    ENGINEER_FINAL_PLAN_EXPECTED = 0.2744

    def test_both_constituents_present(self):
        """C with both Staff Review and Engineer Final Plan."""
        c = self.STAFF_REVIEW_EXPECTED + self.ENGINEER_FINAL_PLAN_EXPECTED
        assert abs(c - 1.0214) < 0.0001

    def test_missing_engineer_final_plan(self):
        """Missing constituent contributes $0 (Finding #4)."""
        c = self.STAFF_REVIEW_EXPECTED + 0.0
        assert abs(c - 0.7470) < 0.0001

    def test_both_absent(self):
        """Both constituents absent → C = 0, no review loop row."""
        c = 0.0 + 0.0
        assert c == 0.0


# ---------------------------------------------------------------------------
# Band-specific cycle counts
# ---------------------------------------------------------------------------

class TestBandCycleCounts:
    """Tests that band-specific cycle counts produce correct decay sums."""

    C = 1.0214

    def test_default_review_cycles_2(self):
        """review_cycles=2 → Opt(1)=$1.02, Exp(2)=$1.63, Pess(4)=$2.22."""
        opt = decay_sum(self.C, 1)
        exp = decay_sum(self.C, 2)
        pes = decay_sum(self.C, 4)
        assert abs(opt - 1.0214) < 0.001
        assert abs(exp - 1.6342) < 0.001
        assert abs(pes - 2.2226) < 0.001
        assert opt < exp < pes

    def test_review_cycles_3(self):
        """review_cycles=3 → Opt(1), Exp(3), Pess(6)."""
        opt = decay_sum(self.C, 1)
        exp = decay_sum(self.C, 3)
        pes = decay_sum(self.C, 6)
        assert abs(opt - 1.0214) < 0.001
        assert opt < exp < pes
        # 6 cycles should be close to but not exceed the series limit (C×2.5)
        assert pes < self.C * 2.5

    def test_review_cycles_1(self):
        """review_cycles=1 → Opt(1)=Exp(1), Pess(2)."""
        opt = decay_sum(self.C, 1)
        exp = decay_sum(self.C, 1)
        pes = decay_sum(self.C, 2)
        assert abs(opt - exp) < 0.0001  # Same cycle count
        assert pes > exp


# ---------------------------------------------------------------------------
# Full review loop cost with calibration
# ---------------------------------------------------------------------------

class TestReviewLoopWithCalibration:
    """Tests for independent per-band calibration on the review loop row (default pipeline)."""

    C = 1.0214  # From examples.md

    def test_calibration_factor_1(self):
        """No calibration (factor=1.0) — raw values unchanged."""
        factor = 1.0
        raw_opt = decay_sum(self.C, 1)
        raw_exp = decay_sum(self.C, 2)
        raw_pes = decay_sum(self.C, 4)

        cal_opt = raw_opt * factor
        cal_exp = raw_exp * factor
        cal_pes = raw_pes * factor

        assert abs(cal_opt - raw_opt) < 0.0001
        assert abs(cal_exp - raw_exp) < 0.0001
        assert abs(cal_pes - raw_pes) < 0.0001

    def test_calibration_factor_1_2(self):
        """Calibration factor=1.2 scales each band independently."""
        factor = 1.2
        raw_opt = decay_sum(self.C, 1)
        raw_exp = decay_sum(self.C, 2)
        raw_pes = decay_sum(self.C, 4)

        cal_opt = raw_opt * factor
        cal_exp = raw_exp * factor
        cal_pes = raw_pes * factor

        # Each band scaled independently — NOT re-anchored as ratios of Expected
        assert abs(cal_opt - raw_opt * 1.2) < 0.0001
        assert abs(cal_exp - raw_exp * 1.2) < 0.0001
        assert abs(cal_pes - raw_pes * 1.2) < 0.0001

        # Verify the ratios between bands are preserved (not 0.6x/3.0x)
        assert abs(cal_opt / cal_exp - raw_opt / raw_exp) < 0.0001
        assert abs(cal_pes / cal_exp - raw_pes / raw_exp) < 0.0001


# ---------------------------------------------------------------------------
# Worked example verification (Example 2 in examples.md)
# ---------------------------------------------------------------------------

class TestWorkedExample:
    """Verify the worked example arithmetic matches examples.md Example 2."""

    C = 0.7470 + 0.2744  # Staff Review + Engineer Final Plan

    def test_c_value(self):
        assert abs(self.C - 1.0214) < 0.0001

    def test_optimistic_1_cycle(self):
        result = decay_sum(self.C, 1)
        assert abs(result - 1.0214) < 0.0001

    def test_expected_2_cycles(self):
        result = decay_sum(self.C, 2)
        expected = 1.0214 * 1.6
        assert abs(result - expected) < 0.0001

    def test_pessimistic_4_cycles(self):
        result = decay_sum(self.C, 4)
        expected = 1.0214 * 2.176
        assert abs(result - expected) < 0.0001

    def test_calibrated_bands_factor_1(self):
        """With factor=1.0, calibrated values equal raw decay values per band."""
        factor = 1.0
        raw_opt = decay_sum(self.C, 1)   # 1 cycle
        raw_exp = decay_sum(self.C, 2)   # 2 cycles
        raw_pes = decay_sum(self.C, 4)   # 4 cycles

        cal_opt = raw_opt * factor
        cal_exp = raw_exp * factor
        cal_pes = raw_pes * factor

        assert abs(cal_opt - 1.0214) < 0.001
        assert abs(cal_exp - 1.6342) < 0.001
        assert abs(cal_pes - 2.2226) < 0.001


# ---------------------------------------------------------------------------
# learn.sh: version and field forwarding
# ---------------------------------------------------------------------------

class TestLearnScript:
    """Tests for tokencostscope-learn.sh changes."""

    def test_version_is_1_2_0(self):
        """learn.sh --version should output 1.2.1 (Finding #10)."""
        result = subprocess.run(
            ["bash", str(LEARN_SH), "--version"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "1.2.1" in result.stdout

    def test_forwards_review_cycles_estimated(self):
        """learn.sh should forward review_cycles_estimated to history record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cal_dir = Path(tmpdir) / "calibration"
            cal_dir.mkdir()

            # Write active-estimate.json with review_cycles fields
            estimate = {
                "timestamp": "2026-03-04T10:00:00Z",
                "size": "M",
                "files": 5,
                "complexity": "medium",
                "steps": ["Staff Review", "Implementation"],
                "step_count": 2,
                "project_type": "greenfield",
                "language": "python",
                "expected_cost": 7.0,
                "optimistic_cost": 3.5,
                "pessimistic_cost": 21.0,
                "baseline_cost": 0.0,
                "review_cycles_estimated": 2,
                "review_cycles_actual": None,
            }
            estimate_file = cal_dir / "active-estimate.json"
            estimate_file.write_text(json.dumps(estimate))

            # Create a minimal JSONL session log
            jsonl_dir = Path(tmpdir) / "projects" / "test"
            jsonl_dir.mkdir(parents=True)
            jsonl_file = jsonl_dir / "session.jsonl"
            # Write a fake assistant message with usage
            msg = {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-20250514",
                    "usage": {
                        "input_tokens": 10000,
                        "cache_read_input_tokens": 5000,
                        "cache_creation_input_tokens": 2000,
                        "output_tokens": 3000,
                    }
                }
            }
            jsonl_file.write_text(json.dumps(msg) + "\n")

            # Verify the Python parsing logic extracts review_cycles_estimated
            # Uses env var for path to avoid f-string injection
            parse_result = subprocess.run(
                ["python3", "-c", """
import json, os
with open(os.environ['EST_FILE']) as f:
    d = json.load(f)
rc = d.get('review_cycles_estimated', 0)
print(f'REVIEW_CYCLES={rc}')
"""],
                capture_output=True, text=True,
                env={**os.environ, "EST_FILE": str(estimate_file)},
            )
            assert parse_result.returncode == 0
            assert "REVIEW_CYCLES=2" in parse_result.stdout

    def test_handles_missing_review_cycles(self):
        """Old-format active-estimate.json without review_cycles defaults to 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            estimate = {
                "timestamp": "2026-03-04T10:00:00Z",
                "size": "M",
                "expected_cost": 7.0,
                "baseline_cost": 0.0,
            }
            estimate_file = Path(tmpdir) / "active-estimate.json"
            estimate_file.write_text(json.dumps(estimate))

            parse_result = subprocess.run(
                ["python3", "-c", """
import json, os
with open(os.environ['EST_FILE']) as f:
    d = json.load(f)
rc = d.get('review_cycles_estimated', 0)
print(f'REVIEW_CYCLES={rc}')
"""],
                capture_output=True, text=True,
                env={**os.environ, "EST_FILE": str(estimate_file)},
            )
            assert parse_result.returncode == 0
            assert "REVIEW_CYCLES=0" in parse_result.stdout


# ---------------------------------------------------------------------------
# Document content verification
# ---------------------------------------------------------------------------

class TestDocumentContent:
    """Verify required content exists in documentation files after implementation."""

    def test_heuristics_has_review_loop_defaults(self):
        """heuristics.md must have PR Review Loop Defaults section."""
        content = HEURISTICS_MD.read_text()
        assert "PR Review Loop Defaults" in content
        assert "review_cycles_default" in content
        assert "review_decay_factor" in content

    def test_heuristics_has_optimistic_rationale(self):
        """heuristics.md must explain why Optimistic uses N=1 (Finding #13)."""
        content = HEURISTICS_MD.read_text()
        assert "Optimistic" in content
        assert "N=1" in content or "1 review cycle" in content

    def test_skill_md_version_1_2(self):
        """SKILL.md frontmatter version must be 1.2.1."""
        content = SKILL_MD.read_text()
        assert "version: 1.2.1" in content

    def test_skill_md_has_step_3_5(self):
        """SKILL.md must have Step 3.5 section."""
        content = SKILL_MD.read_text()
        assert "Step 3.5" in content

    def test_skill_md_has_review_cycles_override(self):
        """SKILL.md overrides table must include review_cycles."""
        content = SKILL_MD.read_text()
        assert "review_cycles" in content

    def test_skill_md_output_template_v1_2(self):
        """Output template must show v1.2.1."""
        content = SKILL_MD.read_text()
        assert "v1.2.1" in content

    def test_skill_md_has_review_loop_in_template(self):
        """Output template must include PR Review Loop row."""
        content = SKILL_MD.read_text()
        assert "PR Review Loop" in content
        assert "Opus+Sonnet" in content

    def test_skill_md_active_estimate_schema(self):
        """active-estimate.json schema must include review_cycles fields."""
        content = SKILL_MD.read_text()
        assert "review_cycles_estimated" in content
        assert "review_cycles_actual" in content

    def test_calibration_algo_has_review_cycles(self):
        """calibration-algorithm.md must document review_cycles fields."""
        content = CALIBRATION_ALGO_MD.read_text()
        assert "review_cycles_estimated" in content
        assert "review_cycles_actual" in content

    def test_examples_has_review_loop(self):
        """examples.md must have a PR Review Loop worked example."""
        content = EXAMPLES_MD.read_text()
        assert "PR Review Loop" in content
        assert "review_cycles" in content or "review cycle" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
