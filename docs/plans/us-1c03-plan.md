# Implementation Plan — US-1c.03: Refactor Session Recorder to Accept Multiple Attribution Sources

*Absorbs US-1b.11*
*Date: 2026-03-26*
*Status: Initial Plan*

---

## Overview

The ~80-line Python RECORD block embedded in `scripts/tokencast-learn.sh` (lines 151–240) is extracted into a pure-Python function `build_history_record()` living in a new module `src/tokencast/session_recorder.py`. Both learn.sh and the `report_session` MCP tool call this single function. The function accepts three attribution source inputs (mutually exclusive — first non-None wins): MCP-accumulated step costs, Claude Code sidecar step costs, or neither (triggering proportional fallback). The `attribution_method` in the resulting record is `"mcp"`, `"sidecar"`, or `"proportional"` accordingly.

The existing learn.sh sidecar path is unchanged in behavior. The report_session stub in `api.py` is replaced with a real implementation. All 441+ existing tests continue to pass without modification.

---

## Changes

### Change 1 — Create `src/tokencast/session_recorder.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/session_recorder.py
Lines: new file
Parallelism: independent
Description: New module containing build_history_record() — the single source of truth for
             constructing calibration history records. Extracted from the learn.sh RECORD block.
```

**Details:**

Function signature (exact, per US-1c.03 acceptance criteria):

```python
def build_history_record(
    estimate: dict,
    actual_cost: float,
    turn_count: int = 0,
    review_cycles_actual: Optional[int] = None,
    step_actuals_mcp: Optional[dict] = None,
    step_actuals_sidecar: Optional[dict] = None,
    timestamp: Optional[str] = None,
) -> dict:
```

Logic rules extracted verbatim from the learn.sh RECORD block:

- `estimate` is the `active-estimate.json` dict (may be `{}` for reconstituted sessions or no-estimate path).
- `timestamp` defaults to `datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")` when `None` — kept as a parameter so tests can inject a fixed value.
- Attribution priority: `step_actuals_mcp` first (if not None and non-empty), then `step_actuals_sidecar` (if not None and non-empty), then proportional fallback.
- `attribution_method`: `"mcp"` when `step_actuals_mcp` provided and non-empty; `"sidecar"` when `step_actuals_sidecar` provided and non-empty; `"proportional"` otherwise.
- `step_costs_estimated`: read from `estimate.get("step_costs", {})`, exclude `"PR Review Loop"` key exactly (case-sensitive, per existing comment in learn.sh line 168).
- `step_ratios` computation:
  - When `step_actuals` (the winning source) and `step_costs_estimated` are both non-empty: per-step ratio `actual_step / estimated` for steps where both `estimated > 0` and `actual_step > 0`; skip steps where either is zero.
  - When either is empty (proportional fallback): `session_ratio = round(actual / session_expected, 4)`; apply uniformly to all steps in `step_costs_estimated`.
- `session_expected = max(float(estimate.get("expected_cost", 0) or 0), 0.001)` — exact guard from learn.sh line 178.
- `ratio = round(actual / session_expected, 4)`.
- `optimistic_cost = estimate.get("optimistic_cost", 0)`.
- `pessimistic_cost = estimate.get("pessimistic_cost", 0)`.
- `parallel_groups = estimate.get("parallel_groups", [])`.
- `parallel_steps_detected`: read `estimate.get("parallel_steps_detected", 0)`; coerce the same way as learn.sh line 231 (int if digit-like, bool-like string to 0/1).
- `file_brackets = estimate.get("file_brackets")` — may be `None` or a dict.
- `files_measured = estimate.get("files_measured", 0)`.
- `continuation = estimate.get("continuation", False)`.
- The returned dict has exactly these keys in this order (order matches learn.sh output for diff-readability):
  `timestamp, size, files, complexity, expected_cost, optimistic_cost, pessimistic_cost, actual_cost, ratio, turn_count, steps, pipeline_signature, project_type, language, step_count, review_cycles_estimated, review_cycles_actual, parallel_groups, parallel_steps_detected, file_brackets, files_measured, step_costs_estimated, step_ratios, step_actuals, attribution_method, continuation`.
