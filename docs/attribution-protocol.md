# tokencast Attribution Protocol

version: 1
status: draft
date: 2026-03-26
blocks: US-1c.02, US-1c.03, US-1c.04

This document specifies the framework-agnostic protocol for reporting cost data to tokencast via MCP tool calls. Clients that cannot produce Claude Code JSONL (e.g., Cursor, VS Code, CI pipelines) use this protocol to produce the same calibration records as Claude Code users.

---

## Section 2 — Protocol Version

The field `attribution_protocol_version` identifies the wire protocol version. Its current value is `1`.

This field appears in:
- Every `report_step_cost` tool response
- Every `report_session` tool response
- The on-disk step-cost accumulator file (`calibration/{hash}-step-accumulator.json`, see Section 6)

**Versioning contract:**

Minor additions (new optional fields in request or response schemas) do not require a version bump — clients that ignore unknown fields will continue to work. Removing or renaming required fields, or changing the type of any existing field, requires incrementing to `2`. Implementers must check this field and reject with a clear error if the version number is higher than they support.

---

## Section 3 — Session Lifecycle

The protocol has two tiers. Clients choose the tier that fits their capability.

### Tier 1 (session-only)

```
estimate_cost(plan_params)          [required — opens session state]
      |
      v
  [work happens in client framework]
      |
      v
report_session(actual_cost=X)       [required — closes and records]
```

### Tier 2 (step-level)

```
estimate_cost(plan_params)          [required]
      |
      v
report_step_cost(step_name, cost)   [optional, repeatable]
report_step_cost(step_name, cost)   [multiple calls for same step accumulate]
      |
      v
report_session(actual_cost=X)       [required — flushes accumulated steps, records, clears state]
```

### Behavioral Rules

The following rules apply in both tiers:

1. `estimate_cost` resets any previously accumulated step costs. Starting a new session discards any step data from the previous one. A second `estimate_cost` call without an intervening `report_session` overwrites the active estimate and discards accumulated step costs. No warning is issued — this is a normal restart.
2. Multiple `report_step_cost` calls for the same `step_name` are **additive** — values sum, not replaced.
3. `report_session` clears all accumulated step costs after recording to history.
4. If `report_session` provides both `actual_cost` and a `step_actuals` dict, the call-time `step_actuals` dict is merged with any step costs accumulated via prior `report_step_cost` calls. For keys that appear in both, the call-time value takes precedence over the accumulated value.
5. `report_step_cost` called after `report_session` (with no new `estimate_cost` in between) returns an error (`no_active_estimate`).
6. `report_session` called without a preceding `estimate_cost` in the same server session: see Section 8 (Error Handling) for detailed behavior.

---

## Section 4 — Tool: `report_step_cost`

Records the cost of a single pipeline step. May be called multiple times for the same step; costs accumulate.

### Input Schema

```json
{
  "type": "object",
  "required": ["step_name"],
  "properties": {
    "step_name": {
      "type": "string",
      "description": "Pipeline step identifier. Canonical names (e.g. 'Research Agent', 'Implementation') map directly. Non-canonical names are accepted and stored as-is — they will appear in history records under step_actuals with the raw name provided."
    },
    "cost": {
      "type": "number",
      "minimum": 0,
      "description": "Dollar cost for this step call. If provided, used directly. Takes precedence over token-derived cost when both are given."
    },
    "tokens_in": {
      "type": "integer",
      "minimum": 0,
      "description": "Input tokens (not cache hits). Used to compute cost when 'cost' is absent."
    },
    "tokens_out": {
      "type": "integer",
      "minimum": 0,
      "description": "Output tokens. Used to compute cost when 'cost' is absent."
    },
    "tokens_cache_read": {
      "type": "integer",
      "minimum": 0,
      "description": "Cache-read input tokens. Used to compute cost when 'cost' is absent."
    },
    "tokens_cache_write": {
      "type": "integer",
      "minimum": 0,
      "description": "Cache-creation (write) input tokens. Used to compute cost when 'cost' is absent."
    },
    "model": {
      "type": "string",
      "description": "Model identifier used for this step. Used for token-to-cost conversion. If absent and 'cost' is also absent, the server uses the model mapped to the step in references/pricing.md (Step->Model mapping), falling back to claude-sonnet-4-6."
    }
  }
}
```

### Validation Rules

- At least one of `cost` or any token count field must be present. If none are provided, the call reports zero cost, which is accepted but generates a warning in the response.
- `cost` and token fields may be provided together; `cost` wins.
- `step_name` must be a non-empty string. Whitespace-only strings are rejected with a validation error.
- All numeric fields must be non-negative. Negative values are rejected with a validation error.

