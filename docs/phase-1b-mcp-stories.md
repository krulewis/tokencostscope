# Phase 1b: Build MCP Server — User Story Decomposition

*Architecture review date: 2026-03-25*
*Author: Architect Agent*
*Input: enterprise-strategy-v2.md, SKILL.md (v2.1.0), codebase review*

---

## 1. Architecture Overview

### What the MCP Server Looks Like

The MCP server is a Python process that exposes tokencast's estimation engine as MCP tools. It runs locally (stdio transport), reads the same `calibration/` directory as the current SKILL.md-based system, and is installable via `npx`, `uvx`, or manual config in any MCP-compatible client.

```
┌─────────────────────────────────────────────────────┐
│  MCP Client (Cursor, VS Code, Claude Code, etc.)    │
└────────────────────┬────────────────────────────────┘
                     │ stdio (JSON-RPC)
┌────────────────────▼────────────────────────────────┐
│  tokencast-mcp (Python, MCP SDK)               │
│                                                      │
│  Tools:                                              │
│    estimate_cost(plan, metadata) → estimate table    │
│    get_calibration_status()      → health dashboard  │
│    get_cost_history(window)      → session history   │
│    report_session(actual_cost, step_actuals, ...)    │
│                                                      │
│  Engine Layer (new):                                 │
│    estimation_engine.py — Steps 0–4 as Python code  │
│    pricing.py           — pricing data as module     │
│    heuristics.py        — heuristic params as module │
│                                                      │
│  Existing modules (reused directly):                 │
│    calibration_store.py — read/write calibration     │
│    update-factors.py    — recompute factors          │
│    tokencast-status.py — health analysis        │
└────────────────────┬────────────────────────────────┘
                     │ file I/O
┌────────────────────▼────────────────────────────────┐
│  calibration/                                        │
│    factors.json, history.jsonl, active-estimate.json │
└─────────────────────────────────────────────────────┘
```

### Key Architectural Decisions

1. **Transport: stdio only for Phase 1b.** HTTP/SSE is deferred to Phase 2+. Every target IDE (Cursor, VS Code, Windsurf, Claude Code) supports stdio MCP servers. stdio is simpler, requires no auth, and matches the "local-first" calibration model.

2. **The estimation algorithm must become executable Python code.** Today it lives in SKILL.md as a prompt-driven algorithm that an LLM executes. An MCP tool receives structured input and must return structured output — no LLM in the loop. This is the single largest work item in Phase 1b.

3. **Session learning via explicit `report_session` tool.** SKILL.md uses a Stop hook (`learn.sh`) that fires at session end. MCP has no lifecycle hooks. Instead, the MCP client (or user) calls `report_session()` with actual cost data. This is a fundamental interaction model change.

4. **Calibration directory is project-scoped by default.** The MCP server resolves `calibration/` relative to the project root (workspace directory passed at server init). This matches the current SKILL.md behavior.

5. **SKILL.md companion remains.** The MCP server and SKILL.md coexist. Claude Code users can use either. The MCP server is the growth path; SKILL.md is the fallback.

---

## 2. User Stories

### US-1b.01: Extract Estimation Algorithm to Python Engine

**As a** developer building the MCP server, **I want** the SKILL.md estimation algorithm (Steps 0-4) implemented as a callable Python module, **so that** estimation can run programmatically without LLM interpretation.

**Acceptance criteria:**
- [ ] `estimation_engine.py` module with a `compute_estimate(plan_params) -> EstimateResult` function
- [ ] Implements Steps 1-4 of SKILL.md: load references, resolve inputs, per-step calculation, sum and format
- [ ] Step 3a (base tokens), 3b (complexity), 3c (context accumulation + parallel discount), 3d (cost per band with three-term cache formula), 3e (calibration factor 5-level precedence chain) all implemented
- [ ] Step 3.5 (PR Review Loop) implemented post-step-loop
- [ ] Reads pricing data from a Python module (not by parsing markdown)
- [ ] Reads heuristic parameters from a Python module (not by parsing markdown)
- [ ] Reads `factors.json` via `calibration_store.read_factors()`
- [ ] Returns structured result: per-step costs, band totals, calibration sources, parallel group info
- [ ] Output matches SKILL.md output for the same inputs (verified by test cases derived from `references/examples.md`)
- [ ] No file I/O for pricing or heuristics (loaded at import time); calibration is the only runtime I/O

**T-shirt estimate:** XL (16-32hrs)

