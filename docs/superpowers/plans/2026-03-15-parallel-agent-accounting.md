# Parallel Agent Accounting Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model parallel agent execution by applying two discount factors (input accumulation × 0.75, cache rate − 0.15) to steps detected as parallel via plan text keyword scanning.

**Architecture:** Detection runs in SKILL.md Step 0 by scanning plan text for parallel keywords, producing a `parallel_groups` map. Steps 3c/3d apply discounts for parallel steps; Step 3.5 uses un-discounted constituent costs for C. Output table brackets parallel groups with ┌│└ rows. learn.sh propagates `parallel_groups` and `parallel_steps_detected` to history.jsonl.

**Tech Stack:** Bash (learn.sh), Python 3.9 (test suite, learn.sh inline Python blocks), Markdown (SKILL.md, heuristics.md)

**Spec:** `docs/superpowers/specs/2026-03-15-parallel-agent-accounting-design.md`

**Run tests with:** `/usr/bin/python3 -m pytest tests/ -v` (system Python 3.9, not Homebrew python3)

---

## Chunk 1: Tests

### Task 1: Write the failing test file

**Files:**
- Create: `tests/test_parallel_agent_accounting.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for parallel agent accounting (v1.3.0).

Tests the two discount factors, PR Review Loop C isolation, learn.sh field
forwarding, and document content verification. All document/learn.sh tests
must fail before implementation; arithmetic tests pass immediately (they test
inline helper formulas, not file content).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
LEARN_SH = SCRIPTS_DIR / "tokencast-learn.sh"
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
        c_correct = self.STAFF_REVIEW_UNDISCOUNTED + self.ENGINEER_FINAL_UNDISCOUNTED
        c_wrong = self.STAFF_REVIEW_UNDISCOUNTED * 0.75 + self.ENGINEER_FINAL_UNDISCOUNTED

        assert abs(c_correct - 1.0214) < 0.001
        assert c_wrong < c_correct  # discounted C would incorrectly lower the loop cost

    def test_c_isolation_preserves_review_loop_accuracy(self):
        """Using discounted C produces a systematically lower loop cost — must be avoided."""
        undiscounted_c = self.STAFF_REVIEW_UNDISCOUNTED + self.ENGINEER_FINAL_UNDISCOUNTED
        discounted_c = self.STAFF_REVIEW_UNDISCOUNTED * 0.75 + self.ENGINEER_FINAL_UNDISCOUNTED

        # At N=2 cycles
        loop_undiscounted = undiscounted_c * (1 - 0.6**2) / 0.4
        loop_discounted = discounted_c * (1 - 0.6**2) / 0.4

        assert loop_undiscounted > loop_discounted


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

    def test_skill_md_version_1_3_0(self):
        assert "version: 1.3.0" in SKILL_MD.read_text()

    def test_skill_md_output_template_v1_3_0(self):
        assert "v1.3.0" in SKILL_MD.read_text()

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


# ---------------------------------------------------------------------------
# learn.sh: version and field forwarding
# ---------------------------------------------------------------------------

class TestLearnScript:
    """Tests for tokencast-learn.sh changes."""

    def test_version_is_1_3_0(self):
        result = subprocess.run(
            ["bash", str(LEARN_SH), "--version"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "1.3.0" in result.stdout

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

- [ ] **Step 2: Run test file and record pass/fail counts**

Run: `/usr/bin/python3 -m pytest tests/test_parallel_agent_accounting.py -v`

Expected outcome:
- **PASS** (tests inline formulas — pass immediately by design): all of `TestCacheRateReduction`, `TestInputAccumulationDiscount`, `TestParallelStepCheaperThanSequential`, `TestPRReviewLoopCIsolation`, `TestLearnScript::test_parallel_groups_roundtrip` — approximately **15 tests pass**
- **FAIL** (document content + learn.sh file checks — require implementation): all of `TestDocumentContent`, `TestLearnScript::test_version_is_1_3_0`, `test_forwards_parallel_steps_detected`, `test_handles_missing_parallel_fields`, `test_parallel_groups_in_history_record` — approximately **20 tests fail**

If you see a different split, that's fine — what matters is that all `TestDocumentContent` and learn.sh tests fail.

- [ ] **Step 3: Commit the test file**

```bash
git add tests/test_parallel_agent_accounting.py
git commit -m "test: add failing tests for parallel agent accounting (v1.3)"
```

---

## Chunk 2: heuristics.md

### Task 2: Add Parallel Agent Accounting section to heuristics.md

**Files:**
- Modify: `references/heuristics.md`

- [ ] **Step 1: Append the new section to the end of heuristics.md**

Open `references/heuristics.md` and append the following at the very end (after the `### Aggregated Row Formula` section and its closing content):

