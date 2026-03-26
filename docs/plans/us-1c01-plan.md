# Implementation Plan: US-1c.01 — Framework-Agnostic Attribution Protocol

## Overview

This story produces a single new documentation file: `docs/attribution-protocol.md`. No code changes. The document defines the versioned wire protocol that any MCP client uses to report cost data to tokencast, decoupled from Claude Code JSONL. It is a prerequisite blocking US-1c.02, US-1c.03, and US-1c.04, which implement the protocol in code.

The document must be precise enough to serve as the implementation specification for those stories. It specifies:
- The two-tier session lifecycle (estimate → optional per-step reports → session report)
- Exact JSON schemas for `report_step_cost` and `report_session` tool inputs/outputs
- The on-disk persistence format for accumulated step costs (used by US-1c.02)
- Token-to-cost conversion rules (leveraging existing `PRICES` dict logic in `sum-session-tokens.py`)
- Error-handling contracts
- Protocol versioning mechanics
- Worked examples for three client scenarios

---

## Changes

```
File: docs/attribution-protocol.md
Lines: new file
Parallelism: independent
Description: The attribution protocol specification document. This is the only deliverable for US-1c.01.
Details:
  See "Document Content Specification" section below for the exact section structure and content requirements.
```

No other files are modified. The document references existing files but does not change them.

---

## Document Content Specification

The file `/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/attribution-protocol.md` must contain all of the following sections in order.

### Section 1 — Header / Frontmatter

```
# tokencast Attribution Protocol

version: 1
status: draft
date: 2026-03-26
blocks: US-1c.02, US-1c.03, US-1c.04
```

Include a one-paragraph summary: "This document specifies the framework-agnostic protocol for reporting cost data to tokencast via MCP tool calls. Clients that cannot produce Claude Code JSONL (e.g., Cursor, VS Code, CI pipelines) use this protocol to produce the same calibration records as Claude Code users."

### Section 2 — Protocol Version

Explain:
- Field name: `attribution_protocol_version`
- Current value: `1`
- Where it appears: in every `report_step_cost` and `report_session` tool response, and in the on-disk step-cost accumulator file (see Section 6)
- Versioning contract: minor additions (new optional fields) do not require a version bump. Removing or renaming required fields, or changing field types, requires incrementing to `2`. Implementers must check this field and reject (with a clear error) if the version is higher than they support.

### Section 3 — Session Lifecycle

Describe the two tiers as a state machine:

**Tier 1 (session-only)**:
```
estimate_cost(plan_params)          [required — opens session state]
      |
      v
  [work happens in client framework]
      |
      v
report_session(actual_cost=X)       [required — closes and records]
```

**Tier 2 (step-level)**:
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

Key behavioral rules (must be stated explicitly):
- `estimate_cost` resets any previously accumulated step costs (starting a new session discards the old one)
- Multiple `report_step_cost` calls for the same `step_name` are additive (values sum, not replaced)
- `report_session` clears all accumulated step costs after recording
- If `report_session` provides both `actual_cost` and `step_actuals`, the `step_actuals` dict from the call is merged with any accumulated step costs from prior `report_step_cost` calls. The call-time values take precedence for keys that appear in both.
- `report_step_cost` called after `report_session` (with no new `estimate_cost`) returns an error
- `report_session` called without a preceding `estimate_cost` in the same server session: behavior defined in Section 8 (Error Handling)

### Section 4 — Tool: `report_step_cost`

#### Input Schema

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