### Output Schema

```json
{
  "type": "object",
  "required": [
    "attribution_protocol_version",
    "step_name",
    "cost_this_call",
    "cumulative_step_cost",
    "total_session_accumulated"
  ],
  "properties": {
    "attribution_protocol_version": {
      "type": "integer",
      "const": 1
    },
    "step_name": {
      "type": "string",
      "description": "Canonicalized step name. Alias resolution uses DEFAULT_AGENT_TO_STEP (hardcoded defaults) merged with calibration/agent-map.json (optional overrides). Config file wins for keys present in both. If no mapping found, equals the raw input step_name."
    },
    "cost_this_call": {
      "type": "number",
      "description": "Dollar cost recorded for this specific call."
    },
    "cumulative_step_cost": {
      "type": "number",
      "description": "Total accumulated cost for this step_name across all report_step_cost calls in the current session."
    },
    "total_session_accumulated": {
      "type": "number",
      "description": "Sum of all accumulated step costs across all steps in the current session."
    },
    "warning": {
      "type": "string",
      "description": "Present only when the call succeeds but has a non-fatal issue (e.g., zero cost reported, unknown step name)."
    }
  }
}
```

---

## Section 5 — Tool: `report_session`

Closes the session, merges any accumulated step costs, and writes a calibration history record.

### Input Schema

```json
{
  "type": "object",
  "required": ["actual_cost"],
  "properties": {
    "actual_cost": {
      "type": "number",
      "minimum": 0,
      "description": "Total dollar cost of the session (post-baseline). This is the primary attribution signal. Required."
    },
    "step_actuals": {
      "type": "object",
      "additionalProperties": {"type": "number"},
      "description": "Optional dict of step_name -> dollar_cost. Merged with any costs accumulated via report_step_cost. Call-time values take precedence for duplicate keys. Keys are step names (canonical or raw). Values are dollar amounts."
    },
    "turn_count": {
      "type": "integer",
      "minimum": 0,
      "description": "Number of billable turns in the session. Optional. Stored in the history record."
    },
    "review_cycles_actual": {
      "type": "integer",
      "minimum": 0,
      "description": "Number of PR review cycles that actually occurred. Optional. Used to calibrate the PR Review Loop cost model."
    }
  }
}
```

### Validation Rules

- `actual_cost` is required. No history record is written when `actual_cost <= 0.001`. The response will include a `warning` explaining no record was written.
- `step_actuals` values must be non-negative numbers. Negative values are rejected with a validation error.
- `review_cycles_actual` must be a non-negative integer.

### Output Schema

```json
{
  "type": "object",
  "required": [
    "attribution_protocol_version",
    "record_written",
    "attribution_method",
    "actual_cost"
  ],
  "properties": {
    "attribution_protocol_version": {
      "type": "integer",
      "const": 1
    },
    "record_written": {
      "type": "boolean",
      "description": "True if a history record was appended to history.jsonl."
    },
    "attribution_method": {
      "type": "string",
      "enum": ["mcp", "proportional"],
      "description": "'mcp' when step_actuals are present (either from report_step_cost calls or step_actuals input). 'proportional' when no step data is available."
    },
    "actual_cost": {
      "type": "number"
    },
    "step_actuals": {
      "type": ["object", "null"],
      "description": "The merged step_actuals dict that was stored. Null when attribution_method is 'proportional'."
    },
    "warning": {
      "type": "string",
      "description": "Present only when the call succeeds but something notable occurred (e.g., no active estimate found, no record written due to zero cost)."
    }
  }
}
```

---

## Section 6 — On-Disk Persistence for Accumulated Step Costs

Step costs accumulated across multiple `report_step_cost` calls are persisted to disk immediately after each call. This ensures no data is lost if the MCP server restarts between calls.

**File path:** `calibration/{hash}-step-accumulator.json` where `{hash}` is the first 12 characters of the MD5 of the `calibration/active-estimate.json` absolute path — the same hash used by `agent-hook.sh` for sidecar files (e.g., `{hash}-timeline.jsonl`). This naming avoids cross-project collisions when multiple tokencast-managed projects share a calibration directory.

**Written:** After each successful `report_step_cost` call, using an atomic rename pattern: write to a `.tmp` file, then call `os.replace()` to atomically overwrite the target. This prevents corrupt reads if the server is interrupted mid-write.

