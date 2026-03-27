# Implementation Plan: US-1b.01 — Extract Estimation Algorithm to Python Engine

**Date:** 2026-03-26
**Story:** US-1b.01 — Extract Estimation Algorithm to Python Engine
**Size:** XL

---

## Overview

The SKILL.md estimation algorithm (Steps 0–4) must become callable Python code in
`src/tokencast/estimation_engine.py`. The MCP tool layer (`src/tokencast_mcp/tools/estimate_cost.py`)
and the public API stub (`src/tokencast/api.py`) already define the interfaces; this plan fills
in the real implementations.

The engine reads all pricing and heuristic constants from the already-implemented Python modules
(`pricing.py`, `heuristics.py`), reads calibration factors at runtime via
`calibration_store.read_factors()`, and returns a structured dict that the MCP tool formats for
the client.

No markdown parsing at runtime. No new data structures for pricing or heuristics. The engine is
a collection of pure helper functions plus one top-level entry point: `compute_estimate(params) -> dict`.

A companion file measurement utility (`src/tokencast/file_measurement.py`) handles the `wc -l`
subprocess calls, bracket assignment, and weighted-average computation. It is kept separate so
the MCP tool can call it independently (Step 0 file measurement is a distinct pre-processing step).

The public API function `estimate_cost(params)` in `api.py` is updated to call `compute_estimate`
and return the real result.

---

## Key Observations from Reading Source Files

1. **Examples.md uses stale pricing** ($15/$1.50/$75 for Opus). `pricing.py` has current pricing
   ($5/$0.50/$25 for Opus). Tests must use current `pricing.py` values for expected outputs, NOT
   the numbers in examples.md. The examples.md worked arithmetic is still valuable for verifying
   formula structure; numbers differ.

2. **Test Writing is a hybrid step.** Three fixed file reads (weighted-average bracket) plus
   N-scaling test_write activities. The `test_write` activity in `ACTIVITY_TOKENS` has
   `input=2000, output=5000`. For fixed-read count, the engine uses `avg_file_read_tokens` from
   bracket weighting; for the N-scaling test_write portion it uses the `test_write` input token
   value (2000) scaled by N.

3. **K (activity count) for `cache_write_fraction = 1/K`.** K is the total count of activity
   instances, not the number of distinct activity types. For Research Agent: 6+4+1+3=14. For
   N-scaling steps, N replaces the sentinel -1. E.g. Implementation with N=5 has K=5+5+4=14.

4. **The PR Review Loop's C uses un-discounted, pre-calibration Expected costs.** These must be
   cached during the per-step loop before any parallel discount or calibration factor is applied.
   The SKILL.md text is unambiguous: "pre-calibration, un-discounted Expected band costs."

5. **The 5-level calibration chain.** SKILL.md Step 3e describes per-step, per-signature,
   size-class, global, and uncalibrated (1.0). The global factor check reads
   `factors["status"] == "active"` — this is a top-level `status` key in `factors.json`, not
   nested under `factors["global"]`. The size-class check reads `factors[size]` (e.g. `"M"`) as a
   plain float plus `factors["{size}_n"]` (e.g. `"M_n"`) for the sample count.

6. **L-size Implementation uses Opus.** The `STEP_MODEL_MAP` comment in `pricing.py` notes that
   "Opus for L-size changes — resolved by engine." The engine must override the model for
   `Implementation` to `MODEL_OPUS` when `size == "L"`.

7. **`active-estimate.json` writes.** Per the story AC, `estimate_cost` writes `active-estimate.json`
   and `last-estimate.md`. These writes require a `calibration_dir` path, which comes from the
   `ServerConfig` passed through the MCP tool handler. The engine itself should not write files;
   writing is the responsibility of the API layer (`api.py` or the MCP tool handler) so the engine
   stays pure-computation and testable without filesystem.

8. **`file_brackets` null vs. zero-count.** When no `file_paths` are provided and no
   `avg_file_lines` override is given, `file_brackets = None`. When paths were provided but none
   were measurable, `file_brackets = {"small": 0, "medium": 0, "large": 0}`.

9. **`pipeline_signature` is not stored in `active-estimate.json`.** Compute it inline in the
   engine from the `steps` list for use in Step 3e, but do not include it in the output dict.
   `learn.sh` recomputes it independently.

10. **The two-term formula in examples.md is outdated.** Use the three-term formula from SKILL.md
    Step 3d: `input_accum × (1 − cache_rate) × price_in + input_accum × cache_rate × (1/K) × price_cw + input_accum × cache_rate × (1 − 1/K) × price_cr`.

---

## Changes

### Change 1: New file — `src/tokencast/file_measurement.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/file_measurement.py
Lines: new file
Parallelism: independent
Description:
  File measurement utility. Handles wc -l subprocess calls, bracket assignment,
  and weighted-average token computation. Separated from estimation_engine.py so
  MCP tool callers can invoke it independently without running a full estimate.
