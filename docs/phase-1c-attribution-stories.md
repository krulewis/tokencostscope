# Phase 1c: Decouple Cost Attribution from Claude Code — User Story Decomposition

*Architecture review date: 2026-03-25*
*Author: Architect Agent*
*Input: enterprise-strategy-v2.md, sum-session-tokens.py, agent-hook.sh, learn.sh, Phase 1b stories*

---

## 1. The Coupling Problem

The current cost attribution pipeline is deeply coupled to Claude Code in six specific ways:

| # | Coupling Point | File | What It Assumes |
|---|---------------|------|-----------------|
| C1 | JSONL line format | `sum-session-tokens.py:compute_line_cost()` | Claude Code's JSONL schema: `type: "assistant"`, `message.usage` with `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `message.model` |
| C2 | Line-number-based span attribution | `sum-session-tokens.py:_build_spans()` | Agent spans are correlated to JSONL lines by `jsonl_line_count` — the hook records the JSONL file's line count at span start/stop time |
| C3 | JSONL discovery | `learn.sh` lines 86-98 | `find ~/.claude/projects/ -name "*.jsonl"` — assumes Claude Code's session storage path |
| C4 | JSONL line counting in hook | `agent-hook.sh` lines 67-81 | `wc -l` on the Claude Code JSONL to get current line number for span events |
| C5 | Agent naming conventions | `sum-session-tokens.py:DEFAULT_AGENT_TO_STEP` | Claude Code agent names ("researcher", "architect", "implementer", etc.) |
| C6 | Hook mechanism | `agent-hook.sh` | Relies on Claude Code's PreToolUse/PostToolUse hook system for Agent tool events |

**For a non-Claude-Code MCP client** (Cursor, VS Code + Copilot), none of these work:
- There is no JSONL session log (C1, C2, C3, C4)
- There is no hook system for agent lifecycle events (C6)
- Agent naming conventions differ (C5)

### The Decoupling Strategy

Phase 1c defines a **framework-agnostic attribution protocol** where the MCP client reports cost data directly in MCP tool calls, bypassing all JSONL correlation. The protocol has two tiers:

1. **Session-level**: Client calls `report_session(actual_cost=X)` — no per-step attribution, but calibration still works (proportional fallback, same as pre-v1.7).
2. **Step-level**: Client calls `report_step_cost(step_name, cost)` during execution, then `report_session()` at the end — full per-step attribution without JSONL or sidecars.

Both tiers work for any MCP client. Tier 1 is always available. Tier 2 requires client cooperation (reporting per-step costs as they occur).

---

## 2. Architecture Overview

```
MCP Client (any framework)

  During workflow:
    estimate_cost(plan) -> records active estimate
    report_step_cost("Research", 1.20) -> optional (Tier 2)
    report_step_cost("Implement", 4.50) -> optional (Tier 2)

  At end of workflow:
    report_session(actual_cost=8.50)
    OR
    report_session(actual_cost=8.50, step_actuals={...})

         | MCP tool calls
         v

tokencast MCP Server

  Attribution Adapter Layer (new):
    +----------------+  +---------------------+
    | MCP Protocol   |  | Claude Code         |
    | Adapter        |  | Sidecar Adapter     |
    | (tool calls)   |  | (JSONL + hooks)     |
    +-------+--------+  +----------+----------+
            |                      |
            +----------+-----------+
                       |
                       v
             Unified Session Recorder
             (build_history_record)
                       |
                       v
             calibration_store.append_history()
```

The Claude Code sidecar path (agent-hook.sh + JSONL line attribution) continues to work unchanged for Claude Code users. The MCP protocol adapter is a parallel path that produces the same history records.

---

## 3. User Stories

### US-1c.01: Define Framework-Agnostic Attribution Protocol

**As a** framework adapter developer, **I want** a documented, versioned protocol for reporting cost data via MCP tool calls, **so that** I can emit cost events without depending on Claude Code JSONL.

**Acceptance criteria:**
- [ ] Protocol document (`docs/attribution-protocol.md`) specifying:
  - Session lifecycle: `estimate_cost` (start) -> optional `report_step_cost` calls -> `report_session` (end)
  - `report_step_cost` input schema: `{step_name, cost, tokens_in?, tokens_out?, tokens_cache_read?, tokens_cache_write?, model?}`
  - `report_session` input schema: `{actual_cost, step_actuals?, turn_count?, review_cycles_actual?}`
  - Tier 1 (session-only) vs Tier 2 (step-level) behavior and tradeoffs
  - How costs are computed from token counts when dollar cost isn't available
  - Error handling: what happens if `report_session` is called without `estimate_cost`
  - What happens if `report_step_cost` is called with an unknown step name (accepted, stored as-is)