**Cleared:** When `report_session` completes successfully (the file is deleted), or when a new `estimate_cost` call is made (any existing accumulator file for the current hash is deleted before opening the new session).

**Schema:**

```json
{
  "attribution_protocol_version": 1,
  "steps": {
    "Research Agent": 1.20,
    "Implementation": 4.50
  },
  "last_updated": "2026-03-26T14:00:00Z"
}
```

**Field notes:**

- `steps`: Dict of step name (canonicalized) to accumulated float cost. Costs are additive across `report_step_cost` calls within a session.
- `last_updated`: ISO 8601 UTC timestamp of the last write. Used for stale-file detection: if `active-estimate.json` does not exist and the accumulator file is older than 48 hours, it is discarded. Note that the hash in the filename detects cross-directory conflicts (two projects whose `active-estimate.json` paths happen to share the same 12-char MD5 prefix), not same-directory sequential sessions — sequential session reuse is prevented by `estimate_cost` deleting the previous accumulator on startup.
- **Stale-file rule**: If `active-estimate.json` does not exist and the accumulator file is present, the accumulator is silently discarded — no error is returned to the client. This mirrors the continuation-session behavior in `learn.sh`.

---

## Section 7 — Token-to-Cost Conversion

When `cost` is absent from a `report_step_cost` call but token counts are provided, the server computes cost using the same formula as `sum-session-tokens.py:compute_line_cost()`.

**Field name mapping:** The protocol field names differ from the Claude Code JSONL field names used internally by `compute_line_cost()`:

| Protocol field | Claude Code JSONL field |
|----------------|------------------------|
| `tokens_in` | `input_tokens` |
| `tokens_out` | `output_tokens` |
| `tokens_cache_read` | `cache_read_input_tokens` |
| `tokens_cache_write` | `cache_creation_input_tokens` |

The server maps protocol fields to JSONL fields before invoking the shared cost formula.

```
cost = (tokens_in       * price_input
      + tokens_cache_read  * price_cache_read
      + tokens_cache_write * price_cache_write
      + tokens_out      * price_output) / 1_000_000
```

Prices are read from `references/pricing.md` via the shared `tokencast.pricing` module.

For reference, Sonnet pricing (the default fallback model) as of 2026-03-04:

| Token type     | Price per million |
|----------------|-------------------|
| input          | $3.00             |
| cache_read     | $0.30             |
| cache_write    | $3.75             |
| output         | $15.00            |

### Model Resolution Order

When determining which model's prices to use for a token-based computation:

1. Use the `model` field from the `report_step_cost` call if provided.
2. Look up the step's model from the `Pipeline Step → Model Mapping` table in `references/pricing.md`.
3. Fall back to `claude-sonnet-4-6`.

**Model string matching:** Partial match is used — for example, `"claude-sonnet"` matches the `"claude-sonnet-4-6"` pricing row. This is the same logic as `tokencast/pricing.py:compute_cost_from_usage()`.

**Unknown model:** If the model string does not match any known model (even partially), the server uses `DEFAULT_MODEL = "claude-sonnet-4-6"` pricing and includes a warning in the response.

**Zero-token call:** If `cost` is absent and all token counts are absent or zero, the call records a cost of `0.0`. The response includes `warning: "No cost or token data provided; recorded 0.0"`. The call is not rejected — the client may legitimately want to mark a step as started before token counts are available.

---

## Section 8 — Error Handling

Each error scenario, the server behavior, and the response shape:

| Scenario | Server Behavior | Response |
|----------|-----------------|----------|
| `report_step_cost` called with no active estimate | Return error response | `{"error": "no_active_estimate", "message": "Call estimate_cost before reporting step costs."}` |
| `report_session` called with no active estimate | Attempt reconstitution from `last-estimate.md`; if that fails, write a minimal record. Warning included. | See detailed behavior below. |
| `report_step_cost` with `step_name` = whitespace-only string | Validation error | `{"error": "invalid_step_name", "message": "step_name must be a non-empty string."}` |
| `report_step_cost` with negative `cost` | Validation error | `{"error": "invalid_cost", "message": "cost must be >= 0."}` |
| `report_step_cost` with negative token count | Validation error | `{"error": "invalid_tokens", "message": "Token counts must be >= 0.", "field": "<field_name>"}` |
| `report_session` with `actual_cost = 0.0` | No record written, warning returned | `{"record_written": false, "warning": "actual_cost is 0.0; no calibration record written."}` |
| `report_session` with negative `actual_cost` | Validation error | `{"error": "invalid_cost", "message": "actual_cost must be >= 0."}` |
| `{hash}-step-accumulator.json` exists but `active-estimate.json` is absent (stale accumulator) | Silently discard the stale accumulator, proceed with no prior step costs | Include `warning: "stale_accumulator_discarded"` in response |

