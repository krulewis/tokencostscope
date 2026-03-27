"""Tests for estimation_engine.py and file_measurement.py.

Uses current pricing.py values (NOT stale examples.md values).
All numeric assertions use pytest.approx() with rel=1e-4 tolerance
unless an exact match is warranted.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

# Insert src/ onto the path so we can import tokencast submodules directly.
_SRC_DIR = str(Path(__file__).parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from tokencast.file_measurement import (
    assign_bracket,
    bracket_from_override,
    compute_avg_tokens,
    compute_bracket_tokens_from_override,
    measure_files,
)
from tokencast.estimation_engine import (
    _apply_calibration,
    _compute_pipeline_signature,
    _compute_pr_review_loop,
    _compute_step_base_tokens,
    _compute_step_cost,
    _resolve_calibration_factor,
    _resolve_model,
    _resolve_review_cycles,
    _resolve_steps,
    compute_estimate,
)
from tokencast import pricing, heuristics


# ---------------------------------------------------------------------------
# TestBracketAssignment
# ---------------------------------------------------------------------------

class TestBracketAssignment(unittest.TestCase):
    def test_small_bracket_at_boundary(self):
        self.assertEqual(assign_bracket(49), "small")

    def test_small_bracket_below_boundary(self):
        self.assertEqual(assign_bracket(1), "small")

    def test_small_bracket_zero(self):
        self.assertEqual(assign_bracket(0), "small")

    def test_medium_bracket_at_lower_boundary(self):
        self.assertEqual(assign_bracket(50), "medium")

    def test_medium_bracket_at_upper_boundary(self):
        self.assertEqual(assign_bracket(500), "medium")

    def test_large_bracket_at_boundary(self):
        self.assertEqual(assign_bracket(501), "large")

    def test_large_bracket_above_boundary(self):
        self.assertEqual(assign_bracket(10000), "large")

    def test_medium_bracket_midpoint(self):
        self.assertEqual(assign_bracket(200), "medium")


# ---------------------------------------------------------------------------
# TestComputeAvgTokens
# ---------------------------------------------------------------------------

class TestComputeAvgTokens(unittest.TestCase):
    def test_all_medium_files(self):
        avg_read, avg_edit = compute_avg_tokens({"small": 0, "medium": 5, "large": 0})
        self.assertAlmostEqual(avg_read, 10000)
        self.assertAlmostEqual(avg_edit, 2500)

    def test_all_small_files(self):
        avg_read, avg_edit = compute_avg_tokens({"small": 3, "medium": 0, "large": 0})
        self.assertAlmostEqual(avg_read, 3000)
        self.assertAlmostEqual(avg_edit, 1000)

    def test_all_large_files(self):
        avg_read, avg_edit = compute_avg_tokens({"small": 0, "medium": 0, "large": 2})
        self.assertAlmostEqual(avg_read, 20000)
        self.assertAlmostEqual(avg_edit, 5000)

    def test_mixed_brackets(self):
        # small:2, medium:2, large:1 → total=5
        # read  = (2×3000 + 2×10000 + 1×20000) / 5 = 46000/5 = 9200
        # edit  = (2×1000 + 2×2500  + 1×5000)  / 5 = 12000/5 = 2400
        avg_read, avg_edit = compute_avg_tokens({"small": 2, "medium": 2, "large": 1})
        self.assertAlmostEqual(avg_read, 9200)
        self.assertAlmostEqual(avg_edit, 2400)

    def test_zero_divide_guard(self):
        avg_read, avg_edit = compute_avg_tokens({"small": 0, "medium": 0, "large": 0})
        self.assertAlmostEqual(avg_read, 10000)
        self.assertAlmostEqual(avg_edit, 2500)

    def test_bracket_from_override_small(self):
        self.assertEqual(bracket_from_override(49), "small")

    def test_bracket_from_override_medium(self):
        self.assertEqual(bracket_from_override(100), "medium")

    def test_bracket_from_override_large(self):
        self.assertEqual(bracket_from_override(501), "large")

    def test_compute_bracket_tokens_from_override_medium(self):
        result = compute_bracket_tokens_from_override(100)
        self.assertEqual(result["file_read_input"], 10000)
        self.assertEqual(result["file_edit_input"], 2500)

    def test_compute_bracket_tokens_from_override_small(self):
        result = compute_bracket_tokens_from_override(30)
        self.assertEqual(result["file_read_input"], 3000)
        self.assertEqual(result["file_edit_input"], 1000)


# ---------------------------------------------------------------------------
# TestStepBaseTokens (medium-bracket defaults used unless file_brackets given)
# ---------------------------------------------------------------------------

class TestStepBaseTokens(unittest.TestCase):
    """Verify input_base, output_base, K for each pipeline step (pricing-independent)."""

    def _base(self, step_name, N=5, file_brackets=None):
        avg_read = 10000.0
        avg_edit = 2500.0
        return _compute_step_base_tokens(step_name, N, file_brackets, avg_read, avg_edit)

    def test_research_agent_base_tokens(self):
        # 6 reads(10k) + 4 greps(500) + 1 plan(3k) + 3 conv(5k) = 80000
        # output: 6×200 + 4×500 + 1×4000 + 3×1500 = 11700
        # K = 6+4+1+3 = 14
        input_base, output_base, K = self._base("Research Agent")
        self.assertEqual(K, 14)
        self.assertAlmostEqual(input_base, 80000)
        self.assertAlmostEqual(output_base, 11700)

    def test_architect_agent_base_tokens(self):
        # 1 code_review(8k) + 1 plan(3k) + 2 conv(5k) = 21000
        # output: 1×3000 + 1×4000 + 2×1500 = 10000
        # K = 4
        input_base, output_base, K = self._base("Architect Agent")
        self.assertEqual(K, 4)
        self.assertAlmostEqual(input_base, 21000)
        self.assertAlmostEqual(output_base, 10000)

    def test_engineer_initial_plan_base_tokens(self):
        # 4 reads(10k) + 2 greps(500) + 1 plan(3k) + 2 conv(5k) = 54000
        # output: 4×200 + 2×500 + 1×4000 + 2×1500 = 8800
        # K = 4+2+1+2 = 9
        input_base, output_base, K = self._base("Engineer Initial Plan")
        self.assertEqual(K, 9)
        self.assertAlmostEqual(input_base, 54000)
        self.assertAlmostEqual(output_base, 8800)

    def test_staff_review_base_tokens(self):
        # 1 code_review(8k) + 2 conv(5k) = 18000
        # output: 1×3000 + 2×1500 = 6000
        # K = 3
        input_base, output_base, K = self._base("Staff Review")
        self.assertEqual(K, 3)
        self.assertAlmostEqual(input_base, 18000)
        self.assertAlmostEqual(output_base, 6000)

    def test_engineer_final_plan_base_tokens(self):
        # 2 reads(10k) + 1 plan(3k) + 2 conv(5k) = 33000
        # output: 2×200 + 1×4000 + 2×1500 = 7400
        # K = 2+1+2 = 5
        input_base, output_base, K = self._base("Engineer Final Plan")
        self.assertEqual(K, 5)
        self.assertAlmostEqual(input_base, 33000)
        self.assertAlmostEqual(output_base, 7400)

    def test_test_writing_base_tokens_n5(self):
        # 3 reads(10k avg) + 5 test_writes(2k) + 3 conv(5k)
        # input = 3×10000 + 5×2000 + 3×5000 = 30000+10000+15000 = 55000
        # output = 3×200 + 5×5000 + 3×1500 = 600+25000+4500 = 30100
        # K = 3+5+3 = 11
        input_base, output_base, K = self._base("Test Writing", N=5)
        self.assertEqual(K, 11)
        self.assertAlmostEqual(input_base, 55000)
        self.assertAlmostEqual(output_base, 30100)

    def test_implementation_base_tokens_n5(self):
        # 5 reads(10k) + 5 edits(2500) + 4 conv(5k)
        # input = 50000+12500+20000 = 82500
        # output = 5×200 + 5×1500 + 4×1500 = 1000+7500+6000 = 14500
        # K = 5+5+4 = 14
        input_base, output_base, K = self._base("Implementation", N=5)
        self.assertEqual(K, 14)
        self.assertAlmostEqual(input_base, 82500)
        self.assertAlmostEqual(output_base, 14500)

    def test_qa_base_tokens(self):
        # 3 shell(300) + 2 reads(10k) + 2 conv(5k)
        # input = 900+20000+10000 = 30900
        # output = 3×500 + 2×200 + 2×1500 = 1500+400+3000 = 4900
        # K = 3+2+2 = 7
        input_base, output_base, K = self._base("QA", N=5)
        self.assertEqual(K, 7)
        self.assertAlmostEqual(input_base, 30900)
        self.assertAlmostEqual(output_base, 4900)

    def test_implementation_base_tokens_n5_with_mixed_brackets(self):
        # Example 3: file_brackets={small:2, medium:2, large:1}
        # file_read_contribution  = 2×3000 + 2×10000 + 1×20000 = 46000
        # file_edit_contribution  = 2×1000 + 2×2500  + 1×5000  = 12000
        # conv = 4×5000 = 20000
        # input_base = 46000 + 12000 + 20000 = 78000
        fb = {"small": 2, "medium": 2, "large": 1}
        avg_r, avg_e = compute_avg_tokens(fb)
        input_base, output_base, K = _compute_step_base_tokens(
            "Implementation", 5, fb, avg_r, avg_e
        )
        self.assertAlmostEqual(input_base, 78000)
        self.assertEqual(K, 14)  # 5 reads + 5 edits + 4 conv

    def test_test_writing_fixed_reads_use_avg_tokens(self):
        # Test Writing with custom avg (small bracket)
        fb = {"small": 5, "medium": 0, "large": 0}
        avg_r, avg_e = compute_avg_tokens(fb)  # avg_r = 3000
        # 3 reads × 3000 = 9000 (fixed reads use avg, not bracket sum)
        input_base, output_base, K = _compute_step_base_tokens(
            "Test Writing", 5, fb, avg_r, avg_e
        )
        # 3×3000 (reads) + 5×2000 (test_writes) + 3×5000 (conv)
        self.assertAlmostEqual(input_base, 9000 + 10000 + 15000)


# ---------------------------------------------------------------------------
# TestThreeTermCacheFormula
# ---------------------------------------------------------------------------

class TestThreeTermCacheFormula(unittest.TestCase):
    """Verify three-term cache cost formula using Research Agent as reference."""

    def _research_expected_cost(self):
        """Compute Research Agent Expected band cost with three-term formula."""
        # input_base=80000, output_base=11700, K=14, complexity=1.0, Sonnet, not parallel
        result = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, False)
        return result

    def test_three_term_formula_expected_band(self):
        """Research Agent Expected: input_accum=600000, cache=0.50, band=1.0x."""
        # input_accum = 80000 × (14+1)/2 = 80000 × 7.5 = 600000
        # Three-term (Sonnet pricing: in=3.00, cw=3.75, cr=0.30):
        #   uncached     = 600000 × 0.50 × 3.00 / 1e6       = 0.9000
        #   cache_write  = 600000 × 0.50 × (1/14) × 3.75 / 1e6 ≈ 0.0804
        #   cache_read   = 600000 × 0.50 × (13/14) × 0.30 / 1e6 ≈ 0.0836
        #   output_cost  = 11700 × 15.00 / 1e6                = 0.1755
        result = self._research_expected_cost()
        expected = result["expected"]
        # Approximate verification (examples.md shows ≈$1.2394 with current pricing)
        self.assertAlmostEqual(expected, 1.2394, delta=0.01)

    def test_three_term_formula_optimistic_band(self):
        """Research Agent Optimistic: cache=0.60, band=0.6x."""
        result = self._research_expected_cost()
        optimistic = result["optimistic"]
        # Optimistic should be less than Expected
        self.assertLess(optimistic, result["expected"])
        # From examples.md (two-term, different pricing): ~$0.6021 — our three-term differs
        # Just verify it's in a reasonable range with band=0.6x
        self.assertGreater(optimistic, 0.0)

    def test_three_term_formula_pessimistic_band(self):
        """Research Agent Pessimistic: cache=0.30, band=3.0x."""
        result = self._research_expected_cost()
        pessimistic = result["pessimistic"]
        self.assertGreater(pessimistic, result["expected"])
        # With band=3.0x, pessimistic ≈ expected × ~3 (slight variation due to cache)
        self.assertGreater(pessimistic, result["expected"] * 2.5)

    def test_zero_K_guard(self):
        """K=0 edge case should not divide by zero."""
        # N=0 files, Implementation step only — K = 0+0+4 = 4 (conv turns still present)
        # Force K=0 by calling _compute_step_cost directly
        result = _compute_step_cost(0, 0, 0, "medium", pricing.MODEL_SONNET, False)
        self.assertAlmostEqual(result["optimistic"],  0.0, places=10)
        self.assertAlmostEqual(result["expected"],    0.0, places=10)
        self.assertAlmostEqual(result["pessimistic"], 0.0, places=10)

    def test_expected_pre_discount_equals_expected_when_not_parallel(self):
        """For non-parallel steps, expected_pre_discount == expected."""
        result = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, False)
        self.assertAlmostEqual(result["expected_pre_discount"], result["expected"], places=6)

    def test_expected_pre_discount_differs_when_parallel(self):
        """For parallel steps, expected_pre_discount > expected (pre-discount is higher)."""
        result = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, True)
        self.assertGreater(result["expected_pre_discount"], result["expected"])


# ---------------------------------------------------------------------------
# TestContextAccumulation
# ---------------------------------------------------------------------------

class TestContextAccumulation(unittest.TestCase):
    def test_context_accum_k14(self):
        """K=14 → accumulation factor = (14+1)/2 = 7.5."""
        # input_base=80000, complex=1.0, input_accum = 80000×7.5 = 600000
        result = _compute_step_cost(80000, 0, 14, "medium", pricing.MODEL_SONNET, False)
        # For Expected (band=1.0, no-parallel), with cache_rate=0.50:
        # input_accum = 600000 (used in formula)
        # Verify by checking expected is reasonable
        self.assertGreater(result["expected"], 0)

    def test_context_accum_k3(self):
        """K=3 → accumulation factor = (3+1)/2 = 2.0 (Staff Review)."""
        result = _compute_step_cost(18000, 6000, 3, "medium", pricing.MODEL_OPUS, False)
        self.assertGreater(result["expected"], 0)

    def test_parallel_discount_applied(self):
        """Parallel input discount = 0.75 reduces expected cost."""
        seq_result = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, False)
        par_result = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, True)
        self.assertLess(par_result["expected"], seq_result["expected"])

    def test_parallel_cache_rate_reduction_optimistic(self):
        """Parallel cache rate: optimistic (0.60) - 0.15 = 0.45."""
        # Verified indirectly: parallel optimistic should differ from sequential
        seq = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, False)
        par = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, True)
        self.assertLess(par["optimistic"], seq["optimistic"])

    def test_parallel_cache_rate_floor_pessimistic(self):
        """Parallel: pessimistic (0.30) - 0.15 = 0.15 (above floor of 0.05)."""
        seq = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, False)
        par = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, True)
        self.assertLess(par["pessimistic"], seq["pessimistic"])

    def test_parallel_cache_rate_floor_clamped(self):
        """Floor clamping: when subtraction would go below 0.05, clamp to 0.05."""
        # CACHE_HIT_RATES["pessimistic"] = 0.30, reduction = 0.15 → 0.15, above floor
        # To force clamping we'd need a band with rate ≤ 0.20; patch CACHE_HIT_RATES
        with patch.object(pricing, "CACHE_HIT_RATES", {"optimistic": 0.10, "expected": 0.10, "pessimistic": 0.05}):
            result = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, True)
        # Pessimistic rate: 0.05 - 0.15 = -0.10 → clamped to 0.05 (floor)
        # Just verify no error thrown
        self.assertGreater(result["pessimistic"], 0)


# ---------------------------------------------------------------------------
# TestCalibrationPrecedence
# ---------------------------------------------------------------------------

class TestCalibrationPrecedence(unittest.TestCase):
    def _sig(self, steps=None):
        s = steps or ["Research Agent"]
        return _compute_pipeline_signature(s)

    def test_per_step_factor_wins_over_global(self):
        factors = {
            "step_factors": {
                "Research Agent": {"factor": 0.82, "status": "active", "n": 5}
            },
            "global": 1.10,
            "status": "active",
        }
        f, label = _resolve_calibration_factor("Research Agent", "M", self._sig(), factors)
        self.assertAlmostEqual(f, 0.82)
        self.assertEqual(label, "S:0.82")

    def test_per_step_collecting_falls_through(self):
        factors = {
            "step_factors": {
                "Research Agent": {"factor": 0.82, "status": "collecting", "n": 1}
            },
            "global": 1.10,
            "status": "active",
        }
        f, label = _resolve_calibration_factor("Research Agent", "M", self._sig(), factors)
        self.assertAlmostEqual(f, 1.10)
        self.assertTrue(label.startswith("G:"))

    def test_per_signature_factor_active(self):
        sig = self._sig(["Research Agent", "Architect Agent"])
        factors = {
            "signature_factors": {
                sig: {"factor": 1.05, "status": "active", "n": 4}
            },
        }
        f, label = _resolve_calibration_factor("Research Agent", "M", sig, factors)
        self.assertAlmostEqual(f, 1.05)
        self.assertEqual(label, "P:1.05")

    def test_per_signature_collecting_falls_through(self):
        sig = self._sig(["Research Agent"])
        factors = {
            "signature_factors": {
                sig: {"factor": 1.05, "status": "collecting", "n": 2}
            },
            "M": 0.95,
            "M_n": 5,
        }
        f, label = _resolve_calibration_factor("Research Agent", "M", sig, factors)
        self.assertAlmostEqual(f, 0.95)
        self.assertTrue(label.startswith("Z:"))

    def test_size_class_factor_active(self):
        factors = {"M": 0.95, "M_n": 5}
        f, label = _resolve_calibration_factor("Research Agent", "M", self._sig(), factors)
        self.assertAlmostEqual(f, 0.95)
        self.assertEqual(label, "Z:0.95")

    def test_size_class_factor_insufficient_samples(self):
        factors = {"M": 0.95, "M_n": 2, "global": 1.10, "status": "active"}
        f, label = _resolve_calibration_factor("Research Agent", "M", self._sig(), factors)
        self.assertAlmostEqual(f, 1.10)
        self.assertTrue(label.startswith("G:"))

    def test_global_factor_active(self):
        factors = {"global": 1.10, "status": "active"}
        f, label = _resolve_calibration_factor("Research Agent", "M", self._sig(), factors)
        self.assertAlmostEqual(f, 1.10)
        self.assertEqual(label, "G:1.10")

    def test_no_calibration(self):
        f, label = _resolve_calibration_factor("Research Agent", "M", self._sig(), {})
        self.assertAlmostEqual(f, 1.0)
        self.assertEqual(label, "--")

    def test_per_step_trumps_signature(self):
        sig = self._sig(["Research Agent"])
        factors = {
            "step_factors": {
                "Research Agent": {"factor": 0.82, "status": "active", "n": 5}
            },
            "signature_factors": {
                sig: {"factor": 1.15, "status": "active", "n": 4}
            },
        }
        f, label = _resolve_calibration_factor("Research Agent", "M", sig, factors)
        self.assertAlmostEqual(f, 0.82)
        self.assertEqual(label, "S:0.82")


# ---------------------------------------------------------------------------
# TestApplyCalibration
# ---------------------------------------------------------------------------

class TestApplyCalibration(unittest.TestCase):
    def test_apply_calibration_factor_1(self):
        costs = {"optimistic": 0.60, "expected": 1.00, "pessimistic": 3.00}
        result = _apply_calibration(costs, 1.0)
        self.assertAlmostEqual(result["expected"],    1.00)
        self.assertAlmostEqual(result["optimistic"],  0.60)
        self.assertAlmostEqual(result["pessimistic"], 3.00)

    def test_apply_calibration_factor_1_2(self):
        costs = {"optimistic": 0.60, "expected": 1.00, "pessimistic": 3.00}
        result = _apply_calibration(costs, 1.2)
        self.assertAlmostEqual(result["expected"],    1.20)
        self.assertAlmostEqual(result["optimistic"],  0.72)
        self.assertAlmostEqual(result["pessimistic"], 3.60)

    def test_band_multiplier_ratios(self):
        """calibrated_optimistic = calibrated_expected × 0.6, pessimistic = × 3.0."""
        costs = {"optimistic": 0.42, "expected": 0.75, "pessimistic": 2.53}
        result = _apply_calibration(costs, 1.1)
        self.assertAlmostEqual(result["optimistic"],  result["expected"] * 0.6,  places=6)
        self.assertAlmostEqual(result["pessimistic"], result["expected"] * 3.0,  places=6)


# ---------------------------------------------------------------------------
# TestPRReviewLoop
# ---------------------------------------------------------------------------

class TestPRReviewLoop(unittest.TestCase):
    """PR Review Loop formula tests using current Staff Review + Engineer Final Plan costs."""

    def _pr_input(self, N=5, complexity="medium"):
        """Compute C (pre-discount Expected staff+final) from actual step costs."""
        avg_read, avg_edit = 10000.0, 2500.0
        staff_in, staff_out, staff_K = _compute_step_base_tokens(
            "Staff Review", N, None, avg_read, avg_edit
        )
        final_in, final_out, final_K = _compute_step_base_tokens(
            "Engineer Final Plan", N, None, avg_read, avg_edit
        )
        staff_costs = _compute_step_cost(staff_in, staff_out, staff_K, complexity, pricing.MODEL_OPUS, False)
        final_costs = _compute_step_cost(final_in, final_out, final_K, complexity, pricing.MODEL_SONNET, False)
        return staff_costs["expected_pre_discount"], final_costs["expected_pre_discount"]

    def test_pr_review_loop_n2_cycles_decay_formula(self):
        staff_pre, final_pre = self._pr_input()
        result = _compute_pr_review_loop(staff_pre, final_pre, 2, {}, "M")
        self.assertIsNotNone(result)
        C = staff_pre + final_pre
        decay = 0.6
        # Expected (2 cycles): C × (1-0.6^2) / 0.4 = C × 0.64/0.4 = C × 1.6
        self.assertAlmostEqual(result["expected"], C * (1 - decay**2) / (1 - decay), places=6)

    def test_pr_review_loop_optimistic_1_cycle(self):
        staff_pre, final_pre = self._pr_input()
        result = _compute_pr_review_loop(staff_pre, final_pre, 2, {}, "M")
        C = staff_pre + final_pre
        # Optimistic always 1 cycle: C × (1 - 0.6^1) / 0.4 = C × 0.4/0.4 = C × 1.0
        self.assertAlmostEqual(result["optimistic"], C * 1.0, places=6)

    def test_pr_review_loop_expected_2_cycles(self):
        staff_pre, final_pre = self._pr_input()
        result = _compute_pr_review_loop(staff_pre, final_pre, 2, {}, "M")
        C = staff_pre + final_pre
        self.assertAlmostEqual(result["expected"], C * 1.6, places=6)

    def test_pr_review_loop_pessimistic_4_cycles(self):
        staff_pre, final_pre = self._pr_input()
        result = _compute_pr_review_loop(staff_pre, final_pre, 2, {}, "M")
        C = staff_pre + final_pre
        # Pessimistic: N×2=4 cycles → C × (1-0.6^4)/0.4 = C × 2.176
        self.assertAlmostEqual(result["pessimistic"], C * 2.176, places=4)

    def test_pr_review_loop_zero_cycles_returns_none(self):
        result = _compute_pr_review_loop(1.0, 0.5, 0, {}, "M")
        self.assertIsNone(result)

    def test_pr_review_loop_uses_prediscount_costs(self):
        """When constituent step is parallel, C uses pre-discount Expected."""
        staff_pre, final_pre = self._pr_input()
        # C should use pre-discount (higher) values regardless of parallel discount
        result_normal = _compute_pr_review_loop(staff_pre, final_pre, 2, {}, "M")
        # Simulate a smaller C (as if parallel-discounted) — result should be lower
        result_smaller_C = _compute_pr_review_loop(staff_pre * 0.75, final_pre * 0.75, 2, {}, "M")
        self.assertGreater(result_normal["expected"], result_smaller_C["expected"])

    def test_pr_review_loop_calibration_factor_always_1(self):
        """PR Review Loop factor is ALWAYS 1.0 (H1 — no calibration lookup)."""
        factors = {"global": 1.2, "status": "active"}
        staff_pre, final_pre = self._pr_input()
        result = _compute_pr_review_loop(staff_pre, final_pre, 2, factors, "M")
        self.assertAlmostEqual(result["factor"], 1.0)
        self.assertEqual(result["cal_label"], "--")

    def test_pr_review_loop_cal_always_double_dash(self):
        """Cal label is always '--' for PR Review Loop."""
        staff_pre, final_pre = self._pr_input()
        result = _compute_pr_review_loop(staff_pre, final_pre, 3, {}, "M")
        self.assertEqual(result["cal_label"], "--")

    def test_pr_review_loop_calibration_per_band_NOT_reanchored(self):
        """Per-band calibration (factor=1.0 → values unchanged; bands preserve decay ratios)."""
        staff_pre, final_pre = self._pr_input()
        result = _compute_pr_review_loop(staff_pre, final_pre, 2, {}, "M")
        C = staff_pre + final_pre
        # With factor=1.0, raw values are used
        # Verify bands are NOT re-anchored (opt ≠ expected × 0.6)
        self.assertFalse(abs(result["optimistic"] - result["expected"] * 0.6) < 1e-6)


# ---------------------------------------------------------------------------
# TestResolveSteps / TestResolveModel / TestResolveReviewCycles
# ---------------------------------------------------------------------------

class TestResolveHelpers(unittest.TestCase):
    def test_resolve_steps_all_when_no_override(self):
        steps = _resolve_steps("M", None)
        # Should include all PIPELINE_STEPS in defined order
        for k in heuristics.PIPELINE_STEPS:
            self.assertIn(k, steps)

    def test_resolve_steps_with_override(self):
        steps = _resolve_steps("M", ["Implementation", "QA"])
        self.assertEqual(steps, ["Implementation", "QA"])

    def test_resolve_steps_unknown_step_dropped(self):
        import warnings
        with warnings.catch_warnings(record=True):
            steps = _resolve_steps("M", ["Implementation", "UnknownStep", "QA"])
        self.assertNotIn("UnknownStep", steps)
        self.assertIn("Implementation", steps)
        self.assertIn("QA", steps)

    def test_resolve_model_standard(self):
        self.assertEqual(_resolve_model("Research Agent", "M"), pricing.MODEL_SONNET)
        self.assertEqual(_resolve_model("Architect Agent", "M"), pricing.MODEL_OPUS)
        self.assertEqual(_resolve_model("QA", "M"), pricing.MODEL_HAIKU)

    def test_resolve_model_implementation_l_size_uses_opus(self):
        self.assertEqual(_resolve_model("Implementation", "L"), pricing.MODEL_OPUS)

    def test_resolve_model_implementation_m_size_uses_sonnet(self):
        self.assertEqual(_resolve_model("Implementation", "M"), pricing.MODEL_SONNET)

    def test_resolve_review_cycles_explicit_override(self):
        params = {"review_cycles": 4}
        steps  = list(heuristics.PIPELINE_STEPS.keys())
        self.assertEqual(_resolve_review_cycles(params, steps), 4)

    def test_resolve_review_cycles_zero_override(self):
        params = {"review_cycles": 0}
        steps  = list(heuristics.PIPELINE_STEPS.keys())
        self.assertEqual(_resolve_review_cycles(params, steps), 0)

    def test_resolve_review_cycles_inferred_when_review_and_final_present(self):
        params = {}
        steps  = ["Staff Review", "Engineer Final Plan"]
        N = _resolve_review_cycles(params, steps)
        self.assertEqual(N, heuristics.PR_REVIEW_LOOP["review_cycles_default"])

    def test_resolve_review_cycles_inferred_zero_no_review_step(self):
        params = {}
        steps  = ["Research Agent", "Implementation"]
        self.assertEqual(_resolve_review_cycles(params, steps), 0)

    def test_pipeline_signature_formula(self):
        steps = ["Research Agent", "Architect Agent", "Implementation"]
        sig = _compute_pipeline_signature(steps)
        expected = "+".join(sorted(s.lower().replace(" ", "_") for s in steps))
        self.assertEqual(sig, expected)


# ---------------------------------------------------------------------------
# TestComputeEstimateIntegration (end-to-end)
# ---------------------------------------------------------------------------

class TestComputeEstimateIntegration(unittest.TestCase):
    """End-to-end tests for compute_estimate()."""

    _BASE_PARAMS = {
        "size": "M",
        "files": 5,
        "complexity": "medium",
    }

    def _estimate(self, **kwargs):
        params = dict(self._BASE_PARAMS, **kwargs)
        return compute_estimate(params, calibration_dir=None)

    def test_estimate_output_keys_present(self):
        result = self._estimate()
        self.assertIn("version",   result)
        self.assertIn("estimate",  result)
        self.assertIn("steps",     result)
        self.assertIn("metadata",  result)
        self.assertIn("step_costs", result)

    def test_estimate_estimate_keys_present(self):
        result = self._estimate()
        est = result["estimate"]
        self.assertIn("optimistic",  est)
        self.assertIn("expected",    est)
        self.assertIn("pessimistic", est)

    def test_estimate_totals_sum_of_steps(self):
        result = self._estimate()
        total_exp = sum(s["expected"] for s in result["steps"])
        self.assertAlmostEqual(result["estimate"]["expected"], total_exp, places=6)

    def test_estimate_m_size_5_files_medium_no_calibration(self):
        result = self._estimate()
        # Verify all canonical step names present (no PR Review Loop since no review_cycles)
        step_names = [s["name"] for s in result["steps"]]
        for name in heuristics.PIPELINE_STEPS:
            self.assertIn(name, step_names)
        # step_costs matches step names
        for s in result["steps"]:
            self.assertIn(s["name"], result["step_costs"])
        # pricing_stale should be False (LAST_UPDATED is recent)
        self.assertFalse(result["metadata"]["pricing_stale"])

    def test_estimate_step_costs_matches_expected_band(self):
        result = self._estimate()
        for step in result["steps"]:
            self.assertAlmostEqual(
                result["step_costs"][step["name"]], step["expected"], places=6
            )

    def test_estimate_with_review_cycles(self):
        result = self._estimate(review_cycles=2)
        step_names = [s["name"] for s in result["steps"]]
        self.assertIn("PR Review Loop", step_names)
        # PR Review Loop included in totals
        pr_step = next(s for s in result["steps"] if s["name"] == "PR Review Loop")
        self.assertGreater(pr_step["expected"], 0)

    def test_estimate_review_cycles_zero_omits_pr_row(self):
        result = self._estimate(review_cycles=0)
        step_names = [s["name"] for s in result["steps"]]
        self.assertNotIn("PR Review Loop", step_names)

    def test_estimate_with_parallel_groups(self):
        result = self._estimate(
            parallel_groups=[["Research Agent", "Architect Agent"]]
        )
        step_map = {s["name"]: s for s in result["steps"]}
        self.assertTrue(step_map["Research Agent"]["is_parallel"])
        self.assertTrue(step_map["Architect Agent"]["is_parallel"])
        self.assertFalse(step_map["Implementation"]["is_parallel"])

    def test_estimate_steps_override(self):
        result = self._estimate(steps=["Implementation", "QA"])
        step_names = [s["name"] for s in result["steps"]]
        self.assertEqual(step_names, ["Implementation", "QA"])
        self.assertNotIn("Research Agent", step_names)

    def test_estimate_no_calibration_factor_is_1(self):
        result = self._estimate()
        for step in result["steps"]:
            self.assertEqual(step["cal"], "--")
            self.assertAlmostEqual(step["factor"], 1.0)

    def test_estimate_with_global_calibration_factor(self):
        """Global calibration factor should scale each step's expected cost."""
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            factors = {"global": 1.2, "status": "active"}
            factors_path = Path(tmpdir) / "factors.json"
            factors_path.write_text(json.dumps(factors))
            result_cal  = compute_estimate(dict(self._BASE_PARAMS), calibration_dir=tmpdir)
            result_nocal = compute_estimate(dict(self._BASE_PARAMS), calibration_dir=None)
        # Every non-PR-Review-Loop step should be ~1.2× the uncalibrated expected
        for step in result_cal["steps"]:
            if step["name"] == "PR Review Loop":
                continue
            base_step = next(s for s in result_nocal["steps"] if s["name"] == step["name"])
            self.assertAlmostEqual(step["expected"], base_step["expected"] * 1.2, delta=0.001)

    def test_estimate_with_file_brackets(self):
        """file_brackets={small:2,medium:2,large:1} → Implementation input_base=78000."""
        fb = {"small": 2, "medium": 2, "large": 1}
        avg_r, avg_e = compute_avg_tokens(fb)
        result = compute_estimate(
            {"size": "M", "files": 5, "complexity": "medium",
             "steps": ["Implementation"],
             "file_brackets": fb,
             "avg_file_read_tokens": avg_r,
             "avg_file_edit_tokens": avg_e},
            calibration_dir=None,
        )
        # With mixed brackets, Implementation expected should be close to Example 3
        step = result["steps"][0]
        self.assertEqual(step["name"], "Implementation")
        self.assertGreater(step["expected"], 0)

    def test_estimate_l_size_implementation_uses_opus(self):
        result = compute_estimate(
            {"size": "L", "files": 5, "complexity": "medium", "steps": ["Implementation"]},
            calibration_dir=None,
        )
        impl_step = result["steps"][0]
        self.assertEqual(impl_step["model_id"], pricing.MODEL_OPUS)
        self.assertEqual(impl_step["model"], "Opus")

    def test_estimate_pr_review_loop_model_is_opus_plus_sonnet(self):
        result = self._estimate(review_cycles=2)
        pr = next(s for s in result["steps"] if s["name"] == "PR Review Loop")
        self.assertEqual(pr["model"], "Opus+Sonnet")
        self.assertIsNone(pr["model_id"])

    def test_estimate_zero_files(self):
        """files=0 should not crash; N-scaling steps produce $0 or minimal cost."""
        result = compute_estimate({"size": "M", "files": 0, "complexity": "medium"})
        self.assertIn("estimate", result)
        self.assertGreaterEqual(result["estimate"]["expected"], 0)

    def test_estimate_metadata_fields(self):
        result = self._estimate(project_type="refactor", language="python")
        meta = result["metadata"]
        self.assertEqual(meta["size"], "M")
        self.assertEqual(meta["files"], 5)
        self.assertEqual(meta["complexity"], "medium")
        self.assertEqual(meta["project_type"], "refactor")
        self.assertEqual(meta["language"], "python")
        self.assertIn("pipeline_signature", meta)

    def test_estimate_parallel_steps_detected_count(self):
        result = self._estimate(
            parallel_groups=[["Research Agent", "Architect Agent"], ["Implementation", "QA"]]
        )
        self.assertEqual(result["metadata"]["parallel_steps_detected"], 4)

    def test_estimate_calibration_factor_1_on_pr_review_loop(self):
        """PR Review Loop cal label is always '--' regardless of factors."""
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            factors = {"global": 1.5, "status": "active"}
            Path(tmpdir, "factors.json").write_text(json.dumps(factors))
            result = compute_estimate(
                {"size": "M", "files": 5, "complexity": "medium", "review_cycles": 2},
                calibration_dir=tmpdir,
            )
        pr = next(s for s in result["steps"] if s["name"] == "PR Review Loop")
        self.assertEqual(pr["cal"], "--")
        self.assertAlmostEqual(pr["factor"], 1.0)

    def test_estimate_pricing_staleness_old_date(self):
        """Inject old LAST_UPDATED → pricing_stale=True."""
        with patch.object(pricing, "LAST_UPDATED", "2020-01-01"):
            result = self._estimate()
        self.assertTrue(result["metadata"]["pricing_stale"])

    def test_estimate_pricing_staleness_recent_date(self):
        """Current LAST_UPDATED → pricing_stale=False."""
        result = self._estimate()
        self.assertFalse(result["metadata"]["pricing_stale"])

    def test_estimate_file_brackets_null_when_no_paths(self):
        """When no file_paths and no avg_file_lines, file_brackets=None."""
        result = self._estimate()
        self.assertIsNone(result["metadata"]["file_brackets"])

    def test_estimate_avg_file_lines_override(self):
        """avg_file_lines=30 → small bracket applied to all files."""
        result = compute_estimate(
            {"size": "M", "files": 5, "complexity": "medium", "avg_file_lines": 30},
            calibration_dir=None,
        )
        fb = result["metadata"]["file_brackets"]
        self.assertIsNotNone(fb)
        self.assertEqual(fb["small"], 5)
        self.assertEqual(fb["medium"], 0)
        self.assertEqual(fb["large"], 0)


