# Phase 1 Execution Plan — tokencast MCP Server + Attribution Decoupling

*Created: 2026-03-26*
*Inputs: phase-1b-mcp-stories.md, phase-1c-attribution-stories.md, phase-1-pm-review.md, owner decisions on open questions*

---

## Scope

Phase 1 delivers:
- **Phase 1b**: tokencast MCP server — estimation engine as Python, MCP tools, PyPI v1.0.0
- **Phase 1c**: Attribution decoupling — framework-agnostic cost reporting, no JSONL dependency
- **PM stories**: Phase 1.5 readiness (telemetry, waitlist, graceful degradation)

## Owner Decisions (resolved 2026-03-26)

### Phase 1b

| # | Question | Decision |
|---|----------|----------|
| Q1 | PyPI package name | `tokencast` (already published at 0.1.0) |
| Q2 | Code location | `src/tokencast_mcp/` |
| Q3 | Param strictness | Lenient — require `size`/`files`/`complexity` only |
| Q4 | Output format | Both structured JSON + markdown table |
| Q5 | `report_session` input | Dollar cost + optional token counts |
| Q6 | Version bump | **Independent versions** — PyPI follows own semver (0.1.0 → 1.0.0), SKILL.md stays at 2.x |

### Phase 1c

| # | Question | Decision |
|---|----------|----------|
| Q1 | `report_step_cost` behavior | Accumulate |
| Q2 | Persist step costs? | **Persist to disk** (atomic-rename, same pattern as `active-estimate.json`) — overrides architect rec of in-memory only. Rationale: compaction and crashes both kill long sessions; v2.1.0 solved this exact problem. |
| Q3 | CI/CD usage | **Dict-based routing layer** — MCP tools are thin wrappers around `estimate_cost(dict) → dict`. CI users import same functions. One schema (matching MCP tool input/output), two access patterns. Prevents API drift. |
| Q4 | Ship separately? | 1b first (PyPI v1.0.0), then 1c (v1.1.0) |

## PM Review Adjustments (incorporated)

| Adjustment | Rationale |
|------------|-----------|
| **Merge US-1b.11 into US-1c.03** | Avoid duplicate extraction — US-1c.03 is a superset of US-1b.11 |
| **Split US-1b.09 → 09a + 09b** | Engine tests (09a) can start before MCP tools finish |
| **Defer US-1c.07** (multi-model pricing) to Phase 2 | No Phase 1 exit criterion requires it |
| **Fold US-1c.06 into US-1c.02** | v2 schema already solves step-name generalization |
| **Add US-PM.01** (telemetry) | MUST-HAVE — blocks Phase 1.5 Experiment 1 |
| **Add US-PM.02** (waitlist) | MUST-HAVE — blocks Phase 1.5 Experiment 2 |
| **Add US-PM.04** (graceful degradation) | MUST-HAVE — first-run experience depends on it |

---

## Story Inventory (adjusted)

### Phase 1b — MCP Server

| ID | Story | Size | Depends on | Status |
|----|-------|------|------------|--------|
| US-1b.02 | Extract pricing/heuristics to Python modules | M (4-8hrs) | None | Not started |
| US-1b.03 | MCP server scaffold with stdio transport | M (4-8hrs) | None | Not started |
| US-1b.01 | Extract estimation algorithm to Python engine | XL (16-32hrs) | 1b.02 | Not started |
| US-1b.04 | Implement `estimate_cost` MCP tool | L (8-16hrs) | 1b.01, 1b.03 | Not started |
| US-1b.05 | Implement `get_calibration_status` MCP tool | S (2-4hrs) | 1b.03 | Not started |
| US-1b.06 | Implement `get_cost_history` MCP tool | S (2-4hrs) | 1b.03 | Not started |
| US-1b.07 | Implement `report_session` MCP tool | M (4-8hrs) | 1b.03, 1c.03 | Not started |
| US-1b.08 | Package, entry point, install config | M (4-8hrs) | 1b.04 | Not started |
| US-1b.09a | Engine unit tests | M (4-8hrs) | 1b.01 | Not started |
| US-1b.09b | MCP tool integration + protocol tests | M (4-8hrs) | 1b.04-.07 | Not started |
| US-1b.10 | Registry publication + docs + CI/CD | M (4-8hrs) | 1b.08, 1b.09 | Not started |

*Note: US-1b.11 (shared record logic) merged into US-1c.03.*

### Phase 1c — Attribution Decoupling

| ID | Story | Size | Depends on | Status |
|----|-------|------|------------|--------|
| US-1c.01 | Define attribution protocol (doc) | S (2-4hrs) | None | Not started |
| US-1c.02 | Implement `report_step_cost` MCP tool (absorbs US-1c.06) | M (4-8hrs) | 1c.01, 1b.03 | Not started |
| US-1c.03 | Refactor session recorder (absorbs US-1b.11) | M (4-8hrs) | 1c.01 | Not started |
| US-1c.04 | E2E integration test: non-Claude-Code client | M (4-8hrs) | 1c.02, 1c.03 | Not started |
| US-1c.05 | Documentation + migration guide | S (2-4hrs) | 1c.04 | Not started |

