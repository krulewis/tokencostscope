# Implementation Plan: US-1c.02 — `report_step_cost` MCP Tool (absorbs US-1c.06)

*Engineer Agent — Initial Plan*
*Date: 2026-03-26*
*Input: phase-1c-attribution-stories.md (US-1c.02, US-1c.06), attribution-protocol.md*

---

## Overview

This story implements two tightly coupled pieces:

1. **US-1c.06 — `compute_cost_from_usage()`**: Extract a framework-agnostic cost computation function from `compute_line_cost()` in `sum-session-tokens.py`, and add it to `src/tokencast/pricing.py`. This is the seam that lets MCP callers compute cost from raw token counts without knowing the Claude Code JSONL schema.

2. **US-1c.02 — `report_step_cost` tool**: New MCP tool + public API function that accumulates per-step costs during a session. Costs persist to disk via atomic rename to `calibration/{hash}-step-accumulator.json`. The tool reads/resolves canonical step names, computes cost from tokens when needed, and returns the per-protocol response shape.

**Approach:**
- `compute_cost_from_usage()` lives in `src/tokencast/pricing.py` (already contains MODEL_PRICES, STEP_MODEL_MAP, and model resolution logic — the natural home for cost arithmetic).
- `DEFAULT_AGENT_TO_STEP` lives in `scripts/sum-session-tokens.py` and is duplicated into a new `src/tokencast/step_names.py` module so the MCP server can import it without depending on the scripts/ directory.
- `compute_line_cost()` in `sum-session-tokens.py` is refactored to delegate to `compute_cost_from_usage()` — behavior unchanged, no existing tests broken.
- Step accumulator state is held in a module-level dict (`_step_accumulator: dict[str, float]`) inside `src/tokencast/api.py` and persisted to `calibration/{hash}-step-accumulator.json` via atomic rename after every write.
- The MCP tool handler follows the exact pattern of existing handlers: `REPORT_STEP_COST_SCHEMA` constant + `async def handle_report_step_cost(params, config)` function.
- `server.py` registers the new tool in the dispatch table and tool list.

---

## Changes

### Change 1: Add `compute_cost_from_usage()` to `src/tokencast/pricing.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/pricing.py
Lines: new additions after line 58 (end of file)
Parallelism: independent
Description: Add the framework-agnostic cost computation function. Also adds DEFAULT_MODEL
  constant. Both are needed by report_step_cost's token-to-cost conversion path.
```

**Details:**

- Add `DEFAULT_MODEL: str = "claude-sonnet-4-6"` constant after `CACHE_HIT_RATES`.
- Add function `compute_cost_from_usage(usage: dict, model: str) -> float`:
  - `usage` uses the **protocol field names** (`tokens_in`, `tokens_out`, `tokens_cache_read`, `tokens_cache_write`) — not JSONL names.
  - Model string matching: iterate `MODEL_PRICES` keys; use `known in model` partial match (mirrors `compute_line_cost()` lines 87-89 in sum-session-tokens.py). If no match, use `DEFAULT_MODEL`.
  - Formula:
    ```python
    prices = MODEL_PRICES.get(model_key, MODEL_PRICES[DEFAULT_MODEL])
    cost = (
        usage.get("tokens_in", 0)          * prices["input"]
      + usage.get("tokens_cache_read", 0)  * prices["cache_read"]
      + usage.get("tokens_cache_write", 0) * prices["cache_write"]
      + usage.get("tokens_out", 0)         * prices["output"]
    ) / 1_000_000
    return cost
    ```
  - Returns `float` (never raises on valid numeric input; negative values are not validated here — validation is the caller's responsibility per protocol Section 8).
  - Docstring must document the protocol field names and note that `model` should be a full model ID string; partial matching is applied internally.

**Function signature:**
```python
def compute_cost_from_usage(usage: dict, model: str = DEFAULT_MODEL) -> float:
```

---

### Change 2: Add `src/tokencast/step_names.py` (new file)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/step_names.py
Lines: new file
Parallelism: independent
Description: Canonical step name resolution for the MCP server. Duplicates
  DEFAULT_AGENT_TO_STEP from sum-session-tokens.py into an importable module.
  Adds load_agent_map() for reading calibration/agent-map.json overrides.
  Adds resolve_step_name() as the single entry point for name canonicalization.
```

