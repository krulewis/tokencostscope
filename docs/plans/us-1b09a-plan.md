# Implementation Plan: US-1b.09a — Engine Unit Tests (Additional Coverage)

*Produced by: engineer agent*
*Date: 2026-03-26*

---

## Overview

`tests/test_estimation_engine.py` already has 109 tests across 9 test classes. This plan adds a new test class — `TestWorkedExamplesVerification` — plus targeted tests inside a new `TestStaffReviewFindings` class to close the gaps identified by the staff review.

The new tests go in a **new file** `tests/test_estimation_engine_additional.py` to avoid merge conflicts with ongoing work and to keep the existing test file unchanged. The new file follows the same structure (unittest.TestCase classes, `_SRC_DIR` path insertion, pytest.approx tolerance).

Key gaps being closed:

1. **Full-chain exact arithmetic** — steps 3a → 3b → 3c → 3d → 3e chained for Research Agent and Architect Agent against examples.md worked values. Examples.md uses the *two-term* cache formula with *old pricing*; the new tests use the *three-term formula* with *current pricing.py values* (not examples.md values) — this is the correct approach (the existing TestThreeTermCacheFormula comment already explains this discrepancy). The tests verify the chain structure and formula correctness, not stale dollar amounts.

2. **PR Review Loop NOT affected by global calibration** — staff review finding #10. A test in compute_estimate end-to-end verifies the PR Review Loop row's expected value with a global factor of 1.5 exactly equals the value with no calibration.

3. **Unknown step names dropped with warning** — staff review finding #11. The existing `test_resolve_steps_unknown_step_dropped` tests that the step is absent from the list but does NOT assert a warning is emitted. The new test asserts a `UserWarning` matching the expected message text is issued.

4. **N-independence for fixed-count steps** — staff review finding #6. Tests that Research Agent (`file_read` count=6, fixed), Engineer Initial Plan (`file_read` count=4, fixed), Engineer Final Plan (`file_read` count=2, fixed) and Staff Review produce the **same** `input_base` and `K` when called with N=0, N=1, N=5, and N=20.

5. **End-to-end per-step full chain** — staff review finding #9. Single-step `compute_estimate` calls verifying every transformation (base → complexity → accumulation → cache → band × band_mult → calibration=1.0) produces values consistent with the formula derivation.

6. **Example 2 PR Review Loop arithmetic** — exact verification of the C=($0.7470+$0.2744) and decay formula outputs from examples.md (using current pricing, which has different Opus/Sonnet rates; the test uses `_compute_pr_review_loop` directly with the values derivable from current pricing).

---

## Changes

```
File: tests/test_estimation_engine_additional.py
Lines: new file
Parallelism: independent
Description: New test file with 4 test classes covering the 4 staff review findings plus
             worked example verification. No changes to any existing files.
Details:
  - Class TestNIndependenceFixedCountSteps (finding #6):
      test_research_agent_n0_equals_n5
      test_research_agent_n20_equals_n5
      test_engineer_initial_plan_n0_equals_n5
      test_engineer_final_plan_n0_equals_n5
      test_staff_review_n0_equals_n5
      test_qa_n0_differs_from_n5  (QA has only shell/read/conv — none are N-scaling, so also N-independent; assert equal)
      test_test_writing_n0_differs_from_n5  (Test Writing has N-scaling test_writes — assert n0 != n5)
      test_implementation_n0_differs_from_n5  (Implementation has N-scaling — assert n0 != n5)

  - Class TestUnknownStepWarning (finding #11):
      test_unknown_step_in_resolve_steps_emits_warning
      test_unknown_step_warning_message_contains_step_name
      test_compute_estimate_with_unknown_steps_skips_them
      test_compute_estimate_with_all_unknown_steps_returns_empty_step_list

  - Class TestPRReviewLoopNotAffectedByCalibration (finding #10):
      test_pr_review_loop_expected_unchanged_with_global_factor
      test_pr_review_loop_optimistic_unchanged_with_global_factor
      test_pr_review_loop_pessimistic_unchanged_with_global_factor
      test_pr_review_loop_unchanged_with_step_factor
      test_pr_review_loop_unchanged_with_size_class_factor

  - Class TestFullChainVerification (finding #9):
      test_research_agent_full_chain_expected_band
      test_research_agent_full_chain_optimistic_band
      test_research_agent_full_chain_pessimistic_band
      test_architect_agent_full_chain_expected_band
      test_staff_review_full_chain_expected_band
      test_engineer_final_plan_full_chain_expected_band
      test_full_chain_with_calibration_factor_scales_expected_only
      test_full_chain_band_ratios_hold_after_calibration

  - Class TestPRReviewLoopExactArithmetic (from examples.md Example 2 structure):
      test_pr_review_loop_c_value_from_current_pricing
      test_pr_review_loop_n1_opt_equals_c
      test_pr_review_loop_n2_exp_equals_c_times_1_6
      test_pr_review_loop_n4_pess_formula
      test_pr_review_loop_n3_custom_cycles
      test_pr_review_loop_pess_is_twice_n_cycles
```