- `step_actuals` in the returned dict: the winning source dict if attribution is mcp or sidecar; `None` if proportional (matching learn.sh line 236: `step_actuals if step_actuals else None`).
- `size, files, complexity, project_type, language` read from estimate with the same fallbacks as learn.sh eval block (lines 65–76): `"M"`, `0`, `"medium"`, `"unknown"`, `"unknown"` respectively.
- `steps` = `estimate.get("steps", [])`.
- `pipeline_signature`: derive inline using `'+'.join(sorted(s.lower().replace(' ', '_') for s in steps))` — same formula as learn.sh line 62.
- `step_count = int(estimate.get("step_count", 0) or 0)`.
- `review_cycles_estimated = int(estimate.get("review_cycles_estimated", 0) or 0)`.

**No sidecar file I/O in this function.** The `review_cycles_actual` from the sidecar is computed by the caller (learn.sh) and passed in as a parameter. The function does not read any files.

Import dependencies: `json` (for coercion helpers), `os` (not required — no file I/O), `typing.Optional`, `datetime`.


### Change 2 — Modify `src/tokencast/api.py`: implement `report_session`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/api.py
Lines: 314-326 (replace stub) + add imports
Parallelism: depends-on: Change 1
Description: Replace the _stub return with a real implementation that:
             1. Reads active-estimate.json from calibration_dir
             2. Merges accumulated step costs (from accumulator file) with call-time step_actuals
             3. Calls build_history_record() with step_actuals_mcp
             4. Calls calibration_store.append_history()
             5. Deletes the accumulator file
             6. Deletes active-estimate.json
             7. Returns protocol-compliant response
```

**Details:**

Add import at top of `api.py`:
```python
from tokencast.session_recorder import build_history_record
```

New `report_session` implementation — replace lines 314–326:

```python
def report_session(params: dict, calibration_dir: Optional[Path] = None) -> dict:
```

Parameter handling:
- `actual_cost`: required float. Return `{"error": "missing_actual_cost", "message": "..."}` if absent. Return `{"error": "invalid_cost", "message": "actual_cost must be >= 0."}` if negative.
- `step_actuals` (call-time): optional dict; validate values are non-negative floats.
- `turn_count`: optional int, default 0.
- `review_cycles_actual`: optional int, default None.

Calibration dir resolution: identical pattern to `report_step_cost` — `calibration_dir or Path.home() / ".tokencast" / "calibration"`.

Active estimate loading:
- Try to read `calibration_dir / "active-estimate.json"`.
- If missing, attempt reconstitution via `parse_last_estimate.py` (same logic as learn.sh lines 30–53). For the MCP path, call the Python `parse()` function directly from `scripts/parse_last_estimate.py` (load via `importlib.util`). If reconstitution fails, build a minimal estimate dict: `{"size": "unknown", "steps": [], "expected_cost": 0, "continuation": False}`. Always include `warning: "no_active_estimate"` in this case.
- If `actual_cost <= 0.001`: return `{"attribution_protocol_version": 1, "record_written": False, "attribution_method": "proportional", "actual_cost": actual_cost, "step_actuals": None, "warning": "actual_cost is 0.0; no calibration record written."}`.

Accumulator merge (Section 3, Rule 4 of attribution-protocol.md):
- Load accumulator: `_load_accumulator(accumulator_path)` if accumulator file exists.
- Stale-accumulator check: if `active-estimate.json` was absent AND accumulator is present, log `warning: "stale_accumulator_discarded"` and skip accumulator.
- Merge: start with accumulated dict; overlay call-time `step_actuals` values (call-time wins for duplicate keys).
- Merged result is `step_actuals_mcp` — pass to `build_history_record()`.

Build and persist record:
```python
record = build_history_record(
    estimate=estimate_data,
    actual_cost=actual_cost,
    turn_count=turn_count,
    review_cycles_actual=review_cycles_actual,
    step_actuals_mcp=merged_step_actuals or None,
)
cs = _load_calibration_store()
history_path = str(calibration_dir / "history.jsonl")
factors_path = str(calibration_dir / "factors.json")
cs.append_history(history_path, record)
# calibration_store.append_history() handles factor recomputation via update-factors.py
```

Cleanup:
- Delete accumulator file if it exists: `accumulator_path.unlink(missing_ok=True)`.
- Delete `active-estimate.json`: `active_estimate_path.unlink(missing_ok=True)`.
- Clear in-memory state: `_step_accumulator = {}`, `_accumulator_file_path = None`.

Return response (per Section 5 output schema):
```python
{
    "attribution_protocol_version": 1,
    "record_written": True,
    "attribution_method": record["attribution_method"],
    "actual_cost": actual_cost,
    "step_actuals": record["step_actuals"],
    # "warning": only present if applicable
}
```

Note: `calibration_store.append_history()` currently has signature `append_history(history_path, record)` — it does NOT take a `factors_path` argument (the CLI wrapper does that). The actual Python function signature must be checked. The CLI `append-history` sub-command calls `append_history()` then invokes `update-factors.py`. For the MCP path, use the CLI sub-command via `subprocess` OR replicate the two-step call (append + run update-factors). The cleaner path is to call the CLI: `python3 calibration_store.py append-history --history ... --factors ... --record ...`. This matches what learn.sh already does (line 244) and avoids duplicating the subprocess invocation. Alternative: call `cs.append_history(history_path, record)` then separately invoke `update-factors.py`. Either works; the plan uses the subprocess approach to match learn.sh exactly.


### Change 3 — Modify `scripts/tokencast-learn.sh`: replace RECORD block with Python call

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-learn.sh
Lines: 151-240 (replace the RECORD=`...` block)
Parallelism: depends-on: Change 1
Description: Replace the ~80-line inline Python RECORD block with a call to
             build_history_record() from session_recorder.py. The surrounding
             shell logic (env var eval, actual cost computation, calibration_store
             invocation) is unchanged.
```