```markdown

## Parallel Agent Accounting

When pipeline steps run as parallel subagents, two adjustments apply to reduce their estimated
cost: (1) they start fresh without inheriting accumulated context from prior steps, and (2) they
cannot reuse cache warmed by preceding sequential steps.

| Parameter                       | Value | Notes                                               |
|---------------------------------|-------|-----------------------------------------------------|
| parallel_input_discount         | 0.75  | Multiplier on input_accum for parallel steps        |
| parallel_cache_rate_reduction   | 0.15  | Subtracted from each band's cache hit rate          |
| parallel_cache_rate_floor       | 0.05  | Minimum effective cache hit rate after reduction    |

These values are heuristic estimates and will be refined via calibration as parallel-tagged
sessions accumulate in history.jsonl. Groups with fewer than 2 resolved steps are discarded
(a single-step "parallel group" is semantically meaningless and is treated as sequential).
```

- [ ] **Step 2: Run the heuristics doc tests**

Run: `/usr/bin/python3 -m pytest tests/test_parallel_agent_accounting.py::TestDocumentContent::test_heuristics_has_parallel_section tests/test_parallel_agent_accounting.py::TestDocumentContent::test_heuristics_has_parallel_input_discount tests/test_parallel_agent_accounting.py::TestDocumentContent::test_heuristics_has_parallel_cache_rate_reduction -v`

Expected: All 3 PASS

- [ ] **Step 3: Commit**

```bash
git add references/heuristics.md
git commit -m "feat: add parallel agent accounting parameters to heuristics.md"
```

---

## Chunk 3: SKILL.md — Step 0 Detection

### Task 3: Add parallel group detection to SKILL.md Step 0

**Files:**
- Modify: `SKILL.md`

Step 0 currently has items 1–7. Item 7 ends with: "N=0 naturally produces $0 via the decay formula (1−0.6^0=0); no special-case handling is needed."

- [ ] **Step 1: Add Step 0 item 8 after item 7**

Find this exact text in `SKILL.md`:
```
If none of the required constituent steps are present, set N=0. N=0 naturally produces $0 via the decay formula (1−0.6^0=0); no special-case handling is needed.
```

Add the following immediately after it (still inside Step 0):

```
8. **Parallel groups:** Scan the plan text for parallel execution indicators (case-insensitive):
   - Keywords: `"in parallel"`, `"simultaneously"`, `"concurrently"`, `"∥"`, `"parallel:"`,
     `"[parallel]"`, `"(parallel)"`
   - For each keyword match, identify step names in the same grouping window: step names joined
     by comma, `+`, or `"and"` immediately preceding (or following, for `"parallel:"` prefix
     syntax) the keyword.
   - **Boundaries:** Sentence breaks (`.`, `\n`) and sequencing words (`"then"`, `"first"`,
     `"after"`, `"before"`, `"next"`) are hard boundaries — step names on the far side are
     not included in the group.
   - **Matching:** Case-insensitive substring match against canonical step names in heuristics.md.
     If a token matches multiple canonical names (e.g., `"engineer"` → both `"Engineer Initial
     Plan"` and `"Engineer Final Plan"`), treat it as ambiguous and note in transparency output:
     `"Ambiguous: 'engineer' matches multiple steps — falls back to sequential modeling"`.
     Unrecognized tokens: `"Unresolved: 'Researcher' — falls back to sequential modeling"`.
   - **Conflict:** A step belongs to at most one group — first occurrence wins.
   - **Minimum size:** Groups with fewer than 2 resolved steps are discarded.
   - Output: `parallel_groups` (list of groups, each a list of canonical step names) and
     `parallel_set` (flat set of all parallel step names for O(1) lookup in Steps 3c/3d).
   - If no parallel language is detected, `parallel_groups = []` and `parallel_set = {}`.
