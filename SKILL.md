---
name: tokencostscope
version: 1.4.0
description: >
  Automatically estimates token usage and dollar cost when a development plan
  is created. Triggers when: a pipeline plan is finalized, an implementation
  plan is produced, an architecture decision is made, or step counts and file
  lists are discussed. Reads the plan from conversation context to infer size,
  file count, complexity, and steps. Loads learned calibration factors from
  prior sessions to improve accuracy over time.
disable-model-invocation: false
allowed-tools: Read, Write, Bash
---

# tokencostscope

Estimate the Claude API cost of a planned software change before execution. Auto-triggers after plans are created. Learns from actual usage to improve over time.

## When This Skill Activates

This skill activates automatically when:
- A planning agent returns an implementation plan, architecture decision, or final plan
- The conversation contains a plan with steps, file lists, or size classification
- The user explicitly invokes `/tokencostscope`

Do NOT activate when:
- No plan exists in the conversation yet
- The conversation is mid-implementation (code is being written, not planned)
- An estimate was already produced for the current plan in this session
- The conversation is about tokencostscope itself (avoid recursive triggering)

## Step 0 — Infer Inputs from Context

If invoked without explicit parameters, infer from the plan in conversation:

1. **Size:** Count pipeline steps mentioned → XS (1-2 steps), S (2-3), M (5-8), L (8+)
2. **Files:** Count file paths or "N files" mentions in the plan
3. **Complexity:** low (bug fix, config, mechanical), medium (new feature, clear scope), high (new system, architectural)
4. **Steps:** Which pipeline steps does the plan cover? Map to the default canonical names in heuristics.md.
5. **Project type:** Infer from plan keywords → `greenfield` (new project/system), `refactor` (restructure/reorganize/simplify), `bug_fix` (fix/broken/regression), `migration` (migrate/upgrade/port), `docs` (documentation/readme). Default: `greenfield`.
6. **Language:** Infer primary language from file extensions in the plan → `.py`→`python`, `.ts/.tsx`→`typescript`, `.js/.jsx`→`javascript`, `.go`→`go`, `.rs`→`rust`, `.rb`→`ruby`, `.java`→`java`, `.sh`→`shell`. If mixed, use the most frequent. Default: `unknown`.
7. **Review cycles (N):** If the inferred steps include a review step (e.g., "Staff Review") AND at least one of a final-plan step (e.g., "Engineer Final Plan"), implementation step, or test-writing step, set `review_cycles = review_cycles_default` from heuristics.md (default 2). If the plan explicitly mentions a cycle count (e.g., "2 review cycles"), use that. If none of the required constituent steps are present, set N=0. N=0 naturally produces $0 via the decay formula (1−0.6^0=0); no special-case handling is needed.

8. **Parallel groups:** Scan the plan text for parallel execution indicators (case-insensitive):
   - Keywords: `"in parallel"`, `"simultaneously"`, `"concurrently"`, `"∥"`, `"parallel:"`,
     `"[parallel]"`, `"(parallel)"`
   - For each keyword match, identify step names in the same grouping window: step names joined
     by comma, `+`, or `"and"` immediately preceding (or following, for `"parallel:"` prefix
     syntax) the keyword.
   - **Boundaries:** Sentence breaks (`.`, `\n`) and sequencing words (`"then"`, `"first"`,
     `"after"`, `"before"`, `"next"`) are hard boundaries — step names on the far side are
     not included in the group.
   - **Matching:** Case-insensitive substring match against canonical step names in heuristics.md.
     If a token matches multiple canonical names (e.g., `"engineer"` → both `"Engineer Initial
     Plan"` and `"Engineer Final Plan"`), treat it as ambiguous and note in transparency output:
     `"Ambiguous: 'engineer' matches multiple steps — falls back to sequential modeling"`.
     Unrecognized tokens: `"Unresolved: 'Linter' — falls back to sequential modeling"`.
   - **Conflict:** A step belongs to at most one group — first occurrence wins.
   - **Minimum size:** Groups with fewer than 2 resolved steps are discarded.
   - Output: `parallel_groups` (list of groups, each a list of canonical step names) and
     `parallel_set` (flat set of all parallel step names for O(1) lookup in Steps 3c/3d).
   - If no parallel language is detected, `parallel_groups = []` and `parallel_set = {}`.

If invoked with explicit parameters (`/tokencostscope size=M files=5 complexity=medium`), use those instead.

## Step 1 — Load References and Calibration

```
Read references/pricing.md      → model prices, cache rates, step→model map
Read references/heuristics.md   → activity token table, pipeline decompositions, multipliers
```

