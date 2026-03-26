# Phase 1 Sr PM Review: Story Completeness and Phase 1.5 Readiness

*Review date: 2026-03-25*
*Reviewer: Sr PM Agent*
*Inputs: phase-1b-mcp-stories.md, phase-1c-attribution-stories.md, enterprise-strategy-v2.md*

---

## Section 1: Exit Criteria Coverage

The strategy defines three Phase 1 exit criteria. Here is how the stories map to each.

### EC-1: "MCP server published and installable in Cursor/VS Code"

| Story | Contribution |
|-------|-------------|
| US-1b.03 | MCP scaffold with stdio transport, tool registration |
| US-1b.04 | `estimate_cost` tool — the core value proposition |
| US-1b.05 | `get_calibration_status` tool |
| US-1b.06 | `get_cost_history` tool |
| US-1b.07 | `report_session` tool (learning loop) |
| US-1b.08 | Package, entry point, config examples for Claude Code, Cursor, VS Code, Windsurf |
| US-1b.10 | MCP registry publication, PyPI, per-IDE setup docs |

**Verdict: FULLY COVERED.** The chain from scaffold to publication is complete. Config examples for Cursor and VS Code are explicitly in US-1b.08 acceptance criteria.

### EC-2: "At least 1 non-Claude-Code client can produce calibrated estimates"

"Calibrated estimates" requires two things: (a) produce an estimate using calibration factors, and (b) report actuals back to improve those factors.

| Requirement | Stories |
|-------------|---------|
| Produce calibrated estimate | US-1b.01 (engine with 5-level precedence), US-1b.04 (`estimate_cost` reads factors.json) |
| Report actuals for learning | US-1b.07 (`report_session`), US-1b.11 (shared record logic) |
| Attribution without JSONL | US-1c.01–US-1c.05 (v2 cost events, direct attribution path) |

**Verdict: COVERED, but with a dependency subtlety.** US-1b.07 (`report_session`) accepts `actual_cost` as user input, which is sufficient for basic calibration. However, for a non-Claude-Code client to produce *per-step* calibrated estimates automatically (not manually entered), it needs the v2 attribution protocol from Phase 1c. Specifically:

- A Cursor user calling `report_session` with manual `actual_cost` satisfies the exit criterion minimally.
- A Cursor user getting *automatic* per-step attribution requires US-1c.01 + US-1c.02 + US-1c.03 + US-1c.05.

The exit criterion says "produce calibrated estimates," not "produce per-step attribution." **The criterion is met by Phase 1b alone**, but the experience is degraded without 1c. This should be documented as a known limitation.

### EC-3: "SKILL.md companion still works for Claude Code users"

| Story | Contribution |
|-------|-------------|
| US-1b.11 | Shared record logic ensures learn.sh and report_session use identical logic — no regression |
| US-1c.04 | Dual-path dispatcher preserves v1 JSONL-based learning |
| US-1c.05 | learn.sh becomes thin wrapper but external behavior preserved |

The 1c backward compatibility plan (Section 6) explicitly guarantees no breaking changes to agent-hook.sh, learn.sh, sum_session_by_agent(), active-estimate.json, or factors.json.

**Verdict: FULLY COVERED.** Both architects made backward compatibility a first-class concern. The guarantees are explicit and testable.

---

## Section 2: Phase 1.5 Readiness Gaps

### Experiment 1: Opt-in usage telemetry

**Requirement:** Anonymous, opt-in telemetry in the MCP server — session count, estimate accuracy, calibration depth, framework used.

**Current coverage:** NONE. No story in Phase 1b addresses telemetry. The MCP server has no mechanism to collect or report anonymous usage data.

**Gap analysis:** The MCP server needs:
1. A telemetry collection point (after each `estimate_cost` and `report_session` call)
2. An opt-in mechanism (config flag, first-run prompt, or environment variable)
3. A lightweight reporting endpoint (could be as simple as a GitHub-hosted ping endpoint or a free analytics service)
4. Privacy controls (what data is collected, how to disable)

**Proposed story:**