```

- [ ] **Step 2: Run detection doc tests**

Run: `/usr/bin/python3 -m pytest tests/test_parallel_agent_accounting.py::TestDocumentContent::test_skill_md_step0_has_parallel_groups_output tests/test_parallel_agent_accounting.py::TestDocumentContent::test_skill_md_step0_has_detection_keywords -v`

Expected: Both PASS

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "feat: add parallel group detection to SKILL.md Step 0"
```

---

## Chunk 4: SKILL.md — Steps 3c/3d/3.5

### Task 4: Update Steps 3c, 3d, and 3.5 in SKILL.md

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: Update Step 3c to apply parallel input discount**

Find the existing Step 3c block:
```
**3c. Apply context accumulation (input only)**
```
K           = total activity count in this step
input_accum = input_complex × (K + 1) / 2
```
```

Replace it with:
```
**3c. Apply context accumulation (input only)**
```
K           = total activity count in this step
input_accum = input_complex × (K + 1) / 2

If this step is in parallel_set:
    input_accum = input_accum × parallel_input_discount
                  [parallel_input_discount from heuristics.md, default 0.75]
```
```

- [ ] **Step 2: Update Step 3d to apply parallel cache rate reduction**

In the Step 3d code block, find:
```
cache_rate ← from pricing.md for this band
```

Replace that line with:
```
cache_rate ← from pricing.md for this band
If this step is in parallel_set:
    cache_rate = max(cache_rate − parallel_cache_rate_reduction, parallel_cache_rate_floor)
                 [parallel_cache_rate_reduction = 0.15, parallel_cache_rate_floor = 0.05,
                  both from heuristics.md]
```

- [ ] **Step 3: Update Step 3.5 constituent steps description**

Find this text in Step 3.5:
```
**Constituent steps:** "Staff Review" and "Engineer Final Plan" — using the pre-calibration
Expected band costs computed at the END of Step 3d (band_mult=1.0, cache_rate=0.50,
complexity and context accumulation already applied). These are the raw step_cost values
before Step 3e calibration is applied. If a constituent step is not in the current plan's
scope, it contributes $0 to C.
```

Replace with:
```
**Constituent steps:** "Staff Review" and "Engineer Final Plan" — using the pre-calibration,
**un-discounted** Expected band costs: `step_cost` values before Step 3e calibration AND
before any parallel discount from Steps 3c/3d. The PR Review Loop cycles are sequential by
nature; C must not inherit the parallel discount even if constituent steps were modeled as
parallel in the main pipeline. Cache each step's pre-discount cost during the per-step loop
for use here. If a constituent step is not in scope, it contributes $0 to C.
```

- [ ] **Step 4: Run Steps 3c/3d/3.5 doc tests**

Run: `/usr/bin/python3 -m pytest tests/test_parallel_agent_accounting.py::TestDocumentContent::test_skill_md_step3c_references_parallel_discount tests/test_parallel_agent_accounting.py::TestDocumentContent::test_skill_md_step3d_references_parallel_cache_reduction tests/test_parallel_agent_accounting.py::TestDocumentContent::test_skill_md_step35_mentions_undiscounted -v`

Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add SKILL.md
git commit -m "feat: apply parallel discounts in SKILL.md Steps 3c/3d, isolate C in Step 3.5"
```

---

## Chunk 5: SKILL.md — Output, Schema, Limitations, Version

### Task 5: Update output template, active-estimate.json schema, Limitations, and version

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: Bump version in frontmatter**

Find: `version: 1.2.1`
Replace with: `version: 1.3.0`

- [ ] **Step 2: Update the output template version and table**

Find: `## costscope estimate (v1.2.1)`
Replace with: `## costscope estimate (v1.3.0)`

Then find the output table in the Output Template section:
```
| Step                  | Model       | Optimistic | Expected | Pessimistic |
|-----------------------|-------------|------------|----------|-------------|
| Research Agent        | Sonnet      | $X.XX      | $X.XX    | $X.XX       |
| ...                   | ...         | ...        | ...      | ...         |
| PR Review Loop        | Opus+Sonnet | $X.XX      | $X.XX    | $X.XX       |
| **TOTAL**             |             | **$X.XX**  | **$X.XX**| **$X.XX**   |
```