*Note: US-1c.06 folded into US-1c.02. US-1c.07 deferred to Phase 2.*

### PM Stories — Phase 1.5 Readiness

| ID | Story | Size | Depends on | Status |
|----|-------|------|------------|--------|
| US-PM.01 | Opt-in anonymous telemetry | M (4-8hrs) | 1b.03, 1b.04, 1b.07 | Not started |
| US-PM.02 | "Share with team" waitlist hook | S (2-4hrs) | 1b.07, 1b.06 | Not started |
| US-PM.04 | Graceful degradation for edge cases | S (2-4hrs) | 1b.03, 1b.04 | Not started |

---

## Execution Schedule

### Week 1-2 — Three parallel streams (no dependencies)

| Stream | Story | Size | Notes |
|--------|-------|------|-------|
| A | **US-1b.02** — Pricing/heuristics → Python modules | M | Start day 1. Unblocks engine. |
| B | **US-1b.03** — MCP server scaffold | M | Start day 1. Unblocks all tools. |
| C | **US-1c.01** — Attribution protocol spec (doc only) | S | Start day 1. Unblocks 1c work. |

### Week 2-4 — Engine extraction (CRITICAL PATH)

| Stream | Story | Size | Notes |
|--------|-------|------|-------|
| A | **US-1b.01** — Estimation engine extraction | XL | **Blocked by 1b.02. This is 50%+ of the critical path.** |
| B | **US-1b.05** — `get_calibration_status` tool | S | Blocked by 1b.03 only. |
| B | **US-1b.06** — `get_cost_history` tool | S | Blocked by 1b.03 only. |
| C | **US-1c.02** — `report_step_cost` tool (incl. `compute_cost_from_usage`) | M | Blocked by 1c.01, 1b.03. |

### Week 4-5 — Tools + shared record logic

| Stream | Story | Size | Notes |
|--------|-------|------|-------|
| A | **US-1b.04** — `estimate_cost` tool | L | Blocked by 1b.01, 1b.03. |
| B | **US-1c.03** — Session recorder refactor (absorbs 1b.11) | M | Blocked by 1c.01. |
| C | **US-1b.09a** — Engine unit tests | M | Blocked by 1b.01 only. Can start before tools. |

### Week 5-6 — Learning loop + packaging

| Stream | Story | Size | Notes |
|--------|-------|------|-------|
| A | **US-1b.07** — `report_session` tool | M | Blocked by 1b.03, 1c.03 (shared recorder). |
| B | **US-1b.08** — Package + install config | M | Blocked by 1b.04. |
| C | **US-1c.04** — E2E integration test (non-Claude-Code) | M | Blocked by 1c.02, 1c.03. |

### Week 6-7 — Testing + publication (SHIP 1b)

| Stream | Story | Size | Notes |
|--------|-------|------|-------|
| A | **US-1b.09b** — MCP tool integration tests | M | Blocked by all tool impls. |
| B | **US-1b.10** — Registry + docs + CI/CD | M | Blocked by 1b.08, 1b.09. |
| C | **US-1c.05** — Attribution docs + migration guide | S | Blocked by 1c.04. |

**→ Ship PyPI v1.0.0 (Phase 1b) at end of week 7.**
**→ Ship PyPI v1.1.0 (Phase 1c) shortly after.**

### Week 7-8 — PM stories (Phase 1.5 readiness)

| Stream | Story | Size | Notes |
|--------|-------|------|-------|
| A | **US-PM.01** — Opt-in telemetry | M | Blocked by tool impls. |
| B | **US-PM.02** — Team sharing waitlist | S | Blocked by 1b.07, 1b.06. |
| C | **US-PM.04** — Graceful degradation | S | Can be folded into earlier tool work if time permits. |

**→ Phase 1.5 market validation can begin at end of week 8.**

---

## Dependency Graph (visual)

```
                    WEEK 1-2 (parallel start)
                    ┌────────────────────────────┐
                    │                            │
              US-1b.02          US-1b.03         US-1c.01
              (pricing)         (scaffold)       (protocol spec)
                    │               │                │
                    ▼               │           ┌────┴────┐
              US-1b.01 ◄───────────┤           │         │
              (ENGINE)             │        US-1c.02  US-1c.03
              ▲▲▲▲▲▲▲▲            │        (step     (session
              CRITICAL             │         cost)    recorder)
              PATH                 │           │         │
                    │    ┌─────────┼───────┐   │         │
                    │    │         │       │   │         │
                    ▼    ▼         ▼       ▼   │         │
              US-1b.04  1b.05   1b.06   1b.07◄┘         │
              (estimate) (status) (history)(report)◄─────┘
                    │                       │
                    ▼                       │
              US-1b.08                      │
              (package)                     │
                    │                       │
               ┌────┴────┐                  │
               │         │                  │
            1b.09a    1b.09b         US-1c.04
            (engine   (MCP           (e2e test)
             tests)    tests)              │
               │         │                 │
               └────┬────┘           US-1c.05
                    │                (docs)
                    ▼
              US-1b.10              US-PM.01  US-PM.02  US-PM.04
              (publish)             (telem)   (waitlist) (graceful)
```