**Details:**

- `DEFAULT_AGENT_TO_STEP: dict[str, str]` — exact copy of the same dict from `scripts/sum-session-tokens.py` lines 52-67. Keys are alias strings (lowercase, with hyphens and underscores); values are canonical step names.

- `CANONICAL_STEP_NAMES: set[str]` — the set of values from `DEFAULT_AGENT_TO_STEP` (the canonical names themselves). Used for the PR Review Loop warning check.

- `PR_REVIEW_LOOP_NAME: str = "PR Review Loop"` — the derived aggregate step name that generates a warning if reported.

- `load_agent_map(calibration_dir: str | Path) -> dict[str, str]`:
  - Reads `calibration_dir / "agent-map.json"` if it exists; returns empty dict if absent or unreadable.
  - Returns the JSON dict (caller is responsible for merge logic).
  - Fail-silent on any exception (returns `{}`).

- `resolve_step_name(raw_name: str, calibration_dir: str | Path | None = None) -> tuple[str, str | None]`:
  - Returns `(canonical_name, warning_or_None)`.
  - Resolution order per protocol Section 9:
    1. Load `agent-map.json` from `calibration_dir` (if provided) → merge with `DEFAULT_AGENT_TO_STEP`; config file wins for conflicting keys.
    2. Check if `raw_name.strip().lower()` matches any alias key in the merged map; if yes, return the canonical name.
    3. Check if `raw_name` is already a canonical step name (direct match in values set); if yes, return it unchanged.
    4. If `raw_name == PR_REVIEW_LOOP_NAME` (after strip), return `(raw_name, "pr_review_loop_is_derived")`.
    5. Return `(raw_name, None)` — unknown names accepted as-is per protocol.
  - The alias lookup is case-insensitive on the key side (normalize to lowercase for lookup), but the returned canonical name preserves its original casing from `DEFAULT_AGENT_TO_STEP` values.

---

### Change 3: Add `report_step_cost` to `src/tokencast/api.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/api.py
Lines: new additions (append after report_session — entire file is ~58 lines, add after line 58)
Parallelism: depends-on: Change 1 (compute_cost_from_usage), Change 2 (step_names)
Description: Public API function for report_step_cost. Manages the in-memory
  step accumulator dict. Handles validation, cost computation, atomic-rename
  persistence, and response construction.
```

**Details:**

**Module-level state:**

```python
# In-memory step cost accumulator. Maps canonical step_name -> cumulative cost.
# Reset when estimate_cost is called or report_session clears it.
_step_accumulator: dict[str, float] = {}