**Depends on:** US-1b.02
**Blocks:** US-1b.04, US-1b.05, US-1b.06

**Notes:**
- This is the critical path item. The SKILL.md algorithm is ~500 lines of structured prose with formulas, conditional logic, and edge cases. Translating it to deterministic Python requires handling every branch.
- Key complexity areas: file size bracket computation (Step 0 item 2), parallel group detection (Step 0 item 8), 5-level calibration precedence (Step 3e), PR Review Loop geometric decay (Step 3.5).
- Step 0 (infer inputs from context) is partially out of scope — the MCP tool receives structured input, not raw plan text. However, the file measurement logic (wc -l) should be extractable as a utility function for clients that want it.
- The three-term cache cost formula (Step 3d) must exactly match SKILL.md: `input_accum * (1 - cache_rate) * price_in + input_accum * cache_rate * cache_write_fraction * price_cw + input_accum * cache_rate * (1 - cache_write_fraction) * price_cr`.

---

### US-1b.02: Extract Pricing and Heuristic Data to Python Modules

**As a** developer building the estimation engine, **I want** pricing and heuristic parameters available as importable Python data structures, **so that** the engine doesn't need to parse markdown files at runtime.

**Acceptance criteria:**
- [ ] `pricing.py` module containing model prices, cache rates per band, step-to-model mapping, and `last_updated` / `staleness_warning_days`
- [ ] `heuristics.py` module containing activity token table, pipeline step activity counts, complexity multipliers, band multipliers, PR Review Loop defaults, parallel accounting parameters, per-step calibration params, file size bracket definitions, time-decay params, mid-session tracking params
- [ ] Both modules are importable (no side effects on import)
- [ ] Values exactly match `references/pricing.md` and `references/heuristics.md`
- [ ] A test validates that the Python module values match the markdown source (prevents drift)
- [ ] Clear comments indicating these are derived from the markdown references and should be updated together

**T-shirt estimate:** M (4-8hrs)

**Depends on:** None
**Blocks:** US-1b.01

**Notes:**
- Decision: extract to Python modules rather than parse markdown at runtime. Parsing markdown is fragile (regex on tables), slow, and unnecessary since the values change infrequently. The drift-detection test catches inconsistencies.
- Alternative considered: parse markdown at startup. Rejected because it couples the MCP server to markdown table format, adds regex complexity, and provides no benefit since values are updated manually.
- The markdown files remain the source of truth for documentation. The Python modules are derived artifacts kept in sync by the drift test.

---

### US-1b.03: MCP Server Scaffold with stdio Transport

**As a** developer setting up the MCP server, **I want** a working MCP server skeleton that registers tools and handles the JSON-RPC protocol over stdio, **so that** I can incrementally add tool implementations.

**Acceptance criteria:**
- [ ] `mcp_server.py` (or `server.py`) entry point using the official MCP Python SDK (`mcp` package)
- [ ] Server runs via `python -m tokencast_mcp` or `python mcp_server.py`
- [ ] Registers tool stubs for: `estimate_cost`, `get_calibration_status`, `get_cost_history`, `report_session`
- [ ] Accepts `--calibration-dir` argument to override the default calibration directory
- [ ] Accepts `--project-dir` argument for file measurement (wc -l) resolution
- [ ] Responds to MCP `initialize`, `tools/list`, and `tools/call` methods
- [ ] Returns proper MCP error responses for unknown tools or malformed input
- [ ] Includes a `pyproject.toml` with dependencies (`mcp>=1.0`) and entry point
- [ ] Passes MCP protocol smoke test (connect via stdio, list tools, call a stub)

**T-shirt estimate:** M (4-8hrs)

**Depends on:** None
**Blocks:** US-1b.04, US-1b.05, US-1b.06, US-1b.07

**Notes:**
- The MCP Python SDK handles protocol framing, JSON-RPC dispatch, and schema generation from type hints. The scaffold should use `@mcp.tool()` decorators or equivalent.
- Package structure: `src/tokencast_mcp/` or `mcp/` subdirectory in the repo. Avoid polluting the existing `scripts/` directory.
- stdio transport is the only transport for Phase 1b. The scaffold should not include HTTP/SSE code.

---

### US-1b.04: Implement `estimate_cost` MCP Tool

**As a** developer using Cursor (or any MCP client), **I want** to call `estimate_cost` with plan metadata and receive a structured cost estimate, **so that** I can see estimated costs before running an agent workflow.

