# Implementation Plan: US-1b.02 — Extract Pricing and Heuristic Data to Python Modules

*Engineer Agent — Initial Plan*
*Date: 2026-03-26*

---

## Overview

This story extracts all values from `references/pricing.md` and `references/heuristics.md` into two importable Python modules: `src/tokencast/pricing.py` and `src/tokencast/heuristics.py`. A third file, `tests/test_data_modules_drift.py`, validates that the Python constants match the markdown sources so drift cannot go undetected.

The target package is `src/tokencast/` — the directory already exists (it holds `__init__.py`). The `pyproject.toml` already declares `packages = ["src/tokencast"]`, so no build configuration changes are needed.

No runtime markdown parsing. No side effects on import. Values are plain Python literals (dicts, floats, ints, strings). The markdown files remain the human-editable source of truth; the Python modules are derived artifacts kept in sync by the drift test.

---

## Changes

### Change 1: `src/tokencast/pricing.py` (new file)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/pricing.py
Lines: new file (~90 lines)
Parallelism: independent
Effort: 1–2 hrs
```

**Description:** All data from `references/pricing.md`, transcribed as Python constants. No logic, no I/O, no imports beyond the standard library.

**Details:**

- Module-level docstring: "Pricing data for Anthropic Claude models. Derived from references/pricing.md — update both files together."

- `LAST_UPDATED: str = "2026-03-04"` — matches line 3 of pricing.md.

- `STALENESS_WARNING_DAYS: int = 90` — matches line 4 of pricing.md.

- `MODEL_PRICES: dict[str, dict[str, float]]` — keyed by canonical model ID strings as they appear in pricing.md. Each value is a dict with keys `"input"`, `"cache_read"`, `"cache_write"`, `"output"` (all per-million-token rates as floats).

  Three entries:
  - `"claude-sonnet-4-6"`: input=3.00, cache_read=0.30, cache_write=3.75, output=15.00
  - `"claude-opus-4-6"`: input=5.00, cache_read=0.50, cache_write=6.25, output=25.00
  - `"claude-haiku-4-5"`: input=1.00, cache_read=0.10, cache_write=1.25, output=5.00

- `STEP_MODEL_MAP: dict[str, str]` — maps pipeline step name (as used throughout the codebase and heuristics.md) to canonical model ID string. Uses the Pipeline Step → Model Mapping table from pricing.md. For "Implementation" the value is `"claude-sonnet-4-6"` with a comment noting Opus applies for L-size changes (handled by the engine, not this constant).

  Entries:
  - `"Research Agent"` → `"claude-sonnet-4-6"`
  - `"Architect Agent"` → `"claude-opus-4-6"`
  - `"Engineer Initial Plan"` → `"claude-sonnet-4-6"`
  - `"Staff Review"` → `"claude-opus-4-6"`
  - `"Engineer Final Plan"` → `"claude-sonnet-4-6"`
  - `"Test Writing"` → `"claude-sonnet-4-6"`
  - `"Implementation"` → `"claude-sonnet-4-6"`
  - `"QA"` → `"claude-haiku-4-5"`

- `CACHE_HIT_RATES: dict[str, float]` — keyed by band name (`"optimistic"`, `"expected"`, `"pessimistic"`), values are the decimal fractions (0.60, 0.50, 0.30) from pricing.md's Cache Hit Rate table.

- Convenience aliases for the three model IDs as module-level constants, to reduce string duplication and enable static analysis:
  - `MODEL_SONNET = "claude-sonnet-4-6"`
  - `MODEL_OPUS = "claude-opus-4-6"`
  - `MODEL_HAIKU = "claude-haiku-4-5"`

---

### Change 2: `src/tokencast/heuristics.py` (new file)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/heuristics.py
Lines: new file (~220 lines)
Parallelism: independent
Effort: 2–3 hrs
```

**Description:** All data from `references/heuristics.md`, transcribed as Python constants. Same rules as pricing.py — no logic, no I/O.

**Details:**

- Module-level docstring: "Heuristic parameters for tokencast estimation. Derived from references/heuristics.md — update both files together."

- `ACTIVITY_TOKENS: dict[str, dict[str, int]]` — the Activity Token Estimates table. Keyed by activity name (lowercase with underscores matching table rows), value is a dict with `"input"` and `"output"` keys. Comment notes that file_read and file_edit values here are the medium-bracket defaults; see FILE_SIZE_BRACKETS for bracket-specific values.

  Entries (input, output):
  - `"file_read"`: 10000, 200
  - `"file_write_new"`: 1500, 4000
  - `"file_edit"`: 2500, 1500
  - `"test_write"`: 2000, 5000
  - `"code_review_pass"`: 8000, 3000
  - `"research_exploration"`: 5000, 2000
  - `"planning_step"`: 3000, 4000
  - `"grep_search"`: 500, 500
  - `"shell_command"`: 300, 500
  - `"conversation_turn"`: 5000, 1500

