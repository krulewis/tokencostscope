# Token Heuristics Reference

## Activity Token Estimates

| Activity            | Input Tokens | Output Tokens | Notes                                      |
|---------------------|--------------|---------------|--------------------------------------------|
| File read           | 10,000 (medium default — see File Size Brackets below) | 200 | Default for medium bracket (50–500 lines); see File Size Brackets |
| File write (new)    | 1,500        | 4,000         | Planning context + generated code          |
| File edit           | 2,500 (medium default — see File Size Brackets below) | 1,500 | Input scales with bracket; output unchanged |
| Test write          | 2,000        | 5,000         | Test files are verbose                     |
| Code review pass    | 8,000        | 3,000         | Includes reading the diff/files            |
| Research/exploration| 5,000        | 2,000         | Search results + synthesis                 |
| Planning step       | 3,000        | 4,000         | Context gathering + plan output            |
| Grep/search         | 500          | 500           | Tool call overhead                         |
| Shell command       | 300          | 500           | Command + result                           |
| Conversation turn   | 5,000        | 1,500         | System prompt + tool definitions + response|

## Pipeline Step Activity Counts

The steps below represent a **default pipeline** — a reasonable baseline for multi-agent
Claude Code workflows. Your workflow may use different step names or omit some steps entirely.
Map your pipeline's steps to the closest matches; the token heuristics and formulas are
pipeline-agnostic.

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
| QA                    | Haiku  | 3 shell commands, 2 file reads, 2 conv turns            |
| PR Review Loop        | Opus+Sonnet | 1 Staff Review + 1 Engineer Final Plan per cycle (default constituents) |

*Opus for L-size changes.

Note: Staff Review does NOT include separate file reads. The code review pass (8,000 input tokens)
already accounts for reading the diff and relevant files.

Note: PR Review Loop is a composite step. Each cycle contains one Staff Review (Opus) and one
Engineer Final Plan (Sonnet) as default constituents. The constituent step costs are calculated
individually using their respective activity rows above, then summed to produce the per-cycle cost.
The label "Opus+Sonnet" in the Model column indicates this composite.

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

## Parallel Agent Accounting

When pipeline steps run as parallel subagents, two adjustments apply to reduce their estimated
cost: (1) they start fresh without inheriting accumulated context from prior steps, and (2) they
cannot reuse cache warmed by preceding sequential steps.

| Parameter                       | Value | Notes                                               |
|---------------------------------|-------|-----------------------------------------------------|
| parallel_input_discount         | 0.75  | Multiplier on input_accum for parallel steps        |
| parallel_cache_rate_reduction   | 0.15  | Subtracted from each band's cache hit rate          |
| parallel_cache_rate_floor       | 0.05  | Minimum effective cache hit rate after reduction    |

These values are heuristic estimates and will be refined via calibration as parallel-tagged
sessions accumulate in history.jsonl. Groups with fewer than 2 resolved steps are discarded
(a single-step "parallel group" is semantically meaningless and is treated as sequential).

## Per-Step Calibration

When per-step correction factors are active, SKILL.md Step 3e applies a step-level factor
before falling back to the size-class or global factor.

| Parameter               | Value | Notes                                                  |
|-------------------------|-------|--------------------------------------------------------|
| per_step_min_samples    | 3     | Minimum history entries before a per-step factor activates |

Matches the existing size-class activation threshold (3 records).
Both thresholds should be updated together if changed. The value is also hardcoded in
update-factors.py Pass 4, consistent with the existing size-class threshold in Pass 3.

## File Size Brackets

When file paths are extractable from the plan and files exist on disk, tokencostscope
measures each file's line count and assigns one of three size brackets. The bracket
determines the input token budget for file read and file edit activities.

| Bracket | Line Count | File Read Input | File Edit Input | Notes                                |
|---------|-----------|-----------------|-----------------|--------------------------------------|
| Small   | ≤ 49      | 3,000           | 1,000           | Config files, type stubs, small scripts |
| Medium  | 50–500    | 10,000          | 2,500           | Typical source file (default)        |
| Large   | ≥ 501     | 20,000          | 5,000           | Large modules, generated files       |

File read output tokens (200) and file edit output tokens (1,500) are unchanged across all brackets.

**Boundary values (tunable):**
- `file_size_small_max = 49`   (lines ≤ 49 → small; lines ≥ 50 → medium)
- `file_size_large_min = 501`  (lines ≤ 500 → medium; lines ≥ 501 → large)

**Measurement cap:** `file_measurement_cap = 30` — maximum files measured per estimate via
`wc -l`. Files beyond the cap use the `avg_file_lines=` override bracket or medium default.

**Binary extensions excluded from measurement (fall back to medium):**
`.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.ico`, `.svg`, `.wasm`, `.pyc`, `.pyo`,
`.so`, `.dll`, `.dylib`, `.exe`, `.bin`, `.o`, `.a`, `.class`

**Resolution order (per file):**
1. Measured on disk via `wc -l` → bracket from line count
2. `avg_file_lines=N` override → bracket from N (applies to new/unmeasured files)
3. Default → medium (10,000 input tokens/read, 2,500 input tokens/edit)

**New-file classification:** A file is "new" only if (a) the surrounding sentence/bullet
contains "create", "new file", or "write", AND (b) the file does not exist on disk (wc -l
returns an error). Files that exist on disk are always "existing" regardless of plan language.

**Step classification by file-read scaling:**
- N-scaling steps (file counts scale with N): Implementation (N reads + N edits), Test Writing (N test writes)
- Fixed-count steps (use weighted average): Research Agent (6 reads), Engineer Initial Plan (4 reads), Engineer Final Plan (2 reads), QA (2 reads)
- No file reads: Architect Agent, Staff Review

**Weighted average for fixed-count file reads:**
```
avg_file_read_tokens = (small_count × 3,000 + medium_count × 10,000 + large_count × 20,000)
                       / total_measured
avg_file_edit_tokens = (small_count × 1,000 + medium_count × 2,500 + large_count × 5,000)
                       / total_measured
```
Where `total_measured = file_brackets["small"] + file_brackets["medium"] + file_brackets["large"]`.

If `total_measured = 0` (no files measured): `avg_file_read_tokens = 10,000` and
`avg_file_edit_tokens = 2,500` (medium defaults — preserves v1.4.0 behavior).

**Cap overflow behavior:** When more than 30 paths are extracted, measure the first 30 (by
plan order). Files beyond the cap receive the weighted-average bracket of the first 30
measured files. If no files are successfully measured, overflow files receive the override
bracket or medium default.