**Acceptance criteria:**
- [ ] Tool accepts input schema:
  ```json
  {
    "size": "M",                          // required: XS|S|M|L
    "files": 5,                           // required: int
    "complexity": "medium",               // required: low|medium|high
    "steps": ["Research Agent", ...],     // optional: list of step names
    "project_type": "greenfield",         // optional: default "greenfield"
    "language": "python",                 // optional: default "unknown"
    "review_cycles": 2,                   // optional: default from heuristics
    "avg_file_lines": null,               // optional: override file size
    "parallel_groups": [["Research Agent", "Architect Agent"]],  // optional
    "file_paths": ["src/foo.py", ...]     // optional: for auto-measurement
  }
  ```
- [ ] Tool returns structured output:
  ```json
  {
    "version": "2.1.0",
    "estimate": {
      "optimistic": 3.44,
      "expected": 6.24,
      "pessimistic": 20.46
    },
    "steps": [
      {
        "name": "Research Agent",
        "model": "Sonnet",
        "calibration": {"source": "S", "factor": 0.82},
        "optimistic": 1.02, "expected": 1.70, "pessimistic": 5.10
      }
    ],
    "metadata": {
      "size": "M", "files": 5, "complexity": "medium",
      "file_brackets": {"small": 1, "medium": 3, "large": 1},
      "parallel_groups": [...],
      "pricing_last_updated": "2026-03-04",
      "pricing_stale": false
    }
  }
  ```
- [ ] When `file_paths` is provided and `--project-dir` is set, runs file measurement (wc -l) and computes brackets
- [ ] When `file_paths` is not provided, uses `avg_file_lines` override or medium default
- [ ] Writes `active-estimate.json` and `last-estimate.md` to calibration directory (for learning loop compatibility)
- [ ] Returns human-readable text summary in addition to structured data (for LLM clients that render tool output as text)
- [ ] Validates input and returns clear error messages for invalid parameters

**T-shirt estimate:** L (8-16hrs)

**Depends on:** US-1b.01, US-1b.03
**Blocks:** US-1b.08, US-1b.09

**Notes:**
- The tool should also return the formatted markdown table (matching SKILL.md output template) as a `text` field, so LLM clients can display it naturally.
- `file_paths` handling: resolve relative to `--project-dir`. Use subprocess `wc -l` with proper quoting (macOS spaces). Cap at 30 files per heuristics.md.
- Step 0's plan-text inference (parallel group detection from keywords, step inference from plan) is NOT in scope for the MCP tool — the client must provide structured input. This is intentional: MCP tools receive structured data, not prose.

---

### US-1b.05: Implement `get_calibration_status` MCP Tool

**As a** developer using VS Code + Copilot, **I want** to check my calibration health via MCP, **so that** I can see whether my estimates are well-calibrated without running a separate command.

**Acceptance criteria:**
- [ ] Tool accepts optional input: `{"window": "30d"}` (same spec as `tokencast-status.py`)
- [ ] Delegates to `tokencast-status.py`'s `build_status_output()` function (import via importlib)
- [ ] Returns structured JSON matching `tokencast-status.py --json` output (schema_version: 1)
- [ ] Also returns a human-readable text summary for LLM clients
- [ ] Works with empty calibration directory (returns "no data yet" status)
- [ ] Handles missing/malformed calibration files gracefully

**T-shirt estimate:** S (2-4hrs)

**Depends on:** US-1b.03
**Blocks:** US-1b.09

**Notes:**
- This is a thin wrapper. `tokencast-status.py` already has `build_status_output()` as a testable function. The MCP tool calls it and formats the result.
- Import via importlib.util (filename has a hyphen — same pattern used by tests).

---

### US-1b.06: Implement `get_cost_history` MCP Tool

**As a** developer, **I want** to query my cost estimation history via MCP, **so that** I can see past estimates, actuals, and trends without reading JSONL files manually.

**Acceptance criteria:**
- [ ] Tool accepts optional input:
  ```json
  {
    "window": "30d",        // optional: "30d", "10", "all"
    "include_outliers": false  // optional: default false
  }
  ```
- [ ] Reads history via `calibration_store.read_history()`
- [ ] Returns list of session records with: timestamp, size, expected_cost, actual_cost, ratio, steps, band_hit, attribution_method
- [ ] Applies window filtering (same logic as status.py)
- [ ] Excludes outliers by default (ratio > 3.0 or < 0.2)
- [ ] Returns summary statistics: mean_ratio, median_ratio, session_count, pct_within_expected
- [ ] Works with empty history (returns empty list + zeros)

