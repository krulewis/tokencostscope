# US-1b.04: Implement `estimate_cost` MCP Tool — Implementation Plan

*Author: Engineer Agent*
*Date: 2026-03-26*
*Story: US-1b.04 from docs/phase-1b-mcp-stories.md*

---

## Overview

The `handle_estimate_cost` function in `src/tokencast_mcp/tools/estimate_cost.py` is already
substantially implemented — it validates input, calls the API, builds `active_estimate`, writes
`active-estimate.json` and `last-estimate.md`, and generates a markdown table. However several
gaps prevent it from meeting the acceptance criteria:

1. **`continuation` field missing** from `active-estimate.json`. `learn.sh` reads
   `_est.get('continuation', False)` from the estimate file. MCP-originated estimates must
   write `"continuation": false`.

2. **Accumulator cleanup on new estimate.** When a new estimate is created, any stale
   step-accumulator file from a prior session should be cleared. The handler does not currently
   do this.

3. **Scaffold tests are broken.** `TestToolStubs` in `tests/test_mcp_scaffold.py` checks
   `result["_stub"] is True` on every `handle_estimate_cost` call. The now-complete handler
   does not return `_stub`, so these tests will fail. They must be updated to assert the real
   output shape.

4. **No dedicated integration test file.** The story requires tests for the full tool chain
   including file writes, markdown output, active-estimate.json schema completeness, and
   last-estimate.md format.

The plan has three file changes and one new test file. Two of the file changes are independent
of each other; both must land before the test file can be written.

---

## Changes

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/tools/estimate_cost.py
Lines: 119-157 (active_estimate dict construction and file-write block)
Parallelism: independent
Description: Add missing `continuation` field to active_estimate dict and add accumulator
  cleanup. Both are small targeted additions to the existing handler.
Details:
  - In the `active_estimate` dict (line 120), add the field:
      "continuation": False
    Place it after the `"step_costs"` field at line 139. This field is required by learn.sh
    (RECORD block reads `_est.get('continuation', False)`) and by the history record schema.
    MCP-originated estimates are never continuation sessions, so the value is always False.
  - After the `_write_json_atomic` call completes successfully (inside the try block that
    ends at line 146), add accumulator cleanup:
    1. Compute the hash of the active-estimate path using the same MD5 pattern as
       `api._compute_accumulator_hash()`.
    2. Construct the accumulator path: `config.calibration_dir / f"{hash_prefix}-step-accumulator.json"`.
    3. Delete the accumulator file if it exists (use `path.unlink(missing_ok=True)`).
    Rationale: a new `estimate_cost` call starts a new session. Any step-accumulator left over
    from a prior session would produce incorrect cumulative totals when `report_step_cost` is
    called. Clear it eagerly when the new active-estimate.json is written.
    Place the cleanup after the atomic write (not before), so it only runs when the write
    succeeds. Keep it inside the outer try/except so cleanup failures are also silently logged.
  - Remove `result["_stub"] = True` if present (the current code does not add _stub, but
    verify this is not re-introduced by confirming the return value is the unmodified `result`
    dict from the API).
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_mcp_scaffold.py
Lines: 189-293 (TestToolStubs.test_estimate_cost_* methods)
Parallelism: depends-on: estimate_cost.py change
Description: The scaffold tests assert `result["_stub"] is True` on every handle_estimate_cost
  call. Now that the handler is fully implemented, these assertions fail. Update the stub
  tests to assert the real output shape instead.
Details:
  - Remove all `assert result["_stub"] is True` assertions from the estimate_cost test
    methods. The `_stub` key is not present in the real result.
  - In `test_estimate_cost_stub_returns_correct_shape` (line 189): remove the `_stub` check.
    Keep the assertions for `"version"`, `"estimate"`, `"steps"`, `"metadata"` keys.
    Add assertion that `"text"` key is present (markdown table).
    Add assertion that `"step_costs"` key is present.
  - In `test_estimate_cost_estimate_keys` (line 200): no change needed (already checks
    optimistic/expected/pessimistic — these remain correct).
  - In `test_estimate_cost_metadata_keys` (line 210): no change needed (checks size/files/
    complexity/file_brackets — correct).
  - In `test_estimate_cost_zero_files_is_valid` (line 221): remove `_stub` check, keep the
    `metadata["files"] == 0` assertion.
  - In `test_estimate_cost_empty_file_paths_is_valid` (line 228): replace
    `assert result["_stub"] is True` with `assert "estimate" in result`.
  - In `test_estimate_cost_all_valid_sizes` (line 277): replace the `_stub` assertion with
    `assert "estimate" in result`.
  - In `test_estimate_cost_all_valid_complexities` (line 285): replace the `_stub` assertion
    with `assert "estimate" in result`.
  - All error-raising tests (missing/invalid params) do not check `_stub` and require no
    changes.
  - Do NOT modify the get_calibration_status, get_cost_history, or report_session stub tests —
    those tools are not yet implemented.
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_estimate_cost_tool.py
Lines: new file
Parallelism: depends-on: estimate_cost.py change
Description: Dedicated integration tests for the fully-wired estimate_cost tool chain. Tests
  cover output shape, file writes, active-estimate.json schema completeness (all fields
  learn.sh reads), last-estimate.md format, markdown table generation, and edge cases.
