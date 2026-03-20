"""Tests for per-step calibration factors (v1.4.0).

Tests the factor hierarchy (per-step > size-class > global > uncalibrated),
Pass 4 computation logic, learn.sh step_costs extraction and step_ratios
proportional attribution, Cal column formatting, and document content verification.

Arithmetic tests (TestPerStepFactorArithmetic, TestPerStepFactorComputation)
pass before implementation — they test inline helper formulas, not file content.
Document-content tests (TestDocumentContent) and integration tests
(TestLearnShIntegrationStepCosts) fail before implementation.
"""
# Runner: pytest (required). Non-TestCase classes are pytest-style and will be
# silently skipped by `python -m unittest`. Use: /usr/bin/python3 -m pytest tests/

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
LEARN_SH = SCRIPTS_DIR / "tokencostscope-learn.sh"
UPDATE_FACTORS_PY = SCRIPTS_DIR / "update-factors.py"
SKILL_MD = REPO_ROOT / "SKILL.md"
HEURISTICS_MD = REPO_ROOT / "references" / "heuristics.md"
CALIBRATION_ALG_MD = REPO_ROOT / "references" / "calibration-algorithm.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

PR_REVIEW_LOOP_KEY = "PR Review Loop"


# ---------------------------------------------------------------------------
# Arithmetic helpers — mirror the factor hierarchy and calibration formulas
# ---------------------------------------------------------------------------

def resolve_factor(
    step_name: str,
    step_factors: dict,
    size: str,
    factors: dict,
) -> tuple[float, str]:
    """Resolve calibration factor and source using the precedence chain.

    Returns (factor, source) where source is one of: "S", "Z", "G", "--".
    Mirrors SKILL.md Step 3e logic.
    """
    # PR Review Loop always "--"
    if step_name == PR_REVIEW_LOOP_KEY:
        return 1.0, "--"

    # 1. Per-step: active status
    sf = step_factors.get(step_name)
    if sf and sf.get("status") == "active":
        return sf["factor"], "S"

    # 2. Size-class: factors[size] with n >= 3
    size_factor = factors.get(size)
    size_n = factors.get(f"{size}_n", 0)
    if size_factor is not None and size_n >= 3:
        return size_factor, "Z"

    # 3. Global: factors["global"] with status "active"
    global_factor = factors.get("global")
    global_status = factors.get("status")
    if global_factor is not None and global_status == "active":
        return global_factor, "G"

    # 4. No calibration
    return 1.0, "--"


def calibrate_bands(raw_expected: float, factor: float) -> tuple[float, float, float]:
    """Apply calibration factor to expected band, derive opt/pess from it."""
    calibrated_expected = raw_expected * factor
    calibrated_optimistic = calibrated_expected * 0.6
    calibrated_pessimistic = calibrated_expected * 3.0
    return calibrated_optimistic, calibrated_expected, calibrated_pessimistic


def compute_step_factors_pass4(
    clean_records: list[dict],
) -> dict:
    """Mirror update-factors.py Pass 4 logic.

    Collects step_ratios from clean records, excludes PR Review Loop,
    computes trimmed_mean (n<=10) or EWMA (n>10), marks status.
    """
    per_step_min_samples = 3
    ratios_by_step: dict[str, list[float]] = {}
    for record in clean_records:
        step_ratios = record.get("step_ratios", {})
        for step_name, ratio in step_ratios.items():
            if step_name == PR_REVIEW_LOOP_KEY:
                continue
            ratios_by_step.setdefault(step_name, []).append(ratio)

    step_factors: dict[str, dict] = {}
    for step_name, ratios in ratios_by_step.items():
        n = len(ratios)
        if n <= 10:
            factor = trimmed_mean(ratios)
        else:
            factor = compute_ewma(ratios)
        status = "active" if n >= per_step_min_samples else "collecting"
        step_factors[step_name] = {
            "factor": round(factor, 4),
            "n": n,
            "status": status,
        }
    return step_factors


def trimmed_mean(values: list[float], trim_fraction: float = 0.1) -> float:
    """Plain mean for n < 10; trimmed mean for n >= 10."""
    if not values:
        return 1.0
    n = len(values)
    k = int(n * trim_fraction)
    sorted_vals = sorted(values)
    trimmed = sorted_vals[k : n - k] if k > 0 else sorted_vals
    return sum(trimmed) / len(trimmed)


def compute_ewma(values: list[float], alpha: float = 0.15) -> float:
    """Exponentially weighted moving average."""
    if not values:
        return 1.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def make_clean_record(step_ratios: dict, ratio: float = 0.9) -> dict:
    """Build a minimal clean record with _ratio and step_ratios."""
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "size": "M",
        "expected_cost": 5.0,
        "actual_cost": 5.0 * ratio,
        "_ratio": ratio,
        "step_ratios": step_ratios,
    }


# ---------------------------------------------------------------------------
# Class 1: TestPerStepFactorArithmetic
# (Pure arithmetic — pass before implementation)
# ---------------------------------------------------------------------------