Read `calibration/factors.json` if it exists → learned correction factors from prior runs.
Read `last_updated` from pricing.md. If >90 days old, prepend warning to output.

## Step 2 — Resolve Inputs

- Look up complexity multiplier from heuristics.md
- Look up model for each pipeline step from pricing.md
- If `steps=` override present, filter to only those steps
- If `review_cycles=` override present, use that value as N. Otherwise use the inferred N from Step 0 item 7. If N=0, the PR Review Loop row is omitted entirely from output and `review_cycles_estimated=0` in active-estimate.json.

## Step 3 — Per-Step Calculation

For each pipeline step in scope:

**3a. Base tokens**
```
input_base  = sum over activities: (activity_input_tokens × activity_count)
output_base = sum over activities: (activity_output_tokens × activity_count)
```
Where activity_count for file reads, file edits, and test writes = N (the `files` parameter).
All other activity counts come from the fixed pipeline table in heuristics.md.

**3b. Apply complexity**
```
input_complex  = input_base  × complexity_multiplier
output_complex = output_base × complexity_multiplier
```

**3c. Apply context accumulation (input only)**
```
K           = total activity count in this step
input_accum = input_complex × (K + 1) / 2

If this step is in parallel_set:
    input_accum = input_accum × parallel_input_discount
                  [parallel_input_discount from heuristics.md, default 0.75]
```

**3d. Compute cost for each band (Optimistic / Expected / Pessimistic)**
```
cache_rate ← from pricing.md for this band
If this step is in parallel_set:
    cache_rate = max(cache_rate − parallel_cache_rate_reduction, parallel_cache_rate_floor)
                 [parallel_cache_rate_reduction = 0.15, parallel_cache_rate_floor = 0.05,
                  both from heuristics.md]
band_mult  ← from heuristics.md for this band
price_in   ← model input price per million
price_cr   ← model cache_read price per million
price_cw   ← model cache_write price per million
price_out  ← model output price per million

cache_write_fraction = 1 / K

input_cost  = (input_accum × (1 - cache_rate) × price_in
            +  input_accum × cache_rate × cache_write_fraction × price_cw
            +  input_accum × cache_rate × (1 - cache_write_fraction) × price_cr) / 1,000,000
output_cost = output_complex × price_out / 1,000,000
step_cost   = (input_cost + output_cost) × band_mult
```

**3e. Apply calibration factor (Expected band only)**

Read `step_factors` from `calibration/factors.json` if it exists (default: {}).
Read size-class and global factors as before.

For each step, determine the factor and its source using this precedence chain:
  1. Per-step: if `step_factors[step_name]` exists and `step_factors[step_name]["status"] == "active"` → use
     `step_factors[step_name].factor`, source = "S"
  2. Size-class: if `factors[size]` exists and `factors["{size}_n"]` (e.g.,
     `factors["M_n"]`) >= 3 → use `factors[size]`, source = "Z"
  3. Global: if `factors["global"]` exists and `factors["status"] == "active"` → use
     `factors["global"]`, source = "G"
  4. No calibration: factor = 1.0, source = "--"

Step factor precedence rules — edge cases:
- If a step has both a per-step factor (active) and a size-class factor, the per-step
  factor wins. Cal column shows "S:x.xx".
- If a step's per-step factor has status "collecting" (n < per_step_min_samples=3),
  it is NOT applied; fall through to size-class or global. Cal shows "Z:x.xx",
  "G:x.xx", or "--" per the remaining chain.
- The PR Review Loop row uses the PR Review Loop's own calibration path (Step 3.5).
  Its Cal column always shows "--"; it is not subject to per-step factor lookup.

Apply calibration:
```
calibrated_expected    = expected_cost × factor
calibrated_optimistic  = calibrated_expected × 0.6
calibrated_pessimistic = calibrated_expected × 3.0
```
Record the factor source per step for use in the output Cal column.

Note: Per-step factors REPLACE (do not stack with) size-class and global factors.

## Step 3.5 — PR Review Loop Row (post-step-loop computation, default constituents)

This section runs AFTER all individual pipeline steps have completed their Steps 3a–3e
calculations. It is not inline with the per-step loop. If N=0 (no PR Review Loop in scope),
skip this section entirely — the PR Review Loop row is omitted from output and contributes
$0 to all band totals.