> **US-PM.01: Add opt-in anonymous telemetry to MCP server**
>
> **As a** tokencast maintainer, **I want** the MCP server to optionally collect anonymous usage metrics (session count, estimate accuracy ratio, calibration depth, client framework), **so that** Phase 1.5 can measure adoption, retention, and accuracy trends across users.
>
> **Acceptance criteria:**
> - [ ] Telemetry is OFF by default — requires explicit opt-in via `--telemetry` flag or `TOKENCOSTSCOPE_TELEMETRY=1` env var
> - [ ] First-run message explains what is collected and how to opt in
> - [ ] Collected metrics: session count, mean accuracy ratio, number of calibrated factors, client name (from MCP init), framework (from `source` field in v2 events)
> - [ ] No PII, no project names, no file paths, no cost amounts — only aggregate metrics
> - [ ] Metrics are batched and sent to a lightweight endpoint (e.g., simple POST to a Vercel serverless function or equivalent)
> - [ ] Telemetry collection does not affect server performance (fire-and-forget, timeout after 2s)
> - [ ] Works when the endpoint is unreachable (fail silently)
>
> **T-shirt estimate:** M (4-8hrs)
> **Depends on:** US-1b.03, US-1b.04, US-1b.07
> **Classification:** MUST-HAVE (blocks Experiment 1 entirely)

### Experiment 2: "Share with team" waitlist

**Requirement:** A `team_sharing_interest()` MCP tool or similar mechanism, surfaced after 5+ calibrated sessions.

**Current coverage:** NONE. No story addresses this.

**Gap analysis:** This requires:
1. A mechanism to count calibrated sessions (US-1b.06 `get_cost_history` provides session count — foundation exists)
2. A trigger mechanism (after `report_session` returns, check if session count >= 5)
3. A tool or notification to surface the waitlist prompt
4. A way to record interest (email, team size) — could be as simple as opening a URL

**Proposed story:**