**Details:**

The new RECORD block (replacing lines 153–240) becomes a shell heredoc or single `-c` invocation that:
1. Imports `session_recorder.build_history_record` — must resolve the module from `src/tokencast/` relative to `SCRIPT_DIR`. Use `sys.path.insert(0, src_dir)` before importing.
2. Passes all inputs via environment variables (same injection-safe env-var pattern as before — no change in security posture).
3. Calls `build_history_record(estimate, actual_cost, ...)` and prints the result as JSON.

```bash
RECORD=$(
  SCRIPT_DIR_ENV="$SCRIPT_DIR" TS_ENV="$TIMESTAMP" \
  AC_ENV="$ACTUAL_COST" TC_ENV="$TURN_COUNT" \
  SA_ENV="$STEP_ACTUALS_JSON" SIDECAR_PATH_ENV="${SIDECAR_PATH:-}" \
  RC_ACT_ENV="${REVIEW_CYCLES_ACTUAL:-}" \
  EST_FILE="$ESTIMATE_FILE" \
  python3 -c "
import json, os, sys
script_dir = os.environ['SCRIPT_DIR_ENV']
src_dir = os.path.join(os.path.dirname(script_dir), 'src')
sys.path.insert(0, src_dir)
from tokencast.session_recorder import build_history_record

_est = json.load(open(os.environ['EST_FILE'])) if os.path.exists(os.environ.get('EST_FILE', '')) else {}

step_actuals_sidecar = json.loads(os.environ.get('SA_ENV', '{}')) or None
if step_actuals_sidecar == {}:
    step_actuals_sidecar = None

rc_raw = os.environ.get('RC_ACT_ENV', '')
review_cycles_actual = int(rc_raw) if rc_raw.strip().isdigit() else None

record = build_history_record(
    estimate=_est,
    actual_cost=float(os.environ['AC_ENV']),
    turn_count=int(os.environ.get('TC_ENV', 0) or 0),
    review_cycles_actual=review_cycles_actual,
    step_actuals_sidecar=step_actuals_sidecar,
)
print(json.dumps(record))
"
)
```

