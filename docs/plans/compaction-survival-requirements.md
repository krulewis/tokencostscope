# Compaction Survival — Requirements & Definition of Done

**Status:** Requirements (pre-design)
**Branch:** `claude/preserve-cost-before-compaction-61EaR`

## Problem statement

Long-running tokencast pipelines lose calibration data when Claude Code's
context compaction fires mid-session. The orchestrator's working state is
truncated; downstream cost-attribution and history-record creation either
no-op silently or attribute the entire session to a partial estimate. The
system must survive compaction and remain locally diagnosable when it does
not.

## Personas in scope

| Persona | Primary context |
|---|---|
| Maintainer (you) | Dogfooding tokencast on multi-hour pipelines; needs diagnostics on own machine |
| End user (you) | Running real work where compaction interrupts cost capture |
| PyPI installers | Other developers who installed tokencast; we never see their machines |

Out of scope persona: dedicated remote-bug-triage workflow (no support pipeline yet).

## Primary job-to-be-done

> When my pipeline compacts mid-run, I still get **one accurate calibration
> record** at the end covering the entire pipeline.

## Questions the system must be able to answer

After this work ships, the system must answer the following without requiring
the asker to read source code:

1. **Full pipeline cost** — What did this pipeline actually cost end-to-end,
   including every compaction segment?
2. **Why no record?** — For any given session, was a calibration record written?
   If not, what was the exact reason?

Out of scope (deferred): fleet-wide failure-rate analytics, cross-user adoption
metrics. These are valuable but not v1.

## Constraints (the system must NEVER)

| # | Constraint | Rationale |
|---|---|---|
| C1 | Block or delay Claude Code's compaction | User workflow always wins over calibration concerns |
| C2 | Add noticeable latency to any hook (target: <500ms added per invocation) | Hooks run in the user's terminal; perceptible delay is unacceptable |
| C3 | Send any cost or session data off-machine without explicit user opt-in | Privacy posture matches existing `telemetry.py` opt-out model |

Note: C1 + C2 require fail-silent / fail-fast behavior. Fail-silent does NOT
mean fail-invisible — see R3 for the local-log requirement.

Note: One-time onboarding output to the user terminal is acceptable (see R5).
Repeated noisy hook output is not.

## Functional requirements

### R1 — Cost survives compaction

A pipeline that triggers one or more compactions must still produce **exactly
one** calibration record in `history.jsonl` at session end. The recorded cost
must match the true session total (sum of all billable JSONL turns minus
baseline) **within 5%**.

### R2 — Original estimate baseline is protected against re-runs

If `/tokencast` runs more than once in a single session (e.g., the orchestrator
re-runs it after compaction), the **first** estimate's baseline and pipeline
shape must be preserved as the basis for the calibration record. A naive
overwrite of the original baseline is a regression of this requirement.

### R3 — Every hook invocation leaves a local trace

Every invocation of the Stop hook and PreCompact hook (and any new hook this
work introduces) must append a structured entry to a local log file under the
`calibration/` directory. Each entry must record at minimum:

- Timestamp
- Hook name + event
- Outcome (success / specific exit reason)

This log is the canonical answer to "Why no record?" — a user or maintainer
can read it directly without tooling.

### R4 — Resumed sessions attribute cost correctly

A session resumed via `claude --resume` (potentially across days) must still
produce a correct calibration record covering only the work performed for the
active estimate. Cost incurred before the estimate was created remains
excluded as baseline.

### R5 — PyPI installers get a one-time onboarding hint

A user who has installed tokencast but never run `/tokencast` should receive a
**single, non-intrusive** hint on a qualifying session pointing them to
`/tokencast`. After that hint, the system stays silent for that user until
they opt in. The hint must not repeat per-session.

## Non-functional requirements

### NFR1 — Hook performance budget

Each hook invocation must add no more than **500ms (p99)** to the user's
terminal latency, measured against a baseline of the hook running with all
of its work disabled.

### NFR2 — Local-only data flow in v1

No new code path introduced by this work may transmit data over the network.
Any future telemetry extension must reuse the existing `telemetry.py` opt-out
mechanism and is explicitly out of scope for v1.

### NFR3 — Bug-report artifact is self-contained

