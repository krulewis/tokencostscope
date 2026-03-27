"""Additional unit tests for estimation_engine.py.

Covers staff review findings #6, #9, #10, #11 plus exact PR Review Loop
arithmetic. These tests are in a new file to avoid modifying the stable
109-test baseline in test_estimation_engine.py.

Uses current pricing.py values (NOT stale examples.md values — examples.md
uses the pre-v1.3.1 two-term cache formula and old Opus pricing).

All float comparisons use pytest.approx(rel=1e-6) unless noted otherwise.
"""

import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

import pytest

# Insert src/ onto the path so we can import tokencast submodules directly.
_SRC_DIR = str(Path(__file__).parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from tokencast.estimation_engine import (
    _apply_calibration,
    _compute_pr_review_loop,
    _compute_step_base_tokens,
    _compute_step_cost,
    _resolve_steps,
    compute_estimate,
)
from tokencast import pricing, heuristics


# ---------------------------------------------------------------------------
# TestNIndependenceFixedCountSteps  (staff finding #6)
# ---------------------------------------------------------------------------

class TestNIndependenceFixedCountSteps(unittest.TestCase):
    """Verify that fixed-count steps produce identical base token tuples
    regardless of the N (file count) parameter passed in.

    Fixed-count steps are those where file_read count is not N-scaling:
    Research Agent, Engineer Initial Plan, Engineer Final Plan, QA.
    Also Architect Agent and Staff Review have no file_read activity at all.

    Contrast tests verify that N-scaling steps (Test Writing, Implementation)
    DO change when N changes.
    """

    def _base(self, step_name, N):
        """Call _compute_step_base_tokens with medium-default token sizes."""
        return _compute_step_base_tokens(step_name, N, None, 10000.0, 2500.0)

    # --- Fixed-count steps: N=0 == N=5 ---

    def test_research_agent_n0_equals_n5(self):
        # Research Agent: file_read×6(fixed), grep_search×4, planning_step×1, conv_turn×3
        # input  = 6×10000 + 4×500 + 1×3000 + 3×5000 = 80000
        # output = 6×200   + 4×500 + 1×4000 + 3×1500 = 11700
        # K      = 6+4+1+3 = 14
        result_n0 = self._base("Research Agent", 0)
        result_n5 = self._base("Research Agent", 5)
        self.assertEqual(result_n0, result_n5)
        self.assertEqual(result_n0, (80000, 11700, 14))

    def test_research_agent_n20_equals_n5(self):
        result_n5  = self._base("Research Agent", 5)
        result_n20 = self._base("Research Agent", 20)
        self.assertEqual(result_n5, result_n20)

    def test_engineer_initial_plan_n0_equals_n5(self):
        # Engineer Initial Plan: file_read×4(fixed), grep_search×2, planning_step×1, conv×2
        # input  = 4×10000 + 2×500 + 1×3000 + 2×5000 = 54000
        # output = 4×200   + 2×500 + 1×4000 + 2×1500 = 8800
        # K      = 4+2+1+2 = 9
        result_n0 = self._base("Engineer Initial Plan", 0)
        result_n5 = self._base("Engineer Initial Plan", 5)
        self.assertEqual(result_n0, result_n5)
        self.assertEqual(result_n0, (54000, 8800, 9))

    def test_engineer_final_plan_n0_equals_n5(self):
        # Engineer Final Plan: file_read×2(fixed), planning_step×1, conv×2
        # input  = 2×10000 + 1×3000 + 2×5000 = 33000
        # output = 2×200   + 1×4000 + 2×1500 = 7400
        # K      = 2+1+2 = 5
        result_n0 = self._base("Engineer Final Plan", 0)
        result_n5 = self._base("Engineer Final Plan", 5)
        self.assertEqual(result_n0, result_n5)
        self.assertEqual(result_n0, (33000, 7400, 5))

    def test_staff_review_n0_equals_n5(self):
        # Staff Review: code_review_pass×1, conv_turn×2 (no file_read at all)
        # input  = 1×8000 + 2×5000 = 18000
        # output = 1×3000 + 2×1500 = 6000
        # K      = 1+2 = 3
        result_n0 = self._base("Staff Review", 0)
        result_n5 = self._base("Staff Review", 5)
        self.assertEqual(result_n0, result_n5)
        self.assertEqual(result_n0, (18000, 6000, 3))

    def test_architect_agent_n0_equals_n5(self):
        # Architect Agent: code_review_pass×1, planning_step×1, conv_turn×2
        # input  = 1×8000 + 1×3000 + 2×5000 = 21000
        # output = 1×3000 + 1×4000 + 2×1500 = 10000
        # K      = 1+1+2 = 4
        result_n0 = self._base("Architect Agent", 0)
        result_n5 = self._base("Architect Agent", 5)
        self.assertEqual(result_n0, result_n5)
        self.assertEqual(result_n0, (21000, 10000, 4))

    def test_qa_n0_equals_n5(self):
        # QA: shell_command×3, file_read×2(fixed-count via FILE_SIZE_BRACKETS), conv×2
        # input  = 3×300 + 2×10000 + 2×5000 = 30900
        # output = 3×500 + 2×200   + 2×1500 = 4900
        # K      = 3+2+2 = 7
        result_n0 = self._base("QA", 0)
        result_n5 = self._base("QA", 5)
        self.assertEqual(result_n0, result_n5)
        self.assertEqual(result_n0, (30900, 4900, 7))

    # --- N-scaling contrast tests: N=0 != N=5 ---

    def test_test_writing_n0_differs_from_n5(self):
        # Test Writing has N-scaling test_write activity: K = 3 + N + 3
        # N=0: K=6;  N=5: K=11
        result_n0 = self._base("Test Writing", 0)
        result_n5 = self._base("Test Writing", 5)
        self.assertNotEqual(result_n0, result_n5)
        # Verify K differs: K=3(file_read)+N(test_write)+3(conv)
        _, _, k0 = result_n0
        _, _, k5 = result_n5
        self.assertEqual(k0, 6)   # 3 + 0 + 3
        self.assertEqual(k5, 11)  # 3 + 5 + 3

    def test_implementation_n0_differs_from_n5(self):
        # Implementation has N-scaling file_read and file_edit: K = N + N + 4
        # N=0: K=4;  N=5: K=14
        result_n0 = self._base("Implementation", 0)
        result_n5 = self._base("Implementation", 5)
        self.assertNotEqual(result_n0, result_n5)
        _, _, k0 = result_n0
        _, _, k5 = result_n5
        self.assertEqual(k0, 4)   # 0 + 0 + 4
        self.assertEqual(k5, 14)  # 5 + 5 + 4


# ---------------------------------------------------------------------------
# TestUnknownStepWarning  (staff finding #11)
# ---------------------------------------------------------------------------

class TestUnknownStepWarning(unittest.TestCase):
    """Verify that unknown step names in the steps override are dropped
    with a UserWarning whose message text contains the step name.

    The existing test_resolve_steps_unknown_step_dropped only checks that
    the step is absent from the result; these tests assert the warning itself.
    """

    def test_unknown_step_in_resolve_steps_emits_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _resolve_steps("M", ["Implementation", "BogusStep", "QA"])
        # At least one UserWarning should have been emitted
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        self.assertGreaterEqual(len(user_warnings), 1)

    def test_unknown_step_warning_message_contains_step_name(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _resolve_steps("M", ["Implementation", "BogusStep", "QA"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        messages = [str(x.message) for x in user_warnings]
        self.assertTrue(
            any("BogusStep" in msg for msg in messages),
            f"Expected 'BogusStep' in warning messages, got: {messages}",
        )

    def test_compute_estimate_with_unknown_steps_skips_them(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = compute_estimate(
                {
                    "size": "M",
                    "files": 5,
                    "complexity": "medium",
                    "steps": ["Implementation", "NonExistentStep"],
                },
                calibration_dir=None,
            )
        step_names = [s["name"] for s in result["steps"]]
        self.assertIn("Implementation", step_names)
        self.assertNotIn("NonExistentStep", step_names)

    def test_compute_estimate_with_all_unknown_steps_returns_empty_step_list(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = compute_estimate(
                {
                    "size": "M",
                    "files": 5,
                    "complexity": "medium",
                    "steps": ["Foo", "Bar"],
                    "review_cycles": 0,
                },
                calibration_dir=None,
            )
        self.assertEqual(result["steps"], [])
        self.assertAlmostEqual(result["estimate"]["optimistic"], 0.0, places=9)
        self.assertAlmostEqual(result["estimate"]["expected"], 0.0, places=9)
        self.assertAlmostEqual(result["estimate"]["pessimistic"], 0.0, places=9)


# ---------------------------------------------------------------------------
# TestPRReviewLoopNotAffectedByCalibration  (staff finding #10)
# ---------------------------------------------------------------------------

class TestPRReviewLoopNotAffectedByCalibration(unittest.TestCase):
    """Verify that PR Review Loop dollar values are numerically identical
    regardless of which calibration factors are active.

    The existing tests verify the `cal` and `factor` fields on the row.
    These tests verify the *dollar amounts* are unchanged.
    """

    _BASE_PARAMS = {
        "size": "M",
        "files": 5,
        "complexity": "medium",
        "review_cycles": 2,
    }

    def _pr_row(self, result):
        return next(s for s in result["steps"] if s["name"] == "PR Review Loop")

    def _estimate_with_global_factor(self, factor_value, extra_params=None):
        params = dict(self._BASE_PARAMS, **(extra_params or {}))
        with tempfile.TemporaryDirectory() as tmpdir:
            factors = {"global": factor_value, "status": "active"}
            (Path(tmpdir) / "factors.json").write_text(json.dumps(factors))
            return compute_estimate(params, calibration_dir=tmpdir)

    def _estimate_no_calibration(self, extra_params=None):
        params = dict(self._BASE_PARAMS, **(extra_params or {}))
        return compute_estimate(params, calibration_dir=None)

    def test_pr_review_loop_expected_unchanged_with_global_factor(self):
        no_cal  = self._pr_row(self._estimate_no_calibration())
        with_cal = self._pr_row(self._estimate_with_global_factor(1.5))
        self.assertAlmostEqual(
            no_cal["expected"], with_cal["expected"], places=9,
            msg="PR Review Loop expected cost should not change with global factor 1.5",
        )

    def test_pr_review_loop_optimistic_unchanged_with_global_factor(self):
        no_cal  = self._pr_row(self._estimate_no_calibration())
        with_cal = self._pr_row(self._estimate_with_global_factor(1.5))
        self.assertAlmostEqual(no_cal["optimistic"], with_cal["optimistic"], places=9)

    def test_pr_review_loop_pessimistic_unchanged_with_global_factor(self):
        no_cal  = self._pr_row(self._estimate_no_calibration())
        with_cal = self._pr_row(self._estimate_with_global_factor(1.5))
        self.assertAlmostEqual(no_cal["pessimistic"], with_cal["pessimistic"], places=9)

    def test_pr_review_loop_unchanged_with_step_factor(self):
        no_cal = self._pr_row(self._estimate_no_calibration())
        with tempfile.TemporaryDirectory() as tmpdir:
            factors = {
                "step_factors": {
                    "Staff Review": {"factor": 1.8, "status": "active"},
                    "Engineer Final Plan": {"factor": 0.7, "status": "active"},
                }
            }
            (Path(tmpdir) / "factors.json").write_text(json.dumps(factors))
            with_cal = self._pr_row(
                compute_estimate(dict(self._BASE_PARAMS), calibration_dir=tmpdir)
            )
        self.assertAlmostEqual(no_cal["expected"], with_cal["expected"], places=9)
        self.assertAlmostEqual(no_cal["optimistic"], with_cal["optimistic"], places=9)
        self.assertAlmostEqual(no_cal["pessimistic"], with_cal["pessimistic"], places=9)

    def test_pr_review_loop_unchanged_with_size_class_factor(self):
        # Size-class factor activates when M_n >= per_step_min_samples (3)
        no_cal = self._pr_row(self._estimate_no_calibration())
        with tempfile.TemporaryDirectory() as tmpdir:
            factors = {"M": 1.3, "M_n": 5}
            (Path(tmpdir) / "factors.json").write_text(json.dumps(factors))
            with_cal = self._pr_row(
                compute_estimate(dict(self._BASE_PARAMS), calibration_dir=tmpdir)
            )
        self.assertAlmostEqual(no_cal["expected"], with_cal["expected"], places=9)
        self.assertAlmostEqual(no_cal["optimistic"], with_cal["optimistic"], places=9)
        self.assertAlmostEqual(no_cal["pessimistic"], with_cal["pessimistic"], places=9)

    def test_pr_review_loop_unchanged_with_review_cycles_4(self):
        """Project override review_cycles=4 also leaves PR loop dollar values unchanged."""
        extra = {"review_cycles": 4}
        no_cal  = self._pr_row(self._estimate_no_calibration(extra_params=extra))
        with_cal = self._pr_row(self._estimate_with_global_factor(1.5, extra_params=extra))
        self.assertAlmostEqual(no_cal["expected"], with_cal["expected"], places=9)
        self.assertAlmostEqual(no_cal["optimistic"], with_cal["optimistic"], places=9)
        self.assertAlmostEqual(no_cal["pessimistic"], with_cal["pessimistic"], places=9)


# ---------------------------------------------------------------------------
# TestFullChainVerification  (staff finding #9)
# ---------------------------------------------------------------------------

class TestFullChainVerification(unittest.TestCase):
    """Verify the complete computation chain for individual pipeline steps.

    Each test derives the expected value from pricing.py constants inline,
    making the math auditable. Uses the three-term cache cost formula
    (NOT the old two-term formula in examples.md).

    Derived from current pricing.py (not examples.md — that uses stale prices
    + two-term formula, as noted in the file itself on lines 6-9).
    """

    def _chain_cost(self, input_base, output_base, K, model_id, band):
        """Manually compute one band cost from first principles."""
        prices = pricing.MODEL_PRICES[model_id]
        p_in  = prices["input"]
        p_cw  = prices["cache_write"]
        p_cr  = prices["cache_read"]
        p_out = prices["output"]

        # 3b: complexity (medium = 1.0, no change)
        cmx = heuristics.COMPLEXITY_MULTIPLIERS["medium"]
        input_complex  = input_base  * cmx
        output_complex = output_base * cmx

        # 3c: context accumulation
        input_accum = input_complex * (K + 1) / 2

        # cache_write_fraction = 1/K (K > 0)
        cwf = 1.0 / K

        cache_rate = pricing.CACHE_HIT_RATES[band]
        band_mult  = heuristics.BAND_MULTIPLIERS[band]

        # 3d: three-term cache cost
        input_cost = (
            input_accum * (1 - cache_rate) * p_in
            + input_accum * cache_rate * cwf * p_cw
            + input_accum * cache_rate * (1 - cwf) * p_cr
        ) / 1_000_000

        output_cost = output_complex * p_out / 1_000_000

        return (input_cost + output_cost) * band_mult

    # --- Research Agent (Sonnet, K=14) ---

    def test_research_agent_full_chain_expected_band(self):
        # Derived from current pricing.py (not examples.md — stale)
        input_base  = 80000   # 6×10000 + 4×500 + 1×3000 + 3×5000
        output_base = 11700   # 6×200   + 4×500 + 1×4000 + 3×1500
        K = 14
        expected_val = self._chain_cost(
            input_base, output_base, K, pricing.MODEL_SONNET, "expected"
        )
        engine_costs = _compute_step_cost(
            input_base, output_base, K, "medium", pricing.MODEL_SONNET, False
        )
        assert engine_costs["expected"] == pytest.approx(expected_val, rel=1e-6)

    def test_research_agent_full_chain_optimistic_band(self):
        input_base, output_base, K = 80000, 11700, 14
        expected_val = self._chain_cost(
            input_base, output_base, K, pricing.MODEL_SONNET, "optimistic"
        )
        engine_costs = _compute_step_cost(
            input_base, output_base, K, "medium", pricing.MODEL_SONNET, False
        )
        assert engine_costs["optimistic"] == pytest.approx(expected_val, rel=1e-6)

    def test_research_agent_full_chain_pessimistic_band(self):
        input_base, output_base, K = 80000, 11700, 14
        expected_val = self._chain_cost(
            input_base, output_base, K, pricing.MODEL_SONNET, "pessimistic"
        )
        engine_costs = _compute_step_cost(
            input_base, output_base, K, "medium", pricing.MODEL_SONNET, False
        )
        assert engine_costs["pessimistic"] == pytest.approx(expected_val, rel=1e-6)

    # --- Architect Agent (Opus, K=4) ---

    def test_architect_agent_full_chain_expected_band(self):
        # Architect Agent: code_review_pass×1, planning_step×1, conv_turn×2
        # input  = 1×8000 + 1×3000 + 2×5000 = 21000
        # output = 1×3000 + 1×4000 + 2×1500 = 10000
        # K = 4
        # Derived from current pricing.py (Opus: $5/$25 not old $15/$75 in examples.md)
        input_base, output_base, K = 21000, 10000, 4
        expected_val = self._chain_cost(
            input_base, output_base, K, pricing.MODEL_OPUS, "expected"
        )
        engine_costs = _compute_step_cost(
            input_base, output_base, K, "medium", pricing.MODEL_OPUS, False
        )
        assert engine_costs["expected"] == pytest.approx(expected_val, rel=1e-6)

    # --- Staff Review (Opus, K=3) ---

    def test_staff_review_full_chain_expected_band(self):
        # Staff Review: code_review_pass×1, conv_turn×2
        # input  = 1×8000 + 2×5000 = 18000
        # output = 1×3000 + 2×1500 = 6000
        # K = 3
        input_base, output_base, K = 18000, 6000, 3
        expected_val = self._chain_cost(
            input_base, output_base, K, pricing.MODEL_OPUS, "expected"
        )
        engine_costs = _compute_step_cost(
            input_base, output_base, K, "medium", pricing.MODEL_OPUS, False
        )
        assert engine_costs["expected"] == pytest.approx(expected_val, rel=1e-6)

    # --- Engineer Final Plan (Sonnet, K=5) ---

    def test_engineer_final_plan_full_chain_expected_band(self):
        # Engineer Final Plan: file_read×2(fixed), planning_step×1, conv_turn×2
        # input  = 2×10000 + 1×3000 + 2×5000 = 33000
        # output = 2×200   + 1×4000 + 2×1500 = 7400
        # K = 5
        input_base, output_base, K = 33000, 7400, 5
        expected_val = self._chain_cost(
            input_base, output_base, K, pricing.MODEL_SONNET, "expected"
        )
        engine_costs = _compute_step_cost(
            input_base, output_base, K, "medium", pricing.MODEL_SONNET, False
        )
        assert engine_costs["expected"] == pytest.approx(expected_val, rel=1e-6)

    # --- Calibration chain ---

    def test_full_chain_with_calibration_factor_scales_expected_only(self):
        """_apply_calibration(costs, factor) scales expected by factor."""
        # Derived from current pricing.py (not examples.md)
        raw = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, False)
        cal = _apply_calibration(raw, 0.8)
        assert cal["expected"] == pytest.approx(raw["expected"] * 0.8, rel=1e-6)

    def test_full_chain_band_ratios_hold_after_calibration(self):
        """After calibration, Opt = Expected×0.6 and Pess = Expected×3.0."""
        raw = _compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, False)
        cal = _apply_calibration(raw, 0.8)
        assert cal["optimistic"]  == pytest.approx(cal["expected"] * 0.6, rel=1e-9)
        assert cal["pessimistic"] == pytest.approx(cal["expected"] * 3.0, rel=1e-9)


# ---------------------------------------------------------------------------
# TestPRReviewLoopExactArithmetic
# ---------------------------------------------------------------------------

class TestPRReviewLoopExactArithmetic(unittest.TestCase):
    """Verify the PR Review Loop geometric decay formula with exact arithmetic.

    Formula (per SKILL.md and _compute_pr_review_loop):
        C = staff_review_expected_pre + engineer_final_expected_pre
        loop_cost(cycles) = C × (1 - decay^cycles) / (1 - decay)
        decay = 0.6

    Band cycle counts:
        optimistic  → 1 cycle
        expected    → N cycles  (where N = review_cycles param)
        pessimistic → N×2 cycles
    """

    def setUp(self):
        """Compute C from current pricing using the engine's own functions."""
        # Staff Review: Opus, K=3, medium complexity
        staff_base = _compute_step_base_tokens(
            "Staff Review", 5, None, 10000.0, 2500.0
        )
        staff_costs = _compute_step_cost(
            staff_base[0], staff_base[1], staff_base[2],
            "medium", pricing.MODEL_OPUS, False,
        )
        self.staff_pre = staff_costs["expected_pre_discount"]

        # Engineer Final Plan: Sonnet, K=5, medium complexity
        final_base = _compute_step_base_tokens(
            "Engineer Final Plan", 5, None, 10000.0, 2500.0
        )
        final_costs = _compute_step_cost(
            final_base[0], final_base[1], final_base[2],
            "medium", pricing.MODEL_SONNET, False,
        )
        self.final_pre = final_costs["expected_pre_discount"]

        self.C = self.staff_pre + self.final_pre
        self.decay = heuristics.PR_REVIEW_LOOP["review_decay_factor"]  # 0.6

    def _loop_cost(self, cycles):
        """Geometric decay formula: C × (1 - decay^cycles) / (1 - decay)."""
        if cycles == 0:
            return 0.0
        return self.C * (1 - self.decay ** cycles) / (1 - self.decay)

    def test_pr_review_loop_c_value_from_current_pricing(self):
        """C is the sum of the two constituents' expected_pre_discount values."""
        result = _compute_pr_review_loop(
            self.staff_pre, self.final_pre, 2, {}, "M"
        )
        # C must be positive and consistent with the sum of the two inputs
        self.assertGreater(self.C, 0)
        self.assertGreater(self.staff_pre, 0)
        self.assertGreater(self.final_pre, 0)
        # N=2 expected = C × (1 - 0.6^2) / 0.4 = C × 0.64/0.4 = C × 1.6
        assert result["expected"] == pytest.approx(self.C * 1.6, rel=1e-6)

    def test_pr_review_loop_n1_opt_equals_c(self):
        """Optimistic always uses 1 cycle: C × (1-0.6^1)/(1-0.6) = C × 1.0."""
        result = _compute_pr_review_loop(
            self.staff_pre, self.final_pre, 2, {}, "M"
        )
        # opt_cycles = 1: (1 - 0.6) / 0.4 = 0.4/0.4 = 1.0
        assert result["optimistic"] == pytest.approx(self.C * 1.0, rel=1e-6)

    def test_pr_review_loop_n2_exp_equals_c_times_1_6(self):
        """review_cycles=2 expected: C × (1 - 0.6^2) / 0.4 = C × 1.6."""
        result = _compute_pr_review_loop(
            self.staff_pre, self.final_pre, 2, {}, "M"
        )
        expected_multiplier = (1 - 0.6 ** 2) / (1 - 0.6)  # = 1.6
        assert result["expected"] == pytest.approx(self.C * expected_multiplier, rel=1e-6)

    def test_pr_review_loop_n4_pess_formula(self):
        """review_cycles=2 pessimistic uses 4 cycles: C × (1-0.6^4)/0.4 = C × 2.176."""
        result = _compute_pr_review_loop(
            self.staff_pre, self.final_pre, 2, {}, "M"
        )
        # pess_cycles = 2×2 = 4
        # (1 - 0.6^4) / 0.4 = (1 - 0.1296) / 0.4 = 0.8704 / 0.4 = 2.176
        pess_multiplier = (1 - 0.6 ** 4) / (1 - 0.6)
        assert result["pessimistic"] == pytest.approx(self.C * pess_multiplier, rel=1e-4)
        assert pess_multiplier == pytest.approx(2.176, rel=1e-6)

    def test_pr_review_loop_n3_custom_cycles(self):
        """review_cycles=3: expected uses 3 cycles, pessimistic uses 6 cycles."""
        result = _compute_pr_review_loop(
            self.staff_pre, self.final_pre, 3, {}, "M"
        )
        # expected: (1 - 0.6^3) / 0.4 = (1 - 0.216) / 0.4 = 0.784/0.4 = 1.96
        exp_mult  = (1 - 0.6 ** 3) / (1 - 0.6)
        # pessimistic: (1 - 0.6^6) / 0.4
        pess_mult = (1 - 0.6 ** 6) / (1 - 0.6)
        assert result["expected"]    == pytest.approx(self.C * exp_mult,  rel=1e-6)
        assert result["pessimistic"] == pytest.approx(self.C * pess_mult, rel=1e-6)

    def test_pr_review_loop_pess_is_twice_n_cycles(self):
        """Pessimistic always uses 2×N cycles — derive same value from formula."""
        for N in (1, 2, 3, 4):
            with self.subTest(N=N):
                result = _compute_pr_review_loop(
                    self.staff_pre, self.final_pre, N, {}, "M"
                )
                pess_cycles = N * 2
                manual = self._loop_cost(pess_cycles)
                assert result["pessimistic"] == pytest.approx(manual, rel=1e-6)

    def test_pr_review_loop_none_when_cycles_zero(self):
        """review_cycles=0 returns None (no PR Review Loop row)."""
        result = _compute_pr_review_loop(
            self.staff_pre, self.final_pre, 0, {}, "M"
        )
        self.assertIsNone(result)

    def test_pr_review_loop_factor_always_1_and_cal_always_dashes(self):
        """Regardless of factors dict content, factor=1.0 and cal='--'."""
        factors = {"global": 2.0, "status": "active"}
        result = _compute_pr_review_loop(
            self.staff_pre, self.final_pre, 2, factors, "M"
        )
        self.assertEqual(result["factor"], 1.0)
        self.assertEqual(result["cal_label"], "--")


if __name__ == "__main__":
    unittest.main()