- `PIPELINE_STEPS: dict[str, dict]` — the Pipeline Step Activity Counts table. Keyed by step name (matching STEP_MODEL_MAP keys). Each value is a dict with `"activities"` (list of `(activity_name, count)` tuples where activity_name matches ACTIVITY_TOKENS keys) and `"model"` (model ID string — redundant with pricing.py's STEP_MODEL_MAP but included for locality; comment says "see also pricing.STEP_MODEL_MAP").

  Entries derived directly from the heuristics.md table:
  - `"Research Agent"`: 6 file_read, 4 grep_search, 1 planning_step, 3 conversation_turn
  - `"Architect Agent"`: 1 code_review_pass, 1 planning_step, 2 conversation_turn
  - `"Engineer Initial Plan"`: 4 file_read, 2 grep_search, 1 planning_step, 2 conversation_turn
  - `"Staff Review"`: 1 code_review_pass, 2 conversation_turn
  - `"Engineer Final Plan"`: 2 file_read, 1 planning_step, 2 conversation_turn
  - `"Test Writing"`: 3 file_read, N test_write, 3 conversation_turn — N-scaling steps use a sentinel value (0 for file_read, -1 or a named constant `N_SCALING` for the N-scaled activity). A comment in the dict and a module-level constant `N_SCALING = -1` mark which activities scale with file count.
  - `"Implementation"`: N file_read, N file_edit, 4 conversation_turn — same N_SCALING sentinel.
  - `"QA"`: 3 shell_command, 2 file_read, 2 conversation_turn

  PR Review Loop is NOT an entry here; it is defined via its own parameters below (it is composite, not a raw activity-count step).

- `COMPLEXITY_MULTIPLIERS: dict[str, float]` — the Complexity Multipliers table. Keys: `"low"`, `"medium"`, `"high"`. Values: 0.7, 1.0, 1.5.

- `BAND_MULTIPLIERS: dict[str, float]` — the Confidence Band Multipliers table. Keys: `"optimistic"`, `"expected"`, `"pessimistic"`. Values: 0.6, 1.0, 3.0.

- `PR_REVIEW_LOOP: dict[str, float | int]` — all PR Review Loop defaults:
  - `"review_cycles_default"`: 2
  - `"review_decay_factor"`: 0.6
  - PR Review Loop band cycle counts as a nested dict under key `"band_cycles"`:
    - `"optimistic"`: 1 (always 1 cycle for optimistic)
    - `"expected"`: None (uses review_cycles input; None means "use the review_cycles parameter")
    - `"pessimistic"`: None (uses review_cycles * 2; None means "double the review_cycles parameter")
  - Comment explains: optimistic is always 1; expected uses review_cycles param; pessimistic doubles it.

- `PARALLEL_ACCOUNTING: dict[str, float]` — the Parallel Agent Accounting table:
  - `"parallel_input_discount"`: 0.75
  - `"parallel_cache_rate_reduction"`: 0.15
  - `"parallel_cache_rate_floor"`: 0.05

- `PER_STEP_CALIBRATION: dict[str, int]` — single entry:
  - `"per_step_min_samples"`: 3

- `FILE_SIZE_BRACKETS: dict` — the File Size Brackets section:
  - `"small_max_lines"`: 49 (boundary: lines ≤ 49 → small)
  - `"large_min_lines"`: 501 (boundary: lines ≥ 501 → large)
  - `"measurement_cap"`: 30
  - `"brackets"`: nested dict with keys `"small"`, `"medium"`, `"large"`, each containing `"file_read_input"`, `"file_edit_input"`:
    - small: read=3000, edit=1000
    - medium: read=10000, edit=2500
    - large: read=20000, edit=5000
  - `"file_read_output"`: 200 (unchanged across brackets)
  - `"file_edit_output"`: 1500 (unchanged across brackets)
  - `"binary_extensions"`: frozenset of excluded extensions as listed in heuristics.md: `{".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".wasm", ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a", ".class"}`
  - `"fixed_count_steps"`: dict mapping step names that use fixed (non-N) file reads to their read count: `{"Research Agent": 6, "Engineer Initial Plan": 4, "Engineer Final Plan": 2, "QA": 2}` — matches heuristics.md "Step classification by file-read scaling" section.

- `TIME_DECAY: dict[str, int]` — single entry:
  - `"decay_halflife_days"`: 30
  - Comment: "DECAY_MIN_RECORDS=5 is a statistical invariant hardcoded in update-factors.py — intentionally not here. See CLAUDE.md architecture conventions."

- `PER_SIGNATURE_CALIBRATION: dict[str, int]` — single entry:
  - `"per_signature_min_samples"`: 3

- `MID_SESSION_TRACKING: dict[str, float | int]` — the Mid-Session Cost Tracking table:
  - `"midcheck_warn_threshold"`: 0.80
  - `"midcheck_sampling_bytes"`: 50000
  - `"midcheck_cooldown_bytes"`: 200000

---

### Change 3: `tests/test_data_modules_drift.py` (new file)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_data_modules_drift.py
Lines: new file (~180 lines)
Parallelism: depends-on: Change 1, Change 2
Effort: 1–2 hrs
```

**Description:** Drift detection tests that parse `references/pricing.md` and `references/heuristics.md` at test time and assert that every value in the Python modules exactly matches the markdown source. These tests are the single safety net preventing silent divergence when the markdown is updated without updating the Python modules.

**Details:**

- One test class `TestPricingDrift` and one `TestHeuristicsDrift`.

- Import `pricing` and `heuristics` from `src/tokencast/` using a `sys.path` insert (same pattern used by existing tests that import from `scripts/`). Path to insert: `str(Path(__file__).parent.parent / "src")`.

- Markdown paths resolved relative to `Path(__file__).parent.parent / "references"` — absolute, not relative.

- `TestPricingDrift` methods:
  - `test_last_updated` — parse `last_updated:` line from pricing.md line 3, assert equals `pricing.LAST_UPDATED`.
  - `test_staleness_warning_days` — parse `staleness_warning_days:` line from pricing.md line 4, assert equals `pricing.STALENESS_WARNING_DAYS`.
  - `test_model_prices` — parse each model block (lines matching `### claude-*`, then bullet lines `- input:`, `- cache_read:`, `- cache_write:`, `- output:`). For each model assert `pricing.MODEL_PRICES[model_id]` matches parsed values. Use `assertAlmostEqual` with 4 decimal places to avoid float formatting issues.
  - `test_step_model_map` — parse the Pipeline Step → Model Mapping table from pricing.md. Map "Sonnet" → `pricing.MODEL_SONNET`, "Opus" → `pricing.MODEL_OPUS`, "Haiku" → `pricing.MODEL_HAIKU`. Assert each row matches `pricing.STEP_MODEL_MAP`. For "Implementation (Opus for L-size changes)" rows, strip the parenthetical before mapping.
  - `test_cache_hit_rates` — parse the Cache Hit Rate table, assert each band rate matches `pricing.CACHE_HIT_RATES`.

- `TestHeuristicsDrift` methods:
  - `test_activity_tokens` — parse the Activity Token Estimates table from heuristics.md. Assert each row matches the corresponding entry in `heuristics.ACTIVITY_TOKENS`. Tolerate commas in numbers (10,000 → 10000).
  - `test_complexity_multipliers` — parse Complexity Multipliers table. Assert matches `heuristics.COMPLEXITY_MULTIPLIERS`.
  - `test_band_multipliers` — parse Confidence Band Multipliers table. Assert matches `heuristics.BAND_MULTIPLIERS`.
  - `test_pr_review_loop_defaults` — parse the PR Review Loop Defaults table (`review_cycles_default`, `review_decay_factor`). Assert matches `heuristics.PR_REVIEW_LOOP["review_cycles_default"]` and `heuristics.PR_REVIEW_LOOP["review_decay_factor"]`.
  - `test_parallel_accounting` — parse Parallel Agent Accounting table. Assert matches `heuristics.PARALLEL_ACCOUNTING`.
  - `test_per_step_calibration` — parse Per-Step Calibration table (`per_step_min_samples`). Assert matches `heuristics.PER_STEP_CALIBRATION["per_step_min_samples"]`.
  - `test_file_size_brackets_boundaries` — parse boundary values from heuristics.md (`file_size_small_max`, `file_size_large_min`, `file_measurement_cap`). Assert matches `heuristics.FILE_SIZE_BRACKETS` keys.
  - `test_file_size_bracket_token_values` — parse the bracket table (Small/Medium/Large rows, File Read Input and File Edit Input columns). Assert matches `heuristics.FILE_SIZE_BRACKETS["brackets"]`.
  - `test_time_decay` — parse Time-Based Decay table (`decay_halflife_days`). Assert matches `heuristics.TIME_DECAY["decay_halflife_days"]`.
  - `test_per_signature_calibration` — parse Per-Signature Calibration table (`per_signature_min_samples`). Assert matches `heuristics.PER_SIGNATURE_CALIBRATION["per_signature_min_samples"]`.
  - `test_mid_session_tracking` — parse Mid-Session Cost Tracking table. Assert matches `heuristics.MID_SESSION_TRACKING`.

- Each parsing helper is a private method on the test class (not a module-level function) so tests are self-contained. Use `re.search` on individual lines rather than full-table regex to keep parsers simple and readable.

- No `unittest.skip` — all tests must actively assert. A parse failure (markdown changed to unexpected format) raises a clear `AssertionError` with a message identifying which value drifted.

- Run under `/usr/bin/python3 -m pytest tests/test_data_modules_drift.py` as a standalone file as well as part of the full test suite.

---

### Change 4: `src/tokencast/__init__.py` (minor update)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/__init__.py
Lines: 1–3 (current), append ~2 lines
Parallelism: depends-on: Change 1, Change 2
Effort: 15 min
```

**Description:** Expose `pricing` and `heuristics` as submodule re-exports from the package so downstream code can do `from tokencast import pricing` or `import tokencast.pricing`.

**Details:**

- Current content (3 lines): docstring + `__version__ = "0.1.0"`.
- Add two import lines after the existing content:
  ```python
  from tokencast import pricing  # noqa: F401
  from tokencast import heuristics  # noqa: F401
  ```
- Do NOT flatten the constants into the top-level namespace — keep them in their submodules. The `__init__.py` imports just ensure the modules are accessible via `import tokencast; tokencast.pricing.MODEL_PRICES` without a separate `import tokencast.pricing` step.
- No `__version__` change — that is scope for US-1b.08.

---

## Dependency Order

```
Change 1 (pricing.py)      ──┐
                              ├──► Change 3 (drift tests)
Change 2 (heuristics.py)   ──┤
                              └──► Change 4 (__init__.py)
```

**Parallel group A (independent, run concurrently):**
- Change 1: `pricing.py`
- Change 2: `heuristics.py`

**Sequential group B (after group A):**
- Change 3: `test_data_modules_drift.py`
- Change 4: `__init__.py` (trivial, can run alongside Change 3)

Changes 3 and 4 have no dependency on each other and can also run concurrently once group A is done.

---

## Test Strategy

### New tests (Change 3)

File: `tests/test_data_modules_drift.py`

**Happy path:**
- All values in `pricing.py` and `heuristics.py` match their markdown sources — all assertions pass.

**Drift detection (the purpose of these tests):**
- If a developer updates `references/pricing.md` (e.g., price change) without updating `pricing.py`, `test_model_prices` fails immediately with an assertion message identifying which model and field drifted.
- If a developer updates `references/heuristics.md` without updating `heuristics.py`, the relevant `TestHeuristicsDrift` method fails.

**Edge cases:**
- Numbers with commas in markdown (e.g., "10,000") — strip commas before `int()` conversion.
- Percentage values in markdown (e.g., "60%") — strip `%` and divide by 100 before float comparison.
- Multiplier strings in markdown (e.g., "0.7x") — strip trailing `x` before float conversion.
- `frozenset` for `binary_extensions` — drift test for this value checks set equality, not ordering.

**Error cases:**
- If markdown files are missing (e.g., running tests from a checkout that somehow lacks `references/`), tests fail with a clear `FileNotFoundError` from `Path.read_text()` — no silent skip.

### Existing tests

No existing tests should break. The new modules (`pricing.py`, `heuristics.py`) are additive — they don't modify any existing module. The `__init__.py` change only adds imports; it does not change `__version__` or any existing export.

Verify by running the full suite after implementing: `/usr/bin/python3 -m pytest tests/` — expect all 441 existing tests to continue passing.

### Tests that can be written in parallel with implementation

Change 3 can be written in parallel with Changes 1 and 2 if the implementer of the drift tests works from the markdown files directly (the test structure — which tables to parse, which constants to check — is fully derivable from the markdown without the Python modules existing yet). The test implementations will fail until group A is complete, which is expected (tests-first discipline).

---

## Rollback Notes

All changes in this story are additive:
- `pricing.py` and `heuristics.py` are new files — delete them to roll back.
- `test_data_modules_drift.py` is a new file — delete it to roll back.
- `__init__.py` change is two lines — revert to the original 3-line file.

No schema changes, no calibration data changes, no SKILL.md changes. Rollback has zero risk of corrupting calibration or breaking existing functionality.

If a drift test is failing after a legitimate markdown update, the fix is always: update the Python module constant to match the new markdown value. The drift test itself should not be modified to suppress a finding.

---

## File Summary

| File | Status | Lines (est.) | Parallelism |
|------|--------|--------------|-------------|
| `src/tokencast/pricing.py` | new | ~90 | independent |
| `src/tokencast/heuristics.py` | new | ~220 | independent |
| `tests/test_data_modules_drift.py` | new | ~180 | depends on pricing.py + heuristics.py |
| `src/tokencast/__init__.py` | modify | +2 lines | depends on pricing.py + heuristics.py |

Total new code: ~490 lines.