# Path to the active accumulator file on disk (set when active estimate is loaded).
# None means no active estimate.
_accumulator_file_path: Path | None = None
```

These module-level variables persist for the lifetime of the MCP server process. `estimate_cost` (when implemented in a later story) must reset both. For this story, `estimate_cost` remains a stub — the accumulator starts empty at server startup.

**Helper: `_compute_accumulator_hash(active_estimate_path: Path) -> str`**:
- `hashlib.md5(str(active_estimate_path).encode()).hexdigest()[:12]`
- Returns the 12-char hash prefix used in the filename, matching the pattern from `agent-hook.sh`.

**Helper: `_get_accumulator_path(calibration_dir: Path) -> Path | None`**:
- Checks if `calibration_dir / "active-estimate.json"` exists.
- If yes, computes hash and returns `calibration_dir / f"{hash}-step-accumulator.json"`.
- If no, returns `None` (no active estimate).

**Helper: `_load_accumulator(path: Path) -> dict[str, float]`**:
- Reads JSON from `path` if it exists; returns `data["steps"]` (a `dict[str, float]`).
- Returns `{}` on any read error or missing `"steps"` key.

**Helper: `_save_accumulator(path: Path, steps: dict[str, float]) -> None`**:
- Atomic rename pattern:
  ```python
  tmp_path = path.with_suffix(".tmp")
  payload = {
      "attribution_protocol_version": 1,
      "steps": steps,
      "last_updated": datetime.utcnow().isoformat() + "Z",
  }
  tmp_path.write_text(json.dumps(payload, indent=2))
  os.replace(tmp_path, path)
  ```
- Uses `os.replace()` for atomic rename.
- Parent directory is `calibration_dir` which is guaranteed to exist by `config.ensure_dirs()`.

**Main function `report_step_cost(params: dict, calibration_dir: Path | None = None) -> dict`**:

Signature:
```python
def report_step_cost(params: dict, calibration_dir: Path | None = None) -> dict:
```

Logic:

1. **Validate `step_name`**:
   - Required field; raise `ValueError("missing_step_name")` if absent.
   - Strip whitespace; if empty after strip, return error dict `{"error": "invalid_step_name", "message": "step_name must be a non-empty string."}` (not raise — tool handlers return error dicts per protocol).
   - Note: the MCP handler converts error dicts to `ValueError` for the server's error path, or returns them as content. See Change 4 for the handler's approach.

2. **Validate numeric fields**:
   - `cost`: if present, must be `>= 0`; else return `{"error": "invalid_cost", ...}`.
   - `tokens_in`, `tokens_out`, `tokens_cache_read`, `tokens_cache_write`: each must be `>= 0` if present; else return `{"error": "invalid_tokens", "field": "<name>", ...}`.

3. **Check active estimate**:
   - `accumulator_path = _get_accumulator_path(calibration_dir)` where `calibration_dir` defaults to `Path.home() / ".tokencast" / "calibration"` if `None`.
   - If `accumulator_path is None` (no `active-estimate.json`): return `{"error": "no_active_estimate", "message": "Call estimate_cost before reporting step costs."}`.
   - **Stale accumulator check**: if `active-estimate.json` is absent but accumulator file exists, silently discard. (This case is already covered by `_get_accumulator_path` returning `None` when `active-estimate.json` is absent.)

4. **Resolve canonical step name**:
   - `canonical_name, warning = resolve_step_name(raw_name, calibration_dir)`

5. **Compute cost for this call**:
   - If `cost` is in params and is not `None`: `cost_this_call = float(params["cost"])`.
   - Else if any token field is present and non-zero: build `usage` dict from protocol fields, call `compute_cost_from_usage(usage, model)` where `model = params.get("model", STEP_MODEL_MAP.get(canonical_name, DEFAULT_MODEL))`.
   - Else: `cost_this_call = 0.0`. Set warning to `"No cost or token data provided; recorded 0.0"` (may overwrite the step-name warning; append both if both are set — join with "; ").

6. **Load accumulator from disk, accumulate, persist**:
   - `steps = _load_accumulator(accumulator_path)` — this handles the case where the server restarted mid-session; disk state is authoritative.
   - `steps[canonical_name] = steps.get(canonical_name, 0.0) + cost_this_call`
   - `_save_accumulator(accumulator_path, steps)`
   - Also update module-level `_step_accumulator` to match for in-memory consistency.

7. **Build response**:
   ```python
   {
       "attribution_protocol_version": 1,
       "step_name": canonical_name,
       "cost_this_call": cost_this_call,
       "cumulative_step_cost": steps[canonical_name],
       "total_session_accumulated": sum(steps.values()),
       # "warning": ... (only if warning is non-None)
   }
   ```

**Imports needed** (add to top of api.py):
```python
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from tokencast.pricing import compute_cost_from_usage, DEFAULT_MODEL, STEP_MODEL_MAP
from tokencast.step_names import resolve_step_name
```

---

### Change 4: Create `src/tokencast_mcp/tools/report_step_cost.py` (new file)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/tools/report_step_cost.py
Lines: new file
Parallelism: depends-on: Change 3 (api.report_step_cost)
Description: MCP tool handler for report_step_cost. Follows the exact pattern
  of existing handlers (SCHEMA constant + async handle_* function). Wires params
  and config.calibration_dir to the public API function.
```

**Details:**

`REPORT_STEP_COST_SCHEMA: dict` — JSON Schema object per protocol Section 4:

```python
REPORT_STEP_COST_SCHEMA: dict = {
    "type": "object",
    "required": ["step_name"],
    "properties": {
        "step_name": {"type": "string"},
        "cost": {"type": "number", "minimum": 0},
        "tokens_in": {"type": "integer", "minimum": 0},
        "tokens_out": {"type": "integer", "minimum": 0},
        "tokens_cache_read": {"type": "integer", "minimum": 0},
        "tokens_cache_write": {"type": "integer", "minimum": 0},
        "model": {"type": "string"},
    },
    "additionalProperties": False,
}
```