Replace with:
```
| Step                  | Model       | Optimistic | Expected | Pessimistic |
|-----------------------|-------------|------------|----------|-------------|
| ┌ Parallel Group 1 ∥  |             |            |          |             |
| │ Research Agent      | Sonnet      | $X.XX      | $X.XX    | $X.XX       |
| └ ...                 | ...         | ...        | ...      | ...         |
| [sequential steps]    | ...         | ...        | ...      | ...         |
| PR Review Loop        | Opus+Sonnet | $X.XX      | $X.XX    | $X.XX       |
| **TOTAL**             |             | **$X.XX**  | **$X.XX**| **$X.XX**   |
```

Add after the table (before the existing "Bands:" line):
```
**Parallel groups (when detected):** Group 1 (step names...) — modeled with 0.75× input accumulation, −0.15 cache rate
[Ambiguous/Unresolved notices, if any]
```

Group header rows (┌/└) have no cost values — structural display only. They do NOT appear in the `steps` array or count toward `step_count` in `active-estimate.json`. When no parallel groups are detected, the table renders with sequential rows only (the ┌│└ rows are omitted).

- [ ] **Step 3: Update the active-estimate.json schema in Step 4**

Find the JSON schema block in Step 4 that ends with:
```
  "review_cycles_estimated": <N from Step 2, or 0 if no PR Review Loop>,
  "review_cycles_actual": null
```

Add two new fields after `"review_cycles_actual": null` (before the closing `}`):
```json
  "parallel_groups": [["<step name>", ...], ...],
  "parallel_steps_detected": <count of steps in any parallel group>
```

Both default to `[]` and `0` when no parallel groups are detected.

- [ ] **Step 4: Update the Limitations section**

Find: `- Does not model parallel agent execution (treated as sequential).`
Replace with: `- Parallel agent modeling uses fixed discount factors; actual cache and context behavior varies by agent topology.`

- [ ] **Step 5: Run version and template doc tests**

Run: `/usr/bin/python3 -m pytest tests/test_parallel_agent_accounting.py::TestDocumentContent -v`

Expected: All 16 tests in `TestDocumentContent` PASS

- [ ] **Step 6: Commit**

```bash
git add SKILL.md
git commit -m "feat: update SKILL.md output template, schema, limitations, version to 1.3.0"
```

---

## Chunk 6: Update existing tests for version bump

### Task 6: Update test_pr_review_loop.py for version 1.3.0

The existing test file asserts version 1.2.1 in three places. These tests will fail after the version bump.

**Files:**
- Modify: `tests/test_pr_review_loop.py`

- [ ] **Step 1: Update version assertions in test_pr_review_loop.py**

Make these three changes:

1. Find: `assert "1.2.1" in result.stdout` (in `test_version_is_1_2_1`)
   Replace with: `assert "1.3.0" in result.stdout`
   Also rename the method: `def test_version_is_1_2_1` → `def test_version_is_1_3_0`

2. Find: `assert "version: 1.2.1" in content` (in `test_skill_md_version_1_2`)
   Replace with: `assert "version: 1.3.0" in content`
   Also rename: `def test_skill_md_version_1_2` → `def test_skill_md_version_1_3`

3. Find: `assert "v1.2.1" in content` (in `test_skill_md_output_template_v1_2`)
   Replace with: `assert "v1.3.0" in content`
   Also rename: `def test_skill_md_output_template_v1_2` → `def test_skill_md_output_template_v1_3`

- [ ] **Step 2: Run the updated existing test file**

Run: `/usr/bin/python3 -m pytest tests/test_pr_review_loop.py -v`

Expected: Most tests PASS. `test_version_is_1_3_0` will still FAIL at this point (learn.sh still says 1.2.1 until Chunk 7). That is expected — the full suite gate is Chunk 8. All other tests should pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pr_review_loop.py
git commit -m "test: update test_pr_review_loop.py version assertions to 1.3.0"
```

---

## Chunk 7: learn.sh

### Task 7: Update tokencast-learn.sh

**Files:**
- Modify: `scripts/tokencast-learn.sh`

The key challenge: `parallel_groups` is a JSON array containing strings with spaces and double quotes (e.g., `[["Research Agent"]]`). Passing this through shell variables via double-quoting will break. **Solution:** pass the estimate file path (`$ESTIMATE_FILE`) to the RECORD-building Python block so it can read `parallel_groups` directly from the file — avoiding shell variable escaping issues entirely.

- [ ] **Step 1: Bump VERSION**

Find: `VERSION="1.2.1"`
Replace with: `VERSION="1.3.0"`

- [ ] **Step 2: Add PARALLEL_STEPS_DETECTED to the eval fields dict**

In the Python block inside the `eval "$(EST_FILE=..."` block, find the `fields` dict and add `PARALLEL_STEPS_DETECTED` after `REVIEW_CYCLES`:

```python
    'REVIEW_CYCLES': d.get('review_cycles_estimated', 0),
    'PARALLEL_STEPS_DETECTED': d.get('parallel_steps_detected', 0),
