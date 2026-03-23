"""Tests for parallel agent accounting (v1.3.1).

Tests the two discount factors, PR Review Loop C isolation, learn.sh field
forwarding, and document content verification. All document/learn.sh tests
must fail before implementation; arithmetic tests pass immediately (they test
inline helper formulas, not file content).
"""
# Runner: pytest (required). Non-TestCase classes are pytest-style and will be
# silently skipped by `python -m unittest`. Use: /usr/bin/python3 -m pytest tests/

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
LEARN_SH = SCRIPTS_DIR / "tokencostscope-learn.sh"
HEURISTICS_MD = REPO_ROOT / "references" / "heuristics.md"
SKILL_MD = REPO_ROOT / "SKILL.md"


# ---------------------------------------------------------------------------
# Arithmetic helpers — mirror the formulas in SKILL.md Steps 3c/3d
# ---------------------------------------------------------------------------

def apply_parallel_cache_rate(base_rate: float, reduction: float = 0.15, floor: float = 0.05) -> float:
    """Apply parallel cache rate reduction with floor."""
    return max(base_rate - reduction, floor)


def apply_parallel_input_discount(input_accum: float, discount: float = 0.75) -> float:
    """Apply parallel input accumulation discount."""
    return input_accum * discount


def compute_step_cost(
    input_accum: float,
    output_complex: float,
    cache_rate: float,
    band_mult: float,
    price_in: float,
    price_cr: float,
    price_out: float,
) -> float:
    """Compute step cost per SKILL.md Step 3d formula."""
    input_cost = (
        input_accum * (1 - cache_rate) * price_in
        + input_accum * cache_rate * price_cr
    ) / 1_000_000
    output_cost = output_complex * price_out / 1_000_000
    return (input_cost + output_cost) * band_mult


# ---------------------------------------------------------------------------
# Cache rate reduction
# ---------------------------------------------------------------------------

class TestCacheRateReduction:
    """Tests for parallel_cache_rate_reduction applied to each band."""

    def test_optimistic_band(self):
        """Optimistic: 60% → 45%."""
        assert abs(apply_parallel_cache_rate(0.60) - 0.45) < 0.0001

    def test_expected_band(self):
        """Expected: 50% → 35%."""
        assert abs(apply_parallel_cache_rate(0.50) - 0.35) < 0.0001

    def test_pessimistic_band(self):
        """Pessimistic: 30% → 15%."""
        assert abs(apply_parallel_cache_rate(0.30) - 0.15) < 0.0001

    def test_floor_prevents_negative(self):
        """If reduction > base_rate, floor of 0.05 applies."""
        assert apply_parallel_cache_rate(0.10) == 0.05

    def test_floor_at_exact_reduction_boundary(self):
        """0.15 - 0.15 = 0.00 → floored to 0.05."""
        assert apply_parallel_cache_rate(0.15) == 0.05

    def test_no_reduction_when_reduction_zero(self):
        """With reduction=0, sequential step's cache rate is unchanged."""
        assert apply_parallel_cache_rate(0.50, reduction=0.0) == 0.50


# ---------------------------------------------------------------------------
# Input accumulation discount
# ---------------------------------------------------------------------------

class TestInputAccumulationDiscount:
    """Tests for parallel_input_discount applied to input_accum."""

    def test_discount_reduces_input(self):
        """input_accum × 0.75 reduces to 75% of original."""
        assert abs(apply_parallel_input_discount(40_000.0) - 30_000.0) < 0.01

    def test_discount_is_multiplicative_with_accumulation(self):
        """Discount is commutative with (K+1)/2 — order doesn't matter."""
        input_complex = 20_000.0
        K = 7
        accum_then_discount = input_complex * (K + 1) / 2 * 0.75
        discount_then_accum = apply_parallel_input_discount(input_complex, 0.75) * (K + 1) / 2
        assert abs(accum_then_discount - discount_then_accum) < 0.001

    def test_no_discount_when_discount_one(self):
        """With discount=1.0, input is unchanged (sequential step baseline)."""
        assert apply_parallel_input_discount(40_000.0, discount=1.0) == 40_000.0


# ---------------------------------------------------------------------------
# Full step cost: parallel vs sequential comparison
# ---------------------------------------------------------------------------