---

## Dependency Order

This change has no dependencies on any other changes. It is a new test file that imports from already-existing modules (`tokencast.estimation_engine`, `tokencast.pricing`, `tokencast.heuristics`).

1. Write `tests/test_estimation_engine_additional.py` (independent, can run in parallel with any other implementation work)

---

## Detailed Test Specifications

### Class: TestNIndependenceFixedCountSteps

**Purpose:** Verify that fixed-count steps (Research Agent, Architect Agent, Engineer Initial Plan, Staff Review, Engineer Final Plan, QA) produce identical `input_base`, `output_base`, and `K` values regardless of the `N` parameter passed in. This is staff review finding #6.

**Method:** Call `_compute_step_base_tokens(step_name, N, None, 10000.0, 2500.0)` with N=0 and N=5, assert the returned tuples are equal.

**Contrast tests:** Call `_compute_step_base_tokens("Test Writing", N, None, 10000.0, 2500.0)` with N=0 and N=5 — `K` must differ (N-scaling test_writes). Same for Implementation.

**Exact assertions:**
- `Research Agent`: N=0 and N=5 both produce `(80000, 11700, 14)`
- `Engineer Initial Plan`: N=0 and N=5 both produce `(54000, 8800, 9)`
- `Engineer Final Plan`: N=0 and N=5 both produce `(33000, 7400, 5)`
- `Staff Review`: N=0 and N=5 both produce `(18000, 6000, 3)`
- `Architect Agent`: N=0 and N=5 both produce `(21000, 10000, 4)`
- `QA`: N=0 and N=5 both produce `(30900, 4900, 7)` — no N-scaling activities, all fixed counts
- `Test Writing` N=0 != N=5: K=6 vs K=11 (test_writes scale with N)
- `Implementation` N=0 != N=5: K=4 vs K=14 (reads + edits scale)

**Note:** QA uses `shell_command×3, file_read×2, conversation_turn×2` — all fixed counts. It also belongs in the N-independent group despite not being listed in `FILE_SIZE_BRACKETS["fixed_count_steps"]` (that dict covers steps with fixed-count file reads, but QA has no file_read activities). Verify the tuple equality directly.

---

### Class: TestUnknownStepWarning

**Purpose:** Verify that unknown step names in the `steps` override list are silently dropped with a `UserWarning`, not silently ignored without warning. Staff review finding #11.

The existing test `test_resolve_steps_unknown_step_dropped` (line 576–582) only checks that the step is absent from the result. It does NOT assert a warning is emitted. The new tests complete that coverage.

**Key implementation detail from `_resolve_steps` (line 69–70 of estimation_engine.py):**
```python
warnings.warn(f"Unknown step name: {s!r} — skipped", stacklevel=3)
```

**Assertions:**
- `test_unknown_step_in_resolve_steps_emits_warning`: Use `warnings.catch_warnings(record=True)` with `warnings.simplefilter("always")`. Call `_resolve_steps("M", ["Implementation", "BogusStep", "QA"])`. Assert `len(w) >= 1` and that at least one warning's message contains `"BogusStep"`.
- `test_unknown_step_warning_message_contains_step_name`: Same as above, assert `"BogusStep"` in `str(w[0].message)`.
- `test_compute_estimate_with_unknown_steps_skips_them`: Call `compute_estimate({"size": "M", "files": 5, "complexity": "medium", "steps": ["Implementation", "NonExistentStep"]})`. Verify step list contains only `"Implementation"`, not `"NonExistentStep"`.
- `test_compute_estimate_with_all_unknown_steps_returns_empty_step_list`: Call `compute_estimate` with `steps=["Foo", "Bar"]`. Verify `result["steps"]` is empty list and totals are all 0.0.

---

### Class: TestPRReviewLoopNotAffectedByCalibration

