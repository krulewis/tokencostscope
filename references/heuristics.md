# Token Heuristics Reference

## Activity Token Estimates

| Activity            | Input Tokens | Output Tokens | Notes                                      |
|---------------------|--------------|---------------|--------------------------------------------|
| File read           | 10,000       | 200           | Typical 150-300 line source file           |
| File write (new)    | 1,500        | 4,000         | Planning context + generated code          |
| File edit           | 2,500        | 1,500         | Existing context + diff output             |
| Test write          | 2,000        | 5,000         | Test files are verbose                     |
| Code review pass    | 8,000        | 3,000         | Includes reading the diff/files            |
| Research/exploration| 5,000        | 2,000         | Search results + synthesis                 |
| Planning step       | 3,000        | 4,000         | Context gathering + plan output            |
| Grep/search         | 500          | 500           | Tool call overhead                         |
| Shell command       | 300          | 500           | Command + result                           |
| Conversation turn   | 5,000        | 1,500         | System prompt + tool definitions + response|

## Pipeline Step Activity Counts

N = file count from the implementation plan (e.g., 5 files → N=5).

| Step                  | Model  | Activities                                              |
|-----------------------|--------|---------------------------------------------------------|
| Research Agent        | Sonnet | 6 file reads, 4 searches, 1 planning step, 3 conv turns |
| Architect Agent       | Opus   | 1 code review pass, 1 planning step, 2 conv turns       |
| Engineer Initial Plan | Sonnet | 4 file reads, 2 searches, 1 planning step, 2 conv turns |
| Staff Review          | Opus   | 1 code review pass, 2 conv turns                        |
| Engineer Final Plan   | Sonnet | 2 file reads, 1 planning step, 2 conv turns             |
| Test Writing          | Sonnet | 3 file reads, N test writes, 3 conv turns               |
| Implementation        | Sonnet*| N file reads, N file edits, 4 conv turns                |
| Playwright QA         | Haiku  | 3 shell commands, 2 file reads, 2 conv turns            |
| PR Review Loop        | Opus+Sonnet | 1 Staff Review + 1 Engineer Final Plan per cycle   |

*Opus for L-size changes.

Note: Staff Review does NOT include separate file reads. The code review pass (8,000 input tokens)
already accounts for reading the diff and relevant files.

Note: PR Review Loop is a composite step. Each cycle contains one Staff Review (Opus) and one
Engineer Final Plan (Sonnet). The constituent step costs are calculated individually using their
respective activity rows above, then summed to produce the per-cycle cost. The label "Opus+Sonnet"
in the Model column indicates this composite.

## Complexity Multipliers

| Complexity | Multiplier |
|------------|------------|
| Low        | 0.7x       |
| Medium     | 1.0x       |
| High       | 1.5x       |

Applied to both input and output base tokens before context accumulation.

## Confidence Band Multipliers

| Band        | Multiplier | Notes                                          |
|-------------|------------|------------------------------------------------|
| Optimistic  | 0.6x       | Best case — fast, focused agent work           |
| Expected    | 1.0x       | Typical run                                    |
| Pessimistic | 3.0x       | With rework loops, debugging, re-reads         |

**PR Review Loop cycle counts by band:**
- Optimistic: N=1 review cycle. Rationale: best case assumes the first review pass finds no
  blocking issues, so the loop exits after a single Staff Review + fix cycle.
- Expected: N review cycles (from `review_cycles` input, default 2).
- Pessimistic: N×2 review cycles (double the Expected cycle count).

If `review_cycles=0`, no PR Review Loop row appears in the output.

## Context Accumulation

Each step's input tokens grow as prior turns accumulate in the context window.
Approximation: multiply step_input_complex by (K+1)/2, where K = total activity count in the step.

This models triangular growth: first activity sees 1x context, last sees Kx, average is (K+1)/2.
Cache hit rate applies to the repeated prefix portion of accumulated input.

## Partial Pipeline

To estimate only specific steps, use the `steps:` override (e.g., `steps:implement,test,qa`).
The skill sums only the specified steps. All other formula steps remain identical.

## PR Review Loop Defaults

These values govern the automatic PR Review Loop row added to estimates when the
planning pipeline includes a Staff Review step alongside Implementation or Test Writing.

| Parameter               | Value | Notes                                               |
|-------------------------|-------|-----------------------------------------------------|
| review_cycles_default   | 2     | Expected number of review-fix-re-review cycles      |
| review_decay_factor     | 0.6   | Geometric decay: each cycle costs 60% of prior      |

### Base Cycle Cost (C)

C is computed from Expected-band step costs (pre-calibration, factor = 1.0):

```
C = staff_review_expected + engineer_final_plan_expected
```

Only steps that are in scope (per the `steps=` override or the inferred pipeline)
contribute to C. If a constituent step is not in scope, its contribution is $0.

### Aggregated Row Formula

The PR Review Loop appears as a single row. Each cycle's cost decays geometrically:

```
review_total(N) = C × (1 − 0.6^N) / (1 − 0.6)
               = C × (1 − 0.6^N) / 0.4
```

Where N = cycle count for the band being computed.

When N=0, the formula naturally produces $0 (since 1−0.6^0 = 0). No special case is needed.

The calibration factor (from factors.json) is applied independently to each band
(Optimistic, Expected, Pessimistic), unlike other steps which re-anchor bands as
fixed ratios of calibrated Expected. See SKILL.md Step 3.5 for details.