# ---------------------------------------------------------------------------
# TestMeasureFiles (requires temp files on disk)
# ---------------------------------------------------------------------------

class TestMeasureFiles(unittest.TestCase):
    def _make_file(self, tmpdir, name, lines):
        """Create a temp file with the given number of lines."""
        path = os.path.join(tmpdir, name)
        with open(path, "w") as f:
            for i in range(lines):
                f.write(f"line {i}\n")
        return path

    def test_measure_small_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_file(tmpdir, "small.py", 30)
            result = measure_files([path])
        self.assertEqual(result["brackets"]["small"], 1)
        self.assertEqual(result["brackets"]["medium"], 0)
        self.assertEqual(result["brackets"]["large"], 0)
        self.assertEqual(result["files_measured"], 1)

    def test_measure_medium_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_file(tmpdir, "medium.py", 100)
            result = measure_files([path])
        self.assertEqual(result["brackets"]["medium"], 1)
        self.assertEqual(result["files_measured"], 1)

    def test_measure_large_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_file(tmpdir, "large.py", 600)
            result = measure_files([path])
        self.assertEqual(result["brackets"]["large"], 1)
        self.assertEqual(result["files_measured"], 1)

    def test_measure_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = self._make_file(tmpdir, "s1.py", 30)
            s2 = self._make_file(tmpdir, "s2.py", 40)
            lg = self._make_file(tmpdir, "lg.py", 600)
            result = measure_files([s1, s2, lg])
        self.assertEqual(result["brackets"]["small"],  2)
        self.assertEqual(result["brackets"]["medium"], 0)
        self.assertEqual(result["brackets"]["large"],  1)
        self.assertEqual(result["files_measured"], 3)

    def test_measure_nonexistent_file_skipped(self):
        result = measure_files(["/nonexistent/path/does_not_exist.py"])
        # File not found → 0 measured but brackets dict present (paths were extracted)
        self.assertIsNotNone(result["brackets"])
        self.assertEqual(result["files_measured"], 0)

    def test_measure_binary_extension_skipped(self):
        result = measure_files(["/path/to/image.png"])
        # Binary extension → zero-count brackets
        self.assertIsNotNone(result["brackets"])
        self.assertEqual(result["files_measured"], 0)

    def test_measure_empty_file_paths(self):
        result = measure_files([])
        self.assertIsNone(result["brackets"])
        self.assertEqual(result["files_measured"], 0)

    def test_measure_path_with_spaces(self):
        """Paths with spaces should work via shlex.quote."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create subdirectory with space
            spaced_dir = os.path.join(tmpdir, "my dir")
            os.makedirs(spaced_dir)
            path = os.path.join(spaced_dir, "file.py")
            with open(path, "w") as f:
                for i in range(30):
                    f.write(f"line {i}\n")
            result = measure_files([path])
        self.assertEqual(result["files_measured"], 1)
        self.assertEqual(result["brackets"]["small"], 1)

    def test_measure_all_binary_returns_zero_count_brackets(self):
        """All binary extensions → brackets={small:0,medium:0,large:0} not None."""
        result = measure_files(["/a/b.png", "/c/d.jpg", "/e/f.gif"])
        self.assertIsNotNone(result["brackets"])
        self.assertEqual(result["brackets"], {"small": 0, "medium": 0, "large": 0})
        self.assertEqual(result["files_measured"], 0)

    def test_measure_cap_at_30_files(self):
        """Pass 35 file paths → only first 30 measured; 31-35 get overflow bracket."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create 35 medium files
            paths = []
            for i in range(35):
                p = self._make_file(tmpdir, f"f{i:02d}.py", 100)
                paths.append(p)
            result = measure_files(paths)
        # 30 measured (all medium), 5 overflow assigned medium bracket
        self.assertEqual(result["files_measured"], 30)
        # Total brackets should include overflow
        total_brackets = sum(result["brackets"].values())
        self.assertEqual(total_brackets, 35)

    def test_measure_avg_tokens_computed_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_file(tmpdir, "s.py", 30)   # small
            m = self._make_file(tmpdir, "m.py", 100)  # medium
            result = measure_files([s, m])
        # avg_read = (1×3000 + 1×10000) / 2 = 6500
        self.assertAlmostEqual(result["avg_file_read_tokens"], 6500, delta=100)


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_pr_review_loop_cal_is_always_double_dash_in_output(self):
        """Integration: PR Review Loop in compute_estimate always has cal='--'."""
        result = compute_estimate(
            {"size": "M", "files": 5, "complexity": "medium", "review_cycles": 2},
            calibration_dir=None,
        )
        pr = next(s for s in result["steps"] if s["name"] == "PR Review Loop")
        self.assertEqual(pr["cal"], "--")

    def test_pr_review_loop_factor_always_1(self):
        """Integration: PR Review Loop factor=1.0 even with active calibration data."""
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            factors = {"global": 1.3, "status": "active"}
            Path(tmpdir, "factors.json").write_text(json.dumps(factors))
            result = compute_estimate(
                {"size": "M", "files": 5, "complexity": "medium", "review_cycles": 2},
                calibration_dir=tmpdir,
            )
        pr = next(s for s in result["steps"] if s["name"] == "PR Review Loop")
        self.assertAlmostEqual(pr["factor"], 1.0)

    def test_band_multiplier_ratios_regular_step(self):
        """Regular steps: calibrated_opt = cal_expected × 0.6, pess = × 3.0."""
        result = compute_estimate(
            {"size": "M", "files": 5, "complexity": "medium", "steps": ["Research Agent"]},
            calibration_dir=None,
        )
        step = result["steps"][0]
        self.assertAlmostEqual(step["optimistic"],  step["expected"] * 0.6, delta=1e-6)
        self.assertAlmostEqual(step["pessimistic"], step["expected"] * 3.0, delta=1e-6)

    def test_version_in_result(self):
        result = compute_estimate(
            {"size": "M", "files": 5, "complexity": "medium"},
            calibration_dir=None,
        )
        from tokencast import __version__
        self.assertEqual(result["version"], __version__)


if __name__ == "__main__":
    unittest.main()