**T-shirt estimate:** S (2-4hrs)

**Depends on:** US-1b.03
**Blocks:** US-1b.09

**Notes:**
- Reuses calibration_store.read_history() and outlier constants from update-factors.py (OUTLIER_HIGH=3.0, OUTLIER_LOW=0.2).
- Window filtering logic can be extracted from tokencast-status.py's windowing code.

---

### US-1b.07: Implement `report_session` MCP Tool (Learning Loop)

**As a** developer finishing an agent workflow, **I want** to report actual costs to tokencast via MCP, **so that** calibration improves over time without needing Claude Code's Stop hook.

**Acceptance criteria:**
- [ ] Tool accepts input:
  ```json
  {
    "actual_cost": 8.50,                  // required: total actual cost
    "step_actuals": {                     // optional: per-step breakdown
      "Research Agent": 1.20,
      "Implementation": 4.50
    },
    "turn_count": 45,                     // optional
    "review_cycles_actual": 3             // optional
  }
  ```
- [ ] Reads the most recent `active-estimate.json` to pair actual with estimate
- [ ] Computes ratio, step_ratios (same logic as learn.sh RECORD block)
- [ ] Appends to history via `calibration_store.append_history()` (which triggers factor recomputation)
- [ ] Cleans up `active-estimate.json` after recording
- [ ] Returns confirmation with: ratio, band_hit, calibration status update
- [ ] Returns error if no active estimate exists (nothing to pair with)
- [ ] Handles the case where `step_actuals` is partial (not all estimated steps have actuals) — uses proportional fallback for missing steps

**T-shirt estimate:** M (4-8hrs)

**Depends on:** US-1b.03
**Blocks:** US-1b.09

**Notes:**
- This replaces `tokencast-learn.sh` for MCP clients. The shell script remains for Claude Code's Stop hook.
- Critical: the RECORD logic in learn.sh is ~80 lines of Python embedded in shell. It should be extracted to a shared function that both learn.sh and report_session use. This prevents logic divergence.
- Open question: how does the MCP client know `actual_cost`? Possibilities:
  1. Client's IDE tracks API costs natively (Cursor does this)
  2. Client estimates from token counts in responses
  3. Manual entry by the user
  4. Phase 1c attribution protocol provides this automatically
  For Phase 1b, accept it as user-provided input. Phase 1c will add automatic attribution.

---

### US-1b.08: Package, Entry Point, and Installation Config

**As a** developer installing tokencast for the first time, **I want** a one-command installation process for my MCP client, **so that** I can start getting cost estimates immediately.

**Acceptance criteria:**
- [ ] `pyproject.toml` with package metadata, dependencies, and entry points
- [ ] Package installable via `pip install .` (local) and eventually `pip install tokencast` (PyPI)
- [ ] Entry point: `tokencast-mcp` command starts the server
- [ ] Also runnable via: `python -m tokencast_mcp`
- [ ] Also runnable via: `uvx tokencast` (for MCP registry compatibility)
- [ ] Config examples for:
  - Claude Code (`~/.claude/settings.json` mcpServers entry)
  - Cursor (`.cursor/mcp.json`)
  - VS Code + Copilot (`.vscode/mcp.json`)
  - Windsurf (MCP config)
- [ ] README section with quickstart for each IDE
- [ ] Server logs to stderr (not stdout — stdout is the MCP stdio transport)

**T-shirt estimate:** M (4-8hrs)

**Depends on:** US-1b.04
**Blocks:** US-1b.10

**Notes:**
- Package name: `tokencast` on PyPI (not `tokencast-mcp` — keep it clean). The MCP server is the primary distribution, not a secondary artifact.
- Dependencies should be minimal: `mcp>=1.0`, standard library otherwise. Avoid heavy deps that slow install.
- The `--calibration-dir` and `--project-dir` flags should be passable via MCP server config args in each IDE's config format.

---

### US-1b.09: Test Suite for MCP Server and Estimation Engine

**As a** developer maintaining tokencast, **I want** comprehensive tests for the estimation engine and MCP tools, **so that** I can verify correctness and catch regressions.

**Acceptance criteria:**
- [ ] Engine unit tests:
  - Per-step cost calculation matches worked examples in `references/examples.md`
  - Three-term cache formula produces correct results
  - 5-level calibration precedence chain (per-step > per-signature > size-class > global > 1.0)
  - PR Review Loop geometric decay matches SKILL.md formula
  - Parallel discount application
  - File size bracket computation from line counts
  - Edge cases: zero files, no calibration data, stale pricing, N=0 review cycles
