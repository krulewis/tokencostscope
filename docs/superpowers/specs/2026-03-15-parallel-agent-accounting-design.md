# Parallel Agent Accounting — Design Spec

**Date:** 2026-03-15
**Feature:** v1.3 — Parallel agent accounting
**Status:** Approved

---

## Problem

The tokencostscope skill models all pipeline steps as sequential. When steps run as parallel
subagents, two cost differences apply that the current model ignores:

1. **Fresh start effect** — parallel agents don't inherit accumulated context from prior steps,
   so their effective input token count is lower than sequential agents at the same pipeline
   position.
2. **Cache miss effect** — parallel agents can't reuse the cache warmed by preceding sequential
   steps, so their cache hit rate is lower.

The current Limitations section explicitly documents this gap: "Does not model parallel agent
execution (treated as sequential)." This feature closes it.

---

## Approach

**Option chosen: Fixed parallel discount factors (Option A)**

Apply two discount parameters to parallel steps at estimation time:

- `parallel_input_discount` — multiplier on `input_accum` for parallel steps
- `parallel_cache_rate_reduction` — subtracted from each band's cache hit rate for parallel steps

All other calculation logic (complexity, output tokens, calibration, PR Review Loop) is
unchanged. Sequential steps are unaffected — zero behavioral change when no parallel groups
are detected.

**Rejected alternatives:**
- *Position-aware discount (Option B)*: more accurate but adds pipeline-ordering logic for a
  small gain that can't yet be calibrated against real data.
- *Parallel group composite rows (Option C)*: loses per-step cost visibility in the output table.

---

## Detection

The skill scans the plan text in Step 0 for parallel indicators. A parallel group is a set of
steps mentioned together in a parallel context.

**Detection patterns (case-insensitive):**
- `"in parallel"` / `"simultaneously"` / `"concurrently"` — steps named in the same sentence
- `"parallel:"` or `"∥"` followed by step names
- `"[parallel]"` or `"(parallel)"` tags adjacent to step names
- Comma/`+`/`"and"`-joined step names immediately preceding any keyword (e.g., `"Research + PM run in parallel"`)

**Grouping boundary rule:** Only step names joined by comma, `+`, or `"and"` immediately preceding
(or immediately following for "parallel:" prefix syntax) the parallel keyword are included in the
group. The following act as group boundaries and exclude steps appearing before them: sentence
breaks (`.`, `\n`), and sequencing words (`"then"`, `"first"`, `"after"`, `"before"`, `"next"`).

Example: `"Research Agent runs first, then PM Agent and Implementation in parallel with Test Writing"`
→ Group: PM Agent, Implementation, Test Writing (Research Agent excluded — it precedes the
sequencing boundary `"then"`).

**Matching:** Step names are matched against the canonical step list in `heuristics.md` using
case-insensitive substring matching (e.g., `"research"` matches `"Research Agent"`). If a token
matches multiple canonical names (e.g., `"engineer"` matches both `"Engineer Initial Plan"` and
`"Engineer Final Plan"`), it is ambiguous and treated as sequential with a distinct transparency
note: `"Ambiguous: 'engineer' matches multiple steps — treated as sequential"`. Only canonical
steps (those with an activity row in the Pipeline Step Activity Counts table) are eligible for
matching. Unrecognized names are noted as: `"Unresolved: 'Researcher' — treated as sequential"`.

**Grouping rules:**
- Steps co-detected as parallel form a named group (Group 1, Group 2, …)
- A step belongs to at most one group — **first occurrence wins**; subsequent mentions of the same
  step in different parallel contexts are ignored
- Unrecognized step names are noted in the transparency output: "Unresolved: 'Researcher' — treated
  as sequential"
- If no parallel language is detected, behavior is identical to current skill

---

## Cost Adjustments

Adjustments apply during Step 3 for each step that belongs to a parallel group.

### Step 3c (modified) — Input Accumulation

```
input_accum = input_complex × (K+1)/2 × parallel_input_discount
```

`parallel_input_discount = 0.75` (from `heuristics.md`)

Rationale: parallel agents start with lighter initial context than sequential agents at the same
pipeline position. The 0.75 multiplier reduces total effective input tokens (including intra-step
accumulation) as an approximation of this effect. Note: `(K+1)/2` models intra-step context growth;
the discount is commutative with it (0.75 × (K+1)/2 = (K+1)/2 × 0.75) and represents a blended
reduction across both intra-step accumulation and the absent inter-step carryover.

### Step 3d (modified) — Cache Rate

```
effective_cache_rate = band_cache_rate − parallel_cache_rate_reduction
effective_cache_rate = max(effective_cache_rate, 0.05)
```

`parallel_cache_rate_reduction = 0.15` (from `heuristics.md`)

Resulting per-band effective cache rates for parallel steps:

| Band        | Default | Parallel (adjusted) |
|-------------|---------|---------------------|
| Optimistic  | 60%     | 45%                 |
| Expected    | 50%     | 35%                 |
| Pessimistic | 30%     | 15%                 |