- [ ] Schema validation rules documented (which fields are required vs optional)
- [ ] Protocol version field (`attribution_protocol_version: 1`) for future evolution
- [ ] Examples for common scenarios: Cursor workflow, CI/CD pipeline, manual entry

**T-shirt estimate:** S (2-4hrs)

**Depends on:** None
**Blocks:** US-1c.02, US-1c.03, US-1c.04

**Notes:**
- This is a design document, not code. It must be written before implementation to align on the protocol.
- Key design decision: the protocol is stateless from the client's perspective. The server holds state (active estimate, accumulated step costs). The client just fires tool calls.

---

### US-1c.02: Implement `report_step_cost` MCP Tool

**As a** developer running an agent workflow step-by-step, **I want** to report the cost of each step as it completes, **so that** tokencast can attribute costs to specific pipeline steps without needing JSONL correlation.

**Acceptance criteria:**
- [ ] New MCP tool `report_step_cost` with input:
  ```json
  {
    "step_name": "Research Agent",
    "cost": 1.20,
    "tokens_in": 50000,
    "tokens_out": 5000,
    "tokens_cache_read": 30000,
    "tokens_cache_write": 10000,
    "model": "claude-sonnet-4-6"
  }
  ```
- [ ] Server accumulates step costs in memory (associated with active estimate)
- [ ] When `cost` is provided, uses it directly
- [ ] When tokens are provided without `cost`, computes cost using pricing module and model
- [ ] When both are provided, `cost` takes precedence
- [ ] Maps step_name to canonical names via agent-map.json (same as DEFAULT_AGENT_TO_STEP but also accepts canonical names directly)
- [ ] Unknown step names are accepted and stored as-is (no rejection)
- [ ] Returns confirmation: `{step_name, cost, cumulative_cost}`
- [ ] Returns error if no active estimate (must call `estimate_cost` first)
- [ ] Accumulated step costs are cleared when `report_session` is called

**T-shirt estimate:** M (4-8hrs)

**Depends on:** US-1c.01, US-1b.03 (MCP scaffold), US-1b.04 (estimate_cost tool), US-1c.06
**Blocks:** US-1c.04

**Notes:**
- Server-side state: the MCP server needs an in-memory dict mapping step_name -> accumulated cost. This is reset when `report_session` fires or when a new `estimate_cost` call is made.
- This is Tier 2 of the attribution protocol. It's optional — clients that only do Tier 1 skip this tool entirely.

---

### US-1c.03: Refactor Session Recorder to Accept Multiple Attribution Sources

**As a** developer maintaining tokencast, **I want** the session recording logic to accept cost data from any source (MCP tool calls, Claude Code sidecar, proportional fallback), **so that** the history records are identical regardless of how costs were reported.

**Acceptance criteria:**
- [ ] `build_history_record()` function (from US-1b.11) accepts an `attribution` parameter:
  ```python
  def build_history_record(
      estimate: dict,              # active-estimate.json contents
      actual_cost: float,
      turn_count: int = 0,
      review_cycles_actual: int = None,
      # Attribution sources (mutually exclusive — first non-None wins):
      step_actuals_mcp: dict = None,      # from report_step_cost accumulation
      step_actuals_sidecar: dict = None,  # from _build_spans() + JSONL
      # Fallback: proportional attribution from session ratio
  ) -> dict:
  ```
- [ ] Attribution method recorded in history: `"mcp"`, `"sidecar"`, or `"proportional"`
- [ ] All three paths produce records with the same schema (step_actuals, step_ratios, step_costs_estimated)
- [ ] Proportional fallback applies the same session-level ratio to all steps (unchanged from current behavior)
- [ ] learn.sh updated to call `build_history_record()` with `step_actuals_sidecar` parameter
- [ ] `report_session` MCP tool calls `build_history_record()` with `step_actuals_mcp` parameter
- [ ] Tests verify that identical inputs produce identical records regardless of attribution source

**T-shirt estimate:** M (4-8hrs)

**Depends on:** US-1b.11 (shared record logic), US-1c.01
**Blocks:** US-1c.04, US-1c.05

**Notes:**
- This is the key unification point. After this story, there is one function that produces history records, and three ways to provide attribution data to it.
- The sidecar path (Claude Code) continues to work exactly as before — this story only changes the calling convention, not the attribution logic.

---

### US-1c.04: End-to-End Integration Test: Non-Claude-Code Client