The `TIMESTAMP` shell variable set at line 152 is no longer needed — `build_history_record()` generates it internally. Remove the `TIMESTAMP=$(date ...)` line.

The `review_cycles_actual` value currently computed inline within the RECORD block (lines 198–210, counting `agent_stop` events from the sidecar) must be computed before this new block and passed in as `RC_ACT_ENV`. Extract that logic into a separate Python call earlier in the script, before the new RECORD block, storing the result in a shell variable `REVIEW_CYCLES_ACTUAL`.

Extract block for sidecar review-cycle counting (new shell snippet, inserted after the existing `ACTUAL_JSON` eval block):

```bash
REVIEW_CYCLES_ACTUAL=""
if [ -n "${SIDECAR_PATH:-}" ] && [ -f "$SIDECAR_PATH" ]; then
    REVIEW_CYCLES_ACTUAL=$(SIDECAR_PATH_ENV="$SIDECAR_PATH" python3 -c "
import json, os
sidecar_path = os.environ['SIDECAR_PATH_ENV']
events = []
with open(sidecar_path) as sf:
    for line in sf:
        try: events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError): pass
rc = len([e for e in events
    if e.get('type') == 'agent_stop'
    and 'staff' in e.get('agent_name', '').lower()
    and 'review' in e.get('agent_name', '').lower()])
print(rc if rc > 0 else '')
" 2>/dev/null || echo "")
fi
```

The `calibration_store.py append-history` call at lines 242–247 is unchanged.

The `if python3 -c "...float > 0.001..." "$ACTUAL_COST"` guard at line 150 is unchanged.


### Change 4 — Add `build_history_record` to `src/tokencast/__init__.py` exports

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/__init__.py
Lines: 1-13 (add one import)
Parallelism: depends-on: Change 1
Description: Export build_history_record from the package for direct Python usage
             (CI pipelines, integration tests, future adapters).
```

**Details:**

Add to the imports block:
```python
from tokencast.session_recorder import build_history_record  # noqa: F401
```

The attribution-protocol.md Section 10 Example B shows `from tokencast import ... report_session` — this is already exported. `build_history_record` does not need to be in `__init__.py` for the protocol to work, but exporting it enables the CI/CD programmatic path and integration test imports without `importlib` gymnastics. Add it for completeness; it is a non-breaking addition.


### Change 5 — Create `tests/test_session_recorder.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_session_recorder.py
Lines: new file
Parallelism: depends-on: Change 1 (can be written in parallel with Changes 2-3 once Change 1 interface is finalized)
Description: Unit tests for build_history_record() covering all three attribution paths,
             schema completeness, edge cases, and cross-path equivalence.