`async def handle_report_step_cost(params: dict, config: ServerConfig) -> dict`:

1. Call `_api_report_step_cost(params, calibration_dir=config.calibration_dir)`.
2. If result contains `"error"` key: raise `ValueError(result["message"])` — this causes `server.py`'s dispatch layer to return a `CallToolResult(isError=True)` with the message.
3. Otherwise return the result dict directly (the server serializes it to JSON).

Imports:
```python
import sys
from tokencast_mcp.config import ServerConfig
from tokencast.api import report_step_cost as _api_report_step_cost
```

---

### Change 5: Register `report_step_cost` in `src/tokencast_mcp/server.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/server.py
Lines: lines 22-59 (imports and _DISPATCH dict), and lines 79-109 (list_tools)
Parallelism: depends-on: Change 4 (report_step_cost handler)
Description: Import the new handler and schema, add to _DISPATCH, add Tool entry
  to list_tools(). Tool count in list_tools goes from 4 to 5.
```

**Details:**

Add import after line 37:
```python
from tokencast_mcp.tools.report_step_cost import (
    REPORT_STEP_COST_SCHEMA,
    handle_report_step_cost,
)
```

Add to `_DISPATCH` dict (after `"report_session"` entry):
```python
"report_step_cost": handle_report_step_cost,
```

Add to `list_tools()` return value:
```python
Tool(
    name="report_step_cost",
    description=(
        "Record the cost of a completed pipeline step. "
        "Costs accumulate per step and are flushed when report_session is called."
    ),
    inputSchema=REPORT_STEP_COST_SCHEMA,
),
```

---

### Change 6: Refactor `compute_line_cost()` in `scripts/sum-session-tokens.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/sum-session-tokens.py
Lines: lines 70-102 (compute_line_cost function)
Parallelism: independent (does not depend on Changes 1-5; only shares the formula)
Description: Refactor compute_line_cost() to delegate to compute_cost_from_usage()
  from src/tokencast/pricing.py. Behavior is identical — existing tests must pass
  without modification.
```

**Details:**

- Add `src/` to `sys.path` at the top of the script (after existing imports) so `from tokencast.pricing import compute_cost_from_usage, MODEL_PRICES, DEFAULT_MODEL` works when the script is run directly from the repo root. Use a path relative to `__file__`:
  ```python
  import sys
  from pathlib import Path
  _SRC = Path(__file__).resolve().parent.parent / "src"
  if str(_SRC) not in sys.path:
      sys.path.insert(0, str(_SRC))
  from tokencast.pricing import compute_cost_from_usage as _compute_cost_from_usage
  ```

- The existing `PRICES` dict at lines 24-43 and `DEFAULT_MODEL` at line 46 are kept as-is for backward compatibility (other parts of the script still use `PRICES` directly). They do NOT need to be removed — the script's internal usage of `PRICES` for direct dict lookups is a separate concern from the refactored function.

- Refactor `compute_line_cost()` body:
  ```python
  def compute_line_cost(obj: dict) -> float:
      if obj.get("type") != "assistant":
          return 0.0
      msg = obj.get("message", {})
      usage_raw = msg.get("usage")
      if not usage_raw:
          return 0.0
      model = msg.get("model", "")
      if not model or model == "<synthetic>":
          return 0.0
      # Map JSONL field names to protocol field names for compute_cost_from_usage
      usage = {
          "tokens_in":          usage_raw.get("input_tokens", 0),
          "tokens_cache_read":  usage_raw.get("cache_read_input_tokens", 0),
          "tokens_cache_write": usage_raw.get("cache_creation_input_tokens", 0),
          "tokens_out":         usage_raw.get("output_tokens", 0),
      }
      return _compute_cost_from_usage(usage, model)
  ```

- No changes to the function signature or its callers within the file.

---

### Change 7: Export `report_step_cost` from `src/tokencast/__init__.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast/__init__.py
Lines: lines 7-12 (import block)
Parallelism: depends-on: Change 3 (api.report_step_cost)
Description: Add report_step_cost to the public API exports so
  `from tokencast import report_step_cost` works (required per
  protocol Section 10 Example B constraint).
```

**Details:**

Change the import from `tokencast.api` to include `report_step_cost`:
```python
from tokencast.api import (  # noqa: F401
    estimate_cost,
    get_calibration_status,
    get_cost_history,
    report_session,
    report_step_cost,
)
```

---

## Dependency Order

The following order must be respected for dependent changes:

```
Changes 1, 2, 6  — independent, can run in parallel
      |
      v