---

## Effort Summary

| Category | Stories | Hours (range) |
|----------|---------|---------------|
| Phase 1b | 11 stories | 60-120 hrs |
| Phase 1c | 5 stories | 16-32 hrs |
| PM stories | 3 stories | 8-14 hrs |
| **Total** | **19 stories** | **84-166 hrs** |

| Metric | Value |
|--------|-------|
| **Critical path** | 1b.02 → 1b.01 → 1b.04 → 1b.08 → 1b.09 → 1b.10 |
| **Critical path hours** | 44-88 hrs |
| **Max parallel streams** | 3 |
| **Calendar time at 10hrs/wk** | Best: 6 wks · Expected: 8-10 wks · Pessimistic: 12-14 wks |
| **First ship (1b, PyPI v1.0.0)** | ~Week 6-7 |
| **Attribution ship (1c, PyPI v1.1.0)** | ~Week 7-8 |
| **Phase 1.5 ready** | ~Week 8 |

---

## Key Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | US-1b.01 (engine extraction) takes full 32hrs | 3+ week slip | Start early. Defer edge cases (ambiguous parallel groups, cap overflow) to fast-follow. |
| 2 | MCP Python SDK immaturity | Integration friction | Pin SDK version. SDK has 97M+ monthly downloads — likely stable. |
| 3 | Pricing/heuristics drift between markdown and Python | Silent estimation errors | Drift detection test (US-1b.09a) catches this. Run in CI. |
| 4 | `report_session` adoption low (users forget) | Calibration doesn't improve for MCP users | Phase 1c addresses this. Document clearly in Phase 1b. |
| 5 | Session recorder refactor (US-1c.03) breaks Claude Code path | Regression for existing users | Run existing 441 tests as regression suite before and after. |

---

## Exit Criteria

### Phase 1b (PyPI v1.0.0)
- [ ] MCP server published and installable in Cursor/VS Code
- [ ] At least 1 non-Claude-Code client can produce calibrated estimates (manual `report_session`)
- [ ] SKILL.md companion still works for Claude Code users
- [ ] PyPI package `tokencast` at v1.0.0
- [ ] CI/CD pipeline running tests on PR, publishing on release

### Phase 1c (PyPI v1.1.0)
- [ ] `report_step_cost` tool supports Tier 2 attribution
- [ ] Step costs persisted to disk (survives crashes and compaction)
- [ ] E2E test: non-Claude-Code client produces per-step calibrated data
- [ ] Claude Code sidecar path unchanged (441+ existing tests pass)
- [ ] Attribution protocol documented

### Phase 1.5 Readiness
- [ ] Opt-in telemetry mechanism in place (US-PM.01)
- [ ] Team sharing waitlist hook active after 5+ sessions (US-PM.02)
- [ ] Graceful degradation for first-run and error cases (US-PM.04)

---

## Architecture Decisions (for implementers)

### Dict-based routing layer (from Q3 override)

The public Python API is:

```python
# tokencast public API — stable contract
def estimate_cost(params: dict) -> dict: ...
def report_session(params: dict) -> dict: ...
def report_step_cost(params: dict) -> dict: ...
def get_calibration_status(params: dict) -> dict: ...
def get_cost_history(params: dict) -> dict: ...
```

- Input/output dicts match MCP tool schemas exactly
- MCP tools are thin wrappers that pass through to these functions
- CI users call `from tokencast import estimate_cost` directly
- Adding optional dict keys is always backward-compatible
- Internal refactors never touch the public surface

### Independent versioning (from Q6 override)

- **PyPI package**: follows own semver starting from 0.1.0. Ships as v1.0.0 with MCP server.
- **SKILL.md algorithm**: stays at 2.x line. Versions independently.
- Both version numbers documented in README.
- Rationale: the two artifacts change for different reasons (MCP bugs vs heuristic tweaks). Unified versioning couples release cadences unnecessarily.

### Step cost persistence (from 1c Q2 override)

- Accumulated step costs persist to disk using atomic-rename pattern (same as `active-estimate.json`)
- File location: `calibration/step-costs-{hash}.json` (or similar, co-located with active estimate)
- Survives MCP server crashes and Claude Code compaction
- Cleared when `report_session` fires or new `estimate_cost` call is made
- Rationale: v2.1.0 proved that in-memory-only state is lost during compaction. The persistence pattern is already built and debugged.