When a user reports a problem, the artifact they share — the local log file
plus their most recent estimate file (`active-estimate.json` or
`last-estimate.md`) — must be sufficient to diagnose any of the failure modes
listed in R1–R4. No additional logs or environment dumps required.

## Edge cases that must work in v1

| EC | Scenario | Required behavior |
|---|---|---|
| EC1 | Pipeline compacts 2+ times | Produces one accurate merged record per R1 |
| EC2 | `/tokencast` runs twice in one session | Original baseline preserved per R2 |
| EC3 | Session resumed across days via `--resume` | Correct attribution per R4 |

Out of scope edge cases (acceptable to lose data on these in v1):

- Claude Code crash or `kill -9` mid-pipeline
- Filesystem full / `calibration/` directory not writable
- Concurrent sessions writing to the same `calibration/` directory

## Out of scope for v1

- **PostHog fleet-visibility events** — no `hook_invoked` / `hook_exit_reason`
  events sent to PostHog. May be revisited as a v2 story.
- **Sentry integration** — neither in hooks nor in MCP server. Deferred.
- **CLI/MCP status command** for hook history (`tokencast diagnose` etc.) —
  the local log file alone is enough for v1.
- **Bundling tool** for bug reports — manual file collection is acceptable.
- **Crash-tolerance** beyond clean shutdown.
- **Concurrent-session safety** for `calibration/` writes.

## Definition of Done

Each item below must be observably true and verifiable by an outside reviewer.

### DoD-1 — Survives multi-compaction (covers R1, EC1)

A real pipeline that triggers ≥2 compactions, run end-to-end against the
shipped code, produces exactly one new line in `history.jsonl` whose recorded
cost is within 5% of the true session cost (computed independently by summing
billable JSONL turns minus baseline).

### DoD-2 — Synthetic merge correctness (covers R1)

An automated test in `tests/` exercises the snapshot-and-merge logic against a
fixture JSONL with simulated PreCompact + Stop events and asserts:
- Exactly one merged history record is produced
- Recorded cost is within 5% of the fixture's known true total
- Test runs in CI on every PR

### DoD-3 — Original-baseline protection (covers R2, EC2)

An automated test asserts that running the estimate-creation flow twice
within a single simulated session results in a calibration record based on
the **first** estimate's baseline and pipeline signature, not the second.

### DoD-4 — Resumed-session attribution (covers R4, EC3)

An automated test or fixture-based test exercises a resumed-session scenario
and asserts the calibration record correctly attributes only the post-estimate
cost.

### DoD-5 — Local trace exists for every invocation (covers R3, NFR3)

For every Stop and PreCompact hook invocation in a recorded test scenario,
the local log file gains exactly one entry containing the timestamp, hook
name, and exit reason. An automated test asserts this for both the success
path and at least three distinct failure paths (e.g., no estimate present,
JSONL not found, cost-computation error).

### DoD-6 — Performance budget met (covers NFR1)

Hook runtime is measured (e.g., via a benchmark script or time-bounded test)
and shown to add ≤500ms p99 to baseline hook execution.

### DoD-7 — No network calls (covers NFR2, C3)

Static review or test-time assertion confirms no new network call is
introduced by this work. (Existing `telemetry.py` opt-out behavior is
unchanged.)

### DoD-8 — One-time onboarding hint (covers R5)

An automated test asserts that the first qualifying hook invocation for a
user with no `/tokencast` history produces a single hint, and subsequent
invocations for that same user do not.

### DoD-9 — Bug-report artifact is sufficient (covers NFR3)

Documentation in the wiki or repo README states which files a user should
share when reporting a bug, and the listed files are sufficient to reproduce
diagnosis for the failure modes covered by DoD-5.

## Open questions for the design phase

These are explicitly **not** decided here — they belong in the architect/engineer
phase after this requirements doc is approved:

- File format and naming for the local log and any snapshot files
- Whether snapshots store cumulative or delta cost
- How and where the one-time onboarding hint state is persisted
- Exact exit-reason taxonomy for the Stop hook
- Whether the Stop hook's silent-failure paths (currently `2>/dev/null` + `|| exit 0`) get a unified wrapper