**Constituent steps:** "Staff Review" and "Engineer Final Plan" — using the pre-calibration,
**un-discounted** Expected band costs: `step_cost` values before Step 3e calibration AND
before any parallel discount from Steps 3c/3d. The PR Review Loop cycles are sequential by
nature; C must not inherit the parallel discount even if constituent steps were modeled as
parallel in the main pipeline. Cache each step's pre-discount cost during the per-step loop
for use here. If a constituent step is not in scope, it contributes $0 to C.

**Per-cycle cost (C):**
```
C = staff_review_expected + engineer_final_plan_expected
```

**Per-band review loop cost using geometric series:**
```
optimistic_cycles  = 1        (best case: first pass clears all issues)
expected_cycles    = N        (from Step 2)
pessimistic_cycles = N × 2   (double the Expected cycle count)

review_loop_cost(cycles) = C × (1 − 0.6^cycles) / (1 − 0.6)
                         = C × (1 − 0.6^cycles) / 0.4

When cycles=0, (1 − 0.6^0) = 0, so review_loop_cost = $0 naturally.
```

**Apply calibration to the PR Review Loop row:**

Unlike other steps (which re-anchor Optimistic/Pessimistic as fixed ratios of calibrated
Expected), the PR Review Loop applies the calibration factor independently to each band.
This preserves the decay model's per-band cycle counts:
```
calibrated_optimistic  = review_loop_optimistic  × calibration_factor
calibrated_expected    = review_loop_expected     × calibration_factor
calibrated_pessimistic = review_loop_pessimistic  × calibration_factor
```
If no calibration data (factor = 1.0), raw values are used unchanged.

Add the calibrated review loop totals to the running band sums in Step 4.

## Step 4 — Sum, Format, and Record

Sum step costs across all in-scope steps for each band. Render the output template.

### Compute baseline_cost

If this invocation is for **post-implementation cost analysis** (pipeline step 10), first check
for a prior estimate:
```
Read calibration/last-estimate.md if it exists → prior expected_cost for delta comparison.
Read calibration/active-estimate.json if it exists → structured prior estimate data.
Report the delta: actual_cost − baseline_cost vs prior expected_cost.
```
If neither file exists, note that the prior estimate is unavailable and proceed.

**Step 10 invocations stop here.** Do NOT write a new `active-estimate.json` or `last-estimate.md` for post-implementation analysis — there is no new forward estimate to record, and overwriting with null values would corrupt the learning pipeline. Skip the rest of Step 4.

Before writing the estimate, compute the session's cost so far (baseline):
```
Find the current session JSONL:
  find ~/.claude/projects/ -name "*.jsonl" -type f -print0 | xargs -0 ls -t | head -1

Run: python3 scripts/sum-session-tokens.py <session-jsonl> 0
Use the returned total_session_cost as baseline_cost. If the command fails, use 0.
```

Then write the estimate marker for the learning system. Record each step's calibrated Expected band cost in `step_costs`, keyed by the canonical step name (e.g., `"Research Agent"`, `"Implement"`). If the PR Review Loop is in scope (N > 0), also record its calibrated expected cost under the key `"PR Review Loop"`. This field should always be present in v1.4.0+ estimates — the learning pipeline reads it at session end to compute per-step calibration ratios.
```
Write calibration/active-estimate.json:
{
  "timestamp": "<ISO 8601 now>",
  "size": "<size>",
  "files": <N>,
  "complexity": "<complexity>",
  "steps": ["<step names>"],
  "step_count": <number of steps>,
  "project_type": "<project_type>",
  "language": "<language>",
  "expected_cost": <expected total>,
  "optimistic_cost": <optimistic total>,
  "pessimistic_cost": <pessimistic total>,
  "baseline_cost": <baseline_cost>,
  "review_cycles_estimated": <N from Step 2, or 0 if no PR Review Loop>,
  "review_cycles_actual": null,
  "parallel_groups": [["<step name>", ...], ...],
  "parallel_steps_detected": <count of steps in any parallel group>,
  "step_costs": {
    "<step name>": <calibrated Expected band cost for that step, float>,
    ...
    "PR Review Loop": <calibrated review loop expected cost, float>  // if in scope
  }
}
```

Then write a human-readable summary for compaction survival:
```
Write calibration/last-estimate.md:
# Last tokencostscope Estimate

**Feature:** {infer from plan context — e.g., "v1.3.0 parallel agent accounting"}
**Recorded:** {ISO 8601 timestamp}
**Size:** {size} | **Files:** {N} | **Complexity:** {complexity}
**Type:** {project_type} | **Language:** {language}
**Steps:** {step names, comma-separated}

| Band       | Cost    |
|------------|---------|
| Optimistic | ${optimistic_cost} |
| Expected   | ${expected_cost}   |
| Pessimistic| ${pessimistic_cost}|

Review cycles estimated: {review_cycles_estimated}
Parallel steps detected: {parallel_steps_detected}
```
This file is the compaction-safe reference for pipeline step 10 cost analysis.