**Purpose:** Verify that the PR Review Loop cost is **numerically unchanged** when calibration factors are present — not just that the factor field reads 1.0, but that the dollar values are identical with and without active calibration. Staff review finding #10.

The existing tests `test_estimate_calibration_factor_1_on_pr_review_loop` and `test_pr_review_loop_factor_always_1` verify the `factor` and `cal` fields. The new tests verify the *dollar amounts* are unchanged.

**Method:** Run `compute_estimate` once without calibration dir, once with a tempdir containing an active global factor (1.5). Extract PR Review Loop row from each. Assert `pr_with_cal["expected"] == pytest.approx(pr_no_cal["expected"])`. Repeat for optimistic and pessimistic bands. Add variants for step-level and size-class factors.

**Additional variant:** Use `review_cycles=4` (the project override) to test with a different cycle count.

---

### Class: TestFullChainVerification

**Purpose:** Verify the complete computation chain for individual steps. Staff review finding #9.

For each step under test, derive expected values from first principles using `pricing.py` current values (not examples.md stale values). Express the formula steps in the test itself so a reader can audit the math.

**Research Agent full chain (Expected band):**
```
input_base  = 6×10000 + 4×500 + 1×3000 + 3×5000 = 80000
output_base = 6×200   + 4×500 + 1×4000 + 3×1500 = 11700
K = 14
complexity_mult = 1.0 (medium)
input_complex = 80000
output_complex = 11700
input_accum = 80000 × (14+1)/2 = 600000
cache_rate_expected = 0.50
cache_write_fraction = 1/14
price_in = 3.00, price_cw = 3.75, price_cr = 0.30, price_out = 15.00
input_cost = (600000×0.50×3.00 + 600000×0.50×(1/14)×3.75 + 600000×0.50×(13/14)×0.30) / 1e6
output_cost = 11700 × 15.00 / 1e6
band_mult = 1.0 (expected)
calibration = 1.0 (no factors)
step_cost = (input_cost + output_cost) × 1.0 × 1.0
```

The test asserts `_compute_step_cost(80000, 11700, 14, "medium", pricing.MODEL_SONNET, False)["expected"]` equals the manually computed value using `pytest.approx(rel=1e-6)`.

**Pattern:** Express each formula term as a Python constant computed inline in the test, then assert the engine function returns the same value. This makes the test self-documenting and auditable.

**Steps to cover:**
- Research Agent (Sonnet, K=14) — Optimistic, Expected, Pessimistic
- Architect Agent (Opus, K=4) — Expected only (different pricing scale)
- Staff Review (Opus, K=3) — Expected
- Engineer Final Plan (Sonnet, K=5) — Expected

**Calibration chain test:**
- Apply `_apply_calibration(costs, factor=0.8)` to Research Agent result, then verify:
  - `cal["expected"] == raw_expected × 0.8`
  - `cal["optimistic"] == cal["expected"] × 0.6`
  - `cal["pessimistic"] == cal["expected"] × 3.0`

**Note on examples.md discrepancy:** The examples.md values use old Opus pricing ($15.00/$75.00 input/output) vs current pricing.py ($5.00/$25.00). The examples.md Sonnet pricing also differs ($3.00 input/$15.00 output matches but cache_write was $0 in the two-term formula vs $3.75 now). Tests must derive expected values from `pricing.py` constants, not hardcode examples.md numbers. Add a comment in each test explaining this.

---

### Class: TestPRReviewLoopExactArithmetic

**Purpose:** Verify the PR Review Loop geometric decay formula with exact arithmetic using current pricing. Extends the existing `TestPRReviewLoop` class (which tests formula structure) with exact numerical assertions.

**Setup:** Compute C from current pricing using `_compute_step_base_tokens` + `_compute_step_cost` for Staff Review and Engineer Final Plan (same approach as `TestPRReviewLoop._pr_input()`). Store `C = staff_pre + final_pre`.