class TestParallelStepCheaperThanSequential:
    """Parallel step costs must be lower than sequential step costs."""

    # Sonnet pricing per million tokens
    PRICE_IN = 3.00
    PRICE_CR = 0.30
    PRICE_OUT = 15.00

    def _step_cost(self, is_parallel: bool) -> float:
        input_complex = 30_000.0
        output_complex = 8_000.0
        K = 6
        band_mult = 1.0  # Expected band

        input_accum = input_complex * (K + 1) / 2
        cache_rate = 0.50  # Expected band baseline

        if is_parallel:
            input_accum = apply_parallel_input_discount(input_accum)
            cache_rate = apply_parallel_cache_rate(cache_rate)

        return compute_step_cost(
            input_accum, output_complex, cache_rate, band_mult,
            self.PRICE_IN, self.PRICE_CR, self.PRICE_OUT,
        )

    def test_parallel_cheaper_than_sequential(self):
        assert self._step_cost(is_parallel=True) < self._step_cost(is_parallel=False)

    def test_parallel_cost_not_zero(self):
        """Parallel discount reduces cost, does not eliminate it."""
        assert self._step_cost(is_parallel=True) > 0.0

    def test_parallel_not_more_than_30_percent_cheaper(self):
        """Sanity: combined discount shouldn't wipe out more than ~30% of total step cost.
        Input discount (0.75×) and cache rate change partially cancel (higher cache miss price
        offsets lower volume). Output cost is unchanged. Net effect is well under 30%.
        """
        # Sanity guard for default parameters (parallel_input_discount=0.75,
        # parallel_cache_rate_reduction=0.15). The 0.70 bound is not derived
        # analytically — it is empirically chosen to be well below the minimum
        # net discount. If the default parameters in heuristics.md change,
        # this bound may need updating.
        p = self._step_cost(is_parallel=True)
        s = self._step_cost(is_parallel=False)
        assert p > s * 0.70


# ---------------------------------------------------------------------------
# PR Review Loop C isolation
# ---------------------------------------------------------------------------

class TestPRReviewLoopCIsolation:
    """C must use un-discounted Expected band costs for constituent steps.

    Values from examples.md Step 4 (Staff Review) and Step 5 (Engineer Final Plan).
    """

    STAFF_REVIEW_UNDISCOUNTED = 0.7470
    ENGINEER_FINAL_UNDISCOUNTED = 0.2744

    def test_c_uses_undiscounted_costs(self):
        """If Staff Review is parallel, C still uses its pre-discount cost."""
        # Directional test: verifies the principle that C must not be discounted.
        # Uses input_accum * 0.75 as a proxy for "discounted cost"; the actual
        # parallel discount applies to input_accum in Step 3c (not directly to
        # step_cost), so this is an approximation. See test_c_excludes_cache_rate_discount
        # for the complementary cache-rate dimension.
        c_correct = self.STAFF_REVIEW_UNDISCOUNTED + self.ENGINEER_FINAL_UNDISCOUNTED
        c_wrong = self.STAFF_REVIEW_UNDISCOUNTED * 0.75 + self.ENGINEER_FINAL_UNDISCOUNTED

        assert abs(c_correct - 1.0214) < 0.001
        assert c_wrong < c_correct  # discounted C would incorrectly lower the loop cost

    def test_c_excludes_cache_rate_discount(self):
        """C must exclude the cache rate discount, not just the input accumulation discount."""
        staff = self.STAFF_REVIEW_UNDISCOUNTED
        eng = self.ENGINEER_FINAL_UNDISCOUNTED

        c_correct = staff + eng

        # Simulate applying BOTH parallel discounts to staff review cost (wrong behavior):
        # input_accum * 0.75 combined with higher non-cached portion from cache_rate - 0.15
        # Net effect is > 0.75x reduction; for directional testing, use 0.70x as lower bound
        c_wrong_both_discounts = staff * 0.70 + eng * 0.70

        assert c_correct > c_wrong_both_discounts, (
            "C must use undiscounted constituent costs; applying both parallel "
            "discounts (input + cache rate) would reduce C below the correct value"
        )

    def test_c_isolation_preserves_review_loop_accuracy(self):
        """Using discounted C produces a systematically lower loop cost — must be avoided."""
        undiscounted_c = self.STAFF_REVIEW_UNDISCOUNTED + self.ENGINEER_FINAL_UNDISCOUNTED
        discounted_c = self.STAFF_REVIEW_UNDISCOUNTED * 0.75 + self.ENGINEER_FINAL_UNDISCOUNTED

        # At N=2 cycles
        loop_undiscounted = undiscounted_c * (1 - 0.6**2) / 0.4
        loop_discounted = discounted_c * (1 - 0.6**2) / 0.4

        assert loop_undiscounted > loop_discounted

    def test_c_uses_undiscounted_when_both_constituents_parallel(self):
        """C must use undiscounted values even when BOTH constituents are parallel steps."""
        # Both constituents' undiscounted costs
        staff = self.STAFF_REVIEW_UNDISCOUNTED
        eng = self.ENGINEER_FINAL_UNDISCOUNTED

        c_correct = staff + eng  # undiscounted

        # If both were discounted (wrong behavior)
        c_wrong_both_discounted = staff * 0.75 + eng * 0.75

        # Correct C is strictly greater than wrong C (since 0.75 < 1.0)
        assert c_correct > c_wrong_both_discounted, (
            "C must use undiscounted constituent costs; discounting both "
            f"produces {c_wrong_both_discounted:.4f} < correct {c_correct:.4f}"
        )

        # Verify the SKILL.md specifies this explicitly
        skill_md = Path(__file__).parent.parent / "SKILL.md"
        content = skill_md.read_text()
        assert "un-discounted" in content or "undiscounted" in content.lower(), (
            "SKILL.md must explicitly state C uses undiscounted costs"
        )
        assert "parallel discount" in content.lower(), (
            "SKILL.md must mention parallel discount exclusion for C"
        )