The `max(..., 0.05)` floor prevents negative rates if parameters are later tuned aggressively.

Note: The flat 0.15 reduction has proportionally different cost impacts per band. Because
`price_in / price_cr` is ~10× for all models, a lower cache rate increases cost more on the
pessimistic band (which already has low cache hits) than on the optimistic band. This is
intentional — the pessimistic scenario already models poor cache utilization, and parallel
execution makes it worse.

### PR Review Loop interaction

If a constituent step of the PR Review Loop (Staff Review or Engineer Final Plan) is in a parallel
group, the parallel discount **must not propagate into C**. The PR Review Loop cycles are sequential
by nature — each cycle is a full Staff Review + Engineer Final Plan pass. C is computed from the
**un-discounted Expected band step costs** (pre-calibration), regardless of whether those steps
were modeled as parallel in the main pipeline.

Implementation: compute and cache the un-discounted step costs before applying the parallel
discount; use the cached values for C in Step 3.5.

---

## Output Format

Parallel groups are visually bracketed in the cost table. Sequential steps appear normally.

```
## costscope estimate (v1.3.0)

| Step                      | Model       | Optimistic | Expected | Pessimistic |
|---------------------------|-------------|------------|----------|-------------|
| ┌ Parallel Group 1 ∥      |             |            |          |             |
| │ Research Agent          | Sonnet      | $X.XX      | $X.XX    | $X.XX       |
| └ Architect Agent         | Opus        | $X.XX      | $X.XX    | $X.XX       |
| Engineer Initial Plan     | Sonnet      | $X.XX      | $X.XX    | $X.XX       |
| ┌ Parallel Group 2 ∥      |             |            |          |             |
| │ Implementation          | Sonnet      | $X.XX      | $X.XX    | $X.XX       |
| └ Test Writing            | Sonnet      | $X.XX      | $X.XX    | $X.XX       |
| PR Review Loop            | Opus+Sonnet | $X.XX      | $X.XX    | $X.XX       |
| **TOTAL**                 |             | **$X.XX**  | **$X.XX**| **$X.XX**   |
```

Group header rows carry no cost values — structural only. Individual step costs remain visible.
Box-drawing characters (┌│└) render correctly in the terminal's monospace context where the
skill output is displayed. A 2-space-indent fallback (e.g., `  Research Agent`) is acceptable
if box chars cause alignment issues in a given environment.

A transparency note is appended below the table when parallel groups are present:

```
**Parallel groups:** Group 1 (Research Agent, Architect Agent), Group 2 (Implementation, Test Writing)
— modeled with 0.75× input accumulation, −0.15 cache rate
[Unresolved: 'Researcher' — treated as sequential]   ← only if unresolved names exist
```

---

## Schema Change — active-estimate.json

Two new fields added (backward compatible — existing readers use `.get()` defaults):

```json
{
  "parallel_groups": [["Research Agent", "Architect Agent"], ["Implementation", "Test Writing"]],
  "parallel_steps_detected": 4
}
```

`parallel_groups` is `[]` and `parallel_steps_detected` is `0` when no parallel groups found.

These fields are also propagated into `history.jsonl` by `learn.sh` to enable future
parallel-aware calibration stratification. Field names in `history.jsonl` are identical:
`"parallel_groups"` (default `[]`) and `"parallel_steps_detected"` (default `0`). Older records
without these fields are read via `.get()` defaults in any future stratification logic.
This is forward-looking data collection; no calibration logic changes are required now.

---

## Files Changed

| File | Change |
|------|--------|
| `SKILL.md` | Bump version to 1.3.0 in frontmatter and output template header. Step 0: add parallel group detection (step 8). Step 3c (modified): apply `parallel_input_discount`. Step 3d (modified): apply `parallel_cache_rate_reduction` with floor. Step 3.5: clarify C uses un-discounted step costs. Step 4: render bracketed group rows + transparency note; group header rows are display-only and do not appear in `active-estimate.json` `steps` array or increment `step_count`. Limitations: replace sequential-only bullet with "Parallel agent modeling uses fixed discount factors; actual cache and context behavior varies by agent topology." |
| `references/heuristics.md` | Add "Parallel Agent Accounting" section with two new parameters. |
| `scripts/tokencostscope-learn.sh` | Bump VERSION to 1.3.0. Propagate `parallel_groups` and `parallel_steps_detected` from `active-estimate.json` into `history.jsonl`. |
| `calibration/active-estimate.json` | Schema extended (written by SKILL.md; no code file changes needed). |

**Not changed:** `references/pricing.md`, `scripts/sum-session-tokens.py`, `scripts/update-factors.py`, PR Review Loop logic.

---

## Parameter Values

Initial values are heuristic estimates. They will be refined via calibration once parallel-tagged
sessions accumulate in `history.jsonl`.

| Parameter                       | Value | Rationale |
|---------------------------------|-------|-----------|
| `parallel_input_discount`       | 0.75  | ~25% total input reduction for fresh-start agents |
| `parallel_cache_rate_reduction` | 0.15  | Cache miss penalty for no warmed prefix |