```

**Functions to implement:**

`assign_bracket(line_count: int) -> str`
- Maps a line count to "small", "medium", or "large" using `FILE_SIZE_BRACKETS` constants.
- `line_count <= small_max_lines` → "small"; `>= large_min_lines` → "large"; else "medium".

`measure_files(file_paths: list[str], project_dir: str | None = None) -> dict`
- Takes a list of file path strings and an optional project directory for resolving relative paths.
- Filters out binary extensions from `FILE_SIZE_BRACKETS["binary_extensions"]`.
- Caps at `FILE_SIZE_BRACKETS["measurement_cap"]` (30) files. Files beyond the cap receive the
  weighted-average bracket of the first 30 measured.
- Builds the `wc -l` command: `wc -l -- "path1" "path2" ...` with each path individually
  double-quoted in the shell command string. Uses `subprocess.run(..., capture_output=True)`.
  Uses `shlex.quote()` for each path to handle spaces (e.g. `/Volumes/Macintosh HD2/...`).
- Parses `wc -l` output: skip the "total" line; extract `(line_count, path)` pairs from each line.
- Returns a dict: `{"brackets": {"small": N, "medium": N, "large": N}, "files_measured": N,
  "avg_file_read_tokens": float, "avg_file_edit_tokens": float}`.
- On subprocess failure or empty output: returns `{"brackets": None, "files_measured": 0,
  "avg_file_read_tokens": 10000, "avg_file_edit_tokens": 2500}`.

`bracket_from_override(avg_file_lines: int) -> str`
- Maps `avg_file_lines` integer override to bracket name per heuristics.md boundaries.
- Returns "small", "medium", or "large".

`compute_bracket_tokens_from_override(avg_file_lines: int) -> dict`
- Returns `{"file_read_input": N, "file_edit_input": N}` for the bracket that `avg_file_lines`
  maps to.
- Used for the fallback case when no `file_paths` are provided but `avg_file_lines` is given.

`compute_avg_tokens(brackets: dict) -> tuple[float, float]`
- Given `brackets = {"small": N, "medium": N, "large": N}`, returns
  `(avg_file_read_tokens, avg_file_edit_tokens)`.
- Formula: `avg_read = (small×3000 + medium×10000 + large×20000) / total_measured`.
- Zero-divide guard: if `total_measured == 0`, return `(10000, 2500)`.

---

### Change 2: New file — `src/tokencast/estimation_engine.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/estimation_engine.py
Lines: new file
Parallelism: depends-on: Change 1 (imports file_measurement)
Description:
  Core estimation algorithm implementing SKILL.md Steps 1–4 as Python functions.
  Pure computation — no file I/O, no subprocess calls (file measurement is done
  by the caller and passed in via params). Returns a structured dict.