Change 3 (api.py) — depends on Changes 1 and 2
Change 7 (__init__.py) — depends on Change 3
      |
      v
Change 4 (MCP handler) — depends on Change 3
      |
      v
Change 5 (server.py) — depends on Change 4
```

Changes 1, 2, and 6 can be implemented simultaneously. Changes 3 and 7 follow. Change 4 follows Change 3. Change 5 follows Change 4.

---

## Test Strategy

### New test file: `tests/test_report_step_cost.py`

Run with: `/usr/bin/python3 -m pytest tests/test_report_step_cost.py -v`

This file is independent of Changes 4 and 5 (it tests `pricing.py`, `step_names.py`, and `api.report_step_cost` directly without MCP machinery), so it can be written in parallel with Changes 1-3.

#### Class `TestComputeCostFromUsage`

Tests for `src/tokencast/pricing.py:compute_cost_from_usage()`.

- `test_all_four_token_types_sonnet`: Verify the worked example from protocol Section 10 Example E: `tokens_in=150000, tokens_out=25000, tokens_cache_read=80000, tokens_cache_write=20000, model="claude-sonnet-4-6"` → `0.924`. Use `pytest.approx`.
- `test_only_input_tokens`: `tokens_in=1_000_000, model=sonnet` → `3.00`.
- `test_only_output_tokens`: `tokens_out=1_000_000, model=sonnet` → `15.00`.
- `test_only_cache_read`: `tokens_cache_read=1_000_000, model=sonnet` → `0.30`.
- `test_only_cache_write`: `tokens_cache_write=1_000_000, model=sonnet` → `3.75`.
- `test_all_zeros_returns_zero`: empty dict → `0.0`.
- `test_opus_model_pricing`: `tokens_in=1_000_000, model="claude-opus-4-6"` → `15.00` (Opus input rate). Note: `pricing.py` has Opus input at `5.00` not `15.00` (Opus-3 was 15; current `claude-opus-4-6` in pricing.py is `5.00`). Test must use the value from `MODEL_PRICES["claude-opus-4-6"]["input"]` — do not hardcode the dollar value, assert against the formula result.
- `test_haiku_model_pricing`: `tokens_in=1_000_000, model="claude-haiku-4-5"` → from `MODEL_PRICES`.
- `test_partial_model_string_match`: `model="claude-sonnet"` (without version suffix) should match `claude-sonnet-4-6` row.
- `test_unknown_model_falls_back_to_default`: `model="gpt-4"` → uses DEFAULT_MODEL prices (Sonnet).
- `test_default_model_arg`: calling without `model` arg uses `DEFAULT_MODEL` prices.
- `test_missing_fields_default_to_zero`: `usage={"tokens_out": 1000}` → only output tokens charged.
- `test_protocol_field_names_not_jsonl_names`: `usage={"input_tokens": 1000}` → `0.0` (wrong field name, protocol uses `tokens_in`).

#### Class `TestResolveStepName`

Tests for `src/tokencast/step_names.py:resolve_step_name()`.

- `test_canonical_alias_lowercase`: `"researcher"` → `("Research Agent", None)`.
- `test_canonical_alias_with_underscore`: `"staff_reviewer"` → `("Staff Review", None)`.
- `test_canonical_alias_with_hyphen`: `"docs-updater"` → `("Docs Updater", None)`.
- `test_canonical_name_direct`: `"Research Agent"` (already canonical) → `("Research Agent", None)`.
- `test_unknown_name_accepted_as_is`: `"my-custom-step"` → `("my-custom-step", None)`.
- `test_pr_review_loop_gets_warning`: `"PR Review Loop"` → `("PR Review Loop", "pr_review_loop_is_derived")`.
- `test_whitespace_stripped_for_lookup`: `"  researcher  "` — should resolve if strip+lower matches; but note the caller validates non-empty before calling resolve_step_name, so behavior on whitespace-only is tested in api tests.
- `test_agent_map_override_wins`: create a temp dir with `agent-map.json` containing `{"my-alias": "Custom Step"}`. `resolve_step_name("my-alias", tmp_path)` → `("Custom Step", None)`.
- `test_agent_map_override_conflicts_with_default`: agent-map has `{"researcher": "MyResearch"}`. `resolve_step_name("researcher", tmp_path)` → `("MyResearch", None)` (config wins).
- `test_missing_agent_map_file_handled_gracefully`: `calibration_dir` that does not exist → returns raw name without error.
- `test_malformed_agent_map_json_handled_gracefully`: `agent-map.json` with invalid JSON → returns raw name without error (fail-silent).
- `test_no_calibration_dir_uses_defaults_only`: `calibration_dir=None` → still resolves standard aliases.

#### Class `TestReportStepCostApi`

Tests for `src/tokencast/api.py:report_step_cost()`.

All tests that touch disk use `tmp_path` pytest fixture. Tests set up a fake `active-estimate.json` in `tmp_path / "calibration"` to simulate an active estimate.

Helper:
```python
def _make_active_estimate(cal_dir: Path) -> None:
    cal_dir.mkdir(parents=True, exist_ok=True)
    (cal_dir / "active-estimate.json").write_text('{"version": "0.1.0"}')