```

This is an integer — safe to pass through `str()` + `shlex.quote()`.

- [ ] **Step 3: Pass ESTIMATE_FILE to the RECORD-building Python block**

Find the RECORD assignment that begins:
```bash
RECORD=$(TS_ENV="$TIMESTAMP" SZ_ENV="$SIZE" FL_ENV="$FILES" CX_ENV="$COMPLEXITY" \
  EC_ENV="$EXPECTED_COST" AC_ENV="$ACTUAL_COST" TC_ENV="$TURN_COUNT" \
  ST_ENV="$STEPS_JSON" PIP_ENV="$PIPELINE_SIGNATURE" \
  PT_ENV="$PROJECT_TYPE" LG_ENV="$LANGUAGE" SC_ENV="$STEP_COUNT" \
  RC_ENV="$REVIEW_CYCLES" \
  python3 -c "
```

Add two new env vars (on the `RC_ENV` line):
```bash
RECORD=$(TS_ENV="$TIMESTAMP" SZ_ENV="$SIZE" FL_ENV="$FILES" CX_ENV="$COMPLEXITY" \
  EC_ENV="$EXPECTED_COST" AC_ENV="$ACTUAL_COST" TC_ENV="$TURN_COUNT" \
  ST_ENV="$STEPS_JSON" PIP_ENV="$PIPELINE_SIGNATURE" \
  PT_ENV="$PROJECT_TYPE" LG_ENV="$LANGUAGE" SC_ENV="$STEP_COUNT" \
  RC_ENV="$REVIEW_CYCLES" PSD_ENV="$PARALLEL_STEPS_DETECTED" EST_FILE="$ESTIMATE_FILE" \
  python3 -c "
```

- [ ] **Step 4: Read parallel_groups from file in the RECORD Python block and add fields**

Inside the Python block, after `import json, os`, add a line to read `parallel_groups` directly from the estimate file:

```python
import json, os
_est = json.load(open(os.environ.get('EST_FILE', '/dev/null'))) if os.path.exists(os.environ.get('EST_FILE', '')) else {}
parallel_groups = _est.get('parallel_groups', [])
```

Then in the `json.dumps({...})` call, after `'review_cycles_actual': None,`, add:
```python
    'parallel_groups': parallel_groups,
    'parallel_steps_detected': int(os.environ['PSD_ENV']),
```

- [ ] **Step 5: Run learn.sh tests**

Run: `/usr/bin/python3 -m pytest tests/test_parallel_agent_accounting.py::TestLearnScript -v`

Expected: All 5 PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/tokencast-learn.sh
git commit -m "feat: forward parallel_groups and parallel_steps_detected in learn.sh, bump to 1.3.0"
```

---

## Chunk 8: Final Verification

### Task 8: Run full test suite and verify

- [ ] **Step 1: Run the complete test suite**

Run: `/usr/bin/python3 -m pytest tests/ -v`

Expected: **All tests in both files PASS. Zero failures.**

If any tests fail, read the output, identify the file to fix, fix it, re-run tests before continuing.

- [ ] **Step 2: Verify version string consistency across all three locations**

Run:
```bash
grep "version:" "/Volumes/Macintosh HD2/Cowork/Projects/costscope/SKILL.md" | head -3
grep "costscope estimate" "/Volumes/Macintosh HD2/Cowork/Projects/costscope/SKILL.md"
grep "VERSION=" "/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-learn.sh"
```

Expected: All three show `1.3.0`.

- [ ] **Step 3: Final commit if anything needed fixing in Step 1**

If all commits are already clean, skip this step. If fixes were made:
```bash
git add -p
git commit -m "fix: correct issues found in final verification"
```