```

**Test classes and cases:**

`TestBuildHistoryRecordProportional`:
- `test_proportional_fallback_no_step_actuals`: both `step_actuals_mcp` and `step_actuals_sidecar` are None; verify `attribution_method == "proportional"` and `step_actuals is None`.
- `test_proportional_fallback_empty_dicts`: both passed as `{}` (empty, not None); verify same result as None case.
- `test_step_ratios_proportional_uniform`: confirm all step keys in `step_ratios` have the same value (`actual / expected`).
- `test_session_expected_floor`: `expected_cost=0` in estimate; verify `session_expected` clamps to 0.001 (no ZeroDivision).
- `test_ratio_field`: `ratio == round(actual / session_expected, 4)`.

`TestBuildHistoryRecordMcp`:
- `test_mcp_attribution_method`: `step_actuals_mcp` non-empty → `attribution_method == "mcp"`.
- `test_mcp_step_ratios_per_step`: verify per-step ratio computed as `actual / estimated` for each step.
- `test_mcp_step_ratios_skip_zero_estimated`: steps with `estimated == 0` are excluded from `step_ratios`.
- `test_mcp_step_ratios_skip_zero_actual`: steps with `actual == 0` are excluded from `step_ratios`.
- `test_mcp_wins_over_sidecar`: both `step_actuals_mcp` and `step_actuals_sidecar` provided; verify mcp wins.
- `test_mcp_step_actuals_in_record`: `step_actuals` field in record equals the mcp input.

`TestBuildHistoryRecordSidecar`:
- `test_sidecar_attribution_method`: `step_actuals_mcp=None`, `step_actuals_sidecar` non-empty → `"sidecar"`.
- `test_sidecar_step_ratios`: verify per-step ratios computed correctly.
- `test_sidecar_step_actuals_in_record`: `step_actuals` field equals sidecar input.

`TestBuildHistoryRecordSchema`:
- `test_all_required_keys_present`: verify all 26 expected keys are present in the returned dict.
- `test_pr_review_loop_excluded_from_step_costs`: estimate has `step_costs` with `"PR Review Loop"` key; verify it is absent from `step_costs_estimated` in the record.
- `test_file_brackets_null_passthrough`: `estimate` has `"file_brackets": null`; verify `record["file_brackets"] is None`.
- `test_file_brackets_dict_passthrough`: estimate has `"file_brackets": {"small": 1, "medium": 2, "large": 0}`; verify round-trip.
- `test_continuation_false_default`: estimate has no `continuation` key; verify `record["continuation"] == False`.
- `test_continuation_true`: estimate has `"continuation": True`; verify passthrough.
- `test_pipeline_signature_derivation`: steps `["Research Agent", "Implementation"]` → signature `"implementation+research_agent"` (sorted, lowercased, underscored).
- `test_empty_estimate`: `estimate={}` → record is still valid with fallback values; no exception.
- `test_review_cycles_actual_none`: passing `review_cycles_actual=None` → `record["review_cycles_actual"] is None`.
- `test_review_cycles_actual_int`: passing `review_cycles_actual=3` → `record["review_cycles_actual"] == 3`.
- `test_timestamp_injected`: passing fixed `timestamp="2026-01-01T00:00:00Z"` → exact value in record.
- `test_timestamp_auto_generated`: passing `timestamp=None` → value is a non-empty string matching ISO 8601 pattern.

`TestBuildHistoryRecordLearnShEquivalence`:
- `test_sidecar_record_matches_learn_sh_output`: construct a full estimate dict (all fields) and step_actuals_sidecar; verify the record dict matches what the learn.sh inline Python would have produced for the same inputs. This is the regression guard for the extraction. Use a fixture that has known expected values manually derived from the learn.sh logic.
- `test_proportional_record_matches_learn_sh_output`: same for proportional case.


### Change 6 — Create `tests/test_report_session.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_report_session.py
Lines: new file
Parallelism: depends-on: Changes 1 and 2
Description: Integration tests for report_session() in api.py — end-to-end through
             build_history_record() to history.jsonl. Covers all three attribution
             paths and error cases.