Details:
  See Test Strategy section below for the full list of test classes and methods.
```

---

## `active-estimate.json` Schema (All Fields)

The complete schema that `handle_estimate_cost` must write. All fields are required by
`learn.sh` unless noted as optional:

```json
{
  "timestamp":               "<ISO-8601 UTC string>",
  "size":                    "M",
  "files":                   5,
  "complexity":              "medium",
  "steps":                   ["Research Agent", "..."],
  "step_count":              7,
  "project_type":            "greenfield",
  "language":                "python",
  "expected_cost":           6.24,
  "optimistic_cost":         3.44,
  "pessimistic_cost":        20.46,
  "baseline_cost":           0,
  "review_cycles_estimated": 2,
  "review_cycles_actual":    null,
  "parallel_groups":         [],
  "parallel_steps_detected": 0,
  "file_brackets":           {"small": 1, "medium": 3, "large": 1},
  "files_measured":          4,
  "step_costs":              {"Research Agent": 1.70, "...": 0.00},
  "continuation":            false
}
```

Field-by-field rationale:

| Field | Source | learn.sh reads it at |
|-------|---------|---------------------|
| `timestamp` | `datetime.now(timezone.utc).isoformat()` | RECORD block `TS_ENV` |
| `size` | `meta["size"]` | eval block `SIZE` |
| `files` | `meta["files"]` | eval block `FILES` |
| `complexity` | `meta["complexity"]` | eval block `COMPLEXITY` |
| `steps` | `all_step_names` | eval block `STEPS_JSON` |
| `step_count` | `len(all_step_names)` | eval block `STEP_COUNT` |
| `project_type` | `meta["project_type"]` | eval block `PROJECT_TYPE` |
| `language` | `meta["language"]` | eval block `LANGUAGE` |
| `expected_cost` | `estimate["expected"]` | eval block `EXPECTED_COST` |
| `optimistic_cost` | `estimate["optimistic"]` | RECORD block F10 |
| `pessimistic_cost` | `estimate["pessimistic"]` | RECORD block F10 |
| `baseline_cost` | `0` (hardcoded — no JSONL in MCP path) | eval block `BASELINE_COST` |
| `review_cycles_estimated` | `meta["review_cycles"]` | eval block `REVIEW_CYCLES` |
| `review_cycles_actual` | `null` (unknown until `report_session`) | RECORD block |
| `parallel_groups` | `meta["parallel_groups"]` | RECORD block |
| `parallel_steps_detected` | `meta["parallel_steps_detected"]` | eval block `PSD_ENV` |
| `file_brackets` | `meta["file_brackets"]` | RECORD block + `file_brackets` key |
| `files_measured` | `meta["files_measured"]` | RECORD block |
| `step_costs` | `result["step_costs"]` | RECORD block `step_costs_raw` |
| `continuation` | `false` (MCP path always false) | RECORD block `.get('continuation', False)` |

Note: `pipeline_signature` is derived inline by learn.sh from the `steps` array (line 62),
not read directly from the file. Do not add it to `active-estimate.json`.

---

## `last-estimate.md` Format

The file must be parseable by `scripts/parse_last_estimate.py` for continuation session support.
The current `_format_last_estimate_md` function already produces the correct format. Verify it
matches these patterns that `parse_last_estimate.py` uses:

1. **Size/Files/Complexity line:** must match the regex that parse_last_estimate.py uses.
   Current format: `**Size:** M | **Files:** 5 | **Complexity:** medium`
2. **Cost table rows:** Must use the exact label names "Optimistic", "Expected", "Pessimistic"
   with `$N.NNNN` format (4 decimal places as written by `_format_last_estimate_md`).
3. **Baseline Cost footer:** Must be the last line and match:
   `Baseline Cost: $0` (plain style — not bold). The `_format_last_estimate_md` function
   currently writes `f"Baseline Cost: ${active_estimate['baseline_cost']}"` which produces
   `Baseline Cost: $0`. This matches the regex in `parse_last_estimate.py`:
   `re.search(r'(?:\*\*Baseline Cost:\*\*|Baseline Cost:)\s*\$?([\d.]+)', line)`.

The `_format_last_estimate_md` function is already correct. No changes needed to it.

---

## Input Validation (Lenient Mode)

The handler's validation is already implemented correctly in lines 62-93. Summary:

- **Required:** `size` (XS|S|M|L), `files` (int ≥ 0), `complexity` (low|medium|high)
- **Optional with defaults in the engine:** `steps`, `project_type` (→ "greenfield"),
  `language` (→ "unknown"), `review_cycles`, `avg_file_lines`, `parallel_groups`, `file_paths`
- **Validation raises `ValueError`** with a descriptive message for invalid enum values or
  negative file counts. The server's `call_tool` handler converts `ValueError` to an MCP
  error response (isError=True) automatically.

No changes to validation logic are needed.

---

## Markdown Table Generation

The `_format_markdown_table` function is already fully implemented (lines 235-338). It produces
output matching the SKILL.md template:

```
## tokencast estimate (v2.1.0)