```

**Module-level imports:**
```python
from tokencast import pricing, heuristics
from tokencast.file_measurement import compute_avg_tokens
from scripts.calibration_store import read_factors  # via sys.path or package install
```

Note: `calibration_store.py` lives in `scripts/` (not `src/`). Import via a resolved path
using `importlib.util` (same pattern as `tokencast-status.py` tests) OR by adding `scripts/`
to `sys.path` inside the function call. The preferred approach is a thin import shim:

```python
import importlib.util, pathlib
_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_cs_spec = importlib.util.spec_from_file_location(
    "calibration_store", _SCRIPTS_DIR / "calibration_store.py"
)
_cs_mod = importlib.util.module_from_spec(_cs_spec)
_cs_spec.loader.exec_module(_cs_mod)
read_factors = _cs_mod.read_factors
```

This is how `tokencast-status.py` tests import the status module — same pattern, no `sys.path`
mutation in production code.

**Top-level entry point:**

`compute_estimate(params: dict, calibration_dir: str | None = None) -> dict`

Signature:
```python
def compute_estimate(params: dict, calibration_dir: str | None = None) -> dict:
```

`params` keys (mirror the MCP tool schema):
- Required: `size` (str), `files` (int), `complexity` (str)
- Optional: `steps` (list[str]), `project_type` (str, default "greenfield"),
  `language` (str, default "unknown"), `review_cycles` (int | None),
  `avg_file_lines` (int | None), `parallel_groups` (list[list[str]], default []),
  `file_paths` (list[str], default []),
  `file_brackets` (dict | None) — pre-computed brackets (when caller already ran `measure_files`),
  `avg_file_read_tokens` (float | None), `avg_file_edit_tokens` (float | None)

`calibration_dir`: path to the `calibration/` directory. If None, calibration factors default to
`{}` (no calibration, factor = 1.0 for all steps).

Returns the output dict described in the Output Dict Structure section below.

**Helper functions (all module-private, prefixed `_`):**

`_resolve_steps(size: str, steps_override: list[str] | None) -> list[str]`
- If `steps_override` is provided and non-empty, return those step names (order preserved).
- Otherwise return all keys from `heuristics.PIPELINE_STEPS` in their defined order.
- Steps not in `heuristics.PIPELINE_STEPS` are silently dropped (no error — forward compat).

`_resolve_model(step_name: str, size: str) -> str`
- Returns model ID from `pricing.STEP_MODEL_MAP[step_name]`.
- Special case: if `step_name == "Implementation"` and `size == "L"`, return
  `pricing.MODEL_OPUS` (L-size override per pricing.py comment).
- Returns `pricing.MODEL_SONNET` as fallback for unknown step names.

`_resolve_review_cycles(params: dict, steps: list[str]) -> int`
- If `params["review_cycles"]` is explicitly set (not None), use that value.
- Otherwise apply SKILL.md Step 0 item 7 inference: N = `PR_REVIEW_LOOP["review_cycles_default"]`
  if both a "Staff Review"-type step and an "Engineer Final Plan"-type step (or Implementation,
  or Test Writing) are in `steps`; else N = 0.
- "Staff Review"-type: step name contains "review" (case-insensitive).
- "Final Plan"-type: step name contains "final" or "implement" or "test" (case-insensitive).
- This inference is deliberately simple — the MCP client is expected to pass explicit
  `review_cycles` for non-default workflows.

`_compute_file_tokens(step_name: str, N: int, file_brackets: dict | None,
                      avg_file_read_tokens: float, avg_file_edit_tokens: float) -> tuple[float, float]`
- Returns `(file_read_contribution, file_edit_contribution)` for the file-related activities
  in this step.
- For N-scaling steps (Implementation, Test Writing's test_write):
  - Implementation: `file_read = sum(bracket_count × bracket_read_input)`,
    `file_edit = sum(bracket_count × bracket_edit_input)`. When `file_brackets` is None,
    fall back to `N × 10000` (read) and `N × 2500` (edit).
  - Test Writing's test_write (N_SCALING): uses `ACTIVITY_TOKENS["test_write"]["input"] × N`.
    This is NOT bracket-dependent — test_write input is a fixed 2000 tokens regardless of file size.
- For fixed-count steps in `FILE_SIZE_BRACKETS["fixed_count_steps"]` (Research Agent: 6,
  Engineer Initial Plan: 4, Engineer Final Plan: 2, QA: 2):
  - `file_read_contribution = avg_file_read_tokens × fixed_count`
  - `file_edit_contribution = 0` (fixed-count steps have no edits)
- For Test Writing's 3 fixed reads: `file_read_contribution = avg_file_read_tokens × 3`
- Returns `(0.0, 0.0)` for steps with no file activities (Architect Agent, Staff Review).

`_compute_step_base_tokens(step_name: str, N: int, file_brackets: dict | None,
                           avg_file_read_tokens: float, avg_file_edit_tokens: float) -> tuple[float, float, int]`
- Returns `(input_base, output_base, K)` for one step.
- Iterates `heuristics.PIPELINE_STEPS[step_name]["activities"]`.
- For each `(activity, count)`:
  - If `count == N_SCALING`: resolve count = N.
  - If activity is `"file_read"`: use `_compute_file_tokens` result for input (bracket-aware).
    Output is `FILE_SIZE_BRACKETS["file_read_output"] × resolved_count`.
  - If activity is `"file_edit"`: use `_compute_file_tokens` result for edit input (bracket-aware).
    Output is `FILE_SIZE_BRACKETS["file_edit_output"] × resolved_count`.
  - Otherwise: use `ACTIVITY_TOKENS[activity]["input"] × resolved_count` and `["output"] × resolved_count`.
- K = sum of all resolved activity counts.
- Note: for Test Writing, the `file_read` entries (count=3, fixed) use `avg_file_read_tokens × 3`
  while `test_write` (count=N_SCALING) uses `ACTIVITY_TOKENS["test_write"]["input"] × N`.
  The `_compute_file_tokens` helper must handle this split correctly by checking whether the
  calling step's activity is `"file_read"` (bracket-dependent) vs. `"test_write"` (fixed token value).

`_compute_step_cost(input_base: float, output_base: float, K: int,
                    complexity: str, model_id: str, is_parallel: bool) -> dict`
- Returns `{"optimistic": float, "expected": float, "pessimistic": float,
             "expected_pre_discount": float}`.
- `expected_pre_discount` is the Expected band cost BEFORE parallel discount — used by the
  PR Review Loop computation. Even if `is_parallel=False`, this equals `expected`.
- Steps:
  1. Apply complexity: `input_complex = input_base × COMPLEXITY_MULTIPLIERS[complexity]`
  2. K accumulation: `input_accum = input_complex × (K + 1) / 2`
  3. Parallel discount on `input_accum` (Step 3c): if `is_parallel`,
     `input_accum_discounted = input_accum × PARALLEL_ACCOUNTING["parallel_input_discount"]`.
     Record `input_accum_pre_discount = input_accum` (for `expected_pre_discount` computation).
  4. For each band ("optimistic", "expected", "pessimistic"):
     - Get `cache_rate = CACHE_HIT_RATES[band]`
     - If `is_parallel`: `cache_rate = max(cache_rate - PARALLEL_ACCOUNTING["parallel_cache_rate_reduction"], PARALLEL_ACCOUNTING["parallel_cache_rate_floor"])`
     - `cache_write_fraction = 1.0 / K` (if K == 0, use 1.0 as guard — shouldn't occur in practice)
     - Three-term input cost: `(input_accum_to_use × (1 - cache_rate) × price_in + input_accum_to_use × cache_rate × cache_write_fraction × price_cw + input_accum_to_use × cache_rate × (1 - cache_write_fraction) × price_cr) / 1_000_000`
       where `input_accum_to_use` is the discounted value for parallel steps, undiscounted for `expected_pre_discount`.
     - `output_cost = output_complex × price_out / 1_000_000`
     - `band_mult = BAND_MULTIPLIERS[band]`
     - `step_cost = (input_cost + output_cost) × band_mult`
  5. `expected_pre_discount`: recompute using `input_accum_pre_discount` (before parallel discount),
     same cache_rate as the expected band but WITHOUT parallel cache_rate reduction.

`_resolve_calibration_factor(step_name: str, size: str, pipeline_signature: str,
                              factors: dict) -> tuple[float, str]`
- Returns `(factor, source_label)` where `source_label` is "S", "P", "Z", "G", or "--".
- Implements the 5-level precedence chain from SKILL.md Step 3e:

  **Level 1 — Per-step:**
  ```
  step_factors = factors.get("step_factors", {})
  entry = step_factors.get(step_name)
  if entry and entry.get("status") == "active":
      return (entry["factor"], f"S:{entry['factor']:.2f}")
  ```

  **Level 2 — Per-signature:**
  ```
  sig_factors = factors.get("signature_factors", {})
  sig_entry = sig_factors.get(pipeline_signature)
  if sig_entry and sig_entry.get("status") == "active":
      return (sig_entry["factor"], f"P:{sig_entry['factor']:.2f}")
  ```

  **Level 3 — Size-class:**
  ```
  sz_factor = factors.get(size)           # e.g. factors["M"]
  sz_n      = factors.get(f"{size}_n", 0) # e.g. factors["M_n"]
  min_samples = heuristics.PER_STEP_CALIBRATION["per_step_min_samples"]  # 3
  if sz_factor is not None and sz_n >= min_samples:
      return (sz_factor, f"Z:{sz_factor:.2f}")
  ```

  **Level 4 — Global:**
  ```
  g_factor = factors.get("global")
  g_status = factors.get("status")  # top-level "status" key
  if g_factor is not None and g_status == "active":
      return (g_factor, f"G:{g_factor:.2f}")
  ```

  **Level 5 — No calibration:**
  ```
  return (1.0, "--")
  ```

- Note: per CLAUDE.md conventions, `factors["global"]` is a plain float (not `{"factor": ...}`),
  and `factors["status"]` is a top-level key. Size-class uses flat `"M": float` and `"M_n": int`.

`_apply_calibration(costs: dict, factor: float) -> dict`
- Given `{"optimistic": X, "expected": Y, "pessimistic": Z}` and a calibration factor:
  ```
  calibrated_expected    = Y × factor
  calibrated_optimistic  = calibrated_expected × 0.6
  calibrated_pessimistic = calibrated_expected × 3.0
  ```
- Returns the calibrated dict. Source labels are tracked separately.
- Note: this is the STANDARD calibration (non-PR-Review-Loop). For PR Review Loop see below.

`_compute_pr_review_loop(staff_review_expected_pre: float, engineer_final_expected_pre: float,
                          review_cycles: int, factors: dict, size: str) -> dict`
- Returns `{"optimistic": float, "expected": float, "pessimistic": float, "cal_label": str}`
  or `None` if `review_cycles == 0`.
- `C = staff_review_expected_pre + engineer_final_expected_pre`
- Cycle counts: optimistic=1, expected=review_cycles, pessimistic=review_cycles×2.
- Formula for each band: `C × (1 - decay^cycles) / (1 - decay)` where `decay = PR_REVIEW_LOOP["review_decay_factor"]` (0.6).
- **PR Review Loop calibration** (per SKILL.md Step 3.5 and CLAUDE.md): apply factor INDEPENDENTLY
  to each band — NOT via `_apply_calibration()`. Each band's raw value is multiplied by the factor.
  - Factor lookup: same 5-level chain as `_resolve_calibration_factor` but for a synthetic step name
    "PR Review Loop". In practice this step has no per-step or per-signature factor, so it always
    falls through to size-class, global, or "--". The SKILL.md output template shows "--" for the
    PR Review Loop Cal column. Use "--" unless a real calibration is found.
  - Returns cal_label = "--" per SKILL.md.

`_compute_pipeline_signature(steps: list[str]) -> str`
- Formula: `'+'.join(sorted(s.lower().replace(' ', '_') for s in steps))`
- Matches `learn.sh` line 38 exactly.

**`compute_estimate` function body outline:**

```
1. Resolve steps list (_resolve_steps)
2. Resolve review_cycles (_resolve_review_cycles)
3. Load calibration factors via read_factors(factors_path) or {} if calibration_dir is None
4. Compute pipeline_signature (_compute_pipeline_signature)
5. Resolve parallel_set: flat set of step names across all parallel_groups
6. Resolve file bracket state:
   - If file_brackets provided in params: use directly, compute avg tokens
   - Elif avg_file_lines provided: use bracket_from_override to get bracket name,
     set file_brackets = {that_bracket: files, others: 0},
     compute avg tokens from bracket token values
   - Else: file_brackets = None, avg_file_read_tokens = 10000, avg_file_edit_tokens = 2500