**Tests:**
- `test_pr_review_loop_c_value_from_current_pricing`: Assert C > 0 and that it equals the sum of `expected_pre_discount` for Staff Review and Engineer Final Plan.
- `test_pr_review_loop_n1_opt_equals_c`: `review_loop_cost(1) = C × (1-0.6)/0.4 = C × 1.0`. Assert `result["optimistic"] == pytest.approx(C * 1.0, rel=1e-6)`.
- `test_pr_review_loop_n2_exp_equals_c_times_1_6`: Assert `result["expected"] == pytest.approx(C * 1.6, rel=1e-6)`.
- `test_pr_review_loop_n4_pess_formula`: N=2 → pessimistic cycles=4. `C × (1-0.6^4)/0.4 = C × 0.8704/0.4 = C × 2.176`. Assert `result["pessimistic"] == pytest.approx(C * 2.176, rel=1e-4)`.
- `test_pr_review_loop_n3_custom_cycles`: Call `_compute_pr_review_loop(staff_pre, final_pre, 3, {}, "M")`. Expected cycles=3: `C × (1-0.6^3)/0.4 = C × 0.784/0.4 = C × 1.96`. Pessimistic cycles=6: `C × (1-0.6^6)/0.4`.
- `test_pr_review_loop_pess_is_twice_n_cycles`: Assert `result["pessimistic"]` cycles count is `N*2` by deriving the same value from the formula.

---

## Dependency Order

Only one file is being created. No other files change.

```
Step 1: Write tests/test_estimation_engine_additional.py  [independent]
```

All test classes within the file are independent of each other and can be mentally developed in any order.

---

## Test Strategy

### What to write
All new tests go in: `tests/test_estimation_engine_additional.py`

The file must:
1. Insert `_SRC_DIR` into `sys.path` using the same pattern as `test_estimation_engine.py` (lines 17–20)
2. Import the same set of functions from `tokencast.estimation_engine` and `tokencast.pricing`, `tokencast.heuristics`
3. Use `unittest.TestCase` classes with `pytest.approx` for float assertions (`rel=1e-6` default, `rel=1e-4` for accumulated multi-step values)

### Happy path
- Research Agent full chain produces positive costs in correct ratio across bands
- PR Review Loop with active calibration produces identical dollar amounts as without calibration
- Fixed-count steps with N=0 produce same result as N=5

### Edge cases
- `steps=["Foo", "Bar"]` (all unknown) → empty step list, totals=0.0
- N=0 for Implementation (N-scaling) → K=4 (only conv_turns), input=20000, significantly different from N=5
- `review_cycles=4` with active factor 1.5 — PR Review Loop unchanged

### Error cases
- Unknown step name must emit a `UserWarning` with the step name in the message text

### Tests that can run in parallel with implementation
All tests in the new file are purely additive — they call existing engine functions and assert correctness. They can be written and run before any other Phase 1b work proceeds. No other currently-passing tests should break.

### Existing tests that might break
None. The new file only reads from existing modules, does not modify them, and adds no monkey-patching that persists across test classes (all `patch` usage is in `with` blocks).

### Running the tests
```bash
/usr/bin/python3 -m pytest tests/test_estimation_engine_additional.py -v
/usr/bin/python3 -m pytest tests/ -v  # full suite
```

---

## Rollback Notes

This plan creates one new file. Rollback is: `rm tests/test_estimation_engine_additional.py`.

No existing files are modified. No data migrations. No configuration changes.

---

## Staff Review Finding Coverage Summary

| Finding # | Finding Description | How Covered |
|-----------|---------------------|-------------|
| #6 | N-independence for fixed-count steps | `TestNIndependenceFixedCountSteps` — 8 tests assert equality (or difference) of base token tuples across N=0 and N=5 |
| #9 | End-to-end per-step full chain | `TestFullChainVerification` — derives each formula term from `pricing.py` constants and asserts match against engine output at `rel=1e-6` |
| #10 | PR Review Loop not affected by global calibration | `TestPRReviewLoopNotAffectedByCalibration` — asserts *dollar values* (not just factor field) are identical with factor=1.0 vs factor=1.5 across all three bands |
| #11 | Unknown step names dropped with warning | `TestUnknownStepWarning` — uses `warnings.catch_warnings(record=True)` to assert a `UserWarning` is issued and its message text contains the step name |

---

## Notes on examples.md Discrepancy

`references/examples.md` was written with old pricing and the two-term cache formula. The file itself notes (lines 6–9): *"The per-step worked calculations further down use the pre-v1.3.1 two-term formula and will be recomputed in a follow-up update."*

The `TestFullChainVerification` tests must therefore:
1. Derive expected values from `pricing.py` current constants (not examples.md hardcoded dollars)
2. Include a comment in each test: `# Derived from current pricing.py (not examples.md — that uses stale prices + two-term formula)`
3. Use the three-term formula: `uncached + cache_write_term + cache_read_term`

This approach satisfies the story requirement for "End-to-end verification against worked examples" — the test verifies the *formula chain structure* from examples.md is correctly implemented, using current pricing values.