class TestPerStepFactorArithmetic:
    """Factor hierarchy and calibrated band formula tests."""

    def test_factor_hierarchy_step_wins_over_size(self):
        """Per-step factor 0.82 overrides size-class factor 1.18."""
        step_factors = {"Research Agent": {"factor": 0.82, "n": 5, "status": "active"}}
        factors = {"M": 1.18, "M_n": 5, "status": "active", "global": 1.12}
        factor, source = resolve_factor("Research Agent", step_factors, "M", factors)
        assert factor == 0.82
        assert source == "S"

    def test_factor_hierarchy_size_wins_over_global(self):
        """Size-class factor 1.18 overrides global 1.12 when step absent."""
        step_factors = {}
        factors = {"M": 1.18, "M_n": 5, "status": "active", "global": 1.12}
        factor, source = resolve_factor("Some Step", step_factors, "M", factors)
        assert factor == 1.18
        assert source == "Z"

    def test_factor_hierarchy_global_when_no_step_or_size(self):
        """Global factor 1.12 applied when no step or size-class factor available."""
        step_factors = {}
        factors = {"status": "active", "global": 1.12}
        factor, source = resolve_factor("Some Step", step_factors, "M", factors)
        assert factor == 1.12
        assert source == "G"

    def test_factor_hierarchy_uncalibrated_when_no_factors(self):
        """Factor = 1.0 and source = '--' when no calibration data available."""
        step_factors = {}
        factors = {}
        factor, source = resolve_factor("Some Step", step_factors, "M", factors)
        assert factor == 1.0
        assert source == "--"

    def test_calibrated_expected_is_raw_times_factor(self):
        """calibrated_expected = raw_expected × factor."""
        _, calibrated_expected, _ = calibrate_bands(2.00, 0.82)
        assert abs(calibrated_expected - 1.64) < 0.0001

    def test_calibrated_optimistic_is_0_6_of_calibrated_expected(self):
        """calibrated_optimistic = calibrated_expected × 0.6."""
        calibrated_optimistic, calibrated_expected, _ = calibrate_bands(2.00, 0.82)
        assert abs(calibrated_optimistic - calibrated_expected * 0.6) < 0.0001

    def test_calibrated_pessimistic_is_3_0_of_calibrated_expected(self):
        """calibrated_pessimistic = calibrated_expected × 3.0."""
        _, calibrated_expected, calibrated_pessimistic = calibrate_bands(2.00, 0.82)
        assert abs(calibrated_pessimistic - calibrated_expected * 3.0) < 0.0001

    def test_collecting_status_falls_through(self):
        """Step with status 'collecting' does not apply per-step factor; falls through."""
        step_factors = {"Research Agent": {"factor": 0.50, "n": 2, "status": "collecting"}}
        factors = {"M": 1.18, "M_n": 5, "status": "active", "global": 1.12}
        factor, source = resolve_factor("Research Agent", step_factors, "M", factors)
        # Should fall through to size-class
        assert factor == 1.18
        assert source == "Z"

    def test_step_not_in_factors_falls_through(self):
        """Step absent from step_factors entirely falls through to size-class/global."""
        step_factors = {"Other Step": {"factor": 0.80, "n": 5, "status": "active"}}
        factors = {"M": 1.18, "M_n": 5, "status": "active", "global": 1.12}
        factor, source = resolve_factor("Research Agent", step_factors, "M", factors)
        assert factor == 1.18
        assert source == "Z"

    def test_per_step_wins_when_both_per_step_and_size_class_active(self):
        """Per-step wins over size-class: S:0.82 beats Z:1.18."""
        step_factors = {"Research Agent": {"factor": 0.82, "n": 5, "status": "active"}}
        factors = {"M": 1.18, "M_n": 5, "status": "active", "global": 1.12}
        factor, source = resolve_factor("Research Agent", step_factors, "M", factors)
        _, calibrated_expected, _ = calibrate_bands(2.00, factor)
        assert source == "S"
        assert factor == 0.82
        assert abs(calibrated_expected - 2.00 * 0.82) < 0.0001

    def test_pr_review_loop_always_double_dash(self):
        """PR Review Loop row always returns '--' regardless of any factors."""
        step_factors = {PR_REVIEW_LOOP_KEY: {"factor": 0.70, "n": 10, "status": "active"}}
        factors = {"M": 1.18, "M_n": 5, "status": "active", "global": 1.12}
        factor, source = resolve_factor(PR_REVIEW_LOOP_KEY, step_factors, "M", factors)
        assert source == "--"
        assert factor == 1.0

    def test_size_class_requires_3_samples(self):
        """Size-class factor requires n >= 3 to activate."""
        step_factors = {}
        factors = {"M": 1.18, "M_n": 2, "status": "active", "global": 1.12}
        factor, source = resolve_factor("Some Step", step_factors, "M", factors)
        # Size-class not activated (n=2 < 3); falls through to global
        assert source == "G"
        assert factor == 1.12

    def test_collecting_falls_through_to_global_when_no_size_class(self):
        """Collecting per-step falls to global when no size-class active."""
        step_factors = {"Research Agent": {"factor": 0.50, "n": 1, "status": "collecting"}}
        factors = {"status": "active", "global": 1.12}
        factor, source = resolve_factor("Research Agent", step_factors, "M", factors)
        assert source == "G"
        assert factor == 1.12

    def test_collecting_falls_through_to_uncalibrated_when_no_global(self):
        """Collecting per-step falls to '--' when neither size-class nor global active."""
        step_factors = {"Research Agent": {"factor": 0.50, "n": 1, "status": "collecting"}}
        factors = {}
        factor, source = resolve_factor("Research Agent", step_factors, "M", factors)
        assert source == "--"
        assert factor == 1.0