### `report_session` Without `estimate_cost` — Detailed Behavior

This is a valid Tier 1 use case for systems that report only session totals without running `estimate_cost` in-session (e.g., a developer who ran a session without tokencast active). The server proceeds as follows:

1. Look for `calibration/last-estimate.md` and attempt reconstitution using the same logic as the `learn.sh` continuation fallback.
2. If reconstitution succeeds, use the reconstituted estimate and proceed normally.
3. If reconstitution fails or `last-estimate.md` is absent or stale (older than 48 hours), write a minimal history record with `size: "unknown"`, `steps: []`, `expected_cost: 0`, and `attribution_method: "proportional"`. The `ratio` field is set to `0.0` for this case — division by a near-zero expected cost would produce a meaningless value.
4. Include `warning: "no_active_estimate"` in the response regardless of whether reconstitution succeeded.

---

## Section 9 — Canonical Step Names

The protocol accepts any non-empty `step_name`. The following are the canonical names recognized by the factor computation in `update-factors.py`. Non-canonical names are stored as-is and will appear in `step_actuals` but may not accumulate per-step calibration factors — they contribute only to the global factor.

| Canonical Name | Aliases (from DEFAULT_AGENT_TO_STEP) |
|----------------|--------------------------------------|
| Research Agent | researcher, research |
| Architect Agent | architect |
| Engineer Initial Plan | engineer-initial |
| Engineer Final Plan | engineer-final |
| Staff Review | staff-reviewer, staff_reviewer |
| Implementation | implementer, implement |
| QA | qa |
| Frontend Designer | frontend-designer, frontend_designer |
| Docs Updater | docs-updater, docs_updater |

Alias resolution uses `DEFAULT_AGENT_TO_STEP` (the hardcoded defaults dict in `sum-session-tokens.py`) merged with `calibration/agent-map.json` (optional overrides). The config file wins for any key present in both. Custom aliases can be added to `calibration/agent-map.json`. This is the same two-source merge performed by `_load_agent_map()` in `sum-session-tokens.py`.

**PR Review Loop exclusion:** The `PR Review Loop` step is not a valid `report_step_cost` target. It is a derived aggregate computed during estimation — not an individual reportable step. If a client attempts to report it, the server accepts the call (it is treated as an unknown-step name and stored as-is) but includes `warning: "pr_review_loop_is_derived"` in the response.

---

## Section 10 — Worked Examples

### Example A: Cursor Workflow (Tier 2 — Step-Level)

Scenario: A Cursor extension runs a tokencast-managed plan and reports each step cost as it finishes.

```python
# Step 1: Start session
estimate_cost({
    "plan": "Implement OAuth login for our Flask app",
    "size": "M",
    "files": 5,
    "complexity": "medium"
})
# -> Records active-estimate.json

# Step 2: Research completes
report_step_cost({
    "step_name": "Research Agent",
    "cost": 1.20
})
# -> Accumulates: {"Research Agent": 1.20}
# -> Response: {attribution_protocol_version: 1, step_name: "Research Agent",
#               cost_this_call: 1.20, cumulative_step_cost: 1.20,
#               total_session_accumulated: 1.20}

# Step 3: Implementation completes (using tokens instead of direct cost)
report_step_cost({
    "step_name": "Implementation",
    "tokens_in": 200000,
    "tokens_out": 30000,
    "model": "claude-sonnet-4-6"
})
# Server computes: (200000 * 3.00 + 30000 * 15.00) / 1_000_000 = $1.05
# -> Accumulates: {"Research Agent": 1.20, "Implementation": 1.05}

# Step 4: Session ends
report_session({
    "actual_cost": 3.75,
    "turn_count": 124
})
# -> Writes history record with:
#    attribution_method: "mcp"
#    step_actuals: {"Research Agent": 1.20, "Implementation": 1.05}
#    actual_cost: 3.75
# -> Clears {hash}-step-accumulator.json
# -> Response: {record_written: true, attribution_method: "mcp", actual_cost: 3.75, ...}
```

### Example B: CI/CD Pipeline (Tier 1 — Session-Only)

Scenario: A GitHub Actions workflow runs a plan and only knows the total cost at the end (no per-step breakdown).