## Output Template

```
## costscope estimate (v1.4.0)

**Change:** size={size}, files={N}, complexity={complexity}, type={project_type}, lang={language}
**Steps:** {all | list of included steps} ({step_count} steps)
**Pricing:** last updated {last_updated}
**Calibration:** {Join active segments with " | ". Show only segments where data exists:
  - Per-step: "{K} steps with per-step factors" (K = count of steps with status="active" in step_factors)
  - Size-class: "size-class {size}={factor}x ({n} runs)"
  - Global: "global {factor}x ({n} runs)"
  Examples: (a) "3 steps with per-step factors | size-class M=1.18x (7 runs) | global 1.12x (10 runs)"
            (b) "size-class M=1.18x (7 runs)"  (c) "global 1.12x (10 runs)"
            (d) "3 steps with per-step factors"  (e) "no prior data — will learn after this session"
            (f) "2 steps with per-step factors | global 1.12x (10 runs)"
            (g) "2 steps with per-step factors | size-class M=1.18x (7 runs)"
            (h) "size-class M=1.18x (7 runs) | global 1.12x (10 runs)"}
{WARNING line if pricing stale}

| Step                  | Model       | Cal    | Optimistic | Expected | Pessimistic |
|-----------------------|-------------|--------|------------|----------|-------------|
| ┌ Parallel Group 1 ∥  |             |        |            |          |             |
| │ Research Agent      | Sonnet      | S:0.82 | $X.XX      | $X.XX    | $X.XX       |
| └ Architect Agent     | Opus        | G:1.12 | $X.XX      | $X.XX    | $X.XX       |
| [sequential steps]    | ...         | --     | ...        | ...      | ...         |
| PR Review Loop        | Opus+Sonnet | --     | $X.XX      | $X.XX    | $X.XX       |
| **TOTAL**             |             |        | **$X.XX**  | **$X.XX**| **$X.XX**   |
Cal: S=per-step  Z=size-class  G=global  --=uncalibrated

**Cal column values:** S:x.xx = per-step factor applied · Z:x.xx = size-class factor · G:x.xx = global factor · -- = no calibration active (factor=1.0) or PR Review Loop row
**Cal column edge cases:**
- Step has active per-step AND size-class factor → per-step wins, show "S:x.xx"
- Step has active per-step AND global factor → per-step wins, show "S:x.xx"
- Step's per-step factor has status "collecting" (n < 3) → not applied; fall through to size-class → show "Z:x.xx" if active, else "G:x.xx" if active, else "--"
- PR Review Loop row → always "--" regardless of any factors in factors.json
**Parallel groups (when detected):** Group 1 (step names...) — modeled with 0.75× input accumulation, −0.15 cache rate
**Bands:** Optimistic (1 review cycle) · Expected (N cycles) · Pessimistic (N×2 cycles)
**Tracking:** Estimate recorded. Actuals will be captured automatically at session end.
```

**Box-drawing rules for parallel groups:** First step in group uses `┌`, intermediate steps use `│`, last step uses `└`. For a 2-step group, the first uses `┌` and the second uses `└` (no `│` rows).

The "Opus+Sonnet" value in the Model column is an accepted composite value indicating the
row spans two models (Staff Review on Opus, Engineer Final Plan on Sonnet). The PR Review
Loop row is omitted when review_cycles=0. When the PR Review Loop row is absent, the Bands
line reverts to: `Optimistic (best case) · Expected (typical) · Pessimistic (with rework)`

## Overrides (manual invocation only)

| Override | Effect |
|----------|--------|
| `size=M` | Set size class explicitly |
| `files=5` | Set file count explicitly |
| `complexity=high` | Set complexity explicitly |
| `steps=implement,test,qa` | Estimate only those pipeline steps |
| `project_type=migration` | Set project type explicitly |
| `language=go` | Set primary language explicitly |
| `review_cycles=3` | Override the number of PR review cycles (N). Use 0 to suppress the PR Review Loop row. |

## Limitations

- Pipeline step names reflect a default workflow. Map your own steps to the closest defaults; the formulas are pipeline-agnostic.
- Token counts assume typical 150-300 line source files.
- Parallel agent modeling uses fixed discount factors; actual cache and context behavior varies by agent topology.
- Calibration requires 3+ completed sessions before corrections activate.
- Pricing data may be stale; check `last_updated` in references/pricing.md.