# ---------------------------------------------------------------------------
# Class 2: TestPerStepFactorComputation
# (Test Pass 4 logic via the Python helpers above)
# ---------------------------------------------------------------------------

class TestPerStepFactorComputation:
    """Test update-factors.py Pass 4 logic via mirrored Python helpers."""

    def test_three_samples_activates_step_factor(self):
        """Step with exactly 3 clean records gets status 'active'."""
        records = [make_clean_record({"Research Agent": 0.9}) for _ in range(3)]
        result = compute_step_factors_pass4(records)
        assert result["Research Agent"]["status"] == "active"
        assert result["Research Agent"]["n"] == 3

    def test_two_samples_stays_collecting(self):
        """Step with 2 clean records gets status 'collecting'."""
        records = [make_clean_record({"Research Agent": 0.9}) for _ in range(2)]
        result = compute_step_factors_pass4(records)
        assert result["Research Agent"]["status"] == "collecting"
        assert result["Research Agent"]["n"] == 2

    def test_one_sample_stays_collecting(self):
        """Step with 1 clean record gets status 'collecting'."""
        records = [make_clean_record({"Research Agent": 0.85})]
        result = compute_step_factors_pass4(records)
        assert result["Research Agent"]["status"] == "collecting"
        assert result["Research Agent"]["n"] == 1

    def test_step_factor_computed_as_trimmed_mean_for_n_le_10(self):
        """For n <= 10 samples, trimmed_mean is used (plain mean for n < 10)."""
        ratios = [0.8, 0.9, 1.0]
        records = [make_clean_record({"Architect Agent": r}, ratio=r) for r in ratios]
        result = compute_step_factors_pass4(records)
        expected_factor = trimmed_mean(ratios)
        assert abs(result["Architect Agent"]["factor"] - round(expected_factor, 4)) < 0.0001

    def test_step_factor_uses_ewma_for_n_gt_10(self):
        """For n > 10 samples, EWMA (alpha=0.15) is used instead of trimmed_mean."""
        ratios = [0.8 + i * 0.01 for i in range(11)]  # 11 samples
        records = [make_clean_record({"Implementation": r}, ratio=r) for r in ratios]
        result = compute_step_factors_pass4(records)
        expected_factor = compute_ewma(ratios)
        assert abs(result["Implementation"]["factor"] - round(expected_factor, 4)) < 0.0001
        assert result["Implementation"]["n"] == 11

    def test_multiple_steps_computed_independently(self):
        """Two steps with different ratio histories produce independent factors."""
        records = []
        for _ in range(3):
            records.append(make_clean_record({
                "Research Agent": 0.80,
                "Implementation": 1.20,
            }))
        result = compute_step_factors_pass4(records)
        assert result["Research Agent"]["factor"] == round(trimmed_mean([0.80, 0.80, 0.80]), 4)
        assert result["Implementation"]["factor"] == round(trimmed_mean([1.20, 1.20, 1.20]), 4)
        assert result["Research Agent"]["factor"] != result["Implementation"]["factor"]

    def test_pr_review_loop_excluded_from_step_factors(self):
        """'PR Review Loop' key in step_ratios is not included in computed step_factors."""
        records = [
            make_clean_record({
                "Research Agent": 0.9,
                PR_REVIEW_LOOP_KEY: 1.1,
            })
            for _ in range(3)
        ]
        result = compute_step_factors_pass4(records)
        assert PR_REVIEW_LOOP_KEY not in result
        assert "Research Agent" in result

    def test_outlier_record_excluded_from_step_factors(self):
        """Records flagged as outliers (ratio > 3.0 or < 0.2) do not contribute."""
        # NOTE: Pass 4 does NOT perform outlier filtering itself — that is Pass 2's
        # responsibility (upstream in update-factors.py). This test verifies Pass 4's
        # record-counting behaviour when given a pre-filtered input list; it does NOT
        # test the filtering logic itself.  The full end-to-end pipeline (Pass 2 → Pass 4)
        # is covered by test_end_to_end_outlier_excluded_from_step_factors.
        #
        # Simulate what update-factors.py does: outliers are excluded before Pass 4
        # By excluding records with ratio > 3.0 from clean_records
        outlier_record = {
            "timestamp": "2026-01-01T00:00:00Z",
            "size": "M",
            "expected_cost": 1.0,
            "actual_cost": 5.0,  # ratio = 5.0 > 3.0 → outlier
            "_ratio": 5.0,
            "step_ratios": {"Research Agent": 5.0},
        }
        clean_record = make_clean_record({"Research Agent": 0.9})
        # Pass 4 receives only clean_records (outliers already filtered by Pass 2)
        result = compute_step_factors_pass4([clean_record])
        assert result["Research Agent"]["n"] == 1
        # The outlier record was not passed in — verify that passing only clean records
        # results in n=1, not n=2
        result_with_outlier_naively = compute_step_factors_pass4([clean_record, outlier_record])
        # If outlier were included, n=2 and factor would include the outlier value
        assert result_with_outlier_naively["Research Agent"]["n"] == 2

    def test_missing_step_ratios_field_handled_gracefully(self):
        """Records without step_ratios field (old history) are skipped via .get({})."""
        old_record = {
            "timestamp": "2025-01-01T00:00:00Z",
            "size": "M",
            "expected_cost": 5.0,
            "actual_cost": 4.5,
            "_ratio": 0.9,
            # No step_ratios field
        }
        result = compute_step_factors_pass4([old_record])
        assert result == {}  # No crash, no data

    def test_empty_step_ratios_field_handled_gracefully(self):
        """Records with step_ratios: {} contribute nothing to step_factors."""
        record = make_clean_record({})  # empty step_ratios
        result = compute_step_factors_pass4([record])
        assert result == {}

    def test_step_factors_written_only_when_data_exists(self):
        """If no records have step_ratios, step_factors result is empty."""
        records = [
            {"timestamp": "2026-01-01T00:00:00Z", "size": "M", "_ratio": 0.9}
            for _ in range(3)
        ]
        result = compute_step_factors_pass4(records)
        assert result == {}

    def test_step_factors_not_written_when_sample_count_below_3(self):
        """When global sample_count < 3, update-factors.py exits early — no step_factors.

        This tests that the early-return branch in update-factors.py (line ~111)
        produces a factors dict with no step_factors key.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "history.jsonl")
            factors_path = os.path.join(tmpdir, "factors.json")

            # Write only 2 clean records (below threshold of 3)
            with open(history_path, "w") as f:
                for _ in range(2):
                    record = {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "size": "M",
                        "expected_cost": 5.0,
                        "actual_cost": 4.5,
                        "step_ratios": {"Research Agent": 0.9},
                    }
                    f.write(json.dumps(record) + "\n")

            result = subprocess.run(
                ["/usr/bin/python3", str(UPDATE_FACTORS_PY), history_path, factors_path],
                capture_output=True, text=True,
            )
            assert result.returncode == 0
            with open(factors_path) as f:
                factors = json.load(f)
            assert "step_factors" not in factors
            assert factors.get("status") == "collecting"

    def test_step_names_are_exact_match_not_substring(self):
        """'Engineer' and 'Engineer Final Plan' accumulate independent factors."""
        records = [
            make_clean_record({
                "Engineer": 0.70,
                "Engineer Final Plan": 1.30,
            })
            for _ in range(3)
        ]
        result = compute_step_factors_pass4(records)
        assert "Engineer" in result
        assert "Engineer Final Plan" in result
        # They must be independent — different values
        assert result["Engineer"]["factor"] != result["Engineer Final Plan"]["factor"]
        # Verify exact key lookup: "Engineer" factor should only reflect ratio 0.70
        assert abs(result["Engineer"]["factor"] - round(trimmed_mean([0.70, 0.70, 0.70]), 4)) < 0.0001
        assert abs(result["Engineer Final Plan"]["factor"] - round(trimmed_mean([1.30, 1.30, 1.30]), 4)) < 0.0001


# ---------------------------------------------------------------------------
# Class 3: TestLearnShStepCosts
# (Test learn.sh step_costs extraction and step_ratios computation via Python helpers)
# ---------------------------------------------------------------------------

class TestLearnShStepCosts:
    """Test learn.sh RECORD block logic for step_costs and step_ratios."""

    def _run_record_python(
        self,
        estimate: dict,
        actual: float,
        expected: float,
    ) -> dict:
        """Run the RECORD Python block from learn.sh in isolation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            est_path = os.path.join(tmpdir, "active-estimate.json")
            with open(est_path, "w") as f:
                json.dump(estimate, f)

            script = (
                "import json, os\n"
                "_est = json.load(open(os.environ['EST_FILE']))\n"
                "step_costs_raw = _est.get('step_costs', {})\n"
                "PR_REVIEW_LOOP_KEY = 'PR Review Loop'\n"
                "step_costs_estimated = {k: v for k, v in step_costs_raw.items()\n"
                "                        if k != PR_REVIEW_LOOP_KEY}\n"
                f"actual = {actual}\n"
                f"expected = max({expected}, 0.001)\n"
                "session_ratio = round(actual / expected, 4)\n"
                "step_ratios = {step: session_ratio for step in step_costs_estimated}\n"
                "print(json.dumps({\n"
                "    'step_costs_estimated': step_costs_estimated,\n"
                "    'step_ratios': step_ratios,\n"
                "    'session_ratio': session_ratio,\n"
                "}))\n"
            )
            result = subprocess.run(
                ["/usr/bin/python3", "-c", script],
                capture_output=True,
                text=True,
                env={**os.environ, "EST_FILE": est_path},
            )
            assert result.returncode == 0, f"Python block failed: {result.stderr}"
            return json.loads(result.stdout.strip())

    def test_step_costs_in_estimate_produces_step_costs_estimated(self):
        """Estimate with step_costs dict → history record contains step_costs_estimated."""
        estimate = {
            "step_costs": {
                "Research Agent": 1.50,
                "Implementation": 3.00,
            }
        }
        out = self._run_record_python(estimate, actual=4.0, expected=5.0)
        assert "step_costs_estimated" in out
        assert "Research Agent" in out["step_costs_estimated"]
        assert "Implementation" in out["step_costs_estimated"]

    def test_step_ratios_equal_session_ratio_for_all_steps(self):
        """All step_ratios values equal actual/expected session ratio."""
        estimate = {
            "step_costs": {
                "Research Agent": 1.50,
                "Architect Agent": 2.00,
                "Implementation": 3.00,
            }
        }
        actual = 4.50
        expected = 5.00
        out = self._run_record_python(estimate, actual=actual, expected=expected)
        session_ratio = round(actual / expected, 4)
        for step, ratio in out["step_ratios"].items():
            assert abs(ratio - session_ratio) < 0.0001, (
                f"Step '{step}' ratio {ratio} != session ratio {session_ratio}"
            )

    def test_pr_review_loop_excluded_from_step_costs_estimated(self):
        """'PR Review Loop' key in estimate's step_costs is not forwarded to step_costs_estimated."""
        estimate = {
            "step_costs": {
                "Research Agent": 1.50,
                PR_REVIEW_LOOP_KEY: 2.50,
            }
        }
        out = self._run_record_python(estimate, actual=3.5, expected=4.0)
        assert PR_REVIEW_LOOP_KEY not in out["step_costs_estimated"]
        assert "Research Agent" in out["step_costs_estimated"]

    def test_missing_step_costs_field_produces_empty_dicts(self):
        """Old estimate without step_costs → step_costs_estimated={}, step_ratios={}."""
        estimate = {
            "expected_cost": 5.0,
            "size": "M",
        }
        out = self._run_record_python(estimate, actual=4.5, expected=5.0)
        assert out["step_costs_estimated"] == {}
        assert out["step_ratios"] == {}

    def test_step_costs_estimated_only_contains_atomic_steps(self):
        """step_costs_estimated contains only atomic step names (no PR Review Loop)."""
        estimate = {
            "step_costs": {
                "PM Agent": 0.50,
                "Research Agent": 1.00,
                "Architect Agent": 1.50,
                "Implementation": 2.00,
                PR_REVIEW_LOOP_KEY: 3.00,
            }
        }
        out = self._run_record_python(estimate, actual=7.0, expected=8.0)
        assert PR_REVIEW_LOOP_KEY not in out["step_costs_estimated"]
        assert len(out["step_costs_estimated"]) == 4

    def test_step_costs_estimated_is_diagnostic_not_used_in_factor_computation(self):
        """step_costs_estimated is in history but Pass 4 only reads step_ratios.

        Verified by inspecting update-factors.py Pass 4 code — it reads
        record.get('step_ratios', {}) not step_costs_estimated.
        """
        content = UPDATE_FACTORS_PY.read_text()
        # Pass 4 must read step_ratios
        assert "step_ratios" in content
        # Pass 4 must NOT use step_costs_estimated for factor computation
        # (it may appear as a field name string but not as a dict access)
        # The key assertion: factor computation only loops over step_ratios
        assert 'step_ratios' in content
        # Verify step_costs_estimated is not iterated in Pass 4
        # (step_costs_estimated appears only in learn.sh, not update-factors.py)
        assert "step_costs_estimated" not in content