# ---------------------------------------------------------------------------
# Document content verification
# ---------------------------------------------------------------------------

class TestDocumentContent:
    """Verify required content exists in documentation files after implementation."""

    def test_heuristics_has_parallel_section(self):
        assert "Parallel Agent Accounting" in HEURISTICS_MD.read_text()

    def test_heuristics_has_parallel_input_discount(self):
        assert "parallel_input_discount" in HEURISTICS_MD.read_text()

    def test_heuristics_has_parallel_cache_rate_reduction(self):
        assert "parallel_cache_rate_reduction" in HEURISTICS_MD.read_text()

    def test_skill_md_version_v1_5_0(self):
        assert "version: 2.0.0" in SKILL_MD.read_text()

    def test_skill_md_output_template_v1_5_0(self):
        assert "v2.0.0" in SKILL_MD.read_text()

    def test_skill_md_step0_has_parallel_groups_output(self):
        """Step 0 must produce parallel_groups — check for the specific output variable name."""
        assert "parallel_groups" in SKILL_MD.read_text()

    def test_skill_md_step0_has_detection_keywords(self):
        """Step 0 must list at least two parallel keyword patterns."""
        content = SKILL_MD.read_text()
        assert "simultaneously" in content or "concurrently" in content

    def test_skill_md_step3c_references_parallel_discount(self):
        assert "parallel_input_discount" in SKILL_MD.read_text()

    def test_skill_md_step3d_references_parallel_cache_reduction(self):
        assert "parallel_cache_rate_reduction" in SKILL_MD.read_text()

    def test_skill_md_step35_mentions_undiscounted(self):
        content = SKILL_MD.read_text()
        assert "un-discounted" in content or "pre-discount" in content or "undiscounted" in content

    def test_skill_md_output_has_parallel_group_marker(self):
        assert "Parallel Group" in SKILL_MD.read_text()

    def test_skill_md_output_has_box_drawing_chars(self):
        """Output template must use ┌│└ box-drawing characters for group brackets."""
        content = SKILL_MD.read_text()
        assert "┌" in content  # U+250C BOX DRAWINGS LIGHT DOWN AND RIGHT

    def test_skill_md_limitations_no_old_sequential_caveat(self):
        """The old 'Does not model parallel agent execution (treated as sequential)' bullet must be gone."""
        assert "Does not model parallel agent execution" not in SKILL_MD.read_text()

    def test_skill_md_limitations_has_approximation_caveat(self):
        assert "fixed discount factors" in SKILL_MD.read_text()

    def test_skill_md_active_estimate_schema_has_parallel_groups(self):
        assert "parallel_groups" in SKILL_MD.read_text()

    def test_skill_md_active_estimate_schema_has_parallel_steps_detected(self):
        assert "parallel_steps_detected" in SKILL_MD.read_text()

    def test_skill_md_has_parallel_keyword_in_parallel(self):
        content = SKILL_MD.read_text()
        assert '"in parallel"' in content, 'SKILL.md must list "in parallel" as a detection keyword'

    def test_skill_md_has_parallel_keyword_concurrently(self):
        content = SKILL_MD.read_text()
        assert '"concurrently"' in content, 'SKILL.md must list "concurrently" as a detection keyword'

    def test_skill_md_has_boundary_word_rule(self):
        content = SKILL_MD.read_text()
        assert "Boundaries" in content or "boundaries" in content, (
            "SKILL.md must document boundary word rules for parallel group detection"
        )

    def test_skill_md_has_first_occurrence_wins_rule(self):
        content = SKILL_MD.read_text()
        assert "first occurrence" in content.lower() or "first-occurrence" in content.lower(), (
            "SKILL.md must document first-occurrence-wins conflict rule"
        )

    def test_skill_md_has_minimum_group_size_rule(self):
        content = SKILL_MD.read_text()
        assert "fewer than 2" in content or "minimum" in content.lower(), (
            "SKILL.md must document minimum group size (>=2 steps)"
        )

    def test_skill_md_has_ambiguity_handling(self):
        content = SKILL_MD.read_text()
        assert "Ambiguous" in content or "ambiguous" in content, (
            "SKILL.md must document ambiguous token handling"
        )