**As a** developer verifying that attribution decoupling works, **I want** an integration test that simulates a complete Cursor-like workflow (estimate -> step reports -> session report) without any Claude Code JSONL, **so that** I can be confident the MCP path produces valid calibration data.

**Acceptance criteria:**
- [ ] Test simulates:
  1. Call `estimate_cost` with plan params
  2. Call `report_step_cost` for 3 steps with dollar costs
  3. Call `report_session` with total actual cost
  4. Verify history.jsonl has a new record with `attribution_method: "mcp"`
  5. Verify step_actuals match the reported values
  6. Verify step_ratios are computed correctly (actual / estimated per step)
  7. Verify factors.json is updated (if sample count >= 3)
- [ ] Test also verifies Tier 1 path (session-only, no step reports):
  1. Call `estimate_cost`
  2. Call `report_session` with actual_cost only
  3. Verify `attribution_method: "proportional"` in history
  4. Verify step_ratios use session-level ratio for all steps
- [ ] Test verifies mixed scenario: some steps reported, some not
  - Reported steps use actual values
  - Unreported steps use proportional fallback from session ratio
- [ ] No Claude Code JSONL files involved in any test path
- [ ] Tests runnable via `/usr/bin/python3 -m pytest`

**T-shirt estimate:** M (4-8hrs)

**Depends on:** US-1c.02, US-1c.03
**Blocks:** US-1c.05

**Notes:**
- These tests are the "exit criteria" for Phase 1c: "at least 1 non-Claude-Code client can produce calibrated estimates."
- Use the MCP SDK's test utilities to drive the server in-process.

---

### US-1c.05: Documentation and Migration Guide

**As a** developer migrating from SKILL.md to MCP, **I want** clear documentation on how cost attribution works in both modes, **so that** I understand the tradeoffs and can choose the right approach.

**Acceptance criteria:**
- [ ] `docs/wiki/Attribution.md` (new wiki page) covering:
  - How attribution works in Claude Code (sidecar + JSONL — unchanged)
  - How attribution works via MCP (tool-call-based — new)
  - Tier 1 vs Tier 2 comparison with accuracy implications
  - Which MCP clients support automatic cost reporting (links to IDE docs)
  - How to manually report costs for clients without native cost tracking
- [ ] `docs/wiki/How-It-Works.md` updated to reference attribution protocol
- [ ] `docs/attribution-protocol.md` (from US-1c.01) linked from wiki
- [ ] `CLAUDE.md` updated with new files and conventions
- [ ] Example snippets for common MCP clients (Cursor, VS Code)

**T-shirt estimate:** S (2-4hrs)

**Depends on:** US-1c.03, US-1c.04
**Blocks:** None

**Notes:**
- The documentation should be honest about Tier 1 vs Tier 2 accuracy. Tier 1 (proportional) is less accurate but always works. Tier 2 (step-level) matches Claude Code sidecar accuracy but requires client cooperation.

---

### US-1c.06: Decouple `compute_line_cost` from Claude Code JSONL Format

**As a** developer extending tokencast to new frameworks, **I want** the cost computation function to accept a framework-agnostic input format, **so that** it can process cost data from any source.

**Acceptance criteria:**
- [ ] New function `compute_cost_from_usage(usage: dict, model: str) -> float` that accepts:
  ```python
  {
    "input_tokens": 50000,
    "output_tokens": 5000,
    "cache_read_input_tokens": 30000,
    "cache_creation_input_tokens": 10000,
  }
  ```
- [ ] `compute_line_cost()` refactored to extract usage from Claude Code JSONL format, then delegate to `compute_cost_from_usage()`
- [ ] `report_step_cost` MCP tool uses `compute_cost_from_usage()` for token-to-cost conversion
- [ ] Both paths use the same PRICES dict and model resolution logic
- [ ] Existing tests pass without modification (compute_line_cost behavior unchanged)
- [ ] New tests for `compute_cost_from_usage()` with various token combinations

**T-shirt estimate:** S (2-4hrs)

**Depends on:** None
**Blocks:** US-1c.02

**Notes:**
- This is a small refactoring that creates the right seam. `compute_line_cost()` is Claude-Code-specific (it knows about `type: "assistant"` and `message.usage`). `compute_cost_from_usage()` is framework-agnostic (it takes a usage dict and model string).
- The existing PRICES dict and model resolution logic in sum-session-tokens.py should be shared with the pricing.py module from US-1b.02.

---

## 4. Dependency Graph