> **US-PM.02: Add "Share with team" waitlist hook to MCP server**
>
> **As a** user who has completed 5+ calibrated sessions, **I want** to be invited to express interest in team sharing features, **so that** the maintainer can gauge demand for shared calibration.
>
> **Acceptance criteria:**
> - [ ] After `report_session` completes and session count >= 5, the tool response includes a `team_sharing_cta` field with a short message and URL
> - [ ] The URL points to a lightweight form (Google Form, Typeform, or GitHub Discussion) collecting: email (optional), team size, current tools used
> - [ ] The CTA appears at most once per session (not on every `report_session` call)
> - [ ] The CTA can be suppressed via `--no-cta` flag or env var
> - [ ] CTA is only shown when telemetry is enabled (respects user's opt-in posture)
>
> **T-shirt estimate:** S (2-4hrs)
> **Depends on:** US-1b.07, US-1b.06
> **Classification:** MUST-HAVE (blocks Experiment 2 — the strategy specifically calls for this mechanism)

### Experiment 3: Community distribution test

**Requirement:** HN, Reddit, Discord materials — README, blog post hook, worked examples.

**Current coverage:** PARTIAL.
- US-1b.10 covers README update, wiki updates, and MCP registry listing with "screenshot of output"
- US-1b.08 covers quickstart per IDE

**Gap analysis:** What's missing for a compelling community launch:
1. A **blog-post-ready worked example** that shows the estimation table, calibration learning over sessions, and before/after accuracy. This goes beyond the dry `references/examples.md` — it needs narrative.
2. A **demo GIF or screenshot set** showing the tool in action in Cursor (not just Claude Code).
3. A **comparison section** in README: "How is this different from LangSmith/Helicone/budget caps?"

These are content tasks, not engineering tasks. They don't need separate user stories — they should be acceptance criteria additions to US-1b.10.

**Proposed addition to US-1b.10:**

> Add to US-1b.10 acceptance criteria:
> - [ ] README includes a "How is this different?" section comparing to LangSmith, Helicone, Portkey, and budget caps
> - [ ] README includes a worked example with narrative (not just table output) showing estimation → execution → learning → improved estimate
> - [ ] At least one screenshot/GIF showing the tool in a non-Claude-Code client (Cursor or VS Code)

**Classification:** NICE-TO-HAVE (improves Experiment 3 effectiveness but doesn't block it — the MCP registry listing alone enables community distribution)

### Experiment 4: Direct outreach

**Requirement:** Easy install path for non-technical-ish users.

**Current coverage:** GOOD.
- US-1b.08 provides `pip install tokencast`, `uvx tokencast`, and per-IDE config examples
- US-1b.10 provides MCP registry one-click install

**Gap analysis:** The install path is covered, but there's no **getting-started guide** that takes a new user from install to first calibrated estimate in under 5 minutes. US-1b.10 has "quickstart for each IDE" but the acceptance criteria don't specify the depth or the "time to first value" experience.

**Proposed story:**

> **US-PM.03: Create "First Estimate in 5 Minutes" quickstart guide**
>
> **As a** new user installing tokencast for the first time, **I want** a guided quickstart that walks me from installation to my first calibrated estimate, **so that** I can see the tool's value before investing time in learning all features.
>
> **Acceptance criteria:**
> - [ ] Quickstart guide covers: install, configure MCP client, run first estimate with sample data, understand the output
> - [ ] Available as both README section and standalone `docs/quickstart.md`
> - [ ] Includes a sample plan input (pre-filled) so the user can call `estimate_cost` immediately without crafting their own input
> - [ ] Time to complete: under 5 minutes for someone who already has an MCP client configured
> - [ ] Tested with at least one non-developer (PM, tech lead) to validate clarity
>
> **T-shirt estimate:** S (2-4hrs)
> **Depends on:** US-1b.08, US-1b.10
> **Classification:** NICE-TO-HAVE (improves Experiment 4 conversion but not a hard blocker — early access users will get hands-on help regardless)

### Experiment 5: Shared calibration simulation

**Requirement:** Take existing history, simulate pooled factors. Does the extracted estimation engine make this feasible?

**Current coverage:** GOOD.
- US-1b.01 extracts the estimation engine to Python — this makes programmatic factor computation possible
- US-1b.02 extracts pricing and heuristics to importable modules
- `update-factors.py` already computes factors from history
- `calibration_store.py` provides read/write for history and factors

**Gap analysis:** The simulation requires:
1. Programmatic access to factor computation (already exists in `update-factors.py`)
2. Ability to generate synthetic histories (scripting task, not a product feature)
3. Ability to compute estimates with different factor sets (US-1b.01 enables this)

**No new story needed.** The existing stories provide adequate foundation. The simulation is a one-off script that uses the extracted engine and existing calibration infrastructure.

### Phase 1.5 Readiness Summary

| Experiment | Foundation Status | New Stories Needed |
|------------|-------------------|-------------------|
| Exp 1: Telemetry | NOT COVERED | **US-PM.01** (MUST-HAVE) |
| Exp 2: Waitlist | NOT COVERED | **US-PM.02** (MUST-HAVE) |
| Exp 3: Community | PARTIAL | Additions to US-1b.10 (NICE-TO-HAVE) |
| Exp 4: Outreach | GOOD | **US-PM.03** (NICE-TO-HAVE) |
| Exp 5: Simulation | GOOD | None |

---

## Section 3: Dependency Graph Review

### Circular Dependencies

**None found.** Both dependency graphs are acyclic. Verified by tracing all chains:
- 1b: US-1b.02→01→04→08→09→10 (critical path, no cycles)
- 1c: US-1c.01→02→04→05→09 (critical path, no cycles)

### Missing Dependencies

**Finding 3.1: US-1b.08 depends on US-1b.07, but this isn't declared.**

US-1b.08 (Package + Install) declares dependency on US-1b.04 only. But the `pyproject.toml` entry point needs all four tools implemented to ship a complete package. The dependency graph shows US-1b.08 ← US-1b.07 via a dotted arrow, but the story text says "Depends on: US-1b.04."

**Recommendation:** US-1b.08 should depend on US-1b.03 (scaffold) and treat tool implementation as a soft dependency — the package can be built with stub tools and updated as tools are completed. This is already implied by the parallelism notes but should be explicit.

**Finding 3.2: US-1b.09 (Test Suite) has a heavy dependency chain that bottlenecks the critical path.**

US-1b.09 depends on US-1b.01, .04, .05, .06, .07 — meaning ALL tool implementations must complete before comprehensive testing begins. This is antipattern for a project of this size.

**Recommendation:** Split US-1b.09 into two stories:
- US-1b.09a: Engine unit tests (depends on US-1b.01 only — can start earlier)
- US-1b.09b: MCP tool integration + protocol tests (depends on US-1b.04-.07)

This moves engine testing off the critical path and parallelizes it with tool implementation.

**Finding 3.3: US-1c.05 depends on US-1c.03, but the dependency graph shows a different chain.**

The dependency graph text shows US-1c.03→US-1c.05 (writer→learning module), and the story text confirms "Depends on: US-1c.02, US-1c.03." But the graph diagram in Section 4 of 1c has some ambiguity — it shows US-1c.05 under US-1c.03 but also referencing US-1c.04. Reading more carefully: US-1c.04 depends on US-1c.05 (not the reverse) via the dispatcher needing the extracted learning logic. The diagram is correct but could be clearer.

**No action needed** — the dependencies are actually correct when read carefully.

### Cross-Phase Dependencies

**Finding 3.4: The cross-phase dependencies are under-specified.**

The 1c document mentions "US-1c.03 BLOCKS US-1b.XX" and "US-1c.05 BLOCKS US-1b.XX" — using placeholder story IDs. These should be resolved:

- US-1c.03 (v2 writer) blocks US-1b.07 (`report_session`) for the v2 cost event path. However, US-1b.07 can function without US-1c.03 — it just won't write v2 sidecar events. **This is a soft dependency, not a hard block.**
- US-1c.05 (learning module) blocks US-1b.07 (`report_session`) for shared record logic. BUT US-1b.11 (shared record logic) covers the same extraction work. **US-1b.11 and US-1c.05 overlap significantly.** See Finding 3.5.

**Finding 3.5: US-1b.11 and US-1c.05 have significant scope overlap.**

- US-1b.11: "Extract learn.sh record logic to shared Python module" — produces `build_history_record()` callable by both learn.sh and `report_session`
- US-1c.05: "Extract learning logic into importable Python module" — produces `learn_from_session()` callable by both learn.sh and MCP server

These are doing the same thing from different angles. US-1c.05 is a superset of US-1b.11 (it includes v1/v2 path dispatch, not just record construction). If 1c runs after 1b, US-1b.11's extraction will be partially redone by US-1c.05.

**Recommendation:** Merge US-1b.11 into US-1c.05. Have US-1b.07 (`report_session`) initially use inline record construction logic (duplicating learn.sh temporarily), then US-1c.05 extracts and unifies both. This avoids doing the extraction twice. Alternatively, do US-1c.05 first (since 1c can start early per the strategy timeline) and have US-1b.07 consume its output.

### Critical Path Analysis

**Phase 1b critical path (from story text):**
```
US-1b.02 → US-1b.01 → US-1b.04 → US-1b.08 → US-1b.09 → US-1b.10
  (M)        (XL)        (L)         (M)         (L)         (M)
 4-8hrs     16-32hrs    8-16hrs     4-8hrs      8-16hrs     4-8hrs
Total: 44-88 hrs
```

**Phase 1c critical path:**
```
US-1c.01 → US-1c.02 → US-1c.04 → US-1c.05 → US-1c.09
  (XS)       (M)         (M)        (L)         (M)
 1-2hrs     4-8hrs      4-8hrs     8-16hrs     4-8hrs
Total: 21-42 hrs
```

**Combined, with overlap starting at 1c.01 immediately:**

The strategy timeline shows 1b starting at Month 1 and 1c starting at Month 2. The 1c architect recommends starting 1c immediately (overlapping). Given the critical path analysis:

- 1b critical path: 44-88 hrs (4.4-8.8 weeks at 10hrs/week)
- 1c critical path: 21-42 hrs (2.1-4.2 weeks at 10hrs/week)
- Overlap: 1c.01-1c.03 have no dependency on 1b (can start immediately)
- 1c.05 blocks 1b.07 (or overlaps with 1b.11)

**Realistic combined timeline: 6-10 weeks** if 1c starts in parallel with 1b after US-1b.03 is complete (so the developer isn't context-switching between scaffold and schema work simultaneously).

---

## Section 4: Risk and Effort Calibration

### Architect Estimates vs Strategy Allocation

| Phase | Architect Estimate | Strategy Allocation | Delta |
|-------|-------------------|-------------------|-------|
| 1b | 60-120 hrs (6-12 weeks) | 4-6 weeks | Strategy is optimistic by 0-6 weeks |
| 1c | 29-58 hrs (3-6 weeks) | 2-3 weeks | Strategy is optimistic by 1-3 weeks |
| Combined | 89-178 hrs (9-18 weeks) | ~6-9 weeks (with overlap) | Strategy is optimistic by 3-9 weeks |

### Is the overlap realistic?

**Yes, with caveats.** The architects correctly identify that 1c.01-1c.03 are independent of 1b. A solo developer can context-switch between 1b and 1c work if the tasks are well-scoped. However:

1. **Context-switching cost is real.** A solo developer at 10hrs/week will lose 1-2 hours/week to context switching between 1b (MCP server, new code) and 1c (refactoring existing code). Budget 15-20% overhead.

2. **US-1b.01 (engine extraction) is the blocker.** At 16-32 hours, this single story is 50%+ of the critical path. If it takes the full 32 hours (4+ weeks for a 10hr/week developer), everything else stalls. The 1c work can fill some of that time.

3. **US-1c.05 (learn.sh extraction) is the second-largest risk.** The 1c architect correctly flagged this at 8-16 hours and noted the strategy's 2-3 week estimate is optimistic by ~50-100%.

### Realistic total calendar time

**Best case (everything at lower bound, max parallelism):** 6 weeks
- Weeks 1-2: US-1b.02 + US-1b.03 + US-1b.11 + US-1c.01 in parallel
- Weeks 2-4: US-1b.01 (engine extraction — critical path)
- Week 3: US-1c.02 + US-1c.03 in parallel (while 1b.01 continues)
- Week 4-5: US-1b.04 + US-1c.04 + US-1c.05
- Week 5-6: US-1b.05-07 in parallel, US-1b.08, US-1b.09a, US-1c.06-07
- Week 6: US-1b.09b, US-1b.10, US-1c.08-09

**Expected case:** 8-10 weeks
**Pessimistic case (edge cases in engine extraction, learn.sh surprises):** 12-14 weeks

### Stories that could be deferred

**Finding 4.1: US-1c.07 (Decouple pricing from hardcoded models) is nice-to-have for Phase 1.**

The exit criteria require "1 non-Claude-Code client can produce calibrated estimates." All target clients (Cursor, VS Code, Windsurf) use Claude or other Anthropic models. Multi-model pricing support is a Phase 2 concern.

**Recommendation:** Defer US-1c.07 to Phase 2. Save 2-4 hours.

**Finding 4.2: US-1b.10 (Registry Publication) could be split — PyPI first, registry second.**

MCP registry submission processes are external dependencies (waiting for review/approval). PyPI publication is self-service. Ship to PyPI as part of Phase 1; registry listing can be done in parallel or slightly after.

**Recommendation:** Keep as-is but note that registry listing may slip — it's not fully within the developer's control. PyPI + README is sufficient for the exit criteria.

**Finding 4.3: US-1c.06 (Generalize agent-to-step mapping) is already largely handled by the v2 schema design.**

The v2 cost event schema has an explicit `step_name` field. US-1c.02 (v2 reader) will naturally use `step_name` directly. US-1c.06's scope is mostly about documenting that `DEFAULT_AGENT_TO_STEP` is a v1-only fallback and adding a test.

**Recommendation:** Fold US-1c.06 into US-1c.02 as additional acceptance criteria. Save the overhead of a separate story.

---

## Section 5: Missing Stories

### MS-1: Error handling and graceful degradation

**No story covers what happens when things go wrong in the MCP server.** US-1b.03 mentions "proper MCP error responses for unknown tools or malformed input" but this is minimal.

Missing scenarios:
- Calibration directory doesn't exist (first-run) — does `estimate_cost` still work? (Should return uncalibrated estimate)
- Calibration files are corrupted — does the server crash or degrade?
- `wc -l` fails (permissions, binary files, paths with special characters)
- Disk full when writing `active-estimate.json`
- Concurrent MCP tool calls (two `estimate_cost` calls in rapid succession — race condition on `active-estimate.json`?)

**Proposed story:**

> **US-PM.04: Graceful degradation for MCP server edge cases**
>
> **As a** user with a misconfigured or fresh environment, **I want** the MCP server to degrade gracefully rather than crash, **so that** I can still get value from uncalibrated estimates and see clear error messages.
>
> **Acceptance criteria:**
> - [ ] Missing calibration directory: `estimate_cost` returns uncalibrated estimate, `get_calibration_status` returns "no data", `report_session` creates the directory
> - [ ] Corrupted `factors.json` or `history.jsonl`: server logs warning and falls back to uncalibrated
> - [ ] `wc -l` failure: falls back to medium default (consistent with SKILL.md behavior)
> - [ ] Disk write failure: returns error response to client, does not crash server
> - [ ] Concurrent tool calls: `active-estimate.json` writes use atomic rename pattern
>
> **T-shirt estimate:** S (2-4hrs)
> **Depends on:** US-1b.03, US-1b.04
> **Classification:** MUST-HAVE (first-run experience will be broken without this)

### MS-2: First-run experience / onboarding

**The stories cover installation (US-1b.08) and documentation (US-1b.10) but not the first interaction.**

When a user installs the MCP server and calls `estimate_cost` for the first time, what happens? They have no calibration data, no history, possibly no idea what parameters to provide.

US-1b.04 handles input validation and defaults (lenient mode with `size` + `files` + `complexity` as minimum), which is a good start. But there's no "welcome" experience or guided first interaction.

**This is partially addressed by US-PM.03 (quickstart guide) above.** The remaining gap is in-tool guidance — when `get_calibration_status` returns "no data yet," it could include a brief explanation of how calibration works and what the user needs to do.

**Recommendation:** Add to US-1b.05 acceptance criteria:
> - [ ] When calibration directory is empty, the "no data yet" response includes a brief explanation: "Run your first estimate with `estimate_cost`, then report results with `report_session` to begin calibrating. After 3+ sessions, estimates will improve automatically."

### MS-3: Migration path from SKILL.md to MCP

**No story explicitly covers the transition experience for existing Claude Code users.**

A user currently using SKILL.md + hooks may want to switch to the MCP server (for consistency with team members using Cursor, or to use the structured API). What happens to their existing calibration data?

**Good news:** The MCP server reads the same `calibration/` directory, so existing `factors.json` and `history.jsonl` are automatically available. This is documented in US-1b.04 ("Writes `active-estimate.json` and `last-estimate.md` to calibration directory").

**Potential issue:** If a user runs both SKILL.md (with hooks) and MCP server simultaneously, they could get double-counting — SKILL.md's learn.sh fires at session end AND the user calls `report_session` manually. This would append two history records for the same session.

**Proposed addition to US-1b.07 acceptance criteria:**
> - [ ] Detects and warns if the most recent history record has the same `active-estimate.json` hash as the current estimate (indicating learn.sh already recorded this session)

**Classification:** NICE-TO-HAVE (edge case, but real for the transition period)

### MS-4: Security considerations

**No story addresses security for the MCP server reading/writing local files.**

The MCP server runs locally via stdio, which limits the attack surface. But:
- The `--calibration-dir` and `--project-dir` flags accept arbitrary paths — could a malicious MCP client read/write outside the intended directory?
- `file_paths` in `estimate_cost` are passed to `wc -l` via subprocess — is there a command injection risk?
- The sidecar timeline (v2 events) is written to a file path derived from the calibration directory — path traversal?

**The existing codebase already handles some of this:** SKILL.md uses `shlex.quote()` for shell safety, and CLAUDE.md documents this convention. But the MCP server is a new attack surface.

**Proposed addition to US-1b.04 acceptance criteria:**
> - [ ] `file_paths` values are validated: must be relative paths (no `..`), resolved against `--project-dir`, and passed through `shlex.quote()` for `wc -l`
> - [ ] `--calibration-dir` and `--project-dir` are resolved to absolute paths at startup and all file operations are constrained to these directories

**Classification:** MUST-HAVE (security is non-negotiable, even for local tools)

### MS-5: MCP server versioning strategy

**US-1b.08 doesn't specify how the MCP server itself will be versioned.**

Q6 in the 1b open questions asks "Should Phase 1b be v3.0.0?" but the versioning strategy for the MCP server *package* (separate from the tokencast version) isn't addressed.

Questions:
- Is the MCP server version the same as the tokencast version (currently 2.0.0)?
- How are breaking changes to MCP tool schemas communicated?
- Does the `version` field in `estimate_cost` output (currently "2.1.0") refer to the estimation algorithm version or the package version?

**Recommendation:** Resolve as part of US-1b.08 planning. Add an acceptance criterion:
> - [ ] Versioning strategy documented: package version, API version, estimation algorithm version — which can change independently and how they relate

**Classification:** NICE-TO-HAVE (can be decided during implementation, but easier to get right upfront)

### MS-6: CI/CD for the MCP server

**No story covers automated testing and release for the MCP server package.**

US-1b.10 mentions "Set up GitHub Actions for automated releases" but this is a sub-bullet, not an acceptance criterion. For a publishable package, CI is essential:
- Run tests on PR
- Build and publish to PyPI on tag
- Drift detection test (US-1b.09) should run on every PR

**Recommendation:** Add to US-1b.10 acceptance criteria:
> - [ ] GitHub Actions workflow: run tests on PR, build package on tag, publish to PyPI on release
> - [ ] Drift detection test runs in CI

**Classification:** MUST-HAVE (without CI, the package will drift and break silently)

---

## Summary of Proposed New Stories

| ID | Title | Size | Classification | Blocks |
|----|-------|------|---------------|--------|
| US-PM.01 | Opt-in anonymous telemetry | M (4-8hrs) | MUST-HAVE | Phase 1.5 Exp 1 |
| US-PM.02 | "Share with team" waitlist hook | S (2-4hrs) | MUST-HAVE | Phase 1.5 Exp 2 |
| US-PM.03 | "First Estimate in 5 Minutes" quickstart | S (2-4hrs) | NICE-TO-HAVE | Phase 1.5 Exp 4 |
| US-PM.04 | Graceful degradation for edge cases | S (2-4hrs) | MUST-HAVE | First-run experience |

**Additional hours:** 12-20 hrs (MUST-HAVE only: 10-14 hrs)

**Impact on timeline:** Adds ~1-2 weeks to the combined Phase 1 estimate. US-PM.01 and US-PM.02 can run in parallel with US-1b.09 (test suite) since they depend on the tool implementations being done. US-PM.04 can be folded into US-1b.04/US-1b.03 as they're being built.

---

## Acceptance Criteria Additions to Existing Stories

These don't require new stories but should be added to existing ones:

1. **US-1b.10**: Add community launch materials (comparison section, worked example with narrative, non-Claude-Code screenshot)
2. **US-1b.05**: Add first-run guidance message in "no data yet" response
3. **US-1b.07**: Add duplicate-detection for learn.sh/MCP double-reporting
4. **US-1b.04**: Add path validation and `shlex.quote()` for `file_paths`
5. **US-1b.08**: Add versioning strategy documentation
6. **US-1b.10**: Add GitHub Actions CI/CD workflow

---

## Key Recommendations

1. **Merge US-1b.11 into US-1c.05** to avoid duplicate extraction work. Sequence: start 1c.05 early, have 1b.07 consume its output.

2. **Split US-1b.09** into engine tests (09a) and MCP tests (09b) to get engine testing off the critical path.

3. **Add US-PM.01 and US-PM.02** — without telemetry and the waitlist mechanism, Phase 1.5 experiments 1 and 2 cannot run. These are the strategy's primary validation tools.

4. **Defer US-1c.07** (multi-model pricing) to Phase 2. It doesn't serve any Phase 1 exit criterion or Phase 1.5 experiment.

5. **Budget 8-10 weeks realistic** for combined Phase 1b+1c, not the strategy's 6-9 weeks. The engine extraction (US-1b.01) and learn.sh extraction (US-1c.05) are both high-risk items with wide estimate ranges.

6. **Fold US-1c.06 into US-1c.02** — the v2 schema already solves the step-name generalization. A separate story adds overhead without proportional value.