```

- `test_no_active_estimate_returns_error`: `calibration_dir` exists but no `active-estimate.json` → result has `"error": "no_active_estimate"`.
- `test_whitespace_only_step_name_returns_error`: `step_name="   "` with active estimate → `"error": "invalid_step_name"`.
- `test_negative_cost_returns_error`: `cost=-1.0` → `"error": "invalid_cost"`.
- `test_negative_token_count_returns_error`: `tokens_in=-100` → `"error": "invalid_tokens"`.
- `test_basic_cost_call_returns_correct_shape`: call with `step_name="Research Agent", cost=1.20`, active estimate present → result has `attribution_protocol_version=1, step_name, cost_this_call, cumulative_step_cost, total_session_accumulated`.
- `test_attribution_protocol_version_is_1`: response has `"attribution_protocol_version": 1`.
- `test_cost_takes_precedence_over_tokens`: call with both `cost=1.0` and `tokens_in=1_000_000` → `cost_this_call == 1.0` (not the token-derived amount).
- `test_token_cost_computed_correctly`: call with `tokens_in=1_000_000, model="claude-sonnet-4-6"` (no `cost`) → `cost_this_call == pytest.approx(3.0)`.
- `test_no_cost_no_tokens_records_zero_with_warning`: call with only `step_name` → `cost_this_call == 0.0`, response has `"warning"` key.
- `test_accumulation_same_step`: call twice with same step, `cost=1.0` each time → second call's `cumulative_step_cost == 2.0`.
- `test_accumulation_different_steps`: call with "Research Agent" (cost=1.0) and "Implementation" (cost=2.0) → `total_session_accumulated == 3.0` on second call.
- `test_accumulator_file_created_on_disk`: after call, `calibration_dir / "{hash}-step-accumulator.json"` exists.
- `test_accumulator_file_schema`: accumulator JSON has `attribution_protocol_version`, `steps`, `last_updated` keys; `steps` is a dict.
- `test_accumulator_file_persists_across_calls`: build a new accumulator file manually with prior state → new call loads and adds to it (simulates server restart between calls).
- `test_accumulator_atomic_rename_no_tmp_file_left`: after call, no `.tmp` file exists in calibration dir.
- `test_step_name_alias_resolved`: call with `step_name="researcher"` → `step_name` in response is `"Research Agent"`.
- `test_pr_review_loop_warning`: call with `step_name="PR Review Loop"` → response has `warning` containing `"pr_review_loop_is_derived"`.
- `test_zero_cost_no_warning_when_cost_explicitly_zero`: calling with `cost=0.0` explicitly does NOT produce the "no cost or token data" warning (that warning is only for when no cost/tokens fields are provided at all).

#### Class `TestReportStepCostMcpHandler`

Tests for `src/tokencast_mcp/tools/report_step_cost.py`. These require `mcp` importable — use `pytest.importorskip("mcp")` at module level.

- `test_schema_type_is_object`: `REPORT_STEP_COST_SCHEMA["type"] == "object"`.
- `test_schema_required_fields`: `"step_name"` is in `required`.
- `test_schema_step_name_property_exists`: `"step_name"` in `properties`.
- `test_schema_cost_minimum_zero`: `properties["cost"]["minimum"] == 0`.
- `test_schema_additional_properties_false`: `"additionalProperties": False`.
- `test_handler_no_active_estimate_raises_value_error`: call with `step_name="Research"`, calibration dir has no `active-estimate.json` → `ValueError` raised.
- `test_handler_whitespace_step_name_raises_value_error`: `step_name="   "` → `ValueError`.
- `test_handler_valid_call_returns_correct_shape`: with active estimate present, `step_name="Research Agent", cost=1.0` → result dict has `attribution_protocol_version`, `step_name`, `cost_this_call`, `cumulative_step_cost`, `total_session_accumulated`.
- `test_handler_passes_calibration_dir_from_config`: verify via accumulator file location (uses `config.calibration_dir` not a hardcoded path).

#### Updates to `tests/test_mcp_scaffold.py`

- `TestToolSchemas.test_all_tools_have_schemas_of_type_object`: currently asserts 4 schemas — update to also import `REPORT_STEP_COST_SCHEMA` and include it.
- `TestServerBuildAndDispatch.test_tools_list_returns_four_tools`: update assertion to `len(tools) == 5` and add `"report_step_cost"` to the expected `tool_names` set.
- `TestProtocolSmoke.test_tools_list_via_stdio`: update `len(tools) == 4` to `== 5` and add `"report_step_cost"` to expected set.

#### Updates to `tests/test_data_modules_drift.py` (if it exists)

Check if this test file validates `PRICES` dict consistency between `sum-session-tokens.py` and `pricing.py`. After Change 6, `sum-session-tokens.py` delegates cost computation to `pricing.py` so drift between the two `PRICES` dicts becomes irrelevant for cost correctness. The test may still be valuable as a consistency check. Verify the test still passes after Change 6 by checking whether it reads `PRICES` directly or tests computed costs.

### What existing tests might break

- `tests/test_mcp_scaffold.py` — tool count assertions (4 → 5). Requires edits per above.
- Tests that import `compute_line_cost` from `sum-session-tokens.py` — behavior is unchanged so they should pass, but any test that monkeypatches `PRICES` inside `sum-session-tokens.py` will no longer affect the cost calculation (which now delegates to `pricing.py`). Check `tests/test_data_modules_drift.py` and `tests/test_file_size_awareness.py` for any such patching.

### Edge cases that must be covered

- Accumulator file exists from a prior session with same hash (stale, but `active-estimate.json` also exists — this would mean the file was not properly cleaned up). The `estimate_cost` stub (future story) will clear it; for now the accumulator is simply loaded and extended. Document this as a known limitation in the test.
- `calibration_dir` does not exist on disk when `report_step_cost` is called: `_get_accumulator_path()` will fail to find `active-estimate.json` (directory missing → file missing) and return `no_active_estimate`. Test: `test_no_active_estimate_returns_error`.
- `agent-map.json` has a key that maps to itself (canonical name as alias): `{"Research Agent": "Research Agent"}` — no infinite loop, just an identity mapping.
- Both `cost=0.0` and token fields provided: `cost` wins, returns `0.0` with no warning (explicit zero is valid).

### Tests that can run in parallel with implementation

`TestComputeCostFromUsage` and `TestResolveStepName` can be written immediately — their interfaces are fully defined in this plan. `TestReportStepCostApi` and `TestReportStepCostMcpHandler` can also be written as soon as Changes 1-3 are complete (interfaces are defined above).

---

## Rollback Notes

- All new files (`step_names.py`, `report_step_cost.py`) can be deleted cleanly.
- `api.py` additions (the `report_step_cost` function and module-level dicts) are append-only; the existing stubs are untouched.
- `pricing.py` additions (`DEFAULT_MODEL`, `compute_cost_from_usage`) are append-only.
- `__init__.py` change: remove `report_step_cost` from the import list.
- `server.py` changes: remove the import block and the `"report_step_cost"` entries from `_DISPATCH` and `list_tools()`.
- `sum-session-tokens.py` refactor: revert `compute_line_cost()` body to the original and remove the `sys.path` insertion.
- No database migrations or data format changes to existing files. The `{hash}-step-accumulator.json` is a new file format in `calibration/`; no existing files are modified.
- Disk state: any `{hash}-step-accumulator.json` files written during testing can be deleted from the `calibration/` directory without consequence — they are ephemeral session state.