Validation rules (state explicitly):
- At least one of `cost` or any token count field must be present (otherwise the call reports zero cost, which is accepted but generates a warning in the response)
- `cost` and token fields may be provided together; `cost` wins
- `step_name` must be a non-empty string; whitespace-only strings are rejected with a validation error
- All numeric fields must be non-negative; negative values are rejected with a validation error

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "attribution_protocol_version": {"type": "integer", "const": 1},
    "step_name": {"type": "string", "description": "Canonicalized step name (after agent-map.json lookup). If no mapping found, equals the raw input step_name."},
    "cost_this_call": {"type": "number", "description": "Dollar cost recorded for this specific call."},
    "cumulative_step_cost": {"type": "number", "description": "Total accumulated cost for this step_name across all report_step_cost calls in the current session."},
    "total_session_accumulated": {"type": "number", "description": "Sum of all accumulated step costs across all steps in the current session."},
    "warning": {"type": "string", "description": "Present only when the call succeeds but has a non-fatal issue (e.g., zero cost reported, unknown step name)."}
  },
  "required": ["attribution_protocol_version", "step_name", "cost_this_call", "cumulative_step_cost", "total_session_accumulated"]
}
```

### Section 5 — Tool: `report_session`

#### Input Schema

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

Validation rules:
- `actual_cost` is required. A value of 0.0 is technically valid but the server will not write a history record (same threshold as learn.sh: `actual_cost > 0.001`). The response will include a `warning` explaining no record was written.
- `step_actuals` values must be non-negative numbers. Negative values are rejected with a validation error.
- `review_cycles_actual` must be a non-negative integer.

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "attribution_protocol_version": {"type": "integer", "const": 1},
    "record_written": {"type": "boolean", "description": "True if a history record was appended to history.jsonl."},
    "attribution_method": {
      "type": "string",
      "enum": ["mcp", "proportional"],
      "description": "'mcp' when step_actuals are present (either from report_step_cost calls or step_actuals input). 'proportional' when no step data is available."
    },
    "actual_cost": {"type": "number"},
    "step_actuals": {
      "type": ["object", "null"],
      "description": "The merged step_actuals dict that was stored. Null when attribution_method is 'proportional'."
    },
    "warning": {"type": "string", "description": "Present only when the call succeeds but something notable occurred (e.g., no active estimate found, no record written due to zero cost)."}
  },
  "required": ["attribution_protocol_version", "record_written", "attribution_method", "actual_cost"]
}
```

### Section 6 — On-Disk Persistence for Accumulated Step Costs

Although the architecture story document (Q2 from phase-1c-attribution-stories.md) listed in-memory as the initial design, the CLAUDE.md notes that the final decision is **atomic-rename pattern, like `active-estimate.json`** (stated in the task brief as an already-made decision). The protocol document must specify this format because US-1c.02 implements it.

**File path:** `calibration/step-accumulator.json`

**Written:** After each successful `report_step_cost` call (atomic rename: write to `.tmp` then `os.replace()`).

**Cleared:** When `report_session` completes (delete the file) or when a new `estimate_cost` call is made (delete any existing file).

**Schema:**
```json
{
  "attribution_protocol_version": 1,
  "session_id_hash": "abc123def456",
  "steps": {
    "Research Agent": 1.20,
    "Implementation": 4.50
  },
  "last_updated": "2026-03-26T14:00:00Z"
}
```

Field notes:
- `session_id_hash`: same hash used by `agent-hook.sh` (md5 of `calibration/active-estimate.json` path, first 12 chars). Allows the server to detect if the accumulator belongs to a different session (stale file from a crashed session).
- `steps`: dict of step_name (canonicalized) -> accumulated float cost. Additive across calls.
- `last_updated`: ISO 8601 UTC timestamp. Used for stale-file detection (discard if older than 48h with no active estimate).
- Stale-file rule: if `active-estimate.json` does not exist and `step-accumulator.json` is present, the accumulator is silently discarded (no error to client). This mirrors the continuation-session behavior in learn.sh.

### Section 7 — Token-to-Cost Conversion

When `cost` is absent from a `report_step_cost` call but token counts are provided, the server computes cost using the same formula as `sum-session-tokens.py:compute_line_cost()`:

```
cost = (tokens_in * price_input
      + tokens_cache_read * price_cache_read
      + tokens_cache_write * price_cache_write
      + tokens_out * price_output) / 1_000_000
```

Prices are read from `references/pricing.md` (via the shared pricing module from US-1b.02 when available; otherwise from the `PRICES` dict in `sum-session-tokens.py`).

**Model resolution order:**
1. Use `model` field from the `report_step_cost` call if provided.
2. Look up the step's model from the `Pipeline Step → Model Mapping` in `references/pricing.md`.
3. Fall back to `claude-sonnet-4-6`.

**Model string matching:** Partial match (e.g., `"claude-sonnet"` matches `"claude-sonnet-4-6"` pricing row). Same logic as `compute_line_cost()` lines 87-89 in `sum-session-tokens.py`.