**Change:** size=M, files=5, complexity=medium, type=greenfield, lang=python
**Files:** 5 total (4 measured: 1 small, 2 medium, 1 large; 1 defaulted to medium)
**Steps:** Research Agent, PM Agent, ... (7 steps)
**Pricing:** last updated 2026-03-04

| Step                   | Model       | Cal    | Optimistic | Expected | Pessimistic |
|------------------------|-------------|--------|------------|----------|-------------|
| ┌ Research Agent       | Sonnet      | --     |      $1.02 |    $1.70 |       $5.10 |
| └ Architect Agent      | Opus        | --     |      $2.00 |    $3.30 |       $9.90 |
| ...                    | ...         | ...    |        ... |      ... |         ... |
| **TOTAL              ** |             |        |   **$3.44** |  **$6.24** |  **$20.46** |
Cal: S=per-step  P=per-signature  Z=size-class  G=global  --=uncalibrated

**Bands:** Optimistic (1 review cycle) · Expected (2 cycles) · Pessimistic (4 cycles)
**Tracking:** Estimate recorded. Actuals will be captured automatically at session end.
```

No changes to `_format_markdown_table` are needed.

---

## Dependency Order

```
1. estimate_cost.py change (add continuation field + accumulator cleanup)
        │
        ├──> test_mcp_scaffold.py update (fix stub assertions)
        │
        └──> tests/test_estimate_cost_tool.py (new test file)