```
US-1c.06 (Decouple compute_line_cost)     US-1c.01 (Attribution Protocol Spec)
    |                                           |
    v                                           |
US-1c.02 (report_step_cost tool) <--------------+
    |                                           |
    |                                           v
    |                              US-1c.03 (Refactor Session Recorder) <-- US-1b.11
    |                                           |
    v                                           v
    +--------------------->  US-1c.04 (Integration Tests)
                                                |
                                                v
                                  US-1c.05 (Docs + Migration Guide)
```

### Critical Path

```
US-1c.06 --> US-1c.02 --> US-1c.04 --> US-1c.05
  (S)          (M)          (M)          (S)
 2-4hrs       4-8hrs       4-8hrs       2-4hrs
```

In parallel:
```
US-1c.01 --> US-1c.03 --> US-1c.04
  (S)          (M)          (already counted)
 2-4hrs       4-8hrs
```

**Critical path total: 12-24 hours**

### Parallelizable Work

- US-1c.01 and US-1c.06 can start simultaneously (no dependencies between them)
- US-1c.02 can start once US-1c.06 is done (and US-1c.01 for protocol awareness)
- US-1c.03 can start once US-1c.01 and US-1b.11 are done
- US-1c.02 and US-1c.03 can run in parallel

---

## 5. Total Effort Estimate

| Story | T-shirt | Hours (range) |
|-------|---------|---------------|
| US-1c.01 | S | 2-4 |
| US-1c.02 | M | 4-8 |
| US-1c.03 | M | 4-8 |
| US-1c.04 | M | 4-8 |
| US-1c.05 | S | 2-4 |
| US-1c.06 | S | 2-4 |
| **Total** | | **18-36 hrs** |

**At 10hrs/week:** 1.8-3.6 weeks. This aligns with the strategy's "2-3 weeks" estimate.

**Assessment:** The strategy's estimate is realistic. The work is smaller than Phase 1b because it builds on the MCP server and shared recorder from 1b. The main risk is getting the session recorder refactoring right (US-1c.03) without breaking the existing Claude Code path.

---

## 6. Risks and Open Questions

### Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| R1 | MCP clients don't expose token usage to tools | Tier 2 attribution impossible in some clients | Tier 1 (session-level) always works as fallback. Document which clients support what. |
| R2 | Session recorder refactoring breaks Claude Code sidecar path | Regression for existing users | Extensive test coverage on the sidecar path before and after refactoring. Run existing 441 tests as regression suite. |
| R3 | Attribution protocol is too verbose (too many tool calls per session) | Poor developer experience | Tier 2 is optional. Most users will use Tier 1. Tier 2 is for power users who want per-step accuracy. |

### Open Questions (Requiring Human Input)

| # | Question | Options | Recommendation |
|---|----------|---------|----------------|
| Q1 | Should `report_step_cost` accumulate or replace? | Accumulate (multiple calls for same step add up) vs replace (last call wins) | Accumulate — a step might have multiple sub-costs (e.g., multiple file reads). Caller can always report the total in a single call. |
| Q2 | Should the server persist accumulated step costs to disk? | In-memory only vs write to disk | In-memory only for Phase 1c. If the MCP server crashes mid-session, step costs are lost (but `report_session` Tier 1 still works). Disk persistence is Phase 2+ complexity. |
| Q3 | How does a CI/CD pipeline (no interactive session) use this? | Same tool calls via MCP client library | Document that CI pipelines can use the Python package directly (`from tokencast import estimate_cost`) without the MCP layer. Add a thin programmatic API alongside MCP tools. |
| Q4 | Should Phase 1c ship as a separate release from Phase 1b? | Combined release vs separate | Separate. Phase 1b is v3.0.0 (MCP server). Phase 1c is v3.1.0 (attribution decoupling). This allows Phase 1b to ship and get user feedback while 1c is in progress. |

---

## 7. Cross-Phase Dependencies (1b <-> 1c)

Phase 1c has several interaction points with Phase 1b that affect scheduling:

| 1c Story | 1b Dependency | Direction | Notes |
|----------|--------------|-----------|-------|
| US-1c.02 | US-1b.03 (MCP scaffold) + US-1b.04 (estimate_cost) | 1c depends on 1b | report_step_cost requires the MCP server to exist |
| US-1c.03 | US-1b.11 (shared record logic) | 1c depends on 1b | Refactoring builds on the extracted function |
| US-1c.06 | US-1b.02 (pricing module) | 1c benefits from 1b | compute_cost_from_usage can share the pricing data |
| US-1c.01, US-1c.06 | None | Independent | Can start before 1b ships |

**Recommended overlap strategy:** Start US-1c.01 and US-1c.06 immediately (they have no Phase 1b dependencies). The remaining stories can begin as their 1b dependencies land.