```python
# Option 1: via MCP tool call (MCP client library in CI)
estimate_cost({"plan": "Update dependency pinning", "size": "S", "files": 2})
# [workflow runs]
report_session({"actual_cost": 0.85, "turn_count": 32})
# -> attribution_method: "proportional" (no step reports)

# Option 2: via Python import (no MCP layer needed in CI)
from tokencast import estimate_cost, report_session
estimate_cost({"plan": "Update dependency pinning", "size": "S", "files": 2})
# [workflow runs]
report_session({"actual_cost": 0.85, "turn_count": 32})
```

Note: The Python import path uses the same underlying functions as the MCP tools. The MCP tools are thin wrappers. CI users can skip MCP entirely.

**Import path constraint:** `from tokencast import estimate_cost, report_session` requires that both functions are exported from `tokencast/__init__.py`. This is a constraint on US-1b.08.

### Example C: Manual Entry (Tier 1 — Post-Hoc)

Scenario: Developer ran a session without tokencast active and wants to record the cost manually afterward.

```python
# No estimate_cost was called during the session.
# The developer reads the session cost from their Anthropic usage dashboard.
report_session({
    "actual_cost": 5.40,
    "turn_count": 201,
    "review_cycles_actual": 3
})
# Server behavior:
# 1. No active-estimate.json found.
# 2. Checks calibration/last-estimate.md — if recent (< 48h), reconstitutes.
# 3. If reconstitution fails, writes a minimal record with size="unknown".
# -> Response includes warning: "no_active_estimate"
# -> record_written: true (cost > 0.001 threshold)
# -> attribution_method: "proportional"
```

### Example D: Mixed Step Reporting + Call-Time `step_actuals`

Scenario: Some steps are reported individually via `report_step_cost`; others are supplied as a batch dict in the final `report_session` call.

```python
report_step_cost({"step_name": "Research Agent", "cost": 1.20})
report_step_cost({"step_name": "Implementation", "cost": 4.50})

report_session({
    "actual_cost": 7.20,
    "step_actuals": {
        "Staff Review": 1.10,
        "Implementation": 5.00   # overrides the 4.50 accumulated above
    }
})
# Merged step_actuals:
#   {"Research Agent": 1.20, "Implementation": 5.00, "Staff Review": 1.10}
# "Implementation" uses the call-time value (5.00) over accumulated (4.50)
# attribution_method: "mcp"
```

### Example E: All Four Token Types (Token-to-Cost Demonstration)

Scenario: A client reports a step using all four token fields to demonstrate the full cost formula.

```python
report_step_cost({
    "step_name": "Implementation",
    "tokens_in": 150000,         # fresh input tokens
    "tokens_out": 25000,         # output tokens
    "tokens_cache_read": 80000,  # tokens served from cache (cheap)
    "tokens_cache_write": 20000, # tokens written to cache
    "model": "claude-sonnet-4-6"
})
# Server computes (using Sonnet pricing):
#   tokens_in        = 150000 * $3.00 / 1_000_000 = $0.45000
#   tokens_out       =  25000 * $15.00 / 1_000_000 = $0.37500
#   tokens_cache_read=  80000 * $0.30 / 1_000_000 = $0.02400
#   tokens_cache_write=  20000 * $3.75 / 1_000_000 = $0.07500
#                                          Total = $0.92400
#
# -> Response: {step_name: "Implementation", cost_this_call: 0.924,
#               cumulative_step_cost: 0.924, total_session_accumulated: 0.924}
```

All four token fields are optional and independent — omitting any field is treated as zero tokens of that type. Providing `cost` directly always takes precedence over token-derived cost.

---

## Section 11 — What This Protocol Does NOT Cover

The following are explicitly out of scope for protocol version 1:

- **JSONL parsing**: The Claude Code sidecar/JSONL path continues unchanged. This protocol is additive — it does not replace the existing path for Claude Code users. Both paths can coexist.
- **Real-time streaming**: `report_step_cost` is a point-in-time report after a step completes, not a streaming cost feed. Clients accumulate cost internally and call the tool once per step.
- **Multi-session aggregation**: Each `report_session` call closes exactly one session. Cross-session analytics are handled by `tokencostscope-status.py`, not this protocol.
- **Authentication**: The MCP server is local (same machine as the client). No authentication is specified in v1. Enterprise authentication is a future concern and would require a version bump.
- **Cost validation**: The server does not verify that `actual_cost` plausibly matches the sum of `step_actuals`. Clients are trusted to report accurately. Discrepancies will appear in per-step calibration data over time.