```

**Test classes and cases:**

`TestReportSessionTier1Proportional`:
- `test_tier1_writes_history_record`: call `estimate_cost`, then `report_session({"actual_cost": 2.0})`; verify `history.jsonl` has 1 record with `attribution_method: "proportional"`.
- `test_tier1_response_fields`: verify response has `attribution_protocol_version=1`, `record_written=True`, `attribution_method="proportional"`, `actual_cost=2.0`.
- `test_tier1_clears_accumulator`: verify `{hash}-step-accumulator.json` is deleted after `report_session`.
- `test_tier1_clears_active_estimate`: verify `active-estimate.json` is deleted after `report_session`.
- `test_tier1_zero_cost_no_record`: `actual_cost=0.0` → `record_written=False`, `warning` present, no line in history.

`TestReportSessionTier2Mcp`:
- `test_tier2_writes_mcp_record`: `estimate_cost` + `report_step_cost("Research Agent", cost=1.2)` + `report_session({"actual_cost": 3.0})`; verify `attribution_method: "mcp"`, `step_actuals: {"Research Agent": 1.2}`.
- `test_tier2_call_time_step_actuals`: pass `step_actuals={"Implementation": 2.0}` directly to `report_session`; verify merged with accumulated.
- `test_tier2_call_time_overrides_accumulated`: accumulate `Implementation: 1.0`, then pass `step_actuals={"Implementation": 2.5}` at call time; verify `step_actuals["Implementation"] == 2.5`.
- `test_tier2_response_attribution_mcp`: verify `attribution_method == "mcp"` when step actuals present.

`TestReportSessionNoEstimate`:
- `test_no_estimate_no_last_estimate_md`: no active estimate, no last-estimate.md; `report_session({"actual_cost": 1.0})` → `record_written=True`, `warning` contains `"no_active_estimate"`, `attribution_method: "proportional"`.
- `test_no_estimate_stale_accumulator_discarded`: accumulator exists but no active estimate; verify `warning` includes `"stale_accumulator_discarded"` and step_actuals=None.

`TestReportSessionValidation`:
- `test_missing_actual_cost`: `report_session({})` → error response.
- `test_negative_actual_cost`: `report_session({"actual_cost": -1.0})` → error response.
- `test_negative_step_actual_value`: `step_actuals={"Research Agent": -0.5}` → error response.


---

## Dependency Order

```
Change 1 (session_recorder.py — new module)
    |
    +-- Change 2 (api.py report_session implementation) [depends-on: 1]
    |
    +-- Change 3 (learn.sh RECORD block replacement)    [depends-on: 1]
    |
    +-- Change 4 (__init__.py export)                   [depends-on: 1]
    |
    +-- Change 5 (test_session_recorder.py)             [depends-on: 1; interface-only deps on 2,3]
    |
    +-- Change 6 (test_report_session.py)               [depends-on: 1, 2]
```

Changes 2, 3, 4, and 5 can all run in parallel after Change 1 is complete.
Change 6 requires both Change 1 and Change 2.

---

## Test Strategy

### Backward compatibility (regression guard)

Before any changes, run:
```bash
/usr/bin/python3 -m pytest tests/ -v
```
All 441+ existing tests must pass. The test count baseline is recorded before implementation begins.

After Change 3 (learn.sh replacement):
- Run the existing learn.sh integration tests (in `test_continuation_session.py` and any other tests that invoke learn.sh with a mock JSONL). These exercise the full shell path. If any test previously asserted on the exact JSON structure of the RECORD output, it will remain valid because `build_history_record()` produces identical output.
- Key: `TestLearnShContinuation` in `test_continuation_session.py` calls `learn.sh` end-to-end with mock JSONLs. These must pass unchanged.

### New tests (Change 5)

`test_session_recorder.py` is pure unit tests — no file I/O, no subprocess calls. All test inputs are in-memory dicts. Fixtures use `tmp_path` only where needed for the timestamp test.

**Equivalence tests** in `TestBuildHistoryRecordLearnShEquivalence`: construct expected output manually using the same arithmetic the learn.sh inline Python performs, then assert equality. This is the definitive regression check that the extraction is correct.

### New tests (Change 6)

`test_report_session.py` is an integration test using `tmp_path` as `calibration_dir`. It calls the real `report_session()` and `report_step_cost()` functions. Does not use subprocess. Does not use MCP wire protocol (that belongs to US-1c.04).

### Edge cases that must be covered

- `estimate = {}` (empty dict — reconstitution failed, minimal record written).
- `step_actuals_mcp = {}` (empty dict — treated as "no step data" → proportional fallback, not "mcp").
- `step_actuals_mcp` has a key not present in `step_costs_estimated` — key still written to `step_actuals`, no ratio computed for it.
- `step_costs_estimated` is empty — proportional fallback even when `step_actuals` is provided (the `step_ratios` dict will be empty).
- `actual_cost = 0.001` (boundary — just above threshold, record IS written).
- `actual_cost = 0.001` minus epsilon (below threshold, record is NOT written).
- `review_cycles_actual = 0` — stored as `None` in the record (matches learn.sh line 210: `rc_count if rc_count > 0 else None`). NOTE: the function receives an already-computed value; the "0 becomes None" logic must be preserved. Adjust the function signature or apply the conversion inside `build_history_record()`.

### What existing tests might break

- Any test that currently patched or mocked the inline Python within learn.sh's RECORD block: these do not exist (the inline Python is invoked via subprocess, not imported). Risk is low.
- `test_continuation_session.py` integration tests: call learn.sh via subprocess. The only risk is if `SCRIPT_DIR` path resolution fails for the `src/` import path. The plan's Change 3 uses `os.path.dirname(script_dir)` to find repo root — this must be correct for the test harness's invocation path. Tests set `SCRIPT_DIR` via the actual script path, so the resolution will be identical to production.

### Tests runnable in parallel with implementation

`test_session_recorder.py` (Change 5) can be written in parallel with Change 2 and Change 3 because the function interface (Change 1) is fully defined in this plan. The QA agent can implement Change 5 immediately after Change 1 lands.

---

## `build_history_record()` — Complete Signature and Contract

```python
def build_history_record(
    estimate: dict,
    actual_cost: float,
    turn_count: int = 0,
    review_cycles_actual: Optional[int] = None,
    step_actuals_mcp: Optional[dict] = None,
    step_actuals_sidecar: Optional[dict] = None,
    timestamp: Optional[str] = None,
) -> dict:
    """Build a calibration history record dict from session data.

    Attribution priority (first non-None, non-empty wins):
      1. step_actuals_mcp  -> attribution_method = "mcp"
      2. step_actuals_sidecar -> attribution_method = "sidecar"
      3. proportional fallback -> attribution_method = "proportional"

    Args:
        estimate: Contents of active-estimate.json (or {} for unknown sessions).
        actual_cost: Total session cost in dollars (post-baseline, already computed).
        turn_count: Number of billable turns. Default 0.
        review_cycles_actual: Actual PR review cycles. None if unknown.
                              Callers must convert 0 to None (learn.sh convention).
        step_actuals_mcp: Step costs from MCP report_step_cost accumulation.
                         None or empty dict means "not provided".
        step_actuals_sidecar: Step costs from sidecar JSONL span attribution.
                              None or empty dict means "not provided".
        timestamp: ISO 8601 UTC string. Auto-generated if None.

    Returns:
        dict with all 26 history record fields. No file I/O is performed.
    """