```

The scaffold test update and new test file are independent of each other and can be written in
parallel after the handler change lands.

---

## Test Strategy

### File: `/Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_estimate_cost_tool.py`

New file. Module-level skip guard: `pytest.importorskip("mcp")` (same pattern as
`test_mcp_scaffold.py`). Uses `asyncio.run()` wrappers for async handler calls.

**Class `TestEstimateCostOutputShape`** — Parallelism: independent

- `test_result_has_version_key` — result["version"] is a non-empty string
- `test_result_has_estimate_key_with_three_bands` — result["estimate"] has optimistic,
  expected, pessimistic keys; all are positive floats
- `test_result_has_steps_list` — result["steps"] is a list with at least one item; each item
  has "name", "model", "cal", "optimistic", "expected", "pessimistic" keys
- `test_result_has_metadata_key` — result["metadata"] has size, files, complexity,
  project_type, language, review_cycles, file_brackets, files_measured, parallel_groups,
  parallel_steps_detected, pricing_last_updated, pricing_stale, pipeline_signature
- `test_result_has_step_costs` — result["step_costs"] is a dict; values are floats
- `test_result_has_text_field` — result["text"] is a non-empty string
- `test_text_contains_tokencast_header` — result["text"] starts with "## tokencast estimate"
- `test_text_contains_total_row` — result["text"] contains "TOTAL"
- `test_text_contains_step_rows` — result["text"] contains each step name from result["steps"]
- `test_optimistic_less_than_expected_less_than_pessimistic` — band ordering invariant

**Class `TestEstimateCostFileWrites`** — Parallelism: independent

- `test_active_estimate_json_is_written` — after handler call, `config.active_estimate_path`
  exists
- `test_active_estimate_json_is_valid_json` — file parses as JSON without error
- `test_active_estimate_json_has_all_required_fields` — assert all 20 fields from schema
  above are present (use a set comparison)
- `test_active_estimate_json_continuation_is_false` — data["continuation"] is False
- `test_active_estimate_json_baseline_cost_is_zero` — data["baseline_cost"] == 0
- `test_active_estimate_json_review_cycles_actual_is_null` — data["review_cycles_actual"]
  is None
- `test_active_estimate_json_step_costs_excludes_pr_review_loop` — if result["steps"] has a
  "PR Review Loop" entry, verify it IS present in data["step_costs"] (step_costs includes
  all steps); verify data["steps"] includes "PR Review Loop" in `all_step_names`
- `test_active_estimate_json_values_match_result` — check that data["expected_cost"] ==
  result["estimate"]["expected"], data["size"] == result["metadata"]["size"], etc.
- `test_last_estimate_md_is_written` — `config.last_estimate_path` exists after call
- `test_last_estimate_md_contains_optimistic_band` — file text contains "Optimistic"
- `test_last_estimate_md_contains_expected_band` — file text contains "Expected"
- `test_last_estimate_md_contains_baseline_cost_footer` — file text contains
  "Baseline Cost: $0"
- `test_last_estimate_md_parseable_by_parse_last_estimate` — call
  `parse_last_estimate.parse(content, mtime=None)` (import via importlib.util from
  `scripts/parse_last_estimate.py`) and assert the result is not None and has "expected_cost"
- `test_write_errors_do_not_raise` — point `config.calibration_dir` at a read-only path
  (use `os.chmod`); handler must return a result dict without raising (writes fail silently)

**Class `TestEstimateCostAccumulatorCleanup`** — Parallelism: independent

- `test_stale_accumulator_is_deleted_on_new_estimate` — create a fake accumulator file
  matching the hash pattern, call handler, verify the file is gone
- `test_accumulator_cleanup_only_targets_correct_hash` — create two fake accumulator files
  with different hashes; after handler call, verify that only the one matching the new
  active-estimate path is removed (the other remains)
- `test_no_accumulator_file_does_not_error` — handler must not raise when no accumulator
  exists (missing_ok=True path)

**Class `TestEstimateCostEdgeCases`** — Parallelism: independent

- `test_zero_files_returns_valid_estimate` — files=0, all bands are non-negative floats
- `test_l_size_includes_more_steps` — size="L" returns more steps than size="XS"
- `test_review_cycles_zero_excludes_pr_review_loop` — pass review_cycles=0; result["steps"]
  must not contain "PR Review Loop"
- `test_review_cycles_nonzero_includes_pr_review_loop` — pass review_cycles=2 with steps
  containing "Staff Review" and "Engineer Final Plan"; result["steps"] must contain
  "PR Review Loop"
- `test_avg_file_lines_override_uses_bracket` — pass avg_file_lines=100 (medium); metadata
  file_brackets should reflect all-medium distribution
- `test_parallel_groups_accepted` — pass parallel_groups with two steps; result must not
  raise and parallel_steps_detected > 0
- `test_unknown_steps_in_override_are_skipped` — pass steps=["Research Agent", "Unknown Step
  XYZ"]; handler must not raise; "Unknown Step XYZ" should not appear in result["steps"]
- `test_high_complexity_costs_more_than_low` — same params except complexity; high expected
  > low expected
- `test_calibration_dir_absent_uses_no_calibration` — pass calibration_dir pointing at a
  non-existent path; result still has valid estimate (factors default to 1.0)
- `test_pricing_stale_flag_in_metadata` — mock pricing.LAST_UPDATED to a date >90 days ago;
  result["metadata"]["pricing_stale"] must be True

**Class `TestEstimateCostWithFilePaths`** — Parallelism: independent

- `test_file_paths_with_existing_files_measured` — create 3 temp files with known line
  counts; pass file_paths pointing at them with project_dir set; verify files_measured == 3
  and file_brackets reflect the correct distribution
- `test_file_paths_with_missing_files_uses_defaults` — pass file_paths pointing at
  non-existent paths; handler must not raise; files_measured may be 0
- `test_file_paths_empty_list_uses_medium_default` — pass file_paths=[]; file_brackets is
  None (no paths extracted)
- `test_file_paths_resolution_relative_to_project_dir` — create a file in tmp_path; pass its
  basename in file_paths with project_dir=tmp_path; verify it is measured

**Updates to `tests/test_mcp_scaffold.py`:**

Class `TestToolStubs`, all `test_estimate_cost_*` methods (lines 189-293):

- Remove every `assert result["_stub"] is True` line.
- Replace with `assert "estimate" in result` in tests that only checked `_stub`.
- In `test_estimate_cost_stub_returns_correct_shape` (line 189): also assert `"text" in result`.
- All error-raising tests need no changes.

---

## Rollback Notes

- All changes are additive or test-only. The only functional change to the handler is the
  addition of a `"continuation": false` field in the JSON write and an `unlink(missing_ok=True)`
  call on an accumulator file.
- To revert: remove the two lines added to `handle_estimate_cost` (continuation field and
  accumulator cleanup block).
- The scaffold test changes (`_stub` assertion removal) can be reverted by restoring the
  original test assertions. The new test file can be deleted wholesale.
- No data migrations. No schema changes to `history.jsonl` or `factors.json`.
- Existing 441+ tests must still pass after these changes. The scaffold tests will fail until
  both the handler change and the scaffold test update are applied together.