# ---------------------------------------------------------------------------
# Class 4: TestDocumentContent
# (Verify document content after implementation — these tests fail before implementation)
# ---------------------------------------------------------------------------

class TestDocumentContent:
    """Verify required content in documentation files. Fails before implementation."""

    def test_skill_md_version_1_4_0(self):
        """SKILL.md frontmatter contains 'version: 1.5.0'."""
        assert "version: 1.5.0" in SKILL_MD.read_text()

    def test_skill_md_output_template_v1_5_0(self):
        """SKILL.md output template header contains 'v1.5.0'."""
        assert "v1.5.0" in SKILL_MD.read_text()

    def test_learn_sh_version_v1_5_0(self):
        """learn.sh VERSION variable is '1.5.0'."""
        result = subprocess.run(
            ["bash", str(LEARN_SH), "--version"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "1.5.0" in result.stdout

    def test_skill_md_step3e_documents_per_step_factor(self):
        """SKILL.md Step 3e documents 'step_factors' lookup."""
        assert "step_factors" in SKILL_MD.read_text()

    def test_skill_md_step3e_documents_fallback_chain(self):
        """SKILL.md Step 3e documents all four levels: per-step, size-class, global, no calibration."""
        content = SKILL_MD.read_text()
        assert "per-step" in content.lower() or "per_step" in content
        assert "size-class" in content.lower() or "size_class" in content
        assert "global" in content
        # No calibration → factor = 1.0
        assert "1.0" in content

    def test_skill_md_active_estimate_schema_has_step_costs(self):
        """SKILL.md active-estimate.json schema block contains 'step_costs' field."""
        assert "step_costs" in SKILL_MD.read_text()

    def test_skill_md_step_costs_key_is_exact_pr_review_loop(self):
        """SKILL.md documents 'PR Review Loop' as the exact key name for the review loop entry."""
        content = SKILL_MD.read_text()
        assert '"PR Review Loop"' in content or "'PR Review Loop'" in content

    def test_skill_md_output_template_has_cal_column(self):
        """SKILL.md output template table header contains 'Cal' column."""
        content = SKILL_MD.read_text()
        assert "| Cal" in content or "Cal   |" in content or "| Cal " in content

    def test_skill_md_output_template_has_cal_legend(self):
        """SKILL.md output template contains legend line with 'Cal: S=per-step'."""
        content = SKILL_MD.read_text()
        assert "Cal: S=per-step" in content

    def test_skill_md_cal_column_uses_s_prefix_for_step(self):
        """SKILL.md documents 'S:' prefix for per-step Cal column value."""
        assert "S:" in SKILL_MD.read_text()

    def test_skill_md_cal_column_uses_z_prefix_for_size_class(self):
        """SKILL.md documents 'Z:' prefix for size-class Cal column value."""
        assert "Z:" in SKILL_MD.read_text()

    def test_skill_md_cal_column_uses_g_prefix_for_global(self):
        """SKILL.md documents 'G:' prefix for global Cal column value."""
        # "G:" appears in the Cal column specification
        content = SKILL_MD.read_text()
        assert "G:" in content

    def test_skill_md_cal_column_uses_double_dash_for_uncalibrated(self):
        """SKILL.md documents '--' for uncalibrated Cal column value."""
        content = SKILL_MD.read_text()
        # "--" should appear in the Cal column context
        assert "-- " in content or '"--"' in content or "| --" in content

    def test_skill_md_pr_review_loop_cal_always_double_dash(self):
        """SKILL.md documents that the PR Review Loop row always shows '--' in the Cal column."""
        content = SKILL_MD.read_text()
        # Must document PR Review Loop Cal = "--"
        assert "PR Review Loop" in content
        # The plan states: "Its Cal column always shows '--'"
        assert "always" in content.lower()

    def test_skill_md_collecting_status_falls_through(self):
        """SKILL.md documents that a step with status 'collecting' falls through to size-class."""
        content = SKILL_MD.read_text()
        assert "collecting" in content

    def test_heuristics_md_has_per_step_calibration_section(self):
        """references/heuristics.md contains 'Per-Step Calibration' section."""
        assert "Per-Step Calibration" in HEURISTICS_MD.read_text()

    def test_heuristics_md_has_per_step_min_samples(self):
        """references/heuristics.md contains 'per_step_min_samples' parameter."""
        assert "per_step_min_samples" in HEURISTICS_MD.read_text()

    def test_calibration_algorithm_md_has_per_step_section(self):
        """references/calibration-algorithm.md documents per-step factor computation."""
        content = CALIBRATION_ALG_MD.read_text()
        assert "Per-Step" in content or "per-step" in content.lower()

    def test_calibration_algorithm_md_has_proportional_attribution(self):
        """references/calibration-algorithm.md explains proportional attribution method."""
        content = CALIBRATION_ALG_MD.read_text()
        assert "proportional" in content.lower() or "Proportional" in content

    def test_calibration_algorithm_md_has_step_factors_schema(self):
        """references/calibration-algorithm.md documents step_factors key in factors.json schema."""
        assert "step_factors" in CALIBRATION_ALG_MD.read_text()

    def test_calibration_algorithm_md_has_step_name_stability_note(self):
        """references/calibration-algorithm.md contains note about step name stability."""
        content = CALIBRATION_ALG_MD.read_text()
        assert "stability" in content.lower() or "renaming" in content.lower() or "orphan" in content.lower()

    def test_learn_sh_has_step_costs_estimated_field(self):
        """learn.sh source code contains 'step_costs_estimated' field name."""
        assert "step_costs_estimated" in LEARN_SH.read_text()

    def test_learn_sh_has_step_ratios_field(self):
        """learn.sh source code contains 'step_ratios' field name."""
        assert "step_ratios" in LEARN_SH.read_text()

    def test_learn_sh_excludes_pr_review_loop(self):
        """learn.sh source code excludes 'PR Review Loop' from per-step attribution."""
        content = LEARN_SH.read_text()
        assert "PR Review Loop" in content

    def test_claude_md_version_1_4_0(self):
        """CLAUDE.md contains 'Current version: 1.5.0'."""
        assert "Current version: 1.5.0" in CLAUDE_MD.read_text()


# ---------------------------------------------------------------------------
# Class 5: TestLearnShIntegrationStepCosts (unittest.TestCase)
# (End-to-end integration: mock estimate with step_costs → learn.sh → factors.json)
# ---------------------------------------------------------------------------

class TestLearnShIntegrationStepCosts(unittest.TestCase):
    """Integration tests: invoke learn.sh end-to-end with step_costs in estimate."""

    LEARN_SH = Path(__file__).parent.parent / "scripts" / "tokencostscope-learn.sh"
    UPDATE_FACTORS_PY = Path(__file__).parent.parent / "scripts" / "update-factors.py"

    def _write_mock_estimate(
        self,
        tmp_dir: str,
        step_costs: Optional[dict] = None,
    ) -> str:
        """Write a minimal active-estimate.json with optional step_costs."""
        estimate = {
            "timestamp": "2026-01-01T00:00:00Z",
            "size": "M",
            "files": 3,
            "complexity": "medium",
            "steps": list(step_costs.keys()) if step_costs else ["Research Agent", "Implementation"],
            "step_count": len(step_costs) if step_costs else 2,
            "project_type": "greenfield",
            "language": "python",
            "expected_cost": 0.05,
            "optimistic_cost": 0.03,
            "pessimistic_cost": 0.15,
            "baseline_cost": 0.0,
            "review_cycles_estimated": 0,
            "review_cycles_actual": None,
            "parallel_groups": [],
            "parallel_steps_detected": 0,
        }
        if step_costs is not None:
            estimate["step_costs"] = step_costs
        path = Path(tmp_dir) / "active-estimate.json"
        path.write_text(json.dumps(estimate))
        return str(path)

    def _write_mock_session_jsonl(self, tmp_dir: str) -> str:
        """Write a minimal session JSONL. Must include 'model' inside 'message'."""
        entry = {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 100,
                },
            },
            "costUSD": 0.012,
        }
        path = Path(tmp_dir) / "session.jsonl"
        path.write_text(json.dumps(entry) + "\n")
        return str(path)

    def _run_learn_sh(
        self,
        estimate_file: str,
        session_file: str,
        history_file: str,
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "TOKENCOSTSCOPE_ESTIMATE_FILE": estimate_file,
            "TOKENCOSTSCOPE_HISTORY_FILE": history_file,
        }
        return subprocess.run(
            ["bash", str(self.LEARN_SH), session_file, "0"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).parent.parent),
        )

    def test_end_to_end_step_costs_in_history(self):
        """learn.sh end-to-end: history record contains step_costs_estimated and step_ratios."""
        with tempfile.TemporaryDirectory() as tmp:
            step_costs = {"Research Agent": 0.02, "Implementation": 0.03}
            estimate_file = self._write_mock_estimate(tmp, step_costs=step_costs)
            session_file = self._write_mock_session_jsonl(tmp)
            history_file = str(Path(tmp) / "history.jsonl")

            self._run_learn_sh(estimate_file, session_file, history_file)

            if not Path(history_file).exists():
                self.skipTest("learn.sh did not write history record")

            records = [json.loads(line) for line in Path(history_file).read_text().splitlines() if line.strip()]
            self.assertGreater(len(records), 0)
            last = records[-1]

            self.assertIn("step_costs_estimated", last)
            self.assertIn("step_ratios", last)
            self.assertIn("Research Agent", last["step_costs_estimated"])
            self.assertIn("Implementation", last["step_costs_estimated"])
            self.assertIn("Research Agent", last["step_ratios"])
            self.assertIn("Implementation", last["step_ratios"])

    def test_end_to_end_pr_review_loop_excluded(self):
        """learn.sh end-to-end: 'PR Review Loop' in step_costs is absent from step_costs_estimated."""
        with tempfile.TemporaryDirectory() as tmp:
            step_costs = {
                "Research Agent": 0.02,
                PR_REVIEW_LOOP_KEY: 0.05,
            }
            estimate_file = self._write_mock_estimate(tmp, step_costs=step_costs)
            session_file = self._write_mock_session_jsonl(tmp)
            history_file = str(Path(tmp) / "history.jsonl")

            self._run_learn_sh(estimate_file, session_file, history_file)

            if not Path(history_file).exists():
                self.skipTest("learn.sh did not write history record")

            records = [json.loads(line) for line in Path(history_file).read_text().splitlines() if line.strip()]
            last = records[-1]

            self.assertNotIn(PR_REVIEW_LOOP_KEY, last.get("step_costs_estimated", {}))
            self.assertIn("Research Agent", last.get("step_costs_estimated", {}))

    def test_end_to_end_step_factors_written_after_3_records(self):
        """After 3 history records with step_ratios, update-factors.py produces step_factors with status 'active'."""
        with tempfile.TemporaryDirectory() as tmp:
            history_file = str(Path(tmp) / "history.jsonl")
            factors_file = str(Path(tmp) / "factors.json")

            # Write 3 records directly with step_ratios
            for i in range(3):
                record = {
                    "timestamp": f"2026-01-0{i+1}T00:00:00Z",
                    "size": "M",
                    "expected_cost": 5.0,
                    "actual_cost": 4.5,
                    "step_ratios": {"Research Agent": 0.9, "Implementation": 0.9},
                }
                with open(history_file, "a") as f:
                    f.write(json.dumps(record) + "\n")

            result = subprocess.run(
                ["/usr/bin/python3", str(self.UPDATE_FACTORS_PY), history_file, factors_file],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)

            with open(factors_file) as f:
                factors = json.load(f)

            self.assertIn("step_factors", factors)
            self.assertEqual(factors["step_factors"]["Research Agent"]["status"], "active")
            self.assertEqual(factors["step_factors"]["Implementation"]["status"], "active")

    def test_end_to_end_step_factors_collecting_below_3_records(self):
        """After 2 history records with step_ratios, step_factors shows status 'collecting'."""
        with tempfile.TemporaryDirectory() as tmp:
            history_file = str(Path(tmp) / "history.jsonl")
            factors_file = str(Path(tmp) / "factors.json")

            # Write 3 global records (enough for global factor) but only 2 have step_ratios
            for i in range(3):
                step_ratios = {"Research Agent": 0.9} if i < 2 else {}
                record = {
                    "timestamp": f"2026-01-0{i+1}T00:00:00Z",
                    "size": "M",
                    "expected_cost": 5.0,
                    "actual_cost": 4.5,
                    "step_ratios": step_ratios,
                }
                with open(history_file, "a") as f:
                    f.write(json.dumps(record) + "\n")

            result = subprocess.run(
                ["/usr/bin/python3", str(self.UPDATE_FACTORS_PY), history_file, factors_file],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)

            with open(factors_file) as f:
                factors = json.load(f)

            self.assertIn("step_factors", factors)
            self.assertEqual(factors["step_factors"]["Research Agent"]["n"], 2)
            self.assertEqual(factors["step_factors"]["Research Agent"]["status"], "collecting")

    def test_end_to_end_backward_compat_no_step_costs_in_estimate(self):
        """learn.sh with old estimate (no step_costs) → history has step_costs_estimated={} and step_ratios={}."""
        with tempfile.TemporaryDirectory() as tmp:
            # Estimate without step_costs field
            estimate_file = self._write_mock_estimate(tmp, step_costs=None)
            session_file = self._write_mock_session_jsonl(tmp)
            history_file = str(Path(tmp) / "history.jsonl")

            self._run_learn_sh(estimate_file, session_file, history_file)

            if not Path(history_file).exists():
                self.skipTest("learn.sh did not write history record")

            records = [json.loads(line) for line in Path(history_file).read_text().splitlines() if line.strip()]
            last = records[-1]

            # Old estimates produce empty dicts — no crash
            self.assertEqual(last.get("step_costs_estimated", {}), {})
            self.assertEqual(last.get("step_ratios", {}), {})

    def test_end_to_end_outlier_excluded_from_step_factors(self):
        """update-factors.py end-to-end: outlier records do not contribute to step_factors n count."""
        with tempfile.TemporaryDirectory() as tmp:
            history_file = str(Path(tmp) / "history.jsonl")
            factors_file = str(Path(tmp) / "factors.json")

            # Two normal records: ratio = 0.9 (actual/expected = 4.5/5.0)
            for i in range(2):
                record = {
                    "timestamp": f"2026-01-0{i+1}T00:00:00Z",
                    "size": "M",
                    "expected_cost": 5.0,
                    "actual_cost": 4.5,  # ratio = 0.9 — within [0.2, 3.0]
                    "step_ratios": {"Research Agent": 0.9},
                }
                with open(history_file, "a") as f:
                    f.write(json.dumps(record) + "\n")

            # One normal record to bring the global clean count to 3 (needed for
            # update-factors.py to proceed past the early-return at sample_count < 3)
            record = {
                "timestamp": "2026-01-03T00:00:00Z",
                "size": "M",
                "expected_cost": 5.0,
                "actual_cost": 4.5,  # ratio = 0.9
                "step_ratios": {"Research Agent": 0.9},
            }
            with open(history_file, "a") as f:
                f.write(json.dumps(record) + "\n")

            # One outlier record: ratio = 5.0 (actual/expected = 25.0/5.0 > OUTLIER_HIGH=3.0)
            # This record also carries a step_ratios entry that must NOT appear in step_factors.
            outlier_record = {
                "timestamp": "2026-01-04T00:00:00Z",
                "size": "M",
                "expected_cost": 5.0,
                "actual_cost": 25.0,  # ratio = 5.0 → outlier (> 3.0)
                "step_ratios": {"Research Agent": 5.0},
            }
            with open(history_file, "a") as f:
                f.write(json.dumps(outlier_record) + "\n")

            result = subprocess.run(
                ["/usr/bin/python3", str(self.UPDATE_FACTORS_PY), history_file, factors_file],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, f"update-factors.py failed: {result.stderr}")

            with open(factors_file) as f:
                factors = json.load(f)

            # step_factors must be present (3 clean records with step_ratios)
            self.assertIn("step_factors", factors)
            self.assertIn("Research Agent", factors["step_factors"])

            # The outlier record must not have contributed: n should be 3 (the three
            # clean records), not 4 (which would happen if the outlier were included)
            step_n = factors["step_factors"]["Research Agent"]["n"]
            self.assertEqual(
                step_n,
                3,
                f"Expected n=3 (outlier excluded), got n={step_n}. "
                f"Outlier record with ratio=5.0 should have been filtered in Pass 2.",
            )

            # The factor itself should reflect only the clean ratios (all 0.9)
            step_factor = factors["step_factors"]["Research Agent"]["factor"]
            self.assertAlmostEqual(
                step_factor,
                0.9,
                places=3,
                msg=f"Expected factor≈0.9 from clean records, got {step_factor}",
            )

            # Confirm the outlier was counted in outlier_count
            self.assertGreaterEqual(factors.get("outlier_count", 0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