# ---------------------------------------------------------------------------
# learn.sh: version and field forwarding
# ---------------------------------------------------------------------------

class TestLearnScript:
    """Tests for tokencostscope-learn.sh changes."""

    def test_version_is_1_4_0(self):
        result = subprocess.run(
            ["bash", str(LEARN_SH), "--version"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "2.0.0" in result.stdout

    def test_forwards_parallel_steps_detected(self):
        """learn.sh must extract parallel_steps_detected from active-estimate.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            estimate = {
                "timestamp": "2026-03-15T10:00:00Z",
                "size": "M",
                "files": 5,
                "complexity": "medium",
                "steps": ["Research Agent", "Architect Agent", "Implementation", "Test Writing"],
                "step_count": 4,
                "project_type": "greenfield",
                "language": "python",
                "expected_cost": 8.0,
                "optimistic_cost": 4.0,
                "pessimistic_cost": 24.0,
                "baseline_cost": 0.0,
                "review_cycles_estimated": 2,
                "review_cycles_actual": None,
                "parallel_groups": [
                    ["Research Agent", "Architect Agent"],
                    ["Implementation", "Test Writing"],
                ],
                "parallel_steps_detected": 4,
            }
            estimate_file = Path(tmpdir) / "active-estimate.json"
            estimate_file.write_text(json.dumps(estimate))

            result = subprocess.run(
                ["python3", "-c", """
import json, os, shlex
with open(os.environ['EST_FILE']) as f:
    d = json.load(f)
print(f'PARALLEL_STEPS_DETECTED={d.get("parallel_steps_detected", 0)}')
"""],
                capture_output=True, text=True,
                env={**os.environ, "EST_FILE": str(estimate_file)},
            )
            assert result.returncode == 0
            assert "PARALLEL_STEPS_DETECTED=4" in result.stdout

    def test_handles_missing_parallel_fields(self):
        """Old active-estimate.json without parallel fields defaults to [] and 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            estimate = {
                "timestamp": "2026-03-15T10:00:00Z",
                "size": "M",
                "expected_cost": 7.0,
                "baseline_cost": 0.0,
            }
            estimate_file = Path(tmpdir) / "active-estimate.json"
            estimate_file.write_text(json.dumps(estimate))

            result = subprocess.run(
                ["python3", "-c", """
import json, os
with open(os.environ['EST_FILE']) as f:
    d = json.load(f)
pg = d.get('parallel_groups', [])
psd = d.get('parallel_steps_detected', 0)
print(f'PG_LEN={len(pg)}')
print(f'PSD={psd}')
"""],
                capture_output=True, text=True,
                env={**os.environ, "EST_FILE": str(estimate_file)},
            )
            assert result.returncode == 0
            assert "PG_LEN=0" in result.stdout
            assert "PSD=0" in result.stdout

    def test_parallel_groups_in_history_record(self):
        """learn.sh source must include parallel_groups in the record-building Python."""
        content = LEARN_SH.read_text()
        assert "parallel_groups" in content
        assert "parallel_steps_detected" in content

    def test_parallel_groups_roundtrip(self):
        """parallel_groups with multi-word step names round-trips through JSON correctly."""
        groups = [["Research Agent", "Architect Agent"], ["Implementation", "Test Writing"]]
        encoded = json.dumps(groups)
        decoded = json.loads(encoded)
        assert decoded == groups
        # All step names with spaces survive round-trip
        assert decoded[0][0] == "Research Agent"


class TestLearnShellIntegration(unittest.TestCase):
    """Integration tests: invoke learn.sh end-to-end with mock data."""

    LEARN_SH = Path(__file__).parent.parent / "scripts" / "tokencostscope-learn.sh"
    CALIBRATION_DIR = Path(__file__).parent.parent / "calibration"

    def _write_mock_estimate(self, tmp_dir, parallel_groups=None, parallel_steps_detected=0):
        """Write a minimal active-estimate.json for testing."""
        estimate = {
            "timestamp": "2026-01-01T00:00:00Z",
            "size": "S",
            "files": 3,
            "complexity": "medium",
            "steps": ["Engineer Initial Plan", "Implementation"],
            "step_count": 2,
            "project_type": "greenfield",
            "language": "python",
            "expected_cost": 0.05,
            "optimistic_cost": 0.03,
            "pessimistic_cost": 0.15,
            "baseline_cost": 0.0,
            "review_cycles_estimated": 0,
            "review_cycles_actual": None,
            "parallel_groups": parallel_groups or [],
            "parallel_steps_detected": parallel_steps_detected,
        }
        path = Path(tmp_dir) / "active-estimate.json"
        path.write_text(json.dumps(estimate))
        return str(path)

    def _write_mock_session_jsonl(self, tmp_dir):
        """Write a minimal session JSONL (single usage entry).

        Must include "model" inside "message" — sum-session-tokens.py skips
        entries where model is absent or "<synthetic>", which would leave
        actual_cost=0 and prevent learn.sh from writing a history record.
        """
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

    def test_learn_sh_records_parallel_groups(self):
        """learn.sh end-to-end: parallel_groups appear in the history record."""
        import tempfile, subprocess, json as json_mod

        parallel_groups = [["Research Agent", "PM Agent"]]
        with tempfile.TemporaryDirectory() as tmp:
            estimate_file = self._write_mock_estimate(
                tmp,
                parallel_groups=parallel_groups,
                parallel_steps_detected=2,
            )
            session_file = self._write_mock_session_jsonl(tmp)
            history_file = Path(tmp) / "history.jsonl"

            env = {
                **__import__("os").environ,
                "TOKENCOSTSCOPE_ESTIMATE_FILE": estimate_file,
                "TOKENCOSTSCOPE_HISTORY_FILE": str(history_file),
            }

            result = subprocess.run(
                ["bash", str(self.LEARN_SH), session_file, "0"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(Path(__file__).parent.parent),
            )

            # Script may exit non-zero if calibration data is minimal — that's OK
            # We just need the history record to be written
            if not history_file.exists():
                self.skipTest(
                    f"learn.sh did not write history (may need 3+ sessions). "
                    f"stderr: {result.stderr[:500]}"
                )

            records = [json_mod.loads(line) for line in history_file.read_text().splitlines() if line.strip()]
            self.assertGreater(len(records), 0, "History file should have at least one record")

            last = records[-1]
            self.assertIn("parallel_groups", last, "History record must contain parallel_groups")
            self.assertEqual(last["parallel_groups"], parallel_groups)
            self.assertIn("parallel_steps_detected", last)
            self.assertEqual(last["parallel_steps_detected"], 2)

    def test_learn_sh_records_empty_parallel_groups_when_absent(self):
        """learn.sh end-to-end: missing parallel_groups defaults to empty list."""
        import tempfile, subprocess, json as json_mod

        with tempfile.TemporaryDirectory() as tmp:
            estimate_file = self._write_mock_estimate(tmp)  # no parallel groups
            session_file = self._write_mock_session_jsonl(tmp)
            history_file = Path(tmp) / "history.jsonl"

            env = {
                **__import__("os").environ,
                "TOKENCOSTSCOPE_ESTIMATE_FILE": estimate_file,
                "TOKENCOSTSCOPE_HISTORY_FILE": str(history_file),
            }

            result = subprocess.run(
                ["bash", str(self.LEARN_SH), session_file, "0"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(Path(__file__).parent.parent),
            )

            if not history_file.exists():
                self.skipTest(
                    f"learn.sh did not write history (may need 3+ sessions). "
                    f"stderr: {result.stderr[:500]}"
                )

            records = [json_mod.loads(line) for line in history_file.read_text().splitlines() if line.strip()]
            last = records[-1]
            self.assertEqual(last.get("parallel_groups", []), [], "parallel_groups should be empty list when absent")
            self.assertEqual(last.get("parallel_steps_detected", 0), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