- [ ] MCP tool integration tests:
  - `estimate_cost` with various input combinations
  - `get_calibration_status` with empty/populated calibration
  - `get_cost_history` with windowing and outlier filtering
  - `report_session` pairing with active estimate
  - `report_session` error when no active estimate
- [ ] Protocol tests:
  - Server starts and responds to `initialize`
  - `tools/list` returns all 4 tools with schemas
  - Invalid tool name returns proper error
  - Malformed input returns validation error
- [ ] Drift detection test: Python pricing/heuristics modules match markdown sources
- [ ] All tests runnable via `/usr/bin/python3 -m pytest tests/`

**T-shirt estimate:** L (8-16hrs)

**Depends on:** US-1b.01, US-1b.04, US-1b.05, US-1b.06, US-1b.07
**Blocks:** US-1b.10

**Notes:**
- The MCP Python SDK provides test utilities for spinning up a server in-process and sending tool calls. Use these rather than spawning subprocess servers.
- Engine tests should be independent of MCP (test the Python functions directly). MCP tests verify the tool layer.
- Existing test count is 441. Target: 500+ after Phase 1b.

---

### US-1b.10: MCP Registry Publication and Documentation

**As a** developer discovering tokencast for the first time, **I want** to find it in MCP registries and install it with one click, **so that** I don't need to manually configure anything.

**Acceptance criteria:**
- [ ] Published to at least one MCP registry (smithery.ai, mcp.run, or equivalent)
- [ ] Registry listing includes: description, tool list, install command, screenshot of output
- [ ] Published to PyPI as `tokencast`
- [ ] `README.md` updated with MCP-first installation instructions
- [ ] `docs/wiki/Installation.md` updated with per-IDE setup guides
- [ ] `docs/wiki/Home.md` updated to reflect MCP as primary distribution
- [ ] GitHub repo description and topics updated (add "mcp", "mcp-server", "cost-estimation")

**T-shirt estimate:** M (4-8hrs)

**Depends on:** US-1b.08, US-1b.09
**Blocks:** None

**Notes:**
- MCP registry submission processes vary. Some require a PR to a registry repo, others have self-service submission.
- PyPI publication requires a PyPI account and API token. Set up GitHub Actions for automated releases.
- The README should lead with MCP installation, with SKILL.md mentioned as a Claude-Code-specific companion.

---

### US-1b.11: Extract learn.sh Record Logic to Shared Python Module

**As a** developer maintaining both the SKILL.md learning path and the MCP learning path, **I want** the session recording logic in a single shared Python module, **so that** learn.sh and report_session use identical logic and don't diverge.

**Acceptance criteria:**
- [ ] New function in `calibration_store.py` (or new `session_recorder.py`): `build_history_record(estimate, actuals, step_actuals, review_cycles_actual, sidecar_path) -> dict`
- [ ] Encapsulates: ratio computation, step_ratios computation (sidecar vs proportional fallback), band_hit determination, record assembly
- [ ] learn.sh RECORD Python block refactored to call this function instead of inline logic
- [ ] `report_session` MCP tool calls the same function
- [ ] Both paths produce identical history records for the same inputs
- [ ] Tests verify record equivalence between learn.sh path and MCP path

**T-shirt estimate:** M (4-8hrs)

**Depends on:** None
**Blocks:** US-1b.07

**Notes:**
- The RECORD block in learn.sh is ~80 lines of Python embedded in shell. Extracting it to an importable function is straightforward but must be done carefully to preserve exact behavior (including the PR Review Loop exclusion, proportional fallback, continuation flag, etc.).
- learn.sh would then call: `python3 -c "from calibration_store import build_history_record; ..."` or add a new CLI subcommand to calibration_store.py.

---

## 3. Dependency Graph

```
US-1b.02 (Pricing/Heuristics modules)
    │
    ▼
US-1b.01 (Estimation Engine) ◄──────────────────┐
    │                                             │
    │                                             │
    ▼                                             │
US-1b.04 (estimate_cost tool) ◄── US-1b.03 (MCP Scaffold)
    │                                │
    │                                ├──► US-1b.05 (calibration_status tool)
    │                                │
    │                                ├──► US-1b.06 (cost_history tool)
    │                                │
    │                                └──► US-1b.07 (report_session tool) ◄── US-1b.11 (Shared record logic)
    │                                         │
    ▼                                         │
US-1b.08 (Package + Install) ◄───────────────┘
    │
    ▼
US-1b.09 (Test Suite) ◄── US-1b.01, .04, .05, .06, .07
    │
    ▼
US-1b.10 (Registry + Docs)
```