**Unknown model:** If the model string does not match any known model (even partially), use `DEFAULT_MODEL = "claude-sonnet-4-6"` pricing. Log a warning in the response.

**Zero-token call:** If `cost` is absent and all token counts are absent or zero, the call records a cost of `0.0`. The response includes a `warning: "No cost or token data provided; recorded 0.0"`. The call is not rejected — the client may legitimately want to mark a step as started.

### Section 8 — Error Handling

State each error case explicitly:

| Scenario | Server Behavior | Response |
|----------|----------------|---------|
| `report_step_cost` called with no active estimate | Return error response | `{"error": "no_active_estimate", "message": "Call estimate_cost before reporting step costs."}` |
| `report_session` called with no active estimate | Record written with `attribution_method: "proportional"` using a synthetic estimate (size=unknown, steps=[]). Warning included. | See note below. |
| `report_step_cost` with `step_name` = whitespace-only | Validation error | `{"error": "invalid_step_name", "message": "step_name must be a non-empty string."}` |
| `report_step_cost` with negative `cost` | Validation error | `{"error": "invalid_cost", "message": "cost must be >= 0."}` |
| `report_step_cost` with negative token count | Validation error | `{"error": "invalid_tokens", "message": "Token counts must be >= 0.", "field": "<field_name>"}` |
| `report_session` with `actual_cost = 0.0` | No record written, warning returned | `{"record_written": false, "warning": "actual_cost is 0.0; no calibration record written."}` |
| `report_session` with negative `actual_cost` | Validation error | `{"error": "invalid_cost", "message": "actual_cost must be >= 0."}` |
| `step-accumulator.json` exists but belongs to a different session (hash mismatch) | Silently discard the stale accumulator, proceed with no prior step costs | Include `warning: "stale_accumulator_discarded"` in response |

**`report_session` without `estimate_cost` — detailed behavior:**

This is a valid Tier 1 use case for systems that report only session totals. The server should:
1. Look for `calibration/last-estimate.md` and attempt reconstitution (same as learn.sh continuation logic).
2. If reconstitution succeeds, use the reconstituted estimate and proceed normally.
3. If reconstitution fails or `last-estimate.md` is absent/stale, write a minimal history record with `size: "unknown"`, `steps: []`, `expected_cost: 0`, and `attribution_method: "proportional"`. The `ratio` field will be meaningless (division by near-zero expected), so set `ratio: null` for this case.
4. Include `warning: "no_active_estimate"` in the response.

### Section 9 — Canonical Step Names

The protocol accepts any non-empty `step_name`. The following are the canonical names that map to calibration history entries and are recognized by the factor computation in `update-factors.py`. Non-canonical names are stored as-is and will appear in `step_actuals` but may not accumulate per-step calibration factors (they will contribute to the global factor).

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

Alias resolution uses the same `agent-map.json` lookup as `_load_agent_map()` in `sum-session-tokens.py`. Custom aliases can be added to `calibration/agent-map.json`.

The `PR Review Loop` step is not accepted as a `report_step_cost` target. It is a derived aggregate computed during estimation, not an individual reportable step. If a client attempts to report it, the server accepts the call (unknown-step rule: stored as-is) but includes a `warning: "pr_review_loop_is_derived"`.

### Section 10 — Worked Examples

#### Example A: Cursor Workflow (Tier 2 — Step-Level)

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
# -> Clears step-accumulator.json
# -> Response: {record_written: true, attribution_method: "mcp", actual_cost: 3.75, ...}
```

#### Example B: CI/CD Pipeline (Tier 1 — Session-Only)

Scenario: A GitHub Actions workflow runs a plan and only knows the total cost at the end (no per-step breakdown).

```python
# Option 1: via MCP tool call (MCP client library in CI)
estimate_cost({"plan": "Update dependency pinning", "size": "S", "files": 2})
# [workflow runs]
report_session({"actual_cost": 0.85, "turn_count": 32})
# -> attribution_method: "proportional" (no step reports)