```

---

## Three Attribution Source Mappings

| Source | learn.sh path | api.py path |
|--------|--------------|-------------|
| MCP (Tier 2) | Not applicable (learn.sh does not receive MCP calls) | `step_actuals_mcp` = merged accumulator + call-time `step_actuals` |
| Sidecar | `step_actuals_sidecar` = output of `sum_session_by_agent()` in `STEP_ACTUALS_JSON` | Not applicable (api.py has no JSONL access) |
| Proportional | Both `step_actuals_mcp` and `step_actuals_sidecar` are None | Both are None or both are empty |

The `attribution_method` value `"sidecar"` only ever appears when learn.sh is the caller. The value `"mcp"` only ever appears when `report_session()` in api.py is the caller. This is a consequence of the architecture, not a protocol restriction.

---

## Rollback Notes

- All changes are additive or in-place replacements with no data migrations.
- `session_recorder.py` is a new file — deleting it rolls back Change 1.
- The learn.sh RECORD block replacement (Change 3) can be reverted by restoring lines 151–240 from git. No history data is written in a different format — records produced by the new path are byte-identical to records produced by the old path for the same inputs (verified by equivalence tests).
- `report_session` in api.py was a stub returning `{"_stub": True}` before this change. No existing tests assert on the stub return value being present in production records. If Change 2 is reverted, the stub behavior resumes.
- `history.jsonl` records written during the new implementation are schema-compatible with all existing readers. Rollback does not require purging history data.
- `active-estimate.json` and `{hash}-step-accumulator.json` lifecycle: both are deleted by `report_session` after successful recording — this cleanup is new behavior (the stub did not delete them). If tests depended on these files persisting after `report_session`, they may need adjustment. The test suite for Change 6 accounts for this.