### Critical Path

```
US-1b.02 → US-1b.01 → US-1b.04 → US-1b.08 → US-1b.09 → US-1b.10
  (M)        (XL)        (L)         (M)         (L)         (M)
 4-8hrs     16-32hrs    8-16hrs     4-8hrs      8-16hrs     4-8hrs
```

**Critical path total: 44-88 hours**

### Parallelizable Work

- US-1b.02 and US-1b.03 and US-1b.11 can all start simultaneously (no dependencies)
- US-1b.05, US-1b.06, US-1b.07 can run in parallel once US-1b.03 is done
- US-1b.09 tests can begin for engine once US-1b.01 is done (MCP tool tests added incrementally)

---

## 4. Total Effort Estimate

| Story | T-shirt | Hours (range) |
|-------|---------|---------------|
| US-1b.01 | XL | 16-32 |
| US-1b.02 | M | 4-8 |
| US-1b.03 | M | 4-8 |
| US-1b.04 | L | 8-16 |
| US-1b.05 | S | 2-4 |
| US-1b.06 | S | 2-4 |
| US-1b.07 | M | 4-8 |
| US-1b.08 | M | 4-8 |
| US-1b.09 | L | 8-16 |
| US-1b.10 | M | 4-8 |
| US-1b.11 | M | 4-8 |
| **Total** | | **60-120 hrs** |

**With parallelism (3 parallel streams at peak):** ~44-88 hours on the critical path.

**At 10hrs/week:** 4.4-8.8 weeks elapsed. This aligns with the strategy's "4-6 weeks" estimate for the optimistic-to-expected range.

**Assessment:** The strategy's 4-6 week estimate is achievable if US-1b.01 (engine extraction) lands on the lower end. If it takes the full 32 hours (high complexity edge cases, extensive debugging), expect 7-9 weeks.

---

## 5. Risks and Open Questions

### Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| R1 | Engine extraction takes longer than estimated due to SKILL.md edge cases | Schedule slip (XL story is 50%+ of critical path) | Start with happy-path coverage; defer edge cases (ambiguous parallel groups, cap overflow) to a fast-follow |
| R2 | MCP Python SDK is immature or has breaking changes | Integration friction | Pin SDK version; review SDK changelog before starting. The SDK has 97M+ monthly downloads — it's stable. |
| R3 | Pricing/heuristics drift between markdown and Python modules | Silent estimation errors | Drift detection test (US-1b.09) catches this. Run in CI. |
| R4 | `report_session` adoption is low (users forget to call it) | Calibration doesn't improve for MCP users | Phase 1c (attribution decoupling) addresses this by making cost reporting automatic. For Phase 1b, document clearly and consider IDE-specific reminder patterns. |
| R5 | File measurement (wc -l) doesn't work in all MCP client environments | Degraded accuracy for file-size-aware estimates | Graceful fallback to medium default when wc -l fails. This is already handled in SKILL.md. |

### Open Questions (Requiring Human Input)

| # | Question | Options | Recommendation |
|---|----------|---------|----------------|
| Q1 | Package name on PyPI? | `tokencast` vs `tokencast-mcp` | `tokencast` — it's the primary distribution, not a companion |
| Q2 | Where to put MCP server code in the repo? | `src/tokencast_mcp/` vs `mcp/` vs root-level `server.py` | `src/tokencast_mcp/` — standard Python package layout, separates MCP code from existing scripts |
| Q3 | Should `estimate_cost` require all parameters or infer defaults? | Strict (all required) vs lenient (smart defaults) | Lenient with required minimum: `size` + `files` + `complexity`. Everything else has sensible defaults. |
| Q4 | Should the engine also produce the markdown table output? | Structured-only vs structured + markdown | Both. Structured for programmatic use; markdown for LLM clients that render tool output as text. |
| Q5 | Should `report_session` accept raw token counts (input/output/cache) instead of dollar cost? | Dollar cost only vs token counts vs both | Both. Dollar cost is the primary input; token counts are optional and converted to cost using pricing.py. This helps clients that track tokens but not dollars. |
| Q6 | Version bump: should Phase 1b be v3.0.0? | v2.1.x (minor) vs v3.0.0 (major) | v3.0.0 — this is a new distribution model and a new public API surface. The MCP tools are a breaking change in how tokencast is consumed. |