7. Per-step loop (cache pre-discount Expected for PR Review Loop):
   For each step_name in steps:
     a. Compute (input_base, output_base, K) via _compute_step_base_tokens
     b. Resolve model via _resolve_model
     c. Determine is_parallel (step_name in parallel_set)
     d. Compute step costs via _compute_step_cost
     e. Cache expected_pre_discount for "Staff Review" and "Engineer Final Plan"
     f. Resolve calibration factor via _resolve_calibration_factor
     g. Apply calibration via _apply_calibration to get final calibrated costs
     h. Append per-step result to output list
8. Compute PR Review Loop (post-step-loop) via _compute_pr_review_loop
9. Sum all bands across steps + PR Review Loop
10. Compute pricing staleness (compare LAST_UPDATED to today's date)
11. Build and return output dict
```

---

### Change 3: Update `src/tokencast/api.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/api.py
Lines: 9-20 (estimate_cost stub body)
Parallelism: depends-on: Change 2
Description:
  Replace the stub body of estimate_cost() with a real call to compute_estimate().
  The other three stubs (get_calibration_status, get_cost_history, report_session)
  remain as stubs — they are implemented in later stories (US-1b.05–1b.07).
```

Updated `estimate_cost` body:
```python
def estimate_cost(params: dict, calibration_dir: str | None = None) -> dict:
    from tokencast.estimation_engine import compute_estimate
    return compute_estimate(params, calibration_dir=calibration_dir)
```

The `calibration_dir` parameter is added to the public signature to pass through from the
MCP tool handler. The existing signature `estimate_cost(params: dict)` gains the optional
`calibration_dir` parameter — backward compatible because it has a default of `None`.

---

### Change 4: Update `src/tokencast_mcp/tools/estimate_cost.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/tools/estimate_cost.py
Lines: 40-110 (handle_estimate_cost stub body)
Parallelism: depends-on: Change 3
Description:
  Replace the stub handler body with a real delegation to estimate_cost() + post-processing:
  - Pass config.calibration_dir to the API call
  - Remove _stub: True from the return (or gate it on result having _stub key)
  - Write active-estimate.json and last-estimate.md after a successful estimate
  - Generate the markdown table string (text field) for LLM clients
  - Handle the file measurement step when file_paths is present
```

**Changes to `handle_estimate_cost`:**

1. **File measurement pre-processing:** If `params.get("file_paths")` is non-empty, call
   `measure_files(params["file_paths"], project_dir=str(config.project_dir) if config.project_dir else None)`
   from `tokencast.file_measurement`. Inject the results back into params:
   `params["file_brackets"] = result["brackets"]`, `params["avg_file_read_tokens"] = result["avg_file_read_tokens"]`,
   etc. This way the engine receives pre-measured data.

2. **Delegate to API:** `result = _api_estimate_cost(params, calibration_dir=str(config.calibration_dir))`

3. **Write active-estimate.json:** Build the `active_estimate` dict from the result (see
   "active-estimate.json structure" below) and write it atomically to `config.active_estimate_path`.

4. **Write last-estimate.md:** Format the markdown summary and write to `config.last_estimate_path`.

5. **Build text summary:** Generate the markdown table string matching SKILL.md's Output Template.
   Include box-drawing characters for parallel groups. Add this as `result["text"]`.

6. **Remove `_stub` key** from the returned dict if it was set.

**active-estimate.json structure** (written by the tool handler):
```json
{
  "timestamp": "<ISO 8601 now>",
  "size": "<size>",
  "files": N,
  "complexity": "<complexity>",
  "steps": ["<step names>"],
  "step_count": N,
  "project_type": "<project_type>",
  "language": "<language>",
  "expected_cost": float,
  "optimistic_cost": float,
  "pessimistic_cost": float,
  "baseline_cost": 0,
  "review_cycles_estimated": N,
  "review_cycles_actual": null,
  "parallel_groups": [[...]],
  "parallel_steps_detected": N,
  "file_brackets": {"small": 0, "medium": N, "large": 0} | null,
  "files_measured": N,
  "step_costs": {"<step name>": float, ...}
}
```

`baseline_cost` is 0 for MCP invocations (the MCP server has no access to the session JSONL).
This differs from the SKILL.md path which reads the current session JSONL. A follow-on story
(US-1b.07 `report_session`) provides the actual cost after the session.

**last-estimate.md structure** (written by the tool handler):
The standard SKILL.md template format — matching `calibration/last-estimate.md` written by
the SKILL.md path so `parse_last_estimate.py` can reconstitute from it.

---

### Change 5: New test file — `tests/test_estimation_engine.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_estimation_engine.py
Lines: new file
Parallelism: depends-on: Change 2 (can begin once engine interface is finalized)
Description:
  Unit tests for estimation_engine.py and file_measurement.py. Structured in classes
  matching the helper function groups. Uses current pricing.py values (not stale
  examples.md values).
```

See Test Strategy section below for full test case list.

---

## Output Dict Structure

`compute_estimate()` returns:
```python
{
    "version": "2.1.0",              # from __version__ or hardcoded
    "estimate": {
        "optimistic": float,
        "expected": float,
        "pessimistic": float,
    },
    "steps": [
        {
            "name": str,             # canonical step name
            "model": str,            # short model name: "Sonnet", "Opus", "Haiku"
            "model_id": str,         # full model ID e.g. "claude-sonnet-4-6"
            "cal": str,              # calibration label: "S:0.82", "G:1.12", "--", etc.
            "factor": float,         # calibration factor applied
            "optimistic": float,
            "expected": float,
            "pessimistic": float,
            "is_parallel": bool,
        },
        # PR Review Loop (when review_cycles > 0):
        {
            "name": "PR Review Loop",
            "model": "Opus+Sonnet",
            "model_id": None,
            "cal": "--",
            "factor": float,
            "optimistic": float,
            "expected": float,
            "pessimistic": float,
            "is_parallel": False,
        }
    ],
    "metadata": {
        "size": str,
        "files": int,
        "complexity": str,
        "project_type": str,
        "language": str,
        "review_cycles": int,
        "file_brackets": dict | None,
        "files_measured": int,
        "parallel_groups": list[list[str]],
        "parallel_steps_detected": int,
        "pricing_last_updated": str,          # LAST_UPDATED from pricing.py
        "pricing_stale": bool,                 # True if > STALENESS_WARNING_DAYS old
        "pipeline_signature": str,             # for debug/transparency, not written to active-estimate.json
    },
    "step_costs": {                            # calibrated Expected costs per step
        "<step name>": float,
        ...
        "PR Review Loop": float,               # present only when review_cycles > 0
    },
}
```

Note: The `model` short name ("Sonnet", "Opus", "Haiku") is derived from a reverse lookup of
`model_id` against the three `MODEL_*` constants in `pricing.py`. A simple dict at module init:
`_MODEL_SHORT = {pricing.MODEL_SONNET: "Sonnet", pricing.MODEL_OPUS: "Opus", pricing.MODEL_HAIKU: "Haiku"}`.

---

## Dependency Order

```
Change 1 (file_measurement.py)       — independent, start immediately
  ↓
Change 2 (estimation_engine.py)      — depends on Change 1
  ↓
Change 3 (api.py)                    — depends on Change 2
  ↓
Change 4 (estimate_cost.py tool)     — depends on Change 3
  ↓
Change 5 (test file)                 — depends on Change 2; tests can be scaffolded
                                        in parallel with Change 2 once function
                                        signatures are confirmed (after Change 1)
```

Changes 1 and the scaffolding of Change 5 (test structure, helper fixtures) are independent
and can run in parallel. Changes 2–4 must be sequential.

---

## Test Strategy

### Test file: `tests/test_estimation_engine.py`

**TestBracketAssignment (independent of calibration)**

- `test_small_bracket_at_boundary`: line_count=49 → "small"
- `test_small_bracket_below_boundary`: line_count=1 → "small"
- `test_medium_bracket_at_lower_boundary`: line_count=50 → "medium"
- `test_medium_bracket_at_upper_boundary`: line_count=500 → "medium"
- `test_large_bracket_at_boundary`: line_count=501 → "large"
- `test_large_bracket_above_boundary`: line_count=10000 → "large"

**TestComputeAvgTokens**

- `test_all_medium_files`: brackets={small:0, medium:5, large:0} → (10000, 2500)
- `test_all_small_files`: brackets={small:3, medium:0, large:0} → (3000, 1000)
- `test_all_large_files`: brackets={small:0, medium:0, large:2} → (20000, 5000)
- `test_mixed_brackets`: brackets={small:2, medium:2, large:1} total_measured=5
  → read=(2×3000 + 2×10000 + 1×20000)/5 = 46000/5 = 9200, edit=12000/5 = 2400
- `test_zero_divide_guard`: brackets={small:0, medium:0, large:0} → (10000, 2500)

**TestStepBaseTokens (verifying formula against examples.md arithmetic, with current pricing)**

Note: examples.md uses the two-term formula and stale pricing. We verify input_base, output_base, K
only — these are pricing-independent. Then verify three-term cost with current pricing values.

- `test_research_agent_base_tokens`: N=5 → input_base=80000, output_base=11700, K=14
  (6 reads + 4 greps + 1 plan + 3 conv = K=14; input = 60000+2000+3000+15000 = 80000)
- `test_architect_agent_base_tokens`: N=5 → input_base=21000, output_base=10000, K=4
- `test_engineer_initial_plan_base_tokens`: N=5 → input_base=54000, output_base=8800, K=9
- `test_staff_review_base_tokens`: N=5 → input_base=18000, output_base=6000, K=3
- `test_engineer_final_plan_base_tokens`: N=5 → input_base=33000, output_base=7400, K=5
- `test_test_writing_base_tokens_n5`: N=5 (3 reads + 5 test_writes + 3 conv)
  → input_base = 3×10000 + 5×2000 + 3×5000 = 55000 (medium default reads),
    output_base = 3×200 + 5×5000 + 3×1500 = 30100, K=11
- `test_implementation_base_tokens_n5`: N=5 (5 reads + 5 edits + 4 conv)
  → input_base = 5×10000 + 5×2500 + 4×5000 = 82500, K=14
- `test_qa_base_tokens`: N=5 → input_base=30900, output_base=4900, K=7
- `test_implementation_base_tokens_n5_with_mixed_brackets`:
  file_brackets={small:2, medium:2, large:1} → input_base=78000 (per Example 3)

**TestThreeTermCacheFormula**

- `test_three_term_formula_expected_band`: Research Agent (Sonnet, K=14, input_accum=600000,
  cache_rate=0.50, complexity=1.0). Verify:
  `input_cost = (600000 × 0.50 × 3.00 + 600000 × 0.50 × (1/14) × 3.75 + 600000 × 0.50 × (13/14) × 0.30) / 1000000`
  ≈ $1.0639 (from examples.md formula note)
  output_cost = 11700 × 15.00 / 1000000 = $0.1755
  step_cost = $1.0639 + $0.1755 = $1.2394 (all pre-band-mult at Expected = 1.0×)
  (This differs from the stale two-term result in examples.md.)
- `test_three_term_formula_optimistic_band`: same step, optimistic band (cache=0.60, band=0.6×)
- `test_three_term_formula_pessimistic_band`: same step, pessimistic band (cache=0.30, band=3.0×)
- `test_zero_K_guard`: K=0 edge case — no division by zero

**TestContextAccumulation**

- `test_context_accum_k14`: K=14 → factor = (14+1)/2 = 7.5
- `test_context_accum_k3`: K=3 → factor = (3+1)/2 = 2.0
- `test_parallel_discount_applied`: is_parallel=True → input_accum × 0.75
- `test_parallel_cache_rate_reduction`: is_parallel=True, band="optimistic" (0.60) → rate = 0.60 - 0.15 = 0.45
- `test_parallel_cache_rate_floor`: is_parallel=True, band="pessimistic" (0.30) → 0.30 - 0.15 = 0.15 (above floor 0.05)
- `test_parallel_cache_rate_floor_clamped`: simulate extreme where subtraction would go below 0.05 → clamped to 0.05

**TestCalibrationPrecedence**

- `test_per_step_factor_wins_over_global`: factors with both step_factors["Research Agent"]["status"]="active"
  and global → returns (factor, "S:...")
- `test_per_step_collecting_falls_through`: step_factors with status="collecting" → falls through to per-signature or lower
- `test_per_signature_factor_active`: no per-step, but signature_factors[sig]["status"]="active" → returns (factor, "P:...")
- `test_per_signature_collecting_falls_through`: signature_factors status="collecting" → falls through to size-class
- `test_size_class_factor_active`: no per-step/signature, but factors["M"]=0.95 and factors["M_n"]=5 → returns (0.95, "Z:0.95")
- `test_size_class_factor_insufficient_samples`: factors["M_n"]=2 (< 3 min_samples) → falls through to global
- `test_global_factor_active`: no per-step/signature/size-class, factors["global"]=1.10 and factors["status"]="active" → returns (1.10, "G:1.10")
- `test_no_calibration`: empty factors dict → returns (1.0, "--")
- `test_per_step_trumps_signature`: both per-step and per-signature active → per-step wins

**TestPRReviewLoop**

All using current pricing (Sonnet + Opus model prices from pricing.py).

- `test_pr_review_loop_n2_cycles`: C=1.0214 (from examples, which used old pricing — we recompute C
  from current Staff Review and Engineer Final Plan expected costs and verify the decay formula).
  Actually: compute C fresh from engine using N=5 files, medium complexity, no calibration.
  Expected cycle costs follow `C × (1 - 0.6^cycles) / 0.4`.
- `test_pr_review_loop_optimistic_1_cycle`: cycles=1 → cost = C × (1-0.6) / 0.4 = C × 1.0
- `test_pr_review_loop_expected_2_cycles`: cycles=2 → cost = C × (1-0.36) / 0.4 = C × 1.6
- `test_pr_review_loop_pessimistic_4_cycles`: cycles=4 → cost = C × (1-0.1296) / 0.4 = C × 2.176
- `test_pr_review_loop_zero_cycles_returns_none`: review_cycles=0 → returns None, no PR row
- `test_pr_review_loop_uses_prediscount_costs`: staff_review step is in parallel_set → C uses
  pre-discount Expected, not discounted
- `test_pr_review_loop_calibration_per_band`: with global factor=1.2 → each band × 1.2 independently
  (not re-anchored via _apply_calibration pattern)

**TestComputeEstimateIntegration (end-to-end)**

- `test_estimate_m_size_5_files_medium_no_calibration`: compute_estimate with canonical M-size
  5-file medium params, no calibration_dir. Verify:
  - `result["estimate"]["expected"]` is within 5% of hand-computed sum (use current pricing).
  - All step names present in `result["steps"]`.
  - `result["metadata"]["pricing_stale"]` is False (LAST_UPDATED is recent).
  - `result["step_costs"]` dict has same keys as step names list.
- `test_estimate_with_review_cycles`: same params + review_cycles=2.
  Verify PR Review Loop row present in steps and included in totals.
- `test_estimate_review_cycles_zero_omits_pr_row`: review_cycles=0 → no PR row in steps.
- `test_estimate_with_parallel_groups`: parallel_groups=[["Research Agent", "Architect Agent"]] →
  those steps have `is_parallel=True`, others False.
- `test_estimate_steps_override`: steps=["Implementation", "QA"] → only those two steps present.
- `test_estimate_no_calibration_factor_is_1`: no factors file → all cal labels are "--", factor=1.0.
- `test_estimate_with_global_calibration_factor`: inject factors with global=1.2, status="active" →
  each step's expected cost × 1.2.
- `test_estimate_with_file_brackets`: file_brackets={small:2, medium:2, large:1} →
  Implementation input_base=78000 (matches Example 3).
- `test_estimate_l_size_implementation_uses_opus`: size="L" → Implementation model is Opus.
- `test_estimate_output_keys_present`: verify all required top-level keys present:
  "version", "estimate", "steps", "metadata", "step_costs".
- `test_estimate_totals_sum_of_steps`: verify `result["estimate"]["expected"]` ==
  sum of all step "expected" values.
- `test_estimate_zero_files`: files=0 → N-scaling steps have 0 activities. No crash.

**TestMeasureFiles (requires temp files on disk)**

- `test_measure_small_file`: create a 30-line temp file → bracket = "small"
- `test_measure_medium_file`: create a 100-line temp file → bracket = "medium"
- `test_measure_large_file`: create a 600-line temp file → bracket = "large"
- `test_measure_multiple_files`: 2 small + 1 large → brackets = {"small":2, "medium":0, "large":1}
- `test_measure_nonexistent_file_skipped`: pass a path that doesn't exist → not counted in brackets
- `test_measure_binary_extension_skipped`: pass a .png path → not sent to wc -l
- `test_measure_cap_at_30_files`: pass 35 file paths → only 30 measured; files 31-35 get
  weighted-average bracket of first 30
- `test_measure_empty_file_paths`: empty list → brackets = None (no paths extracted)
- `test_measure_path_with_spaces`: create temp file in a path with spaces → wc -l succeeds
  (verifies shlex.quote handling)
- `test_measure_all_binary_returns_null_brackets`: all paths have binary extensions →
  brackets = {"small":0, "medium":0, "large":0} (paths extracted but none measurable)

**Edge Cases**

- `test_calibration_factor_1_on_pr_review_loop`: PR Review Loop cal label is always "--"
  regardless of factors (per SKILL.md).
- `test_band_multiplier_ratios`: calibrated_optimistic = calibrated_expected × 0.6,
  calibrated_pessimistic = calibrated_expected × 3.0 (for regular steps).
- `test_pricing_staleness_old_date`: inject LAST_UPDATED="2020-01-01" → pricing_stale=True.
- `test_pricing_staleness_recent_date`: LAST_UPDATED within 90 days → pricing_stale=False.

### Existing tests that may need updating

- `tests/test_mcp_scaffold.py` — the `test_estimate_cost_stub_returns_correct_shape` and related
  tests assert `result["_stub"] is True`. After Change 4, the real implementation no longer sets
  `_stub=True`. Those assertions must be updated to assert `"_stub" not in result` or changed to
  verify real content. The validation error tests (ValueError for missing params) remain unchanged.
- `tests/test_data_modules_drift.py` — no changes needed; already tests the Python modules.

---

## Implementation Notes for the Implementer

### `wc -l` output format

On macOS, `wc -l path` output looks like:
```
      10 /path/to/file
     500 /path/to/another
    1510 total
```
When multiple files are passed, there is a "total" line at the end. When a single file is passed,
there is NO "total" line. Parse by stripping the line, splitting on whitespace, checking if the
second token is "total" (skip it), otherwise first token is line count and remaining tokens form
the path. Use `' '.join(parts[1:])` to reconstruct paths with spaces.

### Import pattern for calibration_store

The engine needs `read_factors` from `scripts/calibration_store.py`. The `scripts/` directory is
not a Python package and is not on `sys.path` by default. Use the `importlib.util` pattern:

```python
import importlib.util
from pathlib import Path

def _load_calibration_store():
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    spec = importlib.util.spec_from_file_location(
        "calibration_store",
        scripts_dir / "calibration_store.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_calibration_store = _load_calibration_store()
read_factors = _calibration_store.read_factors
```

This mirrors the pattern in existing tests (e.g. `test_status_analysis.py` which uses the same
importlib approach for `tokencast-status.py`). The module is loaded once at module import time
(module-level), not per function call.

### K computation for Test Writing

Test Writing has: `("file_read", 3)`, `("test_write", N_SCALING)`, `("conversation_turn", 3)`.
With N=5 files:
- K = 3 (fixed reads) + 5 (N-scaling test writes) + 3 (conv turns) = 11
- input_base = `avg_file_read_tokens × 3` + `2000 × 5` + `5000 × 3` = 30000+10000+15000 = 55000 (medium default)
- output_base = `200 × 3` + `5000 × 5` + `1500 × 3` = 600+25000+4500 = 30100

### L-size Implementation model override

In `_resolve_model`, check `step_name == "Implementation" and size == "L"` and return
`pricing.MODEL_OPUS`. This is the only special case. All other steps use `STEP_MODEL_MAP` directly.

### Calibration factor global check

Per CLAUDE.md: `update-factors.py` writes `"global": 0.95` (plain float) and `"status": "active"`
as a top-level key. So the check is:
```python
g_factor = factors.get("global")           # plain float or None
g_status = factors.get("status")           # top-level "status"
if g_factor is not None and g_status == "active":
    return (g_factor, f"G:{g_factor:.2f}")
```
Do NOT check `factors["global"]["status"]` — it is not a dict.

### Calibration size-class check

`factors.get("M")` returns a plain float (e.g. 0.95), and `factors.get("M_n")` returns an int
(sample count). Both are top-level keys in `factors.json`.

### Three-term formula edge case when K=0

`cache_write_fraction = 1/K` would divide by zero if K=0. Add a guard:
`cache_write_fraction = 1.0 / max(K, 1)`. K=0 only occurs when a step has zero activities
(e.g. a step with only N_SCALING activities and N=0 files).

### Pricing staleness check

```python
from datetime import date
last_updated = date.fromisoformat(pricing.LAST_UPDATED)
today = date.today()
pricing_stale = (today - last_updated).days > pricing.STALENESS_WARNING_DAYS
```

### Output model short name

```python
_MODEL_SHORT = {
    pricing.MODEL_SONNET: "Sonnet",
    pricing.MODEL_OPUS: "Opus",
    pricing.MODEL_HAIKU: "Haiku",
}
model_short = _MODEL_SHORT.get(model_id, model_id)
```

---

## Rollback Notes

All changes are additive (new files) except:
- `api.py` (Change 3): one function body modified. The original stub body can be restored by
  reverting the `estimate_cost` function to return `{"_stub": True, "message": "Not yet implemented"}`.
- `estimate_cost.py` (Change 4): the handler body is replaced. Restore from git to return to
  the stub that sets `_stub: True`.

The new files (`estimation_engine.py`, `file_measurement.py`, `test_estimation_engine.py`) can
be deleted without affecting any other functionality.

No database migrations or persistent data structure changes.

The existing 441 tests continue to pass — only `test_mcp_scaffold.py`'s stub-flag assertions
need updating (see Test Strategy). The other test files are not affected by this change.