# Option 2: via Python import (no MCP layer needed in CI)
from tokencast import estimate_cost, report_session
estimate_cost(plan="Update dependency pinning", size="S", files=2)
# [workflow runs]
report_session(actual_cost=0.85, turn_count=32)
```

Note: The Python import path uses the same underlying functions. The MCP tools are thin wrappers. CI users can skip MCP entirely.

#### Example C: Manual Entry (Tier 1 — Post-Hoc)

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

#### Example D: Mixed Step Reporting + Call-Time `step_actuals`

Scenario: Some steps reported via `report_step_cost`, others provided as a batch in `report_session`.

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

### Section 11 — What This Protocol Does NOT Cover

Be explicit about out-of-scope items to prevent implementer confusion:

- **JSONL parsing**: The Claude Code sidecar/JSONL path continues unchanged. This protocol is additive — it does not replace the existing path for Claude Code users.
- **Real-time streaming**: `report_step_cost` is a point-in-time report after a step completes, not a streaming cost feed.
- **Multi-session aggregation**: Each `report_session` closes exactly one session. Cross-session analytics live in `tokencostscope-status.py`.
- **Authentication**: The MCP server is local (same machine). No auth is specified in v1. Enterprise auth is a future concern.
- **Cost validation**: The server does not verify that `actual_cost` plausibly matches the sum of `step_actuals`. Clients are trusted to report accurately.

---

## Dependency Order

This story has no dependencies and produces no code. The single output is the document at `docs/attribution-protocol.md`. The document itself is the dependency for US-1c.02, US-1c.03, and US-1c.04.

Execution order:
1. Write `docs/attribution-protocol.md` (no prerequisites)
2. Review for completeness against all acceptance criteria in US-1c.01
3. Done — unblocks US-1c.02 and US-1c.03 (which can start in parallel after this)

---

## Test Strategy

This story produces a document, not code. There are no automated tests for the document itself. However, the document must satisfy a completeness checklist before it can be considered done.

**Completeness checklist (manual review gate):**

```
[ ] Section 2: protocol version field named, value=1, bump rules stated
[ ] Section 3: both tier diagrams present, all 5 behavioral rules stated
[ ] Section 4: report_step_cost input schema has all 7 fields, output schema has all 5 fields
[ ] Section 4: all 4 validation rules stated
[ ] Section 5: report_session input schema has all 4 fields, output schema has all 5 fields
[ ] Section 5: all 3 validation rules stated
[ ] Section 6: step-accumulator.json schema specified, all 4 fields documented
[ ] Section 6: stale-file rule and clear-on-new-session rule stated
[ ] Section 7: token-to-cost formula written out, model resolution order stated (3 steps)
[ ] Section 7: zero-token call behavior stated
[ ] Section 8: error table has all 8 rows, report_session-without-estimate behavior specified
[ ] Section 9: canonical step names table present, PR Review Loop exclusion noted
[ ] Section 10: all 4 examples present (Cursor Tier 2, CI/CD Tier 1, manual entry, mixed)
[ ] Section 11: 5 out-of-scope items listed
```

**Tests written by downstream stories (not this story):**

- US-1c.02 will write tests validating that `report_step_cost` implements the input schema exactly as specified here
- US-1c.03 will write tests validating that `report_session` produces history records matching the protocol's specified `attribution_method` values
- US-1c.04 will write end-to-end integration tests covering all four example scenarios above

The document must be stable before those tests are written — schema changes after US-1c.02 starts create rework.

---

## Rollback Notes

This story creates one new file (`docs/attribution-protocol.md`) with no code changes. Rollback is `git rm docs/attribution-protocol.md`. No data migration, no schema changes, no impact on existing calibration files.

If the protocol design is found to be wrong after US-1c.02 begins implementation:
- Protocol version bump is the forward path (not rollback) — bump to version 2 and update the spec
- Rollback of US-1c.02 implementation is the implementer's responsibility (separate story)
- This document itself can be freely updated before US-1c.02 is merged

---

## Estimated Effort

- **Document writing**: 2-3 hours (schema design, worked examples, edge cases)
- **Review against acceptance criteria**: 30 minutes
- **Total**: 2.5-3.5 hours (within S estimate of 2-4hrs)

The effort is front-loaded in Section 8 (error handling) and Section 6 (persistence format), which require the most design judgment. Sections 4-5 (schemas) are mechanical given the architecture decisions already made.
